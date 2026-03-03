import json
import sys
from pathlib import Path

import cv2
import mss
import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.json"

sys.path.insert(0, str(Path(__file__).parent))
from core import _ensure_dpi_aware, win_client_origin


def _draw_dashed_rect(img: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                      color: tuple, thickness: int = 1, gap: int = 8) -> None:
    """Draw a dashed rectangle outline."""
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    for i in range(len(corners) - 1):
        ax, ay = corners[i]
        bx, by = corners[i + 1]
        dist = int(np.hypot(bx - ax, by - ay))
        if dist == 0:
            continue
        for d in range(0, dist, gap * 2):
            sx = int(ax + (bx - ax) * d / dist)
            sy = int(ay + (by - ay) * d / dist)
            ex = int(ax + (bx - ax) * min(d + gap, dist) / dist)
            ey = int(ay + (by - ay) * min(d + gap, dist) / dist)
            cv2.line(img, (sx, sy), (ex, ey), color, thickness, cv2.LINE_AA)


def select_roi(window_name: str, image: np.ndarray, hint: str):
    """Returns (x, y, w, h) on confirm, or None if ESC was pressed to exit."""
    print(hint)
    print("Click and drag to select. ENTER/SPACE to confirm. C to re-select. ESC to exit.")

    state: dict = {"drawing": False, "start": None, "end": None,
                   "confirmed": False, "exit": False}

    def mouse_cb(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN:
            state["drawing"] = True
            state["start"] = (x, y)
            state["end"] = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and state["drawing"]:
            state["end"] = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and state["drawing"]:
            state["drawing"] = False
            state["end"] = (x, y)

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, image.shape[1], image.shape[0])
    cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)
    cv2.setMouseCallback(window_name, mouse_cb)

    while not state["confirmed"] and not state["exit"]:
        display = image.copy()

        if state["start"] and state["end"]:
            x1, y1 = state["start"]
            x2, y2 = state["end"]
            rx, ry = min(x1, x2), min(y1, y2)
            rw, rh = abs(x2 - x1), abs(y2 - y1)

            # Semi-transparent blue fill
            if rw > 0 and rh > 0:
                overlay = display.copy()
                cv2.rectangle(overlay, (rx, ry), (rx + rw, ry + rh), (200, 100, 0), -1)
                cv2.addWeighted(overlay, 0.20, display, 0.80, 0, display)

            # Dashed border: dark outer for contrast, white inner
            _draw_dashed_rect(display, rx, ry, rx + rw, ry + rh, (0, 0, 0), thickness=2, gap=8)
            _draw_dashed_rect(display, rx, ry, rx + rw, ry + rh, (255, 255, 255), thickness=1, gap=8)

            # Size label
            label = f"{rw} x {rh}"
            lx, ly = rx + 3, ry - 6 if ry > 16 else ry + rh + 14
            cv2.putText(display, label, (lx, ly), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(display, label, (lx, ly), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 255, 255), 1, cv2.LINE_AA)

        cv2.imshow(window_name, display)
        key = cv2.waitKey(16) & 0xFF
        if key in (13, 32):  # Enter / Space — confirm
            if state["start"] and state["end"]:
                x1, y1 = state["start"]
                x2, y2 = state["end"]
                if abs(x2 - x1) > 0 and abs(y2 - y1) > 0:
                    state["confirmed"] = True
        elif key in (ord('c'), ord('C')):  # C — reset, re-select
            state["start"] = None
            state["end"] = None
            state["drawing"] = False
            print("Selection cleared — drag to re-select.")
        elif key == 27:  # ESC — exit tool
            state["exit"] = True

    cv2.destroyWindow(window_name)

    if state["exit"] or state["start"] is None or state["end"] is None:
        return None

    x1, y1 = state["start"]
    x2, y2 = state["end"]
    x, y = min(x1, x2), min(y1, y2)
    w, h = abs(x2 - x1), abs(y2 - y1)
    return x, y, w, h


def _find_monitor_containing(monitors: list[dict], x: int, y: int) -> dict | None:
    """Return the mss monitor dict whose bounds contain (x, y), or None."""
    for mon in monitors[1:]:  # skip monitors[0] (virtual desktop)
        if (mon["left"] <= x < mon["left"] + mon["width"]
                and mon["top"] <= y < mon["top"] + mon["height"]):
            return mon
    return None


def main():
    _ensure_dpi_aware()

    # Load existing settings to read game_window_title
    out = _CONFIG_PATH
    settings = {}
    if out.exists():
        try:
            settings = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            pass

    game_title = settings.get("game_window_title", "")
    ox, oy = 0, 0
    origin = None
    if game_title:
        origin = win_client_origin(game_title)
        if origin is not None:
            ox, oy = origin
            print(f"[CALIBRATE] Game window '{game_title}' client origin: ({ox}, {oy})")
        else:
            print(f"[CALIBRATE] WARNING: window '{game_title}' not found — will use primary monitor")
    else:
        print("[CALIBRATE] No game_window_title in settings — will use primary monitor")

    with mss.mss() as sct:
        # Screenshot the monitor that contains the game window
        mon = None
        if origin is not None:
            mon = _find_monitor_containing(sct.monitors, ox, oy)
            if mon:
                print(f"[CALIBRATE] Game is on monitor: {mon}")
            else:
                print(f"[CALIBRATE] WARNING: ({ox}, {oy}) not inside any monitor — using primary")
        if mon is None:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = np.array(sct.grab(mon))[:, :, :3]  # BGRA -> BGR

    # ROI coordinates from select_roi are relative to the screenshot.
    # Convert to absolute screen coords by adding the monitor's top-left.
    mon_ox, mon_oy = mon["left"], mon["top"]

    # 1) Select Player's stats widget
    result = select_roi(
        "Select Player Widget",
        shot,
        "Drag a rectangle around Player's stats widget (CP/HP/MP area), then press ENTER/SPACE.",
    )
    if result is None:
        print("Calibration cancelled.")
        return
    wx, wy, ww, wh = result
    wx += mon_ox; wy += mon_oy

    # 2) Optionally select Pet's stats widget
    pet_enabled = False
    nx, ny, nw, nh = 0, 0, 0, 0
    print("\nDo you want to monitor Pet? (y/N): ", end="")
    answer = input().strip().lower()
    if answer in ("y", "yes"):
        pet_enabled = True
        result = select_roi(
            "Select Pet Widget",
            shot,
            "Drag a rectangle around Pet's stats widget (HP/MP area), then press ENTER/SPACE.",
        )
        if result is None:
            print("Calibration cancelled.")
            return
        nx, ny, nw, nh = result
        nx += mon_ox; ny += mon_oy

    # 3) Select enemy stats widget
    result = select_roi(
        "Select Enemy Widget",
        shot,
        "Drag a rectangle around the enemy's HP/MP % area, then press ENTER/SPACE.",
    )
    if result is None:
        print("Calibration cancelled.")
        return
    ex, ey, ew, eh = result
    ex += mon_ox; ey += mon_oy

    # Convert absolute screen coordinates -> relative to game window client area
    if origin is not None:
        print(f"[CALIBRATE] Subtracting window origin ({ox}, {oy}) from coordinates")
    else:
        print("[CALIBRATE] Saving absolute coordinates (no window found)")

    settings.update({
        "widget_left": wx - ox,
        "widget_top": wy - oy,
        "widget_width": ww,
        "widget_height": wh,
        "pet_enabled": pet_enabled,
        "pet_left": nx - ox,
        "pet_top": ny - oy,
        "pet_width": nw,
        "pet_height": nh,
        "enemy_left": ex - ox,
        "enemy_top": ey - oy,
        "enemy_width": ew,
        "enemy_height": eh,
    })

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    print(f"Saved calibration to: {out}")
    print(json.dumps(settings, indent=2))


if __name__ == "__main__":
    main()