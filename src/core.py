"""Shared utilities for game stat monitors."""

import re
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    # Falka widget (CP/HP/MP)
    widget_left: int = 100
    widget_top: int = 100
    widget_width: int = 184
    widget_height: int = 82

    cp_threshold: float = 0.95
    hp_threshold: float = 0.70
    mp_threshold: float = 0.30

    # Nightshade widget (HP/MP)
    nightshade_enabled: bool = True
    nightshade_left: int = 100
    nightshade_top: int = 200
    nightshade_width: int = 184
    nightshade_height: int = 60

    nightshade_hp_threshold: float = 0.80
    nightshade_mp_threshold: float = 0.30
    nightshade_hp_heal_threshold: float = 0.70

    # Enemy widget (HP%/MP%)
    enemy_left: int = 100
    enemy_top: int = 300
    enemy_width: int = 120
    enemy_height: int = 40

    serial_port: str = "COM4"
    serial_baud: int = 115200

    alert_cooldown_sec: float = 1.0
    poll_interval_sec: float = 0.10
    show_preview: bool = True
    ocr_every_frames: int = 1


def load_config() -> MonitorConfig:
    config_path = Path(r"c:\code\config\settings.json")
    if not config_path.exists():
        return MonitorConfig()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return MonitorConfig(**data)


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
    conn = get_serial(cfg)
    if conn is None:
        return
    try:
        conn.write((cmd + "\r\n").encode())
        conn.flush()
    except Exception as e:
        print(f"\n[SERIAL] Write error: {e}")
        global _serial_conn
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


def on_low_nightshade_hp(hp_ratio: float, cfg: MonitorConfig | None = None) -> None:
    print(f"\n[ACTION] Nightshade low HP at {hp_ratio:.1%} -> pressing 1 via Pico")
    if cfg is not None:
        send_pico_command(cfg, "press1")


def on_low_nightshade_mp(mp_ratio: float) -> None:
    print(f"\n[ACTION] Nightshade low MP at {mp_ratio:.1%}")


def on_enemy_alive(hp_pct: float, cfg: MonitorConfig | None = None) -> None:
    print(f"\n[ACTION] Enemy alive at {hp_pct:.0%} -> pressing A to attack")
    if cfg is not None:
        send_pico_command(cfg, "pressa")


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

def _fix_ocr_chars(text: str) -> str:
    table = str.maketrans({
        'O': '0', 'o': '0', 'Q': '0',
        'l': '1', 'I': '1', 'i': '1', '|': '1',
        'Z': '2', 'z': '2',
        'S': '5', 's': '5',
        'B': '8', 'b': '6',
        'G': '6', 'g': '9',
        'T': '7', 't': '7',
        'A': '4', 'a': '4',
    })
    return text.translate(table)


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
    ratios = _extract_slash_pairs(text)
    if len(ratios) >= expected:
        return ratios
    fixed = _fix_ocr_chars(text)
    ratios = _extract_slash_pairs(fixed)
    return ratios


def _extract_percentages(text: str) -> list[float]:
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
    normalized = cv2.normalize(sharpened, None, 0, 255, cv2.NORM_MINMAX)
    _, binary = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    binary_inv = cv2.bitwise_not(binary)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    clahe_img = clahe.apply(upscaled)
    return [normalized, binary, binary_inv, clahe_img]


# ---------------------------------------------------------------------------
# OCR functions
# ---------------------------------------------------------------------------

def ocr_full_widget(bgr_widget: np.ndarray) -> list[float]:
    """OCR Falka's widget -> [CP, HP, MP] ratios."""
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
        ratios = parse_all_ratios_from_text(combined)
        if len(ratios) >= 3:
            return ratios[:3]
    return []


def ocr_nightshade_widget(bgr_widget: np.ndarray) -> list[float]:
    """OCR Nightshade's widget -> [HP, MP] ratios."""
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
        ratios = parse_all_ratios_from_text(combined, expected=2)
        if len(ratios) >= 2:
            return ratios[:2]
    return []


def ocr_enemy_widget(bgr_widget: np.ndarray) -> tuple[bool, float | None]:
    """OCR enemy widget -> (has_enemy, hp_ratio)."""
    engine = get_ocr_engine()
    if engine is None:
        return (False, None)
    for image in _preprocess(bgr_widget):
        try:
            result, _ = engine(image)
        except Exception:
            continue
        if not result:
            continue
        combined = " ".join(e[1] for e in result if len(e) >= 2)
        pcts = _extract_percentages(combined)
        if pcts:
            return (True, pcts[0])
        fixed = _fix_ocr_chars(combined)
        pcts = _extract_percentages(fixed)
        if pcts:
            return (True, pcts[0])
    return (False, None)


# ---------------------------------------------------------------------------
# Pixel-based detection (fast, no OCR)
# ---------------------------------------------------------------------------

def detect_target_type(bgr_widget: np.ndarray) -> str:
    """Return 'enemy', 'player', or 'unknown' based on bar colours."""
    hsv = cv2.cvtColor(bgr_widget, cv2.COLOR_BGR2HSV)
    total = hsv.shape[0] * hsv.shape[1]
    if total == 0:
        return "unknown"
    blue_ratio = cv2.countNonZero(cv2.inRange(hsv, (100, 60, 50), (130, 255, 255))) / total
    gold_ratio = cv2.countNonZero(cv2.inRange(hsv, (15, 80, 80), (40, 255, 255))) / total
    min_r = 0.02
    if blue_ratio >= min_r and blue_ratio > gold_ratio:
        return "enemy"
    if gold_ratio >= min_r and gold_ratio > blue_ratio:
        return "player"
    return "unknown"


def detect_enemy_dead(bgr_widget: np.ndarray) -> bool:
    """Fast check: is enemy widget empty (< 1% colourful pixels)?"""
    hsv = cv2.cvtColor(bgr_widget, cv2.COLOR_BGR2HSV)
    total = hsv.shape[0] * hsv.shape[1]
    if total == 0:
        return False
    colour_ratio = cv2.countNonZero(cv2.inRange(hsv, (0, 50, 50), (180, 255, 255))) / total
    return colour_ratio < 0.01
