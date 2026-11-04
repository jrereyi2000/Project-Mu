"""Typed text -> a draft JR confirms (FRONT_OFFICE_INBOX_PLAN.md §2.3, D10; U1). No real model: fakes only."""
import json

import pytest

from engines.inbox import parse


@pytest.mark.parametrize("text, want", [
    ("3 years, 40 million, 15 guaranteed", (3, 40_000_000, 15_000_000, 0)),
    ("3 yrs $40M total with $15M gtd", (3, 40_000_000, 15_000_000, 0)),
    ("Three years, $40,000,000, $15,000,000 guaranteed and a $5M signing bonus", (3, 40_000_000, 15_000_000,
                                                                                    5_000_000)),
    ("4-year deal worth 52.5 million, bonus 8 million", (4, 52_500_000, 0, 8_000_000)),
    ("2 years 1.2m", (2, 1_200_000, 0, 0)),
    ("1 year, $750K", (1, 750_000, 0, 0)),
])
def test_contract_phrasings(text, want):
    got = parse.parse_contract_terms(text)
    assert (got["years"], got["total"], got["guaranteed"], got["bonus"]) == want


@pytest.mark.parametrize("text", ["40", "40 million", "3 years", "", "let's talk next week",
                                  "3 years, 10 million, 15 guaranteed", "12 years 40 million"])
def test_not_contract_terms(text):
    assert parse.parse_contract_terms(text) is None


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)


SCHEMA = {"give": {"type": "ids"}, "years": {"type": "number", "min": 1, "max": 7},
          "slot": {"type": "enum", "values": ["WR", "CB"]}, "week": {"type": "number", "min": 6, "max": 18,
                                                                     "optional": True}}
CHOICES = ["player:100", "pick:2027:2"]


def test_model_fallback_accepts_listed_ids_and_ranges():
    llm = FakeLLM({"give": ["pick:2027:2"], "years": 3, "slot": "WR"})
    got = parse.parse_with_llm(llm, "my 2nd for him, 3 years", SCHEMA, CHOICES, budget_s=1)
    assert got == {"give": ["pick:2027:2"], "years": 3, "slot": "WR"}
    assert "pick:2027:2" in llm.prompts[0]


@pytest.mark.parametrize("answer", [
    {"give": ["player:999"], "years": 3, "slot": "WR"},          # unknown id
    {"give": ["pick:2027:2"], "years": 9, "slot": "WR"},         # out of range
    {"give": ["pick:2027:2"], "years": 3, "slot": "QB"},         # not an enum value
    {"give": ["pick:2027:2"], "years": 3, "slot": "WR", "week": 2},
    {"give": ["pick:2027:2"], "slot": "WR"},                     # missing required
    {"give": ["pick:2027:2"], "years": True, "slot": "WR"},      # a bool isn't a number
    {"none": True},
    "not json at all",
])
def test_model_fallback_rejects_bad_answers(answer):
    assert parse.parse_with_llm(FakeLLM(answer), "whatever", SCHEMA, CHOICES, budget_s=1) is None


def test_model_fallback_without_model():
    assert parse.parse_with_llm(None, "3 years", SCHEMA, CHOICES) is None


def test_summaries():
    assert parse.draft_summary("MAKE_OFFER", {"years": 3, "total": 40_000_000, "guaranteed": 15_000_000,
                                              "bonus": 0}) == "Offer 3 years, $40.0M total, $15.0M guaranteed, $0 bonus"
    assert parse.draft_summary("PROMISE", {"slot": "WR", "max_depth": 1, "due_week": 9}) == \
        "Promise: you'll start at WR by week 9"
    assert parse.draft_summary("PROPOSE", {"direction": "REBUILD", "emphasis": "DEFENSE"}) == \
        "Propose: direction rebuild, emphasis defense"
    assert parse.draft_summary("END_TALKS", {}) == "End talks"
    assert parse.money(750_000) == "$750K"
