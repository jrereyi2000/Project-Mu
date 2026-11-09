"""The league office's once-a-season producers and articles (CLOSE_OUT_CHECKLIST P1-18 to P1-23).

Offline: the producers, the articles and the connection are faked; the test pins the order, the once-a-season gate
and the warning-only rule.
"""
from unittest import mock

from engines import compensatory_picks_run
from engines import report_card_run
from engines import staff_contracts_run
from newsroom import compensatory_picks_article
from newsroom import report_card_article
from pipeline import league_office as lo


class Conn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Cur:
    def __init__(self, fail=()):
        self.connection = Conn()
        self.fail = set(fail)
        self.sql = []

    def execute(self, q, a=None):
        self.sql.append(q)
        name = next((n for n in self.fail if n in q), None)
        if name is not None:
            raise RuntimeError(f"{name} broke")
        return self


def _patch(monkeypatch, calls, fail=()):
    def make(name, result):
        def fn(*a, **k):
            calls.append(name)
            if name in fail:
                raise RuntimeError(f"{name} broke")
            return result
        return fn

    monkeypatch.setattr(compensatory_picks_run, "award_and_persist",
                        make("comp", {"status": "built", "qfas": 3, "awards": 2, "draft_picks": 2}))
    monkeypatch.setattr(staff_contracts_run, "annual", make("staff", {"status": "built", "filed": 4, "payroll": 9}))
    monkeypatch.setattr(report_card_run, "annual", make("cards", {"status": "built", "teams": 32, "orders": 1}))
    monkeypatch.setattr(compensatory_picks_article, "run", make("comp_article", {"status": "PUBLISHED"}))
    monkeypatch.setattr(report_card_article, "run", make("card_article", {"status": "PUBLISHED"}))
    monkeypatch.setattr(lo, "_conninfo", lambda cur: "fake")
    monkeypatch.setattr(lo, "_user_team_ids", lambda cur: {1})


def test_only_the_first_free_agency_wave_runs_the_annual_steps(monkeypatch):
    calls = []
    _patch(monkeypatch, calls)
    assert lo.annual_offseason_steps(Cur(), "RESIGN", 2036, None, out=lambda *_: None) is None
    assert lo.annual_offseason_steps(Cur(), lo.FREE_AGENCY, 2036, 2, out=lambda *_: None) is None
    assert calls == []


def test_the_steps_run_in_order_and_commit(monkeypatch):
    calls = []
    _patch(monkeypatch, calls)
    cur = Cur()
    rep = lo.annual_offseason_steps(cur, lo.FREE_AGENCY, 2036, 1, out=lambda *_: None)
    assert calls == ["comp", "staff", "cards", "comp_article", "card_article"]
    assert rep["compensatory_picks"]["qfas"] == 3 and rep["report_cards"]["teams"] == 32
    assert cur.connection.commits >= 5                    # each producer/article commits before the next


def test_a_failing_step_is_recorded_and_never_stops_the_rest(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, fail=("staff",))
    rep = lo.annual_offseason_steps(Cur(), lo.FREE_AGENCY, 2036, 1, out=lambda *_: None)
    assert rep["staff_contracts"]["status"] == "FAILED" and "broke" in rep["staff_contracts"]["error"]
    assert calls == ["comp", "staff", "cards", "comp_article", "card_article"]
    assert rep["report_cards"]["status"] == "built"


def test_annual_summaries_never_carry_article_text():
    summary = lo._annual_summary("x", {"status": "PUBLISHED", "article": {"content": "x" * 5000},
                                       "article_id": 7, "articles_written": 1})
    assert summary == {"status": "PUBLISHED", "article_id": 7, "articles_written": 1}
