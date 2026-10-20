"""Mock Draft Simulation Engine.

Simulates an analytical 32-pick Round 1 NFL Mock Draft by pairing:
1. Official Round 1 selection order (reverse standings + playoff finish).
2. Team roster deficiencies & positional needs from starter deficits and age cliffs.
3. Scouted prospect board (draft_prospects for class season + 1, via pipeline.draft_board; or a CSV with --file).
4. Positional value weights (QB / OT / EDGE / CB draft premium).

The board's value term is the public scouting consensus (team management step 4, engines/scouting_engine.py): BPA =
(grade - 60) * 4 plus the slot delta against the consensus rank, so this stays the media's mock, which the GM agents'
real draft can beat or miss. Without a visible consensus (no scouting layer yet, or a CSV board) it falls back to OVR.

Each selection carries its pick's `locked` flag and `selection_status` from the draft order. A run from the
database is saved to mock_draft_picks for its (season, week) by `save`, so every article in a week cites the
same picks; newsroom.db_tools.get_mock_draft returns that saved run and only runs the engine when there is none.

CLI:
    python3 -m engines.mock_draft_engine                     # current season, board from the database
    python3 -m engines.mock_draft_engine --season 2035 --save
    python3 -m engines.mock_draft_engine --file captures/<run>/prospects.csv    # a board not loaded yet
"""

import argparse
import json
import sys
from typing import Dict, List, Optional

from pipeline.ingest_prospects import Prospect, load_prospects_from_csv

UNRATED_OVR = 50.0    # a prospect whose OVR the scan left blank is valued as a low grade, not dropped


# Positional Group mapping matching newsroom/db_tools.py
POSITION_GROUP_MAP = {
    "QB": "QB",
    "HB": "RB", "RB": "RB", "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "LT": "OT", "RT": "OT", "OT": "OT",
    "LG": "IOL", "C": "IOL", "RG": "IOL", "IOL": "IOL", "OG": "IOL",
    "LEDG": "EDGE", "REDG": "EDGE", "EDGE": "EDGE", "DE": "EDGE",
    "DT": "DT", "NT": "DT",
    "MIKE": "LB", "WILL": "LB", "SAM": "LB", "LB": "LB", "ILB": "LB", "OLB": "LB",
    "CB": "CB",
    "FS": "S", "SS": "S", "S": "S",
    "K": "SPECIALISTS", "P": "SPECIALISTS", "LS": "SPECIALISTS"
}

# Premium positional multipliers in Round 1
POSITIONAL_PREMIUM = {
    "QB": 1.25,
    "OT": 1.12,
    "EDGE": 1.12,
    "CB": 1.10,
    "WR": 1.02,
    "DT": 1.00,
    "TE": 0.90,
    "IOL": 0.88,
    "LB": 0.85,
    "S": 0.82,
    "RB": 0.80,
    "SPECIALISTS": 0.20
}

class MockDraftEngine:
    def __init__(self, season: Optional[int] = None, prospects_path: Optional[str] = None,
                 grades: Optional[Dict[int, Dict]] = None):
        self.season = season or self._resolve_current_season()
        self.draft_season = self.season + 1
        self.prospects_path = prospects_path          # None: the board comes from draft_prospects
        self.grades = grades or {}                    # {prospect_id: {grade, consensus_rank, round_label}}

    def load_prospects(self) -> List[Prospect]:
        """The board: the CSV given to the constructor (read leniently), else draft_prospects."""
        if self.prospects_path:
            return load_prospects_from_csv(self.prospects_path, strict=False)
        import psycopg
        from db.db_handler import DatabaseHandler
        from pipeline.draft_board import read_board
        with psycopg.connect(DatabaseHandler().db_conn_info) as conn:
            board = read_board(conn.cursor(), self.draft_season)
            if not self.grades:
                try:
                    from engines.scouting_read import public_grades
                    self.grades = public_grades(conn.cursor(), self.season)
                except Exception:
                    self.grades = {}
        if not board:
            raise ValueError(f"No scouted board for the {self.draft_season} draft class in draft_prospects "
                             f"(load a Draft Board scan: python3 -m pipeline.load_capture captures/<run>)")
        return board

    def _resolve_current_season(self) -> int:
        try:
            from db.db_handler import DatabaseHandler
            db = DatabaseHandler()
            season, _ = db.get_current_league_state()
            return season
        except Exception:
            return 2035

    def get_draft_order(self) -> List[Dict]:
        """Fetch the official Round 1 draft order."""
        try:
            from newsroom.db_tools import get_draft_order
            raw = get_draft_order.invoke({"season": self.season})
            data = json.loads(raw)
            if "round_1_order" in data and data["round_1_order"]:
                return data["round_1_order"]
        except Exception as e:
            # Fallback mock order if database is unavailable
            pass

        return [
            {"pick": i, "team": f"Team {i}", "abbreviation": f"T{i}", "team_id": i, "record": "8-9"}
            for i in range(1, 33)
        ]

    def get_team_needs(self, team_id_or_abbr: str) -> List[Dict]:
        """Fetch team draft needs with scores."""
        try:
            from newsroom.db_tools import get_team_draft_needs
            raw = get_team_draft_needs.invoke({"team_identifier": str(team_id_or_abbr), "season": self.season})
            data = json.loads(raw)
            return data.get("top_ranked_draft_needs", [])
        except Exception:
            return []

    def evaluate_fit(self, prospect: Prospect, pick_number: int, team_needs: List[Dict]) -> tuple[float, str]:
        """Calculate composite fit score and analytical rationale."""
        pos_group = POSITION_GROUP_MAP.get(prospect.position, prospect.position)
        prem = POSITIONAL_PREMIUM.get(pos_group, 1.0)
        ovr = prospect.overall_rating if prospect.overall_rating is not None else UNRATED_OVR
        cons = self.grades.get(prospect.prospect_id) if getattr(self, "grades", None) else None

        # 1. Base Player Value (BPA): the public consensus grade when the scouting layer has one, else OVR
        if cons:
            bpa_score = (cons["grade"] - 60.0) * 4.0
            slot_value = (pick_number - cons["consensus_rank"]) * 0.5
            elite = cons["grade"] >= 88.0
        else:
            bpa_score = (ovr - 70.0) * 4.0
            slot_value = (pick_number - prospect.projected_pick) * 0.5
            elite = bpa_score > 35
        bpa_score += slot_value

        # 2. Team Need Fit
        need_score = 0.0
        need_match = None
        for need in team_needs:
            if need.get("position_group") == pos_group:
                need_match = need
                need_score = float(need.get("need_score") or 0.0)
                break

        # QB Special Rule: If team has top QB need, high priority; if not needed, steep penalty
        if pos_group == "QB":
            if need_match and need_score >= 30:
                need_score *= 1.35
            else:
                need_score = -50.0  # Avoid drafting 1st round QB if already set

        # 3. Composite Calculation
        # Total = 40% Need + 40% BPA + 20% Positional Premium
        composite = (0.45 * (need_score * 1.5)) + (0.40 * bpa_score) + (10.0 * (prem - 1.0))

        # 4. Formulate Rationale
        if need_match and need_score >= 35:
            reasons = need_match.get("analytical_reasons", [])
            detail = f"; {reasons[0]}" if reasons else ""
            rationale = (f"Fills primary {need_match.get('urgency', 'HIGH')} need at {pos_group}. "
                         f"{need_match.get('current_starter', 'Starter deficit')}{detail}")
        elif need_match:
            rationale = (f"Addresses positional depth at {pos_group}. "
                         f"Adds blue-chip competition to {need_match.get('current_starter', 'unit')}.")
        elif elite:
            label = f" ({cons['grade']:.1f} consensus grade)" if cons else ""      # writers never see OVR (Q6)
            rationale = (f"Best Player Available: {prospect.full_name}{label} represents "
                         f"transcendent value too high to pass on at pick {pick_number}.")
        else:
            school = f"{prospect.college} standout" if prospect.college else "a polished prospect"
            rationale = f"Secures foundational talent at premium position ({pos_group}) with {school}."

        return composite, rationale

    def run_mock(self, prospects: Optional[List[Prospect]] = None, limit: Optional[int] = None) -> List[Dict]:
        """Simulate Round 1 (all 32 picks, or the first `limit`)."""
        if prospects is None:
            prospects = self.load_prospects()

        available_prospects = list(prospects)
        draft_order = self.get_draft_order()
        if limit:
            draft_order = draft_order[:limit]
        selections = []

        for slot in draft_order:
            pick_num = slot["pick"]
            team_name = slot.get("team", f"Team {pick_num}")
            team_abbr = slot.get("abbreviation", "NFL")
            team_id = slot.get("team_id")

            # Query needs for this team
            needs = self.get_team_needs(team_id or team_abbr)

            best_prospect = None
            best_score = -9999.0
            best_rationale = ""

            for p in available_prospects:
                score, rationale = self.evaluate_fit(p, pick_num, needs)
                if score > best_score:
                    best_score = score
                    best_prospect = p
                    best_rationale = rationale

            if best_prospect is None and available_prospects:
                best_prospect = available_prospects[0]
                best_rationale = "Best remaining prospect on the board."

            if best_prospect:
                available_prospects.remove(best_prospect)
                selections.append({
                    "pick": pick_num,
                    "team_id": team_id,
                    "team": team_name,
                    "team_abbr": team_abbr,
                    "record": slot.get("record", ""),
                    "locked": bool(slot.get("locked")),
                    "selection_status": slot.get("selection_status"),
                    "prospect": best_prospect.to_dict(),
                    "fit_score": round(best_score, 1),
                    "rationale": best_rationale
                })

        return selections

    def save(self, cur, week: int, selections: List[Dict]) -> int:
        """Store this run as the (season, week) mock draft. Only a run from the database board can be saved
        (its picks point at draft_prospects rows)."""
        from pipeline.draft_board import save_mock
        if any(s["prospect"].get("prospect_id") is None for s in selections):
            raise ValueError("only a mock drawn from draft_prospects can be saved (this one came from a CSV)")
        return save_mock(cur, self.season, week, selections)

    def format_board(self, selections: List[Dict]) -> str:
        """Render a readable textual representation of the mock draft."""
        lines = [
            f"=" * 90,
            f"PROJECT MU: ROUND 1 MOCK DRAFT (CLASS OF {self.draft_season})",
            f"Evaluated against Season {self.season} roster deficiencies and official draft order",
            f"=" * 90,
            f"{'PICK':<6}{'TEAM':<18}{'PLAYER':<22}{'POS':<6}{'COLLEGE':<18}{'OVR':<6}{'PROJ':<6}",
            f"-" * 90,
        ]

        for s in selections:
            p = s["prospect"]
            pick_str = f"#{s['pick']}"
            lock = "" if s.get("locked") else " (provisional)"
            lines.append(
                f"{pick_str:<6}{s['team'] or '?':<18}{p['full_name']:<22}{p['position'] or '?':<6}"
                f"{p['college'] or '?':<18}{p['overall_rating'] or '?':<6}{p['projected_pick']:<6}{lock}"
            )
            lines.append(f"      -> Rationale: {s['rationale']}")
            lines.append("")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Run Project Mu Round 1 Mock Draft Simulation")
    parser.add_argument("--season", type=int, help="NFL season to evaluate order and needs from")
    parser.add_argument("--file", help="a prospects CSV instead of the draft_prospects board")
    parser.add_argument("--limit", type=int, help="only the first N picks (24 at Week 20)")
    parser.add_argument("--save", action="store_true",
                        help="store the run as this season's current-week mock draft (database board only)")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    engine = MockDraftEngine(season=args.season, prospects_path=args.file)
    selections = engine.run_mock(limit=args.limit)
    if args.save:
        import psycopg
        from db.db_handler import DatabaseHandler
        with psycopg.connect(DatabaseHandler().db_conn_info) as conn:
            cur = conn.cursor()
            cur.execute("SELECT current_week FROM league_state WHERE id = 1")
            week = cur.fetchone()[0]
            engine.save(cur, week, selections)
            conn.commit()
        print(f"Saved as the Season {engine.season}, Week {week} mock draft", file=sys.stderr)

    if args.json:
        print(json.dumps(selections, indent=2))
    else:
        print(engine.format_board(selections))


if __name__ == "__main__":
    main()
