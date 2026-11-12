"""The write-time fact gate: does an article contradict the evidence its writer had?

Every article is checked claim by claim after it is written, so nothing reaches `news_feed` with a
claim that disagrees with the tool results its writer actually received. This module is the single
source for the rubric, the response parser and the client construction; `tools/article_factcheck.py`
(the offline veracity CLI) imports them back instead of keeping its own copies.

Three verdicts per claim:

- **SUPPORTED** — the evidence states it (including arithmetic the writer did on evidence values).
- **CONTRADICTED** — the evidence states something different for the same thing (the real error),
  or the article attaches a statistic, record, award, status word, team or role to a person the
  evidence never mentions — an invented person with a stat line is a fabrication, not an omission.
  A bare name in passing is UNSUPPORTED: absence alone is never a contradiction.
- **UNSUPPORTED** — the evidence does not mention it. Not an error by itself.

The window is the whole evidence. The first R1 run (docs/in_progress/ARTICLE_FACT_GATE_PLAN.md §3.1,
2026-11-12) kept the CLI's 24,000-character evidence and 6,000-character brief bounds, so the checker
never saw the tool results: the 18 compared articles carry 56,590-205,579 characters of evidence
(median 97,004), and their assignment alone is 46,484-48,496. 20-62% of claims came back
"contradicted", almost all falsely ("Mike LaFleur is the Cardinals' head coach" ×37 in one article);
the bound was the bug. So `EVIDENCE_LIMIT` is 400,000 characters — past any real article and far
inside DeepSeek V4.1 Flash's context — the assignment is never the part that is cut unless it alone
fills the window (then it is bounded too and the marker says so), and a cut is marked in the prompt
and turns absence into UNSUPPORTED, never CONTRADICTED. The same rule covers the whole tool result:
the dispatcher hands it over uncut, so the only cut is this visible one.

The fix is coverage, not the model. The rubric runs three passes before it weighs any number:
every named person's team and role, every status word against `years_of_experience`, and every
game result as a (winner, loser, score) triple. That is the class of error the first veracity pass
missed (the fabricated Isaiah Carson, docs/reference/MODEL_ROUTING_EVALUATION.md §8.1).

A gate outage must never fail an article write: no key, no client, no SDK, a transport error or an
unparsable answer all return `{"ok": True, "skipped": "no checker", ...}` and never raise.

A malformed answer is not an outage. The second R1 run (plan §3.2) lost 4 of 18 articles to
"no parsable answer" — intermittent malformation of a 60-claim answer quoting article text, not a
size limit — and a parse failure is a `skipped` verdict, so those articles reached `news_feed`
ungated. `parse_answer` therefore repairs the two common ways a long JSON answer breaks (a trailing
comma before `}`/`]`, and a raw newline, tab or other control character inside a JSON string), and
`judge` makes at most one retry asking for JSON only. Both the gate and the CLI call `judge`, so a
retry behaves the same in production and in an offline audit.

    from newsroom.fact_gate import check, rewrite_prompt
    result = check(article_text, evidence)
    if result["counts"]["CONTRADICTED"]:
        message = rewrite_prompt([c for c in result["claims"] if c["verdict"] == "CONTRADICTED"])
"""
from __future__ import annotations

import json
import os

FACT_GATE_MODEL = os.environ.get("FACT_GATE_MODEL") or "deepseek-v4.1-flash"
DEFAULT_MODEL = "deepseek-v4.1-flash"

# A raised cap from the CLI's 40: the Carson section sat past the cut.
MAX_CLAIMS = 60

# Bounds on the prompt. The first R1 run kept the CLI's 24,000 / 6,000 and the checker never saw the
# tool results (plan §3.1): a real article's evidence is 56,590-205,579 characters, median 97,004, and
# the assignment alone 46,484-48,496. The whole evidence now fits with room to spare — DeepSeek V4.1
# Flash's context is far larger than this — and a cut, if it ever happens, is marked and never silent.
EVIDENCE_LIMIT = 400_000
BRIEF_LIMIT = 60_000
# The dispatcher hands the gate every tool result whole (newsroom/swarm_dispatcher.py
# `evidence_blocks`); a single result is no longer cut to 12,000 characters on the way in, so the
# only cut is the visible one `window_evidence` makes at `EVIDENCE_LIMIT`.
TOOL_RESULT_LIMIT = 12_000  # kept for callers that still bound their own trace

# A tool block starts at the beginning of a line: the evidence is "<assignment>\n\nTOOL name(args):\n
# <result>\n\nTOOL ..." (newsroom/swarm_dispatcher.py `_execute_agent`).
TOOL_MARKER = "TOOL "
# Written into the prompt where the cut is, so the checker knows the evidence is partial. `{missing}`
# is the exact number of tool-result characters dropped.
TRUNCATION_MARKER = ("[... EVIDENCE TRUNCATED HERE: {missing:,} characters of tool results "
                     "not shown ...]")
# Used when the assignment alone is already over the window: no tool result is shown at all and the
# count covers the assignment's own tail as well, so the prompt never claims more than it shows.
ASSIGNMENT_TRUNCATION_MARKER = ("[... EVIDENCE TRUNCATED HERE: {missing:,} characters not shown "
                                "(the assignment alone exceeds the window, so no tool results are "
                                "shown) ...]")
# The brief is the assignment's copy in the prompt; a cut there is marked and reported too.
BRIEF_TRUNCATION_MARKER = "[... ASSIGNMENT TRUNCATED HERE: {missing:,} characters not shown ...]"

# Thinking shares the output cap on Go's reasoning models: at 8,192 DeepSeek V4.1 Flash spent the
# whole budget thinking and returned nothing. The cap is generous and thinking is asked low.
MAX_OUTPUT_TOKENS = 32768
THINKING_LEVEL = "LOW"

SKIP_REASON = "no checker"

RUBRIC_TEMPLATE = """You are a fact-checker for a fictional NFL league's newsroom. You get an article, the \
assignment it answered, and the evidence its writer retrieved from the league database. Judge the \
article's factual claims ONLY against that evidence.

Make these three passes FIRST, before you judge any number. They are the claims the writers get \
wrong most often, and a number does not excuse a wrong person, status or result:
1. Every named person a claim is attached to: for each person the article credits with a statistic, \
a record, an award, a status word, a team or a role, is that person in the evidence, on the same team \
and in the same role? This pass is about the claims attached to people, not about whether every name \
is mentioned: a name that carries no claim is not an error.
2. Every status word ("rookie", "second-year", "veteran"): check it against that player's \
years_of_experience in the evidence (0 = rookie, 1 = second-year, 2 or more = veteran). A draft \
status ("undrafted", "first-round pick") is only CONTRADICTED when the evidence states a different \
draft round or pick; with no draft round in the evidence it is UNSUPPORTED, never an error.
3. Every game result: the winner, the loser and the exact score, as a (winner, loser, score) triple. \
A swapped winner and a swapped score are each a contradiction.

Claim types: numbers (yards, records, scores, ranks, percentages, ages, money), names and roles \
(player on team, coach on team), events (a trade, a firing, a result), and stated standings.

For each claim output one of:
- "SUPPORTED": the evidence states this value/fact.
- "CONTRADICTED": the evidence gives a DIFFERENT value/fact for the same thing. Use this only when \
the evidence positively says otherwise; never infer it from silence.
- "UNSUPPORTED": the evidence does not mention it. This is not an error; say so and move on.

A claim is CONTRADICTED with the evidence quote "not in evidence" only when the article attaches \
something to a person — a statistic, a record, an award, a status word, a team or a role — and that \
person appears nowhere in the evidence: the whole line is invented. A bare name in passing, or a \
coach named without a claim attached, is UNSUPPORTED, not a contradiction. Any other fact the \
evidence simply does not mention is UNSUPPORTED.

Rules:
- Cluster a sentence's numbers into one claim per fact (a stat line is several claims).
- Skip pure opinion, prediction, style, and obvious arithmetic the writer did on evidence values \
(mark arithmetic "SUPPORTED" and set "derived": true).
- Quote the evidence line for every SUPPORTED or CONTRADICTED claim, 12 words maximum.
- Cap at {max_claims} claims, the most load-bearing first.
- Never use outside knowledge about real NFL players or teams; this league is fictional.

Reply with JSON only:
{"claims": [{"claim": "...", "verdict": "SUPPORTED|CONTRADICTED|UNSUPPORTED", "evidence": "...", \
"derived": false}], "note": "one sentence on the article's overall accuracy"}"""


# Added to the rubric only when the evidence was cut. The first R1 run (plan §3.1) judged a truncated
# window as if it were the whole evidence, which is how real coaches became "not in evidence".
TRUNCATION_RULE = """The evidence shown to you is PARTIAL: it was longer than the window, so part of \
the tool results is missing where the truncation marker sits. Absence is then never proof — a person \
or fact that might sit in the omitted part is UNSUPPORTED, never CONTRADICTED."""


def build_rubric(max_claims: int = MAX_CLAIMS, truncated: bool = False) -> str:
    """The checker's system instruction, with the claim cap it should honour.

    `truncated=True` adds the rule that makes a partial window safe: when the evidence shown has a
    hole in it, absence is never proof, so anything that might sit in the omitted part is UNSUPPORTED.
    """
    rubric = RUBRIC_TEMPLATE.replace("{max_claims}", str(int(max_claims)))
    if truncated:
        rubric += "\n\n" + TRUNCATION_RULE
    return rubric


RUBRIC = build_rubric()


def resolve_model(model: str | None = None) -> str:
    """The argument, else `FACT_GATE_MODEL` from the environment now, else the module default."""
    return (model or os.environ.get("FACT_GATE_MODEL") or FACT_GATE_MODEL
            or DEFAULT_MODEL)


def _env(name: str) -> str | None:
    """Read an environment variable, then the repo's .env (as `go_transport` does)."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        from pipeline.notify import env
        return env(name) or None
    except Exception:
        return None


def client_for_model(model: str):
    """(client, error). Go models use the Go Google-compatible transport; Gemini gets its own key."""
    try:
        from newsroom.tool_loop.go_transport import go_client, is_go_model
        if is_go_model(model):
            return go_client(), None
        api_key = _env("GEMINI_API_KEY")
        if not api_key:
            return None, "GEMINI_API_KEY is not set"
        from google import genai
        return genai.Client(api_key=api_key), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def split_evidence(evidence: str) -> tuple[str, list]:
    """(the assignment, the tool blocks) — the tool results start at the first "TOOL " line.

    The writer's evidence is the assignment followed by one `TOOL name(args):\\n<result>` block per
    call (newsroom/swarm_dispatcher.py `_execute_agent`). The boundary is what lets a cut fall on the
    tool results only: the assignment is the writer's own instruction and is never dropped.
    """
    text = evidence or ""
    start = None
    for needle in ("\n\n" + TOOL_MARKER, "\n" + TOOL_MARKER):
        idx = text.find(needle)
        if idx >= 0 and (start is None or idx < start):
            start = idx
    if start is None:
        return text, []
    assignment = text[:start]
    rest = text[start:].lstrip("\n")
    parts = rest.split("\n" + TOOL_MARKER)
    # `parts[0]` already starts with TOOL_MARKER (the boundary find stopped on it): prefixing it again
    # both corrupted block 0 and inflated the tool-text length `window_evidence` counts.
    blocks = [parts[0]] + [TOOL_MARKER + part for part in parts[1:]]
    return assignment, [block for block in blocks if block.strip()]


def window_evidence(evidence: str, limit: int | None = None) -> tuple[str, bool]:
    """(the evidence the checker sees, whether it was cut). The assignment is never the part cut.

    Under the limit — any real article, and every article in the 18-article R1 run — the evidence is
    returned whole. Over it, the tool results are cut from the middle outwards (the first and last
    blocks are the ones the writer leaned on), the assignment is kept in full, and a marker carrying
    the exact number of characters dropped is written at the cut.
    """
    limit = EVIDENCE_LIMIT if limit is None else limit
    text = evidence or ""
    if len(text) <= limit:
        return text, False
    assignment, blocks = split_evidence(text)
    tool_text = "\n".join(blocks)
    if not tool_text:
        # Nothing but the assignment: there is no tool text to cut, so the window bounds it and says so.
        kept = assignment[:limit].rstrip()
        marker = TRUNCATION_MARKER.format(missing=len(assignment) - len(kept))
        return f"{kept}\n{marker}", True
    budget = limit - len(assignment)
    if budget <= 0:
        # The assignment alone fills the window (a long brief). Bound it too, and count every dropped
        # character — the assignment's own tail plus all of the tool results — in the marker.
        kept = assignment[:max(limit, 0)].rstrip()
        missing = (len(assignment) - len(kept)) + len(tool_text)
        marker = ASSIGNMENT_TRUNCATION_MARKER.format(missing=missing)
        return f"{kept}\n{marker}", True
    head_len = budget // 2
    tail_len = budget - head_len
    head = tool_text[:head_len].rstrip()
    tail = tool_text[len(tool_text) - tail_len:].lstrip() if tail_len else ""
    # The exact number of characters that are not shown, counted from the slices actually kept.
    missing = len(tool_text) - len(head) - len(tail)
    marker = TRUNCATION_MARKER.format(missing=missing)
    shown = f"{head}\n{marker}\n{tail}" if head else f"{marker}\n{tail}"
    return (f"{assignment.rstrip()}\n\n{shown}" if assignment.strip() else shown), True


def clip_brief(brief: str, limit: int = BRIEF_LIMIT) -> tuple[str, bool]:
    """(the brief the prompt shows, whether it was cut). A cut is marked, never silent."""
    text = (brief or "").strip()
    if len(text) <= limit:
        return text, False
    keep = max(limit - len(BRIEF_TRUNCATION_MARKER), 0)
    kept = text[:keep].rstrip()
    marker = BRIEF_TRUNCATION_MARKER.format(missing=len(text) - len(kept))
    return f"{kept}\n{marker}", True


def render_prompt(text: str, evidence: str = "", brief: str = "") -> tuple[str, bool]:
    """(the prompt, whether the evidence or the assignment was cut)."""
    shown, truncated = window_evidence(evidence)
    brief_text, brief_cut = clip_brief(brief)
    sections = []
    if brief_text:
        sections.append("ASSIGNMENT (what the writer was asked to produce):\n"
                        f"{brief_text}")
    sections.append("EVIDENCE THE WRITER RETRIEVED (the only facts it may use):\n"
                    f"{shown.strip()}")
    sections.append(f"ARTICLE:\n{text or ''}")
    return "\n\n".join(sections), truncated or brief_cut


def build_prompt(text: str, evidence: str = "", brief: str = "") -> str:
    """The prompt alone, for callers that do not need the truncation flag (the CLI keeps this shape)."""
    return render_prompt(text, evidence, brief)[0]


def answer_text(response) -> str:
    """The model's answer text, from `response.text` or the non-thought parts (some Go models only fill parts)."""
    text = getattr(response, "text", "") or ""
    if text.strip():
        return text
    chunks = []
    try:
        for part in response.candidates[0].content.parts:
            if not getattr(part, "thought", False) and getattr(part, "text", None):
                chunks.append(part.text)
    except Exception:
        pass
    return "\n".join(chunks)


def json_blobs(text: str) -> list:
    """Every balanced `{...}` object in the text, string-aware, so nested JSON parses cleanly."""
    blobs, start, depth, in_string, escape = [], None, 0, False, False
    for i, ch in enumerate(text or ""):
        if start is None:
            if ch == "{":
                start, depth, in_string, escape = i, 1, False, False
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blobs.append(text[start:i + 1])
                start = None
    return blobs


# Control characters that JSON forbids raw inside a string, and their escapes. A 60-claim answer that
# quotes an article line breaks exactly here: the model writes a real newline instead of "\n".
_CONTROL_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def _escape_control_chars(blob: str) -> str:
    """Escape raw control characters that sit inside a JSON string; leave the rest of the blob alone.

    The walk is string-aware, so a newline between tokens (legal) is untouched and an escaped sequence
    (`\\n`, `\\\"`, `\\u00e9`) passes through unchanged.
    """
    out, in_string, escape = [], False, False
    for ch in blob:
        if not in_string:
            if ch == '"':
                in_string = True
            out.append(ch)
            continue
        if escape:
            escape = False
            out.append(ch)
        elif ch == "\\":
            escape = True
            out.append(ch)
        elif ch == '"':
            in_string = False
            out.append(ch)
        elif ch < " " or ch == "\x7f":
            out.append(_CONTROL_ESCAPES.get(ch, "\\u%04x" % ord(ch)))
        else:
            out.append(ch)
    return "".join(out)


def _strip_trailing_commas(blob: str) -> str:
    """Drop a comma that sits just before a closing `}` or `]`, outside a string.

    String-aware, so a claim that itself ends in ",}" keeps its text.
    """
    out, in_string, escape = [], False, False
    i, n = 0, len(blob)
    while i < n:
        ch = blob[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            j = i + 1
            while j < n and blob[j] in " \t\r\n":
                j += 1
            if j < n and blob[j] in "}]":
                i += 1                      # the comma was trailing: drop it
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def repair_json(blob: str) -> str:
    """The blob with the two common malformations undone, in the order that keeps both safe."""
    return _strip_trailing_commas(_escape_control_chars(blob))


def parse_answer(text: str) -> dict | None:
    """The last JSON object in the answer that carries a `claims` list, or None.

    A strictly valid blob always wins. Only when none parses is each candidate tried again after
    `repair_json` — a trailing comma, or a raw newline/tab/control character inside a string — so a
    long answer that quotes article text still yields its claims. Never raises.
    """
    try:
        blobs = json_blobs(text or "")
        for repaired in (False, True):           # valid blobs first; repair only when none parses
            for blob in reversed(blobs):
                try:
                    data = json.loads(repair_json(blob) if repaired else blob)
                except Exception:
                    continue
                if isinstance(data, dict) and isinstance(data.get("claims"), list):
                    return data
    except Exception:
        return None
    return None


def normalize_claims(answer: dict | None, max_claims: int = MAX_CLAIMS) -> list:
    """One dict per claim, with exactly `claim`, `verdict`, `evidence`, `derived`, capped."""
    claims = []
    for raw in (answer or {}).get("claims", [])[:max_claims]:
        if not isinstance(raw, dict):
            continue
        claims.append({
            "claim": str(raw.get("claim") or "").strip(),
            "verdict": str(raw.get("verdict") or "").strip().upper(),
            "evidence": str(raw.get("evidence") or "").strip(),
            "derived": bool(raw.get("derived")),
        })
    return claims


def count_claims(claims: list) -> dict:
    """The three verdict counts, with unknown verdicts left uncounted."""
    counts = {"SUPPORTED": 0, "CONTRADICTED": 0, "UNSUPPORTED": 0}
    for claim in claims:
        verdict = str(claim.get("verdict", "")).upper()
        if verdict in counts:
            counts[verdict] += 1
    return counts


def ask_checker(client, model: str, prompt: str, max_claims: int = MAX_CLAIMS,
                truncated: bool = False) -> dict | None:
    """One checker call at temperature 0, low thinking and a thinking-inclusive output cap.

    `truncated` selects the rubric that makes a partial evidence window safe. Returns the parsed
    answer, or None. Transport and SDK errors propagate; `check` swallows them (the CLI prints them).
    """
    from google.genai import types

    config = types.GenerateContentConfig(system_instruction=build_rubric(max_claims, truncated),
                                         temperature=0.0,
                                         max_output_tokens=MAX_OUTPUT_TOKENS)
    try:
        config.thinking_config = types.ThinkingConfig(thinking_level=THINKING_LEVEL)
    except Exception:
        pass
    response = client.models.generate_content(model=model, contents=prompt, config=config)
    return parse_answer(answer_text(response))


# Appended to the same prompt when the first answer did not parse. Short on purpose: the rubric and the
# schema are already in the conversation, so this only says "again, JSON only".
REPAIR_INSTRUCTION = ("Your last answer was not valid JSON. Reply with JSON only, in the schema above, "
                      "no prose.")


def judge(client, model: str, prompt: str, max_claims: int = MAX_CLAIMS,
          truncated: bool = False) -> dict | None:
    """One checker call, and at most one retry when the answer does not parse or carries no claims.

    The retry re-sends the same prompt with `REPAIR_INSTRUCTION` appended and the same rubric, so a
    malformed answer costs one extra call, never a loop. Returns the parsed answer or None; transport
    and SDK errors propagate (`check` swallows them, the CLI prints them). `check` and
    `tools/article_factcheck.verify` both go through here, so production and an offline audit retry
    identically.
    """
    answer = ask_checker(client, model, prompt, max_claims, truncated=truncated)
    if answer is not None and answer.get("claims"):
        return answer
    return ask_checker(client, model, f"{prompt}\n\n{REPAIR_INSTRUCTION}", max_claims,
                       truncated=truncated)


def _skipped(model: str) -> dict:
    """The shape a failed checker returns: gate open, nothing judged, no exception."""
    return {"ok": True, "skipped": SKIP_REASON, "claims": [],
            "counts": {"SUPPORTED": 0, "CONTRADICTED": 0, "UNSUPPORTED": 0}, "model": model}


def check(text: str, evidence: str, model: str | None = None, client=None) -> dict:
    """Judge `text` against `evidence`. Never raises: a checker outage is a skip, not a failure.

    `evidence` is the assignment text plus the tool results the writer received. `client=None`
    builds one for the resolved model; a caller may inject a client (tests, or a shared session).
    """
    resolved = resolve_model(model)
    try:
        if client is None:
            client, _error = client_for_model(resolved)
            if client is None:
                return _skipped(resolved)
        prompt, truncated = render_prompt(text, evidence)
        answer = judge(client, resolved, prompt, MAX_CLAIMS, truncated=truncated)
        if not answer:
            return _skipped(resolved)
        claims = normalize_claims(answer, MAX_CLAIMS)
        counts = count_claims(claims)
        return {"claims": claims, "counts": counts,
                "ok": counts["CONTRADICTED"] == 0, "model": resolved,
                "truncated": truncated}
    except Exception:
        return _skipped(resolved)


def rewrite_prompt(contradicted: list) -> str:
    """One correction message: each contradicted claim, its evidence, and no new claims allowed."""
    items = [c for c in (contradicted or []) if isinstance(c, dict)]
    flagged = [c for c in items if str(c.get("verdict", "")).upper() == "CONTRADICTED"]
    chosen = flagged or items
    if not chosen:
        return ""
    lines = ["The article contradicts the evidence its writer had. Correct or delete each claim "
             "below, using only the evidence quoted. Do not add any new claims and do not "
             "introduce a fact the evidence does not state.", ""]
    for claim in chosen:
        quote = str(claim.get("evidence") or "").strip() or "not in evidence"
        lines.append(f"- CLAIM: {str(claim.get('claim') or '').strip()} — EVIDENCE: {quote}")
    lines += ["", "Rewrite the article now. Change only what these lines require; add no new claims."]
    return "\n".join(lines)
