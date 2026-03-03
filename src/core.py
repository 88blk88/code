"""Shared utilities for game stat monitors."""

import re
import sys
import json
import time as _time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import socket
import winsound

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.json"

try:
    import serial as _serial_mod
except ImportError:
    _serial_mod = None

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class MonitorConfig:
    # Player widget (CP/HP/MP)
    widget_left: int = 55
    widget_top: int = 23
    widget_width: int = 98
    widget_height: int = 38

    cp_threshold: float = 0.95
    alert_cp_enabled: bool = True
    hp_threshold: float = 0.70
    alert_hp_enabled: bool = True
    mp_threshold: float = 0.30
    alert_mp_enabled: bool = True

    # Pet widget (HP/MP)
    pet_enabled: bool = True
    pet_left: int = 1518
    pet_top: int = 26
    pet_width: int = 72
    pet_height: int = 25

    pet_hp_threshold: float = 0.80
    alert_pet_hp_enabled: bool = True
    pet_mp_threshold: float = 0.30
    alert_pet_mp_enabled: bool = True
    pet_heal_enabled: bool = True
    pet_heal_threshold: float = 0.70

    # Enemy widget (HP%)
    enemy_left: int = 1229
    enemy_top: int = 22
    enemy_width: int = 59
    enemy_height: int = 25

    game_window_title: str = ""           # title substring of game window — used for coordinate tracking
    focus_window_titles: list = field(default_factory=list)  # auto-pause when none of these are focused (empty = disabled)
    test_focus_switch: bool = False   # switch to test_target_window on each new enemy
    test_target_window: str = ""      # window to switch to
    focus_switch_delay_sec: float = 0.4   # seconds to wait after switching TO secondary window before sending key
    focus_switch_stay_sec: float = 0.3   # seconds to keep secondary window focused after sending key
    focus_switch_back_delay_sec: float = 0.1  # seconds to wait after switching BACK to game window

    transport: str = "wifi"       # "wifi" or "serial"
    pico_ip: str = "192.168.138.2"
    pico_port: int = 9999
    serial_port: str = "COM4"
    serial_baud: int = 115200

    alert_cooldown_sec: float = 2.0
    poll_interval_sec: float = 0.10
    show_preview: bool = True

    # Key bindings (Pico command strings)
    key_next_target_near: str = "presstab"       # cycle to nearest target
    add_shift_next_target_near: bool = False
    add_hold_shift_next_target_near: bool = False  # hold shift+key instead of press
    key_next_target_far: str = "pressbacktick"   # cycle to far/next target
    add_shift_next_target_far: bool = False
    add_hold_shift_next_target_far: bool = False   # hold shift+key instead of press
    key_attack: str = "press6"           # basic attack
    key_attack_hold: bool = False        # hold attack key throughout ATTACKING instead of pressing each cycle
    add_shift: bool = False              # press Shift together with attack key
    add_hold_shift: bool = False         # hold Shift together with hold-attack key
    key_attack_2nd: str = "press7"       # optional follow-up attack
    key_attack_2nd_enabled: bool = False
    key_attack_2nd_hold: bool = False    # hold secondary attack key throughout ATTACKING
    attack_2nd_delay_min: float = 0.1    # seconds after first attack
    attack_2nd_delay_max: float = 0.3
    key_heal_pet: str = "press1"          # Pet HP heal
    add_shift_heal_pet: bool = False
    key_pick_up: str = "press5"          # Pick Up (--pick-up mode)
    add_shift_pick_up: bool = False
    key_buff1: str = "press8"            # scheduled buff 1
    add_shift_buff1: bool = False
    key_buff2: str = "press9"            # scheduled buff 2
    add_shift_buff2: bool = False
    key_buff3: str = "press10"           # scheduled buff 3
    add_shift_buff3: bool = False
    key_buff4: str = "press11"           # scheduled buff 4
    add_shift_buff4: bool = False

    enemy_bar_full_red: float = 80.0          # minimum mean_red for enemy bar detection
    enemy_bar_full_rg_ratio: float = 2.0      # minimum R/G ratio for enemy bar detection
    player_bar_min_red: float = 90.0          # player self-target detection: min R
    player_bar_max_red: float = 180.0         # player self-target detection: max R
    player_bar_max_blue: float = 80.0         # player self-target detection: max B
    player_bar_min_rg: float = 1.0            # player self-target detection: min R/G
    player_bar_max_rg: float = 2.0            # player self-target detection: max R/G
    player_bar_min_gb: float = 1.6            # player self-target detection: min G/B
    calibration_scale: int = 8    # upscale factor for the calibrate_enemy_bar.py preview window

    # Buff intervals and grace periods (seconds)
    buff1_interval_sec: float = 3 * 60 + 40   # 3m40s
    buff1_grace_sec: float = 0.0              # suppress attacks after buff1 fires
    buff2_interval_sec: float = 9 * 60.0       # 9min
    buff2_grace_sec: float = 0.0
    buff3_interval_sec: float = 19 * 60.0      # 19min
    buff3_grace_sec: float = 0.0
    buff4_interval_sec: float = 19 * 60.0      # 19min
    buff4_grace_sec: float = 0.0

    # Periodic action in the secondary window (test_target_window)
    key_buff_2nd_window: str = "press7"           # key to send in secondary window every buff_2nd_window_interval_sec
    add_shift_buff_2nd_window: bool = False
    buff_2nd_window_interval_sec: float = 180.0  # 3 minutes; 0 = disabled
    buff_2nd_window_delay_sec: float = 0.9       # wait after switching to secondary before sending key
    buff_2nd_window_stay_sec: float = 2.5        # how long to keep secondary focused after key

    # Combat timing (moved from hp_ns.py constants)
    alert_idle_enabled: bool = True       # beep when SEARCHING -> IDLE transition occurs

    tab_cooldown_sec: float = 1.0        # minimum seconds between TAB presses
    max_no_enemy_cycles: int = 10        # SEARCHING->IDLE cycles before auto-pause
    attack_cd_min: float = 3.0           # attack cooldown range (seconds)
    attack_cd_max: float = 4.0
    idle_resume_sec: float = 3.0         # seconds in IDLE before resuming SEARCHING

    # Event popup detection patterns (case-insensitive substring match)
    event_patterns: list = field(default_factory=lambda: [
        "Hello Falka",
        "To village",
        "Your actions seem suspicious to us",
        "To avoid disconnection",
        "you need to",
        "solve a simple arithmetic problem",
        "Decision time",
        "Attempt",
        "Solve the task",
        "Do you want to participate in",
    ])


def load_config() -> MonitorConfig:
    if not _CONFIG_PATH.exists():
        return MonitorConfig()
    data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    valid_keys = set(MonitorConfig.__dataclass_fields__)
    unknown = [k for k in data if k not in valid_keys and not k.startswith("_")]
    if unknown:
        print(f"[CONFIG] Warning: unknown keys in settings.json (ignored): {unknown}")
    filtered_data = {k: v for k, v in data.items() if k in valid_keys}
    return MonitorConfig(**filtered_data)


# ---------------------------------------------------------------------------
# Window helpers  (shared by hp_ns.py, calibrate_enemy_bar.py, etc.)
# ---------------------------------------------------------------------------

_dpi_aware = False

def _ensure_dpi_aware() -> None:
    """Call once: make the process DPI-aware so Win32 coords match mss physical pixels."""
    global _dpi_aware
    if _dpi_aware:
        return
    _dpi_aware = True
    import ctypes
    try:
        # Windows 8.1+: per-monitor DPI awareness (most accurate)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # Windows Vista+: system DPI awareness (fallback)
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def win_client_origin(substring: str) -> tuple[int, int] | None:
    """Return screen (x, y) of the game window client area top-left, or None.

    ClientToScreen(hwnd, 0,0) already accounts for title bar and window borders —
    (0,0) in client coordinates is the first drawable pixel inside the frame.
    _ensure_dpi_aware() is called first so the returned coordinates are in physical
    pixels, matching what mss captures.
    """
    if sys.platform != "win32" or not substring:
        return None
    _ensure_dpi_aware()
    import ctypes
    import ctypes.wintypes as _wt
    u32 = ctypes.windll.user32
    found = [0]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, _wt.HWND, _wt.LPARAM)
    def _cb(hwnd, _):
        if u32.IsWindowVisible(hwnd):
            n = u32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, buf, n + 1)
                if substring.lower() in buf.value.lower():
                    found[0] = hwnd
                    return False
        return True

    u32.EnumWindows(_cb, 0)
    if not found[0]:
        return None
    pt = _wt.POINT(0, 0)
    u32.ClientToScreen(found[0], ctypes.byref(pt))
    return (pt.x, pt.y)


def make_capture_regions(cfg: "MonitorConfig") -> tuple[dict, dict, dict]:
    """Build (player_mon, pet_mon, enemy_mon) mss capture dicts with window origin offset.

    Resolves the game window client origin the same way hp_ns.py does:
      game_title = cfg.game_window_title  or  cfg.focus_window_titles[0]
    Saves result to calling code via the returned dicts.
    """
    game_title = cfg.game_window_title or (
        cfg.focus_window_titles[0] if cfg.focus_window_titles else ""
    )
    origin = win_client_origin(game_title) if game_title else None
    if game_title and origin is None:
        print(f"[WINDOW] WARNING: '{game_title}' not found — using (0,0), capture will be wrong")
    ox, oy = origin if origin is not None else (0, 0)
    return (
        {"left": cfg.widget_left + ox, "top": cfg.widget_top + oy,
         "width": cfg.widget_width,    "height": cfg.widget_height},
        {"left": cfg.pet_left    + ox, "top": cfg.pet_top    + oy,
         "width": cfg.pet_width,       "height": cfg.pet_height},
        {"left": cfg.enemy_left  + ox, "top": cfg.enemy_top  + oy,
         "width": cfg.enemy_width,     "height": cfg.enemy_height},
    )


# ---------------------------------------------------------------------------
# Transport (WiFi or Serial)
# ---------------------------------------------------------------------------

_wifi_sock: socket.socket | None = None
_serial_conn = None
_pico_lock = __import__("threading").Lock()  # one command at a time across all threads
_pico_fail_until: float = 0.0   # backoff: skip sends until this timestamp
_pico_fail_logged: float = 0.0  # last time we logged a backoff skip
_PICO_BACKOFF_SEC = 5.0
_PICO_BACKOFF_LOG_INTERVAL = 10.0

_VALID_CMD_RE = re.compile(r"^(press|hold|release)(all|shift\+)?[a-z0-9+]*$")


def _get_wifi(cfg: MonitorConfig) -> socket.socket | None:
    global _wifi_sock
    if _wifi_sock is not None:
        return _wifi_sock
    sock = None
    try:
        sock = socket.create_connection((cfg.pico_ip, cfg.pico_port), timeout=3.0)
        sock.settimeout(1.0)
        _wifi_sock = sock
        print(f"[WIFI] Connected to {cfg.pico_ip}:{cfg.pico_port}")
        return _wifi_sock
    except Exception as e:
        print(f"\n[WIFI] Cannot connect to {cfg.pico_ip}:{cfg.pico_port}: {e}")
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        return None


def _get_serial(cfg: MonitorConfig):
    global _serial_conn
    if _serial_conn is not None and _serial_conn.is_open:
        return _serial_conn
    if _serial_mod is None:
        print("\n[SERIAL] pyserial not installed.")
        return None
    try:
        _serial_conn = _serial_mod.Serial(cfg.serial_port, cfg.serial_baud, timeout=0.1, write_timeout=1.0)
        print(f"[SERIAL] Connected to {cfg.serial_port}")
        return _serial_conn
    except Exception as e:
        print(f"\n[SERIAL] Cannot open {cfg.serial_port}: {e}")
        return None


def send_pico_command(cfg: MonitorConfig, cmd: str) -> None:
    global _wifi_sock, _serial_conn, _pico_fail_until, _pico_fail_logged

    if not _VALID_CMD_RE.match(cmd):
        print(f"[PICO] Warning: command '{cmd}' doesn't match expected format (press|hold|release...)")

    now = _time.time()
    if now < _pico_fail_until:
        if now - _pico_fail_logged >= _PICO_BACKOFF_LOG_INTERVAL:
            print(f"[PICO] Skipping commands — backoff active ({_pico_fail_until - now:.1f}s remaining)")
            _pico_fail_logged = now
        return

    print(f"[ACTION] Sending Pico command: {cmd}")
    with _pico_lock:
        if cfg.transport == "serial":
            conn = _get_serial(cfg)
            if conn is None:
                _pico_fail_until = now + _PICO_BACKOFF_SEC
                _pico_fail_logged = now
                print(f"[ACTION] Pico command '{cmd}' not sent: serial unavailable. Backoff {_PICO_BACKOFF_SEC}s.")
                return
            try:
                conn.write((cmd + "\r\n").encode())
                conn.flush()
                _pico_fail_until = 0.0
            except Exception as e:
                print(f"\n[SERIAL] Write error: {e}")
                _serial_conn = None
                _pico_fail_until = now + _PICO_BACKOFF_SEC
                _pico_fail_logged = now
        else:
            conn = _get_wifi(cfg)
            if conn is None:
                _pico_fail_until = now + _PICO_BACKOFF_SEC
                _pico_fail_logged = now
                print(f"[ACTION] Pico command '{cmd}' not sent: WiFi unavailable. Backoff {_PICO_BACKOFF_SEC}s.")
                return
            try:
                conn.sendall((cmd + "\r\n").encode())
                _pico_fail_until = 0.0
            except Exception as e:
                print(f"\n[WIFI] Send error: {e}")
                try:
                    _wifi_sock.close()
                except Exception:
                    pass
                _wifi_sock = None
                _pico_fail_until = now + _PICO_BACKOFF_SEC
                _pico_fail_logged = now


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------

def alert_beep() -> None:
    """Play a distinctive double beep alert (non-blocking)."""
    import threading as _t
    def _play():
        winsound.Beep(1200, 120)
        winsound.Beep(1600, 120)
    _t.Thread(target=_play, daemon=True).start()


def idle_beep() -> None:
    """Play a soft descending triple tone indicating idle state (non-blocking)."""
    import threading as _t
    def _play():
        winsound.Beep(700, 100)
        winsound.Beep(550, 110)
        winsound.Beep(400, 140)
    _t.Thread(target=_play, daemon=True).start()


def no_enemy_warning() -> None:
    """Play a prolonged warning sound when max idle cycles reached (non-blocking)."""
    import threading as _t
    def _play():
        for _ in range(3):
            winsound.Beep(1800, 300)
            winsound.Beep(600, 300)
    _t.Thread(target=_play, daemon=True).start()


# ---------------------------------------------------------------------------
# Action callbacks
# ---------------------------------------------------------------------------

def on_low_hp(hp_ratio: float) -> None:
    print(f"\n[ACTION] Low HP action triggered at {hp_ratio:.1%}")


def on_low_cp(cp_ratio: float) -> None:
    print(f"\n[ACTION] Low CP action triggered at {cp_ratio:.1%}")


def on_low_mp(mp_ratio: float) -> None:
    print(f"\n[ACTION] Low MP action triggered at {mp_ratio:.1%}")


def on_low_pet_hp(hp_ratio: float, cfg: MonitorConfig | None = None) -> None:
    print(f"\n[ALERT] Pet low HP at {hp_ratio:.1%}")


def on_low_pet_mp(mp_ratio: float) -> None:
    print(f"\n[ACTION] Pet low MP at {mp_ratio:.1%}")


def on_enemy_alive(hp_pct: float, cfg: MonitorConfig | None = None) -> None:
    print(f"\n[ACTION] Enemy alive at {hp_pct:.0%} -> pressing A to attack")
    if cfg is not None:
        print(f"[ACTION] About to send '{cfg.key_attack}' to Pico for enemy alive.")
        send_pico_command(cfg, cfg.key_attack)


# ---------------------------------------------------------------------------
# OCR engine
# ---------------------------------------------------------------------------

_ocr_engine = None
_ocr_warning_printed = False


def get_ocr_engine():
    global _ocr_engine
    if RapidOCR is None:
        return None
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    return _ocr_engine


# ---------------------------------------------------------------------------
# OCR text parsing
# ---------------------------------------------------------------------------


def _extract_slash_pairs(text: str) -> list[float]:
    # Fix OCR misread: "6638/6638" → "663816638" (slash read as digit 1, no space)
    # Pattern: same 3-5 digit number appearing twice with a "1" between them
    text = re.sub(r'(\d{3,5})1(\1)', r'\1/\2', text)
    candidates = re.findall(r"(\d{2,6})\s*/\s*(\d{2,6})", text)
    ratios = []
    for current_str, maximum_str in candidates:
        current = int(current_str)
        maximum = int(maximum_str)
        if maximum <= 0:
            continue
        if current > maximum:
            current, maximum = maximum, current
        ratios.append(float(np.clip(current / maximum, 0.0, 1.0)))
    return ratios


def parse_all_ratios_from_text(text: str, expected: int = 3) -> list[float]:
    return _extract_slash_pairs(text)


def _extract_percentages(text: str) -> list[float]:
    # Collapse OCR-inserted spaces within numbers: "1 00. 00%" -> "100.00%"
    text = re.sub(r'(\d)\s+(\d)', r'\1\2', text)
    text = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', text)
    matches = re.findall(r"(\d{1,3}(?:\.\d+)?)\s*%", text)
    ratios = []
    for m in matches:
        val = float(m)
        if 0.0 < val <= 100.0:  # reject 0% — OCR artifact, live enemy can't be 0%
            ratios.append(val / 100.0)
    return ratios


# ---------------------------------------------------------------------------
# Image preprocessing helper
# ---------------------------------------------------------------------------

def _preprocess(bgr_widget: np.ndarray) -> list[np.ndarray]:
    """Return 4 preprocessed variants for OCR (grayscale, 2x upscale)."""
    gray = cv2.cvtColor(bgr_widget, cv2.COLOR_BGR2GRAY)
    upscaled = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    blur = cv2.GaussianBlur(upscaled, (0, 0), 3)
    sharpened = cv2.addWeighted(upscaled, 1.5, blur, -0.5, 0)
    normalized = np.empty_like(sharpened)
    cv2.normalize(sharpened, normalized, 0, 255, cv2.NORM_MINMAX)
    normalized = normalized.astype(np.uint8)
    _, binary = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary_inv = cv2.bitwise_not(binary)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    clahe_img = clahe.apply(upscaled)
    return [normalized, binary, binary_inv, clahe_img]


# ---------------------------------------------------------------------------
# OCR functions
# ---------------------------------------------------------------------------

def ocr_full_widget(bgr_widget: np.ndarray) -> list[float]:
    """OCR Player's widget -> [CP, HP, MP] ratios."""
    global _ocr_warning_printed
    engine = get_ocr_engine()
    if engine is None:
        if not _ocr_warning_printed:
            print("\n[ERROR] rapidocr_onnxruntime not installed.")
            _ocr_warning_printed = True
        return []
    for image in _preprocess(bgr_widget):
        try:
            result, _ = engine(image)
        except Exception:
            continue
        if not result:
            continue
        combined = " ".join(e[1] for e in result if len(e) >= 2)
        print(f"[OCR DEBUG] Player raw OCR text: {combined}")
        ratios = parse_all_ratios_from_text(combined)
        print(f"[OCR DEBUG] Player parsed ratios: {ratios}")
        if len(ratios) >= 3:
            return ratios[:3]
    return []


def ocr_pet_widget(bgr_widget: np.ndarray) -> list[float]:
    """OCR Pet's widget -> [HP, MP] ratios."""
    engine = get_ocr_engine()
    if engine is None:
        return []
    for image in _preprocess(bgr_widget):
        try:
            result, _ = engine(image)
        except Exception:
            continue
        if not result:
            continue
        combined = " ".join(e[1] for e in result if len(e) >= 2)
        print(f"[OCR DEBUG] Pet raw OCR text: {combined}")
        ratios = parse_all_ratios_from_text(combined, expected=2)
        print(f"[OCR DEBUG] Pet parsed ratios: {ratios}")
        if len(ratios) >= 2:
            return ratios[:2]
    return []


_PREPROCESS_VARIANT_NAMES = ["normalized", "binary", "binary_inv", "clahe"]

def ocr_enemy_widget(bgr_widget: np.ndarray) -> tuple[bool, float | None, str | None]:
    """OCR enemy widget -> (has_enemy, hp_ratio)."""
    engine = get_ocr_engine()
    if engine is None:
        return (False, None, None)
    for variant, image in zip(_PREPROCESS_VARIANT_NAMES, _preprocess(bgr_widget)):
        try:
            result, _ = engine(image)
        except Exception as exc:
            print(f"[OCR ENEMY] variant={variant} engine error: {exc}")
            continue
        if not result:
            print(f"[OCR ENEMY] variant={variant} -> no text detected")
            continue
        combined = " ".join(e[1] for e in result if len(e) >= 2)
        print(f"[OCR ENEMY] variant={variant} raw='{combined}'")
        pcts = _extract_percentages(combined)
        if pcts:
            best = max(pcts)
            best_match = None
            for m in re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*%", combined):
                if abs(float(m.group(1)) / 100.0 - best) < 1e-6:
                    best_match = m
                    break
            pct_str = best_match.group(0) if best_match else None
            print(f"[OCR ENEMY] variant={variant} pct_str={pct_str!r} hp_val={best} all_pcts={pcts}")
            return (True, best, pct_str)
        pct_str = None
        print(f"[OCR ENEMY] variant={variant} pct_str=None hp_val=None all_pcts={pcts}")
        print(f"[OCR ENEMY] variant={variant} -> text found but no percentage parsed, trying next variant")
    print(f"[OCR ENEMY] all variants exhausted -> no result")
    return (False, None, None)


def enemy_widget_is_player(bgr_widget: np.ndarray, cfg: MonitorConfig | None = None) -> bool:
    """Pixel check: does the enemy widget show the player's own CP bar (golden/amber)?

    Calibrated from player_bar_color.png: B=46.7 G=101.4 R=131.0
      R/G ≈ 1.29  (enemy red bar R/G is ~2.5+, so this is distinctly lower)
      G/B ≈ 2.17  (golden hue: green well above blue)
    Intended to be called unconditionally every frame — only logs on detection.
    Enemy bar fails this check because its R/G >> 2.0.
    """
    min_r = cfg.player_bar_min_red if cfg else 90.0
    max_r = cfg.player_bar_max_red if cfg else 180.0
    max_b = cfg.player_bar_max_blue if cfg else 80.0
    min_rg = cfg.player_bar_min_rg if cfg else 1.0
    max_rg = cfg.player_bar_max_rg if cfg else 2.0
    min_gb = cfg.player_bar_min_gb if cfg else 1.6

    h, w = bgr_widget.shape[:2]
    x_end = max(6, w // 10)       # left 10% of bar width
    trim = max(2, h // 4)         # ~25% top/bottom trim against misalignment
    strip = bgr_widget[trim:h-trim, 1:x_end].astype(np.float32)
    mean_r = float(strip[:, :, 2].mean())
    mean_g = float(strip[:, :, 1].mean())
    mean_b = float(strip[:, :, 0].mean())
    rg_ratio = mean_r / mean_g if mean_g > 0 else 0.0
    gb_ratio = mean_g / mean_b if mean_b > 0 else 0.0
    is_player = (
        min_r <= mean_r < max_r
        and mean_b < max_b
        and min_rg <= rg_ratio < max_rg
        and gb_ratio >= min_gb
    )
    if is_player:
        print(f"[PIXEL] PLAYER BAR detected: R={mean_r:.1f} G={mean_g:.1f} B={mean_b:.1f} R/G={rg_ratio:.2f} G/B={gb_ratio:.2f}")
    return is_player


def enemy_bar_empty(bgr_widget: np.ndarray, cfg: MonitorConfig | None = None) -> tuple[bool, float]:
    """Fast pixel check: is the enemy HP bar empty (no red bar visible)?

    Samples a 3×3 px patch centred at 75% of bar height (25% from bottom),
    columns 2–4. Returns (is_empty, mean_red) where is_empty is True when the
    bar is absent (enemy dead / no target) and mean_red is the raw red channel
    mean for use in OCR cross-checks.
    Thresholds come from cfg (MonitorConfig). Falls back to defaults if cfg is None.
    """
    full_red = cfg.enemy_bar_full_red if cfg else 80.0
    full_rg_ratio = cfg.enemy_bar_full_rg_ratio if cfg else 2.0
    h = bgr_widget.shape[0]
    x_end = 3
    mid = h * 3 // 4  # 25% from bottom
    strip = bgr_widget[mid-1:mid+2, 2:2 + x_end].astype(np.float32)
    mean_red = float(strip[:, :, 2].mean())
    mean_green = float(strip[:, :, 1].mean())
    mean_blue = float(strip[:, :, 0].mean())
    rg_ratio = mean_red / mean_green if mean_green > 0 else 0.0
    print(f"[PIXEL] enemy bar mean_red={mean_red:.1f} rg_ratio={rg_ratio:.2f} (need red>={full_red:.1f} rg>={full_rg_ratio:.2f} red>blue)")
    is_empty = mean_red < full_red or rg_ratio < full_rg_ratio or mean_red <= mean_blue
    return is_empty, mean_red

