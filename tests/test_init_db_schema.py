"""db/init_db.sql builds a new franchise database (db/seed_clean_db.py), so every table, index and view a
migration creates must also be in it (PROJECT_SPEC §3)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CREATE = re.compile(
    r"create\s+(table|(?:unique\s+)?index|(?:or\s+replace\s+)?view)\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*)")


def test_every_migration_object_is_in_init_db():
    init = (ROOT / "db" / "init_db.sql").read_text().lower()
    missing = []
    for path in sorted((ROOT / "db" / "migrations").glob("*.sql")):
        for kind, name in CREATE.findall(path.read_text().lower()):
            noun = kind.split()[-1]
            if not re.search(rf"\b{noun}\s+(if\s+not\s+exists\s+)?{name}\b", init):
                missing.append(f"{path.name}: {kind} {name}")
    assert not missing, "not in db/init_db.sql:\n" + "\n".join(missing)


def test_personality_core_events_carry_the_extension_rule():
    """2027_01_29 replaced the one-change-a-season UNIQUE with a partial index on evolution rows."""
    init = " ".join((ROOT / "db" / "init_db.sql").read_text().split())
    table = init[init.index("CREATE TABLE IF NOT EXISTS persona_core_events"):]
    table = table[:table.index(");")]
    assert "UNIQUE (persona_type, persona_id, season)" not in table
    assert "seq BIGINT GENERATED ALWAYS AS IDENTITY" in table
    assert ("CREATE UNIQUE INDEX IF NOT EXISTS persona_core_events_one_evolution_per_season "
            "ON persona_core_events (persona_type, persona_id, season) WHERE kind = 'evolution'") in init
