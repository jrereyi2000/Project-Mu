"""Agent Wire model comparison (docs/done/SOCIAL_MATRIX.md build step 4).

Writes the same 20 social-desk assignments with each model and saves the posts, token usage and cost to a
JSON file for side-by-side review. Reads the database; writes nothing to it. Needs GEMINI_API_KEY (or .env)
only when a `gemini-*` model is among `--models`: an OpenCode Go list runs on OPENCODE_GO_API_KEY, and its
cost line says "Go allowance (not $0 free)" instead of a $0.00 that reads as free (AUDIT R6 F4).

  python3 tools/social_model_compare.py                      # 2035 Week 9, 3.8 Flash vs 3.5 Flash-Lite
  python3 tools/social_model_compare.py --week 18 --models gemini-3.8-flash gemini-3.5-flash-lite
  python3 tools/social_model_compare.py --models gemini-3.8-flash gemini-3.8-flash:low   # model:thinking level
  python3 tools/social_model_compare.py --models glm-5.3-flash        # Go only: no GEMINI_API_KEY needed

Prompt A/B (docs/done/AGENT_WIRE_VOICE_PLAN.md §2.6): `--prompts old|new|both` writes the same sampled
assignments with the pre-U2 post writer (loaded from git at OLD_WRITER_REV) and/or the current one, side by side
in captures/social_compare/. `both` also writes a blind-read Markdown file with the answer key at the end.
`--dry-run` builds and saves the prompt pairs without any model call (useful for checking the pairing offline).

  python3 tools/social_model_compare.py --prompts both --db "host=localhost dbname=madden_2036 user=postgres password=postgres"
  python3 tools/social_model_compare.py --prompts both --dry-run --db "..."
"""
import argparse
import collections
import datetime
import json
import os
import random
import subprocess
import sys
import types
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newsroom.social_desk import load_week_context, plan_social_week  # noqa: E402
from newsroom import social_writer as new_writer  # noqa: E402
from newsroom.social_writer import load_api_key  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Last commit before the U2 post-writer rewrite (newsroom/social_writer.py); the old prompt side loads this.
OLD_WRITER_REV = "0b73ab5"

# How many of each kind to sample (20 in all). Shares are used only when the week has stored articles;
# otherwise their places go to reactions.
QUOTA = [("REACTION", 6), ("PERFORMANCE", 3), ("BEAT", 2), ("STANDINGS", 1), ("PREVIEW", 2),
         ("MEME", 2), ("SHARE", 2)]
REPLIES = 2


def sample(plan: dict) -> list:
    """20 assignments covering every kind and every kind of account, chosen the same way each run."""
    by = collections.defaultdict(list)
    for a in plan["assignments"]:
        if a["agent_id"]:
            by[a["kind"]].append(a)
    out = []
    shortfall = 0
    for kind, n in QUOTA:
        pool = by[kind]
        if kind == "REACTION":
            # mix: both fan bases of the top game, a pundit, a bandwagon fan, then more fans
            fans = [a for a in pool if a["archetype"] == "DIE_HARD_FAN"]
            others = [a for a in pool if a["archetype"] != "DIE_HARD_FAN"]
            pool = fans[:2] + others[:2] + fans[2:]
        if kind == "PREVIEW":
            fan = [a for a in pool if a["subject"]["type"] == "game"][:1]
            fantasy = [a for a in pool if a["subject"]["type"] == "fantasy"][:1]
            pool = fan + fantasy + [a for a in pool if a not in fan + fantasy]
        take = pool[:n]
        shortfall += n - len(take)
        out += take
    if shortfall:
        used = {id(a) for a in out}
        out += [a for a in by["REACTION"] if id(a) not in used][:shortfall]
    return out


def reply_author(parent_assignment, ctx, accounts, used):
    """A rival fan from the other team in the parent's game, else a pundit who isn't the parent's author."""
    tid = parent_assignment.get("team_id")
    gid = parent_assignment["subject"].get("id")
    game = next((g for g in ctx["recap_games"] if g["id"] == gid), None)
    if game and tid:
        rival = game["away_team_id"] if game["home_team_id"] == tid else game["home_team_id"]
        for a in accounts:
            if a["archetype"] == "DIE_HARD_FAN" and a["team_id"] == rival and a["agent_id"] not in used:
                return a
    for arch in ("SHANNON", "STEPHEN_A", "MCAFEE", "COWHERD"):
        for a in accounts:
            if a["archetype"] == arch and a["agent_id"] != parent_assignment["agent_id"] and a["agent_id"] not in used:
                return a
    return None


def load_old_writer(rev: str = OLD_WRITER_REV):
    """The pre-U2 newsroom/social_writer.py read out of git, as a module.

    Old code is loaded the way tools/persona_side_by_side.py reads old config: from `git show`, not from the
    working tree. The module still imports current helpers (personas, fantasy_desk, llm_provider) at call time."""
    try:
        src = subprocess.run(["git", "show", f"{rev}:newsroom/social_writer.py"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        sys.exit(f"Cannot load the old social_writer from git ({rev}): {exc}")
    module = types.ModuleType(f"social_writer_old_{rev.replace('-', '_')}")
    module.__file__ = f"<git {rev}:newsroom/social_writer.py>"
    exec(compile(src, module.__file__, "exec"), module.__dict__)
    # The pre-U2 write_post uses `types.Content` in its rewrite path without importing it; supply it.
    try:
        from google.genai import types as genai_types
        module.types = genai_types
    except Exception:                                  # google-genai is optional at prompt-build time
        pass
    return module


def _savepoint(cur, name, fn, default):
    """Run a helper inside a savepoint: a missing table/column in an archive database rolls back and returns
    `default` instead of leaving the read-only transaction aborted."""
    cur.execute(f"SAVEPOINT {name}")
    try:
        out = fn()
    except Exception:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        out = default
    else:
        cur.execute(f"RELEASE SAVEPOINT {name}")
    return out


def side_key(side: str, model: str, prompts: str) -> str:
    """`by_model` key: bare model for the default/new-only layout, `side:model` for old/both."""
    return model if prompts == "new" else f"{side}:{model}"


def build_prompt(writer, assignment, account, persona, team_name, memory_block="", parent=None):
    """One (system, prompt) pair with the given writer version. Old and new signatures share the first six args."""
    return writer.build_post_prompt(assignment, account, persona, team_name, memory_block, parent)


def save_blind_markdown(path: str, results: dict, season: int, week: int, prompts: str):
    """Old/new posts side by side under A/B labels, answer key at the end (JR's blind read)."""
    flip = random.Random(f"{season}-{week}").random() < 0.5
    blind = {"A": "old", "B": "new"} if flip else {"A": "new", "B": "old"}
    lines = [f"# Agent Wire prompt A/B, {season} Week {week} (blind)", "",
             "The same assignment written with each prompt version. Read A against B; the answer key is at the end.", ""]
    for post in results["posts"]:
        lines.append(f"## {post['n']}. {post['kind']} — {post['subject']}")
        lines.append("")
        for model in results["models"]:
            if len(results["models"]) > 1:
                lines.append(f"### {model}")
                lines.append("")
            for label in ("A", "B"):
                key = side_key(blind[label], model, prompts)
                out = post["by_model"].get(key, {})
                text = out.get("text") or out.get("error") or ""
                lines.append(f"**{label}:** {text}")
                lines.append("")
        lines.append("---")
        lines.append("")
    lines.append(f"Answer key: A = {blind['A']}, B = {blind['B']}")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Write the same 20 Agent Wire posts with each model, for review.")
    ap.add_argument("--season", type=int, default=2035)
    ap.add_argument("--week", type=int, default=9)
    ap.add_argument("--models", nargs="+", default=["gemini-3.8-flash", "gemini-3.5-flash-lite"])
    ap.add_argument("--no-memory", action="store_true", help="leave out each agent's memory block")
    ap.add_argument("--prompts", choices=("old", "new", "both"), default="new",
                    help="write with the pre-U2 prompt (old), the current prompt (new), or both")
    ap.add_argument("--db", help="Postgres DSN to read (default: the DatabaseHandler connection)")
    ap.add_argument("--dry-run", action="store_true", help="build and save the prompt pairs; make no model calls")
    ap.add_argument("--out", help="output JSON (default: captures/social_compare/<season>_w<week>_<time>.json)")
    args = ap.parse_args()

    from newsroom.tool_loop.go_transport import is_go_model
    gemini_models = [m for m in args.models if not is_go_model(m)]
    key = load_api_key()
    if gemini_models and not key and not args.dry_run:
        sys.exit(f"GEMINI_API_KEY is not set (environment or .env) and {gemini_models[0]} is a Gemini model.")

    import psycopg
    if args.db:
        conn_info = args.db
    else:
        from db.db_handler import DatabaseHandler
        conn_info = DatabaseHandler().db_conn_info
    from newsroom import agent_memory
    from newsroom.personas import build_persona_block, load_personas

    personas = load_personas()
    sides = ["new"] if args.prompts == "new" else ["old"] if args.prompts == "old" else ["old", "new"]
    writers = {"new": new_writer}
    if "old" in sides:
        writers["old"] = load_old_writer()

    conn = psycopg.connect(conn_info)
    # One read-only transaction: the planner's savepoint-guarded optional tables work, and nothing can write.
    try:
        conn.read_only = True
    except Exception:
        pass
    with conn, conn.cursor() as cur:
        ctx = load_week_context(cur, args.season, args.week)
        cur.execute("SELECT agent_id, archetype, assigned_team_id, persistent_memory, handle, display_name FROM agent_memories")
        accounts = [dict(zip(("agent_id", "archetype", "team_id", "persistent_memory", "handle", "display_name"), r))
                    for r in cur.fetchall()]
        by_id = {a["agent_id"]: a for a in accounts}
        plan = plan_social_week(ctx)
        picks = sample(plan)

        def memory(agent_id, team_id):
            if args.no_memory:
                return ""
            return _savepoint(cur, "sc_memory",
                              lambda: agent_memory.build_memory_block(cur, agent_id, args.season, args.week, team_id), "")

        def base_prompt(writer, a, parent=None):
            acct = by_id[a["agent_id"]]
            team = ctx["teams"].get(acct.get("team_id") or -1, {}).get("name")
            persona = _savepoint(
                cur, "sc_persona",
                lambda: build_persona_block(cur, personas.get(acct["archetype"], ""), agent_id=acct["agent_id"],
                                            season=args.season, week=args.week, fmt="POST", post_kind=writer.kind_key(a),
                                            position=(a.get("facts") or {}).get("position"), team_id=a.get("team_id")),
                personas.get(acct["archetype"], ""))
            return build_prompt(writer, a, acct, persona, team,
                                memory(acct["agent_id"], a.get("team_id")), parent)

        prompts_by_side = {side: [base_prompt(writers[side], a) for a in picks] for side in sides}

        # Reply authors are fixed up front so every model and both prompt versions reply as the same accounts.
        used = {a["agent_id"] for a in picks}
        reply_pairs = []
        for parent in picks[:REPLIES * 3]:
            if len(reply_pairs) == REPLIES:
                break
            author = reply_author(parent, ctx, accounts, used)
            if author:
                used.add(author["agent_id"])
                reply_pairs.append((picks.index(parent), author))
        reply_assignments = [{"kind": "REPLY", "agent_id": au["agent_id"], "handle": au["handle"], "archetype": au["archetype"],
                              "team_id": au["team_id"], "subject": {"type": "post", "id": None, "label": f"reply to #{i + 1}"},
                              "window": "", "facts": picks[i]["facts"]} for i, au in reply_pairs]

    client = None
    if not args.dry_run and gemini_models:
        # A Go-only list needs no Gemini client at all: write_post builds Go's from OPENCODE_GO_API_KEY.
        from google import genai
        client = genai.Client(api_key=key)

    results = {"season": args.season, "week": args.week, "models": args.models, "memory": not args.no_memory,
               "prompts": args.prompts, "dry_run": args.dry_run,
               "created": datetime.datetime.now().isoformat(timespec="seconds"), "posts": []}
    for i, a in enumerate(picks + reply_assignments):
        acct = by_id[a["agent_id"]]
        results["posts"].append({"n": i + 1, "kind": a["kind"], "subject": a["subject"]["label"], "facts": a["facts"],
                                 "agent_id": a["agent_id"], "handle": acct["handle"], "display_name": acct["display_name"],
                                 "archetype": acct["archetype"], "reply_to": None, "by_model": {}})

    if args.dry_run:
        results["prompt_pairs"] = {}
        for side in sides:
            writer = writers[side]
            base = [{"n": i + 1, "kind": picks[i]["kind"], "system": s, "prompt": p}
                    for i, (s, p) in enumerate(prompts_by_side[side])]
            replies = []
            for j, (parent_idx, author) in enumerate(reply_pairs):
                acct = by_id[author["agent_id"]]
                team = ctx["teams"].get(acct.get("team_id") or -1, {}).get("name")
                sysmsg, prompt = build_prompt(
                    writer, reply_assignments[j], acct, personas.get(acct["archetype"], ""), team, "",
                    {"handle": results["posts"][parent_idx]["handle"],
                     "display_name": results["posts"][parent_idx]["display_name"], "text": ""})
                replies.append({"n": len(picks) + j + 1, "kind": "REPLY", "system": sysmsg, "prompt": prompt})
            results["prompt_pairs"][side] = {"writer_rev": OLD_WRITER_REV if side == "old" else "working tree",
                                             "base": base, "replies": replies}
        print(f"Dry run: built {len(sides)} prompt side(s) x {len(picks)} assignments "
              f"(+{len(reply_assignments)} replies); no model calls.")
    else:
        for side in sides:
            writer = writers[side]
            for model in args.models:
                key_ = side_key(side, model, args.prompts)
                label = model if args.prompts == "new" else f"{side}:{model}"
                print(f"Writing {len(picks)} posts with {label} ...")
                with ThreadPoolExecutor(max_workers=5) as ex:
                    outs = list(ex.map(lambda sp, w=writer, m=model: _safe(w, client, m, *sp), prompts_by_side[side]))
                for p, o in zip(results["posts"], outs):
                    p["by_model"][key_] = o
                # Replies: each model replies to its own version of the parent post, in the same prompt version.
                for j, (parent_idx, author) in enumerate(reply_pairs):
                    parent_post = results["posts"][parent_idx]
                    parent_text = parent_post["by_model"][key_].get("text") or ""
                    acct = by_id[author["agent_id"]]
                    team = ctx["teams"].get(acct.get("team_id") or -1, {}).get("name")
                    sysmsg, prompt = build_prompt(
                        writer, reply_assignments[j], acct, personas.get(acct["archetype"], ""), team, "",
                        {"handle": parent_post["handle"], "display_name": parent_post["display_name"], "text": parent_text})
                    post = results["posts"][len(picks) + j]
                    post["reply_to"] = parent_idx + 1
                    post["by_model"][key_] = _safe(writer, client, model, sysmsg, prompt)
        for side in sides:
            for model in args.models:
                key_ = side_key(side, model, args.prompts)
                label = model if args.prompts == "new" else f"{side}:{model}"
                rows = [p["by_model"][key_] for p in results["posts"] if "usage" in p["by_model"].get(key_, {})]
                tok = lambda k: sum(r["usage"][k] for r in rows)
                flagged = sum(1 for r in rows if r["problems"])
                print(f"{label}: {len(rows)} posts, {cost_text(model, rows)}, input {tok('input')}, "
                      f"output {tok('output')}, thinking {tok('thinking')} tokens; {flagged} flagged")

    out = args.out or os.path.join(ROOT, "captures", "social_compare",
                                   f"{args.season}_w{args.week}_{datetime.datetime.now():%Y%m%d_%H%M%S}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=1, ensure_ascii=False, default=str)
    print(f"Saved: {out}")
    if args.prompts == "both" and not args.dry_run:
        md = os.path.splitext(out)[0] + ".md"
        save_blind_markdown(md, results, args.season, args.week, args.prompts)
        print(f"Blind read: {md}")


def cost_text(model: str, rows: list) -> str:
    """The cost column of the summary: dollars for a per-token model, an allowance label for a Go one.

    AUDIT R6 F4: an OpenCode Go post returns `cost_usd: 0.0` because the subscription pays for it, not because
    it was free. Printing `$0.0000` next to Gemini's dollar cost reads as "free", so the label says otherwise."""
    cost = sum(r.get("cost_usd", 0.0) for r in rows)
    note = new_writer.cost_note(model)
    if note:
        return f"{note}, {len(rows)} posts"
    per = cost / max(1, len(rows))
    return f"${cost:.4f} total, ${per:.5f}/post (≈${per * 100:.2f} per 100)"


def _safe(writer, client, model, system, prompt):
    try:
        return {**writer.write_post(client, model, system, prompt), "system": system, "prompt": prompt}
    except Exception as e:  # keep going; the review shows the error
        return {"error": f"{type(e).__name__}: {e}", "text": "", "problems": ["call failed"], "system": system, "prompt": prompt}


if __name__ == "__main__":
    main()
