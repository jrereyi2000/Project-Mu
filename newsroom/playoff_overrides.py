"""Playoff-specific article overrides (newsroom/config/playoff_overrides.json): lookup and validation.

Each override replaces an article type's directive, curatorial and structure text for its target weeks and
sets the article's temporal mode. Pure functions; no database needed.
"""

TEMPORAL_MODES = ("RETROSPECTIVE", "PROSPECTIVE", "CUMULATIVE")
POSTSEASON_WEEKS = range(19, 24)  # 19 Wild Card ... 23 Super Bowl


def normalize_temporal_mode(value):
    """Canonical temporal mode for a config value (case-insensitive), or None if missing or unknown."""
    if not value:
        return None
    mode = str(value).strip().upper()
    return mode if mode in TEMPORAL_MODES else None


def find_active_override(overrides: dict, article_type_id: str, week: int):
    """The override for this article type whose target_weeks include `week`, or None."""
    for ov in overrides.get(article_type_id, []):
        if week in ov.get("target_weeks", []):
            return ov
    return None


def validate_playoff_overrides(overrides: dict, article_type_ids) -> list:
    """Returns a list of human-readable problems; empty when the config is valid."""
    problems = []
    known_types = set(article_type_ids)
    for at_id, entries in overrides.items():
        if at_id not in known_types:
            problems.append(f"{at_id}: not a known article type")
        claimed = {}
        for ov in entries:
            name = ov.get("override_name", "<unnamed>")
            if normalize_temporal_mode(ov.get("temporal_mode")) is None:
                problems.append(f"{at_id}/{name}: temporal_mode {ov.get('temporal_mode')!r} must be one of {', '.join(TEMPORAL_MODES)}")
            weeks = ov.get("target_weeks") or []
            if not weeks:
                problems.append(f"{at_id}/{name}: no target_weeks")
            for w in weeks:
                if w not in POSTSEASON_WEEKS:
                    problems.append(f"{at_id}/{name}: target week {w} is not a postseason week (19-23)")
                if w in claimed:
                    problems.append(f"{at_id}/{name}: week {w} is also claimed by {claimed[w]}")
                claimed[w] = name
            pw = ov.get("preview_week")
            if pw is not None:
                if normalize_temporal_mode(ov.get("temporal_mode")) != "PROSPECTIVE":
                    problems.append(f"{at_id}/{name}: preview_week only applies to PROSPECTIVE overrides")
                if pw not in POSTSEASON_WEEKS:
                    problems.append(f"{at_id}/{name}: preview_week {pw} is not a postseason week (19-23)")
            if not ov.get("directive_override"):
                problems.append(f"{at_id}/{name}: missing directive_override")
    return problems
