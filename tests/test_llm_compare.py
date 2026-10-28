"""Unit tests for tools.llm_compare.
(docs/PRE_PC_WORK.md §7, docs/PC_MIGRATION_PLAN.md §7.4)

Uses mock providers to ensure ZERO real paid API calls.
Tests:
- CLI argument parsing
- Input building across all 7 tasks (database read-only & synthetic fallback)
- Model evaluation loop and metrics aggregation
- Automatic validation checks (length, schema, hallucination numbers, domain guards)
- HTML and JSON report generation
"""
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from tools.llm_compare import (
    TASKS,
    TaskInput,
    build_agent_wire_inputs,
    build_gm_negotiation_inputs,
    build_gm_persona_inputs,
    build_gm_rationale_inputs,
    build_memory_summary_inputs,
    build_memory_takes_inputs,
    build_scouting_notes_inputs,
    extract_numbers,
    extract_numbers_from_facts,
    extract_task_inputs,
    main,
    render_html_report,
    run_evaluation,
    save_reports,
    validate_agent_wire,
    validate_gm_negotiation,
    validate_gm_persona,
    validate_gm_rationale,
    validate_memory_summary,
    validate_memory_takes,
    validate_output,
    validate_scouting_notes,
)


class TestCLIArgumentParsing:
    def test_cli_parsing_valid(self):
        with patch("tools.llm_compare.extract_task_inputs") as mock_extract, \
             patch("tools.llm_compare.run_evaluation") as mock_eval, \
             patch("tools.llm_compare.save_reports") as mock_save:

            mock_extract.return_value = [
                TaskInput(id="t1", title="T1", task="agent_wire", prompt="prompt")
            ]
            mock_eval.return_value = {"task": "agent_wire", "models": ["m1", "m2"], "summary": {}, "items": []}
            mock_save.return_value = ("out.json", "out.html")

            main([
                "--task", "agent_wire",
                "--season", "2036",
                "--week", "18",
                "--models", "gemini-3.8-flash:low", "gemini-3.5-flash-lite",
                "--limit", "3",
                "--out", "test_output",
            ])

            mock_extract.assert_called_once_with("agent_wire", 2036, 18, limit=3)
            mock_eval.assert_called_once()
            args, _ = mock_eval.call_args
            assert args[0] == "agent_wire"
            assert len(args[1]) == 1
            assert args[2] == ["gemini-3.8-flash:low", "gemini-3.5-flash-lite"]
            mock_save.assert_called_once_with(mock_eval.return_value, out_dir="test_output")

    def test_cli_invalid_task_exits(self):
        with pytest.raises(SystemExit):
            main(["--task", "invalid_task", "--models", "m1"])


class TestInputBuilding:
    def test_all_tasks_have_builders(self):
        for task in TASKS:
            inputs = extract_task_inputs(task, season=2036, week=20, limit=2)
            assert len(inputs) > 0, f"Task {task} should return at least 1 input"
            for inp in inputs:
                assert inp.id
                assert inp.title
                assert inp.prompt
                assert inp.task == task

    def test_synthetic_fallback_agent_wire(self):
        inputs = build_agent_wire_inputs(season=2036, week=20, limit=3, cur=None)
        assert len(inputs) == 3
        assert inputs[0].task == "agent_wire"
        assert inputs[0].system_prompt is not None

    def test_synthetic_fallback_memory_takes(self):
        inputs = build_memory_takes_inputs(season=2036, week=20, limit=2, cur=None)
        assert len(inputs) == 2
        assert inputs[0].task == "memory_takes"
        assert "headline" in inputs[0].facts

    def test_synthetic_fallback_memory_summary(self):
        inputs = build_memory_summary_inputs(season=2036, week=20, limit=1, cur=None)
        assert len(inputs) == 1
        assert inputs[0].task == "memory_summary"
        assert "takes" in inputs[0].facts

    def test_synthetic_fallback_scouting_notes(self):
        inputs = build_scouting_notes_inputs(season=2036, week=20, limit=2, cur=None)
        assert len(inputs) == 2
        assert inputs[0].task == "scouting_notes"
        assert "height_in" in inputs[0].facts

    def test_synthetic_fallback_gm_persona(self):
        inputs = build_gm_persona_inputs(season=2036, week=20, limit=1, cur=None)
        assert len(inputs) == 1
        assert inputs[0].task == "gm_persona"
        assert inputs[0].metadata["start_season"] == 2035

    def test_synthetic_fallback_gm_rationale(self):
        inputs = build_gm_rationale_inputs(season=2036, week=20, limit=1, cur=None)
        assert len(inputs) == 1
        assert inputs[0].task == "gm_rationale"
        assert "teams" in inputs[0].facts
        assert "assets" in inputs[0].facts

    def test_synthetic_fallback_gm_negotiation(self):
        inputs = build_gm_negotiation_inputs(season=2036, week=20, limit=2, cur=None)
        assert len(inputs) == 2
        assert inputs[0].task == "gm_negotiation"
        assert "teams" in inputs[0].facts

    def test_db_cursor_input_extraction(self):
        # Mock cursor for memory_takes
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (101, "Marcus Vance", "Panthers Defense Rolls", "Long article body here about Panthers defense" * 20)
        ]
        inputs = build_memory_takes_inputs(season=2036, week=20, limit=1, cur=mock_cur)
        assert len(inputs) == 1
        assert inputs[0].id == "take_101"
        assert "Panthers Defense Rolls" in inputs[0].title

        # Mock cursor for scouting_notes
        mock_cur.fetchall.return_value = [
            (42, "COMBINE", "Jamar Bodden ran 4.45", {"forty": 4.45}, "Jamar Bodden", "CB", "Illinois", 74, 185, 21)
        ]
        scout_inputs = build_scouting_notes_inputs(season=2036, week=20, limit=1, cur=mock_cur)
        assert len(scout_inputs) == 1
        assert scout_inputs[0].id == "scout_42"
        assert "Jamar Bodden" in scout_inputs[0].title


class TestNumberExtraction:
    def test_extract_numbers(self):
        text = "Drake Maye threw 3 touchdowns for 312 yards and 1 interception."
        nums = extract_numbers(text)
        assert 3.0 in nums
        assert 312.0 in nums
        assert 1.0 in nums

    def test_extract_numbers_from_facts(self):
        facts = {
            "score": "24-17",
            "passing_yards": 280,
            "rate": 105.4,
            "nested": [{"stat": 42}],
        }
        nums = extract_numbers_from_facts(facts)
        assert 24.0 in nums
        assert 17.0 in nums
        assert 280.0 in nums
        assert 105.4 in nums
        assert 42.0 in nums


class TestValidationGuards:
    def test_agent_wire_length_and_numbers(self):
        inp = TaskInput(
            id="aw1",
            title="Wire",
            task="agent_wire",
            prompt="Prompt",
            facts={"passing_yards": 300, "td": 3},
            metadata={"is_rumor": False},
        )
        # Passes with grounded numbers
        passed, fails, _ = validate_agent_wire("Bryce Young had 300 yards and 3 touchdowns. Dominant win.", inp)
        assert passed is True
        assert len(fails) == 0

        # Fails length constraint (> 280 chars)
        long_text = "A" * 285
        passed, fails, _ = validate_agent_wire(long_text, inp)
        assert passed is False
        assert any("280 characters" in f for f in fails)

        # Fails hallucinated number
        passed, fails, _ = validate_agent_wire("Bryce Young had 999 yards today!", inp)
        assert passed is False
        assert any("Number 999.0 not found" in f for f in fails)

    def test_agent_wire_money_and_rumor_guards(self):
        inp = TaskInput(
            id="aw2",
            title="Rumor wire",
            task="agent_wire",
            prompt="Prompt",
            facts={"deal": "$10M extension"},
            metadata={"is_rumor": True},
        )
        # Fails ungrounded dollar amount
        passed, fails, _ = validate_agent_wire("Hearing he wants $85M on a new deal.", inp)
        assert passed is False
        assert any("amount" in f.lower() or "dollar" in f.lower() or "problem" in f.lower() for f in fails)

        # Fails rumor guard claiming deal completed
        passed, fails, _ = validate_agent_wire("Sources say the trade is a done deal and finalized.", inp)
        assert passed is False
        assert any("rumor" in f.lower() for f in fails)

    def test_memory_takes_validation(self):
        inp = TaskInput(
            id="mt1",
            title="Take extraction",
            task="memory_takes",
            prompt="Article about 312 passing yards",
            facts={"text": "312 passing yards in week 16."},
        )
        # Valid JSON array
        valid_json = json.dumps([
            {"take": "The offense looked unstoppable with 312 yards.", "team": "Bills", "player": "Drake Maye"}
        ])
        passed, fails, _ = validate_memory_takes(valid_json, inp)
        assert passed is True

        # Invalid JSON
        passed, fails, _ = validate_memory_takes("Not JSON at all", inp)
        assert passed is False
        assert any("JSON" in f for f in fails)

        # Hallucinated number
        bad_nums = json.dumps([{"take": "He threw 850 yards.", "team": "Bills"}])
        passed, fails, _ = validate_memory_takes(bad_nums, inp)
        assert passed is False
        assert any("850" in f for f in fails)

    def test_memory_summary_validation(self):
        inp = TaskInput(
            id="ms1",
            title="Summary",
            task="memory_summary",
            prompt="Memory prompt with Drake Maye",
            facts={"takes": [{"text": "Drake Maye is elite in week 18"}]},
        )
        # Valid summary
        passed, fails, _ = validate_memory_summary("I have stood by Drake Maye all season through week 18.", inp)
        assert passed is True

        # Too long (> 140 words)
        long_summary = "word " * 150
        passed, fails, _ = validate_memory_summary(long_summary, inp)
        assert passed is False
        assert any("exceeds maximum" in f for f in fails)

    def test_scouting_notes_validation(self):
        sheet = {
            "name": "Jamar Bodden",
            "position": "cornerback",
            "school": "Illinois",
            "height_in": 74,
            "weight": 185,
            "age": 21,
            "payload": {"targets": 4, "int": 1},
            "template": "Bodden allowed 1 catch on 4 targets.",
        }
        inp = TaskInput(
            id="sc1",
            title="Scout note",
            task="scouting_notes",
            prompt="Prompt",
            facts=sheet,
        )
        # Valid note
        good_note = "Jamar Bodden locked down his side of the field, giving up only 1 catch on 4 targets with 1 pick."
        passed, fails, _ = validate_scouting_notes(good_note, inp)
        assert passed is True

        # Fails mentioning OVR / ratings
        bad_note = "His 88 OVR rating shows up on tape."
        passed, fails, _ = validate_scouting_notes(bad_note, inp)
        assert passed is False
        assert any("OVR" in f or "Scout validation" in f for f in fails)

    def test_gm_persona_validation(self):
        inp = TaskInput(
            id="gmp1",
            title="GM Persona",
            task="gm_persona",
            prompt="Prompt",
            facts={},
            metadata={"start_season": 2035, "taken_names": ["Existing Name"]},
        )
        valid_json = json.dumps({
            "gm_name": "Calvin Sterling",
            "bio": "Calvin Sterling has led the front office since 2035. He leans on analytics and cap flexibility.",
            "voice": "Direct, numbers-driven, and forward-looking.",
        })
        passed, fails, _ = validate_gm_persona(valid_json, inp)
        assert passed is True

        # Fails forbidden bio word (e.g. drafted)
        bad_bio = json.dumps({
            "gm_name": "Calvin Sterling",
            "bio": "Calvin Sterling has led since 2035. He drafted three Pro Bowlers.",
            "voice": "Direct.",
        })
        passed, fails, _ = validate_gm_persona(bad_bio, inp)
        assert passed is False

    def test_gm_rationale_validation(self):
        sheet = {
            "season": 2036, "week": 20, "trigger": "NEED",
            "teams": {
                "a": {"team": "Carolina Panthers", "nickname": "Panthers", "abbr": "CAR", "gm": "Dan Morgan", "voice": "Direct.", "cap_before": "$35.00M", "cap_after": "$27.50M", "needs": ["receiver"]},
                "b": {"team": "Buffalo Bills", "nickname": "Bills", "abbr": "BUF", "gm": "Conrad Keller", "voice": "Pragmatic.", "cap_before": "$18.00M", "cap_after": "$25.50M", "needs": ["picks"]},
            },
            "assets": [
                {"kind": "player", "name": "Marcus Holliday", "pos": "WR", "group": "WR", "ovr": 84, "age": 27, "cap_hit": "$7.50M", "years_left": 2, "from": "BUF", "to": "CAR"},
                {"kind": "pick", "name": "2037 R2 (CAR)", "from": "CAR", "to": "BUF"},
            ]
        }
        inp = TaskInput(id="rat1", title="Rationale", task="gm_rationale", prompt="Prompt", facts=sheet)

        valid_json = json.dumps({
            "headline": "Panthers acquire Marcus Holliday from Bills",
            "rationale_a": "Dan Morgan: We needed receiver help and Marcus Holliday brings veteran polish. Our cap moves from $35.00M to $27.50M.",
            "rationale_b": "Conrad Keller: Adding a 2037 R2 pick fits our long-term plan, bringing our cap to $25.50M.",
        })
        passed, fails, _ = validate_gm_rationale(valid_json, inp)
        assert passed is True

        # Invalid schema (missing headline)
        bad_json = json.dumps({"rationale_a": "Text", "rationale_b": "Text"})
        passed, fails, _ = validate_gm_rationale(bad_json, inp)
        assert passed is False

    def test_gm_negotiation_validation(self):
        sheet = {
            "season": 2036, "week": 20, "trigger": "USER",
            "teams": {
                "a": {"team": "Carolina Panthers", "nickname": "Panthers", "abbr": "CAR", "gm": "JR", "voice": "plain"},
                "b": {"team": "Seattle Seahawks", "nickname": "Seahawks", "abbr": "SEA", "gm": "John Schneider", "voice": "Sharp."},
            },
            "assets": [
                {"kind": "player", "name": "Tariq Woolen", "pos": "CB", "group": "CB", "ovr": 86, "age": 28, "cap_hit": "$12.00M", "years_left": 2, "from": "SEA", "to": "CAR"},
                {"kind": "pick", "name": "2037 R2 (CAR)", "from": "CAR", "to": "SEA"},
            ]
        }
        inp = TaskInput(
            id="neg1",
            title="Negotiation",
            task="gm_negotiation",
            prompt="Prompt",
            facts=sheet,
            metadata={"league": {"players": {"PATRICK MAHOMES"}, "teams": set()}},
        )

        valid_json = json.dumps({
            "message": "John Schneider: We value Tariq Woolen highly. Send over the 2037 R2 pick and we will consider it."
        })
        passed, fails, _ = validate_gm_negotiation(valid_json, inp)
        assert passed is True

        # Fails ungrounded player name not in deal
        bad_json = json.dumps({
            "message": "John Schneider: Give us Patrick Mahomes instead."
        })
        passed, fails, _ = validate_gm_negotiation(bad_json, inp)
        assert passed is False


class TestModelEvaluationLoop:
    def test_run_evaluation_mocked(self):
        inp = TaskInput(
            id="item1",
            title="Wire Item 1",
            task="agent_wire",
            prompt="Write a reaction tweet for 24-17 win",
            facts={"score": "24-17"},
        )
        models = ["gemini-3.8-flash:low", "ollama:qwen2.5:14b"]

        def mock_generate_text(prompt, model, return_usage=False, **kwargs):
            usage = {
                "input_tokens": 120,
                "output_tokens": 30,
                "thinking_tokens": 40 if ":low" in model else 0,
                "cached_tokens": 0,
            }
            text = "Great 24-17 win today by the team! Dominant defense."
            if return_usage:
                return text, usage
            return text

        with patch("tools.llm_compare.generate_text", side_effect=mock_generate_text):
            data = run_evaluation("agent_wire", [inp], models)

        assert data["task"] == "agent_wire"
        assert len(data["models"]) == 2
        assert len(data["items"]) == 1

        # Check summary metrics
        s1 = data["summary"]["gemini-3.8-flash:low"]
        assert s1["total_calls"] == 1
        assert s1["passed_calls"] == 1
        assert s1["input_tokens"] == 120
        assert s1["output_tokens"] == 30
        assert s1["thinking_tokens"] == 40
        assert s1["total_cost_usd"] > 0.0

        # Ollama cost must be 0.0
        s2 = data["summary"]["ollama:qwen2.5:14b"]
        assert s2["total_calls"] == 1
        assert s2["passed_calls"] == 1
        assert s2["total_cost_usd"] == 0.0

    def test_model_call_failure_handled_gracefully(self):
        inp = TaskInput(id="item2", title="Failing Item", task="agent_wire", prompt="Prompt")

        def mock_failing_call(**kwargs):
            raise ConnectionError("Connection refused")

        with patch("tools.llm_compare.generate_text", side_effect=mock_failing_call):
            data = run_evaluation("agent_wire", [inp], ["ollama:broken"])

        res = data["items"][0]["results"]["ollama:broken"]
        assert res["passed"] is False
        assert any("Connection refused" in f for f in res["failures"])
        assert data["summary"]["ollama:broken"]["failed_calls"] == 1


class TestReportGeneration:
    def test_save_json_and_html_reports(self):
        data = {
            "task": "scouting_notes",
            "models": ["gemini-3.8-flash:low", "gemini-3.5-flash-lite"],
            "timestamp": "2026-10-28T12:00:00",
            "summary": {
                "gemini-3.8-flash:low": {
                    "model": "gemini-3.8-flash:low", "total_calls": 1, "passed_calls": 1, "failed_calls": 0,
                    "pass_rate": 100.0, "total_failures": 0, "input_tokens": 150, "output_tokens": 40,
                    "thinking_tokens": 50, "total_seconds": 0.45, "avg_seconds": 0.45, "total_cost_usd": 0.00007,
                    "avg_cost_usd": 0.00007,
                },
                "gemini-3.5-flash-lite": {
                    "model": "gemini-3.5-flash-lite", "total_calls": 1, "passed_calls": 0, "failed_calls": 1,
                    "pass_rate": 0.0, "total_failures": 1, "input_tokens": 150, "output_tokens": 40,
                    "thinking_tokens": 0, "total_seconds": 0.32, "avg_seconds": 0.32, "total_cost_usd": 0.00003,
                    "avg_cost_usd": 0.00003,
                },
            },
            "items": [
                {
                    "id": "scout_1",
                    "title": "Prospect Note 1",
                    "prompt": "Write note for prospect",
                    "facts": {"name": "Evan Kemp", "position": "OT"},
                    "metadata": {},
                    "results": {
                        "gemini-3.8-flash:low": {
                            "model": "gemini-3.8-flash:low",
                            "text": "Evan Kemp displayed explosive footwork at tackle.",
                            "input_tokens": 150, "output_tokens": 40, "thinking_tokens": 50,
                            "seconds": 0.45, "cost_usd": 0.00007, "passed": True, "failures": [], "metrics": {},
                        },
                        "gemini-3.5-flash-lite": {
                            "model": "gemini-3.5-flash-lite",
                            "text": "Evan Kemp is an 88 OVR prospect.",
                            "input_tokens": 150, "output_tokens": 40, "thinking_tokens": 0,
                            "seconds": 0.32, "cost_usd": 0.00003, "passed": False,
                            "failures": ["Scout validation failed: mentions 'OVR'"], "metrics": {},
                        },
                    },
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, html_path = save_reports(data, out_dir=tmpdir)

            assert os.path.exists(json_path)
            assert os.path.exists(html_path)

            with open(json_path) as f:
                loaded = json.load(f)
                assert loaded["task"] == "scouting_notes"
                assert len(loaded["items"]) == 1

            with open(html_path) as f:
                html_text = f.read()
                assert "Quality Test Harness: SCOUTING_NOTES" in html_text
                assert "badge-pass" in html_text
                assert "badge-fail" in html_text
                assert "Persona Voice Verdict" in html_text
                assert "localStorage" in html_text
