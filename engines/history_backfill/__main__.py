"""
History backfill CLI (docs/HISTORY_BACKFILL_PLAN.md §3.8). Phase 2: the team arc.

  python3 -m engines.history_backfill generate [--seeds 3] [--pins data/history/pins.json] [--time-limit S]
  python3 -m engines.history_backfill apply --candidate N
  python3 -m engines.history_backfill revert
  python3 -m engines.history_backfill players [--seed 1]      # phase 3: stage player lines and fill-ins
  python3 -m engines.history_backfill apply-players --run N    # write a staged run (source SYNTH)
  python3 -m engines.history_backfill revert-players           # delete every SYNTH player row
  python3 -m engines.history_backfill link-facts               # link 2026-2034 awards / record rows to players
"""
import argparse

from engines.history_backfill import players, team_arc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="solve candidate histories into the staging tables and write the report")
    g.add_argument("--seeds", type=int, default=3)
    g.add_argument("--pins", default=team_arc.PINS_JSON)
    g.add_argument("--time-limit", type=int, default=180, help="seconds per candidate")
    a = sub.add_parser("apply", help="copy one staged candidate into the live tables (source SYNTH)")
    a.add_argument("--candidate", type=int, required=True)
    sub.add_parser("revert", help="delete every SYNTH row")
    pr = sub.add_parser("players", help="stage 2026-2034 player lines and fill-ins, and write their report")
    pr.add_argument("--seed", type=int, default=1)
    ap_ = sub.add_parser("apply-players", help="write a staged player-lines run into the live tables (source SYNTH)")
    ap_.add_argument("--run", type=int, required=True)
    sub.add_parser("revert-players", help="delete every SYNTH player row and the players the backfill created")
    sub.add_parser("link-facts", help="link the applied run's 2026-2034 awards and record-book rows to players")
    args = ap.parse_args()
    if args.cmd == "generate":
        for cid, status, problems in team_arc.generate(args.seeds, args.pins, args.time_limit):
            print(f"candidate {cid}: {status}; verify {'clean' if not problems else problems}")
        print(f"wrote {team_arc.REPORT_MD}")
    elif args.cmd == "apply":
        print(f"applied candidate {args.candidate}: {team_arc.apply(args.candidate)} team-seasons")
    elif args.cmd == "players":
        run_id, report = players.run(args.seed)
        print(f"run {run_id}: {report['players']} players, {report['lines']} lines, {report['fillins']} fill-ins, "
              f"{len(report['problems'])} problems; wrote {players.REPORT_MD}")
    elif args.cmd == "apply-players":
        print(f"applied run {args.run}: {players.apply_run(args.run)}")
    elif args.cmd == "link-facts":
        print(f"linked {players.link_applied_facts()} award and record rows")
    elif args.cmd == "revert-players":
        print(f"deleted {players.revert_players()} SYNTH player rows")
    else:
        print(f"deleted {team_arc.revert()} SYNTH rows")


if __name__ == "__main__":
    main()
