"""Resolution JC-2A data path (COMPENSATORY_PICKS_PLAN follow-up U6): the reviewed program-eligibility registry,
the hire-away journal the comp producer reads, and the carousel's guarded lookup. Offline; fake cursors only, no
database. The registry never invents demographic data: the reviewed `eligible` flag stands in for the engine's
`is_minority` input.
"""
from engines import coaching_carousel as cc
from engines import compensatory_picks_engine as cpe

BOTH = {"staff_development_eligibility", "staff_development_departures"}


class FakeCursor:
    """Answers the four query shapes coaching_carousel's data path issues. An absent table answers the to_regclass
    guard with False, exactly as a database without the migration would."""

    def __init__(self, tables=(), eligibility=None, departures=None):
        self.tables = set(tables)
        self.eligibility = dict(eligibility or {})     # {(person_kind, person_key): eligible}
        self.departures = list(departures or [])       # rows eligible_departures reads
        self.inserted = []
        self.sql = []
        self._result = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.sql.append(s)
        if s.startswith("SELECT to_regclass"):
            self._result = (params[0] in self.tables,)
        elif s.startswith("SELECT eligible FROM staff_development_eligibility"):
            self._result = (self.eligibility.get((params[0], params[1])),)
        elif s.startswith("INSERT INTO staff_development_departures"):
            self.inserted.append(params)
            self._result = None
        elif s.startswith("SELECT from_team_id, person_name, from_role, to_role, tenure_years, source"):
            self._result = list(self.departures)
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def fetchall(self):
        return list(self._result or [])


def test_role_sets_match_the_engine_contract():
    assert cc.STAFF_DEV_FROM_ROLES == cpe.JC2A_FROM_ROLES
    assert cc.STAFF_DEV_TO_ROLES == cpe.JC2A_TO_ROLES
    assert cc.STAFF_DEV_TENURE_YEARS == cpe.JC2A_TENURE_YEARS


def test_eligibility_lookup_tolerates_absent_tables():
    cur = FakeCursor()                                 # no migration applied: both tables absent
    assert cc.development_eligible(cur, "COORDINATOR", 7) is False
    assert cc.record_staff_departure(cur, 2030, "COORDINATOR", 7, "Bo Coach", 1, 2, "OC", "HC", 3) is False
    assert cc.eligible_departures(cur, 2030) == []
    assert cur.inserted == []
    assert all(s.startswith("SELECT to_regclass") for s in cur.sql)


def test_eligible_coordinator_with_two_plus_years_hired_away_records_one_departure():
    cur = FakeCursor(tables=BOTH, eligibility={("COORDINATOR", "7"): True})
    assert cc.record_staff_departure(cur, 2030, "COORDINATOR", 7, "Bo Coach", 1, 2, "OC", "HC", 3,
                                     source="coaching_carousel:POACHED", hire_coach_id=41) is True
    assert len(cur.inserted) == 1
    season, from_team, to_team, name, from_role, to_role, tenure, source, kind, key, coach_id = cur.inserted[0]
    assert (season, from_team, to_team, name, from_role, to_role, tenure) == \
        (2030, 1, 2, "Bo Coach", "OC", "HC", 3)
    assert source == "coaching_carousel:POACHED"
    # 2027_02_15: the journal keeps the ids, so a JC-2A award can name the coach without matching on a name
    assert (kind, key, coach_id) == ("COORDINATOR", "7", 41)


def test_non_eligible_or_absent_person_records_none():
    absent = FakeCursor(tables=BOTH, eligibility={})
    assert cc.record_staff_departure(absent, 2030, "COORDINATOR", 7, "Bo", 1, 2, "OC", "HC", 3) is False
    assert absent.inserted == []
    ineligible = FakeCursor(tables=BOTH, eligibility={("COORDINATOR", "7"): False})
    assert cc.record_staff_departure(ineligible, 2030, "COORDINATOR", 7, "Bo", 1, 2, "OC", "HC", 3) is False
    assert ineligible.inserted == []


def test_one_year_tenure_records_none():
    cur = FakeCursor(tables=BOTH, eligibility={("COORDINATOR", "7"): True})
    assert cc.record_staff_departure(cur, 2030, "COORDINATOR", 7, "Bo", 1, 2, "OC", "HC", 1) is False
    assert cur.inserted == []


def test_same_club_or_non_qualifying_move_records_none():
    cur = FakeCursor(tables=BOTH, eligibility={("COORDINATOR", "7"): True})
    assert cc.record_staff_departure(cur, 2030, "COORDINATOR", 7, "Bo", 1, 1, "OC", "HC", 3) is False
    assert cc.record_staff_departure(cur, 2030, "COORDINATOR", 7, "Bo", 1, 2, "HC", "HC", 3) is False
    assert cc.record_staff_departure(cur, 2030, "COORDINATOR", 7, "Bo", 1, 2, "OC", "OC", 3) is False
    assert cur.inserted == []


def test_pure_jc2a_contract_two_round3_picks_across_consecutive_drafts_from_a_departure_row():
    # A journalled eligible departure, read the way the comp producer will read it, then the pure contract.
    cur = FakeCursor(tables=BOTH, departures=[(1, "Bo Coach", "OC", "HC", 3, "coaching_carousel:POACHED",
                                               "COORDINATOR", "7", 41)])
    rows = cc.eligible_departures(cur, 2030)
    assert len(rows) == 1
    assert (rows[0]["person_kind"], rows[0]["person_key"], rows[0]["hire_coach_id"]) == ("COORDINATOR", "7", 41)
    assert cpe.jc2a_eligible(rows[0]) is True
    awards = cpe.jc2a_awards(rows, 2030)
    assert [(a["draft_season"], a["team_id"], a["round"], a["award_kind"]) for a in awards] == \
        [(2031, 1, 3, "JC2A"), (2032, 1, 3, "JC2A")]
