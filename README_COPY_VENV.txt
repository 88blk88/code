===========================================================
  SETUP & USAGE GUIDE
===========================================================

WHAT YOU NEED
-------------
- Python 3.14.2  (download from https://www.python.org/downloads/)
  During installation, tick the box "Add Python to PATH"
- The project folder (this folder: c:\code)
- A Pico 2 W connected via USB (serial) or WiFi


===========================================================
  STEP 1 — CREATE A VIRTUAL ENVIRONMENT (one time only)
===========================================================

A virtual environment is a private folder where Python
installs all the packages this project needs, without
touching the rest of your computer.

1. Open the Start menu and search for "Command Prompt".
   Right-click it and choose "Run as administrator".

2. Navigate to the project folder by typing:

     cd c:\code

   Then press Enter.

3. Create the virtual environment by typing:

     python -m venv .venv

   Then press Enter. Wait until the prompt returns.
   A new folder called ".venv" will appear — that is normal.

4. Activate the virtual environment by typing:

     .venv\Scripts\activate

   Then press Enter.
   You will see (.venv) appear at the start of the line.
   This means the virtual environment is active.

5. Install all required packages by typing:

     pip install -r requirements.txt

   Then press Enter. This will download and install
   everything. It may take a few minutes the first time.

   When it finishes you are ready to use the bot.

   NOTE: You only need to do steps 1-5 once.
         Next time, start from Step 2 below.


===========================================================
  STEP 2 — OPEN A TERMINAL BEFORE EACH SESSION
===========================================================

Every time you want to run the bot, do this first:

1. Open Command Prompt (search "cmd" in Start menu).

2. Navigate to the project folder:

     cd c:\code

3. Activate the virtual environment:

     .venv\Scripts\activate

   You should see (.venv) at the start of the line.
   You are now ready to run the bot.


===========================================================
  STEP 3 — CALIBRATE (first time, or after UI changes)
===========================================================

Calibration teaches the bot where the HP/MP bars are on
your screen. You must do this at least once, and again
any time the game window is resized or the UI changes.

1. Launch the game and log in. Make sure the game window
   is visible on screen (not minimised).

2. In your Command Prompt (with .venv active), type:

     python src/calibrate.py

   Then press Enter.

3. A screenshot of your screen will appear in a window.

4. Click and drag to draw a box around the PLAYER stats
   bar (CP / HP / MP area), then press ENTER or SPACE
   to confirm.

5. The tool will ask: "Do you want to monitor Pet? (y/N)"
   Type y and press Enter if you have a pet, otherwise
   just press Enter to skip.

   If you typed y, draw a box around the PET stats bar
   and press ENTER or SPACE to confirm.

6. Draw a box around the ENEMY HP bar, then press ENTER
   or SPACE to confirm.

7. The calibration data is saved automatically to:
     config\settings.json

   You do not need to edit that file manually.


===========================================================
  STEP 4 — CONFIGURE THE PICO CONNECTION
===========================================================

Open config\settings.json in Notepad and check the
"transport" setting:

  "transport": "serial"   — use this if the Pico is
                            connected via USB cable.
                            Also set the correct COM port:
                            "serial_port": "COM5"
                            (check Device Manager if unsure)

  "transport": "wifi"     — use this if the Pico is on WiFi.
                            Set the correct IP and port:
                            "pico_ip": "192.168.138.2"
                            "pico_port": 9999

Also set "game_window_title" to a word from your game
window's title bar (e.g. "Falka"), so the bot can find
and track the window automatically.


===========================================================
  STEP 5 — RUN THE BOT
===========================================================

In your Command Prompt (with .venv active), type:

     python src/hp_ns.py

Then press Enter. A small preview window will appear
showing the captured bars in real time.

HOTKEYS while the bot is running:
  F4  — Pause / Resume the bot
  F5  — Resume (also clears event-pause)
  ESC or Ctrl+C — Stop the bot

OPTIONAL FLAGS:
  --no-preview   Runs without the preview window (lighter)
  --pick-up      Presses the Pick Up key after each kill

  Example with flags:
     python src/hp_ns.py --no-preview --pick-up


===========================================================
  TROUBLESHOOTING
===========================================================

"python is not recognized..."
  -> Python is not installed or not added to PATH.
     Reinstall Python and tick "Add Python to PATH".

"No module named ..."
  -> The virtual environment is not active, or packages
     were not installed. Run steps 2 and 3 of SETUP again.

"Cannot connect to Pico"
  -> Check the cable / WiFi and the COM port or IP in
     config\settings.json.

"OCR not working / bars always show 100%"
  -> Re-run calibration (Step 3). Make sure the game is
     visible and not covered by other windows.

The bot auto-pauses when the game window loses focus.
It resumes automatically when you click back into the game.
