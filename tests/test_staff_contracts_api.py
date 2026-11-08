"""server/front_office_api.py: the staff-contract block and offer endpoint (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN
§2.5, U3). Pure shaping/validation tests need no database; the offer endpoint tests fake the connection, never the
live `madden` database."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from server import front_office_api as fo


def contract(**kw):
    c = {"person_id": 42, "person_kind": "HC", "annual_salary": 19_000_000, "total_years": 5,
         "remaining_years": 3, "signed_season": 2037, "full_control": True}
    c.update(kw)
    return c


# ------------------------------------------------------------------ shape ----
def test_shape_staff_contract():
    shaped = fo.shape_staff_contract(contract())
    assert set(shaped) == {"person_id", "person_kind", "annual_salary", "total_years", "remaining_years",
                           "signed_season", "full_control"}
    assert shaped == {"person_id": 42, "person_kind": "HC", "annual_salary": 19_000_000, "total_years": 5,
                      "remaining_years": 3, "signed_season": 2037, "full_control": True}
    assert isinstance(shaped["annual_salary"], int)
    assert shaped["full_control"] is True


class _FetchCursor:
    """A tiny cursor for fetch_staff_contracts: records the SQL and returns the rows given."""

    def __init__(self, rows):
        self._rows = rows
        self.sql = ""

    def execute(self, sql, *args):
        self.sql = sql

    def fetchall(self):
        return self._rows


def test_fetch_staff_contracts_shapes_each_row():
    cur = _FetchCursor([(9, "GM", 8_000_000, 4, 2, 2037, False)])
    assert fo.fetch_staff_contracts(cur, 1) == [
        {"person_id": 9, "person_kind": "GM", "annual_salary": 8_000_000, "total_years": 4,
         "remaining_years": 2, "signed_season": 2037, "full_control": False}]


def test_fetch_staff_contracts_orders_hc_gm_oc_dc_and_is_none_when_empty():
    cur = _FetchCursor([])
    assert fo.fetch_staff_contracts(cur, 1) is None
    # The block is served empty (not $0 rows) without the migration's rows; the SQL keeps the role order.
    assert "ORDER BY CASE person_kind WHEN 'HC' THEN 1 WHEN 'GM' THEN 2 WHEN 'OC' THEN 3 ELSE 4 END" in cur.sql
    assert "remaining_years > 0" in cur.sql


# ------------------------------------------------------------------ offer validation ----
def test_validate_staff_offer_accepts_a_valid_offer():
    fo.validate_staff_offer("HC", 9_000_000, 5, True)          # full control on a head coach is fine
    fo.validate_staff_offer("DC", 2_000_000, 3, False)          # a plain coordinator offer


def test_validate_staff_offer_rejects_unknown_kind():
    with pytest.raises(HTTPException) as e:
        fo.validate_staff_offer("QB", 5_000_000, 3, False)
    assert e.value.status_code == 400


def test_validate_staff_offer_rejects_non_positive_salary_and_years():
    for kind in fo.STAFF_OFFER_KINDS:
        with pytest.raises(HTTPException) as e:
            fo.validate_staff_offer(kind, 0, 3, False)
        assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            fo.validate_staff_offer(kind, -1, 3, False)
        assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            fo.validate_staff_offer(kind, 5_000_000, 0, False)
        assert e.value.status_code == 400


def test_validate_staff_offer_rejects_full_control_outside_a_head_coach():
    for kind in ("GM", "OC", "DC"):
        with pytest.raises(HTTPException) as e:
            fo.validate_staff_offer(kind, 5_000_000, 4, True)
        assert e.value.status_code == 400
        assert "full_control" in e.value.detail


# ------------------------------------------------------------------ offer endpoint ----
class _FakeCursor:
    """Answers the offer handler's cursor calls by matching the SQL text; the INSERT echoes its own arguments so the
    returned row carries the request's values."""

    def __init__(self, active_full_control=0, last_godfather_season=None):
        self.active = active_full_control
        self.last = last_godfather_season
        self.sql = ""
        self.args = ()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *args):
        self.sql = sql
        self.args = args

    def fetchone(self):
        s = self.sql
        if "FROM teams" in s:
            return (1, "Carolina Panthers", "CAR")
        if "league_state" in s:
            return (2037,)
        if "to_regclass" in s:
            return (True,)
        if "count(*) FROM staff_contracts" in s:
            return (self.active,)
        if "max(season) FROM full_control_history" in s:
            return (self.last,)
        if "INSERT INTO staff_contracts" in s:
            a = self.args[0]
            return (a[1], a[2], a[3], a[4], a[5], a[6], a[7])
        raise AssertionError(f"unexpected SQL: {s}")


class _FakeConnection:
    def __init__(self, cur):
        self._cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cur

    def commit(self):
        pass


def _client(monkeypatch, cur):
    monkeypatch.setattr(fo.psycopg, "connect", lambda *a, **kw: _FakeConnection(cur))
    app = FastAPI()
    app.include_router(fo.router)
    return TestClient(app)


def test_staff_offer_validation_returns_400_before_any_db():
    # Validation runs first, so a bad body never reaches psycopg.connect (no DB needed).
    app = FastAPI()
    app.include_router(fo.router)
    client = TestClient(app)
    assert client.post("/api/team/car/staff/offer",
                       json={"person_kind": "QB", "person_id": 1, "annual_salary": 5_000_000,
                             "total_years": 3}).status_code == 400
    assert client.post("/api/team/car/staff/offer",
                       json={"person_kind": "GM", "person_id": 1, "annual_salary": 5_000_000,
                             "total_years": 3, "full_control": True}).status_code == 400


def test_staff_offer_inserts_an_active_contract(monkeypatch):
    client = _client(monkeypatch, _FakeCursor())
    r = client.post("/api/team/car/staff/offer",
                    json={"person_kind": "HC", "person_id": 77, "annual_salary": 9_000_000, "total_years": 5})
    assert r.status_code == 200
    data = r.json()
    assert data["team"] == "CAR"
    assert data["season"] == 2037
    assert data["contract"] == {"person_id": 77, "person_kind": "HC", "annual_salary": 9_000_000,
                                "total_years": 5, "remaining_years": 5, "signed_season": 2037, "full_control": False}


def test_staff_offer_full_control_blocked_by_the_godfather_gate(monkeypatch):
    # 2 active full-control coaches league-wide: the ceiling gate rejects the offer with 409.
    client = _client(monkeypatch, _FakeCursor(active_full_control=2))
    r = client.post("/api/team/car/staff/offer",
                    json={"person_kind": "HC", "person_id": 77, "annual_salary": 20_000_000, "total_years": 6,
                          "full_control": True})
    assert r.status_code == 409


def test_staff_offer_full_control_allowed_when_the_gate_passes(monkeypatch):
    client = _client(monkeypatch, _FakeCursor(active_full_control=0, last_godfather_season=None))
    r = client.post("/api/team/car/staff/offer",
                    json={"person_kind": "HC", "person_id": 77, "annual_salary": 20_000_000, "total_years": 6,
                          "full_control": True})
    assert r.status_code == 200
    assert r.json()["contract"]["full_control"] is True
