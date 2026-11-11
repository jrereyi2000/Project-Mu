"""OTC contracts for mirrored signings (NFL_WEEKLY_MIRROR_PLAN.md §2.5). Pure: a parquet file in, contract dicts out.

nflverse publishes OverTheCap's whole contract history as `historical_contracts.parquet` (one row per contract:
`gsis_id`, `team` (OTC's full name), `year_signed`, `years`, `apy`, `value`, `guaranteed`, `draft_year`, ...).
It has no full signing date in the current release, so a row's date is its `year_signed`; a file that carries a
`date_signed` / `contract_signed_date` / `signed_date` / `date` column is read too.

`ContractBook.from_otc(path)` keeps every deal by gsis id. `contract_for(gsis_id, signed_date, cap, team=...)`
gives a contract dict in the shape `pipeline.league_office.offer_contract` returns:

- OTC's own contract when one was signed in the mirror window (on or after 2026-08-01 and before the 2027 league
  year, so the whole 2026 season including the postseason is covered; a file without dates uses `year_signed`), is
  for the target team, and was signed no later than the mirrored signing (the `signed_date` as-of boundary): real
  APY and years, no assumed bonus;
- otherwise `contracts_engine.minimum_salary(years_exp, cap)` for one year.

`team` is the target's canonical Mu franchise code ("BUF", "WAS"); OTC's club names ("Bills", "Commanders") are
normalized to it. A row whose club cannot be mapped, or a target team that cannot be mapped, never matches another
franchise. `signed_date` is the as-of boundary, not necessarily the signing's calendar date; a dated row is never
used before its own date, while a year-only row is the plan's accepted 2026-window approximation.

`years_exp` comes from the caller when it knows it (the mirror passes the real player's entry year); without one it
is derived from the newest OTC row's `draft_year` (SIGNING_SEASON - draft_year), else 0.
"""
import dataclasses

from engines import contracts_engine as ce
from engines.nfl_history.teams import MU_TEAMS

SIGNING_SEASON = 2026
OTC_CUTOFF = "2026-08-01"                  # a midseason 2026 signing, not the March free-agency window
# The 2026 league season runs through the Super Bowl in February 2027, so the mirror window must cross the calendar
# year: a January or February deal is still a 2026-season signing. March 2027 opens the 2027 league year and is out.
MIRROR_WINDOW_END = "2027-03-01"
MILLIONS = 1_000_000
DATE_COLUMNS = ("date_signed", "contract_signed_date", "signed_date", "date")
CONTRACT_TYPES = {"otc": "STANDARD", "minimum": "MINIMUM"}

# OTC's `team` cell is the club nickname ("Bengals", "Bills", "Chiefs"); a contract split between clubs is a
# combined value ("NYJ/GB", "SEA/LV") that maps to no single franchise. Mu franchise codes come from
# db/seed_clean_db.py TEAMS_DATA. There is no shared nickname -> code table importable from engines/ (the only
# full-name map lives in the tools/ coach-library script), so this one is local to the mirror.
OTC_TEAM_CODES = {
    "Cardinals": "AZ", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF",
    "Panthers": "CAR", "Bears": "CHI", "Bengals": "CIN", "Browns": "CLE",
    "Cowboys": "DAL", "Broncos": "DEN", "Lions": "DET", "Packers": "GB",
    "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX", "Chiefs": "KC",
    "Raiders": "LV", "Chargers": "LAC", "Rams": "LAR", "Dolphins": "MIA",
    "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG",
    "Jets": "NYJ", "Eagles": "PHI", "Steelers": "PIT", "49ers": "SF",
    "Seahawks": "SEA", "Buccaneers": "TB", "Titans": "TEN", "Commanders": "WAS",
}
_OTC_TEAM_CODES_FOLDED = {name.casefold(): code for name, code in OTC_TEAM_CODES.items()}


def normalize_team(team):
    """The Mu franchise code for an OTC club cell or an already-canonical code, or None when unmappable.

    Only an exact OTC nickname (case-insensitive) or a real Mu code is accepted, so an unknown alias and a
    split club ("SEA/LV") map to None rather than quietly matching some other franchise.
    """
    if team is None:
        return None
    text = str(team).strip()
    if not text:
        return None
    code = _OTC_TEAM_CODES_FOLDED.get(text.casefold())
    if code:
        return code
    upper = text.upper()
    return upper if upper in MU_TEAMS else None


def _num(value):
    """A float from a parquet cell, or None (None, NaN and blank all mean 'no value')."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _int(value):
    out = _num(value)
    return None if out is None else int(round(out))


def _date(value):
    """'YYYY-MM-DD' from a string / date / timestamp cell, or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else None


@dataclasses.dataclass(frozen=True)
class OtcDeal:
    """One row of OTC's contract history, in OTC's units: money in millions of dollars."""
    gsis_id: str
    team: str | None
    year_signed: int | None
    signed: str | None           # the full date when the file has one
    years: int
    apy: float | None
    total: float | None
    guaranteed: float | None
    draft_year: int | None
    active: bool

    def qualifies(self, signed_date=None, team=None):
        """True when this is a qualifying deal for `team` that was known by the `signed_date` as-of boundary.

        A dated row is never used before its own date and never outside the 2026 mirror window (2026-08-01 through
        the postseason, before the 2027 league year); a year-only row is a 2026-window approximation, so the whole
        window counts as known. `team` is a canonical Mu code, or None to skip the team check.
        """
        if team is not None:
            target = normalize_team(team)
            if target is None or normalize_team(self.team) != target:
                return False
        as_of = str(signed_date)[:10] if signed_date else None
        if self.signed:
            if as_of is not None and self.signed > as_of:
                return False
            return OTC_CUTOFF <= self.signed < MIRROR_WINDOW_END
        if self.year_signed != SIGNING_SEASON:
            return False
        return as_of is None or as_of >= OTC_CUTOFF


class ContractBook:
    """OTC deals by gsis id, newest first; empty for a season with no file."""

    def __init__(self, deals=None):
        self._deals = {}
        for gsis, rows in (deals or {}).items():
            rows = list(rows)
            self._deals[str(gsis)] = sorted(rows, key=_newest_first, reverse=True)

    @classmethod
    def from_otc(cls, parquet_path):
        """Read nflverse `historical_contracts.parquet`; a missing or unreadable file gives an empty book."""
        import pyarrow.parquet as pq

        try:
            names = set(pq.ParquetFile(str(parquet_path)).schema.names)
        except (OSError, ValueError):
            return cls()
        if "gsis_id" not in names:
            return cls()
        want = [c for c in ("gsis_id", "team", "year_signed", "years", "apy", "value", "guaranteed", "draft_year",
                            "is_active", *DATE_COLUMNS) if c in names]
        table = pq.read_table(str(parquet_path), columns=want)
        cols = {name: table.column(name).to_pylist() for name in want}
        date_col = next((c for c in DATE_COLUMNS if c in cols), None)
        deals = {}
        for i, raw_gsis in enumerate(cols["gsis_id"]):
            gsis = str(raw_gsis).strip() if raw_gsis is not None else ""
            if not gsis:
                continue
            deals.setdefault(gsis, []).append(OtcDeal(
                gsis_id=gsis,
                team=(cols["team"][i] if "team" in cols and cols["team"][i] else None),
                year_signed=_int(cols["year_signed"][i]) if "year_signed" in cols else None,
                signed=_date(cols[date_col][i]) if date_col else None,
                years=max(1, _int(cols["years"][i]) or 1) if "years" in cols else 1,
                apy=_num(cols["apy"][i]) if "apy" in cols else None,
                total=_num(cols["value"][i]) if "value" in cols else None,
                guaranteed=_num(cols["guaranteed"][i]) if "guaranteed" in cols else None,
                draft_year=_int(cols["draft_year"][i]) if "draft_year" in cols else None,
                active=bool(cols["is_active"][i]) if "is_active" in cols else False))
        return cls(deals)

    def contract_for(self, gsis_id, signed_date, cap, years_exp=None, team=None):
        """The mirrored signing's contract dict, or None for a blank gsis id.

        OTC terms when a qualifying deal for `team` exists by `signed_date`, else the one-year league minimum.
        """
        gsis = str(gsis_id or "").strip()
        if not gsis:
            return None
        rows = self._deals.get(gsis) or ()
        deal = next((d for d in rows if d.qualifies(signed_date, team)), None)
        if deal is None:
            if years_exp is None:
                years_exp = _implied_years_exp(rows)
            minimum = ce.minimum_salary(0 if years_exp is None else years_exp, cap)
            return _offer_shape(1, minimum, 0, 0, _signed_season(signed_date, None),
                                CONTRACT_TYPES["minimum"])
        years = max(1, int(deal.years or 1))
        apy = deal.apy if deal.apy is not None else (
            (deal.total / years) if deal.total is not None else 0.0)
        total = max(0, int(round(apy * years * MILLIONS)))
        guaranteed = max(0, int(round((deal.guaranteed or 0.0) * MILLIONS)))
        return _offer_shape(years, total, 0, min(total, guaranteed),
                            _signed_season(signed_date, deal.year_signed), CONTRACT_TYPES["otc"])


def _newest_first(deal):
    """Sort key: dated rows first, newest first, active deals before the others."""
    return (1 if deal.signed else 0, deal.signed or "", deal.year_signed or 0, 1 if deal.active else 0)


def _implied_years_exp(rows):
    """Accrued seasons implied by the newest OTC row's draft year (0 for undrafted or unknown)."""
    for deal in rows:
        if deal.draft_year is not None:
            return max(0, SIGNING_SEASON - deal.draft_year)
    return 0


def _signed_season(signed_date, year_signed):
    """The league season the deal was signed in: the date's year, else OTC's, else the mirror season."""
    text = str(signed_date or "")[:4]
    if text.isdigit():
        return int(text)
    return int(year_signed) if year_signed else SIGNING_SEASON


def _offer_shape(years, total, bonus, guaranteed, signed_season, contract_type):
    """`pipeline.league_office.offer_contract`'s dict for these terms, in whole dollars."""
    years = max(1, int(years))
    total, bonus = int(total), min(int(total), max(0, int(bonus)))
    pro = ce._prorate(bonus, min(years, ce.CBA["PRORATION_MAX_YEARS"]))
    return {"length": years, "current_year": 0,
            "salary_by_year": ce._prorate(total - bonus, years),
            "bonus_by_year": pro + [0] * (years - len(pro)),
            "guaranteed": max(0, int(guaranteed)), "contract_type": contract_type,
            "signed_season": int(signed_season), "last_restructure_season": None, "drafted_round": None}
