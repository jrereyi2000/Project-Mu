"""
The shared trade-talk line writer and its style contract (GM_TALK_VOICE_PLAN §2.5; PROJECT_SPEC §2.17).

Both CPU-to-CPU talks (`engines/gm_talks.py`) and JR's inbox negotiations (`engines/gm_negotiation.py`) already
let the engine pick the move and only ask a model to write the line. This module is the one place that line is
written and checked: `write_line` builds the prompt from an already-rendered voice card, the scene, the fixed
move, the fact sheet and the last three lines, asks for JSON `{"message": "..."}`, and keeps the answer only
when it passes both `gm_rationale.validate_rationale` (facts still bind) and `check_style` (short, spoken, no
filler, no repetition). A failure retries once with the reason appended and then falls back to the caller's
template; `llm=None` returns the template at once. The writer never picks or changes a move, and it does not
import `gm_voices` or `gm_talks` at import time: the caller renders the card (`gm_voices.render_card`) and the
timeout budget is a lazy import of `gm_talks.call_with_timeout`.
"""
import json
import re

# Words and phrases that turn a spoken line into analyst filler. `market efficiency` and `efficiency` are the
# same word; the pattern keeps both named because the plan lists them.
STYLE_BANNED = re.compile(r"\b(efficiency|asset value|framework|process|leverage|value proposition|"
                          r"market efficiency|floor[- ]raising)\b", re.I)
ELLIPSIS_RE = re.compile(r"\.\.\.|…")
RUN = 5                       # a repeated run of this many words is plagiarism of the previous line
RESTATEMENT_WORDS = 8         # a restatement needs a run this long; shorter repeats fall to the phrase rule
_WORD_RE = re.compile(r"[a-z0-9']+")


# ------------------------------------------------------------------ style ----
def _words(text):
    return _WORD_RE.findall((text or "").lower())


def _runs(words, n):
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _norm(text):
    return " ".join(_words(text))


def _prior_messages(prior):
    """Prior lines as (message, source, team) triples (history dicts carry `message`, `source`/`message_source`
    and `team`)."""
    out = []
    for h in prior or []:
        if isinstance(h, dict):
            msg, source = h.get("message"), h.get("source") or h.get("message_source")
            team = h.get("team")
        else:
            msg, source, team = h, None, None
        if msg:
            out.append((str(msg), source, team))
    return out


def check_style(text, prior, max_words=45, speaker=None):
    """(ok, why). Rejects a line that is over `max_words`, uses a banned word or phrase, holds more than one
    ellipsis, repeats a 5-word run from either of the previous two lines, or restates one of those lines
    verbatim. Pure; `prior` is the earlier lines (strings, or dicts carrying `message`, `source` and `team`).
    The repeated-run rule skips TEMPLATE and USER lines and the speaker's own lines: a canned fallback or JR's
    note is not another GM's wording, and a man repeating his own tic is not the two GMs sounding alike
    (2026-11-09, 26.5% fallbacks)."""
    t = (text or "").strip()
    if not t:
        return False, "empty line"
    if len(re.findall(r"\S+", t)) > max_words:
        return False, f"over {max_words} words"
    m = STYLE_BANNED.search(t)
    if m:
        return False, f"banned phrase '{m.group(0)}'"
    if len(ELLIPSIS_RE.findall(t)) > 1:
        return False, "more than one ellipsis"
    prev = _prior_messages(prior)[-2:]
    w, nt = _words(t), _norm(t)
    for p, src, team in prev:
        if src in ("TEMPLATE", "USER") or (speaker and team and team == speaker):
            continue                     # the same exemptions the phrase rule applies: a template or JR's own words
        np_ = _norm(p)                   # are not the GM plagiarising himself, and he may repeat his own tic
        if not np_ or not nt:
            continue
        # Compare word runs, not letters: `nt in np_ or np_ in nt` on the raw strings let a short earlier line
        # ("No.", "JR: no") reject any later line that merely contained its letters.
        if nt == np_ or (len(w) >= RESTATEMENT_WORDS and _runs(w, RESTATEMENT_WORDS)
                         & _runs(_words(p), RESTATEMENT_WORDS)):
            return False, "restates an earlier line verbatim"
    for p, src, team in prev:
        if src in ("TEMPLATE", "USER") or (speaker and team and team == speaker):
            continue
        if _runs(w, RUN) & _runs(_words(p), RUN):
            return False, "repeats a phrase from an earlier line"
    return True, None


# ----------------------------------------------------------------- prompt ----
def _history_messages(history):
    """The history as display strings for the prompt (sources stay on the raw history for `check_style`)."""
    return [msg for msg, _src, _team in _prior_messages(history)]


def _line_prompt(card_text, sheet, history, move, scene, max_words):
    """The writer's one prompt: card, scene, fixed move, sheet JSON, the last three lines and the style rules.
    (The caller renders `card_text` with `gm_voices.render_card`; this module never imports gm_voices.)"""
    mv = move or {}
    sc = scene or {}
    lines = ["You are a general manager in a simulated NFL league, on the phone with another general manager "
             "about a trade. Speak in this man's voice and only his:",
             card_text or ""]

    scene_bits = []
    if sc.get("week") is not None:
        scene_bits.append(f"week {sc['week']}")
    if sc.get("deadline_week") is not None:
        scene_bits.append(f"the deadline is week {sc['deadline_week']}")
    if sc.get("who_called"):
        scene_bits.append(f"{sc['who_called']} called")
    if sc.get("mood"):
        scene_bits.append(f"the mood is {sc['mood']}")
    if sc.get("stakes"):
        scene_bits.append(f"the stakes are {sc['stakes']}")
    if sc.get("relationship"):
        scene_bits.append(f"the relationship is {sc['relationship']}")
    if scene_bits:
        lines.append("SCENE: " + "; ".join(scene_bits) + ".")

    move_bits = [f"action {mv.get('action') or 'COUNTER'}"]
    if mv.get("posture"):
        move_bits.append(f"posture {mv['posture']}")
    lines.append("THIS TURN (the move is already decided; do not change it): " + ", ".join(move_bits) + ".")
    if mv.get("gets"):
        lines.append("Your side gets: " + ", ".join(str(x) for x in mv["gets"]) + ".")
    if mv.get("gives"):
        lines.append("Your side gives: " + ", ".join(str(x) for x in mv["gives"]) + ".")

    lines += ["FACT SHEET (JSON; the only facts you may state):",
              json.dumps(sheet, indent=1, default=str)]

    prior = _history_messages(history)[-3:]
    if prior:
        lines.append("WHAT WAS SAID (the last lines, verbatim):")
        lines += [f"- {h}" for h in prior]

    lines += [
        "STYLE RULES:",
        f"- One spoken line, at most {max_words} words. No preamble and no summary of the deal.",
        "- Write like a man talking, not a memo. Never use these words or phrases: efficiency, asset value, "
        "framework, process, leverage, value proposition, market efficiency, floor-raising.",
        "- At most one ellipsis. Do not repeat a phrase another man just used, and do not restate the package "
        "word for word.",
        "- Never state a number, dollar amount, player, team, record or contract fact that isn't on the fact "
        "sheet, and never say the trade is final or completed.",
        'Answer with JSON only: {"message": "..."}',
    ]
    return "\n".join(lines)


def _parse_message(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0) if m else (raw or ""))
    except (ValueError, TypeError, AttributeError):
        return ""
    if not isinstance(d, dict):
        return ""
    return str(d.get("message") or "").strip()


def _call(llm, prompt, timeout):
    if timeout is not None:
        from engines.gm_talks import call_with_timeout
        return call_with_timeout(llm, prompt, timeout)
    from engines.gm_llm import call
    return call(llm, prompt)


# ------------------------------------------------------------------ write ----
def write_line(llm, *, card_text, sheet, history, move, scene, fallback, max_words=45, timeout=None,
               league=None):
    """(text, source). One model call writes a line from the card, scene, fixed move, fact sheet and the last
    three lines; it is kept only when `validate_rationale` and `check_style` both pass. A failure retries once
    with the reason appended, then returns `(fallback, "TEMPLATE")`. `source` is "LLM" or "TEMPLATE". With
    `llm=None` the fallback comes back at once. `timeout` uses `gm_talks.call_with_timeout`. `league` is the name
    check `validate_rationale` runs against; without it a reply may name a team or player outside the deal."""
    if llm is None:
        return fallback, "TEMPLATE"
    from engines.gm_rationale import validate_rationale

    prompt = _line_prompt(card_text, sheet, history, move, scene, max_words)
    prior = (history or [])[-2:]
    why = None
    for _ in range(2):
        attempt = prompt if why is None else (
            f"{prompt}\nYour previous answer was rejected: {why}. Write a different line that fixes it. "
            'Answer with JSON only: {"message": "..."}')
        raw = _call(llm, attempt, timeout)
        if raw is None:
            # a timed-out call has already spent the budget and is still running: retrying doubles the wall time
            # (one inbox reply could take ~16 s against an 8 s budget) and bills a second call.
            why = "the model timed out"
            break
        text = _parse_message(raw)
        if not text:
            why = "empty or unreadable answer"
            continue
        ok, fact_why = validate_rationale(text, sheet, league)
        if not ok:
            why = f"a fact that isn't on the sheet ({fact_why})"
            continue
        ok, style_why = check_style(text, prior, max_words, speaker=(scene or {}).get("speaker"))
        if not ok:
            why = f"style ({style_why})"
            continue
        return text, "LLM"
    print(f"[gm_writer] template fallback: {why}", flush=True)
    return fallback, "TEMPLATE"
