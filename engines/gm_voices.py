"""
GM voice cards (GM_TALK_VOICE_PLAN §2.1, U1; PROJECT_SPEC §3.3f).

Every GM tenure carries a `gm_profiles.voice_card`: the register he talks in (in speech terms, not adjectives),
two or three lines he would actually say, his verbal tics, the words he never uses, and what he wants out of a
call. The 2026 cast is authored by hand in `data/gm_voices_2026.json`; a GM hired later gets a card from the
persona model (`LLM`) or the deterministic trait template (`TEMPLATE`), so no GM is ever voiceless.

  python3 -m engines.gm_voices show --team ATL [--dsn DSN]
  python3 -m engines.gm_voices apply --file data/gm_voices_2026.json --season 2026 [--dry-run] [--force] [--dsn DSN]
  python3 -m engines.gm_voices generate --team ATL [--no-llm] [--dsn DSN]

The card schema:

  {"register": "10-200 chars, how he talks in speech terms",
   "samples": ["8-160 chars", "8-160 chars"],
   "tics": ["a verbal habit"],
   "avoids": ["a word he never says"],
   "wants": "5-120 chars, what he wants from a call"}

Database commands refuse the live `madden` database and the frozen `madden_2036` archive, like the other engines.
"""
import argparse
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "gm_voices_2026.json"
REFUSED_DBS = {"madden", "madden_2036"}

CARD_KEYS = ("register", "samples", "tics", "avoids", "wants")


# ------------------------------------------------------------------ schema --
def _text(value):
    return value.strip() if isinstance(value, str) else None


def _string_list(value, lo, hi, item_min=1, item_max=160):
    if not isinstance(value, (list, tuple)) or not (lo <= len(value) <= hi):
        return None
    out = []
    for item in value:
        s = _text(item)
        if not s or not (item_min <= len(s) <= item_max):
            return None
        out.append(s)
    return out


def parse_card(obj):
    """A card object -> the validated card (extra keys dropped), or None. A JSON string is parsed first."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return None
    if not isinstance(obj, dict):
        return None
    register = _text(obj.get("register"))
    if not register or not (10 <= len(register) <= 200):
        return None
    samples = _string_list(obj.get("samples"), 2, 3, item_min=8, item_max=160)
    if samples is None:
        return None
    tics = _string_list(obj.get("tics"), 1, 3, item_min=1, item_max=160)
    if tics is None:
        return None
    avoids = _string_list(obj.get("avoids"), 1, 3, item_min=1, item_max=80)
    if avoids is None:
        return None
    wants = _text(obj.get("wants"))
    if not wants or not (5 <= len(wants) <= 120):
        return None
    return {"register": register, "samples": samples, "tics": tics, "avoids": avoids, "wants": wants}


# --------------------------------------------------------------- templates --
_AGG_REGISTER = {
    "high": "starts high and makes you close the gap",
    "low": "lets silence do the work and names no number first",
    "mid": "stays even until there is a reason to push",
}
_RISK_REGISTER = {
    "high": "bets on projection when the tape agrees",
    "low": "wants certainty and fit before he moves",
    "mid": "swings when the price makes sense",
}
_PICKS_REGISTER = {
    "high": "hoards draft picks",
    "low": "spends picks freely",
    "mid": "treats picks as chips",
}
_YOUTH_REGISTER = {
    "youth": "builds on young legs",
    "veteran": "trusts proven veterans",
    "mixed": "weighs youth and experience",
}
_SAMPLES = {
    "high": ["Put your number on the table and let us get somewhere.",
             "I am not here to wait. Tell me what closes this today."],
    "low": ["I have got time. You tell me what you are thinking.",
            "No rush on my end. Walk me through the deal."],
    "mid": ["Give me the real number and I will give you mine.",
            "Let us see if there is a deal that fits both sides."],
}
_TICS = {
    "high": ["says 'look' before he pushes back", "ends calls with 'that is where I am at'"],
    "low": ["repeats your number back before answering", "lets a pause sit before he says 'go on'"],
    "mid": ["says 'fair enough' when he disagrees", "asks 'what else do you have' before closing"],
}
_AVOIDS = {
    "high": ["framework", "process"],
    "low": ["aggressive", "urgent"],
    "mid": ["leverage", "optics"],
}
_WANTS = {
    "high": "a number on the table before the call ends",
    "low": "a deal he can make without overpaying",
    "mid": "a clear number he can take to the owner",
}
_COACH_GM = {
    "register": "Talks like a coach who got the whole building: short, blunt, players by last name.",
    "samples": ["He is a good player. I need to know if he fits what we run.",
                "I will coach him up. You know what I mean."],
    "tics": ["says 'that is football' when he likes a player", "calls everyone 'kid'"],
    "avoids": ["efficiency", "asset"],
    "wants": "a player he can put on the field Sunday",
}


def _band_agg(value):
    return "high" if value >= 0.65 else "low" if value <= 0.35 else "mid"


def _band_risk(value):
    return "high" if value >= 0.6 else "low" if value <= 0.4 else "mid"


def _band_picks(value):
    return "high" if value >= 1.15 else "low" if value <= 0.85 else "mid"


def _band_youth(value):
    return "youth" if value >= 0.35 else "veteran" if value <= -0.35 else "mixed"


def template_card(traits, role="GM"):
    """A deterministic card from a GM's traits. Never fails: the hiring fallback. `role` is "GM" or "COACH_GM"."""
    if role == "COACH_GM":
        return dict(_COACH_GM)
    traits = traits or {}
    try:
        agg = float(traits.get("aggressiveness", 0.5))
        risk = float(traits.get("risk_appetite", 0.5))
        picks = float(traits.get("pick_affinity", 1.0))
        youth = float(traits.get("youth_bias", 0.0))
    except (AttributeError, TypeError, ValueError):
        agg, risk, picks, youth = 0.5, 0.5, 1.0, 0.0
    a, r, p, y = _band_agg(agg), _band_risk(risk), _band_picks(picks), _band_youth(youth)
    card = {
        "register": (f"He {_AGG_REGISTER[a]}, {_RISK_REGISTER[r]}, {_PICKS_REGISTER[p]}, "
                     f"and {_YOUTH_REGISTER[y]}."),
        "samples": list(_SAMPLES[a]),
        "tics": list(_TICS[a]),
        "avoids": list(_AVOIDS[a]),
        "wants": _WANTS[a],
    }
    return parse_card(card) or card


# ------------------------------------------------------------------ render --
def one_line(card):
    """The old `gm_profiles.voice` one-liner: the register plus the first line he would actually say."""
    card = parse_card(card) or card
    return f"{card['register']} Sounds like: \"{card['samples'][0]}\""


def render_card(card):
    """The prompt block: every section the writer needs."""
    card = parse_card(card) or card
    samples = " / ".join(f"\"{s}\"" for s in card["samples"])
    return "\n".join((
        f"REGISTER: {card['register']}",
        f"HOW HE SOUNDS: {samples}",
        f"TICS: {'; '.join(card['tics'])}",
        f"NEVER SAYS: {', '.join(card['avoids'])}",
        f"WANTS: {card['wants']}",
    ))


# ----------------------------------------------------------- authored file --
def load_file(path=DATA_FILE):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def authored_card(abbr, season, path=DATA_FILE, gm_name=None):
    """The authored card for a team and season, or None when the file is for another season, lacks the team, or —
    when `gm_name` is given — belongs to a different man. A card is written for a named GM, so a 2026 replacement
    or a model-named seed must not inherit it (`apply_file` refuses exactly that mismatch)."""
    try:
        data = load_file(path)
    except (OSError, ValueError):
        return None
    if data.get("season") is None or int(data["season"]) != int(season):
        return None
    raw = (data.get("cards") or {}).get(str(abbr).upper())
    if not raw:
        return None
    if gm_name is not None and str((raw or {}).get("gm") or "").strip().lower() != str(gm_name).strip().lower():
        return None
    return parse_card(raw)


# ------------------------------------------------------------------- apply --
def apply_file(cur, path, season, *, dry_run=False, force=False):
    """Update the open GM of each team whose abbreviation is in the file. Writes `voice` (one line), `voice_card`
    and source AUTHORED. A GM-name mismatch is refused and reported unless `force`. Returns a report dict."""
    data = load_file(path)
    rep = {"file_season": data.get("season"), "season": season, "updated": [], "would_update": [],
           "mismatched": [], "missing": [], "invalid": [], "dry_run": dry_run}
    if data.get("season") is not None and int(data["season"]) != int(season):
        rep["error"] = f"file season {data['season']} does not match --season {season}"
        return rep
    for abbr, raw in sorted((data.get("cards") or {}).items()):
        card = parse_card(raw)
        if card is None:
            rep["invalid"].append(abbr)
            continue
        cur.execute("""SELECT g.id, g.gm_name FROM gm_profiles g JOIN teams t ON t.id = g.team_id
                       WHERE t.abbreviation = %s AND g.end_season IS NULL""", (abbr,))
        row = cur.fetchone()
        if not row:
            rep["missing"].append(abbr)
            continue
        gm_id, gm_name = row[0], row[1]
        want = str((raw or {}).get("gm") or "").strip()
        if want and gm_name and want.lower() != gm_name.lower() and not force:
            rep["mismatched"].append({"team": abbr, "file": want, "db": gm_name})
            continue
        if dry_run:
            rep["would_update"].append(abbr)
            continue
        cur.execute("UPDATE gm_profiles SET voice = %s, voice_card = %s, voice_card_source = 'AUTHORED' "
                    "WHERE id = %s", (one_line(card), json.dumps(card), gm_id))
        rep["updated"].append(abbr)
    return rep


# ---------------------------------------------------------------- generate --
def _generate_prompt(team, name, traits):
    return (
        "You are writing a speech card for the general manager of a simulated NFL franchise.\n"
        f"Team: {team}. GM: {name}. Traits: aggressiveness {traits['aggressiveness']}, risk appetite "
        f"{traits['risk_appetite']}, pick affinity {traits['pick_affinity']}, youth bias {traits['youth_bias']}.\n"
        "Write how he talks, not what he is like. Return JSON only:\n"
        '{"register": "one sentence on how he talks, in speech terms, 10-200 characters", '
        '"samples": ["a line he would actually say", "a second, different line"], '
        '"tics": ["a verbal habit"], "avoids": ["a word he never says"], '
        '"wants": "what he wants out of a call"}\n'
        "Samples must be 8-160 characters each; 2-3 samples, 1-3 tics, 1-3 avoids; wants 5-120 characters."
    )


def generate(cur, team, llm=None, log=print):
    """Build and store a card for the open GM of `team`: the model's card (LLM) when it validates, else the trait
    template (TEMPLATE). Returns a report dict."""
    team = str(team).upper()
    cur.execute("""SELECT g.id, g.gm_name, g.aggressiveness, g.risk_appetite, g.pick_affinity, g.youth_bias,
                          g.position_bias
                   FROM gm_profiles g JOIN teams t ON t.id = g.team_id
                   WHERE t.abbreviation = %s AND g.end_season IS NULL""", (team,))
    row = cur.fetchone()
    if not row:
        return {"team": team, "error": "no open GM"}
    gid, name = row[0], row[1]
    traits = {"aggressiveness": float(row[2]), "risk_appetite": float(row[3]), "pick_affinity": float(row[4]),
              "youth_bias": float(row[5]), "position_bias": row[6] or {}}
    card, source = template_card(traits), "TEMPLATE"
    if llm is not None:
        from engines.gm_llm import call
        got = parse_card(call(llm, _generate_prompt(team, name, traits)))
        if got:
            card, source = got, "LLM"
    cur.execute("UPDATE gm_profiles SET voice = %s, voice_card = %s, voice_card_source = %s WHERE id = %s",
                (one_line(card), json.dumps(card), source, gid))
    log(f"  {team}: {name} [{source}]")
    return {"team": team, "gm": name, "source": source, "card": card}


# -------------------------------------------------------------------- CLI --
def _dsn():
    return os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def db_name(dsn):
    """The database name of a DSN (`dbname=x ...` or a URL) or a bare name."""
    s = str(dsn).strip()
    m = re.search(r"(?:^|\s)dbname\s*=\s*([^\s'\"]+)", s)
    if m:
        return m.group(1)
    if "://" in s:
        return s.rsplit("/", 1)[-1].split("?", 1)[0]
    return s


def _connect(dsn):
    name = db_name(dsn)
    if name in REFUSED_DBS:
        raise SystemExit(f"refusing database {name!r} (madden and madden_2036 are refused)")
    import psycopg
    return psycopg.connect(dsn)


def show(cur, team):
    team = str(team).upper()
    cur.execute("""SELECT g.gm_name, g.voice_card, g.voice_card_source
                   FROM gm_profiles g JOIN teams t ON t.id = g.team_id
                   WHERE t.abbreviation = %s AND g.end_season IS NULL""", (team,))
    row = cur.fetchone()
    if not row:
        return f"no open GM for {team}"
    name, card, source = row
    card = parse_card(card) if card else None
    if not card:
        return f"{name} ({team}): no voice card"
    return f"{name} ({team}) [{source}]\n{render_card(card)}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    sh = sub.add_parser("show", help="print a team's stored card")
    sh.add_argument("--team", required=True)
    sh.add_argument("--dsn", default=_dsn())

    ap_ = sub.add_parser("apply", help="apply an authored card file to the open GMs")
    ap_.add_argument("--file", default=str(DATA_FILE))
    ap_.add_argument("--season", type=int, required=True)
    ap_.add_argument("--dry-run", action="store_true", help="print and change nothing")
    ap_.add_argument("--force", action="store_true", help="apply despite a GM-name mismatch")
    ap_.add_argument("--dsn", default=_dsn())

    gen = sub.add_parser("generate", help="build a card for a team's open GM")
    gen.add_argument("--team", required=True)
    gen.add_argument("--season", type=int)
    gen.add_argument("--no-llm", action="store_true")
    gen.add_argument("--dsn", default=_dsn())

    a = ap.parse_args(argv)
    llm = None
    if a.command == "generate":
        from engines.gm_llm import flush, make_llm
        llm = make_llm("gm_voice", enabled=not a.no_llm)

    with _connect(a.dsn) as conn:
        cur = conn.cursor()
        if a.command == "show":
            print(show(cur, a.team))
        elif a.command == "apply":
            rep = apply_file(cur, a.file, a.season, dry_run=a.dry_run, force=a.force)
            if rep.get("error"):
                print(rep["error"])
            print(f"season {a.season}: {len(rep['updated'])} updated, {len(rep['would_update'])} would update, "
                  f"{len(rep['mismatched'])} name mismatches, {len(rep['missing'])} no open GM, "
                  f"{len(rep['invalid'])} invalid cards")
            for m in rep["mismatched"]:
                print(f"  mismatch {m['team']}: file {m['file']!r}, database {m['db']!r} (use --force to apply)")
            if a.dry_run:
                conn.rollback()
                print("dry run: rolled back")
            else:
                conn.commit()
        else:
            rep = generate(cur, a.team, llm)
            if rep.get("error"):
                print(f"{rep['team']}: {rep['error']}")
            else:
                print(f"{rep['team']}: {rep['gm']} [{rep['source']}]")
            conn.commit()
    if a.command == "generate":
        from engines.gm_llm import flush
        flush(llm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
