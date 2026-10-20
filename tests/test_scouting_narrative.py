"""Scouting prose behind the Gemini seam (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md Phase 5). Pure: fake LLMs."""
from unittest import mock

from engines import scouting_engine as se
from engines import scouting_narrative as sn
from engines import scouting_value as sv

P = {"key": "2037:JAMARBODDEN:CB", "full_name": "JAMAR BODDEN", "position": "CB", "school": sv.school("ILLINOIS"),
     "college": "ILLINOIS", "class_year": "senior", "height_in": 73, "weight": 195, "age": 22}
EV = {"kind": "GAME", "payload": {"opponent": "Penn State", "stats": {"targets": 6, "catches_allowed": 2, "pbu": 3,
                                                                       "int": 1}, "_production_pct": 88.0},
      "headline": "Jamar Bodden shines vs Penn State",
      "blurb": "Illinois cornerback Jamar Bodden: 2 catches allowed on 6 targets, 3 breakups, 1 INT."}
SHEET = sn.fact_sheet(EV, P)


def ok(text):
    return sn.validate_scout_text(text, SHEET)


def test_validator_rejects_invented_facts():
    assert not ok("Bodden allowed 4 catches on 6 targets.")[0]                       # invented number
    assert not ok("Bodden shut down Ohio State's top receiver.")[0]                  # invented school
    assert not ok("The 6-3 corner was everywhere.")[0]                               # wrong height (6-1)
    assert not ok("Bodden is out for the bowl game with a torn ACL.")[0]              # ongoing injury
    assert not ok("A 74 OVR corner with range.")[0] and not ok("His overall rating jumped.")[0]
    assert not ok("Bodden was suspended for a game.")[0]
    nfl = {"schools": {"ILLINOIS", "PENN STATE", "OHIO STATE"}, "teams": {"BEARS"}}
    assert not sn.validate_scout_text("The Bears loved it.", SHEET, nfl)[0]          # invented NFL team


def test_validator_accepts_colour():
    good = ("Bodden smothered Penn State's receivers: 2 catches allowed on 6 targets, 3 breakups and 1 INT. The "
            "6-1 Illinois senior plays with patience and mirrors routes like a veteran.")
    assert ok(good) == (True, None)
    assert ok("Scouts love his footwork and his competitive streak.") == (True, None)


def test_texas_inside_texas_tech_is_the_sheets_school():
    s = sn.fact_sheet(dict(EV, payload={"opponent": "Baylor"}), dict(P, school=sv.school("TEXAS TECH")))
    assert sn.validate_scout_text("The Texas Tech corner handled Baylor.", s)[0]


def test_client_error_falls_back_to_template():
    def boom(prompt):
        raise RuntimeError("quota")
    text, src, why = sn.write_text(SHEET, "p", boom)
    assert (text, src) == (EV["blurb"], "TEMPLATE")
    text, src, why = sn.write_text(SHEET, "p", lambda p: "Bodden picked off 5 passes.")      # rejected
    assert (text, src, why) == (EV["blurb"], "TEMPLATE", "number 5 not on the sheet")
    assert sn.write_text(SHEET, "p", None)[1] == "TEMPLATE"


def test_write_notes_marks_llm_text_and_caps_count():
    cls = [dict(P, key=f"2037:P{i}:CB", full_name=f"PLAYER {chr(65 + i)}", V=80 - i, true_rank=i + 1, status="ACTIVE")
           for i in range(12)]
    cons = {p["key"]: {"rank": i + 1, "move": 1.0 + i / 10, "err": 0.0} for i, p in enumerate(cls)}
    events = {p["key"]: [dict(EV, blurb="template")] for p in cls}
    out = {"cls": cls, "cons": cons, "events": events}
    n = sn.write_notes(out, lambda prompt: "Scouts love his footwork.", log=lambda *a: None)
    assert n == sn.MAX_NOTES
    srcs = [e[0].get("text_source") for e in events.values()]
    assert srcs.count("LLM") == sn.MAX_NOTES
    assert sn.write_notes(out, None) == 0


def test_stored_text_is_not_rewritten_on_rerun():
    llm = mock.MagicMock(return_value="Scouts love his footwork.")
    cur = mock.MagicMock()
    with mock.patch.object(se, "current_step", return_value=(2036, 5)), \
         mock.patch.object(se, "stored_steps", return_value=[4, 5]):
        assert se.run(cur, 2037, 5, log=lambda *a: None, llm=llm) is None
    llm.assert_not_called()


def test_draft_rationale_fallback():
    sheet = {"gm": "GM Smith", "name": "Jamar Bodden", "school": "Illinois", "position": "cornerback",
             "grade_label": "1st round", "need_group": "CB", "voice": "blunt"}
    r = sn.draft_rationale(sheet, lambda p: "We took him at 74 overall.")
    assert r["source"] == "TEMPLATE" and r["rejected"]
    r = sn.draft_rationale(sheet, lambda p: "Bodden is the cornerback we wanted all along. Illinois tape doesn't lie.")
    assert r["source"] == "LLM"
