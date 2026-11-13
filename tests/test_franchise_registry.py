"""The registry's one "is this team JR's?" rule (db/franchises.py; JR 2026-11-12, P8-08).

An empty `user_teams` means **nobody** is a user team, never all 32. Before this, the inbox refused every team on an
empty registry (`engines/inbox/actors.py`, `server/inbox_api.py`) while `server/franchise_api.py` answered "all 32
are yours" (`require_user_team`, `overview.user_controlled`) — so the live `madden_mu2026` printed YOUR TEAM on all
32 and only the inbox 403'd. These tests pin the rule once, then pin that the surfaces ask it and get one answer.

Offline: the rule is pure over a registry entry; the server modules are asked through their own (patched) view of
the list, exactly as their route tests do, so no database and no HTTP are involved.
"""
import pytest
from fastapi import HTTPException

from db import franchises
from engines.inbox import actors
from server import franchise_api as fa

NOBODY = "no user team is registered for this franchise"


def test_an_empty_registry_means_nobody_is_a_user_team():
    assert franchises.user_team_abbrs({"user_teams": []}) == []
    for abbr in ("CAR", "ATL", "ARI", "WSH"):
        assert franchises.registry_team_is_user(abbr, []) is False
        assert franchises.user_team_refusal(abbr, []) == NOBODY


def test_a_missing_list_and_no_franchise_are_the_same_as_empty():
    assert franchises.user_team_abbrs({"db": "madden_x"}) == []          # registered, no user_teams key
    assert franchises.user_team_abbrs(None) == []                        # no active franchise


def test_an_unreadable_registry_is_no_franchise_not_all_of_them(monkeypatch):
    def boom(*a, **k):
        raise OSError("no registry file")

    monkeypatch.setattr(franchises, "active_entry", boom)
    assert franchises.user_team_abbrs() == []
    assert franchises.registry_team_is_user("CAR") is False


def test_one_user_team_means_only_that_team():
    teams = franchises.user_team_abbrs({"user_teams": ["car"]})
    assert teams == ["CAR"]
    assert franchises.registry_team_is_user("CAR", teams) is True
    assert franchises.registry_team_is_user("car", teams) is True        # the save's lower/upper case
    assert franchises.registry_team_is_user("ATL", teams) is False
    assert franchises.user_team_refusal("CAR", teams) is None
    assert franchises.user_team_refusal("ATL", teams) == "ATL isn't one of your user teams (CAR)"


def test_an_empty_abbreviation_is_never_a_user_team():
    assert franchises.registry_team_is_user("", ["CAR"]) is False
    assert franchises.registry_team_is_user(None, ["CAR"]) is False


def test_two_user_teams_both_count():
    teams = franchises.user_team_abbrs({"user_teams": ["IND", "car"]})
    assert teams == ["IND", "CAR"]
    assert franchises.registry_team_is_user("IND", teams) and franchises.registry_team_is_user("CAR", teams)
    assert franchises.user_team_refusal("ATL", teams) == "ATL isn't one of your user teams (IND, CAR)"


def test_the_surfaces_agree_when_no_user_team_is_registered(monkeypatch):
    """The P8-08 split itself: the inbox and the franchise API now give the same answer, in the same words."""
    monkeypatch.setattr(fa, "user_teams", lambda: [])
    monkeypatch.setattr(actors, "user_teams", lambda: [])
    with pytest.raises(HTTPException) as refused:
        fa.require_user_team({"abbr": "CAR"})
    assert (refused.value.status_code, refused.value.detail) == (403, NOBODY)
    assert fa.user_team_refusal("CAR", fa.user_teams()) == NOBODY       # what overview.user_controlled reads
    assert actors.is_user_team("CAR") is False
    assert actors.user_team_refusal("CAR") == NOBODY


def test_the_surfaces_agree_when_one_user_team_is_registered(monkeypatch):
    monkeypatch.setattr(fa, "user_teams", lambda: ["CAR"])
    monkeypatch.setattr(actors, "user_teams", lambda: ["CAR"])
    assert fa.require_user_team({"abbr": "CAR"}) is None                # CAR still served, exactly as before
    with pytest.raises(HTTPException) as refused:
        fa.require_user_team({"abbr": "ATL"})
    assert (refused.value.status_code, refused.value.detail) == (403, "ATL isn't one of your user teams (CAR)")
    assert actors.is_user_team("CAR") is True
    assert actors.user_team_refusal("ATL") == "ATL isn't one of your user teams (CAR)"
