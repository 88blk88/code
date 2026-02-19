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
                    press1_cooldown = random.uniform(0.5, 1.0)
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
            elif cmd == "press4":
                kbd.send(Keycode.FOUR)
                print("Pressed 4")
            elif cmd == "press5":
                kbd.send(Keycode.FIVE)
                print("Pressed 5")
            elif cmd == "press7":
                kbd.send(Keycode.SEVEN)
                print("Pressed 7")
            elif cmd == "press8":
                kbd.send(Keycode.EIGHT)
                print("Pressed 8")
            elif cmd == "press9":
                kbd.send(Keycode.NINE)
                print("Pressed 9")
            # ignore unknown commands
        else:
            buf += ch

    time.sleep(0.01)