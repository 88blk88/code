"""Pico 2 W WiFi firmware — TCP command server + USB HID keyboard.

Listens on port 9999 for TCP connections.
Accepts the same command format as the serial version:
  press<key>          e.g. presstab, press6, pressbacktick
  press<mod>+<key>    e.g. pressctrl+c, pressalt+f4

WiFi credentials are read from settings.toml (CircuitPython standard):
  WIFI_SSID = "your_network"
  WIFI_PASSWORD = "your_password"

Setup:
  1. Copy this file and settings.toml to the root of the Pico's CIRCUITPY drive.
  2. Fill in settings.toml with your WiFi credentials.
  3. Open Thonny (or any serial console) and note the IP printed on startup.
  4. Use that IP in test_wifi.py on your PC.
"""

import time
import os
import random
import wifi
import socketpool
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

# ---------------------------------------------------------------------------
# USB HID keyboard
# ---------------------------------------------------------------------------

kbd = Keyboard(usb_hid.devices)
print("HID keyboard ready.")

# ---------------------------------------------------------------------------
# Key lookup tables (identical to serial code.py)
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
# Command handler (identical logic to serial code.py)
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
# WiFi connection
# ---------------------------------------------------------------------------

PORT = 9999

ssid     = os.getenv("WIFI_SSID")
password = os.getenv("WIFI_PASSWORD")

if not ssid:
    raise RuntimeError("WIFI_SSID missing from settings.toml")

print(f"Connecting to WiFi: {ssid!r} ...")
MAX_RETRIES = 3
for attempt in range(1, MAX_RETRIES + 1):
    try:
        wifi.radio.connect(ssid, password, timeout=15)
        break
    except ConnectionError as e:
        print(f"Attempt {attempt}/{MAX_RETRIES} failed: {e}")
        if attempt < MAX_RETRIES:
            time.sleep(2)
else:
    raise RuntimeError(
        "WiFi connection failed.\n"
        "Check: 1) SSID/password in settings.toml  "
        "2) Router is 2.4 GHz (Pico 2 W is 2.4 GHz only)"
    )

print(f"Connected! IP: {wifi.radio.ipv4_address}")

pool   = socketpool.SocketPool(wifi.radio)
server = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
server.setsockopt(pool.SOL_SOCKET, pool.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", PORT))
server.listen(1)
print(f"Listening on {wifi.radio.ipv4_address}:{PORT}")

# ---------------------------------------------------------------------------
# Main loop — accept one client at a time
# ---------------------------------------------------------------------------

MAX_LINE_LENGTH = 1024  # discard buffer if no newline within this many bytes

while True:
    print("Waiting for connection...")
    conn, addr = server.accept()
    print(f"Client connected: {addr}")
    conn.settimeout(10)  # prevent recv_into from blocking forever

    line_buf = b""
    while True:
        chunk = bytearray(256)
        try:
            n = conn.recv_into(chunk)
            if n == 0:
                break                          # client closed connection
            line_buf += bytes(chunk[:n])
            if len(line_buf) > MAX_LINE_LENGTH:
                print("Buffer overflow, discarding")
                line_buf = b""
                continue
            while b"\n" in line_buf:
                line, line_buf = line_buf.split(b"\n", 1)
                cmd = line.strip(b"\r").decode("utf-8", "replace").lower()
                if cmd:
                    handle_press(cmd)
                    conn.send(f"OK {cmd}\r\n".encode())
        except OSError:
            break                              # connection reset / timeout

    kbd.release_all()  # release any held keys on disconnect
    conn.close()
    print("Client disconnected — all keys released.")
