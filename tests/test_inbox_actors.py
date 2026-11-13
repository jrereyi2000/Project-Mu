"""Which personas JR controls (FRONT_OFFICE_INBOX_PLAN.md §2.1, D6, D8; U1). Offline SQLite fake (test_inbox_store)."""
import pytest

from engines.inbox import Conflict, NotUserTeam, actors, registry
from tests.test_inbox_store import InboxDB, cur, db  # noqa: F401  (fixtures)


def test_no_rows_means_no_actors(cur):
    assert actors.controlled(cur, "CAR") == []          # D8: JR controls no one by default
    assert actors.actor_for(cur, "CAR", "GM") is None


def test_candidates_are_the_open_gm_and_the_head_coach(cur):
    got = actors.candidates(cur, "CAR")
    assert [(c["role"], c["persona_id"], c["name"]) for c in got] == [("GM", "10", "Dan Morgan"),
                                                                      ("COACH", "41", "Dave Canales")]
    assert all(c["also"] is None for c in got)
    assert actors.candidates(cur, "ATL") == []          # not a registry user team


def test_take_and_release(cur):
    a = actors.take_control(cur, "car", "COACH", "41")
    assert (a.team_id, a.abbr, a.role, a.persona_id, a.name) == (1, "CAR", "COACH", "41", "Dave Canales")
    assert a.to_dict() == {"role": "COACH", "id": "41", "name": "Dave Canales"}
    actors.take_control(cur, "CAR", "GM", 10)
    assert [x.role for x in actors.controlled(cur, "CAR")] == ["GM", "COACH"]
    actors.release_control(cur, "CAR", "COACH", "41")
    assert [x.role for x in actors.controlled(cur, "CAR")] == ["GM"]
    actors.take_control(cur, "CAR", "COACH", "41")       # retaking reactivates the row
    assert actors.actor_for(cur, "CAR", "COACH").name == "Dave Canales"


def test_team_outside_registry_is_403(cur):
    with pytest.raises(NotUserTeam) as e:
        actors.take_control(cur, "ATL", "GM", "20")
    assert e.value.status == 403


def test_persona_not_on_team_is_409(cur):
    with pytest.raises(Conflict) as e:
        actors.take_control(cur, "CAR", "GM", "20")       # Atlanta's GM
    assert e.value.status == 409
    with pytest.raises(Conflict):
        actors.take_control(cur, "CAR", "GM", "9")        # Carolina's former GM (closed tenure)
    with pytest.raises(Conflict):
        actors.take_control(cur, "CAR", "PLAYER", "200")  # an Atlanta player


def test_rows_of_a_team_that_left_the_registry_are_ignored(db, cur):
    actors.take_control(cur, "CAR", "GM", "10")
    db.user_teams = []
    assert actors.controlled(cur, "CAR") == []


def test_coach_gm_is_one_candidate_and_creates_both_rows(db, cur):
    db.make_coach_gm()
    got = actors.candidates(cur, "CAR")
    assert len(got) == 1
    assert got[0]["role"] == "GM" and got[0]["also"] == {"role": "COACH", "persona_id": "41"}
    actors.take_control(cur, "CAR", "GM", "10")
    assert sorted((a.role, a.persona_id) for a in actors.controlled(cur, "CAR")) == [("COACH", "41"), ("GM", "10")]
    actors.release_control(cur, "CAR", "COACH", "41")
    assert actors.controlled(cur, "CAR") == []


def test_player_rows_are_accepted_but_served_by_no_kind(cur, monkeypatch):
    a = actors.take_control(cur, "CAR", "PLAYER", "100")
    assert a.name == "Bryce Young"
    assert [x.role for x in actors.controlled(cur, "CAR")] == ["PLAYER"]
    assert all(c["role"] != "PLAYER" for c in actors.candidates(cur, "CAR"))

    class Contract:
        KIND, COUNTERPART_TYPE, ACTOR_ROLES = "CONTRACT", "player", ("GM",)

    assert registry.actors_to_scan(Contract(), actors.controlled(cur, "CAR")) == []


def test_bad_role_is_400(cur):
    with pytest.raises(Exception) as e:
        actors.take_control(cur, "CAR", "OWNER", "1")
    assert getattr(e.value, "status", None) == 400


def test_user_teams_reads_the_registry(monkeypatch):
    import db.franchises as fr
    monkeypatch.setattr(fr, "active_entry", lambda *a, **k: ("x", {"user_teams": ["car", "Atl"]}))
    assert actors.user_teams() == ["CAR", "ATL"]
    monkeypatch.setattr(fr, "active_entry", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no registry")))
    assert actors.user_teams() == []


def test_an_empty_registry_refuses_control_and_lists_nobody(db, cur):
    """P8-08: `user_teams: []` means nobody is JR's, and the refusal says so instead of naming the team."""
    actors.take_control(cur, "CAR", "GM", "10")                 # while CAR is the user team
    db.user_teams = []
    with pytest.raises(NotUserTeam) as e:
        actors.take_control(cur, "CAR", "GM", "10")
    assert (e.value.status, e.value.message) == (403, "no user team is registered for this franchise")
    with pytest.raises(NotUserTeam) as e:
        actors.release_control(cur, "CAR", "GM", "10")
    assert e.value.message == "no user team is registered for this franchise"
    assert actors.candidates(cur, "CAR") == []                  # nothing to take control of
    assert actors.controlled(cur, "CAR") == []                   # the stored row is ignored (D6)
    assert actors.is_user_team("CAR") is False
    assert actors.user_team_refusal("CAR") == "no user team is registered for this franchise"


def test_no_tables_no_actors(monkeypatch):
    d = InboxDB(inbox=False)
    monkeypatch.setattr(actors, "user_teams", lambda: ["CAR"])
    assert actors.controlled(d.cursor(), "CAR") == []
