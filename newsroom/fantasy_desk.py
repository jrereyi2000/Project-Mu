"""Fantasy Editorial Matrix: which fantasy articles run each week (docs/FANTASY_EDITORIAL_MATRIX.md).

The fantasy page follows the fantasy calendar, not the NFL one:
    W1 draft prep · W2–W14 regular season · W15 playoffs round 1 · W16 semifinals · W17 championship ·
    W18 season in review · W19–W23 nothing new (the page keeps the W18 slate).
A Week N slate is published before Week N's games, like the national slate.

Slots: Fantasy Hero, Fantasy 1–3 (front-page cards) and Fantasy Shelf 1..n ("View all analysis" only).
"""
from typing import Optional

LAST_FANTASY_SLATE_WEEK = 18

# K and D/ST content needs kicking/defense lines and K/DST scoring (pipeline migration phase 3).
KDST_ENABLED = False

HERO, CARD1, CARD2, CARD3 = "Fantasy Hero", "Fantasy 1", "Fantasy 2", "Fantasy 3"

# Card 03 in W2–W14: one rotating feature per week. Fixed so the slate is predictable and reviewable.
CARD3_SCHEDULE = {
    # Pool A: early reads (Defense vs. Position once, in Week 3)
    2: "FANTASY_OVERREACTION_CHECK",
    3: "FANTASY_DVP_REPORT",
    4: "FANTASY_OVERREACTION_CHECK",
    5: "FANTASY_OVERREACTION_CHECK",
    # Pool B: midseason market
    6: "FANTASY_BUY_SELL",
    7: "FANTASY_ADP_CHECKPOINT",
    8: "FANTASY_ROS_TIERS",
    9: "FANTASY_DVP_REPORT",
    10: "FANTASY_BUY_SELL",
    # Pool C: stretch run (trade deadline before Week 12 kickoff)
    11: "FANTASY_ROS_TIERS",
    12: "FANTASY_TRADE_DEADLINE_SPECIAL",
    13: "FANTASY_PLAYOFF_SCHEDULE_PLANNER",
    14: "FANTASY_MUST_WIN_WEEK",
}

DRAFT_TIER_POSITIONS = (("WR", "Wide Receivers"), ("RB", "Running Backs"), ("QB", "Quarterbacks"),
                        ("TE", "Tight Ends"))


def fantasy_phase(week: int) -> Optional[str]:
    """Fantasy-calendar phase for a publication week, or None when no fantasy slate runs (W19+)."""
    if week == 1:
        return "Draft Prep"
    if 2 <= week <= 14:
        return "Regular Season"
    if week == 15:
        return "Playoffs: Round 1"
    if week == 16:
        return "Playoffs: Semifinals"
    if week == 17:
        return "Playoffs: Championship"
    if week == 18:
        return "Season in Review"
    return None


def fantasy_week_label(week: int) -> str:
    return {15: "fantasy playoffs, round 1 (quarterfinals)", 16: "fantasy semifinals",
            17: "fantasy championship week"}.get(week, f"fantasy Week {week}")


def _slot(slot: str, article_type: str, note: str = "") -> dict:
    return {"slot": slot, "article_type": article_type, "note": note}


def _shelf(types: list) -> list:
    return [_slot(f"Fantasy Shelf {i}", t) for i, t in enumerate(types, start=1)]


def fantasy_slate(week: int) -> list:
    """The week's fantasy slots: [{slot, article_type, note}] in display order. Empty for W19+."""
    if week == 1:
        slots = [_slot(s, "FANTASY_DRAFT_TIERS",
                       f"Position: {pos} only. Rank and tier the {name} for the draft; ignore other positions.")
                 for s, (pos, name) in zip((HERO, CARD1, CARD2, CARD3), DRAFT_TIER_POSITIONS)]
        return slots + _shelf(["FANTASY_SLEEPERS_BUSTS", "FANTASY_MOCK_DRAFT", "FANTASY_ROOKIE_RANKINGS"])

    weekly_shelf = ["FANTASY_RANKINGS_TIERS", "FANTASY_BOX_SCORE_USAGE"]
    matchup = ["FANTASY_MATCHUP_EXPLOITS"] if week >= 3 else []
    kdst = ["FANTASY_STREAMING_DST_K"] if KDST_ENABLED and week >= 3 else []

    if 2 <= week <= 14:
        return [
            _slot(HERO, "FANTASY_START_SIT"),
            _slot(CARD1, "FANTASY_WAIVER_WIRE"),
            _slot(CARD2, "FANTASY_WEEKLY_REACTIONS"),
            _slot(CARD3, CARD3_SCHEDULE[week]),
        ] + _shelf(weekly_shelf + ["FANTASY_TEN_THINGS"] + matchup + kdst)
    if week == 15:
        return [
            _slot(HERO, "FANTASY_START_SIT", "Playoff edition: fantasy playoffs round 1 (quarterfinals)."),
            _slot(CARD1, "FANTASY_PLAYOFF_STREAMERS"),
            _slot(CARD2, "FANTASY_WEEKLY_REACTIONS", "Recap of Week 14, the last week of the fantasy regular season."),
            _slot(CARD3, "FANTASY_PLAYOFF_SCHEDULE_PLANNER", "Final look: Weeks 15–17 only, for teams that made the playoffs."),
        ] + _shelf(weekly_shelf + ["FANTASY_CEILING_PLAYS"] + kdst + matchup)
    if week == 16:
        return [
            _slot(HERO, "FANTASY_START_SIT", "Playoff edition: fantasy semifinals."),
            _slot(CARD1, "FANTASY_PLAYOFF_STREAMERS"),
            _slot(CARD2, "FANTASY_WEEKLY_REACTIONS", "Recap of Week 15, fantasy playoffs round 1."),
            _slot(CARD3, "FANTASY_CEILING_PLAYS"),
        ] + _shelf(weekly_shelf + kdst + matchup)
    if week == 17:
        return [
            _slot(HERO, "FANTASY_START_SIT", "Championship edition: every call is for the fantasy final."),
            _slot(CARD1, "FANTASY_CEILING_PLAYS", "Championship week: favorites vs. underdogs in the final."),
            _slot(CARD2, "FANTASY_PLAYOFF_STREAMERS"),
            _slot(CARD3, "FANTASY_WEEKLY_REACTIONS", "Recap of Week 16, the fantasy semifinals."),
        ] + _shelf(["FANTASY_RANKINGS_TIERS"] + kdst + ["FANTASY_BOX_SCORE_USAGE"] + matchup)
    if week == 18:
        return [
            _slot(HERO, "FANTASY_SEASON_IN_REVIEW"),
            _slot(CARD1, "FANTASY_AWARDS"),
            _slot(CARD2, "FANTASY_DRAFT_RETRO"),
            _slot(CARD3, "FANTASY_PROJECTION_REPORT_CARD"),
        ] + _shelf(["FANTASY_DYNASTY_EARLY_LOOK"])
    return []


def fantasy_temporal_instruction(week: int) -> str:
    """The fantasy calendar block for a fantasy article's prompt."""
    phase = fantasy_phase(week)
    if week == 1:
        return """
TEMPORAL PUBLISHING CONTEXT (FANTASY DRAFT PREP, BEFORE WEEK 1):
- No games have been played this season. There are no weekly projections, box scores, matchup ratings or rostered percentages yet.
- Your evidence is the preseason draft board (ADP and its range), each player's role on his depth chart, age, experience and his offense's strength.
"""
    when = {
        15: "Week 15 opens the fantasy playoffs (round 1 / quarterfinals; the top two seeds have a bye).",
        16: "Week 16 is the fantasy semifinals.",
        17: "Week 17 is the fantasy championship. Week 18 does not count in fantasy.",
        18: "The fantasy season is over: Week 17 was the championship and NFL Week 18 does not count.",
    }.get(week, f"Week {week} is part of the fantasy regular season (Weeks 1–14; Week 14 decides playoff spots).")
    return f"""
TEMPORAL PUBLISHING CONTEXT (FANTASY {phase.upper()}, PUBLISHED BEFORE WEEK {week}):
- {when}
- Box scores are available through Week {week - 1}. Week {week} has not been played.
- Projections and rostered percentages for Week {week} are available (made before kickoff).
"""


FANTASY_DESK_GUIDELINES = """
FANTASY DESK RULES (MU FANTASY):
1. SCORING & POSITIONS: Full PPR (1 point per reception). Cover QB, RB, WR and TE only; there are no kicker, team-defense or IDP rankings yet.
2. RESEARCH FIRST: Use the fantasy tools before writing. Every projection, fantasy point total, rank, ADP, rostered percentage and stat line in your article must come from them. Name players with their team and position the first time they appear.
3. HOW TO TALK ABOUT THE DATA:
- Rostered and started percentages: write "rostered in 35% of leagues" or "started in 80% of leagues." Never mention simulations, simulated leagues, or how many leagues there are.
- ADP: "an ADP of 14.3" or "a third-round pick on average"; give the range when it matters ("went as early as pick 8 and as late as 22").
- Projections are the Mu projection: "we project him for 17.8 points," "the Mu projection has him as the RB3 this week."
- Matchup ratings: describe them in words ("the softest matchup a receiver can get," "a top-five defense against tight ends"), not as star counts.
4. NO INJURIES: This league has no injury data. Never mention injuries, injury reports, questionable tags or players being out.
5. MAKE CALLS: Readers want decisions. Give clear verdicts (start, sit, add, drop, buy, sell, draft, avoid) and a reason built on the numbers.
6. YOUR OWN TRACK RECORD: Only mention your earlier columns, picks or calls if they appear under YOUR RECENT TAKES or YOUR PREDICTION TRACK RECORD above. Never invent a past call.
"""
