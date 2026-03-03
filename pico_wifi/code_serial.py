"""Pico 2 W USB-serial firmware — reads press<key> commands from USB CDC.

Accepts the same command format as the WiFi version:
  press<key>          e.g. presstab, press6, pressbacktick
  press<mod>+<key>    e.g. pressctrl+c, pressalt+f4

Setup:
  1. Copy this file to the Pico's CIRCUITPY drive as code.py
     (rename or remove the WiFi code.py first).
  2. In config/settings.json set:
       "transport": "serial"
       "serial_port": "COMx"   (check Device Manager -> Ports for the right port)
  3. Baud rate in settings ("serial_baud") is ignored by CircuitPython USB CDC,
     but 115200 is fine as a placeholder.
"""

import sys
import time
import random
import supervisor
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

# ---------------------------------------------------------------------------
# USB HID keyboard
# ---------------------------------------------------------------------------

kbd = Keyboard(usb_hid.devices)
print("HID keyboard ready. Waiting for serial commands...")

# ---------------------------------------------------------------------------
# Key lookup tables (identical to WiFi code.py)
# ---------------------------------------------------------------------------

KEY_MAP = {
    # Numbers
    "0": Keycode.ZERO,  "1": Keycode.ONE,   "2": Keycode.TWO,
    "3": Keycode.THREE, "4": Keycode.FOUR,  "5": Keycode.FIVE,
    "6": Keycode.SIX,   "7": Keycode.SEVEN, "8": Keycode.EIGHT, "9": Keycode.NINE,
    "10": Keycode.ZERO,
    # Letters
    "a": Keycode.A, "b": Keycode.B, "c": Keycode.C, "d": Keycode.D,
    "e": Keycode.E, "f": Keycode.F, "g": Keycode.G, "h": Keycode.H,
    "i": Keycode.I, "j": Keycode.J, "k": Keycode.K, "l": Keycode.L,
    "m": Keycode.M, "n": Keycode.N, "o": Keycode.O, "p": Keycode.P,
    "q": Keycode.Q, "r": Keycode.R, "s": Keycode.S, "t": Keycode.T,
    "u": Keycode.U, "v": Keycode.V, "w": Keycode.W, "x": Keycode.X,
    "y": Keycode.Y, "z": Keycode.Z,
    # Function keys
    "f1":  Keycode.F1,  "f2":  Keycode.F2,  "f3":  Keycode.F3,  "f4":  Keycode.F4,
    "f5":  Keycode.F5,  "f6":  Keycode.F6,  "f7":  Keycode.F7,  "f8":  Keycode.F8,
    "f9":  Keycode.F9,  "f10": Keycode.F10, "f11": Keycode.F11, "f12": Keycode.F12,
    # Navigation / editing
    "tab":        Keycode.TAB,
    "enter":      Keycode.ENTER,
    "return":     Keycode.ENTER,
    "esc":        Keycode.ESCAPE,
    "escape":     Keycode.ESCAPE,
    "backspace":  Keycode.BACKSPACE,
    "delete":     Keycode.DELETE,
    "del":        Keycode.DELETE,
    "insert":     Keycode.INSERT,
    "home":       Keycode.HOME,
    "end":        Keycode.END,
    "pageup":     Keycode.PAGE_UP,
    "pagedown":   Keycode.PAGE_DOWN,
    "space":      Keycode.SPACEBAR,
    "up":         Keycode.UP_ARROW,
    "down":       Keycode.DOWN_ARROW,
    "left":       Keycode.LEFT_ARROW,
    "right":      Keycode.RIGHT_ARROW,
    "printscreen":Keycode.PRINT_SCREEN,
    "scrolllock": Keycode.SCROLL_LOCK,
    "pause":      Keycode.PAUSE,
    "capslock":   Keycode.CAPS_LOCK,
    "numlock":    Keycode.KEYPAD_NUMLOCK,
    "app":        Keycode.APPLICATION,
    # Symbols
    "backtick":   Keycode.GRAVE_ACCENT,
    "grave":      Keycode.GRAVE_ACCENT,
    "minus":      Keycode.MINUS,
    "equals":     Keycode.EQUALS,
    "lbracket":   Keycode.LEFT_BRACKET,
    "rbracket":   Keycode.RIGHT_BRACKET,
    "backslash":  Keycode.BACKSLASH,
    "semicolon":  Keycode.SEMICOLON,
    "quote":      Keycode.QUOTE,
    "comma":      Keycode.COMMA,
    "period":     Keycode.PERIOD,
    "slash":      Keycode.FORWARD_SLASH,
    # Numpad
    "num0":     Keycode.KEYPAD_ZERO,
    "num1":     Keycode.KEYPAD_ONE,
    "num2":     Keycode.KEYPAD_TWO,
    "num3":     Keycode.KEYPAD_THREE,
    "num4":     Keycode.KEYPAD_FOUR,
    "num5":     Keycode.KEYPAD_FIVE,
    "num6":     Keycode.KEYPAD_SIX,
    "num7":     Keycode.KEYPAD_SEVEN,
    "num8":     Keycode.KEYPAD_EIGHT,
    "num9":     Keycode.KEYPAD_NINE,
    "numenter": Keycode.KEYPAD_ENTER,
    "numslash": Keycode.KEYPAD_FORWARD_SLASH,
    "numstar":  Keycode.KEYPAD_ASTERISK,
    "numminus": Keycode.KEYPAD_MINUS,
    "numplus":  Keycode.KEYPAD_PLUS,
    "numperiod":Keycode.KEYPAD_PERIOD,
}

MOD_MAP = {
    "ctrl":    Keycode.LEFT_CONTROL,
    "control": Keycode.LEFT_CONTROL,
    "shift":   Keycode.LEFT_SHIFT,
    "alt":     Keycode.LEFT_ALT,
    "win":     Keycode.LEFT_GUI,
    "gui":     Keycode.LEFT_GUI,
    "rctrl":   Keycode.RIGHT_CONTROL,
    "rshift":  Keycode.RIGHT_SHIFT,
    "ralt":    Keycode.RIGHT_ALT,
    "rwin":    Keycode.RIGHT_GUI,
}

# ---------------------------------------------------------------------------
# Command handler (identical logic to WiFi code.py)
# ---------------------------------------------------------------------------

last_press1 = 0
press1_cooldown = 0


def handle_press(cmd):
    global last_press1, press1_cooldown

    if cmd == "releaseall":
        kbd.release_all()
        print("Released all keys")
        return

    if cmd.startswith("hold"):
        action = "hold"
        rest = cmd[4:]
    elif cmd.startswith("press"):
        action = "press"
        rest = cmd[5:]
    else:
        return

    if not rest:
        return

    if action == "press" and rest == "1":
        now = time.monotonic()
        if now - last_press1 >= press1_cooldown:
            kbd.send(Keycode.ONE)
            last_press1 = now
            press1_cooldown = random.uniform(0.5, 1.0)
            print(f"Pressed 1 (cd {press1_cooldown:.2f}s)")
        else:
            print("press1 on cooldown")
        return

    parts = rest.split("+")
    modifiers = []
    key = None

    for part in parts:
        if part in MOD_MAP:
            modifiers.append(MOD_MAP[part])
        elif part in KEY_MAP:
            if key is not None:
                print(f"Ambiguous command (two keys?): {rest!r}")
                return
            key = KEY_MAP[part]
        else:
            print(f"Unknown key/mod: {part!r}")
            return

    if key is None:
        print(f"No key specified in: {rest!r}")
        return

    try:
        if action == "hold":
            kbd.press(*modifiers, key)
            print(f"Held {'+'.join(parts)}")
        else:
            kbd.send(*modifiers, key)
            print(f"Pressed {'+'.join(parts)}")
    except OSError as e:
        print(f"HID error: {e}")


# ---------------------------------------------------------------------------
# Main loop — read commands from USB CDC serial
# ---------------------------------------------------------------------------

MAX_LINE_LENGTH = 1024  # discard buffer if no newline within this many chars

line_buf = ""
while True:
    n = supervisor.runtime.serial_bytes_available
    if n:
        data = sys.stdin.read(n)
        for char in data:
            if char in ("\n", "\r"):
                cmd = line_buf.strip().lower()
                if cmd:
                    handle_press(cmd)
                    sys.stdout.write(f"OK {cmd}\r\n")
                line_buf = ""
            else:
                line_buf += char
                if len(line_buf) > MAX_LINE_LENGTH:
                    print("Buffer overflow, discarding")
                    line_buf = ""
    time.sleep(0.001)
