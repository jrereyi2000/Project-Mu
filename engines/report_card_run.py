"""Annual NFLPA report-card producer (NFLPA_REPORT_CARD_PLAN follow-up; CLOSE_OUT_CHECKLIST P1-20/P1-23).

`engines/report_card_engine.py` is pure and the schema (`2027_02_08_nflpa_report_cards.sql`) has the tables, but
nothing filed a season's card or its owner cash ledger. This producer, run at the league office's offseason
FREE_AGENCY stage, for every team:

  1. starts from last season's card (or the 2026 baseline before one exists);
  2. completes CapEx renovations whose construction season has arrived (their target grades apply);
  3. wears down the physical categories one sub-tier per three seasons without a renovation (§2.3d);
  4. applies the team's enabled OpEx policies (§2.3b);
  5. files `team_report_cards` for the season (overall score and grade from the engine);
  6. files `owner_cash_ledgers`: the wealth band's base budget + the season's success dividend (wins, home playoff
     gates, Super Bowl), less the staff payroll (`staff_contracts`), OpEx and CapEx;
  7. **CPU owners** (P1-23) then order facility upgrades on their own policy
     (`engines.owner_policy.facility_upgrade_orders`: aggressive / balanced / frugal), spending only cash left
     above their reserve. JR's teams never order their own (he spends in the Franchise Command Center).

Idempotent: cards and ledgers are upserted per (season, team); an upgrade already ordered in the season is not
ordered again. CLI:
  python3 -m engines.report_card_run --season S [--no-cpu-upgrades] [--dry-run] [--dsn DSN]
"""
from __future__ import annotations

import argparse
import json
import os

import psycopg

from engines import owner_policy as op
from engines import report_card_engine as rce
from engines import staff_contracts_engine as sce

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
BACKFILL_START = 2026                 # the season the baseline cards describe (degradation starts after it)

CARD_COLS = ("season", "team_id", "weight_room", "training_room", "locker_room", "food_nutrition", "team_travel",
             "family_treatment", "head_coach", "ownership", "overall_score", "overall_grade", "summary_text",
             "last_renovated_season")


def _table(cur, name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    row = cur.fetchone()
    return bool(row and row[0])


def _installed(cur):
    return all(_table(cur, t) for t in ("team_report_cards", "owner_cash_ledgers", "facility_upgrades",
                                        "team_operating_policies", "teams"))


def _grades(card):
    """The eight category grades from a card (a team with no baseline card starts neutral at C)."""
    return {k: card.get(k, "C") for k in rce.CATEGORIES}


def _previous_cards(cur, season):
    cur.execute(f"""SELECT {', '.join(CARD_COLS)} FROM team_report_cards WHERE season < %s
                    ORDER BY season DESC""", (season,))
    out = {}
    for r in cur.fetchall():
        d = dict(zip(CARD_COLS, r))
        out.setdefault(d["team_id"], d)
    return out


def _top5_popularity(cur, season):
    """The season's five most popular teams (`team_seasons.popularity_index`), for the cash pool's +$2M term. Empty
    when the table is absent (a fresh franchise before the engines have run) or nothing is stored yet."""
    cur.execute("SELECT to_regclass('team_seasons') IS NOT NULL")
    row = cur.fetchone()
    if not row or not row[0]:
        return set()
    # Only teams with a computed popularity can be a top-5 franchise; a season with none stored yet pays nobody.
    cur.execute("SELECT team_id FROM team_seasons WHERE season = %s AND popularity_index IS NOT NULL "
                "ORDER BY popularity_index DESC LIMIT 5", (season,))
    return {r[0] for r in cur.fetchall()}


def _upgrades(cur, season):
    cur.execute("""SELECT id, team_id, season_ordered, category, cost_dollars, target_grade, completion_season,
                          status FROM facility_upgrades ORDER BY id""")
    cols = ("id", "team_id", "season_ordered", "category", "cost_dollars", "target_grade", "completion_season",
            "status")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _policies(cur):
    cur.execute("""SELECT team_id, luxury_travel_enabled, no_roommate_policy, family_daycare_enabled,
                          private_chef_program, annual_opex_cost FROM team_operating_policies""")
    out = {}
    for tid, travel, roommate, daycare, chef, cost in cur.fetchall():
        enabled = [p for p, on in (("luxury_travel", travel), ("family_daycare", daycare), ("private_chef", chef))
                   if on]
        out[tid] = {"enabled": enabled, "opex": sum(rce.opex_cost(p) for p in enabled), "no_roommate": roommate}
    return out


def _results(cur, season):
    """{team_id: {wins, home_playoff_games, sb_appearance, sb_championship}} for the season just played."""
    out = {}
    cur.execute("SELECT team_id, wins FROM team_seasons WHERE season = %s", (season,))
    for tid, wins in cur.fetchall():
        out.setdefault(tid, {"wins": int(wins or 0), "home_playoff_games": 0, "sb_appearance": False,
                             "sb_championship": False})
    cur.execute("""SELECT home_team_id, away_team_id, home_score, away_score, game_type FROM games
                   WHERE season = %s AND status = 'COMPLETED' AND game_type <> 'REGULAR_SEASON'""", (season,))
    for home, away, hs, as_, gtype in cur.fetchall():
        for tid, is_home in ((home, True), (away, False)):
            if tid is None:
                continue
            rec = out.setdefault(tid, {"wins": 0, "home_playoff_games": 0, "sb_appearance": False,
                                       "sb_championship": False})
            if is_home:
                rec["home_playoff_games"] += 1
            if gtype == "SUPER_BOWL":
                rec["sb_appearance"] = True
                if (hs or 0) > (as_ or 0) if is_home else (as_ or 0) > (hs or 0):
                    rec["sb_championship"] = True
    return out


def _payroll(cur, season, team_id):
    """One team's active HC/OC/DC payroll for the season, from `staff_contracts` (0 without the table)."""
    if not _table(cur, "staff_contracts"):
        return 0
    cur.execute("""SELECT person_kind, annual_salary FROM staff_contracts
                   WHERE team_id = %s AND person_kind IN ('HC', 'OC', 'DC') AND signed_season <= %s
                     AND total_years - (%s - signed_season) > 0""", (team_id, season, season))
    return int(sce.staff_payroll([{"person_kind": k, "annual_salary": s} for k, s in cur.fetchall()]))


def _previous_bands(cur, season):
    """{team_id: wealth_band} from each team's latest owner cash ledger before `season`."""
    cur.execute("""SELECT team_id, wealth_band FROM owner_cash_ledgers WHERE season < %s
                   ORDER BY season DESC""", (season,))
    out = {}
    for tid, band in cur.fetchall():
        out.setdefault(tid, band)
    return out


def _previous_reserve(cur, season, team_id):
    cur.execute("""SELECT accumulated_reserve FROM owner_cash_ledgers WHERE team_id = %s AND season < %s
                   ORDER BY season DESC LIMIT 1""", (team_id, season))
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def annual(cur, season, dry_run=False, cpu_upgrades=True, user_team_ids=()):
    """File the season's report cards and owner cash ledgers, then let CPU owners order facility upgrades.
    Returns {season, status, teams, upgraded, orders}."""
    season = int(season)
    if not _installed(cur):
        return {"season": season, "status": "not_installed", "teams": 0, "upgraded": 0, "orders": 0}
    baseline = rce.load_baseline()
    cur.execute("SELECT id, abbreviation FROM teams WHERE abbreviation IS NOT NULL ORDER BY id")
    teams = cur.fetchall()
    prev = _previous_cards(cur, season)
    upgrades = _upgrades(cur, season)
    policies = _policies(cur)
    results = _results(cur, season)
    bands = _previous_bands(cur, season)
    views = op_views(cur)
    user = set(user_team_ids or ())
    cards, ledgers, orders = {}, {}, []
    completed = []
    top5 = _top5_popularity(cur, season)
    for tid, abbr in teams:
        base = baseline.get((abbr or "").upper()) or {}
        old = prev.get(tid)
        card = _grades(old) if old else _grades(base)
        last_renovated = (old or {}).get("last_renovated_season")
        # 2. this season's completed renovations
        for u in upgrades:
            # COMPLETED included: a re-run of the same season used to skip the upgrade it had already completed,
            # falling back to season-1's card, reverting the renovation and letting a CPU owner order it again.
            if u["team_id"] == tid and u["status"] in ("IN_PROGRESS", "COMPLETED") \
                    and season >= u["completion_season"]:
                card[u["category"]] = u["target_grade"]
                last_renovated = max(last_renovated or 0, u["completion_season"]) or None
                completed.append(u["id"])
        # 3. degradation for unmaintained physical categories
        unmaintained = season - (last_renovated or BACKFILL_START)
        # One sub-tier per 3 unmaintained seasons (rce.degrade's own rule): a backfill applies every missed notch at
        # once, and each later season applies one only on the season that completes another three. Passing a flat 3
        # dropped a sub-tier EVERY season from year 3 on, three times too fast.
        if not old:
            card = rce.degrade(card, unmaintained)
        elif unmaintained >= 3 and unmaintained % 3 == 0:
            card = rce.degrade(card, 3)
        # 4. OpEx policies
        pol = policies.get(tid) or {"enabled": [], "opex": 0}
        for p in pol["enabled"]:
            card = rce.apply_policy(card, p)
        score = rce.facility_score(card)
        cards[tid] = {"season": season, "team_id": tid, **_grades(card), "overall_score": score,
                      "overall_grade": rce.score_to_grade(score),
                      "summary_text": (old or {}).get("summary_text") or base.get("summary_text"),
                      "last_renovated_season": last_renovated}
        # 6. the owner cash ledger
        band = bands.get(tid) or base.get("wealth_band") or "TIER_3"
        res = results.get(tid) or {}
        budget = rce.annual_cash_pool(band, wins=res.get("wins", 0), home_playoff_games=res.get("home_playoff_games", 0),
                                      sb_appearance=res.get("sb_appearance", False),
                                      sb_championship=res.get("sb_championship", False),
                                      top5_popularity=tid in top5)      # the engine's own +$2M term
        payroll = _payroll(cur, season, tid)
        ordered = [u for u in upgrades if u["team_id"] == tid and u["season_ordered"] == season]
        capex = sum(int(u["cost_dollars"] or 0) for u in ordered)
        reserve = max(0, _previous_reserve(cur, season, tid) + budget - payroll - pol["opex"] - capex)
        ledgers[tid] = {"season": season, "team_id": tid, "wealth_band": band,
                        "base_budget": rce.base_budget(band),
                        "success_dividend": budget - rce.base_budget(band), "total_cash_budget": budget,
                        "staff_payroll": payroll, "opex_committed": pol["opex"], "capex_spent": capex,
                        "accumulated_reserve": reserve}
        # 7. CPU owners spend on facilities on their own policy (P1-23)
        if cpu_upgrades and tid not in user:
            reserve_floor = int(budget * op.FACILITY_RESERVE_SHARE[op.facility_style(views.get(tid))])
            avail = max(0, budget - payroll - pol["opex"] - capex - reserve_floor)
            already = {u["category"] for u in ordered}
            chosen = [o for o in op.facility_upgrade_orders(card, views.get(tid), avail, season)
                      if o["category"] not in already]
            for order in chosen:
                orders.append(dict(order, team_id=tid))
                ledgers[tid]["capex_spent"] += int(order["cost_dollars"])
                ledgers[tid]["accumulated_reserve"] = max(
                    0, ledgers[tid]["accumulated_reserve"] - int(order["cost_dollars"]))
    if not dry_run:
        for cid in completed:
            cur.execute("UPDATE facility_upgrades SET status = 'COMPLETED' WHERE id = %s", (cid,))
        for tid, card in cards.items():
            cur.execute(f"""INSERT INTO team_report_cards ({', '.join(CARD_COLS)})
                            VALUES ({', '.join(['%s'] * len(CARD_COLS))})
                            ON CONFLICT (season, team_id) DO UPDATE SET
                                weight_room = EXCLUDED.weight_room, training_room = EXCLUDED.training_room,
                                locker_room = EXCLUDED.locker_room, food_nutrition = EXCLUDED.food_nutrition,
                                team_travel = EXCLUDED.team_travel, family_treatment = EXCLUDED.family_treatment,
                                head_coach = EXCLUDED.head_coach, ownership = EXCLUDED.ownership,
                                overall_score = EXCLUDED.overall_score, overall_grade = EXCLUDED.overall_grade,
                                summary_text = EXCLUDED.summary_text,
                                last_renovated_season = EXCLUDED.last_renovated_season""",
                        tuple(card[k] for k in CARD_COLS))
        for tid, led in ledgers.items():
            cur.execute("""INSERT INTO owner_cash_ledgers (season, team_id, wealth_band, base_budget, success_dividend,
                               total_cash_budget, staff_payroll, opex_committed, capex_spent, accumulated_reserve)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (season, team_id) DO UPDATE SET wealth_band = EXCLUDED.wealth_band,
                               base_budget = EXCLUDED.base_budget, success_dividend = EXCLUDED.success_dividend,
                               total_cash_budget = EXCLUDED.total_cash_budget,
                               staff_payroll = EXCLUDED.staff_payroll, opex_committed = EXCLUDED.opex_committed,
                               capex_spent = EXCLUDED.capex_spent,
                               accumulated_reserve = EXCLUDED.accumulated_reserve""",
                        (led["season"], led["team_id"], led["wealth_band"], led["base_budget"],
                         led["success_dividend"], led["total_cash_budget"], led["staff_payroll"],
                         led["opex_committed"], led["capex_spent"], led["accumulated_reserve"]))
        for order in orders:
            cur.execute("""INSERT INTO facility_upgrades (team_id, season_ordered, category, cost_dollars,
                               target_grade, completion_season, status)
                           VALUES (%s, %s, %s, %s, %s, %s, 'IN_PROGRESS')""",
                        (order["team_id"], order["season_ordered"], order["category"], order["cost_dollars"],
                         order["target_grade"], order["completion_season"]))
    return {"season": season, "status": "dry_run" if dry_run else "built", "teams": len(cards),
            "upgraded": len(completed), "orders": len(orders), "orders_detail": orders}


def op_views(cur):
    """{team_id: owner view} for the reviewed owners ({} on any error)."""
    try:
        from engines import owner_context as octx
        return octx.team_owner_views(cur)
    except Exception:  # noqa: BLE001 - a neutral owner is fine
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="File the annual NFLPA report cards and owner cash ledgers")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--no-cpu-upgrades", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dsn", default=CONN_INFO)
    a = ap.parse_args(argv)
    with psycopg.connect(a.dsn) as conn:
        cur = conn.cursor()
        rep = annual(cur, a.season, dry_run=a.dry_run, cpu_upgrades=not a.no_cpu_upgrades)
        if a.dry_run:
            conn.rollback()
        else:
            conn.commit()
    print(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
