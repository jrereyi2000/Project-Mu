"""engines/nfl_mirror EA ratings and created players (NFL_WEEKLY_MIRROR_PLAN.md §2.8, §4).

Offline: the EA page shape comes from ``tests/fixtures/ea_ratings_page.json`` and the fetch test monkeypatches the
page reader, so nothing here touches the network or a database.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from engines.nfl_mirror import create, ea_ratings
from engines.nfl_mirror.real_state import RealPlayer

FIXTURE = Path(__file__).parent / "fixtures" / "ea_ratings_page.json"


# ----------------------------------------------------------------------------------------------- fixtures ----
def page():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def ea_items():
    return page()["props"]["pageProps"]["ratingDetails"]["items"]


def page_html(items, total=10):
    data = {"props": {"pageProps": {"ratingDetails": {"items": items, "totalItems": total}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def real(name, position="WR", birth_date=None, **kwargs):
    return RealPlayer(gsis_id="00-test", name=name, position=position, team="KC", status="ACTIVE",
                      designation="A01", birth_date=birth_date, **kwargs)


def madden_player(pgid, position, overall, ratings, status="Signed", team_index=0, archetype="WR_DeepThreat"):
    return {"pgid": pgid, "position": position, "overall": overall, "status": status,
            "team_index": team_index, "archetype": archetype, "ratings": ratings}


def free_agent(pgid, overall, position="WR", status="FreeAgent", team_index=32):
    return {"pgid": pgid, "position": position, "overall": overall, "status": status,
            "team_index": team_index, "archetype": "WR_DeepThreat", "ratings": {"Speed": 50}}


def donor(pgid=900, position="WR", archetype="WR_DeepThreat"):
    return {"pgid": pgid, "position": position, "archetype": archetype,
            "ratings": {"Speed": 40, "Catching": 40, "Overall": 50}, "overall": 50}


# ---------- page parsing and fetch ----------

def test_parse_page_reads_next_data_iteration_and_total():
    parsed = ea_ratings.parse_page(page_html(ea_items(), total=4))
    assert parsed["iteration"] == "madden-ratings-week-8"
    assert parsed["total"] == 4
    assert len(parsed["items"]) == 4


def test_parse_page_fails_loudly_on_a_shape_change():
    with pytest.raises(RuntimeError, match="__NEXT_DATA__"):
        ea_ratings.parse_page("<html>nothing here</html>")


def test_fetch_iteration_stops_when_a_page_adds_no_new_ids_and_caches(tmp_path, monkeypatch):
    items = ea_items()
    pages = {1: items[:2], 2: items[1:], 3: items[2:]}
    calls, sleeps = [], []
    monkeypatch.setattr(ea_ratings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ea_ratings.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_page(number):
        calls.append(number)
        return page_html(pages[number])

    monkeypatch.setattr(ea_ratings, "_fetch_page", fake_page)
    fetched = ea_ratings.fetch_iteration(refresh=True)

    assert calls == [1, 2, 3]                      # page 3 adds nothing -> stop
    assert sleeps == [1.5, 1.5]                    # one wait between each pair of pages
    assert Path(fetched.path) == tmp_path / "ea_m27_madden-ratings-week-8.json"
    assert len(ea_ratings.read_items(fetched)) == 4
    digest = hashlib.sha256(Path(fetched.path).read_bytes()).hexdigest()
    assert fetched.sha256 == digest
    assert fetched.bytes == Path(fetched.path).stat().st_size


def test_fetch_iteration_uses_the_cache_without_fetching(tmp_path, monkeypatch):
    monkeypatch.setattr(ea_ratings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ea_ratings, "_fetch_page", lambda number: page_html(ea_items()))
    first = ea_ratings.fetch_iteration(refresh=True)
    monkeypatch.setattr(ea_ratings, "_fetch_page",
                        lambda number: pytest.fail("a cached iteration must not fetch"))
    assert ea_ratings.fetch_iteration().sha256 == first.sha256


def week9(item):
    """A fixture item relabelled to the next weekly iteration."""
    item = copy.deepcopy(item)
    item["iteration"] = {"id": "madden-ratings-week-9", "label": "Week 9 Ratings"}
    return item


def test_fetch_iteration_refuses_a_later_page_with_another_iteration(tmp_path, monkeypatch):
    """Audit M-0509 F1's repro: page 1 week 8 and page 2 week 9 never become one mixed cache."""
    items = ea_items()
    monkeypatch.setattr(ea_ratings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ea_ratings.time, "sleep", lambda seconds: None)
    pages = {1: copy.deepcopy(items[:1]), 2: [week9(items[1])]}
    calls = []

    def fake_page(number):
        calls.append(number)
        return page_html(pages[number], total=2)

    monkeypatch.setattr(ea_ratings, "_fetch_page", fake_page)
    with pytest.raises(RuntimeError, match="two iterations"):
        ea_ratings.fetch_iteration(refresh=True)
    assert calls == [1, 2]
    assert list(tmp_path.glob("ea_m27_*")) == []       # no mixed cache published under either id


def test_fetch_iteration_refuses_mixed_item_iterations_within_one_page(tmp_path, monkeypatch):
    monkeypatch.setattr(ea_ratings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ea_ratings.time, "sleep", lambda seconds: None)
    rows = [copy.deepcopy(ea_items()[0]), week9(ea_items()[1])]
    monkeypatch.setattr(ea_ratings, "_fetch_page", lambda number: page_html(rows, total=2))
    with pytest.raises(RuntimeError, match="madden-ratings-week-9"):
        ea_ratings.fetch_iteration(refresh=True)
    assert list(tmp_path.glob("ea_m27_*")) == []


def test_fetch_iteration_keeps_an_existing_cache_when_a_refresh_hits_another_iteration(tmp_path, monkeypatch):
    """Old-cache preservation: the disagreement is found before anything touches the cached file."""
    monkeypatch.setattr(ea_ratings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ea_ratings.time, "sleep", lambda seconds: None)
    existing = tmp_path / "ea_m27_madden-ratings-week-8.json"
    existing.write_text(json.dumps({"iteration": "madden-ratings-week-8", "total_items": 2,
                                    "items": ea_items()[:2]}, separators=(",", ":")), encoding="utf-8")
    before = (existing.read_bytes(), existing.stat().st_mtime_ns)
    pages = {1: copy.deepcopy(ea_items()[:1]), 2: [week9(ea_items()[1])]}
    monkeypatch.setattr(ea_ratings, "_fetch_page", lambda number: page_html(pages[number], total=2))

    with pytest.raises(RuntimeError, match="two iterations"):
        ea_ratings.fetch_iteration(refresh=True)

    assert existing.read_bytes() == before[0]
    assert existing.stat().st_mtime_ns == before[1]
    assert sorted(path.name for path in tmp_path.glob("ea_m27_*")) == [existing.name]


def test_fetch_iteration_keeps_an_existing_cache_when_a_later_page_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(ea_ratings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ea_ratings.time, "sleep", lambda seconds: None)
    existing = tmp_path / "ea_m27_madden-ratings-week-8.json"
    existing.write_text(json.dumps({"iteration": "madden-ratings-week-8", "total_items": 2,
                                    "items": ea_items()[:2]}, separators=(",", ":")), encoding="utf-8")
    before = (existing.read_bytes(), existing.stat().st_mtime_ns)

    def failing_page(number):
        if number == 1:
            return page_html(ea_items()[:1], total=2)
        raise RuntimeError("EA ratings page 2 fetch failed")

    monkeypatch.setattr(ea_ratings, "_fetch_page", failing_page)
    with pytest.raises(RuntimeError, match="fetch failed"):
        ea_ratings.fetch_iteration(refresh=True)

    assert existing.read_bytes() == before[0]
    assert existing.stat().st_mtime_ns == before[1]
    assert sorted(path.name for path in tmp_path.glob("ea_m27_*")) == [existing.name]


# ---------- EA stat and position mapping ----------

def test_ea_to_save_covers_every_fixture_stat_key():
    keys = set()
    for item in ea_items():
        keys |= set(item["stats"])
    assert keys == set(ea_ratings.EA_TO_SAVE)
    assert len(ea_ratings.EA_TO_SAVE) == 55


def test_madden_fields_maps_stats_and_positions():
    items = ea_items()
    out = ea_ratings.madden_fields(items[0])
    assert out["position"] == "WR"
    assert out["archetype"] == "WR_DeepThreat"
    assert out["ratings"]["SpeedRating"] == items[0]["stats"]["speed"]["value"]
    assert out["ratings"]["BCVisionRating"] == items[0]["stats"]["bCVision"]["value"]
    assert out["ratings"]["OverallRating"] == items[0]["overallRating"]
    assert "RunningStyleRating" not in out["ratings"]      # an enum name, never guessed
    assert out["unmapped"] == ["runningStyle"]
    assert ea_ratings.madden_fields(items[1])["position"] == "LE"    # LEDG
    assert ea_ratings.madden_fields(items[2])["position"] == "MLB"   # MIKE


def test_madden_fields_reports_an_unknown_stat_key():
    item = copy.deepcopy(ea_items()[0])
    item["stats"]["clutch"] = {"value": 88, "diff": 0}
    out = ea_ratings.madden_fields(item)
    assert "clutch" in out["unmapped"]
    assert "ClutchRating" not in out["ratings"]


# ---------- index and find ----------

def test_find_matches_name_and_birthdate():
    idx = ea_ratings.index(ea_items())
    hit = ea_ratings.find(real("Avery Stone", birth_date="1999-05-04"), idx)
    assert hit["id"] == 1001


def test_find_name_only_when_ea_has_no_birthdate_and_position_agrees():
    idx = ea_ratings.index(ea_items())
    hit = ea_ratings.find(real("Blake Rivers", position="EDGE"), idx)
    assert hit["id"] == 1002
    assert ea_ratings.find(real("Blake Rivers", position="CB"), idx) is None


def test_find_refuses_a_birthdate_mismatch():
    idx = ea_ratings.index(ea_items())
    assert ea_ratings.find(real("Avery Stone", birth_date="2000-05-04"), idx) is None
    # Casey's EA birthdate is known, so a real player without one does not match on name alone.
    assert ea_ratings.find(real("Casey Wills", position="MLB"), idx) is None


# ---------- role template ----------

def test_role_template_uses_the_role_percentiles():
    players = [madden_player(pgid, "WR", ovr, {"Speed": speed, "Catching": catch})
               for pgid, ovr, speed, catch in [
                   (1, 60, 70, 60), (2, 64, 72, 62), (3, 68, 74, 64), (4, 72, 76, 66), (5, 76, 78, 68)]]
    players.append(madden_player(6, "WR", 99, {"Speed": 99, "Catching": 99}, status="Draft"))
    players.append(madden_player(7, "QB", 99, {"Speed": 99, "Catching": 99}))
    snapshot = {"players": players}

    starter = create.role_template(snapshot, "WR", "STARTER")
    backup = create.role_template(snapshot, "WR", "BACKUP")
    injured = create.role_template(snapshot, "WR", "IR")
    assert (starter["ratings"]["OverallRating"], starter["ratings"]["SpeedRating"]) == (68, 74)
    assert (backup["ratings"]["OverallRating"], backup["ratings"]["SpeedRating"]) == (64, 72)
    assert (injured["ratings"]["OverallRating"], injured["ratings"]["SpeedRating"]) == (62, 71)
    assert starter["archetype"] == "WR_DeepThreat"
    assert create.role_template({"players": []}, "WR", "STARTER") is None
    with pytest.raises(ValueError):
        create.role_template(snapshot, "WR", "PRO BOWL")


# ---------- donors ----------

def test_pick_donors_takes_lowest_ovr_free_agents_and_never_draft_or_rostered():
    snapshot = {"players": [free_agent(1, 75), free_agent(2, 60), free_agent(3, 55, status="Draft"),
                            free_agent(4, 50, status="Signed", team_index=0)]}
    assert create.pick_donors(snapshot, {}, 2, "WR") == [2, 1]


def test_pick_donors_prefers_the_position_then_fills_from_other_free_agents():
    snapshot = {"players": [free_agent(10, 80, "WR"), free_agent(11, 40, "CB")]}
    assert create.pick_donors(snapshot, {}, 2, "WR") == [10, 11]


def test_pick_donors_skips_links_to_people_on_a_2025_2026_roster():
    snapshot = {"players": [free_agent(10, 70), free_agent(11, 65), free_agent(12, 60)]}
    assert create.pick_donors(snapshot, {"00-active": 10}, 2, "WR") == [12, 11]
    assert create.pick_donors(snapshot, {10}, 2, "WR") == [12, 11]


def test_pick_donors_allows_a_link_to_a_person_with_no_roster_row():
    snapshot = {"players": [free_agent(10, 70), free_agent(11, 65)]}
    assert create.pick_donors(snapshot, {10: None}, 2, "WR") == [11, 10]
    assert create.pick_donors(snapshot, {10: "00-retired"}, 2, "WR") == [11]


# ---------- created edits ----------

def test_created_edit_uses_ea_ratings(monkeypatch):
    monkeypatch.setattr(create.cn, "commentary_id", lambda name: 1234)
    item = ea_items()[0]
    player = real("Avery Stone", birth_date="1999-05-04", height_in=72, weight_lbs=205,
                  college="Fixture College", entry_year=2021, jersey=11)
    out = create.created_edit(donor(), player, item, None)
    set_ = out["set"]

    assert out["source"] == "EA"
    assert out["donor_pgid"] == out["pgid"] == 900
    assert (set_["FirstName"], set_["LastName"]) == ("Avery", "Stone")
    assert set_["Age"] == 27
    assert set_["YearsPro"] == 5
    assert set_["Height"] == 72
    assert set_["Weight"] == 45                    # Madden stores pounds - 160
    assert set_["JerseyNum"] == 11
    assert set_["PLYR_COMMENT"] == 1234
    assert set_["SpeedRating"] == item["stats"]["speed"]["value"]
    assert set_["OverallRating"] == item["overallRating"]
    assert "College" not in set_                   # a reference field, not a Player set
    assert out["unmapped_ea_keys"] == ["runningStyle"]


def test_created_edit_maps_an_ea_position_code(monkeypatch):
    monkeypatch.setattr(create.cn, "commentary_id", lambda name: 0)
    player = real("Blake Rivers", position="EDGE", height_in=76, weight_lbs=265, entry_year=2023, jersey=91)
    out = create.created_edit(donor(901, "CB", "CB_ManToMan"), player, ea_items()[1], None)
    assert out["position"] == "LE"                 # EA LEDG
    assert out["set"]["Position"] == "LE"
    assert "Age" not in out["set"]                 # EA has no birthdate
    assert out["set"]["Weight"] == 105


def test_created_edit_falls_back_to_the_role_template(monkeypatch):
    monkeypatch.setattr(create.cn, "commentary_id", lambda name: 0)
    snapshot = {"players": [madden_player(pgid, "WR", 60 + i * 4, {"Speed": 70 + i * 2, "Catching": 60 + i * 2})
                            for i, pgid in enumerate(range(1, 6))]}
    template = create.role_template(snapshot, "WR", "STARTER")
    player = real("Drew Hart", position="QB", birth_date="2004-02-02", height_in=75, weight_lbs=220)
    out = create.created_edit(donor(), player, None, template)

    assert out["source"] == "TEMPLATE"
    assert out["position"] == "QB"                 # real position, not the template's
    assert out["set"]["Position"] == "QB"
    assert out["set"]["SpeedRating"] == template["ratings"]["SpeedRating"]
    assert out["set"]["OverallRating"] == template["ratings"]["OverallRating"]
    assert out["set"]["Age"] == 22


def test_created_edit_needs_a_source():
    with pytest.raises(ValueError):
        create.created_edit(donor(), real("Drew Hart", position="QB"), None, None)
