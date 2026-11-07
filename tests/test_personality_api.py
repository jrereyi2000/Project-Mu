"""server/personality_api.py (docs/done/PERSONALITY_DISPLAY_PLAN.md §2.1, §4): the Personality panel's
profile read, catalogue and JR's override, against fake sources and real pure core profiles. No database."""
import unittest
from copy import deepcopy
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from newsroom import personas as legacy
from personality import core
from server import personality_api as pa

UNIVERSE = "test-universe"


def generated(role="player", key="p:4411"):
    return core.initialize(core.Identity(UNIVERSE, role, key), "generated", 2026)


def person(role, pid):
    return {"kind": "person", "universe_id": UNIVERSE, "role": role, "persona_id": pid}


NAMES = {"coach-12": {"kind": "coach", "ref": "12", "name": "Dave Canales"},
         "player-9": {"kind": "player", "ref": "9", "name": "A Teammate"},
         "player-8": {"kind": "player", "ref": "8", "name": "B Teammate"},
         "gm-3": {"kind": "gm", "ref": "3", "name": "Dan Morgan"}}


class FakeSources:
    """The Sources interface over in-memory data. Any block can be made unavailable by name."""

    def __init__(self, *, kind="player", profile=None, status="ok", missing=(), context=None, mood=None,
                 events=None, projection=None, notes=None, history=None, team=None):
        self.kind, self.status, self.missing = kind, status, set(missing)
        role = pa.KINDS[kind][0]
        self.profile = profile if profile is not None else generated(role)
        self.identity = core.Identity(UNIVERSE, role, self.profile["birth_key"])
        self.subject = person(role, self.identity.persona_id)
        self.context = context if context is not None else {"status": "enabled", "edges": []}
        self.mood_value = mood if mood is not None else {"primary": "frustrated", "secondary": ["uncertain"]}
        self.events = events or []
        self.projection = projection
        self.notes = notes if notes is not None else ["2029: Since the title he talks more about legacy than rings."]
        self.team = team if team is not None else {"id": 5, "abbr": "CAR"}
        self.overrides, self.commits = [], 0

    def _gate(self, block):
        if block in self.missing:
            raise pa.Unavailable(f"{block} not built")

    def resolve(self, kind, ref):
        if self.status != "ok":
            return {"status": self.status}
        return {"status": "ok", "identity": self.identity, "subject": self.subject, "profile": self.profile,
                "name": "Bryce Young", "team": self.team, "kind": kind}

    def catalogue(self):
        return legacy.load_traits()

    def clock(self):
        return (2029, 8)

    def change_notes(self, identity):
        self._gate("changes")
        return list(self.notes)

    def relationship_events(self, subject):
        self._gate("promises")
        return list(self.events)

    def relationship_context(self, subject):
        self._gate("relationships")
        return self.context

    def mood(self, subject, profile):
        self._gate("mood")
        return self.mood_value

    def trade_request(self, subject):
        self._gate("trade_request")
        return self.projection

    def name_of(self, ref):
        return NAMES.get(f"{ref.get('role')}-{ref.get('persona_id')}")

    def override(self, identity, request):
        self.overrides.append(request)
        after, _ = core.apply_override(self.profile, request)
        self.profile = after
        return after

    def commit(self):
        self.commits += 1


def client(src):
    app = FastAPI()
    app.include_router(pa.router)
    app.dependency_overrides[pa.get_sources] = lambda: src
    return TestClient(app)


def edge(role, pid, trust=None, breaches=(), ties=()):
    return {"target": person(role, pid), "status": "known", "trust": trust, "breaches": list(breaches),
            "ties": list(ties), "attachment": {"tier": "unknown", "eligible": False}}


def team_edge(team_id, tier):
    return {"target": {"kind": "team", "universe_id": UNIVERSE, "team_key": f"team:{team_id}"}, "status": "known",
            "trust": None, "breaches": [], "ties": [], "attachment": {"tier": tier, "eligible": tier != "none"}}


def numbers(value, path=""):
    """Every (path, number) in a JSON body; bools aren't numbers."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [(path, value)]
    if isinstance(value, dict):
        return [x for k, v in value.items() for x in numbers(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [x for i, v in enumerate(value) for x in numbers(v, f"{path}[]")]
    return []


ALLOWED_NUMBERS = {".sliders[].value", ".revision", ".trade_request.public_since.season",
                   ".trade_request.public_since.tick"}


class ShapeTest(unittest.TestCase):
    def test_get_shape_for_each_kind(self):
        for kind in pa.KINDS:
            src = FakeSources(kind=kind)
            r = client(src).get(f"/api/personality/{kind}/x1")
            self.assertEqual(r.status_code, 200, kind)
            body = r.json()
            for key in ("kind", "ref", "name", "origin", "editable", "sliders", "traits", "mood", "changes", "ties",
                        "attachment", "promises", "trade_request", "unavailable"):
                self.assertIn(key, body, f"{kind}: {key}")
            self.assertEqual(body["kind"], kind)
            self.assertEqual(len(body["sliders"]), 6)
            self.assertTrue(body["editable"])
            self.assertEqual(body["mood"], {"primary": "frustrated", "secondary": ["uncertain"]})
            self.assertEqual(body["changes"], src.notes)
            for t in body["traits"]:
                self.assertTrue(t["name"] and t["category"] and t["tendency"])
            if kind != "player":
                self.assertIsNone(body["trade_request"])
                self.assertNotIn("trade_request", body["unavailable"])

    def test_unknown_kind_404(self):
        r = client(FakeSources()).get("/api/personality/janitor/1")
        self.assertEqual(r.status_code, 404)

    def test_prospect_hidden_until_drafted(self):
        r = client(FakeSources(status="hidden_until_drafted")).get("/api/personality/player/77")
        self.assertEqual((r.status_code, r.json()["detail"]), (404, "hidden_until_drafted"))

    def test_unresolved_is_no_profile(self):
        r = client(FakeSources(kind="coach", status="no_profile")).get("/api/personality/coach/77")
        self.assertEqual((r.status_code, r.json()["detail"]), (404, "no_profile"))

    def test_slider_sentences_at_band_edges(self):
        rows = {s: pa.slider_rows({"temperament": v})[0]["sentence"] for s, v in
                (("v0", 0), ("v3", 3), ("v4", 4), ("v6", 6), ("v7", 7), ("v10", 10))}
        low, high = legacy.SLIDER_WORDS["temperament"]
        self.assertEqual(rows, {"v0": low, "v3": low, "v4": None, "v6": None, "v7": high, "v10": high})
        row = pa.slider_rows({"temperament": 7})[0]
        self.assertEqual((row["low"], row["high"], row["value"]), (low, high, 7))

    def test_inferred_flag_from_provenance(self):
        ev = {"slider:warmth": {"source": "inferred:slider-inference-v1", "confidence": "low"},
              "slider:drive": {"source": "https://example.com/a", "confidence": "high"},
              "slider:openness": {"source": "jr-set:2026-11-07", "confidence": "high"}}
        rows = {r["name"]: r["inferred"] for r in pa.slider_rows(
            {s: 5 for s in legacy.SLIDERS}, ev)}
        self.assertEqual(rows, {"temperament": False, "sociability": False, "confidence": False,
                                "openness": False, "warmth": True, "drive": False})
        self.assertTrue(all(r["inferred"] is False for r in pa.slider_rows({"warmth": 5})))

    def test_inferred_flag_in_body_adds_no_numbers(self):
        class Inferred(FakeSources):
            def resolve(self, kind, ref):
                out = super().resolve(kind, ref)
                if out.get("profile") is not None:
                    out = dict(out, profile=dict(out["profile"], evidence={
                        "slider:warmth": {"source": "inferred:slider-inference-v1", "confidence": "low"}}))
                return out
        body = client(Inferred(kind="coach")).get("/api/personality/coach/x1").json()
        flags = {s["name"]: s["inferred"] for s in body["sliders"]}
        self.assertTrue(flags["warmth"])
        self.assertEqual(sum(flags.values()), 1)
        stray = [(p, v) for p, v in numbers(body) if p not in ALLOWED_NUMBERS]
        self.assertEqual(stray, [])

    def test_legacy_profile_is_read_only(self):
        class Legacy(FakeSources):
            def resolve(self, kind, ref):
                return {"status": "ok", "identity": None, "name": "Bill Barnwell", "team": None,
                        "profile": {"origin": "legacy", "sliders": dict.fromkeys(legacy.SLIDERS, 5),
                                    "traits": ["FILM_JUNKIE"], "revision": None}}
        body = client(Legacy(kind="writer")).get("/api/personality/writer/BARNWELL").json()
        self.assertFalse(body["editable"])
        self.assertEqual(body["origin"], "legacy")
        self.assertEqual(set(body["unavailable"]), set(pa.BLOCKS))
        self.assertEqual(body["traits"][0]["id"], "FILM_JUNKIE")


class TieTest(unittest.TestCase):
    def test_labels_by_band(self):
        cases = [(0.5, "trusts"), (0.9, "trusts"), (0.15, "on good terms with"), (0.49, "on good terms with"),
                 (0.14, None), (0.0, None), (-0.14, None), (-0.15, "wary of"), (-0.49, "wary of"),
                 (-0.5, "at odds with"), (-1.0, "at odds with"), (None, None)]
        for trust, label in cases:
            self.assertEqual(pa.trust_label(trust), label, trust)

    def test_breach_is_grudge_and_mentorship(self):
        self.assertEqual(pa.tie_label(edge("coach", "12", trust=0.6, breaches=[{"source_id": "s"}])),
                         "holds a grudge against")
        self.assertEqual(pa.tie_label(edge("player", "9", ties=[{"kind": "mentorship", "direction": "mentor"}])),
                         "mentor to")
        self.assertEqual(pa.tie_label(edge("player", "9", ties=[{"kind": "mentorship", "direction": "mentee"}])),
                         "mentored by")

    def test_neutral_omitted_strongest_first_and_capped(self):
        ctx = {"edges": [edge("player", "9", trust=0.05), edge("coach", "12", trust=0.2),
                         edge("player", "8", trust=-0.9), edge("gm", "3", trust=0.3, breaches=[{"source_id": "x"}])]}
        ties = pa.ties_block(ctx, FakeSources().name_of)
        self.assertEqual([(t["label"], t["target"]["name"]) for t in ties],
                         [("holds a grudge against", "Dan Morgan"), ("at odds with", "B Teammate"),
                          ("on good terms with", "Dave Canales")])
        many = {"edges": [edge("coach", "12", trust=0.6)] * 10}
        self.assertEqual(len(pa.ties_block(many, FakeSources().name_of)), pa.MAX_TIES)

    def test_attachment_labels(self):
        src = FakeSources(context={"status": "enabled", "edges": [team_edge(5, "strong"), team_edge(6, "weak")]})
        body = client(src).get("/api/personality/player/1").json()
        self.assertEqual(body["attachment"], {"team": "CAR", "label": "deeply attached"})
        for tier, label in (("weak", "settling in"), ("established", "attached"), ("none", None)):
            got = pa.attachment_block({"edges": [team_edge(5, tier)]}, {"id": 5, "abbr": "CAR"})
            self.assertEqual(got and got["label"], label)

    def test_promises_open_only(self):
        src = FakeSources()
        me = src.subject
        coach = person("coach", "12")
        made = {"kind": "promise_made", "payload": {"promise_id": "p1", "issuer": coach, "beneficiary": me,
                                                    "terms": {"description": "WR1 by week 9"}}}
        kept = {"kind": "promise_made", "payload": {"promise_id": "p2", "issuer": coach, "beneficiary": me,
                                                    "terms": {"description": "more snaps"}}}
        done = {"kind": "promise_resolved", "payload": {"promise_id": "p2", "resolution": "kept"}}
        src.events = [made, kept, done]
        body = client(src).get("/api/personality/player/1").json()
        self.assertEqual(body["promises"], [{"direction": "made_to_him", "by": {"kind": "coach", "name": "Dave Canales",
                                                                             "ref": "12"},
                                             "text": "WR1 by week 9", "status": "open"}])

    def test_public_trade_request(self):
        src = FakeSources(projection={"request_id": "r", "persona_id": "x", "team_id": 5, "status": "ACTIVE",
                                      "as_of_ordinal": 2029 * 1000 + 8, "current": True})
        body = client(src).get("/api/personality/player/1").json()
        self.assertEqual(body["trade_request"], {"status": "ACTIVE", "public_since": {"season": 2029, "tick": 8},
                                                 "current": True})


class AvailabilityTest(unittest.TestCase):
    def test_missing_modules_give_unavailable(self):
        src = FakeSources(missing={"changes", "promises", "relationships", "mood", "trade_request"})
        r = client(src).get("/api/personality/player/1")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for block in pa.BLOCKS:
            self.assertIsNone(body[block], block)
            self.assertIn(block, body["unavailable"])
        self.assertEqual(len(body["sliders"]), 6)

    def test_production_relationships_off_without_policy(self):
        self.assertIsNone(pa.RELATIONSHIP_POLICY)
        s = pa.Sources.__new__(pa.Sources)
        with self.assertRaises(pa.Unavailable):
            s.relationship_context({})
        with self.assertRaises(pa.Unavailable):
            s.mood({}, {})

    def test_no_numbers_outside_sliders(self):
        ctx = {"status": "enabled", "edges": [edge("coach", "12", trust=0.73), edge("player", "8", trust=-0.61),
                                              team_edge(5, "established")]}
        src = FakeSources(context=ctx, projection={"request_id": "r", "persona_id": "x", "team_id": 5,
                                                   "status": "ACTIVE", "as_of_ordinal": 2029008, "current": True})
        body = client(src).get("/api/personality/player/1").json()
        stray = [(p, v) for p, v in numbers(body) if p not in ALLOWED_NUMBERS]
        self.assertEqual(stray, [])
        self.assertNotIn("0.73", str(body))
        self.assertNotIn("team_id", str(body))


class CatalogueTest(unittest.TestCase):
    def test_role_eligibility(self):
        cat = legacy.load_traits()
        for role in ("player", "coach", "coordinator", "gm", "owner", "writer", "scout"):
            body = client(FakeSources()).get(f"/api/personality/catalogue?role={role}").json()
            core_role = pa.KINDS[role][0]
            ids = {t["id"] for t in body["traits"]}
            self.assertEqual(ids, {k for k, v in cat.items() if v.get("roles", {}).get(core_role, 0) > 0}, role)
            self.assertEqual(body["slider_band"], {"min": 24, "max": 36})
            for t in body["traits"]:
                self.assertIsInstance(t["conflicts"], list)

    def test_unknown_role(self):
        self.assertEqual(client(FakeSources()).get("/api/personality/catalogue?role=janitor").status_code, 422)


class OverrideTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("pipeline.notify.env", side_effect=lambda k: "tok" if k == "DEBUG_TOKEN" else None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _edit(self, profile):
        sliders = dict(profile["sliders"])
        name = next(s for s in legacy.SLIDERS if sliders[s] < 10)
        sliders[name] += 1
        return name, sliders[name]

    def test_token_required(self):
        src = FakeSources()
        c = client(src)
        self.assertEqual(c.post("/api/personality/player/1/override", json={"reason": "x"}).status_code, 403)
        self.assertEqual(c.post("/api/personality/player/1/override", json={"reason": "x"},
                                headers={"X-Debug-Token": "nope"}).status_code, 403)
        self.assertEqual(src.overrides, [])

    def test_success_returns_new_body(self):
        src = FakeSources()
        name, value = self._edit(src.profile)
        r = client(src).post("/api/personality/player/1/override", headers={"X-Debug-Token": "tok"},
                             json={"sliders": {name: value}, "reason": "note: he grew up"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(next(s["value"] for s in body["sliders"] if s["name"] == name), value)
        self.assertEqual(body["revision"], 1)
        req = src.overrides[0]
        self.assertEqual((req["actor"], req["season"], req["tick"], req["expected_revision"]), ("JR", 2029, 8, 0))
        self.assertEqual(src.commits, 1)

    def test_stale_revision_409(self):
        src = FakeSources()
        name, value = self._edit(src.profile)
        r = client(src).post("/api/personality/player/1/override", headers={"X-Debug-Token": "tok"},
                             json={"sliders": {name: value}, "reason": "x", "expected_revision": 5})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(src.commits, 0)

    def test_invalid_422(self):
        src = FakeSources()
        c = client(src)
        h = {"X-Debug-Token": "tok"}
        self.assertEqual(c.post("/api/personality/player/1/override", headers=h,
                                json={"sliders": {"temperament": 11}, "reason": "x"}).status_code, 422)
        self.assertEqual(c.post("/api/personality/player/1/override", headers=h,
                                json={"add_traits": ["NOT_A_TRAIT"], "reason": "x"}).status_code, 422)
        name, value = self._edit(src.profile)
        self.assertEqual(c.post("/api/personality/player/1/override", headers=h,
                                json={"sliders": {name: value}, "reason": "  "}).status_code, 422)
        all_ten = dict.fromkeys(legacy.SLIDERS, 10)    # total 60, outside the 24-36 band
        r = c.post("/api/personality/player/1/override", headers=h, json={"sliders": all_ten, "reason": "x"})
        self.assertEqual(r.status_code, 422)
        self.assertTrue(r.json()["detail"])
        self.assertEqual(src.commits, 0)

    def test_hidden_and_legacy_refused(self):
        h = {"X-Debug-Token": "tok"}
        r = client(FakeSources(status="hidden_until_drafted")).post("/api/personality/player/1/override",
                                                                     headers=h, json={"reason": "x"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
