"""
The 2037-offseason teleporter (docs/in_progress/PC_MIGRATION_PLAN.md §4.3): matches every Project Mu player to a donor already on
the same team in a Madden 27 PC save, retargets the donor's ratings to the Project Mu OVR, and writes the edits file
pipeline/save_reader/save_writer.mjs applies. Reads the database, never writes it; never touches a save itself.

  python3 -m pipeline.teleport <snapshot.json> [--season 2036] [--edits edits.json] [--report report.json]
                               [--age-add N] [--write-ovr] [--contract-years-add N] [--draft draft.json]
                               [--release] [--appearance] [--no-scan]

<snapshot.json> is pipeline/save_reader/pc_save_reader.mjs's read of the PC save (version 2+). --season is the Project
Mu season whose rosters move (default league_state.current_season). --age-add adds years to every age (1 if Madden
has already aged its players into the new league year and Project Mu hasn't). --write-ovr also writes OverallRating,
OverallGrade0-4 and PlayerType from engines/ovr_weights's predictions, for a Madden that doesn't recompute OVR on load
(a PC check). --contract-years-add moves every contract's current year on by N (Madden's league year ahead of
--season's).
--draft also teleports the draft class and pick owners (pipeline/teleport_draft.py, from
pipeline/save_reader/draft_tables.mjs's read of the same save) into the same edits file.
--release also puts the leftover donors in the edits as "release": [pgid, ...], which save_writer.mjs
sends to free agency (pipeline/save_reader/release.mjs); opt-in until the PC checks show Madden accepts the result.
--appearance also gives each player a generic head and body type matching their AI headshot
(pipeline/teleport_appearance.py).

Why same-team only: Madden ties players to teams through each team's Roster and DepthChart lists, not just
Player.TeamIndex, so moving a player between teams isn't one field. Every Project Mu team has 54-62 players and every
Madden team ~63-65 (signed + practice squad), so each Project Mu player takes over a donor already on that team: an
identity swap. Per team the pairing is an optimal assignment (scipy linear_sum_assignment) on a cost of:
  same position and archetype           |donor's archetype grade - target OVR|
  same position, another archetype      + 15  (PlayerType rewritten)
  a neighbouring position (LT/RT, ...)  + 40  (Position rewritten)
  anything else                         + 120
Donors left over (about 5-10 a team) are listed in the report (leftover_donors) and released with --release, so each
team's Roster and PracticeSquad end up holding exactly its Project Mu players.

Per pair the edits set name, age, years pro, jersey, height, weight, dev trait, position / archetype when they change,
the ratings, and PLYR_COMMENT (pipeline/commentary_names, 0 when the surname has no recording). Ratings: a player with
a PS5 attribute scan (player_attributes, §2.3; the latest of --season or before) gets the scanned ratings as read, so
the scan's OVR is theirs (scanned_ratings; moved only by Project Mu's OVR change when --season is past the scan's
season); --no-scan turns this off. Everyone else gets the donor's ratings moved by engines/ovr_weights.adjust_ratings
to the Project Mu OVR for the archetype, then settled. Contracts: pipeline/teleport_contracts adds the player's ACTIVE Project Mu contract to the edit, written into
the donor's own PlayerContract record (no Project Mu contract, or a practice-squad donor with no record: the donor's
contract stays; counted in the report). With --appearance it adds GenericHeadAssetName,
PLYR_GENERICHEAD, PLYR_ASSETNAME, CharacterBodyType and CharacterVisuals skinTone. Not yet: portraits.
The draft class and pick owners are pipeline/teleport_draft.py (--draft).
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

import numpy as np
import psycopg
from scipy.optimize import linear_sum_assignment

from engines import ovr_weights as ow
from pipeline import commentary_names as cn
from pipeline import teleport_contracts
from pipeline import teleport_appearance as ta
from pipeline.load_save import POSITIONS, TEAM_ABBR_ALIASES, archetype as mu_archetype_name

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
WEIGHT_OFFSET = 160      # Madden stores weight as pounds - 160 (pipeline/save_reader/lib.mjs)

TO_MADDEN_POSITION = {mu: madden for madden, mu in POSITIONS.items()}       # LEDG -> LE, MIKE -> MLB, ...
NEIGHBOURS = [{"LT", "RT"}, {"LG", "RG", "C"}, {"LE", "RE"}, {"LOLB", "ROLB", "MLB"}, {"FS", "SS"}, {"K", "P"},
              {"HB", "FB"}, {"WR", "TE"}, {"DT", "LE", "RE"}, {"CB", "FS", "SS"}]
DEV_TO_MADDEN = {"Normal": "Normal", "Star": "Star", "Superstar": "Superstar", "Superstar X-Factor": "XFactor",
                 "X-Factor": "XFactor", "Hidden": "Hidden"}
DONOR_STATUSES = ("Signed", "PracticeSquad")
SKIP_POSITIONS = ("DEF",)       # Project Mu's team-defense rows, not players
SAME_ARCHETYPE, OTHER_ARCHETYPE, NEIGHBOUR, ANYWHERE = 0, 15, 40, 120


# ------------------------------------------------------------------ mapping ----
def _key(text):
    return re.sub(r"[^a-z]", "", (text or "").lower())


def madden_archetype(doc, madden_position, mu_archetype):
    """Project Mu's archetype text ('Power Back', '• Power') -> the Madden PlayerType of that position, or None."""
    want = _key(mu_archetype)
    if not want:
        return None
    for arch in doc["slots"].get(madden_position, []):
        if arch and _key(mu_archetype_name(arch)) == want:
            return arch
    return None


def split_name(full_name, player_name):
    """'KC Concepcion Jr' -> ('KC', 'Concepcion Jr'); falls back to player_name 'G. Crawford'."""
    name = (full_name or player_name or "").strip()
    first, _, last = name.partition(" ")
    return first.rstrip("."), last


def height_inches(text):
    m = re.match(r"\s*(\d+)'\s*(\d+)", text or "")
    return int(m.group(1)) * 12 + int(m.group(2)) if m else None


def surname_key(last):
    return _key(re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$", "", last or "", flags=re.I))


# ----------------------------------------------------------------- matching ----
def pair_cost(mu, donor, doc):
    """Cost of turning `donor` into `mu` (see the module docstring). A scanned player's ratings replace the donor's,
    so only position and archetype count."""
    target = None if mu.get("scan") else mu["overall"]
    arch = mu["madden_archetype"]
    same_pos = donor["position"] == mu["madden_position"]
    if same_pos and arch == donor["archetype"]:
        base = SAME_ARCHETYPE
    elif same_pos:
        base = OTHER_ARCHETYPE
    elif any(mu["madden_position"] in g and donor["position"] in g for g in NEIGHBOURS):
        base = NEIGHBOUR
    else:
        base = ANYWHERE
    if target is None:
        return base
    use = arch if arch in doc["archetypes"] else donor["archetype"]
    try:
        grade = ow.predict_grade(doc, use, donor["ratings"])
    except KeyError:
        grade = donor["overall"]
    return base + abs(grade - target)


def match_team(mu_players, donors, doc):
    """[(mu, donor)] for one team by optimal assignment; Project Mu players beyond the donors stay unmatched."""
    if not mu_players or not donors:
        return []
    cost = np.array([[pair_cost(m, d, doc) for d in donors] for m in mu_players], float)
    rows, cols = linear_sum_assignment(cost)
    return [(mu_players[r], donors[c]) for r, c in zip(rows, cols)]


# ---------------------------------------------------------------- settling ----
def settle_archetype(doc, position, arch, ratings, target, max_rounds=200):
    """Ratings where `arch` is the player's top archetype at `target`. After adjust_ratings another archetype can end
    up higher (Madden would then show that archetype and a higher OVR), so each round lowers the rating that costs the
    highest rival more than it costs `arch`, then brings `arch` back to the target using only ratings that help `arch`
    more than that rival. A tie is fine: the player keeps their archetype. Returns (ratings, settled?)."""
    ratings = dict(ratings)
    own = doc["archetypes"][arch]
    for _ in range(max_rounds):
        grades = ow.predict_grades(doc, position, ratings)
        rivals = {a: g for a, g in grades.items() if a != arch and g > target}
        if not rivals:
            return ratings, True
        rival = doc["archetypes"][max(rivals, key=rivals.get)]
        options = []
        for k in set(rival.get("skills", rival["terms"])) | set(own.get("skills", own["terms"])):
            x = ratings.get(k, 0)
            if x <= 0:
                continue
            rival_drop = -ow._gain(rival["terms"], k, x, -1)
            own_drop = -ow._gain(own["terms"], k, x, -1)
            if rival_drop > own_drop:
                options.append((rival_drop - own_drop, k))
        if not options:
            return ratings, False
        ratings[max(options)[1]] -= 1
        # Back up to the target only on ratings that help `arch` more than the rival, or the step is undone.
        favour = [k for k in own.get("skills", own["terms"])
                  if ow._gain(own["terms"], k, ratings.get(k, 0), 1) > ow._gain(rival["terms"], k, ratings.get(k, 0), 1)]
        try:
            ratings, _ = ow.adjust_ratings(doc, arch, ratings, target, skills=favour)
        except ValueError:
            return ratings, False
    grades = ow.predict_grades(doc, position, ratings)
    return ratings, all(g <= target for a, g in grades.items() if a != arch)


# -------------------------------------------------------------------- edits ----
def player_edit(mu, donor, doc, age_add=0, write_ovr=False):
    """(save_writer edit, notes) for one pair."""
    first, last = split_name(mu["full_name"], mu["player_name"])
    position = mu["madden_position"]
    arch = mu["madden_archetype"] if mu["madden_archetype"] in doc["archetypes"] else donor["archetype"]
    if donor["position"] != position and arch not in (doc["slots"].get(position) or []):
        arch = next((a for a in doc["slots"].get(position, []) if a), arch)
    notes = []
    set_ = {"FirstName": first, "LastName": last}
    if mu["age"] is not None:
        set_["Age"] = mu["age"] + age_add
    if mu["years_pro"] is not None:
        set_["YearsPro"] = mu["years_pro"]
    if mu["jersey"] is not None:
        set_["JerseyNum"] = mu["jersey"]
    h = height_inches(mu["height"])
    if h:
        set_["Height"] = h
    if mu["weight"]:
        set_["Weight"] = mu["weight"] - WEIGHT_OFFSET
    dev = DEV_TO_MADDEN.get(mu["dev_trait"] or "")
    if dev:
        set_["TraitDevelopment"] = dev
    if donor["position"] != position:
        set_["Position"] = position
        notes.append(f"position {donor['position']} -> {position}")
    if arch != donor["archetype"]:
        set_["PlayerType"] = arch
        notes.append(f"archetype {donor['archetype']} -> {arch}")
    name = mu.get("full_name") or mu.get("player_name")
    cid = cn.commentary_id(name)
    set_["PLYR_COMMENT"] = cid if cid is not None else 0

    ratings = dict(donor["ratings"])
    if mu.get("scan"):
        ratings, target, scan_notes = scanned_ratings(mu, donor, doc, arch)
        notes += scan_notes
    elif mu["overall"] is not None and arch in doc["archetypes"]:
        target = mu["overall"]
        try:
            ratings, _ = ow.adjust_ratings(doc, arch, ratings, target)
        except ValueError as e:
            notes.append(f"unreachable: {e}")
        if position in doc["slots"] and arch in doc["slots"][position]:
            ratings, settled = settle_archetype(doc, position, arch, ratings, target)
            if not settled:
                notes.append("rival archetype: another archetype stays above the target")
    else:
        notes.append("no Project Mu OVR: ratings kept")
        return {"pgid": donor["pgid"], "set": set_}, notes
    for k, v in ratings.items():
        if v != donor["ratings"].get(k):
            set_[f"{k}Rating"] = v
    if write_ovr and position in doc["slots"]:
        grades = [ow.predict_grade(doc, a, ratings) if a in doc["archetypes"] else 0 for a in doc["slots"][position]]
        if mu.get("scan") and target is not None and arch in doc["slots"][position]:
            # Scanned ratings carry Madden's own OVR; the fit's error (K, LS, FB) mustn't change it or the archetype.
            grades = [target if a == arch else min(g, target) for a, g in zip(doc["slots"][position], grades)]
            top, top_arch = target, arch
        else:
            top, top_arch = ow.predict_ovr(doc, position, ratings, current=arch)
        grades += [0] * (5 - len(grades))
        for i, g in enumerate(grades[:5]):
            set_[f"OverallGrade{i}"] = max(0, min(g, 99))
        set_["OverallRating"] = max(0, min(top, 99))
        set_["PlayerType"] = top_arch
    return {"pgid": donor["pgid"], "set": set_}, notes


def scanned_ratings(mu, donor, doc, arch):
    """(ratings, target OVR, notes) for a player with a PS5 scan (§2.3): the scanned ratings over the donor's (a rating
    the list doesn't show keeps the donor's value). Madden computed the scan's OVR from those very ratings, so they are
    written as read and the scan's OVR is the target, even where Project Mu's OVR for the same season differs (an
    older roster read; noted as a scan gap). Only when Project Mu has moved a season past the scan (progression) are
    they moved by Project Mu's OVR change, measured on the predicted grade so the formula's own error cancels out."""
    scan = mu["scan"]
    ratings = dict(donor["ratings"])
    ratings.update({k: v for k, v in scan["ratings"].items() if k in ratings})
    target, shown, notes = mu["overall"], scan["overall"], []
    if shown is None or target is None or shown == target:
        return ratings, shown if shown is not None else target, notes
    if mu.get("season") is None or scan["season"] >= mu["season"]:
        notes.append(f"scan gap: scan OVR {shown}, Project Mu {target}")
        return ratings, shown, notes
    notes.append(f"scan moved: {shown} -> {target} since {scan['season']}")
    if arch in doc["archetypes"]:
        try:
            ratings, _ = ow.adjust_ratings(doc, arch, ratings, ow.predict_grade(doc, arch, ratings) + target - shown)
        except ValueError as e:
            notes.append(f"unreachable: {e}")
    return ratings, target, notes


# --------------------------------------------------------------------- load ----
def project_mu_roster(cur, season):
    cur.execute("""
        SELECT ps.player_id, ps.season, p.player_name, p.full_name, p.height, p.weight, p.player_number,
               t.abbreviation, ps.position, ps.archetype, ps.overall_rating, ps.age, ps.years_of_experience, ps.dev_trait
        FROM player_seasons ps JOIN players p ON p.id = ps.player_id JOIN teams t ON t.id = ps.team_id
        WHERE ps.season = %s ORDER BY t.abbreviation, ps.overall_rating DESC NULLS LAST""", (season,))
    cols = ("player_id", "season", "player_name", "full_name", "height", "weight", "jersey", "team", "position",
            "archetype", "overall", "age", "years_pro", "dev_trait")
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def attribute_scans(cur, season):
    """{player_id: {season, capture_folder, overall, ratings}}: each player's latest PS5 scan (player_attributes, §2.3)
    from `season` or before."""
    cur.execute("""
        SELECT DISTINCT ON (player_id) player_id, season, capture_folder, overall_rating, ratings
        FROM player_attributes WHERE season <= %s
        ORDER BY player_id, season DESC, created_at DESC, id DESC""", (season,))
    return {pid: {"season": s, "capture_folder": folder, "overall": ovr, "ratings": ratings}
            for pid, s, folder, ovr, ratings in cur.fetchall()}


def donors_by_team(snapshot):
    from_abbr = {v: k for k, v in TEAM_ABBR_ALIASES.items()}        # AZ -> ARI
    index = {t["short_name"]: t["team_index"] for t in snapshot["teams"] if t["team_index"] < 32}
    by_team = defaultdict(list)
    for p in snapshot["players"]:
        if p["status"] in DONOR_STATUSES and p["team_index"] < 32:
            by_team[p["team_index"]].append({
                "pgid": p["pgid"], "position": p["position"], "archetype": p["archetype"], "overall": p["overall"],
                "ratings": p["ratings"], "first_name": p.get("first_name"), "last_name": p["last_name"],
                "status": p["status"], "commentary_id": p["appearance"]["commentary_id"]})
    return by_team, lambda abbr: index.get(from_abbr.get(abbr, abbr))


def teleport(snapshot, roster, doc, age_add=0, write_ovr=False, release=False, seeds=None, scans=None):
    """(edits for save_writer, report). With `release` the edits also release the leftover donors; `seeds`
    (teleport_appearance.headshot_seeds) turns on appearance; `scans` (attribute_scans) gives scanned players their
    PS5 ratings instead of the donor's moved to their OVR."""
    by_team, team_index = donors_by_team(snapshot)
    roster = [m for m in roster if m["position"] not in SKIP_POSITIONS]
    for m in roster:
        m["madden_position"] = TO_MADDEN_POSITION.get(m["position"], m["position"])
        m["madden_archetype"] = madden_archetype(doc, m["madden_position"], m["archetype"])
        m["scan"] = (scans or {}).get(m["player_id"])
    teams = defaultdict(list)
    unknown_teams = Counter()
    for m in roster:
        ti = team_index(m["team"])
        if ti is None:
            unknown_teams[m["team"]] += 1
        else:
            teams[ti].append(m)

    edits, pairs, unmatched, leftover = [], [], [], []
    kinds, notes_count, ovr_moves, scan_gaps = Counter(), Counter(), [], Counter()
    pool = ta.head_pool(snapshot) if seeds is not None else None
    looks, seed_sources = [], Counter()
    short_name = {t["team_index"]: t["short_name"] for t in snapshot["teams"]}
    for ti in sorted(set(teams) | set(by_team)):        # a Madden team without Project Mu players loses every donor
        mu_players, donors = teams.get(ti, []), by_team.get(ti, [])
        matched = match_team(mu_players, donors, doc)
        used = {d["pgid"] for _, d in matched}
        got = {id(m) for m, _ in matched}
        unmatched += [f"{m['team']} {m['player_name']} {m['position']}" for m in mu_players if id(m) not in got]
        leftover += [{"pgid": d["pgid"], "team": short_name.get(ti), "position": d["position"],
                      "name": f"{d['first_name'] or ''} {d['last_name']}".strip(), "overall": d["overall"],
                      "status": d["status"]} for d in donors if d["pgid"] not in used]
        for m, d in matched:
            edit, notes = player_edit(m, d, doc, age_add, write_ovr)
            seed = seeds.get(m["player_id"]) if seeds is not None else None
            if seed:
                first, last = split_name(m["full_name"], m["player_name"])
                extra, look = ta.appearance_fields(pool, seed, first, last, d["pgid"], height_inches(m["height"]),
                                                   m["weight"])
                edit["set"].update(extra)
                if look.get("body_skin_tone") is not None:
                    edit["visuals"] = {"skinTone": look["body_skin_tone"]}
                looks.append(look)
                seed_sources[seed["source"]] += 1
            elif seeds is not None:
                seed_sources["no seed"] += 1
            edits.append(edit)
            cost = pair_cost(m, d, doc)
            kind = ("same archetype" if cost < OTHER_ARCHETYPE else "other archetype" if cost < NEIGHBOUR
                    else "neighbouring position" if cost < ANYWHERE else "other position")
            kinds[kind] += 1
            for n in notes:
                notes_count[n.split(":")[0] if ":" in n else " ".join(n.split(" ")[:1])] += 1
            rating_edits = {k: v for k, v in edit["set"].items() if k.endswith("Rating") and k != "OverallRating"}
            if m["scan"]:
                if m["overall"] is not None and m["scan"]["overall"] is not None:
                    scan_gaps[m["scan"]["overall"] - m["overall"]] += 1
            elif m["overall"] is not None:
                ovr_moves.append(sum(abs(v - d["ratings"][k[:-6]]) for k, v in rating_edits.items()))
            pairs.append({"player_id": m["player_id"], "name": m["player_name"], "team": m["team"],
                          "position": m["position"], "target_ovr": m["overall"], "donor_pgid": d["pgid"],
                          "donor_position": d["position"], "donor_archetype": d["archetype"],
                          "donor_ovr": d["overall"], "kind": kind, "ratings": "scan" if m["scan"] else "donor",
                          "notes": notes})
    moves = np.array(ovr_moves) if ovr_moves else np.zeros(1)
    report = {
        "season_players": len(roster), "matched": len(pairs), "unmatched": unmatched,
        "unknown_teams": dict(unknown_teams), "donors_left_over": len(leftover), "match_kinds": dict(kinds),
        "notes": dict(notes_count),
        "rating_points_moved": {"median": float(np.median(moves)), "p90": float(np.percentile(moves, 90)),
                                "max": float(moves.max())},
        "no_archetype_match": sum(1 for m in roster if m["madden_archetype"] is None),
        "scanned": sum(1 for p in pairs if p["ratings"] == "scan"),
        "scan_ovr_minus_project_mu": dict(sorted(scan_gaps.items())),
        "commentary_kept": sum(1 for e in edits if e["set"].get("PLYR_COMMENT")),
        "commentary_matched": sum(1 for e in edits if e["set"].get("PLYR_COMMENT", 0) > 0),
        "commentary_unmatched": sum(1 for e in edits if not e["set"].get("PLYR_COMMENT", 0)),
        "released": len(leftover) if release else 0,
        "pairs": pairs,
        "leftover_donors": leftover,
    }
    if seeds is not None:
        report["appearance"] = ta.summarize(looks, seed_sources)
    out = {"players": edits}
    if release:
        out["release"] = [d["pgid"] for d in leftover]
    return out, report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot")
    ap.add_argument("--season", type=int)
    ap.add_argument("--weights", default=ow.DEFAULT_PATH)
    ap.add_argument("--edits", help="write the save_writer edits file here")
    ap.add_argument("--report", help="write the full report (every pair) here")
    ap.add_argument("--age-add", type=int, default=0)
    ap.add_argument("--write-ovr", action="store_true")
    ap.add_argument("--contract-years-add", type=int, default=0)
    ap.add_argument("--draft", help="draft_tables.mjs's read of the save: also teleport the draft class and picks")
    ap.add_argument("--release", action="store_true", help="release the leftover donors to free agency")
    ap.add_argument("--appearance", action="store_true", help="also write generic head and body type")
    ap.add_argument("--no-scan", action="store_true", help="ignore player_attributes: every player gets donor ratings")
    a = ap.parse_args()
    with open(a.snapshot) as fh:
        snapshot = json.load(fh)
    if snapshot["meta"].get("reader_version", 1) < 2:
        sys.exit("re-read the save with pc_save_reader.mjs (version 2+)")
    doc = ow.load_weights(a.weights)
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        season = a.season
        if season is None:
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            season = cur.fetchone()[0]
        roster = project_mu_roster(cur, season)
        contracts = teleport_contracts.load_contracts(cur, season)
        seeds = ta.headshot_seeds(cur, season) if a.appearance else None
        scans = None if a.no_scan else attribute_scans(cur, season)
        edits, report = teleport(snapshot, roster, doc, a.age_add, a.write_ovr, a.release, seeds, scans)
        report["contracts"] = teleport_contracts.add_contracts(edits, report, contracts, snapshot, a.contract_years_add)
        if a.draft:
            from pipeline import teleport_draft
            with open(a.draft) as fh:
                draft_edits, report["draft"] = teleport_draft.run(cur, snapshot, json.load(fh), doc, season + 1,
                                                                  a.age_add, a.write_ovr)
            edits["players"] += draft_edits["players"]
            edits["records"] = draft_edits["records"]
    report["season"] = season
    if a.edits:
        with open(a.edits, "w") as fh:
            json.dump(edits, fh)
    if a.report:
        with open(a.report, "w") as fh:
            json.dump(report, fh, indent=1)
    summary = {k: v for k, v in report.items() if k not in ("pairs", "draft", "leftover_donors")}
    summary["unmatched"] = len(report["unmatched"])
    if "draft" in report:
        from pipeline import teleport_draft
        summary["draft"] = teleport_draft.summary(report["draft"])
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
