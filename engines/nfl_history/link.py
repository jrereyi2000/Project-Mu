"""Real player <-> Madden player linking (docs/in_progress/REAL_NFL_HISTORY_PLAN.md §2.6).

Pure core (normalize_name, real_side, match), thin database wrapper (madden_side, run_link).

match(mu_players, people, overrides) works in this order, each player decided once:
  1. OVERRIDE  a data/history/nfl_link_overrides.csv row for the player's madden_pgid (gsis_id `none` = no real
               person); refused, and reported, when the row's name or position group disagrees with the player.
  2. EXACT / ALIAS  people with the same normalized full name (or one of the player's player_name_aliases), the same
               position group, and entry season (±1) or real 2026 team agreeing. Several: the best score must lead the
               next by MARGIN and agree on entry season, else ambiguous.
  3. FUZZY     no name match at all: one person with the same first initial (or a NICKNAMES first name) and a last
               name within edit distance 2, the same position group, entry season and 2026 team. Linked and listed.
  4. unmatched.
A person claimed by two players makes both ambiguous. An ESPN id on the Madden side (players.espn_id, rarely set)
links by ESPN when the position group and entry season agree.

Evidence weights (WEIGHTS) add to a link's score: position group, entry season (season - years_of_experience vs
rookie_season, ±1), draft year (snapshot: 2027 + year_drafted, ±1; spike S22), college (snapshot), real 2026 team
(data/history/rosters_2026.csv by espn_id), height (±1 in) and weight (±15 lb). nfl_players keeps no jersey number,
so jersey isn't evidence.

run_link(cur, season, snapshot=None, overrides_path=OVERRIDES_CSV, apply=False, db_label=...) always writes the
review report verify_output/nfl_history/link_<db>.md and .csv; with apply it replaces every nfl_player_links row
and records an nfl_history_loads row (kind 'link'), in the caller's transaction.
"""
import collections
import csv
import dataclasses
import json
import os
import re
import unicodedata

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
OVERRIDES_CSV = os.path.join(ROOT, "data", "history", "nfl_link_overrides.csv")
ROSTERS_2026 = os.path.join(ROOT, "data", "history", "rosters_2026.csv")
REPORT_DIR = os.path.join(ROOT, "verify_output", "nfl_history")
OVERRIDE_COLUMNS = ("madden_pgid", "full_name", "position", "gsis_id", "note")

MARGIN = 2.0
FUZZY_MAX_DISTANCE = 2
REAL_ACTIVE_SINCE = 2023
DRAFT_YEAR_BASE = 2027          # snapshot year_drafted is relative: real draft year = 2027 + year_drafted (S22)
WEIGHTS = {"position": 3.0, "entry": 3.0, "draft_year": 2.0, "college": 2.0, "team": 2.0, "height": 1.0,
           "weight": 1.0}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
NICKNAMES = {   # first-name forms that don't share an initial (same-initial ones are covered by the fuzzy rule)
    "bill": "william", "billy": "william", "will": "william", "bob": "robert", "bobby": "robert",
    "rob": "robert", "tony": "anthony", "dick": "richard", "rick": "richard", "ted": "edward", "ed": "edward",
    "ned": "edward", "jack": "john", "johnny": "john", "jim": "james", "jimmy": "james", "jamie": "james",
    "chuck": "charles", "charlie": "charles", "hank": "henry", "peggy": "margaret", "andy": "andrew",
    "drew": "andrew", "dru": "andru", "zeke": "ezekiel", "trey": "william", "deuce": "william", "bo": "robert",
    "sonny": "william", "kurt": "curtis", "kris": "christopher", "chris": "christopher", "topher": "christopher",
    "tre": "treylon", "jt": "justin", "tj": "thomas", "aj": "alfred", "cj": "charles", "dj": "david",
    "pj": "patrick", "kj": "kenneth", "bj": "brandon",
}
# Madden position -> groups a real person's position may be in; nflverse position -> its group
MADDEN_GROUPS = {
    "QB": {"QB"}, "HB": {"RB"}, "FB": {"RB"}, "WR": {"WR"}, "TE": {"TE"},
    "LT": {"OL"}, "LG": {"OL"}, "C": {"OL"}, "RG": {"OL"}, "RT": {"OL"},
    "LE": {"DL", "EDGE", "LB"}, "RE": {"DL", "EDGE", "LB"}, "DT": {"DL"},     # nflverse lists most edge rushers LB
    "LOLB": {"LB", "EDGE"}, "MLB": {"LB"}, "ROLB": {"LB", "EDGE"}, "SAM": {"LB", "EDGE"}, "WILL": {"LB", "EDGE"},
    "MIKE": {"LB"}, "CB": {"CB", "S", "DB"}, "FS": {"CB", "S", "DB"}, "SS": {"CB", "S", "DB"}, "K": {"K"}, "P": {"P"},
    "LS": {"LS", "OL", "TE"},
}
NFL_GROUP = {
    "QB": "QB", "RB": "RB", "FB": "RB", "HB": "RB", "WR": "WR", "TE": "TE",
    "OT": "OL", "T": "OL", "G": "OL", "OG": "OL", "C": "OL", "OL": "OL", "OC": "OL",
    "DE": "EDGE", "OLB": "EDGE", "EDGE": "EDGE", "DT": "DL", "NT": "DL", "DL": "DL",
    "LB": "LB", "ILB": "LB", "MLB": "LB", "CB": "CB", "DB": "DB", "S": "S", "SAF": "S", "FS": "S", "SS": "S",
    "K": "K", "PK": "K", "P": "P", "LS": "LS",
}
# an EDGE-group real person can be a Madden LE/RE/LOLB/ROLB; DE also counts as DL, OLB as LB
NFL_GROUP_ALSO = {"DE": {"DL"}, "OLB": {"LB"}, "DB": {"CB", "S"}}


@dataclasses.dataclass
class MuPlayer:
    player_id: int
    pgid: int | None
    first_name: str
    last_name: str
    position: str | None
    team: str | None = None
    years_of_experience: int | None = None
    age: int | None = None
    overall: int | None = None
    aliases: tuple = ()
    draft_year: int | None = None
    college: str | None = None
    height_in: int | None = None
    weight: int | None = None
    espn_id: str | None = None
    season: int = 2026

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def entry_season(self):
        return None if self.years_of_experience is None else self.season - self.years_of_experience


@dataclasses.dataclass
class RealPerson:
    gsis_id: str
    display_name: str
    first_name: str | None = None
    last_name: str | None = None
    position: str | None = None
    espn_id: str | None = None
    rookie_season: int | None = None
    last_season: int | None = None
    draft_year: int | None = None
    college: str | None = None
    height_in: int | None = None
    weight: int | None = None
    team_2026: str | None = None


@dataclasses.dataclass
class MatchResult:
    links: list          # [{player_id, gsis_id, method, score, evidence}]
    ambiguous: list      # [{player, candidates: [{gsis_id, name, score, evidence}]}]
    fuzzy: list          # the FUZZY links (also in links), for review
    unmatched: list      # [MuPlayer]
    refused: list = dataclasses.field(default_factory=list)       # [{row, reason}]
    no_person: list = dataclasses.field(default_factory=list)     # player ids overridden to `none`


# ---------- names ----------

def normalize_name(s):
    """Lower case, accents stripped, punctuation removed, suffixes dropped, spaces collapsed."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[’'`.]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    from engines.nfl_history.awards import collapse_initials
    return collapse_initials([w for w in s.split() if w not in SUFFIXES])


def _split(name):
    words = normalize_name(name).split()
    return (words[0], " ".join(words[1:])) if words else ("", "")


def edit_distance(a, b):
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _first_names_agree(a, b):
    if not a or not b:
        return False
    if a[0] == b[0]:
        return True
    return NICKNAMES.get(a, a) == NICKNAMES.get(b, b) or NICKNAMES.get(a) == b or NICKNAMES.get(b) == a


def person_names(p):
    names = {normalize_name(p.display_name)}
    if p.first_name and p.last_name:
        names.add(normalize_name(f"{p.first_name} {p.last_name}"))
    names.discard("")
    return names


# ---------- evidence ----------

def position_ok(mu_pos, real_pos):
    groups = MADDEN_GROUPS.get((mu_pos or "").upper())
    rp = (real_pos or "").upper()
    if not groups or rp not in NFL_GROUP:
        return False
    return bool(groups & ({NFL_GROUP[rp]} | NFL_GROUP_ALSO.get(rp, set())))


def _same_school(a, b):
    try:
        from pipeline.colleges import madden_name, same_school
    except Exception:       # pragma: no cover
        return normalize_name(a) == normalize_name(b)
    return same_school(madden_name(a), madden_name(b))


def evidence(mu, p):
    """{key: True/False} for each piece of evidence both sides have."""
    ev = {"position": position_ok(mu.position, p.position)}
    if mu.entry_season is not None and p.rookie_season is not None:
        ev["entry"] = abs(mu.entry_season - p.rookie_season) <= 1
    if mu.draft_year is not None and p.draft_year is not None:
        ev["draft_year"] = abs(mu.draft_year - p.draft_year) <= 1
    if mu.college and p.college:
        ev["college"] = _same_school(mu.college, p.college)
    if mu.team and p.team_2026:
        ev["team"] = mu.team == p.team_2026
    if mu.height_in and p.height_in:
        ev["height"] = abs(mu.height_in - p.height_in) <= 1
    if mu.weight and p.weight:
        ev["weight"] = abs(mu.weight - p.weight) <= 15
    return ev


def score(ev):
    """Agreeing evidence adds its weight, disagreeing evidence takes it away."""
    return round(sum(WEIGHTS[k] * (1 if v else -1) for k, v in ev.items()), 2)


# ---------- sides ----------

def real_side(nfl_players, rosters_2026=(), season=2026):
    """RealPerson for each nfl_players row that could be on a 2026 roster: rookie season <= `season` and (last season
    >= REAL_ACTIVE_SINCE, on rosters_2026.csv, or drafted in `season`); the real 2026 team comes from the roster by
    espn_id (ignored for people whose last season is before 2020: nflverse reuses some ESPN ids, spike S20)."""
    team_by_espn = {}
    for r in rosters_2026:
        if r.get("espn_id"):
            team_by_espn[str(r["espn_id"]).strip()] = (r.get("team") or "").strip() or None
    out = []
    for r in nfl_players:
        get = r.get if isinstance(r, dict) else (lambda k, _r=r: getattr(_r, k, None))
        espn = str(get("espn_id")).strip() if get("espn_id") else None
        rookie, last, draft = get("rookie_season"), get("last_season"), get("draft_year")
        team = team_by_espn.get(espn) if espn else None
        if team and last is not None and last < 2020:
            team = None
        if rookie is not None and rookie > season:
            continue
        if not ((last is not None and last >= REAL_ACTIVE_SINCE) or team or draft == season):
            continue
        out.append(RealPerson(gsis_id=get("gsis_id"), display_name=get("display_name"),
                              first_name=get("first_name"), last_name=get("last_name"),
                              position=get("position"), espn_id=espn, rookie_season=rookie, last_season=last,
                              draft_year=draft, college=get("college"), height_in=get("height_in"),
                              weight=get("weight"), team_2026=team))
    return out


def _height(text):
    m = re.match(r"\s*(\d+)'\s*(\d+)", text or "")
    return int(m.group(1)) * 12 + int(m.group(2)) if m else None


def snapshot_extras(snapshot):
    """{pgid: {"draft_year", "college"}} from a pc_save_reader snapshot (dict or path)."""
    if isinstance(snapshot, str):
        with open(snapshot) as f:
            snapshot = json.load(f)
    try:
        from pipeline.colleges import college_name
    except Exception:       # pragma: no cover
        def college_name(ref):
            return None
    out = {}
    for p in (snapshot or {}).get("players", []):
        yd = p.get("year_drafted")
        draft = None
        if isinstance(yd, int):
            draft = yd if yd > 1900 else DRAFT_YEAR_BASE + yd
        out[p.get("pgid")] = {"draft_year": draft, "college": college_name(p.get("college"))}
    return out


def madden_side(cur, season, snapshot=None):
    """MuPlayer for every players row with a madden_pgid, with its player_seasons row for `season`, its aliases
    and, from the snapshot, draft year and college."""
    cur.execute("SELECT to_regclass('public.player_name_aliases') IS NOT NULL")
    row = cur.fetchone()
    has_aliases = bool(row[0] if not isinstance(row, dict) else list(row.values())[0])
    cur.execute("""
        SELECT p.id, p.madden_pgid, p.player_name, p.full_name, p.height, p.weight, p.espn_id,
               ps.position, t.abbreviation, ps.years_of_experience, ps.age, ps.overall_rating
        FROM players p
        LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
        LEFT JOIN teams t ON t.id = ps.team_id
        WHERE p.madden_pgid IS NOT NULL ORDER BY p.id""", (season,))
    rows = [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]
    aliases = collections.defaultdict(list)
    if has_aliases:
        cur.execute("SELECT player_id, alias FROM player_name_aliases ORDER BY player_id, alias")
        for r in cur.fetchall():
            r = tuple(r.values()) if isinstance(r, dict) else tuple(r)
            aliases[r[0]].append(r[1])
    extras = snapshot_extras(snapshot) if snapshot else {}
    out = []
    for pid, pgid, pname, full, height, weight, espn, pos, team, yoe, age, ovr in rows:
        name = full or pname or ""
        first, _, last = name.partition(" ")
        ex = extras.get(pgid, {})
        out.append(MuPlayer(player_id=pid, pgid=pgid, first_name=first, last_name=last, position=pos, team=team,
                            years_of_experience=yoe, age=age, overall=ovr, aliases=tuple(aliases.get(pid, ())),
                            draft_year=ex.get("draft_year"), college=ex.get("college"), height_in=_height(height),
                            weight=weight, espn_id=str(espn) if espn else None, season=season))
    return out


# ---------- overrides ----------

def read_overrides(path=OVERRIDES_CSV):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("madden_pgid") or "").strip()]


# ---------- match ----------

def _cand(p, ev):
    return {"gsis_id": p.gsis_id, "name": p.display_name, "position": p.position, "rookie_season": p.rookie_season,
            "team_2026": p.team_2026, "score": score(ev), "evidence": ev}


def match(mu_players, people, overrides=()):
    mu_players = sorted(mu_players, key=lambda m: m.player_id)
    by_gsis = {p.gsis_id: p for p in people}
    by_name = collections.defaultdict(list)
    by_last = collections.defaultdict(list)
    by_espn = {}
    for p in sorted(people, key=lambda p: p.gsis_id):
        for n in person_names(p):
            by_name[n].append(p)
            first, last = _split(n)
            if last:
                by_last[last[:1]].append((first, last, p))
        if p.espn_id:
            by_espn[p.espn_id] = p

    decided = {}            # player_id -> link dict
    ambiguous, refused, no_person, fuzzy = [], [], [], []
    ov_by_pgid = {}
    for r in overrides:
        try:
            ov_by_pgid[int(str(r["madden_pgid"]).strip())] = r
        except ValueError:
            refused.append({"row": r, "reason": "madden_pgid is not a number"})
    pending = []
    for mu in mu_players:
        r = ov_by_pgid.get(mu.pgid)
        if r is None:
            pending.append(mu)
            continue
        name_ok = normalize_name(r.get("full_name")) == normalize_name(mu.full_name)
        pos_ok = (r.get("position") or "").strip().upper() in ("", (mu.position or "").upper()) or bool(
            MADDEN_GROUPS.get((r.get("position") or "").strip().upper(), set())
            & MADDEN_GROUPS.get((mu.position or "").upper(), set()))
        if not (name_ok and pos_ok):
            refused.append({"row": r, "reason": f"pgid {mu.pgid} is {mu.full_name} ({mu.position}) in this database"})
            pending.append(mu)
            continue
        gsis = (r.get("gsis_id") or "").strip()
        if gsis.lower() == "none":
            no_person.append(mu.player_id)
            continue
        if gsis not in by_gsis:
            refused.append({"row": r, "reason": f"gsis_id {gsis!r} is not in nfl_players"})
            pending.append(mu)
            continue
        decided[mu.player_id] = {"player_id": mu.player_id, "gsis_id": gsis, "method": "OVERRIDE", "score": None,
                                 "evidence": {"note": r.get("note") or ""}}
    used = {l["gsis_id"] for l in decided.values()}
    known = set(ov_by_pgid)
    for r in overrides:
        try:
            if int(str(r["madden_pgid"]).strip()) not in {m.pgid for m in mu_players}:
                refused.append({"row": r, "reason": "madden_pgid not in this database"})
        except ValueError:
            pass
    del known

    proposals = {}          # player_id -> link dict (methods ESPN / EXACT / ALIAS / FUZZY)
    unmatched = []
    for mu in pending:
        people_avail = lambda ps: [p for p in ps if p.gsis_id not in used]   # noqa: E731
        # ESPN id
        if mu.espn_id and mu.espn_id in by_espn and by_espn[mu.espn_id].gsis_id not in used:
            p = by_espn[mu.espn_id]
            ev = evidence(mu, p)
            if ev["position"] and ev.get("entry", True):
                proposals[mu.player_id] = {"player_id": mu.player_id, "gsis_id": p.gsis_id, "method": "ESPN",
                                           "score": score(ev), "evidence": ev}
                continue
        # exact name, then aliases
        decided_here = False
        for method, names in (("EXACT", [normalize_name(mu.full_name)]),
                              ("ALIAS", [normalize_name(a) for a in mu.aliases])):
            cands = []
            seen = set()
            for n in names:
                for p in people_avail(by_name.get(n, [])):
                    if p.gsis_id in seen:
                        continue
                    seen.add(p.gsis_id)
                    ev = evidence(mu, p)
                    if ev["position"] and (ev.get("entry") or ev.get("team")):
                        cands.append((score(ev), p, ev))
            if not cands:
                continue
            cands.sort(key=lambda c: (-c[0], c[1].gsis_id))
            best = cands[0]
            if len(cands) == 1 or (best[0] - cands[1][0] >= MARGIN and best[2].get("entry")):
                proposals[mu.player_id] = {"player_id": mu.player_id, "gsis_id": best[1].gsis_id, "method": method,
                                           "score": best[0], "evidence": best[2]}
            else:
                ambiguous.append({"player": mu, "candidates": [_cand(p, ev) for _, p, ev in cands]})
            decided_here = True
            break
        if decided_here:
            continue
        if any(by_name.get(n) for n in [normalize_name(mu.full_name)] + [normalize_name(a) for a in mu.aliases]):
            unmatched.append(mu)        # the name exists but no person's evidence agrees
            continue
        # fuzzy
        mf, ml = _split(mu.full_name)
        cands = {}
        if ml:
            for first, last, p in by_last.get(ml[:1], []):
                if p.gsis_id in used or p.gsis_id in cands:
                    continue
                if edit_distance(last, ml) > FUZZY_MAX_DISTANCE or not _first_names_agree(mf, first):
                    continue
                ev = evidence(mu, p)
                if ev["position"] and ev.get("entry") and ev.get("team"):
                    cands[p.gsis_id] = (score(ev), p, ev)
        if len(cands) == 1:
            s, p, ev = next(iter(cands.values()))
            proposals[mu.player_id] = {"player_id": mu.player_id, "gsis_id": p.gsis_id, "method": "FUZZY",
                                       "score": s, "evidence": ev}
        elif len(cands) > 1:
            ambiguous.append({"player": mu, "candidates": [_cand(p, ev) for s, p, ev in
                                                           sorted(cands.values(), key=lambda c: -c[0])]})
        else:
            unmatched.append(mu)

    # a person claimed twice: every claimant is ambiguous
    claims = collections.defaultdict(list)
    for pid, l in proposals.items():
        claims[l["gsis_id"]].append(pid)
    mu_by_id = {m.player_id: m for m in mu_players}
    for gsis, pids in claims.items():
        if len(pids) > 1:
            p = by_gsis[gsis]
            for pid in pids:
                l = proposals.pop(pid)
                ambiguous.append({"player": mu_by_id[pid], "candidates": [_cand(p, l["evidence"])],
                                  "reason": "claimed by several players"})
    decided.update(proposals)
    links = [decided[k] for k in sorted(decided)]
    fuzzy = [l for l in links if l["method"] == "FUZZY"]
    ambiguous.sort(key=lambda a: a["player"].player_id)
    unmatched.sort(key=lambda m: m.player_id)
    return MatchResult(links, ambiguous, fuzzy, unmatched, refused, sorted(no_person))


# ---------- report ----------

def _mu_line(m):
    return (f"{m.full_name} ({m.position or '?'}, {m.team or 'FA'}, OVR {m.overall if m.overall is not None else '?'},"
            f" exp {m.years_of_experience if m.years_of_experience is not None else '?'}, pgid {m.pgid})")


def report_markdown(result, mu_players, people, db_label, season):
    mu_by_id = {m.player_id: m for m in mu_players}
    pby = {p.gsis_id: p for p in people}
    counts = collections.Counter(l["method"] for l in result.links)
    vets = sorted([m for m in result.unmatched if (m.years_of_experience or 0) >= 1],
                  key=lambda m: (-(m.overall or 0), m.player_id))
    rookies = sorted([m for m in result.unmatched if (m.years_of_experience or 0) < 1],
                     key=lambda m: (-(m.overall or 0), m.player_id))
    linked = {l["gsis_id"] for l in result.links}
    left = sorted([p for p in people if (p.last_season or 0) >= season - 1 and p.gsis_id not in linked
                   and (p.rookie_season or 0) < season], key=lambda p: p.display_name)
    n_vets = sum(1 for m in mu_players if (m.years_of_experience or 0) >= 1)
    linked_vets = sum(1 for l in result.links if (mu_by_id[l["player_id"]].years_of_experience or 0) >= 1)
    out = [f"# Real NFL player links: {db_label} (season {season})", "",
           f"Madden players with a pgid: {len(mu_players)}; linked: {len(result.links)}; "
           f"veterans linked: {linked_vets} of {n_vets}"
           + (f" ({100.0 * linked_vets / n_vets:.1f}%)" if n_vets else "") + ".", "",
           "| Method | Links |", "|---|---|"]
    for m in ("OVERRIDE", "ESPN", "EXACT", "ALIAS", "FUZZY"):
        out.append(f"| {m} | {counts.get(m, 0)} |")
    out += ["", f"Ambiguous: {len(result.ambiguous)}. Unmatched veterans: {len(vets)}. Unmatched rookies: "
            f"{len(rookies)}. Overridden to no real person: {len(result.no_person)}. Refused overrides: "
            f"{len(result.refused)}.", "",
            "Fix any line with one row in `data/history/nfl_link_overrides.csv` "
            "(`madden_pgid,full_name,position,gsis_id,note`; gsis_id `none` = no real person) and re-run `link`.", ""]
    if result.refused:
        out += ["## Refused overrides", ""]
        out += [f"- pgid {r['row'].get('madden_pgid')} {r['row'].get('full_name')}: {r['reason']}"
                for r in result.refused]
        out.append("")
    out += ["## Ambiguous", ""]
    for a in result.ambiguous:
        out.append(f"- {_mu_line(a['player'])}" + (f" ({a['reason']})" if a.get("reason") else ""))
        for c in a["candidates"]:
            out.append(f"  - `{c['gsis_id']}` {c['name']} ({c['position']}, rookie {c['rookie_season']}, "
                       f"2026 {c['team_2026'] or '?'}) score {c['score']}: {json.dumps(c['evidence'])}")
    out += ["", "## Fuzzy links (check these)", ""]
    for l in result.fuzzy:
        p = pby[l["gsis_id"]]
        out.append(f"- {_mu_line(mu_by_id[l['player_id']])} -> `{p.gsis_id}` {p.display_name} ({p.position}, "
                   f"rookie {p.rookie_season}) score {l['score']}")
    out += ["", "## Unmatched veterans (by OVR)", ""]
    out += [f"- {_mu_line(m)}" for m in vets]
    out += ["", "## Unmatched rookies (by OVR)", ""]
    out += [f"- {_mu_line(m)}" for m in rookies]
    out += ["", f"## Real players active in {season - 1} with no link ({len(left)})", "",
            "Left the league, or missed (first 300; all in the CSV).", ""]
    out += [f"- `{p.gsis_id}` {p.display_name} ({p.position}, {p.rookie_season}-{p.last_season}, "
            f"2026 {p.team_2026 or '?'})" for p in left[:300]]
    return "\n".join(out) + "\n", left


def write_report(result, mu_players, people, db_label, season, report_dir=REPORT_DIR):
    os.makedirs(report_dir, exist_ok=True)
    md, left = report_markdown(result, mu_players, people, db_label, season)
    md_path = os.path.join(report_dir, f"link_{db_label}.md")
    csv_path = os.path.join(report_dir, f"link_{db_label}.csv")
    with open(md_path, "w") as f:
        f.write(md)
    mu_by_id = {m.player_id: m for m in mu_players}
    pby = {p.gsis_id: p for p in people}
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["status", "player_id", "madden_pgid", "full_name", "position", "team", "overall",
                    "years_of_experience", "gsis_id", "real_name", "method", "score", "evidence"])
        for l in result.links:
            m, p = mu_by_id[l["player_id"]], pby.get(l["gsis_id"])
            w.writerow(["linked", m.player_id, m.pgid, m.full_name, m.position, m.team, m.overall,
                        m.years_of_experience, l["gsis_id"], p.display_name if p else "", l["method"], l["score"],
                        json.dumps(l["evidence"])])
        for a in result.ambiguous:
            m = a["player"]
            for c in a["candidates"]:
                w.writerow(["ambiguous", m.player_id, m.pgid, m.full_name, m.position, m.team, m.overall,
                            m.years_of_experience, c["gsis_id"], c["name"], "", c["score"], json.dumps(c["evidence"])])
        for m in result.unmatched:
            w.writerow(["unmatched", m.player_id, m.pgid, m.full_name, m.position, m.team, m.overall,
                        m.years_of_experience, "", "", "", "", ""])
        for p in left:
            w.writerow(["real_unlinked", "", "", "", p.position, p.team_2026, "", "", p.gsis_id, p.display_name,
                        "", "", ""])
    return md_path, csv_path


# ---------- database wrapper ----------

def _read_people(cur):
    cur.execute("""
        SELECT gsis_id, display_name, first_name, last_name, position, espn_id, rookie_season, last_season,
               draft_year, college, height_in, weight FROM nfl_players ORDER BY gsis_id""")
    names = [d[0] for d in cur.description]
    return [r if isinstance(r, dict) else dict(zip(names, r)) for r in cur.fetchall()]


def read_rosters(path=ROSTERS_2026):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def run_link(cur, season, snapshot=None, overrides_path=OVERRIDES_CSV, apply=False, db_label="db",
             report_dir=REPORT_DIR, rosters_path=ROSTERS_2026):
    """Match this database's Madden players to nfl_players and write the report; with apply, replace every
    nfl_player_links row and record the load (the caller commits)."""
    from engines.nfl_history import store
    people = real_side(_read_people(cur), read_rosters(rosters_path), season)
    mu = madden_side(cur, season, snapshot)
    result = match(mu, people, read_overrides(overrides_path))
    md_path, csv_path = write_report(result, mu, people, db_label, season, report_dir)
    result.report_paths = (md_path, csv_path)
    if apply:
        store.write_links(cur, result.links)
        counts = dict(collections.Counter(l["method"] for l in result.links))
        counts.update(ambiguous=len(result.ambiguous), unmatched=len(result.unmatched),
                      refused=len(result.refused))
        store.record_load(cur, "link", season, season, [], counts, {"report": os.path.relpath(md_path, ROOT)})
    return result
