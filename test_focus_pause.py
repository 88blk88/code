"""Test auto-pause on focus loss.

Run this WHILE the bot (hp_ns.py) is already running.

SETUP: Set "test_target_window" in config/settings.json to a substring of
       the window you want to switch to.
       Run with --list to see all open windows.

The script will:
  1. Wait COUNTDOWN seconds (focus the game window now)
  2. Switch to test_target_window
  3. Press 'a' there  <- bot should be paused, Pico silent
  4. Wait AWAY_TIME seconds
  5. Switch back to the first entry in focus_window_titles  <- bot auto-resumes

Watch the bot console for:
  [FOCUS] Game lost focus — auto-paused
  [FOCUS] Game regained focus — resumed
"""

import sys
import time
import json
import ctypes
import ctypes.wintypes as _wt
from pathlib import Path

import keyboard

_PROJECT_ROOT = Path(__file__).resolve().parent
_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.json"

COUNTDOWN = 3   # seconds before switching
AWAY_TIME = 5   # seconds to stay in the other window

_u32 = ctypes.windll.user32

# Load from settings.json
try:
    settings = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    TARGET_WINDOW = settings.get("test_target_window", "")
    GAME_WINDOWS  = settings.get("focus_window_titles", [])
except Exception as e:
    print(f"Failed to load settings.json: {e}")
    sys.exit(1)


def _find_hwnd(substring: str) -> int:
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


def list_windows():
    """Print all visible windows with titles."""
    print("Open windows:")

    @ctypes.WINFUNCTYPE(ctypes.c_bool, _wt.HWND, _wt.LPARAM)
    def _cb(hwnd, _):
        if _u32.IsWindowVisible(hwnd):
            n = _u32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                _u32.GetWindowTextW(hwnd, buf, n + 1)
                print(f"  {hwnd:10d}  {buf.value}")
        return True

    _u32.EnumWindows(_cb, 0)


def focus_window_by_title(substring):
    """Bring window whose title contains substring to foreground. Returns True on success."""
    hwnd = _find_hwnd(substring)
    if not hwnd:
        print(f"  [ERROR] No window found matching '{substring}'")
        return False
    n = _u32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    title = buf.value
    _u32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _u32.BringWindowToTop(hwnd)
    _u32.SetForegroundWindow(hwnd)
    print(f"  Focused: '{title}'")
    return True


if "--list" in sys.argv:
    list_windows()
    sys.exit(0)

if not TARGET_WINDOW:
    print("Set \"test_target_window\" in config/settings.json, or run with --list to see options.")
    sys.exit(1)

game_title = GAME_WINDOWS[0] if GAME_WINDOWS else None
if not game_title:
    print("WARNING: focus_window_titles is empty in settings.json — can't switch back automatically.")

print(f"Focus the GAME WINDOW now. Switching away in {COUNTDOWN}s...")
for i in range(COUNTDOWN, 0, -1):
    print(f"  {i}...")
    time.sleep(1)

print(f"\nSwitching to '{TARGET_WINDOW}'...")
if not focus_window_by_title(TARGET_WINDOW):
    sys.exit(1)
time.sleep(0.4)

print("Pressing 1 2 3 4 5 into Scryde Game text box (bot should be paused — Pico silent)...")
for key in ['1', '2', '3', '4', '5']:
    keyboard.press_and_release(key)
    print(f"  sent: {key}")
    time.sleep(0.8)

if game_title:
    print(f"\nSwitching back to '{game_title}'...")
    focus_window_by_title(game_title)
    time.sleep(0.4)
else:
    print("\nSwitch back to the game manually.")

print("Done. Check bot console for [FOCUS] messages.")
