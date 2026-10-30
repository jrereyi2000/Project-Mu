# Project Mu: fbmod Conflicts CSV Plan

> **Example only.** Output of a `/propose` dry run on 2026-10-30 for the idea "add a CLI flag to `tools/fbmod_conflicts.py` that outputs CSV". Kept in `docs/reference/examples/` to show the format; it is not pending work. See [`PROPOSE_AND_EXECUTE.md`](../PROPOSE_AND_EXECUTE.md).

**Status:** proposal, awaiting JR's approval (2026-10-30).
**Builds on:** `tools/fbmod_conflicts.py` (PROJECT_SPEC §1.1 Repository Layout, `tools/`), [`MOD_INSTALL.md`](../../MOD_INSTALL.md) §3 (load order, built from this tool's report).

**Goal:** JR can open the mod conflict report in a spreadsheet on the PC, filter it by mod or asset, and sort it, instead of reading the text report, which shows only the first three assets of each group.

**Delivers:**
- `python3 -m tools.fbmod_conflicts <dir-or-fbmod>... --csv`: one CSV row per shared asset per mod, on stdout.
- `csv_rows(mods, groups)` and `CSV_COLUMNS` in `tools/fbmod_conflicts.py`.
- `tests/test_fbmod_conflicts.py`, the tool's first tests.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `main` (`tools/fbmod_conflicts.py:79`) has two outputs: `--json` (`:83`, dumped at `:96-99`) and the text report (`:100-111`). | `--csv` is a third branch next to `--json`. The two are mutually exclusive. |
| S2 | `overlaps(mods)` (`:64`) returns `{tuple of mod files: [(type, name, merged, identical), ...]}`. An asset shared by three mods lands in a three-file group. | The CSV is built from `overlaps`' output; no parsing changes. One row per (asset, mod file) keeps three-way shares readable. |
| S3 | The text report labels a whole group `CONFLICT` if any asset in it isn't merged (`:105-106`), and prints only the first three assets (`:110`). | The CSV labels each asset on its own (`merged` when its `merged` flag is set) and lists every asset. Its counts can differ from the text report's group labels; the docstring says so. |
| S4 | Unparseable mods are reported to stderr (`:94`). | stdout stays pure CSV, so `> conflicts.csv` works as is. |
| S5 | Names come from `[\x20-\x7e]` matches (`:45`), so asset names are ASCII; titles are decoded latin-1 (`:24`). | Commas and quotes are possible in titles; `csv.DictWriter` quotes them. No encoding work. |
| S6 | No test covers the tool (no `tests/*fbmod*` file); `tests/test_mod_packs.py` uses .fbmod file names only. Nothing else imports the module. | New `tests/test_fbmod_conflicts.py`, built on mod dicts, so no binary fixtures. No other caller to keep working. |

## 1. Scope

**In:**
- `--csv` flag, CSV on stdout, fixed columns.
- Tests for `csv_rows`, the flag, and `--csv` with `--json`.
- A one-line usage note in `MOD_INSTALL.md`.

**Out (non-goals):**
- Changing the header parser or the conflict rules.
- An `--output PATH` option (redirect stdout instead; Q2).
- A CSV of every resource of every mod (the `--json` output has that).
- Reading the Mod Manager's pack order to say which mod wins (`pipeline/mod_packs.py` territory).

## 2. Design

**Columns** (`CSV_COLUMNS`, in this order):

| Column | From |
|---|---|
| `status` | `merged` if the asset's `merged` flag is set, else `CONFLICT` |
| `asset_type` | `ebx`, `res`, `chunk`, … |
| `asset_name` | resource name or chunk id |
| `identical` | `true` if every mod's sha1 for the asset is the same, else `false` |
| `mod_count` | number of mods sharing the asset |
| `mod_file` | the `.fbmod` file name (not the full path) |
| `mod_title`, `mod_version`, `mod_author` | from that mod's header |

**Rows:** one per (shared asset, mod file). Sorted by `status` (`CONFLICT` first), then `asset_type`, `asset_name`, `mod_file`.

**Code** (all in `tools/fbmod_conflicts.py`):
- `CSV_COLUMNS = ("status", "asset_type", "asset_name", "identical", "mod_count", "mod_file", "mod_title", "mod_version", "mod_author")`.
- `csv_rows(mods, groups) -> list[dict]`: pure; `groups` is `overlaps(mods)`; mods are looked up by `mod["file"]`.
- `main`: `--json` and `--csv` go in an `argparse` mutually exclusive group. With `--csv`, `csv.DictWriter(sys.stdout, CSV_COLUMNS, lineterminator="\n")` writes the header and the rows, then returns.
- The module docstring's usage line becomes `[--exclude SUBSTR ...] [--json | --csv]`, plus one sentence on S3.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Excel on the PC may split columns wrongly if the system list separator isn't a comma. | JR opens one export on the PC. | Import through Data > From Text/CSV; no code change. |
| R2 | A large mod set (68 mods, MOD_INSTALL §1) gives a long file. | Run it on `C:\Madden27Modding`. | It's a spreadsheet; size is fine. |

**Spike first:** none.

## 4. Tests (synthetic, no live DB)

`tests/test_fbmod_conflicts.py`, with mod dicts shaped like `read_fbmod`'s output:
- `csv_rows`: a shared non-merged asset gives one `CONFLICT` row per mod; a shared asset with handler hashes gives `merged` rows; same sha1 gives `identical` `true`; an asset only one mod edits gives no row; `embedded` resources give no row.
- Three mods sharing one asset give three rows with `mod_count` 3.
- Row order: `CONFLICT` before `merged`, then by type, name, file.
- `main(["--csv", dir])` with `read_fbmod` monkeypatched over empty `*.fbmod` files in `tmp_path`: header equals `CSV_COLUMNS`, rows parse back with `csv.DictReader`.
- `main(["--csv", "--json", dir])` exits with status 2.

Run with `pytest -m "not integration" tests/test_fbmod_conflicts.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: CSV output, tests and usage note

- **Owns:** `tools/fbmod_conflicts.py`, `tests/test_fbmod_conflicts.py` (new), `docs/MOD_INSTALL.md`
- **Reads:** `tests/test_mod_packs.py` (test style), `docs/MOD_INSTALL.md` §3
- **Depends on:** none
- **Done when:** `--csv` prints the §2 columns and rows; `--csv --json` is refused; the text and `--json` outputs are unchanged; `MOD_INSTALL.md`'s intro line on conflicts mentions `--csv`.
- **Verify:** `pytest -m "not integration" tests/test_fbmod_conflicts.py` passes, and `python3 -m tools.fbmod_conflicts --help` shows `[--json | --csv]`.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1 | — | One unit: the flag, its tests and the doc line are one small change. |

This is small enough to build inline; `/execute-plan` would run it as one agent. Splitting the doc line into its own unit would cost a second cold start for one sentence.

**File ownership check:**

| File | Unit |
|---|---|
| `docs/MOD_INSTALL.md` | U1 |
| `tests/test_fbmod_conflicts.py` | U1 |
| `tools/fbmod_conflicts.py` | U1 |

## 7. Spec changes when built

- Repository Layout, `tools/fbmod_conflicts.py` line: usage becomes `[--exclude SUBSTR] [--json | --csv]`; add "`--csv` prints one row per shared asset per mod (status, asset_type, asset_name, identical, mod_count, mod_file, mod_title, mod_version, mod_author), each asset labelled on its own".
- Repository Layout, `tests/`: add `test_fbmod_conflicts.py` (csv_rows, `--csv`, flag exclusivity; synthetic mod dicts).

## 8. Decisions for JR

Asked none up front: each has a safe default and is cheap to change later.

| # | Question | Recommendation |
|---|---|---|
| Q1 | One row per asset per mod (filterable by mod), or one row per asset with the mods joined by a pipe (matches the text report)? | Per asset per mod: it filters and pivots cleanly in a spreadsheet. |
| Q2 | Stdout only, or also `--output PATH`? | Stdout; `> conflicts.csv` works in both WSL and PowerShell. |
| Q3 | Label each asset on its own, or copy the text report's group label? | Each asset on its own (S3); it's the more accurate view. |
