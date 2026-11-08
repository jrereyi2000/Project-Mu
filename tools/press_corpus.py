"""Press conference corpus statistics and generated-presser evaluation (U1).

This is the research tool for the press-conference plan
(docs/in_progress/PRESS_CONFERENCE_PLAN.md, U1). It reads real postgame
head-coach press-conference transcripts kept locally in
``data/press_corpus/*.txt`` (that directory is git-ignored), splits each
transcript into question and answer turns, and writes a derived statistics
file. Nothing here is imported by prompts: the statistics file is reference
and test material only.

Transcript formats detected
---------------------------
Three layouts are recognised, in this order:

1. **Explicit markers** -- a line begins with ``Q.``/``Q:``/``Question:``/
   ``REPORTER:`` for a question and ``A:``/``Answer:``/``COACH:`` for an
   answer. This is the format the corpus collector emits for ASAP Sports
   transcripts (questions are ``Q.`` paragraphs, answers begin with the
   speaker's name in capitals followed by a colon).
2. **Speaker lines** -- an uppercase ``NAME:`` line starts an answer. A
   ``Q.``-marked line starts a question. Answer paragraphs continue until the
   next question marker. An answer that appears before the first question is
   the opening statement.
3. **Question-mark heuristic** -- no explicit question markers: any paragraph
   ending in ``?`` is a question, and the paragraphs after it (quoted or
   plain) are its answer. A parenthetical line such as ``(Brian Wacker)``
   directly after a question records the reporter. This covers team-website
   transcripts that print questions unmarked and answers in quotation marks.

Front matter
------------
A transcript may start with ``# key: value`` lines (``source``, ``date``,
``season``, ``team``, ``opponent``, ``result``, ``score``, ``tags``,
``speaker``). The collector writes these for the local corpus. The body after
the front matter is never rewritten.

Commands
--------
``python3 -m tools.press_corpus stats [--transcripts DIR] [--out PATH]``
    Parse the corpus, print the statistics, and write the JSON reference file.

``python3 -m tools.press_corpus eval --transcripts DIR [--stats PATH]``
    Parse a directory of generated pressers with the same splitter and compare
    their statistics with the corpus statistics: questions per presser,
    follow-up rate, question length and question-type mix.

Both commands support ``--help``.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import statistics
import sys
from collections import Counter, OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_DIR = ROOT / "data" / "press_corpus"
DEFAULT_STATS_PATH = ROOT / "newsroom" / "config" / "press_corpus_stats.json"

# ---------------------------------------------------------------------------
# Question taxonomy
# ---------------------------------------------------------------------------
#
# Each type carries: a one-line description, the keyword regexes that vote for
# it, and the reporter archetypes (keys in newsroom/config/agent_personas.json)
# that are the natural people to ask that kind of question. The taxonomy is
# descriptive: it is fitted to the corpus, not imposed on the agents.

QUESTION_TYPES = OrderedDict(
    [
        (
            "INJURY",
            {
                "description": "A player's health, availability, injury or practice status.",
                "patterns": [
                    r"\binjur(y|ed|ies)\b",
                    r"\bhurt\b",
                    r"\bhealth(y)?\b",
                    r"\bconcussion\b",
                    r"\bankle\b",
                    r"\bknee\b",
                    r"\bhamstring\b",
                    r"\bshoulder\b",
                    r"\bgroin\b",
                    r"\bribs?\b",
                    r"\bstatus\b",
                    r"\bavailable\b",
                    r"\bIR\b",
                    r"\bsore\b",
                    r"\bstinger\b",
                    r"\bprotocol\b",
                    r"\bquestionable\b",
                    r"\bdoubtful\b",
                    r"\bruled out\b",
                    r"\bmiss(ed|es)? (time|the game|a game)\b",
                    r"\bcan he play\b",
                ],
                "archetypes": ["LOCAL_BEAT_REPORTER", "SCHEFTER", "RAPOPORT"],
            },
        ),
        (
            "ROSTER_PERSONNEL",
            {
                "description": "Who starts, who plays, depth, rotation, signings, releases or trades.",
                "patterns": [
                    r"\bstart(ed|er|ers|ing)?\b",
                    r"\bbackup\b",
                    r"\bbench\b",
                    r"\bdepth\b",
                    r"\brotation\b",
                    r"\bsnap count\b",
                    r"\bsnaps?\b",
                    r"\bline-?up\b",
                    r"\broster\b",
                    r"\breleas(e|ed)\b",
                    r"\bsign(ed|ing)?\b",
                    r"\btrade(d|s)?\b",
                    r"\bwaiver\b",
                    r"\binactive\b",
                    r"\bactive\b",
                    r"\bwho('| i)?s (playing|starting|out)\b",
                ],
                "archetypes": ["LOCAL_BEAT_REPORTER", "SCHEFTER", "SANDO"],
            },
        ),
        (
            "SCHEME_ADJUSTMENT",
            {
                "description": "Scheme, coverage, protection, play calls and halftime adjustments.",
                "patterns": [
                    r"\bscheme\b",
                    r"\bcoverage(s)?\b",
                    r"\bblitz(es|ed)?\b",
                    r"\bpressure\b",
                    r"\bplay[- ]?call(ing|s)?\b",
                    r"\badjust(ed|ment|ments)?\b",
                    r"\bhalftime\b",
                    r"\bformation(s)?\b",
                    r"\bprotection\b",
                    r"\bpersonnel group\b",
                    r"\bmatchup(s)?\b",
                    r"\bgame ?plan\b",
                    r"\bfront\b",
                    r"\bshell\b",
                    r"\bzone\b",
                    r"\bman coverage\b",
                ],
                "archetypes": ["THE_ATHLETIC", "KIMES", "ORLOVSKY", "BARNWELL", "JOYNER"],
            },
        ),
        (
            "PLAYER_PERFORMANCE",
            {
                "description": "One player's game, usage or development.",
                "patterns": [
                    r"\bplay(ed|ing|s)?\b",
                    r"\bperformance\b",
                    r"\btargets?\b",
                    r"\bcarries\b",
                    r"\bcatches\b",
                    r"\byards\b",
                    r"\btouchdowns?\b",
                    r"\bsacks?\b",
                    r"\binterceptions?\b",
                    r"\bdevelopment\b",
                    r"\bstep(ped)? up\b",
                ],
                "archetypes": ["YATES", "LOCAL_BEAT_REPORTER", "BERRY"],
            },
        ),
        (
            "TEAM_EXECUTION",
            {
                "description": "The team's execution: turnovers, penalties, third down, red zone, run/pass game.",
                "patterns": [
                    r"\bexecution\b",
                    r"\bturnovers?\b",
                    r"\bpenalt(y|ies)\b",
                    r"\bthird downs?\b",
                    r"\bred zones?\b",
                    r"\boffenses?\b",
                    r"\bdefenses?\b",
                    r"\brun game\b",
                    r"\bpass(ing)? game\b",
                    r"\brush(ing)?\b",
                    r"\bmistakes?\b",
                    r"\bfundamentals?\b",
                    r"\btackl(e|ing)\b",
                    r"\bblock(ing)?\b",
                    r"\bprotect(ion)?\b",
                    r"\bfinish\b",
                    r"\bdrives?\b",
                ],
                "archetypes": ["LOCAL_BEAT_REPORTER", "BARNWELL", "KIMES"],
            },
        ),
        (
            "COACHING_DECISION",
            {
                "description": "A specific game-management choice: fourth down, clock, timeout, challenge.",
                "patterns": [
                    r"\bfourth downs?\b",
                    r"\b4th downs?\b",
                    r"\bgo for it\b",
                    r"\bpunt(ed|ing)?\b",
                    r"\bfield goals?\b",
                    r"\btwo[- ]point\b",
                    r"\btimeouts?\b",
                    r"\bclock\b",
                    r"\bchallenges?\b",
                    r"\bon-?side\b",
                    r"\bdecisions?\b",
                    r"\breviews?\b",
                ],
                "archetypes": ["BARNWELL", "KIMES", "SIMMONS"],
            },
        ),
        (
            "OPPONENT_NEXT",
            {
                "description": "The opponent just played, or the next game and its preparation.",
                "patterns": [
                    r"\bnext week\b",
                    r"\bnext game\b",
                    r"\bopponents?\b",
                    r"\bmatchups?\b",
                    r"\bprepar(e|ation|ing)\b",
                    r"\bshort week\b",
                    r"\btravel\b",
                    r"\bfacing\b",
                    r"\bcoming up\b",
                    r"\bahead\b",
                ],
                "archetypes": ["LOCAL_BEAT_REPORTER", "YATES"],
            },
        ),
        (
            "SEASON_CONTEXT",
            {
                "description": "Standings, playoffs, streaks, records and where the season is going.",
                "patterns": [
                    r"\bplayoffs?\b",
                    r"\bpostseason\b",
                    r"\bstandings\b",
                    r"\bdivision\b",
                    r"\brecords?\b",
                    r"\bstreaks?\b",
                    r"\bmomentum\b",
                    r"\bseasons?\b",
                    r"\bseeds?\b",
                    r"\brace\b",
                    r"\bcontention\b",
                    r"\bmathematically\b",
                ],
                "archetypes": ["BARNWELL", "SIMMONS", "SANDO"],
            },
        ),
        (
            "EMOTION_STORY",
            {
                "description": "How the coach feels, what the result means, and the human story.",
                "patterns": [
                    r"\bhow do you feel\b",
                    r"\bfeel(ings?|s)?\b",
                    r"\bemotion(s|al)?\b",
                    r"\bexcite(ment|d)?\b",
                    r"\bmessage\b",
                    r"\bproud\b",
                    r"\bdisappoint(ed|ing|ment)?\b",
                    r"\bfrustrat(ed|ing|ion)?\b",
                    r"\bcharacter\b",
                    r"\brespond(ed|s)?\b",
                    r"\breactions?\b",
                    r"\bencouraged\b",
                    r"\bmindset\b",
                    r"\blocker room\b",
                    r"\bwhat does (this|it) mean\b",
                    r"\btalk about (the|this)\b",
                ],
                "archetypes": ["CHADIHA", "EISEN", "CLARK", "HERBSTREIT"],
            },
        ),
        (
            "FRONT_OFFICE_CONTRACT",
            {
                "description": "Job security, contracts, ownership, the front office and the future.",
                "patterns": [
                    r"\bjob security\b",
                    r"\bhot seat\b",
                    r"\bcontracts?\b",
                    r"\bfuture\b",
                    r"\bownership\b",
                    r"\bgeneral manager\b",
                    r"\bGM\b",
                    r"\bfront office\b",
                    r"\bfir(e|ed|ing)\b",
                    r"\bextensions?\b",
                    r"\btrade deadline\b",
                    r"\byears left\b",
                ],
                "archetypes": ["SCHEFTER", "RAPOPORT", "SANDO", "COWHERD"],
            },
        ),
        (
            "PERSONAL_MILESTONE",
            {
                "description": "Records, milestones, history, a return home or a career marker.",
                "patterns": [
                    r"\brecords?\b",
                    r"\bmilestones?\b",
                    r"\bhistory\b",
                    r"\bhistoric\b",
                    r"\bhometown\b",
                    r"\bfamily\b",
                    r"\b(first|100th|1,?000th)\b",
                    r"\bcareer\b",
                    r"\bpassed\b",
                    r"\btied\b",
                    r"\boldest\b",
                    r"\byoungest\b",
                    r"\breturn to\b",
                ],
                "archetypes": ["EISEN", "YATES", "WINGO", "HERBSTREIT"],
            },
        ),
        (
            "OFFICIATING",
            {
                "description": "Officials, flags, replay reviews and disputed calls.",
                "patterns": [
                    r"\bofficial(s)?\b",
                    r"\breferee(s)?\b",
                    r"\bref\b",
                    r"\bflag(s|ged)?\b",
                    r"\bpass interference\b",
                    r"\breplays?\b",
                    r"\bthe (spot|call)\b",
                ],
                "archetypes": ["LOCAL_BEAT_REPORTER", "SKIP_BAYLESS", "STEPHEN_A"],
            },
        ),
        (
            "GAME_MOMENT",
            {
                "description": "A specific play, drive, series or moment in the game.",
                "patterns": [
                    r"\bmoment\b",
                    r"\bsequence\b",
                    r"\bseries\b",
                    r"\bthird[- ]and\b",
                    r"\bfourth[- ]and\b",
                    r"\bwhat happened\b",
                    r"\bwalk (me|us) through\b",
                ],
                "archetypes": ["LOCAL_BEAT_REPORTER", "KIMES", "ORLOVSKY"],
            },
        ),
        (
            "CLARIFICATION",
            {
                "description": "A short clarification, clarification probe or question that fits no other type.",
                "patterns": [],
                "archetypes": ["LOCAL_BEAT_REPORTER"],
            },
        ),
    ]
)

# Precedence when two types score equally: the earlier type wins.
TYPE_PRECEDENCE = list(QUESTION_TYPES)

# The answer-side taxonomy is coarser. A coach's answer is usually a mix of
# several moves, so the primary answer type is the highest-scoring one and the
# others are recorded as secondary hits.
ANSWER_TYPES = OrderedDict(
    [
        (
            "CREDIT_PLAYERS",
            {
                "description": "Credits the players, a unit or a specific performance.",
                "patterns": [r"\bplayers?\b", r"\bguys?\b", r"\bcredit\b", r"\bproud of\b",
                             r"\bthey (made|did|played)\b", r"\bgive (them|him) credit\b"],
            },
        ),
        (
            "TAKE_BLAME",
            {
                "description": "Puts the loss or a mistake on himself.",
                "patterns": [r"\bon me\b", r"\bmy fault\b", r"\bstarts with me\b",
                             r"\bi (have|need) to (be better|do better|coach better)\b",
                             r"\bi put (them|us) in\b", r"\bthat'?s on me\b"],
            },
        ),
        (
            "DEFLECT",
            {
                "description": "Deflects: watch the tape, no update, can't speak to that.",
                "patterns": [r"\bwe'?ll (look|see|watch)\b", r"\blook at the (tape|film)\b",
                             r"\bno update\b", r"\bdon'?t know\b", r"\bcan'?t speak\b",
                             r"\bwe'?ll evaluate\b", r"\bnot going to (get into|talk about)\b"],
            },
        ),
        (
            "ACCOUNTABILITY",
            {
                "description": "Names the problem: mistakes, turnovers, penalties, not good enough.",
                "patterns": [r"\bnot good enough\b", r"\bdisappoint(ed|ing)\b", r"\bmistakes?\b",
                             r"\bturnovers?\b", r"\bpenalt(y|ies)\b", r"\bfix\b", r"\bclean(ed)? up\b"],
            },
        ),
        (
            "SCHEME_TECHNICAL",
            {
                "description": "Explains the football: scheme, coverage, protection, adjustments.",
                "patterns": [r"\bscheme\b", r"\bcoverage\b", r"\bprotection\b", r"\bplay call\b",
                             r"\badjust(ed|ment)?\b", r"\bfront\b", r"\bpressure\b", r"\btechnique\b"],
            },
        ),
        (
            "EMOTION",
            {
                "description": "An emotional register: proud, frustrated, excited, tough.",
                "patterns": [r"\bproud\b", r"\bfrustrat(ed|ing|ion)?\b", r"\bexcit(ed|ing)\b",
                             r"\bemotion(s|al)?\b", r"\btough\b", r"\bhurt\b"],
            },
        ),
        (
            "CLICHE",
            {
                "description": "A stock football phrase: one game at a time, next man up, do our job.",
                "patterns": [r"\bone game at a time\b", r"\bnext man up\b", r"\bdo our job\b",
                             r"\bcontrol what we can\b", r"\bgoing to watch the film\b"],
            },
        ),
        (
            "OTHER",
            {"description": "An answer that fits no other answer type.", "patterns": []},
        ),
    ]
)

ANSWER_TYPE_PRECEDENCE = list(ANSWER_TYPES)

QUESTION_MARKER_RE = re.compile(
    r"^\s*(?:>{0,2}\s*)?(?:\*\*)?(?P<marker>Q|QUESTION|REPORTER|ASK)\s*(?:\.|:)\s*(?:\*\*)?\s*(?P<text>.*)$",
    re.IGNORECASE,
)
ANSWER_MARKER_RE = re.compile(
    r"^\s*(?:\*\*)?(?P<marker>A|ANSWER|COACH)\s*(?:\.|:)\s*(?:\*\*)?\s*(?P<text>.*)$",
    re.IGNORECASE,
)
# A speaker name: every token starts with a capital (which admits "McDANIEL",
# "O'Connell" and all-caps names) and there are at most a few tokens. This
# deliberately rejects ordinary sentences such as "Here's the thing: ...".
SPEAKER_RE = re.compile(
    r"^\s*((?:[A-Z][A-Za-z0-9.'\-]*)(?: (?:[A-Z][A-Za-z0-9.'\-]*))*):\s*(.*)$"
)
SPEAKER_MIN_WORDS = 6
REPORTER_RE = re.compile(r"^\s*\(([^)]{2,80})\)\s*$")
SCORE_LINE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z .'\-]{1,30}?)\s+(\d{1,3})\s*[,;]\s*"
    r"([A-Za-z][A-Za-z .'\-]{1,30}?)\s+(\d{1,3})\s*(\(?(?:OT|OT\)|overtime|FINAL|Final)\)?)?\s*$"
)
FRONT_MATTER_RE = re.compile(r"^\s*#\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
FOLLOW_UP_RE = re.compile(r"^\s*\(?(?:follow[- ]?up|to follow)\b|^\s*follow[- ]?up\s*[:\-]", re.IGNORECASE)
# Transcript artifacts that are printed as "questions" but carry no question:
# "(Indiscernible.)", "(Off microphone)", "(No microphone.)", "(Inaudible)".
NOISE_QUESTION_RE = re.compile(
    r"^\s*[\(\[]?\s*(?:no|off|indiscernible|inaudible|unintelligible|crosstalk|microphone)"
    r"[^\)\]]*[\)\]]?\s*\.?\s*$",
    re.IGNORECASE,
)

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "is", "are", "was", "were", "be", "been", "being", "am",
    "do", "does", "did", "done", "have", "has", "had", "will", "would", "can",
    "could", "should", "may", "might", "must", "shall", "to", "of", "in", "on",
    "at", "by", "for", "with", "about", "from", "into", "over", "after", "before",
    "you", "your", "he", "she", "it", "they", "we", "I", "his", "her", "its",
    "their", "our", "my", "me", "him", "them", "us", "what", "how", "why", "when",
    "where", "who", "which", "there", "here", "so", "just", "like", "up", "out",
    "go", "going", "get", "got", "one", "two", "also", "very", "much", "more",
    "some", "any", "all", "as", "not", "no", "yes", "now", "still", "back",
}

PROBE_PHRASES = {
    "why", "how so", "what happened", "what did you see", "why not",
    "how come", "what changed", "what was that", "what did he say",
    "can you explain", "what do you mean", "and then", "how about",
}


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text or ""))


def clean_space(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", " - ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def content_words(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z'\-]+", (text or "").lower())
    return [w for w in words if w not in STOPWORDS and len(w) >= 3]


def _split_front_matter(raw: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = FRONT_MATTER_RE.match(line)
        if not m:
            break
        meta[m.group(1).lower()] = m.group(2).strip()
        i += 1
    body = "\n".join(lines[i:]) if i else raw
    return meta, body


def _paragraphs(body: str) -> list[str]:
    """Return non-empty logical paragraphs.

    Corpus files put one paragraph per line. Generated pressers may use blank
    lines. Blank lines are dropped and are not significant.
    """
    out = []
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = clean_space(line)
        if line:
            out.append(line)
    return out


def _strip_markdown(text: str) -> str:
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    return text.strip()


def _looks_like_question(text: str) -> bool:
    t = text.strip()
    if not t.endswith("?"):
        return False
    return word_count(t) >= 2


def _speaker_name(line: str) -> str | None:
    m = SPEAKER_RE.match(line)
    if not m:
        return None
    name = m.group(1).strip()
    # Avoid matching ordinary sentences that happen to contain a colon.
    if word_count(name) > 8:
        return None
    if name.upper() in {"Q", "A", "QUESTION", "ANSWER", "NOTE", "NOTES"}:
        return None
    return name


def is_noise_question(text: str) -> bool:
    """True for transcript artifacts that are printed as questions but are not."""
    t = (text or "").strip()
    if not t:
        return True
    if NOISE_QUESTION_RE.match(t):
        return True
    if re.match(r"^\s*\([^)]{0,60}\)\s*\.?\s*$", t) and "?" not in t:
        return True
    if word_count(t) <= 1 and not t.endswith("?"):
        return True
    return False


def _register_pair(pairs: list[dict], question: str, answer: str, reporter: str | None) -> None:
    question = clean_space(_strip_markdown(question)).lstrip("Qq.: ").strip()
    answer = clean_space(answer)
    if not question:
        return
    pairs.append({"question": question, "answer": answer, "reporter": reporter or ""})


# ---------------------------------------------------------------------------
# Transcript splitting
# ---------------------------------------------------------------------------

def split_turns(body: str) -> dict:
    """Split a transcript body into opening, question/answer pairs, game line.

    Returns ``{"opening": str, "pairs": [{"question", "answer", "reporter"}],
    "game": str}``. The splitter documents its own detection in
    ``detect_format``.
    """
    paras = _paragraphs(body)
    has_q_marker = any(QUESTION_MARKER_RE.match(p) for p in paras)
    has_a_marker = any(ANSWER_MARKER_RE.match(p) for p in paras)

    opening_parts: list[str] = []
    pairs: list[dict] = []
    game = ""

    cur_q: str | None = None
    cur_a: list[str] = []
    cur_reporter: str | None = None
    mode = "opening"  # opening | question | answer

    def flush() -> None:
        nonlocal cur_q, cur_a, cur_reporter, mode
        if cur_q is not None:
            _register_pair(pairs, cur_q, " ".join(cur_a), cur_reporter)
        cur_q = None
        cur_a = []
        cur_reporter = None
        mode = "opening"

    for raw_line in paras:
        line = _strip_markdown(raw_line)

        # A score line before any turn is metadata, not speech.
        if not game and not pairs and cur_q is None and SCORE_LINE_RE.match(line):
            game = line
            continue

        # Section headings in team-website layouts ("HEAD COACH ...",
        # "Opening Statement:") are not speech.
        if re.match(r"^\s*opening statement\s*:?\s*$", line, re.I):
            continue
        if (
            not _speaker_name(line)
            and not _looks_like_question(line)
            and re.match(r"^\s*(?:head |assistant )?coach\b", line, re.I)
        ):
            continue

        qm = QUESTION_MARKER_RE.match(line)
        if qm:
            flush()
            cur_q = qm.group("text").strip()
            mode = "question"
            continue

        am = ANSWER_MARKER_RE.match(line)
        if am:
            text = am.group("text").strip()
            if cur_q is not None:
                cur_a.append(text)
                mode = "answer"
            else:
                opening_parts.append(text)
                mode = "opening"
            continue

        rep = REPORTER_RE.match(line)
        if rep:
            if cur_q is not None:
                cur_reporter = rep.group(1).strip()
            mode = "answer" if cur_q is not None else mode
            continue

        speaker = _speaker_name(line)
        if speaker:
            # Everything after the colon is the answer text.
            text = line.split(":", 1)[1].strip()
            if cur_q is not None:
                cur_a.append(text)
                mode = "answer"
            else:
                opening_parts.append(text)
                mode = "opening"
            continue

        # A bare question line: only trusted when the transcript has no
        # explicit Q markers (team-website layout).
        if not has_q_marker and _looks_like_question(line):
            # Quoted questions are answers/quotes, not new questions.
            if not (line.startswith('"') and line.endswith('"')):
                flush()
                cur_q = line
                mode = "question"
                continue

        # Continuation text belongs to the open question (wrapped line) or
        # answer; before either, it is opening / preamble.
        if cur_q is not None:
            if mode == "question" and not cur_a:
                cur_q = (cur_q + " " + line).strip()
            else:
                cur_a.append(line)
                mode = "answer"
        else:
            opening_parts.append(line)
            mode = "opening"

    flush()
    opening = clean_space(" ".join(opening_parts))
    return {"opening": opening, "pairs": pairs, "game": game,
            "format": detect_format(has_q_marker, has_a_marker, pairs)}


def detect_format(has_q_marker: bool, has_a_marker: bool, pairs: list[dict]) -> str:
    if has_q_marker and has_a_marker:
        return "explicit Q/A markers"
    if has_q_marker:
        return "Q markers with speaker lines" if any(p.get("answer") for p in pairs) else "Q markers only"
    if has_a_marker:
        return "A markers with heuristic questions"
    return "question-mark heuristic"


# ---------------------------------------------------------------------------
# Question classification and follow-up detection
# ---------------------------------------------------------------------------

_COMPILED_TYPES = {
    key: [re.compile(p, re.IGNORECASE) for p in spec["patterns"]]
    for key, spec in QUESTION_TYPES.items()
}
_COMPILED_ANSWER_TYPES = {
    key: [re.compile(p, re.IGNORECASE) for p in spec["patterns"]]
    for key, spec in ANSWER_TYPES.items()
}


def classify_question(text: str) -> dict:
    """Rank the question types for a question.

    Returns ``{"type": primary, "scores": {type: hits}, "matched": [...]}``.
    The primary type is the highest-scoring type, with the earlier taxonomy
    entry winning ties. Questions with no keyword hit are CLARIFICATION.
    """
    scores = {}
    for key, patterns in _COMPILED_TYPES.items():
        hits = sum(1 for pat in patterns if pat.search(text))
        if hits:
            scores[key] = hits
    matched = [k for k in TYPE_PRECEDENCE if k in scores]
    if not scores:
        return {"type": "CLARIFICATION", "scores": {}, "matched": []}
    best = max(TYPE_PRECEDENCE, key=lambda k: (scores.get(k, 0), -TYPE_PRECEDENCE.index(k)))
    return {"type": best, "scores": scores, "matched": matched}


def classify_answer(text: str) -> dict:
    """Rank the answer types for an answer.

    Returns ``{"type": primary, "scores": {type: hits}, "matched": [...]}``.
    Answers with no keyword hit are OTHER.
    """
    scores = {}
    for key, patterns in _COMPILED_ANSWER_TYPES.items():
        hits = sum(1 for pat in patterns if pat.search(text or ""))
        if hits:
            scores[key] = hits
    matched = [k for k in ANSWER_TYPE_PRECEDENCE if k in scores]
    if not scores:
        return {"type": "OTHER", "scores": {}, "matched": []}
    best = max(
        ANSWER_TYPE_PRECEDENCE,
        key=lambda k: (scores.get(k, 0), -ANSWER_TYPE_PRECEDENCE.index(k)),
    )
    return {"type": best, "scores": scores, "matched": matched}


def is_follow_up(
    question: str,
    previous_question: str | None,
    previous_answer: str | None,
    previous_was_follow_up: bool = False,
) -> bool:
    """Heuristic follow-up detector.

    A question is a follow-up when it is explicitly marked as one, or when it
    shares at least two content words with the previous question, or when it
    is a short probe (at most 8 words) that opens with an interrogative and
    shares a content word with the previous question. This is deliberately
    conservative: a new question about a different player or topic is not a
    follow-up.
    """
    if FOLLOW_UP_RE.search(question or ""):
        return True
    if not previous_question:
        return False

    q_words = content_words(question)
    pq_words = set(content_words(previous_question))
    qa_words = set(content_words(previous_answer or ""))
    q_set = set(q_words)
    overlap_pq = q_set & pq_words
    overlap_qa = q_set & qa_words
    wc = word_count(question)

    if len(overlap_pq) >= 2:
        return True
    if wc <= 10 and (len(overlap_pq) + len(overlap_qa)) >= 2:
        return True
    # A question that picks up two content words from the answer (a name, a
    # play, a number) is following up on that answer even when it is longer.
    if len(overlap_qa) >= 2 and wc <= 24:
        return True
    if wc <= 8 and overlap_pq and re.match(
        r"^(why|how|what|did|was|is|were|can|could|do|does|are|would|will|who)\b",
        question.strip().lower(),
    ):
        return True
    if wc <= 6 and question.strip().rstrip("?").lower() in PROBE_PHRASES:
        return True
    # A third consecutive short probe on the same exchange is still a follow-up.
    if previous_was_follow_up and wc <= 12 and (overlap_pq or overlap_qa):
        return True
    return False


# ---------------------------------------------------------------------------
# Parsing a transcript file
# ---------------------------------------------------------------------------

def parse_transcript(raw: str, path: str | Path | None = None) -> dict:
    meta, body = _split_front_matter(raw)
    split = split_turns(body)
    pairs = split["pairs"]

    enriched = []
    prev_q = None
    prev_a = None
    prev_follow = False
    for pair in pairs:
        if is_noise_question(pair["question"]):
            continue
        cls = classify_question(pair["question"])
        acls = classify_answer(pair["answer"])
        follow = is_follow_up(pair["question"], prev_q, prev_a, prev_follow)
        enriched.append(
            {
                "question": pair["question"],
                "answer": pair["answer"],
                "reporter": pair.get("reporter", ""),
                "type": cls["type"],
                "matched_types": cls["matched"],
                "answer_type": acls["type"],
                "answer_matched_types": acls["matched"],
                "follow_up": follow,
                "question_words": word_count(pair["question"]),
                "answer_words": word_count(pair["answer"]),
            }
        )
        prev_q, prev_a, prev_follow = pair["question"], pair["answer"], follow

    inferred = _infer_meta(meta, split.get("game", ""))
    return {
        "path": str(path) if path is not None else "",
        "meta": inferred,
        "game_line": split.get("game", ""),
        "format": split.get("format", ""),
        "opening": split.get("opening", ""),
        "pairs": enriched,
    }


def _infer_meta(meta: dict, game_line: str) -> dict:
    out = dict(meta)
    date = out.get("date", "")
    result = (out.get("result") or "").upper()
    score = out.get("score", "")
    raw_tags = out.get("tags", "")
    explicit_tags = bool(raw_tags.strip())
    tags = {t.strip().lower() for t in re.split(r"[;,]", raw_tags) if t.strip()}

    # Infer the season and playoff status from the date.
    season = out.get("season", "")
    playoff = False
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", date)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not season:
            season = str(year if month >= 8 else year - 1)
        playoff = month in (1, 2)
    if season:
        out["season"] = season

    # Infer the result from the score when possible (team score first).
    if score:
        sm = re.search(r"(\d{1,3})\s*-\s*(\d{1,3})", score)
        if sm:
            team_score, opp_score = int(sm.group(1)), int(sm.group(2))
            if not result:
                result = "W" if team_score > opp_score else "L" if team_score < opp_score else "T"
            margin = abs(team_score - opp_score)
            if result == "L" and margin >= 17:
                tags.add("blowout_loss")
            if re.search(r"\bOT\b|overtime", score, re.IGNORECASE):
                tags.add("overtime")
    if result:
        out["result"] = result
        if result == "W":
            tags.add("win")
        elif result == "L":
            tags.add("loss")
        elif result == "T":
            tags.add("tie")
    # When the transcript carries explicit tags, trust them: a January Week 18
    # regular-season game is not a playoff game. Infer from the date only when
    # no tags were supplied (generated pressers, ad-hoc files).
    if playoff and not explicit_tags:
        tags.add("playoff")
    out["tags"] = sorted(tags)
    return out


def load_transcripts(directory: str | Path) -> list[dict]:
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"transcript directory not found: {directory}")
    files = sorted(p for p in directory.glob("*.txt") if p.is_file())
    transcripts = []
    for path in files:
        transcripts.append(parse_transcript(path.read_text(encoding="utf-8", errors="replace"), path))
    return transcripts


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _summary(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None,
                "p10": None, "p90": None}
    vals = sorted(values)
    n = len(vals)

    def pct(p: float) -> float:
        if n == 1:
            return vals[0]
        idx = (n - 1) * p
        lo, hi = math.floor(idx), math.ceil(idx)
        if lo == hi:
            return vals[lo]
        return vals[lo] + (vals[hi] - vals[lo]) * (idx - lo)

    return {
        "n": n,
        "mean": round(statistics.fmean(vals), 3),
        "median": round(statistics.median(vals), 3),
        "min": round(vals[0], 3),
        "max": round(vals[-1], 3),
        "p10": round(pct(0.10), 3),
        "p90": round(pct(0.90), 3),
    }


def _metrics(transcripts: list[dict]) -> dict:
    n_pressers = len(transcripts)
    q_counts = []
    q_lengths = []
    a_lengths = []
    follow_ups = 0
    type_counter: Counter = Counter()
    second_counter: Counter = Counter()
    answer_type_counter: Counter = Counter()
    answer_second_counter: Counter = Counter()
    answered_questions = 0
    opening_lengths = []
    questions_with_player_name = 0
    total_questions = 0

    for t in transcripts:
        pairs = t["pairs"]
        q_counts.append(len(pairs))
        if t.get("opening"):
            opening_lengths.append(word_count(t["opening"]))
        for p in pairs:
            total_questions += 1
            q_lengths.append(p["question_words"])
            if p["answer"]:
                a_lengths.append(p["answer_words"])
                answered_questions += 1
                answer_type_counter[p.get("answer_type", "OTHER")] += 1
                for at in p.get("answer_matched_types", []):
                    if at != p.get("answer_type"):
                        answer_second_counter[at] += 1
            if p["follow_up"]:
                follow_ups += 1
            type_counter[p["type"]] += 1
            for mt in p["matched_types"]:
                if mt != p["type"]:
                    second_counter[mt] += 1
            if re.search(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", p["question"]):
                questions_with_player_name += 1

    type_shares = {
        k: round(type_counter.get(k, 0) / total_questions, 4) if total_questions else 0.0
        for k in QUESTION_TYPES
    }
    if not total_questions:
        type_shares = {k: 0.0 for k in QUESTION_TYPES}
    answer_type_shares = {
        k: round(answer_type_counter.get(k, 0) / answered_questions, 4)
        if answered_questions else 0.0
        for k in ANSWER_TYPES
    }

    return {
        "pressers": n_pressers,
        "questions": total_questions,
        "questions_per_presser": _summary([float(x) for x in q_counts]),
        "follow_up_rate": round(follow_ups / total_questions, 4) if total_questions else 0.0,
        "follow_ups": follow_ups,
        "question_length_words": _summary([float(x) for x in q_lengths]),
        "answer_length_words": _summary([float(x) for x in a_lengths]),
        "opening_length_words": _summary([float(x) for x in opening_lengths]),
        "question_type_counts": {k: type_counter.get(k, 0) for k in QUESTION_TYPES},
        "question_type_shares": type_shares,
        "question_secondary_counts": {k: second_counter.get(k, 0) for k in QUESTION_TYPES},
        "answer_type_counts": {k: answer_type_counter.get(k, 0) for k in ANSWER_TYPES},
        "answer_type_shares": answer_type_shares,
        "answer_secondary_counts": {k: answer_second_counter.get(k, 0) for k in ANSWER_TYPES},
        "answered_questions": answered_questions,
        "question_names_two_word_share": round(
            questions_with_player_name / total_questions, 4
        ) if total_questions else 0.0,
    }


def aggregate(transcripts: list[dict]) -> dict:
    def subset(tag: str) -> list[dict]:
        return [t for t in transcripts if tag in t["meta"].get("tags", [])]

    groups = {
        "all": transcripts,
        "win": subset("win"),
        "loss": subset("loss"),
        "blowout_loss": subset("blowout_loss"),
        "overtime": subset("overtime"),
        "playoff": subset("playoff"),
    }

    teams = sorted({t["meta"].get("team") for t in transcripts if t["meta"].get("team")})
    seasons = sorted({t["meta"].get("season") for t in transcripts if t["meta"].get("season")})
    sources = sorted({t["meta"].get("source") for t in transcripts if t["meta"].get("source")})
    formats = Counter(t.get("format", "") for t in transcripts)

    overall = _metrics(transcripts)
    taxonomy = {}
    for key, spec in QUESTION_TYPES.items():
        taxonomy[key] = {
            "description": spec["description"],
            "keywords": [p.replace("\\b", "") for p in spec["patterns"]],
            "archetypes": spec["archetypes"],
            "count": overall["question_type_counts"].get(key, 0),
            "share": overall["question_type_shares"].get(key, 0.0),
        }
    answer_taxonomy = {}
    for key, spec in ANSWER_TYPES.items():
        answer_taxonomy[key] = {
            "description": spec["description"],
            "keywords": [p.replace("\\b", "") for p in spec["patterns"]],
            "count": overall["answer_type_counts"].get(key, 0),
            "share": overall["answer_type_shares"].get(key, 0.0),
        }

    by_result = {}
    for name, rows in groups.items():
        metrics = _metrics(rows)
        metrics["transcripts"] = len(rows)
        metrics["teams"] = sorted({t["meta"].get("team") for t in rows if t["meta"].get("team")})
        by_result[name] = metrics

    by_season = {}
    by_team = {}
    for season in seasons:
        rows = [t for t in transcripts if t["meta"].get("season") == season]
        m = _metrics(rows)
        m["transcripts"] = len(rows)
        by_season[season] = m
    for team in teams:
        rows = [t for t in transcripts if t["meta"].get("team") == team]
        m = _metrics(rows)
        m["transcripts"] = len(rows)
        by_team[team] = m

    return {
        "generated_by": "tools/press_corpus.py",
        "purpose": "Reference and test material for the press-conference agents. No prompt reads this file.",
        "method": {
            "turn_split": "Q./Question:/REPORTER: markers, else uppercase SPEAKER: lines, else question-mark heuristic",
            "follow_up": "explicit marker, or >=2 shared content words, or a short interrogative probe sharing a content word",
            "classification": "keyword vote per question/answer type; ties go to the earlier taxonomy entry; no hit is CLARIFICATION or OTHER",
            "note": "Transcripts are local and git-ignored; only these derived counts are committed.",
        },
        "corpus": {
            "transcripts": len(transcripts),
            "questions": overall["questions"],
            "teams": teams,
            "team_count": len(teams),
            "seasons": seasons,
            "sources": sources,
            "formats": dict(formats),
        },
        "taxonomy": taxonomy,
        "answer_taxonomy": answer_taxonomy,
        "overall": overall,
        "by_result": by_result,
        "by_season": by_season,
        "by_team": by_team,
    }


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------

def eval_report(generated: dict, corpus: dict) -> dict:
    """Compare generated-presser metrics with the corpus metrics."""
    g = generated["overall"]
    c = corpus["overall"]

    def delta(gv, cv):
        if gv is None or cv is None:
            return None
        return round(gv - cv, 4)

    type_rows = []
    tvd = 0.0
    for key in QUESTION_TYPES:
        gs = g["question_type_shares"].get(key, 0.0)
        cs = c["question_type_shares"].get(key, 0.0)
        tvd += abs(gs - cs)
        type_rows.append(
            {
                "type": key,
                "generated_share": gs,
                "corpus_share": cs,
                "delta": round(gs - cs, 4),
            }
        )
    tvd = round(tvd / 2.0, 4)

    return {
        "generated": {
            "pressers": g["pressers"],
            "questions": g["questions"],
            "questions_per_presser_mean": g["questions_per_presser"]["mean"],
            "follow_up_rate": g["follow_up_rate"],
            "question_length_words_mean": g["question_length_words"]["mean"],
            "answer_length_words_mean": g["answer_length_words"]["mean"],
        },
        "corpus": {
            "pressers": c["pressers"],
            "questions": c["questions"],
            "questions_per_presser_mean": c["questions_per_presser"]["mean"],
            "follow_up_rate": c["follow_up_rate"],
            "question_length_words_mean": c["question_length_words"]["mean"],
            "answer_length_words_mean": c["answer_length_words"]["mean"],
        },
        "deltas": {
            "questions_per_presser_mean": delta(
                g["questions_per_presser"]["mean"], c["questions_per_presser"]["mean"]
            ),
            "follow_up_rate": delta(g["follow_up_rate"], c["follow_up_rate"]),
            "question_length_words_mean": delta(
                g["question_length_words"]["mean"], c["question_length_words"]["mean"]
            ),
            "answer_length_words_mean": delta(
                g["answer_length_words"]["mean"], c["answer_length_words"]["mean"]
            ),
            "question_type_tvd": tvd,
        },
        "type_mix": type_rows,
        "verdict": _verdict(g, c, tvd),
    }


def _verdict(g: dict, c: dict, tvd: float) -> dict:
    """A rough closeness verdict; the numbers are guides for a reviewer."""
    checks = {}
    qpp_g, qpp_c = g["questions_per_presser"]["mean"], c["questions_per_presser"]["mean"]
    checks["questions_per_presser"] = (
        "PASS" if qpp_g is not None and qpp_c is not None and abs(qpp_g - qpp_c) <= 3.0 else "CHECK"
    )
    checks["follow_up_rate"] = (
        "PASS" if abs(g["follow_up_rate"] - c["follow_up_rate"]) <= 0.15 else "CHECK"
    )
    lg, lc = g["question_length_words"]["mean"], c["question_length_words"]["mean"]
    checks["question_length"] = (
        "PASS" if lg is not None and lc is not None and abs(lg - lc) <= 5.0 else "CHECK"
    )
    checks["question_type_mix"] = "PASS" if tvd <= 0.20 else "CHECK"
    return checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_stats(stats: dict, out_path: Path) -> None:
    corpus = stats["corpus"]
    overall = stats["overall"]
    qpp = overall["questions_per_presser"]
    qlen = overall["question_length_words"]
    print("Press conference corpus statistics")
    print("----------------------------------")
    print(f"transcripts: {corpus['transcripts']}  questions: {corpus['questions']}")
    print(f"teams: {corpus['team_count']}  seasons: {', '.join(map(str, corpus['seasons']))}")
    print(f"questions per presser: mean {qpp['mean']}  median {qpp['median']}  "
          f"p10 {qpp['p10']}  p90 {qpp['p90']}  range {qpp['min']}-{qpp['max']}")
    print(f"follow-up rate: {overall['follow_up_rate']:.3f}  "
          f"question length (words): mean {qlen['mean']}  median {qlen['median']}")
    print("question type mix:")
    for key, share in overall["question_type_shares"].items():
        print(f"  {key:<22} {share*100:5.1f}%")
    print("answer type mix:")
    for key, share in overall["answer_type_shares"].items():
        print(f"  {key:<22} {share*100:5.1f}%")
    print("by result:")
    for name, m in stats["by_result"].items():
        if not m["transcripts"]:
            continue
        q = m["questions_per_presser"]["mean"]
        print(f"  {name:<12} n={m['transcripts']:<3} questions/presser={q}  "
              f"follow-up={m['follow_up_rate']:.3f}  q-length={m['question_length_words']['mean']}")
    print(f"wrote {out_path}")


def _print_eval(report: dict, transcripts_dir: Path) -> None:
    print(f"Press conference eval: generated pressers in {transcripts_dir}")
    print("-------------------------------------------------------------")
    labels = [
        ("questions per presser (mean)", "questions_per_presser_mean"),
        ("follow-up rate", "follow_up_rate"),
        ("question length (words, mean)", "question_length_words_mean"),
        ("answer length (words, mean)", "answer_length_words_mean"),
    ]
    print(f"{'metric':<32}{'generated':>12}{'corpus':>12}{'delta':>10}")
    for label, key in labels:
        gv = report["generated"].get(key)
        cv = report["corpus"].get(key)
        dv = report["deltas"].get(key)
        gs = "-" if gv is None else f"{gv:.3f}" if isinstance(gv, float) else str(gv)
        cs = "-" if cv is None else f"{cv:.3f}" if isinstance(cv, float) else str(cv)
        ds = "-" if dv is None else f"{dv:+.3f}"
        print(f"{label:<32}{gs:>12}{cs:>12}{ds:>10}")
    print(f"question-type total variation: {report['deltas']['question_type_tvd']:.3f}")
    print("type mix (generated vs corpus):")
    for row in report["type_mix"]:
        if row["generated_share"] or row["corpus_share"]:
            print(f"  {row['type']:<22} {row['generated_share']*100:5.1f}%  vs "
                  f"{row['corpus_share']*100:5.1f}%  ({row['delta']*100:+.1f}pp)")
    verdict = report["verdict"]
    print("verdict: " + "  ".join(f"{k}={v}" for k, v in verdict.items()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.press_corpus",
        description="Press conference corpus statistics and generated-presser evaluation.",
        epilog=(
            "The corpus lives in data/press_corpus/*.txt and is git-ignored. "
            "The derived statistics file is reference/test material only."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="{stats,eval}")

    p_stats = sub.add_parser(
        "stats",
        help="print corpus statistics and write newsroom/config/press_corpus_stats.json",
        description="Parse the local corpus, print its statistics and write the JSON reference file.",
    )
    p_stats.add_argument(
        "--transcripts",
        default=str(DEFAULT_CORPUS_DIR),
        help=f"directory of transcript .txt files (default: {DEFAULT_CORPUS_DIR})",
    )
    p_stats.add_argument(
        "--out",
        default=str(DEFAULT_STATS_PATH),
        help=f"statistics JSON output path (default: {DEFAULT_STATS_PATH})",
    )
    p_stats.add_argument("--json", action="store_true", help="print the statistics JSON instead of a summary")

    p_eval = sub.add_parser(
        "eval",
        help="compare generated pressers with the corpus statistics",
        description=(
            "Parse generated pressers with the same turn splitter and compare "
            "questions per presser, follow-up rate, question length and type mix "
            "with the committed corpus statistics."
        ),
    )
    p_eval.add_argument(
        "--transcripts",
        required=True,
        help="directory of generated presser .txt files (the same Q/A split applies)",
    )
    p_eval.add_argument(
        "--stats",
        default=str(DEFAULT_STATS_PATH),
        help=f"corpus statistics JSON (default: {DEFAULT_STATS_PATH})",
    )
    p_eval.add_argument("--json", action="store_true", help="print the comparison as JSON")
    return parser


def cmd_stats(args: argparse.Namespace) -> int:
    try:
        transcripts = load_transcripts(args.transcripts)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        print("the raw corpus is local and git-ignored; put transcript .txt files there first", file=sys.stderr)
        return 2
    stats = aggregate(transcripts)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(stats, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        _print_stats(stats, out_path)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    generated = load_transcripts(args.transcripts)
    stats_path = Path(args.stats)
    if not stats_path.exists():
        print(f"corpus statistics not found: {stats_path}", file=sys.stderr)
        print("run `python3 -m tools.press_corpus stats` first", file=sys.stderr)
        return 2
    corpus = json.loads(stats_path.read_text(encoding="utf-8"))
    report = eval_report(aggregate(generated), corpus)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_eval(report, Path(args.transcripts))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    if args.command == "stats":
        return cmd_stats(args)
    if args.command == "eval":
        return cmd_eval(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
