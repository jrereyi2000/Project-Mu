"""
Invented college events that explain each scouting step (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md §2.2, §2.6;
PROJECT_SPEC §2.18). Pure: no DB, every draw seeded.

The engine draws each prospect's consensus move first; generate() then picks an event whose sign and size fit it from
the step's calendar. Events explain the move, they don't cause it. Constraints (hallucination policy D6, sub-plan §2.6):
  - stat categories by position group, writers name the group in words (scouting_value.POSITION_WORDS);
  - regular-season and title-game opponents only from the school's conference (data/scouting/colleges.json), an
    unmapped school plays "a non-conference opponent"; bowls and the playoff are non-conference;
  - combine height and weight are exactly the board's; the drills are drawn from position-and-weight priors and
    tilted by the move, and are Mu's invented combine;
  - class year follows age; only age-21 prospects declare;
  - season production stays within PRODUCTION_BAND percentile points of the prospect's V percentile; an injury is
    always day-to-day (no games missed) and never after INJURY_LAST_STEP; a character note is never a charge or a
    suspension.
Payload keys starting with "_" are hidden (production percentile).
"""
import math
import random

from engines import scouting_value as sv

QUIET = 0.6                     # |move| below this (OVR points): a quiet week, no event
TEXTURE_RANK = 120              # the consensus top N gets a GAME line every playing week regardless
PRODUCTION_BAND = 25
INJURY_LAST_STEP = 21           # nothing injury-like after the bowls; the combine and draft week only clear players
COMBINE_INVITES = 320
SENIOR_BOWL_RANK = 110
SHRINE_RANKS = (50, 200)

PHASES = {"REGULAR": range(1, 14), "CONF_CHAMP": range(14, 15), "BOWLS": range(15, 22), "ALL_STAR": range(22, 24),
          "COMBINE": range(24, 25), "PRO_DAY": range(25, 27), "DRAFT_WEEK": range(27, 28)}

STAT_KEYS = {"QB": ("comp", "att", "pass_yds", "pass_td", "int", "rush_yds"),
             "RB": ("carries", "rush_yds", "rush_td", "rec", "rec_yds"),
             "WR": ("rec", "rec_yds", "rec_td", "targets"),
             "TE": ("rec", "rec_yds", "rec_td", "targets"),
             "OT": ("snaps", "sacks_allowed", "pressures_allowed", "penalties"),
             "IOL": ("snaps", "sacks_allowed", "pressures_allowed", "penalties"),
             "EDGE": ("tackles", "tfl", "sacks", "pressures"),
             "DT": ("tackles", "tfl", "sacks", "pressures"),
             "LB": ("tackles", "tfl", "sacks", "pbu"),
             "CB": ("targets", "catches_allowed", "pbu", "int"),
             "S": ("tackles", "pbu", "int"),
             "K": ("fgm", "fga", "fg_long", "xpm"),
             "P": ("punts", "punt_avg", "inside_20"),
             "LS": ("snaps", "bad_snaps")}

AWARDS = {"QB": "Davey O'Brien Award", "RB": "Doak Walker Award", "WR": "Biletnikoff Award", "TE": "Mackey Award",
          "OT": "Outland Trophy", "IOL": "Outland Trophy", "EDGE": "Bednarik Award", "DT": "Nagurski Trophy",
          "LB": "Butkus Award", "CB": "Thorpe Award", "S": "Thorpe Award", "K": "Lou Groza Award",
          "P": "Ray Guy Award", "LS": "Patrick Mannelly Award"}
BOWLS = ("Citrus Bowl", "Gator Bowl", "Alamo Bowl", "Holiday Bowl", "Music City Bowl", "Las Vegas Bowl",
         "Liberty Bowl", "Pinstripe Bowl", "Sun Bowl", "Texas Bowl", "Armed Forces Bowl", "Birmingham Bowl")
CFP = ("College Football Playoff first round", "College Football Playoff quarterfinal")
INJURY_PARTS = ("ankle", "hamstring", "shoulder", "wrist", "knee", "hip")


def stat_group(position):
    g = sv.group_of(position)
    if g == "SPECIALISTS":
        return (position or "K").upper() if (position or "").upper() in ("K", "P", "LS") else "K"
    return g


def phase(step):
    return next(name for name, r in PHASES.items() if step in r)


def college_week(step):
    return step + 1                     # NFL week 4 ~ college week 5


def _rng(*parts):
    return random.Random(":".join(str(p) for p in parts))


def pos_words(position):
    return sv.POSITION_WORDS.get((position or "").upper(), "prospect")


# ------------------------------------------------------------- schedules ----
def schedule(school_key, conference, draft_season):
    """{step: opponent display name | None (bye) | "a non-conference opponent"} for steps 1-13, seeded per school."""
    rng = _rng("sched", draft_season, school_key)
    members = [m for m in (sv.conference_members(conference) if conference else []) if m != school_key]
    rng.shuffle(members)
    bye = rng.randint(6, 12)
    out, i = {}, 0
    for step in range(1, 14):
        if step == bye:
            out[step] = None
            continue
        if members and (step >= 4 or len(members) >= 9):        # early weeks: non-conference unless a big league
            out[step] = sv.colleges()["schools"][members[i % len(members)]]["display"]
            i += 1
        else:
            out[step] = "a non-conference opponent"
    return out


def title_game(conference, draft_season):
    """The two schools in the conference's title game (step 14), seeded; () for a conference under 4 schools."""
    members = sv.conference_members(conference) if conference else []
    if len(members) < 4:
        return ()
    return tuple(sorted(_rng("title", draft_season, conference).sample(members, 2)))


def postseason(school, draft_season):
    """(step, name, opponent display) for the school's bowl / playoff game in steps 15-20, or None."""
    if not school:
        return None
    rng = _rng("bowl", draft_season, school["key"])
    p = {"P4": 0.7, "FBS": 0.5, "FCS": 0.25}.get(school["level"], 0.15)
    if rng.random() >= p:
        return None
    step = rng.randint(15, 20)
    if school["level"] == "P4" and rng.random() < 0.2:
        name = rng.choice(CFP)
    elif school["level"] in ("P4", "FBS"):
        name = rng.choice(BOWLS)
    else:
        name = f"{school['level']} playoff game"
    others = sorted(k for k, s in sv.colleges()["schools"].items()
                    if s["conference"] != school["conference"] and
                    (s["level"] in ("P4", "FBS")) == (school["level"] in ("P4", "FBS")))
    opp = sv.colleges()["schools"][rng.choice(others)]["display"] if others else "a non-conference opponent"
    return step, name, opp


# ------------------------------------------------------------ production ----
def production_pct(v_pct, move, rng):
    """Season production percentile: the V percentile, tilted by this step's move, within PRODUCTION_BAND."""
    raw = v_pct + 20 * math.tanh(move / 1.5) + rng.gauss(0, 4)
    lo = math.ceil(max(1.0, v_pct - PRODUCTION_BAND) * 10) / 10      # rounded inward: stays inside the band
    hi = math.floor(min(99.0, v_pct + PRODUCTION_BAND) * 10) / 10
    return min(max(round(raw, 1), lo), hi)


def stat_line(group, pct, rng):
    """A one-game line for the group at production percentile pct (0-100). Integers except the punt average."""
    q = pct / 100.0
    r = lambda lo, hi: lo + (hi - lo) * q + rng.gauss(0, (hi - lo) * 0.08)
    n = lambda x, lo=0: max(lo, int(round(x)))
    if group == "QB":
        att = n(r(26, 38), 15)
        comp = min(att, n(att * (0.54 + 0.18 * q + rng.gauss(0, 0.03))))
        return {"comp": comp, "att": att, "pass_yds": n(att * r(5.6, 9.4)), "pass_td": n(r(0.6, 3.8)),
                "int": n(r(1.6, 0.2)), "rush_yds": n(r(-4, 45))}
    if group == "RB":
        c = n(r(11, 24), 5)
        return {"carries": c, "rush_yds": n(c * r(3.6, 6.8)), "rush_td": n(r(0.1, 2.2)), "rec": n(r(0.5, 4)),
                "rec_yds": n(r(3, 40))}
    if group in ("WR", "TE"):
        scale = 1.0 if group == "WR" else 0.7
        t = n(r(4, 12) * scale, 2)
        rec = min(t, n(t * r(0.5, 0.78)))
        return {"rec": rec, "rec_yds": n(rec * r(9, 16)), "rec_td": n(r(0.0, 1.8) * scale), "targets": t}
    if group in ("OT", "IOL"):
        return {"snaps": n(r(58, 74)), "sacks_allowed": n(r(1.3, 0.0)), "pressures_allowed": n(r(4.5, 0.3)),
                "penalties": n(r(1.4, 0.0))}
    if group in ("EDGE", "DT"):
        k = 1.0 if group == "EDGE" else 0.75
        return {"tackles": n(r(2, 7)), "tfl": n(r(0.2, 2.6) * k), "sacks": n(r(0.0, 2.2) * k),
                "pressures": n(r(1, 8) * k)}
    if group == "LB":
        return {"tackles": n(r(4, 13)), "tfl": n(r(0.2, 2.4)), "sacks": n(r(0.0, 1.4)), "pbu": n(r(0.0, 1.6))}
    if group == "CB":
        t = n(r(7, 4), 1)
        return {"targets": t, "catches_allowed": min(t, n(t * r(0.7, 0.3))), "pbu": n(r(0.0, 2.6)),
                "int": n(r(-0.3, 1.3))}
    if group == "S":
        return {"tackles": n(r(3, 10)), "pbu": n(r(0.0, 2.0)), "int": n(r(-0.3, 1.2))}
    if group == "K":
        fga = n(r(1, 4), 1)
        return {"fgm": min(fga, n(fga * r(0.55, 1.0))), "fga": fga, "fg_long": n(r(33, 54)), "xpm": n(r(1, 5))}
    if group == "P":
        return {"punts": n(r(6, 3), 1), "punt_avg": round(r(39.5, 48.5), 1), "inside_20": n(r(0, 3))}
    return {"snaps": n(r(14, 22)), "bad_snaps": 0}


def pl(n, word, plural=None):
    return f"{n} {word if n == 1 else (plural or word + 's')}"


GAME_LINE = {
    "QB": lambda s: f"{s['comp']}-of-{s['att']} for {pl(s['pass_yds'], 'yard')}, {s['pass_td']} TD, {s['int']} INT",
    "RB": lambda s: f"{pl(s['carries'], 'carry', 'carries')} for {pl(s['rush_yds'], 'yard')} and {s['rush_td']} TD, "
                    f"{pl(s['rec'], 'catch', 'catches')}",
    "WR": lambda s: f"{pl(s['rec'], 'catch', 'catches')} for {pl(s['rec_yds'], 'yard')} and {s['rec_td']} TD on "
                    f"{pl(s['targets'], 'target')}",
    "OT": lambda s: f"{s['snaps']} snaps, {pl(s['sacks_allowed'], 'sack')} and {pl(s['pressures_allowed'], 'pressure')} "
                    f"allowed",
    "EDGE": lambda s: f"{pl(s['tackles'], 'tackle')}, {s['tfl']} for loss, {pl(s['sacks'], 'sack')}, "
                      f"{pl(s['pressures'], 'pressure')}",
    "LB": lambda s: f"{pl(s['tackles'], 'tackle')}, {s['tfl']} for loss, {pl(s['sacks'], 'sack')}",
    "CB": lambda s: f"{pl(s['catches_allowed'], 'catch', 'catches')} allowed on {pl(s['targets'], 'target')}, "
                    f"{pl(s['pbu'], 'breakup')}, {s['int']} INT",
    "S": lambda s: f"{pl(s['tackles'], 'tackle')}, {pl(s['pbu'], 'breakup')}, {s['int']} INT",
    "K": lambda s: f"{s['fgm']}-of-{s['fga']} on field goals, long of {s['fg_long']}",
    "P": lambda s: f"{pl(s['punts'], 'punt')} at {s['punt_avg']} yards a kick, {s['inside_20']} inside the 20",
    "LS": lambda s: f"{pl(s['snaps'], 'clean snap')}",
}
GAME_LINE["TE"] = GAME_LINE["WR"]
GAME_LINE["IOL"] = GAME_LINE["OT"]
GAME_LINE["DT"] = GAME_LINE["EDGE"]


# ------------------------------------------------------------- measurables ----
FORTY = {"WR": 4.45, "CB": 4.44, "S": 4.52, "RB": 4.50, "LB": 4.62, "EDGE": 4.68, "TE": 4.68, "QB": 4.76,
         "DT": 4.98, "OT": 5.14, "IOL": 5.17, "K": 4.95, "P": 4.95, "LS": 5.02}
TYPICAL_WT = {"WR": 200, "CB": 192, "S": 205, "RB": 215, "LB": 235, "EDGE": 255, "TE": 250, "QB": 220, "DT": 305,
              "OT": 315, "IOL": 312, "K": 200, "P": 210, "LS": 240}


def measurables(group, height_in, weight, move, rng):
    """Mu's invented combine: height and weight exactly the board's, drills from priors tilted by the move."""
    wt = weight or TYPICAL_WT.get(group, 230)
    tilt = max(-1.0, min(1.0, move / 2.0))
    forty = FORTY.get(group, 4.7) + (wt - TYPICAL_WT.get(group, wt)) * 0.004 - 0.05 * tilt + rng.gauss(0, 0.04)
    heavy = min(max((wt - 190) / 140.0, 0), 1)
    vert = 36.5 - 9 * heavy + 2.5 * tilt + rng.gauss(0, 1.5)
    broad = 124 - 18 * heavy + 4 * tilt + rng.gauss(0, 3)
    cone = 6.9 + 0.8 * heavy - 0.1 * tilt + rng.gauss(0, 0.08)
    bench = 12 + 20 * heavy + 2 * tilt + rng.gauss(0, 2.5)
    return {"height_in": height_in, "weight": weight, "forty": round(forty, 2), "vertical": round(vert * 2) / 2,
            "broad_jump": int(round(broad)), "three_cone": round(cone, 2), "bench": max(4, int(round(bench)))}


def height_text(inches):
    return f"{inches // 12}-{inches % 12}" if inches else None


# --------------------------------------------------------------- generate ----
def _who(p):
    sch = p.get("school")
    school = sch["display"] if sch else (p.get("college") or "").title() or "his school"
    return school, pos_words(p.get("position")), p["full_name"].title()


def _game(p, step, move, rng, opponent, game_type, label):
    group = stat_group(p.get("position"))
    pct = production_pct(p["v_pct"], move, rng)
    stats = stat_line(group, pct, rng)
    school, pw, name = _who(p)
    line = GAME_LINE[group](stats)
    good = move >= QUIET
    bad = move <= -QUIET
    verb = "shines" if good else "struggles" if bad else "steady"
    head = (f"{name} {verb} vs {opponent}" if verb != "steady" else f"{name} steady vs {opponent}")
    tone = (" Scouts came away impressed." if good else " Evaluators left with questions." if bad else "")
    blurb = f"{school} {pw} {name} ({label}): {line}.{tone}"
    return {"kind": "GAME", "payload": {"opponent": opponent, "game_type": game_type, "label": label,
                                        "college_week": college_week(step) if game_type == "REGULAR" else None,
                                        "stats": stats, "stat_group": group, "_production_pct": pct},
            "headline": head, "blurb": blurb}


POS_STORIES = (("breakout", "breaks out", "{name} is turning heads at {school}: coaches say the {pw} has taken a "
                                          "clear step this fall."),
               ("award_watch", "lands on award watch list", "{name} was added to the {award} watch list."),
               ("scheme_up", "thrives in new scheme", "A scheme change at {school} has unlocked the {pw}; his tape "
                                                     "since the switch is his best."),
               ("captain", "named team captain", "{school} named {name} a team captain; coaches praise his work "
                                                 "habits."))
NEG_STORIES = (("rough_outing", "rough outing raises questions", "Scouts flagged inconsistent tape from {name} "
                                                                  "this week."),
               ("scheme_down", "scheme change muddies tape", "A scheme change at {school} has the {pw} playing out "
                                                             "of position, and his tape has dipped."),
               ("injury", "nicked up", "{name} left with a {part} injury and is listed day-to-day; he isn't "
                                        "expected to miss time."),
               ("character", "coaches want more consistency", "{school} coaches publicly challenged {name} to "
                                                              "bring more consistency to practice."))


def _story(p, step, move, rng):
    school, pw, name = _who(p)
    group = stat_group(p.get("position"))
    pool = POS_STORIES if move > 0 else NEG_STORIES
    if move <= 0 and step > INJURY_LAST_STEP:
        pool = tuple(s for s in pool if s[0] != "injury")
    sub, head, text = rng.choice(pool)
    payload = {"subtype": sub}
    fmt = {"name": name, "school": school, "pw": pw, "award": AWARDS.get(group, "national"),
           "part": rng.choice(INJURY_PARTS)}
    if sub == "injury":
        payload.update(part=fmt["part"], status="day-to-day", missed_games=0)
    if sub == "award_watch":
        payload["award"] = fmt["award"]
    return {"kind": "STORYLINE", "payload": payload, "headline": f"{name} {head}", "blurb": text.format(**fmt)}


def generate(step, p, move, consensus_rank, draft_season):
    """Events (0+) for one prospect at one step. p: {key, full_name, position, college, school, age, class_year,
    height_in, weight, v_pct, conference}. Returns [{kind, payload, headline, blurb, impact, seq}]."""
    rng = _rng("event", draft_season, step, p["key"])
    ph = phase(step)
    big = abs(move) >= QUIET
    texture = consensus_rank <= TEXTURE_RANK
    school, pw, name = _who(p)
    out = []
    if ph == "REGULAR":
        sch = schedule(p["school"]["key"] if p.get("school") else p.get("college"), p.get("conference"),
                       draft_season)
        opp = sch.get(step, "a non-conference opponent")
        if opp is not None and (big or texture):
            out.append(_game(p, step, move, rng, opp, "REGULAR", f"college week {college_week(step)}"))
        if big and (opp is None or abs(move) >= 2 * QUIET and rng.random() < 0.5):
            out.append(_story(p, step, move, rng))
    elif ph == "CONF_CHAMP":
        pair = title_game(p.get("conference"), draft_season)
        key = p["school"]["key"] if p.get("school") else None
        if key in pair and (big or texture):
            other = sv.colleges()["schools"][pair[0] if pair[1] == key else pair[1]]["display"]
            out.append(_game(p, step, move, rng, other, "TITLE", f"{p['conference']} championship game"))
        elif big:
            out.append(_story(p, step, move, rng))
    elif ph == "BOWLS":
        post = postseason(p.get("school"), draft_season)
        if post and post[0] == step and (big or texture):
            out.append(_game(p, step, move, rng, post[2], "BOWL", post[1]))
        elif step in (15, 16) and move >= QUIET and consensus_rank <= 40:
            award = AWARDS.get(stat_group(p.get("position")), "national")
            out.append({"kind": "AWARD", "payload": {"award": award, "result": "finalist"},
                        "headline": f"{name} named {award} finalist",
                        "blurb": f"{school}'s {name} is a finalist for the {award}."})
        elif big:
            out.append(_story(p, step, move, rng))
        if int(p.get("age") or 22) == 21 and step == _rng("declare", draft_season, p["key"]).randint(16, 19):
            out.append({"kind": "DECLARE", "payload": {"class_year": p.get("class_year")}, "impact_zero": True,
                        "headline": f"{name} declares for the {draft_season} draft",
                        "blurb": f"{school} {p.get('class_year')} {pw} {name} is skipping his final season of "
                                 f"eligibility and entering the {draft_season} draft."})
    elif ph == "ALL_STAR":
        senior = int(p.get("age") or 22) >= 22
        game = "Senior Bowl" if step == 23 else "Shrine Bowl"
        invited = senior and (consensus_rank <= SENIOR_BOWL_RANK if step == 23
                              else SHRINE_RANKS[0] <= consensus_rank <= SHRINE_RANKS[1])
        if invited:
            good = move >= 0
            out.append({"kind": "ALL_STAR", "payload": {"game": game, "report": "strong" if good else "uneven"},
                        "headline": f"{name} {'stands out' if good else 'uneven'} at {game} practices",
                        "blurb": (f"{name} {'won his reps' if good else 'had an up-and-down week'} at the {game}, "
                                  f"according to scouts on site.")})
        elif big:
            out.append(_story(p, step, move, rng) if step <= INJURY_LAST_STEP else
                       {"kind": "BUZZ", "payload": {"direction": "up" if move > 0 else "down"},
                        "headline": f"{name} {'gaining' if move > 0 else 'losing'} steam before the combine",
                        "blurb": f"Evaluators are {'warming to' if move > 0 else 'cooling on'} the {school} {pw}."})
    elif ph == "COMBINE":
        if consensus_rank <= COMBINE_INVITES:
            m = measurables(stat_group(p.get("position")), p.get("height_in"), p.get("weight"), move, rng)
            ht = height_text(m["height_in"])
            out.append({"kind": "COMBINE", "payload": {"measurables": m, "interviews": "strong" if move > 0 else
                                                       "mixed", "medical": "cleared"},
                        "headline": f"{name} runs {m['forty']} at the combine",
                        "blurb": (f"{name} ({ht or '?'}, {m['weight'] or '?'} lb) ran {m['forty']} in the 40 with a "
                                  f"{m['vertical']}-inch vertical and {m['bench']} bench reps. Medically cleared; "
                                  f"interviews were {'strong' if move > 0 else 'mixed'}.")})
        elif big:
            out.append({"kind": "BUZZ", "payload": {"direction": "up" if move > 0 else "down", "combine": "snub"},
                        "headline": f"{name} {'outworks' if move > 0 else 'misses'} combine snub",
                        "blurb": f"Left off the combine list, {name} {'is still drawing calls' if move > 0 else 'is fading from boards'}."})
    elif ph == "PRO_DAY":
        day = _rng("proday", draft_season, p["key"]).choice((25, 26))
        if day == step and (big or texture):
            good = move >= 0
            out.append({"kind": "PRO_DAY", "payload": {"result": "strong" if good else "flat"},
                        "headline": f"{name} {'impresses' if good else 'flat'} at {school} pro day",
                        "blurb": f"{name} {'helped himself' if good else 'did little to help himself'} at "
                                 f"{school}'s pro day in front of NFL scouts."})
        elif big:
            out.append({"kind": "BUZZ", "payload": {"direction": "up" if move > 0 else "down"},
                        "headline": f"{name} {'rising' if move > 0 else 'sliding'} on boards",
                        "blurb": f"League sources say the {pw} is {'climbing' if move > 0 else 'sliding'} on "
                                 f"draft boards."})
    else:                                                    # DRAFT_WEEK
        if move <= -QUIET and rng.random() < 0.5:
            out.append({"kind": "MEDICAL", "payload": {"result": "cleared", "note": "old injury re-checked"},
                        "headline": f"{name} cleared after medical re-check",
                        "blurb": f"Teams re-checked an old, healed injury for {name} at the final medicals; he was "
                                 f"cleared, but the extra look has him sliding a bit."})
        elif big:
            out.append({"kind": "BUZZ", "payload": {"direction": "up" if move > 0 else "down"},
                        "headline": f"Late buzz: {name} {'climbing' if move > 0 else 'slipping'}",
                        "blurb": f"Draft-week chatter has the {school} {pw} {'climbing' if move > 0 else 'slipping'}."})
    seqs = {}
    for e in out:
        e["seq"] = seqs.get(e["kind"], 0)
        seqs[e["kind"]] = e["seq"] + 1
        e["impact"] = 0.0 if e.pop("impact_zero", False) else round(move, 3)
    return out


def visits(step, teams, candidates, draft_season, per_team=6):
    """Top-30 visits (steps 25-26): each team visits per_team prospects from candidates (the consensus top ~100),
    weighted toward its need groups. teams: {team_id: {need_groups}}; candidates: [{key, group, full_name}].
    Returns [(team_id, key)], seeded."""
    out = []
    for tid in sorted(teams):
        rng = _rng("visit", draft_season, step, tid)
        needs = teams[tid].get("need_groups") or []
        pool = list(candidates)
        weights = [3.0 if c["group"] in needs[:3] else 1.5 if c["group"] in needs else 1.0 for c in pool]
        chosen = set()
        for _ in range(min(per_team, len(pool))):
            k = rng.choices(range(len(pool)), weights=weights)[0]
            chosen.add(pool[k]["key"])
            weights[k] = 0.0
            if not any(weights):
                break
        out += [(tid, k) for k in sorted(chosen)]
    return out


def visit_event(p, team_abbr, team_name):
    _, pw, name = _who(p)
    return {"kind": "VISIT", "payload": {"team": team_abbr}, "impact": 0.0,
            "headline": f"{name} visits the {team_name}",
            "blurb": f"The {team_name} brought in the {pw} for a top-30 visit."}
