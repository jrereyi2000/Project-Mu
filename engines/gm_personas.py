"""
GM personas (docs/team_management/03_GM_AGENTS_PLAN.md §2.1, Phase 1; PROJECT_SPEC §3.3f).

One invented GM per team, stable across seasons: traits are deterministic (random.Random(sha256("gm:" + abbr)))
and stratified across the league, so there are always 6-10 aggressive GMs (>= 0.65) and 6-10 passive ones
(<= 0.35). Name, bio and voice come from one Gemini call per GM through the engines.gm_llm seam, with a seeded
name-pool template as the fallback (and whenever the model's answer fails validation). Seeding also creates the
season's gm_team_seasons rows (fo_quality 1.00, step 4 owns it) and every team's own draft picks for the next
three drafts (sub-plan §1.1 C5: 2037-2039 from 2036).

  python3 -m engines.gm_personas seed [--season S] [--replace TEAM] [--no-llm] [--dry-run]
  python3 -m engines.gm_personas backfill-bios [--dry-run]     # LLM bios for GMs still on the template
"""
import argparse
import hashlib
import json
import random
import re

GROUPS = ("QB", "RB", "WR", "TE", "OT", "IOL", "EDGE", "DT", "LB", "CB", "S")
PICK_YEARS = 3                       # tradeable drafts after the current season (C5)
ROUNDS = 7
FIRST_TENURE = 2030                  # no GM tenure starts before 2030
AGGRESSIVE, PASSIVE = 0.65, 0.35     # stratification bands: 6-10 GMs in each
BAND_MIN, BAND_MAX = 6, 10
POSITION_BIAS_MAX_GROUPS = 3
POSITION_BIAS_SPREAD = 0.15

FIRST_NAMES = (
    "Adrian", "Beth", "Calvin", "Dana", "Elliot", "Felicia", "Grant", "Harlan", "Imani", "Jerome", "Kendra",
    "Lowell", "Marcus", "Nadia", "Omar", "Priya", "Quentin", "Renee", "Silas", "Tamsin", "Ulysses", "Vera",
    "Warren", "Xavier", "Yolanda", "Zeke", "Ansel", "Brooks", "Cora", "Desmond", "Everett", "Faye", "Gideon",
    "Hollis", "Ines", "Jasper", "Keaton", "Lorraine", "Malcolm", "Noreen", "Otis", "Pierce", "Rosalind",
    "Sterling", "Theo", "Uma", "Vaughn", "Winston", "Yusuf", "Zora")
LAST_NAMES = (
    "Abernathy", "Bellweather", "Castellano", "Dunmore", "Eckhart", "Fairbanks", "Galloway", "Hargrove",
    "Iverson", "Jessup", "Kowalczyk", "Lindqvist", "Marchetti", "Northcutt", "Okonkwo", "Prescott", "Quaid",
    "Ramsdell", "Sorensen", "Thibodeaux", "Underhill", "Valentine", "Whitlock", "Yarborough", "Zielinski",
    "Ashcroft", "Blackwood", "Crenshaw", "Delacroix", "Ellsworth", "Fontaine", "Greer", "Holloway", "Ingram",
    "Jennings", "Kingsley", "Lockhart", "Mbeki", "Novak", "Oyelaran", "Pemberton", "Radcliffe", "Stroud",
    "Tremblay", "Upton", "Vasquez", "Wakefield", "Yamamoto", "Zamora", "Ridgeway")


def _rng(key):
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest(), 16))


def _order(abbrs, trait):
    """A deterministic league-wide permutation of the teams for one trait."""
    return sorted(abbrs, key=lambda a: hashlib.sha256(f"gm:{trait}:{a}".encode()).hexdigest())


def _stratified(abbrs, trait, lo, hi):
    """One value per team: strata (i + u)/n mapped onto [lo, hi], strata dealt out by a seeded permutation, the
    in-stratum jitter u from the team's own seed. Every stratum is used once, so the league spread is fixed."""
    n = len(abbrs)
    out = {}
    for i, a in enumerate(_order(abbrs, trait)):
        u = _rng(f"gm:{a}:{trait}").random()
        out[a] = round(lo + (hi - lo) * (i + u) / n, 2)
    return out


def league_traits(abbrs):
    """{abbr: {risk_appetite, aggressiveness, pick_affinity, youth_bias, position_bias}} for the whole league.
    aggressiveness spans 0.12-0.88 so the >= 0.65 and <= 0.35 bands hold 9-10 GMs each (32 teams)."""
    abbrs = sorted(abbrs)
    agg = _stratified(abbrs, "aggressiveness", 0.12, 0.88)
    risk = _stratified(abbrs, "risk_appetite", 0.05, 0.95)
    picks = _stratified(abbrs, "pick_affinity", 0.65, 1.35)
    youth = _stratified(abbrs, "youth_bias", -0.9, 0.9)
    return {a: {"aggressiveness": agg[a], "risk_appetite": risk[a], "pick_affinity": picks[a],
                "youth_bias": youth[a], "position_bias": position_bias(_rng("gm:" + a))} for a in abbrs}


def position_bias(rng):
    """0-3 groups the GM over- or under-values, each multiplier within +-15%."""
    k = rng.choice((0, 1, 1, 2, 2, 3))
    groups = rng.sample(GROUPS, k)
    out = {}
    for g in groups:
        m = 1 + rng.choice((-1, 1)) * rng.uniform(0.03, POSITION_BIAS_SPREAD)
        out[g] = round(min(1 + POSITION_BIAS_SPREAD, max(1 - POSITION_BIAS_SPREAD, m)), 2)
    return out


def band_counts(traits):
    vals = [t["aggressiveness"] for t in traits.values()]
    return sum(v >= AGGRESSIVE for v in vals), sum(v <= PASSIVE for v in vals)


def replacement_traits(abbr, season, others):
    """Traits for a new GM replacing one (seed 'gm:<abbr>:<season>'), redrawn until the league's aggressiveness
    bands still hold with the other 31 GMs. others: {abbr: traits} without this team."""
    rng = _rng(f"gm:{abbr}:{season}")
    for _ in range(200):
        t = {"aggressiveness": round(rng.uniform(0.1, 0.9), 2), "risk_appetite": round(rng.uniform(0.05, 0.95), 2),
             "pick_affinity": round(rng.uniform(0.65, 1.35), 2), "youth_bias": round(rng.uniform(-0.9, 0.9), 2),
             "position_bias": position_bias(rng)}
        hi, lo = band_counts(dict(others, **{abbr: t}))
        if BAND_MIN <= hi <= BAND_MAX and BAND_MIN <= lo <= BAND_MAX:
            return t
    return t


# ------------------------------------------------------------------ text ----
def _style(t):
    agg, youth, picks, risk = t["aggressiveness"], t["youth_bias"], t["pick_affinity"], t["risk_appetite"]
    if agg >= AGGRESSIVE:
        a = "keeps the phone lines open and would rather strike early than wait out a market"
    elif agg <= PASSIVE:
        a = "is a patient operator who rarely blinks first in trade talks"
    else:
        a = "is measured in negotiations but moves quickly once the price is right"
    if youth >= 0.35:
        y = "builds around young players and cheap contracts"
    elif youth <= -0.35:
        y = "trusts proven veterans over projection"
    else:
        y = "weighs youth and experience case by case"
    if picks >= 1.15:
        p = "guards draft capital closely"
    elif picks <= 0.85:
        p = "treats picks as currency"
    else:
        p = "values picks like any other asset"
    voice = ["blunt, short sentences" if agg >= 0.6 else "careful, measured phrasing",
             "talks about timelines and the long view" if youth >= 0.2 else "talks about winning now",
             "comfortable with a calculated gamble" if risk >= 0.6 else "stresses certainty and fit"]
    return a, y, p, "; ".join(voice)


def template_persona(abbr, ctx, traits, season, seed_key=None, start=None):
    """Seeded name pool + trait-driven bio and voice. ctx: {city, name (nickname)}. start: the tenure's first
    season (default: seeded, 2030 .. season)."""
    rng = _rng(seed_key or "gm:" + abbr)
    name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    drawn = rng.randint(FIRST_TENURE, max(FIRST_TENURE, season))
    start = start or drawn
    a, y, p, voice = _style(traits)
    bio = (f"{name} has run the {ctx['city']} {ctx['name']} front office since {start}. "
           f"{name.split()[0]} {a}, {y}, and {p}.")
    return {"gm_name": name, "bio": bio, "voice": voice, "start_season": start, "bio_source": "TEMPLATE"}


def persona_prompt(ctx, traits, start_season, taken_names=()):
    coach = ctx.get("coach") or "the head coach"
    return (
        "You are inventing the general manager of a simulated NFL franchise for a fictional league.\n"
        f"Team: {ctx['city']} {ctx['name']}. Head coach: {coach}. The GM has run the front office since "
        f"{start_season}.\n"
        f"Traits (0-1 unless noted): aggressiveness {traits['aggressiveness']}, risk appetite "
        f"{traits['risk_appetite']}, pick affinity {traits['pick_affinity']} (0.6-1.4), youth bias "
        f"{traits['youth_bias']} (-1 veterans .. +1 youth), position bias {json.dumps(traits['position_bias'])}.\n"
        "Write a fictional, invented person. Never use a real NFL executive's name. "
        f"Don't use these names: {', '.join(taken_names) or 'none'}.\n"
        "The bio is 2-3 sentences of colour about how he runs the front office. Don't mention any player, any "
        "specific draft pick or trade, any record, or any number except the year he started.\n"
        "The voice is one line of style notes for how he talks in trade rationale.\n"
        'Answer with JSON only: {"gm_name": "...", "bio": "...", "voice": "..."}')


FORBIDDEN_BIO = re.compile(r"\b(drafted|selected|picked|traded for|acquired)\b", re.I)


def parse_persona(text, start_season, taken_names=(), coach=None):
    """The model's JSON answer -> {gm_name, bio, voice}, or None when it doesn't validate: a 2-3 word alphabetic
    name not already used (nor the coach's), a bio without numbers other than the start year and without claims
    about specific picks or trades."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0) if m else text)
    except (ValueError, AttributeError):
        return None
    name, bio, voice = (str(d.get(k) or "").strip() for k in ("gm_name", "bio", "voice"))
    if not (name and bio and voice) or len(bio) > 600 or len(voice) > 200:
        return None
    if not re.fullmatch(r"[A-Z][A-Za-z'\-]+(?: [A-Z][A-Za-z'\-.]+){1,2}", name):
        return None
    if name.lower() in {n.lower() for n in taken_names} or (coach and name.lower() == coach.lower()):
        return None
    if any(n != str(start_season) for n in re.findall(r"\d+", bio)) or FORBIDDEN_BIO.search(bio):
        return None
    return {"gm_name": name, "bio": bio, "voice": voice}


def make_persona(abbr, ctx, traits, season, llm=None, taken_names=(), seed_key=None, start=None):
    """Template persona, with the name/bio/voice replaced by the LLM's when it answers and validates. A template
    name whose last name is already taken is redrawn from the pool."""
    from engines.gm_llm import call
    base = template_persona(abbr, ctx, traits, season, seed_key, start)
    k = 1
    taken_last = {n.split()[-1].lower() for n in taken_names}
    while base["gm_name"].split()[-1].lower() in taken_last and k < 200:
        alt = template_persona(abbr, ctx, traits, season, f"{seed_key or 'gm:' + abbr}#{k}", base["start_season"])
        base = dict(alt, start_season=base["start_season"])
        k += 1
    text = call(llm, persona_prompt(ctx, traits, base["start_season"], taken_names))
    got = parse_persona(text, base["start_season"], taken_names, ctx.get("coach"))
    if got:
        base.update(got, bio_source="LLM")
    return base


def pick_seed_rows(team_ids, season, years=PICK_YEARS):
    """Every team owns its own picks in rounds 1-7 of the next `years` drafts:
    [(draft_season, round, original_team_id, owner_team_id)]."""
    return [(season + k, r, t, t) for k in range(1, years + 1) for r in range(1, ROUNDS + 1) for t in team_ids]


# -------------------------------------------------------------------- DB ----
def read_teams(cur, season):
    """{abbr: {id, city, name, coach}} (head coach from the open/current tenure)."""
    cur.execute("""SELECT t.id, t.abbreviation, t.city, t.name,
                          (SELECT c.coach_name FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
                           WHERE ct.team_id = t.id AND ct.start_season <= %s
                             AND (ct.end_season IS NULL OR ct.end_season >= %s)
                           ORDER BY ct.start_season DESC LIMIT 1)
                   FROM teams t WHERE t.abbreviation IS NOT NULL ORDER BY t.abbreviation""", (season, season))
    return {r[1]: {"id": r[0], "city": r[2], "name": r[3], "coach": r[4]} for r in cur.fetchall()}


def read_open(cur):
    cur.execute("""SELECT t.abbreviation, g.id, g.gm_name, g.aggressiveness, g.risk_appetite, g.pick_affinity,
                          g.youth_bias, g.position_bias
                   FROM gm_profiles g JOIN teams t ON t.id = g.team_id WHERE g.end_season IS NULL""")
    return {r[0]: {"id": r[1], "gm_name": r[2], "aggressiveness": float(r[3]), "risk_appetite": float(r[4]),
                   "pick_affinity": float(r[5]), "youth_bias": float(r[6]), "position_bias": r[7] or {}}
            for r in cur.fetchall()}


def ensure_gm_persona(cur, gm_id, traits_cat=None):
    """Ensures persona_sliders and persona_traits rows exist in DB for this GM."""
    from newsroom.personas import seed_sliders, seed_traits
    gid = str(gm_id)
    sliders = seed_sliders("gm", gid)
    traits = seed_traits("gm", gid, count=4, traits=traits_cat)
    cur.execute("""
        INSERT INTO persona_sliders (persona_type, persona_id, temperament, sociability,
                                     confidence, openness, warmth, drive, updated_at)
        VALUES ('gm', %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (persona_type, persona_id) DO NOTHING
    """, (gid, sliders["temperament"], sliders["sociability"], sliders["confidence"],
          sliders["openness"], sliders["warmth"], sliders["drive"]))
    for t in traits:
        cur.execute("""
            INSERT INTO persona_traits (persona_type, persona_id, trait_id, active)
            VALUES ('gm', %s, %s, TRUE)
            ON CONFLICT (persona_type, persona_id, trait_id) DO NOTHING
        """, (gid, t))


def get_gm_persona(cur, gm_id):
    """Loads a GM's persona dict from DB or fallback seeding."""
    from newsroom.personas import get_persona
    return get_persona("gm", str(gm_id), cur=cur)


def insert_gm(cur, team_id, p, traits, seed):
    cur.execute("""INSERT INTO gm_profiles (team_id, gm_name, bio, voice, bio_source, start_season,
                                            first_draft_season, risk_appetite, aggressiveness, pick_affinity,
                                            youth_bias, position_bias, seed)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (team_id) WHERE end_season IS NULL DO NOTHING RETURNING id""",
                (team_id, p["gm_name"], p["bio"], p["voice"], p["bio_source"], p["start_season"],
                 p.get("first_draft_season") or p["start_season"],
                 traits["risk_appetite"], traits["aggressiveness"], traits["pick_affinity"], traits["youth_bias"],
                 json.dumps(traits["position_bias"]), seed))
    row = cur.fetchone()
    if row:
        ensure_gm_persona(cur, row[0])
        return row[0]
    return None


def ensure_team_seasons(cur, season):
    """A gm_team_seasons row per team for the season, pointing at the open GM (fo_quality stays 1.00 until step 4
    writes it). Returns rows inserted."""
    cur.execute("""INSERT INTO gm_team_seasons (team_id, season, gm_id)
                   SELECT g.team_id, %s, g.id FROM gm_profiles g WHERE g.end_season IS NULL
                   ON CONFLICT (team_id, season) DO UPDATE SET gm_id = EXCLUDED.gm_id
                   WHERE gm_team_seasons.gm_id IS DISTINCT FROM EXCLUDED.gm_id""", (season,))
    return cur.rowcount


def picks_to_seed(existing, team_ids, season, years=PICK_YEARS):
    """pick_seed_rows minus the picks that already have a row (traded or corrected picks are never touched).
    existing: {(draft_season, round, original_team_id)}."""
    return [r for r in pick_seed_rows(team_ids, season, years) if r[:3] not in existing]


def seed_picks(cur, team_ids, season, years=PICK_YEARS):
    """Own picks for the next `years` drafts; existing rows (traded or corrected picks) are never overwritten."""
    cur.execute("SELECT draft_season, round, original_team_id FROM draft_picks WHERE draft_season > %s", (season,))
    existing = {tuple(r) for r in cur.fetchall()}
    n = 0
    for row in picks_to_seed(existing, team_ids, season, years):
        cur.execute("""INSERT INTO draft_picks (draft_season, round, original_team_id, owner_team_id, source)
                       VALUES (%s, %s, %s, %s, 'SEED') ON CONFLICT DO NOTHING""", row)
        n += cur.rowcount
    return n


def seed(cur, season, llm=None, replace=None, log=print):
    """Seed missing GMs (and, with replace=ABBR, close that team's tenure and seed a new GM), then the season's
    gm_team_seasons rows and the draft picks. Returns a report dict."""
    teams = read_teams(cur, season)
    traits = league_traits(list(teams))
    open_ = read_open(cur)
    rep = {"seeded": [], "replaced": None, "team_seasons": 0, "picks": 0}
    if replace:
        replace = replace.upper()
        if replace not in teams:
            raise SystemExit(f"unknown team {replace}")
        cur.execute("UPDATE gm_profiles SET end_season = %s, end_reason = 'replaced by hand' "
                    "WHERE team_id = %s AND end_season IS NULL", (season, teams[replace]["id"]))
        others = {a: t for a, t in open_.items() if a != replace}
        traits[replace] = replacement_traits(replace, season, others)
        open_.pop(replace, None)
        rep["replaced"] = replace
    taken = [g["gm_name"] for g in open_.values()]
    for abbr, ctx in teams.items():
        if abbr in open_:
            continue
        key = f"gm:{abbr}:{season}" if abbr == replace else "gm:" + abbr
        p = make_persona(abbr, ctx, traits[abbr], season, llm, taken, seed_key=key,
                         start=season if abbr == replace else None)
        if abbr == replace:
            p = dict(p, first_draft_season=season + 1)   # hired after this year's draft (Season scan or by hand)
        gid = insert_gm(cur, ctx["id"], p, traits[abbr], key)
        if gid:
            taken.append(p["gm_name"])
            rep["seeded"].append({"team": abbr, "gm_id": gid, **p, **traits[abbr]})
            log(f"  {abbr:4} {p['gm_name']:24} agg {traits[abbr]['aggressiveness']:.2f} "
                f"risk {traits[abbr]['risk_appetite']:.2f} picks {traits[abbr]['pick_affinity']:.2f} "
                f"youth {traits[abbr]['youth_bias']:+.2f} {json.dumps(traits[abbr]['position_bias'])} "
                f"[{p['bio_source']}] since {p['start_season']}")
    rep["team_seasons"] = ensure_team_seasons(cur, season)
    rep["picks"] = seed_picks(cur, [t["id"] for t in teams.values()], season)
    return rep


def backfill_bios(cur, season, llm, log=print):
    """Rewrite name-pool bios with the LLM for GMs still on the template (name kept: the GM already exists).
    Returns the number rewritten."""
    if llm is None:
        return 0
    from engines.gm_llm import call
    teams = read_teams(cur, season)
    cur.execute("""SELECT g.id, t.abbreviation, g.gm_name, g.start_season, g.aggressiveness, g.risk_appetite,
                          g.pick_affinity, g.youth_bias, g.position_bias
                   FROM gm_profiles g JOIN teams t ON t.id = g.team_id
                   WHERE g.end_season IS NULL AND g.bio_source = 'TEMPLATE'""")
    n = 0
    for gid, abbr, name, start, agg, risk, picks, youth, pb in cur.fetchall():
        tr = {"aggressiveness": float(agg), "risk_appetite": float(risk), "pick_affinity": float(picks),
              "youth_bias": float(youth), "position_bias": pb or {}}
        prompt = persona_prompt(teams[abbr], tr, start) + f"\nThe GM's name is {name}; keep it exactly."
        got = parse_persona(call(llm, prompt), start, (), teams[abbr].get("coach"))
        if got:
            cur.execute("UPDATE gm_profiles SET bio = %s, voice = %s, bio_source = 'LLM' WHERE id = %s",
                        (got["bio"], got["voice"], gid))
            n += 1
            log(f"  {abbr}: LLM bio")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("seed", "backfill-bios"))
    ap.add_argument("--season", type=int)
    ap.add_argument("--replace", metavar="TEAM")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print and roll back")
    a = ap.parse_args(argv)
    import psycopg
    from engines.gm_llm import flush, make_llm
    llm = make_llm("gm_persona", enabled=not a.no_llm)
    with psycopg.connect(_dsn()) as conn:
        cur = conn.cursor()
        season = a.season or _current_season(cur)
        if a.command == "seed":
            rep = seed(cur, season, llm, a.replace)
            print(f"season {season}: {len(rep['seeded'])} GMs seeded, replaced {rep['replaced']}, "
                  f"{rep['team_seasons']} gm_team_seasons rows, {rep['picks']} draft picks")
        else:
            print(f"{backfill_bios(cur, season, llm)} bios rewritten")
        if a.dry_run:
            conn.rollback()
            print("dry run: rolled back")
        else:
            conn.commit()
    flush(llm)


def _dsn():
    import os
    return os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def _current_season(cur):
    cur.execute("SELECT current_season FROM league_state WHERE id = 1")
    return cur.fetchone()[0]


if __name__ == "__main__":
    main()
