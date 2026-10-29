# Fresh Save Strategy PDF: what's out of date

Notes on [`Project Mu_ Fresh Save Strategy & Remote PC-WSL Migration Plan.pdf`](./Project%20Mu_%20Fresh%20Save%20Strategy%20%26%20Remote%20PC-WSL%20Migration%20Plan.pdf) (written on the Mac before the PC was set up). Its decision stands: no teleport, a fresh franchise with modded gameplay. The decision as adopted, and the PC's actual state, are in [`PC_MIGRATION_PLAN.md`](./PC_MIGRATION_PLAN.md) (top of the file and §3).

**Changed since the PDF (2026-10-29):**
* **Start year and history.** The PDF starts "from 2026/2027" with the Colts as an example. JR's decision: the franchise starts in **2034** and all of Mu's 2026–2036 history is discarded (its stats are inflated). The 2036 database stays read-only as `madden_2036`.
* **§2.1 "192+ newsroom media writers".** The real figure is 218 writer and social accounts over 38 archetypes. The seed used to write only the 38 archetype personas and none of the accounts (they had only ever existed as rows in the old database); since 2026-10-29 it also seeds all 218 from `newsroom/config/agent_accounts.json`.
* **§2.1 record book.** `seed_records` also runs `db/migrations/2026_10_28_league_record_book.sql`, which loads Mu's own 2026–2035 record book (source `BOOK`). Fixed 2026-10-29: the seed deletes every non-`NFL` record row afterwards, so a fresh database has real NFL history through 2025 only.
* **§2.2 auto-init.** `watch_save.py --auto-init` does not clear an existing database before seeding, decides "different franchise" only from a season jump of more than a year, and sends every franchise to the one `DATABASE_URL`. Fixed 2026-10-29: one database per franchise, keyed on `Franchise.LeagueID` (PC_MIGRATION_PLAN.md §5.2).
* **§4.1–§4.2 SSH and remote setup.** Not used. The repo was cloned directly in the PC's WSL2 Ubuntu at `~/Project-Mu` (not `~/Project Mu`, no rsync from the Mac, no SSH MCP).
* **§4.2 step 2, Postgres.** Postgres runs in Docker Desktop (`madden-db` container, Postgres 14), not apt-installed Postgres 16/17. The 2036 data is `madden_2036`; there is no `madden` database until the watcher creates the new franchise's.
* **§4.2 step 4, saves path.** Documents was redirected to OneDrive, then the OneDrive backup was turned off. The path is `/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/` (Windows user `jrere`, not `jrereyi`).
* **§4.2 step 4, `DATABASE_URL`.** The Docker database uses `user=postgres`, not `user=jrereyi`.
