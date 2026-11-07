"""Historical team codes -> Project Mu franchises (docs/done/REAL_NFL_HISTORY_PLAN.md §2.1, §2.3). Pure.

mu_abbr(code, season) maps nflverse codes (OAK, SD, STL, LA, ARI, WSH) and Pro-Football-Reference draft codes (GNB,
KAN, LVR, NOR, NWE, SDG, SFO, TAM, ...) to the current franchise's teams.abbreviation. Houston before 2002 and any
unknown code raise UnknownTeam. division_of(code, season) is the (conference, division) that season: the 1999-2001
alignment (six-team Central divisions, 31 teams) or the current one from 2002.
"""

MU_TEAMS = {   # teams.abbreviation -> (conference, division) since 2002 (db/seed_clean_db.py TEAMS_DATA)
    "BUF": ("AFC", "AFC East"), "MIA": ("AFC", "AFC East"), "NE": ("AFC", "AFC East"), "NYJ": ("AFC", "AFC East"),
    "BAL": ("AFC", "AFC North"), "CIN": ("AFC", "AFC North"), "CLE": ("AFC", "AFC North"),
    "PIT": ("AFC", "AFC North"),
    "HOU": ("AFC", "AFC South"), "IND": ("AFC", "AFC South"), "JAX": ("AFC", "AFC South"),
    "TEN": ("AFC", "AFC South"),
    "DEN": ("AFC", "AFC West"), "KC": ("AFC", "AFC West"), "LV": ("AFC", "AFC West"), "LAC": ("AFC", "AFC West"),
    "DAL": ("NFC", "NFC East"), "NYG": ("NFC", "NFC East"), "PHI": ("NFC", "NFC East"), "WAS": ("NFC", "NFC East"),
    "CHI": ("NFC", "NFC North"), "DET": ("NFC", "NFC North"), "GB": ("NFC", "NFC North"),
    "MIN": ("NFC", "NFC North"),
    "ATL": ("NFC", "NFC South"), "CAR": ("NFC", "NFC South"), "NO": ("NFC", "NFC South"), "TB": ("NFC", "NFC South"),
    "AZ": ("NFC", "NFC West"), "LAR": ("NFC", "NFC West"), "SF": ("NFC", "NFC West"), "SEA": ("NFC", "NFC West"),
}
NFL_TO_MU = {
    "ARI": "AZ", "WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LAR", "LA": "LAR",
    # Pro-Football-Reference codes (nflverse draft_picks.csv)
    "GNB": "GB", "KAN": "KC", "LVR": "LV", "NOR": "NO", "NWE": "NE", "SDG": "LAC", "SFO": "SF", "TAM": "TB",
    "RAM": "LAR", "RAI": "LV", "HTX": "HOU", "CLT": "IND", "CRD": "AZ", "OTI": "TEN", "RAV": "BAL",
    "JAC": "JAX",
}
HOUSTON_FIRST_SEASON = 2002
DIVISIONS_1999_2001 = {   # Mu abbreviation -> (conference, division), 1999-2001
    **{t: ("AFC", "AFC East") for t in ("BUF", "IND", "MIA", "NE", "NYJ")},
    **{t: ("AFC", "AFC Central") for t in ("BAL", "CIN", "CLE", "JAX", "PIT", "TEN")},
    **{t: ("AFC", "AFC West") for t in ("DEN", "KC", "LV", "LAC", "SEA")},
    **{t: ("NFC", "NFC East") for t in ("AZ", "DAL", "NYG", "PHI", "WAS")},
    **{t: ("NFC", "NFC Central") for t in ("CHI", "DET", "GB", "MIN", "TB")},
    **{t: ("NFC", "NFC West") for t in ("ATL", "CAR", "NO", "LAR", "SF")},
}


class UnknownTeam(ValueError):
    pass


def mu_abbr(code, season=None):
    """The current franchise's teams.abbreviation for a historical code in `season`."""
    c = (code or "").strip().upper()
    mu = NFL_TO_MU.get(c, c)
    if mu not in MU_TEAMS:
        raise UnknownTeam(f"unknown team code {code!r}")
    if mu == "HOU" and season is not None and int(season) < HOUSTON_FIRST_SEASON:
        raise UnknownTeam(f"Houston has no team in {season}")
    return mu


def division_of(code, season):
    mu = mu_abbr(code, season)
    if int(season) < HOUSTON_FIRST_SEASON:
        return DIVISIONS_1999_2001[mu]
    return MU_TEAMS[mu]


def teams_in(season):
    """Mu abbreviations of the franchises that played in `season` (31 before 2002, then 32)."""
    if int(season) < HOUSTON_FIRST_SEASON:
        return sorted(DIVISIONS_1999_2001)
    return sorted(MU_TEAMS)
