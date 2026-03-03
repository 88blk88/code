"""Background OCR — monitors the centre of the screen for event popup keywords."""

import ctypes
import sys
import threading
import time
import winsound
from dataclasses import dataclass, field
from pathlib import Path

import cv2

import mss
import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None

from core import (
    _preprocess,
    ocr_full_widget, ocr_pet_widget, ocr_enemy_widget,
    MonitorConfig,
)


# ---------------------------------------------------------------------------
# Shared state for WidgetOCRWorker
# ---------------------------------------------------------------------------

@dataclass
class _WidgetOCRState:
    player_ratios: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    player_ts:    float          = 0.0   # timestamp of last successful player OCR
    pet_ratios:    list[float] = field(default_factory=lambda: [1.0, 1.0])
    pet_ts:       float          = 0.0   # timestamp of last successful pet OCR
    enemy_has:    bool           = False
    enemy_hp:     float | None   = None
    enemy_hp_str: str | None     = None
    enemy_ts:     float          = 0.0   # timestamp of last successful enemy OCR


class EventWatcher:
    """
    Captures the centre of the game window and runs OCR in a daemon background
    thread.  When any EVENT_PATTERNS phrase is detected, sets an internal flag
    that the main loop can poll via is_event_found().

    The region is recalculated every scan so it tracks window movement.
    Falls back to the primary monitor centre if the window is not found.
    """

    def __init__(self, interval: float = 1.0, game_title: str = "",
                 event_patterns: list[str] | None = None):
        self._interval = interval
        self._game_title = game_title
        self._event_patterns = event_patterns or []

        self._event_found = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._alert_trigger = threading.Event()   # set once on first detection

        self._thread = threading.Thread(
            target=self._run, daemon=True, name="EventWatcher"
        )
        self._alert_thread = threading.Thread(
            target=self._alert_loop, daemon=True, name="EventAlert"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background watcher thread."""
        if RapidOCR is None:
            print("[EventWatcher] rapidocr_onnxruntime not installed — watcher disabled.")
            return
        self._thread.start()
        self._alert_thread.start()
        print(f"[EventWatcher] Started. Tracking window: '{self._game_title or 'monitor centre'}'")

    def stop(self) -> None:
        """Signal the threads to stop and wait for them to exit."""
        self._stop_event.set()
        self._alert_trigger.set()   # unblock alert_loop if still waiting for first event
        self._thread.join(timeout=5.0)
        self._alert_thread.join(timeout=2.0)
        print("[EventWatcher] Stopped.")

    def is_event_found(self) -> bool:
        """Return True if a popup keyword was detected since the last clear_event()."""
        with self._lock:
            return self._event_found

    def clear_event(self) -> None:
        """Reset the flag after the main loop has acted on the event."""
        with self._lock:
            self._event_found = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _calc_region(self) -> dict:
        """Return the centre 30% of the game window client area, or monitor centre fallback."""
        if sys.platform == "win32" and self._game_title:
            hwnd = self._find_hwnd(self._game_title)
            if hwnd:
                u32 = ctypes.windll.user32
                import ctypes.wintypes as _wt
                pt = _wt.POINT(0, 0)
                u32.ClientToScreen(hwnd, ctypes.byref(pt))
                cr = _wt.RECT()
                u32.GetClientRect(hwnd, ctypes.byref(cr))
                cw = cr.right - cr.left
                ch = cr.bottom - cr.top
                rw = int(cw * 0.30)
                rh = int(ch * 0.30)
                rx = pt.x + (cw - rw) // 2
                ry = pt.y + (ch - rh) // 2
                # Expand upward so upper-centre popups are caught too
                ry = ry - rh
                rh = rh * 2
                return {"left": rx, "top": ry, "width": rw, "height": rh}
        # Fallback: primary monitor centre
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        sw, sh = mon["width"], mon["height"]
        w, h = int(sw * 0.20), int(sh * 0.20)
        x = mon["left"] + (sw - w) // 2
        y = mon["top"] + (sh - h) // 2 - h
        return {"left": x, "top": y, "width": w, "height": h * 2}

    @staticmethod
    def _find_hwnd(title: str) -> int:
        found = [0]
        u32 = ctypes.windll.user32
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_long)
        def cb(hwnd, _):
            if u32.IsWindowVisible(hwnd):
                n = u32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u32.GetWindowTextW(hwnd, buf, n + 1)
                    if title.lower() in buf.value.lower():
                        found[0] = hwnd
                        return False
            return True
        u32.EnumWindows(cb, 0)
        return found[0]

    def _run(self) -> None:
        if RapidOCR is None:
            return
        engine = RapidOCR()
        print("[EventWatcher] Engine ready.")

        _event_save_index = 0

        with mss.mss() as sct:
            while not self._stop_event.is_set():
                try:
                    region = self._calc_region()
                    shot = np.array(sct.grab(region))[:, :, :3]
                    text = self._do_ocr(engine, shot)
                    if text:
                        print(f"[EventWatcher] OCR: {text!r}")
                        matched = self._check_patterns(text)
                        if matched:
                            _event_save_index += 1
                            path = str(_PROJECT_ROOT / f"event_match_{_event_save_index}.png")
                            cv2.imwrite(path, shot)
                            print(f"[EventWatcher] Pattern matched: {matched!r} — saved {path}")
                            with self._lock:
                                self._event_found = True
                            self._alert_trigger.set()
                except Exception as exc:
                    print(f"[EventWatcher] Error: {exc}")

                self._stop_event.wait(self._interval)

        print("[EventWatcher] Loop exited.")

    def _alert_loop(self) -> None:
        """Play obnoxious repeating alarm sounds from first detection until bot stops."""
        self._alert_trigger.wait()      # block until a pattern is first matched
        if self._stop_event.is_set():   # triggered by stop(), not a real event
            return
        print("[EventWatcher] ALARM: playing sounds until bot stops.")
        # Siren pattern: rapid high/low alternation followed by a sustained tone
        sequence = [
            (2800, 150), (400, 150),
            (2800, 150), (400, 150),
            (2800, 150), (400, 150),
            (3000, 150),
        ]
        while not self._stop_event.is_set():
            for freq, dur in sequence:
                if self._stop_event.is_set():
                    return
                winsound.Beep(freq, dur)
            self._stop_event.wait(0.4)  # short pause between cycles

    def _do_ocr(self, engine, bgr_widget: np.ndarray) -> str:
        """Return concatenated OCR text from the first variant that gives output."""
        for image in _preprocess(bgr_widget):
            try:
                result, _ = engine(image)
            except Exception:
                continue
            if not result:
                continue
            combined = " ".join(e[1] for e in result if len(e) >= 2)
            if combined.strip():
                return combined
        return ""

    def _check_patterns(self, text: str) -> str | None:
        """Return the first matching pattern (case-insensitive), or None."""
        lower = text.lower()
        for pattern in self._event_patterns:
            if pattern.lower() in lower:
                return pattern
        return None


# ---------------------------------------------------------------------------
# WidgetOCRWorker
# ---------------------------------------------------------------------------

class WidgetOCRWorker:
    """Background daemon: grabs widget screenshots and runs OCR continuously.
    Main loop reads cached results via get_player(), get_pet(), get_enemy().
    Owns its own mss context — no screenshots are passed from the main loop.
    """

    def __init__(self, cfg: MonitorConfig,
                 ox: int = 0, oy: int = 0,
                 player_interval: float = 0.60,
                 pet_interval:    float = 0.60,
                 enemy_interval:  float = 0.20):
        self._cfg = cfg
        self._ox, self._oy = ox, oy
        self._origin_lock = threading.Lock()
        self._pi, self._peti, self._ei = player_interval, pet_interval, enemy_interval
        self._state = _WidgetOCRState()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="WidgetOCRWorker")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()
        print("[WidgetOCRWorker] Started.")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)
        print("[WidgetOCRWorker] Stopped.")

    def update_origin(self, ox: int, oy: int) -> None:
        """Update window origin offset so capture regions track window movement."""
        with self._origin_lock:
            self._ox, self._oy = ox, oy

    def get_player(self) -> list[float]:
        with self._lock:
            return list(self._state.player_ratios)

    def get_player_with_ts(self) -> tuple[list[float], float]:
        """Atomically return (player_ratios, timestamp)."""
        with self._lock:
            return list(self._state.player_ratios), self._state.player_ts

    def get_pet(self) -> list[float]:
        with self._lock:
            return list(self._state.pet_ratios)

    def get_pet_with_ts(self) -> tuple[list[float], float]:
        """Atomically return (pet_ratios, timestamp)."""
        with self._lock:
            return list(self._state.pet_ratios), self._state.pet_ts

    def get_enemy(self) -> tuple[bool, float | None, str | None]:
        with self._lock:
            return self._state.enemy_has, self._state.enemy_hp, self._state.enemy_hp_str

    def get_enemy_ts(self) -> float:
        with self._lock:
            return self._state.enemy_ts

    def get_enemy_with_ts(self) -> tuple[bool, float | None, str | None, float]:
        """Atomically return (has_enemy, hp, hp_str, timestamp)."""
        with self._lock:
            return self._state.enemy_has, self._state.enemy_hp, self._state.enemy_hp_str, self._state.enemy_ts

    def clear_enemy(self) -> None:
        """Reset cached enemy data (call when target is confirmed dead/lost)."""
        with self._lock:
            self._state.enemy_has = False
            self._state.enemy_hp = None
            self._state.enemy_hp_str = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        cfg = self._cfg

        def _make_mons(ox: int, oy: int):
            return (
                {"left": cfg.widget_left + ox, "top": cfg.widget_top + oy,
                 "width": cfg.widget_width, "height": cfg.widget_height},
                {"left": cfg.pet_left + ox,    "top": cfg.pet_top + oy,
                 "width": cfg.pet_width,        "height": cfg.pet_height},
                {"left": cfg.enemy_left + ox,  "top": cfg.enemy_top + oy,
                 "width": cfg.enemy_width,      "height": cfg.enemy_height},
            )

        with self._origin_lock:
            cur_ox, cur_oy = self._ox, self._oy
        p_mon, pt_mon, e_mon = _make_mons(cur_ox, cur_oy)

        now = time.time()
        next_p, next_pt, next_e = now, now + 0.30, now   # stagger player vs pet by 0.3s
        _pet_pending_count = 0  # consecutive readings awaiting jump confirmation

        with mss.mss() as sct:
            while not self._stop.is_set():
                now = time.time()

                with self._origin_lock:
                    new_ox, new_oy = self._ox, self._oy
                if new_ox != cur_ox or new_oy != cur_oy:
                    cur_ox, cur_oy = new_ox, new_oy
                    p_mon, pt_mon, e_mon = _make_mons(cur_ox, cur_oy)

                if now >= next_e and not self._stop.is_set():
                    next_e = now + self._ei
                    try:
                        shot = np.array(sct.grab(e_mon))[:, :, :3]
                        has_e, hp, hp_str = ocr_enemy_widget(shot)
                        with self._lock:
                            self._state.enemy_has = has_e
                            if has_e:
                                self._state.enemy_hp     = hp
                                self._state.enemy_hp_str = hp_str
                                if hp is not None:
                                    self._state.enemy_ts = now
                            else:
                                self._state.enemy_hp     = None
                                self._state.enemy_hp_str = None
                    except Exception as exc:
                        print(f"[WidgetOCRWorker] Enemy error: {exc}")

                if now >= next_p and not self._stop.is_set():
                    next_p = now + self._pi
                    try:
                        shot = np.array(sct.grab(p_mon))[:, :, :3]
                        ratios = ocr_full_widget(shot)
                        if len(ratios) >= 3:
                            with self._lock:
                                self._state.player_ratios = ratios[:3]
                                self._state.player_ts = now
                    except Exception as exc:
                        print(f"[WidgetOCRWorker] Player error: {exc}")

                if now >= next_pt and cfg.pet_enabled and cfg.pet_width > 0 and not self._stop.is_set():
                    next_pt = now + self._peti
                    try:
                        shot = np.array(sct.grab(pt_mon))[:, :, :3]
                        ratios = ocr_pet_widget(shot)
                        if len(ratios) >= 2:
                            # Jump filter: if HP changes >30% in one reading, require confirmation
                            _new_hp = ratios[0]
                            _old_hp = self._state.pet_ratios[0]
                            _accept = True
                            if abs(_new_hp - _old_hp) > 0.30 and _old_hp > 0:
                                _pet_pending_count += 1
                                if _pet_pending_count < 2:
                                    print(f"[WidgetOCRWorker] Pet HP jump {_old_hp:.0%}->{_new_hp:.0%}, waiting for confirmation ({_pet_pending_count}/2)")
                                    _accept = False
                            if _accept:
                                _pet_pending_count = 0
                                with self._lock:
                                    self._state.pet_ratios = ratios[:2]
                                    self._state.pet_ts = now
                            else:
                                # Still update timestamp so main loop knows worker is alive
                                with self._lock:
                                    self._state.pet_ts = now
                    except Exception as exc:
                        print(f"[WidgetOCRWorker] Pet error: {exc}")

                sleep_for = max(0.0, min(next_e, next_p, next_pt) - time.time())
                self._stop.wait(sleep_for)

        print("[WidgetOCRWorker] Loop exited.")
