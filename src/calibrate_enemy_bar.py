"""Enemy bar pixel calibration tool.

Shows a live preview of the enemy widget (same region as the bot) with the
detection patch highlighted.  Press ENTER to record ALIVE, then DEAD samples.

Usage:
    python src/calibrate_enemy_bar.py
"""

import json
import sys
import threading
import time
from pathlib import Path

import cv2
import mss
import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.json"

sys.path.insert(0, str(Path(__file__).parent))
from core import load_config, _ensure_dpi_aware, win_client_origin

WINDOW = "Enemy bar calibration — press ENTER to sample, ESC to quit"


# ---------------------------------------------------------------------------
# Patch helpers  (must match enemy_bar_empty() in core.py exactly)
# ---------------------------------------------------------------------------

def patch_slice(h: int):
    mid = h * 3 // 4
    return slice(mid - 1, mid + 2), slice(2, 5)


def sample_patch(bgr: np.ndarray) -> dict:
    h = bgr.shape[0]
    rs, cs = patch_slice(h)
    patch = bgr[rs, cs].astype(np.float32)
    r = float(patch[:, :, 2].mean())
    g = float(patch[:, :, 1].mean())
    b = float(patch[:, :, 0].mean())
    return {"R": r, "G": g, "B": b, "R/G": r / g if g > 0 else 0.0}


def render_preview(bgr: np.ndarray, label: str, s: dict, scale: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    rs, cs = patch_slice(h)
    big = cv2.resize(bgr, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)
    # Highlight patch
    cv2.rectangle(big,
                  (cs.start * scale, rs.start * scale),
                  (cs.stop  * scale - 1, rs.stop  * scale - 1),
                  (0, 255, 255), 1)
    pad = np.full((30, big.shape[1], 3), 20, dtype=np.uint8)
    info = (f"{label}  R={s['R']:.0f} G={s['G']:.0f} B={s['B']:.0f}"
            f"  R/G={s['R/G']:.2f}")
    cv2.putText(pad, info, (4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (0, 220, 220), 1, cv2.LINE_AA)
    return np.vstack([big, pad])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()
    scale = cfg.calibration_scale

    # Use the same DPI-aware window detection as the bot
    _ensure_dpi_aware()
    game_title = cfg.game_window_title or (
        cfg.focus_window_titles[0] if cfg.focus_window_titles else "")
    ox, oy = 0, 0
    if game_title:
        origin = win_client_origin(game_title)
        if origin is not None:
            ox, oy = origin
            print(f"[OK] Game window '{game_title}' at ({ox}, {oy})")
        else:
            print(f"[WARN] Window '{game_title}' not found — using (0,0)")
    else:
        print("[WARN] No game_window_title configured — using (0,0)")
    e_mon = {"left": cfg.enemy_left + ox, "top": cfg.enemy_top + oy,
             "width": cfg.enemy_width, "height": cfg.enemy_height}

    print(f"Enemy region: {e_mon}  ({cfg.enemy_width}x{cfg.enemy_height})")
    h = cfg.enemy_height
    mid = h * 3 // 4
    print(f"Patch: rows {mid-1}–{mid+1}, cols 2–4  (matches bot)")
    print()
    print("Live preview open — cyan rectangle = detection patch.")
    print("  1) Target an ALIVE enemy → press ENTER")
    print("  2) Untarget / let enemy die → press ENTER")
    print("  ESC in preview window to quit.")
    print()

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)

    samples = {}
    enter_event = threading.Event()
    quit_event  = threading.Event()

    def _stdin():
        for _ in range(2):
            input()
            enter_event.set()

    threading.Thread(target=_stdin, daemon=True).start()

    with mss.mss() as sct:
        for label in ("ALIVE", "DEAD"):
            print(f"--- [{label}] Prepare state, then press ENTER ---")
            enter_event.clear()

            while not enter_event.is_set() and not quit_event.is_set():
                bgr = np.array(sct.grab(e_mon))[:, :, :3]
                s   = sample_patch(bgr)
                cv2.imshow(WINDOW, render_preview(bgr, label, s, scale))
                if cv2.waitKey(100) & 0xFF == 27:
                    quit_event.set()
                    break

            if quit_event.is_set():
                cv2.destroyAllWindows()
                return

            bgr = np.array(sct.grab(e_mon))[:, :, :3]
            samples[label] = sample_patch(bgr)
            save_path = str(_PROJECT_ROOT / f"enemy_bar_{label.lower()}.png")
            cv2.imwrite(save_path, bgr)
            s = samples[label]
            print(f"  Recorded: R={s['R']:.1f} G={s['G']:.1f} B={s['B']:.1f}"
                  f" R/G={s['R/G']:.2f}  → {save_path}")
            print()

    cv2.destroyAllWindows()

    alive, dead = samples["ALIVE"], samples["DEAD"]
    print("=" * 56)
    print(f"  ALIVE  R={alive['R']:.1f}  G={alive['G']:.1f}  B={alive['B']:.1f}"
          f"  R/G={alive['R/G']:.2f}")
    print(f"  DEAD   R={dead['R']:.1f}   G={dead['G']:.1f}   B={dead['B']:.1f}"
          f"  R/G={dead['R/G']:.2f}")
    print()

    mid_red = (alive["R"] + dead["R"]) / 2.0
    mid_rg  = (alive["R/G"] + dead["R/G"]) / 2.0
    print(f"  Suggested  enemy_bar_full_red      = {mid_red:.1f}"
          f"  (alive={alive['R']:.1f}, dead={dead['R']:.1f})")
    print(f"  Suggested  enemy_bar_full_rg_ratio = {mid_rg:.2f}"
          f"  (alive={alive['R/G']:.2f}, dead={dead['R/G']:.2f})")
    print()

    raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    print(f"  Current    enemy_bar_full_red      = {raw.get('enemy_bar_full_red')}")
    print(f"  Current    enemy_bar_full_rg_ratio = {raw.get('enemy_bar_full_rg_ratio')}")
    print()

    if input("Write suggested values to config/settings.json? [y/N] ").strip().lower() == "y":
        raw["enemy_bar_full_red"]      = round(mid_red, 1)
        raw["enemy_bar_full_rg_ratio"] = round(mid_rg,  2)
        _CONFIG_PATH.write_text(
            json.dumps(raw, indent=2), encoding="utf-8")
        print("Saved. Restart the bot for the new values to take effect.")
    else:
        print("Not saved.")


if __name__ == "__main__":
    main()
