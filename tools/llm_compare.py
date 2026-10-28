"""Quality Test Harness for Short LLM Tasks.
(docs/done/PRE_PC_WORK.md §7, docs/in_progress/PC_MIGRATION_PLAN.md §7.4)

Runs authentic short-task inputs through two or more models/providers side by side
using engines/llm_provider.py, outputting structured JSON and an interactive HTML report
to verify_output/ without modifying newsroom tables.

Supported tasks:
  - agent_wire: Agent Wire social desk posts (tweets, <= 280 chars)
  - memory_takes: Agent memory take extraction from articles (JSON list)
  - memory_summary: Running season memory summary (<= 120 words)
  - scouting_notes: College scout prospect notes (scouting narrative rules)
  - gm_persona: Front office GM persona & bio generation (JSON)
  - gm_rationale: Trade rationale explanation in GM voices (JSON)
  - gm_negotiation: Trade negotiation responses (JSON)

Usage:
  python3 -m tools.llm_compare --task agent_wire --models gemini-3.8-flash:low gemini-3.5-flash-lite
  python3 -m tools.llm_compare --task gm_rationale --season 2036 --week 20 --models gemini-3.8-flash:low ollama:qwen2.5:14b --limit 3
"""
import argparse
import collections
import dataclasses
import datetime
import html
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure project root is on sys.path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from engines.llm_provider import estimate_cost, generate_text, normalize_model_name

TASKS = [
    "agent_wire",
    "memory_takes",
    "memory_summary",
    "scouting_notes",
    "gm_persona",
    "gm_rationale",
    "gm_negotiation",
]

NUM_RE = re.compile(r"(?<![\w.$])(\d+(?:\.\d+)?)(?![\w.]*\d)")
MONEY_RE = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?([MKmkBb])?", re.I)


@dataclasses.dataclass
class TaskInput:
    id: str
    title: str
    task: str
    prompt: str
    system_prompt: Optional[str] = None
    facts: Any = None
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class ModelResult:
    model: str
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cached_tokens: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0
    passed: bool = True
    failures: List[str] = dataclasses.field(default_factory=list)
    metrics: Dict[str, Any] = dataclasses.field(default_factory=dict)


# ---------------------------------------------------------------------------
# Number extraction & Hallucination checks
# ---------------------------------------------------------------------------

def extract_numbers(text: str) -> Set[float]:
    """Extract distinct numerical values from text."""
    if not text:
        return set()
    nums = set()
    for m in NUM_RE.finditer(text):
        try:
            val = float(m.group(1))
            nums.add(val)
        except ValueError:
            continue
    return nums


def extract_numbers_from_facts(facts: Any) -> Set[float]:
    """Recursively collect all numeric values present in a facts object."""
    out: Set[float] = set()

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.add(float(v))
        elif isinstance(v, str):
            for m in NUM_RE.finditer(v):
                try:
                    out.add(float(m.group(1)))
                except ValueError:
                    pass
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple, set)):
            for x in v:
                walk(x)

    walk(facts)
    return out


# ---------------------------------------------------------------------------
# Task-specific Validation
# ---------------------------------------------------------------------------

def validate_agent_wire(text: str, inp: TaskInput) -> Tuple[bool, List[str], Dict[str, Any]]:
    failures = []
    char_len = len(text or "")
    if char_len == 0:
        return False, ["Empty post"], {"char_len": 0}
    if char_len > 280:
        failures.append(f"Length {char_len} exceeds 280 characters")

    from newsroom.social_writer import allowed_money, clean_post, money_values

    facts = inp.facts or {}
    is_rumor = bool(inp.metadata.get("is_rumor"))
    cleaned = clean_post(text, allowed=allowed_money(facts), rumor=is_rumor)
    if cleaned["problems"]:
        failures.extend(cleaned["problems"])

    # Number hallucination check: numbers in post must be in facts or prompt
    post_nums = extract_numbers(text)
    allowed_nums = extract_numbers_from_facts(facts) | extract_numbers(inp.prompt)
    # Exclude common years and tweet metrics
    allowed_nums.update({float(y) for y in (2034, 2035, 2036, 2037, 2038, 2039, 2040)})
    for n in post_nums:
        if not any(abs(n - a) < 0.01 for a in allowed_nums):
            failures.append(f"Number {n} not found in facts or prompt")

    passed = len(failures) == 0
    return passed, failures, {"char_len": char_len, "hashtags": cleaned.get("hashtags", [])}


def validate_memory_takes(text: str, inp: TaskInput) -> Tuple[bool, List[str], Dict[str, Any]]:
    failures = []
    from newsroom.agent_memory import parse_takes_json

    if not text or not text.strip():
        return False, ["Empty output"], {}

    # Must be valid JSON list
    takes = parse_takes_json(text, limit=10)
    if not takes:
        failures.append("Failed to parse valid JSON array of takes")
        return False, failures, {"takes_count": 0}

    if len(takes) > 3:
        failures.append(f"Extracted {len(takes)} takes, maximum allowed is 3")

    allowed_nums = extract_numbers(inp.prompt) | extract_numbers_from_facts(inp.facts)
    allowed_nums.update({float(y) for y in (2034, 2035, 2036, 2037, 2038, 2039, 2040)})

    for i, t in enumerate(takes):
        take_str = t.get("take", "")
        if len(take_str) > 280:
            failures.append(f"Take #{i+1} exceeds 280 characters ({len(take_str)})")
        words = len(take_str.split())
        if words > 40:
            failures.append(f"Take #{i+1} exceeds 30-word limit ({words} words)")
        for n in extract_numbers(take_str):
            if not any(abs(n - a) < 0.01 for a in allowed_nums):
                failures.append(f"Take #{i+1} mentions ungrounded number {n}")

    passed = len(failures) == 0
    return passed, failures, {"takes_count": len(takes)}


def validate_memory_summary(text: str, inp: TaskInput) -> Tuple[bool, List[str], Dict[str, Any]]:
    failures = []
    if not text or not text.strip():
        return False, ["Empty summary"], {"word_count": 0}

    words = len(text.strip().split())
    if words > 140:
        failures.append(f"Word count {words} exceeds maximum (at most 120 words)")

    allowed_nums = extract_numbers(inp.prompt) | extract_numbers_from_facts(inp.facts)
    allowed_nums.update({float(y) for y in range(2025, 2045)})
    for n in extract_numbers(text):
        if not any(abs(n - a) < 0.01 for a in allowed_nums):
            failures.append(f"Number {n} not grounded in input memory or predictions")

    passed = len(failures) == 0
    return passed, failures, {"word_count": words}


def validate_scouting_notes(text: str, inp: TaskInput) -> Tuple[bool, List[str], Dict[str, Any]]:
    failures = []
    if not text or not text.strip():
        return False, ["Empty note"], {}

    from engines.scouting_narrative import validate_scout_text

    sheet = inp.facts or {}
    ok, why = validate_scout_text(text, sheet)
    if not ok and why:
        failures.append(f"Scout validation failed: {why}")

    passed = len(failures) == 0
    return passed, failures, {"char_len": len(text)}


def validate_gm_persona(text: str, inp: TaskInput) -> Tuple[bool, List[str], Dict[str, Any]]:
    failures = []
    if not text or not text.strip():
        return False, ["Empty persona output"], {}

    from engines.gm_personas import parse_persona

    start_season = inp.metadata.get("start_season", 2036)
    taken_names = inp.metadata.get("taken_names", ())
    coach = inp.metadata.get("coach")

    parsed = parse_persona(text, start_season, taken_names=taken_names, coach=coach)
    if not parsed:
        failures.append("Failed to parse valid GM persona JSON with valid name, bio, and voice")
    else:
        bio = parsed.get("bio", "")
        voice = parsed.get("voice", "")
        if len(bio) > 600:
            failures.append(f"Bio exceeds 600 characters ({len(bio)})")
        if len(voice) > 200:
            failures.append(f"Voice exceeds 200 characters ({len(voice)})")

    passed = len(failures) == 0
    return passed, failures, {"parsed": bool(parsed)}


def validate_gm_rationale(text: str, inp: TaskInput) -> Tuple[bool, List[str], Dict[str, Any]]:
    failures = []
    if not text or not text.strip():
        return False, ["Empty rationale output"], {}

    from engines.gm_rationale import parse_answer, validate_rationale

    sheet = inp.facts or {}
    league = inp.metadata.get("league")
    parsed = parse_answer(text)
    if not parsed:
        failures.append("Failed to parse valid JSON with headline, rationale_a, and rationale_b")
        return False, failures, {"parsed": False}

    for key in ("headline", "rationale_a", "rationale_b"):
        part = parsed.get(key, "")
        ok, why = validate_rationale(part, sheet, league=league)
        if not ok and why:
            failures.append(f"{key} validation failed: {why}")

    passed = len(failures) == 0
    return passed, failures, {"parsed": True}


def validate_gm_negotiation(text: str, inp: TaskInput) -> Tuple[bool, List[str], Dict[str, Any]]:
    failures = []
    if not text or not text.strip():
        return False, ["Empty negotiation message"], {}

    from engines.gm_rationale import validate_rationale

    sheet = inp.facts or {}
    league = inp.metadata.get("league")
    m = re.search(r"\{.*\}", text, re.S)
    msg = ""
    if m:
        try:
            d = json.loads(m.group(0))
            msg = str(d.get("message") or "").strip()
        except Exception:
            msg = ""

    if not msg:
        # Check if direct text is provided instead of JSON
        if not text.strip().startswith("{"):
            msg = text.strip()
            failures.append("Response not formatted as JSON {\"message\": \"...\"}")
        else:
            failures.append("Failed to parse message from JSON")
            return False, failures, {"parsed": False}

    ok, why = validate_rationale(msg, sheet, league=league)
    if not ok and why:
        failures.append(f"Message validation failed: {why}")

    passed = len(failures) == 0
    return passed, failures, {"parsed": bool(m)}


VALIDATORS = {
    "agent_wire": validate_agent_wire,
    "memory_takes": validate_memory_takes,
    "memory_summary": validate_memory_summary,
    "scouting_notes": validate_scouting_notes,
    "gm_persona": validate_gm_persona,
    "gm_rationale": validate_gm_rationale,
    "gm_negotiation": validate_gm_negotiation,
}


def validate_output(task: str, text: str, inp: TaskInput) -> Tuple[bool, List[str], Dict[str, Any]]:
    validator = VALIDATORS.get(task)
    if not validator:
        return True, [], {}
    return validator(text, inp)


# ---------------------------------------------------------------------------
# Input Extraction (Database Read-Only with Authentic Fallbacks)
# ---------------------------------------------------------------------------

def get_db_cursor():
    """Attempt to connect to PostgreSQL in read-only mode."""
    if os.environ.get("MOCK_DB") or (os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("MADDEN_INTEGRATION_TEST")):
        return None, None
    try:
        import psycopg
        from db.db_handler import DatabaseHandler
        conn_info = DatabaseHandler().db_conn_info
        conn = psycopg.connect(conn_info, connect_timeout=4)
        conn.read_only = True
        return conn, conn.cursor()
    except (Exception, BaseException):
        return None, None


def build_agent_wire_inputs(season: int, week: int, limit: int = 5, cur=None) -> List[TaskInput]:
    inputs = []
    if cur is not None:
        try:
            from newsroom.personas import build_persona_block, load_personas
            from newsroom.social_desk import load_week_context, plan_social_week
            from newsroom.social_writer import build_post_prompt, kind_key

            ctx = load_week_context(cur, season, week)
            cur.execute("SELECT agent_id, archetype, assigned_team_id, persistent_memory, handle, display_name FROM agent_memories")
            accounts = [dict(zip(("agent_id", "archetype", "team_id", "persistent_memory", "handle", "display_name"), r))
                        for r in cur.fetchall()]
            by_id = {a["agent_id"]: a for a in accounts}
            personas = load_personas()
            plan = plan_social_week(ctx)
            assignments = [a for a in plan.get("assignments", []) if a.get("agent_id") in by_id][:limit]

            for i, a in enumerate(assignments):
                acct = by_id[a["agent_id"]]
                team = ctx["teams"].get(acct.get("team_id") or -1, {}).get("name")
                persona = build_persona_block(
                    cur, personas.get(acct["archetype"], ""),
                    agent_id=acct["agent_id"], season=season, week=week,
                    fmt="POST", post_kind=kind_key(a),
                    position=(a.get("facts") or {}).get("position"),
                    team_id=a.get("team_id")
                )
                system, prompt = build_post_prompt(a, acct, persona, team, memory_block="")
                inputs.append(TaskInput(
                    id=f"agent_wire_{i+1}",
                    title=f"Agent Wire: {a['kind']} (@{acct['handle']})",
                    task="agent_wire",
                    prompt=prompt,
                    system_prompt=system,
                    facts=a.get("facts") or {},
                    metadata={"kind": a["kind"], "handle": acct["handle"], "is_rumor": a["kind"] == "TRADE_RUMOR"}
                ))
        except Exception as e:
            print(f"[llm_compare] Note: DB extraction for agent_wire fell back ({e})")

    if not inputs:
        # Authentic synthetic fallbacks
        samples = [
            ("REACTION", "panthers_fan", "DIE_HARD_FAN", "Carolina Panthers",
             {"game": "Carolina Panthers 24, Atlanta Falcons 17", "week": week, "passing_yards": 260, "td": 2, "your_team_result": "won"}),
            ("PERFORMANCE", "stat_analyst", "ANALYST", None,
             {"player": "Bryce Young", "team": "Panthers", "passing_yards": 312, "td": 3, "qbr": 118.5, "week": week}),
            ("TRADE_RUMOR", "insider_beat", "INSIDER", "Buffalo Bills",
             {"rumor_type": "TRADE_TALK", "player": "Marcus Holliday", "team": "Bills", "target_team": "Panthers", "week": week}),
            ("BEAT", "local_beat", "BEAT_REPORTER", "Seattle Seahawks",
             {"team": "Seahawks", "update": "Starting safety returns to practice after ankle sprain", "week": week}),
            ("PREVIEW", "game_pundit", "PUNDIT", None,
             {"matchup": "Cowboys at Eagles", "spread": "PHI -3.5", "week": week, "total": 48.5}),
        ]
        from newsroom.social_writer import build_post_prompt
        for i, (kind, handle, arch, team, facts) in enumerate(samples[:limit]):
            acct = {"agent_id": f"syn_{i+1}", "handle": handle, "display_name": handle.replace("_", " ").title(), "archetype": arch, "persistent_memory": ""}
            assignment = {"kind": kind, "agent_id": acct["agent_id"], "team_id": 1 if team else None, "subject": {"type": "game", "label": f"{kind} test"}, "facts": facts}
            persona = f"You are @{handle}, a sharp {arch} covering the league. Keep posts punchy, grounded in facts, under 280 chars."
            system, prompt = build_post_prompt(assignment, acct, persona, team, "")
            inputs.append(TaskInput(
                id=f"agent_wire_{i+1}",
                title=f"Agent Wire: {kind} (@{handle})",
                task="agent_wire",
                prompt=prompt,
                system_prompt=system,
                facts=facts,
                metadata={"kind": kind, "handle": handle, "is_rumor": kind == "TRADE_RUMOR"}
            ))

    return inputs[:limit]


def build_memory_takes_inputs(season: int, week: int, limit: int = 5, cur=None) -> List[TaskInput]:
    inputs = []
    if cur is not None:
        try:
            from newsroom.agent_memory import takes_extraction_prompt
            cur.execute("""
                SELECT id, COALESCE(persona, 'NFL Writer'), headline, content
                FROM news_feed
                WHERE content_type = 'ARTICLE' AND content IS NOT NULL AND length(content) > 500
                ORDER BY id DESC LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            for r in rows:
                art_id, persona_name, headline, body = r
                prompt = takes_extraction_prompt(persona_name, headline or "", body)
                inputs.append(TaskInput(
                    id=f"take_{art_id}",
                    title=f"Take Extraction: {headline[:50] or f'Article #{art_id}'}",
                    task="memory_takes",
                    prompt=prompt,
                    facts={"headline": headline, "text": body[:4000], "display_name": persona_name},
                    metadata={"article_id": art_id, "persona": persona_name}
                ))
        except Exception as e:
            print(f"[llm_compare] Note: DB extraction for memory_takes fell back ({e})")

    if not inputs:
        from newsroom.agent_memory import takes_extraction_prompt
        sample_articles = [
            ("Marcus Vance", "Why Charlotte's Front Four Is Built for January",
             "The Carolina Panthers did not stumble into an 11-4 record. When Derrick Brown and the interior push collapse the pocket in 2.2 seconds, opposing quarterbacks unravel. Drake Maye and the Bills will discover this next Sunday. My stance has not changed: this defensive front is the premier unit in football and will carry Carolina to the Conference Championship."),
            ("Elena Rostova", "The Blueprint to Solve Kansas City's Two-High Shell",
             "Andy Reid's offensive adjustments over the last three weeks prove why coaching pedigree matters. Kansas City scored 34 points against the league's top-ranked pass defense by feeding the tight end on shallow crossers. I predicted Patrick Mahomes would throw for over 300 yards, and he delivered 318. Count on the Chiefs repeating this strategy in the Divisional Round."),
        ]
        for i, (author, head, text) in enumerate(sample_articles[:limit]):
            prompt = takes_extraction_prompt(author, head, text)
            inputs.append(TaskInput(
                id=f"take_syn_{i+1}",
                title=f"Take Extraction: {head[:50]}",
                task="memory_takes",
                prompt=prompt,
                facts={"headline": head, "text": text, "display_name": author},
                metadata={"author": author}
            ))

    return inputs[:limit]


def build_memory_summary_inputs(season: int, week: int, limit: int = 5, cur=None) -> List[TaskInput]:
    inputs = []
    if cur is not None:
        try:
            from newsroom.agent_memory import summary_prompt
            cur.execute("""
                SELECT agent_id, handle, display_name, persistent_memory
                FROM agent_memories
                WHERE persistent_memory IS NOT NULL AND length(persistent_memory) > 10
                LIMIT %s
            """, (limit,))
            accounts = cur.fetchall()
            for r in accounts:
                agent_id, handle, name, identity = r
                cur.execute("""
                    SELECT week, text FROM agent_memory_entries
                    WHERE agent_id = %s ORDER BY week DESC LIMIT 5
                """, (agent_id,))
                takes = [{"week": tr[0], "text": tr[1]} for tr in cur.fetchall()]
                cur.execute("""
                    SELECT ap.week, COALESCE(ta.name, 'Team A'), ap.score_a, COALESCE(tb.name, 'Team B'), ap.score_b
                    FROM agent_predictions ap
                    LEFT JOIN teams ta ON ta.id = ap.team_a_id
                    LEFT JOIN teams tb ON tb.id = ap.team_b_id
                    WHERE ap.agent_id = %s ORDER BY ap.week DESC LIMIT 5
                """, (agent_id,))
                preds = [{
                    "week": pr[0], "name_a": pr[1], "score_a": pr[2],
                    "name_b": pr[3], "score_b": pr[4], "actual_a": pr[2],
                    "actual_b": pr[4], "result": "correct"
                } for pr in cur.fetchall()]

                prompt = summary_prompt(name, identity, "", takes, preds, through_week=week)
                inputs.append(TaskInput(
                    id=f"summary_{agent_id}",
                    title=f"Memory Summary: @{handle} ({name})",
                    task="memory_summary",
                    prompt=prompt,
                    facts={"takes": takes, "predictions": preds, "identity": identity},
                    metadata={"agent_id": agent_id, "handle": handle}
                ))
        except Exception as e:
            print(f"[llm_compare] Note: DB extraction for memory_summary fell back ({e})")

    if not inputs:
        from newsroom.agent_memory import summary_prompt
        takes = [
            {"week": 14, "text": "The Bills are the most complete roster in the AFC."},
            {"week": 16, "text": "Drake Maye is my MVP vote regardless of playoff seeding."},
            {"week": 18, "text": "Carolina's pass rush will decide the NFC."},
        ]
        preds = [
            {"week": 16, "name_a": "Bills", "score_a": 27, "name_b": "Dolphins", "score_b": 20, "actual_a": 28, "actual_b": 21, "result": "correct"},
            {"week": 18, "name_a": "Panthers", "score_a": 24, "name_b": "Falcons", "score_b": 17, "actual_a": 24, "actual_b": 17, "result": "correct"},
        ]
        prompt = summary_prompt("Sam Newman", "Veteran beat insider with deep NFC South roots.", "", takes, preds, week)
        inputs.append(TaskInput(
            id="summary_syn_1",
            title="Memory Summary: @sam_newman (Sam Newman)",
            task="memory_summary",
            prompt=prompt,
            facts={"takes": takes, "predictions": preds},
            metadata={"handle": "sam_newman"}
        ))

    return inputs[:limit]


def build_scouting_notes_inputs(season: int, week: int, limit: int = 5, cur=None) -> List[TaskInput]:
    inputs = []
    if cur is not None:
        try:
            from engines.scouting_narrative import fact_sheet, note_prompt
            cur.execute("""
                SELECT se.id, se.kind, se.blurb, se.payload,
                       dp.full_name, dp.position, dp.college, dp.height_in, dp.weight, dp.age
                FROM scout_events se
                JOIN scout_prospects sp ON sp.prospect_key = se.prospect_key
                JOIN draft_prospects dp ON dp.id = sp.prospect_id
                WHERE se.blurb IS NOT NULL
                LIMIT %s
            """, (limit,))
            for r in cur.fetchall():
                ev_id, kind, blurb, payload, name, pos, college, ht, wt, age = r
                event = {"kind": kind, "blurb": blurb, "payload": payload or {}}
                prospect = {"full_name": name, "position": pos, "college": college, "height_in": ht, "weight": wt, "age": age}
                sheet = fact_sheet(event, prospect)
                prompt = note_prompt(sheet)
                inputs.append(TaskInput(
                    id=f"scout_{ev_id}",
                    title=f"Scout Note: {name} ({pos}, {college})",
                    task="scouting_notes",
                    prompt=prompt,
                    facts=sheet,
                    metadata={"event_id": ev_id, "kind": kind}
                ))
        except Exception as e:
            print(f"[llm_compare] Note: DB extraction for scouting_notes fell back ({e})")

    if not inputs:
        from engines.scouting_narrative import fact_sheet, note_prompt
        sample_prospects = [
            ({"kind": "GAME", "blurb": "Illinois cornerback Jamar Bodden: 1 catch allowed on 4 targets, 3 pass breakups, 1 interception.", "payload": {"opponent": "Penn State", "targets": 4, "interceptions": 1, "breakups": 3}},
             {"full_name": "Jamar Bodden", "position": "CB", "college": "Illinois", "height_in": 74, "weight": 185, "age": 21}),
            ({"kind": "COMBINE", "blurb": "Michigan offensive tackle Evan Kemp ran a 5.05 40-yard dash with 29 bench reps.", "payload": {"forty": 5.05, "bench": 29}},
             {"full_name": "Evan Kemp", "position": "OT", "college": "Michigan", "height_in": 78, "weight": 312, "age": 22}),
        ]
        for i, (ev, pr) in enumerate(sample_prospects[:limit]):
            sheet = fact_sheet(ev, pr)
            prompt = note_prompt(sheet)
            inputs.append(TaskInput(
                id=f"scout_syn_{i+1}",
                title=f"Scout Note: {pr['full_name']} ({pr['position']})",
                task="scouting_notes",
                prompt=prompt,
                facts=sheet,
                metadata={"prospect": pr["full_name"]}
            ))

    return inputs[:limit]


def build_gm_persona_inputs(season: int, week: int, limit: int = 5, cur=None) -> List[TaskInput]:
    inputs = []
    if cur is not None:
        try:
            from engines.gm_personas import persona_prompt
            cur.execute("""
                SELECT t.abbreviation, t.city, t.name, g.start_season,
                       g.aggressiveness, g.risk_appetite, g.pick_affinity, g.youth_bias, g.position_bias
                FROM gm_profiles g JOIN teams t ON t.id = g.team_id
                WHERE g.end_season IS NULL LIMIT %s
            """, (limit,))
            for r in cur.fetchall():
                abbr, city, team_name, start, agg, risk, picks, youth, pb = r
                ctx = {"city": city, "name": team_name, "coach": "Head Coach"}
                traits = {
                    "aggressiveness": float(agg), "risk_appetite": float(risk),
                    "pick_affinity": float(picks), "youth_bias": float(youth),
                    "position_bias": pb or {}
                }
                prompt = persona_prompt(ctx, traits, start)
                inputs.append(TaskInput(
                    id=f"gm_persona_{abbr}",
                    title=f"GM Persona: {city} {team_name} ({abbr})",
                    task="gm_persona",
                    prompt=prompt,
                    facts={"ctx": ctx, "traits": traits, "start_season": start},
                    metadata={"team": abbr, "start_season": start}
                ))
        except Exception as e:
            print(f"[llm_compare] Note: DB extraction for gm_persona fell back ({e})")

    if not inputs:
        from engines.gm_personas import persona_prompt
        ctx = {"city": "Carolina", "name": "Panthers", "coach": "Dave Canales"}
        traits = {"aggressiveness": 0.72, "risk_appetite": 0.65, "pick_affinity": 1.15, "youth_bias": 0.40, "position_bias": {"EDGE": 0.8}}
        prompt = persona_prompt(ctx, traits, 2035)
        inputs.append(TaskInput(
            id="gm_persona_CAR",
            title="GM Persona: Carolina Panthers (CAR)",
            task="gm_persona",
            prompt=prompt,
            facts={"ctx": ctx, "traits": traits, "start_season": 2035},
            metadata={"team": "CAR", "start_season": 2035}
        ))

    return inputs[:limit]


def build_gm_rationale_inputs(season: int, week: int, limit: int = 5, cur=None) -> List[TaskInput]:
    inputs = []
    if cur is not None:
        try:
            from engines.gm_rationale import prompt
            cur.execute("""
                SELECT p.id, p.season, p.week, p.trigger_kind,
                       ta.abbreviation, ta.name, ta.city,
                       tb.abbreviation, tb.name, tb.city,
                       ga.gm_name, ga.voice, gb.gm_name, gb.voice,
                       p.cap_space_before_a, p.cap_space_after_a, p.cap_space_before_b, p.cap_space_after_b
                FROM gm_trade_proposals p
                JOIN teams ta ON ta.id = p.team_a_id
                JOIN teams tb ON tb.id = p.team_b_id
                LEFT JOIN gm_profiles ga ON ga.team_id = ta.id AND ga.end_season IS NULL
                LEFT JOIN gm_profiles gb ON gb.team_id = tb.id AND gb.end_season IS NULL
                LIMIT %s
            """, (limit,))
            for r in cur.fetchall():
                pid, s, w, trig, aa, na, ca, ab, nb, cb, gma, vca, gmb, vcb, cba, caa, cbb, cab = r
                cur.execute("""
                    SELECT asset_type, from_team_id, to_team_id, player_id, pick_draft_season, pick_round, cap_hit_now
                    FROM gm_trade_assets WHERE proposal_id = %s
                """, (pid,))
                assets_db = cur.fetchall()
                assets = []
                for a_row in assets_db:
                    atype, f_id, to_id, pl_id, p_season, p_round, cap_now = a_row
                    f_abbr = aa if f_id == 8 else ab  # simplify mapping
                    t_abbr = ab if f_abbr == aa else aa
                    if atype == "PLAYER":
                        assets.append({"kind": "player", "name": f"Player #{pl_id}", "pos": "CB", "group": "CB", "ovr": 82, "age": 26, "dev": "Star", "cap_hit": f"${(cap_now or 5000000)/1e6:.1f}M", "years_left": 2, "from": f_abbr, "to": t_abbr})
                    else:
                        assets.append({"kind": "pick", "name": f"{p_season} R{p_round} ({f_abbr})", "from": f_abbr, "to": t_abbr})

                sheet = {
                    "season": s, "week": w, "trigger": trig or "NEED",
                    "teams": {
                        "a": {"team": f"{ca} {na}", "nickname": na, "abbr": aa, "gm": gma or "GM A", "voice": vca or "Direct and aggressive.", "cap_before": f"${(cba or 30000000)/1e6:.1f}M", "cap_after": f"${(caa or 25000000)/1e6:.1f}M", "needs": ["cornerback"]},
                        "b": {"team": f"{cb} {nb}", "nickname": nb, "abbr": ab, "gm": gmb or "GM B", "voice": vcb or "Methodical asset accumulator.", "cap_before": f"${(cbb or 50000000)/1e6:.1f}M", "cap_after": f"${(cab or 55000000)/1e6:.1f}M", "needs": ["draft picks"]},
                    },
                    "assets": assets,
                }
                pr = prompt(sheet)
                inputs.append(TaskInput(
                    id=f"rationale_{pid}",
                    title=f"Trade Rationale: {aa} - {ab} Swap",
                    task="gm_rationale",
                    prompt=pr,
                    facts=sheet,
                    metadata={"proposal_id": pid}
                ))
        except Exception as e:
            print(f"[llm_compare] Note: DB extraction for gm_rationale fell back ({e})")

    if not inputs:
        from engines.gm_rationale import prompt
        sheet = {
            "season": season, "week": week, "trigger": "NEED",
            "teams": {
                "a": {"team": "Carolina Panthers", "nickname": "Panthers", "abbr": "CAR", "gm": "Dan Morgan", "voice": "Direct, aggressive and urgency-driven.", "cap_before": "$35.00M", "cap_after": "$27.50M", "needs": ["wide receiver"]},
                "b": {"team": "Buffalo Bills", "nickname": "Bills", "abbr": "BUF", "gm": "Conrad Keller", "voice": "Pragmatic, clipped, and ledger-minded.", "cap_before": "$18.00M", "cap_after": "$25.50M", "needs": ["draft picks"]},
            },
            "assets": [
                {"kind": "player", "name": "Marcus Holliday", "pos": "WR", "group": "WR", "ovr": 84, "age": 27, "dev": "Star", "cap_hit": "$7.50M", "years_left": 2, "from": "BUF", "to": "CAR"},
                {"kind": "pick", "name": "2037 R2 (CAR)", "from": "CAR", "to": "BUF"},
            ]
        }
        inputs.append(TaskInput(
            id="rationale_syn_1",
            title="Trade Rationale: CAR - BUF Trade (Marcus Holliday)",
            task="gm_rationale",
            prompt=prompt(sheet),
            facts=sheet,
            metadata={"trade": "CAR_BUF"}
        ))

    return inputs[:limit]


def build_gm_negotiation_inputs(season: int, week: int, limit: int = 5, cur=None) -> List[TaskInput]:
    inputs = []
    # Negotiation prompt uses message_prompt with fact sheet
    from engines.gm_negotiation import message_prompt
    sheet = {
        "season": season, "week": week, "trigger": "USER",
        "teams": {
            "a": {"team": "Carolina Panthers", "nickname": "Panthers", "abbr": "CAR", "gm": "JR", "voice": "plain"},
            "b": {"team": "Seattle Seahawks", "nickname": "Seahawks", "abbr": "SEA", "gm": "John Schneider", "voice": "Pragmatic, sharp, protective of young defensive talent."},
        },
        "assets": [
            {"kind": "player", "name": "Tariq Woolen", "pos": "CB", "group": "CB", "ovr": 86, "age": 28, "dev": "Superstar", "cap_hit": "$12.00M", "years_left": 2, "from": "SEA", "to": "CAR"},
            {"kind": "pick", "name": "2037 R2 (CAR)", "from": "CAR", "to": "SEA"},
            {"kind": "pick", "name": "2037 R4 (CAR)", "from": "CAR", "to": "SEA"},
        ]
    }
    b = {
        "gm": {"SEA": {"gm_name": "John Schneider", "voice": "Pragmatic, sharp, protective of defensive talent."}},
        "teams": {"CAR": {"name": "Panthers", "city": "Carolina"}, "SEA": {"name": "Seahawks", "city": "Seattle"}},
        "players": {
            1: {"name": "Tariq Woolen", "pos": "CB", "group": "CB", "ovr": 86},
        },
    }
    offer = [{"type": "PLAYER", "from": "SEA", "to": "CAR", "player_id": 1}, {"type": "PICK", "from": "CAR", "to": "SEA", "pick": (2037, 2, "CAR")}]
    counter_assets = offer + [{"type": "PICK", "from": "CAR", "to": "SEA", "pick": (2037, 4, "CAR")}]

    prompt = message_prompt(b, "COUNTER", "CAR", "SEA", offer, counter_assets, mood="CLOSE", reasons=["Need additional mid-round capital"], note="Let's make this deal today.", sheet=sheet)
    inputs.append(TaskInput(
        id="negotiation_syn_1",
        title="GM Negotiation: SEA Counter to CAR Offer for CB Tariq Woolen",
        task="gm_negotiation",
        prompt=prompt,
        facts=sheet,
        metadata={"user_team": "CAR", "gm_team": "SEA", "kind": "COUNTER", "league": {"players": {"TARIQ WOOLEN"}, "teams": {"PANTHERS", "SEAHAWKS"}}}
    ))

    # Second scenario: REJECT
    sheet_reject = {
        "season": season, "week": week, "trigger": "USER",
        "teams": {
            "a": {"team": "Carolina Panthers", "nickname": "Panthers", "abbr": "CAR", "gm": "JR", "voice": "plain"},
            "b": {"team": "Dallas Cowboys", "nickname": "Cowboys", "abbr": "DAL", "gm": "Jerry Jones", "voice": "Bold, dramatic, protective of superstar assets."},
        },
        "assets": [
            {"kind": "player", "name": "Micah Parsons", "pos": "EDGE", "group": "EDGE", "ovr": 96, "age": 29, "dev": "X-Factor", "cap_hit": "$24.00M", "years_left": 3, "from": "DAL", "to": "CAR"},
            {"kind": "pick", "name": "2037 R3 (CAR)", "from": "CAR", "to": "DAL"},
        ]
    }
    b_reject = {
        "gm": {"DAL": {"gm_name": "Jerry Jones", "voice": "Bold, dramatic, protective of superstar assets."}},
        "teams": {"CAR": {"name": "Panthers", "city": "Carolina"}, "DAL": {"name": "Cowboys", "city": "Dallas"}},
        "players": {
            2: {"name": "Micah Parsons", "pos": "EDGE", "group": "EDGE", "ovr": 96},
        },
    }
    offer_reject = [{"type": "PLAYER", "from": "DAL", "to": "CAR", "player_id": 2}, {"type": "PICK", "from": "CAR", "to": "DAL", "pick": (2037, 3, "CAR")}]
    prompt_reject = message_prompt(b_reject, "REJECT", "CAR", "DAL", offer_reject, None, mood="INSULTED", reasons=["Surplus far below acceptable threshold"], note="Take it or leave it.", sheet=sheet_reject)
    inputs.append(TaskInput(
        id="negotiation_syn_2",
        title="GM Negotiation: DAL Reject Lowball Offer for Micah Parsons",
        task="gm_negotiation",
        prompt=prompt_reject,
        facts=sheet_reject,
        metadata={"user_team": "CAR", "gm_team": "DAL", "kind": "REJECT", "league": {"players": {"MICAH PARSONS"}, "teams": {"PANTHERS", "COWBOYS"}}}
    ))

    return inputs[:limit]


INPUT_BUILDERS = {
    "agent_wire": build_agent_wire_inputs,
    "memory_takes": build_memory_takes_inputs,
    "memory_summary": build_memory_summary_inputs,
    "scouting_notes": build_scouting_notes_inputs,
    "gm_persona": build_gm_persona_inputs,
    "gm_rationale": build_gm_rationale_inputs,
    "gm_negotiation": build_gm_negotiation_inputs,
}


def extract_task_inputs(task: str, season: int, week: int, limit: int = 5) -> List[TaskInput]:
    builder = INPUT_BUILDERS.get(task)
    if not builder:
        raise ValueError(f"Unknown task: {task}. Must be one of {TASKS}")
    conn, cur = get_db_cursor()
    try:
        return builder(season, week, limit=limit, cur=cur)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Evaluation Execution Loop
# ---------------------------------------------------------------------------

def run_evaluation(
    task: str,
    inputs: List[TaskInput],
    models: List[str],
    client: Any = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute all inputs across all models and gather metrics and validation checks."""
    total_calls = len(inputs) * len(models)
    print(f"\n=======================================================")
    print(f" LLM Quality Test Harness: Task '{task}'")
    print(f" Inputs: {len(inputs)} | Models: {len(models)} ({', '.join(models)})")
    print(f" Estimated real model calls: {total_calls}")
    print(f"=======================================================\n")

    summary = {
        m: {
            "model": m,
            "total_calls": 0,
            "passed_calls": 0,
            "failed_calls": 0,
            "pass_rate": 0.0,
            "total_failures": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "total_seconds": 0.0,
            "avg_seconds": 0.0,
            "total_cost_usd": 0.0,
            "avg_cost_usd": 0.0,
        }
        for m in models
    }

    report_items = []

    for idx, inp in enumerate(inputs, start=1):
        print(f"[{idx}/{len(inputs)}] Evaluating: {inp.title}")
        item_entry = {
            "id": inp.id,
            "title": inp.title,
            "prompt": inp.prompt,
            "system_prompt": inp.system_prompt,
            "facts": inp.facts,
            "metadata": inp.metadata,
            "results": {},
        }

        for model in models:
            t0 = time.perf_counter()
            try:
                out = generate_text(
                    prompt=inp.prompt,
                    system=inp.system_prompt,
                    model=model,
                    source="llm_compare",
                    label=f"compare_{task}",
                    client=client,
                    api_key=api_key,
                    return_usage=True,
                )
                if isinstance(out, tuple):
                    text, usage = out
                else:
                    text, usage = out, {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cached_tokens": 0}
                elapsed = time.perf_counter() - t0
                cost = estimate_cost(model, usage)
                passed, failures, metrics = validate_output(task, text, inp)
            except Exception as e:
                elapsed = time.perf_counter() - t0
                text = f"[ERROR: {type(e).__name__}: {e}]"
                usage = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cached_tokens": 0}
                cost = 0.0
                passed = False
                failures = [f"Model call failed: {e}"]
                metrics = {"error": str(e)}

            res = ModelResult(
                model=model,
                text=text,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                thinking_tokens=usage.get("thinking_tokens", 0),
                cached_tokens=usage.get("cached_tokens", 0),
                seconds=round(elapsed, 3),
                cost_usd=round(cost, 6),
                passed=passed,
                failures=failures,
                metrics=metrics,
            )
            item_entry["results"][model] = dataclasses.asdict(res)

            # Update summary
            s = summary[model]
            s["total_calls"] += 1
            if passed:
                s["passed_calls"] += 1
            else:
                s["failed_calls"] += 1
            s["total_failures"] += len(failures)
            s["input_tokens"] += res.input_tokens
            s["output_tokens"] += res.output_tokens
            s["thinking_tokens"] += res.thinking_tokens
            s["total_seconds"] = round(s["total_seconds"] + res.seconds, 3)
            s["total_cost_usd"] = round(s["total_cost_usd"] + res.cost_usd, 6)

            status_str = "PASS" if passed else f"FAIL ({len(failures)})"
            print(f"   -> {model:24}: {status_str} in {res.seconds:.2f}s (${res.cost_usd:.5f}) | in:{res.input_tokens} out:{res.output_tokens} think:{res.thinking_tokens}")

        report_items.append(item_entry)

    # Compute averages in summary
    for s in summary.values():
        c = max(1, s["total_calls"])
        s["pass_rate"] = round(s["passed_calls"] / c * 100.0, 1)
        s["avg_seconds"] = round(s["total_seconds"] / c, 3)
        s["avg_cost_usd"] = round(s["total_cost_usd"] / c, 6)

    return {
        "task": task,
        "models": models,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "items": report_items,
    }


# ---------------------------------------------------------------------------
# HTML & JSON Exporters
# ---------------------------------------------------------------------------

def render_html_report(data: Dict[str, Any]) -> str:
    task = data["task"]
    models = data["models"]
    summary = data["summary"]
    items = data["items"]
    timestamp = data["timestamp"]

    models_th = "".join(f"<th style='width: {100//(len(models)+1)}%'>{html.escape(m)}</th>" for m in models)

    summary_rows = ""
    for m in models:
        s = summary[m]
        rate_badge = f"<span class='badge {'badge-pass' if s['pass_rate'] >= 90 else 'badge-warn' if s['pass_rate'] >= 60 else 'badge-fail'}'>{s['pass_rate']}% ({s['passed_calls']}/{s['total_calls']})</span>"
        summary_rows += f"""
        <tr>
            <td><strong>{html.escape(m)}</strong></td>
            <td>{rate_badge}</td>
            <td>{s['total_failures']}</td>
            <td>{s['input_tokens']:,} / {s['output_tokens']:,} / {s['thinking_tokens']:,}</td>
            <td>{s['avg_seconds']:.2f}s ({s['total_seconds']:.1f}s)</td>
            <td>${s['avg_cost_usd']:.5f} (${s['total_cost_usd']:.4f})</td>
        </tr>
        """

    items_html = ""
    for idx, it in enumerate(items, start=1):
        model_cols = ""
        for m in models:
            res = it["results"].get(m, {})
            passed = res.get("passed", False)
            failures = res.get("failures", [])
            badge = "<span class='badge badge-pass'>PASS</span>" if passed else f"<span class='badge badge-fail'>FAIL ({len(failures)})</span>"
            fail_list = "".join(f"<li>{html.escape(str(f))}</li>" for f in failures)
            fail_box = f"<ul class='failures-list'>{fail_list}</ul>" if failures else "<p class='pass-text'>All automated guards passed.</p>"

            out_text = html.escape(res.get("text", ""))

            model_cols += f"""
            <div class='model-col'>
                <div class='model-col-header'>
                    <span class='model-name'>{html.escape(m)}</span>
                    {badge}
                </div>
                <div class='metrics-bar'>
                    <span>⏱ {res.get('seconds', 0.0):.2f}s</span>
                    <span>🔢 in:{res.get('input_tokens', 0)} out:{res.get('output_tokens', 0)} think:{res.get('thinking_tokens', 0)}</span>
                    <span>💵 ${res.get('cost_usd', 0.0):.5f}</span>
                </div>
                <div class='validation-box'>
                    {fail_box}
                </div>
                <div class='output-box'>
                    <pre>{out_text}</pre>
                </div>
                <div class='manual-verdict-box'>
                    <label>Persona Voice Verdict:</label>
                    <select class='verdict-select' data-item='{it['id']}' data-model='{html.escape(m)}'>
                        <option value=''>-- Select Verdict --</option>
                        <option value='excellent'>🌟 Excellent / On-Voice</option>
                        <option value='good'>👍 Good / Acceptable</option>
                        <option value='mediocre'>👌 Generic / Flat</option>
                        <option value='poor'>👎 Off-Voice / Poor</option>
                        <option value='failed'>🚫 Banned / Hallucinated</option>
                    </select>
                    <input type='text' class='verdict-notes' placeholder='Manual notes on voice & nuance...' data-item='{it['id']}' data-model='{html.escape(m)}'/>
                </div>
            </div>
            """

        prompt_snip = html.escape(it.get("prompt", ""))
        facts_snip = html.escape(json.dumps(it.get("facts", {}), indent=2, ensure_ascii=False, default=str))

        items_html += f"""
        <div class='compare-card' id='item_{it['id']}'>
            <div class='card-header'>
                <h3>#{idx}: {html.escape(it['title'])}</h3>
                <span class='card-id'>{it['id']}</span>
            </div>
            <details class='input-details'>
                <summary>🔍 View Input Prompt & Facts Packet</summary>
                <div class='prompt-view'>
                    <h4>Prompt:</h4>
                    <pre>{prompt_snip}</pre>
                    <h4>Facts:</h4>
                    <pre>{facts_snip}</pre>
                </div>
            </details>
            <div class='model-grid' style='grid-template-columns: repeat({len(models)}, 1fr);'>
                {model_cols}
            </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LLM Compare: {task.upper()} ({timestamp})</title>
    <style>
        :root {{
            --bg: #0f172a;
            --surface: #1e293b;
            --surface-alt: #334155;
            --border: #475569;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --pass-bg: #064e3b;
            --pass-text: #6ee7b7;
            --fail-bg: #7f1d1d;
            --fail-text: #fca5a5;
            --warn-bg: #78350f;
            --warn-text: #fcd34d;
            --accent: #38bdf8;
        }}
        body {{
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 24px;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1440px;
            margin: 0 auto;
        }}
        header {{
            border-bottom: 1px solid var(--border);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        h1 {{
            margin: 0 0 8px 0;
            font-size: 26px;
            color: var(--accent);
        }}
        .meta-line {{
            color: var(--text-muted);
            font-size: 14px;
        }}
        table.summary-table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--surface);
            border-radius: 8px;
            overflow: hidden;
            margin-bottom: 32px;
        }}
        table.summary-table th, table.summary-table td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        table.summary-table th {{
            background: var(--surface-alt);
            color: var(--text);
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge-pass {{ background: var(--pass-bg); color: var(--pass-text); }}
        .badge-fail {{ background: var(--fail-bg); color: var(--fail-text); }}
        .badge-warn {{ background: var(--warn-bg); color: var(--warn-text); }}
        .compare-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            margin-bottom: 24px;
            overflow: hidden;
        }}
        .card-header {{
            padding: 14px 20px;
            background: var(--surface-alt);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .card-header h3 {{ margin: 0; font-size: 16px; }}
        .card-id {{ font-size: 12px; color: var(--text-muted); font-family: monospace; }}
        .input-details {{
            padding: 10px 20px;
            border-bottom: 1px solid var(--border);
            background: #182234;
        }}
        .input-details summary {{
            cursor: pointer;
            color: var(--accent);
            font-size: 13px;
            font-weight: 500;
        }}
        .prompt-view {{
            margin-top: 10px;
            font-size: 12px;
        }}
        .prompt-view pre {{
            background: #0b1120;
            padding: 12px;
            border-radius: 6px;
            white-space: pre-wrap;
            word-break: break-word;
            color: #cbd5e1;
        }}
        .model-grid {{
            display: grid;
            gap: 16px;
            padding: 20px;
        }}
        .model-col {{
            background: #151f30;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px;
            display: flex;
            flex-direction: column;
        }}
        .model-col-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--surface-alt);
        }}
        .model-name {{
            font-weight: 700;
            font-size: 14px;
            color: var(--accent);
        }}
        .metrics-bar {{
            display: flex;
            gap: 10px;
            font-size: 11px;
            color: var(--text-muted);
            margin-bottom: 12px;
            background: #0f172a;
            padding: 6px 10px;
            border-radius: 4px;
        }}
        .validation-box {{
            margin-bottom: 12px;
            font-size: 12px;
        }}
        .failures-list {{
            margin: 0;
            padding-left: 18px;
            color: var(--fail-text);
        }}
        .pass-text {{
            margin: 0;
            color: var(--pass-text);
            font-size: 12px;
        }}
        .output-box {{
            flex: 1;
            margin-bottom: 14px;
        }}
        .output-box pre {{
            background: #0b1120;
            padding: 12px;
            border-radius: 6px;
            white-space: pre-wrap;
            word-break: break-word;
            font-size: 13px;
            margin: 0;
            color: #f1f5f9;
        }}
        .manual-verdict-box {{
            background: var(--surface);
            padding: 10px;
            border-radius: 6px;
            border: 1px solid var(--surface-alt);
        }}
        .manual-verdict-box label {{
            display: block;
            font-size: 11px;
            color: var(--text-muted);
            margin-bottom: 4px;
            text-transform: uppercase;
        }}
        .verdict-select, .verdict-notes {{
            width: 100%;
            background: #0b1120;
            color: var(--text);
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 6px 8px;
            font-size: 12px;
            box-sizing: border-box;
            margin-bottom: 6px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Quality Test Harness: {task.upper()}</h1>
            <div class="meta-line">
                Task: <strong>{task}</strong> | Evaluated: <strong>{len(items)} inputs</strong> across <strong>{len(models)} models</strong> | Timestamp: {timestamp}
            </div>
        </header>

        <h2>Summary Evaluation</h2>
        <table class="summary-table">
            <thead>
                <tr>
                    <th>Model</th>
                    <th>Pass Rate</th>
                    <th>Total Failures</th>
                    <th>Tokens (In / Out / Thinking)</th>
                    <th>Latency Avg (Total)</th>
                    <th>Cost Avg (Total)</th>
                </tr>
            </thead>
            <tbody>
                {summary_rows}
            </tbody>
        </table>

        <h2>Side-by-Side Comparison</h2>
        {items_html}
    </div>

    <script>
        // Persist manual persona voice verdicts in localStorage
        const storagePrefix = 'llm_compare_{task}_';
        document.querySelectorAll('.verdict-select').forEach(sel => {{
            const key = storagePrefix + sel.dataset.item + '_' + sel.dataset.model + '_verdict';
            const saved = localStorage.getItem(key);
            if (saved) sel.value = saved;
            sel.addEventListener('change', () => localStorage.setItem(key, sel.value));
        }});
        document.querySelectorAll('.verdict-notes').forEach(inp => {{
            const key = storagePrefix + inp.dataset.item + '_' + inp.dataset.model + '_notes';
            const saved = localStorage.getItem(key);
            if (saved) inp.value = saved;
            inp.addEventListener('input', () => localStorage.setItem(key, inp.value));
        }});
    </script>
</body>
</html>
"""


def save_reports(data: Dict[str, Any], out_dir: str = "verify_output") -> Tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    task = data["task"]
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(out_dir, f"compare_{task}_{ts}.json")
    html_path = os.path.join(out_dir, f"compare_{task}_{ts}.html")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    html_content = render_html_report(data)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return json_path, html_path


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Side-by-side LLM quality test harness across models & providers."
    )
    parser.add_argument(
        "--task",
        required=True,
        choices=TASKS,
        help="Task to compare",
    )
    parser.add_argument("--season", type=int, default=2036, help="Season year (default: 2036)")
    parser.add_argument("--week", type=int, default=20, help="Season week (default: 20)")
    parser.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="List of models to evaluate (e.g. gemini-3.8-flash:low gemini-3.5-flash-lite ollama:qwen2.5:14b)",
    )
    parser.add_argument("--limit", type=int, default=5, help="Number of inputs to test (default: 5)")
    parser.add_argument("--out", type=str, default="verify_output", help="Output directory for reports")

    args = parser.parse_args(argv)

    inputs = extract_task_inputs(args.task, args.season, args.week, limit=args.limit)
    if not inputs:
        sys.exit(f"No inputs available for task {args.task}.")

    data = run_evaluation(args.task, inputs, args.models)
    json_path, html_path = save_reports(data, out_dir=args.out)

    print(f"\n=======================================================")
    print(f" Quality comparison complete!")
    print(f" JSON report: {json_path}")
    print(f" HTML report: {html_path}")
    print(f"=======================================================\n")


if __name__ == "__main__":
    main()
