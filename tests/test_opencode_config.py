"""Offline checks for the project OpenCode config and the `/offline` repo-qa agent.

These tests only read files: no OpenCode session is started and nothing on the network is used.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".opencode" / "opencode.json"
AGENT_PATH = ROOT / ".opencode" / "agents" / "repo-qa.md"
OFFLINE_SKILL_PATH = ROOT / ".claude" / "skills" / "offline" / "SKILL.md"

EXPECTED_MODEL = "opencode-go/deepseek-v4.1-flash"
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
)


def _unquote(value):
    """Strip one matching pair of single or double quotes from a scalar."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text):
    """Parse the small YAML subset used by agent frontmatter: scalars and lists of mappings.

    Returns (scalars, lists). Nested lists are handled one level deep, which is all the agent
    files use (``permissions:`` followed by ``- action: ...`` items). No YAML dependency.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError("frontmatter does not start with '---'")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise AssertionError("frontmatter is not closed with '---'")

    scalars, lists = {}, {}
    body = lines[1:end]
    i = 0
    while i < len(body):
        line = body[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            raise AssertionError(f"unexpected indented line in frontmatter: {line!r}")
        key, sep, rest = line.partition(":")
        if not sep:
            raise AssertionError(f"frontmatter line is not 'key: value': {line!r}")
        key, rest = key.strip(), rest.strip()
        i += 1
        if rest:
            scalars[key] = _unquote(rest)
            continue
        items, current = [], None
        while i < len(body) and (body[i].startswith(" ") or body[i].startswith("\t")):
            item = body[i].strip()
            if not item or item.startswith("#"):
                i += 1
                continue
            if item.startswith("- "):
                current = {}
                items.append(current)
                item = item[2:].strip()
            if current is None:
                raise AssertionError(f"list item without a '-' under {key!r}: {item!r}")
            item_key, item_sep, item_value = item.partition(":")
            if not item_sep:
                raise AssertionError(f"list item is not 'key: value': {item!r}")
            current[item_key.strip()] = _unquote(item_value.strip())
            i += 1
        lists[key] = items
    return scalars, lists


@pytest.fixture(scope="module")
def config():
    assert CONFIG_PATH.is_file(), f"missing {CONFIG_PATH}"
    return json.loads(CONFIG_PATH.read_text())


@pytest.fixture(scope="module")
def agent_frontmatter():
    assert AGENT_PATH.is_file(), f"missing {AGENT_PATH}"
    return parse_frontmatter(AGENT_PATH.read_text())


def test_opencode_config_pins_the_model(config):
    assert config["$schema"] == "https://opencode.ai/config.json"
    assert config["model"] == EXPECTED_MODEL


def test_opencode_config_keeps_the_planned_shape(config):
    assert set(config) == {"$schema", "model"}


def test_opencode_config_parses_as_json():
    # json.loads already ran in the fixture; assert the file is plain JSON, not JSONC.
    text = CONFIG_PATH.read_text()
    assert "/*" not in text
    assert not re.search(r"^\s*//", text, re.MULTILINE)
    assert json.loads(text)["model"] == EXPECTED_MODEL


def test_repo_qa_agent_is_a_subagent_without_a_model_pin(agent_frontmatter):
    scalars, _lists = agent_frontmatter
    assert scalars.get("name") == "repo-qa"
    assert scalars.get("mode") == "subagent"
    assert "description" in scalars and scalars["description"]
    assert "model" not in scalars


def test_repo_qa_agent_permissions_are_read_only(agent_frontmatter):
    _scalars, lists = agent_frontmatter
    rules = lists.get("permissions")
    assert rules, "repo-qa has no permissions list"

    # Last matching rule wins, so deny-all has to come first.
    assert rules[0] == {"action": "*", "resource": "*", "effect": "deny"}
    assert [r["action"] for r in rules[1:]] == ["read", "glob", "grep"]
    for rule in rules[1:]:
        assert rule["resource"] == "*"
        assert rule["effect"] == "allow"

    allowed = {r["action"] for r in rules if r["effect"] == "allow"}
    assert allowed == {"read", "glob", "grep"}
    assert not allowed & {"shell", "bash", "edit", "write", "patch", "webfetch", "task"}


def test_offline_skill_names_both_agent_paths():
    text = OFFLINE_SKILL_PATH.read_text()
    assert ".claude/agents/repo-qa.md" in text
    assert ".opencode/agents/repo-qa.md" in text
    # The "agent not found" fallback survives.
    assert 'subagent_type: "general-purpose"' in text


@pytest.mark.parametrize("path", [CONFIG_PATH, AGENT_PATH], ids=["opencode.json", "repo-qa.md"])
def test_config_files_carry_no_secret(path):
    text = path.read_text()
    for pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        assert match is None, f"{path.name} looks like it contains a secret: {match.group(0)[:4]}…"
