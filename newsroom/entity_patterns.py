"""Regex patterns the article entity tagger uses to find team mentions. Pure functions; no database needed.

Team names are matched case-sensitively: writers capitalize them, and lowercase matching turned ordinary
words into team mentions ("was" -> WAS Commanders, "no" -> NO Saints, "ten" -> TEN Titans, "pays the bills").
"""
import re

# Abbreviations that are also English words. Even case-sensitive they show up in all-caps writing
# ("THERE WAS NO WAY"), so those teams are matched by nickname and city only.
WORD_ABBREVIATIONS = frozenset({"NO", "WAS", "TEN", "MIN", "CAR", "SEA", "DEN"})

# Cities shared by two teams: a bare city mention can't be attributed to either one.
SHARED_CITIES = frozenset({"New York", "Los Angeles"})


def team_mention_pattern(name: str, city: str, abbreviation: str):
    """Compiled pattern matching one team's full name, nickname, city or abbreviation (all case-sensitive)."""
    parts = []
    full_name = f"{city} {name}".strip()
    parts.append(re.escape(full_name))           # "Buffalo Bills" (listed first so it wins over its pieces)
    parts.append(re.escape(name))                # "Bills", "49ers"
    abbr = (abbreviation or "").strip()
    if len(abbr) >= 2 and abbr.upper() not in WORD_ABBREVIATIONS:
        parts.append(re.escape(abbr.upper()))    # "KC", "BUF" (uppercase only)
    if city and city not in SHARED_CITIES:
        parts.append(re.escape(city))            # "Buffalo"
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")
