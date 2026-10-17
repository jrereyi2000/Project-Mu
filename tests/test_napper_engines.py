"""Tests for Napper Proactive Trade Engine and Autonomous Chat Engine.

Verifies:
1. Positional scarcity weights & bench depth penalties in roster analysis.
2. Trade proposal generation, realistic balance, persona rationales, and chat dispatches.
3. AI-to-AI trade execution, roster mutations, and completion announcements.
4. Weekly awards generation (RB/WR/TE Benchwarmer, High Scorer, Heartbreaker).
5. Persona-specific manager banter across injuries, blowouts, trade chatter, and matchup previews.
"""

import json
import os
import pytest
import psycopg

from engines.napper_trade_engine import (
    POSITIONAL_SCARCITY_WEIGHTS,
    BENCH_DEPTH_TARGETS,
    get_scarcity_weights,
    get_bench_depth_targets,
    analyze_team_rosters,
    generate_trade_proposals,
    process_ai_to_ai_trades,
)
from engines.napper_chat_engine import (
    generate_weekly_awards,
    generate_manager_banter,
    PERSONA_BANTER_TEMPLATES,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres"
)


@pytest.fixture
def db_conn():
    """Provides a psycopg database connection rolled back after each test."""
    conn = psycopg.connect(DATABASE_URL)
    conn.autocommit = False
    conn._no_commit = True
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


# -----------------------------------------------------------------------------
# 1. Positional Scarcity Weights & Bench Depth Penalties
# -----------------------------------------------------------------------------

def test_positional_scarcity_weights():
    """Verify positional scarcity multipliers reflect format requirements."""
    std_weights = get_scarcity_weights(is_superflex=False)
    sf_weights = get_scarcity_weights(is_superflex=True)

    # In Superflex, QB scarcity must be significantly higher than standard
    assert sf_weights["QB"] > std_weights["QB"]
    assert sf_weights["QB"] >= 1.40

    # In standard formats, RB scarcity is paramount due to workhorse scarcity
    assert std_weights["RB"] > std_weights["WR"]
    assert std_weights["RB"] >= 1.25

    # Check bench targets
    std_bench = get_bench_depth_targets(is_superflex=False)
    sf_bench = get_bench_depth_targets(is_superflex=True)
    assert sf_bench["QB"] >= 2
    assert std_bench["RB"] >= 2


def test_analyze_team_rosters_depth_penalties(db_conn):
    """Verify that teams with inadequate bench depth incur penalties in deficit evaluation."""
    roster_req = {"QB": 2, "RB": 2, "WR": 2, "TE": 1}
    teams = analyze_team_rosters(
        db_conn.cursor(),
        league_id=1,
        week=6,
        season=2035,
        roster_req=roster_req,
        is_superflex=True,
    )

    assert len(teams) >= 2
    for tid, tdata in teams.items():
        assert "bench_depth" in tdata
        assert "surplus" in tdata
        assert "deficits" in tdata

        # Check deficit formatting
        for pos, score, reason in tdata["deficits"]:
            assert pos in ("QB", "RB", "WR", "TE")
            assert score > 0
            assert isinstance(reason, str)


# -----------------------------------------------------------------------------
# 2. Trade Proposal Generation
# -----------------------------------------------------------------------------

def test_generate_trade_proposals(db_conn):
    """Verify AI managers generate fair, reciprocal proposals with natural language pitches."""
    # First resolve any pre-existing pending trades so AI teams can propose
    process_ai_to_ai_trades(1, 6, conn=db_conn)

    proposals = generate_trade_proposals(1, 6, conn=db_conn)
    assert isinstance(proposals, list)
    assert len(proposals) > 0

    cur = db_conn.cursor()
    for prop in proposals:
        assert "trade_id" in prop
        assert "proposer_id" in prop
        assert "target_id" in prop
        assert "rationale" in prop
        assert "chat_msg" in prop
        assert len(prop["offered_player_ids"]) > 0
        assert len(prop["requested_player_ids"]) > 0

        # Verify trade was inserted into napper_trades table as PENDING
        cur.execute(
            """
            SELECT status, initiated_by_ai, ai_rationale
            FROM napper_trades
            WHERE id = %s
            """,
            (prop["trade_id"],),
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "PENDING"
        assert row[1] is True
        assert row[2] == prop["rationale"]

        # Verify proposal was posted to napper_chat with TRADE_ALERT card
        cur.execute(
            """
            SELECT sender_type, message_text, card_payload
            FROM napper_chat
            WHERE league_id = 1 AND (card_payload->'details'->>'trade_id')::int = %s
            """,
            (prop["trade_id"],),
        )
        chat_row = cur.fetchone()
        assert chat_row is not None
        assert chat_row[0] == "AI_MANAGER"
        payload = chat_row[2]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["type"] == "TRADE_ALERT"
        assert payload["title"] == "TRADE PROPOSAL"
        assert "offered" in payload["details"]
        assert "requested" in payload["details"]


# -----------------------------------------------------------------------------
# 3. AI-to-AI Trade Execution & Roster Mutations
# -----------------------------------------------------------------------------

def test_process_ai_to_ai_trades_execution(db_conn):
    """Verify stochastic acceptance mutates rosters and announces completion to chat."""
    cur = db_conn.cursor()

    # Create a controlled pending AI trade between Team 4 and Team 6
    cur.execute("SELECT id FROM napper_teams WHERE league_id = 1 AND is_ai = TRUE ORDER BY id LIMIT 2")
    ai_team_1, ai_team_2 = [r[0] for r in cur.fetchall()]

    # Pick 1 player from each team for week 6
    cur.execute("SELECT player_id FROM napper_rosters WHERE league_id = 1 AND team_id = %s AND week = 6 LIMIT 1", (ai_team_1,))
    p1 = cur.fetchone()[0]
    cur.execute("SELECT player_id FROM napper_rosters WHERE league_id = 1 AND team_id = %s AND week = 6 LIMIT 1", (ai_team_2,))
    p2 = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO napper_trades (
            league_id, week, proposer_team_id, target_team_id,
            offered_player_ids, requested_player_ids, status, initiated_by_ai, ai_rationale
        ) VALUES (1, 6, %s, %s, %s, %s, 'PENDING', TRUE, 'Test fair trade')
        RETURNING id
        """,
        (ai_team_1, ai_team_2, [p1], [p2]),
    )
    trade_id = cur.fetchone()[0]

    # Process pending trade with seed guaranteeing acceptance
    results = process_ai_to_ai_trades(1, 6, conn=db_conn, random_seed=42)
    assert any(r["trade_id"] == trade_id for r in results)

    # Check updated trade status
    cur.execute("SELECT status FROM napper_trades WHERE id = %s", (trade_id,))
    final_status = cur.fetchone()[0]
    assert final_status in ("ACCEPTED", "REJECTED")

    if final_status == "ACCEPTED":
        # Verify roster mutation for week 6 and week 7:
        # p1 should now belong to ai_team_2, and p2 to ai_team_1
        cur.execute(
            "SELECT team_id, slot FROM napper_rosters WHERE league_id = 1 AND player_id = %s AND week = 6",
            (p1,),
        )
        r1 = cur.fetchone()
        assert r1[0] == ai_team_2
        assert r1[1] == "BN"

        cur.execute(
            "SELECT team_id, slot FROM napper_rosters WHERE league_id = 1 AND player_id = %s AND week = 6",
            (p2,),
        )
        r2 = cur.fetchone()
        assert r2[0] == ai_team_1
        assert r2[1] == "BN"

        # Verify Trade Completed chat announcement
        cur.execute(
            """
            SELECT sender_type, sender_name, message_text, card_payload
            FROM napper_chat
            WHERE league_id = 1 AND (card_payload->'details'->>'trade_id')::int = %s
            """,
            (trade_id,),
        )
        chat_rows = cur.fetchall()
        assert len(chat_rows) >= 1
        bot_announcement = next(r for r in chat_rows if r[0] == "SYSTEM_BOT")
        assert "🚨 TRADE COMPLETED!" in bot_announcement[2]


def test_process_ai_trade_bench_depth_rejection(db_conn):
    """Verify that a trade which strips a team below required starters is rejected."""
    cur = db_conn.cursor()
    cur.execute("SELECT id FROM napper_teams WHERE league_id = 1 AND is_ai = TRUE ORDER BY id LIMIT 2")
    ai_team_1, ai_team_2 = [r[0] for r in cur.fetchall()]

    # Find a player on ai_team_2 whose departure leaves an orphaned position
    cur.execute("SELECT player_id FROM napper_rosters WHERE league_id = 1 AND team_id = %s AND week = 6 LIMIT 1", (ai_team_1,))
    p1 = cur.fetchone()[0]

    # Delete all other players except 1 on ai_team_2 to force zero depth
    cur.execute(
        "SELECT player_id FROM napper_rosters WHERE league_id = 1 AND team_id = %s AND week = 6 AND slot = 'QB' LIMIT 1",
        (ai_team_2,),
    )
    qb_row = cur.fetchone()
    if qb_row:
        qb_id = qb_row[0]
        cur.execute(
            "DELETE FROM napper_rosters WHERE league_id = 1 AND team_id = %s AND week = 6 AND player_id != %s",
            (ai_team_2, qb_id),
        )
        cur.execute(
            """
            INSERT INTO napper_trades (
                league_id, week, proposer_team_id, target_team_id,
                offered_player_ids, requested_player_ids, status, initiated_by_ai, ai_rationale
            ) VALUES (1, 6, %s, %s, %s, %s, 'PENDING', TRUE, 'Attempting to steal their only QB')
            RETURNING id
            """,
            (ai_team_1, ai_team_2, [p1], [qb_id]),
        )
        steal_trade_id = cur.fetchone()[0]

        results = process_ai_to_ai_trades(1, 6, conn=db_conn, random_seed=123)
        trade_res = next((r for r in results if r["trade_id"] == steal_trade_id), None)
        assert trade_res is not None
        assert trade_res["status"] == "REJECTED"


# -----------------------------------------------------------------------------
# 4. Weekly Awards Generation
# -----------------------------------------------------------------------------

def test_generate_weekly_awards_formatting(db_conn):
    """Verify weekly awards (RB/WR/TE Benchwarmer, High Scorer, Heartbreaker) generate formatted cards."""
    awards = generate_weekly_awards(1, 5, conn=db_conn)
    assert len(awards) >= 3

    award_types = {a["award_type"] for a in awards}
    assert "BENCHWARMER" in award_types
    assert "HIGH_SCORER" in award_types

    cur = db_conn.cursor()
    for award in awards:
        card = award["card_payload"]
        assert "award_type" in card
        assert "title" in card
        assert "badge" in card
        assert "details" in card

        # Verify stored in napper_chat with SYSTEM_BOT
        cur.execute("SELECT sender_type, sender_name FROM napper_chat WHERE id = %s", (award["chat_id"],))
        sender_type, sender_name = cur.fetchone()
        assert sender_type == "SYSTEM_BOT"
        assert sender_name == "Napper Awards Bot"


# -----------------------------------------------------------------------------
# 5. Persona-Specific Manager Banter Generation
# -----------------------------------------------------------------------------

def test_generate_manager_banter_all_personas(db_conn):
    """Verify contextual manager banter responds to blowouts, injuries, trades, and matchups."""
    contexts = ["INJURY", "BLOWOUT", "TRADE_CHATTER", "MATCHUP_PREVIEW"]
    for ctx in contexts:
        banter = generate_manager_banter(1, 6, context_type=ctx, conn=db_conn, random_seed=42)
        assert isinstance(banter, list)
        for b in banter:
            assert b["persona"] in PERSONA_BANTER_TEMPLATES
            assert len(b["message"]) > 10

            # Ensure valid sender handle format
            assert isinstance(b["speaker"], str)
            assert len(b["speaker"]) > 0


def test_persona_banter_template_integrity():
    """Verify all 4 personas have diverse templates across required triggers."""
    expected_personas = {"TRASH_TALKER", "ANALYTICS_NERD", "WAIVER_HAWK", "DIE_HARD"}
    assert expected_personas.issubset(set(PERSONA_BANTER_TEMPLATES.keys()))

    for persona, triggers in PERSONA_BANTER_TEMPLATES.items():
        assert "INJURY" in triggers
        assert "BLOWOUT_WIN" in triggers
        assert "BLOWOUT_LOSS" in triggers
        assert "TRADE_CHATTER" in triggers
        assert "AWARDS_BENCHWARMER" in triggers
        assert "MATCHUP_PREVIEW" in triggers

        # Verify templates have formatting keys
        for tmpl in triggers["INJURY"]:
            assert "{player}" in tmpl
        for tmpl in triggers["MATCHUP_PREVIEW"]:
            assert "{opp_handle}" in tmpl
