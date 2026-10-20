"""Roster moves, contracts and cap facts for the newsroom (docs/team_management/05_NEWSROOM_INTEGRATION_PLAN.md).

Pure helpers, no database: the writer tools in newsroom/db_tools.py, the briefs in swarm_dispatcher.py, the Agent
Wire (social_desk.py) and the fantasy tools fetch rows and pass them through here, so the tests need no DB.

- Every money figure is printed with its basis (§2.1): scanned from the team's books, from a CPU signing row, an
  estimated cap row (moves applied since the last scan), or a projected year of a new deal, which always carries
  `est.` (JR, 2026-10-20: projected yearly hits are shown, labelled).
- `txn_visible` is the week cutoff for `transactions` rows (§2.2); `on_roster` is the has-a-team rule for a Week N
  article (`released_week` IS NULL or later than N).
- `notability` decides which moves the briefs and the Agent Wire carry (§2.3). OVR only ranks, it's never printed.
"""

# Depth slots that count as starters (also the beat injury block's rule).
_STARTER_ONE = ("QB", "TE", "LT", "LG", "C", "RG", "RT", "FS", "SS", "K", "P", "FB")
_STARTER_TWO = ("HB", "DT", "LEDG", "REDG", "LE", "RE")
_STARTER_THREE = ("WR", "CB", "MIKE", "SAM", "WILL", "MLB", "LOLB", "ROLB")

# Notability thresholds (§2.3; JR accepted them as the starting point, 2026-10-20)
NOTABLE_POPULARITY = 50
NOTABLE_RE_SIGN_TOTAL = 20_000_000
NOTABLE_SIGNING_TOTAL = 5_000_000
NOTABLE_RESTRUCTURE_FREED = 5_000_000
NOTABLE_DEAD_MONEY = 5_000_000
NOTABLE_OVR = 88

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
    """(notable, reasons, rank_score) for a move row. Row keys used: type, contract_value, is_starter, popularity,
    dead_money (stored cut_penalty of a released contract), ovr. OVR only ranks; it's never a printed reason."""
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
    elite = (row.get("ovr") or 0) >= NOTABLE_OVR
    notable = bool(reasons) or elite
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
    elif t == "TRADED":
        parts.append(f"{row.get('from_team') or 'A team'} traded {pos} {name} to the {team}")
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
    [{team, cap_space, cap_penalty, total_spend, basis}] cap rows. Empty dict when there are no new moves."""
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
    """The beat brief's `team_transactions` block (§2.7): this team's moves new this week and its cap line."""
    ranked = annotate([dict(r) for r in rows])
    block = {
        "moves_this_week": [move_line(r) for r in ranked],
        "reporting_guidance": ("A notable move gets a paragraph: who it is, the role he leaves or fills, what it means "
                               "for the depth chart. Terms only from these lines or get_player_contract; invent the "
                               "negotiation colour, never a figure. Mention the cap only if it bears on the story."),
    }
    if cap_snapshot:
        block["cap_snapshot"] = cap_snapshot
    return block
