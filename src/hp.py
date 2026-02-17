"""Game stat monitor — Falka (CP/HP/MP) + Enemy targeting.

Run:  python src/hp.py [--no-preview]
"""

import time
import random
import argparse

import cv2
import mss
import numpy as np

from core import (
    MonitorConfig, load_config, send_pico_command, alert_beep,
    on_low_cp, on_low_hp, on_low_mp, on_enemy_alive,
    get_ocr_engine, ocr_full_widget, ocr_enemy_widget,
    detect_target_type, detect_enemy_dead,
)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def render_preview(
    f_widget: np.ndarray, e_widget: np.ndarray,
    cp: float, hp: float, mp: float, enemy_hp: float,
) -> None:
    preview = f_widget.copy()
    h = preview.shape[0]

    labels = [
        (f"Falka  CP {cp:.1%}", (0, 215, 255)),
        (f"Falka  HP {hp:.1%}", (0, 0, 255)),
        (f"Falka  MP {mp:.1%}", (255, 150, 0)),
        (f"Enemy  HP {enemy_hp:.0%}" if enemy_hp >= 0 else "Enemy  HP --", (0, 180, 0)),
    ]

    spacer = np.full((h, 4, 3), 40, dtype=np.uint8)
    e_resized = cv2.resize(e_widget, (e_widget.shape[1], h))
    combined = cv2.hconcat([preview, spacer, e_resized])

    pad_h = len(labels) * 16 + 8
    padded = cv2.copyMakeBorder(combined, 0, pad_h, 0, 0, cv2.BORDER_CONSTANT, value=(30, 30, 30))
    ph = combined.shape[0]
    for i, (text, color) in enumerate(labels):
        cv2.putText(padded, text, (4, ph + 14 + i * 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.40, color, 1, cv2.LINE_AA)

    scaled = cv2.resize(padded, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_NEAREST)
    cv2.imshow("Monitor Preview", scaled)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Falka + Enemy monitor (no Nightshade).")
    parser.add_argument("--no-preview", action="store_true", help="Disable preview window.")
    args = parser.parse_args()

    cfg = load_config()
    if args.no_preview:
        cfg.show_preview = False

    # Falka bars
    f_thresholds = [cfg.cp_threshold, cfg.hp_threshold, cfg.mp_threshold]
    f_actions = [on_low_cp, on_low_hp, on_low_mp]
    f_ratios = [0.0, 0.0, 0.0]
    f_last_alerts = [0.0, 0.0, 0.0]

    # Falka HP heal (press4 when HP < 70%)
    f_hp_heal_threshold = 0.70
    f_hp_heal_cd = 0.0
    f_last_hp_heal = 0.0

    # Enemy state machine
    SEARCHING, ATTACKING, IDLE = "searching", "attacking", "idle"
    e_state = SEARCHING
    e_hp: float = -1.0
    e_tab_attempts = 0
    e_max_tabs = 8
    e_last_attack = 0.0
    e_attack_cd = 0.0

    if get_ocr_engine() is None:
        print("[ERROR] Install: pip install rapidocr-onnxruntime")
        return

    frame = 0

    with mss.mss() as sct:
        f_mon = {"left": cfg.widget_left, "top": cfg.widget_top,
                 "width": cfg.widget_width, "height": cfg.widget_height}
        e_mon = {"left": cfg.enemy_left, "top": cfg.enemy_top,
                 "width": cfg.enemy_width, "height": cfg.enemy_height}

        print("Monitor started (Falka + Enemy). Press Ctrl+C or Esc to stop.")
        while True:
            e_shot = np.array(sct.grab(e_mon))[:, :, :3]

            frame += 1
            # 2-phase stagger: enemy on odd, Falka on even
            do_enemy_ocr = (frame % 2 == 1)
            do_falka_ocr = (frame % 2 == 0)

            # Grab Falka only when needed (OCR frame or preview)
            if do_falka_ocr or cfg.show_preview:
                f_shot = np.array(sct.grab(f_mon))[:, :, :3]

            # ---- Fast pixel checks (every frame) ----
            now = time.time()
            if e_state == ATTACKING:
                if detect_enemy_dead(e_shot):
                    print(f"\n[ENEMY] Pixel: dead — TAB")
                    send_pico_command(cfg, "presstab")
                    e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0
                elif detect_target_type(e_shot) == "player":
                    print(f"\n[ENEMY] Pixel: PLAYER — S + TAB")
                    send_pico_command(cfg, "presss")
                    send_pico_command(cfg, "presstab")
                    e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0
                elif (now - e_last_attack) >= e_attack_cd:
                    print(f"\n[ENEMY] Re-A ({e_hp:.0%})")
                    send_pico_command(cfg, "pressa")
                    e_last_attack, e_attack_cd = now, random.uniform(0.2, 0.7)

            # ---- Enemy OCR ----
            run_e_ocr = (e_state == ATTACKING and frame % 5 == 0) or \
                        (e_state in (SEARCHING, IDLE) and do_enemy_ocr)

            if run_e_ocr:
                has_enemy, hp_val = ocr_enemy_widget(e_shot)
                now = time.time()

                if e_state == SEARCHING:
                    if has_enemy and hp_val is not None and hp_val > 0.01:
                        tgt = detect_target_type(e_shot)
                        if tgt == "player":
                            print(f"\n[ENEMY] PLAYER — S + TAB")
                            send_pico_command(cfg, "presss")
                            send_pico_command(cfg, "presstab")
                            e_hp = -1.0
                        else:
                            e_hp, e_state, e_tab_attempts = hp_val, ATTACKING, 0
                            on_enemy_alive(e_hp, cfg)
                            e_last_attack, e_attack_cd = now, random.uniform(0.2, 0.7)
                    else:
                        e_tab_attempts += 1
                        if e_tab_attempts > e_max_tabs:
                            print(f"\n[ENEMY] {e_max_tabs} TABs failed — idle")
                            alert_beep()
                            e_state = IDLE
                        else:
                            print(f"\n[ENEMY] TAB ({e_tab_attempts}/{e_max_tabs})")
                            send_pico_command(cfg, "presstab")

                elif e_state == ATTACKING:
                    if has_enemy and hp_val is not None:
                        if hp_val <= 0.01:
                            print(f"\n[ENEMY] Dead — TAB")
                            send_pico_command(cfg, "presstab")
                            e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0
                        else:
                            e_hp = hp_val
                    else:
                        e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0

                elif e_state == IDLE:
                    if has_enemy and hp_val is not None and hp_val > 0.01:
                        tgt = detect_target_type(e_shot)
                        if tgt == "player":
                            print(f"\n[ENEMY] PLAYER (idle) — S + TAB")
                            send_pico_command(cfg, "presss")
                            send_pico_command(cfg, "presstab")
                            e_hp = -1.0
                        else:
                            e_hp, e_state, e_tab_attempts = hp_val, ATTACKING, 0
                            on_enemy_alive(e_hp, cfg)
                            e_last_attack, e_attack_cd = now, random.uniform(0.2, 0.7)

            # ---- Falka OCR ----
            if do_falka_ocr:
                f_ocr = ocr_full_widget(f_shot)
                for i in range(min(len(f_ocr), 3)):
                    f_ratios[i] = f_ocr[i]
                now = time.time()
                for i in range(3):
                    if f_ratios[i] < f_thresholds[i] and (now - f_last_alerts[i]) >= cfg.alert_cooldown_sec:
                        alert_beep()
                        f_actions[i](f_ratios[i])
                        f_last_alerts[i] = now

                # Heal press4 when Falka HP < 70%
                if f_ratios[1] < f_hp_heal_threshold and (now - f_last_hp_heal) >= f_hp_heal_cd:
                    print(f"\n[FALKA] HP {f_ratios[1]:.0%} < 70% — press4")
                    send_pico_command(cfg, "press4")
                    f_last_hp_heal, f_hp_heal_cd = now, random.uniform(0.5, 1.0)

                # Stop if Falka HP hits 0
                if f_ratios[1] <= 0.0:
                    print(f"\n[FALKA] HP is 0% — stopping script.")
                    alert_beep()
                    break

            # ---- Status line ----
            cp_r, hp_r, mp_r = f_ratios
            e_str = f"{e_hp:.0%}" if e_hp >= 0 else "--"
            print(f"\rFalka CP:{cp_r:.0%} HP:{hp_r:.0%} MP:{mp_r:.0%}  Enemy HP:{e_str}   ", end="")

            if cfg.show_preview:
                render_preview(f_shot, e_shot, cp_r, hp_r, mp_r, e_hp)
                if cv2.waitKey(1) & 0xFF == 27:
                    break

    if cfg.show_preview:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
