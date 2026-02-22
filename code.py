import time
import sys
import random
import usb_hid
import supervisor
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode

kbd = Keyboard(usb_hid.devices)
buf = ""
last_press1 = 0
press1_cooldown = 0
print("CODE.PY LOADED")

# ---------------------------------------------------------------------------
# Key lookup tables
# ---------------------------------------------------------------------------

KEY_MAP = {
    # Numbers (row)
    "0": Keycode.ZERO,  "1": Keycode.ONE,   "2": Keycode.TWO,
    "3": Keycode.THREE, "4": Keycode.FOUR,  "5": Keycode.FIVE,
    "6": Keycode.SIX,   "7": Keycode.SEVEN, "8": Keycode.EIGHT, "9": Keycode.NINE,
    # Alias: "10" -> key 0  (skill slot 10 in games)
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
    # Symbols (unshifted)
    "backtick":   Keycode.GRAVE_ACCENT,   # `
    "grave":      Keycode.GRAVE_ACCENT,
    "minus":      Keycode.MINUS,           # -
    "equals":     Keycode.EQUALS,          # =
    "lbracket":   Keycode.LEFT_BRACKET,    # [
    "rbracket":   Keycode.RIGHT_BRACKET,   # ]
    "backslash":  Keycode.BACKSLASH,       # \
    "semicolon":  Keycode.SEMICOLON,       # ;
    "quote":      Keycode.QUOTE,           # '
    "comma":      Keycode.COMMA,           # ,
    "period":     Keycode.PERIOD,          # .
    "slash":      Keycode.FORWARD_SLASH,   # /
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
# Command handler
# Command format:  press<key>  or  press<mod>+<mod>+<key>
# Examples:        press6  presstab  pressbacktick  pressctrl+c  pressalt+f4
# ---------------------------------------------------------------------------

def handle_press(cmd):
    global last_press1, press1_cooldown

    if not cmd.startswith("press"):
        return  # ignore unknown commands

    rest = cmd[5:]  # strip "press" prefix
    if not rest:
        return

    # Special case: press1 has a randomised cooldown
    if rest == "1":
        now = time.monotonic()
        if now - last_press1 >= press1_cooldown:
            kbd.send(Keycode.ONE)
            last_press1 = now
            press1_cooldown = random.uniform(0.5, 1.0)
            print(f"Pressed 1 (cd {press1_cooldown:.2f}s)")
        else:
            print("press1 on cooldown")
        return

    # Generic: split on '+' to separate modifiers from the key
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

    kbd.send(*modifiers, key)
    label = "+".join(parts)
    print(f"Pressed {label}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

while True:
    while supervisor.runtime.serial_bytes_available:
        ch = sys.stdin.read(1)
        if ch in ("\r", "\n"):
            cmd = buf.strip().lower()
            buf = ""
            if cmd:
                handle_press(cmd)
        else:
            buf += ch

    time.sleep(0.01)
