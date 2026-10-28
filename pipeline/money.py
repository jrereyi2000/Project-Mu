"""
Money as Madden shows it on TEAM SALARY and LEAGUE TRANSACTIONS (docs/done/TEAM_MANAGEMENT_PLAN.md §2.1).
Pure, no database access. Every team-management step parses money through parse_money().

    parse_money("$16.8M")  -> 16800000
    parse_money("$200K")   -> 200000
    parse_money("-$29.5M") -> -29500000      also "$-29.5M" and "($29.5M)"
    parse_money("")        -> None           also "-", "--", "N/A"
    parse_money("S16.8M")  -> ValueError     OCR junk never becomes a number

Money is stored as whole dollars (bigint). Decimal keeps "$0.7M" at exactly 700000.
"""
import re
from decimal import Decimal, InvalidOperation

SUFFIX = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
BLANK = {"", "-", "--", "N/A", "NA"}
MONEY_RE = re.compile(r"^(-?)\$(-?)(\d+(?:\.\d+)?)([KMB]?)$")


def parse_money(s: str | None) -> int | None:
    """Whole dollars from a money cell, None for a blank cell. Raises ValueError on anything else."""
    if s is None:
        return None
    t = str(s).strip().upper()
    for ch in "−–—":                     # Unicode minus, en and em dash
        t = t.replace(ch, "-")
    t = t.replace(" ", "").replace(",", "")
    if t in BLANK:
        return None
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    m = MONEY_RE.match(t)
    if not m:
        raise ValueError(f"not money: {s!r}")
    lead, inner, digits, suffix = m.groups()
    if lead and inner:
        raise ValueError(f"not money: {s!r}")
    neg = neg != bool(lead or inner)
    try:
        value = Decimal(digits) * SUFFIX[suffix]
    except InvalidOperation:
        raise ValueError(f"not money: {s!r}") from None
    if value != value.to_integral_value():
        raise ValueError(f"not money (fraction of a dollar): {s!r}")
    return -int(value) if neg else int(value)
