"""Synthetic staff-contract timelines, run through the real producer, hot seats and carousel (ported from the
auditor's verify_82c6125/invariant.py, verify_2fc9d9e/r4.py and pg_repro.py).

Each scenario builds a tiny league inside one transaction on an empty scratch database (db/init_db.sql only), plays
league years forward and, for each year Y, applies that offseason's events and then FREE_AGENCY's
`staff_contracts_run.annual(Y)`, snapshotting the ledger after each (league_office.annual_offseason_steps' order).
Events use the real write paths: the coach hot seat's fire (`coach_hot_seat._store`), the GM hot seat
(`gm_rollover._fire_hot_seat`, vacate-only), `gm_rollover.fill_vacant_gms`, and in `carousel-A` the whole
`coaching_carousel.run` + `gm_rollover.hot_seat_only`. Then the year-by-year invariant (invariant.py) is applied,
and the money each scenario's key deals cost is compared with `EXPECTED`. Every transaction is rolled back.

`EXPECTED` is the reviewed totals table: each entry is (kind, who, deal) -> total paid over the timeline, plus the
buyout and its first season where one is owed. A `who` of None matches the one deal of that kind and span (a GM
named by the persona generator).
"""
import traceback

from engines import coach_hot_seat as chs, gm_rollover
from engines import staff_contracts_run as scr

from tools.offseason_check import invariant as inv, safety

QUIET = lambda *a, **k: None  # noqa: E731
M = 1_000_000


# ----------------------------------------------------------------------------------------------- tiny-league kit
def team(cur, abbr):
    """A team row with a real abbreviation: _gm_budget prices the owner's pool from the baseline by abbreviation."""
    cur.execute("INSERT INTO teams (name, city, abbreviation, conference, division) VALUES (%s,%s,%s,'AFC','AFC East') "
                "RETURNING id", (abbr, abbr, abbr))
    return cur.fetchone()[0]


def coach(cur, name, tid, start):
    cur.execute("INSERT INTO coaches (coach_name, slant, offensive_scheme, defensive_scheme) "
                "VALUES (%s,'OFFENSIVE','West Coast','4-3') RETURNING id", (name,))
    cid = cur.fetchone()[0]
    if tid is not None:
        cur.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season) VALUES (%s,%s,%s)", (cid, tid, start))
    return cid


def coord(cur, name, tid, role, start):
    cur.execute("INSERT INTO coordinators (coordinator_name, side, scheme) VALUES (%s,%s,%s) RETURNING id",
                (name, "OFFENSIVE" if role == "OC" else "DEFENSIVE", "West Coast" if role == "OC" else "4-3"))
    co = cur.fetchone()[0]
    if tid is not None:
        cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) "
                    "VALUES (%s,%s,%s,%s)", (co, tid, role, start))
    return co


def gm(cur, name, tid, start, coach_id=None):
    cur.execute("""INSERT INTO gm_profiles (team_id, gm_name, start_season, coach_id, risk_appetite, aggressiveness,
                   pick_affinity, youth_bias, seed) VALUES (%s,%s,%s,%s,0.5,0.5,1.0,0,%s) RETURNING id""",
                (tid, name, start, coach_id, f"seed:{name}"))
    return cur.fetchone()[0]


def full_control_deal(cur, tid, hc, signed, salary=20 * M, years=5):
    """The Godfather row coaching_carousel writes: an HC deal with full_control, signed in `signed`."""
    cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years,
                   remaining_years, signed_season, full_control) VALUES (%s,%s,'HC',%s,%s,%s,%s,true)""",
                (tid, hc, salary, years, years, signed))


def fire_coach(cur, tid, cid, abbr, season):
    """The coach hot seat's real fire write: ends the tenure at `season`, vacates a coach-GM seat and settles a
    full-control deal."""
    rows = [{"team_id": tid, "abbr": abbr, "coach_name": "x"}]
    chs._store(cur, season, "BLACK_MONDAY", rows, {tid: {"coach_id": cid, "score": 3.0, "fire": True, "inputs": {}}},
               {"fired": [], "decided": []}, QUIET)


def fire_gm(cur, tid, season):
    """The GM hot seat for `season` (run at season+1, as weekly_capture does), vacate-only, as P1-27 runs it."""
    gm_rollover._fire_hot_seat(cur, season + 1, {tid: {"fire": True, "score": 2.0, "inputs": {}}}, {"fired": []},
                               None, QUIET, vacate_only=True)


def end_coord(cur, season, *coord_ids):
    cur.execute("UPDATE coordinator_tenures SET end_season=%s WHERE coordinator_id = ANY(%s) AND end_season IS NULL",
                (season, list(coord_ids)))


def timeline(cur, first, last, events, names, label):
    """Plays league years first..last: each year's events, then annual(Y), then a snapshot. Returns the report."""
    t = inv.Tracker(cur)
    for y in range(first, last + 1):
        for ev in events.get(y, []):
            ev()
        scr.annual(cur, y)
        t.snap(y)
    return t.report(label, names)


# ------------------------------------------------------------------------------------- scenarios (invariant.py)
def sc_hc(cur, fire_after, replaced):
    """Plain HC (4 yrs from 2026) and OC (3 yrs from 2026) filed by annual(2026). The coach hot seat fires the HC
    after `fire_after`, the OC goes with him, and the carousel replaces both from fire_after+1 when `replaced`."""
    tid = team(cur, "BUF")
    hc = coach(cur, "Old HC", tid, 2026)
    oc = coord(cur, "Old OC", tid, "OC", 2026)
    gm(cur, "Steady GM", tid, 2026)

    def fire():
        fire_coach(cur, tid, hc, "BUF", fire_after)
        end_coord(cur, fire_after, oc)
        if replaced:
            coach(cur, "New HC", tid, fire_after + 1)
            coord(cur, "New OC", tid, "OC", fire_after + 1)
    return {fire_after: [fire]}, (2026, 2032), {tid: "BUF"}


def sc_gm(cur, fire_after, replaced):
    """GM on a 4-yr deal from 2033 (annual(2033)); the GM hot seat fires him after `fire_after`;
    fill_vacant_gms(fire_after+1) when `replaced`."""
    tid = team(cur, "NYJ")
    coach(cur, "Some HC", tid, 2033)
    gm(cur, "Old GM", tid, 2033)

    def fire():
        fire_gm(cur, tid, fire_after)
        if replaced:
            gm_rollover.fill_vacant_gms(cur, fire_after + 1, log=QUIET)
    return {fire_after: [fire]}, (2033, 2039), {tid: "NYJ"}


def sc_fc(cur, fire_after, replaced):
    """Full-control coach-GM, 5 x $20M from 2031. The coach hot seat fires him after `fire_after`; a successor and
    fill_vacant_gms from fire_after+1 when `replaced`."""
    tid = team(cur, "MIA")
    hc = coach(cur, "Godfather", tid, 2031)
    gm(cur, "Godfather", tid, 2031, coach_id=hc)
    full_control_deal(cur, tid, hc, 2031)

    def fire():
        fire_coach(cur, tid, hc, "MIA", fire_after)
        if replaced:
            coach(cur, "Successor", tid, fire_after + 1)
            gm_rollover.fill_vacant_gms(cur, fire_after + 1, log=QUIET)
    return {fire_after: [fire]}, (2031, 2038), {tid: "MIA"}


def sc_movers(cur):
    """Coordinators on 3-yr deals from 2026. After 2027 team A's OC is poached to be B's head coach and A's DC moves
    laterally to B's DC; A refills both seats; B's old HC is fired and B's DC let go."""
    a, b = team(cur, "PHI"), team(cur, "DAL")
    coach(cur, "A HC", a, 2026)
    bhc = coach(cur, "B HC", b, 2026)
    aoc = coord(cur, "A OC", a, "OC", 2026)
    adc = coord(cur, "A DC", a, "DC", 2026)
    coord(cur, "B OC", b, "OC", 2026)
    bdc = coord(cur, "B DC", b, "DC", 2026)

    def move():
        fire_coach(cur, b, bhc, "DAL", 2027)
        end_coord(cur, 2027, aoc, adc, bdc)
        coach(cur, "A OC", b, 2028)                                   # poached: a new coaches.id, same name
        cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) "
                    "VALUES (%s,%s,'DC',2028)", (adc, b))
        coord(cur, "A new OC", a, "OC", 2028)
        coord(cur, "A new DC", a, "DC", 2028)
    return {2027: [move]}, (2026, 2031), {a: "PHI", b: "DAL"}


def sc_next_season_hire(cur):
    """A Godfather HC accepted at the carousel after 2027 (full control, signed 2028, coach-GM seat from 2028)
    replacing a fired plain HC; plus a GM hired by fill_vacant_gms for 2028 on another team."""
    a, b = team(cur, "KC"), team(cur, "DEN")
    old = coach(cur, "Old KC HC", a, 2026)
    coach(cur, "DEN HC", b, 2026)
    gm(cur, "KC GM", a, 2026)
    gm(cur, "DEN GM", b, 2026)

    def carousel():
        fire_coach(cur, a, old, "KC", 2027)
        cur.execute("UPDATE gm_profiles SET end_season=2027 WHERE team_id=%s AND end_season IS NULL", (a,))
        gf = coach(cur, "Godfather", a, 2028)
        gm(cur, "Godfather", a, 2028, coach_id=gf)
        full_control_deal(cur, a, gf, 2028)
        fire_gm(cur, b, 2027)
        gm_rollover.fill_vacant_gms(cur, 2028, log=QUIET)
    return {2027: [carousel]}, (2026, 2033), {a: "KC", b: "DEN"}


def sc_rehire(cur):
    """A full-control HC, 5 x $20M from 2031, fired after 2033; re-hired into the same seat for 2036."""
    tid = team(cur, "SEA")
    hc = coach(cur, "Boomerang", tid, 2031)
    full_control_deal(cur, tid, hc, 2031)
    coach(cur, "Interim", tid, 2034)

    def back():
        cur.execute("UPDATE coach_tenures SET end_season=2035 WHERE team_id=%s AND end_season IS NULL", (tid,))
        cur.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season) VALUES (%s,%s,2036)", (hc, tid))
    return {2033: [lambda: fire_coach(cur, tid, hc, "SEA", 2033)], 2035: [back]}, (2031, 2040), {tid: "SEA"}


def sc_expiry(cur):
    """A plain HC on a 4-yr deal (2026-2029) who keeps the job through 2034: kept past the deal and re-signed."""
    tid = team(cur, "GB")
    coach(cur, "Lifer", tid, 2026)
    return {}, (2026, 2034), {tid: "GB"}


def sc_gm_hired_y1(cur):
    """A GM hired by fill_vacant_gms for 2034 (row written at hire), fired by the GM hot seat after 2034."""
    tid = team(cur, "NYJ")
    coach(cur, "Some HC", tid, 2033)
    return ({2033: [lambda: gm_rollover.fill_vacant_gms(cur, 2034, log=QUIET)],
             2034: [lambda: fire_gm(cur, tid, 2034)]}, (2033, 2039), {tid: "NYJ"})


# pg_repro's four-team league (S = 2030): MIA a coach-GM on 5 x $20M full control signed 2028, the other HCs 5 x $8M
# and GMs 4 x $3M signed 2028, a completed regular season and a Wild Card.
_REPRO_S = 2030
_REPRO_TEAMS = [("BUF", "Buffalo", "AFC"), ("NYJ", "New York", "AFC"), ("PHI", "Philadelphia", "NFC"),
                ("MIA", "Miami", "NFC")]
_REPRO_REC = {"BUF": (13, 4), "NYJ": (11, 6), "PHI": (14, 3), "MIA": (2, 15)}


def _repro_seed(cur, coach_gm_abbr):
    S = _REPRO_S
    cur.execute("UPDATE league_state SET current_season = %s, current_week = 1 WHERE id = 1", (S,))
    if cur.rowcount == 0:
        cur.execute("INSERT INTO league_state (id, current_season, current_week) VALUES (1, %s, 1)", (S,))
    ids = {}
    for abbr, city, conf in _REPRO_TEAMS:
        cur.execute("INSERT INTO teams (name, city, abbreviation, conference, division) VALUES (%s,%s,%s,%s,%s) "
                    "RETURNING id", (f"{city} {abbr}", city, abbr, conf, f"{conf} East"))
        tid = cur.fetchone()[0]
        ids[abbr] = tid
        w, l = _REPRO_REC[abbr]
        for s, ww, ll in ((S, w, l), (S - 1, 8, 9)):
            cur.execute("INSERT INTO team_seasons (team_id, season, wins, losses, expected_wins) "
                        "VALUES (%s,%s,%s,%s,%s)", (tid, s, ww, ll, 8.5))
        cid = coach(cur, f"HC {abbr}", tid, S - 4)
        for role in ("OC", "DC"):
            coord(cur, f"{role} {abbr}", tid, role, S - 3)
        coach_gm = abbr == coach_gm_abbr
        gid = gm(cur, f"HC {abbr}" if coach_gm else f"GM {abbr}", tid, S - 3, cid if coach_gm else None)
        if coach_gm:
            cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years,
                           remaining_years, signed_season, full_control, buyout_owed)
                           VALUES (%s,%s,'HC',20000000,5,3,%s,true,0)""", (tid, cid, S - 2))
            cur.execute("""INSERT INTO full_control_history (season, team_id, coach_id, annual_salary, contract_years,
                           trigger_reason) VALUES (%s,%s,%s,20000000,5,'seed')""", (S - 3, tid, cid))
        else:
            cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years,
                           remaining_years, signed_season, full_control, buyout_owed)
                           VALUES (%s,%s,'HC',8000000,5,3,%s,false,0)""", (tid, cid, S - 2))
            cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years,
                           remaining_years, signed_season, full_control, buyout_owed)
                           VALUES (%s,%s,'GM',3000000,4,2,%s,false,0)""", (tid, gid, S - 2))
        cur.execute("INSERT INTO gm_team_seasons (team_id, season, gm_id, direction) VALUES (%s,%s,%s,'CONTEND')",
                    (tid, S, gid))
    order = [ids[t[0]] for t in _REPRO_TEAMS]
    wk = 1
    for i in range(4):
        for j in range(i + 1, 4):
            cur.execute("""INSERT INTO games (season, week, home_team_id, away_team_id, home_score, away_score, status,
                           game_type) VALUES (%s,%s,%s,%s,24,17,'COMPLETED','REGULAR_SEASON')""",
                        (S, wk, order[i], order[j]))
            wk += 1
    cur.execute("""INSERT INTO games (season, week, home_team_id, away_team_id, home_score, away_score, status,
                   game_type) VALUES (%s,19,%s,%s,27,20,'COMPLETED','WILD_CARD')""", (S, ids["BUF"], ids["NYJ"]))
    return ids


def sc_carousel_a(cur):
    """pg_repro scenario A as a timeline: after 2030 the real Black Monday `coaching_carousel.run`, then the Super
    Bowl, `gm_rollover.hot_seat_only(2031)` and `coaching_carousel.run(2030)` again (hiring cycle +
    fill_vacant_gms); annual(2028..2034) around it."""
    from engines import coaching_carousel as cc
    ids = _repro_seed(cur, "MIA")
    names = {ids[a]: a for a in ("BUF", "NYJ", "PHI", "MIA")}

    def offseason():
        cc.run(cur, 2030, log=QUIET)
        cur.execute("""INSERT INTO games (season, week, home_team_id, away_team_id, home_score, away_score, status,
                       game_type) VALUES (%s,22,%s,%s,17,31,'COMPLETED','SUPER_BOWL')""",
                    (_REPRO_S, ids["BUF"], ids["PHI"]))
        gm_rollover.hot_seat_only(cur, 2031, log=QUIET)
        cc.run(cur, 2030, log=QUIET)
    return {2030: [offseason]}, (2028, 2034), names


# ------------------------------------------------------------------------------------------ scenarios (r4.py)
def sc_lifers(cur):
    """HC, OC, DC and GM all signed 2026 and kept through 2036: several re-signs each."""
    tid = team(cur, "NE")
    coach(cur, "Lifer HC", tid, 2026)
    coord(cur, "Lifer OC", tid, "OC", 2026)
    coord(cur, "Lifer DC", tid, "DC", 2026)
    gm(cur, "Lifer GM", tid, 2026)
    return {}, (2026, 2036), {tid: "NE"}


def sc_let_go(cur, after, kind):
    """HC 4-yr 2026-29 and OC 3-yr 2026-28; the HC is fired (or the OC let go) after `after` and replaced."""
    tid = team(cur, "SF")
    hc = coach(cur, "HC", tid, 2026)
    oc = coord(cur, "OC", tid, "OC", 2026)

    def fire():
        if kind == "HC":
            fire_coach(cur, tid, hc, "SF", after)
            coach(cur, "New HC", tid, after + 1)
        else:
            end_coord(cur, after, oc)
            coord(cur, "New OC", tid, "OC", after + 1)
    return {after: [fire]}, (2026, 2033), {tid: "SF"}


def sc_poach(cur, after):
    """An OC on 3 yrs 2026-28 at ATL, poached after `after` to be LAR's head coach (new coaches.id, same name)."""
    a, b = team(cur, "ATL"), team(cur, "LAR")
    coach(cur, "ATL HC", a, 2026)
    bhc = coach(cur, "LAR HC", b, 2026)
    oc = coord(cur, "Hot OC", a, "OC", 2026)

    def poach():
        fire_coach(cur, b, bhc, "LAR", after)
        end_coord(cur, after, oc)
        coach(cur, "Hot OC", b, after + 1)
        coord(cur, "ATL new OC", a, "OC", after + 1)
    return {after: [poach]}, (2026, 2033), {a: "ATL", b: "LAR"}


def sc_retire(cur, after):
    tid = team(cur, "PIT")
    hc = coach(cur, "Old HC", tid, 2026)

    def retire():
        cur.execute("UPDATE coach_tenures SET end_season=%s WHERE coach_id=%s", (after, hc))
        coach(cur, "Next HC", tid, after + 1)
    return {after: [retire]}, (2026, 2033), {tid: "PIT"}


def sc_gm_fired(cur, after):
    """GM 4-yr 2026-29 (annual(2026)); GM hot seat after `after`, fill_vacant_gms(after+1)."""
    tid = team(cur, "BAL")
    coach(cur, "BAL HC", tid, 2026)
    gm(cur, "BAL GM", tid, 2026)

    def fire():
        fire_gm(cur, tid, after)
        gm_rollover.fill_vacant_gms(cur, after + 1, log=QUIET)
    return {after: [fire]}, (2026, 2034), {tid: "BAL"}


def sc_fc_kept(cur, fire_after):
    """Full-control coach-GM 5 x $20M 2031-35, kept past his deal; fired after `fire_after` (None: kept to 2040)."""
    tid = team(cur, "CHI")
    hc = coach(cur, "Godfather", tid, 2031)
    gm(cur, "Godfather", tid, 2031, coach_id=hc)
    full_control_deal(cur, tid, hc, 2031)
    ev = {}
    if fire_after:
        ev[fire_after] = [lambda: fire_coach(cur, tid, hc, "CHI", fire_after),
                          lambda: coach(cur, "Successor", tid, fire_after + 1),
                          lambda: gm_rollover.fill_vacant_gms(cur, fire_after + 1, log=QUIET)]
    return ev, (2031, 2040), {tid: "CHI"}


def sc_fc_no_seat(cur):
    """A full-control HC row with no coach-GM seat, 5 x $20M 2031-35, kept through 2038."""
    tid = team(cur, "DET")
    hc = coach(cur, "FC no seat", tid, 2031)
    gm(cur, "DET GM", tid, 2031)
    full_control_deal(cur, tid, hc, 2031)
    return {}, (2031, 2038), {tid: "DET"}


def sc_elite_gm(cur):
    """An ELITE GM hired for 2033 at $10M (row written at hire, signed 2033), kept through 2042."""
    tid = team(cur, "MIN")
    coach(cur, "MIN HC", tid, 2033)
    g = gm(cur, "Elite GM", tid, 2033)
    cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years,
                   remaining_years, signed_season, full_control) VALUES (%s,%s,'GM',10000000,4,4,2033,false)""",
                (tid, g))
    return {}, (2033, 2042), {tid: "MIN"}


def sc_champion(cur):
    """A kept HC who has won a Super Bowl (coach_seasons snapshot super_bowl_wins = 1): the re-sign price."""
    tid = team(cur, "TEN")
    hc = coach(cur, "Champ HC", tid, 2026)
    cur.execute("""INSERT INTO coach_seasons (coach_id, team_id, season, career_wins, career_losses, career_ties,
                   winning_seasons, playoff_wins, playoff_losses, super_bowl_wins, super_bowl_losses)
                   VALUES (%s,%s,2028,40,20,0,3,4,0,1,0)""", (hc, tid))
    return {}, (2026, 2031), {tid: "TEN"}


def sc_user_teams(cur):
    """JR's CAR and IND (gm_team_settings.user_controlled) next to a CPU HOU, all HC/OC/GM from 2026 kept."""
    ids = {}
    for ab in ("CAR", "IND", "HOU"):
        tid = team(cur, ab)
        ids[tid] = ab
        coach(cur, f"{ab} HC", tid, 2026)
        coord(cur, f"{ab} OC", tid, "OC", 2026)
        gm(cur, f"{ab} GM", tid, 2026)
        if ab != "HOU":
            cur.execute("INSERT INTO gm_team_settings (team_id, user_controlled) VALUES (%s, true)", (tid,))
    return {}, (2026, 2031), ids


def sc_gm_league_start(cur):
    """A league-start GM (start 2026, no contract row yet) fired by the GM hot seat after 2026, before annual(2026):
    the league-start GM fired first."""
    tid = team(cur, "JAX")
    coach(cur, "JAX HC", tid, 2026)
    gm(cur, "JAX GM", tid, 2026)

    def fire():
        fire_gm(cur, tid, 2026)
        gm_rollover.fill_vacant_gms(cur, 2027, log=QUIET)
    return {2026: [fire]}, (2026, 2031), {tid: "JAX"}


def sc_gm_by_hand(cur):
    """gm_personas.seed(replace=)'s shape: the old GM's end_season = S and the new GM's start_season = S."""
    tid = team(cur, "LV")
    coach(cur, "LV HC", tid, 2026)
    gm(cur, "LV old GM", tid, 2026)

    def by_hand():
        cur.execute("UPDATE gm_profiles SET end_season=2028, end_reason='replaced by hand' WHERE team_id=%s "
                    "AND end_season IS NULL", (tid,))
        gm(cur, "LV new GM", tid, 2028)
    return {2028: [by_hand]}, (2026, 2032), {tid: "LV"}


SCENARIOS = {
    # key: (builder, label)
    "hc-y1-replaced": (lambda c: sc_hc(c, 2026, True), "plain HC + OC fired after 2026 (year 1), replaced"),
    "hc-mid-replaced": (lambda c: sc_hc(c, 2027, True), "plain HC + OC fired after 2027 (mid-deal), replaced"),
    "hc-mid-vacant": (lambda c: sc_hc(c, 2027, False), "plain HC + OC fired after 2027 (mid-deal), seat vacant"),
    "hc-last-replaced": (lambda c: sc_hc(c, 2029, True), "plain HC fired after 2029 (last deal year), replaced"),
    "gm-y1-replaced": (lambda c: sc_gm(c, 2033, True), "GM fired after 2033 (year 1), fill_vacant_gms"),
    "gm-mid-replaced": (lambda c: sc_gm(c, 2035, True), "GM fired after 2035 (mid-deal), fill_vacant_gms"),
    "gm-mid-vacant": (lambda c: sc_gm(c, 2035, False), "GM fired after 2035 (mid-deal), seat vacant"),
    "gm-last-replaced": (lambda c: sc_gm(c, 2036, True), "GM fired after 2036 (last deal year), fill_vacant_gms"),
    "fc-y1-replaced": (lambda c: sc_fc(c, 2031, True), "full-control coach-GM fired after 2031 (year 1), replaced"),
    "fc-mid-replaced": (lambda c: sc_fc(c, 2033, True), "full-control coach-GM fired after 2033 (mid), replaced"),
    "fc-mid-vacant": (lambda c: sc_fc(c, 2033, False), "full-control coach-GM fired after 2033 (mid), vacant"),
    "fc-last-vacant": (lambda c: sc_fc(c, 2035, False), "full-control coach-GM fired after 2035 (last yr), vacant"),
    "fc-last-replaced": (lambda c: sc_fc(c, 2035, True), "full-control coach-GM fired after 2035 (last yr), replaced"),
    "movers": (sc_movers, "OC poached to HC and DC lateral after 2027; the other HC fired"),
    "next-season-hire": (sc_next_season_hire, "Godfather and a carousel GM hired for 2028"),
    "rehire": (sc_rehire, "full-control HC fired after 2033, re-hired for 2036"),
    "expiry": (sc_expiry, "plain HC kept past a 4-yr deal, re-signed"),
    "gm-hired-y1": (sc_gm_hired_y1, "GM hired for 2034 by fill_vacant_gms, fired after 2034 (year 1)"),
    "carousel-A": (sc_carousel_a, "real coaching_carousel.run + GM hot seat + fill_vacant_gms, coach-GM fired"),
    "lifers": (sc_lifers, "HC/OC/DC/GM from 2026 kept through 2036 (several re-signs)"),
    "hc-fired-last-deal-yr": (lambda c: sc_let_go(c, 2029, "HC"), "HC 2026-29 fired after 2029 (last deal season)"),
    "hc-fired-resign-yr": (lambda c: sc_let_go(c, 2030, "HC"), "HC 2026-29 kept for 2030 then leaving"),
    "oc-let-go-last-deal-yr": (lambda c: sc_let_go(c, 2028, "OC"), "OC 2026-28 let go after 2028 (last season)"),
    "oc-let-go-resign-yr": (lambda c: sc_let_go(c, 2029, "OC"), "OC 2026-28 kept for 2029 then let go"),
    "oc-poached-last-deal-yr": (lambda c: sc_poach(c, 2028), "OC 2026-28 poached to HC after 2028 (last season)"),
    "oc-poached-resign-yr": (lambda c: sc_poach(c, 2029), "OC 2026-28 kept for 2029, then poached to HC"),
    "hc-retires-resign-yr": (lambda c: sc_retire(c, 2030), "HC 2026-29 kept for 2030, retires after 2030"),
    "gm-fired-last-deal-yr": (lambda c: sc_gm_fired(c, 2029), "GM 2026-29 fired after 2029 (last deal season)"),
    "gm-fired-resign-yr": (lambda c: sc_gm_fired(c, 2030), "GM 2026-29 kept for 2030, fired after 2030"),
    "fc-kept": (lambda c: sc_fc_kept(c, None), "full-control coach-GM 2031-35 kept through 2040"),
    "fc-kept-fired-2037": (lambda c: sc_fc_kept(c, 2037), "full-control coach-GM re-signed 2036, fired after 2037"),
    "fc-fired-resign-yr": (lambda c: sc_fc_kept(c, 2036), "full-control coach-GM kept for 2036, fired after 2036"),
    "fc-no-seat": (sc_fc_no_seat, "full-control HC row without a coach-GM seat, kept through 2038"),
    "elite-gm": (sc_elite_gm, "ELITE GM $10M 2033-36 kept through 2042"),
    "champion-hc": (sc_champion, "Super Bowl-winning HC re-signed 2030"),
    "user-teams": (sc_user_teams, "JR's CAR/IND next to a CPU HOU, staff kept"),
    "gm-league-start": (sc_gm_league_start, "league-start GM (no row yet) fired first, after 2026"),
    "gm-by-hand": (sc_gm_by_hand, "GM replaced by hand in 2028 (old end = new start)"),
}

# (kind, who, deal) -> (total paid over the timeline, buyout total or None, buyout first season or None).
# Reviewed against the auditor's verify_last runs at c581479 (invariant.head.sum, r4.head.sum).
EXPECTED = {
    "hc-y1-replaced": {("HC", "Old HC", "2026-2029"): (24 * M, 18 * M, 2027),
                       ("OC", "Old OC", "2026-2028"): (5_850_000, 3_900_000, 2027),
                       ("HC", "New HC", "2027-2030"): (24 * M, None, None)},
    "hc-mid-replaced": {("HC", "Old HC", "2026-2029"): (24 * M, 12 * M, 2028),
                        ("OC", "Old OC", "2026-2028"): (5_850_000, 1_950_000, 2028)},
    "hc-mid-vacant": {("HC", "Old HC", "2026-2029"): (24 * M, 12 * M, 2028),
                      ("OC", "Old OC", "2026-2028"): (5_850_000, 1_950_000, 2028)},
    "hc-last-replaced": {("HC", "Old HC", "2026-2029"): (24 * M, None, None),
                         ("OC", "Old OC", "2029-2029"): (1_950_000, None, None)},
    "gm-y1-replaced": {("GM", "Old GM", "2033-2036"): (11 * M, 8_250_000, 2034)},
    "gm-mid-replaced": {("GM", "Old GM", "2033-2036"): (11 * M, 5_500_000, 2035)},
    "gm-mid-vacant": {("GM", "Old GM", "2033-2036"): (11 * M, 5_500_000, 2035)},
    "gm-last-replaced": {("GM", "Old GM", "2033-2036"): (11 * M, 2_750_000, 2036)},
    "fc-y1-replaced": {("HC", "Godfather", "2031-2035"): (100 * M, 100 * M, 2031)},
    "fc-mid-replaced": {("HC", "Godfather", "2031-2035"): (100 * M, 60 * M, 2033)},
    "fc-mid-vacant": {("HC", "Godfather", "2031-2035"): (100 * M, 60 * M, 2033)},
    "fc-last-vacant": {("HC", "Godfather", "2031-2035"): (100 * M, 20 * M, 2035)},
    "fc-last-replaced": {("HC", "Godfather", "2031-2035"): (100 * M, 20 * M, 2035)},
    "movers": {("HC", "B HC", "2026-2029"): (24 * M, 12 * M, 2028),
               ("OC", "A OC", "2026-2028"): (3_900_000, None, None),
               ("DC", "A DC", "2026-2028"): (3_900_000, None, None),
               ("DC", "B DC", "2026-2028"): (5_850_000, 1_950_000, 2028),
               ("HC", "A OC", "2028-2031"): (24 * M, None, None)},
    "next-season-hire": {("HC", "Old KC HC", "2026-2029"): (24 * M, 12 * M, 2028),
                         ("HC", "Godfather", "2028-2032"): (100 * M, None, None),
                         ("GM", "DEN GM", "2026-2029"): (11 * M, 8_250_000, 2027)},
    "rehire": {("HC", "Boomerang", "2031-2035"): (100 * M, 60 * M, 2033),
               ("HC", "Interim", "2034-2037"): (24 * M, 12 * M, 2036),
               ("HC", "Boomerang", "2036-2039"): (80 * M, None, None)},
    "expiry": {("HC", "Lifer", "2026-2029"): (24 * M, None, None),
               ("HC", "Lifer", "2030-2033"): (44 * M, None, None)},
    "gm-hired-y1": {("GM", None, "2034-2037"): (11 * M, 11 * M, 2034)},
    "carousel-A": {("HC", "HC MIA", "2028-2032"): (100 * M, 60 * M, 2030),
                   ("HC", "HC BUF", "2028-2032"): (40 * M, 16 * M, 2031),
                   ("GM", "GM BUF", "2028-2031"): (12 * M, 6 * M, 2030)},
    "lifers": {("HC", "Lifer HC", "2026-2029"): (24 * M, None, None),
               ("HC", "Lifer HC", "2030-2033"): (44 * M, None, None),
               ("GM", "Lifer GM", "2030-2033"): (23 * M, None, None),
               ("OC", "Lifer OC", "2032-2034"): (9_750_000, None, None)},
    "hc-fired-last-deal-yr": {("HC", "HC", "2026-2029"): (24 * M, None, None)},
    "hc-fired-resign-yr": {("HC", "HC", "2030-2030"): (6 * M, None, None)},
    "oc-let-go-last-deal-yr": {("OC", "OC", "2026-2028"): (5_850_000, None, None)},
    "oc-let-go-resign-yr": {("OC", "OC", "2029-2029"): (1_950_000, None, None)},
    "oc-poached-last-deal-yr": {("OC", "Hot OC", "2026-2028"): (5_850_000, None, None),
                                ("HC", "LAR HC", "2026-2029"): (24 * M, 6 * M, 2029)},
    "oc-poached-resign-yr": {("OC", "Hot OC", "2029-2029"): (1_950_000, None, None)},
    "hc-retires-resign-yr": {("HC", "Old HC", "2030-2030"): (6 * M, None, None)},
    "gm-fired-last-deal-yr": {("GM", "BAL GM", "2026-2029"): (11 * M, 2_750_000, 2029)},
    "gm-fired-resign-yr": {("GM", "BAL GM", "2030-2030"): (2_750_000, None, None)},
    "fc-kept": {("HC", "Godfather", "2031-2035"): (100 * M, None, None),
                ("HC", "Godfather", "2036-2039"): (80 * M, None, None)},
    "fc-kept-fired-2037": {("HC", "Godfather", "2036-2039"): (80 * M, 60 * M, 2037)},
    "fc-fired-resign-yr": {("HC", "Godfather", "2036-2036"): (20 * M, None, None)},
    "fc-no-seat": {("HC", "FC no seat", "2031-2035"): (100 * M, None, None)},
    "elite-gm": {("GM", "Elite GM", "2033-2036"): (40 * M, None, None),
                 ("GM", "Elite GM", "2037-2040"): (40 * M, None, None)},
    "champion-hc": {("HC", "Champ HC", "2026-2029"): (76 * M, None, None),
                    ("HC", "Champ HC", "2030-2033"): (38 * M, None, None)},
    "user-teams": {("HC", "CAR HC", "2026-2029"): (24 * M, None, None),
                   ("GM", "IND GM", "2026-2029"): (11 * M, None, None)},
    "gm-league-start": {("GM", "JAX GM", "2026-2029"): (11 * M, 8_250_000, 2027)},
    "gm-by-hand": {("GM", "LV old GM", "2026-2029"): (11 * M, 5_500_000, 2028),
                   ("GM", "LV new GM", "2028-2031"): (11 * M, None, None)},
}


def expected_mismatches(report, expected):
    """[str] for each EXPECTED entry the report's deals don't match (missing, ambiguous or a different total)."""
    out = []
    for (kind, who, deal), (total, buyout, fired) in sorted(expected.items(), key=str):
        hits = [d for d in report["deals"] if d["kind"] == kind and d["deal"] == deal
                and (who is None or d["who"] == who)]
        label = f"{kind} {who or '*'} {deal}"
        if len(hits) != 1:
            out.append(f"{label}: {len(hits)} deals found, expected 1")
            continue
        d = hits[0]
        got = (d["total_paid"], d["buyout_total"] or None, d["fired_season"])
        if got != (total, buyout, fired):
            out.append(f"{label}: paid/buyout/from {got}, expected {(total, buyout, fired)}")
    return out


def run_rerun_safety(cur):
    """r4's rerun scenario: per league year a dry run writes nothing and predicts the real run, and a second
    annual() changes nothing. Returns a list of problems."""
    tid = team(cur, "CIN")
    coach(cur, "CIN HC", tid, 2026)
    coord(cur, "CIN OC", tid, "OC", 2026)
    gm(cur, "CIN GM", tid, 2026)
    keys = ("filed", "rolled", "fired")
    problems = []
    for y in range(2026, 2036):
        before = inv.snapshot_rows(cur)
        dry = scr.annual(cur, y, dry_run=True)
        if inv.snapshot_rows(cur) != before:
            problems.append(f"{y}: the dry run wrote")
        first = scr.annual(cur, y)
        after1 = inv.snapshot_rows(cur)
        scr.annual(cur, y)
        if inv.snapshot_rows(cur) != after1:
            problems.append(f"{y}: a second annual() changed the ledger")
        if {k: dry[k] for k in keys} != {k: first[k] for k in keys}:
            problems.append(f"{y}: dry run {[dry[k] for k in keys]} != real {[first[k] for k in keys]} "
                            "(filed, rolled, fired)")
    dups = inv.live_duplicates(inv.snapshot_rows(cur))
    if dups:
        problems.append(f"live duplicates {dups}")
    return problems


def run_one(conn, key):
    """Runs one scenario in its own rolled-back transaction; returns its result dict."""
    cur = conn.cursor()
    try:
        cur.execute("UPDATE league_state SET current_season = 2030 WHERE id = 1")
        if key == "rerun":
            problems = run_rerun_safety(cur)
            return {"key": key, "label": "rerun safety: dry run and second annual() write nothing",
                    "ok": not problems, "invariant_ok": not problems, "issues": problems, "expected": [],
                    "deals": [], "held_unpaid": [], "live_duplicates": [], "team_checks": []}
        build, label = SCENARIOS[key]
        events, (first, last), names = build(cur)
        rep = timeline(cur, first, last, events, names, label)
        mism = expected_mismatches(rep, EXPECTED.get(key, {}))
        rep.update(key=key, label=label, expected=mism, invariant_ok=rep["ok"])
        rep["ok"] = rep["ok"] and not mism
        return rep
    except Exception as e:      # noqa: BLE001 - one broken scenario is reported, the rest still run
        return {"key": key, "label": SCENARIOS.get(key, (None, key))[1], "ok": False,
                "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-2500:]}
    finally:
        conn.rollback()


ALL = list(SCENARIOS) + ["rerun"]


def run_all(dsn, keys=None, log=print):
    """Runs the scenarios on a scratch DSN whose schema is already built (empty: no seed)."""
    results = []
    with safety.connect_scratch(dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM teams")
        if cur.fetchone()[0]:
            raise safety.SafetyError("the timelines need an EMPTY initialised database (db/init_db.sql, no seed)")
        conn.rollback()
        for key in keys or ALL:
            r = run_one(conn, key)
            log(f"  {'PASS' if r['ok'] else 'FAIL'}  {key}")
            results.append(r)
    return results
