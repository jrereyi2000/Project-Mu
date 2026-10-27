"""pipeline/ir_returns.py: who JR takes off IR by hand (Madden never activates healed IR players)."""
from pipeline.ir_returns import banner, due_players, notice_text


def row(pid, week, rem, ir=True, team=1, name=None):
    return {"player_id": pid, "player_name": name or f"P.{pid}", "position": "WR", "ovr": 80, "team_id": team,
            "team": f"T{team}", "week": week, "weeks_remaining": rem, "is_ir": ir}


def ids(players):
    return [p["player_id"] for p in players]


def test_healed_on_ir_is_due_and_new_only_the_first_week():
    rows = [row(1, 8, 1), row(1, 9, 0), row(1, 10, 0)]
    p = due_players(rows, 10)
    assert ids(p) == [1] and p[0]["healed_week"] == 9 and not p[0]["new"]
    assert due_players(rows[:2], 9)[0]["new"]


def test_projected_from_last_report_countdown():
    rows = [row(1, 8, 1), row(2, 8, 3)]
    assert ids(due_players(rows, 9)) == [1]
    assert due_players(rows, 9)[0]["new"]


def test_dropped_off_the_report_or_not_ir_is_not_due():
    rows = [row(1, 7, 1), row(2, 8, 0), row(3, 8, 0, ir=False)]   # 1 is absent from the week 8 report
    assert ids(due_players(rows, 8)) == [2]


def test_postseason_only_teams_playing_and_their_own_reports():
    rows = [row(1, 18, 0, team=1), row(2, 18, 0, team=2), row(3, 18, 1, team=3), row(4, 19, 0, team=4)]
    playing = {19: {4}, 20: {3, 4}}                 # team 3 had a bye in 19, its last report is week 18
    assert ids(due_players(rows, 20, playing)) == [3, 4]
    assert ids(due_players(rows, 19, playing)) == [4]


def test_text_empty_when_nobody_due():
    assert banner([], 5) == "" and notice_text([], 5) == ""
    p = due_players([row(1, 4, 0, name="J.Doe")], 4)
    assert "J.Doe" in banner(p, 4) and "J.Doe*" in notice_text(p, 4)
