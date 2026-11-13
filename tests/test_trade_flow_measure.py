"""CPU trade-flow measurement tool (CLOSE_OUT_CHECKLIST P3-08; audit-p3-trade F2/LOW).

Pure tests: the scratch-database guard, the per-week row (the old `new` read `rep["proposals"]`, which is always
empty in the all-CPU era, so it was always 0) and the season diagnostic over a scripted cursor. The real replay is
exercised by running the tool against a `mu_scratch_*` clone of `madden_2036`; nothing here opens a database."""
from tools import trade_flow_measure as tfm


class FakeConn:
    def __init__(self, name):
        self.name = name

    def execute(self, sql, params=()):
        return self

    def fetchone(self):
        return (self.name,)


def test_require_scratch_refuses_a_non_scratch_database():
    assert tfm._require_scratch(FakeConn("mu_scratch_p3_trade_test")) == "mu_scratch_p3_trade_test"
    for name in ("madden_2036", "madden_mu2026", "madden_test", "madden_tune_p2a", "postgres"):
        try:
            tfm._require_scratch(FakeConn(name))
        except SystemExit as e:
            assert name in str(e)
        else:
            raise AssertionError(f"{name} was accepted")


def test_week_row_counts_the_talks_sourced_not_the_empty_proposal_list():
    """`rep["proposals"]` holds only advisory/user deals, so the all-CPU era reports 0 there. The row reports the
    talks the run sourced, and the budget's draw beside it (week 8 asks for more than it can source)."""
    rep = {"budget": {"new": 4, "n": 4}, "talks": {"talks": [{"id": 1, "status": "AGREED"},
                                                             {"id": 2, "status": "STALLED"}]},
           "proposals": [], "expired": [7], "rechecked": [8, 9]}
    row = tfm.week_row(6, rep, executed=1, refused=[{"id": 3, "reason": "x"}])
    assert row["new"] == 2 and row["talks"] == 2 and row["requested"] == 4
    assert row["talk_outcomes"] == {"AGREED": 1, "STALLED": 1}
    assert row["expired"] == 1 and row["rechecked"] == 2
    assert row["executed"] == 1 and len(row["refused"]) == 1


class DiagCur:
    """A cursor that answers the diagnostic's seven reads by their SQL text, in order."""
    def __init__(self):
        self._rows = []

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        if "message_source" in q:
            self._rows = [("LLM", 57), ("TEMPLATE", 5)]
        elif "FROM gm_trade_leaks" in q:
            self._rows = [(3,)]
        elif "t.posture" in q:
            self._rows = [("FIRM", 20), ("ANCHOR", 5)]
        elif "action = 'COUNTER'" in q:
            self._rows = [(13,)]
        elif "status = 'EXECUTED'" in q:
            self._rows = [(9,)]
        elif "status IN ('EXECUTED', 'STALLED', 'REJECTED', 'EXPIRED')" in q:
            self._rows = [(25,)]
        elif "FROM gm_trade_negotiations" in q:
            self._rows = [("AGREED", 9), ("STALLED", 16)]
        else:                                                   # pragma: no cover - an unexpected read
            raise AssertionError(f"unrouted query: {q}")
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_diagnostic_is_the_measured_table():
    rep = tfm.diagnostic(DiagCur(), 2036, [{"week": 4}], cleared=17)
    assert rep["talks"] == 25
    assert rep["outcomes"] == {"AGREED": 9, "STALLED": 16, "WALKED_AWAY": 0, "OPEN": 0}
    assert rep["breakdown_share"] == 0.64 and rep["executed"] == 9 and rep["decided"] == 25
    assert rep["openings"] == {"FIRM": 20, "ANCHOR": 5}
    assert rep["turns"] == 62 and rep["message_lines"]["template_share"] == 0.081
    assert rep["counters"] == 13 and rep["counters_per_talk"] == 0.52
    assert rep["leaks"] == 3 and rep["executed_share"] == 0.36
    assert rep["cleared_proposals"] == 17
