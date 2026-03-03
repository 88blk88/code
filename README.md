# Game Bot — Auto-targeting & Stat Monitor

Windows-only bot that reads game UI widgets via screen capture + OCR, detects enemies via pixel analysis, and sends keyboard commands through a Raspberry Pi Pico 2 W acting as a USB HID device.

## Features

- **Player stat monitoring** — reads CP/HP/MP via OCR, alerts on low values
- **Pet stat monitoring** — reads pet HP/MP, auto-heals when low
- **Enemy detection** — pixel-based HP bar detection + OCR confirmation
- **Auto-targeting** — TAB cycles targets, attacks automatically, detects stuck/dead enemies
- **Buff scheduling** — periodic key presses on configurable intervals
- **Event popup detection** — OCR watches screen centre for popup keywords, auto-pauses bot
- **Multi-window support** — optional focus switching to a secondary game window
- **Preview window** — live overlay showing captured widgets and parsed values

## Requirements

- **OS:** Windows 10/11 (uses Win32 API for window tracking and DPI awareness)
- **Python:** 3.12+ (tested on 3.14.2)
- **Hardware:** Raspberry Pi Pico 2 W running CircuitPython with `adafruit_hid` library

## Setup

### 1. Python environment

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Pico firmware

The Pico acts as a USB HID keyboard — the PC sends commands over WiFi or serial, and the Pico types the corresponding keys into the game.

1. Flash CircuitPython onto your Pico 2 W
2. Install `adafruit_hid` library (copy `adafruit_hid` folder to `CIRCUITPY/lib/`)
3. Copy `pico_wifi/code.py` and `pico_wifi/settings.toml` to the Pico's `CIRCUITPY` drive
4. Edit `settings.toml` with your WiFi credentials:
   ```toml
   WIFI_SSID = "your_network"
   WIFI_PASSWORD = "your_password"
   ```
5. Note the IP address printed on the Pico's serial console at startup

For serial mode, use `pico_wifi/code_serial.py` instead and set `"transport": "serial"` in settings.

### 3. Configuration

Edit `config/settings.json`. The key sections are:

**Game window** — set this to a substring of your game window title:
```json
"game_window_title": "MyGame",
"focus_window_titles": ["MyGame", "Monitor Preview"]
```

**Widget positions** — pixel coordinates relative to the game window's client area. Run the calibration tool to set these automatically:
```bash
python src/calibrate.py
```
This takes a screenshot, lets you drag-select the Player, Pet, and Enemy widgets, and saves coordinates to `config/settings.json`.

**Enemy bar pixel thresholds** — fine-tune with:
```bash
python src/calibrate_enemy_bar.py
```

**Pico connection:**
```json
"transport": "serial",
"serial_port": "COM5",
"serial_baud": 115200
```
Or for WiFi:
```json
"transport": "wifi",
"pico_ip": "192.168.x.x",
"pico_port": 9999
```

**Key bindings** — Pico command strings (e.g. `"press6"`, `"presstab"`, `"pressbacktick"`). Add `"add_shift": true` to send Shift+key.

**Buff timers** — intervals in seconds for periodic skill presses:
```json
"buff1_interval_sec": 240,
"buff2_interval_sec": 540,
"buff3_interval_sec": 1140
```

See `src/core.py` `MonitorConfig` class for all available settings and their defaults.

## Usage

```bash
python src/hp_ns.py
```

Options:
- `--no-preview` — disable the preview window
- `--pick-up` — press pick-up key after each kill before targeting next enemy

### Hotkeys (while running)

| Key | Action |
|-----|--------|
| **F4** | Toggle pause/resume |
| **F5** | Force resume (clears event pause too) |
| **F6** | Trigger manual focus-switch test |
| **Esc** | Stop the bot |
| **Ctrl+C** | Stop the bot |

## Project Structure

```
config/settings.json       — all configuration (widget positions, thresholds, keys, timers)
src/
  core.py                  — shared utilities: config, transport, OCR engine, pixel checks
  ocr_thread.py            — background OCR workers (EventWatcher, WidgetOCRWorker)
  hp_ns.py                 — main bot loop (state machine, combat logic, preview)
  calibrate.py             — interactive widget position calibration tool
  calibrate_enemy_bar.py   — enemy bar pixel threshold calibration tool
pico_wifi/
  code.py                  — Pico 2 W WiFi firmware (CircuitPython)
  code_serial.py           — Pico 2 W serial firmware (CircuitPython)
  settings.toml            — WiFi credentials for the Pico
  test_wifi.py             — test script to verify Pico WiFi connection
logs/                      — rotating bot logs (auto-created)
```
