"""
A/B Testing Harness: ReAct Tool Loop (Variant A) vs. Deterministic Python Dossier (Variant B)
Reference: docs/in_progress/V2_MASTER_PLAN.md §5.3

Evaluates token consumption, dollar cost, generation latency, and output quality side by side:
- Variant A (Control): Multi-turn ReAct loop with autonomous database tool calls (Current Production).
- Variant B (Treatment): Deterministic Python Dossier compiling micro-evidence in Turn 1 with zero tool calls.

Usage:
  # Dry-run inspection (compiles prompts & dossier without API spend):
  python3 -m tools.dossier_ab_compare --dry-run --team CAR --opponent CHI

  # Live A/B test on Gemini 3.8 Flash (Season 2036 Week 12):
  python3 -m tools.dossier_ab_compare --season 2036 --week 12 --team CAR --opponent CHI

  # Evaluate Variant B on local Ollama against Variant A on Gemini:
  python3 -m tools.dossier_ab_compare --season 2036 --week 12 --team CAR --opponent CHI --model-b ollama:gemma4:e4b@nothink
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import psycopg
from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
from newsroom.db_tools import (
    get_matchup_edges,
    get_team_efficiency_profile,
    get_coach_profile,
    get_team_injury_report,
    get_game_box_score,
    get_advanced_game_analytics,
)
from engines.llm_provider import generate_text, estimate_cost, normalize_model_name

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


# ==============================================================================
# 1. Deterministic Python Dossier Compilers
# ==============================================================================

def compile_matchup_dossier(team_abbr: str, opponent_abbr: str, season: int, week: int) -> str:
    """Compiles a targeted micro-dossier for LOCAL_MATCHUP_PRIMER in ~15ms via direct DB queries."""
    sections = [
        f"### TARGETED MATCHUP DOSSIER: {team_abbr} vs. {opponent_abbr} (Season {season}, Week {week})",
        "The following tactical data was compiled deterministically from the canonical database.",
        "",
        "#### 1. POSITIONAL MATCHUP EDGES & SCHEMATIC COMPARISON:",
        get_matchup_edges(team_abbr, opponent_abbr, season=season),
        "",
        f"#### 2. {team_abbr} TEAM EFFICIENCY PROFILE & SITUATIONAL RATES:",
        get_team_efficiency_profile(team_abbr, season=season),
        "",
        f"#### 3. {opponent_abbr} TEAM EFFICIENCY PROFILE & SITUATIONAL RATES:",
        get_team_efficiency_profile(opponent_abbr, season=season),
        "",
        f"#### 4. COACHING SCHEMES & TENDENCIES ({team_abbr} vs. {opponent_abbr}):",
        f"- **{team_abbr} Staff:**\n{get_coach_profile(team_abbr, season=season)}",
        f"- **{opponent_abbr} Staff:**\n{get_coach_profile(opponent_abbr, season=season)}",
        "",
        "#### 5. OFFICIAL MATCHUP INJURY REPORT:",
        f"- **{team_abbr} Injuries:**\n{get_team_injury_report(team_abbr, season=season, week=week)}",
        f"- **{opponent_abbr} Injuries:**\n{get_team_injury_report(opponent_abbr, season=season, week=week)}",
    ]
    return "\n".join(sections)


def compile_game_dossier(game_id: int) -> str:
    """Compiles a targeted micro-dossier for SINGLE_GAME_SCHEMATIC_AUTOPSY / POSTGAME_REPORT_CARD."""
    sections = [
        f"### TARGETED GAME DOSSIER (Game ID: {game_id})",
        "The following box-score facts and advanced analytics were compiled deterministically from the canonical database.",
        "",
        "#### 1. OFFICIAL GAME BOX SCORE & SCORING PLAYS:",
        get_game_box_score(game_id),
        "",
        "#### 2. ADVANCED TACTICAL & PACE METRICS:",
        get_advanced_game_analytics(game_id),
    ]
    return "\n".join(sections)


# ==============================================================================
# 2. Automated Quality Audit & Validation
# ==============================================================================

BANNED_CLICHES = [
    "paper tiger",
    "blueprint to victory",
    "blueprint for victory",
    "litmus test",
    "bellwether",
    "championship pedigree",
    "look no further than",
    "house of cards",
    "smoke and mirrors",
    "fool's gold",
    "silver lining",
    "flashing red",
    "ticking time bomb",
    "powder keg",
    "crucible of",
    "mood inside the locker room",
    "mood inside the facility",
    "that's the list",
]

RAW_SYMBOLS = [
    r"\$P\$",
    r"\(P\)",
    r"Y_prod",
    r"\$Y_\\text\{prod\}\$",
    r"P_draft",
    r"\$R_\\text\{rating\}\$",
    r"\$U\$",
    r"\$T\$",
    r"\$S_\\text\{all-time\}\$",
    r"SpikeIndex",
]


def audit_article_quality(text: str) -> Dict[str, Any]:
    """Audits published article text for structural integrity, banned idioms, and raw symbols."""
    issues = []
    t_lower = text.lower()

    # 1. Headline & Excerpt Structure
    has_h1 = bool(re.search(r"^#\s+[^\n]+", text, re.MULTILINE))
    has_excerpt = bool(re.search(r"^>\s*\*\*EXCERPT:\*\*", text, re.MULTILINE))
    if not has_h1:
        issues.append("Missing H1 title format (# Headline)")
    if not has_excerpt:
        issues.append("Missing formal blockquote excerpt (> **EXCERPT:**)")

    # 2. Banned Clichés
    found_cliches = [c for c in BANNED_CLICHES if c in t_lower]
    if found_cliches:
        issues.append(f"Contains banned clichés: {', '.join(found_cliches)}")

    # 3. Raw Math Symbols
    found_symbols = []
    for sym in RAW_SYMBOLS:
        if re.search(sym, text):
            found_symbols.append(sym.replace("\\", ""))
    if found_symbols:
        issues.append(f"Contains prohibited internal symbols: {', '.join(found_symbols)}")

    # 4. Word & Paragraph Counts
    words = len(text.split())
    paragraphs = [p for p in text.split("\n\n") if p.strip() and not p.strip().startswith("#")]

    return {
        "word_count": words,
        "paragraph_count": len(paragraphs),
        "has_h1": has_h1,
        "has_excerpt": has_excerpt,
        "cliches_detected": found_cliches,
        "symbols_detected": found_symbols,
        "issues": issues,
        "pass": len(issues) == 0,
    }


# ==============================================================================
# 3. A/B Execution Engine
# ==============================================================================

def run_dossier_ab_test(
    season: int = 2036,
    week: int = 12,
    team_abbr: str = "CAR",
    opponent_abbr: str = "CHI",
    model_a: str = "gemini-3.8-flash:low",
    model_b: str = "gemini-3.8-flash:low",
    dry_run: bool = False,
) -> Dict[str, Any]:
    dispatcher = SwarmDispatcher(debug_mode=False)

    # 1. Resolve Team IDs from database
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, abbreviation FROM teams WHERE abbreviation IN (%s, %s);", (team_abbr, opponent_abbr))
            teams_map = {r[2]: {"id": r[0], "name": r[1]} for r in cur.fetchall()}

    if team_abbr not in teams_map or opponent_abbr not in teams_map:
        raise ValueError(f"Could not find teams {team_abbr} and {opponent_abbr} in database.")

    team_id = teams_map[team_abbr]["id"]
    opp_id = teams_map[opponent_abbr]["id"]

    # 2. Build Macro Assignment Brief (Shared baseline across both variants)
    print(f"\n[A/B Test] Building Macro Assignment Brief for Season {season} Week {week}...")
    macro_brief = build_weekly_assignment_brief(DSN, season, week)

    slot_target = {
        "team_id": team_id,
        "team_name": teams_map[team_abbr]["name"],
        "opponent_team_id": opp_id,
        "opponent_name": teams_map[opponent_abbr]["name"],
        "note": f"Matchup preview: {team_abbr} vs. {opponent_abbr}",
    }

    # Compile prompt for Variant A (ReAct)
    prompt_a = dispatcher.compile_agent_prompt(
        agent_id=f"beat_{team_abbr.lower()}",
        archetype="LOCAL_BEAT_REPORTER",
        persistent_memory="I have covered this franchise through multiple coordinators.",
        handle=f"@{team_abbr}Beat",
        display_name=f"{teams_map[team_abbr]['name']} Beat",
        assigned_team_id=team_id,
        brief=macro_brief,
        season=season,
        week=week,
        content_type="ARTICLE",
        article_type="LOCAL_MATCHUP_PRIMER",
        slot_target=slot_target,
    )

    # Compile micro-dossier for Variant B (Python Dossier)
    print(f"[A/B Test] Compiling Targeted Python Micro-Dossier ({team_abbr} vs {opponent_abbr})...")
    t0_dossier = time.time()
    micro_dossier_text = compile_matchup_dossier(team_abbr, opponent_abbr, season, week)
    dossier_duration_ms = round((time.time() - t0_dossier) * 1000, 2)
    print(f"   -> Dossier compiled in {dossier_duration_ms} ms ({len(micro_dossier_text)} chars).")

    # Inject micro-dossier directly into Variant B's task instruction
    prompt_b = dict(prompt_a)
    prompt_b["task_instruction"] = (
        prompt_a["task_instruction"]
        + "\n\n"
        + micro_dossier_text
        + "\n\nINSTRUCTION: You have been provided with the complete Targeted Matchup Dossier above. "
        "Write your full article immediately using these facts. Do NOT call any external database tools."
    )

    if dry_run:
        print("\n" + "=" * 80)
        print("DRY-RUN COMPLETE: PROMPT COMPILATION & DOSSIER PREVIEW")
        print("=" * 80)
        print(f"Macro Brief Size: {len(json.dumps(macro_brief))} chars")
        print(f"Targeted Micro-Dossier Size: {len(micro_dossier_text)} chars")
        print(f"Variant A System Instruction: {len(prompt_a['system_instruction'])} chars | Tools Exposed: 40")
        print(f"Variant B Task Instruction: {len(prompt_b['task_instruction'])} chars | Tools Exposed: 0")
        print("\n--- SAMPLE DOSSIER EXCERPT ---")
        print(micro_dossier_text[:800] + "\n...")
        return {"status": "dry_run", "dossier_ms": dossier_duration_ms}

    # ==============================================================================
    # Execute Variant A: Multi-Turn ReAct Loop (Control)
    # ==============================================================================
    print(f"\n🚀 [EXECUTING VARIANT A: MULTI-TURN ReAct] Model: {model_a}...")
    t0_a = time.time()
    from newsroom.tool_loop.gemini_loop import run_gemini_tool_loop

    result_a = run_gemini_tool_loop(
        client=None,
        model=model_a.split(":")[0],
        system_instruction=prompt_a["system_instruction"],
        prompt=prompt_a["task_instruction"],
        tools=dispatcher.tools_for("LOCAL_MATCHUP_PRIMER"),
        max_steps=25,
    )
    time_a = round(time.time() - t0_a, 2)
    cost_a = estimate_cost(model_a, result_a.usage.get("input_tokens", 0), result_a.usage.get("output_tokens", 0))

    # ==============================================================================
    # Execute Variant B: Deterministic Python Dossier Single-Turn (Treatment)
    # ==============================================================================
    print(f"\n🚀 [EXECUTING VARIANT B: PYTHON DOSSIER SINGLE-TURN] Model: {model_b}...")
    t0_b = time.time()
    resp_b = generate_text(
        prompt=prompt_b["task_instruction"],
        system_instruction=prompt_b["system_instruction"],
        model=model_b,
        temperature=0.7,
    )
    time_b = round(time.time() - t0_b, 2)
    cost_b = estimate_cost(model_b, resp_b.input_tokens, resp_b.output_tokens)

    # Quality Audits
    audit_a = audit_article_quality(result_a.text)
    audit_b = audit_article_quality(resp_b.text)

    # Summary KPI Report
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "season": season,
        "week": week,
        "matchup": f"{team_abbr} vs. {opponent_abbr}",
        "variant_a_react": {
            "model": model_a,
            "latency_s": time_a,
            "tool_calls": result_a.tool_calls_count,
            "total_tool_calls": sum(result_a.tool_calls_count.values()),
            "tokens": result_a.usage,
            "cost_dollars": cost_a,
            "audit": audit_a,
            "text": result_a.text,
        },
        "variant_b_dossier": {
            "model": model_b,
            "dossier_compile_ms": dossier_duration_ms,
            "latency_s": time_b,
            "tool_calls": {},
            "total_tool_calls": 0,
            "tokens": {
                "input_tokens": resp_b.input_tokens,
                "output_tokens": resp_b.output_tokens,
                "thinking_tokens": resp_b.thinking_tokens,
            },
            "cost_dollars": cost_b,
            "audit": audit_b,
            "text": resp_b.text,
        },
        "savings": {
            "token_reduction_pct": round(
                (1 - (resp_b.input_tokens + resp_b.output_tokens) / max(1, (result_a.usage.get("input_tokens", 0) + result_a.usage.get("output_tokens", 0)))) * 100, 1
            ),
            "cost_reduction_pct": round((1 - cost_b / max(0.0001, cost_a)) * 100, 1),
            "latency_speedup_factor": round(time_a / max(0.1, time_b), 1),
        },
    }

    # Save artifact to verify_output
    os.makedirs(os.path.join(ROOT_DIR, "verify_output"), exist_ok=True)
    ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = os.path.join(ROOT_DIR, "verify_output", f"compare_dossier_{team_abbr}_{opponent_abbr}_{ts_str}.json")
    out_md = os.path.join(ROOT_DIR, "verify_output", f"compare_dossier_{team_abbr}_{opponent_abbr}_{ts_str}.md")

    with open(out_json, "w") as f:
        json.dump(report, f, indent=2)

    with open(out_md, "w") as f:
        f.write(f"# A/B Evaluation Report: ReAct Tool Loop vs. Python Dossier\n\n")
        f.write(f"**Matchup:** {team_abbr} vs. {opponent_abbr} (Season {season}, Week {week})\n")
        f.write(f"**Timestamp:** {report['timestamp']}\n\n")
        f.write(f"## Summary KPIs\n\n")
        f.write(f"| Dimension | Variant A: ReAct Loop | Variant B: Python Dossier | Improvement |\n")
        f.write(f"| :--- | :--- | :--- | :--- |\n")
        f.write(f"| **Tool Calls** | {report['variant_a_react']['total_tool_calls']} calls | 0 calls (Pre-compiled) | 100% eliminated |\n")
        f.write(f"| **Latency** | {time_a}s | {time_b}s | {report['savings']['latency_speedup_factor']}x faster |\n")
        f.write(f"| **Total Tokens** | {result_a.usage.get('input_tokens', 0) + result_a.usage.get('output_tokens', 0):,} | {resp_b.input_tokens + resp_b.output_tokens:,} | -{report['savings']['token_reduction_pct']}% |\n")
        f.write(f"| **Cost** | ${cost_a:.4f} | ${cost_b:.4f} | -{report['savings']['cost_reduction_pct']}% |\n")
        f.write(f"| **Word Count** | {audit_a['word_count']} words | {audit_b['word_count']} words | |\n")
        f.write(f"| **Audit Checks** | {'✅ PASS' if audit_a['pass'] else '⚠️ ISSUES'} | {'✅ PASS' if audit_b['pass'] else '⚠️ ISSUES'} | |\n\n")
        f.write(f"## Output Texts Side-by-Side\n\n")
        f.write(f"### Variant A (ReAct Multi-Turn):\n\n{result_a.text}\n\n")
        f.write(f"---\n\n### Variant B (Python Dossier Single-Turn):\n\n{resp_b.text}\n")

    print(f"\n[A/B Test Complete] Report saved to:")
    print(f"   • JSON: {out_json}")
    print(f"   • Markdown: {out_md}")
    return report


def main():
    parser = argparse.ArgumentParser(description="A/B Test: Multi-turn ReAct vs. Python Dossier Compiler")
    parser.add_argument("--season", type=int, default=2036, help="Franchise season (default: 2036)")
    parser.add_argument("--week", type=int, default=12, help="Franchise week (default: 12)")
    parser.add_argument("--team", type=str, default="CAR", help="Assigned team abbreviation (default: CAR)")
    parser.add_argument("--opponent", type=str, default="CHI", help="Opponent team abbreviation (default: CHI)")
    parser.add_argument("--model-a", type=str, default="gemini-3.8-flash:low", help="Model for Variant A (ReAct)")
    parser.add_argument("--model-b", type=str, default="gemini-3.8-flash:low", help="Model for Variant B (Dossier)")
    parser.add_argument("--dry-run", action="store_true", help="Compile and inspect prompts without executing LLMs")
    args = parser.parse_args()

    run_dossier_ab_test(
        season=args.season,
        week=args.week,
        team_abbr=args.team.upper(),
        opponent_abbr=args.opponent.upper(),
        model_a=args.model_a,
        model_b=args.model_b,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
