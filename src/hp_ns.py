"""Game stat monitor — Player (CP/HP/MP) + Pet (HP/MP) + Enemy targeting.

Run:  python src/hp_ns.py [--no-preview]
"""

import os
import io
import logging
import logging.handlers
import time
import random
import argparse
import threading
import signal

import sys
import ctypes
import subprocess
from pathlib import Path

import keyboard

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Win32 helpers via ctypes (stdlib — no pywin32 needed)
_WIN32_AVAILABLE = sys.platform == "win32"

if _WIN32_AVAILABLE:
    import ctypes.wintypes as _wt
    _u32 = ctypes.windll.user32

    def _get_foreground_title() -> str:
        hwnd = _u32.GetForegroundWindow()
        n = _u32.GetWindowTextLengthW(hwnd)
        if not n:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        _u32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _win_find_hwnd(substring: str) -> int:
        """Return the HWND of the first visible window whose title contains substring, or 0."""
        found = [0]
        @ctypes.WINFUNCTYPE(ctypes.c_bool, _wt.HWND, _wt.LPARAM)
        def _cb(hwnd, _):
            if _u32.IsWindowVisible(hwnd):
                n = _u32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    _u32.GetWindowTextW(hwnd, buf, n + 1)
                    if substring.lower() in buf.value.lower():
                        found[0] = hwnd
                        return False
            return True
        _u32.EnumWindows(_cb, 0)
        return found[0]

    def _win_activate(substring: str) -> bool:
        """Bring window to foreground: restore if minimized, raise Z-order, click title bar."""
        hwnd = _win_find_hwnd(substring)
        if not hwnd:
            print(f"[FOCUS] '{substring}' — window not found")
            return False
        _u32.ShowWindow(hwnd, 9)   # SW_RESTORE — unminimize if needed
        _u32.BringWindowToTop(hwnd)
        _u32.SetForegroundWindow(hwnd)
        return True

    def _win_focus_click(substring: str) -> bool:
        """No-op — focus is handled entirely by _win_activate."""
        return True

else:
    def _get_foreground_title() -> str:
        return ""

    def _win_find_hwnd(substring: str) -> int:
        return 0

    def _win_activate(substring: str) -> bool:
        return False

    def _win_focus_click(substring: str) -> bool:
        return False

import cv2
import mss
import numpy as np

from core import (
    MonitorConfig, load_config, send_pico_command, alert_beep,
    on_low_cp, on_low_hp, on_low_mp,
    on_low_pet_hp, on_low_pet_mp,
    get_ocr_engine,
    enemy_bar_empty, enemy_widget_is_player,
    make_capture_regions,
)

from ocr_thread import EventWatcher, WidgetOCRWorker

HOLD_RELEASE_BEFORE_SEC = 0.3  # Release hold key this many seconds before next attack cycle
STUCK_ATTACKS_REQUIRED = 2  # Consecutive attacks with no HP change before declaring stuck
LOW_HP_OCR_THRESHOLD = 4    # OCR readings below LOW_HP_OCR_PCT before flagging as tough enemy
LOW_HP_OCR_PCT = 0.50       # HP percentage considered "low" for tough-enemy detection

last_tab_time = 0.0


class _TeeWriter:
    """Writes to both the original stream and a log file."""
    def __init__(self, original, log_path: Path):
        self._original = original
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = logging.handlers.RotatingFileHandler(
            str(log_path), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        self._handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        self._logger = logging.getLogger("bot_tee")
        self._logger.setLevel(logging.DEBUG)
        self._logger.addHandler(self._handler)

    def write(self, text):
        self._original.write(text)
        if text and text.strip():
            self._logger.info(text.rstrip())

    def flush(self):
        self._original.flush()
        self._handler.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)



# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def render_preview(
    player_widget: np.ndarray, pet_widget: np.ndarray, e_widget: np.ndarray,
    cp: float, hp: float, mp: float,
    pet_hp: float, pet_mp: float,
    enemy_hp: float,
) -> tuple[int, int]:
    preview = player_widget.copy()
    h = preview.shape[0]

    labels = [
        (f"Player CP {cp:.1%}", (0, 215, 255)),
        (f"Player HP {hp:.1%}", (0, 0, 255)),
        (f"Player MP {mp:.1%}", (255, 150, 0)),
        (f"Pet    HP {pet_hp:.1%}", (0, 0, 255)),
        (f"Pet    MP {pet_mp:.1%}", (255, 150, 0)),
        (f"Enemy  HP {enemy_hp:.0%}" if enemy_hp >= 0 else "Enemy  HP --", (0, 180, 0)),
    ]

    spacer = np.full((h, 4, 3), 40, dtype=np.uint8)
    parts = [preview]
    if pet_widget.shape[0] > 0 and pet_widget.shape[1] > 0:
        parts += [spacer, cv2.resize(pet_widget, (pet_widget.shape[1], h))]
    if e_widget.shape[0] > 0 and e_widget.shape[1] > 0:
        parts += [spacer.copy(), cv2.resize(e_widget, (e_widget.shape[1], h))]
    combined = cv2.hconcat(parts)

    pad_h = len(labels) * 16 + 8
    padded = cv2.copyMakeBorder(combined, 0, pad_h, 0, 0, cv2.BORDER_CONSTANT, value=(30, 30, 30))
    ph = combined.shape[0]
    for i, (text, color) in enumerate(labels):
        cv2.putText(padded, text, (4, ph + 14 + i * 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.40, color, 1, cv2.LINE_AA)

    scaled = cv2.resize(padded, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_NEAREST)
    cv2.imshow("Monitor Preview", scaled)
    return scaled.shape[1], scaled.shape[0]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Player + Pet + Enemy monitor.")
    parser.add_argument("--no-preview", action="store_true", help="Disable preview window.")
    parser.add_argument("--pick-up", action="store_true", help="Press 7 (Pick Up) immediately after enemy dies, before TAB.")
    args = parser.parse_args()

    # Tee all print() output to a rotating log file
    sys.stdout = _TeeWriter(sys.stdout, _PROJECT_ROOT / "logs" / "bot.log")

    cfg = load_config()
    if args.no_preview:
        cfg.show_preview = False
    pick_up_mode: bool = args.pick_up

    # --- Event Watcher: monitors centre screen for popup keywords, auto-pauses bot ---
    _event_watcher = EventWatcher(interval=1.0, game_title=cfg.game_window_title,
                                      event_patterns=cfg.event_patterns)
    _event_watcher.start()
    last_event_check = 0.0
    EVENT_CHECK_SEC = 3.0

    # Player bars
    player_thresholds = [cfg.cp_threshold, cfg.hp_threshold, cfg.mp_threshold]
    player_alert_enabled = [cfg.alert_cp_enabled, cfg.alert_hp_enabled, cfg.alert_mp_enabled]
    player_actions = [on_low_cp, on_low_hp, on_low_mp]
    player_ratios = [1.0, 1.0, 1.0]
    player_last_alerts = [0.0, 0.0, 0.0]

    # Pet bars
    pet_thresholds = [cfg.pet_hp_threshold, cfg.pet_mp_threshold]
    pet_alert_enabled = [cfg.alert_pet_hp_enabled, cfg.alert_pet_mp_enabled]
    pet_ratios = [1.0, 1.0]
    pet_last_alerts = [0.0, 0.0]
    # Pet HP healing threshold is editable via settings
    pet_hp_heal_threshold = cfg.pet_hp_heal_threshold
    pet_hp_heal_cd = 0.0
    pet_last_hp_heal = 0.0
    pet_actions = [lambda r: on_low_pet_hp(r, cfg), on_low_pet_mp]
    pet_present: bool = True      # updated by periodic presence check
    last_pet_check: float = 0.0
    PET_PRESENCE_CHECK_SEC = 10.0

    # Scheduled skill presses
    last_press8 = 0.0
    last_press9 = 0.0
    last_press10 = 0.0
    _attack_2nd_at: float = 0.0          # timestamp to fire follow-up attack (0 = none pending)
    _buff_grace_until: float = 0.0       # suppress attack keypresses until this timestamp
    _last_buff_2nd_window: float = 0.0   # timestamp of last periodic secondary-window action
    _scryde_lock = threading.Lock()      # prevent concurrent window-switch threads

    def _do_switch_and_attack(target: str, game: str, key: str,
                              delay_sec: float | None = None,
                              stay_sec: float | None = None,
                              back_delay_sec: float | None = None) -> None:
        """Switch to target window, send key, switch back. Lock must be held by caller."""
        _delay = delay_sec if delay_sec is not None else cfg.focus_switch_delay_sec
        _stay  = stay_sec  if stay_sec  is not None else cfg.focus_switch_stay_sec
        _back  = back_delay_sec if back_delay_sec is not None else cfg.focus_switch_back_delay_sec
        print(f"\n[SCRYDE] Switching to '{target}'...")
        _win_activate(target)
        time.sleep(_delay)
        _win_focus_click(target)
        print(f"[SCRYDE] Foreground: '{_get_foreground_title()}' — sending {key}...")
        send_pico_command(cfg, key)
        time.sleep(_stay)
        if game:
            print(f"[SCRYDE] Switching back to '{game}'...")
            _win_activate(game)
            time.sleep(_back)
            _win_focus_click(game)

    def _run_focus_test():
        import traceback
        if not _scryde_lock.acquire(blocking=False):
            print("[SCRYDE] Skipped — previous switch still in progress")
            return
        _target = cfg.test_target_window
        _game   = cfg.focus_window_titles[0] if cfg.focus_window_titles else ""
        _did_tab = False
        try:
            _do_switch_and_attack(_target, _game, cfg.key_attack_2nd)
            # Check if enemy still alive
            time.sleep(0.2)
            with mss.mss() as _sct:
                _e_shot = np.array(_sct.grab(e_mon))[:, :, :3]
            if enemy_bar_empty(_e_shot, cfg)[0]:
                print(f"[SCRYDE] No target after switch — {cfg.key_next_target_near}")
                _send_near()
                _did_tab = True
        except Exception:
            traceback.print_exc()
        finally:
            _scryde_lock.release()  # release before polling so main loop isn't blocked

        if not _did_tab:
            return
        # Poll for new target — lock free so other threads aren't blocked
        _deadline = time.time() + 2.0
        while time.time() < _deadline:
            time.sleep(0.15)
            with mss.mss() as _sct2:
                _e_shot2 = np.array(_sct2.grab(e_mon))[:, :, :3]
            if not enemy_bar_empty(_e_shot2, cfg)[0]:
                if not _scryde_lock.acquire(blocking=False):
                    print("[SCRYDE] New target found but lock busy — main loop will handle")
                    return
                try:
                    print(f"[SCRYDE] New target locked — switching again")
                    _do_switch_and_attack(_target, _game, cfg.key_attack_2nd)
                except Exception:
                    traceback.print_exc()
                finally:
                    _scryde_lock.release()
                return
        print(f"[SCRYDE] No new target appeared within 2s")

    # Enemy state
    empty_frames: int = 0          # consecutive frames with bar empty
    EMPTY_FRAMES_REQUIRED = 2      # must be empty this many frames before switching
    self_target_frames: int = 0    # consecutive frames where player bar detected in enemy widget
    search_empty_frames: int = 0   # consecutive no-enemy frames in SEARCHING before pressing TAB
    SEARCH_EMPTY_FRAMES_REQUIRED = 6
    search_found_frames: int = 0   # consecutive alive-OCR frames in SEARCHING before transitioning
    SEARCH_FOUND_FRAMES_REQUIRED = 3

    # Enemy state machine
    SEARCHING, ATTACKING, IDLE = "searching", "attacking", "idle"
    e_state = SEARCHING
    e_hp: float = -1.0
    e_tab_attempts = 0
    e_max_tabs = 2
    e_last_attack = 0.0
    e_attack_cd = 0.0
    e_stuck_attacks = 0
    e_hp_at_attack: float = -1.0   # e_hp recorded at last press6
    low_hp_ocr_count: int = 0      # OCR readings below LOW_HP_OCR_PCT for current target
    tough_enemy: bool = False      # True once low_hp_ocr_count >= LOW_HP_OCR_THRESHOLD
    _last_count_ts: float = 0.0    # enemy_ts of last reading that incremented low_hp_ocr_count

    e_idle_since = 0.0
    _no_enemy_cycles: int = 0              # counts SEARCHING->IDLE transitions with no enemy found
    e_search_has_enemy_start: float = 0.0  # time when has_enemy first became True in SEARCHING
    e_last_ocr_success: float = 0.0        # time of last successful enemy OCR reading
    _attack_held: bool = False             # primary attack key currently held (hold mode)
    _attack_2nd_held: bool = False         # secondary attack key currently held (hold mode)
    _tab_near_held: bool = False           # next_target_near held (add_hold_shift mode)
    _tab_far_held: bool = False            # next_target_far held (add_hold_shift mode)
    e_bar_red: float = 0.0                 # most recent mean_red from enemy pixel check
    cfg.idle_resume_sec = 3.0

    if get_ocr_engine() is None:
        print("[ERROR] Install: pip install rapidocr-onnxruntime")
        return

    _ocr_worker = WidgetOCRWorker(cfg)
    _ocr_worker.start()

    global last_tab_time
    # Window position tracking — coordinates in settings are relative to game window client area
    _game_title = cfg.game_window_title or (cfg.focus_window_titles[0] if cfg.focus_window_titles else "")
    _last_win_pos_check = time.time()
    WIN_POS_CHECK_SEC = 1.0

    sct = mss.mss()
    try:
        player_mon, pet_mon, e_mon = make_capture_regions(cfg)
        _win_ox = player_mon["left"] - cfg.widget_left
        _win_oy = player_mon["top"]  - cfg.widget_top
        _ocr_worker.update_origin(_win_ox, _win_oy)
        if _game_title:
            print(f"[WINDOW] Tracking '{_game_title}' — client origin: ({_win_ox}, {_win_oy})")
        else:
            print("[WINDOW] No game_window_title set — using absolute coordinates from settings")

        player_shot = np.zeros((max(1, cfg.widget_height), max(1, cfg.widget_width), 3), dtype=np.uint8)
        pet_shot    = np.zeros((max(1, cfg.pet_height),    max(1, cfg.pet_width),    3), dtype=np.uint8)
        e_shot      = np.zeros((max(1, cfg.enemy_height),  max(1, cfg.enemy_width),  3), dtype=np.uint8)

        screen_h = sct.monitors[1]["height"]
        screen_w = sct.monitors[1]["width"]
        last_preview_update = 0.0
        preview_positioned = False
        PREVIEW_INTERVAL = 0.10  # seconds between preview redraws (~10 FPS)

        _user_paused  = [False]   # set by F4 — persists across focus changes
        _focus_paused = [False]   # set by focus-loss auto-pause
        _event_paused = [False]   # set by EventWatcher popup detection
        bot_paused    = [False]   # True when any of the above is True

        def _add_shift(press_cmd: str) -> str:
            """Insert 'shift+' modifier: 'press6' -> 'pressshift+6'."""
            return "press" + "shift+" + press_cmd[5:] if press_cmd.startswith("press") else press_cmd

        def _make_hold_cmd(press_cmd: str) -> str:
            """Convert 'press6' -> 'hold6' for hold mode."""
            return "hold" + press_cmd[5:] if press_cmd.startswith("press") else press_cmd

        def _release_holds() -> None:
            nonlocal _attack_held, _attack_2nd_held, _tab_near_held, _tab_far_held
            if _attack_held or _attack_2nd_held or _tab_near_held or _tab_far_held:
                send_pico_command(cfg, "releaseall")
                _attack_held = False
                _attack_2nd_held = False
                _tab_near_held = False
                _tab_far_held = False

        def _send_near() -> None:
            nonlocal _tab_near_held
            if cfg.add_hold_shift_next_target_near:
                send_pico_command(cfg, _make_hold_cmd(_add_shift(cfg.key_next_target_near)))
                _tab_near_held = True
            else:
                send_pico_command(cfg, _add_shift(cfg.key_next_target_near) if cfg.add_shift_next_target_near else cfg.key_next_target_near)

        def _send_far() -> None:
            nonlocal _tab_far_held
            if cfg.add_hold_shift_next_target_far:
                send_pico_command(cfg, _make_hold_cmd(_add_shift(cfg.key_next_target_far)))
                _tab_far_held = True
            else:
                send_pico_command(cfg, _add_shift(cfg.key_next_target_far) if cfg.add_shift_next_target_far else cfg.key_next_target_far)

        def _sync_paused():
            was_paused = bot_paused[0]
            bot_paused[0] = _user_paused[0] or _focus_paused[0] or _event_paused[0]
            if bot_paused[0] and not was_paused:
                _release_holds()

        def toggle_pause():
            _user_paused[0] = not _user_paused[0]
            if not _user_paused[0]:
                _event_paused[0] = False   # F4 resume also clears event pause
            _sync_paused()
            print(f"\n[BOT] {'PAUSED' if bot_paused[0] else 'RESUMED'} (F4)")

        def unpause():
            _user_paused[0] = False
            _event_paused[0] = False       # F5 clears event pause too
            _sync_paused()
            print(f"\n[BOT] RESUMED (F5)")

        def trigger_focus_test():
            import traceback
            try:
                print(f"[F6] trigger_focus_test called. WIN32={_WIN32_AVAILABLE} switch={cfg.test_focus_switch} target={cfg.test_target_window!r}")
                if not _WIN32_AVAILABLE:
                    print("[TEST] Cannot trigger: win32 not available")
                elif not cfg.test_focus_switch:
                    print("[TEST] Cannot trigger: test_focus_switch=False in settings")
                elif not cfg.test_target_window:
                    print("[TEST] Cannot trigger: test_target_window not set")
                else:
                    threading.Thread(target=_run_focus_test, daemon=True).start()
            except Exception:
                traceback.print_exc()

        # Ctrl+C: set a flag so the loop exits cleanly and cleanup code always runs.
        _ctrl_c_stop = threading.Event()
        _prev_sigint = signal.getsignal(signal.SIGINT)
        def _sigint_handler(sig, frame):
            print("\n[BOT] Ctrl+C — stopping...")
            _ctrl_c_stop.set()
        signal.signal(signal.SIGINT, _sigint_handler)

        # Hotkeys polled via GetAsyncKeyState in a dedicated thread — never missed
        # even when main loop is blocked on send_pico_command() TCP round-trips.
        _VK_F4, _VK_F5, _VK_F6 = 0x73, 0x74, 0x75
        _hotkey_stop = threading.Event()

        _main_loop_heartbeat = time.time()
        _WATCHDOG_TIMEOUT = 10.0  # force-exit if main loop stuck for this long

        def _hotkey_poll_thread():
            _prev = {_VK_F4: False, _VK_F5: False, _VK_F6: False}
            _bindings = ((_VK_F4, toggle_pause), (_VK_F5, unpause), (_VK_F6, trigger_focus_test))
            while not _hotkey_stop.is_set():
                if _WIN32_AVAILABLE:
                    for _vk, _cb in _bindings:
                        _dn = bool(_u32.GetAsyncKeyState(_vk) & 0x8000)
                        if _dn and not _prev[_vk]:
                            _cb()
                        _prev[_vk] = _dn
                # Watchdog: force-exit if main loop is stuck in native code
                if time.time() - _main_loop_heartbeat > _WATCHDOG_TIMEOUT:
                    print(f"\n[WATCHDOG] Main loop stuck for >{_WATCHDOG_TIMEOUT:.0f}s — force-exiting!")
                    os._exit(1)
                _hotkey_stop.wait(0.02)  # 20 ms poll — fast enough to catch any keypress

        threading.Thread(target=_hotkey_poll_thread, daemon=True, name="HotkeyPoll").start()

        print(f"[DEBUG] _WIN32_AVAILABLE={_WIN32_AVAILABLE}, test_focus_switch={cfg.test_focus_switch}, target={cfg.test_target_window!r}")
        _focus_check_enabled = bool(cfg.focus_window_titles) and _WIN32_AVAILABLE
        if _focus_check_enabled:
            print(f"Monitor started. Auto-pause on focus loss enabled (watching: {cfg.focus_window_titles}). F4=pause F5=resume Esc=stop.")
        else:
            print("Monitor started (Player + Pet + Enemy). Press F4 to pause/resume, F5 to resume. Press Ctrl+C or Esc to stop.")

        while True:
            _main_loop_heartbeat = time.time()

            # --- Window position tracking — update capture regions if window moved ---
            if _game_title and time.time() - _last_win_pos_check >= WIN_POS_CHECK_SEC:
                _last_win_pos_check = time.time()
                _new_player, _new_pet, _new_e = make_capture_regions(cfg)
                _new_ox = _new_player["left"] - cfg.widget_left
                _new_oy = _new_player["top"]  - cfg.widget_top
                if (_new_ox, _new_oy) != (_win_ox, _win_oy):
                    _win_ox, _win_oy = _new_ox, _new_oy
                    player_mon, pet_mon, e_mon = _new_player, _new_pet, _new_e
                    _ocr_worker.update_origin(_win_ox, _win_oy)
                    print(f"[WINDOW] Position updated: ({_win_ox}, {_win_oy})")

            # Read cached captures from background worker — main thread never calls sct.grab()
            _bar_empty, e_bar_red, _bar_ts = _ocr_worker.get_enemy_bar_pixel()
            _last_e_shot, _last_p_shot, _last_pt_shot = _ocr_worker.get_last_shots()
            if _last_e_shot is not None:
                e_shot = _last_e_shot

            preview_due = cfg.show_preview and (time.time() - last_preview_update >= PREVIEW_INTERVAL)
            if preview_due and _last_p_shot is not None:
                player_shot = _last_p_shot
            if preview_due and cfg.pet_enabled and cfg.pet_width > 0 and _last_pt_shot is not None:
                pet_shot = _last_pt_shot

            # --- Event Watcher check ---
            if time.time() - last_event_check >= EVENT_CHECK_SEC:
                last_event_check = time.time()
                if _event_watcher.is_event_found():
                    _event_paused[0] = True
                    _sync_paused()
                    print("\n[EVENT] Popup detected — bot paused. Press F4 to resume.")
                    _event_watcher.clear_event()

            # --- Focus check: auto-pause when game window loses focus ---
            if _focus_check_enabled:
                title = _get_foreground_title().lower()
                focused = any(t.lower() in title for t in cfg.focus_window_titles if t)
                if not focused and not _focus_paused[0]:
                    _focus_paused[0] = True
                    _sync_paused()
                    print("\n[FOCUS] Game lost focus — auto-paused")
                elif focused and _focus_paused[0]:
                    _focus_paused[0] = False
                    _sync_paused()  # respects _user_paused — F4 stays in effect
                    if not _user_paused[0]:
                        print("\n[FOCUS] Game regained focus — resumed")
                    else:
                        print("\n[FOCUS] Game regained focus — still paused (F4)")

            if bot_paused[0]:
                if preview_due:
                    win_w, win_h = render_preview(player_shot, pet_shot, e_shot, player_ratios[0], player_ratios[1], player_ratios[2], pet_ratios[0], pet_ratios[1], e_hp)
                    last_preview_update = time.time()
                    if not preview_positioned:
                        cv2.moveWindow("Monitor Preview", max(0, screen_w - win_w), max(0, screen_h - win_h - 148))
                        preview_positioned = True
                if _ctrl_c_stop.is_set() or cv2.waitKey(1) & 0xFF == 27:
                    break
                time.sleep(0.05)
                continue

            # ---- Scheduled skill presses (highest priority) ----
            now = time.time()
            if now - last_press10 >= cfg.buff3_interval_sec:
                print(f"\n[BUFF3] {cfg.key_buff3} ({cfg.buff3_interval_sec:.0f}s)")
                send_pico_command(cfg, _add_shift(cfg.key_buff3) if cfg.add_shift_buff3 else cfg.key_buff3)
                last_press10 = now
                if cfg.buff3_grace_sec > 0:
                    _buff_grace_until = max(_buff_grace_until, now + cfg.buff3_grace_sec)
            if now - last_press9 >= cfg.buff2_interval_sec:
                print(f"\n[BUFF2] {cfg.key_buff2} ({cfg.buff2_interval_sec:.0f}s)")
                send_pico_command(cfg, _add_shift(cfg.key_buff2) if cfg.add_shift_buff2 else cfg.key_buff2)
                last_press9 = now
                if cfg.buff2_grace_sec > 0:
                    _buff_grace_until = max(_buff_grace_until, now + cfg.buff2_grace_sec)
            if now - last_press8 >= cfg.buff1_interval_sec:
                print(f"\n[BUFF1] {cfg.key_buff1} ({cfg.buff1_interval_sec:.0f}s)")
                send_pico_command(cfg, _add_shift(cfg.key_buff1) if cfg.add_shift_buff1 else cfg.key_buff1)
                last_press8 = now
                if cfg.buff1_grace_sec > 0:
                    _buff_grace_until = max(_buff_grace_until, now + cfg.buff1_grace_sec)

            # ---- Periodic secondary-window action ----
            if (cfg.test_focus_switch and _WIN32_AVAILABLE
                    and cfg.buff_2nd_window_interval_sec > 0
                    and now - _last_buff_2nd_window >= cfg.buff_2nd_window_interval_sec):
                _last_buff_2nd_window = now
                _pk = _add_shift(cfg.key_buff_2nd_window) if cfg.add_shift_buff_2nd_window else cfg.key_buff_2nd_window
                _pt = cfg.test_target_window
                _pg = cfg.focus_window_titles[0] if cfg.focus_window_titles else ""
                print(f"\n[BUFF 2ND WIN] {_pk} ({cfg.buff_2nd_window_interval_sec:.0f}s)")
                def _run_periodic(_pk=_pk, _pt=_pt, _pg=_pg):
                    if not _scryde_lock.acquire(blocking=False):
                        print("[SCRYDE PERIODIC] Skipped — switch in progress")
                        return
                    try:
                        _do_switch_and_attack(_pt, _pg, _pk,
                                              delay_sec=cfg.buff_2nd_window_delay_sec,
                                              stay_sec=cfg.buff_2nd_window_stay_sec,
                                              back_delay_sec=cfg.focus_switch_back_delay_sec)
                    except Exception:
                        import traceback; traceback.print_exc()
                    finally:
                        _scryde_lock.release()
                threading.Thread(target=_run_periodic, daemon=True).start()

            # ---- 2nd attack follow-up ----
            if cfg.key_attack_2nd_enabled and _attack_2nd_at > 0 and now >= _attack_2nd_at:
                _attack_2nd_at = 0.0
                if now >= _buff_grace_until:
                    if cfg.key_attack_2nd_hold:
                        if not _attack_2nd_held:
                            print(f"[ACTION] 2nd attack hold: {cfg.key_attack_2nd}")
                            send_pico_command(cfg, _make_hold_cmd(_add_shift(cfg.key_attack_2nd) if cfg.add_hold_shift else cfg.key_attack_2nd))
                            _attack_2nd_held = True
                    else:
                        print(f"[ACTION] 2nd attack: {cfg.key_attack_2nd}")
                        send_pico_command(cfg, _add_shift(cfg.key_attack_2nd) if cfg.add_shift else cfg.key_attack_2nd)
                else:
                    print(f"[GRACE] 2nd attack suppressed ({_buff_grace_until - now:.2f}s remaining)")

            now = time.time()
            if e_state == ATTACKING:
                # Release holds before next cycle so they can be re-applied cleanly
                _any_attack_hold = (cfg.key_attack_hold and _attack_held) or (cfg.key_attack_2nd_hold and _attack_2nd_held)
                if _any_attack_hold and e_last_attack > 0 and now >= e_last_attack + e_attack_cd - HOLD_RELEASE_BEFORE_SEC:
                    send_pico_command(cfg, "releaseall")
                    _attack_held = False
                    _attack_2nd_held = False
                if (now - e_last_attack) >= e_attack_cd:
                    if now < _buff_grace_until:
                        pass  # grace active: wait silently, attack fires immediately when grace ends
                    else:
                        # Stuck detection: HP unchanged across consecutive attacks.
                        # Only counts when OCR gave a fresh reading recently (stale data would false-trigger).
                        ocr_recent = (now - e_last_ocr_success) < e_attack_cd * 2
                        if ocr_recent and e_hp_at_attack >= 0 and e_hp >= 0 and abs(e_hp - e_hp_at_attack) < 0.005:
                            e_stuck_attacks += 1
                            print(f"[STUCK] HP unchanged at {e_hp:.0%} (x{e_stuck_attacks}/{STUCK_ATTACKS_REQUIRED})")
                        else:
                            e_stuck_attacks = 0
                        if e_hp >= 0:
                            e_hp_at_attack = e_hp

                        if e_stuck_attacks >= STUCK_ATTACKS_REQUIRED:
                            print(f"\n[STUCK] Enemy unreachable — {cfg.key_next_target_far} to switch target")
                            _release_holds()
                            _send_far()
                            last_tab_time = now
                            e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0
                            e_last_attack, e_attack_cd = now, random.uniform(cfg.attack_cd_min, cfg.attack_cd_max)
                            e_stuck_attacks, e_hp_at_attack = 0, -1.0
                            empty_frames = 0
                            search_empty_frames = 0
                            search_found_frames = 0
                            low_hp_ocr_count, tough_enemy, _last_count_ts = 0, False, 0.0
                        else:
                            if cfg.key_attack_hold:
                                if not _attack_held:
                                    print(f"\n[ENEMY] Re-A hold start: {cfg.key_attack}")
                                    send_pico_command(cfg, _make_hold_cmd(_add_shift(cfg.key_attack) if cfg.add_hold_shift else cfg.key_attack))
                                    _attack_held = True
                                else:
                                    print(f"\n[ENEMY] Re-A (hold active)")
                            else:
                                print(f"\n[ENEMY] Re-A ({e_hp:.0%})")
                                send_pico_command(cfg, _add_shift(cfg.key_attack) if cfg.add_shift else cfg.key_attack)
                            if cfg.key_attack_2nd_enabled:
                                _attack_2nd_at = now + random.uniform(cfg.attack_2nd_delay_min, cfg.attack_2nd_delay_max)
                            e_last_attack, e_attack_cd = now, random.uniform(cfg.attack_cd_min, cfg.attack_cd_max)

            # ---- Enemy detection ----
            # Self-target guard: checked BEFORE enemy_bar_empty because the player
            # CP bar (R/G ≈ 1.29) fails the enemy-red R/G threshold and would make
            # has_enemy = False, leaving it undetected. Run unconditionally every frame.
            # Requires EMPTY_FRAMES_REQUIRED consecutive detections to avoid
            # acting on single-frame false positives during targeting transitions.
            if enemy_widget_is_player(e_shot):
                self_target_frames += 1
                if self_target_frames >= EMPTY_FRAMES_REQUIRED and now - last_tab_time >= cfg.tab_cooldown_sec:
                    print(f"\n[SELF-TARGET] Player bar detected x{self_target_frames} — TAB to change target")
                    _release_holds()
                    _send_near()
                    now = time.time()
                    last_tab_time = now
                    e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0
                    e_last_attack, e_stuck_attacks, e_hp_at_attack = 0.0, 0, -1.0
                    empty_frames = 0
                    self_target_frames = 0
                    search_empty_frames = 0
                    search_found_frames = 0
                    low_hp_ocr_count, tough_enemy, _last_count_ts = 0, False, 0.0
                    e_search_has_enemy_start = 0.0
                    e_last_ocr_success = 0.0
                has_enemy = False
            else:
                self_target_frames = 0
                # Primary: pixel check from background worker cache (never blocks main thread).
                # At low HP the bar is thin and can look empty while alive; OCR overrides below.
                has_enemy = not _bar_empty
            if has_enemy:
                _, _, hp_str = _ocr_worker.get_enemy()
            else:
                hp_str = None
            now = time.time()

            # Parse hp_str -> float for combat logic (more reliable: requires % sign)
            hp_str_val: float | None = None
            if hp_str is not None:
                try:
                    hp_str_val = float(hp_str.strip().replace('%', '').replace(' ', '')) / 100.0
                except ValueError:
                    print(f"[ENEMY PARSE] hp_str={hp_str!r} -> ValueError, hp_str_val remains None")

            # Update e_hp from hp_str (primary source)
            prev_e_hp = e_hp
            prev_ocr_ts = e_last_ocr_success  # timestamp when prev_e_hp was last set from OCR
            if hp_str_val is not None:
                e_hp = hp_str_val
                _worker_ts = _ocr_worker.get_enemy_ts()
                e_last_ocr_success = _worker_ts
                if _worker_ts > _last_count_ts:      # only count each new OCR cycle once
                    _last_count_ts = _worker_ts
                    if hp_str_val < LOW_HP_OCR_PCT:
                        low_hp_ocr_count += 1
                        if low_hp_ocr_count >= LOW_HP_OCR_THRESHOLD and not tough_enemy:
                            tough_enemy = True
                            print(f"[TOUGH] Enemy flagged as tough ({low_hp_ocr_count} readings below {LOW_HP_OCR_PCT:.0%})")

            print(f"[ENEMY] state={e_state} | has_enemy={has_enemy} hp_str={hp_str!r} hp_str_val={f'{hp_str_val:.0%}' if hp_str_val is not None else None} e_hp={f'{e_hp:.0%}' if e_hp >= 0 else '--'}")

            if bot_paused[0]:
                continue
            if e_state == SEARCHING:
                # Release tab holds before next cycle so they can be re-applied cleanly
                if (_tab_near_held or _tab_far_held) and now >= last_tab_time + cfg.tab_cooldown_sec - HOLD_RELEASE_BEFORE_SEC:
                    send_pico_command(cfg, "releaseall")
                    _tab_near_held = False
                    _tab_far_held = False
                if has_enemy:
                    search_empty_frames = 0
                    # Trigger window switch immediately on first pixel detection — don't wait for OCR
                    if e_search_has_enemy_start == 0.0:
                        e_search_has_enemy_start = now
                        if cfg.test_focus_switch and _WIN32_AVAILABLE:
                            threading.Thread(target=_run_focus_test, daemon=True).start()

                    # If enemy bar is present, do not press TAB, even if OCR fails
                    if hp_str_val is not None and hp_str_val > 0.01:
                        search_found_frames += 1
                        if search_found_frames >= SEARCH_FOUND_FRAMES_REQUIRED:
                            print(f"\n[STATE] SEARCHING -> ATTACKING (hp_str_val={hp_str_val:.0%}, confirmed x{search_found_frames})")
                            e_state, e_tab_attempts = ATTACKING, 0
                            empty_frames = 0
                            search_empty_frames = 0
                            search_found_frames = 0
                            e_search_has_enemy_start = 0.0
                            _no_enemy_cycles = 0
                            e_hp_at_attack = -1.0  # don't seed until first Re-A (avoids free stuck +1)
                            last_tab_time = now
                            if e_last_attack == 0.0:
                                # No TAB was pressed; fire initial attack now
                                if now >= _buff_grace_until:
                                    if cfg.key_attack_hold:
                                        if not _attack_held:
                                            send_pico_command(cfg, _make_hold_cmd(_add_shift(cfg.key_attack) if cfg.add_hold_shift else cfg.key_attack))
                                            _attack_held = True
                                    else:
                                        send_pico_command(cfg, _add_shift(cfg.key_attack) if cfg.add_shift else cfg.key_attack)
                                    if cfg.key_attack_2nd_enabled:
                                        _attack_2nd_at = now + random.uniform(cfg.attack_2nd_delay_min, cfg.attack_2nd_delay_max)
                                else:
                                    print(f"[GRACE] Initial attack suppressed ({_buff_grace_until - now:.2f}s remaining)")
                                e_last_attack, e_attack_cd = now, random.uniform(cfg.attack_cd_min, cfg.attack_cd_max)
                            # else: TAB already set the cooldown; Re-A block sends first press or hold
                    else:
                        search_found_frames = 0
                        # OCR unavailable or reads 0% — pixel confirms enemy is alive, wait for 3s timeout

                        if now - e_search_has_enemy_start >= 3.0:
                            print(f"\n[STATE] SEARCHING -> ATTACKING (OCR timeout, bar visible 3s)")
                            e_state, e_tab_attempts = ATTACKING, 0
                            empty_frames = 0
                            search_empty_frames = 0
                            e_search_has_enemy_start = 0.0
                            e_hp_at_attack = -1.0  # don't seed until first Re-A
                            last_tab_time = now
                            if e_last_attack == 0.0:
                                if now >= _buff_grace_until:
                                    if cfg.key_attack_hold:
                                        if not _attack_held:
                                            send_pico_command(cfg, _make_hold_cmd(_add_shift(cfg.key_attack) if cfg.add_hold_shift else cfg.key_attack))
                                            _attack_held = True
                                    else:
                                        send_pico_command(cfg, _add_shift(cfg.key_attack) if cfg.add_shift else cfg.key_attack)
                                    if cfg.key_attack_2nd_enabled:
                                        _attack_2nd_at = now + random.uniform(cfg.attack_2nd_delay_min, cfg.attack_2nd_delay_max)
                                else:
                                    print(f"[GRACE] Initial attack suppressed ({_buff_grace_until - now:.2f}s remaining)")
                                e_last_attack, e_attack_cd = now, random.uniform(cfg.attack_cd_min, cfg.attack_cd_max)
                else:
                    e_search_has_enemy_start = 0.0
                    search_found_frames = 0
                    search_empty_frames += 1
                    if now - last_tab_time >= cfg.tab_cooldown_sec and search_empty_frames >= SEARCH_EMPTY_FRAMES_REQUIRED:
                        e_tab_attempts += 1
                        if e_tab_attempts > e_max_tabs:
                            _no_enemy_cycles += 1
                            print(f"\n[STATE] SEARCHING -> IDLE ({e_max_tabs} TABs failed, no-enemy cycle {_no_enemy_cycles}/{cfg.max_no_enemy_cycles})")
                            alert_beep()
                            _send_far()
                            now = time.time()
                            last_tab_time = now
                            if _no_enemy_cycles >= cfg.max_no_enemy_cycles:
                                print(f"[BOT] No enemy found after {cfg.max_no_enemy_cycles} search cycles — auto-pausing. Press F5 to resume.")
                                _event_paused[0] = True
                                _sync_paused()
                                _no_enemy_cycles = 0
                            e_state = IDLE
                            e_idle_since = now
                        else:
                            print(f"\n[ENEMY] TAB ({e_tab_attempts}/{e_max_tabs})")
                            _send_near()
                            now = time.time()
                            last_tab_time = now
                            search_found_frames = 0
                            e_last_attack, e_attack_cd = now, random.uniform(cfg.attack_cd_min, cfg.attack_cd_max)

            elif e_state == ATTACKING:
                if not has_enemy:
                    empty_frames += 1
                else:
                    empty_frames = 0
                if not has_enemy and empty_frames >= EMPTY_FRAMES_REQUIRED:
                    do_switch = True
                    if tough_enemy:
                        _, ocr_hp, _ = _ocr_worker.get_enemy()
                        if ocr_hp is not None and ocr_hp > 0.01:
                            print(f"[TOUGH] pixel empty x{empty_frames} but OCR={ocr_hp:.0%} — still alive, resetting")
                            empty_frames = 0
                            do_switch = False
                        else:
                            print(f"[TOUGH] pixel empty x{empty_frames}, OCR={ocr_hp} — confirmed dead")
                    if do_switch:
                        _release_holds()
                        print(f"\n[STATE] ATTACKING -> SEARCHING (pixel bar empty x{empty_frames})")
                        if pick_up_mode:
                            print(f"[PICK_UP] {cfg.key_pick_up}")
                            send_pico_command(cfg, _add_shift(cfg.key_pick_up) if cfg.add_shift_pick_up else cfg.key_pick_up)
                            pick_up_delay = random.uniform(0.5, 0.7)
                            last_tab_time = now - cfg.tab_cooldown_sec + pick_up_delay
                            print(f"[PICK_UP] TAB delayed {pick_up_delay:.2f}s")
                        else:
                            last_tab_time = now - cfg.tab_cooldown_sec
                        e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0
                        e_last_attack, e_stuck_attacks, e_hp_at_attack = 0.0, 0, -1.0
                        empty_frames = 0
                        self_target_frames = 0
                        search_empty_frames = 0
                        search_found_frames = 0
                        low_hp_ocr_count, tough_enemy, _last_count_ts = 0, False, 0.0
                        e_search_has_enemy_start = 0.0
                        e_last_ocr_success = 0.0
                elif has_enemy and hp_str_val is not None and prev_e_hp >= 0 and hp_str_val > prev_e_hp + 0.20 and now - prev_ocr_ts < 2.0:
                    print(f"\n[ENEMY] Target change detected ({prev_e_hp:.0%} -> {hp_str_val:.0%}) — re-engaging")
                    if now >= _buff_grace_until:
                        if cfg.key_attack_hold:
                            if not _attack_held:
                                send_pico_command(cfg, _make_hold_cmd(_add_shift(cfg.key_attack) if cfg.add_hold_shift else cfg.key_attack))
                                _attack_held = True
                        else:
                            send_pico_command(cfg, _add_shift(cfg.key_attack) if cfg.add_shift else cfg.key_attack)
                        if cfg.key_attack_2nd_enabled and not cfg.key_attack_2nd_hold:
                            _attack_2nd_at = now + random.uniform(cfg.attack_2nd_delay_min, cfg.attack_2nd_delay_max)
                    else:
                        print(f"[GRACE] Initial attack suppressed ({_buff_grace_until - now:.2f}s remaining)")
                    e_last_attack, e_attack_cd = now, random.uniform(cfg.attack_cd_min, cfg.attack_cd_max)

            elif e_state == IDLE:
                if has_enemy and hp_str_val is not None and hp_str_val > 0.01:
                    search_found_frames += 1
                    if search_found_frames >= SEARCH_FOUND_FRAMES_REQUIRED:
                        print(f"\n[STATE] IDLE -> ATTACKING (hp_str_val={hp_str_val:.0%}, confirmed x{search_found_frames})")
                        _no_enemy_cycles = 0
                        if cfg.test_focus_switch and _WIN32_AVAILABLE:
                            threading.Thread(target=_run_focus_test, daemon=True).start()
                        e_state, e_tab_attempts = ATTACKING, 0
                        empty_frames = 0
                        search_found_frames = 0
                        e_hp_at_attack = -1.0  # don't seed until first Re-A (avoids free stuck +1)
                        e_last_attack = 0.0    # reset so initial attack block fires (not stale Re-A)
                        last_tab_time = now
                        if e_last_attack == 0.0:
                            if now >= _buff_grace_until:
                                if cfg.key_attack_hold:
                                    if not _attack_held:
                                        send_pico_command(cfg, _make_hold_cmd(_add_shift(cfg.key_attack) if cfg.add_hold_shift else cfg.key_attack))
                                        _attack_held = True
                                else:
                                    send_pico_command(cfg, _add_shift(cfg.key_attack) if cfg.add_shift else cfg.key_attack)
                                if cfg.key_attack_2nd_enabled:
                                    _attack_2nd_at = now + random.uniform(cfg.attack_2nd_delay_min, cfg.attack_2nd_delay_max)
                            else:
                                print(f"[GRACE] Initial attack suppressed ({_buff_grace_until - now:.2f}s remaining)")
                            e_last_attack, e_attack_cd = now, random.uniform(cfg.attack_cd_min, cfg.attack_cd_max)
                elif now - e_idle_since >= cfg.idle_resume_sec:
                    print(f"\n[STATE] IDLE -> SEARCHING (timeout {cfg.idle_resume_sec}s — resuming search)")
                    _send_far()
                    last_tab_time = now
                    e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0
                    low_hp_ocr_count, tough_enemy, _last_count_ts = 0, False, 0.0
                    search_empty_frames = 0
                    search_found_frames = 0
                    e_search_has_enemy_start = 0.0
                else:
                    search_found_frames = 0  # no enemy or OCR unavailable — reset confirmation

            # ---- Player OCR ----
            player_ocr = _ocr_worker.get_player()
            for i in range(min(len(player_ocr), 3)):
                player_ratios[i] = player_ocr[i]
            if bot_paused[0]:
                continue
            now = time.time()
            for i in range(3):
                if player_alert_enabled[i] and player_ratios[i] < player_thresholds[i] and (now - player_last_alerts[i]) >= cfg.alert_cooldown_sec:
                    alert_beep()
                    player_actions[i](player_ratios[i])
                    player_last_alerts[i] = now

            # Stop if Player HP hits 0
            if player_ratios[1] <= 0.0:
                print(f"\n[PLAYER] HP is 0% \u2014 stopping script.")
                alert_beep()
                break

            # ---- Pet OCR ----
            pet_ocr = _ocr_worker.get_pet()
            now = time.time()
            # Periodic presence check every PET_PRESENCE_CHECK_SEC
            if now - last_pet_check >= PET_PRESENCE_CHECK_SEC:
                last_pet_check = now
                new_present = len(pet_ocr) >= 2
                if new_present != pet_present:
                    pet_present = new_present
                    print(f"\n[PET] Widget {'detected' if pet_present else 'not found'} — reactions {'enabled' if pet_present else 'disabled'}")
            if not bot_paused[0] and pet_present:
                for i in range(min(len(pet_ocr), 2)):
                    pet_ratios[i] = pet_ocr[i]
                for i in range(2):
                    if pet_alert_enabled[i] and pet_ratios[i] < pet_thresholds[i] and (now - pet_last_alerts[i]) >= cfg.alert_cooldown_sec:
                        alert_beep()
                        pet_actions[i](pet_ratios[i])
                        pet_last_alerts[i] = now
                # Pet HP heal (press1) when HP < threshold
                if cfg.alert_pet_hp_heal_enabled and pet_ratios[0] < pet_hp_heal_threshold and (now - pet_last_hp_heal) >= pet_hp_heal_cd:
                    print(f"\n[PET] HP {pet_ratios[0]:.0%} < {pet_hp_heal_threshold:.0%} — {cfg.key_heal_pet}")
                    send_pico_command(cfg, _add_shift(cfg.key_heal_pet) if cfg.add_shift_heal_pet else cfg.key_heal_pet)
                    pet_last_hp_heal, pet_hp_heal_cd = now, random.uniform(3.0, 3.5)
                    pet_last_alerts[0] = now + pet_hp_heal_cd - cfg.alert_cooldown_sec  # suppress alert during heal animation

            # ---- Status line ----
            cp_r, hp_r, mp_r = player_ratios
            pet_hp_r, pet_mp_r = pet_ratios
            e_str = f"{e_hp:.1%}" if e_hp >= 0 else "--"
            print(f"\rPlayer CP:{cp_r:.0%} HP:{hp_r:.0%} MP:{mp_r:.0%}  Pet HP:{pet_hp_r:.0%} MP:{pet_mp_r:.0%}  Enemy HP:{e_str}   ", end="")


            if preview_due:
                win_w, win_h = render_preview(player_shot, pet_shot, e_shot, cp_r, hp_r, mp_r, pet_hp_r, pet_mp_r, e_hp)
                last_preview_update = time.time()
                if not preview_positioned:
                    cv2.moveWindow("Monitor Preview", max(0, screen_w - win_w), max(0, screen_h - win_h - 148))
                    preview_positioned = True
            if _ctrl_c_stop.is_set() or cv2.waitKey(1) & 0xFF == 27:
                break

    finally:
        sct.close()
    try:
        _release_holds()  # ensure no attack keys are stuck held on exit
        _hotkey_stop.set()
        _ocr_worker.stop()
        _event_watcher.stop()
        if cfg.show_preview:
            cv2.destroyAllWindows()
    finally:
        signal.signal(signal.SIGINT, _prev_sigint)  # always restore default handler


if __name__ == "__main__":
    main()
