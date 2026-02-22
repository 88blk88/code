"""Game stat monitor — Falka (CP/HP/MP) + Nightshade (HP/MP) + Enemy targeting.

Run:  python src/hp_ns.py [--no-preview]
"""

import time
import random
import argparse

import keyboard

import cv2
import mss
import numpy as np

from core import (
    MonitorConfig, load_config, send_pico_command, alert_beep,
    on_low_cp, on_low_hp, on_low_mp,
    on_low_nightshade_hp, on_low_nightshade_mp,
    on_enemy_alive,
    get_ocr_engine, ocr_full_widget, ocr_nightshade_widget, ocr_enemy_widget,
    enemy_bar_empty,
)

from ocr_thread import EventWatcher

TAB_COOLDOWN_SEC = 1.5    # Minimum seconds between TAB presses
ATTACK_CD_MIN = 3.0       # Attack cooldown range (seconds)
ATTACK_CD_MAX = 4.0
STUCK_ATTACKS_REQUIRED = 3  # Consecutive attacks with no HP change before declaring stuck

last_tab_time = 0.0



# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def render_preview(
    f_widget: np.ndarray, n_widget: np.ndarray, e_widget: np.ndarray,
    cp: float, hp: float, mp: float,
    ns_hp: float, ns_mp: float,
    enemy_hp: float,
) -> None:
    preview = f_widget.copy()
    h = preview.shape[0]

    labels = [
        (f"Falka  CP {cp:.1%}", (0, 215, 255)),
        (f"Falka  HP {hp:.1%}", (0, 0, 255)),
        (f"Falka  MP {mp:.1%}", (255, 150, 0)),
        (f"Night  HP {ns_hp:.1%}", (0, 0, 255)),
        (f"Night  MP {ns_mp:.1%}", (255, 150, 0)),
        (f"Enemy  HP {enemy_hp:.0%}" if enemy_hp >= 0 else "Enemy  HP --", (0, 180, 0)),
    ]

    spacer = np.full((h, 4, 3), 40, dtype=np.uint8)
    parts = [preview]
    if n_widget.shape[0] > 0 and n_widget.shape[1] > 0:
        parts += [spacer, cv2.resize(n_widget, (n_widget.shape[1], h))]
    if e_widget.shape[0] > 0 and e_widget.shape[1] > 0:
        parts += [spacer.copy(), cv2.resize(e_widget, (e_widget.shape[1], h))]
    combined = cv2.hconcat(parts)

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
    parser = argparse.ArgumentParser(description="Falka + Nightshade + Enemy monitor.")
    parser.add_argument("--no-preview", action="store_true", help="Disable preview window.")
    parser.add_argument("--spoil", action="store_true", help="Press 7 (Spoil) immediately after enemy dies, before TAB.")
    args = parser.parse_args()

    cfg = load_config()
    if args.no_preview:
        cfg.show_preview = False
    spoil_mode: bool = args.spoil

    # --- Event Watcher: monitors centre screen for popup keywords, auto-pauses bot ---
    _event_watcher = EventWatcher(interval=1.0)
    _event_watcher.start()
    last_event_check = 0.0
    EVENT_CHECK_SEC = 3.0

    # Falka bars
    f_thresholds = [cfg.cp_threshold, cfg.hp_threshold, cfg.mp_threshold]
    f_actions = [on_low_cp, on_low_hp, on_low_mp]
    f_ratios = [1.0, 1.0, 1.0]
    f_last_alerts = [0.0, 0.0, 0.0]

    # Nightshade bars
    n_thresholds = [cfg.nightshade_hp_threshold, cfg.nightshade_mp_threshold]
    n_ratios = [1.0, 1.0]
    n_last_alerts = [0.0, 0.0]
    # Nightshade HP healing threshold is now editable via settings
    n_hp_heal_threshold = cfg.nightshade_hp_heal_threshold
    n_hp_heal_cd = 0.0
    n_last_hp_heal = 0.0
    n_actions = [lambda r: on_low_nightshade_hp(r, cfg), on_low_nightshade_mp]
    ns_present: bool = True      # updated by periodic presence check
    last_ns_check: float = 0.0
    NS_PRESENCE_CHECK_SEC = 10.0

    # Scheduled skill presses
    last_press8 = 0.0
    last_press9 = 0.0
    last_press10 = 0.0

    # Enemy OCR cache (updated every 3rd frame; has_enemy always from pixel check)
    last_hp_str: str | None = None
    empty_frames: int = 0          # consecutive frames with bar empty
    EMPTY_FRAMES_REQUIRED = 2      # must be empty this many frames before switching

    # Enemy state machine
    SEARCHING, ATTACKING, IDLE = "searching", "attacking", "idle"
    e_state = SEARCHING
    e_hp: float = -1.0
    e_tab_attempts = 0
    e_max_tabs = 2
    e_last_attack = 0.0
    e_attack_cd = 0.0
    e_stuck_attacks = 0
    e_hp_at_attack: float = -1.0   # e_hp recorded at last press6
    e_last_saw_enemy: float = 0.0  # last time has_enemy was True

    e_idle_since = 0.0
    IDLE_RESUME_SEC = 3.0

    if get_ocr_engine() is None:
        print("[ERROR] Install: pip install rapidocr-onnxruntime")
        return

    frame = 0

    global last_tab_time
    with mss.mss() as sct:
        f_mon = {"left": cfg.widget_left, "top": cfg.widget_top,
                 "width": cfg.widget_width, "height": cfg.widget_height}
        n_mon = {"left": cfg.nightshade_left, "top": cfg.nightshade_top,
                 "width": cfg.nightshade_width, "height": cfg.nightshade_height}
        e_mon = {"left": cfg.enemy_left, "top": cfg.enemy_top,
                 "width": cfg.enemy_width, "height": cfg.enemy_height}

        n_shot = np.zeros((max(1, cfg.nightshade_height), max(1, cfg.nightshade_width), 3), dtype=np.uint8)

        bot_paused = [False]

        def toggle_pause():
            bot_paused[0] = not bot_paused[0]
            print(f"\n[BOT] {'PAUSED' if bot_paused[0] else 'RESUMED'} (F4)")

        keyboard.add_hotkey('f4', toggle_pause)
        print("Monitor started (Falka + Nightshade + Enemy). Press F4 to pause/resume. Press Ctrl+C or Esc to stop.")

        while True:
            e_shot = np.array(sct.grab(e_mon))[:, :, :3]

            frame += 1
            do_enemy_ocr = (frame % 2 == 0)
            do_falka_ocr = (frame % 6 == 1)
            do_night_ocr = (frame % 6 == 4)

            # Grab Falka / Nightshade only when needed (OCR frame or preview)
            if do_falka_ocr or cfg.show_preview:
                f_shot = np.array(sct.grab(f_mon))[:, :, :3]
            if (do_night_ocr or cfg.show_preview) and cfg.nightshade_width > 0:
                n_shot = np.array(sct.grab(n_mon))[:, :, :3]

            # --- Event Watcher check ---
            if time.time() - last_event_check >= EVENT_CHECK_SEC:
                last_event_check = time.time()
                if _event_watcher.is_event_found():
                    if not bot_paused[0]:
                        bot_paused[0] = True
                        print("\n[EVENT] Popup detected — bot paused. Press F4 to resume.")
                    _event_watcher.clear_event()

            if bot_paused[0]:
                if cfg.show_preview:
                    render_preview(f_shot, n_shot, e_shot, f_ratios[0], f_ratios[1], f_ratios[2], n_ratios[0], n_ratios[1], e_hp)
                    if cv2.waitKey(1) & 0xFF == 27:
                        break
                time.sleep(0.05)
                continue

            # ---- Scheduled skill presses (highest priority) ----
            now = time.time()
            if now - last_press10 >= cfg.buff3_interval_sec:
                print(f"\n[BUFF3] {cfg.key_buff3} ({cfg.buff3_interval_sec:.0f}s)")
                send_pico_command(cfg, cfg.key_buff3)
                last_press10 = now
            if now - last_press9 >= cfg.buff2_interval_sec:
                print(f"\n[BUFF2] {cfg.key_buff2} ({cfg.buff2_interval_sec:.0f}s)")
                send_pico_command(cfg, cfg.key_buff2)
                last_press9 = now
            if now - last_press8 >= cfg.buff1_interval_sec:
                print(f"\n[BUFF1] {cfg.key_buff1} ({cfg.buff1_interval_sec:.0f}s)")
                send_pico_command(cfg, cfg.key_buff1)
                last_press8 = now

            if bot_paused[0]:
                continue
            now = time.time()
            if e_state == ATTACKING:
                if (now - e_last_attack) >= e_attack_cd:
                    # Stuck detection: HP unchanged across consecutive attacks
                    if e_hp_at_attack >= 0 and e_hp >= 0 and abs(e_hp - e_hp_at_attack) < 0.02:
                        e_stuck_attacks += 1
                        print(f"[STUCK] HP unchanged at {e_hp:.0%} (x{e_stuck_attacks}/{STUCK_ATTACKS_REQUIRED})")
                    else:
                        e_stuck_attacks = 0
                    if e_hp >= 0:
                        e_hp_at_attack = e_hp

                    if e_stuck_attacks >= STUCK_ATTACKS_REQUIRED:
                        print(f"\n[STUCK] Enemy unreachable — {cfg.key_target_switch} to switch target")
                        send_pico_command(cfg, cfg.key_target_switch)
                        last_tab_time = now
                        e_state, e_tab_attempts, e_hp, empty_frames = SEARCHING, 0, -1.0, 0
                        e_last_attack, e_attack_cd = now, random.uniform(ATTACK_CD_MIN, ATTACK_CD_MAX)
                        e_stuck_attacks, e_hp_at_attack = 0, -1.0
                    else:
                        print(f"\n[ENEMY] Re-A ({e_hp:.0%})")
                        send_pico_command(cfg, cfg.key_attack)
                        e_last_attack, e_attack_cd = now, random.uniform(ATTACK_CD_MIN, ATTACK_CD_MAX)

            # ---- Enemy detection ----
            # Primary: pixel check every frame. At low HP the bar is thin and can
            # look empty even while the enemy is alive; OCR is used to override.
            has_enemy = not enemy_bar_empty(e_shot)
            if has_enemy:
                e_last_saw_enemy = now
                if do_enemy_ocr:
                    _, _, hp_str = ocr_enemy_widget(e_shot)
                    last_hp_str = hp_str
                else:
                    hp_str = last_hp_str
            else:
                hp_str = None
                last_hp_str = None
                # At low HP pixel bar can look empty while enemy is still alive.
                # Run OCR on this frame to verify; if it reads HP > 0, override pixel.
                if e_state == ATTACKING and 0.0 <= e_hp < 0.15 and do_enemy_ocr:
                    _, ocr_hp, ocr_hp_str = ocr_enemy_widget(e_shot)
                    if ocr_hp is not None and ocr_hp > 0.01:
                        print(f"[PIXEL OVERRIDE] pixel=dead but OCR={ocr_hp:.0%} — treating as alive")
                        has_enemy = True
                        hp_str = ocr_hp_str
                        e_last_saw_enemy = now
            now = time.time()

            # Parse hp_str -> float for combat logic (more reliable: requires % sign)
            hp_str_val: float | None = None
            if hp_str is not None:
                try:
                    hp_str_val = float(hp_str.strip().replace('%', '').replace(' ', '')) / 100.0
                except ValueError:
                    print(f"[ENEMY PARSE] hp_str={hp_str!r} -> ValueError, hp_str_val remains None")

            # Update e_hp from hp_str (primary source)
            prev_e_hp = e_hp
            if hp_str_val is not None:
                e_hp = hp_str_val

            print(f"[ENEMY] state={e_state} | has_enemy={has_enemy} hp_str={hp_str!r} hp_str_val={f'{hp_str_val:.0%}' if hp_str_val is not None else None} e_hp={f'{e_hp:.0%}' if e_hp >= 0 else '--'}")

            if bot_paused[0]:
                continue
            if e_state == SEARCHING:
                if has_enemy:
                    # If enemy bar is present, do not press TAB, even if OCR fails
                    if hp_str_val is not None and hp_str_val > 0.01:
                        print(f"\n[STATE] SEARCHING -> ATTACKING (hp_str_val={hp_str_val:.0%})")
                        e_state, e_tab_attempts = ATTACKING, 0

                        last_tab_time = now
                        if e_last_attack == 0.0:
                            # No TAB was pressed; fire initial attack now
                            on_enemy_alive(e_hp, cfg)
                            e_last_attack, e_attack_cd = now, random.uniform(ATTACK_CD_MIN, ATTACK_CD_MAX)
                        # else: TAB already set the cooldown; periodic ATTACKING block handles next press6
                    # else: remain in SEARCHING, but do not press TAB
                else:
                    if now - last_tab_time >= TAB_COOLDOWN_SEC:
                        e_tab_attempts += 1
                        if e_tab_attempts > e_max_tabs:
                            print(f"\n[STATE] SEARCHING -> IDLE ({e_max_tabs} TABs failed)")
                            alert_beep()
                            send_pico_command(cfg, cfg.key_target_switch)
                            last_tab_time = now
                            e_state = IDLE
                            e_idle_since = now
                        else:
                            print(f"\n[ENEMY] TAB ({e_tab_attempts}/{e_max_tabs})")
                            send_pico_command(cfg, cfg.key_tab)
                            last_tab_time = now
                            e_last_attack, e_attack_cd = now, random.uniform(ATTACK_CD_MIN, ATTACK_CD_MAX)

            elif e_state == ATTACKING:
                if not has_enemy:
                    empty_frames += 1
                else:
                    empty_frames = 0
                # At very low HP the pixel bar looks empty due to thin bar width;
                # require the bar to be gone for 1.5s (not just a few frames) before declaring dead
                low_hp_wait = (0.0 <= e_hp < 0.15) and (now - e_last_saw_enemy) < 1.5
                if not has_enemy and empty_frames >= EMPTY_FRAMES_REQUIRED and not low_hp_wait:
                    print(f"\n[STATE] ATTACKING -> SEARCHING (pixel bar empty x{empty_frames}, last_saw={now - e_last_saw_enemy:.1f}s ago)")
                    if spoil_mode:
                        print(f"[SPOIL] {cfg.key_spoil}")
                        send_pico_command(cfg, cfg.key_spoil)
                        spoil_delay = random.uniform(0.4, 0.6)
                        last_tab_time = now - TAB_COOLDOWN_SEC + spoil_delay
                        print(f"[SPOIL] TAB delayed {spoil_delay:.2f}s")
                    else:
                        last_tab_time = now - TAB_COOLDOWN_SEC
                    e_state, e_tab_attempts, e_hp, empty_frames = SEARCHING, 0, -1.0, 0
                    e_last_attack, e_stuck_attacks, e_hp_at_attack, e_last_saw_enemy = 0.0, 0, -1.0, 0.0
                elif has_enemy and hp_str_val is not None and prev_e_hp >= 0 and hp_str_val > prev_e_hp + 0.20:
                    print(f"\n[ENEMY] Target change detected ({prev_e_hp:.0%} -> {hp_str_val:.0%}) — re-engaging")
                    on_enemy_alive(e_hp, cfg)
                    e_last_attack, e_attack_cd = now, random.uniform(ATTACK_CD_MIN, ATTACK_CD_MAX)

            elif e_state == IDLE:
                if has_enemy and hp_str_val is not None and hp_str_val > 0.01:
                    print(f"\n[STATE] IDLE -> ATTACKING (hp_str_val={hp_str_val:.0%})")
                    e_state, e_tab_attempts = ATTACKING, 0

                    last_tab_time = now
                    if e_last_attack == 0.0:
                        on_enemy_alive(e_hp, cfg)
                        e_last_attack, e_attack_cd = now, random.uniform(ATTACK_CD_MIN, ATTACK_CD_MAX)
                elif has_enemy and hp_str_val is not None and hp_str_val <= 0.01:
                    print(f"\n[STATE] IDLE -> SEARCHING (dead enemy at hp_str_val={hp_str_val:.0%}) — {cfg.key_target_switch} to resume")
                    if now - last_tab_time >= TAB_COOLDOWN_SEC:
                        send_pico_command(cfg, cfg.key_target_switch)
                        last_tab_time = now
                    e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0
                elif now - e_idle_since >= IDLE_RESUME_SEC:
                    print(f"\n[STATE] IDLE -> SEARCHING (timeout {IDLE_RESUME_SEC}s — resuming search)")
                    send_pico_command(cfg, cfg.key_target_switch)
                    last_tab_time = now
                    e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0

            # ---- Falka OCR ----
            if do_falka_ocr:
                f_ocr = ocr_full_widget(f_shot)
                for i in range(min(len(f_ocr), 3)):
                    f_ratios[i] = f_ocr[i]
                if bot_paused[0]:
                    continue
                now = time.time()
                for i in range(3):
                    if f_ratios[i] < f_thresholds[i] and (now - f_last_alerts[i]) >= cfg.alert_cooldown_sec:
                        alert_beep()
                        f_actions[i](f_ratios[i])
                        f_last_alerts[i] = now

                # Stop if Falka HP hits 0
                if f_ratios[1] <= 0.0:
                    print(f"\n[FALKA] HP is 0% \u2014 stopping script.")
                    alert_beep()
                    break

            # ---- Nightshade OCR ----
            if do_night_ocr:
                n_ocr = ocr_nightshade_widget(n_shot)
                now = time.time()
                # Periodic presence check every NS_PRESENCE_CHECK_SEC
                if now - last_ns_check >= NS_PRESENCE_CHECK_SEC:
                    last_ns_check = now
                    new_present = len(n_ocr) >= 2
                    if new_present != ns_present:
                        ns_present = new_present
                        print(f"\n[NIGHTSHADE] Widget {'detected' if ns_present else 'not found'} — reactions {'enabled' if ns_present else 'disabled'}")
                if bot_paused[0]:
                    continue
                if ns_present:
                    for i in range(min(len(n_ocr), 2)):
                        n_ratios[i] = n_ocr[i]
                    for i in range(2):
                        if n_ratios[i] < n_thresholds[i] and (now - n_last_alerts[i]) >= cfg.alert_cooldown_sec:
                            alert_beep()
                            n_actions[i](n_ratios[i])
                            n_last_alerts[i] = now
                    # Nightshade HP heal (press1) when HP < threshold
                    if n_ratios[0] < n_hp_heal_threshold and (now - n_last_hp_heal) >= n_hp_heal_cd:
                        print(f"\n[NIGHTSHADE] HP {n_ratios[0]:.0%} < {n_hp_heal_threshold:.0%} — {cfg.key_heal_ns}")
                        send_pico_command(cfg, cfg.key_heal_ns)
                        n_last_hp_heal, n_hp_heal_cd = now, random.uniform(0.5, 1.0)

            # ---- Status line ----
            cp_r, hp_r, mp_r = f_ratios
            nhp_r, nmp_r = n_ratios
            e_str = f"{e_hp:.0%}" if e_hp >= 0 else "--"
            print(f"\rFalka CP:{cp_r:.0%} HP:{hp_r:.0%} MP:{mp_r:.0%}  Night HP:{nhp_r:.0%} MP:{nmp_r:.0%}  Enemy HP:{e_str}   ", end="")


            if cfg.show_preview:
                render_preview(f_shot, n_shot, e_shot, cp_r, hp_r, mp_r, nhp_r, nmp_r, e_hp)
                if cv2.waitKey(1) & 0xFF == 27:
                    break

    _event_watcher.stop()

    if cfg.show_preview:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
