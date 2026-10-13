#!/bin/bash
# One-shot verification of the newsroom changes. Run from the project root:  bash tools/verify_session.sh
# Applies the roundtable and agent-memory migrations, runs the unit tests, runs the read-only DB checks, and a weekly-run dry run.
# Nothing calls Gemini. The only database write is the (idempotent) roundtable migration.
cd "$(dirname "$0")/.." || exit 1
mkdir -p verify_output
LOG=verify_output/run.log
{
  echo "=== $(date) ==="
  echo "--- python: $(command -v python) $(python --version 2>&1)"
  echo; echo "=== 1. migration ==="
  docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_10_19_roundtable_panel_agent.sql 2>&1
  docker exec -i madden-db psql -U postgres -d madden -v ON_ERROR_STOP=1 < db/migrations/2026_10_20_agent_memory.sql 2>&1
  echo; echo "=== 2. unit tests ==="
  python -m unittest -v tests.test_weekly_run tests.test_playoff_overrides tests.test_writer_config \
    tests.test_article_saving tests.test_entity_patterns tests.test_run_context tests.test_local_portfolio \
    tests.test_season_landscape tests.test_agent_memory tests.test_slot_targets 2>&1
  echo; echo "=== 3. database checks ==="
  python tools/verify_newsroom.py 2>&1
  echo; echo "=== 4. weekly run dry run (prompts only) ==="
  python -m newsroom.weekly_run --dry-run > verify_output/weekly_dry_run.txt 2>&1
  echo "exit $?; $(wc -l < verify_output/weekly_dry_run.txt) lines -> verify_output/weekly_dry_run.txt"
  tail -3 verify_output/weekly_dry_run.txt
  echo; echo "=== done ==="
} 2>&1 | tee "$LOG"
