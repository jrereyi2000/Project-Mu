"""OTC contracts for mirrored signings (NFL_WEEKLY_MIRROR_PLAN.md §2.5). Pure: a parquet file in, contract dicts out.

nflverse publishes OverTheCap's whole contract history as `historical_contracts.parquet` (one row per contract:
`gsis_id`, `team` (OTC's full name), `year_signed`, `years`, `apy`, `value`, `guaranteed`, `draft_year`, ...).
It has no full signing date in the current release, so a row's date is its `year_signed`; a file that carries a
`date_signed` / `contract_signed_date` / `signed_date` / `date` column is read too.

`ContractBook.from_otc(path)` keeps every deal by gsis id. `contract_for(gsis_id, signed_date, cap)` gives a
contract dict in the shape `pipeline.league_office.offer_contract` returns:

- OTC's own contract when one was signed in the mirror window (on or after 2026-08-01, the 2026 league year for a
  file without dates) and the signing being mirrored is not before it: real APY and years, no assumed bonus;
- otherwise `contracts_engine.minimum_salary(years_exp, cap)` for one year.

`years_exp` comes from the caller when it knows it (the mirror passes the real player's entry year); without one it
is derived from the newest OTC row's `draft_year` (SIGNING_SEASON - draft_year), else 0.
"""
import dataclasses

from engines import contracts_engine as ce

SIGNING_SEASON = 2026
OTC_CUTOFF = "2026-08-01"                  # a midseason 2026 signing, not the March free-agency window
MILLIONS = 1_000_000
DATE_COLUMNS = ("date_signed", "contract_signed_date", "signed_date", "date")
CONTRACT_TYPES = {"otc": "STANDARD", "minimum": "MINIMUM"}


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

    def qualifies(self, signed_date=None):
        """True when this is a deal of the 2026 mirror window (and the mirrored signing is not before it)."""
        if signed_date and str(signed_date)[:10] < OTC_CUTOFF:
            return False
        if self.signed:
            return OTC_CUTOFF <= self.signed < "2027-01-01"
        return self.year_signed == SIGNING_SEASON


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

    def contract_for(self, gsis_id, signed_date, cap, years_exp=None):
        """The mirrored signing's contract dict, or None for a blank gsis id.

        OTC terms when a deal of the window exists, else the one-year league minimum.
        """
        gsis = str(gsis_id or "").strip()
        if not gsis:
            return None
        rows = self._deals.get(gsis) or ()
        deal = next((d for d in rows if d.qualifies(signed_date)), None)
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
