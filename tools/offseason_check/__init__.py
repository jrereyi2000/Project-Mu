"""offseason_check: the auditor's staff-contract and coach save-write checks, packaged for builders.

Run `python3 -m tools.offseason_check --help`; what each check proves and when to run it is in
docs/reference/ACCEPTANCE_CHECKS.md.

Modules:
  safety     the DSN, save-path and sha256 guards every command passes first (pure; no connection, no write)
  scratch    create / build / seed / drop `mu_scratch_offseason_<random>_test` databases
  invariant  the year-by-year staff-contract invariant: the pure classifier, the tenure truth, the ledger tracker
  timelines  the synthetic staff-contract timelines and their expected-totals table
  checks     the e2e run's per-write checks (seat contracts, F1 years, duplicates, wiring) as pure functions
  e2e        the end-to-end offseason through pipeline.league_office on a copy of a real save
  report     Check results, the PASS / FAIL table and the JSON report
"""
