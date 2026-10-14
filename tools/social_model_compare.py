"""Agent Wire model comparison (docs/SOCIAL_MATRIX.md build step 4).

Writes the same 20 social-desk assignments with each model and saves the posts, token usage and cost to a
JSON file for side-by-side review. Reads the database; writes nothing to it. Needs GEMINI_API_KEY (or .env).

  python3 tools/social_model_compare.py                      # 2035 Week 9, 3.8 Flash vs 3.5 Flash-Lite
  python3 tools/social_model_compare.py --week 18 --models gemini-3.8-flash gemini-3.5-flash-lite
"""
import argparse
import collections
import datetime
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newsroom.social_desk import load_week_context, plan_social_week  # noqa: E402
from newsroom.social_writer import build_post_prompt, load_api_key, write_post  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def main():
    ap = argparse.ArgumentParser(description="Write the same 20 Agent Wire posts with each model, for review.")
    ap.add_argument("--season", type=int, default=2035)
    ap.add_argument("--week", type=int, default=9)
    ap.add_argument("--models", nargs="+", default=["gemini-3.8-flash", "gemini-3.5-flash-lite"])
    ap.add_argument("--no-memory", action="store_true", help="leave out each agent's memory block")
    ap.add_argument("--out", help="output JSON (default: captures/social_compare/<season>_w<week>_<time>.json)")
    args = ap.parse_args()

    key = load_api_key()
    if not key:
        sys.exit("GEMINI_API_KEY is not set (environment or .env).")

    import psycopg
    from google import genai
    from db.db_handler import DatabaseHandler
    from newsroom import agent_memory

    conn_info = DatabaseHandler().db_conn_info
    personas = json.load(open(os.path.join(ROOT, "newsroom", "config", "agent_personas.json")))["archetypes"]
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
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
            try:
                return agent_memory.build_memory_block(cur, agent_id, args.season, args.week, team_id)
            except Exception:
                return ""

        def prompt_for(a, parent=None):
            acct = by_id[a["agent_id"]]
            team = ctx["teams"].get(acct.get("team_id") or -1, {}).get("name")
            return build_post_prompt(a, acct, personas.get(acct["archetype"], ""), team,
                                     memory(acct["agent_id"], a.get("team_id")), parent)

        prompts = [prompt_for(a) for a in picks]
        # Reply authors are fixed up front so both models reply as the same accounts.
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

    client = genai.Client(api_key=key)
    results = {"season": args.season, "week": args.week, "models": args.models, "memory": not args.no_memory,
               "created": datetime.datetime.now().isoformat(timespec="seconds"), "posts": []}
    for i, a in enumerate(picks + reply_assignments):
        acct = by_id[a["agent_id"]]
        results["posts"].append({"n": i + 1, "kind": a["kind"], "subject": a["subject"]["label"], "facts": a["facts"],
                                 "agent_id": a["agent_id"], "handle": acct["handle"], "display_name": acct["display_name"],
                                 "archetype": acct["archetype"], "reply_to": None, "by_model": {}})

    for model in args.models:
        print(f"Writing {len(picks)} posts with {model} ...")
        with ThreadPoolExecutor(max_workers=5) as ex:
            outs = list(ex.map(lambda sp: _safe(client, model, *sp), prompts))
        for p, o in zip(results["posts"], outs):
            p["by_model"][model] = o
        # Replies: each model replies to its own version of the parent post.
        for j, (parent_idx, author) in enumerate(reply_pairs):
            parent_post = results["posts"][parent_idx]
            parent_text = parent_post["by_model"][model].get("text") or ""
            ra = reply_assignments[j]
            sysmsg, prompt = build_post_prompt(ra, by_id[author["agent_id"]], personas.get(author["archetype"], ""),
                                               ctx["teams"].get(author.get("team_id") or -1, {}).get("name"), "",
                                               {"handle": parent_post["handle"], "display_name": parent_post["display_name"], "text": parent_text})
            post = results["posts"][len(picks) + j]
            post["reply_to"] = parent_idx + 1
            post["by_model"][model] = _safe(client, model, sysmsg, prompt)

    for model in args.models:
        rows = [p["by_model"][model] for p in results["posts"] if "usage" in p["by_model"][model]]
        cost = sum(r["cost_usd"] for r in rows)
        tok = lambda k: sum(r["usage"][k] for r in rows)
        flagged = sum(1 for r in rows if r["problems"])
        print(f"{model}: {len(rows)} posts, ${cost:.4f} total, ${cost / max(1, len(rows)):.5f}/post "
              f"(≈${cost / max(1, len(rows)) * 100:.2f} per 100), input {tok('input')}, output {tok('output')}, "
              f"thinking {tok('thinking')} tokens; {flagged} flagged")
    out = args.out or os.path.join(ROOT, "captures", "social_compare",
                                   f"{args.season}_w{args.week}_{datetime.datetime.now():%Y%m%d_%H%M%S}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=1, ensure_ascii=False, default=str)
    print(f"Saved: {out}")


def _safe(client, model, system, prompt):
    try:
        return {**write_post(client, model, system, prompt), "system": system, "prompt": prompt}
    except Exception as e:  # keep going; the review shows the error
        return {"error": f"{type(e).__name__}: {e}", "text": "", "problems": ["call failed"], "system": system, "prompt": prompt}


if __name__ == "__main__":
    main()
