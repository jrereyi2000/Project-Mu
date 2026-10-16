"""Agent Wire post writer (docs/SOCIAL_MATRIX.md §3, build step 4).

Turns one social-desk assignment (newsroom/social_desk.py) into a prompt, calls Gemini once with no tools,
and cleans the reply into a post. The facts packet is the only source of numbers; the account's tweet persona
(agent_personas.json "archetypes") sets the voice. Nothing here writes to the database.
"""
import json
import os
import re
import time

# USD per 1M tokens (input, output incl. thinking). docs/SOCIAL_MATRIX.md §9; Gemini 3.8 Flash doubles Jan 1, 2027.
PRICES = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}

MAX_CHARS = 280
MAX_HASHTAGS = 2

SOCIAL_RULES = """AGENT WIRE POSTING RULES (MANDATORY):
- Write ONE social post, at most 280 characters. Output only the post text: no quotes around it, no label, no markdown, no links, no explanations.
- Every number (scores, records, stats, ranks) must come from the FACTS below. Never invent an injury, a contract figure or a betting line, and never state a trade or signing as fact. If FACTS has how_it_happened, that is how the game went: never contradict it.
- Facts are factual boundaries, not a checklist: do NOT try to cram every number from FACTS into the post. Mentioning 0, 1, or 2 key numbers naturally (or focusing purely on a moment, momentum, or the feeling of the game) makes for a much better post than a dense box-score summary. Never cite decimal fantasy points unless you are an explicit fantasy analyst.
- CADENCE & SYNTAX VARIETY: Avoid stock three-sentence templates (Exclamation! -> Stats list -> Catchphrase). Vary your syntax: try a punchy one-liner, a provocative question, a fragmented real-time reaction, a sharp contrast, or a vivid observation.
- Real people post about what they saw from the stands or the couch, a play they can't stop thinking about, sideline body language, a hot take, an overreaction, a joke, or a grudge. Fans can be wrong in their opinions, never about the score.
- BANNED CLICHÉS: Never use the word "expeditiously". Avoid overused internet crutches like "doing pure cardio" / "cardio session", "selling behavior", "coaching malpractice", or constantly joking about buying/trashing jerseys on repeat. Speak like a real human sports fan or analyst on their phone.
- You have no tools for this post. If your persona tells you to query an index or metric, ignore that: use only the FACTS, and never name internal metrics (popularity index, watchability, market gravity, excitement index and the like) or their symbols.
- Never mention player or team ratings, "OVR", "overall", Madden or the video game. This league is covered as real football.
- At most 2 hashtags, and none is fine. Emojis only if they fit your voice.
- Do not @-mention anyone unless you are replying, and then only the account you reply to.
- Stay in character. Fans speak as fans of their own team; reporters and analysts speak as themselves. Former players are retired and never claim to still play."""

KIND_INSTRUCTIONS = {
    "REACTION": "React to this game right after it ended. If FACTS has your_team, you are a fan of that team and it {your_team_result}: react authentically (elation, disbelief, heartbreak, receipts against doubters, or sheer relief). Otherwise react as yourself.",
    "PERFORMANCE": "Post about this player's performance in the game. Lead with what stood out—their impact, swagger, legacy standing, or whether they're the best on the planet right now. Season context is optional.",
    "PLAYER": "Your team's season is over or nearly over. Post about this player, following the angle in FACTS or your honest evaluation.",
    "BAD_GAME": "This player had a bad game by his own standard (FACTS has his line and his average before it). Call out the performance: whether he ghosted, made business decisions, looked overwhelmed, or was let down by his scheme. Go after the on-field play, never personal life or injuries.",
    "COMPARISON": "Make your case in a player debate using FACTS. Argue who is truly better, debate all-time peaks, or break down tape impact vs. empty box-score stats.",
    "TEAM_STORY": "The league is talking about this team; FACTS.why_people_are_talking says why and how_big says how big. Give your take on the storyline: is it sustainable, are they frauds or juggernauts, or what comes next? Cite history only from FACTS. Never predict an exact score.",
    "BEAT": "Post a beat reporter's note on your team: a practice vibe, film detail, trench battle, or locker room quote. Sound like a reporter who was there—measured, specific, and distinct.",
    "BEAT_INJURY": "Report an official roster or injury update for your team based on FACTS. Be concise, factual, and measured like an NFL beat reporter breaking injury news (status, injury diagnosis, timeline or IR designation). Never speculate on unlisted timelines.",
    "INJURY_SHOCK": "React to the major superstar injury in FACTS. If you are a fan of this team: react with pure heartbreak, devastation, or cynical dark humor about your franchise's luck. If you are an analyst or former player: react to what this loss means for the team's season or playoff hopes.",
    "STANDINGS": "Post about this race in the standings. Records come from FACTS and are by record only, without tiebreakers, so don't claim clinching scenarios.",
    "PREVIEW": "Preview this upcoming game before kickoff. Bring your perspective: dreading an opponent's star, swagger, anxious energy, or key matchup battle. You don't know the result; don't predict an exact final score unless it fits your voice. If FACTS has your_team, you are a fan of that team.",
    "FANTASY_PREVIEW": "Post this week's start/sit calls for full-PPR fantasy using the projections in FACTS. Name specific players and make clear calls.",
    "MEME": "Write a meme-page post roasting the target in FACTS. Punch at teams and fan bases, never at real-world tragedies, injuries or anyone's personal life.",
    "DRAFT": "Post about the top of the draft order using FACTS. Celebrate the tank, look ahead to April.",
    "SHARE": "Share your own new article. Write one line that makes people want to read it, in your voice; don't summarize the whole piece and don't include a link (the card is attached).",
    "REPLY": "Reply to the post below. Start with the author's handle. React to what they actually said: agree, push back or clown them, as your persona would.",
}


def kind_key(assignment: dict) -> str:
    kind, subject = assignment["kind"], assignment["subject"]["type"]
    if kind == "PREVIEW" and subject == "fantasy":
        return "FANTASY_PREVIEW"
    if kind == "MEME" and subject == "draft":
        return "DRAFT"
    return kind


def build_post_prompt(assignment: dict, account: dict, persona: str, team_name: str = None,
                      memory_block: str = "", parent: dict = None) -> tuple:
    """(system_instruction, user_prompt) for one post.

    account: agent_memories row (agent_id, archetype, persistent_memory, handle, display_name).
    persona: the archetype's tweet persona. parent: for replies, {"handle", "display_name", "text"}.
    """
    identity = (account.get("persistent_memory") or "").strip()
    who = f"You are {account.get('display_name') or account['agent_id']} ({account.get('handle')}) on Agent Wire, the league's social network."
    if team_name and account.get("archetype") in ("DIE_HARD_FAN", "LOCAL_BEAT_REPORTER"):
        who += f" Your team is the {team_name}."
    system = "\n\n".join(x for x in (
        "ROLE & PERSONA:\n" + persona,
        "IDENTITY:\n" + who + (("\n" + identity) if identity else ""),
        memory_block.strip() if memory_block else "",
        SOCIAL_RULES,
    ) if x)

    facts = dict(assignment.get("facts") or {})
    # For non-fantasy personas, sanitize out raw fantasy points from facts so real football voices don't cite decimals
    arch = (account.get("archetype") or "").upper()
    if arch not in ("FANTASY_GURU", "BERRY", "YATES"):
        facts.pop("fantasy_points_ppr", None)
        facts.pop("season_average_before_this_game_ppr", None)
        if "season_rank" in facts and isinstance(facts["season_rank"], str) and "full-PPR" in facts["season_rank"]:
            facts["season_rank"] = re.sub(r"\s*in full-PPR points\s*\([^)]*\)", "", facts["season_rank"]).strip()
        if "why_people_are_talking" in facts and isinstance(facts["why_people_are_talking"], list):
            filtered_buzz = [x for x in facts["why_people_are_talking"] if "full-PPR" not in x and "fantasy" not in x.lower()]
            if filtered_buzz:
                facts["why_people_are_talking"] = filtered_buzz

    instruction = KIND_INSTRUCTIONS[kind_key(assignment)]
    instruction = instruction.replace("{your_team_result}", str(facts.get("your_team_result", "played")))
    parts = [f"POST TYPE: {kind_key(assignment)}", f"TASK: {instruction}"]
    from newsroom.fantasy_desk import fantasy_phase
    week_num = facts.get("week") or assignment.get("week")
    if week_num is not None and fantasy_phase(week_num) is None:
        parts.append("MANDATORY RULE: The fantasy football season is over. Never mention fantasy football, fantasy points, PPR, start/sit, waivers, or fantasy matchups.")
    if assignment.get("angle"):
        parts.append(f"PERSPECTIVE / ANGLE FOR THIS POST: {assignment['angle']}. Use this as creative inspiration or your point of view—do not treat it as a rigid script. Write organically.")
    if assignment.get("slant_team"):
        parts.append(f"YOUR OLD TEAM: you played for the {assignment['slant_team']} and still have a soft spot for them. "
                     "Let it color your take when it fits, but talk like someone who follows the whole league.")
    if assignment.get("stance"):
        parts.append(f"YOUR STANCE: {assignment['stance']}. Say something the post didn't already say.")
    if parent:
        parts.append(f"POST YOU ARE REPLYING TO ({parent['display_name']}, {parent['handle']}):\n{parent['text']}")
    parts.append("FACTS:\n" + json.dumps(facts, indent=1, ensure_ascii=False, default=str))
    parts.append("Write the post now.")
    return system, "\n\n".join(parts)


_URL = re.compile(r"https?://\S+|www\.\S+")
_HASHTAG = re.compile(r"(?<!\w)#\w+")


def clean_post(text: str, fantasy_active: bool = True) -> dict:
    """{"text", "hashtags", "problems"} from raw model output. Problems are kept for review, not fixed silently
    beyond formatting: over-length posts are flagged so the caller can ask for a rewrite."""
    problems = []
    t = (text or "").strip()
    t = re.sub(r"^(?:tweet|post|reply)\s*:\s*", "", t, flags=re.I)
    t = re.sub(r"[*_`]{1,3}([^*_`]+)[*_`]{1,3}", r"\1", t)          # markdown emphasis
    t = re.sub(r"^#+\s+", "", t, flags=re.M)                       # markdown headings (not hashtags)
    if len(t) >= 2 and t[0] in "\"'“" and t[-1] in "\"'”":
        t = t[1:-1].strip()
    if _URL.search(t):
        problems.append("contained a link (removed)")
        t = _URL.sub("", t).strip()
    tags = _HASHTAG.findall(t)
    if len(tags) > MAX_HASHTAGS:
        problems.append(f"{len(tags)} hashtags (kept {MAX_HASHTAGS})")
        for extra in tags[MAX_HASHTAGS:]:
            t = re.sub(rf"\s*{re.escape(extra)}\b", "", t, count=1)
        tags = tags[:MAX_HASHTAGS]
    t = re.sub(r"[ \t]+", " ", t).strip()
    if not t:
        problems.append("empty")
    if len(t) > MAX_CHARS:
        problems.append(f"{len(t)} characters (over {MAX_CHARS})")
    if re.search(r"\b\d{2}\s*(?:ovr|overall)\b|\bmadden\b", t, re.I):
        problems.append("mentions ratings or Madden")
    if not fantasy_active and re.search(r"\b(?:fantasy|full-ppr|\bppr\b|start/sit|waiver\s*wire)\b", t, re.I):
        problems.append("mentions fantasy after season is over")
    if re.search(r"\bexpeditiously\b", t, re.I):
        problems.append("contained 'expeditiously'")
    return {"text": t, "hashtags": [x[1:] for x in tags], "problems": problems}


def usage_dict(response) -> dict:
    u = getattr(response, "usage_metadata", None)
    get = lambda k: int(getattr(u, k, 0) or 0) if u else 0
    return {"input": get("prompt_token_count"), "cached": get("cached_content_token_count"),
            "output": get("candidates_token_count"), "thinking": get("thoughts_token_count")}


def cost_usd(model: str, usage: dict) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return (usage["input"] * pin + (usage["output"] + usage["thinking"]) * pout) / 1_000_000


def write_post(client, model: str, system: str, prompt: str, temperature: float = 0.9, fantasy_active: bool = True) -> dict:
    """One Gemini call (no tools). If the post runs over 280 characters, asks once for a shorter version.
    Returns {"raw", "text", "hashtags", "problems", "usage", "cost_usd", "seconds", "calls"}."""
    from google.genai import types

    config = types.GenerateContentConfig(system_instruction=system, temperature=temperature)
    start = time.time()
    usage_total = {"input": 0, "cached": 0, "output": 0, "thinking": 0}

    def call(contents):
        resp = client.models.generate_content(model=model, contents=contents, config=config)
        for k, v in usage_dict(resp).items():
            usage_total[k] += v
        return resp.text or ""

    raw = call(prompt)
    post = clean_post(raw, fantasy_active=fantasy_active)
    calls = 1
    if any("over 280" in p for p in post["problems"]):
        turn = lambda role, text: types.Content(role=role, parts=[types.Part(text=text)])
        raw2 = call([turn("user", prompt), turn("model", raw),
                     turn("user", f"That is {len(post['text'])} characters. Rewrite it in under 260 characters, "
                                  "same voice. Output only the post.")])
        calls = 2
        post2 = clean_post(raw2, fantasy_active=fantasy_active)
        post2["problems"] = ["rewritten once for length"] + post2["problems"]
        raw, post = raw2, post2
    return {"raw": raw, **post, "usage": usage_total, "cost_usd": cost_usd(model, usage_total),
            "seconds": round(time.time() - start, 2), "calls": calls}


def load_api_key():
    """GEMINI_API_KEY / GOOGLE_API_KEY from the environment, else from the project's .env."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env):
        with open(env) as f:
            for line in f:
                if line.startswith(("GEMINI_API_KEY=", "GOOGLE_API_KEY=")):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None
