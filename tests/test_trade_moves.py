"""Trade moves (docs/team_management/03_GM_AGENTS_PLAN.md Phase 5). Pure plan + a recording fake cursor: no DB."""
import pytest

from pipeline import trade_moves as tm
from pipeline.transactions_parse import parse_row, row_key

SEASON = 2036


def contract(cid, hit=5_000_000, penalty=2_000_000, projected=()):
    years = {y: (hit if y < 2039 else 0, y in projected) for y in range(2036, 2041)}
    return {"contract_id": cid, "season": SEASON, "length": 3, "years_remaining": 3, "total_value": 15_000_000,
            "bonus": 3_000_000, "cut_savings": 3_000_000, "cut_penalty": penalty, "no_trade_clause": False,
            "incentive": None, "extension_years": None, "extension_value": None, "extended_transaction_id": None,
            "years": years}


def state():
    return {"teams": {11: {"abbr": "CAR", "name": "Panthers"}, 29: {"abbr": "NO", "name": "Saints"}},
            "players": {1: {"team_id": 11, "pos": "WR", "name": "Ann Able", "contract": contract(100, projected={2039})},
                        2: {"team_id": 29, "pos": "CB", "name": "Bo Bell", "contract": contract(200, hit=3_000_000,
                                                                                                penalty=0)}},
            "cap_rows": {11, 29}, "picks": {(2037, 2, 29): 29}}


def proposal(assets=None):
    return {"id": 42, "season": SEASON, "week": 5, "assets": assets or [
        {"type": "PLAYER", "player_id": 1, "from": 11, "to": 29},
        {"type": "PLAYER", "player_id": 2, "from": 29, "to": 11},
        {"type": "PICK", "pick": (2037, 2, 29), "from": 29, "to": 11}]}


def ops_of(kind, ops):
    return [o for o in ops if o["op"] == kind]


def test_contract_copy_keeps_every_year_and_projected_flag():
    ops, warnings = tm.plan_trade_ops(proposal(), state())
    assert not warnings
    c = next(o for o in ops_of("contract_trade", ops) if o["player"] == 1)
    assert c["team_id"] == 29 and c["old_contract_id"] == 100
    assert c["years"] == state()["players"][1]["contract"]["years"]          # every year, flags included
    assert c["years"][2039][1] is True and c["copy"]["cut_penalty"] == 2_000_000


def test_cap_rows_move_by_the_same_hit_and_dead_money_follows_the_mode(monkeypatch):
    ops, _ = tm.plan_trade_ops(proposal(), state())
    caps = {o["team_id"]: o for o in ops_of("cap_delta", ops)}
    # CAR sends a $5M hit (pays $2M dead) and takes a $3M one; NO the reverse with no dead money
    assert caps[11]["salaries"] == -5_000_000 + 3_000_000 and caps[11]["cap_penalty"] == 2_000_000
    assert caps[29]["salaries"] == 5_000_000 - 3_000_000 and caps[29]["cap_penalty"] == 0
    assert caps[11]["salaries"] == -caps[29]["salaries"]
    monkeypatch.setattr(tm, "TRADE_DEAD_MONEY", "none")
    ops, _ = tm.plan_trade_ops(proposal(), state())
    assert all(o["cap_penalty"] == 0 for o in ops_of("cap_delta", ops))


def test_pick_ownership_moves_and_one_transaction_per_leg():
    ops, _ = tm.plan_trade_ops(proposal(), state())
    assert ops_of("pick_owner", ops) == [{"op": "pick_owner", "pick": (2037, 2, 29), "owner": 11, "known": True}]
    txns = ops_of("txn", ops)
    assert len(txns) == 3 and [t["row_key"] for t in txns] == ["gm:42:1", "gm:42:2", "gm:42:3"]
    assert {t["trade_group"] for t in txns} == {"gm:42"}
    assert txns[0]["txn_date"] == "2036 SEASON - WEEK 5"
    assert txns[0]["raw_text"] == "PANTHERS TRADE (WR) ANN ABLE TO SAINTS"
    assert txns[2]["raw_text"] == "SAINTS TRADE 2037 2ND ROUND PICK TO PANTHERS"
    assert ops[-1] == {"op": "proposal_status", "id": 42, "executed_as": "PROPOSED"}


def test_row_keys_cannot_collide_with_scan_keys():
    ops, _ = tm.plan_trade_ops(proposal(), state())
    for t in ops_of("txn", ops):
        scanned = row_key(parse_row(t["raw_text"]), t["raw_text"])
        assert not scanned.startswith("gm:") and t["row_key"].startswith("gm:")


def test_warnings_never_block_and_bad_trades_raise():
    st = state()
    st["players"][1]["team_id"] = 5                                          # Mu has him elsewhere
    st["picks"] = {}
    ops, warnings = tm.plan_trade_ops(proposal(), st)
    assert len(warnings) == 2 and ops_of("move", ops)
    with pytest.raises(tm.TradeError):
        tm.plan_trade_ops(proposal([{"type": "PLAYER", "player_id": 1, "from": 11, "to": 11}]), state())
    with pytest.raises(tm.TradeError):
        tm.plan_trade_ops(proposal([{"type": "PLAYER", "player_id": 99, "from": 11, "to": 29}]), state())
    assert tm.heading(2036, 24) == "2036 OFFSEASON"


class FakeCursor:
    def __init__(self):
        self.sql, self.n = [], 1000

    def execute(self, q, args=None):
        self.sql.append((" ".join(q.split()), args))

    def fetchone(self):
        self.n += 1
        return (self.n,)


def test_execute_runs_every_op_in_order():
    ops, _ = tm.plan_trade_ops(proposal(), state())
    cur = FakeCursor()
    out = tm.execute_trade(cur, ops, SEASON)
    text = [q for q, _ in cur.sql]
    assert sum(q.startswith("INSERT INTO transactions") for q in text) == 3
    assert text.index(next(q for q in text if q.startswith("INSERT INTO transactions"))) == 0   # legs first
    assert sum("SET status = 'TRADED'" in q for q in text) == 2
    assert sum(q.startswith("INSERT INTO player_contracts") for q in text) == 2 and len(out["contracts"]) == 2
    proj = [a for q, a in cur.sql if "SET projected = true" in q]
    assert proj and proj[0][1] == [2039]                                     # the projected year stays projected
    cap = [a for q, a in cur.sql if q.startswith("UPDATE team_cap_seasons")]
    assert len(cap) == 2 and all(a[4] == out["transactions"][0] for a in cap)  # derived_transaction_id
    assert any(q.startswith("INSERT INTO draft_picks") for q in text)
    assert "SET status = 'EXECUTED'" in text[-1]
