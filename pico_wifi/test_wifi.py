"""PC-side test script for Pico 2 W WiFi firmware.

Usage:
  1. Copy code.py + settings.toml to the Pico's CIRCUITPY drive.
  2. Fill in settings.toml with your WiFi credentials.
  3. Open Thonny (or any serial terminal) and note the IP printed, e.g.:
       Connected! IP: 192.168.1.42
  4. Set PICO_IP below to that address.
  5. Run:  python pico_wifi/test_wifi.py
  6. You should see TAB, then key 6, then backtick pressed on your PC,
     and "OK ..." responses printed here.
"""

import socket
import time

# ---- CONFIGURE THIS ----
PICO_IP   = "192.168.138.2"  # Pico 2 W IP (from serial console)
PICO_PORT = 9999
# ------------------------

COMMANDS = [
    "presstab",
    "press6",
    "pressbacktick",
]

print(f"Connecting to Pico 2 W at {PICO_IP}:{PICO_PORT} ...")

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.settimeout(5.0)
    try:
        s.connect((PICO_IP, PICO_PORT))
    except OSError as e:
        print(f"Connection failed: {e}")
        print("Check: Is the IP correct? Is the Pico powered and connected to WiFi?")
        raise SystemExit(1)

    print(f"Connected.\n")

    for cmd in COMMANDS:
        s.sendall(f"{cmd}\r\n".encode())
        print(f"Sent:     {cmd!r}")
        try:
            resp = s.recv(64).decode("utf-8", "replace").strip()
            print(f"Response: {resp!r}")
        except socket.timeout:
            print("Response: (timeout — no reply received)")
        time.sleep(0.5)

print("\nDone. If you saw key presses on your PC, WiFi is working correctly.")
