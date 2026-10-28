"""
League Memory (V2.8) narrative search index (docs/PRE_PC_WORK.md §9, docs/PC_MIGRATION_PLAN.md V2.8).

Builds and searches a narrative embedding index over historical universe events:
- news_feed (published articles)
- agent_memory_entries (writer takes & viewpoints)
- franchise_records (all 32 franchise records)
- league_record_entries (all-time NFL records)

Enables newsroom writers, beat reporters, and agents to query authentic historical callbacks
("the last time Carolina came back from 17 down was 2031").

Uses PostgreSQL 14 without pgvector, storing embeddings as REAL[] and performing brute-force
cosine similarity in Python/numpy.
"""

import argparse
import os
import re
import psycopg
import numpy as np
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from pipeline.notify import env
from newsroom.run_context import is_visible_week

DEFAULT_MODEL = os.environ.get("LEAGUE_MEMORY_MODEL", "gemini-embedding-001")
DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

# Global hooks for testing & local PC models
_MOCK_EMBEDDER: Optional[Callable[[Union[str, List[str]]], Any]] = None
_LOCAL_EMBEDDER: Optional[Callable[[Union[str, List[str]]], Any]] = None


def set_mock_embedder(fn: Optional[Callable[[Union[str, List[str]]], Any]]):
    """Set or clear a mock embedder function for testing."""
    global _MOCK_EMBEDDER
    _MOCK_EMBEDDER = fn


def set_local_embedder(fn: Optional[Callable[[Union[str, List[str]]], Any]]):
    """Set or clear a local model embedding hook for the PC."""
    global _LOCAL_EMBEDDER
    _LOCAL_EMBEDDER = fn


def api_key() -> str:
    """Resolve Gemini API key from environment or .env."""
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or env("GEMINI_API_KEY") or ""


# ------------------------------------------------------------------ Cosine Similarity ----

def cosine_similarity(query_emb: Union[np.ndarray, List[float]], matrix: Union[np.ndarray, List[List[float]]]) -> np.ndarray:
    """Vectorized cosine similarity between query_emb and rows of matrix in numpy.
    
    query_emb: shape (D,) or (1, D)
    matrix: shape (N, D) or (D,)
    Returns: shape (N,) similarity scores in [-1.0, 1.0].
    """
    q = np.asarray(query_emb, dtype=np.float32)
    m = np.asarray(matrix, dtype=np.float32)
    if m.size == 0:
        return np.array([], dtype=np.float32)
    if m.ndim == 1:
        m = m.reshape(1, -1)
    if q.ndim == 2:
        q = q.squeeze(0)

    norm_q = np.linalg.norm(q)
    norm_m = np.linalg.norm(m, axis=1)

    denom = norm_q * norm_m
    denom = np.where(denom == 0, 1e-10, denom)
    sims = np.dot(m, q) / denom
    return sims


# ------------------------------------------------------------------ Embedding Seam ----

def embed_text(
    texts: Union[str, List[str]],
    model: str = DEFAULT_MODEL,
    client: Any = None,
    mock_fn: Optional[Callable] = None,
    batch_size: int = 50,
) -> Union[List[float], List[List[float]]]:
    """Embed one or more text strings.
    
    Returns a list of float values for a single string input, or a list of list of floats
    for a list of strings input.
    
    Metered in db.llm_usage with source='memory_index'.
    """
    is_single = isinstance(texts, str)
    text_list = [texts] if is_single else list(texts)
    if not text_list:
        return []

    # 1. Mock embedder takes precedence (unit tests)
    effective_mock = mock_fn or _MOCK_EMBEDDER
    if effective_mock is not None:
        raw = effective_mock(texts)
        if is_single and isinstance(raw, list) and raw and isinstance(raw[0], (int, float)):
            return raw
        elif is_single and isinstance(raw, list) and raw and isinstance(raw[0], list):
            return raw[0]
        return raw

    # 2. Local embedder hook (for local PC Ollama / ONNX embedding models)
    if _LOCAL_EMBEDDER is not None:
        raw = _LOCAL_EMBEDDER(texts)
        if is_single and isinstance(raw, list) and raw and isinstance(raw[0], (int, float)):
            return raw
        elif is_single and isinstance(raw, list) and raw and isinstance(raw[0], list):
            return raw[0]
        return raw

    # 3. Gemini embedding API
    from google import genai
    from db.llm_usage import log_usage

    key = api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set for embed_text")

    if client is None:
        client = genai.Client(api_key=key, http_options={"timeout": 30000})

    all_embeddings: List[List[float]] = []

    import time

    # Batch process
    for i in range(0, len(text_list), batch_size):
        batch = text_list[i : i + batch_size]
        res = None
        for attempt in range(5):
            try:
                res = client.models.embed_content(model=model, contents=batch)
                break
            except Exception as e:
                if attempt == 4:
                    raise
                time.sleep(2.0 ** attempt)
        batch_vecs = [e.values for e in res.embeddings]
        all_embeddings.extend(batch_vecs)

        # Token estimation (~4 chars per token)
        est_tokens = max(1, sum(len(t) for t in batch) // 4)
        log_usage(
            source="memory_index",
            label=f"embed_batch_{len(batch)}",
            model=model,
            input_tokens=est_tokens,
            output_tokens=0,
        )

    return all_embeddings[0] if is_single else all_embeddings


# ------------------------------------------------------------------ Chunk Formatting ----

def clean_text_snippet(text: str, max_chars: int = 2500) -> str:
    """Trim excess whitespace and bound length to max_chars without chopping mid-word."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    # Cut at last whitespace before max_chars
    cut = text[:max_chars].rfind(" ")
    return (text[:cut] if cut > 0 else text[:max_chars]) + "..."


def format_chunk(
    source_table: str,
    row: Dict[str, Any],
    team_mapping: Optional[Dict[int, str]] = None,
) -> Tuple[str, List[str], List[int], Optional[int], Optional[int]]:
    """Generate a clean narrative text chunk and metadata for a source row.
    
    Returns: (text_chunk, team_ids, player_ids, season, week)
    """
    team_mapping = team_mapping or {}
    season: Optional[int] = row.get("season")
    week: Optional[int] = row.get("week")
    team_ids: List[str] = []
    player_ids: List[int] = []

    if source_table == "news_feed":
        headline = (row.get("headline") or "").strip()
        persona = (row.get("persona") or "").strip()
        article_type = (row.get("article_type") or "").strip()
        excerpt = (row.get("excerpt") or "").strip()
        content = (row.get("content") or "").strip()
        
        team_id = row.get("team_id")
        if team_id and team_id in team_mapping:
            team_ids.append(team_mapping[team_id])
            
        subj_player = row.get("subject_player_id")
        if subj_player:
            player_ids.append(subj_player)

        # Build chunk
        prefix = f"[{season or '?'} Week {week or '?'}] {headline}"
        byline = f"By {persona or 'Staff'} | {article_type or 'Article'}"
        body_snip = clean_text_snippet(excerpt or content, max_chars=2000)
        chunk = f"{prefix}\n{byline}\n{body_snip}".strip()
        return chunk, team_ids, player_ids, season, week

    elif source_table == "agent_memory_entries":
        agent_id = row.get("agent_id") or "Writer"
        kind = row.get("kind") or "TAKE"
        text = (row.get("text") or "").strip()

        team_id = row.get("team_id")
        if team_id and team_id in team_mapping:
            team_ids.append(team_mapping[team_id])

        pid = row.get("player_id")
        if pid:
            player_ids.append(pid)

        chunk = f"[{season or '?'} Week {week or '?'}] Writer Take by {agent_id} ({kind}):\n{text}".strip()
        return chunk, team_ids, player_ids, season, week

    elif source_table == "franchise_records":
        team_id = row.get("team_id")
        team_abbr = team_mapping.get(team_id) if team_id else None
        if team_abbr:
            team_ids.append(team_abbr)

        pid = row.get("player_id")
        if pid:
            player_ids.append(pid)

        category = (row.get("category") or "").replace("_", " ")
        holder = row.get("holder") or "Unknown"
        val = row.get("value")
        source = row.get("source") or "NFL"

        chunk = f"[{season or 'Historical'}] Franchise Record ({team_abbr or 'Franchise'}): {holder} holds the franchise record for {category} with {val} (Source: {source})."
        return chunk, team_ids, player_ids, season, None

    elif source_table == "league_record_entries":
        team_id = row.get("team_id")
        team_abbr = team_mapping.get(team_id) if team_id else None
        if team_abbr:
            team_ids.append(team_abbr)

        pid = row.get("player_id")
        if pid:
            player_ids.append(pid)

        category = (row.get("category") or "").replace("_", " ")
        holder = row.get("holder") or "Unknown"
        val = row.get("value")
        source = row.get("source") or "NFL"

        chunk = f"[{season or 'Historical'}] NFL League Record ({team_abbr or 'NFL'}): {holder} holds the all-time league record for {category} with {val} (Source: {source})."
        return chunk, team_ids, player_ids, season, None

    else:
        raise ValueError(f"Unknown source table: {source_table}")


# ------------------------------------------------------------------ Indexing Engine ----

def fetch_team_mapping(cur: psycopg.Cursor) -> Dict[int, str]:
    """Fetch mapping of team ID -> abbreviation."""
    cur.execute("SELECT id, abbreviation FROM teams;")
    return {row[0]: row[1] for row in cur.fetchall()}


def fetch_existing_index(cur: psycopg.Cursor) -> Dict[Tuple[str, int], Tuple[str, str]]:
    """Fetch existing index entries: (source_table, source_id) -> (text_chunk, model)."""
    cur.execute("SELECT source_table, source_id, text_chunk, model FROM league_memory;")
    return {(row[0], row[1]): (row[2], row[3]) for row in cur.fetchall()}


def build_indexing_candidates(
    cur: psycopg.Cursor,
    model: str = DEFAULT_MODEL,
    season: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Gather source items from the four tables that need indexing or re-indexing."""
    team_mapping = fetch_team_mapping(cur)
    existing = fetch_existing_index(cur)
    candidates: List[Dict[str, Any]] = []

    # 1. news_feed
    nf_sql = "SELECT id, season, week, team_id, headline, excerpt, content, persona, article_type, subject_player_id FROM news_feed"
    nf_params = []
    if season is not None:
        nf_sql += " WHERE season = %s"
        nf_params.append(season)
    nf_sql += " ORDER BY id ASC;"
    cur.execute(nf_sql, nf_params)
    for r in cur.fetchall():
        row_dict = {
            "id": r[0], "season": r[1], "week": r[2], "team_id": r[3],
            "headline": r[4], "excerpt": r[5], "content": r[6], "persona": r[7],
            "article_type": r[8], "subject_player_id": r[9],
        }
        chunk, team_ids, player_ids, s, w = format_chunk("news_feed", row_dict, team_mapping)
        key = ("news_feed", row_dict["id"])
        if key not in existing or existing[key][0] != chunk or existing[key][1] != model:
            candidates.append({
                "source_table": "news_feed",
                "source_id": row_dict["id"],
                "season": s,
                "week": w,
                "team_ids": team_ids,
                "player_ids": player_ids,
                "text_chunk": chunk,
            })
            if limit and len(candidates) >= limit:
                return candidates

    # 2. agent_memory_entries
    ame_sql = "SELECT id, agent_id, season, week, article_id, kind, text, team_id, player_id FROM agent_memory_entries"
    ame_params = []
    if season is not None:
        ame_sql += " WHERE season = %s"
        ame_params.append(season)
    ame_sql += " ORDER BY id ASC;"
    cur.execute(ame_sql, ame_params)
    for r in cur.fetchall():
        row_dict = {
            "id": r[0], "agent_id": r[1], "season": r[2], "week": r[3],
            "article_id": r[4], "kind": r[5], "text": r[6], "team_id": r[7],
            "player_id": r[8],
        }
        chunk, team_ids, player_ids, s, w = format_chunk("agent_memory_entries", row_dict, team_mapping)
        key = ("agent_memory_entries", row_dict["id"])
        if key not in existing or existing[key][0] != chunk or existing[key][1] != model:
            candidates.append({
                "source_table": "agent_memory_entries",
                "source_id": row_dict["id"],
                "season": s,
                "week": w,
                "team_ids": team_ids,
                "player_ids": player_ids,
                "text_chunk": chunk,
            })
            if limit and len(candidates) >= limit:
                return candidates

    # 3. franchise_records
    fr_sql = "SELECT id, team_id, category, value, holder, player_id, season, source FROM franchise_records"
    fr_params = []
    if season is not None:
        fr_sql += " WHERE season = %s"
        fr_params.append(season)
    fr_sql += " ORDER BY id ASC;"
    cur.execute(fr_sql, fr_params)
    for r in cur.fetchall():
        row_dict = {
            "id": r[0], "team_id": r[1], "category": r[2], "value": r[3],
            "holder": r[4], "player_id": r[5], "season": r[6], "source": r[7],
        }
        chunk, team_ids, player_ids, s, w = format_chunk("franchise_records", row_dict, team_mapping)
        key = ("franchise_records", row_dict["id"])
        if key not in existing or existing[key][0] != chunk or existing[key][1] != model:
            candidates.append({
                "source_table": "franchise_records",
                "source_id": row_dict["id"],
                "season": s,
                "week": w,
                "team_ids": team_ids,
                "player_ids": player_ids,
                "text_chunk": chunk,
            })
            if limit and len(candidates) >= limit:
                return candidates

    # 4. league_record_entries
    lre_sql = "SELECT id, category, value, holder, team_id, player_id, season, source FROM league_record_entries"
    lre_params = []
    if season is not None:
        lre_sql += " WHERE season = %s"
        lre_params.append(season)
    lre_sql += " ORDER BY id ASC;"
    cur.execute(lre_sql, lre_params)
    for r in cur.fetchall():
        row_dict = {
            "id": r[0], "category": r[1], "value": r[2], "holder": r[3],
            "team_id": r[4], "player_id": r[5], "season": r[6], "source": r[7],
        }
        chunk, team_ids, player_ids, s, w = format_chunk("league_record_entries", row_dict, team_mapping)
        key = ("league_record_entries", row_dict["id"])
        if key not in existing or existing[key][0] != chunk or existing[key][1] != model:
            candidates.append({
                "source_table": "league_record_entries",
                "source_id": row_dict["id"],
                "season": s,
                "week": w,
                "team_ids": team_ids,
                "player_ids": player_ids,
                "text_chunk": chunk,
            })
            if limit and len(candidates) >= limit:
                return candidates

    return candidates


def index_corpus(
    conn: psycopg.Connection,
    model: str = DEFAULT_MODEL,
    season: Optional[int] = None,
    limit: Optional[int] = None,
    commit: bool = False,
    dry_run: bool = False,
    embedder: Optional[Callable] = None,
    batch_size: int = 50,
) -> Dict[str, Any]:
    """Incrementally indexes the league memory corpus.
    
    If commit=False or dry_run=True, prints the estimate and returns without writing.
    """
    with conn.cursor() as cur:
        candidates = build_indexing_candidates(cur, model=model, season=season, limit=limit)

    total_chars = sum(len(c["text_chunk"]) for c in candidates)
    est_tokens = max(1, total_chars // 4)
    # Gemini embedding price is ~$0.15 per million tokens
    est_cost = (est_tokens / 1_000_000.0) * 0.15

    report = {
        "candidate_count": len(candidates),
        "total_chars": total_chars,
        "est_tokens": est_tokens,
        "est_cost_usd": round(est_cost, 6),
        "model": model,
        "season_filter": season,
        "committed": False,
        "indexed_count": 0,
    }

    print(f"=== League Memory Index Plan ===")
    print(f"Model:               {model}")
    print(f"Candidate chunks:    {len(candidates):,}")
    print(f"Total characters:    {total_chars:,}")
    print(f"Estimated tokens:    {est_tokens:,}")
    print(f"Estimated cost:      ${est_cost:.4f}")
    if season:
        print(f"Season filter:       {season}")
    if limit:
        print(f"Limit:               {limit}")

    if not commit or dry_run:
        print("\nDry-run complete: no database modifications made (use --commit to write).")
        return report

    if not candidates:
        print("All items are already indexed and up to date.")
        report["committed"] = True
        return report

    print(f"\nEmbedding and writing {len(candidates)} items in batches of {batch_size}...")

    upsert_sql = """
        INSERT INTO league_memory (
            source_table, source_id, season, week, team_ids, player_ids, text_chunk, embedding, model, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (source_table, source_id) DO UPDATE SET
            season = EXCLUDED.season,
            week = EXCLUDED.week,
            team_ids = EXCLUDED.team_ids,
            player_ids = EXCLUDED.player_ids,
            text_chunk = EXCLUDED.text_chunk,
            embedding = EXCLUDED.embedding,
            model = EXCLUDED.model,
            created_at = NOW();
    """

    indexed_count = 0
    with conn.cursor() as cur:
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]
            texts = [c["text_chunk"] for c in batch]
            vecs = embed_text(texts, model=model, mock_fn=embedder, batch_size=batch_size)

            for item, vec in zip(batch, vecs):
                cur.execute(
                    upsert_sql,
                    (
                        item["source_table"],
                        item["source_id"],
                        item["season"],
                        item["week"],
                        item["team_ids"],
                        item["player_ids"],
                        item["text_chunk"],
                        vec,
                        model,
                    ),
                )
                indexed_count += 1
            conn.commit()
            if (i + batch_size) % 500 < batch_size or (i + len(batch)) == len(candidates):
                print(f"  Indexed {indexed_count} / {len(candidates)} items...")

    report["committed"] = True
    report["indexed_count"] = indexed_count
    print(f"Successfully committed {indexed_count} items to league_memory.")
    return report


# ------------------------------------------------------------------ Search & Writer Tool ----

def resolve_team_abbr(team: Optional[str], cur: psycopg.Cursor) -> Optional[str]:
    """Resolve a team string ('CAR', 'Panthers', 'Carolina Panthers') to its abbreviation."""
    if not team:
        return None
    t = team.strip().upper()
    cur.execute("SELECT abbreviation FROM teams WHERE UPPER(abbreviation) = %s;", (t,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("SELECT abbreviation FROM teams WHERE UPPER(name) = %s OR UPPER(name) ILIKE %s;", (t, f"%{t}%"))
    row = cur.fetchone()
    return row[0] if row else t


def search_league_memory(
    query: str,
    team: Optional[str] = None,
    player_id: Optional[int] = None,
    seasons: Optional[List[int]] = None,
    top_k: int = 5,
    conn: Optional[psycopg.Connection] = None,
    embedder: Optional[Callable] = None,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, Any]]:
    """Search historical universe memory for authentic narrative callbacks.
    
    query: semantic search query (e.g. 'largest fourth quarter comeback', 'historic sack record')
    team: optional team abbreviation or name filter (e.g. 'CAR', 'Panthers')
    player_id: optional player ID filter
    seasons: optional list of seasons to restrict search
    top_k: number of citations to return (default 5)
    
    Returns short cited snippets (headline/source, date/season/week, text excerpt, similarity score).
    """
    if not query or not query.strip():
        return []

    should_close = False
    if conn is None:
        conn = psycopg.connect(DSN)
        should_close = True

    try:
        with conn.cursor() as cur:
            # 1. Resolve team abbreviation if provided
            team_abbr = resolve_team_abbr(team, cur) if team else None

            # 2. Build SQL candidate fetch
            sql = "SELECT id, source_table, source_id, season, week, team_ids, player_ids, text_chunk, embedding FROM league_memory WHERE 1=1"
            params: List[Any] = []

            if team_abbr:
                sql += " AND %s = ANY(team_ids)"
                params.append(team_abbr)

            if player_id is not None:
                sql += " AND %s = ANY(player_ids)"
                params.append(int(player_id))

            if seasons:
                sql += " AND season = ANY(%s)"
                params.append([int(s) for s in seasons])

            cur.execute(sql, params)
            rows = cur.fetchall()

            if not rows:
                return []

            # 3. Filter candidates for article cutoff if active
            valid_rows = []
            for r in rows:
                r_season = r[3]
                r_week = r[4]
                # If season and week are present, verify visibility; historical records without week are visible
                if r_season is not None and r_week is not None:
                    if not is_visible_week(r_season, r_week):
                        continue
                valid_rows.append(r)

            if not valid_rows:
                return []

            # 4. Generate query embedding
            q_emb = embed_text(query, model=model, mock_fn=embedder)
            q_dim = len(q_emb)

            # 5. Filter candidate embeddings matching query dimension
            dim_matched_rows = [r for r in valid_rows if len(r[8]) == q_dim]
            if not dim_matched_rows:
                return []

            # 6. Compute cosine similarities
            matrix = np.array([r[8] for r in dim_matched_rows], dtype=np.float32)
            sims = cosine_similarity(q_emb, matrix)

            # 7. Rank top_k
            top_k_eff = min(top_k, len(dim_matched_rows))
            top_indices = np.argsort(sims)[-top_k_eff:][::-1]

            results = []
            for idx in top_indices:
                r = dim_matched_rows[idx]
                sim_score = float(sims[idx])
                source_table = r[1]
                source_id = r[2]
                s = r[3]
                w = r[4]
                t_ids = r[5] or []
                p_ids = r[6] or []
                chunk = r[7]

                # Extract headline/label from first line
                first_line = chunk.split("\n", 1)[0]
                headline = first_line[:120]

                date_label = f"Season {s} Week {w}" if (s and w) else f"Season {s}" if s else "All-Time"
                results.append({
                    "headline": headline,
                    "source": f"{source_table}:{source_id}",
                    "source_table": source_table,
                    "source_id": source_id,
                    "date": date_label,
                    "season": s,
                    "week": w,
                    "team_ids": t_ids,
                    "player_ids": p_ids,
                    "text_excerpt": chunk[:400] + ("..." if len(chunk) > 400 else ""),
                    "similarity_score": round(sim_score, 4),
                })

            return results
    finally:
        if should_close:
            conn.close()


# ------------------------------------------------------------------ CLI Interface ----

def main():
    parser = argparse.ArgumentParser(description="League Memory Narrative Index CLI (docs/PRE_PC_WORK.md §9)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # index subcommand
    idx_p = subparsers.add_parser("index", help="Index or update the league memory corpus")
    idx_p.add_argument("--limit", type=int, default=None, help="Maximum items to embed")
    idx_p.add_argument("--season", type=int, default=None, help="Filter items by season")
    idx_p.add_argument("--commit", action="store_true", help="Write embeddings to database")
    idx_p.add_argument("--dry-run", action="store_true", help="Estimate chunk count and cost without embedding")
    idx_p.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Embedding model name")
    idx_p.add_argument("--dsn", type=str, default=DSN, help="Database connection DSN")
    idx_p.add_argument("--batch-size", type=int, default=50, help="Embedding batch size (default 50)")

    # search subcommand
    search_p = subparsers.add_parser("search", help="Query the league memory index")
    search_p.add_argument("query", type=str, help="Search query")
    search_p.add_argument("--team", type=str, default=None, help="Team filter (abbreviation or name)")
    search_p.add_argument("--player-id", type=int, default=None, help="Player ID filter")
    search_p.add_argument("--seasons", type=int, nargs="+", default=None, help="Season filters")
    search_p.add_argument("--top-k", type=int, default=5, help="Number of results to return")
    search_p.add_argument("--model", type=str, default=DEFAULT_MODEL, help="Embedding model name")
    search_p.add_argument("--dsn", type=str, default=DSN, help="Database connection DSN")

    args = parser.parse_args()

    if args.command == "index":
        with psycopg.connect(args.dsn) as conn:
            index_corpus(
                conn=conn,
                model=args.model,
                season=args.season,
                limit=args.limit,
                commit=args.commit,
                dry_run=args.dry_run,
                batch_size=args.batch_size,
            )

    elif args.command == "search":
        with psycopg.connect(args.dsn) as conn:
            results = search_league_memory(
                query=args.query,
                team=args.team,
                player_id=args.player_id,
                seasons=args.seasons,
                top_k=args.top_k,
                conn=conn,
                model=args.model,
            )
            print(f"\nFound {len(results)} matches for: '{args.query}'\n")
            for idx, res in enumerate(results, start=1):
                print(f"[{idx}] {res['headline']} (score: {res['similarity_score']})")
                print(f"    Source: {res['source']} | Date: {res['date']} | Teams: {res['team_ids']}")
                print(f"    Excerpt: {res['text_excerpt']}\n")


if __name__ == "__main__":
    main()
