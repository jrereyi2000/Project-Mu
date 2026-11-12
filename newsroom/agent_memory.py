"""Evolving agent memory: takes log, prediction track record and weekly season summaries (PROJECT_SPEC §7.7).

Each agent keeps its hand-written identity line (agent_memories.persistent_memory). On top of it:
  - Takes (agent_memory_entries): after a live article is saved, one small model call extracts up to 3 stances the
    writer committed to; a tweet is stored as a take as-is (no extra call).
  - Predictions (agent_predictions): "Projected Final: Team N, Team N" lines (and roundtable panellist lines) in
    preview articles are parsed into per-game score picks and graded against final scores when a prompt is built.
  - Summaries (agent_season_summaries): at the start of each weekly run, agents with enough new takes get their
    "season so far" rewritten (one call each), stored per (agent, season, through_week).
  - Picks from posts (agent_predictions.pick_type 'WINNER': winner only, scores NULL) are graded by winner.
  - Season calls (agent_season_calls): MVP, OPOY, DPOY, ROY, Super Bowl winner, playoff team, division winner, coach
    fired; graded by grade_season_calls from awards, the Super Bowl, final standings and coach_hot_seat.
Memory is per person (agent_memories.person_key, newsroom/relationships.person_agent_ids): reads cover every account
of the person; writes stay per account; a summary is stored once per person under the person's first account.
Prompts read only memories from before the article's publication week, so regenerating an old week can't see later
takes, results or summaries.
"""
import json
import os
import re

from newsroom.power_rankings import resolve_team
from newsroom.relationships import has_person_key, person_agent_ids

MAX_TAKES_PER_ARTICLE = 3
RECENT_TAKES = 6
SUBJECT_TAKES = 3
RECENT_PREDICTIONS = 5
PRESS_SHOWN = 3
OPEN_CALLS_SHOWN = 5
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
    """Adds 'result' ('correct' | 'wrong' | 'push' | 'pending') from actual scores (actual_a / actual_b or None).
    WINNER picks (pick_type 'WINNER', no scores) are graded by winner only: winner_team_id against team_a_id."""
    if p.get("actual_a") is None or p.get("actual_b") is None:
        return {**p, "result": "pending"}
    if p.get("pick_type") == "WINNER":
        w = p.get("winner_team_id")
        if w is None or w not in (p.get("team_a_id"), p.get("team_b_id")):
            return {**p, "result": "pending"}
        picked = 1 if w == p.get("team_a_id") else -1
    elif p.get("score_a") is None or p.get("score_b") is None:
        return {**p, "result": "pending"}
    else:
        picked = (p["score_a"] > p["score_b"]) - (p["score_a"] < p["score_b"])
    actual = (p["actual_a"] > p["actual_b"]) - (p["actual_a"] < p["actual_b"])
    if picked == 0 or actual == 0:
        return {**p, "result": "push"}
    return {**p, "result": "correct" if picked == actual else "wrong"}


def _pick_text(p: dict) -> str:
    """'you predicted A 27, B 24' for score picks; 'you picked the A to beat the B' for winner picks."""
    if p.get("pick_type") == "WINNER":
        won, lost = ((p["name_a"], p["name_b"]) if p.get("winner_team_id") == p.get("team_a_id")
                     else (p["name_b"], p["name_a"]))
        return f"you picked the {won} to beat the {lost}"
    return f"you predicted {p['name_a']} {p['score_a']}, {p['name_b']} {p['score_b']}"


CALL_LABELS = {"MVP": "MVP", "OPOY": "Offensive Player of the Year", "DPOY": "Defensive Player of the Year",
               "ROY": "Rookie of the Year", "SUPER_BOWL": "Super Bowl winner", "PLAYOFF_TEAM": "makes the playoffs",
               "MISSES_PLAYOFFS": "misses the playoffs", "DIVISION_WINNER": "wins the division",
               "COACH_FIRED": "coach gets fired"}


def _call_text(c: dict) -> str:
    label = CALL_LABELS.get(c["call_type"], c["call_type"].replace("_", " ").lower())
    subject = c.get("subject_name") or "?"
    detail = f" ({c['detail']})" if c.get("detail") else ""
    return f"Week {c['made_week']}: {label}: {subject}{detail}"


def format_memory_block(summary: str, takes: list, subject_takes: list, predictions: list, record: tuple,
                        open_calls: list = None, graded_calls: list = None, press: list = None) -> str:
    """Prompt section appended under the identity line. Empty string when there is nothing to remember.
    open_calls / graded_calls: this season's season calls ({made_week, call_type, subject_name, detail, result}).
    press: this person's recent head-coach press conferences ({week, text}), never mixed with takes."""
    parts = []
    if summary:
        parts.append(f"YOUR SEASON SO FAR (your own running memory):\n{summary.strip()}")
    if takes:
        parts.append("YOUR RECENT TAKES (stay consistent with them, or say plainly that you've changed your mind):\n"
                     + "\n".join(f"- Week {t['week']}: {t['text']}" for t in takes))
    if subject_takes:
        parts.append("WHAT YOU'VE SAID BEFORE ABOUT THIS ARTICLE'S SUBJECT:\n"
                     + "\n".join(f"- Week {t['week']}: {t['text']}" for t in subject_takes))
    if press:
        parts.append("YOUR RECENT PRESS CONFERENCES (you were in the room):\n"
                     + "\n".join(f"- Week {p['week']}: {p['text']}" for p in press))
    graded = [p for p in predictions if p["result"] != "pending"]
    if graded or any(record):
        wins, losses = record
        lines = [f"YOUR PREDICTION TRACK RECORD: {wins}-{losses} picking winners this season."]
        for p in graded:
            verdict = {"correct": "you were right", "wrong": "you were WRONG", "push": "a tie"}[p["result"]]
            lines.append(f"- Week {p['week']}{(' (' + p['label'] + ')') if p.get('label') else ''}: {_pick_text(p)}; "
                         f"final was {p['name_a']} {p['actual_a']}, {p['name_b']} {p['actual_b']}: {verdict}.")
        lines.append("Own your misses in your own voice when they're relevant; don't pretend they didn't happen.")
        parts.append("\n".join(lines))
    if open_calls:
        parts.append("YOUR OPEN SEASON CALLS (graded at season's end; double down or hedge, but don't pretend you "
                     "never made them):\n" + "\n".join(f"- {_call_text(c)}" for c in open_calls[:OPEN_CALLS_SHOWN]))
    if graded_calls:
        right = sum(c["result"] == "RIGHT" for c in graded_calls)
        wrong = sum(c["result"] == "WRONG" for c in graded_calls)
        lines = [f"YOUR SEASON CALL RECORD: {right} right, {wrong} wrong this season."]
        for c in graded_calls[:OPEN_CALLS_SHOWN]:
            lines.append(f"- {_call_text(c)}: {'you were right' if c['result'] == 'RIGHT' else 'you were WRONG'}.")
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
        f"- Week {p['week']}: {_pick_text(p)}; actual {p['name_a']} {p['actual_a']}-{p['actual_b']} {p['name_b']} "
        f"({p['result']})" for p in predictions if p["result"] != "pending") or "- (none)"
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


def fetch_predictions(cur, agent_id, season: int, before_week: int, limit: int = None) -> list:
    """Picks made before `before_week`, graded. agent_id is one account or a list of a person's accounts."""
    ids = list(agent_id) if isinstance(agent_id, (list, tuple)) else [agent_id]
    cur.execute("""
        SELECT p.week, p.label, p.team_a_id, p.score_a, p.team_b_id, p.score_b,
               CASE WHEN g.status = 'COMPLETED' AND g.week < %s THEN
                    CASE WHEN g.home_team_id = p.team_a_id THEN g.home_score ELSE g.away_score END END,
               CASE WHEN g.status = 'COMPLETED' AND g.week < %s THEN
                    CASE WHEN g.home_team_id = p.team_b_id THEN g.home_score ELSE g.away_score END END,
               p.pick_type, p.winner_team_id
        FROM agent_predictions p
        JOIN games g ON g.id = p.game_id
        WHERE p.agent_id = ANY(%s) AND p.season = %s AND p.week < %s
        ORDER BY p.week DESC, p.id DESC
    """, (before_week, before_week, ids, season, before_week))
    pred_rows = cur.fetchall()   # read before _team_names reuses the cursor
    names = _team_names(cur)
    rows = []
    for r in pred_rows:
        week, label, a, sa, b, sb, aa, ab = r[:8]
        pick_type, winner = (r[8], r[9]) if len(r) > 9 else ("SCORE", None)
        rows.append(grade_prediction({"week": week, "label": label, "score_a": sa, "score_b": sb, "actual_a": aa,
                                      "actual_b": ab, "name_a": names.get(a, str(a)), "name_b": names.get(b, str(b)),
                                      "team_a_id": a, "team_b_id": b, "pick_type": pick_type or "SCORE",
                                      "winner_team_id": winner}))
    return rows[:limit] if limit else rows


def calls_table_exists(cur) -> bool:
    cur.execute("SELECT to_regclass('public.agent_season_calls') IS NOT NULL;")
    row = cur.fetchone()
    return bool(row and row[0])


def fetch_season_calls(cur, agent_ids: list, season: int, before_week: int) -> tuple:
    """(open calls, graded calls) of a person's accounts for `season`, from calls made before `before_week`. A call
    counts as graded only when it was graded before `before_week` (no peeking at later results)."""
    cur.execute("""
        SELECT c.made_week, c.call_type, c.detail, c.result, c.graded_week,
               CASE c.subject_type
                    WHEN 'player' THEN (SELECT COALESCE(full_name, player_name) FROM players WHERE id = c.subject_id)
                    WHEN 'coach' THEN (SELECT coach_name FROM coaches WHERE id = c.subject_id)
                    WHEN 'team' THEN (SELECT city || ' ' || name FROM teams WHERE id = c.subject_id) END
        FROM agent_season_calls c
        WHERE c.agent_id = ANY(%s) AND c.season = %s AND c.made_week < %s
        ORDER BY c.made_week DESC, c.id DESC;""", (list(agent_ids), season, before_week))
    open_calls, graded = [], []
    for made, ctype, detail, result, gw, name in cur.fetchall():
        c = {"made_week": made, "call_type": ctype, "detail": detail, "result": result, "subject_name": name}
        if result in ("RIGHT", "WRONG") and gw is not None and gw < before_week:
            graded.append(c)
        elif result != "VOID":
            open_calls.append({**c, "result": "PENDING"})
    return open_calls, graded


def build_memory_block(cur, agent_id: str, season: int, week: int, subject_team_id: int = None) -> str:
    """Memory section for an agent's prompt, from memories recorded before `week` of `season`."""
    if not agent_id or not tables_exist(cur):
        return ""
    ids = person_agent_ids(cur, agent_id)
    cur.execute("""SELECT summary FROM agent_season_summaries
                   WHERE agent_id = ANY(%s) AND season = %s AND through_week < %s
                   ORDER BY through_week DESC LIMIT 1;""", (ids, season, week))
    row = cur.fetchone()
    summary = row[0] if row else ""
    cur.execute("""SELECT week, text FROM agent_memory_entries
                   WHERE agent_id = ANY(%s) AND season = %s AND week < %s AND kind = 'TAKE'
                   ORDER BY week DESC, id DESC LIMIT %s;""", (ids, season, week, RECENT_TAKES))
    takes = [{"week": w, "text": t} for w, t in cur.fetchall()]
    subject_takes = []
    if subject_team_id:
        cur.execute("""SELECT week, text FROM agent_memory_entries
                       WHERE agent_id = ANY(%s) AND season = %s AND week < %s AND kind = 'TAKE' AND team_id = %s
                       ORDER BY week DESC, id DESC LIMIT %s;""",
                    (ids, season, week, subject_team_id, SUBJECT_TAKES + RECENT_TAKES))
        recent = {(t["week"], t["text"]) for t in takes}
        subject_takes = [{"week": w, "text": t} for w, t in cur.fetchall() if (w, t) not in recent][:SUBJECT_TAKES]
    # Press memories (press_room.close_effects) are kind = 'PRESS' rows, so they never mix with takes. Only read
    # them when the press tables are installed: without them old behaviour stands (no section, no error).
    press = []
    cur.execute("SELECT to_regclass('public.press_conferences') IS NOT NULL AS press_installed;")
    press_row = cur.fetchone()
    installed = (press_row.get("press_installed") if isinstance(press_row, dict)
                 else (press_row[0] if press_row else False))
    if installed:
        cur.execute("""SELECT week, text FROM agent_memory_entries
                       WHERE agent_id = ANY(%s) AND season = %s AND week < %s AND kind = 'PRESS'
                       ORDER BY week DESC, id DESC LIMIT %s;""", (ids, season, week, PRESS_SHOWN))
        press = [{"week": w, "text": t} for w, t in cur.fetchall()]
    preds = fetch_predictions(cur, ids, season, week)
    record = (sum(p["result"] == "correct" for p in preds), sum(p["result"] == "wrong" for p in preds))
    open_calls, graded_calls = fetch_season_calls(cur, ids, season, week) if calls_table_exists(cur) else ([], [])
    return format_memory_block(summary, takes, subject_takes, preds[:RECENT_PREDICTIONS], record,
                               open_calls, graded_calls, press)


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
    """[(agent_id, last_through_week)] for people with SUMMARY_MIN_NEW_TAKES+ takes (across all their accounts) after
    their last summary, counting takes through week - 1. agent_id is the person's first account (lowest
    agent_memories.id), where the person's summary is stored."""
    pk = "COALESCE(person_key, agent_id)" if has_person_key(cur) else "agent_id"
    cur.execute(f"""
        WITH acct AS (SELECT agent_id, FIRST_VALUE(agent_id) OVER (PARTITION BY {pk} ORDER BY id) AS owner
                      FROM agent_memories),
             last AS (SELECT agent_id, MAX(through_week) AS tw FROM agent_season_summaries
                      WHERE season = %s AND through_week < %s GROUP BY agent_id)
        SELECT COALESCE(a.owner, e.agent_id) AS owner, COALESCE(l.tw, 0)
        FROM agent_memory_entries e
        LEFT JOIN acct a ON a.agent_id = e.agent_id
        LEFT JOIN last l ON l.agent_id = COALESCE(a.owner, e.agent_id)
        WHERE e.season = %s AND e.week <= %s AND e.week > COALESCE(l.tw, 0) AND e.kind = 'TAKE'
        GROUP BY 1, l.tw HAVING COUNT(*) >= %s;
    """, (season, week, season, week - 1, SUMMARY_MIN_NEW_TAKES))
    return cur.fetchall()


def summary_inputs(cur, agent_id: str, season: int, week: int, last_tw: int) -> dict:
    """Everything the summary prompt needs: name, identity, previous summary, new takes, newly graded picks.
    agent_id is the person's first account; takes and picks come from all the person's accounts."""
    ids = person_agent_ids(cur, agent_id)
    cur.execute("SELECT COALESCE(display_name, agent_id), COALESCE(persistent_memory, '') FROM agent_memories WHERE agent_id = %s;", (agent_id,))
    name, identity = cur.fetchone() or (agent_id, "")
    cur.execute("""SELECT summary FROM agent_season_summaries WHERE agent_id = ANY(%s) AND season = %s AND through_week = %s
                   ORDER BY (agent_id = %s) DESC LIMIT 1;""", (ids, season, last_tw, agent_id))
    prev = cur.fetchone()
    cur.execute("""SELECT week, text FROM agent_memory_entries WHERE agent_id = ANY(%s) AND season = %s
                   AND week > %s AND week < %s AND kind = 'TAKE' ORDER BY week, id;""", (ids, season, last_tw, week))
    takes = [{"week": w, "text": t} for w, t in cur.fetchall()]
    preds = [p for p in fetch_predictions(cur, ids, season, week) if p["week"] > last_tw]
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


# ---------------------------------------------------------------- season calls

AWARD_CALLS = {"MVP": ("MVP",), "OPOY": ("OPOY",), "DPOY": ("DPOY",), "ROY": ("OROY", "DROY")}
CALL_TYPES = tuple(AWARD_CALLS) + ("SUPER_BOWL", "PLAYOFF_TEAM", "MISSES_PLAYOFFS", "DIVISION_WINNER", "COACH_FIRED")
REGULAR_SEASON_FINAL_WEEK = 18
PLAYOFF_FIELD = 14          # 12 wild-card teams + 2 byes: the field is known once the divisional round is scheduled
NON_POSTSEASON = ("REGULAR_SEASON", "PRESEASON", "PRO_BOWL")


def grade_call(call: dict, facts: dict):
    """Pure: 'RIGHT' | 'WRONG' | 'VOID' | None (not decided yet) for one season call.
    call: {call_type, subject_type, subject_id}; facts (missing key = not decided): awards {MVP: {player ids}, ...,
    ROY: {...}}, champion team id, playoff_teams set, division_winners set, division_undecided set (teams whose
    division has no clear winner yet), fired_teams / fired_coaches sets, season_final bool."""
    ctype, stype, sid = call.get("call_type"), call.get("subject_type"), call.get("subject_id")
    if ctype not in CALL_TYPES:
        return "VOID"
    if ctype in AWARD_CALLS:
        winners = (facts.get("awards") or {}).get(ctype)
        if not winners:
            return None
        if stype != "player" or sid is None:
            return "VOID"
        return "RIGHT" if sid in winners else "WRONG"
    if ctype == "COACH_FIRED":
        if stype not in ("team", "coach") or sid is None:
            return "VOID"
        fired = facts.get("fired_teams" if stype == "team" else "fired_coaches") or set()
        if sid in fired:
            return "RIGHT"
        return "WRONG" if facts.get("season_final") else None
    if stype != "team" or sid is None:
        return "VOID" if facts.get("season_final") else None
    if ctype == "SUPER_BOWL":
        champ = facts.get("champion")
        return None if champ is None else ("RIGHT" if sid == champ else "WRONG")
    if ctype in ("PLAYOFF_TEAM", "MISSES_PLAYOFFS"):
        field = facts.get("playoff_teams")
        if field is None:
            return None
        made = sid in field
        return "RIGHT" if made == (ctype == "PLAYOFF_TEAM") else "WRONG"
    if ctype == "DIVISION_WINNER":
        winners = facts.get("division_winners")
        if winners is None or sid in (facts.get("division_undecided") or set()):
            return None
        return "RIGHT" if sid in winners else "WRONG"
    return None


def _regclass(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (f"public.{table}",))
    row = cur.fetchone()
    return bool(row and row[0])


def season_call_facts(cur, season: int) -> dict:
    """Stored outcomes for grade_call: awards, the Super Bowl, final standings (or the playoff field and division
    leaders once the regular season is over) and coach_hot_seat firings. Only what is already decided."""
    facts = {}
    if _regclass(cur, "awards"):
        cur.execute("SELECT award_type, player_id FROM awards WHERE season = %s AND player_id IS NOT NULL "
                    "AND award_type IN ('MVP', 'OPOY', 'DPOY', 'OROY', 'DROY');", (season,))
        by_type = {}
        for t, pid in cur.fetchall():
            by_type.setdefault(t, set()).add(pid)
        facts["awards"] = {call: set().union(*(by_type[t] for t in types if t in by_type))
                           for call, types in AWARD_CALLS.items() if any(t in by_type for t in types)}
    final = False
    if _regclass(cur, "league_seasons"):
        cur.execute("SELECT champion_team_id FROM league_seasons WHERE season = %s;", (season,))
        row = cur.fetchone()
        if row:
            final = True
            if row[0] is not None:
                facts["champion"] = row[0]
    facts["season_final"] = final
    if "champion" not in facts:
        cur.execute("""SELECT CASE WHEN home_score > away_score THEN home_team_id
                                   WHEN away_score > home_score THEN away_team_id END
                       FROM games WHERE season = %s AND game_type = 'SUPER_BOWL' AND status = 'COMPLETED'
                       ORDER BY week DESC LIMIT 1;""", (season,))
        row = cur.fetchone()
        if row and row[0] is not None:
            facts["champion"] = row[0]
    results = []
    if _regclass(cur, "team_season_results"):
        cur.execute("SELECT team_id, division_place, conference_seed FROM team_season_results WHERE season = %s;",
                    (season,))
        results = cur.fetchall()
    if results:
        facts["playoff_teams"] = {t for t, _, seed in results if seed is not None}
        facts["division_winners"] = {t for t, place, _ in results if place == 1}
    else:
        cur.execute(f"""SELECT DISTINCT unnest(ARRAY[home_team_id, away_team_id]) FROM games
                        WHERE season = %s AND COALESCE(game_type, '') NOT IN {NON_POSTSEASON!r}
                          AND status <> 'BYE';""", (season,))
        field = {r[0] for r in cur.fetchall() if r[0] is not None}
        if len(field) >= PLAYOFF_FIELD:
            facts["playoff_teams"] = field
        cur.execute("""SELECT MAX(week) FILTER (WHERE status = 'COMPLETED'), COUNT(*) FILTER (WHERE status = 'SCHEDULED')
                       FROM games WHERE season = %s AND game_type = 'REGULAR_SEASON';""", (season,))
        last_week, left = cur.fetchone() or (None, 0)
        if last_week is not None and last_week >= REGULAR_SEASON_FINAL_WEEK and not left:
            cur.execute("""SELECT t.id, t.division, ts.wins, ts.losses FROM teams t
                           JOIN team_seasons ts ON ts.team_id = t.id AND ts.season = %s;""", (season,))
            divisions = {}
            for tid, div, w, l in cur.fetchall():
                divisions.setdefault(div, []).append((tid, (w or 0) - (l or 0)))
            winners, undecided = set(), set()
            for teams in divisions.values():
                best = max(m for _, m in teams)
                top = [t for t, m in teams if m == best]
                if len(top) == 1:
                    winners.add(top[0])
                else:
                    undecided |= {t for t, _ in teams}
            facts["division_winners"], facts["division_undecided"] = winners, undecided
    facts["fired_teams"], facts["fired_coaches"] = set(), set()
    if _regclass(cur, "coach_hot_seat"):
        cur.execute("SELECT team_id, coach_id FROM coach_hot_seat WHERE season = %s AND fired;", (season,))
        for tid, cid in cur.fetchall():
            facts["fired_teams"].add(tid)
            facts["fired_coaches"].add(cid)
    return facts


def grade_season_calls(cur, season: int) -> int:
    """Grades the PENDING season calls of `season` that stored data can now decide (awards: MVP, OPOY, DPOY, ROY =
    OROY or DROY; the Super Bowl; the playoff field and division winners; coach_hot_seat firings). graded_week = the
    latest completed week of the season. Never commits. Returns the number of calls graded."""
    if not calls_table_exists(cur):
        return 0
    cur.execute("""SELECT id, call_type, subject_type, subject_id FROM agent_season_calls
                   WHERE season = %s AND result = 'PENDING';""", (season,))
    pending = cur.fetchall()
    if not pending:
        return 0
    facts = season_call_facts(cur, season)
    cur.execute("SELECT MAX(week) FROM games WHERE season = %s AND status = 'COMPLETED';", (season,))
    row = cur.fetchone()
    graded_week = row[0] if row else None
    n = 0
    for cid, ctype, stype, sid in pending:
        result = grade_call({"call_type": ctype, "subject_type": stype, "subject_id": sid}, facts)
        if result is None:
            continue
        cur.execute("UPDATE agent_season_calls SET result = %s, graded_week = %s WHERE id = %s AND result = 'PENDING';",
                    (result, graded_week, cid))
        n += 1
    return n


def _model_prefix(model: str | None) -> str | None:
    """The provider a ``go:``/``gemini:``/``ollama:`` model prefix names, or None."""
    m = (model or "").strip().lower()
    for name in ("ollama", "gemini", "go"):
        if m.startswith((f"{name}:", f"{name}/")):
            return name
    return None


def _env_provider(label: str = "", source: str = "") -> str | None:
    """The provider ``LLM_PROVIDER_<SOURCE>``, ``LLM_PROVIDER_<LABEL>`` or ``LLM_PROVIDER`` names, or None.

    The same keys, in the same order, ``engines.llm_provider.resolve_provider`` reads."""
    for candidate in (source, label):
        if candidate:
            key = f"LLM_PROVIDER_{candidate.strip().lower().replace(' ', '_').upper()}"
            value = os.environ.get(key, "").strip().lower()
            if value in ("gemini", "ollama", "go"):
                return value
    value = os.environ.get("LLM_PROVIDER", "").strip().lower()
    return value if value in ("gemini", "ollama", "go") else None


def model_provider(model: str | None = None, provider: str | None = None,
                   label: str = "", source: str = "agent_memory") -> str:
    """The provider a text call reaches (GLM_5_3_UNIFICATION_PLAN §2.3).

    ``engines.llm_provider.resolve_provider`` decides first, so an explicit ``provider``, a
    ``go:``/``gemini:``/``ollama:`` model prefix, ``LLM_PROVIDER_<SOURCE|LABEL>`` and ``LLM_PROVIDER`` all win as
    they always did. Only when nothing named a provider and the answer is just the Gemini default, a model id
    OpenCode Go serves (``newsroom.tool_loop.go_transport.is_go_model``: anything that is not a ``gemini-*`` id)
    is a Go call. So ``AGENT_MEMORY_MODEL=glm-5.3-flash``, ``PRESS_MODEL=glm-5.3-flash`` or
    ``INBOX_MODEL=glm-5.3-flash`` reaches Go without also setting that site's ``LLM_PROVIDER_*``, and every
    site's unset default stays Gemini.
    """
    from engines.llm_provider import resolve_provider
    prov = resolve_provider(label=label, source=source, model=model, provider=provider)
    named = str(provider or "").strip().lower() in ("gemini", "ollama", "go")
    if prov != "gemini" or named or _model_prefix(model) or _env_provider(label, source):
        return prov
    from newsroom.tool_loop.go_transport import is_go_model
    return "go" if is_go_model(model) else prov


def summaries_enabled(api_key: str | None, model: str) -> bool:
    """The ``refresh_season_summaries`` gate: a Gemini run needs its API key, exactly as before; a Go run needs
    ``OPENCODE_GO_API_KEY`` instead, so a Go model is never skipped just because ``GEMINI_API_KEY`` is unset."""
    if api_key:
        return True
    if model_provider(model=model, label="summary", source="agent_memory") != "go":
        return False
    from newsroom.tool_loop.go_transport import go_api_key
    return bool(go_api_key())


def llm_text(prompt: str, label: str = "agent_memory", model: str | None = None,
             thinking_level: str | None = None, api_key: str | None = None,
             client=None, provider: str | None = None, fallback_model: str | None = None) -> str:
    """Provider-neutral text helper for agent memory, supporting Ollama, Gemini and OpenCode Go with fallback."""
    from engines.llm_provider import generate_text
    return generate_text(
        prompt=prompt,
        source="agent_memory",
        label=label,
        model=model,
        provider=model_provider(model=model, provider=provider, label=label or "agent_memory"),
        fallback_model=fallback_model,
        thinking_level=thinking_level,
        temperature=0.3,
        client=client,
        api_key=api_key,
    )


def gemini_text(api_key: str, model: str, prompt: str, label: str = "",
                thinking_level: str | None = None, client=None, provider: str | None = None) -> str:
    """Provider-aware text helper for agent memory, maintaining backwards-compatible signature.

    ``provider`` is a passthrough, so a Go model reaches OpenCode Go through
    ``generate_text(..., provider="go")`` with the same prompt and the same 0.3 temperature as the Gemini call."""
    return llm_text(
        prompt=prompt,
        label=label or "agent_memory",
        model=model,
        provider=provider,
        thinking_level=thinking_level,
        api_key=api_key,
        client=client,
    )


def refresh_season_summaries(conn_info: str, api_key: str, model: str, season: int, week: int, max_workers: int = 5) -> int:
    """Before publishing `week`: rewrite the season summary (through week - 1) of every agent with at least
    SUMMARY_MIN_NEW_TAKES takes since its last summary. Returns the number of summaries written."""
    import psycopg
    from concurrent.futures import ThreadPoolExecutor
    through = week - 1
    if through < 1 or not summaries_enabled(api_key, model):
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
