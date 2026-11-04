"""
Relationship event producers (docs/done/RELATIONSHIP_EVENT_PRODUCERS_PLAN.md).

Turns what happens in the simulated league (who is on which team, who starts, who is cut, re-signed, hurt,
honoured, fired or hired) into the relationships plan's canonical events (PERSONALITY_RELATIONSHIPS_PLAN.md
§2.1-§2.2), every in-season week and every offseason stop. Every outcome comes from fixed, versioned rules
(PRODUCER_RULES_VERSION) with no model calls, so every event replays exactly.

  calendar.py   ticks: 32 per season (weeks 1-23 games, 24-32 offseason stops), time points       pure
  rules.py      role tiers, eligibility, established roles, and the envelope builders of plan §2.2 pure
  sources.py, store.py, run.py, __main__.py: the database half (plan §2.3-§2.6, units U2 and U3)

calendar.py and rules.py never import a database driver; the rules take plain dicts.
"""

PRODUCER_RULES_VERSION = "producers-v1"
