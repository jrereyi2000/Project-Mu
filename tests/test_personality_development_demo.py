"""Synthetic experiment runner (PERSONALITY_DEVELOPMENT_COACHING_PLAN U2): offline, no DB."""
import json

import pytest

from tools import personality_development_demo as demo

CANDIDATE = {
    "label": "trial-a",
    "development": {"version": "trial-a", "mode": "enabled",
                    "weights": {"drive": .1, "film": .2, "technical_preparation": .2, "coasting": -.2,
                                "teaching": .2, "qb_teaching": .3, "mentor": .2},
                    "max_relative_change": .4, "max_annual_abs_points": 3, "max_annual_ovr_difference": 1.0,
                    "max_per_rating_annual_difference": 1.5},
    "coaching": {"version": "trial-a", "mode": "enabled", "weights": {"youth_developer": 2.0, "scheme_tendency": 1.0},
                 "max_depth_score_shift": 2.0, "close_score_window": 3.0, "max_scheme_score_shift": 1.0}}


@pytest.fixture(scope="module")
def candidate_report():
    return demo.run("all", 2026, 8, 2026, demo.load_config(CANDIDATE))


def test_help_runs(capsys):
    with pytest.raises(SystemExit) as exc:
        demo.main(["--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert "--scenario" in text and "dsn" not in text.lower()


def test_missing_config_is_off_and_every_arm_equals_neutral():
    report = demo.run("all", 2026, 3, 2026, demo.load_config(None))
    arms = report["scenarios"]["paired_arms"]["arms"]
    for arm, row in arms.items():
        assert row["attributed_effect_total"] == 0
        assert row["final_rating_vector_abs_difference"] == 0, arm
    assert all(c["within_caps"] for c in report["cap_checks"])


def test_strict_config_rejects_unknown_fields_and_nan(tmp_path):
    with pytest.raises(demo.DemoError):
        demo.load_config({"development": {"mode": "off", "preset": 1}})
    with pytest.raises(demo.DemoError):
        demo.load_config({"extra": {}})
    bad = tmp_path / "bad.json"
    bad.write_text('{"development": {"max_relative_change": NaN}}')
    assert demo.main(["--config", str(bad), "--out", str(tmp_path / "o")]) == 2


def test_byte_stable_canonical_report(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps(CANDIDATE))
    for n in (1, 2):
        assert demo.main(["--config", str(cfg), "--seasons", "3", "--out", str(tmp_path / f"r{n}")]) == 0
    assert (tmp_path / "r1/report.json").read_bytes() == (tmp_path / "r2/report.json").read_bytes()
    assert (tmp_path / "r1/README.md").read_text().startswith("# Personality development demo")


def test_paired_trajectories_2026_2033_and_handoff(candidate_report):
    assert candidate_report["handoff_season"] == 2034
    traj = candidate_report["scenarios"]["paired_arms"]["trajectories"]
    assert [y["season"] for y in traj["neutral"]] == list(range(2026, 2034))
    assert set(traj) == set(demo.ARMS)


def test_caps_hold_every_season(candidate_report):
    assert candidate_report["cap_checks"] and all(c["within_caps"] for c in candidate_report["cap_checks"])
    arms = candidate_report["scenarios"]["paired_arms"]["arms"]
    assert arms["teaching"]["max_annual_abs_spend"] <= 3 + 1e-6
    assert arms["teaching"]["max_paired_ovr_gap"] <= 1.0 + 1e-6


def test_direct_and_indirect_arms_and_residual_visible(candidate_report):
    causal = candidate_report["scenarios"]["paired_arms"]["causal"]
    arms = candidate_report["scenarios"]["paired_arms"]["arms"]
    assert arms["preparation"]["attributed_effect_total"] != 0
    # Opportunity has no direct attributed effect; it moves appearances (indirect feedback).
    assert arms["opportunity"]["attributed_effect_total"] == 0
    assert arms["opportunity"]["final_rating_vector_abs_difference"] > 0
    assert "combined_interaction_residual" in causal and causal["indirect_arms"] == ["opportunity"]
    assert candidate_report["labels"]["coefficients"].startswith("CANDIDATE")


def test_neutral_arm_identical_regardless_of_config(candidate_report):
    off = demo.run("all", 2026, 8, 2026, demo.load_config(None))
    assert (candidate_report["scenarios"]["paired_arms"]["trajectories"]["neutral"]
            == off["scenarios"]["paired_arms"]["trajectories"]["neutral"])


def test_policy_revision_does_not_reroll_baseline():
    a = demo.run("learning", 2026, 2, 2026, demo.load_config(CANDIDATE))
    changed = json.loads(json.dumps(CANDIDATE))
    changed["development"]["version"] = "trial-b"
    changed["development"]["weights"]["film"] = .3
    b = demo.run("learning", 2026, 2, 2026, demo.load_config(changed))
    assert a["scenarios"]["learning"]["arms"]["neutral"] == b["scenarios"]["learning"]["arms"]["neutral"]


def test_longer_compounding_run():
    report = demo.run("all", 2026, 12, 2026, demo.load_config(CANDIDATE))
    assert len(report["scenarios"]["paired_arms"]["trajectories"]["combined"]) == 12
    assert report["handoff_season"] == 2038


def test_mentoring_offline_only_and_loner_neutral(candidate_report):
    mentoring = candidate_report["scenarios"]["mentoring"]
    assert mentoring["status"] == "OFFLINE_ONLY_MENTOR_DISABLED_IN_INTEGRATION"
    for row in mentoring["rows"]:
        assert row["cases"]["loner_trait"]["difference"] == 0
        assert row["cases"]["no_exposure"]["difference"] == 0
        if row["neutral_component_ovr_term"] == 0:
            assert row["cases"]["mentor_trait"]["difference"] == 0


def test_scheme_advice_only_and_illegal_excluded(candidate_report):
    scheme = candidate_report["scenarios"]["scheme"]
    assert scheme["advice_only"] is True and "air_raid" not in scheme["ranked"]
    assert scheme["neutral_ranked"] == ["west_coast", "spread"]
    assert scheme["ranked"] == ["spread", "west_coast"]


def test_reconciliation_includes_effect_once(candidate_report):
    rec = candidate_report["scenarios"]["reconciliation"]
    assert rec["target_includes_effect_once"]["effect_survives"] == rec["neutral_target_only"]["effect_survives"] + 1
    assert rec["carry_paid_again"] is False


def test_sweep(tmp_path):
    sweep = tmp_path / "sweep.json"
    sweep.write_text(json.dumps([CANDIDATE, {"label": "off"}]))
    assert demo.main(["--sweep", str(sweep), "--seasons", "2", "--out", str(tmp_path / "s")]) == 0
    report = json.loads((tmp_path / "s/report.json").read_text())
    assert [s["index"] for s in report["sweep"]] == [0, 1]
    assert all(s["cap_checks_ok"] for s in report["sweep"])


def test_no_database_imports():
    import tools.personality_development_demo as module
    source = open(module.__file__).read()
    assert "psycopg" not in source and "DATABASE_URL" not in source
