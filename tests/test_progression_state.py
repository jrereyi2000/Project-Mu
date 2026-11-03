"""Pure accounting and isolated scratch-schema receipt/CAS tests."""
import os
from pathlib import Path
import uuid

import psycopg
import pytest

from engines import progression_state as ps


BASE = {"Awareness": 70, "Speed": 88}
BAND = {"technical": 85, "mental": 90}


def test_event_identity_stable_and_kind_earned_year_scoped():
    a = ps.event_id(1, 2026, "MENTAL", 4, "v1")
    assert a == ps.event_id(1, 2026, "MENTAL", "4", "v1")
    assert len({a, ps.event_id(1, 2027, "MENTAL", 4, "v1"),
                ps.event_id(1, 2026, "TECHNICAL", 4, "v1"),
                ps.event_id(1, 2026, "MENTAL", 4, "v2")}) == 4


def test_fractional_accounting_and_signed_remainder():
    whole, carry = ps.quantize_entitlement({"Awareness": 2.8, "Speed": -1.4}, {"Awareness": 1})
    assert whole == {"Awareness": 1, "Speed": -1}
    assert carry == {"Awareness": .8, "Speed": -.4}
    assert ps.annual_remainder({"Awareness": 3.1}, {"Awareness": 2.8}) == {"Awareness": .3}
    assert ps.annual_remainder({"Awareness": 2}, {"Awareness": 2}) == {"Awareness": 0}


@pytest.mark.parametrize("values", [{"Awareness": float("nan")}, {"Speed": float("inf")}, {"Speed": True}])
def test_nonfinite_or_boolean_entitlement_refused(values):
    with pytest.raises(ps.ProgressionStateError):
        ps.annual_remainder(values, {})


def test_overlay_does_not_contaminate_permanent_base():
    base = dict(BASE)
    assert ps.effective_ratings(base, {"Awareness": 40, "Speed": -99}) == {"Awareness": 99, "Speed": 0}
    assert base == BASE
    with pytest.raises(ps.ProgressionStateError):
        ps.effective_ratings(base, {"NotARating": 1})


@pytest.fixture
def scratch():
    dsn = os.environ.get("MU_PROGRESSION_STATE_TEST_DSN")
    if not dsn:
        pytest.skip("explicit scratch DSN not supplied")
    database = psycopg.conninfo.conninfo_to_dict(dsn).get("dbname", "")
    if "test" not in database or database == "madden_2036":
        pytest.fail("progression integration tests require explicitly named test database")
    schema = "test_progression_state_" + uuid.uuid4().hex
    conn = psycopg.connect(dsn)
    cur = conn.cursor()
    cur.execute(f"CREATE SCHEMA {schema}")
    cur.execute(f"SET search_path TO {schema},public")
    cur.execute("CREATE TABLE players (id INT PRIMARY KEY)")
    cur.execute("INSERT INTO players VALUES (1),(2),(3)")
    sql = (Path(__file__).parents[1] / "db/migrations/2027_01_15_progression_state.sql").read_text()
    sql = sql.replace("BEGIN;", "").replace("COMMIT;", "")
    cur.execute(sql)
    cur.execute(sql)  # migration is idempotent
    conn.commit()
    try:
        yield conn, cur, schema, dsn
    finally:
        conn.rollback()
        conn.autocommit = True
        cur.execute("SET search_path TO public")
        cur.execute(f"DROP SCHEMA {schema} CASCADE")
        conn.close()


def bootstrap(cur, pid=1):
    return ps.bootstrap_state(cur, pid, 2026, BASE, BAND, "initial", confidence=50, form_neutral=True)


def prepare(cur, pid=1, ordinal="4", source="week4", revision=0, purpose="mental", evidence=("g1", "g2")):
    return ps.prepare_event(cur, player_id=pid, earned_season=2026, application_season=2026,
                            kind="CHECKPOINT", ordinal=ordinal, rules_version="v1", source_fingerprint=source,
                            base_revision=revision, candidate_base={**BASE, "Awareness": 71},
                            candidate_band=BAND, overlay={"Awareness": 2}, evidence_ids=evidence,
                            evidence_purpose=purpose, annual_deltas={"Awareness": 1},
                            metadata={"reviewed_game_ids": ["g1", "g2"]})


def ack(cur, events, **kw):
    return ps.acknowledge_batch(cur, events, source_fingerprint="week4", output_fingerprint="written",
                                edits_hash="edits", verified=True, **kw)


@pytest.mark.integration
def test_bootstrap_requires_neutral_layers_and_is_explicit(scratch):
    _, cur, *_ = scratch
    with pytest.raises(ps.ProgressionStateError):
        ps.bootstrap_state(cur, 1, 2026, BASE, BAND, "initial")
    with pytest.raises(ps.ProgressionStateError):
        ps.bootstrap_state(cur, 1, 2026, BASE, BAND, "initial", confidence=99, form_neutral=True)
    assert bootstrap(cur)["revision"] == 0
    assert bootstrap(cur)["base_ratings"] == BASE
    with pytest.raises(ps.ProgressionStateError):
        ps.bootstrap_state(cur, 1, 2026, {**BASE, "Speed": 90}, BAND, "initial", form_neutral=True)


@pytest.mark.integration
def test_prepare_does_not_spend_ack_requires_verified_source_and_hash(scratch):
    _, cur, *_ = scratch
    bootstrap(cur)
    event = prepare(cur)
    assert ps.load_state(cur, 1)["base_ratings"] == BASE
    assert ps.load_annual_applied(cur, 1, 2026) == {}
    with pytest.raises(ps.ProgressionStateError):
        ps.acknowledge_batch(cur, [event["event_id"]], source_fingerprint="week4", output_fingerprint="o",
                              edits_hash="e")
    with pytest.raises(ps.ProgressionStateError):
        ps.acknowledge_batch(cur, [event["event_id"]], source_fingerprint="wrong", output_fingerprint="o",
                              edits_hash="e", verified=True)
    assert ack(cur, [event["event_id"]])[0]["status"] == "APPLIED"
    assert ack(cur, [event["event_id"]])[0]["status"] == "APPLIED"
    assert ps.load_state(cur, 1)["revision"] == 1
    assert ps.load_annual_applied(cur, 1, 2026) == {"Awareness": 1}
    with pytest.raises(ps.ProgressionStateError):
        ps.acknowledge_batch(cur, [event["event_id"]], source_fingerprint="week4", output_fingerprint="written",
                              edits_hash="wrong", verified=True)


@pytest.mark.integration
def test_changed_proposal_and_stale_revision_refused(scratch):
    _, cur, *_ = scratch
    bootstrap(cur)
    event = prepare(cur)
    assert prepare(cur)["event_id"] == event["event_id"]
    with pytest.raises(ps.ProgressionStateError):
        prepare(cur, source="changed")
    ack(cur, [event["event_id"]])
    with pytest.raises(ps.ProgressionStateError):
        prepare(cur, ordinal="8")
    # Advance Week changes source fingerprint; revision anchors the unchanged Mu base.
    next_event = prepare(cur, ordinal="8", source="week8", revision=1, evidence=("g3",))
    assert next_event["source_fingerprint"] == "week8"


@pytest.mark.integration
def test_batch_stale_player_rolls_back_entire_ack(scratch):
    _, cur, *_ = scratch
    bootstrap(cur, 1)
    bootstrap(cur, 2)
    one, two = prepare(cur, 1), prepare(cur, 2)
    rival = prepare(cur, 2, ordinal="other", evidence=("different",))
    ack(cur, [rival["event_id"]])
    with pytest.raises(ps.ProgressionStateError):
        ack(cur, [one["event_id"], two["event_id"]])
    assert ps.load_state(cur, 1)["revision"] == 0
    assert ps.load_annual_applied(cur, 1, 2026) == {}
    assert ps.load_events(cur, 1, 2026, "PREPARED")[0]["status"] == "PREPARED"


@pytest.mark.integration
def test_consumption_scoped_by_purpose_and_application_year_separate(scratch):
    _, cur, *_ = scratch
    bootstrap(cur)
    event = prepare(cur)
    ack(cur, [event["event_id"]])
    assert ps.load_consumed_evidence(cur, 1, 2026, "mental") == {"g1", "g2"}
    with pytest.raises(ps.ProgressionStateError):
        prepare(cur, ordinal="8", revision=1)
    technical = prepare(cur, ordinal="tech", revision=1, purpose="technical")
    ack(cur, [technical["event_id"]])
    annual = ps.prepare_event(cur, player_id=1, earned_season=2026, application_season=2027,
                             kind="OFFSEASON", ordinal="final", rules_version="v1", source_fingerprint="week4",
                             base_revision=2, candidate_base={**BASE, "Awareness": 73}, candidate_band=BAND,
                             annual_deltas={"Awareness": 1}, evidence_purpose="offseason")
    ack(cur, [annual["event_id"]])
    assert ps.load_annual_applied(cur, 1, 2026) == {"Awareness": 3}
    assert ps.load_annual_applied(cur, 1, 2027) == {}
    assert ps.load_state(cur, 1)["application_season"] == 2027
    assert ps.load_bases(cur, 2026) == {}
    assert ps.load_bases(cur, 2027)[1]["Awareness"] == 73
    assert ps.load_applied_overlays(cur, 2027) == {1: {}}


@pytest.mark.integration
def test_helpers_do_not_commit_even_when_connection_was_idle(scratch):
    conn, cur, schema, dsn = scratch
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    bootstrap(cur)
    event = prepare(cur)
    ack(cur, [event["event_id"]])
    with psycopg.connect(dsn) as observer:
        with observer.cursor() as other:
            other.execute(f"SELECT count(*) FROM {schema}.progression_player_state")
            assert other.fetchone()[0] == 0
    conn.rollback()
    assert ps.load_state(cur, 1) is None


@pytest.mark.integration
def test_sql_failure_mid_batch_rolls_back_earlier_player(scratch):
    _, cur, *_ = scratch
    bootstrap(cur, 1)
    bootstrap(cur, 2)
    one, two = prepare(cur, 1), prepare(cur, 2)
    failing_pid = sorted((one, two), key=lambda e: e["event_id"])[1]["player_id"]
    cur.execute(f"""
        CREATE FUNCTION reject_second_update() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.player_id = {failing_pid} THEN
                RAISE EXCEPTION 'injected second player failure';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER reject_second BEFORE UPDATE ON progression_player_state
        FOR EACH ROW EXECUTE FUNCTION reject_second_update();
    """)
    with pytest.raises(psycopg.Error, match="injected second player failure"):
        ack(cur, [one["event_id"], two["event_id"]])
    assert ps.load_state(cur, 1)["revision"] == ps.load_state(cur, 2)["revision"] == 0
    assert ps.load_annual_applied(cur, 1, 2026) == ps.load_annual_applied(cur, 2, 2026) == {}
    assert ps.load_events(cur, 1, 2026, "PREPARED")
    assert ps.load_events(cur, 2, 2026, "PREPARED")
