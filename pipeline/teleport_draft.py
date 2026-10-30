"""
The teleporter's draft step (docs/in_progress/PC_MIGRATION_PLAN.md §4.3 Step 5): turns a Madden 27 PC save's draft class into
Project Mu's and gives its future picks Project Mu's owners. Writes the edits file
pipeline/save_reader/save_writer.mjs applies; reads the database, never writes it; never touches a save itself.

  python3 -m pipeline.teleport_draft <snapshot.json> <draft.json> [--draft-season 2037] [--edits edits.json]
                                     [--report report.json] [--age-add N] [--write-ovr]

<snapshot.json> is pc_save_reader.mjs's read of the save (version 2+: the prospects' ratings), <draft.json> is
pipeline/save_reader/draft_tables.mjs's read of the same save (picks, DraftPlayer rows, Team rows). --draft-season is
the class on the save's coming draft (YearOffset 0; default league_state.current_season + 1); picks for it and the next
two drafts are mapped. pipeline/teleport.py --draft <draft.json> runs this too and writes one edits file.

Draft class. The save's prospects are Player rows with ContractStatus Draft, each linked from a DraftPlayer row. Each
Project Mu prospect (draft_prospects, the class) takes over one Madden prospect: an optimal assignment over the whole
class (scipy linear_sum_assignment) on a cost of
  same Madden position                  |donor's best archetype grade there - target OVR|
  a neighbouring position (LT/RT, ...)  + 40  (Position rewritten)
  anything else                         + 120
Project Mu prospects carry only a position and a scouted OVR (no archetype, ratings or dev trait), so the archetype is
the donor's when the position stays, else the archetype the donor's ratings grade highest at the new position; the dev
trait stays the donor's. The Player edits are pipeline/teleport.player_edit's (name, age, years pro 0, height, weight,
position / archetype, ratings to the OVR via engines/ovr_weights, PLYR_COMMENT). The DraftPlayer row gets DraftPosition
(Madden's prospect groups: OT, IOL, DE, OLB, S, ...) when the position moves, InitialDraftRank (the board: Project Mu's
projected_pick order) and TrueOverallRanking (the hidden truth: scout_prospects.true_rank, else OVR). Madden prospects
left over keep their player and are ranked after every Project Mu prospect, in their old order. Not written: college
(Player.College points into the game's static college list, table 16384, not the save), combine and pro day results.

Picks. A future pick is a DraftPick row keyed by (YearOffset, Round, OriginalTeam); its owner is CurrentTeam, a
reference to a Team row. Each is matched to Project Mu's draft_picks row (draft season, round, original team) and gets
CurrentTeam = the owner's Team row when they differ, as a {"records": [...]} edit (save_reader/record_edits.mjs
validates the target is a Team row in the table the field already points into). When every pick of the coming draft has
a Project Mu overall_pick (the Draft Room locks it at offseason stage 4), PickNumber = overall_pick - 1 too.
"""
import argparse
import json
import os
import sys
from collections import Counter

import numpy as np
import psycopg
from scipy.optimize import linear_sum_assignment

from engines import ovr_weights as ow
from pipeline.capture_matching import name_case
from pipeline.load_save import TEAM_ABBR_ALIASES
from pipeline.madden_player_edit import (
    ANYWHERE,
    NEIGHBOUR,
    NEIGHBOURS,
    TO_MADDEN_POSITION,
    player_edit,
)

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
SAME = 0
# Madden position -> DraftPlayer.DraftPosition (ProspectPosition enum).
PROSPECT_POSITION = {"QB": "QB", "HB": "RB", "FB": "RB", "WR": "WR", "TE": "TE", "LT": "OT", "RT": "OT", "LG": "IOL",
                     "RG": "IOL", "C": "IOL", "LE": "DE", "RE": "DE", "DT": "DT", "LOLB": "OLB", "ROLB": "OLB",
                     "MLB": "MLB", "CB": "CB", "FS": "S", "SS": "S", "K": "K", "P": "P", "LS": "LS"}
ROUNDS = 7


# ----------------------------------------------------------------- matching ----
def best_archetype(doc, position, donor):
    """(archetype, grade) the prospect plays at `position`: the donor's own when it belongs to the position, else the
    position's archetype the donor's ratings grade highest."""
    archs = [a for a in doc["slots"].get(position, []) if a and a in doc["archetypes"]]
    if donor["archetype"] in archs:
        return donor["archetype"], ow.predict_grade(doc, donor["archetype"], donor["ratings"])
    if not archs:
        return donor["archetype"], donor["overall"]
    grades = {a: ow.predict_grade(doc, a, donor["ratings"]) for a in archs}
    top = max(grades, key=grades.get)
    return top, grades[top]


def draft_cost(mu, donor, doc, cache=None):
    """Cost of turning Madden prospect `donor` into Project Mu prospect `mu` (see the module docstring)."""
    pos = mu["madden_position"]
    if donor["position"] == pos:
        base = SAME
    elif any(pos in g and donor["position"] in g for g in NEIGHBOURS):
        base = NEIGHBOUR
    else:
        base = ANYWHERE
    if mu["overall"] is None:
        return base
    key = (donor["pgid"], pos)
    if cache is None or key not in cache:
        grade = best_archetype(doc, pos, donor)[1]
        if cache is None:
            return base + abs(grade - mu["overall"])
        cache[key] = grade
    return base + abs(cache[key] - mu["overall"])


def match_class(prospects, donors, doc):
    """[(mu, donor)] over the whole class by optimal assignment; the side with more rows keeps some unmatched."""
    if not prospects or not donors:
        return []
    cache = {}
    cost = np.array([[draft_cost(m, d, doc, cache) for d in donors] for m in prospects], float)
    rows, cols = linear_sum_assignment(cost)
    return [(prospects[r], donors[c]) for r, c in zip(rows, cols)]


def cost_kind(cost):
    return "same position" if cost < NEIGHBOUR else "neighbouring position" if cost < ANYWHERE else "other position"


# -------------------------------------------------------------------- class ----
def prospect_name(full_name):
    """'TREY BETHEL' -> 'Trey Bethel', 'JOHN MCCAIN III' -> 'John McCain III' (the board is captured in capitals)."""
    return name_case((full_name or "").strip().title())


def as_teleport_player(p):
    """A draft_prospects row in pipeline/teleport.player_edit's shape."""
    h = p["height_in"]
    return {"player_id": None, "prospect_id": p["id"], "player_name": None, "full_name": prospect_name(p["full_name"]),
            "height": f"{h // 12}'{h % 12}\"" if h else None, "weight": p["weight"], "jersey": None, "team": None,
            "position": p["position"], "archetype": p["archetype"], "overall": p["ovr"], "age": p["age"],
            "years_pro": 0, "dev_trait": None, "projected_pick": p["projected_pick"], "true_rank": p.get("true_rank"),
            "madden_position": TO_MADDEN_POSITION.get(p["position"], p["position"])}


def class_donors(snapshot, draft):
    """The save's prospects: snapshot Player rows with ContractStatus Draft that a DraftPlayer row links, with that
    row's ranks."""
    rows = {d["pgid"]: d for d in draft["prospects"] if d["contract_status"] == "Draft"}
    out = []
    for p in snapshot["players"]:
        if p["status"] == "Draft" and p["pgid"] in rows:
            r = rows[p["pgid"]]
            out.append({"pgid": p["pgid"], "position": p["position"], "archetype": p["archetype"],
                        "overall": p["overall"], "ratings": p["ratings"], "last_name": p["last_name"],
                        "commentary_id": p["appearance"]["commentary_id"], "draft_row": r["row"],
                        "initial_draft_rank": r["initial_draft_rank"], "true_overall_ranking": r["true_overall_ranking"]})
    return out


def rank_order(pairs, leftover, key_mu, key_donor):
    """{pgid: 0-based rank}: Project Mu prospects first by key_mu, then the leftover Madden prospects by key_donor."""
    order = [d["pgid"] for _, d in sorted(pairs, key=lambda md: key_mu(md[0]))]
    order += [d["pgid"] for d in sorted(leftover, key=key_donor)]
    return {pgid: i for i, pgid in enumerate(order)}


def draft_class(snapshot, draft, prospects, doc, age_add=0, write_ovr=False):
    """(player edits, DraftPlayer record edits, report)."""
    donors = class_donors(snapshot, draft)
    mus = [as_teleport_player(p) for p in prospects]
    pairs = match_class(mus, donors, doc)
    used = {d["pgid"] for _, d in pairs}
    got = {id(m) for m, _ in pairs}
    leftover = [d for d in donors if d["pgid"] not in used]
    board = rank_order(pairs, leftover, lambda m: (m["projected_pick"] is None, m["projected_pick"] or 0, -(m["overall"] or 0)),
                       lambda d: d["initial_draft_rank"])
    truth = rank_order(pairs, leftover, lambda m: (m["true_rank"] is None, m["true_rank"] or 0, -(m["overall"] or 0)),
                       lambda d: d["true_overall_ranking"])

    players, records, report_pairs = [], [], []
    kinds, notes_count = Counter(), Counter()
    for m, d in pairs:
        m["madden_archetype"] = best_archetype(doc, m["madden_position"], d)[0]
        edit, notes = player_edit(m, d, doc, age_add, write_ovr)
        players.append(edit)
        kind = cost_kind(draft_cost(m, d, doc))
        kinds[kind] += 1
        for n in notes:
            notes_count[n.split(":")[0] if ":" in n else n.split(" ")[0]] += 1
        report_pairs.append({"prospect_id": m["prospect_id"], "name": m["full_name"], "position": m["position"],
                             "target_ovr": m["overall"], "donor_pgid": d["pgid"], "donor_position": d["position"],
                             "donor_archetype": d["archetype"], "donor_ovr": d["overall"], "kind": kind, "notes": notes})
    by_pgid = {d["pgid"]: d for d in donors}
    position_of = {d["pgid"]: m["madden_position"] for m, d in pairs}
    for pgid, d in by_pgid.items():
        set_ = {}
        if pgid in position_of and position_of[pgid] != d["position"]:
            set_["DraftPosition"] = PROSPECT_POSITION.get(position_of[pgid], "INVALID")
        if board[pgid] != d["initial_draft_rank"]:
            set_["InitialDraftRank"] = board[pgid]
        if truth[pgid] != d["true_overall_ranking"]:
            set_["TrueOverallRanking"] = truth[pgid]
        if set_:
            records.append({"table": "DraftPlayer", "tableId": draft["draft_player_table_id"], "row": d["draft_row"],
                            "set": set_})
    report = {
        "project_mu_prospects": len(mus), "madden_prospects": len(donors), "matched": len(pairs),
        "unmatched_project_mu": [f"{m['full_name']} {m['position']}" for m in mus if id(m) not in got],
        "madden_left_over": len(leftover),
        "positions": {"project_mu": dict(Counter(m["madden_position"] for m in mus)),
                      "madden": dict(Counter(d["position"] for d in donors))},
        "match_kinds": dict(kinds), "notes": dict(notes_count), "pairs": report_pairs,
        "commentary_matched": sum(1 for p in players if p["set"].get("PLYR_COMMENT", 0) > 0),
        "commentary_unmatched": sum(1 for p in players if not p["set"].get("PLYR_COMMENT", 0)),
    }
    return players, records, report


# -------------------------------------------------------------------- picks ----
def team_rows(draft):
    """{Project Mu abbreviation: Team row} for the 32 clubs."""
    return {TEAM_ABBR_ALIASES.get(t["short_name"], t["short_name"]): t["row"]
            for t in draft["teams"] if t["team_index"] < 32}


def pick_edits(draft, mu_picks, draft_season):
    """(DraftPick record edits, report). mu_picks: draft_picks rows as dicts with draft_season, round, original
    (abbreviation), owner (abbreviation), overall_pick."""
    rows = team_rows(draft)
    abbr_of = {row: abbr for abbr, row in rows.items()}
    mu = {(p["draft_season"], p["round"], p["original"]): p for p in mu_picks}
    table_id, team_table = draft["draft_pick_table_id"], draft["team_table_id"]
    coming = [p for p in mu_picks if p["draft_season"] == draft_season]
    numbers = sorted(p["overall_pick"] for p in coming if p["overall_pick"] is not None)
    write_numbers = numbers == list(range(1, ROUNDS * 32 + 1))

    records, missing, moved, renumbered, unknown = [], [], [], 0, Counter()
    seen = set()
    for pk in draft["picks"]:
        if pk["selected"]:
            continue
        season = draft_season + pk["year_offset"]
        original = abbr_of.get(pk["original_team_row"])
        if original is None:
            unknown[pk["original_team_row"]] += 1
            continue
        m = mu.get((season, pk["round"], original))
        if m is None:
            missing.append(f"{season} R{pk['round']} {original}")
            continue
        seen.add((season, pk["round"], original))
        set_ = {}
        owner_row = rows.get(m["owner"])
        if owner_row is None:
            unknown[m["owner"]] += 1
        elif owner_row != pk["current_team_row"]:
            set_["CurrentTeam"] = {"tableId": team_table, "row": owner_row}
            moved.append(f"{season} R{pk['round']} {original}: {abbr_of.get(pk['current_team_row'])} -> {m['owner']}")
        if write_numbers and season == draft_season and m["overall_pick"] - 1 != pk["pick_number"]:
            set_["PickNumber"] = m["overall_pick"] - 1
            renumbered += 1
        if set_:
            records.append({"table": "DraftPick", "tableId": table_id, "row": pk["row"], "set": set_})
    seasons = {draft_season + pk["year_offset"] for pk in draft["picks"] if not pk["selected"]}
    report = {
        "save_picks": sum(1 for pk in draft["picks"] if not pk["selected"]), "matched": len(seen),
        "owner_changes": len(moved), "owners_moved": moved,
        "traded_in_project_mu": sum(1 for p in mu_picks if p["owner"] != p["original"] and p["draft_season"] in seasons),
        "pick_numbers": renumbered if write_numbers else "not written: the coming draft has no full Project Mu order",
        "save_picks_without_project_mu_row": missing,
        "project_mu_picks_not_in_save": sorted(f"{k[0]} R{k[1]} {k[2]}" for k in mu
                                               if k not in seen and k[0] in seasons),
        "unknown_teams": {str(k): v for k, v in unknown.items()},
    }
    return records, report


# --------------------------------------------------------------------- load ----
def project_mu_class(cur, draft_season):
    cur.execute("""
        SELECT dp.id, dp.full_name, dp.position, dp.college, dp.height_in, dp.weight, dp.age, dp.ovr, dp.archetype,
               dp.projected_pick, sp.true_rank
        FROM draft_prospects dp
        LEFT JOIN scout_prospects sp ON sp.prospect_id = dp.id AND sp.status = 'ACTIVE'
        WHERE dp.draft_season = %s ORDER BY dp.projected_pick NULLS LAST, dp.id""", (draft_season,))
    cols = ("id", "full_name", "position", "college", "height_in", "weight", "age", "ovr", "archetype",
            "projected_pick", "true_rank")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def project_mu_picks(cur, first_season, years=3):
    cur.execute("""
        SELECT p.draft_season, p.round, o.abbreviation, w.abbreviation, p.overall_pick
        FROM draft_picks p JOIN teams o ON o.id = p.original_team_id JOIN teams w ON w.id = p.owner_team_id
        WHERE p.draft_season BETWEEN %s AND %s""", (first_season, first_season + years - 1))
    cols = ("draft_season", "round", "original", "owner", "overall_pick")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def teleport_draft(snapshot, draft, prospects, mu_picks, doc, draft_season, age_add=0, write_ovr=False):
    """(edits {"players", "records"}, report)."""
    players, class_records, class_report = draft_class(snapshot, draft, prospects, doc, age_add, write_ovr)
    pick_records, picks_report = pick_edits(draft, mu_picks, draft_season)
    return ({"players": players, "records": class_records + pick_records},
            {"draft_season": draft_season, "draft_class": class_report, "picks": picks_report})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot")
    ap.add_argument("draft", help="draft_tables.mjs's read of the same save")
    ap.add_argument("--draft-season", type=int)
    ap.add_argument("--weights", default=ow.DEFAULT_PATH)
    ap.add_argument("--edits", help="write the save_writer edits file here")
    ap.add_argument("--report", help="write the full report (every pair and moved pick) here")
    ap.add_argument("--age-add", type=int, default=0)
    ap.add_argument("--write-ovr", action="store_true", help="as pipeline/teleport.py --write-ovr")
    a = ap.parse_args()
    with open(a.snapshot) as fh:
        snapshot = json.load(fh)
    with open(a.draft) as fh:
        draft = json.load(fh)
    if snapshot["meta"].get("reader_version", 1) < 2:
        sys.exit("re-read the save with pc_save_reader.mjs (version 2+)")
    with psycopg.connect(DSN) as conn:
        edits, report = run(conn.cursor(), snapshot, draft, ow.load_weights(a.weights), a.draft_season, a.age_add,
                            a.write_ovr)
    if a.edits:
        with open(a.edits, "w") as fh:
            json.dump(edits, fh)
    if a.report:
        with open(a.report, "w") as fh:
            json.dump(report, fh, indent=1)
    print(json.dumps(summary(report), indent=1))


def run(cur, snapshot, draft, doc, draft_season=None, age_add=0, write_ovr=False):
    """Loads Project Mu's class and picks and builds the edits: (edits, report)."""
    if draft_season is None:
        cur.execute("SELECT current_season FROM league_state WHERE id = 1")
        draft_season = cur.fetchone()[0] + 1
    prospects = project_mu_class(cur, draft_season)
    mu_picks = project_mu_picks(cur, draft_season)
    return teleport_draft(snapshot, draft, prospects, mu_picks, doc, draft_season, age_add, write_ovr)


def summary(report):
    c, p = dict(report["draft_class"]), dict(report["picks"])
    c.pop("pairs")
    c["unmatched_project_mu"] = len(c["unmatched_project_mu"])
    p["owners_moved"] = len(p["owners_moved"])
    return {"draft_season": report["draft_season"], "draft_class": c, "picks": p}


if __name__ == "__main__":
    main()
