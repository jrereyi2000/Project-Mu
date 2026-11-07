# Panthers personality evidence sample — phase 4 review

Prepared 2026-10-30 (America/New_York). **Review only; no seeding or simulation effects.** Two players and two coaches, deliberately sparse rather than a forced trait quota.

## Cohort and provenance

The fresh 2034 franchise is a planned destination, not an established roster. Actual scratch evidence: `/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/CAREER-WEEK1`, read through `pipeline/save_reader/pc_save_reader.mjs` v4 on a temporary copy. It reports **2026 RegularSeason, week_index 0**, 3,244 players, 32 depth charts. CAR: team_index 20, Team row 24. Source modified `2026-10-30T07:39:08.530Z`; read `2026-10-30T23:35:01.262Z`. SHA-256: `de6c3d8e972faa80ea142f7f1cc8a72ee62b0777f5802d17e0559ce87dca339e`. Expected schema 988.0, used 620.0, with generic-schema warning: identity evidence, not complete schema certification.

| Person | Actual project evidence | Destination limitation |
|---|---|---|
| Bryce Young, QB | WEEK1 CAR, Signed, pgid 22686, Player row 3041 | Recheck actual 2034 roster and identity |
| Derrick Brown, DT | WEEK1 CAR, Signed, pgid 21286, Player row 346 | No assumed continued career or team membership |
| Dave Canales, HC | CAR head-coach seed in `db/seed_clean_db.py`, start 2024 | Seed candidate, not verified destination appointment |
| Ejiro Evero, DC | CAR/DC coordinator seed in same file, start 2023 | Seed candidate, not verified destination appointment |

Coach DB IDs were not queried or guessed; reader exports no coach cohort. Player rows are diagnostics, not durable identifiers; pgids require destination verification. The repository GIANTS save was also inspected: 2027 RegularSeason week_index 4, 2,990 players; it is not the destination. No old `madden_2036` roster was used. Current saves are under Windows Documents, not the old OneDrive path.

## Historical evidence ledger

All pages checked 2026-10-30. These are **historical 2023–2024 observations**, not current psychological assessments or 2034 facts. Official team pages contain direct participant statements; editorial framing is supporting observation. Multiple speakers in one team story do not constitute independent longitudinal evidence. Paraphrases are narrow; source confidence differs from mapping confidence.

| ID / publication date / exact primary page | Evidence versus inference |
|---|---|
| E1, 2024-09-19: [Young interview after benching](https://www.panthers.com/news/bryce-young-i-m-always-going-to-look-in-the-mirror) | Young accepted responsibility, deferred future questions and described helping Dalton. He explicitly described continuing confidence. High confidence in this public response; medium in recurring restrained interview style. Does not establish low confidence, obedience or private loyalty. |
| E2, 2024-09-05: [Brown preparation and work](https://www.panthers.com/news/derrick-brown-s-quest-for-his-best-this-is-kind-of-who-i-am-i-love-the-work) | Brown described sustained effort; Corbett described lifting together; Canales and Moton described consistent work. High confidence in reported habits, medium in persistent trait inference. No causal estimate of growth or durability follows. |
| E3, 2024-02-28: [Canales on retaining Evero](https://www.panthers.com/news/keeping-ejiro-evero-a-huge-part-of-offseason-plan) | Canales described retention as part of his hiring pitch and explained adjustments and communication after personnel changes. High for his stated decision and explanation, medium for generalized tendency. Assessment of Evero remains attributed testimony. |
| E4, 2024-02-08: [Coordinator appointments](https://www.panthers.com/news/panthers-retain-evero-announce-idzik-and-smith-to-fill-out-staff) | Retention was implemented; Canales explained continuity and offensive responsibility. High for observable appointment, no evidence of personal financial sacrifice or private loyalty. |
| E5, 2023-02-22: [Evero on influences](https://www.panthers.com/news/ejiro-evero-has-a-long-list-of-influences-and-ideas) | Evero described learning from varied coaching experiences and combining influences. Planned 3-4 base documented. High for stated influences; medium for learning-oriented football voice. Scheme does not prove experimental personality or successful development. |

## Proposed existing-catalogue mappings

Each mapping is an inference. IDs come from `newsroom/config/persona_traits.json`. **Nothing is activated now.** Future allowed use below is contextual wording only, after review; no exact-quote imitation. Unknown wants, fears and relationships remain unset.

| Person / candidate | Basis; confidence | Proposed allowed use after review | Uncertainty and excluded effects |
|---|---|---|---|
| Young: `GUARDED` — public voice | E1; medium | Brief accountable answers around setbacks; prefer a narrower voice note if catalogue wording overstates secrecy | No interview refusal, confidence reduction, Loner or Loyal inference. Supporting Dalton does not establish career allegiance. |
| Brown: `WORKOUT_FANATIC` — observable preparation | E2; medium for label, high for reported habits | Training-related wording about preparation and sustained effort | No progression, ceiling, wear, injury or longevity bonus. Physical ability already belongs to ratings. Extension does not establish Money First. |
| Canales: `SCHEME_NERD` — football explanation | E3/E4; medium | Explain continuity, communication responsibilities and role fit in relevant staff discussions | No forced retention, playbook change, Loyal trait or QB development multiplier. One continuity decision is not an immutable rule. |
| Evero: `SCHEME_NERD` — football explanation | E5; E3 attributed support; medium | Discuss influences and personnel fit when scheme is relevant | No automatic scheme switch, openness increase, Teacher or Youth Developer inference. Need evidence of teaching actions and opportunity allocation. |

For all four: money versus winning, relocation tolerance, discounts, trade demands, retirement motives and private loyalty are **unknown**; engine terms remain baseline. Outspoken does not imply selfish; restrained does not imply doubtful. Do not double-count ratings, scheme fit or production with personality multipliers.

Six existing sliders in order: `temperament`, `sociability`, `confidence`, `openness`, `warmth`, `drive`. **All four: [5,5,5,5,5,5], budget 30.** Neutral placeholders, not measurements. Raising drive or lowering sociability would force an unsupported compensating change under the fixed budget. Preserve evidence in scoped notes instead. Warmth must not substitute for private loyalty.

Idzik is a useful later negative control: appointment and job history alone justify no trait. This sample is representative of evidence problems, not a population distribution or exhaustive speaker list.

## Implementation findings and concrete review

Inspected persona catalogue, `newsroom/personas.py` seeding/validation/persistence/rendering, `db/migrations/2027_01_09_personas.sql`, clean-DB coach seeds and personality plan. `validate_persona_traits` requires 3–5 traits; `save_persona` invokes it. These one-trait proposals therefore cannot be imported as-is. DB trait notes/active flags do not provide structured provenance/confidence/effect permissions. Rendering uses catalogue prompts and tags, not this document's scope restrictions. GUARDED and SCHEME_NERD have empty effects; WORKOUT_FANATIC currently contains `progression_decline: 0.0`. This metadata grants no engine permission for these proposed profiles and was not changed by this task. A review note cannot enforce future engine eligibility.

JR review requested before expansion or implementation:

1. Accept/revise/reject the four mappings and neutral sliders; particularly Young's narrow voice note versus the broader Guarded label.
2. Approve sparse real-person profiles (including zero traits) as a future exception to the 3–5 validator. Generated people may retain seeded plausible draws. Never randomize real-person gaps to satisfy validation.
3. Choose historical cutoff for initialization; recommend real NFL history through 2025, matching clean-DB history. These earlier sources are eligible priors, not simulated career events. Recheck actual destination eligibility.
4. Approve designing enforceable evidence permissions before activation. Sample approval does not approve engine coefficients or production seeding.

After initialization, both real and generated people evolve from logged simulated events, under the plan's at-most-one-change-per-season limit. Do not import later real-world outcomes as simulation history. Coaches should first express observable continuity, personnel usage and development opportunities; do not add a second scheme-fit/development reward. Weak evidence may shape wording only. Consequential effects require separate evidence and owning-engine review.

## Validation

Save reads used temporary copies, without DB loader or seeding. Offline validation checks six-slider budget, catalogue IDs, role eligibility, conflicts, categories and actual effect metadata (including WORKOUT_FANATIC progression_decline 0.0). Expected rejection by the 3–5 validator is a documented blocker, not filled with invented traits. Only this artifact and personality plan/spec references are part of this change.
