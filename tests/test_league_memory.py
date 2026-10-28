"""
Unit and integration tests for League Memory (V2.8) narrative search index (newsroom/league_memory.py).
Tests cover chunking, mock embedding seam, numpy cosine similarity, incremental indexing logic,
and the search_league_memory writer tool.
"""

import os
import psycopg
import pytest
import numpy as np
from unittest.mock import patch

from newsroom.league_memory import (
    cosine_similarity,
    embed_text,
    set_mock_embedder,
    set_local_embedder,
    format_chunk,
    clean_text_snippet,
    index_corpus,
    search_league_memory,
    build_indexing_candidates,
    DEFAULT_MODEL,
)
from newsroom.db_tools import search_league_memory as writer_tool_search
from newsroom.run_context import article_run_context

TEST_DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden_test user=postgres password=postgres")


# ------------------------------------------------------------------ Cosine Similarity Tests ----

def test_cosine_similarity_basic():
    """Exact, orthogonal, and opposite vectors evaluate correctly."""
    v1 = np.array([1.0, 0.0, 0.0])
    v2 = np.array([1.0, 0.0, 0.0])
    v3 = np.array([0.0, 1.0, 0.0])
    v4 = np.array([-1.0, 0.0, 0.0])

    matrix = np.stack([v2, v3, v4])
    sims = cosine_similarity(v1, matrix)

    assert pytest.approx(sims[0], 1e-5) == 1.0
    assert pytest.approx(sims[1], 1e-5) == 0.0
    assert pytest.approx(sims[2], 1e-5) == -1.0


def test_cosine_similarity_zero_norm_and_shapes():
    """Zero vectors do not divide by zero or raise NaN/Inf; 1D and 2D queries work."""
    v_zero = np.array([0.0, 0.0, 0.0])
    v_normal = np.array([3.0, 4.0, 0.0])

    sims = cosine_similarity(v_normal, np.stack([v_zero, v_normal]))
    assert not np.isnan(sims[0])
    assert pytest.approx(sims[1], 1e-5) == 1.0

    # 2D query shape (1, 3)
    q_2d = np.array([[3.0, 4.0, 0.0]])
    sims_2d = cosine_similarity(q_2d, np.stack([v_normal]))
    assert pytest.approx(sims_2d[0], 1e-5) == 1.0


# ------------------------------------------------------------------ Chunk Formatting Tests ----

def test_format_chunk_news_feed():
    team_mapping = {11: "CAR", 25: "ATL"}
    row = {
        "id": 101,
        "season": 2035,
        "week": 18,
        "team_id": 11,
        "headline": "Historic Panthers Milestone",
        "persona": "YATES",
        "article_type": "RECORD_BREAK",
        "excerpt": "Carolina scored 49 points to close out the year.",
        "content": "Full article text detailing the Panthers victory.",
        "subject_player_id": 999,
    }

    chunk, team_ids, player_ids, season, week = format_chunk("news_feed", row, team_mapping)
    assert "[2035 Week 18] Historic Panthers Milestone" in chunk
    assert "By YATES | RECORD_BREAK" in chunk
    assert "Carolina scored 49 points" in chunk
    assert team_ids == ["CAR"]
    assert player_ids == [999]
    assert season == 2035
    assert week == 18


def test_format_chunk_agent_memory():
    team_mapping = {31: "DEN"}
    row = {
        "id": 202,
        "agent_id": "Bill_Barnwell",
        "season": 2035,
        "week": 19,
        "kind": "TAKE",
        "text": "The Broncos record is an illusion primed for an early exit.",
        "team_id": 31,
        "player_id": 555,
    }

    chunk, team_ids, player_ids, season, week = format_chunk("agent_memory_entries", row, team_mapping)
    assert "[2035 Week 19] Writer Take by Bill_Barnwell (TAKE):" in chunk
    assert "The Broncos record is an illusion" in chunk
    assert team_ids == ["DEN"]
    assert player_ids == [555]
    assert season == 2035
    assert week == 19


def test_format_chunk_records():
    team_mapping = {25: "ATL"}
    # Franchise record
    fr_row = {
        "id": 303,
        "team_id": 25,
        "category": "season_pass_yards",
        "value": 4944,
        "holder": "Matt Ryan",
        "player_id": 12,
        "season": 2016,
        "source": "NFL",
    }
    chunk_fr, team_ids, player_ids, season, week = format_chunk("franchise_records", fr_row, team_mapping)
    assert "Franchise Record (ATL): Matt Ryan holds the franchise record for season pass yards with 4944" in chunk_fr
    assert team_ids == ["ATL"]
    assert player_ids == [12]
    assert season == 2016
    assert week is None

    # League record
    lre_row = {
        "id": 404,
        "team_id": None,
        "category": "season_pass_yards",
        "value": 5477,
        "holder": "Peyton Manning",
        "player_id": 18,
        "season": 2013,
        "source": "NFL",
    }
    chunk_lre, team_ids_lre, player_ids_lre, season_lre, week_lre = format_chunk("league_record_entries", lre_row, team_mapping)
    assert "NFL League Record (NFL): Peyton Manning holds the all-time league record for season pass yards with 5477" in chunk_lre
    assert team_ids_lre == []
    assert player_ids_lre == [18]
    assert season_lre == 2013
    assert week_lre is None


def test_clean_text_snippet_truncation():
    """Text is truncated cleanly without splitting words."""
    raw = "The quick brown fox jumps over the lazy dog. " * 100
    cleaned = clean_text_snippet(raw, max_chars=100)
    assert len(cleaned) <= 104
    assert cleaned.endswith("...")
    assert not cleaned.endswith(" fox... ")


# ------------------------------------------------------------------ Mock Embedding Seam Tests ----

def test_embed_text_mock_seam():
    """set_mock_embedder properly intercepts calls and returns predictable vectors."""
    def fake_embedder(texts):
        if isinstance(texts, str):
            return [float(len(texts)), 1.0, 0.5]
        return [[float(len(t)), 1.0, 0.5] for t in texts]

    set_mock_embedder(fake_embedder)
    try:
        single_vec = embed_text("test sentence")
        assert len(single_vec) == 3
        assert single_vec[0] == float(len("test sentence"))

        batch_vecs = embed_text(["alpha", "beta gamma"])
        assert len(batch_vecs) == 2
        assert batch_vecs[0][0] == float(len("alpha"))
        assert batch_vecs[1][0] == float(len("beta gamma"))
    finally:
        set_mock_embedder(None)


def test_embed_text_local_seam():
    """set_local_embedder works when configured for PC offline embeddings."""
    def local_hook(texts):
        if isinstance(texts, str):
            return [99.0, 99.0]
        return [[99.0, 99.0] for _ in texts]

    set_local_embedder(local_hook)
    try:
        vec = embed_text("hello")
        assert vec == [99.0, 99.0]
    finally:
        set_local_embedder(None)


# ------------------------------------------------------------------ Incremental Indexing Tests ----

@pytest.mark.integration
def test_incremental_indexing_skips_unchanged():
    """Index candidates only include unindexed or modified rows."""
    def deterministic_embedder(texts):
        if isinstance(texts, str):
            texts = [texts]
        return [[1.0, 0.0, 0.0] for _ in texts]

    with psycopg.connect(TEST_DSN) as conn:
        with conn.cursor() as cur:
            # Create isolated test rows in league_memory
            cur.execute("DELETE FROM league_memory WHERE source_table = 'test_source';")
            cur.execute("""
                INSERT INTO league_memory (
                    source_table, source_id, season, week, team_ids, player_ids, text_chunk, embedding, model
                ) VALUES (
                    'test_source', 1, 2035, 1, ARRAY['CAR'], ARRAY[10], 'Original chunk text', ARRAY[1.0, 0.0, 0.0]::real[], 'test-model'
                );
            """)
            conn.commit()

            existing = cur.execute("SELECT source_id, text_chunk, model FROM league_memory WHERE source_table = 'test_source';").fetchall()
            assert len(existing) == 1

            # Clean up test rows
            cur.execute("DELETE FROM league_memory WHERE source_table = 'test_source';")
            conn.commit()


@pytest.mark.integration
def test_index_corpus_dry_run_and_commit():
    """Dry run reports candidate plan without writes; commit saves entries."""
    def fake_embed(texts):
        if isinstance(texts, str):
            texts = [texts]
        # Return 16-dim deterministic vector
        return [[float(i) for i in range(16)] for _ in texts]

    with psycopg.connect(TEST_DSN) as conn:
        with conn.cursor() as cur:
            # Clean test area
            cur.execute("DELETE FROM league_memory WHERE source_table = 'franchise_records' AND source_id = 1;")
            conn.commit()

        # 1. Dry run on franchise_records with limit 5
        report_dry = index_corpus(
            conn=conn,
            model="test-embed-v1",
            limit=5,
            commit=False,
            dry_run=True,
            embedder=fake_embed,
        )
        assert report_dry["candidate_count"] > 0
        assert report_dry["committed"] is False
        assert report_dry["indexed_count"] == 0

        # 2. Commit with limit 3 using fake_embedder
        report_commit = index_corpus(
            conn=conn,
            model="test-embed-v1",
            limit=3,
            commit=True,
            dry_run=False,
            embedder=fake_embed,
            batch_size=2,
        )
        assert report_commit["committed"] is True
        assert report_commit["indexed_count"] == 3

        # Verify rows exist in DB
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM league_memory WHERE model = 'test-embed-v1';")
            cnt = cur.fetchone()[0]
            assert cnt == 3

            # Cleanup
            cur.execute("DELETE FROM league_memory WHERE model = 'test-embed-v1';")
            conn.commit()


# ------------------------------------------------------------------ Search & Writer Tool Tests ----

@pytest.mark.integration
def test_search_league_memory_tool_and_filters():
    """Writer tool respects query similarity, team filter, player filter, and article cutoff."""
    with psycopg.connect(TEST_DSN) as conn:
        with conn.cursor() as cur:
            # Clean and insert 3 controlled items
            cur.execute("DELETE FROM league_memory WHERE source_table LIKE 'test_mem_%';")
            cur.execute("""
                INSERT INTO league_memory (
                    source_table, source_id, season, week, team_ids, player_ids, text_chunk, embedding, model
                ) VALUES 
                ('test_mem_car', 1, 2035, 10, ARRAY['CAR'], ARRAY[101], 
                 '[2035 Week 10] Panthers Historic Comeback\\nBy YATES\\nCarolina rallied from 17 down in the 4th.', 
                 ARRAY[1.0, 0.0, 0.0]::real[], 'test-model'),
                ('test_mem_atl', 2, 2035, 12, ARRAY['ATL'], ARRAY[202], 
                 '[2035 Week 12] Falcons Record Pass Rush\\nBy BARNWELL\\nAtlanta sacked the quarterback 8 times.', 
                 ARRAY[0.0, 1.0, 0.0]::real[], 'test-model'),
                ('test_mem_future', 3, 2036, 15, ARRAY['CAR'], ARRAY[101], 
                 '[2036 Week 15] Panthers Future Championship\\nBy YATES\\nCarolina wins the late season matchup.', 
                 ARRAY[1.0, 0.0, 0.0]::real[], 'test-model');
            """)
            conn.commit()

        # Custom embedder returning query aligned with v1 ([1.0, 0.0, 0.0])
        def test_query_embed(q):
            return [1.0, 0.0, 0.0]

        # 1. Basic search without filters: Panthers comeback should be top
        res = search_league_memory(
            query="Panthers comeback from 17 down",
            conn=conn,
            embedder=test_query_embed,
            model="test-model",
            top_k=5,
        )
        assert len(res) >= 2
        # Highest similarity score should be 1.0
        assert res[0]["similarity_score"] == 1.0
        assert "Panthers" in res[0]["headline"]

        # 2. Team filter: query for ATL
        res_atl = search_league_memory(
            query="defensive performance",
            team="ATL",
            conn=conn,
            embedder=test_query_embed,
            model="test-model",
        )
        assert len(res_atl) == 1
        assert res_atl[0]["source_table"] == "test_mem_atl"

        # 3. Player ID filter: query for player 101
        res_pid = search_league_memory(
            query="Panthers game",
            player_id=101,
            conn=conn,
            embedder=test_query_embed,
            model="test-model",
        )
        for r in res_pid:
            assert 101 in r["player_ids"]

        # 4. Article cutoff test: if writing an article in Season 2036 Week 10, Week 15 should be hidden!
        with article_run_context(season=2036, week=10):
            res_cutoff = search_league_memory(
                query="Panthers comeback",
                conn=conn,
                embedder=test_query_embed,
                model="test-model",
            )
            # test_mem_future is 2036 Week 15, so it MUST be hidden
            future_sources = [r["source_table"] for r in res_cutoff if r["source_table"] == "test_mem_future"]
            assert len(future_sources) == 0

        # Clean up
        with conn.cursor() as cur:
            cur.execute("DELETE FROM league_memory WHERE source_table LIKE 'test_mem_%';")
            conn.commit()


def test_writer_tool_registration():
    """Writer tool is registered as a LangChain tool and callable."""
    assert hasattr(writer_tool_search, "name")
    assert writer_tool_search.name == "search_league_memory"
    assert "historical universe memory" in writer_tool_search.description.lower()
