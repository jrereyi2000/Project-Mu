"""Inbound scan (FRONT_OFFICE_INBOX_PLAN.md §2.10; U1). Fake kinds, offline SQLite fake."""
import pytest

from engines.inbox import Conflict, InboxError, NotUserTeam, actors, registry, scan, store
from engines.inbox.registry import Trigger
from tests.test_inbox_store import InboxDB


class Kind:
    COUNTERPART_TYPE = "player"

    def __init__(self, name, roles=("GM", "COACH"), fail=False):
        self.KIND = name
        if roles is not None:
            self.ACTOR_ROLES = roles
        self.fail, self.scanned, self.reviews = fail, [], []

    def scan(self, cur, season, week, team):
        self.scanned.append(team["actor"].role)
        if self.fail:
            cur.execute("INSERT INTO inbox_threads (team_id, kind, actor_role, actor_id, counterpart_type, "
                        "counterpart_id, counterpart_name, subject, started_by, source_ref, season) VALUES "
                        "(1, 'BAD', 'GM', '10', 'player', '1', 'x', 'x', 'SYSTEM', 'partial', 2026)")
            raise RuntimeError("kind exploded")
        a = team["actor"]
        return [Trigger(self.KIND, team["team_id"], "player", "100", "Bryce Young", f"{self.KIND} talk",
                        f"{self.KIND}:{a.role}:{week}", {"player": "Bryce Young"}, "OPEN")]

    def options(self, cur, thread, last):
        return [{"id": "TALK", "label": "Talk"}]

    def template(self, intent, facts):
        return f"{facts['player']} wants a word."

    def start(self, cur, team, body):
        return Trigger(self.KIND, team["team_id"], "player", "101", "Xavier Legette", body.get("topic", "Talk"),
                       f"jr:{body.get('topic')}", {"player": "Xavier Legette"}, "OPEN")


class Reviewing(Kind):
    def review_promises(self, cur, season, week, team):
        self.reviews.append((season, week, team["abbr"]))
        return 2


@pytest.fixture
def db(monkeypatch):
    d = InboxDB()
    monkeypatch.setattr(actors, "user_teams", lambda: [t.upper() for t in d.user_teams])
    return d


@pytest.fixture
def cur(db):
    return db.cursor()


@pytest.fixture
def kinds(monkeypatch):
    found = {}
    monkeypatch.setattr(registry, "kinds", lambda *a, **k: dict(found))
    return found


def test_no_actors_no_threads(cur, kinds):
    kinds["ROLE_TALK"] = Kind("ROLE_TALK")
    out = scan.scan(cur, 2026, 5)
    assert out["created"] == 0 and kinds["ROLE_TALK"].scanned == []
    assert store.list_threads(cur, 1) == []


def test_triggers_once_per_actor_role(cur, kinds):
    kinds["REPORTER"] = Kind("REPORTER", roles=("GM", "COACH"))
    kinds["CONTRACT"] = Kind("CONTRACT", roles=("GM",))
    kinds["PRESS"] = Kind("PRESS", roles=None)
    actors.take_control(cur, "CAR", "GM", "10")
    actors.take_control(cur, "CAR", "COACH", "41")
    out = scan.scan(cur, 2026, 5, llm=None)
    assert kinds["REPORTER"].scanned == ["GM", "COACH"]
    assert kinds["CONTRACT"].scanned == ["GM"]
    assert kinds["PRESS"].scanned == ["GM"]                   # no ACTOR_ROLES: once per team, first actor
    assert out["created"] == 4 and out["by_kind"] == {"CONTRACT": 1, "PRESS": 1, "REPORTER": 2}
    threads = store.list_threads(cur, 1, kind="REPORTER")
    assert sorted(t["actor_role"] for t in threads) == ["COACH", "GM"]
    t = threads[0]
    assert t["status"] == "WAITING_JR" and t["unread"] and t["started_by"] == "COUNTERPART"
    msg = store.messages(cur, t["id"])[0]
    assert msg["body"] == "Bryce Young wants a word." and msg["body_source"] == "TEMPLATE"
    assert msg["options"] == [{"id": "TALK", "label": "Talk"}] and msg["facts"] == {"player": "Bryce Young"}


def test_reruns_create_nothing(cur, kinds):
    kinds["CONTRACT"] = Kind("CONTRACT", roles=("GM",))
    actors.take_control(cur, "CAR", "GM", "10")
    assert scan.scan(cur, 2026, 5)["created"] == 1
    assert scan.scan(cur, 2026, 5)["created"] == 0
    assert len(store.list_threads(cur, 1)) == 1


def test_only_registry_user_teams(db, cur, kinds):
    kinds["CONTRACT"] = Kind("CONTRACT", roles=("GM",))
    actors.take_control(cur, "CAR", "GM", "10")
    assert scan.scan(cur, 2026, 5, teams=["ATL"])["created"] == 0
    db.user_teams = []
    assert scan.scan(cur, 2026, 5)["created"] == 0


def test_promise_reviews_run_once_per_team(cur, kinds):
    kinds["ROLE_TALK"] = Reviewing("ROLE_TALK", roles=("COACH",))
    kinds["REPORTER"] = Reviewing("REPORTER", roles=("GM",))     # serves no controlled actor -> no review
    actors.take_control(cur, "CAR", "GM", "10")
    actors.take_control(cur, "CAR", "COACH", "41")
    out = scan.scan(cur, 2026, 9)
    assert kinds["ROLE_TALK"].reviews == [(2026, 9, "CAR")] and out["reviewed"] == 2 + 2
    actors.release_control(cur, "CAR", "GM", "10")
    kinds["REPORTER"].reviews.clear()
    scan.scan(cur, 2026, 10)
    assert kinds["REPORTER"].reviews == []
    kinds["ROLE_TALK"].reviews.clear()
    scan.scan(cur, 2026, 10, dry_run=True)
    scan.scan(cur, 2026, 10, review=False)
    assert kinds["ROLE_TALK"].reviews == []


def test_failing_kind_is_skipped_and_rolled_back(cur, kinds):
    kinds["BAD"] = Kind("BAD", fail=True)
    kinds["CONTRACT"] = Kind("CONTRACT", roles=("GM",))
    actors.take_control(cur, "CAR", "GM", "10")
    out = scan.scan(cur, 2026, 5, log_fn=None)
    assert out["created"] == 1 and len(out["errors"]) == 1 and "kind exploded" in out["errors"][0]
    assert [t["kind"] for t in store.list_threads(cur, 1)] == ["CONTRACT"]


def test_dry_run_stores_nothing(cur, kinds):
    kinds["CONTRACT"] = Kind("CONTRACT", roles=("GM",))
    actors.take_control(cur, "CAR", "GM", "10")
    out = scan.scan(cur, 2026, 5, dry_run=True)
    assert out["triggers"] == 1 and out["created"] == 0 and store.list_threads(cur, 1) == []


def test_expiry_and_flush_run(cur, kinds, monkeypatch):
    kinds["CONTRACT"] = Kind("CONTRACT", roles=("GM",))
    actors.take_control(cur, "CAR", "GM", "10")
    scan.scan(cur, 2026, 5)
    out = scan.scan(cur, 2026, 9)
    assert out["expired"] == 1 and out["created"] == 1          # week 5's thread expires; week 9's opens
    assert out["flush"]["status"] in ("relationship_store_missing", "relationship_tables_missing")


def test_scan_on_open_runs_once_per_week(cur, kinds, monkeypatch):
    monkeypatch.setattr(scan, "_OPENED", set())
    kinds["ROLE_TALK"] = Reviewing("ROLE_TALK", roles=("GM",))
    actors.take_control(cur, "CAR", "GM", "10")
    first = scan.scan_on_open(cur, "car", 2026, 5)
    assert first["created"] == 1 and first["flush"] is None
    assert kinds["ROLE_TALK"].reviews == []                     # reviews run in the weekly scan only
    assert scan.scan_on_open(cur, "CAR", 2026, 5) is None
    assert scan.scan_on_open(cur, "CAR", 2026, 6) is not None


def test_start_thread(cur, kinds):
    kinds["ROLE_TALK"] = Kind("ROLE_TALK", roles=("COACH",))
    with pytest.raises(NotUserTeam):
        scan.start_thread(cur, "ROLE_TALK", "CAR", "COACH", {"topic": "snaps"}, season=2026, week=5)
    actors.take_control(cur, "CAR", "COACH", "41")
    actors.take_control(cur, "CAR", "GM", "10")
    with pytest.raises(InboxError) as e:
        scan.start_thread(cur, "NOPE", "CAR", "COACH", {}, season=2026, week=5)
    assert e.value.status == 400
    with pytest.raises(InboxError):
        scan.start_thread(cur, "ROLE_TALK", "CAR", "GM", {"topic": "snaps"}, season=2026, week=5)
    tid = scan.start_thread(cur, "ROLE_TALK", "CAR", "COACH", {"topic": "snaps"}, season=2026, week=5)
    t = store.get_thread(cur, tid)
    assert t["started_by"] == "JR" and t["actor_role"] == "COACH" and t["actor_id"] == "41"
    with pytest.raises(Conflict):
        scan.start_thread(cur, "ROLE_TALK", "CAR", "COACH", {"topic": "snaps"}, season=2026, week=5)


def test_no_inbox_tables_is_a_noop(monkeypatch, kinds):
    d = InboxDB(inbox=False)
    monkeypatch.setattr(actors, "user_teams", lambda: ["CAR"])
    assert scan.scan(d.cursor(), 2026, 5)["skipped"] == "inbox tables missing"


def test_step_inbox_without_user_teams_or_db_never_raises(monkeypatch):
    logs = []
    monkeypatch.setattr(actors, "user_teams", lambda: [])
    assert scan.step_inbox(2026, log_fn=logs.append) is None and "no user team" in logs[-1]
    monkeypatch.setattr(actors, "user_teams", lambda: ["CAR"])
    monkeypatch.setenv("MU_INBOX_LLM", "0")
    import psycopg

    def refuse(*a, **k):
        raise psycopg.OperationalError("no database here")
    monkeypatch.setattr(psycopg, "connect", refuse)
    assert scan.step_inbox(2026, log_fn=logs.append, dsn="dbname=mu_scratch_none") is None
    assert "[WARN] Inbox step skipped" in logs[-1]
