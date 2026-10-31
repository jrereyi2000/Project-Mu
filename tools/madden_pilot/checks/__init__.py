"""Mod checks for `python3 -m tools.madden_pilot verify-mods --check <name>` (plan section 2.3).

A check is a small module here with `run(pilot) -> dict`. It drives Madden only through the
pilot (goto, look, confirm_on), captures the screen where the mod shows, and returns
{"status": "pass" | "fail" | "unknown", "why": ..., "screenshot": ...}. "unknown" means the check
couldn't tell (for example, the screen it needs isn't catalogued yet). jobs.verify_mods loads it
with jobs.load_check(name) and writes the result to run_dir/result.json.
"""
