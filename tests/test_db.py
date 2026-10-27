"""Unit test for DatabaseHandler UPSERT and event emission logic (tests/test_db.py)."""

from unittest.mock import MagicMock, patch
from pipeline.ocr_extractor import PlayerStat
from db.db_handler import DatabaseHandler


def test_database():
    mock_stats = [
        PlayerStat(player_name="C. Stroud", team="HOU", completions=20, attempts=30, passing_yards=250, passing_touchdowns=2),
        PlayerStat(player_name="J. Mixon", team="HOU", carries=15, rushing_yards=85, rushing_touchdowns=1),
        PlayerStat(player_name="C. Stroud", team="HOU", carries=3, rushing_yards=15, rushing_touchdowns=0),
    ]

    executed_queries = []
    mock_cur = MagicMock()

    def record_execute(sql, params=None):
        executed_queries.append((sql.strip(), params))

    mock_cur.execute.side_effect = record_execute
    mock_cur.fetchone.return_value = (2035, 1)
    mock_cur.fetchall.return_value = []

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    mock_redis = MagicMock()

    with patch("db.db_handler.redis.Redis.from_url", return_value=mock_redis), \
         patch("db.db_handler.psycopg.connect", return_value=mock_conn):
        db = DatabaseHandler(db_conn_info="dbname=madden_test user=postgres", redis_url="redis://localhost:6379/0")
        game_id = db.create_active_game()
        db.upsert_player_stats(game_id=game_id, player_stats_list=mock_stats)
        db.emit_game_completed(game_id)

    assert any("INSERT INTO games" in q[0] for q in executed_queries)
    upsert_calls = [q for q in executed_queries if "INSERT INTO player_stats" in q[0] and "ON CONFLICT" in q[0]]
    assert len(upsert_calls) == 3
    mock_redis.publish.assert_called_once()

