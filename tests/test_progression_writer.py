"""Progression writer privacy and article-time boundaries (no database needed)."""
import json
from unittest.mock import MagicMock
from newsroom import db_tools as dt


def setup_tool(monkeypatch, rows=(), exists=True):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = ("player_progression_history",) if exists else (None,)
    cur.fetchall.return_value = rows
    monkeypatch.setattr(dt.psycopg, "connect", lambda *a, **kw: conn)
    monkeypatch.setattr(dt, "resolve_season", lambda season: season or 2037)
    monkeypatch.setattr(dt, "context_season", lambda: 2037)
    monkeypatch.setattr(dt, "_resolve_player_id", lambda cur, identifier: 17)
    return cur


def test_progression_tool_exposes_only_recorded_public_fields(monkeypatch):
    cur = setup_tool(monkeypatch, [(2037, 80, 82, 2, "PLAYING_TIME", "Star", "Superstar")])
    result = json.loads(dt.get_player_progression.invoke({"player_identifier": "17", "season": 2040}))
    assert result["changes"][0] == dict(season=2037, old_ovr=80, new_ovr=82, delta_ovr=2,
                                        cause="PLAYING_TIME", dev_before="Star", dev_after="Superstar")
    sql, params = cur.execute.call_args.args
    assert params == (17, 2037)
    assert "checkpoint = 'OFFSEASON'" in sql
    for hidden in ("ceiling", "durability", "ratings_diff", "persona_term"):
        assert hidden not in sql


def test_progression_tool_missing_table_is_unknown(monkeypatch):
    cur = setup_tool(monkeypatch, exists=False)
    assert "Do not infer" in dt.get_player_progression.invoke({"player_identifier": "17"})
    assert cur.execute.call_count == 1


def test_progression_tool_database_failure_cannot_invent_causes(monkeypatch):
    setup_tool(monkeypatch)
    def fail(*args):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(dt.psycopg, "connect", fail)
    assert "Do not infer" in dt.get_player_progression.invoke({"player_identifier": "17"})
