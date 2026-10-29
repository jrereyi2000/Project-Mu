# Project Mu: Automated Media Engine & Simulation Network

An automated, event-driven platform and media ecosystem for a simulated NFL universe.

## Quick Start & Setup

### 1. Python Environment & Dependencies
Python 3.11+ is recommended. Install required packages using the requirements file:

```bash
# In the project root
pip3 install -r requirements.txt
```

*(Note: On macOS, `requirements.txt` automatically includes Apple Vision and Quartz PyObjC frameworks for screen capture and OCR.)*

### 2. Node.js Dependencies
Install packages for the frontend and PC save reader:

```bash
# Frontend (Mu_Network - Vite + React 19)
cd Mu_Network && npm install && cd ..

# PC Franchise Save Reader/Writer
cd pipeline/save_reader && npm install && cd ../..
```

### 3. Environment Variables
Copy the template configuration and supply your keys:

```bash
cp .env.example .env
```
Key variables to verify:
- `GEMINI_API_KEY`: Required for OCR extraction, media swarms, and editorial dispatch.
- `DATABASE_URL`: PostgreSQL connection string (defaults to `host=localhost dbname=madden user=postgres password=postgres`).
- `DEBUG_TOKEN`: Authentication token for `#debug` management routes.

### 4. Database Setup (PostgreSQL)
Ensure PostgreSQL is running, create the database, and run the initial schema and migrations:

```bash
createdb madden
psql -d madden -f db/init_db.sql
# Apply migrations in db/migrations/ as needed
```

---

## Console Capture Setup (macOS + chiaki-ng)

If running weekly game/stats capture directly from PS5 Remote Play:
1. Prevent App Nap throttling on chiaki-ng:
   ```bash
   defaults write org.streetpea.chiaking NSAppSleepDisabled -bool YES
   ```
2. In **System Settings → Privacy & Security**: give **Terminal** (or your IDE) both **Screen Recording** and **Accessibility** permissions.

---

## Running the Application

### Start Backend API & Frontend UI
```bash
# Start FastAPI backend (port 8000)
python3 -m uvicorn server.api:app --host 0.0.0.0 --port 8000 --reload

# Start Vite React frontend (port 5173)
cd Mu_Network && npm run dev
```
To serve the frontend directly through FastAPI at `http://localhost:8000/`, build the site:
```bash
cd Mu_Network && npm run build
```

---

## Weekly Pipeline Workflow

### From the Debug Page (`#debug`)
1. Subscribe to the ntfy topic in `.env` (`NTFY_TOPIC`) in the ntfy phone app. Test with `python3 -m pipeline.notify "hello"`.
2. Keep the API running and open `http://<Host IP>:8000/#debug`. Enter your `DEBUG_TOKEN`.
3. After **Advance Week** in Madden, tap **Scan week** (or **Backfill previous week** for no articles, **Game scan** for last week's box scores). Remote Play takes the console and your controller disconnects. The page displays the real-time log.
4. When your phone notifies **"your turn"** (~20 min), turn the controller back on. Extract, load, quantitative engines, and newsroom articles continue autonomously.
5. If a step fails, the notification alerts you to fix it and resume via `#debug` or CLI (`python3 -m pipeline.weekly_capture --resume <folder>`).

### Manual CLI Run
1. Open chiaki-ng and stream Madden. Be in franchise menus (keep part of the window visible).
2. Test capture: `python3 madden_capture.py --test`
3. Run capture: `python3 madden_capture.py`
4. Run Gemini extraction: `python3 extract.py captures/<folder>`

---

## Safety Rules & Troubleshooting
- **Screen Verification:** The capture script checks the screen before every keypress and stops if anywhere unexpected.
- **Safety Locks:** Never presses Cross on the Gameday tab (Advance Week) or on the schedule list (Actions / Force Win).
- **Quit Prompts:** If the quit prompt appears, it only presses Cross when **NO** is highlighted.
- **Ctrl-C Safety:** Pressing Ctrl-C at any time releases all virtual keys.
- **Frozen Streams:** If chiaki-ng stops updating, the script detects it and restarts chiaki-ng (`CHIAKI_HOST` / `CHIAKI_HOST_IP` in `.env`).
- **Keys Don't Register:** Pass `--foreground` to bring chiaki-ng to the foreground during keystrokes.
