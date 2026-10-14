# Madden capture (chiaki-ng → CSV)

## One-time setup (Mac)
```bash
pip3 install pyobjc-framework-Quartz pyobjc-framework-Vision pillow google-genai pydantic
defaults write org.streetpea.chiaking NSAppSleepDisabled -bool YES   # stop App Nap throttling
```
System Settings → Privacy & Security: give **Terminal** both **Screen Recording** and **Accessibility**.

## Each week
1. Open chiaki-ng and stream Madden. Be anywhere in your franchise menus (not in a game). **Keep part of chiaki-ng's window visible**, and don't minimize it or put it in full screen. When macOS thinks nobody can see it, chiaki-ng stops drawing the stream and the window goes blank gray. If that happens mid-run, the script brings chiaki-ng to the front to wake it up, then carries on.
2. `python3 madden_capture.py --test` checks that the script can see and read the screen. It presses nothing.
3. `python3 madden_capture.py` does the full run: season stats (NFL, every category) plus last week's schedule.
4. `python3 extract.py captures/<folder>` turns the screenshots into CSVs with Gemini. It uses `GEMINI_API_KEY` from your environment or the project-root `.env`.

## Automatic weekly run (phase 5)
1. Subscribe to the ntfy topic in `.env` (`NTFY_TOPIC`) in the ntfy phone app. `python3 -m pipeline.notify "hello"` sends a test.
2. Before you play, start the trigger and leave it running: `python3 -m pipeline.main --watch`. It watches your Twitch broadcast (`BROADCAST_URL` in `.env`).
3. Play as usual. After **Advance Week**, once the Weekly Hub has been on screen for 30 seconds, the trigger connects Remote Play, which takes the console and ends your broadcast. Put the controller down.
4. When your phone says **"your turn"** (~20 min), turn the controller back on and restart the broadcast. Extract, load, engines and articles carry on without you, and a last notice says the week is done.
5. If a notice says **"week stopped"**, nothing after the failed step ran (a failed load publishes nothing). Fix it, then run the command in the notice: `python3 -m pipeline.weekly_capture --resume <folder>`.

## Safety rules built in
- The script checks the screen before every press and stops if it is anywhere unexpected.
- It never presses Cross on the Gameday tab (Advance Week) or on the schedule list (Actions / Force Win).
- If the quit prompt appears, it presses Cross only when **NO** is highlighted.
- Press Ctrl-C at any time to stop. It releases all keys.

## If something goes wrong
- **The stream freezes** (picture stuck, keys do nothing): in any mode the script notices (a harmless press that doesn't change the screen), force-quits chiaki-ng and relaunches the stream (`CHIAKI_HOST` / `CHIAKI_HOST_IP` in `.env`), up to 8 times a run, then redoes the step it was on (a stats mode from scratch, the game or roster team it was on). Otherwise force quit chiaki-ng and reopen it yourself, then `python3 madden_capture.py --only roster --resume <folder>`.
- **"STOPPED: ..."**: read the message; it names the screen it expected and what it saw.
- **Keys don't register:** add `--foreground`. This brings chiaki-ng to the front and sends normal keystrokes.
- **The NFL scope dropdown fails:** open Player Stats, run `python3 madden_capture.py --probe-l2`, and send me the screenshots in `captures/<folder>/probe/`.
