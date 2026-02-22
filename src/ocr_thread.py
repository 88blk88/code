"""Background OCR — monitors the centre of the screen for event popup keywords."""

import threading
import winsound

import cv2

import mss
import numpy as np

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None

from core import _preprocess


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
