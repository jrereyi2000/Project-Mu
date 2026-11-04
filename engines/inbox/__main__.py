"""
Front-office inbox CLI (FRONT_OFFICE_INBOX_PLAN.md §2.1, §2.10).

  python3 -m engines.inbox scan [--season S --week W] [--team CAR] [--no-llm] [--dry-run]
  python3 -m engines.inbox list --team CAR [--status OPEN] [--kind CONTRACT]
  python3 -m engines.inbox show <thread id>
  python3 -m engines.inbox control --team CAR [--role COACH|GM|PLAYER --persona ID] [--off]
  python3 -m engines.inbox flush-events

The database is pipeline.load_capture.DSN (DATABASE_URL). --dry-run rolls back.
"""
import argparse
import json
import sys

from engines.inbox import InboxError, actors, appraisal, scan as scan_mod, store, voice


def build_parser():
    p = argparse.ArgumentParser(prog="python3 -m engines.inbox", description="Front-office inbox")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="open inbound conversations for the controlled personas")
    s.add_argument("--season", type=int)
    s.add_argument("--week", type=int)
    s.add_argument("--team", action="append", help="a user team (repeatable); default: every registry user team")
    s.add_argument("--no-llm", action="store_true", help="templates only")
    s.add_argument("--dry-run", action="store_true", help="count triggers, store nothing")
    lst = sub.add_parser("list", help="a team's threads")
    lst.add_argument("--team", required=True)
    lst.add_argument("--status")
    lst.add_argument("--kind")
    lst.add_argument("--limit", type=int, default=50)
    sh = sub.add_parser("show", help="one thread and its messages")
    sh.add_argument("thread_id", type=int)
    c = sub.add_parser("control", help="list, take or release control of a persona")
    c.add_argument("--team", required=True)
    c.add_argument("--role", choices=actors.ROLES)
    c.add_argument("--persona")
    c.add_argument("--off", action="store_true", help="release control")
    sub.add_parser("flush-events", help="append pending relationship events to the relationships store")
    return p


def _connect():
    import psycopg
    from pipeline.load_capture import DSN
    return psycopg.connect(DSN)


def cmd_scan(cur, args):
    season, week = args.season, args.week
    if season is None or week is None:
        now = scan_mod.league_time(cur)
        if now is None:
            raise SystemExit("no league_state row; pass --season and --week")
        season = season if season is not None else now[0]
        week = week if week is not None else now[1]
    llm = None if args.no_llm else voice.default_llm()
    res = scan_mod.scan(cur, season, week, teams=args.team, llm=llm, dry_run=args.dry_run)
    print(json.dumps(res, indent=1, default=str))


def cmd_list(cur, args):
    team = actors.team_row(cur, args.team)
    if team is None:
        raise SystemExit(f"no team {args.team}")
    for t in store.list_threads(cur, team[0], status=args.status, kind=args.kind, limit=args.limit):
        dot = "*" if t["unread"] else " "
        print(f"{dot} {t['id']:>5}  {t['kind']:<10} {t['status']:<10} {t['actor_role']:<6} "
              f"{t['counterpart_name']}: {t['subject']}")


def cmd_show(cur, args):
    t = store.get_thread(cur, args.thread_id)
    if t is None:
        raise SystemExit(f"no thread {args.thread_id}")
    print(f"#{t['id']} {t['kind']} {t['status']}  {t['actor_role']} {t['actor_id']} <-> "
          f"{t['counterpart_type']} {t['counterpart_name']}: {t['subject']}")
    for m in store.messages(cur, t["id"]):
        tag = f" [{m['body_source']}]" if m["body_source"] in ("TEMPLATE", "LLM") else ""
        print(f"  {m['seq']:>2} {m['author']:<11}{tag} {m['body']}")
        if m.get("appraisal"):
            print(f"       appraisal: {m['appraisal'].get('outcome')} ({m['appraisal'].get('reason')})")
        if m["options"]:
            print("       options: " + ", ".join(o.get("id", "?") for o in m["options"]))


def cmd_control(cur, args):
    if args.role and args.persona:
        if args.off:
            actors.release_control(cur, args.team, args.role, args.persona)
            print(f"released {args.role} {args.persona} on {args.team.upper()}")
        else:
            a = actors.take_control(cur, args.team, args.role, args.persona)
            print(f"controlling {a.role} {a.name} ({a.persona_id}) on {a.abbr}")
        return
    print("controlled:", json.dumps([a.to_dict() for a in actors.controlled(cur, args.team)]))
    print("candidates:", json.dumps(actors.candidates(cur, args.team)))


def cmd_flush(cur, args):
    print(json.dumps(appraisal.flush(cur), indent=1, default=str))


COMMANDS = {"scan": cmd_scan, "list": cmd_list, "show": cmd_show, "control": cmd_control,
            "flush-events": cmd_flush}


def main(argv=None):
    args = build_parser().parse_args(argv)
    with _connect() as conn:
        cur = conn.cursor()
        try:
            COMMANDS[args.cmd](cur, args)
        except InboxError as e:
            conn.rollback()
            print(f"error ({e.status}): {e.message}", file=sys.stderr)
            return 1
        if getattr(args, "dry_run", False) or args.cmd in ("list", "show"):
            conn.rollback()
        else:
            conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
