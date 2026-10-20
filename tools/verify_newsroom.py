"""Read-only verification of the newsroom changes against the live database. No Gemini calls, no writes.

Run from the project root:  python tools/verify_newsroom.py [--only TEXT]
Writes a summary to stdout and full detail to verify_output/. --only runs just the checks whose name contains TEXT.
"""
import io
import json
import os
import re
import sys
import traceback
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "verify_output")
os.makedirs(OUT, exist_ok=True)

results = []  # (status, name, detail)


ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None


def check(name):
    def wrap(fn):
        if ONLY and ONLY.lower() not in name.lower():
            return fn
        try:
            detail = fn()
            results.append(("PASS", name, detail or ""))
        except AssertionError as e:
            results.append(("FAIL", name, str(e)))
        except Exception:
            results.append(("ERROR", name, traceback.format_exc()))
        return fn
    return wrap


NEGATION_RE = re.compile(r"\b(do not|don't|never|no injury data|not mention|without|key_injuries|team_injury_report|starter_injuries|starter_injuries_count|injury_type|get_team_injury_report|get_fantasy_injuries|reporting_guidance|official injury report|INJURY REPORTING RULE|BEAT INJURY REPORTING RULE|FANTASY INJURY|restricted to marquee key injuries|Remember national coverage|injured starters)\b", re.I)


def injury_asks(text: str) -> list:
    """Sentences that mention injuries without forbidding them or without sourcing from official tools/briefs."""
    sentences = re.split(r"(?<=[.!?])\s+|\n", text or "")
    return [x.strip() for x in sentences if re.search(r"injur", x, re.I) and not NEGATION_RE.search(x)]


def save(name, text):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(text if isinstance(text, str) else json.dumps(text, indent=2, default=str))


import psycopg
from db.db_handler import DatabaseHandler
from newsroom import db_tools
from newsroom.run_context import article_run_context
from newsroom.weekly_run import publication_week, _week_rows

CONN = DatabaseHandler().db_conn_info
tool = lambda t: getattr(t, "func", t)

with psycopg.connect(CONN) as conn, conn.cursor() as cur:
    cur.execute("SELECT current_season, current_week FROM league_state LIMIT 1;")
    LS = cur.fetchone()
    SEASON = LS[0] if LS else None
    rows = _week_rows(cur, SEASON)
    PUB_WEEK = publication_week(rows)
    cur.execute("SELECT MAX(week) FROM games WHERE season = %s AND status = 'COMPLETED' AND week <= 18;", (SEASON,))
    LAST_DONE = cur.fetchone()[0]
    cur.execute("SELECT id FROM games WHERE season = %s AND week = %s AND status = 'COMPLETED' ORDER BY id LIMIT 1;", (SEASON, LAST_DONE))
    r = cur.fetchone(); GAME_IN_LAST = r[0] if r else None
    cur.execute("SELECT id FROM games WHERE season = %s AND week = %s AND status = 'COMPLETED' ORDER BY id LIMIT 1;", (SEASON, LAST_DONE - 1 if LAST_DONE else None))
    r = cur.fetchone(); GAME_BEFORE = r[0] if r else None
    cur.execute("SELECT id, name FROM teams ORDER BY id LIMIT 1;")
    TEAM_ID, TEAM_NAME = cur.fetchone()

save("00_context.txt", f"league_state={LS}\nweeks(week, non_bye, completed)={rows}\npublication_week={PUB_WEEK}\n"
     f"last_completed_regular_week={LAST_DONE}\nsample game in that week={GAME_IN_LAST}, week before={GAME_BEFORE}\nsample team={TEAM_ID} {TEAM_NAME}\n")


@check("migration: ROUNDTABLE_PANEL writer record exists")
def _():
    with psycopg.connect(CONN) as conn, conn.cursor() as cur:
        cur.execute("SELECT agent_id, archetype, handle, display_name FROM agent_memories WHERE archetype = 'ROUNDTABLE_PANEL';")
        rows = cur.fetchall()
    assert len(rows) == 1, f"expected 1 row, got {rows}"
    return str(rows[0])


@check("resolve_season() with no article context = league_state season")
def _():
    s = db_tools.resolve_season()
    assert s == SEASON, f"{s} != {SEASON}"
    return f"season {s}"


@check(f"cutoff: get_weekly_slate hides Week {LAST_DONE} results for a Week {LAST_DONE} article")
def _():
    with article_run_context(SEASON, LAST_DONE):
        out = json.loads(tool(db_tools.get_weekly_slate)(LAST_DONE))
    save("10_slate_hidden.json", out)
    bad = [g for g in out["games"] if g.get("home_score") is not None or g.get("status") == "COMPLETED"]
    assert not bad, f"{len(bad)} games still show results"
    return f"{len(out['games'])} games shown as scheduled"


@check(f"cutoff: get_weekly_slate shows Week {LAST_DONE} results for a Week {LAST_DONE + 1} article")
def _():
    with article_run_context(SEASON, LAST_DONE + 1):
        out = json.loads(tool(db_tools.get_weekly_slate)(LAST_DONE))
    shown = [g for g in out["games"] if g.get("home_score") is not None]
    assert shown, "no results visible"
    return f"{len(shown)} results visible"


@check("cutoff: get_game_box_score refuses a game from the article's own week")
def _():
    with article_run_context(SEASON, LAST_DONE):
        out = tool(db_tools.get_game_box_score)(GAME_IN_LAST)
        prior = tool(db_tools.get_game_box_score)(GAME_BEFORE) if GAME_BEFORE else "{}"
    save("11_box_scores.txt", f"own week game {GAME_IN_LAST}:\n{out}\n\nprevious week game {GAME_BEFORE}:\n{prior}")
    assert "has not been played yet" in out, out[:200]
    assert "has not been played yet" not in prior, "previous-week game was refused too"
    return "own-week refused, previous week returned"


@check("cutoff: get_season_standings counts only games before the article's week")
def _():
    with article_run_context(SEASON, LAST_DONE):
        cut = json.loads(tool(db_tools.get_season_standings)())
    full = json.loads(tool(db_tools.get_season_standings)())
    save("12_standings.json", {"cut_at_week": LAST_DONE, "cut": cut, "uncut": full})
    games = lambda st: sum(sum(map(int, t["record"].split("-")[:2])) for d in st["standings"].values() for t in d)
    assert games(cut) < games(full), f"cut standings not smaller: {games(cut)} vs {games(full)}"
    return f"team-games counted: {games(cut)} (cut) vs {games(full)} (uncut)"


@check("cutoff: schedule, credit, efficiency, edges, game analytics, articles run inside a context")
def _():
    outs = {}
    with article_run_context(SEASON, LAST_DONE):
        outs["schedule"] = tool(db_tools.get_team_season_schedule)(str(TEAM_ID))
        outs["credit_all"] = tool(db_tools.get_opponent_adjusted_credit)()
        outs["efficiency_all"] = tool(db_tools.get_team_efficiency_profile)()
        outs["edges"] = tool(db_tools.get_matchup_edges)(str(TEAM_ID))
        outs["game_analytics"] = tool(db_tools.get_advanced_game_analytics)(GAME_IN_LAST)
        outs["team_articles"] = tool(db_tools.get_team_articles)(str(TEAM_ID))
        outs["player_stats"] = tool(db_tools.get_player_season_stats)("1")
        outs["team_context"] = tool(db_tools.get_team_context)(str(TEAM_ID))
        outs["leaderboards"] = tool(db_tools.get_league_leaderboards)()
        outs["top_rated"] = tool(db_tools.get_top_rated_players)()
    save("13_tools.json", outs)
    errors = {k: v[:300] for k, v in outs.items() if str(v).startswith("Error")}
    assert not errors, f"tool errors: {errors}"
    top = json.loads(outs["top_rated"])
    assert top["count"] == 200, f"get_top_rated_players returned {top['count']} players"
    assert not any(" | LS | " in line for line in top["players"]), "long snapper in the rated pool"
    sched = json.loads(outs["schedule"])
    leaked = [g for g in sched["season_schedule"] if g["week"] >= LAST_DONE and g["status"] == "COMPLETED"]
    assert not leaked, f"schedule shows {len(leaked)} games from week >= {LAST_DONE} as completed"
    ga = json.loads(outs["game_analytics"])
    assert ga.get("status") != "COMPLETED", "game analytics shows own-week game as completed"
    return "all ran; no own-week results in schedule or game analytics"


BRIEF = {}


@check(f"brief for Week {PUB_WEEK}: season_landscape present, slate_manifest gone")
def _():
    from newsroom.swarm_dispatcher import build_weekly_assignment_brief
    b = build_weekly_assignment_brief(CONN, SEASON, PUB_WEEK)
    BRIEF.update(b)
    save("20_brief.json", b)
    assert "slate_manifest" not in b and "league_leaders" not in b, "old fields still present"
    sl = b["season_landscape"]
    n = sum(len(v) for v in sl["division_standings"].values())
    assert n == 32, f"standings list {n} teams"
    assert len(sl["most_watchable_teams"]) == 5 and len(sl["most_popular_teams"]) == 5
    assert "EDITORIAL DESK SUBJECT ASSIGNMENT" in b["curatorial_guidance"]
    return f"{len(sl['division_standings'])} divisions, 32 teams; brief {len(json.dumps(b))} chars"


@check("brief standings agree with team_seasons records")
def _():
    sl = BRIEF["season_landscape"]["division_standings"]
    mine = {}
    for lines in sl.values():
        for line in lines:
            tid = int(line.split("(ID: ")[1].split(")")[0]); mine[tid] = line.split(") ")[1].split(",")[0]
    with psycopg.connect(CONN) as conn, conn.cursor() as cur:
        cur.execute("SELECT team_id, wins, losses FROM team_seasons WHERE season = %s;", (SEASON,))
        stored = {t: f"{w}-{l}" for t, w, l in cur.fetchall()}
    diff = {t: (mine.get(t), stored[t]) for t in stored if mine.get(t, "").split("-")[:2] != stored[t].split("-")}
    save("21_standings_vs_team_seasons.json", diff)
    assert not diff, f"{len(diff)} teams differ (brief vs team_seasons), see 21_*.json: {list(diff.items())[:5]}"
    return "all 32 match"


@check("dispatcher loads; playoff override config has no warnings")
def _():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        from newsroom.swarm_dispatcher import SwarmDispatcher
        global SD
        SD = SwarmDispatcher(debug_mode=False)
    warn = [l for l in buf.getvalue().splitlines() if "playoff_overrides" in l]
    assert not warn, warn
    return "ok"


@check(f"Week {PUB_WEEK} slate commissions 10 slots and every prompt compiles")
def _():
    slate = SD.commission_weekly_editorial_slate(SEASON, PUB_WEEK)
    plan, prompts, problems = [], [], []
    for item in slate:
        a = item["agent_record"]; target = item.get("target")
        p = SD.compile_agent_prompt(a[0], a[1], a[2], a[3], a[4], target["team_id"] if target else a[5],
                                    BRIEF, SEASON, PUB_WEEK, "ARTICLE", article_type=item["article_type_id"], slot_target=target)
        full = p["system_instruction"] + p["task_instruction"]
        plan.append(f"{item['slot']}: {item['article_type_id']} -> {item['display_name']} ({item['archetype']})"
                    + (f" | subject {target['team_name']}" if target else "") + f" | mode {p['temporal_mode']}")
        prompts.append(f"\n\n{'=' * 80}\n{plan[-1]}\n{'=' * 80}\n{full}")
        if "You are an NFL media agent with archetype" in full:
            problems.append(f"{item['slot']}: fallback persona")
        if not p["temporal_mode"]:
            problems.append(f"{item['slot']}: no temporal mode")
        asks = injury_asks(p["task_instruction"])
        if asks:
            problems.append(f"{item['slot']}: asks about injuries: {asks[0][:120]}")
    save("30_weekly_plan.txt", "\n".join(plan))
    save("31_weekly_prompts.txt", "".join(prompts))
    assert len(slate) == 10, f"{len(slate)} slots"
    assert not problems, problems
    return " / ".join(plan)


@check("every article type compiles for every allowed writer, with no injury asks (all playoff overrides)")
def _():
    problems = []
    for at_id, info in SD.article_types_data.items():
        is_fantasy = at_id.startswith("FANTASY_")
        # Fantasy slates only run in Weeks 1–18; playoff overrides (Weeks 19–23) only apply to national types.
        weeks = [1, PUB_WEEK] if is_fantasy else ([PUB_WEEK] + [w for w in range(19, 24)])
        for writer in info["allowed_writers"]:
            rec = SD.get_agent_record(writer)
            for wk in weeks:
                p = SD.compile_agent_prompt(rec[0], rec[1], rec[2], rec[3], rec[4], rec[5], BRIEF, SEASON, wk,
                                            "ARTICLE", article_type=at_id)
                full = p["system_instruction"] + p["task_instruction"]
                if "You are an NFL media agent with archetype" in full:
                    problems.append(f"{at_id}/{writer}: fallback persona")
                if "ARTICLE STRUCTURE" not in full:
                    problems.append(f"{at_id}/{writer}/W{wk}: no structure section")
                if not p["temporal_mode"]:
                    problems.append(f"{at_id}/{writer}/W{wk}: no temporal mode")
                asks = injury_asks(p["task_instruction"])
                if asks:
                    problems.append(f"{at_id}/{writer}/W{wk}: asks about injuries: {asks[0][:100]}")
    problems = sorted(set(problems))
    assert not problems, problems[:15]
    return f"{len(SD.article_types_data)} types checked"


@check("local beat wakeup (dry run, nothing saved) plans the full portfolio")
def _():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        SD.dispatch_team_local(TEAM_ID, BRIEF, SEASON, PUB_WEEK, live=False, save_to_db=False, max_workers=1)
    out = buf.getvalue()
    save("40_local_wakeup.txt", out)
    line = [l for l in out.splitlines() if "Local beat portfolio" in l]
    assert line, "no portfolio line printed"
    assert "Local Beat" in out or "nothing new" in line[0], "no local beat articles dispatched"
    return line[0].strip()


@check("weekly news endpoint excludes 'Local Beat' articles")
def _():
    from server import api
    weeks = [PUB_WEEK, PUB_WEEK - 1]
    found = []
    for wk in weeks:
        res = api.get_weekly_news(wk, SEASON)
        found += [a["slot"] for a in res.get("articles", []) if str(a.get("slot", "")).startswith("Local Beat")]
        save(f"50_weekly_news_w{wk}.json", res)
    assert not found, f"Local Beat articles returned: {found}"
    return f"checked weeks {weeks}"


@check("agent memory: tables exist and every kind of agent's memory block builds")
def _():
    from newsroom import agent_memory
    out = {}
    with psycopg.connect(CONN) as conn, conn.cursor() as cur:
        assert agent_memory.tables_exist(cur), "memory tables missing (apply db/migrations/2026_10_20_agent_memory.sql)"
        cur.execute("""SELECT DISTINCT ON (archetype) agent_id, archetype, assigned_team_id FROM agent_memories
                       WHERE archetype IN ('BARNWELL', 'LOCAL_BEAT_REPORTER', 'DIE_HARD_FAN', 'SKIP_TWEET', 'ROUNDTABLE_PANEL')
                       ORDER BY archetype, id;""")
        for agent_id, arch, team in cur.fetchall():
            out[f"{arch} {agent_id}"] = agent_memory.build_memory_block(cur, agent_id, SEASON, PUB_WEEK, team) or "(no memories yet)"
        cur.execute("SELECT COUNT(*) FROM agent_memory_entries;"); n_e = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM agent_predictions;"); n_p = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM agent_season_summaries;"); n_s = cur.fetchone()[0]
    save("60_memory_blocks.json", out)
    return f"{len(out)} agents built; stored: {n_e} takes, {n_p} predictions, {n_s} summaries"


@check("agent memory write path: takes, tweet take, prediction, grading, summary (rolled back)")
def _():
    from newsroom import agent_memory
    from newsroom.power_rankings import load_team_index
    agent = "__verify_memory_agent__"
    W = LAST_DONE  # article published before Week W's games; its pick is graded from Week W + 1 on
    detail = {}
    conn = psycopg.connect(CONN)
    try:
        with conn.cursor() as cur:
            team_index = load_team_index(cur)
            nick = {tid: n for tid, n, _c, _a in team_index}
            cur.execute("SELECT home_team_id, away_team_id, home_score, away_score FROM games WHERE id = %s;", (GAME_IN_LAST,))
            home, away, hs, as_ = cur.fetchone()
            cur.execute("""INSERT INTO news_feed (season, week, content_type, persona, content)
                           VALUES (%s, %s, 'ARTICLE', 'VERIFY', 'verify') RETURNING id;""", (SEASON, W))
            art = cur.fetchone()[0]

            # predictions: parse a Projected Final line naming the real game, then store and grade it
            text = f"Analysis...\n**Projected Final: {nick[home]} 27, {nick[away]} 24**"
            preds = agent_memory.parse_score_predictions(text, team_index)
            assert len(preds) == 1, f"parsed {preds}"
            n_pred = agent_memory.store_predictions(cur, agent, SEASON, W, art, preds)
            assert n_pred == 1, f"stored {n_pred} predictions (game lookup failed?)"

            # takes: three extracted takes (one tagged with a team) and a tweet take
            takes = agent_memory.parse_takes_json(
                f'[{{"take": "The {nick[home]} are for real.", "team": "{nick[home]}", "player": ""}},'
                '{"take": "Regression is coming for somebody.", "team": "", "player": ""},'
                '{"take": "This league runs on one-score luck.", "team": "", "player": ""}]')
            assert agent_memory.store_takes(cur, agent, SEASON, W, art, takes, team_index) == 3
            agent_memory.store_tweet_take(cur, agent, SEASON, W, art, "Hot take tweet.", home)

            # cutoff: nothing from Week W is visible to a Week W article
            assert agent_memory.build_memory_block(cur, agent, SEASON, W, home) == "", "Week W memories leaked into Week W"

            # summary: agent is due before Week W + 1; inputs gather; store a summary
            due = dict(agent_memory.agents_due_for_summary(cur, SEASON, W + 1))
            assert agent in due, "agent not due for a summary"
            inp = agent_memory.summary_inputs(cur, agent, SEASON, W + 1, due[agent])
            assert len(inp["takes"]) == 4 and len(inp["predictions"]) == 1, f"summary inputs {inp}"
            agent_memory.summary_prompt(inp["name"], inp["identity"], inp["previous"], inp["takes"], inp["predictions"], W)
            agent_memory.store_summary(cur, agent, SEASON, W, "VERIFY summary text.")

            block = agent_memory.build_memory_block(cur, agent, SEASON, W + 1, home)
            detail["block"] = block
            expected_result = "you were right" if (hs > as_) else ("you were WRONG" if hs < as_ else "a tie")
            for needle in ("VERIFY summary text.", "Regression is coming", "picking winners", expected_result):
                assert needle in block, f"memory block missing {needle!r}"
    finally:
        conn.rollback()
        conn.close()
    with psycopg.connect(CONN) as c2, c2.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM agent_memory_entries WHERE agent_id = %s;", (agent,))
        assert cur.fetchone()[0] == 0, "rollback failed: test rows remain"
    save("61_memory_write_path.txt", detail.get("block", ""))
    return "parsed/stored 1 prediction, 4 takes, 1 summary; cutoff held; block graded the pick; all rolled back"


@check("roster moves: contract tools return JSON for a team, a re-signed player and the league")
def _():
    from newsroom import roster_moves as rm
    with psycopg.connect(CONN) as conn, conn.cursor() as cur:
        cur.execute("""SELECT p.id FROM transactions t JOIN players p ON p.id = t.player_id
                       WHERE t.season = %s AND t.type = 'RE_SIGNED' AND t.week = 1 ORDER BY t.contract_value DESC LIMIT 1;""",
                    (SEASON,))
        r = cur.fetchone()
    out = {}
    with article_run_context(SEASON, PUB_WEEK):
        out["cap_IND"] = json.loads(tool(db_tools.get_team_cap_sheet)("IND"))
        out["cap_league"] = json.loads(tool(db_tools.get_team_cap_sheet)(""))
        out["txns_league"] = json.loads(tool(db_tools.get_league_transactions)(limit=200))
        if r:
            out["contract_re_signed"] = json.loads(tool(db_tools.get_player_contract)(str(r[0])))
        with psycopg.connect(CONN) as conn, conn.cursor() as cur:
            rows = rm.annotate(db_tools.fetch_moves(cur, SEASON))
    reasons = {}
    for m in rows:
        for x in m["notable_reasons"] or (["elite (OVR only)"] if m["notable"] else []):
            reasons[x] = reasons.get(x, 0) + 1
    save("70_contract_tools.json", out)
    notable = sum(m["notable"] for m in rows)
    return (f"{len(rows)} visible moves for a Week {PUB_WEEK} article; notable {notable} (aim 15-35, JR tunes "
            f"roster_moves thresholds); by reason {reasons}")


@check("roster moves: week cutoff hides later rows")
def _():
    seen = {}
    for wk in (3, 4):
        with article_run_context(SEASON, wk):
            moves = json.loads(tool(db_tools.get_league_transactions)(limit=200))["moves"]
        seen[wk] = sorted({int(m.split(" · ")[0].split()[1]) for m in moves if m.startswith("Week ")})
    assert all(w < 3 for w in seen[3]), f"Week 3 article sees weeks {seen[3]}"
    assert 4 in seen[4], f"Week 4 article misses Week 4 moves: {seen[4]}"
    return f"weeks seen: {seen}"


lines = [f"{s:5}  {n}\n       {d}" for s, n, d in results]
summary = "\n".join(lines)
save("99_summary.txt", summary)
print(summary)
print(f"\n{sum(s == 'PASS' for s, _, _ in results)}/{len(results)} passed. Detail in verify_output/")
