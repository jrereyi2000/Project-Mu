# Real Postgame Press Conference Corpus (U1)

**Status:** built 2026-11-08 as wave 1 of
[`PRESS_CONFERENCE_PLAN.md`](../done/PRESS_CONFERENCE_PLAN.md) (U1, the
research spike). **Owner:** U1. **Material:** reference and test material only.

This document records the corpus of real NFL head-coach postgame press
conferences collected for the press-conference agents, the statistics derived
from it, and the latency/token spike. It is the human-readable companion to
`tools/press_corpus.py` and `newsroom/config/press_corpus_stats.json`.

## 1. Rules this corpus follows

- **No transcript text in prompts.** Per plan D8, every agent prompt is
  hand-written. No real transcript text and no line from this corpus goes into
  a prompt; the corpus only tests the prompts and the agent behaviour.
- **The raw transcripts stay local.** They live in `data/press_corpus/*.txt`
  in the working tree. `data/*` is git-ignored, so the transcripts are never
  committed. The committed material is the derived statistics file and this
  document.
- **The statistics file is not readable by prompts.** Nothing in the pipeline
  imports `press_corpus_stats.json`; it is read only by the tool's `eval`
  command, by tests, and by a human reviewer.
- **No invented data.** Every transcript below came from a public source and
  every statistic was computed from those files. The two files that parsed to
  zero questions were removed rather than counted as empty pressers.

## 2. What was collected

| | |
|---|---|
| Head-coach postgame press conferences | **175** |
| Questions parsed | **2,092** |
| Teams represented | **30 of 32** (all but LAR and SF) |
| Seasons | 2023 (65), 2024 (67), 2025 (43) |
| Competition | 161 regular season, 14 postseason |
| Wins / losses | 90 / 85 |
| Blowout losses (17+ points) | 23 |
| Overtime games | 2 |
| Playoff pressers | 14 (7 games, both coaches) |
| Date range | 2023-09-10 to 2026-01-12 |

The 2025 season includes its postseason, played in January 2026, because the
NFL season year is the calendar year the regular season started in.

The target was at least 40 pressers from at least 16 teams across wins,
losses, blowouts, overtime and playoffs. The collection reached 175 from 30
teams, so the overtime and playoff cells are the only small ones: the source
archives are home-team media conferences, and a home team's playoff pressers
exist only when that team hosted a playoff game. The two overtime pressers are
the Eagles' 2023 overtime win and the Dolphins' 2024 overtime win. A larger
overtime sample would need playoff/Super Bowl media archives or team-site
transcripts from more overtime games; the statistics below carry that caveat.

## 3. Sources, URLs and terms of use

Every transcript came from the ASAP Sports `asaptext.com` team media-conference
archives. Each archive index lists a whole season of that team's pressers,
including the visiting coach's postgame presser. The exact URL of all 175
transcripts is recorded in the `corpus.sources` list of
`newsroom/config/press_corpus_stats.json`.

**URL pattern.** Each archive index is
`http://asaptext.com/asap_media/media/<org>/<event>/`; each transcript is
`http://asaptext.com/asap_media/media/<org>/<event>/browse_file.php?browse_file_name=transcripts/<id>.html`.

| Archive | Index URL | Transcripts used | Terms of use |
|---|---|---:|---|
| Houston Texans Media Conference | `http://asaptext.com/asap_media/media/1048/1078/` | 44 | ASAP Sports transcript; index footer: "Copyright to be established by CLIENT and copyright holder. All rights reserved. This is a test site and no warranties of any kind are implied or granted." Each transcript ends "FastScripts Transcript by ASAP Sports". |
| Miami Dolphins Media Conference | `http://asaptext.com/asap_media/media/1049/1079/` | 44 | same |
| Pittsburgh Steelers Media Conference | `http://asaptext.com/asap_media/media/1055/1091/` | 43 | same |
| Minnesota Vikings Media Conference | `http://asaptext.com/asap_media/media/1060/1105/` | 18 | same |
| Philadelphia Eagles Media Conference | `http://asaptext.com/asap_media/media/1034/1049/` | 15 | same |
| Tampa Bay Buccaneers Media Conference | `http://asaptext.com/asap_media/media/1145/1544/` | 9 | same |
| Baltimore Ravens Media Conference | `http://asaptext.com/asap_media/media/1148/1547/` | 2 | same |

ASAP Sports publishes no separate click-through terms page on the site. The
operative statements are the copyright line above and the per-transcript
"FastScripts Transcript by ASAP Sports" credit. The corpus is used locally for
research and testing only; no transcript is redistributed, committed, or fed
to a model.

### Sources checked but not used

| Source | URL | Result |
|---|---|---|
| NFL league communications site | `https://communications.nfl.com/` | Unreachable from this environment (connection failed, HTTP 000 on both `https` and `http`). The NFL's public terms are at `https://www.nfl.com/legal/terms` ("Terms and Conditions", updated 2024-05-16). Not used. |
| Baltimore Ravens transcript hub | `https://www.baltimoreravens.com/news/transcripts` | Reachable and a clean postgame-transcript source. Used to validate the question-mark/speaker-line parser but not included in the final corpus (the ASAP archive already exceeded the target). Terms: "Copyright © 2026 Baltimore Ravens. All Rights Reserved." |
| New England Patriots postgame quotes | `https://www.patriots.com/news/new-england-patriots-postgame-quotes-11-10` | Reachable. Each page mixes the head coach and several players under separate headings, so it is not a single-speaker Q/A transcript without extra segmentation. Checked, not used. Terms: "Copyright New England Patriots (the 'Club'). Commercial reproduction, distribution or transmission of any part or parts of this website or any information contained therein by any means whatsoever without the prior written permission of the Club is not permitted." The mirror `https://patriots.1rmg.com/transcripts/` returned 403 and was not used. |
| Official team YouTube captions | `https://www.youtube.com/` (e.g. official club channels) | Checked as the fallback path. YouTube is reachable and its terms are at `https://www.youtube.com/t/terms`. Captions were not downloaded: the ASAP text archives already exceeded the target, and auto-captions are not a reliable verbatim Q/A source. Not used. |

## 4. Method

### 4.1 Selection

1. All ASAP team media-conference archives were enumerated (`media/<org>/`
   lists its event ids). Twenty-four NFL team archives exist on the platform.
   Seven archives were deep enough in the 2023–2025 window to download: Texans,
   Dolphins, Steelers, Vikings, Eagles, Buccaneers, Ravens.
2. Each archive's index was parsed by date and transcript id; only entries
   dated 2023 through January 2026 were fetched.
3. A candidate was kept when its header named a head coach (a name containing
   "Coach", or a known 2023–2025 head-coach name) and its session label
   contained "postgame" / "postgame presser".
4. Preseason games were dropped, leaving regular-season and postseason games.
5. Duplicate games (the same game appearing in two archives) were removed by
   `(date, team, opponent)`; nine duplicate game-coach entries were found and
   removed.
6. Two transcripts in which every question was printed as "(Indiscernible.)"
   or "(No microphone.)" were removed: they carry no question data and would
   otherwise count as empty pressers.

### 4.2 Metadata

Each local `.txt` begins with a small front-matter block derived from the
transcript header and score line:

```
# source, archive, transcript_id, date, season, competition
# team, team_name, opponent, opponent_name
# result, score, tags, speaker
```

`result` is the speaker team's result parsed from the score line. `tags`
contains `win`/`loss`, `blowout_loss` (loss by 17+), `overtime` (the score line
or the transcript's own discussion of overtime), and `playoff` (the subtitle
or a known postseason date window). Preseason is tagged and then excluded.
When a file has no explicit tags, the tool infers them from the score, date
and front matter; when tags are present, the tool trusts them (so a January
Week 18 regular-season game is not mislabelled a playoff game).

### 4.3 Turn splitting

The tool detects three layouts, in order:

1. **Explicit markers** — `Q.` / `Q:` / `Question:` / `REPORTER:` starts a
   question; `A:` / `Answer:` / `COACH:` starts an answer. This covers the
   ASAP format, where questions are `Q.` paragraphs and answers begin with the
   speaker's name in capitals.
2. **Speaker lines** — an uppercase or mixed-case name followed by a colon
   starts an answer (the regex admits `MIKE McDANIEL`, `NICK SIRIANNI`,
   `O'Connell` and all-caps names). Text before the first question is the
   opening statement. Section headings such as `HEAD COACH …` and
   `Opening Statement:` are skipped.
3. **Question-mark heuristic** — when there are no explicit question markers,
   a line ending in `?` is a question and the following quoted/plain lines are
   its answer; a parenthetical line such as `(Ben Fixture)` records the
   reporter. This covers team-website layouts such as the Ravens'.

Transcript artifacts printed as questions — `(Indiscernible.)`,
`(Off microphone)`, `(No microphone.)`, `(Regarding …)` — are dropped before
statistics. A one-word call-out such as a reporter's first name is also
dropped.

### 4.4 Question taxonomy

The taxonomy is fitted to the corpus and mapped to the archetypes in
`newsroom/config/agent_personas.json`. It is descriptive, not a rulebook for
the agents. A question can match several types; the type with the most keyword
hits wins, with ties going to the earlier entry in this list.

| Type | What it covers | Natural reporter archetypes |
|---|---|---|
| `INJURY` | health, availability, injury, practice status | LOCAL_BEAT_REPORTER, SCHEFTER, RAPOPORT |
| `ROSTER_PERSONNEL` | starters, depth, rotation, signings, releases, trades | LOCAL_BEAT_REPORTER, SCHEFTER, SANDO |
| `SCHEME_ADJUSTMENT` | scheme, coverage, protection, play calls, halftime | THE_ATHLETIC, KIMES, ORLOVSKY, BARNWELL, JOYNER |
| `PLAYER_PERFORMANCE` | one player's game, usage or development | YATES, LOCAL_BEAT_REPORTER, BERRY |
| `TEAM_EXECUTION` | turnovers, penalties, third down, red zone, run/pass game | LOCAL_BEAT_REPORTER, BARNWELL, KIMES |
| `COACHING_DECISION` | fourth down, clock, timeout, challenge, two-point | BARNWELL, KIMES, SIMMONS |
| `OPPONENT_NEXT` | the opponent just played or next week's preparation | LOCAL_BEAT_REPORTER, YATES |
| `SEASON_CONTEXT` | standings, playoffs, streaks, records, the season | BARNWELL, SIMMONS, SANDO |
| `EMOTION_STORY` | how the coach feels, what the result means | CHADIHA, EISEN, CLARK, HERBSTREIT |
| `FRONT_OFFICE_CONTRACT` | job security, contracts, ownership, the future | SCHEFTER, RAPOPORT, SANDO, COWHERD |
| `PERSONAL_MILESTONE` | records, milestones, history, a return home | EISEN, YATES, WINGO, HERBSTREIT |
| `OFFICIATING` | officials, flags, replay reviews | LOCAL_BEAT_REPORTER, SKIP_BAYLESS, STEPHEN_A |
| `GAME_MOMENT` | a specific play, drive, series or moment | LOCAL_BEAT_REPORTER, KIMES, ORLOVSKY |
| `CLARIFICATION` | short probes and anything that fits no other type | LOCAL_BEAT_REPORTER |

`CLARIFICATION` is deliberately a large catch-all (30.9%); it contains genuine
clarification probes and short multi-purpose questions. The other shares are
the meaningful signal.

### 4.5 Follow-up detection

A question is a follow-up when it is explicitly marked ("Follow up …"), or
when it shares at least two content words with the previous question, or when
it is a short interrogative probe (at most 8 words) sharing a content word, or
when it picks up two content words from the previous answer (a name, a play, a
number). This is conservative: a new question about a different player or a
different phase of the game is not counted. The corpus's 12.1% rate is a
lower bound on true follow-ups, because ASAP transcripts do not label reporter
identities.

### 4.6 Answer taxonomy

The answer side is coarser than the question side: a coach's answer is usually
several moves at once (credit, deflection, accountability), so the primary
answer type is the highest-scoring one and the other keyword hits are recorded
as secondary counts.

| Type | What it covers |
|---|---|
| `CREDIT_PLAYERS` | credits the players, a unit or a specific performance |
| `TAKE_BLAME` | puts the loss or a mistake on himself |
| `DEFLECT` | watch the tape, no update, can't speak to that |
| `ACCOUNTABILITY` | names the problem: mistakes, turnovers, penalties, not good enough |
| `SCHEME_TECHNICAL` | explains the football: scheme, coverage, protection, adjustments |
| `EMOTION` | proud, frustrated, excited, tough |
| `CLICHE` | a stock football phrase (one game at a time, next man up, do our job) |
| `OTHER` | an answer that fits no other answer type |

The answer taxonomy is a descriptive signal for the coach-agent tests, not a
rule the coach is told to follow in the prompt.

## 5. Statistics

All figures are in `newsroom/config/press_corpus_stats.json`. Re-run
`python3 -m tools.press_corpus stats` to regenerate them.

### 5.1 Overall

| Statistic | Value |
|---|---|
| Pressers | 175 |
| Questions | 2,092 |
| Questions per presser | mean 11.95, median 11, p10 7, p90 17, range 2–27 |
| Follow-up rate | 12.14% (254 follow-ups) |
| Question length | mean 21.5 words, median 19, p90 40, max 92 |
| Answer length | mean 80.5 words, median 64, p90 171 |
| Opening statement length | mean 158.6 words (108 pressers had one) |

### 5.2 Question-type mix

| Type | Share |
|---|---:|
| PLAYER_PERFORMANCE | 18.8% |
| TEAM_EXECUTION | 14.3% |
| EMOTION_STORY | 6.4% |
| SCHEME_ADJUSTMENT | 6.3% |
| COACHING_DECISION | 5.2% |
| ROSTER_PERSONNEL | 4.0% |
| SEASON_CONTEXT | 3.9% |
| INJURY | 3.8% |
| PERSONAL_MILESTONE | 2.4% |
| GAME_MOMENT | 1.8% |
| OPPONENT_NEXT | 1.3% |
| OFFICIATING | 0.6% |
| FRONT_OFFICE_CONTRACT | 0.3% |
| CLARIFICATION | 30.9% |

### 5.3 Answer-type mix

Of the 2,091 questions with a recorded answer:

| Answer type | Share |
|---|---:|
| CREDIT_PLAYERS | 42.2% |
| OTHER | 39.0% |
| DEFLECT | 5.8% |
| ACCOUNTABILITY | 5.1% |
| EMOTION | 3.8% |
| SCHEME_TECHNICAL | 3.2% |
| TAKE_BLAME | 0.8% |
| CLICHE | 0.1% |

`CREDIT_PLAYERS` is broad (it includes any answer that names the players or
"guys"), so treat it as a floor on credit-giving rather than a precise count.
`TAKE_BLAME` is rare in real coach answers (0.8%); the coach agent should not
be instructed to blame himself routinely.

### 5.4 Split by result

| Group | n | Questions/presser | Follow-up rate | Question length (mean words) |
|---|---:|---:|---:|---:|
| Win | 90 | 10.87 | 14.6% | 22.6 |
| Loss | 85 | 13.11 | 10.0% | 20.5 |
| Blowout loss (17+) | 23 | 13.52 | 5.8% | 21.6 |
| Overtime | 2 | 10.50 | 28.6% | 24.8 |
| Playoff | 14 | 12.93 | 16.6% | 23.6 |
| All | 175 | 11.95 | 12.1% | 21.5 |

The clearest finding is the direction of the result effect: pressers after
losses and blowout losses are **longer** than after wins (13.1 and 13.5
questions versus 10.9), and follow-ups are **fewer** after a blowout loss
(5.8% versus 14.6% after a win). Reporters ask more, broader accountability
questions after a bad loss, while the follow-up probes and the playoff pressers
carry more follow-ups. This is the opposite of a model in which a bad loss
simply ends the presser early.

## 6. How it tests the agents

- **U4 (reporter and coach agents).** The test targets are the numbers above:
  a generated presser should average about 12 questions, a follow-up
  rate near 12% (higher after wins and playoffs, lower after blowouts), a
  question length near 21 words, and a question-type mix close to the shares
  in §5.2. The `eval` command computes all four and reports a total-variation
  distance for the type mix; the tool's verdict treats a TVD up to 0.20, a
  questions-per-presser delta up to 3, a follow-up delta up to 0.15 and a
  question-length delta up to 5 words as "PASS".
- **U6 (session engine and tests).** A dry week of generated pressers can be
  written to a directory and checked with
  `python3 -m tools.press_corpus eval --transcripts <dir>`. The same numbers
  become assertions in the U4/U6 tests (e.g. a scripted 12-question presser
  has 1–2 follow-ups, not 6).
- **U2 (casting).** The archetype map in §4.4 records which kind of reporter
  naturally asks which kind of question. It is a check on the pool tiers and on
  the persona prompts, not an input to them.
- **The `eval` command is the runnable gate.** Generate pressers with the
  same Q/A split, put them in a directory, and compare. The corpus statistics
  file is the reference; no prompt reads it.

### Worked example

With two small synthetic generated pressers, `eval` prints a table such as:

```
metric                             generated      corpus     delta
questions per presser (mean)           3.500      11.954    -8.454
follow-up rate                         0.143       0.121    +0.021
question length (words, mean)          6.571      21.478   -14.907
answer length (words, mean)            9.857      80.484   -70.627
question-type total variation: 0.547
```

That output is a deliberately tiny fixture; a real generated week should land
much closer to the corpus.

## 7. Latency and token spike

The plan's spike asks for one agent-style presser prompt on fake data with a
cloud model and the local model, recording calls, tokens, round latency and
time per presser.

### 7.1 Cloud model — not taken

The cloud half was **not measured**. This worktree has no `.env` (it is
git-ignored) and no `GEMINI_API_KEY` or `GOOGLE_API_KEY` in the environment,
so `engines/llm_provider.generate_text` cannot reach a cloud provider, and
there is no other cloud credential available to the repository. No cloud
numbers are invented; the plan's §2.10 cloud estimates (about 75 calls,
~280k input tokens and 12–25k output tokens per presser; 8–12 minutes per week
at 6 workers) remain unverified estimates.

### 7.2 Local model — measured

The local half was measured on the installed Ollama server with
**`gemma4:12b`** (11.9B, the closest installed model to the plan's local 14B),
`num_ctx=16384`, `think=false`, temperature 0.6. The fake fact sheet described
an invented Week 12 game (ABC Roughriders 24, XYZ Comets 17) with full box
score, player lines, injuries, records and a coach profile. The prompt followed
the §2.5 shape: persona, beat/national role, fact sheet, transcript so far,
hand count. The round was three reporter turns in parallel, then the coach's
answer; a reporter judgment call was measured separately.

| Call | Prompt tokens | Output tokens | Wall time (warm) |
|---|---:|---:|---:|
| Reporter 1 (local beat) | 1,100 | 114 | 4.31 s |
| Reporter 2 (national columnist) | 1,092 | 116 | 3.09 s |
| Reporter 3 (national insider) | 1,091 | 113 | 1.15 s |
| Coach answer | 1,025 | 98 | 0.99 s |
| Reporter judgment | 292 | 128 | 1.07 s |

- **One round (5 calls, three reporters in parallel + coach answer):**
  **5.30 s wall**, 4,308 prompt tokens, 441 output tokens. The first, cold
  run took 79.5 s because the model was loading; the warm run above is the
  representative number.
- **Extrapolated per presser** (12 rounds plus 4 judgments, with a short
  pick call estimated at half a coach answer): **about 74 s**, **64 calls**,
  **~57k input tokens**, **~6.2k output tokens**.
- **Prompt size caveat.** The fake fact sheet was ~1,100 tokens, smaller than
  §2.10's ~4,000-token reporter prompt (persona + memory + ~2,000-token fact
  sheet + ~1,000-token transcript). With the real fact sheet and a growing
  transcript, the input-token estimate rises toward §2.10's 280k per presser;
  the round wall time would rise too, but the measured warm calls suggest the
  local model is faster than the plan's 5–8 minutes-per-presser estimate.
- **Calls.** The measured extrapolation is 64 calls per presser, against
  §2.10's estimate of ~75; the difference is retries, which this spike did
  not exercise.

## 8. Proposed §2.5 constants and test targets

These are proposals from the corpus statistics, for U4 (which sets the final
constants) and JR. The corpus measures how long real pressers ran, not the
coach's private patience; the proposals keep the patience cap above the
observed length in most cases so that pressers end because reporters run out,
with patience binding after bad losses and long losing streaks.

| Constant | Current plan | Proposed | Why |
|---|---|---|---|
| `BASE_PATIENCE` | 12 | **14** | Median 11, mean 11.95; the plan's 12 sits at the median. A base of 14 keeps the cap above the typical presser and avoids systematic truncation. |
| Win | +3 | **+1** | Win pressers are shorter (10.9 questions), so a large bonus rarely binds; +1 keeps the sign without inventing length. |
| Tie | +1 | **0** | No tie pressers in the corpus; neutral is safer. |
| Loss | −2 | **0** | Loss pressers are longer (13.1), not shorter; a penalty would contradict the corpus. Carry the loss in `mood`, not in the question budget. |
| Blowout loss (17+) | −4 | **−1** | Blowout-loss pressers are the longest (13.5) and have the fewest follow-ups (5.8%). −1 keeps a small mood signal while the cap (13) stays close to the observed mean; a larger penalty would truncate below what real rooms did. |
| Losing streak beyond 2 | −1/game | **−1/game** | Keep; the corpus has no streak split, so the plan's value stands. |
| Hot seat | −2 | **−2** | Keep; the corpus has no hot-seat labels. |
| Temperament | `(t−5)×0.6` | **`(t−5)×0.5`** | Smaller coefficient so a temperament can move patience by at most ±2.5 rather than ±3; either is within the corpus noise. |
| Clamp | [5, 20] | **[6, 20]** | The observed maximum is 27 questions; a minimum of 6 avoids a pathological 5-question presser while the 20 ceiling is generous. |
| `MAX_QUESTIONS` | 25 | **28** | Observed maximum 27 (p90 17). 28 covers every real presser while remaining a hard safety bound; keep 25 if JR prefers the lower safety cap and accepts truncating the longest ~1%. |
| `MAX_FOLLOW_UPS_IN_A_ROW` | 2 | **2** | Safety rule; the corpus cannot observe reporter identity, so keep. |
| Follow-up target | — | **~12% overall** | Win ~15%, loss ~10%, blowout loss ~6%, playoff ~17%. Use as a test target and as prompt guidance, not a hard rule. |
| Question length target | ≤60 words | **≤60 hard, ~21 typical** | Median 19, mean 21.5, p90 40; 60 words is a generous hard cap, and the prompt should ask for about 20. |
| National counts | §2.4 tiers | **unchanged** | The corpus has no reporter-attendance data (ASAP does not label who asked), so it cannot set national counts. Keep the approved §2.4 tiers. |

**Test targets for U4/U6.** On a dry week, expect: questions per presser
11.95 ± 3; follow-up rate 0.121 ± 0.15; question length 21.5 ± 5 words; type-mix
TVD ≤ 0.20 from the shares in §5.2. The `eval` command reports exactly these
four checks.

## 9. Limitations

- **Home-archive bias.** The ASAP team archives are home-team media
  conferences, so the sample is weighted toward the seven host teams and their
  opponents. It is not a random sample of all 32 teams.
- **No reporter identities.** ASAP does not label which reporter asked which
  question, so the follow-up rate is a text-similarity lower bound, and the
  corpus cannot measure attendance, which reporter spoke, or national-versus-
  beat mix.
- **Small overtime and playoff cells.** Two overtime pressers and 14 playoff
  pressers (7 games) are enough to show direction, not to fit stable
  coefficients. A larger sample would need playoff/Super Bowl media archives.
- **The type classifier is a keyword vote.** `CLARIFICATION` carries 30.9% of
  questions, mostly short probes and multi-purpose questions. The other shares
  are the useful signal; the eval's TVD is a soft check, not a hard gate.
- **Cloud latency and cost are unverified.** See §7.1.
- **No transcript text is committed.** The `.txt` files are local; the
  committed JSON contains counts, type descriptions and URL strings only.

## 10. Reproducing

```bash
# Regenerate the statistics and rewrite the committed JSON.
python3 -m tools.press_corpus stats

# Compare a directory of generated pressers with the corpus.
python3 -m tools.press_corpus eval --transcripts data/generated_pressers

# Help for the module and both subcommands.
python3 -m tools.press_corpus --help
python3 -m tools.press_corpus stats --help
python3 -m tools.press_corpus eval --help

# Offline tests (synthetic fixtures; no real transcripts, no network).
pytest -m "not integration" tests/test_press_corpus.py
```

The raw corpus is at `data/press_corpus/*.txt` in the working tree and is
git-ignored. The derived file is
`newsroom/config/press_corpus_stats.json`. The tests live in
`tests/test_press_corpus.py`, with synthetic fixtures in
`tests/fixtures/press_corpus/`.
