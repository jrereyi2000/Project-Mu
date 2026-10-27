"""
JR fires a coordinator on one of his own teams (PROJECT_SPEC §2.19). Mu never fires anyone on JR's teams
(gm_team_settings.user_controlled), so this is how he does it:

  python3 -m engines.fire_coordinator CAR OC                                   # the hiring cycle fills the job
  python3 -m engines.fire_coordinator IND DC --replacement "Name" [--scheme S] [--playbook P]
                                             [--promoted] [--background "linebackers coach"]
  python3 -m engines.fire_coordinator CAR OC --undo                            # before the hiring cycle only
  common: [--season S] [--dry-run]

The firing is recorded like one of Mu's own (coordinator_hot_seat, fired, inputs.fired_by = "JR"): his tenure closes
at the season just played, the Black Monday article reports it (phase BLACK_MONDAY when the team missed the playoffs,
PLAYOFF_EXIT once a playoff team is out), the league staff directory marks him fired, and the offseason Coaching
Carousel article lists it. Fire before the Week 18 run for the Week 19 articles to see it. Without --replacement the
hiring cycle fills the job after the Super Bowl like any other team's opening (an out-of-work coordinator, an
internal promotion or an outsider) and JR gets a "Mu: coordinator hired" notice. With --replacement his tenure starts
next season now (staff_moves HIRED, inputs.by = "JR"): an out-of-work coordinator by that name is rehired with his own
scheme; anyone else is a new coordinator who keeps the predecessor's scheme, playbook and tendencies unless --scheme
or --playbook says otherwise. Coordinators are Mu's own: nothing changes in Madden.
"""
import argparse
import json
import os

from engines import coach_hot_seat as chs
from engines import coaching_carousel as cc

SOURCES = {"promoted": "PROMOTED", "outside": "OUTSIDE"}


def _team(cur, abbr):
    cur.execute("SELECT id, city, name, abbreviation FROM teams WHERE upper(abbreviation) = upper(%s)", (abbr,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"unknown team {abbr}")
    return {"id": row[0], "city": row[1], "name": row[2], "abbr": row[3]}


def _cycle_ran(cur, season):
    cur.execute("SELECT count(*) FROM staff_moves WHERE season = %s AND COALESCE(inputs->>'by', '') <> 'JR'",
                (season,))
    return cur.fetchone()[0] > 0


def phase_for(cur, season, team_id):
    """BLACK_MONDAY for a team that missed the playoffs, PLAYOFF_EXIT for a playoff team that is out; raises while the
    regular season or the team's playoff run is still going."""
    cur.execute("""SELECT count(*), count(*) FILTER (WHERE status <> 'COMPLETED') FROM games
                   WHERE season = %s AND game_type = 'REGULAR_SEASON'
                     AND %s IN (home_team_id, away_team_id)""", (season, team_id))
    total, open_ = cur.fetchone()
    if not total or open_:
        raise ValueError(f"the {season} regular season isn't over for this team yet")
    if team_id not in chs.playoff_teams(cur, season):
        return "BLACK_MONDAY"
    if not chs.playoff_results(cur, season).get(team_id, {}).get("out"):
        raise ValueError("the team is still in the playoffs: fire him once it is out")
    return "PLAYOFF_EXIT"


def fire(cur, season, abbr, role, replacement=None, scheme=None, playbook=None, source="OUTSIDE",
         background=None, log=print):
    """Fire JR's `role` coordinator at `abbr` after `season`, inside the caller's transaction. Returns {team, role,
    fired, phase, replacement (None: the hiring cycle fills it)}."""
    role = role.upper()
    if role not in ("OC", "DC"):
        raise ValueError("role must be OC or DC")
    t = _team(cur, abbr)
    if t["id"] not in cc.jr_teams(cur):
        raise ValueError(f"{t['abbr']} isn't one of JR's teams: Mu runs its coordinators")
    nxt = season + 1
    cur.execute("""SELECT c.id, c.coordinator_name, c.scheme, c.playbook, c.pass_lean, c.blitz_rate, c.man_coverage,
                          ct.end_season
                   FROM coordinator_tenures ct JOIN coordinators c ON c.id = ct.coordinator_id
                   WHERE ct.team_id = %s AND ct.role = %s AND ct.start_season <= %s
                     AND (ct.end_season IS NULL OR ct.end_season > %s)
                   ORDER BY ct.start_season DESC LIMIT 1""", (t["id"], role, season, season))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"{t['abbr']} has no {role} under contract past {season} (already fired or gone)")
    keys = ("coordinator_id", "name", "scheme", "playbook", "pass_lean", "blitz_rate", "man_coverage", "end_season")
    cur_c = dict(zip(keys, row))
    if replacement is None and _cycle_ran(cur, season):
        raise ValueError(f"the {season} hiring cycle already ran: name a --replacement")
    phase = phase_for(cur, season, t["id"])

    # The hot-seat row: the review's (JR's teams are reviewed, never fired) or a new one.
    cur.execute("SELECT score, inputs, fired FROM coordinator_hot_seat WHERE season = %s AND team_id = %s AND role = %s",
                (season, t["id"], role))
    seat = cur.fetchone()
    if seat and seat[2]:
        raise ValueError(f"{t['abbr']}'s {role} was already fired after {season}")
    inputs = {"coordinator": cur_c["name"], "fired_by": "JR", "prior_end_season": cur_c["end_season"],
              "review": seat[1] if seat else None}
    cur.execute("""UPDATE coordinator_tenures SET end_season = %s
                   WHERE coordinator_id = %s AND team_id = %s AND role = %s
                     AND (end_season IS NULL OR end_season > %s)""",
                (season, cur_c["coordinator_id"], t["id"], role, season))

    hired = None
    if replacement:
        name = replacement.strip()
        cur.execute("""SELECT c.id, c.coordinator_name, c.side, c.scheme, c.playbook,
                              EXISTS (SELECT 1 FROM coordinator_tenures ct WHERE ct.coordinator_id = c.id
                                        AND (ct.end_season IS NULL OR ct.end_season >= %s))
                       FROM coordinators c WHERE lower(c.coordinator_name) = lower(%s)""", (nxt, name))
        known = cur.fetchone()
        if known and known[5]:
            raise ValueError(f"{known[1]} already has a job for {nxt}")
        if known and known[2] != cc.SIDE[role]:
            raise ValueError(f"{known[1]} is a {known[2].lower()} coordinator")
        if known:
            cid, name, src = known[0], known[1], "REHIRED"
            if scheme or playbook:
                cur.execute("UPDATE coordinators SET scheme = COALESCE(%s, scheme), playbook = COALESCE(%s, playbook) "
                            "WHERE id = %s", (scheme, playbook, cid))
            notes = f"rehired by JR; brings his {scheme or known[3] or 'system'}"
        else:
            src = SOURCES.get(source.lower(), source.upper())
            bg = background or ("an internal promotion" if src == "PROMOTED" else "an outside hire")
            keeps = not scheme and not playbook
            origin = (f"{t['name']} {role} from {nxt}; " + (f"promoted from {t['name']} {bg}" if src == "PROMOTED"
                                                              else f"previously {bg}") + "; hired by JR")
            cid = cc._insert_coordinator(cur, name, role, scheme or cur_c["scheme"], playbook or cur_c["playbook"],
                                         cur_c, origin)
            notes = (f"{'promoted from ' if src == 'PROMOTED' else 'previously '}{bg}; "
                     + (f"keeps {cur_c['name']}'s system" if keeps else f"runs {scheme or cur_c['scheme']}"))
            inputs["created_coordinator_id"] = cid
        cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) VALUES (%s, %s, %s, %s)",
                    (cid, t["id"], role, nxt))
        cc._move(cur, season, t["id"], role, "HIRED", name, coordinator_id=cid, source=src,
                 notes=f"replaces {cur_c['name']}; {notes}", inputs=json.dumps({"by": "JR"}))
        hired = {"coordinator": name, "source": src, "coordinator_id": cid}

    if seat:
        cur.execute("""UPDATE coordinator_hot_seat SET fired = true, coordinator_id = %s, phase = %s, inputs = %s
                       WHERE season = %s AND team_id = %s AND role = %s""",
                    (cur_c["coordinator_id"], phase, json.dumps(inputs), season, t["id"], role))
    else:
        cur.execute("""INSERT INTO coordinator_hot_seat (season, team_id, role, coordinator_id, score, fired, phase, inputs)
                       VALUES (%s, %s, %s, %s, 0, true, %s, %s)""",
                    (season, t["id"], role, cur_c["coordinator_id"], phase, json.dumps(inputs)))
    log(f"  {t['abbr']} fires {role} {cur_c['name']} after {season} ({phase})"
        + (f"; {hired['coordinator']} ({hired['source'].lower()}) takes over in {nxt}" if hired
           else "; the hiring cycle fills the job after the Super Bowl"))
    return {"team": t["abbr"], "role": role, "fired": cur_c["name"], "phase": phase, "replacement": hired}


def undo(cur, season, abbr, role, log=print):
    """Take back JR's firing of `role` at `abbr` after `season` (before the hiring cycle): his tenure reopens, a
    replacement named with the firing is removed, and the hot-seat row goes back to the review's."""
    role = role.upper()
    t = _team(cur, abbr)
    if _cycle_ran(cur, season):
        raise ValueError(f"the {season} hiring cycle already ran: change the staff by migration")
    cur.execute("""SELECT coordinator_id, inputs FROM coordinator_hot_seat
                   WHERE season = %s AND team_id = %s AND role = %s AND fired AND inputs->>'fired_by' = 'JR'""",
                (season, t["id"], role))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"JR didn't fire {t['abbr']}'s {role} after {season}")
    coord_id, inputs = row
    cur.execute("SELECT coordinator_id FROM staff_moves WHERE season = %s AND team_id = %s AND role = %s "
                "AND inputs->>'by' = 'JR'", (season, t["id"], role))
    for (hid,) in cur.fetchall():
        cur.execute("DELETE FROM coordinator_tenures WHERE coordinator_id = %s AND team_id = %s AND start_season = %s",
                    (hid, t["id"], season + 1))
        if hid == inputs.get("created_coordinator_id"):
            cur.execute("DELETE FROM staff_moves WHERE coordinator_id = %s", (hid,))
            cur.execute("DELETE FROM coordinators WHERE id = %s", (hid,))
    cur.execute("DELETE FROM staff_moves WHERE season = %s AND team_id = %s AND role = %s AND inputs->>'by' = 'JR'",
                (season, t["id"], role))
    cur.execute("UPDATE coordinator_tenures SET end_season = %s WHERE coordinator_id = %s AND team_id = %s AND role = %s "
                "AND end_season = %s", (inputs.get("prior_end_season"), coord_id, t["id"], role, season))
    if inputs.get("review") is not None:
        cur.execute("UPDATE coordinator_hot_seat SET fired = false, inputs = %s WHERE season = %s AND team_id = %s "
                    "AND role = %s", (json.dumps(inputs["review"]), season, t["id"], role))
    else:
        cur.execute("DELETE FROM coordinator_hot_seat WHERE season = %s AND team_id = %s AND role = %s",
                    (season, t["id"], role))
    log(f"  {t['abbr']} keeps {role} {inputs.get('coordinator')} (firing undone)")
    return {"team": t["abbr"], "role": role, "kept": inputs.get("coordinator")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("team", help="CAR or IND")
    ap.add_argument("role", choices=["OC", "DC", "oc", "dc"])
    ap.add_argument("--replacement", help="name the new coordinator now (else the hiring cycle fills the job)")
    ap.add_argument("--scheme", help="the replacement's scheme (default: the predecessor's, or a rehire's own)")
    ap.add_argument("--playbook", help="the replacement's playbook (default: the predecessor's)")
    ap.add_argument("--promoted", action="store_true", help="the replacement is promoted from within (else outside)")
    ap.add_argument("--background", help='where he comes from, e.g. "quarterbacks coach" or "a college OC"')
    ap.add_argument("--undo", action="store_true", help="take back the firing (before the hiring cycle)")
    ap.add_argument("--season", type=int, help="the season just played (default league_state.current_season)")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen and roll back")
    a = ap.parse_args(argv)
    import psycopg
    dsn = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season FROM league_state WHERE id = 1")
        season = a.season or cur.fetchone()[0]
        try:
            if a.undo:
                rep = undo(cur, season, a.team, a.role)
            else:
                rep = fire(cur, season, a.team, a.role, replacement=a.replacement, scheme=a.scheme,
                           playbook=a.playbook, source="PROMOTED" if a.promoted else "OUTSIDE",
                           background=a.background)
        except ValueError as e:
            conn.rollback()
            raise SystemExit(f"error: {e}")
        print(json.dumps(rep, indent=1, default=str))
        if a.dry_run:
            conn.rollback()
            print("dry run: rolled back")
        else:
            conn.commit()


if __name__ == "__main__":
    main()
