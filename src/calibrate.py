import json
from pathlib import Path

import cv2
import mss
import numpy as np


def select_roi(window_name: str, image: np.ndarray, hint: str):
    print(hint)
    print("After dragging, press ENTER or SPACE to confirm. Press C to cancel.")
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, image.shape[1], image.shape[0])
    cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
    cv2.imshow(window_name, image)
    cv2.waitKey(100)  # let window render and gain focus
    x, y, w, h = cv2.selectROI(window_name, image, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(window_name)
    if w == 0 or h == 0:
        raise RuntimeError("ROI selection cancelled.")
    return int(x), int(y), int(w), int(h)


def main():
    with mss.mss() as sct:
        # Primary monitor screenshot
        mon = sct.monitors[1]
        shot = np.array(sct.grab(mon))[:, :, :3]  # BGRA -> BGR

    # 1) Select Falka's stats widget
    wx, wy, ww, wh = select_roi(
        "Select Falka Widget",
        shot,
        "Drag a rectangle around Falka's stats widget (CP/HP/MP area), then press ENTER/SPACE.",
    )

    # 2) Optionally select Nightshade's stats widget
    ns_enabled = False
    nx, ny, nw, nh = 0, 0, 0, 0
    print("\nDo you want to monitor Nightshade? (y/N): ", end="")
    answer = input().strip().lower()
    if answer in ("y", "yes"):
        ns_enabled = True
        nx, ny, nw, nh = select_roi(
            "Select Nightshade Widget",
            shot,
            "Drag a rectangle around Nightshade's stats widget (HP/MP area), then press ENTER/SPACE.",
        )

    # 3) Select enemy stats widget
    ex, ey, ew, eh = select_roi(
        "Select Enemy Widget",
        shot,
        "Drag a rectangle around the enemy's HP/MP % area, then press ENTER/SPACE.",
    )

    settings = {
        "widget_left": wx,
        "widget_top": wy,
        "widget_width": ww,
        "widget_height": wh,
        "cp_threshold": 0.95,
        "hp_threshold": 0.70,
        "mp_threshold": 0.30,
        "nightshade_enabled": ns_enabled,
        "nightshade_left": nx,
        "nightshade_top": ny,
        "nightshade_width": nw,
        "nightshade_height": nh,
        "nightshade_hp_threshold": 0.80,
        "nightshade_mp_threshold": 0.30,
        "enemy_left": ex,
        "enemy_top": ey,
        "enemy_width": ew,
        "enemy_height": eh,
        "serial_port": "COM4",
        "serial_baud": 115200,
        "alert_cooldown_sec": 2.0,
        "poll_interval_sec": 0.10,
    }

    out = Path(r"c:\code\config\settings.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    print(f"Saved calibration to: {out}")
    print(json.dumps(settings, indent=2))


if __name__ == "__main__":
    main()