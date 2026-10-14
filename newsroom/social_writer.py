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
- Every number (scores, records, stats, ranks) must come from the FACTS below. Never invent a stat, score, record, injury, contract, trade or betting line. You may describe how a play or moment felt without inventing numbers.
- You have no tools for this post. If your persona tells you to query an index or metric, ignore that: use only the FACTS, and never name internal metrics (popularity index, watchability, market gravity, excitement index and the like) or their symbols.
- Never mention player or team ratings, "OVR", "overall", Madden or the video game. This league is covered as real football.
- At most 2 hashtags, and none is fine. Emojis only if they fit your voice.
- Do not @-mention anyone unless you are replying, and then only the account you reply to.
- Stay in character. Fans speak as fans of their own team; reporters and analysts speak as themselves. Former players are retired and never claim to still play.
- Sound like one specific person, not a template. Avoid stock fan phrases: "casuals", "real fans" / "real ones", "plastic franchise", "bleed [colors] forever", "sick to my stomach", "refs handed them". Use one only if it is truly the point of the post."""

KIND_INSTRUCTIONS = {
    "REACTION": "React to this game right after it ended. If FACTS has your_team, you are a fan of that team and it {your_team_result}: react like it. Otherwise react as yourself.",
    "PERFORMANCE": "Post about this player's performance in the game. Lead with what stood out; use the season context only if it adds something. If FACTS has why_people_are_talking, the whole league is talking about this game: that is the story, and how_big says how big.",
    "PLAYER": "Your team's season is over or nearly over. Post about this player, following the angle in FACTS.",
    "BAD_GAME": "This player had a bad game by his own standard (FACTS has his line and his average before it). Bandwagon fans and meme pages: clown him. Analysts and ex-players: call it out bluntly. Go after the performance, never his personal life, injuries or anything outside football.",
    "COMPARISON": "Make the case in a player debate using FACTS. If FACTS has your_player and their_player, argue your team's guy is better than theirs. If it has a top-five list, say who the best at that position really is (if FACTS has your_old_team_player, you can lean his way). If FACTS has this_game, say where it ranks among the best games at that position this season. Use only the numbers in FACTS.",
    "TEAM_STORY": "The league is talking about this team; FACTS.why_people_are_talking says why and how_big says how big. Give your take as yourself, not as a fan of the team: is it real, what it means, what comes next. Any NFL history you cite must come from FACTS.nfl_history_through_2025. Never predict an exact score.",
    "BEAT": "Post a beat reporter's note on your team: what last week's game showed and what to watch next. Sound like a reporter who was there, measured and specific.",
    "STANDINGS": "Post about this race in the standings. Records come from FACTS and are by record only, without tiebreakers, so don't claim clinching scenarios.",
    "PREVIEW": "Preview this upcoming game before kickoff. You don't know the result; don't predict an exact final score unless it fits your voice. If FACTS has your_team, you are a fan of that team.",
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
    instruction = KIND_INSTRUCTIONS[kind_key(assignment)]
    instruction = instruction.replace("{your_team_result}", str(facts.get("your_team_result", "played")))
    parts = [f"POST TYPE: {kind_key(assignment)}", f"TASK: {instruction}"]
    if assignment.get("angle"):
        parts.append(f"YOUR ANGLE FOR THIS POST: {assignment['angle']}.")
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


def clean_post(text: str) -> dict:
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
    return {"text": t, "hashtags": [x[1:] for x in tags], "problems": problems}


def usage_dict(response) -> dict:
    u = getattr(response, "usage_metadata", None)
    get = lambda k: int(getattr(u, k, 0) or 0) if u else 0
    return {"input": get("prompt_token_count"), "cached": get("cached_content_token_count"),
            "output": get("candidates_token_count"), "thinking": get("thoughts_token_count")}


def cost_usd(model: str, usage: dict) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return (usage["input"] * pin + (usage["output"] + usage["thinking"]) * pout) / 1_000_000


def write_post(client, model: str, system: str, prompt: str, temperature: float = 0.9) -> dict:
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
    post = clean_post(raw)
    calls = 1
    if any("over 280" in p for p in post["problems"]):
        turn = lambda role, text: types.Content(role=role, parts=[types.Part(text=text)])
        raw2 = call([turn("user", prompt), turn("model", raw),
                     turn("user", f"That is {len(post['text'])} characters. Rewrite it in under 260 characters, "
                                  "same voice. Output only the post.")])
        calls = 2
        post2 = clean_post(raw2)
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
