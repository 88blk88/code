"""Shared utilities for game stat monitors."""

import re
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import serial
import winsound

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
    hp_threshold: float = 0.70
    mp_threshold: float = 0.30

    # Pet widget (HP/MP)
    pet_enabled: bool = True
    pet_left: int = 1518
    pet_top: int = 26
    pet_width: int = 72
    pet_height: int = 25

    pet_hp_threshold: float = 0.80
    pet_mp_threshold: float = 0.30
    pet_hp_heal_threshold: float = 0.70

    # Enemy widget (HP%)
    enemy_left: int = 1229
    enemy_top: int = 22
    enemy_width: int = 59
    enemy_height: int = 25

    serial_port: str = "COM4"
    serial_baud: int = 115200

    alert_cooldown_sec: float = 2.0
    poll_interval_sec: float = 0.10
    show_preview: bool = True

    # Key bindings (Pico command strings)
    key_tab: str = "presstab"            # target switch in SEARCHING
    key_target_switch: str = "pressbacktick"  # target switch in IDLE / stuck
    key_attack: str = "press6"           # basic attack
    key_heal_pet: str = "press1"          # Pet HP heal
    key_pick_up: str = "press5"          # Pick Up (--pick-up mode)
    key_buff1: str = "press8"            # scheduled buff 1
    key_buff2: str = "press9"            # scheduled buff 2
    key_buff3: str = "press10"           # scheduled buff 3

    # Buff intervals (seconds)
    buff1_interval_sec: float = 3 * 60 + 40   # 3m40s
    buff2_interval_sec: float = 9 * 60.0       # 9min
    buff3_interval_sec: float = 19 * 60.0      # 19min


def load_config() -> MonitorConfig:
    config_path = Path(r"c:\code\config\settings.json")
    if not config_path.exists():
        return MonitorConfig()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    # Only keep keys that are fields of MonitorConfig
    valid_keys = set(MonitorConfig.__dataclass_fields__)
    filtered_data = {k: v for k, v in data.items() if k in valid_keys}
    return MonitorConfig(**filtered_data)


# ---------------------------------------------------------------------------
# Serial
# ---------------------------------------------------------------------------

_serial_conn: serial.Serial | None = None


def get_serial(cfg: MonitorConfig) -> serial.Serial | None:
    global _serial_conn
    if _serial_conn is not None and _serial_conn.is_open:
        return _serial_conn
    try:
        _serial_conn = serial.Serial(cfg.serial_port, cfg.serial_baud, timeout=0.1)
        print(f"[SERIAL] Connected to {cfg.serial_port}")
        return _serial_conn
    except Exception as e:
        print(f"\n[SERIAL] Cannot open {cfg.serial_port}: {e}")
        return None


def send_pico_command(cfg: MonitorConfig, cmd: str) -> None:
    global _serial_conn
    print(f"[ACTION] Sending Pico command: {cmd}")
    conn = get_serial(cfg)
    if conn is None:
        print(f"[ACTION] Pico command '{cmd}' not sent: serial unavailable.")
        return
    try:
        conn.write((cmd + "\r\n").encode())
        conn.flush()
        print(f"[ACTION] Pico command '{cmd}' sent.")
    except Exception as e:
        print(f"\n[SERIAL] Write error: {e}")
        _serial_conn = None


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------

def alert_beep() -> None:
    """Play a distinctive double beep alert."""
    winsound.Beep(1200, 120)
    winsound.Beep(1600, 120)


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
    print(f"\n[ACTION] Pet low HP at {hp_ratio:.1%} -> pressing 1 via Pico")
    if cfg is not None:
        print(f"[ACTION] About to send '{cfg.key_heal_pet}' to Pico for Pet low HP.")
        send_pico_command(cfg, cfg.key_heal_pet)


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
        if 0.0 <= val <= 100.0:
            ratios.append(val / 100.0)
    if ratios:
        return ratios
    matches = re.findall(r"\b(\d{1,3}(?:\.\d+)?)\b", text)
    for m in matches:
        val = float(m)
        if 0.0 <= val <= 100.0:
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


_ENEMY_BAR_THRESHOLDS = None

def enemy_widget_is_player(bgr_widget: np.ndarray) -> bool:
    """Pixel check: does the enemy widget show the player's own CP bar (golden/amber)?

    Samples the same 5-pixel wide strip as enemy_bar_empty.
    Player bar BGR signature from player_bar_color.png: B≈46, G≈99, R≈130
      → R/G ≈ 1.3  (much lower than enemy red ~2.5)
      → G/B ≈ 2.15 (green well above blue — golden hue)
    Returns True when the bar matches player colour.
    """
    h = bgr_widget.shape[0]
    x_end = 5
    strip = bgr_widget[2:h-2, 2:2 + x_end].astype(np.float32)
    mean_r = float(strip[:, :, 2].mean())
    mean_g = float(strip[:, :, 1].mean())
    mean_b = float(strip[:, :, 0].mean())
    rg_ratio = mean_r / mean_g if mean_g > 0 else 0.0
    gb_ratio = mean_g / mean_b if mean_b > 0 else 0.0
    is_player = mean_r >= 90 and rg_ratio < 2.0 and gb_ratio >= 1.5 and mean_r > mean_b
    print(f"[PIXEL] player-bar check R={mean_r:.1f} G={mean_g:.1f} B={mean_b:.1f} R/G={rg_ratio:.2f} G/B={gb_ratio:.2f} -> {'PLAYER' if is_player else 'not-player'}")
    return is_player


def enemy_bar_empty(bgr_widget: np.ndarray) -> bool:
    """Fast pixel check: is the enemy HP bar empty (no red bar visible)?

    Samples the leftmost 20% of the widget width, trimming 2px border,
    and checks the mean red channel value.
    Returns True when the bar is absent (enemy dead / no target).
    Actual widget size from settings: 157x13 px.
    """
    global _ENEMY_BAR_THRESHOLDS
    if _ENEMY_BAR_THRESHOLDS is None:
        try:
            config_path = Path(r"c:\code\config\settings.json")
            data = json.loads(config_path.read_text(encoding="utf-8"))
            full_red = float(data.get("enemy_bar_full_red", 120))
            empty_red = float(data.get("enemy_bar_empty_red", 40))
            full_rg_ratio = float(data.get("enemy_bar_full_rg_ratio", 2.5))
            _ENEMY_BAR_THRESHOLDS = (full_red, empty_red, full_rg_ratio)
        except Exception as e:
            print(f"[PIXEL] Failed to load color thresholds from settings.json: {e}")
            _ENEMY_BAR_THRESHOLDS = (120, 40, 2.5)
    full_red, empty_red, full_rg_ratio = _ENEMY_BAR_THRESHOLDS
    h = bgr_widget.shape[0]
    x_end = 5
    strip = bgr_widget[2:h-2, 2:2 + x_end].astype(np.float32)
    mean_red = float(strip[:, :, 2].mean())
    mean_green = float(strip[:, :, 1].mean())
    mean_blue = float(strip[:, :, 0].mean())
    rg_ratio = mean_red / mean_green if mean_green > 0 else 0.0
    print(f"[PIXEL] enemy bar mean_red={mean_red:.1f} rg_ratio={rg_ratio:.2f} (need red>={full_red:.1f} rg>={full_rg_ratio:.2f} red>blue)")
    return mean_red < full_red or rg_ratio < 1.5 or mean_red <= mean_blue

