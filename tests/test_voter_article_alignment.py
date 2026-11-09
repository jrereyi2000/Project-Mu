"""Voter article alignment (OWNERS_SCOUTS_VOTERS U7 follow-up; CLOSE_OUT_CHECKLIST P1-12; PROJECT_SPEC §7.6b/§7.6c).

With media_voters active, the panel's ballots decide the results before the writer runs, the writer is handed the
stored results, and the article is checked against them with one rewrite on a miss. Offline: the ballot engine and
the database connection are faked; no PostgreSQL connection is opened.
"""
import types
from unittest import mock

from newsroom import award_ballots
from newsroom import award_winners as aw
from newsroom import swarm_dispatcher as sd


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return FakeCursor()

    def commit(self):
        pass


def _dispatcher():
    return types.SimpleNamespace(db=types.SimpleNamespace(db_conn_info="fake"))


AWARDS_RESULT = {
    "tallies": {"MVP": [{"id": 1, "points": 200, "first_place": 18}],
                "COTY": [{"id": 2, "points": 150, "first_place": 10}]},
    "candidates": {"MVP": [{"id": 1, "name": "Josh Allen", "team_id": 2, "position": "QB"}],
                   "COTY": [{"id": 2, "name": "Sean McDermott", "team_id": 2, "position": "HC"}]},
    "ballots": [],
}
ALL_PRO_RESULT = {
    "teams": {"first": {"QB": [{"id": 1, "votes": 20}]}, "second": {"QB": [{"id": 2, "votes": 12}]}},
    "candidates": {"QB": [{"id": 1, "name": "Josh Allen", "team_id": 2},
                          {"id": 2, "name": "Patrick Mahomes", "team_id": 12}]},
    "ballots": [],
}


def _run(at_id, week=22):
    d = _dispatcher()
    with mock.patch.object(sd.psycopg, "connect", return_value=FakeConn()), \
         mock.patch.object(award_ballots, "feature_mode", return_value="active"), \
         mock.patch.object(award_ballots, "decide_awards", return_value=AWARDS_RESULT) as dec_a, \
         mock.patch.object(award_ballots, "decide_all_pro", return_value=ALL_PRO_RESULT) as dec_p, \
         mock.patch.object(sd, "load_stored_winners", return_value={"MVP": {"name": "Josh Allen"}}), \
         mock.patch.object(sd, "load_stored_all_pro", return_value=[{"player_name": "Josh Allen"}]):
        return sd.SwarmDispatcher._ballot_brief(d, 2035, week, at_id), dec_a, dec_p


def test_active_awards_brief_hands_the_writer_the_stored_winner():
    brief, dec_a, _ = _run("AWARDS_TRACKER")
    assert brief["kind"] == "awards"
    assert "STORED BALLOT RESULTS" in brief["text"]
    assert "Josh Allen" in brief["text"] and "Sean McDermott" in brief["text"]
    assert brief["stored"] == {"MVP": {"name": "Josh Allen"}}
    dec_a.assert_called_once()


def test_active_all_pro_brief_hands_the_writer_the_stored_teams():
    brief, _, dec_p = _run("OFFICIAL_AP_ALL_PRO_ROSTER", week=19)
    assert brief["kind"] == "all_pro"
    assert "All-Pro teams are final" in brief["text"] and "Josh Allen" in brief["text"]
    dec_p.assert_called_once()


def test_off_or_shadow_and_other_articles_are_untouched():
    d = _dispatcher()
    with mock.patch.object(sd.psycopg, "connect", return_value=FakeConn()), \
         mock.patch.object(award_ballots, "feature_mode", return_value="shadow"):
        assert sd.SwarmDispatcher._ballot_brief(d, 2035, 22, "AWARDS_TRACKER") is None
        assert sd.SwarmDispatcher._ballot_brief(d, 2035, 19, "OFFICIAL_AP_ALL_PRO_ROSTER") is None
    assert sd.SwarmDispatcher._ballot_brief(d, 2035, 22, "POWER_RANKINGS") is None


def test_awards_brief_only_on_week_22():
    d = _dispatcher()
    assert sd.SwarmDispatcher._ballot_brief(d, 2035, 21, "AWARDS_TRACKER") is None


def test_a_broken_ballot_run_falls_back_to_todays_path():
    d = _dispatcher()
    with mock.patch.object(sd.psycopg, "connect", side_effect=RuntimeError("no db")):
        assert sd.SwarmDispatcher._ballot_brief(d, 2035, 22, "AWARDS_TRACKER") is None


def test_stored_checks_and_rewrite_prompts_describe_the_miss():
    teams = [(2, "Buffalo Bills", "Buffalo", "BUF"), (4, "Baltimore Ravens", "Baltimore", "BAL")]
    roster = {"teams": teams, "players": [
        {"player_id": 1, "full_name": "Josh Allen", "player_name": "Allen", "team_id": 2, "position": "QB",
         "team_name": "Buffalo Bills", "city": "Buffalo", "abbreviation": "BUF"},
        {"player_id": 9, "full_name": "Lamar Jackson", "player_name": "Jackson", "team_id": 4, "position": "QB",
         "team_name": "Baltimore Ravens", "city": "Baltimore", "abbreviation": "BAL"},
    ]}
    coaches = {"teams": teams, "coaches": [
        {"coach_id": 2, "coach_name": "Sean McDermott", "team_id": 2, "team_name": "Buffalo Bills",
         "city": "Buffalo", "abbreviation": "BUF"}]}
    stored = {"MVP": {"player_id": 1, "name": "Josh Allen", "team_id": 2}}
    text = "## MVP: Lamar Jackson, QB, Baltimore Ravens\n## OPOY: Josh Allen, QB, Buffalo Bills\n" \
           "## DPOY: Josh Allen, QB, Buffalo Bills\n## OROY: Josh Allen, QB, Buffalo Bills\n" \
           "## DROY: Josh Allen, QB, Buffalo Bills\n## COTY: Sean McDermott, Buffalo Bills"
    errors = aw.check_against_stored(text, stored, roster, coaches)
    assert any("Lamar Jackson" in e and "Josh Allen" in e for e in errors)
    prompt = aw.stored_winners_fix_prompt(errors, stored)
    assert "Josh Allen" in prompt and "Rewrite the article" in prompt
