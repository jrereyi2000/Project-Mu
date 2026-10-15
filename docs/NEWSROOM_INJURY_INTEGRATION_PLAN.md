# Project Mu: Newsroom & Social Desk Injury Integration Plan

> **System Objective**: Incorporate the weekly ground-truth `injuries` ledger into the Project Mu Newsroom (National writers, local beat reporters, fantasy analysts) and Agent Wire (social desk) with authentic, tiered coverage thresholds.
> **Git Rule**: Working on isolated worktree `.claude/worktrees/newsroom-injuries` on branch `feat/newsroom-injuries`. When commits are made, check out `main` and merge `feat/newsroom-injuries` in right away without asking.

---

## 1. Architectural Principles & Tiered Thresholds

In real-world sports coverage, injury reporting is strictly tiered: national pundits ignore depth-chart attrition while obsessing over superstars; beat writers cover every starter and backup line change; fantasy analysts watch skill players and touches.

### The Threshold Rules
1. **National Media & Pundits (Barnwell, Sando, Bayless, Simmons, Acho, National Previews)**:
   - **Key Injuries Threshold: OVR $\ge$ 88 OR Popularity $\ge$ 50**:
     - Audited across 1,719 players in 2035: $\text{OVR} \ge 88$ represents the top 14% of the NFL (~7.6 players per team), capturing true franchise cornerstones, top-tier WR1s (e.g. Rams WR1 D. Randolph 89 OVR, Patriots WR1 J. Tyson 89 OVR, Chiefs WR1 E. Santiago 89 OVR), All-Pro offensive tackles/guards, and top edge rushers.
     - $\text{Pop} \ge 50$ represents the 98th+ percentile of famous players (~30 players league-wide), elevating household names even if their OVR is in the mid-80s.
   - **Quarterback Exception**: Any starting Quarterback (`position = 'QB'` and Depth 1), regardless of rating, because an injured starting QB fundamentally shifts point spreads, game scripts, and playoff stakes.
   - All other injuries are treated as routine attrition and ignored at the national level.

2. **Local Beat Reporters (Assigned Franchise)**:
   - **Starters across the 22 + Specialists**: Any player who is a starter (Depth 1 at their position, top 3 WRs, top 2 RBs, starting 5 offensive linemen, nickel CB, key pass rushers, K/P).
   - Beat writers focus on tactical and locker-room consequences: "next man up", offensive line protection shifts, defensive coverage adjustments without a lockdown corner, and coach press conference quotes.

3. **Fantasy Editorial Matrix (Berry, Yates, Barnwell, Joyner)**:
   - **Fantasy Skill Contributors**: QBs, RBs, WRs, and TEs who are active contributors or depth chart starters.
   - **Handcuffs & Beneficiaries**: Backup RBs/WRs who inherit elevated volume, target share, or goal-line carries because a starter is on IR or ruled out.
   - Yates: Rankings tiers noting why a player dropped off the board (`OUT`/`IR`).
   - Berry: Love/Hate and Start/Sit calls elevated by injury vacancies.

4. **Agent Wire / Social Desk (`social_desk.py`)**:
   - **Beat Accounts**: Factual injury updates for any starter (`#Raiders LT Kolton Miller (turf toe) is officially OUT for Week 18`).
   - **Die-Hard Fan & Meme Accounts**: Raw emotional reactions (devastation, memes, doomposting) restricted to superstar injuries ($\ge 88$ OVR, $\ge 50$ Pop, or starting QBs).
   - **Former Player Accounts**: Veteran perspective on severe injuries (IR, torn ligaments, collarbones) and recovery grinds.

---

## 2. Phase Breakdown

### Phase 1: Research Tools (`newsroom/db_tools.py` & `newsroom/fantasy_tools.py`)
1. Create `get_team_injury_report(team_identifier: str, week: int = 0)` in `newsroom/db_tools.py`:
   - Inputs: Team name/abbreviation, optional publication week (defaults to `run_context.get_run_week()`).
   - Joins `injuries` with `player_seasons` and `players` for that season and week.
   - Resolves depth chart starter status and player popularity index.
   - Returns structured JSON:
     ```json
     {
       "team": "Las Vegas Raiders",
       "week": 18,
       "season": 2035,
       "injured_count": 2,
       "injuries": [
         {
           "player_name": "Kolton Miller",
           "position": "LT",
           "overall_rating": 90,
           "popularity_index": 78.4,
           "is_starter": true,
           "injury_type": "TURF TOE",
           "duration": "WEEK TO WEEK",
           "weeks_remaining": 1,
           "is_ir": false,
           "is_key_injury": true
         }
       ]
     }
     ```
   - Flags `is_key_injury: true` if $\text{OVR} \ge 88$ OR $Pop \ge 50$ OR (`position = 'QB'` and `is_starter`).
2. Add `get_fantasy_injuries(week: int = 0)` to `newsroom/fantasy_tools.py`:
   - Returns skill-position players on the injury report with their projected replacement / next player on depth chart.

### Phase 2: Assignment Brief Context Injection (`newsroom/weekly_run.py` & `swarm_dispatcher.py`)
1. In `newsroom/weekly_run.py`:
   - During the preview slate generation (`compile_weekly_digest`), query active injuries for both teams in each scheduled matchup.
   - For **National Matchup Previews**: Inject `key_injuries` into the matchup brief containing only players meeting the **OVR $\ge$ 88 OR Pop $\ge$ 50 OR Starting QB** rule.
   - For **Local Beat Briefs**: Inject `team_injuries` containing all starting players on the team's injury list.
   - This ensures writers see critical injury news upfront without being forced to spend an exploratory tool call.

### Phase 3: Prompt & Directive Modernization
1. **Remove Legacy Prohibitions**:
   - `newsroom/swarm_dispatcher.py`:
     - Line 850: Remove *"There is no injury data in this league: do not mention injuries or injury reports."*
     - Line 903: Remove *"There is no injury data in this league: do not mention injuries or injury reports."*
   - `newsroom/fantasy_desk.py`:
     - Line 155: Replace Rule 4 (*"NO INJURIES: This league has no injury data..."*) with the new Fantasy Injury Directive.
2. **Add Grounded Injury Rules**:
   - **National Directive**:
     > *"INJURY REPORTING RULE (THRESHOLD 88 OVR / 50 POP / STARTING QB): You may cite official injuries from your injury tool or the Assignment Brief, but ONLY for marquee players (OVR 88+, Popularity 50+, or starting Quarterbacks). Do not discuss minor injuries to role players or backups. Never invent injuries, timelines, or diagnoses not present in the official data."*
   - **Beat Directive**:
     > *"BEAT INJURY REPORTING RULE: Query your team's injury status using `get_team_injury_report`. Report on any injured starters across offense, defense, and specialists. Detail who steps into the starting lineup ('next man up') and how the coordinator must adjust the gameplan. Never invent injuries not on the report."*
   - **Fantasy Directive**:
     > *"FANTASY INJURY & OPPORTUNITY RULE: Starters ruled OUT or on IR create immediate fantasy opportunity. Highlight backup running backs and targeted receivers stepping into high-volume roles. Note players who are OUT/IR to explain why they drop out of this week's rankings or need to be benched."*

### Phase 4: Agent Wire / Social Desk Integration (`newsroom/social_desk.py` & `newsroom/social_writer.py`)
1. **Beat Reporter Breaking Posts**:
   - Add post type `BEAT_INJURY`: factual, reporter-toned tweet announcing starter status or IR placement.
2. **Die-Hard Fan & Meme Reactions**:
   - In `social_desk.py`, when a team loses a **Key Injury ($\ge 88$ OVR, $\ge 50$ Pop, or starting QB)**, inject an `INJURY_SHOCK` facts packet for that team's die-hard fans and meme pages.
   - Directs fans to react with genuine football heartbreak or cynical humor about their franchise's luck.
3. **Former Player Perspective**:
   - Former players weigh in on major injuries (e.g. broken collarbone, ACL), sharing perspective on the mental and physical toll of long rehabs.

### Phase 5: Verification, Testing & Specification
1. **Unit Tests**:
   - Create `tests/test_newsroom_injuries.py`:
     - Test `get_team_injury_report` with starter resolution and 88 OVR / 50 Pop threshold flagging.
     - Test brief compilation with `key_injuries` injection.
     - Test prompt compilation to confirm legacy prohibition strings are absent and new grounding rules are present.
     - Test fantasy tool injury outputs.
2. **Dry Run**:
   - Run a dry-run article generation for a matchup with an injured star (e.g. Raiders with 90-OVR DT C. Miller on turf toe, or Rams with 89-OVR WR D. Randolph with broken collarbone) and verify that the writer naturally cites the injury in the breakdown.
3. **Spec Updates**:
   - Update `PROJECT_SPEC.md` Section 7 (Newsroom), Section 7.8 (Agent Wire), and ReAct tool registry.
   - Commit on branch and merge cleanly into `main`.
