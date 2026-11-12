"""CLI: ``python3 -m tools.mod_check run|capture`` (VISUAL_MOD_CHECK_PLAN sections 2.1/2.3).

``run``
    ``python3 -m tools.mod_check run --save CAREER-TUNE-<name> --mod <slug>
       [--segments game_start,halftime] [--dry-run] [--run-dir DIR] [--pack P]
       [--reuse-launch] [--data-dir DIR]``

    Launches the plan's own test pack through the pilot's launch job (an already-running Madden
    is refused), clones the save, records the plan's segments, scores them and writes
    ``data/mod_checks/runs/<stamp>_<slug>/report.json`` and ``report.md`` (or ``--run-dir``,
    which must stay inside the check folder), prints the markdown, and sends one ntfy line.
    Exit 0 pass, 1 fail, 2 unknown or refused (the pilot's convention). ``--dry-run`` prints the
    segment plan and the check specs and scores nothing: no clone, no driver, no frame, no ntfy.
    ``--reuse-launch`` skips the launch and attaches to the same verified Madden process and
    unchanged applied pack a previous cold run recorded in ``<mod>/launch.json`` (F6); it
    refuses unknown, stale, wrong-pack or changed state instead of relaunching.

``capture``
    ``python3 -m tools.mod_check capture --mod <slug> --segment <name>
       --pass-at M:SS --fail-at M:SS [--asset NAME] [--yes] [--run-dir DIR]
       [--reuse-launch] [--data-dir DIR]``

    Cuts the two named moments out of one segment's frames (a previous ``run``'s frames in
    ``--run-dir``, else the newest run for the mod), shows both crops and their similarity
    numbers, and writes ``refs/<asset>_pass.png`` / ``refs/<asset>_native.png`` only with
    ``--yes``. A pair that the margin cannot separate is refused, with the numbers shown. Give
    ``--save CAREER-TUNE-<name>`` to record the segment live instead of loading frames.

``--data-dir`` (or ``MU_MOD_CHECKS_DIR``) moves the git-ignored root for tests and temp fixtures;
the default is ``data/mod_checks``. Every written path must stay inside it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.mod_check import runner
from tools.mod_check.segments import PlanError


def _segments(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _spec_text(spec: dict) -> str:
    parts = []
    if spec.get("region"):
        parts.append(f"region {list(spec['region'])}")
    colour = spec.get("colour")
    if isinstance(colour, dict):
        parts.append(f"colour hue {colour.get('hue')} pass_at {colour.get('pass_at')}"
                     + (f" min_sat {colour['min_sat']}" if "min_sat" in colour else ""))
    reference = spec.get("reference")
    if isinstance(reference, dict):
        parts.append(f"reference pass {reference.get('pass')} fail {reference.get('fail')} "
                     f"margin {reference.get('margin', 0.05)}")
    return "; ".join(parts) or "(no measures)"


def dry_run_text(result: dict) -> str:
    """The plan ``run --dry-run`` prints, before anything is touched."""
    segments = result.get("segments") or []
    lines = [f"mod_check plan: {result.get('mod')} ({result.get('mod_dir')})",
             f"pack: {result.get('pack_required') or 'MISSING (a run refuses without one)'} "
             f"(the run launches this pack itself and verifies the launch receipt)",
             f"save: {result.get('save')} (dry run: would clone it to "
             f"{result.get('would_clone')} and score that clone)",
             "", "segments:"]
    for segment in segments:
        cue = segment.get("cue") or "no cue"
        lines.append(f"  {segment.get('name')}: cue {cue}, record_s {segment.get('record_s')}, "
                     f"fps {segment.get('fps')}, width {segment.get('width')}, "
                     f"preroll {segment.get('preroll_s')}s")
        for asset in segment.get("checks") or []:
            lines.append(f"    check {asset}")
    lines += ["", "checks:"]
    for name, spec in (result.get("checks") or {}).items():
        lines.append(f"  {name}: {_spec_text(spec)}")
    lines += ["", "dry run: nothing recorded, nothing cloned, nothing scored, no notice sent."]
    return "\n".join(lines)


def capture_text(result: dict) -> str:
    """The preview ``capture`` prints: both numbers, both crops, and whether it wrote them."""
    lines = [f"mod_check capture: {result.get('mod')} / {result.get('segment')} / "
             f"{result.get('asset')}"]
    for key, label, moment in (("pass", "pass frame", result.get("pass_at")),
                               ("fail", "fail frame", result.get("fail_at"))):
        measures = (result.get("similarities") or {}).get(key) or {}
        lines.append(f"  {label} ({moment}): "
                     f"similarity_pass={float(measures.get('similarity_pass', 0)):.3f} "
                     f"similarity_fail={float(measures.get('similarity_fail', 0)):.3f} "
                     f"-> {(result.get('verdicts') or {}).get(key)}")
    lines.append(f"  margin: {result.get('margin')}")
    preview = result.get("preview") or {}
    if preview:
        lines.append(f"  preview pass:   {preview.get('pass')}")
        lines.append(f"  preview native: {preview.get('native')}")
    why = result.get("why")
    if why:
        lines.append(f"  {why}")
    paths = result.get("paths") or {}
    if result.get("saved"):
        lines.append(f"  wrote pass:   {paths.get('pass')}")
        lines.append(f"  wrote native: {paths.get('native')}")
    elif paths:
        lines.append(f"  would write pass:   {paths.get('pass')}")
        lines.append(f"  would write native: {paths.get('native')}")
        lines.append("  nothing written: re-run with --yes once the crops look right")
    if result.get("refused"):
        lines.append(f"  refused: {result['refused']}")
    operational = result.get("operational") or {}
    if operational.get("status") not in (None, "complete"):
        lines.append(f"  run status: {operational.get('status')}: {operational.get('why')}")
        if operational.get("recovery"):
            lines.append(f"  recovery: {operational['recovery']}")
    return "\n".join(lines)


def _run(args: argparse.Namespace) -> int:
    result = runner.run(args.save, args.mod, segments=_segments(args.segments),
                        run_dir=args.run_dir, dry_run=args.dry_run, data_dir=args.data_dir,
                        pack=args.pack, reuse=args.reuse_launch)
    if result.get("status") == "dry_run":
        print(dry_run_text(result))
        return 0
    if result.get("status") == "refused":
        print(f"mod_check: refused: {result.get('why')}", file=sys.stderr)
        return 2
    if result.get("status") == "stopped":
        print(f"mod_check: stopped: {result.get('why')}", file=sys.stderr)
        return 2
    print(f"mod_check: scored clone {result.get('clone')} "
          f"(cloned from {result.get('source_save')}, pack {result.get('pack')})")
    markdown = Path(result["report_md"]).read_text(encoding="utf-8")
    print(markdown, end="" if markdown.endswith("\n") else "\n")
    if result.get("status") == "incomplete":
        operational = result.get("operational") or {}
        print(f"mod_check: INCOMPLETE: {operational.get('why')}", file=sys.stderr)
        if operational.get("recovery"):
            print(f"mod_check: recovery: {operational['recovery']}", file=sys.stderr)
        return 2
    return {"pass": 0, "fail": 1}.get(str(result.get("status")), 2)


def _capture(args: argparse.Namespace) -> int:
    result = runner.capture(args.mod, args.segment, args.pass_at, args.fail_at,
                            asset=args.asset, yes=args.yes, save=args.save,
                            run_dir=args.run_dir, data_dir=args.data_dir,
                            reuse=args.reuse_launch)
    print(capture_text(result))
    if result.get("status") == "refused":
        print(f"mod_check capture: refused: {result.get('why')}", file=sys.stderr)
        return 2
    if result.get("status") == "stopped":
        print(f"mod_check capture: stopped: {result.get('why')}", file=sys.stderr)
        return 2
    operational = result.get("operational") or {}
    if operational.get("status") not in (None, "complete"):
        print(f"mod_check capture: INCOMPLETE: {operational.get('why')}", file=sys.stderr)
        if operational.get("recovery"):
            print(f"mod_check capture: recovery: {operational['recovery']}", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=None,
                        help="mod-check folder (default data/mod_checks or MU_MOD_CHECKS_DIR)")
    common.add_argument("--run-dir", default=None,
                        help="where to write or read the run (must be inside --data-dir)")

    parser = argparse.ArgumentParser(
        prog="python3 -m tools.mod_check",
        description="Record a Madden 27 game and say whether a mod's assets appeared.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", parents=[common],
                                help="clone a TUNE save, record its segments, score the assets")
    run_parser.add_argument("--save", required=True,
                            help="CAREER-TUNE-<name>; the run clones it and scores the clone")
    run_parser.add_argument("--mod", required=True, help="mod slug: data/mod_checks/<slug>")
    run_parser.add_argument("--segments", default=None,
                            help="comma-separated segment names (default: every segment)")
    run_parser.add_argument("--dry-run", action="store_true",
                            help="print the plan and score nothing: no clone, no PC, no notice")
    run_parser.add_argument("--pack", default=None,
                            help="cross-check the plan's expected pack; the run launches that pack "
                                 "itself and verifies the launch receipt")
    run_parser.add_argument("--reuse-launch", action="store_true",
                            help="attach to the verified Madden a previous cold run launched "
                                 "(same pack, same process identity, unchanged applied pack); "
                                 "refuses unknown/stale/wrong-pack state and never relaunches")
    run_parser.set_defaults(func=_run)

    cap_parser = sub.add_parser("capture", parents=[common],
                                help="cut one segment's pass/native moments into refs/")
    cap_parser.add_argument("--mod", required=True, help="mod slug: data/mod_checks/<slug>")
    cap_parser.add_argument("--segment", required=True, help="segment name from segments.json")
    cap_parser.add_argument("--pass-at", required=True, help="the mod's moment, M:SS")
    cap_parser.add_argument("--fail-at", required=True, help="the native moment, M:SS")
    cap_parser.add_argument("--asset", default=None,
                            help="check name (default: the segment's first check)")
    cap_parser.add_argument("--yes", action="store_true",
                            help="write the pair after you have looked at the previews")
    cap_parser.add_argument("--reuse-launch", action="store_true",
                            help="live capture only: attach to the verified Madden a previous "
                                 "cold run launched instead of relaunching it")
    cap_parser.add_argument("--save", default=None,
                            help="CAREER-TUNE-<name>: record the segment live instead of "
                                 "loading a previous run's frames")
    cap_parser.set_defaults(func=_capture)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except PlanError as exc:
        print(f"mod_check: {exc}", file=sys.stderr)
        return 2
    except (runner.DriverError, OSError) as exc:
        print(f"mod_check: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
