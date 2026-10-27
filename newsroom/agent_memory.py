"""Evolving agent memory: takes log, prediction track record and weekly season summaries (PROJECT_SPEC §7.7).

Each agent keeps its hand-written identity line (agent_memories.persistent_memory). On top of it:
  - Takes (agent_memory_entries): after a live article is saved, one small model call extracts up to 3 stances the
    writer committed to; a tweet is stored as a take as-is (no extra call).
  - Predictions (agent_predictions): "Projected Final: Team N, Team N" lines (and roundtable panellist lines) in
    preview articles are parsed into per-game score picks and graded against final scores when a prompt is built.
  - Summaries (agent_season_summaries): at the start of each weekly run, agents with enough new takes get their
    "season so far" rewritten (one call each), stored per (agent, season, through_week).
Prompts read only memories from before the article's publication week, so regenerating an old week can't see later
takes, results or summaries.
"""
import json
import re

from newsroom.power_rankings import resolve_team

MAX_TAKES_PER_ARTICLE = 3
RECENT_TAKES = 6
SUBJECT_TAKES = 3
RECENT_PREDICTIONS = 5
SUMMARY_MIN_NEW_TAKES = 3
TAKE_MAX_CHARS = 280

SCORE_PAIR_RE = re.compile(r"(?P<a>[^,\n]{2,90}?)\s+(?P<sa>\d{1,2})\s*,\s*(?P<b>[^,\n]{2,60}?)\s+(?P<sb>\d{1,2})\b")
LABEL_LINE_RE = re.compile(r"^\s*(?:[-*•]\s*)?(?P<label>[A-Z][\w.' -]{1,40}?):\s*(?P<rest>.+)$")


# ---------------------------------------------------------------- pure parsing / formatting

def _clean(line: str) -> str:
    return re.sub(r"[*_`#>]", "", line).strip()


def parse_score_predictions(text: str, team_index: list, panel: bool = False) -> list:
    """[{label, team_a, score_a, team_b, score_b}] from 'Projected Final: ...' lines; with panel=True also from
    'Name: Team N, Team N' lines (roundtable). Teams resolve by nickname (power_rankings.resolve_team)."""
    out, seen = [], set()
    for raw in (text or "").splitlines():
        line = _clean(raw)
        low = line.lower()
        label = ""
        if "projected final" in low:
            body = line[low.index("projected final") + len("projected final"):].lstrip(" :-–")
        elif panel:
            m = LABEL_LINE_RE.match(line)
            if not m:
                continue
            label, body = m.group("label").strip(), m.group("rest")
        else:
            continue
        m = SCORE_PAIR_RE.search(body)
        if not m:
            continue
        a, b = resolve_team(m.group("a"), team_index), resolve_team(m.group("b"), team_index)
        if not a or not b or a == b:
            continue
        key = (label, frozenset((a, b)))
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "team_a": a, "score_a": int(m.group("sa")), "team_b": b, "score_b": int(m.group("sb"))})
    return out


def parse_takes_json(raw: str, limit: int = MAX_TAKES_PER_ARTICLE) -> list:
    """Model output -> [{take, team, player}] (tolerates code fences and stray text)."""
    if not raw:
        return []
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    takes = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not str(it.get("take", "")).strip():
            continue
        takes.append({"take": str(it["take"]).strip()[:TAKE_MAX_CHARS],
                      "team": str(it.get("team") or "").strip(), "player": str(it.get("player") or "").strip()})
    return takes[:limit]


def grade_prediction(p: dict) -> dict:
    """Adds 'result' ('correct' | 'wrong' | 'push' | 'pending') from actual scores (actual_a / actual_b or None)."""
    if p.get("actual_a") is None or p.get("actual_b") is None:
        return {**p, "result": "pending"}
    picked = (p["score_a"] > p["score_b"]) - (p["score_a"] < p["score_b"])
    actual = (p["actual_a"] > p["actual_b"]) - (p["actual_a"] < p["actual_b"])
    if picked == 0 or actual == 0:
        return {**p, "result": "push"}
    return {**p, "result": "correct" if picked == actual else "wrong"}


def format_memory_block(summary: str, takes: list, subject_takes: list, predictions: list, record: tuple) -> str:
    """Prompt section appended under the identity line. Empty string when there is nothing to remember."""
    parts = []
    if summary:
        parts.append(f"YOUR SEASON SO FAR (your own running memory):\n{summary.strip()}")
    if takes:
        parts.append("YOUR RECENT TAKES (stay consistent with them, or say plainly that you've changed your mind):\n"
                     + "\n".join(f"- Week {t['week']}: {t['text']}" for t in takes))
    if subject_takes:
        parts.append("WHAT YOU'VE SAID BEFORE ABOUT THIS ARTICLE'S SUBJECT:\n"
                     + "\n".join(f"- Week {t['week']}: {t['text']}" for t in subject_takes))
    graded = [p for p in predictions if p["result"] != "pending"]
    if graded or any(record):
        wins, losses = record
        lines = [f"YOUR PREDICTION TRACK RECORD: {wins}-{losses} picking winners this season."]
        for p in graded:
            verdict = {"correct": "you were right", "wrong": "you were WRONG", "push": "a tie"}[p["result"]]
            lines.append(f"- Week {p['week']}{(' (' + p['label'] + ')') if p.get('label') else ''}: you predicted "
                         f"{p['name_a']} {p['score_a']}, {p['name_b']} {p['score_b']}; final was "
                         f"{p['name_a']} {p['actual_a']}, {p['name_b']} {p['actual_b']}: {verdict}.")
        lines.append("Own your misses in your own voice when they're relevant; don't pretend they didn't happen.")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def takes_extraction_prompt(display_name: str, headline: str, text: str) -> str:
    return f"""You maintain the long-term memory of {display_name}, an NFL writer. Read the article below and list up to {MAX_TAKES_PER_ARTICLE} specific stances, opinions or predictions the author committed to and should stay consistent with in future weeks (for example: "The Bills' 9-3 record is a mirage built on one-score luck" or "Drake Maye is my MVP pick"). Skip generic statements and plain facts.
Write each take in the first person, in the author's voice, under 30 words. Name the one team (nickname) and/or player (full name) it is mainly about, or leave them empty.
Return only JSON: [{{"take": "...", "team": "...", "player": "..."}}]

HEADLINE: {headline or ''}
ARTICLE:
{(text or '')[:12000]}"""


def summary_prompt(display_name: str, identity: str, previous_summary: str, takes: list, predictions: list, through_week: int) -> str:
    take_lines = "\n".join(f"- Week {t['week']}: {t['text']}" for t in takes) or "- (none)"
    pred_lines = "\n".join(
        f"- Week {p['week']}: predicted {p['name_a']} {p['score_a']}-{p['score_b']} {p['name_b']}, actual "
        f"{p['actual_a']}-{p['actual_b']} ({p['result']})" for p in predictions if p["result"] != "pending") or "- (none)"
    return f"""You maintain the long-term memory of {display_name}. Their fixed identity: "{identity}".
Rewrite their running "season so far" memory through Week {through_week}, in the first person and in their voice, in at most 120 words. Keep the stances they keep returning to, running grudges and favorites, players or teams they've championed or doubted, and notable predictions they got right or wrong. Drop one-off remarks. Return only the memory text.

PREVIOUS MEMORY:
{previous_summary or '(none yet)'}

NEW TAKES:
{take_lines}

GRADED PREDICTIONS:
{pred_lines}"""


# ---------------------------------------------------------------- database

def tables_exist(cur) -> bool:
    cur.execute("SELECT to_regclass('public.agent_memory_entries') IS NOT NULL AND to_regclass('public.agent_predictions') IS NOT NULL "
                "AND to_regclass('public.agent_season_summaries') IS NOT NULL;")
    return bool(cur.fetchone()[0])


def _team_names(cur) -> dict:
    cur.execute("SELECT id, city || ' ' || name FROM teams;")
    return dict(cur.fetchall())


def fetch_predictions(cur, agent_id: str, season: int, before_week: int, limit: int = None) -> list:
    cur.execute("""
        SELECT p.week, p.label, p.team_a_id, p.score_a, p.team_b_id, p.score_b,
               CASE WHEN g.status = 'COMPLETED' AND g.week < %s THEN
                    CASE WHEN g.home_team_id = p.team_a_id THEN g.home_score ELSE g.away_score END END,
               CASE WHEN g.status = 'COMPLETED' AND g.week < %s THEN
                    CASE WHEN g.home_team_id = p.team_b_id THEN g.home_score ELSE g.away_score END END
        FROM agent_predictions p
        JOIN games g ON g.id = p.game_id
        WHERE p.agent_id = %s AND p.season = %s AND p.week < %s
        ORDER BY p.week DESC, p.id DESC
    """, (before_week, before_week, agent_id, season, before_week))
    pred_rows = cur.fetchall()   # read before _team_names reuses the cursor
    names = _team_names(cur)
    rows = []
    for week, label, a, sa, b, sb, aa, ab in pred_rows:
        rows.append(grade_prediction({"week": week, "label": label, "score_a": sa, "score_b": sb, "actual_a": aa,
                                      "actual_b": ab, "name_a": names.get(a, str(a)), "name_b": names.get(b, str(b))}))
    return rows[:limit] if limit else rows


def build_memory_block(cur, agent_id: str, season: int, week: int, subject_team_id: int = None) -> str:
    """Memory section for an agent's prompt, from memories recorded before `week` of `season`."""
    if not agent_id or not tables_exist(cur):
        return ""
    cur.execute("""SELECT summary FROM agent_season_summaries
                   WHERE agent_id = %s AND season = %s AND through_week < %s
                   ORDER BY through_week DESC LIMIT 1;""", (agent_id, season, week))
    row = cur.fetchone()
    summary = row[0] if row else ""
    cur.execute("""SELECT week, text FROM agent_memory_entries
                   WHERE agent_id = %s AND season = %s AND week < %s AND kind = 'TAKE'
                   ORDER BY week DESC, id DESC LIMIT %s;""", (agent_id, season, week, RECENT_TAKES))
    takes = [{"week": w, "text": t} for w, t in cur.fetchall()]
    subject_takes = []
    if subject_team_id:
        cur.execute("""SELECT week, text FROM agent_memory_entries
                       WHERE agent_id = %s AND season = %s AND week < %s AND kind = 'TAKE' AND team_id = %s
                       ORDER BY week DESC, id DESC LIMIT %s;""", (agent_id, season, week, subject_team_id, SUBJECT_TAKES + RECENT_TAKES))
        recent = {(t["week"], t["text"]) for t in takes}
        subject_takes = [{"week": w, "text": t} for w, t in cur.fetchall() if (w, t) not in recent][:SUBJECT_TAKES]
    preds = fetch_predictions(cur, agent_id, season, week)
    record = (sum(p["result"] == "correct" for p in preds), sum(p["result"] == "wrong" for p in preds))
    return format_memory_block(summary, takes, subject_takes, preds[:RECENT_PREDICTIONS], record)


def store_takes(cur, agent_id: str, season: int, week: int, article_id: int, takes: list, team_index: list) -> int:
    n = 0
    for t in takes:
        team_id = resolve_team(t["team"], team_index) if t.get("team") else None
        player_id = None
        if t.get("player"):
            cur.execute("SELECT id FROM players WHERE full_name ILIKE %s ORDER BY (origin IS NOT NULL), id LIMIT 1;",
                        (t["player"],))
            r = cur.fetchone()
            player_id = r[0] if r else None
        cur.execute("""INSERT INTO agent_memory_entries (agent_id, season, week, article_id, kind, text, team_id, player_id)
                       VALUES (%s, %s, %s, %s, 'TAKE', %s, %s, %s);""",
                    (agent_id, season, week, article_id, t["take"], team_id, player_id))
        n += 1
    return n


def store_tweet_take(cur, agent_id: str, season: int, week: int, article_id: int, text: str, team_id=None) -> None:
    """A tweet is remembered as-is (no extraction call)."""
    cur.execute("""INSERT INTO agent_memory_entries (agent_id, season, week, article_id, kind, text, team_id)
                   VALUES (%s, %s, %s, %s, 'TAKE', %s, %s);""", (agent_id, season, week, article_id, (text or "")[:TAKE_MAX_CHARS], team_id))


def agents_due_for_summary(cur, season: int, week: int) -> list:
    """[(agent_id, last_through_week)] for agents with SUMMARY_MIN_NEW_TAKES+ takes after their last summary,
    counting takes through week - 1."""
    cur.execute("""
        WITH last AS (SELECT agent_id, MAX(through_week) AS tw FROM agent_season_summaries
                      WHERE season = %s AND through_week < %s GROUP BY agent_id)
        SELECT e.agent_id, COALESCE(l.tw, 0)
        FROM agent_memory_entries e LEFT JOIN last l ON l.agent_id = e.agent_id
        WHERE e.season = %s AND e.week <= %s AND e.week > COALESCE(l.tw, 0) AND e.kind = 'TAKE'
        GROUP BY e.agent_id, l.tw HAVING COUNT(*) >= %s;
    """, (season, week, season, week - 1, SUMMARY_MIN_NEW_TAKES))
    return cur.fetchall()


def summary_inputs(cur, agent_id: str, season: int, week: int, last_tw: int) -> dict:
    """Everything the summary prompt needs: name, identity, previous summary, new takes, newly graded picks."""
    cur.execute("SELECT COALESCE(display_name, agent_id), COALESCE(persistent_memory, '') FROM agent_memories WHERE agent_id = %s;", (agent_id,))
    name, identity = cur.fetchone() or (agent_id, "")
    cur.execute("SELECT summary FROM agent_season_summaries WHERE agent_id = %s AND season = %s AND through_week = %s;",
                (agent_id, season, last_tw))
    prev = cur.fetchone()
    cur.execute("""SELECT week, text FROM agent_memory_entries WHERE agent_id = %s AND season = %s
                   AND week > %s AND week < %s AND kind = 'TAKE' ORDER BY week, id;""", (agent_id, season, last_tw, week))
    takes = [{"week": w, "text": t} for w, t in cur.fetchall()]
    preds = [p for p in fetch_predictions(cur, agent_id, season, week) if p["week"] > last_tw]
    return {"name": name, "identity": identity, "previous": prev[0] if prev else "", "takes": takes, "predictions": preds}


def store_summary(cur, agent_id: str, season: int, through_week: int, summary: str) -> None:
    cur.execute("""INSERT INTO agent_season_summaries (agent_id, season, through_week, summary)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (agent_id, season, through_week) DO UPDATE SET summary = EXCLUDED.summary, updated_at = NOW();""",
                (agent_id, season, through_week, summary[:1500]))


def store_predictions(cur, agent_id: str, season: int, preview_week: int, article_id: int, preds: list) -> int:
    """Keeps picks that match a game scheduled in `preview_week`; returns how many were stored."""
    n = 0
    for p in preds:
        cur.execute("""SELECT id FROM games WHERE season = %s AND week = %s AND status <> 'BYE'
                       AND ((home_team_id = %s AND away_team_id = %s) OR (home_team_id = %s AND away_team_id = %s))
                       LIMIT 1;""", (season, preview_week, p["team_a"], p["team_b"], p["team_b"], p["team_a"]))
        row = cur.fetchone()
        if not row:
            continue
        cur.execute("""INSERT INTO agent_predictions (agent_id, season, week, article_id, game_id, label,
                                                      team_a_id, score_a, team_b_id, score_b)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (article_id, game_id, label) DO NOTHING;""",
                    (agent_id, season, preview_week, article_id, row[0], p["label"], p["team_a"], p["score_a"], p["team_b"], p["score_b"]))
        n += cur.rowcount
    return n


# ---------------------------------------------------------------- model calls

def gemini_text(api_key: str, model: str, prompt: str, label: str = "") -> str:
    from google import genai
    from google.genai import types
    from db.llm_usage import log_response
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=model, contents=prompt,
                                          config=types.GenerateContentConfig(temperature=0.3))
    log_response("agent_memory", label, model, resp)
    return (resp.text or "").strip()


def refresh_season_summaries(conn_info: str, api_key: str, model: str, season: int, week: int, max_workers: int = 5) -> int:
    """Before publishing `week`: rewrite the season summary (through week - 1) of every agent with at least
    SUMMARY_MIN_NEW_TAKES takes since its last summary. Returns the number of summaries written."""
    import psycopg
    from concurrent.futures import ThreadPoolExecutor
    through = week - 1
    if through < 1 or not api_key:
        return 0
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        if not tables_exist(cur):
            return 0
        due = agents_due_for_summary(cur, season, week)

    def work(item):
        agent_id, last_tw = item
        try:
            with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
                inp = summary_inputs(cur, agent_id, season, week, last_tw)
                text = gemini_text(api_key, model, summary_prompt(inp["name"], inp["identity"], inp["previous"],
                                                                  inp["takes"], inp["predictions"], through),
                                   label=f"summary {agent_id}")
                if not text:
                    return 0
                store_summary(cur, agent_id, season, through, text)
                conn.commit()
                return 1
        except Exception as e:
            print(f"⚠️  [MEMORY SUMMARY FAILED] {agent_id}: {e}")
            return 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return sum(ex.map(work, due))
