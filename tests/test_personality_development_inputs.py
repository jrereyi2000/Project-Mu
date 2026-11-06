"""Read-only development context adapter (PERSONALITY_DEVELOPMENT_COACHING_PLAN U3): mocked store only."""
import pytest

from engines import personality_development_inputs as inputs
from personality import core, store
from personality.core import Identity


def profile(identity, traits=(), revision=2, origin="generated"):
    return {"identity": identity, "traits": list(traits), "revision": revision, "origin": origin}


@pytest.fixture
def fake_core(monkeypatch):
    profiles, calls = {}, []

    def resolve(cur, universe, role, namespace, external_id):
        calls.append(("resolve", role, namespace, external_id))
        key = (role, namespace, external_id)
        return Identity(universe, role, f"{namespace}-{external_id}") if key in profiles else None

    def read(cur, identity):
        calls.append(("read", identity.role, identity.birth_key))
        for (role, ns, ext), value in profiles.items():
            if role == identity.role and f"{ns}-{ext}" == identity.birth_key:
                if isinstance(value, Exception):
                    raise value
                return value
        return None

    def view(prof, feature):
        calls.append(("view", feature))
        if prof is None:
            return {"status": "missing", "sliders": {}, "traits": [], "persona_id": None, "revision": None}
        return {"status": "stored", "origin": prof["origin"], "traits": prof["traits"], "sliders": {},
                "revision": prof["revision"], "persona_id": f"pid-{prof['revision']}", "catalogue_version": "c"}

    def forbidden(*a, **k):
        raise AssertionError("reads must never seed or fall back")
    monkeypatch.setattr(store, "resolve_alias", resolve)
    monkeypatch.setattr(store, "read", read)
    monkeypatch.setattr(core, "feature_view", view)
    monkeypatch.setattr(store, "ensure", forbidden)
    monkeypatch.setattr(store, "bind_alias", forbidden)
    import newsroom.personas as personas
    monkeypatch.setattr(personas, "get_persona", forbidden, raising=False)
    return profiles, calls


def load(**kw):
    args = dict(universe_id="u", player_id=1, earned_season=2034, as_of_week=8,
                player_alias=("save-pgid:u", "101"), staff_aliases=[("db-coach", "9")], mentor_alias=None,
                source_event_ids=["e2", "e1"])
    args.update(kw)
    return inputs.load_development_context(None, **args)


def test_exact_feature_scopes(fake_core):
    profiles, calls = fake_core
    profiles[("player", "save-pgid:u", "101")] = profile(None, ["FILM_ROOM_RAT"])
    profiles[("coach", "db-coach", "9")] = profile(None, ["TEACHER"], origin="real")
    profiles[("player", "save-pgid:u", "55")] = profile(None, ["MENTOR"])
    ctx = load(mentor_alias=("save-pgid:u", "55"))
    assert [c for c in calls if c[0] == "view"] == [("view", "development"), ("view", "coaching"), ("view", "development")]
    assert ctx["player_view"]["traits"] == ["FILM_ROOM_RAT"]
    assert ctx["coach_views"]["db-coach:9"]["origin"] == "real"
    assert ctx["mentor_view"]["traits"] == ["MENTOR"]
    assert ctx["source_event_ids"] == ["e1", "e2"]


def test_absence_is_neutral_with_reasons(fake_core):
    ctx = load()
    assert ctx["player_view"]["status"] == "missing"
    assert "PLAYER_PROFILE_MISSING" in ctx["reasons"] and "NO_VERIFIED_EXPOSURE_INTERVALS" in ctx["reasons"]


def test_storage_failure_propagates(fake_core):
    profiles, _ = fake_core
    profiles[("player", "save-pgid:u", "101")] = RuntimeError("relation persona_core_profiles does not exist")
    with pytest.raises(RuntimeError, match="does not exist"):
        load()


def test_cutoff_and_trade_split_intervals(fake_core):
    profiles, _ = fake_core
    profiles[("coach", "db-coach", "9")] = profile(None, ["TEACHER"])
    ctx = load(staff_aliases=[("db-coach", "9"), ("db-coach", "4")], intervals=[
        {"start_week": 1, "end_week": 4, "coach_ids": ["db-coach:4"]},
        {"start_week": 5, "end_week": 18, "coach_ids": ["db-coach:9"]},
        {"start_week": 10, "end_week": 12, "coach_ids": ["db-coach:9"]}])
    assert ctx["intervals"] == [
        {"start_week": 1, "end_week": 4, "coach_ids": ["db-coach:4"], "mentor_id": None},
        {"start_week": 5, "end_week": 8, "coach_ids": ["db-coach:9"], "mentor_id": None}]
    exposure = inputs.exposure_by_coach(ctx, [1, 2, 5, 6])
    assert exposure == {"db-coach:4": .5, "db-coach:9": .5}


def test_unverified_staff_or_mentor_in_interval_refused(fake_core):
    with pytest.raises(inputs.DevelopmentContextError):
        load(intervals=[{"start_week": 1, "end_week": 4, "coach_ids": ["db-coach:77"]}])
    with pytest.raises(inputs.DevelopmentContextError):
        load(intervals=[{"start_week": 1, "end_week": 4, "coach_ids": [], "mentor_id": "x"}])
    with pytest.raises(inputs.DevelopmentContextError):
        load(intervals=[{"start_week": 5, "end_week": 4, "coach_ids": []}])


def test_bad_aliases_refused(fake_core):
    with pytest.raises(inputs.DevelopmentContextError):
        load(player_alias="101")
    with pytest.raises(inputs.DevelopmentContextError):
        load(staff_aliases=[("db-coach",)])
    with pytest.raises(inputs.DevelopmentContextError):
        load(player_id="1")


def test_context_hash_changes_with_revision_and_is_stable(fake_core):
    profiles, _ = fake_core
    profiles[("player", "save-pgid:u", "101")] = profile(None, ["FILM_ROOM_RAT"], revision=2)
    a, b = load(), load()
    assert a["context_hash"] == b["context_hash"]
    profiles[("player", "save-pgid:u", "101")] = profile(None, ["FILM_ROOM_RAT"], revision=3)
    assert load()["context_hash"] != a["context_hash"]


def test_real_feature_view_reads_reviewed_real_profile_in_full():
    assert core.FEATURE_VIEW_VERSION == "views-v2"
    assert core.feature_view(None, "coaching")["status"] == "missing"
