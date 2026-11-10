"""Agent Wire post writer (docs/done/SOCIAL_MATRIX.md §3, build step 4).

Turns one social-desk assignment (newsroom/social_desk.py) into a prompt, calls Gemini once with no tools,
and cleans the reply into a post. The facts packet is the only source of numbers; the account's tweet persona
(agent_personas.json "archetypes") sets the voice. Nothing here writes to the database.
"""
import json
import os
import random
import re
import time

# USD per 1M tokens (input, output incl. thinking). docs/done/SOCIAL_MATRIX.md §9; Gemini 3.8 Flash doubles Jan 1, 2027.
PRICES = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}

MAX_CHARS = 280
MAX_HASHTAGS = 2
MONEY_PROBLEM = "dollar figure not in FACTS"
RUMOR_PROBLEM = "rumor worded as a done deal"

SOCIAL_RULES = """AGENT WIRE POSTING RULES (MANDATORY):
- Write ONE social post, at most 280 characters. No links, no markdown, no label in front of it.
- Real posts are short. Most are under 25 words, and plenty are a fragment of five. Closing punctuation is optional. Post about one thing (one play, one player, one call, one feeling), not the whole game. Use at most one number unless your POST STYLE allows more, and zero is often better. FOCUS is what this post is about; FACTS are background.
- Every number (scores, records, stats, ranks) must come from FOCUS or FACTS. Never invent an injury or a betting line. Contract figures, signings, releases and trades only from FACTS; a trade is done only if FACTS says it was executed; a rumor stays a rumor; never invent a contract figure. If FACTS has how_it_happened, that is how the game went: never contradict it. Fans can be wrong in their opinions, never about what happened.
- Skip the stock lines other accounts use ("expeditiously", "pure cardio", "selling", "coaching malpractice", buying or burning a jersey). Say it the way you would.
- You have no tools for this post. If your persona tells you to query an index or metric, ignore that: use only FOCUS and FACTS, and never name internal metrics (popularity index, watchability, market gravity, excitement index and the like) or their symbols. Never cite decimal fantasy points unless you are a fantasy analyst.
- Never mention player or team ratings, "OVR", "overall", Madden or the video game. This league is covered as real football.
- Hashtags as your POST STYLE says, at most 2. Emojis only if they fit your voice.
- Do not @-mention anyone unless you are replying, and then only the account you reply to.
- Stay in character. Fans speak as fans of their own team; reporters and analysts speak as themselves. Former players are retired and never claim to still play."""

# Situation (what happened, from whose point of view) + intent (what this post is for). No menus of moods or
# moves: the persona, the shape and the focus decide how it sounds.
KIND_INSTRUCTIONS = {
    "REACTION": "Your game just ended. If FACTS has your_team, you are a fan of that team and it {your_team_result}. "
                "Post the one thing you can't let go of from it: a play, a player, a coaching decision, the feeling in your gut. "
                "Everyone already knows the final score and the record, so leave them out unless they are the point. "
                "Without your_team, react as yourself.",
    "PERFORMANCE": "This player just had a game people will talk about. Say what you saw from him and what it means to you; "
                   "the stat line is background, so pick the one detail that sold you.",
    "PLAYER": "Your team's season is over or nearly over. Post about this player and where he fits in what comes next for you, "
              "using the angle in FACTS when there is one.",
    "BAD_GAME": "This player had a bad game by his own standard (FACTS has his line and his average before it). "
                "Post the moment that summed it up and what you make of it. Go after the football, not his personal life and not injuries.",
    "COMPARISON": "There's a player debate going and you have a side. Make your case for one player in it, "
                  "with the single piece of evidence from FACTS that wins it for you.",
    "TEAM_STORY": "The league is talking about this team; FACTS.why_people_are_talking says why and how_big says how big. "
                  "Say what you actually believe about it and what you think happens next. Cite history only from FACTS. "
                  "Never predict an exact score.",
    "BEAT": "You cover this team every day. Post one note the national people don't have, from being around the team "
            "this week. Measured and specific, like a reporter who was there.",
    "BEAT_INJURY": "You cover this team and there's an injury update in FACTS. Report it plainly: who, the status, the injury and "
                   "the timeline or IR designation exactly as FACTS gives them. Never guess a timeline FACTS doesn't have.",
    "INJURY_SHOCK": "A star just got hurt (FACTS). Fans of his team are taking it personally; everyone else is thinking about what "
                    "it does to the season. Post your first reaction from where you sit.",
    "STANDINGS": "There's a race in the standings in FACTS. Say how you read it. Records are by record only, without "
                 "tiebreakers, so don't claim clinching scenarios.",
    "PREVIEW": "A game is coming up and you haven't seen it yet. If FACTS has your_team, you are a fan of that team. "
               "Post what's on your mind going in: the matchup you're watching or the player you're worried about. "
               "Don't predict an exact final score unless that's how you talk.",
    "FANTASY_PREVIEW": "It's lineup-lock week for full-PPR fantasy. Post your start/sit calls using the projections in FACTS: "
                       "name specific players and commit to the call.",
    "MEME": "You run a meme page and the target in FACTS had it coming this week. Post the joke. Punch at teams and fan bases; "
            "real-world tragedies, injuries and anyone's personal life are off limits.",
    "DRAFT": "The top of the draft order in FACTS is a race to the bottom. Post about it the way the people who live for "
             "April do.",
    "DRAFT_BOARD": "A prospect moved on the consensus draft board this week (FACTS has him, his position and school, his new rank, "
                   "how far he moved and the storyline). Say what you make of the move. Quote ranks, grades and storylines exactly "
                   "as given. Never invent a stat, a 40 time or a measurement, and never mention Madden ratings or an \"overall\".",
    "MOVE_NEWS": "You break news. The roster move in FACTS just happened and you have it first. Post who and what, with the terms "
                 "exactly as FACTS gives them. You may add colour no data records (a source's read on why, how fast it came "
                 "together, unnamed and unpriced interest elsewhere). Never add a term, a dollar figure or a team that isn't in FACTS.",
    "BEAT_MOVE": "You cover this team and it just made the roster moves in FACTS. Post what it means for the depth chart or "
                 "this week's lineup. Terms and figures only as FACTS gives them; mention the cap only if it's part of the story.",
    "MOVE_REACTION": "You are a fan of this team and it just made the roster move in FACTS. Post how it lands with you. "
                     "If FACTS has seasons_with_your_team, he's been with your team that many seasons. Never invent a contract figure.",
    "CAP_TAKE": "This team's salary-cap situation is in FACTS. Say what you think it means for how the front office can "
                "operate. Cap figures only as FACTS gives them (an 'estimated' figure is approximate: say 'roughly'). "
                "Never invent a contract figure or a trade.",
    "TRADE_NEWS": "You break news. The executed trade in FACTS just went through. Post who goes where and the picks, exactly as "
                  "FACTS gives them. You may add colour no data records (how fast it came together, who made the first call). "
                  "Never add a player, a pick, a dollar figure or a team that isn't in FACTS.",
    "TRADE_REACTION": "You are a fan of your_team in FACTS and your team just made this executed trade. Post how you feel about it. "
                      "If FACTS has your_gm_said, that is your GM's stated thinking and you can answer it. "
                      "Never invent a contract figure, a player or a pick.",
    "TRADE_RUMOR": "Two front offices have talked (FACTS). It is NOT a done deal: post it as talks, discussions, 'sources say'. "
                   "Never say a trade was made, agreed or completed, never name a player (only the position or 'a pick' FACTS "
                   "gives), and no dollar figures. If FACTS says talks stalled, say they went nowhere.",
    "PLAYER_STATEMENT": "A player went public about his contract (FACTS). Report it as a statement, not a signing: he says "
                        "he wants a deal; no deal has been agreed and none has been signed. Use only the words in "
                        "FACTS.statement and the figures it contains; add no new figures, never claim a trade or a deal, "
                        "and don't put words in his mouth beyond what FACTS.statement says.",
    "SHARE": "You just published the article in FACTS. Write one line that makes people want to read it, in your voice; "
             "don't summarize the piece and don't include a link (the card is attached).",
    "REPLY": "Someone posted the post below and you have something to say back. Start with the author's handle. "
             "Answer what they actually said, from your side of it.",
    "REPLY_QUOTE": "You're quote-posting the post below: it's shown under yours, so your followers see both. "
                   "Add the line you'd put on top of it. Don't repeat what it says and don't start with the handle.",
    # Wire voice kinds (AGENT_WIRE_VOICE_PLAN §2.3, §2.7).
    "PICKS": "It's picks day. PICK_GAMES lists this week's games. Call the ones you care about, anywhere from 1 to 4, and give "
             "a reason for at least one. Put every pick in \"picks\". A score is optional; never claim inside information.",
    "HOT_TAKE": "The league topic in FOCUS is what people are arguing about this week. Post your opinion on it, not a recap of it. "
                "Commit to a side.",
    "AWARD_RACE": "An award race is taking shape (FOCUS). Say who you think deserves it right now, or who people are wrong about.",
    "POWER_TAKE": "It's the time of year to decide who's real. Using FOCUS, post who you believe in and who you don't, "
                  "or your own top five.",
    "QUESTION": "Ask your followers something about the league you'd happily argue about with them, built on FOCUS. "
                "One question they'll want to answer.",
    "SEASON_CALL": "Go on record with a prediction that gets graded when the season ends, built on FOCUS. Put the call in "
                   "\"calls\" and say it plainly in the post.",
    "LIVE_MOMENT": "The game is still going. FOCUS is the one play that just happened, from the save's scoring summary or game "
                   "script. Post about that play as if you just watched it. You don't know how the game ends.",
    "QUARTER_UPDATE": "You cover this team and a quarter just ended (FOCUS has the score at that point and what decided the "
                      "quarter). Post the update the way a beat writer does mid-game: the score, then a line of context. "
                      "You don't know how the game ends.",
    "TRADE_IDEA": "Your team (or the team in FOCUS) has a need and you have an idea. Propose a trade you'd make, built only "
                  "from the needs and players in FOCUS and FACTS, and say it's your idea, not news.",
    "DRAFT_POSITION": "Your team keeps losing and the draft order (FOCUS) is starting to look like the only bright side. "
                      "Post about where your team is headed in April.",
}

# Shapes a post can take (one line in the prompt). pick_shape samples one per post by persona weights.
POST_SHAPES = ("one_liner", "question", "fragmented_live", "contrast", "list_of_three", "callback", "prediction",
               "observation", "sarcasm", "plain_statement")
SHAPE_LINES = {
    "one_liner": "one short line, a single thought",
    "question": "built around a question to your followers",
    "fragmented_live": "fragments typed in the moment, maybe on two or three short lines",
    "contrast": "set one thing against another",
    "list_of_three": "three short items, one per line",
    "callback": "tie it to something said or seen earlier (yours, the team's, the league's)",
    "prediction": "say what happens next",
    "observation": "one specific thing you noticed, stated plainly",
    "sarcasm": "dry or sarcastic",
    "plain_statement": "a flat statement of what happened or what you think",
}
# Kinds whose shapes are narrowed; each list keeps at least 4 shapes so the last-3 rule always leaves one.
KIND_SHAPES = {
    "QUESTION": ("question", "contrast", "one_liner", "sarcasm"),
    "PICKS": ("prediction", "list_of_three", "one_liner", "plain_statement", "contrast"),
    "SEASON_CALL": ("prediction", "one_liner", "plain_statement", "contrast"),
    "LIVE_MOMENT": ("fragmented_live", "one_liner", "sarcasm", "observation"),
    "QUARTER_UPDATE": ("plain_statement", "observation", "fragmented_live", "one_liner"),
    "MOVE_NEWS": ("plain_statement", "one_liner", "observation", "fragmented_live"),
    "TRADE_NEWS": ("plain_statement", "one_liner", "observation", "fragmented_live"),
    "TRADE_RUMOR": ("plain_statement", "one_liner", "observation", "question"),
    "PLAYER_STATEMENT": ("plain_statement", "one_liner", "observation", "question", "contrast"),
    "BEAT_INJURY": ("plain_statement", "one_liner", "observation", "fragmented_live"),
    "BEAT_MOVE": ("plain_statement", "observation", "one_liner", "contrast"),
    "SHARE": ("one_liner", "question", "plain_statement", "observation", "contrast"),
    "FANTASY_PREVIEW": ("list_of_three", "plain_statement", "one_liner", "contrast", "prediction"),
    "STANDINGS": ("observation", "contrast", "prediction", "one_liner", "question"),
}
RECENT_SHAPES = 3

PROFANITY_ARCHETYPES = {"DIE_HARD_FAN", "BANDWAGON_FAN"}      # D6: mild, rare, fans only
PROFANITY_LEVELS = ("none", "mild_rare")
PICK_CONFIDENCE = ("lock", "lean", "upset")
SEASON_CALL_TYPES = ("MVP", "OPOY", "DPOY", "ROY", "SUPER_BOWL", "PLAYOFF_TEAM", "MISSES_PLAYOFFS",
                     "COACH_FIRED")
CALL_SUBJECT_TYPES = {"MVP": ("player",), "OPOY": ("player",), "DPOY": ("player",), "ROY": ("player",),
                      "SUPER_BOWL": ("team",), "PLAYOFF_TEAM": ("team",), "MISSES_PLAYOFFS": ("team",),
                      "COACH_FIRED": ("coach", "team")}

OUTPUT_CONTRACT = (
    "OUTPUT: return only a JSON object, \"text\" first:\n"
    '{"text": "the post", "about": [{"type": "player|coach|team", "name": "as written in FOCUS or FACTS", "lean": -1|0|1}], '
    '"picks": [{"game": "AWAY @ HOME", "winner": "TEAM", "score": "27-24" or null, "confidence": "lock|lean|upset"}], '
    '"calls": [{"call_type": "' + "|".join(SEASON_CALL_TYPES) + '", "subject": "player, coach or team name", "detail": "short"}]}\n'
    "\"about\" lists who the post is about (lean 1 if you're for them, -1 against, 0 neither). \"picks\" and \"calls\" "
    "stay empty unless this post makes them.")


def kind_key(assignment: dict) -> str:
    kind, subject = assignment["kind"], (assignment.get("subject") or {}).get("type")
    if kind == "PREVIEW" and subject == "fantasy":
        return "FANTASY_PREVIEW"
    if kind == "MEME" and subject == "draft":
        return "DRAFT"
    if kind == "REPLY" and assignment.get("quote"):
        return "REPLY_QUOTE"
    return kind


# ------------------------------------------------------------------------------------------ persona styles ---
_ARCHETYPES = None


def _archetypes() -> dict:
    """agent_personas.json archetypes, read once (post_style and post_shapes live there)."""
    global _ARCHETYPES
    if _ARCHETYPES is None:
        try:
            from newsroom.personas import load_personas_json
            _ARCHETYPES = load_personas_json()
        except Exception:
            _ARCHETYPES = {}
    return _ARCHETYPES


def _pair(v, lo, hi):
    return (isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in v)
            and lo <= v[0] <= v[1] <= hi)


def validate_post_style(style: dict, archetype: str = None) -> bool:
    """Raises ValueError unless style has every post_style field in range (AGENT_WIRE_VOICE_PLAN §2.7 rule 1, D6)."""
    if not isinstance(style, dict):
        raise ValueError("post_style must be a dict")
    need = {"length", "numbers", "case", "punctuation", "hashtags", "profanity", "off_topic_share"}
    if need - set(style):
        raise ValueError(f"post_style missing {sorted(need - set(style))}")
    if not _pair(style["length"], 1, 60):
        raise ValueError(f"length {style['length']} must be [min, max] words within 1-60")
    if not _pair(style["numbers"], 0, 3):
        raise ValueError(f"numbers {style['numbers']} must be [min, max] within 0-3")
    if not _pair(style["hashtags"], 0, MAX_HASHTAGS):
        raise ValueError(f"hashtags {style['hashtags']} must be [min, max] within 0-{MAX_HASHTAGS}")
    for k in ("case", "punctuation"):
        if not isinstance(style[k], str) or not style[k].strip():
            raise ValueError(f"{k} must be a non-empty string")
    if style["profanity"] not in PROFANITY_LEVELS:
        raise ValueError(f"profanity must be one of {PROFANITY_LEVELS}")
    if archetype is not None and style["profanity"] != "none" and archetype not in PROFANITY_ARCHETYPES:
        raise ValueError(f"{archetype}: swearing is for fan archetypes only (D6)")
    o = style["off_topic_share"]
    if not isinstance(o, (int, float)) or isinstance(o, bool) or not 0 <= o <= 0.5:
        raise ValueError("off_topic_share must be a number in 0-0.5")
    return True


def render_post_style(style: dict) -> str:
    """The POST STYLE block of the system instruction; empty without a style."""
    if not style:
        return ""
    lines = []
    if style.get("length"):
        lo, hi = style["length"]
        lines.append(f"- Length: usually {lo}-{hi} words.")
    if style.get("numbers"):
        lo, hi = style["numbers"]
        lines.append("- Numbers: none." if hi == 0 else f"- Numbers: {lo}-{hi} per post." if lo else
                     f"- Numbers: at most {hi} per post; none is often better." if hi > 1 else "- Numbers: one at most, often none.")
    if style.get("case"):
        lines.append(f"- Case: {style['case']}.")
    if style.get("punctuation"):
        lines.append(f"- Punctuation: {style['punctuation']}.")
    if style.get("hashtags"):
        lo, hi = style["hashtags"]
        lines.append("- Hashtags: none." if hi == 0 else f"- Hashtags: {lo}-{hi}, your team's tag." if lo else
                     "- Hashtags: rarely, at most " + str(hi) + ".")
    prof = style.get("profanity")
    if prof == "mild_rare":
        lines.append("- Language: a mild swear now and then when you're really worked up (most of your posts have none). "
                     "Never a slur.")
    elif prof:
        lines.append("- Language: clean, no swearing.")
    if (style.get("off_topic_share") or 0) >= 0.15:
        lines.append("- You drift off football now and then (your show, other sports, your own life); a short aside is fine.")
    return "POST STYLE (how your posts usually look):\n" + "\n".join(lines) if lines else ""


def pick_shape(account: dict, kind: str, recent_shapes: list = (), seed: str = "") -> str:
    """One of POST_SHAPES for this post, weighted by the archetype's post_shapes (unlisted shapes weigh 1), limited
    to the kind's shapes (KIND_SHAPES), and never one of the account's last 3 shapes (recent_shapes, most recent last).
    Deterministic for a seed."""
    allowed = list(KIND_SHAPES.get(kind, POST_SHAPES))
    recent = set(list(recent_shapes or ())[-RECENT_SHAPES:])
    candidates = [s for s in allowed if s not in recent] or [s for s in POST_SHAPES if s not in recent]
    weights_map = account.get("post_shapes") or (_archetypes().get(account.get("archetype") or "") or {}).get("post_shapes") or {}
    weights = [max(0.0, float(weights_map.get(s, 1.0))) for s in candidates]
    if not any(weights):
        weights = [1.0] * len(candidates)
    rng = random.Random(f"shape:{seed}:{account.get('agent_id')}:{kind}")
    return rng.choices(candidates, weights=weights, k=1)[0]


def _game_label(g) -> str:
    if isinstance(g, str):
        return g
    if g.get("game"):
        return str(g["game"])
    side = lambda s: (s.get("abbr") or s.get("name")) if isinstance(s, dict) else s
    return f"{side(g.get('away'))} @ {side(g.get('home'))}"


def build_post_prompt(assignment: dict, account: dict, persona: str, team_name: str = None,
                      memory_block: str = "", parent: dict = None, relationship_block: str = "",
                      shape: str = None, post_style: dict = None) -> tuple:
    """(system_instruction, user_prompt) for one post.

    account: agent_memories row (agent_id, archetype, persistent_memory, handle, display_name).
    persona: the archetype's rendered persona block (newsroom/personas.build_persona_block); a raw persona dict is
    rendered here without database context. parent: for replies, {"handle", "display_name", "text"}.
    relationship_block: the writer's biases (newsroom/relationships.relationship_block), shown as YOUR BIASES.
    shape: one of POST_SHAPES (pick_shape). post_style: overrides the archetype's post_style from agent_personas.json.
    assignment["focus"] (1-3 facts) is what the post is about; assignment["facts"] is background;
    assignment["pick_games"] lists the games a PICKS post may call.
    """
    if post_style is None:
        src = persona if isinstance(persona, dict) else _archetypes().get(account.get("archetype") or "") or {}
        post_style = src.get("post_style")
    if not isinstance(persona, str):
        from newsroom.personas import post_tags, render_persona
        persona = render_persona(persona, "POST", post_tags(kind_key(assignment), (assignment.get("facts") or {}).get("position")),
                                 seed=f"{account.get('agent_id')}:{assignment.get('week')}:{kind_key(assignment)}")
    identity = (account.get("persistent_memory") or "").strip()
    who = f"You are {account.get('display_name') or account['agent_id']} ({account.get('handle')}) on Agent Wire, the league's social network."
    if team_name and account.get("archetype") in ("DIE_HARD_FAN", "LOCAL_BEAT_REPORTER"):
        who += f" Your team is the {team_name}."
    biases = (relationship_block or "").strip()
    system = "\n\n".join(x for x in (
        "ROLE & PERSONA:\n" + persona,
        render_post_style(post_style),
        "IDENTITY:\n" + who + (("\n" + identity) if identity else ""),
        memory_block.strip() if memory_block else "",
        ("YOUR BIASES (how you feel about the people in this post; let it colour the take, don't announce it):\n" + biases)
        if biases else "",
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

    key = kind_key(assignment)
    instruction = KIND_INSTRUCTIONS[key]
    instruction = instruction.replace("{your_team_result}", str(facts.get("your_team_result", "played")))
    parts = [f"POST TYPE: {key}", f"TASK: {instruction}"]
    from newsroom.fantasy_desk import fantasy_phase
    week_num = facts.get("week") or assignment.get("week")
    if week_num is not None and fantasy_phase(week_num) is None:
        parts.append("MANDATORY RULE: The fantasy football season is over. Never mention fantasy football, fantasy points, PPR, start/sit, waivers, or fantasy matchups.")
    if shape:
        parts.append(f"SHAPE: {shape}: {SHAPE_LINES.get(shape, shape)}.")
    if assignment.get("angle"):
        parts.append(f"PERSPECTIVE / ANGLE FOR THIS POST: {assignment['angle']}. Use this as your point of view, not a script.")
    if assignment.get("slant_team"):
        parts.append(f"YOUR OLD TEAM: you played for the {assignment['slant_team']} and still have a soft spot for them. "
                     "Let it color your take when it fits, but talk like someone who follows the whole league.")
    if assignment.get("stance"):
        parts.append(f"YOUR STANCE: {assignment['stance']}. Say something the post didn't already say.")
    if parent:
        verb = "QUOTING" if key == "REPLY_QUOTE" else "REPLYING TO"
        parts.append(f"POST YOU ARE {verb} ({parent['display_name']}, {parent['handle']}):\n{parent['text']}")
    if assignment.get("pick_games"):
        parts.append("PICK_GAMES (this week's games you may pick):\n"
                     + "\n".join(f"- {_game_label(g)}" for g in assignment["pick_games"]))
    focus = assignment.get("focus")
    if focus:
        items = focus if isinstance(focus, list) else [focus]
        parts.append("FOCUS (what this post is about):\n" + "\n".join(
            f"- {x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)}" for x in items[:3]))
        parts.append("FACTS (background, use at most one):\n" + json.dumps(facts, indent=1, ensure_ascii=False, default=str))
    else:
        parts.append("FACTS:\n" + json.dumps(facts, indent=1, ensure_ascii=False, default=str))
    parts.append("Write the post now.")
    parts.append(OUTPUT_CONTRACT)
    return system, "\n\n".join(parts)


# ------------------------------------------------------------------------------------- structured output ---
def _json_payload(raw: str):
    """The JSON object in a model reply (code fences and stray text around it allowed), or None."""
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    if "{" not in t:
        return None
    t = t[t.index("{"): t.rindex("}") + 1] if "}" in t else ""
    try:
        obj = json.loads(t)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) and isinstance(obj.get("text"), str) else None


def _norm(s) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


_ID_KEYS = {"player": ("player_id",), "coach": ("coach_id",), "team": ("team_id",)}
_NAME_KEYS = ("name", "label", "display_name", "full_name", "abbr")


class _Index:
    """Name -> (type, id) lookups built from an assignment: subject, subjects, focus, facts and pick_games. Any dict
    with type + id + a name, or player_id/coach_id/team_id + a name field (player/coach/team/name), is an entity."""

    def __init__(self, assignment: dict):
        self.entities = {}                        # (type, id) -> set of aliases
        a = assignment or {}
        for src in (a.get("subject"), a.get("subjects"), a.get("focus"), a.get("facts"), a.get("pick_games")):
            self._walk(src)

    def add(self, typ, eid, *names):
        if typ not in _ID_KEYS or eid is None:
            return
        try:
            eid = int(eid)
        except (TypeError, ValueError):
            return
        aliases = self.entities.setdefault((typ, eid), set())
        for n in names:
            n = _norm(n)
            if not n:
                continue
            aliases.add(n)
            words = n.split()
            if typ == "team" and len(words) > 1:
                aliases.add(words[-1])                        # nickname: "kansas city chiefs" -> "chiefs"
                aliases.add(" ".join(words[:-1]))             # city
            if typ in ("player", "coach") and len(words) > 1:
                aliases.add(words[-1])                        # last name

    def _walk(self, x):
        if isinstance(x, list):
            for v in x:
                self._walk(v)
            return
        if not isinstance(x, dict):
            return
        typ = x.get("type")
        if typ in _ID_KEYS and x.get("id") is not None:
            self.add(typ, x["id"], *(x.get(k) for k in _NAME_KEYS))
        id_types = [t for t, keys in _ID_KEYS.items() if any(x.get(k) is not None for k in keys)]
        for t in id_types:
            eid = next(x[k] for k in _ID_KEYS[t] if x.get(k) is not None)
            own = x.get(t) if isinstance(x.get(t), str) else None          # {"player": "Name", "player_id": 5}
            generic = [x.get(n) for n in _NAME_KEYS] if len(id_types) == 1 and typ in (None, t) else []
            self.add(t, eid, own, *generic)
        for side in ("away", "home"):                         # pick_games: {"away": {...}|"KC", "away_team_id": 12}
            s = x.get(side)
            if isinstance(s, dict) and s.get("id") is not None:
                self.add("team", s["id"], *(s.get(k) for k in _NAME_KEYS))
            elif isinstance(s, str) and x.get(f"{side}_team_id") is not None:
                self.add("team", x[f"{side}_team_id"], s)
        for v in x.values():
            if isinstance(v, (dict, list)):
                self._walk(v)

    def resolve(self, name, types=("player", "coach", "team")):
        """(type, id) for a name, or None when unknown or ambiguous. Exact alias first, then contained alias."""
        n = _norm(name)
        if not n:
            return None
        for exact in (True, False):
            hits = {k for k, al in self.entities.items() if k[0] in types
                    and (n in al if exact else any(len(a) > 3 and (a in n or n in a) for a in al))}
            if len(hits) == 1:
                return hits.pop()
            if len(hits) > 1:
                return None
        return None


def _lean(v) -> int:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0
    return 1 if v > 0 else -1 if v < 0 else 0


def _pick_game_teams(g, index: _Index):
    """(away (type,id), home (type,id)) for a pick game, resolving names when ids aren't given."""
    out = []
    for side in ("away", "home"):
        s = g.get(side) if isinstance(g, dict) else None
        tid = (s.get("id") if isinstance(s, dict) else None) if s is not None else None
        if tid is None and isinstance(g, dict):
            tid = g.get(f"{side}_team_id")
        if tid is not None:
            out.append(("team", int(tid)))
            continue
        label = s.get("abbr") or s.get("name") if isinstance(s, dict) else s
        if label is None:
            parts = re.split(r"\s+(?:@|at|vs\.?)\s+", _game_label(g), flags=re.I)
            label = parts[0 if side == "away" else -1] if len(parts) == 2 else None
        out.append(index.resolve(label, ("team",)) if label else None)
    return tuple(out)


def parse_post_json(raw: str, assignment: dict = None) -> dict:
    """{"text", "about", "picks", "calls", "structured"} from a model reply (OUTPUT_CONTRACT).

    about: [{"type", "id", "name", "lean"}] for names that resolve against the assignment (unknown names dropped).
    picks: [{"game", "game_id", "away_team_id", "home_team_id", "winner", "winner_team_id", "score",
    "winner_score", "loser_score", "confidence", "pick_type"}] for picks on assignment["pick_games"] whose winner is
    one of the two teams; pick_type SCORE with a valid score, else WINNER. calls: [{"call_type", "subject",
    "subject_type", "subject_id", "detail"}] with a known call_type and a resolved subject. A reply that isn't the
    JSON object comes back as plain text with structured False and nothing else."""
    obj = _json_payload(raw)
    if obj is None:
        return {"text": (raw or "").strip(), "about": [], "picks": [], "calls": [], "structured": False}
    a = assignment or {}
    index = _Index(a)
    about, seen = [], set()
    for item in obj.get("about") or []:
        if not isinstance(item, dict):
            continue
        types = (item["type"],) if item.get("type") in _ID_KEYS else ("player", "coach", "team")
        hit = index.resolve(item.get("name"), types)
        if hit and hit not in seen:
            seen.add(hit)
            about.append({"type": hit[0], "id": hit[1], "name": item.get("name"), "lean": _lean(item.get("lean"))})

    picks, games = [], list(a.get("pick_games") or [])
    game_teams = [(g, _pick_game_teams(g, index)) for g in games]
    picked = set()
    for p in obj.get("picks") or []:
        if not isinstance(p, dict) or not games:
            continue
        winner = index.resolve(p.get("winner"), ("team",))
        if not winner:
            continue
        sides = re.split(r"\s+(?:@|at|vs\.?)\s+", str(p.get("game") or ""), flags=re.I)
        named = {index.resolve(s, ("team",)) for s in sides} if len(sides) == 2 else set()
        match = [(g, t) for g, t in game_teams if None not in t and set(t) == named] or \
                [(g, t) for g, t in game_teams if winner in t]
        if len(match) != 1:
            continue
        g, (away, home) = match[0]
        if winner not in (away, home) or (away, home) in picked:
            continue
        picked.add((away, home))
        score = re.fullmatch(r"\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\s*", str(p.get("score") or ""))
        hi = lo = None
        if score and int(score.group(1)) != int(score.group(2)):
            hi, lo = sorted((int(score.group(1)), int(score.group(2))), reverse=True)
        conf = str(p.get("confidence") or "").lower()
        picks.append({"game": _game_label(g), "game_id": g.get("game_id") if isinstance(g, dict) else None,
                      "away_team_id": away[1], "home_team_id": home[1], "winner": p.get("winner"),
                      "winner_team_id": winner[1], "score": f"{hi}-{lo}" if hi is not None else None,
                      "winner_score": hi, "loser_score": lo,
                      "confidence": conf if conf in PICK_CONFIDENCE else "lean",
                      "pick_type": "SCORE" if hi is not None else "WINNER"})

    calls = []
    for c in obj.get("calls") or []:
        if not isinstance(c, dict):
            continue
        ctype = re.sub(r"[^A-Z_]", "", str(c.get("call_type") or "").upper().replace(" ", "_"))
        if ctype not in SEASON_CALL_TYPES:
            continue
        hit = index.resolve(c.get("subject"), CALL_SUBJECT_TYPES[ctype])
        if not hit:
            continue
        calls.append({"call_type": ctype, "subject": c.get("subject"), "subject_type": hit[0], "subject_id": hit[1],
                      "detail": str(c.get("detail") or "")[:200]})
    return {"text": obj["text"].strip(), "about": about, "picks": picks, "calls": calls, "structured": True}


# --------------------------------------------------------------------------------------- repetition check ---
def _words(text: str) -> list:
    t = re.sub(r"https?://\S+|@\w+", " ", (text or "").lower())
    return re.findall(r"[a-z0-9']+", t)


def _trigrams(words: list) -> set:
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)} if len(words) >= 3 else {tuple(words)} if words else set()


def too_similar(text: str, corpus: list, threshold: float = 0.55, same_kind: list = ()) -> bool:
    """True when text's word trigrams overlap any corpus post by Jaccard >= threshold (the week's posts so far and the
    account's last 15), or its first three words match a post in same_kind (this week's posts of the same kind)."""
    words = _words(text)
    grams = _trigrams(words)
    if not grams:
        return False
    for other in corpus or ():
        og = _trigrams(_words(other))
        if og and len(grams & og) / len(grams | og) >= threshold:
            return True
    if len(words) >= 3:
        opener = words[:3]
        if any(_words(o)[:3] == opener for o in same_kind or ()):
            return True
    return False


_URL = re.compile(r"https?://\S+|www\.\S+")
_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?(m|mil|million|k|thousand|b|billion)?\b", re.I)


def _money_value(num: str, unit: str):
    v = float(num.replace(",", ""))
    unit = (unit or "").lower()
    mult = 1_000_000 if unit.startswith("m") else 1_000 if unit.startswith(("k", "t")) else 1_000_000_000 if unit.startswith("b") else 1
    return round(v * mult / 10_000)            # compared to the nearest $10K


def money_values(text: str) -> set:
    """Dollar amounts in a text ('$16.8M', '$16.8 million', '$200K') as comparable values."""
    return {_money_value(n, u) for n, u in _MONEY.findall(text or "")}


def allowed_money(facts) -> set:
    """The dollar amounts a post may use: those in its facts packet (social_desk move and cap facts)."""
    return money_values(json.dumps(facts or {}, ensure_ascii=False, default=str))
_HASHTAG = re.compile(r"(?<!\w)#\w+")
# A rumor post that says the deal happened (TRADE_RUMOR's guard).
_DONE = re.compile(r"\b(?:traded|acquired|agreed to|finali[sz]ed|completed|shipped|(?<!not a )done deal)\b", re.I)


def clean_post(text: str, fantasy_active: bool = True, allowed=None, rumor: bool = False) -> dict:
    """{"text", "hashtags", "problems"} from raw model output. Problems are kept for review, not fixed silently
    beyond formatting: over-length posts are flagged so the caller can ask for a rewrite. `allowed` (allowed_money
    of the facts packet) turns on the money guard: a dollar amount not in FACTS is a problem. `rumor` (a TRADE_RUMOR
    post) turns on the rumor guard: wording that says the trade happened is a problem."""
    problems = []
    t = (text or "").strip()
    payload = _json_payload(t) if t.startswith(("{", "```")) else None
    if payload is not None:                                         # a structured reply passed in whole
        t = payload["text"].strip()
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
    if allowed is not None:
        invented = sorted(m.group(0).strip() for m in _MONEY.finditer(t)
                          if _money_value(m.group(1), m.group(2)) not in allowed)
        if invented:
            problems.append(f"{MONEY_PROBLEM}: {', '.join(invented)}")
    if rumor:
        done = _DONE.search(t)
        if done:
            problems.append(f"{RUMOR_PROBLEM}: {done.group(0)}")
    return {"text": t, "hashtags": [x[1:] for x in tags], "problems": problems}


def usage_dict(response) -> dict:
    u = getattr(response, "usage_metadata", None)
    get = lambda k: int(getattr(u, k, 0) or 0) if u else 0
    return {"input": get("prompt_token_count"), "cached": get("cached_content_token_count"),
            "output": get("candidates_token_count"), "thinking": get("thoughts_token_count")}


def cost_usd(model: str, usage: dict) -> float:
    if model and (model.startswith("ollama/") or model.startswith("ollama:")):
        return 0.0
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return (usage["input"] * pin + (usage["output"] + usage["thinking"]) * pout) / 1_000_000


SIMILAR_PROBLEM = "too similar to another post"


def assignment_money(assignment: dict) -> set:
    """allowed_money for a whole assignment: its facts, its focus and (for a reply) the parent post's text."""
    a = assignment or {}
    return allowed_money({"facts": a.get("facts"), "focus": a.get("focus")}) | money_values((a.get("parent") or {}).get("text", ""))


def _rewrite_asks(post: dict, similar: bool) -> tuple:
    """(asks, reasons) for write_post's one rewrite; both empty when the post is fine."""
    too_long = any("over 280" in p for p in post["problems"])
    money = [p for p in post["problems"] if p.startswith(MONEY_PROBLEM)]
    done = [p for p in post["problems"] if p.startswith(RUMOR_PROBLEM)]
    asks, reasons = [], []
    if too_long:
        asks.append(f"That is {len(post['text'])} characters. Rewrite it in under 260 characters.")
        reasons.append("length")
    if money:
        asks.append(f"It names a dollar figure that isn't in FACTS ({money[0].split(': ', 1)[1]}). Use only the "
                    "figures in FACTS, or none.")
        reasons.append("money")
    if done:
        asks.append("It says the trade happened, but this is a rumor: only talks. Word it as talks, not a deal.")
        reasons.append("rumor wording")
    if similar:
        asks.append("It reads too much like another post this week. Say it your own way: a different opening and a "
                    "different detail.")
        reasons.append("repetition")
    return asks, reasons


def _finish(raw: str, assignment, corpus, same_kind, fantasy_active, allowed, rumor) -> dict:
    """Parse, clean and check one reply: the post dict plus a `similar` flag."""
    parsed = parse_post_json(raw, assignment)
    post = clean_post(parsed["text"], fantasy_active=fantasy_active, allowed=allowed, rumor=rumor)
    post.update({"about": parsed["about"], "picks": parsed["picks"], "season_calls": parsed["calls"],
                 "structured": parsed["structured"]})
    similar = bool(post["text"]) and too_similar(post["text"], corpus or (), same_kind=same_kind or ())
    if similar:
        post["problems"].append(SIMILAR_PROBLEM)
    return post, similar


def write_post(client, model: str, system: str, prompt: str, temperature: float = 0.9, fantasy_active: bool = True,
               allowed=None, rumor: bool = False, provider: str | None = None,
               fallback_model: str | None = None, label: str | None = None,
               assignment: dict = None, corpus: list = None, same_kind: list = None) -> dict:
    """One LLM call (no tools); supports Gemini or local Ollama with fallback to Gemini on network/timeout errors.
    The reply is the OUTPUT_CONTRACT JSON (parse_post_json, resolved against `assignment`; plain text still works).
    If the post runs over 280 characters, names an ungrounded dollar figure, frames a rumor as a done deal, or is
    too_similar to `corpus` (the week's posts and the account's recent ones; `same_kind` for the opener check),
    asks once for a rewrite. Never falls back on bad answer / validation failure.
    Returns {"raw", "text", "hashtags", "problems", "about", "picks", "season_calls", "structured", "usage",
    "cost_usd", "seconds", "calls"}: parse_post_json's "calls" come back as "season_calls" because "calls" has
    always been the number of LLM calls."""
    from engines.llm_provider import (
        resolve_provider, resolve_model, resolve_fallback_model,
        call_ollama, OLLAMA_FALLBACK_EXCEPTIONS,
    )
    from db.llm_usage import log_usage

    finish = lambda r: _finish(r, assignment, corpus, same_kind, fantasy_active, allowed, rumor)
    prov = resolve_provider(label="agent_wire", model=model, provider=provider)

    if prov == "ollama":
        ollama_model = resolve_model("ollama", label="agent_wire", model=model)
        fb_model = resolve_fallback_model(label="agent_wire", fallback_model=fallback_model)
        start = time.time()
        usage_total = {"input": 0, "cached": 0, "output": 0, "thinking": 0}

        def _log_ollama_call(usage, call_label):
            meter = getattr(client, "_usage_meter", None)
            if meter is not None:
                meter.add_call(usage, model=f"ollama/{ollama_model}", label=call_label or getattr(meter._local, "label", None), source="agent_wire")
            else:
                log_usage(source="agent_wire", label=call_label or label or "post", model=f"ollama/{ollama_model}",
                          input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"])

        def _call_ollama(msgs):
            text, usage = call_ollama(model=ollama_model, messages=msgs, temperature=temperature)
            usage_total["input"] += usage["input_tokens"]
            usage_total["output"] += usage["output_tokens"]
            return text, usage

        try:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
            raw, u1 = _call_ollama(messages)
            _log_ollama_call(u1, label)
        except OLLAMA_FALLBACK_EXCEPTIONS as e:
            print(f"[social_writer] Ollama ({ollama_model}) unreachable or timed out ({type(e).__name__}: {e}). "
                  f"Falling back to Gemini ({fb_model}).", flush=True)
            return write_post(client, fb_model, system, prompt, temperature=temperature,
                              fantasy_active=fantasy_active, allowed=allowed, rumor=rumor,
                              provider="gemini", label=label, assignment=assignment, corpus=corpus, same_kind=same_kind)

        post, similar = finish(raw)
        calls = 1
        asks, reasons = _rewrite_asks(post, similar)
        if asks:
            tail = " Same voice. Return the same JSON object." if post["structured"] else " Same voice. Output only the post."
            rewrite_messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": raw},
                {"role": "user", "content": " ".join(asks) + tail},
            ]
            try:
                raw2, u2 = _call_ollama(rewrite_messages)
                _log_ollama_call(u2, f"{label or ''} rewrite".strip())
                calls = 2
                post2, _ = finish(raw2)
                post2["problems"] = [f"rewritten once for {' and '.join(reasons)}"] + post2["problems"]
                raw, post = raw2, post2
            except OLLAMA_FALLBACK_EXCEPTIONS as e:
                print(f"[social_writer] Ollama rewrite failed ({type(e).__name__}: {e}); keeping initial post.", flush=True)

        return {"raw": raw, **post, "usage": usage_total, "cost_usd": 0.0,
                "seconds": round(time.time() - start, 2), "calls": calls}

    # Gemini provider flow
    from engines.llm_config import make_content_config

    if client is None:
        from google import genai
        key = load_api_key()
        client = genai.Client(api_key=key)

    config, model = make_content_config(model=model, system_instruction=system, temperature=temperature)
    start = time.time()
    usage_total = {"input": 0, "cached": 0, "output": 0, "thinking": 0}

    def call(contents):
        resp = client.models.generate_content(model=model, contents=contents, config=config)
        for k, v in usage_dict(resp).items():
            usage_total[k] += v
        return resp.text or ""

    raw = call(prompt)
    post, similar = finish(raw)
    calls = 1
    asks, reasons = _rewrite_asks(post, similar)
    if asks:
        from google.genai import types
        turn = lambda role, text: types.Content(role=role, parts=[types.Part(text=text)])
        tail = " Same voice. Return the same JSON object." if post["structured"] else " Same voice. Output only the post."
        raw2 = call([turn("user", prompt), turn("model", raw), turn("user", " ".join(asks) + tail)])
        calls = 2
        post2, _ = finish(raw2)
        post2["problems"] = [f"rewritten once for {' and '.join(reasons)}"] + post2["problems"]
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
