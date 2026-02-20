import numpy as np
import json
import cv2
from pathlib import Path

# Paths to your screenshots
full_path = "enemy_bar_full.png"
empty_path = "enemy_bar_empty.png"
settings_path = "config/settings.json"

settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
x_end = 5

def extract_strip(image_path):
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Could not load {image_path}")
    h = img.shape[0]
    return img[2:h-2, 2:2 + x_end]

full_strip = extract_strip(full_path)
empty_strip = extract_strip(empty_path)

full_red = float(full_strip[:, :, 2].mean())
empty_red = float(empty_strip[:, :, 2].mean())
full_green = float(full_strip[:, :, 1].mean())
full_rg_ratio = full_red / full_green if full_green > 0 else 0.0

# Update settings.json
settings["enemy_bar_full_red"] = full_red
settings["enemy_bar_empty_red"] = empty_red
settings["enemy_bar_full_rg_ratio"] = full_rg_ratio

Path(settings_path).write_text(json.dumps(settings, indent=2))
print(f"Extracted and saved: enemy_bar_full_red={full_red:.2f}, enemy_bar_empty_red={empty_red:.2f}, enemy_bar_full_rg_ratio={full_rg_ratio:.2f}")