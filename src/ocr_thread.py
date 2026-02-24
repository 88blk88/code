"""Background OCR — monitors the centre of the screen for event popup keywords."""

import threading
import time
import winsound
from dataclasses import dataclass, field

import cv2

import mss
import numpy as np

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
    pet_ratios:    list[float] = field(default_factory=lambda: [1.0, 1.0])
    enemy_has:    bool           = False
    enemy_hp:     float | None   = None
    enemy_hp_str: str | None     = None
    enemy_ts:     float          = 0.0   # timestamp of last successful enemy OCR


# ---------------------------------------------------------------------------
# Keyword patterns (case-insensitive substring match).
# Add more phrases here to extend detection.
# ---------------------------------------------------------------------------

EVENT_PATTERNS: list[str] = [
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
]


class EventWatcher:
    """
    Captures the centre 20% of the primary monitor and runs OCR in a daemon
    background thread.  When any EVENT_PATTERNS phrase is detected, sets an
    internal flag that the main loop can poll via is_event_found().

    The main loop should:
      1. Call is_event_found() every few seconds.
      2. If True — pause the bot, then call clear_event() to reset the flag.

    Usage:
        watcher = EventWatcher(interval=1.0)
        watcher.start()
        ...
        if watcher.is_event_found():
            bot_paused[0] = True
            print("\\n[EVENT] Popup detected — bot paused. Press F4 to resume.")
            watcher.clear_event()
        ...
        watcher.stop()
    """

    def __init__(self, interval: float = 1.0):
        self._interval = interval
        self._region = self._centre_region()

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
        print(f"[EventWatcher] Started. Region: {self._region}")

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

    @staticmethod
    def _centre_region() -> dict:
        """Return the centre 20% of the primary monitor as an mss region dict."""
        with mss.mss() as sct:
            mon = sct.monitors[1]   # index 1 = primary monitor
        sw, sh = mon["width"], mon["height"]
        w = int(sw * 0.20)
        h = int(sh * 0.20)
        x = mon["left"] + (sw - w) // 2
        y = mon["top"] + (sh - h) // 2
        # Expand upward by 100% of the original height (top moves up, height doubles)
        y = y - h
        h = h * 2
        return {"left": x, "top": y, "width": w, "height": h}

    def _run(self) -> None:
        if RapidOCR is None:
            return
        engine = RapidOCR()
        print("[EventWatcher] Engine ready.")

        _event_save_index = 0

        with mss.mss() as sct:
            while not self._stop_event.is_set():
                try:
                    shot = np.array(sct.grab(self._region))[:, :, :3]
                    text = self._do_ocr(engine, shot)
                    if text:
                        matched = self._check_patterns(text)
                        if matched:
                            _event_save_index += 1
                            path = rf"c:\code\event_match_{_event_save_index}.png"
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
            (2800, 180), (400, 180),
            (2800, 180), (400, 180),
            (2800, 180), (400, 180),
            (3000, 500),
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

    @staticmethod
    def _check_patterns(text: str) -> str | None:
        """Return the first matching pattern (case-insensitive), or None."""
        lower = text.lower()
        for pattern in EVENT_PATTERNS:
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
                 player_interval: float = 0.60,
                 pet_interval:    float = 0.60,
                 enemy_interval:  float = 0.20):
        self._cfg = cfg
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

    def get_player(self) -> list[float]:
        with self._lock:
            return list(self._state.player_ratios)

    def get_pet(self) -> list[float]:
        with self._lock:
            return list(self._state.pet_ratios)

    def get_enemy(self) -> tuple[bool, float | None, str | None]:
        with self._lock:
            return self._state.enemy_has, self._state.enemy_hp, self._state.enemy_hp_str

    def get_enemy_ts(self) -> float:
        with self._lock:
            return self._state.enemy_ts

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        cfg = self._cfg
        p_mon  = {"left": cfg.widget_left, "top": cfg.widget_top,
                  "width": cfg.widget_width, "height": cfg.widget_height}
        pt_mon = {"left": cfg.pet_left,    "top": cfg.pet_top,
                  "width": cfg.pet_width,   "height": cfg.pet_height}
        e_mon  = {"left": cfg.enemy_left,  "top": cfg.enemy_top,
                  "width": cfg.enemy_width, "height": cfg.enemy_height}

        now = time.time()
        next_p, next_pt, next_e = now, now + 0.30, now   # stagger player vs pet by 0.3s

        with mss.mss() as sct:
            while not self._stop.is_set():
                now = time.time()

                if now >= next_e:
                    next_e = now + self._ei
                    try:
                        shot = np.array(sct.grab(e_mon))[:, :, :3]
                        has_e, hp, hp_str = ocr_enemy_widget(shot)
                        with self._lock:
                            self._state.enemy_has    = has_e
                            self._state.enemy_hp     = hp
                            self._state.enemy_hp_str = hp_str
                            if has_e and hp is not None:
                                self._state.enemy_ts = now
                    except Exception as exc:
                        print(f"[WidgetOCRWorker] Enemy error: {exc}")

                if now >= next_p:
                    next_p = now + self._pi
                    try:
                        shot = np.array(sct.grab(p_mon))[:, :, :3]
                        ratios = ocr_full_widget(shot)
                        if len(ratios) >= 3:
                            with self._lock:
                                self._state.player_ratios = ratios[:3]
                    except Exception as exc:
                        print(f"[WidgetOCRWorker] Player error: {exc}")

                if now >= next_pt and cfg.pet_enabled and cfg.pet_width > 0:
                    next_pt = now + self._peti
                    try:
                        shot = np.array(sct.grab(pt_mon))[:, :, :3]
                        ratios = ocr_pet_widget(shot)
                        if len(ratios) >= 2:
                            with self._lock:
                                self._state.pet_ratios = ratios[:2]
                    except Exception as exc:
                        print(f"[WidgetOCRWorker] Pet error: {exc}")

                sleep_for = max(0.0, min(next_e, next_p, next_pt) - time.time())
                self._stop.wait(sleep_for)

        print("[WidgetOCRWorker] Loop exited.")
