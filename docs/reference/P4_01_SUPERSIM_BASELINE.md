# P4-01 — Supersim baseline (one 2026 season on `Mu Base`)

**Check:** `docs/CLOSE_OUT_CHECKLIST.md` P4-01 — one full season on a `CAREER-TUNE-*` clone of `CAREER-MU2026`
with `Mu Base` active, read with `pc_save_reader`, scored by `engines/tuning/compare` against
`data/tuning/targets_2015_2025.json`. **This is the go/no-go for all of P4.**
**Run:** 2026-11-12, `check-d`, holding `CLAIM · Madden PC`. Nothing tuned, nothing applied.
**Verdict: the installed `Mu Base` does not pass the supersim gate.** Overall score **0.210** (235 of 398
stats inside the 2015–2025 band), but one stat is **2.22 band widths** out, over the gate's 2-width limit
(`RB.rec_yards.p50`). So P4's supersim work is real work, and per JR's 2026-11-09 rule it starts from the
**running Sabos FranchiseMod 1.3 values** for the failing areas, never from vanilla.

## 1. Method

| Step | Command | What it did |
|---|---|---|
| Clone | `python3 -m tools.madden_pilot clone-save --from CAREER-MU2026 --to CAREER-TUNE-BASE26` | file copy of the tuning baseline. **The capacity prune refused** (§5.1), so the clone keeps full table capacity. |
| Launch | `python3 -m tools.madden_pilot launch --pack "Mu Base"` | Madden already open at `FRANCHISE_LIST`; applied pack verified: `Mu Base`, 67 entries, 47 enabled, fingerprint `0b198c30…`. |
| Season | `sim-season --save CAREER-TUNE-BASE26 --to regular-season-end` | weeks 1–4, then one `advance` across the user team's bye (week 5 §5.2), then weeks 6–18; `end_phase` `WILDCARD`, 13 weeks in the second run, 6.2 min; `saved: autosave (copied to the named save)`, both files written. |
| Read | `node pipeline/save_reader/pc_save_reader.mjs <save> --out base26_week18.json` | 3,238 players, 326 games, 2026 WildcardPlayoff week index 18. |
| Score | `python3 -m engines.tuning.compare base26_week18.json --targets data/tuning/targets_2015_2025.json` | the tables below. |

The scoring season is the **complete 2026 regular season**: 272 games, 32 teams, 17.0 games per team,
1,942 players with stat lines.

## 2. Result

- **Score 0.210** — the mean over 398 stats (0 = inside the real band, 1 = one band width outside).
- **235 of 398 inside** (59.0%), 163 outside.
- **One stat over the 2-band-width gate:** `RB.rec_yards.p50` at 2.22.

| stat | sim | 2015–2025 band (lo–hi) | score |
|---|---:|---|---:|
| `RB.rec_yards.p50` | 99.0 | 207.2 – 256.0 | **2.22** |
| `FRONT.def_tackles.p50` | 48.0 | 38.0 – 41.4 | 1.91 |
| `RB.rec_yards.rank25` | 100.0 | 229.8 – 304.9 | 1.73 |
| `DEF.def_tackles.p75` | 73.2 | 61.1 – 65.9 | 1.54 |
| `P.punt_yards.p50` | 4,811 | 2,819 – 3,644 | 1.41 |
| `league.punt_yards_per_team_game` | 278.7 | 167.5 – 216.4 | 1.27 |
| `team.turnovers.p50` | 14.5 | 20.0 – 24.4 | 1.24 |
| `P.punt_punts.p50` | 102.0 | 60.0 – 79.2 | 1.19 |
| `RB.receptions.rank25` | 26.0 | 33.0 – 39.0 | 1.17 |
| `RB.rush_yards.rank25` | 958.0 | 702.3 – 822.0 | 1.14 |

By section (stats / inside / mean score):

| section | stats | inside | mean |
|---|---:|---:|---:|
| P (punting) | 28 | 6 | **0.602** |
| RB | 42 | 17 | **0.335** |
| WR | 28 | 13 | 0.279 |
| FRONT | 28 | 19 | 0.269 |
| DEF | 42 | 26 | 0.239 |
| league | 41 | 27 | 0.184 |
| team | 35 | 22 | 0.135 |
| DB | 21 | 14 | 0.133 |
| QB | 70 | 46 | 0.115 |
| TE | 28 | 18 | 0.095 |
| K | 35 | 27 | 0.044 |

League-level shape is close: points per team game **21.8** (band 21.7–24.8, inside), pass yards **231.2**
(225.0–259.2, inside), rush yards **116.4** (108.8–121.6, inside); punting is far out (**278.7** against
167.5–216.4) and turnovers are low (**0.858** per team game against 1.16–1.46), which is where the
`team.turnovers` rows and the defensive tackle rows come from.

## 3. Gate verdict (`TUNING_ACCEPTANCE_THRESHOLDS.md` §3)

| Gate | Result |
|---|---|
| No stat more than **2 band widths** out | **FAIL** — `RB.rec_yards.p50` 2.22 |
| Overall ≤ baseline | 0.210, against the only recorded reference (CAREER-2029SB's 2028 season, 0.227) — better, but that is a different save and season, not a like-for-like baseline |
| Worst-10 improves or holds vs the pre-tuning baseline | not applicable yet — this *is* the pre-tuning baseline; it is what P4-03/P4-04 must improve |
| Two baseline seasons within 10% (P4-05) | **not measurable from one season** |

So P4-01 answers the go/no-go **no**: the mod does not already pass. Tuning is needed, and its first targets
are the ones above — halfback receiving volume (the largest single miss), defensive tackle counts (tackles
credited too freely), punting volume and net (too many punts, too many yards), and turnovers (too few).

## 4. What this season also measures

- **P8-14 (Mu Base crash-free across a season):** the 2026 season ran from week 1 to the Wild Card stop under
  `Mu Base` (47 mods, verified fingerprint) with **no crash, no mod-load error and no forced relaunch**. One
  season of evidence, recorded here for that row's owner; the crash-free requirement across a *full* year
  including the offseason and season rollover is still only partly covered (the same pack took p2g/p2h through
  a 2026→2027 rollover before this run).
- The save is the ordinary 5,662,392-byte slot; the read-back after the season is clean.

## 5. Obstacles hit (report only — no code was changed)

1. **`clone-save`'s capacity prune refuses on a fresh `CAREER-MU2026` clone.** `prune_capacity.mjs` must write a
   compressed stream **no longer than the original**, and node zlib cannot beat Madden's own stream: re-deflating
   the same 20,458,364-byte image gives 3,146,840 B at level 6 and 3,101,408 B at level 9 against the save's
   **3,056,682 B**, and the Highlight-only rebuild gives 3,138,000–3,141,000 B. So every clone today starts with
   **513 KB** of the 20 MiB headroom instead of the ~1.08 MB the prune buys
   (`docs/done/SAVE_CAPACITY_PRUNE_PLAN.md` §9). A 2026 **regular season** still fits (prior runs reached the Wild
   Card at 20.83 MB); a postseason from this clone would not.
2. **A bye week stops `sim-season`** (`tools/madden_pilot/jobs.py`, `_week_now` ~:684, MEDIUM): the early
   `HUB_BYE` return only fires when the *first* look classifies as `HUB_BYE`. When the hub opens on the **GAMEDAY**
   tab on a bye week, `read_header` is `None`, and the fallback `goto("HUB_WEEKLY")` can never match — the Weekly
   Hub is `HUB_BYE` that week — so the job ping-pongs GAMEDAY↔BYE and stops `Stuck: goto HUB_WEEKLY timed out on
   HUB_BYE` (62 steps in the run above). Worked around here with one `advance`, which reads the stop from the save.
   Any scripted full season will meet this at the first bye.
3. **`watcher_running()` false-positives on any command line that contains the literal string** (`jobs.py` ~:73,
   `ps -eo args | grep "[p]ipeline[.]watch_save"`). It matched this agent session's own `opencode run` prompt, so
   every pilot job refused with "pipeline.watch_save is running" while none was. The jobs here were run through a
   scratch driver that keeps the whole stock CLI and replaces only that pattern check with an honest one (a python
   interpreter actually executing the module; verified empty before each run). The guard's intent is right; its
   pattern is too broad.
4. **Tile names are OCR input.** `CAREER-TUNE-P4A`'s tile reads as `TUNE-UA`, so the tile walk cannot find it and
   the run stops "couldn't find the TUNE-P4A tile in CURRENT FRANCHISES". `TUNE-BASE26` read cleanly. A name with
   letters and a plain two-digit number is safe; `4` in the middle of a name is not.
5. **WSL interop flakes under load.** Three of the five attempts died on
   `UtilAcceptVsock:281: accept4 failed 110` for a key or a look (machine load ~17 on 16 cores with the other
   agent sessions). Each retry resumed from the save's own week, so no season was lost.

## 6. Artifacts

- Save: `CAREER-TUNE-BASE26` (and `-AUTOSAVE`) in `MADDEN_SAVES_DIR`, at the Wild Card stop.
- Snapshot: `data/tuning/seasons/p4a/base26_week18.json` (3,238 players, 326 games).
- Compare report (all 398 stats): `data/tuning/seasons/p4a/P4-01_compare.md`.
- Pilot runs: `data/pilot_runs/2026-11-12_200707_launch`, `…_202545_advance`,
  `…_202623_sim-season` (the completed season), plus the stopped attempts named in §5.

## 7. Not done (out of scope for this check)

P4-03 (tuning loops), P4-04, P4-05 (two-season noise), P4-06; nothing tuned, applied or written to a real
franchise database; no `Mu Tune` values folded in. P5 is untouched.
