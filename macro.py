"""Simple repeating key press. Run: python macro.py  |  Ctrl+C to stop. F4 = pause/resume."""

import ctypes, json, socket, time

KEY             = "press1"   # key to press in the main window
INTERVAL        = 4         # seconds between full cycles

SWITCH_ENABLED  = False       # set False to skip the Adela window switch entirely
KEY_ADELA       = "press3"   # key to press in the Adela window
ADELA_WINDOW    = "Adela"    # Adela window title substring
ADELA_DELAY     = 1.0        # seconds to wait after switching to Adela before pressing

VK_F4 = 0x73
_f4_was_down = False

def f4_pressed():
    global _f4_was_down
    down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F4) & 0x8000)
    edge = down and not _f4_was_down
    _f4_was_down = down
    return edge

# --- Window helpers (ctypes, same pattern as hp_ns.py) ---
_u32 = ctypes.windll.user32

def _win_find_hwnd(substring):
    found = [0]
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_long)
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

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004

def _win_activate(substring):
    hwnd = _win_find_hwnd(substring)
    if not hwnd:
        print(f"[WARN] window '{substring}' not found")
        return False
    _u32.ShowWindow(hwnd, 9)        # SW_RESTORE
    _u32.BringWindowToTop(hwnd)
    _u32.SetForegroundWindow(hwnd)
    # Click the centre of the window to ensure game accepts focus
    rect = RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(rect))
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    _u32.SetCursorPos(cx, cy)
    _u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    _u32.mouse_event(MOUSEEVENTF_LEFTUP,   0, 0, 0, 0)
    return True

def _get_foreground_title():
    hwnd = _u32.GetForegroundWindow()
    n = _u32.GetWindowTextLengthW(hwnd)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value

# --- Transport ---
with open("config/settings.json") as f:
    cfg = json.load(f)

transport   = cfg.get("transport", "wifi")
GAME_WINDOW = cfg.get("game_window_title", "Falka")

if transport == "serial":
    import serial
    conn = serial.Serial(cfg.get("serial_port", "COM5"), cfg.get("serial_baud", 115200), timeout=2)
    def send(cmd): conn.write((cmd + "\r\n").encode())
else:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((cfg.get("pico_ip", "192.168.138.2"), cfg.get("pico_port", 9999)))
    def send(cmd): sock.sendall((cmd + "\r\n").encode())

# --- Main loop ---
_switch_info = f" → switch to '{ADELA_WINDOW}' → press '{KEY_ADELA}' → back" if SWITCH_ENABLED else ""
print(f"Every {INTERVAL}s: press '{KEY}'{_switch_info}. F4=pause Ctrl+C=stop.")
paused = False
count = 0
try:
    next_press = time.time()
    while True:
        if f4_pressed():
            paused = not paused
            print("PAUSED" if paused else "RESUMED")

        if not paused and time.time() >= next_press:
            # 1. Press key in current (game) window
            count += 1
            send(KEY)
            print(f"[{count}] -> {KEY}  (in '{_get_foreground_title()}')")

            # 2. Optionally switch to Adela, wait, press, switch back
            if SWITCH_ENABLED:
                _win_activate(ADELA_WINDOW)
                time.sleep(ADELA_DELAY)
                send(KEY_ADELA)
                print(f"-> {KEY_ADELA}  (in '{_get_foreground_title()}')")
                _win_activate(GAME_WINDOW)

            # 4. Reset timer
            next_press = time.time() + INTERVAL

        time.sleep(0.05)
except KeyboardInterrupt:
    send("releaseall")
    print("Stopped.")
