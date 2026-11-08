"""Offline tests for the press-conference corpus tool (U1).

The fixtures are synthetic transcripts written for these tests. No real
transcript text is committed anywhere in this repository.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import press_corpus as pc

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "press_corpus"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Turn splitting
# ---------------------------------------------------------------------------

def test_parse_asap_style_transcript():
    t = pc.parse_transcript(_read("asap_loss.txt"))
    assert t["meta"]["team"] == "ABC"
    assert t["meta"]["opponent"] == "XYZ"
    assert t["meta"]["result"] == "L"
    assert "loss" in t["meta"]["tags"]
    assert "blowout_loss" in t["meta"]["tags"]
    assert t["meta"]["season"] == "2024"
    assert t["opening"].startswith("We did not play well enough")
    # The "(Indiscernible.)" artifact is not a question.
    assert len(t["pairs"]) == 4
    assert [p["type"] for p in t["pairs"]] == [
        "INJURY",
        "COACHING_DECISION",
        "CLARIFICATION",
        "EMOTION_STORY",
    ]
    assert t["pairs"][2]["follow_up"] is True
    assert t["pairs"][0]["follow_up"] is False


def test_parse_explicit_qa_transcript():
    t = pc.parse_transcript(_read("bare_qa.txt"))
    assert t["format"] == "explicit Q/A markers"
    assert t["meta"]["tags"] == []
    assert len(t["pairs"]) == 3
    assert t["pairs"][0]["type"] == "TEAM_EXECUTION"
    assert t["pairs"][2]["type"] == "OPPONENT_NEXT"
    assert all(p["answer"] for p in t["pairs"])


def test_parse_question_mark_heuristic_transcript():
    t = pc.parse_transcript(_read("heuristic_win.txt"))
    assert t["format"] == "question-mark heuristic"
    assert "We executed better" in t["opening"]
    assert len(t["pairs"]) == 3
    assert t["pairs"][0]["reporter"] == "Ben Fixture"
    assert t["pairs"][1]["type"] == "INJURY"
    assert t["pairs"][2]["type"] == "EMOTION_STORY"
    # A January date is a playoff game; the tag is inferred from the date.
    assert "playoff" in t["meta"]["tags"]
    assert "win" in t["meta"]["tags"]


def test_overtime_metadata_and_tags():
    t = pc.parse_transcript(_read("asap_overtime.txt"))
    assert t["meta"]["result"] == "W"
    assert "overtime" in t["meta"]["tags"]
    assert "win" in t["meta"]["tags"]


def test_score_line_is_not_a_turn():
    t = pc.parse_transcript(_read("asap_loss.txt"))
    assert t["game_line"] == "ABC 13, XYZ 30"
    assert all("ABC 13" not in p["question"] for p in t["pairs"])


# ---------------------------------------------------------------------------
# Classification and follow-up detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question,expected",
    [
        ("How is your quarterback's ankle?", "INJURY"),
        ("Why did you go for it on fourth down?", "COACHING_DECISION"),
        ("How do you feel about the win?", "EMOTION_STORY"),
        ("What is the plan for next week?", "OPPONENT_NEXT"),
        ("How do you fix the protection?", "SCHEME_ADJUSTMENT"),
        ("Anything else?", "CLARIFICATION"),
    ],
)
def test_classify_question(question, expected):
    assert pc.classify_question(question)["type"] == expected


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("I'm proud of the guys; they fought all night.", "CREDIT_PLAYERS"),
        ("That's on me. I have to be better.", "TAKE_BLAME"),
        ("We'll look at the tape and evaluate it.", "DEFLECT"),
        ("We had three turnovers and it was not good enough.", "ACCOUNTABILITY"),
        ("We changed the protection and the coverage in the second half.", "SCHEME_TECHNICAL"),
    ],
)
def test_classify_answer(answer, expected):
    assert pc.classify_answer(answer)["type"] == expected


def test_follow_up_detection():
    prev_q = "Why did you go for it on fourth down?"
    prev_a = "We wanted to keep the drive alive."
    assert pc.is_follow_up("Why?", prev_q, prev_a) is True
    assert pc.is_follow_up("Follow up on that look, who made the call?", prev_q, prev_a) is True
    assert pc.is_follow_up(
        "How is the receiver's hamstring?",
        "What did you see on the touchdown drive?",
        "It was a great block by the tight end.",
    ) is False


def test_noise_question_filter():
    assert pc.is_noise_question("(Indiscernible.)") is True
    assert pc.is_noise_question("(Off microphone)") is True
    assert pc.is_noise_question("(Regarding Kyler.)") is True
    assert pc.is_noise_question("Why?") is False
    assert pc.is_noise_question("What happened on the third down?") is False


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def test_aggregate_splits_by_result():
    stats = pc.aggregate(pc.load_transcripts(FIXTURES))
    assert stats["corpus"]["transcripts"] == 4
    assert stats["corpus"]["questions"] == 12
    assert stats["corpus"]["teams"] == ["ABC"]
    assert stats["corpus"]["seasons"] == ["2024"]

    by = stats["by_result"]
    assert by["all"]["transcripts"] == 4
    assert by["win"]["transcripts"] == 2
    assert by["loss"]["transcripts"] == 1
    assert by["blowout_loss"]["transcripts"] == 1
    assert by["overtime"]["transcripts"] == 1
    assert by["playoff"]["transcripts"] == 1
    assert by["loss"]["questions"] == 4
    assert by["overtime"]["questions"] == 2

    overall = stats["overall"]
    assert overall["questions_per_presser"]["mean"] == 3.0
    assert round(sum(overall["question_type_shares"].values()), 6) == 1.0
    assert "INJURY" in stats["taxonomy"]
    assert "LOCAL_BEAT_REPORTER" in stats["taxonomy"]["INJURY"]["archetypes"]
    assert "CREDIT_PLAYERS" in stats["answer_taxonomy"]
    assert round(sum(overall["answer_type_shares"].values()), 6) == 1.0
    assert all(p["answer_type"] for p in pc.parse_transcript(_read("asap_loss.txt"))["pairs"])


def test_stats_command_writes_json(tmp_path, capsys):
    out = tmp_path / "press_corpus_stats.json"
    rc = pc.main(["stats", "--transcripts", str(FIXTURES), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["corpus"]["transcripts"] == 4
    assert data["overall"]["questions"] == 12
    assert data["taxonomy"]["INJURY"]["description"]
    assert data["method"]["follow_up"]
    assert "wrote" in capsys.readouterr().out

    # Deterministic: running again produces byte-identical JSON.
    first = out.read_text(encoding="utf-8")
    assert pc.main(["stats", "--transcripts", str(FIXTURES), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == first


def test_eval_command_compares_generated_pressers(tmp_path, capsys):
    stats_path = tmp_path / "stats.json"
    assert pc.main(["stats", "--transcripts", str(FIXTURES), "--out", str(stats_path)]) == 0

    generated = tmp_path / "generated"
    generated.mkdir()
    shutil.copy(FIXTURES / "asap_loss.txt", generated / "gen_loss.txt")
    shutil.copy(FIXTURES / "bare_qa.txt", generated / "gen_bare.txt")

    capsys.readouterr()
    rc = pc.main(["eval", "--transcripts", str(generated), "--stats", str(stats_path)])
    assert rc == 0
    output = capsys.readouterr().out
    assert "questions per presser" in output
    assert "follow-up rate" in output
    assert "question-type total variation" in output


def test_eval_reports_missing_stats(tmp_path, capsys):
    rc = pc.main(
        ["eval", "--transcripts", str(FIXTURES), "--stats", str(tmp_path / "missing.json")]
    )
    assert rc == 2
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "args",
    [["--help"], ["stats", "--help"], ["eval", "--help"]],
)
def test_help_works(args):
    proc = subprocess.run(
        [sys.executable, "-m", "tools.press_corpus", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "usage:" in proc.stdout.lower()


def test_no_subcommand_prints_help(capsys):
    assert pc.main([]) == 0
    assert "usage:" in capsys.readouterr().out.lower()
