"""Roster moves, contracts and cap facts for the newsroom (docs/done/team_management/05_NEWSROOM_INTEGRATION_PLAN.md).

Pure helpers, no database: the writer tools in newsroom/db_tools.py, the briefs in swarm_dispatcher.py, the Agent
Wire (social_desk.py) and the fantasy tools fetch rows and pass them through here, so the tests need no DB.

- Every money figure is printed with its basis (§2.1): scanned from the team's books, from a CPU signing row, an
  estimated cap row (moves applied since the last scan), or a projected year of a new deal, which always carries
  `est.` (JR, 2026-10-20: projected yearly hits are shown, labelled).
- `txn_visible` is the week cutoff for `transactions` rows (§2.2); `on_roster` is the has-a-team rule for a Week N
  article (`released_week` IS NULL or later than N).
- `notability` decides which moves the briefs and the Agent Wire carry (§2.3). OVR only ranks, it's never printed.
- Trades and rumors (Phase 8): an executed GM trade (engines/front_office.executed_trades) is one `trade_line`, fact;
  a rumor_ok proposal that was never executed (front_office.trade_rumors) is one `rumor_line`, always labelled a
  rumor, with teams and asset types/positions only. `trade_visible` / `rumor_visible` are their week cutoffs.
"""

# Depth slots that count as starters (also the beat injury block's rule).
_STARTER_ONE = ("QB", "TE", "LT", "LG", "C", "RG", "RT", "FS", "SS", "K", "P", "FB")
_STARTER_TWO = ("HB", "DT", "LEDG", "REDG", "LE", "RE")
_STARTER_THREE = ("WR", "CB", "MIKE", "SAM", "WILL", "MLB", "LOLB", "ROLB")

# Notability (JR, 2026-10-20): only moves of players rated NOTABLE_OVR+ are notable, in line with the 90+ marquee
# signings. An in-season free-agent pickup is never rated that high, so it never leads the briefs or the Agent Wire.
# The context flags below only describe and rank a notable move; they never make one notable.
NOTABLE_OVR = 85
NOTABLE_POPULARITY = 50
NOTABLE_RE_SIGN_TOTAL = 20_000_000
NOTABLE_SIGNING_TOTAL = 5_000_000
NOTABLE_RESTRUCTURE_FREED = 5_000_000
NOTABLE_DEAD_MONEY = 5_000_000

OFFSEASON_WEEK = 24          # the offseason package's week (offseason rows have week NULL)
NO_CUTOFF_WEEK = 99          # on_roster with no article context: every release counts

TYPE_VERBS = {
    "SIGNED": "signed",
    "RELEASED": "released",
    "RE_SIGNED": "re-signed",
    "EXTENDED": "extended",
    "RESTRUCTURED": "restructured the contract of",
    "TRADED": "traded",
}

MONEY_NOTE = ("Cite contract terms and cap figures only as these tools give them, with their basis. Years and "
              "totals are fact. A figure marked 'est.' is a projected year of a new deal: hedge it in prose "
              "('roughly', 'projected', 'about'). An estimated cap row is approximate ('roughly $4M in space'). "
              "'not known' means it isn't recorded: never fill it in (no guaranteed money, no incentive details, "
              "no option years). Never give a number these tools don't show.")


def is_starter_rank(pos, rank) -> bool:
    """True when depth slot `rank` (1 = best OVR at the position on the team) is a starting spot."""
    if not rank:
        return False
    if pos in _STARTER_ONE:
        return rank == 1
    if pos in _STARTER_TWO:
        return rank <= 2
    if pos in _STARTER_THREE:
        return rank <= 3
    return rank == 1


def money_text(value) -> str:
    """Whole dollars as prose money: 16800000 -> '$16.8M', 3930000 -> '$3.93M', 200000 -> '$200K',
    -29500000 -> '-$29.5M'. None -> 'not known' (never $0)."""
    if value is None:
        return "not known"
    v = int(value)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000:
        s = f"{a / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}${s}M"
    if a >= 1_000:
        s = f"{a / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{sign}${s}K"
    return f"{sign}${a}"


def est_text(value) -> str:
    """A projected (not scanned) figure: always labelled `est.`."""
    return f"{money_text(value)} est."


def scan_label(capture_week=None, scanned_at=None) -> str:
    """When the books were read: 'Week 4' (the capture's league week), else the scan date."""
    if capture_week is not None:
        return f"Week {capture_week}"
    if scanned_at is not None:
        return str(scanned_at)[:10]
    return "unknown date"


def cap_is_derived(scanned_at, derived_at) -> bool:
    return derived_at is not None and (scanned_at is None or derived_at > scanned_at)


def cap_basis(scanned: str, derived: bool, moves_since: int = 0) -> str:
    """Basis of a team_cap_seasons row (§2.1)."""
    if derived:
        n = int(moves_since or 0)
        return (f"estimated: last cap scan {scanned} plus {n} roster move{'s' if n != 1 else ''} since; "
                "dead money from in-season signings is unknown")
    return f"scanned {scanned}"


def contract_basis(source: str, scanned: str) -> str:
    """Basis of a player_contracts row (§2.1)."""
    if source == "TRANSACTION":
        return "from the signing (bonus, cut savings and dead money not known)"
    return f"scanned {scanned}"


def year_hits(years) -> list:
    """[(year, cap_hit, projected)] -> ['2036: $16.8M', '2037: $14.2M est.', ...]; zero years left out."""
    out = []
    for year, hit, projected in sorted(years or [], key=lambda y: y[0]):
        if not hit:
            continue
        out.append(f"{year}: {est_text(hit) if projected else money_text(hit)}")
    return out


def extension_text(years, value, start_season) -> str:
    """A re-signing or extension's headline terms (fact), e.g. 'new deal: 4 years, $88M total, starts 2038'."""
    if not years:
        return ""
    return (f"new deal: {years} year{'s' if years != 1 else ''}, {money_text(value)} total, starts {start_season}; "
            "year-by-year split is projected (est.)")


def committed_by_year(rows, season: int, horizon: int = 4) -> dict:
    """Committed cap by future year from [(year, cap_hit, projected)] across a team's contracts: scanned hits as fact,
    projected new-deal years added separately with `est.`."""
    out = {}
    for y in range(season + 1, season + 1 + horizon):
        scanned = sum(h or 0 for yr, h, p in rows if yr == y and not p)
        projected = [h for yr, h, p in rows if yr == y and p and h]
        if not scanned and not projected:
            continue
        text = money_text(scanned)
        if projected:
            text += f" + {est_text(sum(projected))} from {len(projected)} new deal{'s' if len(projected) != 1 else ''}"
        out[str(y)] = text
    return out


# ----------------------------------------------------------------------------------------------------------------
# Week cutoff (§2.2)
# ----------------------------------------------------------------------------------------------------------------

def txn_visible(season, week, capture_week, ctx_season, ctx_week, offseason_event_ok=True) -> bool:
    """May a Week `ctx_week` article of `ctx_season` see a transactions row?

    A row headed WEEK N happens before Week N's games (like the injury report), so a Week N article sees it, but
    only when the capture that loaded it was taken at week <= N (a late WEEK N row loaded at N+1 stays out of a
    regenerated Week N article). A row with no capture (step 3's confirmed trades) counts as week <= N. Offseason
    rows (week NULL) are visible only to the offseason package once its signings event is out."""
    if ctx_season is None or ctx_week is None:
        return True
    if season < ctx_season:
        return True
    if season > ctx_season:
        return False
    if week is None:
        return ctx_week >= OFFSEASON_WEEK and bool(offseason_event_ok)
    if week < ctx_week:
        return True
    if week == ctx_week:
        return capture_week is None or capture_week <= ctx_week
    return False


def visible_rows(rows, ctx_season, ctx_week, offseason_event_ok=True) -> list:
    return [r for r in rows if txn_visible(r["season"], r.get("week"), r.get("capture_week"),
                                           ctx_season, ctx_week, offseason_event_ok)]


def new_this_week(rows, ctx_season, ctx_week, offseason_event_ok=True) -> list:
    """Rows a Week N article sees that the Week N-1 article didn't."""
    if ctx_season is None or ctx_week is None:
        return list(rows)
    prev_ok = offseason_event_ok if ctx_week - 1 >= OFFSEASON_WEEK else False
    return [r for r in rows
            if txn_visible(r["season"], r.get("week"), r.get("capture_week"), ctx_season, ctx_week, offseason_event_ok)
            and not txn_visible(r["season"], r.get("week"), r.get("capture_week"), ctx_season, ctx_week - 1, prev_ok)]


def on_roster(released_week, week) -> bool:
    """Is a player on his team for a Week `week` article? Released players leave from their release week on
    (Napper's has-game rule). `week` None = no article context: any release counts."""
    if released_week is None:
        return True
    return released_week > (NO_CUTOFF_WEEK if week is None else week)


def roster_week(cutoff):
    """The N in on_roster's SQL form `(released_week IS NULL OR released_week > N)` for a tool's cutoff week."""
    return NO_CUTOFF_WEEK if cutoff is None else cutoff


def free_agent_label(team_name, released_week) -> str:
    when = "the offseason" if released_week == OFFSEASON_WEEK else ("the preseason" if released_week == 0
                                                                    else f"Week {released_week}")
    return f"free agent (released by the {team_name}, {when})" if team_name else f"free agent (released, {when})"


# ----------------------------------------------------------------------------------------------------------------
# Notability (§2.3) and move lines
# ----------------------------------------------------------------------------------------------------------------

def notability(row) -> tuple:
    """(notable, reasons, rank_score) for a move row. Notable iff the player is rated NOTABLE_OVR+ (the rating is
    never printed). reasons (starter, popular, big deal...) describe the move and rank it. Row keys used: type,
    contract_value, is_starter, popularity, dead_money (stored cut_penalty of a released contract), ovr."""
    t = row.get("type")
    value = row.get("contract_value") or 0
    reasons = []
    if row.get("is_starter"):
        reasons.append("starter")
    if (row.get("popularity") or 0) >= NOTABLE_POPULARITY:
        reasons.append("popular")
    if t in ("RE_SIGNED", "EXTENDED") and value >= NOTABLE_RE_SIGN_TOTAL:
        reasons.append("big new deal")
    if t == "SIGNED" and value >= NOTABLE_SIGNING_TOTAL:
        reasons.append("big signing")
    if t == "RESTRUCTURED" and value >= NOTABLE_RESTRUCTURE_FREED:
        reasons.append("big restructure")
    if t == "RELEASED" and (row.get("dead_money") or 0) >= NOTABLE_DEAD_MONEY:
        reasons.append("big dead money")
    if t == "TRADED":
        reasons.append("trade")
    notable = (row.get("ovr") or 0) >= NOTABLE_OVR or t == "TRADED"     # every executed trade is news (Phase 8)
    score = (10 if notable else 0) + len(reasons) * 3 + (row.get("popularity") or 0) / 20 + (row.get("ovr") or 0) / 25 \
        + max(value, row.get("dead_money") or 0) / 10_000_000
    return notable, reasons, round(score, 3)


def annotate(rows) -> list:
    """Adds notable / notable_reasons / rank_score to each row (in place) and returns them most notable first,
    newest week first within that."""
    for r in rows:
        r["notable"], r["notable_reasons"], r["rank_score"] = notability(r)
    return sorted(rows, key=lambda r: (-int(r["notable"]), -r["rank_score"], -(r.get("week") or 0)))


def display_name(row) -> str:
    """The player's name as prose (players.full_name, else the row's caps name in title case)."""
    name = row.get("full_name") or row.get("player_name") or "Unknown player"
    return name.title() if name.isupper() else name


def week_label(row) -> str:
    w = row.get("week")
    if w is None:
        return "Offseason"
    return f"Week {w}"


def move_line(row, with_notable: bool = True) -> str:
    """One move as a line, e.g. 'Week 4 · SIGNED · Jaguars signed WR Devonta Taylor (free agent): 1 year, $3.93M ·
    now WR4 on the depth chart · notable: no'. Row keys: week, type, position, team (the acting team's name),
    from_team, contract_years, contract_value, new_deal_start, dead_money, depth_slot, later_team, later_week,
    released_week."""
    t = row.get("type")
    pos = row.get("position") or ""
    name = display_name(row)
    team = row.get("team") or "A team"
    parts = [week_label(row), t]
    years = row.get("contract_years")
    value = row.get("contract_value")
    if t == "SIGNED":
        origin = f" (from the {row['from_team']})" if row.get("from_team") else " (free agent)"
        terms = ""
        if years or value:
            terms = f": {years or '?'} year{'s' if years != 1 else ''}, {money_text(value)}"
        if row.get("practice_squad"):
            terms = " to the practice squad"
        parts.append(f"{team} signed {pos} {name}{origin}{terms}")
        if row.get("depth_slot"):
            parts.append(f"now {pos}{row['depth_slot']} on the depth chart")
    elif t == "RELEASED":
        parts.append(f"{team} released {pos} {name}")
        dead = row.get("dead_money")
        parts.append(f"dead money {money_text(dead)}" if dead is not None else "dead money not known")
        if row.get("later_team"):
            parts.append(f"since signed by the {row['later_team']} ({week_label({'week': row.get('later_week')})})")
        else:
            parts.append(f"free agent since {week_label(row)}")
    elif t in ("RE_SIGNED", "EXTENDED"):
        verb = "re-signed" if t == "RE_SIGNED" else "extended"
        terms = f"{years} year{'s' if years != 1 else ''}, {money_text(value)} total" if years else money_text(value)
        line = f"{team} {verb} {pos} {name}: {terms}"
        if row.get("new_deal_start"):
            line += f", new deal starts {row['new_deal_start']}"
        parts.append(line)
    elif t == "RESTRUCTURED":
        parts.append(f"{team} restructured {pos} {name}'s contract: freed {money_text(value)} in cap space this season")
    elif t == "TRADED" and not row.get("player_id"):
        parts.append(f"{row.get('from_team') or 'A team'} traded a draft pick to the {team} (executed trade)")
    elif t == "TRADED":
        parts.append(f"{row.get('from_team') or 'A team'} traded {pos} {name} to the {team} (executed trade)")
    else:
        parts.append(row.get("raw_text") or f"{team} {pos} {name}")
    if with_notable:
        parts.append(f"notable: {'yes' if row.get('notable') else 'no'}")
    return " · ".join(parts)


def counts_by_type(rows) -> dict:
    out = {}
    for r in rows:
        out[r.get("type")] = out.get(r.get("type"), 0) + 1
    return dict(sorted(out.items()))


def cap_line(team_name, row, basis) -> str:
    """One team's cap snapshot: 'Colts: -$29.5M in cap space, $37.8M dead money, $398M total spend (scanned Week 4)'."""
    return (f"{team_name}: {money_text(row.get('cap_space'))} in cap space, {money_text(row.get('cap_penalty'))} dead "
            f"money, {money_text(row.get('total_spend'))} total spend ({basis})")


CAP_WATCH_SPACE = 2_000_000


def brief_block(rows, through_week, cap_rows=None, limit: int = 12) -> dict:
    """The national brief's `league_transactions` block (§2.7) from the moves new this week (annotated or not) and
    [{team, cap_space, cap_penalty, total_spend, basis}] cap rows. Empty dict when there are no new moves. Trade legs
    are left out: trades are whole-trade lines in `league_trades` (trades_block)."""
    rows = [r for r in rows if r.get("type") != "TRADED"]
    if not rows:
        return {}
    ranked = annotate([dict(r) for r in rows])
    watch = [cap_line(c["team"], c, c["basis"]) for c in sorted(cap_rows or [], key=lambda c: c.get("cap_space") or 0)
             if c.get("cap_space") is not None and c["cap_space"] < CAP_WATCH_SPACE]
    block = {
        "through_week": through_week,
        "notable_moves": [move_line(r) for r in ranked[:limit]],
        "counts": counts_by_type(ranked),
        "note": ("League moves new since last week's edition, most notable first. They feed the existing slots: use one "
                 "only when it bears on your subject; most articles carry none. Terms and cap figures only from these "
                 "lines or get_league_transactions / get_player_contract / get_team_cap_sheet. Invent the negotiation "
                 "colour, never a term."),
    }
    if watch:
        block["cap_watch"] = watch
    return block


def team_block(rows, cap_snapshot: str = "") -> dict:
    """The beat brief's `team_transactions` block (§2.7): this team's moves new this week and its cap line (trade
    legs left out: the beat brief's trades come from trades_block)."""
    ranked = annotate([dict(r) for r in rows if r.get("type") != "TRADED"])
    block = {
        "moves_this_week": [move_line(r) for r in ranked],
        "reporting_guidance": ("A notable move gets a paragraph: who it is, the role he leaves or fills, what it means "
                               "for the depth chart. Terms only from these lines or get_player_contract; invent the "
                               "negotiation colour, never a figure. Mention the cap only if it bears on the story."),
    }
    if cap_snapshot:
        block["cap_snapshot"] = cap_snapshot
    return block


# ----------------------------------------------------------------------------------------------------------------
# Trades and rumors (Phase 8; step 3's engines/front_office.py read helpers feed these)
# ----------------------------------------------------------------------------------------------------------------

TRADE_DEADLINE_WEEK = 8      # engines/gm_trade_engine.TRADE_DEADLINE_WEEK (the last trades before Week 9's games)
# Rumors run only through the deadline (JR, §6 Q7): the Week 8 edition is the last to carry them.
RUMOR_LAST_EDITION_WEEK = TRADE_DEADLINE_WEEK
REGULAR_SEASON_LAST_WEEK = 18

RUMOR_TRIGGERS = {"INJURY": "after an injury at the position", "SELL": "a seller shopping a veteran",
                  "NEED": "to fill a need", "DRAFT": "around the draft"}

TRADE_NOTE = ("An executed trade is fact: who went where, the picks, and the cap hits as given. Invent the colour "
              "(how talks went, who called whom, locker-room reaction), never a player, a pick, a figure or a team.")
RUMOR_NOTE = ("A rumor is never a done deal. These are real conversations between two front offices (teams and the "
              "kinds of assets only): report them only as talks ('discussed', 'sources say', 'kicked the tires'), never "
              "as a trade that happened or was agreed. Don't name a player or give a figure the rumor doesn't give. "
              "'Talks stalled' means no deal came of it. A rumor never outranks the facts: if get_league_transactions "
              "shows an executed trade, that is the story.")


def trade_week(week):
    """A trade's league week for the cutoff: its regular-season week, else None (an offseason trade)."""
    return week if week and 1 <= week <= REGULAR_SEASON_LAST_WEEK else None


def trade_visible(season, week, ctx_season, ctx_week, offseason_event_ok=True) -> bool:
    """May a Week `ctx_week` article see an executed trade? Its transactions rows have no capture, so a Week N trade
    (confirmed before Week N's games) is visible from the Week N edition on (txn_visible); offseason trades follow
    the offseason signings rule."""
    return txn_visible(season, trade_week(week), None, ctx_season, ctx_week, offseason_event_ok)


def new_trades(trades, ctx_week) -> list:
    """The executed trades an edition reports as new: those of the previous week. The weekly run writes Week N's
    articles right after the GM step proposes Week N's trades, and JR makes them in Madden after that, so the Week
    N+1 edition is the first to see them (a regenerated Week N edition still sees them through the tools)."""
    if ctx_week is None:
        return []
    return [t for t in trades if trade_week(t.get("week")) is not None and t["week"] == ctx_week - 1]


def trade_grades_due(trades, week) -> bool:
    """A TRADE_GRADES feature takes a Feature Trio slot in a regular-season week right after an executed trade."""
    return 2 <= (week or 0) <= REGULAR_SEASON_LAST_WEEK and bool(new_trades(trades, week))


def rumor_visible(proposal_week, ctx_week) -> bool:
    """May a Week `ctx_week` edition carry a rumor from a proposal made for `proposal_week`? Only proposals from
    earlier weeks (the status timestamps are wall-clock, so the proposal's week is the cutoff), and only through
    the trade deadline. No article context: visible."""
    if ctx_week is None:
        return True
    return proposal_week is not None and proposal_week < ctx_week <= RUMOR_LAST_EDITION_WEEK


def rumor_outcome(status, expires_week, ctx_week) -> str:
    """'stalled' when the proposal was rejected or expired before this edition's week, else 'open' (the outcome
    isn't for the writer to state). An EXECUTED proposal is never a rumor."""
    if status == "EXECUTED":
        raise ValueError("an executed trade is not a rumor")
    if status in ("REJECTED", "EXPIRED") and (ctx_week is None or (expires_week is not None and expires_week < ctx_week)):
        return "stalled"
    return "open"


def _pick_text(leg, team_names) -> str:
    pick = leg.get("pick") or {}
    orig = team_names.get(pick.get("original_team"), pick.get("original_team"))
    rd = pick.get("round")
    ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(rd, f"{rd}th")
    own = leg.get("from") == pick.get("original_team")
    return f"the {orig}' {pick.get('draft_season')} {ordinal}-round pick" if own else \
        f"the {orig}' {pick.get('draft_season')} {ordinal}-round pick (held by the {team_names.get(leg.get('from'), leg.get('from'))})"


def _player_text(leg) -> str:
    name = display_name({"full_name": leg.get("player_name")})
    hit = leg.get("cap_hit_now")
    cap = f" ({money_text(hit)} cap hit this season)" if hit else ""
    return f"{leg.get('position') or ''} {name}{cap}".strip()


def _join(items) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def trade_line(trade, team_names=None) -> str:
    """One executed trade as a line (fact), e.g. 'Week 4 · TRADE (executed) · Jets acquired WR Garrett Wilson
    ($12.5M cap hit this season) from the Patriots; Patriots acquired CB Sauce Gardner ($9M cap hit this season) and
    the Jets' 2037 2nd-round pick from the Jets · cap this season: Jets +$3.5M, Patriots -$3.5M'. trade: an
    executed_trades row ({week, team_a, team_b, legs}); team_names: {abbreviation: name}. OVR is never printed."""
    names = team_names or {}
    nm = lambda abbr: names.get(abbr, abbr)
    week = trade_week(trade.get("week"))
    parts = [f"Week {week}" if week else "Offseason", "TRADE (executed)"]
    sides, cap = [], {}
    for receiver in (trade["team_a"], trade["team_b"]):
        legs = [l for l in trade.get("legs") or [] if l.get("to") == receiver]
        if not legs:
            continue
        sender = next(l["from"] for l in legs)
        items = [_player_text(l) if l.get("type") == "PLAYER" else _pick_text(l, names) for l in legs]
        sides.append(f"{nm(receiver)} acquired {_join(items)} from the {nm(sender)}")
    for l in trade.get("legs") or []:
        if l.get("type") == "PLAYER" and l.get("cap_hit_now") is not None:
            cap[l["to"]] = cap.get(l["to"], 0) + l["cap_hit_now"]
            cap[l["from"]] = cap.get(l["from"], 0) - l["cap_hit_now"]
    parts.append("; ".join(sides))
    if cap:
        parts.append("cap this season: " + ", ".join(
            f"{nm(t)} {'+' if v > 0 else ''}{money_text(v)}" for t, v in sorted(cap.items(), key=lambda kv: -kv[1])))
    return " · ".join(parts)


def rumor_line(rumor, team_names=None) -> str:
    """One rumor as a line, always labelled: 'Week 5 · RUMOR, not a done deal · the Jets and the Patriots discussed a
    trade (the Jets made the call, to fill a need): a WR to the Jets; a draft pick to the Patriots · talks stalled,
    no deal'. rumor: a trade_rumors row ({week, teams, initiator, trigger, assets, outcome}). No player names, no
    figures."""
    names = team_names or {}
    nm = lambda abbr: names.get(abbr, abbr)
    a, b = rumor["teams"]
    why = RUMOR_TRIGGERS.get(rumor.get("trigger"))
    head = f"the {nm(a)} and the {nm(b)} discussed a trade (the {nm(rumor.get('initiator') or a)} made the call" \
           + (f", {why})" if why else ")")
    flows = []
    for receiver in (a, b):
        kinds = []
        for x in rumor.get("assets") or []:
            if x.get("to") != receiver:
                continue
            kinds.append(f"a {x['position']}" if x.get("type") == "PLAYER" and x.get("position") else
                         "a draft pick" if x.get("type") == "PICK" else "a player")
        if kinds:
            flows.append(f"{_join(kinds)} to the {nm(receiver)}")
    line = head + (": " + "; ".join(flows) if flows else "")
    outcome = ("talks stalled, no deal" if rumor.get("outcome") == "stalled"
               else "outcome not known: report it only as talks")
    return " · ".join([f"Week {rumor['week']}", "RUMOR, not a done deal", line, outcome])


def trades_block(trades, rumors, team_names=None, through_week=None) -> dict:
    """The briefs' `league_trades` block: executed trades new this edition (fact) and rumors new this edition.
    Empty dict when there is neither."""
    if not trades and not rumors:
        return {}
    block = {"through_week": through_week}
    if trades:
        block["executed_trades"] = [trade_line(t, team_names) for t in trades]
        block["trade_note"] = TRADE_NOTE
    if rumors:
        block["rumors"] = [rumor_line(r, team_names) for r in rumors]
        block["rumor_note"] = RUMOR_NOTE
    return block


def trade_grades_target(trades, team_names=None, team_ids=None, gms=None) -> dict:
    """The TRADE_GRADES slot's Editorial Desk target for an edition's new executed trades (new_trades): subject team =
    the first trade's initiator, and a note listing every trade as a trade_line with each GM's stated thinking
    (gm_trade_proposals.rationale_a/b, written to the deal's fact sheet). gms: {abbreviation: {gm_name, direction}}.
    None without trades."""
    if not trades:
        return None
    names, ids, gms = team_names or {}, team_ids or {}, gms or {}
    nm = lambda abbr: names.get(abbr, abbr)
    week = trades[0]["week"]
    parts = [f"TRADE GRADES: grade every trade executed in Week {week} below, for both teams (a letter grade per "
             "team), and no other. Each trade is fact exactly as written."]
    for n, t in enumerate(trades, 1):
        parts.append(f"Trade {n}: {trade_line(t, names)}.")
        for abbr, why in ((t["team_a"], t.get("rationale_a")), (t["team_b"], t.get("rationale_b"))):
            gm = gms.get(abbr) or {}
            if why:
                who = f"{nm(abbr)} GM {gm['gm_name']}" if gm.get("gm_name") else f"The {nm(abbr)} front office"
                parts.append(f"{who} on the deal: \"{why}\"")
    pairs = sorted({nm(x) for t in trades for x in (t["team_a"], t["team_b"])})
    first = trades[0]["team_a"]
    return {"team_id": ids.get(first), "team_name": " / ".join(pairs) if len(pairs) <= 4 else nm(first),
            "kind": "TRADE_GRADES", "week": week, "trades": [t["proposal_id"] for t in trades], "note": " ".join(parts)}
