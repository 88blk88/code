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

while True:
    while supervisor.runtime.serial_bytes_available:
        ch = sys.stdin.read(1)
        if ch in ("\r", "\n"):
            cmd = buf.strip().lower()
            buf = ""
            if cmd == "press1":
                now = time.monotonic()
                if now - last_press1 >= press1_cooldown:
                    kbd.send(Keycode.ONE)
                    last_press1 = now
                    press1_cooldown = 2.5 + random.uniform(-0.5, 0.5)
                    print(f"Pressed 1 (cd {press1_cooldown:.2f}s)")
                else:
                    print("press1 on cooldown")
            elif cmd == "presstab":
                kbd.send(Keycode.TAB)
                print("Pressed TAB")
            elif cmd == "press6":
                kbd.send(Keycode.SIX)
                print("Pressed 6")
            elif cmd == "presss":
                kbd.send(Keycode.S)
                print("Pressed S")
            # ignore unknown commands
        else:
            buf += ch

    time.sleep(0.01)