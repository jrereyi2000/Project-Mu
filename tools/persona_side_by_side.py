"""Writer persona side-by-side (docs/PERSONALITY_PLAN.md phase 1).

Writes the same national articles twice, once with the personas and writer slate filters from before phase 1
(read from git at OLD_REV) and once with the current ones, and saves both versions with their persona blocks to
verify_output/. Reads the database (writer tools, briefs, recent work); saves no article, takes or memory. Gemini
calls are logged to llm_usage as usual. Needs GEMINI_API_KEY (or .env).

  python3 tools/persona_side_by_side.py                          # 2036: Week 12 slate + Barnwell in Weeks 8, 12, 16
  python3 tools/persona_side_by_side.py --week 10 --slate 4 --barnwell-weeks 6 10 14
"""
import argparse
import datetime
import json
import os
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_REV = "6b8aa81"     # last commit before the phase 1 personas


def old_config(name: str) -> dict:
    out = subprocess.run(["git", "show", f"{OLD_REV}:newsroom/config/{name}"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    return json.loads(out)


def old_dispatcher() -> SwarmDispatcher:
    """A dispatcher with the pre-phase-1 article voices (article_personas over archetypes) and slate filters."""
    d = SwarmDispatcher(debug_mode=False)
    personas = old_config("agent_personas.json")
    article = {k: v for k, v in personas.get("article_personas", {}).items() if not k.startswith("_")}
    d.personas_data = {k: article.get(k, v) for k, v in personas["archetypes"].items()}
    d.curatorial_autonomy_directives = old_config("curatorial_autonomy_directives.json")
    return d


def persona_block(system_instruction: str) -> str:
    return system_instruction.split("ROLE & PERSONA:", 1)[-1].split("IDENTITY & HANDLE:", 1)[0].strip()


def measures(text: str) -> dict:
    t = text or ""
    return {
        "words": len(re.findall(r"\b\w+\b", t)),
        "regression_mentions": len(re.findall(r"regress", t, re.I)),
        "regression_warning_section": bool(re.search(r"^#+.*regression warning", t, re.I | re.M)),
        "pythagorean_mentions": len(re.findall(r"pythag", t, re.I)),
        "headings": len(re.findall(r"^#{2,3} ", t, re.M)),
    }


def jobs_for(new: SwarmDispatcher, season: int, week: int, slate: int, barnwell_weeks: list, seed: int) -> list:
    random.seed(seed)
    items = [i for i in new.commission_weekly_editorial_slate(season, week)
             if i["archetype"] != "LOCAL_BEAT_REPORTER" and not i["article_type_id"].startswith("FANTASY_")][:slate]
    jobs = [dict(week=week, **{k: i[k] for k in ("slot", "article_type_id", "archetype", "agent_record", "target")})
            for i in items]
    rec = new.get_agent_record("BARNWELL")
    for w in barnwell_weeks:
        if not any(j["archetype"] == "BARNWELL" and j["week"] == w for j in jobs):
            jobs.append(dict(week=w, slot="Barnwell column", article_type_id="NATIONAL_OPINION_COLUMN",
                             archetype="BARNWELL", agent_record=rec, target=None))
    return jobs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--season", type=int, default=2036)
    ap.add_argument("--week", type=int, default=12, help="week whose commissioned national slate is sampled")
    ap.add_argument("--slate", type=int, default=4, help="articles taken from that slate (beat and fantasy skipped)")
    ap.add_argument("--barnwell-weeks", type=int, nargs="*", default=[8, 12, 16])
    ap.add_argument("--model", default="gemini-3.8-flash")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    new, old = SwarmDispatcher(debug_mode=False), old_dispatcher()
    if not new.api_key:
        sys.exit("GEMINI_API_KEY is not set (environment or .env).")
    jobs = jobs_for(new, args.season, args.week, args.slate, args.barnwell_weeks, args.seed)
    briefs = {w: build_weekly_assignment_brief(new.db.db_conn_info, args.season, w) for w in {j["week"] for j in jobs}}

    tasks = []
    for n, j in enumerate(jobs):
        a, target = j["agent_record"], j["target"]
        team_id = target["team_id"] if target else a[5]
        for label, d in (("old", old), ("new", new)):
            p = d.compile_agent_prompt(a[0], a[1], a[2], a[3], a[4], team_id, briefs[j["week"]], args.season,
                                       j["week"], "ARTICLE", article_type=j["article_type_id"], slot_target=target)
            tasks.append((n, label, d, p, team_id, j))
    print(f"{len(jobs)} articles x 2 versions = {len(tasks)} Gemini runs ({args.model})")

    def run(t):
        n, label, d, p, team_id, j = t
        res = d._execute_agent(p, args.season, j["week"], team_id, live=True, model=args.model, save_to_db=False,
                               slot=j["slot"], article_type=j["article_type_id"])
        print(f"  done #{n + 1} {label}: {j['archetype']} {j['article_type_id']} W{j['week']}")
        return n, label, p, res

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(run, tasks))

    out = {"season": args.season, "model": args.model, "old_rev": OLD_REV,
           "created": datetime.datetime.now().isoformat(timespec="seconds"), "articles": []}
    for n, j in enumerate(jobs):
        entry = {"n": n + 1, "week": j["week"], "slot": j["slot"], "article_type": j["article_type_id"],
                 "archetype": j["archetype"], "subject": (j["target"] or {}).get("team_name")}
        for _, label, p, res in (r for r in results if r[0] == n):
            entry[label] = {"persona_block": persona_block(p["system_instruction"]), "headline": res.get("headline"),
                            "text": res.get("text"), "error": res.get("error"), **measures(res.get("text"))}
        out["articles"].append(entry)

    os.makedirs(os.path.join(ROOT, "verify_output"), exist_ok=True)
    stem = os.path.join(ROOT, "verify_output", f"persona_side_by_side_{args.season}_w{args.week}")
    with open(stem + ".json", "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    with open(stem + ".md", "w") as f:
        f.write(f"# Persona side-by-side, {args.season} (old = {OLD_REV}, new = phase 1)\n\n")
        for e in out["articles"]:
            f.write(f"\n---\n\n## {e['n']}. {e['archetype']} · {e['article_type']} · Week {e['week']}"
                    f"{' · ' + e['subject'] if e['subject'] else ''}\n")
            for label in ("old", "new"):
                v = e.get(label, {})
                m = {k: v.get(k) for k in ("words", "regression_mentions", "regression_warning_section", "headings")}
                f.write(f"\n### {label.upper()}: {v.get('headline')}\n\n`{m}`\n\n{v.get('text') or v.get('error')}\n")
    print(f"Saved {stem}.md and .json")


if __name__ == "__main__":
    main()
