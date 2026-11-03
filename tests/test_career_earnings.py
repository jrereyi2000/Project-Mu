"""Career-earnings ledger (CAP_RELATIVE_CONTRACTS_PLAN §2.4). Fixture rows only: no database."""
import pytest

from engines.cap_history import cap_in
from engines.career_earnings import (FINANCIAL_SECURITY_SHARE, deal_spans, earned_shares, is_secure,
                                     read_contract_rows)

CAPS = {2034: 300_000_000, 2035: 320_000_000, 2036: 350_000_000}


def row(season, length, rem, total, pid=1, team=10, status="ACTIVE", yoe=5):
    return {"player_id": pid, "team_id": team, "season": season, "length": length, "years_remaining": rem,
            "total_value": total, "status": status, "yoe": yoe}


def test_one_deal_across_three_scans_counts_once():
    rows = [row(2034, 3, 3, 30_000_000, status="SUPERSEDED"), row(2035, 3, 2, 30_000_000, status="SUPERSEDED"),
            row(2036, 3, 1, 30_000_000)]
    spans = deal_spans(rows)
    assert spans == [{"player_id": 1, "signed": 2034, "first_season": 2034, "last_season": 2036,
                      "apy": 10_000_000, "rookie_start": False}]
    e = earned_shares(spans, CAPS, 2036)[1]
    assert e["share"] == pytest.approx(10e6 / 300e6 + 10e6 / 320e6 + 10e6 / 350e6)


def test_release_cuts_the_span():
    rows = [row(2034, 5, 5, 50_000_000), row(2035, 5, 4, 50_000_000, status="RELEASED")]
    (d,) = deal_spans(rows)
    assert (d["first_season"], d["last_season"]) == (2034, 2035)
    assert earned_shares([d], CAPS, 2036)[1]["share"] == pytest.approx(10e6 / 300e6 + 10e6 / 320e6)


def test_new_deal_ends_the_old_ones_span():
    rows = [row(2034, 4, 4, 40_000_000), row(2036, 3, 3, 60_000_000, team=11)]
    old, new = deal_spans(rows)
    assert (old["signed"], old["last_season"]) == (2034, 2035)          # 2037 cut to the season before 2036
    assert (new["signed"], new["last_season"]) == (2036, 2038)
    e = earned_shares([old, new], CAPS, 2036)[1]
    assert e["share"] == pytest.approx(10e6 / 300e6 + 10e6 / 320e6 + 20e6 / 350e6)


def test_later_signed_deal_wins_an_overlapping_season():
    old = {"player_id": 1, "signed": 2034, "first_season": 2034, "last_season": 2036, "apy": 1e6,
           "rookie_start": False}
    new = {"player_id": 1, "signed": 2036, "first_season": 2036, "last_season": 2037, "apy": 9e6,
           "rookie_start": False}
    e = earned_shares([new, old], CAPS, 2036)[1]
    assert e["share"] == pytest.approx(1e6 / 300e6 + 1e6 / 320e6 + 9e6 / 350e6)


def test_shares_are_summed_per_seasons_cap_including_estimated_ones():
    rows = [row(2036, 4, 2, 40_000_000)]                               # signed 2034 per the scan, known caps 2036 only
    caps = {2036: 350_000_000}
    e = earned_shares(deal_spans(rows), caps, 2036)[1]
    assert e["share"] == pytest.approx(sum(10e6 / cap_in(s, caps) for s in (2034, 2035, 2036)))
    assert cap_in(2034, caps) < cap_in(2035, caps) < 350_000_000


def test_rookie_start_history_is_complete():
    rows = [row(2035, 4, 3, 8_000_000, yoe=1), row(2036, 4, 2, 8_000_000, yoe=2)]   # yoe at signing 2034 = 0
    spans = deal_spans(rows)
    assert spans[0]["rookie_start"] is True
    e = earned_shares(spans, CAPS, 2036)[1]
    assert e["complete"] is True and e["first_known"] == 2034


def test_deal_first_seen_mid_career_is_incomplete():
    rows = [row(2036, 3, 2, 30_000_000, yoe=6)]
    e = earned_shares(deal_spans(rows), CAPS, 2036)[1]
    assert e["complete"] is False and e["first_known"] == 2035


def test_complete_follows_the_earliest_deal():
    rows = [row(2034, 2, 2, 2_000_000, yoe=0), row(2036, 3, 3, 30_000_000, yoe=2)]
    e = earned_shares(deal_spans(rows), CAPS, 2036)[1]
    assert e["complete"] is True
    assert earned_shares(deal_spans(rows[1:]), CAPS, 2036)[1]["complete"] is False


def test_is_secure_three_states():
    assert FINANCIAL_SECURITY_SHARE == 0.15
    assert is_secure({"share": 0.15, "first_known": 2030, "complete": False}) is True
    assert is_secure({"share": 0.40, "first_known": 2030, "complete": True}) is True
    assert is_secure({"share": 0.05, "first_known": 2030, "complete": True}) is False
    assert is_secure({"share": 0.05, "first_known": 2030, "complete": False}) is None
    assert is_secure(None) is None
    assert is_secure({"share": 0.05, "first_known": 2030, "complete": True}, threshold=0.04) is True


def test_through_season_excludes_later_seasons():
    rows = [row(2034, 3, 3, 30_000_000), row(2037, 2, 2, 90_000_000, team=12, pid=2)]
    ledger = earned_shares(deal_spans(rows), CAPS, 2035)
    assert ledger[1]["share"] == pytest.approx(10e6 / 300e6 + 10e6 / 320e6)
    assert 2 not in ledger                                             # his only deal starts after 2035


def test_restructure_keeps_one_deal_with_the_latest_value():
    rows = [row(2035, 4, 4, 40_000_000, status="SUPERSEDED"), row(2036, 4, 3, 44_000_000)]
    spans = deal_spans(rows)
    assert len(spans) == 1
    assert spans[0]["apy"] == 11_000_000 and spans[0]["last_season"] == 2038
    assert earned_shares(spans, CAPS, 2036)[1]["share"] == pytest.approx(11e6 / 320e6 + 11e6 / 350e6)


def test_bad_rows_are_skipped():
    rows = [row(2036, None, 1, 1_000_000), row(2036, 2, 3, 1_000_000), row(2036, 2, 1, None),
            row(2036, 2, None, 1_000_000), row(2036, 0, 0, 1_000_000)]
    assert deal_spans(rows) == []


def test_transaction_row_then_scan_of_the_same_deal_is_one_deal():
    rows = [row(2035, 3, 3, 15_000_000), row(2036, 3, 2, 15_100_000)]  # signing row, then next scan's re-read
    assert len(deal_spans(rows)) == 1


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, args=None):
        self.sql, self.args = sql, args

    def fetchall(self):
        assert "player_contracts" in self.sql and "years_of_experience" in self.sql
        assert self.sql.lstrip().upper().startswith("SELECT")
        return self.rows


def test_read_contract_rows_on_a_fake_cursor():
    cur = _Cursor([(1, 10, 2036, 3, 2, 30_000_000, "ACTIVE", 6), (2, 11, 2035, 1, 1, 1_000_000, "RELEASED", None)])
    rows = read_contract_rows(cur, 2036)
    assert cur.args == (2036,)
    assert rows[0] == {"player_id": 1, "team_id": 10, "season": 2036, "length": 3, "years_remaining": 2,
                       "total_value": 30_000_000, "status": "ACTIVE", "yoe": 6}
    assert rows[1]["yoe"] is None and rows[1]["status"] == "RELEASED"
    assert len(deal_spans(rows)) == 2
