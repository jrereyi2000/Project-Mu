"""The article comparison tool (tools/article_model_compare.py). Offline: no DB, no network.

Run: pytest -m "not integration" tests/test_article_model_compare.py
"""
import json
import os
from unittest import mock

import pytest

from tools import article_model_compare as compare


# ---------------------------------------------------------------- pure helpers

def test_cost_uses_uncached_cached_and_thinking_tokens():
    calls = [{"input_tokens": 1_000_000, "cached_tokens": 500_000,
              "output_tokens": 100_000, "thinking_tokens": 100_000}]
    # gemini-3.8-flash: 0.5M uncached * 0.75 + 0.5M cached * 0.075 + 0.2M out * 3.75
    assert compare.cost_usd("gemini-3.8-flash", calls) == pytest.approx(0.375 + 0.0375 + 0.75)
    assert compare.cost_usd("not-a-priced-model", calls) is None
    assert compare.cost_usd("gemini-3.8-flash", []) is None


def test_token_totals_sums_every_row():
    rows = [{"input_tokens": 10, "cached_tokens": 4, "output_tokens": 3, "thinking_tokens": 2},
            {"input_tokens": 5, "cached_tokens": 0, "output_tokens": 1, "thinking_tokens": 0}]
    assert compare.token_totals(rows) == {"input_tokens": 15, "cached_tokens": 4,
                                          "output_tokens": 4, "thinking_tokens": 2, "calls": 2}


def test_grounding_flags_only_numbers_absent_from_the_trace():
    result = {
        "text": "He threw for 4,812 yards. The team finished 16-0 in 2036.",
        "evidence": "record: 16-0, season 2036, yards 4,199",
    }
    flags = compare.grounding_flags(result)
    numbers = {n for flag in flags for n in flag["numbers"]}
    assert "4812" in numbers
    assert "16" not in numbers and "2036" not in numbers


def test_allowance_guard_refuses_a_window_already_over():
    payload = {"windows": {"five_hour": {"percent": 55}, "monthly": {"percent": 3}}}
    assert "5-hour" in compare.allowance_guard("muse-spark-1.3-contributor", payload)
    payload = {"windows": {"five_hour": {"percent": 10}, "monthly": {"percent": 40}}}
    assert "month" in compare.allowance_guard("muse-spark-1.3-contributor", payload)
    payload = {"windows": {"five_hour": {"percent": 10}, "monthly": {"percent": 4}}}
    assert compare.allowance_guard("muse-spark-1.3-contributor", payload) is None
    # Gemini is not billed to Go, so no guard applies.
    assert compare.allowance_guard("gemini-3.8-flash", {"windows": {"five_hour": {"percent": 99}}}) is None


# ---------------------------------------------------------------- artifacts

def _records():
    return [
        {"slot": "Hero Slot", "model": "gemini-3.8-flash", "repeat": 0, "article_type_id": "MACRO_SLATE_RECAP",
         "handle": "TESTER", "display_name": "Tester", "desk": "national", "seconds": 1.0,
         "text": "# A\n\nA body with 4,812 yards.", "words": 8, "failure_reason": None, "rewrites": [],
         "tool_calls": [], "tool_call_count": 0, "usage_calls": [], "tokens": compare.token_totals([]),
         "cost_usd": 0.1, "evidence": "yards 4,199"},
        {"slot": "Hero Slot", "model": "muse-spark-1.3-contributor", "repeat": 0, "article_type_id": "MACRO_SLATE_RECAP",
         "handle": "TESTER", "display_name": "Tester", "desk": "national", "seconds": 2.0,
         "text": "# B\n\nB body.", "words": 5, "failure_reason": None, "rewrites": [],
         "tool_calls": [], "tool_call_count": 0, "usage_calls": [], "tokens": compare.token_totals([]),
         "cost_usd": 0.01, "evidence": ""},
    ]


def test_blind_read_strips_names_and_writes_a_matching_answer_key(tmp_path):
    jobs = [{"slot": "Hero Slot", "article_type_name": "Slate Recap", "handle": "TESTER", "desk": "national",
             "desk_note": None, "archetype": "TESTER"}]
    models = ["gemini-3.8-flash", "muse-spark-1.3-contributor"]
    path = tmp_path / "blind.md"
    compare.blind_read(_records(), jobs, models, 2036, 18, str(path))
    text = path.read_text()
    before_key = text.split("## Answer key")[0]
    assert "gemini-3.8-flash" not in before_key and "muse-spark-1.3-contributor" not in before_key
    assert "Answer key" in text
    key_line = [line for line in text.splitlines() if line.startswith("- Hero Slot:")][0]
    for label, model in zip("AB", models):
        assert model in key_line
    assert text.count("### A") == 1 and text.count("### B") == 1


def test_factual_and_grounding_sheets(tmp_path):
    compare.write_factual(_records(), str(tmp_path))
    rows = (tmp_path / "factual_review.csv").read_text().splitlines()
    assert rows[0].startswith("sample,paragraph,text")
    assert any("A body with 4,812 yards." in row for row in rows)

    compare.write_grounding(_records(), str(tmp_path))
    grounded = (tmp_path / "grounding.csv").read_text()
    assert "4,812" in grounded


# ---------------------------------------------------------------- judge

def test_judge_scores_a_pair_both_orders_and_flags_order_dependence(tmp_path):
    answers = [
        {"winner": "a", "a": {}, "b": {}},   # first order: model a wins
        {"winner": "b", "a": {}, "b": {}},   # swapped order: model a wins again (its "b")
        {"winner": "a", "a": {}, "b": {}},   # different pair, first order
        {"winner": "a", "a": {}, "b": {}},   # swapped order: model a wins again (its "a" is now b)
    ]
    jobs = [{"slot": "Hero Slot", "article_type_name": "Slate Recap", "handle": "TESTER",
             "desk": "national", "desk_note": None, "archetype": "TESTER"}]
    with mock.patch.object(compare, "client_for", return_value=(object(), None)), \
         mock.patch.object(compare, "_judge_call", side_effect=answers):
        payload = compare.judge_pairs(_records(), jobs, ["gemini-3.8-flash", "muse-spark-1.3-contributor"],
                                      "glm-5.3", str(tmp_path))
    assert payload["scoreboard"]["gemini-3.8-flash"]["wins"] == 1
    assert payload["scoreboard"]["muse-spark-1.3-contributor"]["losses"] == 1
    assert (tmp_path / "judge.json").exists() and (tmp_path / "judge.md").exists()


def test_judge_reports_a_split_verdict_as_order_dependent(tmp_path):
    answers = [{"winner": "a", "a": {}, "b": {}}, {"winner": "a", "a": {}, "b": {}}]
    jobs = [{"slot": "Hero Slot", "article_type_name": "Slate Recap", "handle": "TESTER",
             "desk": "national", "desk_note": None, "archetype": "TESTER"}]
    with mock.patch.object(compare, "client_for", return_value=(object(), None)), \
         mock.patch.object(compare, "_judge_call", side_effect=answers):
        payload = compare.judge_pairs(_records(), jobs, ["gemini-3.8-flash", "muse-spark-1.3-contributor"],
                                      "glm-5.3", str(tmp_path))
    assert payload["pairs"][0]["verdict"] == "order-dependent"
    assert payload["scoreboard"]["gemini-3.8-flash"]["order_dependent"] == 1


# ---------------------------------------------------------------- main

def test_dry_run_writes_prompts_and_makes_no_model_call(tmp_path):
    job = {"slot": "Hero Slot", "article_type_id": "MACRO_SLATE_RECAP", "article_type_name": "Slate Recap",
           "display_name": "Tester", "handle": "TESTER", "archetype": "TESTER", "team_id": 1,
           "desk": "national", "desk_note": None, "system_instruction": "sys", "task_instruction": "task"}
    with mock.patch("tools.llm_compare.use_database_read_only"), \
         mock.patch("newsroom.swarm_dispatcher.SwarmDispatcher", return_value=mock.MagicMock()), \
         mock.patch.object(compare, "plan_jobs", return_value=[job]), \
         mock.patch.object(compare, "client_for") as client_for:
        code = compare.main(["--season", "2036", "--week", "18", "--dry-run", "--out", str(tmp_path)])
    assert code == 0
    client_for.assert_not_called()
    data = json.loads((tmp_path / "prompts.json").read_text())
    assert data["jobs"][0]["task_instruction"] == "task"
    assert data["models"] == compare.DEFAULT_MODELS


def test_main_skips_a_model_when_the_go_window_is_full(tmp_path):
    job = {"slot": "Hero Slot", "article_type_id": "MACRO_SLATE_RECAP", "article_type_name": "Slate Recap",
           "display_name": "Tester", "handle": "TESTER", "archetype": "TESTER", "team_id": 1,
           "desk": "national", "desk_note": None, "system_instruction": "sys", "task_instruction": "task"}
    full = {"windows": {"five_hour": {"percent": 60}, "monthly": {"percent": 5}}}
    with mock.patch("tools.llm_compare.use_database_read_only"), \
         mock.patch("newsroom.swarm_dispatcher.SwarmDispatcher", return_value=mock.MagicMock()), \
         mock.patch.object(compare, "plan_jobs", return_value=[job]), \
         mock.patch.object(compare, "go_allowance", return_value=full), \
         mock.patch.object(compare, "client_for") as client_for:
        code = compare.main(["--season", "2036", "--week", "18", "--models", "muse-spark-1.3-contributor",
                             "--no-judge", "--out", str(tmp_path)])
    assert code == 0
    client_for.assert_not_called()                    # refused before any client is built
    assert json.loads((tmp_path / "results.json").read_text()) == []
