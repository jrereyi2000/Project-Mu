"""
Free agency engine (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.4, U5; V2 master plan §4).

A free agent never signs with one click: he scores every offer with a utility and accepts, counters or rejects
the best one. Pure functions over plain dicts, money in whole dollars, no DB access; randomness comes only from
sha256 of a seed string (no global random state), so a run is reproducible for its seed.

  U = w_money S_financial + w_role S_opportunity + w_team S_contention + w_scheme S_scheme + e_persona
      W_DEFAULT = money 0.45, role 0.25, team 0.20, scheme 0.10 (plan weights); e_persona is 0 (plan D4: persona
      traits ship at 0; the argument is accepted and ignored).
  S_financial    w_val s_value + w_guar s_guar + w_term s_term, weights (0.70, 0.15, 0.15), veterans 30+
                 (0.55, 0.30, 0.15). s_value = AAV / W(ovr, group) capped at FIN_VALUE_CAP 1.6, W from
                 engines/trade_value.worth with the market fit passed in (market None = the league minimum for his
                 accrued seasons, the street and UDFA markets). s_guar = guaranteed share / target (0.35, veterans
                 0.60), minus a backload penalty for veterans when the offer carries salary_by_year. s_term = years
                 fit by age: <= 23 ideal 3, 24-27 ideal 2 (prefer shorter, test the market again), 28-29 ideal 3,
                 30+ more years never hurt (years / 3, capped at 1).
  S_opportunity  role_score(projected_depth, ovr): 1 clear starter 1.0, 2 situational 0.65, 3+ backup 0.3; an
                 80+ OVR player treats backup as severe (0.15). Depth from team["projected_depth"][player_id], else
                 offer["projected_depth"], else situational.
  S_contention   0.45 win_pct + 0.35 playoff_odds + 0.20 popularity_index / 100 (each 0.5 when missing),
                 +0.05 CONTEND / -0.05 REBUILD direction. A contender gets the same utility for less money (the
                 contender discount); a rebuilder must pay more (the rebuilder tax).
  S_scheme       team["scheme_fit"][player_id] in 0..1, else 0.5.

  acceptance     ACCEPT when U >= bar; bar = 0.68 + 0.005 (ovr - 75) clamped to [0.55, 0.80], minus 0.15 in
                 season (street free agents want to stay in the league). An 80+ OVR player offered a backup role
                 (depth 3+) REJECTs unless the AAV is >= 1.5 W (STAR_BACKUP_PREMIUM) and the bar is met. Otherwise
                 the smallest raise (5% steps, at most +35%, at his ideal term and guarantee share) that clears the
                 bar is a COUNTER {years, total_value, bonus, guaranteed}; none -> REJECT.
  waves          offseason: 1 superstars (OVR >= 85), 2 quality starters (>= 75), 3 depth. Wave n considers every
                 unsigned player of tier <= n; a player past his tier's wave lowers his bar 0.03 a wave (market
                 cools). Each player gets one decision: offers ranked by U plus seeded noise (+-0.03), the first
                 ACCEPT wins, else the first COUNTER, else REJECT on the best; his bar carries a seeded +-0.03 mood.
  street / UDFA  teams with a need in the player's group (and roster spots: street 1, UDFA 3 by default) bid;
                 street = 1 year at the league minimum, UDFA = 3 years at the rookie minimum plus a small seeded
                 priority bonus; the role decides. Players go best-first (OVR, seeded tie-break).

Every weight and threshold here is an estimate (V2 §4 names the shape, not the numbers) except W_DEFAULT and the
role values 1.0 / 0.65 / 0.3, which the plan fixes. Statuses are ACCEPT | REJECT | COUNTER; the caller maps them
to contract_offers.status (ACCEPTED / REJECTED / COUNTERED).
"""
import hashlib

from engines.contracts_engine import minimum_salary
from engines.trade_value import worth

W_DEFAULT = {"money": 0.45, "role": 0.25, "team": 0.20, "scheme": 0.10}

# role (plan values; STAR_* estimates)
STARTER_U, SITUATIONAL_U, BACKUP_U = 1.0, 0.65, 0.3
STAR_OVR = 80                           # "starting caliber": rejects a backup role without a big premium
STAR_BACKUP_U = 0.15                    # an 80+ player's view of a backup role (severe)
STAR_BACKUP_PREMIUM = 1.5               # AAV >= 1.5 W buys an 80+ player's acceptance of a backup role
# financial
FIN_VALUE_CAP = 1.6                     # money above 1.6 W stops adding
FIN_WEIGHTS = (0.70, 0.15, 0.15)        # value, guarantee, term
FIN_WEIGHTS_VET = (0.55, 0.30, 0.15)
VET_AGE = 30
GUAR_TARGET, GUAR_TARGET_VET = 0.35, 0.60
BACKLOAD_PENALTY = 1.0                  # x (back-half share - front-half share) x non-guaranteed share, veterans
# contention
CONTENTION_WEIGHTS = (0.45, 0.35, 0.20)  # win pct, playoff odds, popularity / 100
DIRECTION_SHIFT = {"CONTEND": 0.05, "REBUILD": -0.05}
NEUTRAL = 0.5
# acceptance
BASE_BAR, BAR_PER_OVR, BAR_CLAMP = 0.68, 0.005, (0.55, 0.80)
IN_SEASON_DISCOUNT = 0.15
COUNTER_STEP, COUNTER_MAX_RAISE = 0.05, 0.35
# waves and markets
WAVE_TIERS = ((1, 85), (2, 75), (3, 0))  # wave, minimum OVR
WAVE_COOLING = 0.03
CHOICE_NOISE = 0.03
MOOD_NOISE = 0.03
SITUATIONAL_WITHIN = 4                  # within 4 OVR of a team's weakest starter = situational role
STREET_SPOTS, UDFA_SPOTS = 1, 3
UDFA_YEARS = 3
UDFA_BONUS_MAX = 100_000                # priority UDFA signing bonus, at most (estimate)


# ---------------------------------------------------------------- helpers ----
def _u01(*parts):
    """A uniform number in [0, 1) from sha256 of the joined parts."""
    h = hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:15], 16) / float(16 ** 15)


def _noise(width, *parts):
    return (2 * _u01(*parts) - 1) * width


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _pid(player):
    return player.get("id", player.get("player_id"))


def _worth(player, market):
    """Season market worth: W(ovr, group) from the trade-value fit, or the league minimum when market is None."""
    floor = minimum_salary(player.get("accrued_seasons") or 0)
    if market is None:
        return float(floor)
    return max(float(floor), worth(player.get("ovr"), player.get("group"), market))


def _aav(offer):
    return (offer.get("total_value") or 0) / max(1, offer.get("years") or 1)


def _depth(offer, player, team):
    d = (team.get("projected_depth") or {}).get(_pid(player))
    if d is None:
        d = offer.get("projected_depth")
    return 2 if d is None else int(d)


def ideal_years(age):
    """The term a player wants: young players a few years, prime players short, veterans as long as possible."""
    age = age or 27
    if age <= 23:
        return 3
    if age <= 27:
        return 2
    if age < VET_AGE:
        return 3
    return 4


def _term_score(years, age):
    years = max(1, years or 1)
    if (age or 27) >= VET_AGE:
        return min(1.0, years / 3.0)
    return _clamp(1 - 0.25 * abs(years - ideal_years(age)))


def _guar_share(offer):
    total = offer.get("total_value") or 0
    if total <= 0:
        return 0.0
    return _clamp(max(offer.get("guaranteed") or 0, offer.get("bonus") or 0) / total)


def _backload(offer):
    """Back-half share minus front-half share of the salaries, 0 without salary_by_year."""
    s = offer.get("salary_by_year") or []
    if len(s) < 2 or sum(s) <= 0:
        return 0.0
    half = len(s) // 2
    return max(0.0, (sum(s[len(s) - half:]) - sum(s[:half])) / sum(s))


def financial_score(offer, player, market):
    """S_financial and its parts (value, guarantee, term)."""
    vet = (player.get("age") or 27) >= VET_AGE
    wv, wg, wt = FIN_WEIGHTS_VET if vet else FIN_WEIGHTS
    s_value = min(FIN_VALUE_CAP, _aav(offer) / _worth(player, market))
    g = _guar_share(offer)
    s_guar = min(1.0, g / (GUAR_TARGET_VET if vet else GUAR_TARGET))
    if vet:
        s_guar = max(0.0, s_guar - BACKLOAD_PENALTY * _backload(offer) * (1 - g))
    s_term = _term_score(offer.get("years"), player.get("age"))
    return wv * s_value + wg * s_guar + wt * s_term


def contention_score(team):
    w_win, w_po, w_pop = CONTENTION_WEIGHTS
    win = team.get("win_pct")
    po = team.get("playoff_odds")
    pop = team.get("popularity_index")
    s = (w_win * (NEUTRAL if win is None else win) + w_po * (NEUTRAL if po is None else po)
         + w_pop * (NEUTRAL if pop is None else pop / 100.0))
    return _clamp(s + DIRECTION_SHIFT.get(team.get("direction"), 0.0))


# ----------------------------------------------------------------- public ----
def role_score(projected_depth, player_ovr):
    """1 clear starter 1.0, 2 situational 0.65, 3+ backup 0.3 (0.15 for an 80+ OVR player)."""
    d = 2 if projected_depth is None else int(projected_depth)
    if d <= 1:
        return STARTER_U
    if d == 2:
        return SITUATIONAL_U
    return STAR_BACKUP_U if (player_ovr or 0) >= STAR_OVR else BACKUP_U


def offer_utility(offer, player, team, market, persona=None):
    """Score one offer for one player. offer: {team_id, player_id, years, total_value, bonus, guaranteed,
    projected_depth?, salary_by_year?}; player: {id, group, ovr, age, accrued_seasons?}; team: {team_id,
    win_pct?, playoff_odds?, popularity_index?, direction?, projected_depth?: {pid: depth}, scheme_fit?: {pid:
    0..1}}; market: a trade_value.fit_market dict, or None for the league-minimum market. persona is ignored (D4).
    Returns {utility, financial, opportunity, contention, scheme, persona: 0.0}."""
    fin = financial_score(offer, player, market)
    opp = role_score(_depth(offer, player, team), player.get("ovr"))
    con = contention_score(team)
    fit = (team.get("scheme_fit") or {}).get(_pid(player))
    sch = NEUTRAL if fit is None else _clamp(float(fit))
    w = W_DEFAULT
    u = w["money"] * fin + w["role"] * opp + w["team"] * con + w["scheme"] * sch + 0.0
    return {"utility": round(u, 4), "financial": round(fin, 4), "opportunity": round(opp, 4),
            "contention": round(con, 4), "scheme": round(sch, 4), "persona": 0.0}


def accept_bar(player, in_season):
    bar = _clamp(BASE_BAR + BAR_PER_OVR * ((player.get("ovr") or 75) - 75), *BAR_CLAMP)
    return bar - (IN_SEASON_DISCOUNT if in_season else 0.0)


def _counter(offer, player, team, market, bar):
    """The smallest raise (COUNTER_STEP steps up to COUNTER_MAX_RAISE) at his ideal term and guarantee share that
    clears the bar, or None."""
    vet = (player.get("age") or 27) >= VET_AGE
    years = max(offer.get("years") or 1, ideal_years(player.get("age"))) if vet else ideal_years(player.get("age"))
    share = max(_guar_share(offer), GUAR_TARGET_VET if vet else GUAR_TARGET)
    bonus_share = (offer.get("bonus") or 0) / max(1, offer.get("total_value") or 1)
    base_aav = max(_aav(offer), 1.0)
    steps = int(round(COUNTER_MAX_RAISE / COUNTER_STEP))
    for k in range(steps + 1):
        total = int(round(base_aav * (1 + k * COUNTER_STEP) * years))
        c = {"team_id": offer.get("team_id"), "player_id": offer.get("player_id"), "years": years,
             "total_value": total, "bonus": int(round(total * bonus_share)), "guaranteed": int(round(total * share)),
             "projected_depth": offer.get("projected_depth")}
        if offer_utility(c, player, team, market)["utility"] >= bar:
            return {"years": c["years"], "total_value": c["total_value"], "bonus": c["bonus"],
                    "guaranteed": c["guaranteed"]}
    return None


def acceptance(best, player, in_season):
    """best: {offer, team, market, score?, bar_shift?} (score = offer_utility's result, computed when missing;
    bar_shift moves the bar, run_wave's seeded mood and wave cooling). Returns (ACCEPT | REJECT | COUNTER,
    counter {years, total_value, bonus, guaranteed} or None)."""
    offer, team, market = best["offer"], best.get("team") or {}, best.get("market")
    score = best.get("score") or offer_utility(offer, player, team, market)
    bar = accept_bar(player, in_season) + (best.get("bar_shift") or 0.0)
    if (player.get("ovr") or 0) >= STAR_OVR and _depth(offer, player, team) >= 3:
        if _aav(offer) < STAR_BACKUP_PREMIUM * _worth(player, market):
            return "REJECT", None
    if score["utility"] >= bar:
        return "ACCEPT", None
    c = _counter(offer, player, team, market, bar)
    return ("COUNTER", c) if c else ("REJECT", None)


def wave_tier(player):
    if player.get("wave"):
        return int(player["wave"])
    for wave, lo in WAVE_TIERS:
        if (player.get("ovr") or 0) >= lo:
            return wave
    return WAVE_TIERS[-1][0]


def _decide(player, scored, in_season, bar_shift, seed, tag):
    """scored: [(index, offer, team, market, score)]. One decision for the player."""
    pid = _pid(player)
    ranked = sorted(scored, key=lambda s: (-(s[4]["utility"] + _noise(CHOICE_NOISE, seed, tag, pid,
                                                                       s[1].get("team_id"), s[0])), s[0]))
    first_counter = None
    for idx, offer, team, market, score in ranked:
        status, counter = acceptance({"offer": offer, "team": team, "market": market, "score": score,
                                      "bar_shift": bar_shift}, player, in_season)
        row = {"player_id": pid, "team_id": offer.get("team_id"), "offer_index": idx, "offer": offer,
               "status": status, "utility": score["utility"], "counter": counter}
        if status == "ACCEPT":
            return row
        if status == "COUNTER" and first_counter is None:
            first_counter = row
    if first_counter:
        return first_counter
    idx, offer, team, market, score = ranked[0]
    return {"player_id": pid, "team_id": offer.get("team_id"), "offer_index": idx, "offer": offer,
            "status": "REJECT", "utility": score["utility"], "counter": None}


def run_wave(players, offers, wave, seed):
    """One offseason wave. players: unsigned free agents (player dicts); offers: offer dicts, each carrying the
    bidding team's context under "team" (offer_utility's team dict) and the market fit under "market" (None = the
    league-minimum market). Considers players of tier <= wave; each player takes at most one offer. Returns one
    decision per considered player with offers: {player_id, team_id, offer_index (into offers), offer, status,
    utility, counter}, in player order."""
    by_player = {}
    for i, o in enumerate(offers):
        by_player.setdefault(o.get("player_id"), []).append(i)
    out = []
    for p in players:
        tier = wave_tier(p)
        pid = _pid(p)
        if tier > wave or pid not in by_player:
            continue
        scored = []
        for i in by_player[pid]:
            o = offers[i]
            team, market = o.get("team") or {}, o.get("market")
            scored.append((i, o, team, market, offer_utility(o, p, team, market)))
        shift = _noise(MOOD_NOISE, seed, "wave", wave, pid) - WAVE_COOLING * (wave - tier)
        out.append(_decide(p, scored, False, shift, seed, f"wave{wave}"))
    return out


def _need_map(team):
    n = team.get("needs") or {}
    if isinstance(n, list):
        return {x["group"]: x.get("score", 1.0) for x in n}
    return dict(n)


def _market_depth(player, team):
    """Role on a team from its weakest starter OVR in the group (team["starter_ovr"]), else need -> situational."""
    known = (team.get("projected_depth") or {}).get(_pid(player))
    if known is not None:
        return int(known)
    line = (team.get("starter_ovr") or {}).get(player.get("group"))
    ovr = player.get("ovr") or 0
    if line is None:
        return 2 if _need_map(team).get(player.get("group"), 0) > 0 else 3
    if ovr >= line:
        return 1
    return 2 if ovr >= line - SITUATIONAL_WITHIN else 3


def _ordered(players, seed, tag):
    return sorted(players, key=lambda p: (-(p.get("ovr") or 0), _u01(seed, tag, "order", _pid(p))))


def _market(players, needs, seed, tag, make_offer, spots_default, in_season):
    spots = {t: int(ctx.get("roster_spots", spots_default)) for t, ctx in needs.items()}
    out = []
    for p in _ordered(players, seed, tag):
        scored = []
        for t in sorted(needs, key=str):
            ctx = needs[t]
            if spots[t] <= 0 or _need_map(ctx).get(p.get("group"), 0) <= 0:
                continue
            offer = make_offer(p, t, ctx)
            offer["projected_depth"] = _market_depth(p, ctx)
            team = dict(ctx, team_id=t)
            scored.append((len(scored), offer, team, None, offer_utility(offer, p, team, None)))
        if not scored:
            continue
        d = _decide(p, scored, in_season, _noise(MOOD_NOISE, seed, tag, "mood", _pid(p)), seed, tag)
        d["offer_index"] = None
        if d["status"] == "ACCEPT":
            spots[d["team_id"]] -= 1
        out.append(d)
    return out


def street_market(players, needs, seed):
    """In-season street free agents. needs: {team_id: {needs: {group: score} | [{group, score}], starter_ovr?:
    {group: weakest starter OVR}, roster_spots? (default 1), win_pct?, playoff_odds?, popularity_index?,
    direction?, scheme_fit?, projected_depth?}}. Teams with a need in his group offer 1 year at the league minimum
    for his accrued seasons; the in-season bar applies. Returns decisions (offer_index None, offer built here)."""
    def make(p, t, ctx):
        m = minimum_salary(p.get("accrued_seasons") or 0)
        return {"team_id": t, "player_id": _pid(p), "years": 1, "total_value": m, "bonus": 0, "guaranteed": 0}
    return _market(players, needs, seed, "street", make, STREET_SPOTS, True)


def udfa_market(prospects, needs, seed):
    """Undrafted rookies after the draft. needs as in street_market (roster_spots default 3). Each team with a
    need in his group offers UDFA_YEARS years at the rookie minimum (0 accrued seasons each year, as Madden's
    flat UDFA deal) plus a seeded priority bonus up to UDFA_BONUS_MAX scaled by need, guaranteed = bonus; the role
    decides. Returns decisions (offer_index None)."""
    def make(p, t, ctx):
        need = min(1.0, _need_map(ctx).get(p.get("group"), 0) / 10.0)
        bonus = int(round(UDFA_BONUS_MAX * need * _u01(seed, "udfa", "bonus", t, _pid(p)) / 1000.0)) * 1000
        base = minimum_salary(0) * UDFA_YEARS
        return {"team_id": t, "player_id": _pid(p), "years": UDFA_YEARS, "total_value": base + bonus,
                "bonus": bonus, "guaranteed": bonus}
    return _market(prospects, needs, seed, "udfa", make, UDFA_SPOTS, True)
