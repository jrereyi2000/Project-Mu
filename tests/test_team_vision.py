"""Team vision engine (FRONT_OFFICE_INBOX_PLAN.md §2.9; U6). Offline: the decision is pure over a context dict;
the table reads/writes run against the inbox tests' in-memory SQLite fake."""
import pytest

from engines import gm_direction, team_vision
from tests import test_gm_direction as tgd
from tests.test_inbox_store import InboxDB


class Cur:
    """A cursor that answers nothing (used where context is patched or unused)."""

    def execute(self, sql, params=()):
        self._last = []

    def fetchone(self):
        return None


class RowsCur(Cur):
    def __init__(self, one=None):
        self.one = one

    def fetchone(self):
        return self.one


@pytest.fixture
def db(monkeypatch):
    d = InboxDB()
    monkeypatch.setattr("engines.inbox.actors.user_teams", lambda: [t.upper() for t in d.user_teams])
    return d


# ------------------------------------------------------------------------------------------------ allowed ---

def test_allowed_applies_the_direction_overrides():
    row = {"proj_wins": 7.0}
    assert team_vision.allowed(row, 10)["direction"] == ["CONTEND", "RETOOL", "REBUILD"]
    assert team_vision.allowed({"proj_wins": 10.5}, 10)["direction"] == ["CONTEND"]
    assert team_vision.allowed({"proj_wins": 11.0}, -1)["direction"] == ["CONTEND"]
    assert team_vision.allowed(row, -1)["direction"] == ["CONTEND", "RETOOL"]
    assert team_vision.allowed(row, None)["direction"] == ["CONTEND", "RETOOL", "REBUILD"]
    view = team_vision.allowed(row, 10)
    assert view["emphasis"] == ["OFFENSE", "DEFENSE", "BALANCED"]
    assert view["playing_time"] == ["YOUTH", "VETERANS", "MERIT"]


# ------------------------------------------------------------------------------------------------ context ---

def _league(n=32):
    lg = tgd.league(n)
    return {"teams": lg, "roster": {t["team_id"]: tgd.roster(t["team_id"]) for t in lg}, "injuries": {},
            "expiring": set(), "visions": {}}


def test_context_reads_the_engine_band_needs_and_cap(monkeypatch):
    data = _league()
    monkeypatch.setattr(gm_direction, "read_league", lambda cur, season, week: data)
    ctx = team_vision.context(Cur(), 1, 2030, 6)
    assert ctx["engine_band"] in team_vision.DIRECTIONS
    assert {"D", "proj_wins"} <= set(ctx["scores_row"])
    assert isinstance(ctx["needs"], list) and isinstance(ctx["edges"], tuple)
    scores = gm_direction.direction_scores(data["teams"])
    caps = {t["team_id"]: t["cap_space"] for t in data["teams"]}
    assert ctx["engine_band"] == gm_direction.assign_directions(scores, None, caps)[1][0]


# ------------------------------------------------------------------------------------------------- decide ---

def ctx(engine_band="RETOOL", D=-0.6, hi=1.0, lo=-1.0, proj=7.0, cap=10_000_000, needs=()):
    return {"scores_row": {"D": D, "proj_wins": proj}, "edges": (hi, lo), "engine_band": engine_band,
            "engine_reason": "band", "needs": list(needs), "surplus": [], "cap_space": cap}


GM = {"type": "gm", "id": "10", "risk_appetite": 0.7}          # lean 1.2, margin 0.6
LOW_RISK = {"type": "gm", "id": "10", "risk_appetite": 0.1}    # lean 0.6, margin 0.3
COACH = {"type": "coach", "id": "41"}                          # lean 1.0, margin 0.5


def proposal(direction, emphasis="BALANCED", playing_time="MERIT"):
    return {"direction": direction, "emphasis": emphasis, "playing_time": playing_time}


def test_a_disallowed_direction_refuses_with_the_override():
    out = team_vision.decide(ctx(proj=11.0), proposal("RETOOL"), GM)
    assert out["decision"] == "REFUSE" and out["reasons"] == ["proj_wins >= 10.5"]
    out = team_vision.decide(ctx(cap=-1), proposal("REBUILD"), GM)
    assert out["decision"] == "REFUSE" and out["reasons"] == ["over the cap"]
    out = team_vision.decide(ctx(), proposal("SIDEWAYS"), GM)
    assert out["decision"] == "REFUSE"


def test_a_direction_equal_to_the_engine_band_agrees():
    out = team_vision.decide(ctx(engine_band="REBUILD", D=-0.6), proposal("REBUILD"), GM)
    assert out["decision"] == "AGREE"
    assert out["vision"] == proposal("REBUILD")


def test_within_the_margin_agrees_and_outside_counters_with_the_band():
    # D -0.6 is 0.4 from the REBUILD edge; a GM at risk 0.7 may stretch 0.6
    assert team_vision.decide(ctx(D=-0.6), proposal("REBUILD"), GM)["decision"] == "AGREE"
    # the same 0.4 is too far for a cautious GM (0.3) and a COACH (0.5), and 0.8 is too far for anyone here
    assert team_vision.decide(ctx(D=-0.6), proposal("REBUILD"), LOW_RISK)["decision"] == "COUNTER"
    assert team_vision.decide(ctx(D=-0.2), proposal("REBUILD"), COACH)["decision"] == "COUNTER"
    out = team_vision.decide(ctx(D=-0.2), proposal("REBUILD"), GM)
    assert out["decision"] == "COUNTER" and out["vision"]["direction"] == "RETOOL"
    # a coach stretches 0.5
    assert team_vision.decide(ctx(D=-0.6), proposal("REBUILD"), COACH)["decision"] == "AGREE"


def test_retool_uses_its_nearest_edge():
    # D -1.4 is 0.4 above the RETOOL lower edge; the engine's own band is REBUILD
    assert team_vision.decide(ctx(engine_band="REBUILD", D=-1.4), proposal("RETOOL"), COACH)["decision"] == "AGREE"
    out = team_vision.decide(ctx(engine_band="REBUILD", D=-2.0), proposal("RETOOL"), COACH)
    assert out["decision"] == "COUNTER" and out["vision"]["direction"] == "REBUILD"


def test_emphasis_counters_balanced_only_when_the_top_needs_are_all_other_side():
    defense = [{"group": "EDGE"}, {"group": "DT"}, {"group": "CB"}]
    out = team_vision.decide(ctx(needs=defense), proposal("RETOOL", "OFFENSE", "YOUTH"), GM)
    assert out["decision"] == "COUNTER"
    assert out["vision"] == {"direction": "RETOOL", "emphasis": "BALANCED", "playing_time": "YOUTH"}
    offense = [{"group": "QB"}, {"group": "WR"}, {"group": "OT"}]
    assert team_vision.decide(ctx(needs=offense), proposal("RETOOL", "DEFENSE"), GM)["decision"] == "COUNTER"
    mixed = [{"group": "QB"}, {"group": "EDGE"}, {"group": "WR"}]
    assert team_vision.decide(ctx(needs=mixed), proposal("RETOOL", "DEFENSE"), GM)["decision"] == "AGREE"
    assert team_vision.decide(ctx(needs=defense[:1]), proposal("RETOOL", "OFFENSE"), GM)["decision"] == "COUNTER"
    assert team_vision.decide(ctx(needs=[]), proposal("RETOOL", "OFFENSE"), GM)["decision"] == "AGREE"
    assert team_vision.decide(ctx(needs=defense), proposal("RETOOL", "BALANCED"), GM)["decision"] == "AGREE"


def test_playing_time_always_agrees():
    for pt in team_vision.PLAYING_TIMES:
        out = team_vision.decide(ctx(engine_band="RETOOL"), proposal("RETOOL", "BALANCED", pt), GM)
        assert out["decision"] == "AGREE" and out["vision"]["playing_time"] == pt


# ------------------------------------------------------------------------------------------------ respond ---

def test_respond_uses_the_counterparts_risk_appetite(monkeypatch):
    monkeypatch.setattr(team_vision, "context", lambda *a, **k: ctx(D=-0.6))
    out = team_vision.respond(Cur(), 1, 2030, proposal("REBUILD"), GM)
    assert out["decision"] == "AGREE"
    out = team_vision.respond(Cur(), 1, 2030, proposal("REBUILD"), COACH)
    assert out["decision"] == "AGREE"


def test_respond_reads_risk_appetite_from_gm_profiles_when_not_given(monkeypatch):
    monkeypatch.setattr(team_vision, "context", lambda *a, **k: ctx(D=-0.6))
    assert team_vision.respond(RowsCur((0.1,)), 1, 2030, proposal("REBUILD"), {"type": "gm", "id": "10"})[
        "decision"] == "COUNTER"
    assert team_vision.respond(RowsCur((0.9,)), 1, 2030, proposal("REBUILD"), {"type": "gm", "id": "10"})[
        "decision"] == "AGREE"


# -------------------------------------------------------------------------------------------- table I/O ---

class NoTable(Cur):
    def execute(self, sql, params=()):
        self._last = [(False,)]

    def fetchone(self):
        return self._last[0]


def test_current_is_none_without_the_table():
    assert team_vision.current(NoTable(), 1, 2026) is None


def test_current_and_upsert(db):
    cur = db.cursor()
    assert team_vision.current(cur, 1, 2026) is None
    team_vision.upsert(cur, 1, 2026, {"direction": "REBUILD", "emphasis": "DEFENSE", "playing_time": "YOUTH"}, 7)
    assert team_vision.current(cur, 1, 2026) == {"direction": "REBUILD", "emphasis": "DEFENSE",
                                                 "playing_time": "YOUTH", "thread_id": 7}
    team_vision.upsert(cur, 1, 2026, {"direction": "CONTEND", "emphasis": "OFFENSE", "playing_time": "VETERANS"},
                       None)
    got = team_vision.current(cur, 1, 2026)
    assert got["direction"] == "CONTEND" and got["emphasis"] == "OFFENSE" and got["thread_id"] is None
    assert team_vision.current(cur, 2, 2026) is None


def test_upsert_normalizes_and_accepts_no_pin():
    class Recorder(Cur):
        def execute(self, sql, params=()):
            self.sql, self.params = sql, params

    cur = Recorder()
    team_vision.upsert(cur, 1, 2026, {"direction": "rebuild", "emphasis": "defense"}, None)
    assert cur.params[:5] == (1, 2026, "REBUILD", "DEFENSE", "MERIT")
