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

## Safety rules built in
- The script checks the screen before every press and stops if it is anywhere unexpected.
- It never presses Cross on the Gameday tab (Advance Week) or on the schedule list (Actions / Force Win).
- If the quit prompt appears, it presses Cross only when **NO** is highlighted.
- Press Ctrl-C at any time to stop. It releases all keys.

## If something goes wrong
- **"STOPPED: ..."**: read the message; it names the screen it expected and what it saw.
- **Keys don't register:** add `--foreground`. This brings chiaki-ng to the front and sends normal keystrokes.
- **The NFL scope dropdown fails:** open Player Stats, run `python3 madden_capture.py --probe-l2`, and send me the screenshots in `captures/<folder>/probe/`.
