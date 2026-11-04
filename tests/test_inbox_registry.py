"""Inbox kind registry (FRONT_OFFICE_INBOX_PLAN.md §2.7; U1): auto-discovery from a package, modules without KIND
ignored, duplicate KIND raises, missing ACTOR_ROLES -> once per team with its first actor."""
import sys
import textwrap
import uuid

import pytest

from engines.inbox import registry
from engines.inbox.actors import Actor


def make_package(tmp_path, monkeypatch, modules):
    name = "inbox_kinds_" + uuid.uuid4().hex[:8]
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for mod, body in modules.items():
        (pkg / f"{mod}.py").write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


CLASS_KIND = """
    from engines.inbox.registry import Trigger
    class GmTalk:
        KIND = "GM_TALK"
        COUNTERPART_TYPE = "gm"
        ACTOR_ROLES = ("GM",)
        def scan(self, cur, season, week, team): return []
        def options(self, cur, thread, last): return []
        def act(self, cur, thread, option_id, payload, note): return None
        def template(self, intent, facts): return "ok"
        def start(self, cur, team, body): return None
"""

MODULE_KIND = """
    KIND = "PRESS"
    COUNTERPART_TYPE = "system"
    def scan(cur, season, week, team): return []
    def template(intent, facts): return "Press is ready."
"""

HELPER = """
    def helper(): return 1
"""


def test_discovers_class_and_module_kinds_and_ignores_helpers(tmp_path, monkeypatch):
    pkg = make_package(tmp_path, monkeypatch, {"gm_talk": CLASS_KIND, "press": MODULE_KIND, "_shared": HELPER,
                                                "util": HELPER})
    found = registry.discover(pkg)
    assert sorted(found) == ["GM_TALK", "PRESS"]
    assert found["GM_TALK"].template("x", {}) == "ok"
    assert found["PRESS"].template("x", {}) == "Press is ready."


def test_duplicate_kind_raises(tmp_path, monkeypatch):
    pkg = make_package(tmp_path, monkeypatch, {"a": CLASS_KIND, "b": CLASS_KIND})
    with pytest.raises(registry.RegistryError, match="GM_TALK"):
        registry.discover(pkg)


def test_kinds_is_cached_and_refreshable(tmp_path, monkeypatch):
    pkg = make_package(tmp_path, monkeypatch, {"press": MODULE_KIND})
    first = registry.kinds(pkg)
    assert list(first) == ["PRESS"]
    (tmp_path / pkg / "gm_talk.py").write_text(textwrap.dedent(CLASS_KIND))
    assert list(registry.kinds(pkg)) == ["PRESS"]
    assert sorted(registry.kinds(pkg, refresh=True)) == ["GM_TALK", "PRESS"]


def test_real_kinds_package_imports():
    assert isinstance(registry.kinds(refresh=True), dict)


def test_actor_roles_and_scan_targets():
    gm = Actor(1, "CAR", "GM", "10", "Dan Morgan")
    coach = Actor(1, "CAR", "COACH", "41", "Dave Canales")

    class GmOnly:
        KIND, ACTOR_ROLES = "SCOUT", ("GM",)

    class Both:
        KIND, ACTOR_ROLES = "REPORTER", ("GM", "COACH")

    class Undeclared:
        KIND = "PRESS"

    assert registry.actors_to_scan(GmOnly(), [gm, coach]) == [gm]
    assert registry.actors_to_scan(GmOnly(), [coach]) == []
    assert registry.actors_to_scan(Both(), [gm, coach]) == [gm, coach]
    assert registry.actors_to_scan(Undeclared(), [coach, gm]) == [coach]      # once per team, first actor
    assert registry.actors_to_scan(Undeclared(), []) == []
    assert registry.serves(Undeclared(), "PLAYER") and not registry.serves(GmOnly(), "COACH")


def test_trigger_and_actresult_defaults():
    t = registry.Trigger("CONTRACT", 1, "player", "100", "Bryce Young", "Extension", "ext:100", {"a": 1}, "OPEN")
    assert t.actor_role is None and t.state is None and t.link_table is None
    r = registry.ActResult({"a": 1}, "COUNTER", None)
    assert r.judge is True and r.close is False and r.state is None
