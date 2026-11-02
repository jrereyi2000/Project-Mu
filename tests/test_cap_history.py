"""League cap history (CAP_RELATIVE_CONTRACTS_PLAN §2.1). Fixture rows only: no database."""
from engines.cap_history import cap_in, league_caps
from engines.contracts_engine import CBA

G = CBA["CAP_GROWTH"]["mean"]
STEP = CBA["CAP_ROUND"]


def rounded(x):
    return int(round(x / STEP)) * STEP


def test_known_season_is_exact():
    assert cap_in(2036, {2036: 367_000_000}) == 367_000_000
    assert cap_in(2036, {2036: 367_012_345, 2037: 390_000_000}) == 367_012_345      # never rounded or estimated


def test_going_back_divides_by_growth_and_rounds():
    caps = {2036: 367_000_000}
    assert cap_in(2035, caps) == rounded(367_000_000 / (1 + G))
    assert cap_in(2035, caps) % STEP == 0
    assert cap_in(2034, caps) == rounded(367_000_000 / (1 + G) ** 2)


def test_going_forward_multiplies():
    caps = {2036: 367_000_000}
    assert cap_in(2037, caps) == rounded(367_000_000 * (1 + G))
    assert cap_in(2037, caps) > 367_000_000 > cap_in(2035, caps)


def test_nearest_known_season_wins_earlier_on_tie():
    caps = {2030: 200_000_000, 2036: 367_000_000}
    assert cap_in(2035, caps) == rounded(367_000_000 / (1 + G))                    # 2036 is nearer
    assert cap_in(2031, caps) == rounded(200_000_000 * (1 + G))                    # 2030 is nearer
    assert cap_in(2033, caps) == rounded(200_000_000 * (1 + G) ** 3)               # tie: the earlier season


def test_empty_caps_give_reference_cap():
    assert cap_in(2036, {}) == CBA["REFERENCE_CAP"]
    assert cap_in(1999, {}) == CBA["REFERENCE_CAP"]


def test_deterministic():
    caps = {2036: 367_000_000}
    assert [cap_in(2033, caps) for _ in range(3)] == [cap_in(2033, caps)] * 3


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, args=None):
        self.sql = sql

    def fetchall(self):
        assert "team_cap_seasons" in self.sql
        return self.rows


def test_league_caps_takes_the_median_per_season():
    rows = [(2036, 360_000_000), (2036, 367_000_000), (2036, 370_000_000),
            (2037, 380_000_000), (2037, 390_000_000), (2037, 400_000_000), (2037, None)]
    assert league_caps(_Cursor(rows)) == {2036: 367_000_000, 2037: 390_000_000}
    assert league_caps(_Cursor([])) == {}
