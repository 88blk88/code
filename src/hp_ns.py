"""Game stat monitor — Player (CP/HP/MP) + Pet (HP/MP) + Enemy targeting.

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
    on_low_pet_hp, on_low_pet_mp,
    on_enemy_alive,
    get_ocr_engine, ocr_full_widget, ocr_pet_widget, ocr_enemy_widget,
    enemy_bar_empty, enemy_widget_is_player,
)

from ocr_thread import EventWatcher

TAB_COOLDOWN_SEC = 1.5    # Minimum seconds between TAB presses
ATTACK_CD_MIN = 3.0       # Attack cooldown range (seconds)
ATTACK_CD_MAX = 4.0
STUCK_ATTACKS_REQUIRED = 2  # Consecutive attacks with no HP change before declaring stuck

last_tab_time = 0.0



# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def render_preview(
    player_widget: np.ndarray, pet_widget: np.ndarray, e_widget: np.ndarray,
    cp: float, hp: float, mp: float,
    pet_hp: float, pet_mp: float,
    enemy_hp: float,
) -> tuple[int, int]:
    preview = player_widget.copy()
    h = preview.shape[0]

    labels = [
        (f"Player CP {cp:.1%}", (0, 215, 255)),
        (f"Player HP {hp:.1%}", (0, 0, 255)),
        (f"Player MP {mp:.1%}", (255, 150, 0)),
        (f"Pet    HP {pet_hp:.1%}", (0, 0, 255)),
        (f"Pet    MP {pet_mp:.1%}", (255, 150, 0)),
        (f"Enemy  HP {enemy_hp:.0%}" if enemy_hp >= 0 else "Enemy  HP --", (0, 180, 0)),
    ]

    spacer = np.full((h, 4, 3), 40, dtype=np.uint8)
    parts = [preview]
    if pet_widget.shape[0] > 0 and pet_widget.shape[1] > 0:
        parts += [spacer, cv2.resize(pet_widget, (pet_widget.shape[1], h))]
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
    return scaled.shape[1], scaled.shape[0]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Player + Pet + Enemy monitor.")
    parser.add_argument("--no-preview", action="store_true", help="Disable preview window.")
    parser.add_argument("--pick-up", action="store_true", help="Press 7 (Pick Up) immediately after enemy dies, before TAB.")
    args = parser.parse_args()

    cfg = load_config()
    if args.no_preview:
        cfg.show_preview = False
    pick_up_mode: bool = args.pick_up

    # --- Event Watcher: monitors centre screen for popup keywords, auto-pauses bot ---
    _event_watcher = EventWatcher(interval=1.0)
    _event_watcher.start()
    last_event_check = 0.0
    EVENT_CHECK_SEC = 3.0

    # Player bars
    player_thresholds = [cfg.cp_threshold, cfg.hp_threshold, cfg.mp_threshold]
    player_actions = [on_low_cp, on_low_hp, on_low_mp]
    player_ratios = [1.0, 1.0, 1.0]
    player_last_alerts = [0.0, 0.0, 0.0]

    # Pet bars
    pet_thresholds = [cfg.pet_hp_threshold, cfg.pet_mp_threshold]
    pet_ratios = [1.0, 1.0]
    pet_last_alerts = [0.0, 0.0]
    # Pet HP healing threshold is editable via settings
    pet_hp_heal_threshold = cfg.pet_hp_heal_threshold
    pet_hp_heal_cd = 0.0
    pet_last_hp_heal = 0.0
    pet_actions = [lambda r: on_low_pet_hp(r, cfg), on_low_pet_mp]
    pet_present: bool = True      # updated by periodic presence check
    last_pet_check: float = 0.0
    PET_PRESENCE_CHECK_SEC = 10.0

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
    e_last_saw_enemy: float = time.time()  # avoid false "not seen" on first frames

    e_idle_since = 0.0
    e_search_has_enemy_start: float = 0.0  # time when has_enemy first became True in SEARCHING
    e_last_ocr_success: float = 0.0        # time of last successful enemy OCR reading
    IDLE_RESUME_SEC = 3.0

    if get_ocr_engine() is None:
        print("[ERROR] Install: pip install rapidocr-onnxruntime")
        return

    frame = 0

    global last_tab_time
    with mss.mss() as sct:
        player_mon = {"left": cfg.widget_left, "top": cfg.widget_top,
                      "width": cfg.widget_width, "height": cfg.widget_height}
        pet_mon = {"left": cfg.pet_left, "top": cfg.pet_top,
                   "width": cfg.pet_width, "height": cfg.pet_height}
        e_mon = {"left": cfg.enemy_left, "top": cfg.enemy_top,
                 "width": cfg.enemy_width, "height": cfg.enemy_height}

        pet_shot = np.zeros((max(1, cfg.pet_height), max(1, cfg.pet_width), 3), dtype=np.uint8)

        screen_h = sct.monitors[1]["height"]
        screen_w = sct.monitors[1]["width"]
        last_preview_update = 0.0
        preview_positioned = False
        PREVIEW_INTERVAL = 0.10  # seconds between preview redraws (~10 FPS)

        bot_paused = [False]

        def toggle_pause():
            bot_paused[0] = not bot_paused[0]
            print(f"\n[BOT] {'PAUSED' if bot_paused[0] else 'RESUMED'} (F4)")

        def unpause():
            if bot_paused[0]:
                bot_paused[0] = False
                print(f"\n[BOT] RESUMED (F5)")

        keyboard.add_hotkey('f4', toggle_pause)
        keyboard.add_hotkey('f5', unpause)
        print("Monitor started (Player + Pet + Enemy). Press F4 to pause/resume, F5 to resume. Press Ctrl+C or Esc to stop.")

        while True:
            e_shot = np.array(sct.grab(e_mon))[:, :, :3]

            frame += 1
            do_enemy_ocr = (frame % 2 == 0)
            do_player_ocr = (frame % 6 == 1)
            do_pet_ocr = (frame % 6 == 4)

            # Grab Player / Pet only when needed (OCR frame or preview redraw due)
            preview_due = cfg.show_preview and (time.time() - last_preview_update >= PREVIEW_INTERVAL)
            if do_player_ocr or preview_due:
                player_shot = np.array(sct.grab(player_mon))[:, :, :3]
            if (do_pet_ocr or preview_due) and cfg.pet_enabled and cfg.pet_width > 0:
                pet_shot = np.array(sct.grab(pet_mon))[:, :, :3]

            # --- Event Watcher check ---
            if time.time() - last_event_check >= EVENT_CHECK_SEC:
                last_event_check = time.time()
                if _event_watcher.is_event_found():
                    if not bot_paused[0]:
                        bot_paused[0] = True
                        print("\n[EVENT] Popup detected — bot paused. Press F4 to resume.")
                    _event_watcher.clear_event()

            if bot_paused[0]:
                if preview_due:
                    win_w, win_h = render_preview(player_shot, pet_shot, e_shot, player_ratios[0], player_ratios[1], player_ratios[2], pet_ratios[0], pet_ratios[1], e_hp)
                    last_preview_update = time.time()
                    if not preview_positioned:
                        cv2.moveWindow("Monitor Preview", max(0, screen_w - win_w), max(0, screen_h - win_h - 148))
                        preview_positioned = True
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

            now = time.time()
            if e_state == ATTACKING:
                if (now - e_last_attack) >= e_attack_cd:
                    # Stuck detection: HP unchanged across consecutive attacks.
                    # Only counts when OCR gave a fresh reading recently (stale data would false-trigger).
                    ocr_recent = (now - e_last_ocr_success) < e_attack_cd * 2
                    if ocr_recent and e_hp_at_attack >= 0 and e_hp >= 0 and abs(e_hp - e_hp_at_attack) < 0.01:
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

            # Self-target guard: if the enemy widget shows our own CP bar colour,
            # immediately TAB away and reset to SEARCHING.
            if has_enemy and enemy_widget_is_player(e_shot):
                print(f"\n[SELF-TARGET] Player bar detected in enemy widget — TAB to skip")
                send_pico_command(cfg, cfg.key_tab)
                last_tab_time = now
                e_state, e_tab_attempts, e_hp, empty_frames = SEARCHING, 0, -1.0, 0
                e_last_attack, e_stuck_attacks, e_hp_at_attack = 0.0, 0, -1.0
                e_search_has_enemy_start = 0.0
                e_last_ocr_success = 0.0
                has_enemy = False
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
                e_last_ocr_success = now

            print(f"[ENEMY] state={e_state} | has_enemy={has_enemy} hp_str={hp_str!r} hp_str_val={f'{hp_str_val:.0%}' if hp_str_val is not None else None} e_hp={f'{e_hp:.0%}' if e_hp >= 0 else '--'}")

            if bot_paused[0]:
                continue
            if e_state == SEARCHING:
                if has_enemy:
                    # If enemy bar is present, do not press TAB, even if OCR fails
                    if hp_str_val is not None and hp_str_val > 0.01:
                        print(f"\n[STATE] SEARCHING -> ATTACKING (hp_str_val={hp_str_val:.0%})")
                        e_state, e_tab_attempts = ATTACKING, 0
                        e_search_has_enemy_start = 0.0
                        e_hp_at_attack = e_hp  # seed stuck detection from first attack
                        last_tab_time = now
                        if e_last_attack == 0.0:
                            # No TAB was pressed; fire initial attack now
                            on_enemy_alive(e_hp, cfg)
                            e_last_attack, e_attack_cd = now, random.uniform(ATTACK_CD_MIN, ATTACK_CD_MAX)
                        # else: TAB already set the cooldown; periodic ATTACKING block handles next press6
                    else:
                        # OCR failed but bar is visible — track how long this has been the case
                        if e_search_has_enemy_start == 0.0:
                            e_search_has_enemy_start = now
                        elif now - e_search_has_enemy_start >= 3.0:
                            print(f"\n[STATE] SEARCHING -> ATTACKING (OCR timeout, bar visible 3s)")
                            e_state, e_tab_attempts = ATTACKING, 0
                            e_search_has_enemy_start = 0.0
                            last_tab_time = now
                            if e_last_attack == 0.0:
                                on_enemy_alive(e_hp if e_hp >= 0 else 0.5, cfg)
                                e_last_attack, e_attack_cd = now, random.uniform(ATTACK_CD_MIN, ATTACK_CD_MAX)
                else:
                    e_search_has_enemy_start = 0.0
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
                # wait 0.5s before declaring dead, unless OCR explicitly confirmed 0% (skip wait).
                ocr_confirmed_dead = (hp_str_val is not None and hp_str_val <= 0.01)
                low_hp_wait = (0.0 <= e_hp < 0.15) and (now - e_last_saw_enemy) < 1.0 and not ocr_confirmed_dead
                if not has_enemy and empty_frames >= EMPTY_FRAMES_REQUIRED and not low_hp_wait:
                    print(f"\n[STATE] ATTACKING -> SEARCHING (pixel bar empty x{empty_frames}, last_saw={now - e_last_saw_enemy:.1f}s ago)")
                    if pick_up_mode:
                        print(f"[PICK_UP] {cfg.key_pick_up}")
                        send_pico_command(cfg, cfg.key_pick_up)
                        pick_up_delay = random.uniform(0.4, 0.6)
                        last_tab_time = now - TAB_COOLDOWN_SEC + pick_up_delay
                        print(f"[PICK_UP] TAB delayed {pick_up_delay:.2f}s")
                    else:
                        last_tab_time = now - TAB_COOLDOWN_SEC
                    e_state, e_tab_attempts, e_hp, empty_frames = SEARCHING, 0, -1.0, 0
                    e_last_attack, e_stuck_attacks, e_hp_at_attack, e_last_saw_enemy = 0.0, 0, -1.0, 0.0
                    e_search_has_enemy_start = 0.0
                    e_last_ocr_success = 0.0
                elif has_enemy and hp_str_val is not None and prev_e_hp >= 0 and hp_str_val > prev_e_hp + 0.20:
                    print(f"\n[ENEMY] Target change detected ({prev_e_hp:.0%} -> {hp_str_val:.0%}) — re-engaging")
                    on_enemy_alive(e_hp, cfg)
                    e_last_attack, e_attack_cd = now, random.uniform(ATTACK_CD_MIN, ATTACK_CD_MAX)

            elif e_state == IDLE:
                if has_enemy and hp_str_val is not None and hp_str_val > 0.01:
                    print(f"\n[STATE] IDLE -> ATTACKING (hp_str_val={hp_str_val:.0%})")
                    e_state, e_tab_attempts = ATTACKING, 0
                    e_hp_at_attack = e_hp  # seed stuck detection from first attack
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
                    e_search_has_enemy_start = 0.0
                elif now - e_idle_since >= IDLE_RESUME_SEC:
                    print(f"\n[STATE] IDLE -> SEARCHING (timeout {IDLE_RESUME_SEC}s — resuming search)")
                    send_pico_command(cfg, cfg.key_target_switch)
                    last_tab_time = now
                    e_state, e_tab_attempts, e_hp = SEARCHING, 0, -1.0
                    e_search_has_enemy_start = 0.0

            # ---- Player OCR ----
            if do_player_ocr:
                player_ocr = ocr_full_widget(player_shot)
                for i in range(min(len(player_ocr), 3)):
                    player_ratios[i] = player_ocr[i]
                if bot_paused[0]:
                    continue
                now = time.time()
                for i in range(3):
                    if player_ratios[i] < player_thresholds[i] and (now - player_last_alerts[i]) >= cfg.alert_cooldown_sec:
                        alert_beep()
                        player_actions[i](player_ratios[i])
                        player_last_alerts[i] = now

                # Stop if Player HP hits 0
                if player_ratios[1] <= 0.0:
                    print(f"\n[PLAYER] HP is 0% \u2014 stopping script.")
                    alert_beep()
                    break

            # ---- Pet OCR ----
            if do_pet_ocr:
                pet_ocr = ocr_pet_widget(pet_shot)
                now = time.time()
                # Periodic presence check every PET_PRESENCE_CHECK_SEC
                if now - last_pet_check >= PET_PRESENCE_CHECK_SEC:
                    last_pet_check = now
                    new_present = len(pet_ocr) >= 2
                    if new_present != pet_present:
                        pet_present = new_present
                        print(f"\n[PET] Widget {'detected' if pet_present else 'not found'} — reactions {'enabled' if pet_present else 'disabled'}")
                if bot_paused[0]:
                    continue
                if pet_present:
                    for i in range(min(len(pet_ocr), 2)):
                        pet_ratios[i] = pet_ocr[i]
                    for i in range(2):
                        if pet_ratios[i] < pet_thresholds[i] and (now - pet_last_alerts[i]) >= cfg.alert_cooldown_sec:
                            alert_beep()
                            pet_actions[i](pet_ratios[i])
                            pet_last_alerts[i] = now
                    # Pet HP heal (press1) when HP < threshold
                    if pet_ratios[0] < pet_hp_heal_threshold and (now - pet_last_hp_heal) >= pet_hp_heal_cd:
                        print(f"\n[PET] HP {pet_ratios[0]:.0%} < {pet_hp_heal_threshold:.0%} — {cfg.key_heal_pet}")
                        send_pico_command(cfg, cfg.key_heal_pet)
                        pet_last_hp_heal, pet_hp_heal_cd = now, random.uniform(0.5, 1.0)

            # ---- Status line ----
            cp_r, hp_r, mp_r = player_ratios
            pet_hp_r, pet_mp_r = pet_ratios
            e_str = f"{e_hp:.1%}" if e_hp >= 0 else "--"
            print(f"\rPlayer CP:{cp_r:.0%} HP:{hp_r:.0%} MP:{mp_r:.0%}  Pet HP:{pet_hp_r:.0%} MP:{pet_mp_r:.0%}  Enemy HP:{e_str}   ", end="")


            if preview_due:
                win_w, win_h = render_preview(player_shot, pet_shot, e_shot, cp_r, hp_r, mp_r, pet_hp_r, pet_mp_r, e_hp)
                last_preview_update = time.time()
                if not preview_positioned:
                    cv2.moveWindow("Monitor Preview", max(0, screen_w - win_w), max(0, screen_h - win_h - 148))
                    preview_positioned = True
            if cv2.waitKey(1) & 0xFF == 27:
                break

    _event_watcher.stop()

    if cfg.show_preview:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
