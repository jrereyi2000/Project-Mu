"""Unit tests for parsing and validating the year-end All-Pro Roster article (PROJECT_SPEC §7.6).
No live database required.

Run: python3 -m unittest tests.test_all_pro
"""
import unittest
from unittest.mock import MagicMock

from newsroom.all_pro import (
    ALL_SLOT_QUOTAS,
    all_pro_fix_prompt,
    parse_all_pro,
    resolve_player,
    resolve_team,
    store_all_pro_teams,
)

TEAMS = [
    (1, "Cowboys", "Dallas", "DAL"),
    (2, "Patriots", "New England", "NE"),
    (3, "Vikings", "Minnesota", "MIN"),
    (4, "Chiefs", "Kansas City", "KC"),
    (5, "Ravens", "Baltimore", "BAL"),
    (6, "49ers", "San Francisco", "SF"),
]

# Mock roster including same-surname players on the same team to verify position disambiguation
MOCK_PLAYERS = [
    # Patriots (ID 2)
    {"player_id": 101, "full_name": "Drake Maye", "player_name": "D. Maye", "team_id": 2, "position": "QB", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 102, "full_name": "Rhamondre Stevenson", "player_name": "R. Stevenson", "team_id": 2, "position": "HB", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 103, "full_name": "Demario Douglas", "player_name": "D. Douglas", "team_id": 2, "position": "WR", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 104, "full_name": "Hunter Henry", "player_name": "H. Henry", "team_id": 2, "position": "TE", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 105, "full_name": "Christian Gonzalez", "player_name": "C. Gonzalez", "team_id": 2, "position": "CB", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 106, "full_name": "Keion White", "player_name": "K. White", "team_id": 2, "position": "LEDG", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 107, "full_name": "David Andrews", "player_name": "D. Andrews", "team_id": 2, "position": "C", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 108, "full_name": "Cole Strange", "player_name": "C. Strange", "team_id": 2, "position": "LG", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 109, "full_name": "Michael Onwenu", "player_name": "M. Onwenu", "team_id": 2, "position": "RT", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 110, "full_name": "Christian Barmore", "player_name": "C. Barmore", "team_id": 2, "position": "DT", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 111, "full_name": "Kyle Dugger", "player_name": "K. Dugger", "team_id": 2, "position": "SS", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 112, "full_name": "Marcus Jones", "player_name": "M. Jones", "team_id": 2, "position": "CB", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 113, "full_name": "Joe Cardona", "player_name": "J. Cardona", "team_id": 2, "position": "LS", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},
    {"player_id": 114, "full_name": "Ja'Whaun Bentley", "player_name": "J. Bentley", "team_id": 2, "position": "MIKE", "team_name": "Patriots", "city": "New England", "abbreviation": "NE"},

    # Same team (Cowboys ID 1) with two players named "Davis": Trey'mane Davis (WR) and Carl Davis (DT)
    {"player_id": 201, "full_name": "Trey'mane Davis", "player_name": "T. Davis", "team_id": 1, "position": "WR", "team_name": "Cowboys", "city": "Dallas", "abbreviation": "DAL"},
    {"player_id": 202, "full_name": "Carl Davis", "player_name": "C. Davis", "team_id": 1, "position": "DT", "team_name": "Cowboys", "city": "Dallas", "abbreviation": "DAL"},
    {"player_id": 203, "full_name": "Micah Parsons", "player_name": "M. Parsons", "team_id": 1, "position": "REDG", "team_name": "Cowboys", "city": "Dallas", "abbreviation": "DAL"},
    {"player_id": 204, "full_name": "CeeDee Lamb", "player_name": "C. Lamb", "team_id": 1, "position": "WR", "team_name": "Cowboys", "city": "Dallas", "abbreviation": "DAL"},
    {"player_id": 205, "full_name": "Trevon Diggs", "player_name": "T. Diggs", "team_id": 1, "position": "CB", "team_name": "Cowboys", "city": "Dallas", "abbreviation": "DAL"},
    {"player_id": 206, "full_name": "Brandon Aubrey", "player_name": "B. Aubrey", "team_id": 1, "position": "K", "team_name": "Cowboys", "city": "Dallas", "abbreviation": "DAL"},
    {"player_id": 207, "full_name": "Tyler Smith", "player_name": "T. Smith", "team_id": 1, "position": "LT", "team_name": "Cowboys", "city": "Dallas", "abbreviation": "DAL"},
    {"player_id": 208, "full_name": "Zack Martin", "player_name": "Z. Martin", "team_id": 1, "position": "RG", "team_name": "Cowboys", "city": "Dallas", "abbreviation": "DAL"},
    {"player_id": 209, "full_name": "Hunter Luepke", "player_name": "H. Luepke", "team_id": 1, "position": "FB", "team_name": "Cowboys", "city": "Dallas", "abbreviation": "DAL"},

    # Vikings (ID 3)
    {"player_id": 301, "full_name": "Justin Jefferson", "player_name": "J. Jefferson", "team_id": 3, "position": "WR", "team_name": "Vikings", "city": "Minnesota", "abbreviation": "MIN"},
    {"player_id": 302, "full_name": "Ryan Wright", "player_name": "R. Wright", "team_id": 3, "position": "P", "team_name": "Vikings", "city": "Minnesota", "abbreviation": "MIN"},
    {"player_id": 303, "full_name": "Ivan Pace Jr.", "player_name": "I. Pace", "team_id": 3, "position": "WILL", "team_name": "Vikings", "city": "Minnesota", "abbreviation": "MIN"},
    {"player_id": 304, "full_name": "Josh Metellus", "player_name": "J. Metellus", "team_id": 3, "position": "FS", "team_name": "Vikings", "city": "Minnesota", "abbreviation": "MIN"},

    # Ravens (ID 5)
    {"player_id": 401, "full_name": "Lamar Jackson", "player_name": "L. Jackson", "team_id": 5, "position": "QB", "team_name": "Ravens", "city": "Baltimore", "abbreviation": "BAL"},
    {"player_id": 402, "full_name": "Derrick Henry", "player_name": "D. Henry", "team_id": 5, "position": "HB", "team_name": "Ravens", "city": "Baltimore", "abbreviation": "BAL"},
    {"player_id": 403, "full_name": "Roquan Smith", "player_name": "R. Smith", "team_id": 5, "position": "MIKE", "team_name": "Ravens", "city": "Baltimore", "abbreviation": "BAL"},
    {"player_id": 404, "full_name": "Kyle Hamilton", "player_name": "K. Hamilton", "team_id": 5, "position": "SS", "team_name": "Ravens", "city": "Baltimore", "abbreviation": "BAL"},
    {"player_id": 405, "full_name": "Justin Tucker", "player_name": "J. Tucker", "team_id": 5, "position": "K", "team_name": "Ravens", "city": "Baltimore", "abbreviation": "BAL"},

    # 49ers (ID 6)
    {"player_id": 501, "full_name": "Christian McCaffrey", "player_name": "C. McCaffrey", "team_id": 6, "position": "HB", "team_name": "49ers", "city": "San Francisco", "abbreviation": "SF"},
    {"player_id": 502, "full_name": "Kyle Juszczyk", "player_name": "K. Juszczyk", "team_id": 6, "position": "FB", "team_name": "49ers", "city": "San Francisco", "abbreviation": "SF"},
    {"player_id": 503, "full_name": "George Kittle", "player_name": "G. Kittle", "team_id": 6, "position": "TE", "team_name": "49ers", "city": "San Francisco", "abbreviation": "SF"},
    {"player_id": 504, "full_name": "Nick Bosa", "player_name": "N. Bosa", "team_id": 6, "position": "REDG", "team_name": "49ers", "city": "San Francisco", "abbreviation": "SF"},
    {"player_id": 505, "full_name": "Fred Warner", "player_name": "F. Warner", "team_id": 6, "position": "MIKE", "team_name": "49ers", "city": "San Francisco", "abbreviation": "SF"},
    {"player_id": 506, "full_name": "Trent Williams", "player_name": "T. Williams", "team_id": 6, "position": "LT", "team_name": "49ers", "city": "San Francisco", "abbreviation": "SF"},
]

ROSTER_INDEX = {
    "teams": TEAMS,
    "players": MOCK_PLAYERS,
}


def create_sample_article():
    """Builds a syntactically valid 58-player All-Pro roster article."""
    return """# The 2035 AP All-Pro Team: The Official Rosters
> **EXCERPT:** The definitive First and Second-Team All-Pro rosters for 2035.

Introductory paragraph about the voting class.

## First-Team Offense
- **QB**: Drake Maye, New England Patriots — Led the NFL in passer rating.
- **RB**: Christian McCaffrey, San Francisco 49ers — Totaled 1,800 yards from scrimmage.
- **FB**: Kyle Juszczyk, San Francisco 49ers — Premier lead blocker in the league.
- **WR**: Justin Jefferson, Minnesota Vikings — Led the league in receiving yards.
- **WR**: CeeDee Lamb, Dallas Cowboys — 110 receptions and 12 scores.
- **WR**: Demario Douglas, New England Patriots — Dynamic slot technician.
- **TE**: George Kittle, San Francisco 49ers — Dominant two-way tight end.
- **AP**: Rhamondre Stevenson, New England Patriots — Dual-threat backfield weapon.
- **LT**: Trent Williams, San Francisco 49ers — Elite blindside protector.
- **LG**: Cole Strange, New England Patriots — Anchor in pass protection.
- **C**: David Andrews, New England Patriots — Offensive line coordinator.
- **RG**: Zack Martin, Dallas Cowboys — First-team anchor.
- **RT**: Michael Onwenu, New England Patriots — Mauler at right tackle.

## First-Team Defense & Special Teams
- **EDGE**: Micah Parsons, Dallas Cowboys — 17.0 sacks and 20 TFLs.
- **EDGE**: Nick Bosa, San Francisco 49ers — Relentless pressure rate.
- **DI**: Christian Barmore, New England Patriots — Interior pocket crusher.
- **DI**: Carl Davis, Dallas Cowboys — Immovable run stuffer.
- **LB**: Fred Warner, San Francisco 49ers — Best cover linebacker in football.
- **LB**: Roquan Smith, Baltimore Ravens — 140 tackles and 3 forced fumbles.
- **LB**: Ja'Whaun Bentley, New England Patriots — Downhill thumper.
- **CB**: Christian Gonzalez, New England Patriots — Lockdown outside cornerback.
- **CB**: Trevon Diggs, Dallas Cowboys — 7 interceptions.
- **SCB**: Marcus Jones, New England Patriots — Shutdown nickel presence.
- **S**: Kyle Hamilton, Baltimore Ravens — All-field defensive eraser.
- **S**: Kyle Dugger, New England Patriots — Physical box enforcer.
- **K**: Brandon Aubrey, Dallas Cowboys — 35 of 36 field goals made.
- **P**: Ryan Wright, Minnesota Vikings — 48.5 net punting average.
- **KR**: Marcus Jones, New England Patriots — 2 kickoff return touchdowns.
- **PR**: Marcus Jones, New England Patriots — League leader in punt return yards.
- **ST**: Josh Metellus, Minnesota Vikings — Special teams ace and tackler.
- **LS**: Joe Cardona, New England Patriots — Flawless snaps all season.

## Second-Team Offense
- **QB**: Lamar Jackson, Baltimore Ravens — Dual-threat dynamic playmaker.
- **RB**: Derrick Henry, Baltimore Ravens — Power runner with 16 touchdowns.
- **FB**: Hunter Luepke, Dallas Cowboys — Dependable short-yardage converted FB.
- **WR**: Trey'mane Davis, Dallas Cowboys — Breakout downfield separator.
- **WR**: Demario Douglas, New England Patriots — Reliable intermediate target.
- **WR**: Justin Jefferson, Minnesota Vikings — Explosive yards after catch.
- **TE**: Hunter Henry, New England Patriots — Red zone safety valve.
- **AP**: Derrick Henry, Baltimore Ravens — Relentless fourth-quarter closer.
- **LT**: Tyler Smith, Dallas Cowboys — Powerhouse left tackle.
- **LG**: Cole Strange, New England Patriots — Strong interior anchor.
- **C**: David Andrews, New England Patriots — Consistent veteran leader.
- **RG**: Zack Martin, Dallas Cowboys — Consistent run game pusher.
- **RT**: Michael Onwenu, New England Patriots — Imposing trench presence.

## Second-Team Defense & Special Teams
- **EDGE**: Keion White, New England Patriots — Breakout edge disruptor.
- **EDGE**: Micah Parsons, Dallas Cowboys — Constant backfield visitor.
- **DI**: Christian Barmore, New England Patriots — Consistent double-team eater.
- **DI**: Carl Davis, Dallas Cowboys — Reliable run lane stopper.
- **LB**: Ivan Pace Jr., Minnesota Vikings — Blitzing second-level playmaker.
- **LB**: Fred Warner, San Francisco 49ers — High-IQ field general.
- **LB**: Ja'Whaun Bentley, New England Patriots — Solid box tackler.
- **CB**: Trevon Diggs, Dallas Cowboys — Ball-hawking perimeter cover.
- **CB**: Christian Gonzalez, New England Patriots — Smooth mirror-and-match corner.
- **SCB**: Marcus Jones, New England Patriots — Agile slot stopper.
- **S**: Josh Metellus, Minnesota Vikings — Versatile defensive chess piece.
- **S**: Kyle Dugger, New England Patriots — Strong run support hybrid.
- **K**: Justin Tucker, Baltimore Ravens — Legendary clutch kicker.
- **P**: Ryan Wright, Minnesota Vikings — Directional punting specialist.
- **KR**: Marcus Jones, New England Patriots — Explosive returner.
- **PR**: Demario Douglas, New England Patriots — Shifty punt return option.
- **ST**: Josh Metellus, Minnesota Vikings — Core special teamer.
- **LS**: Joe Cardona, New England Patriots — Dependable battery partner.
"""


class ResolvePlayerTests(unittest.TestCase):
    def test_name_and_team_resolution(self):
        player, name, team, err = resolve_player("Drake Maye, New England Patriots", "QB", ROSTER_INDEX)
        self.assertIsNone(err)
        self.assertIsNotNone(player)
        self.assertEqual(player["player_id"], 101)
        self.assertEqual(player["position"], "QB")

    def test_position_disambiguation_same_team_same_surname(self):
        # Two players named "Davis" on the Dallas Cowboys: Trey'mane Davis (WR) and Carl Davis (DT)
        # 1. Resolve for WR slot -> must resolve to Trey'mane Davis (ID 201)
        wr_player, _, _, wr_err = resolve_player("Davis, Dallas Cowboys", "WR", ROSTER_INDEX)
        self.assertIsNone(wr_err)
        self.assertEqual(wr_player["player_id"], 201)

        # 2. Resolve for DI / Interior Lineman slot -> must resolve to Carl Davis (ID 202)
        di_player, _, _, di_err = resolve_player("Davis, Dallas Cowboys", "DI", ROSTER_INDEX)
        self.assertIsNone(di_err)
        self.assertEqual(di_player["player_id"], 202)

    def test_edge_rusher_slot_compatibility(self):
        # Micah Parsons is listed as REDG in Madden; slot is EDGE
        player, _, _, err = resolve_player("Micah Parsons (Cowboys)", "EDGE", ROSTER_INDEX)
        self.assertIsNone(err)
        self.assertEqual(player["player_id"], 203)

    def test_unlisted_player_returns_error(self):
        player, _, _, err = resolve_player("Fake Player, Patriots", "QB", ROSTER_INDEX)
        self.assertIsNone(player)
        self.assertIn("Could not find player", err)


class ParseAllProTests(unittest.TestCase):
    def test_valid_all_pro_article(self):
        entries, errors = parse_all_pro(create_sample_article(), ROSTER_INDEX)
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 62)  # 31 First-Team + 31 Second-Team

        # Check First-Team QB
        first_qb = next(e for e in entries if e["award_type"] == "First Team All-Pro" and e["slot"] == "QB")
        self.assertEqual(first_qb["player_name"], "Drake Maye")
        self.assertEqual(first_qb["player_id"], 101)

        # Check Second-Team QB
        second_qb = next(e for e in entries if e["award_type"] == "Second Team All-Pro" and e["slot"] == "QB")
        self.assertEqual(second_qb["player_name"], "Lamar Jackson")
        self.assertEqual(second_qb["player_id"], 401)

    def test_missing_slot_reported(self):
        # Remove First-Team FB
        text = create_sample_article().replace(
            "- **FB**: Kyle Juszczyk, San Francisco 49ers — Premier lead blocker in the league.\n",
            ""
        )
        entries, errors = parse_all_pro(text, ROSTER_INDEX)
        self.assertTrue(any("Missing 1 slot(s) for FB" in e for e in errors))

    def test_duplicate_player_on_same_team_level_reported(self):
        # Duplicate Drake Maye in place of McCaffrey on First Team (QB and RB)
        text = create_sample_article().replace(
            "- **RB**: Christian McCaffrey, San Francisco 49ers — Totaled 1,800 yards from scrimmage.",
            "- **RB**: Drake Maye, New England Patriots — Running back duplicate."
        )
        entries, errors = parse_all_pro(text, ROSTER_INDEX)
        self.assertTrue(any("selected at multiple primary offensive positions" in e for e in errors))

    def test_fix_prompt_generation(self):
        errors = [
            "First Team All-Pro: Missing 1 slot(s) for FB",
            "Could not find player 'Fake Guy' (team: 'Patriots')",
        ]
        prompt = all_pro_fix_prompt(errors)
        self.assertIn("MANDATORY CORRECTION INSTRUCTIONS", prompt)
        self.assertIn("Missing 1 slot(s) for FB", prompt)
        self.assertIn("Fake Guy", prompt)


class StoreAllProTests(unittest.TestCase):
    def test_store_all_pro_teams(self):
        cur = MagicMock()
        entries = [
            {"award_type": "First Team All-Pro", "player_id": 101, "team_id": 2, "player_name": "Drake Maye", "position": "QB"},
            {"award_type": "Second Team All-Pro", "player_id": 401, "team_id": 5, "player_name": "Lamar Jackson", "position": "QB"},
        ]
        count = store_all_pro_teams(cur, 2035, 19, 123, "YATES", entries)
        self.assertEqual(count, 2)
        cur.execute.assert_called_once()  # DELETE call
        cur.executemany.assert_called_once()  # INSERT call


if __name__ == "__main__":
    unittest.main()
