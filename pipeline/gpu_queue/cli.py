"""
pipeline/gpu_queue/cli.py
Standalone CLI for the Nightly GPU Queue.

Usage:
    python -m pipeline.gpu_queue.cli enqueue --kind text --model gemma4:12b --payload '{"prompt": "foo"}'
    python -m pipeline.gpu_queue.cli list [--status pending] [--json]
    python -m pipeline.gpu_queue.cli run --dry-run [--once] [--poll-interval 2.0]
    python -m pipeline.gpu_queue.cli clear [--status completed]
    python -m pipeline.gpu_queue.cli check-madden
"""
import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

from .models import GPUJob, VALID_STATUSES
from .process_check import is_madden_running
from .queue import GPUQueue, SQLiteJobStore, JsonJobStore, BaseJobStore
from .runner import GPUQueueRunner, mock_job_handler


def _get_store(args: argparse.Namespace) -> BaseJobStore:
    store_type = getattr(args, "store_type", "sqlite")
    if store_type == "json":
        json_path = getattr(args, "json_path", "data/gpu_queue.json")
        return JsonJobStore(json_path=json_path)
    db_path = getattr(args, "db_path", "data/gpu_queue.db")
    return SQLiteJobStore(db_path=db_path)


def cmd_enqueue(args: argparse.Namespace) -> int:
    store = _get_store(args)
    queue = GPUQueue(store=store)

    payload_data: Dict[str, Any] = {}
    if args.payload:
        raw_p = args.payload.strip()
        if raw_p.startswith("{") or raw_p.endswith("}") or raw_p.startswith("["):
            try:
                parsed = json.loads(raw_p)
                if isinstance(parsed, dict):
                    payload_data = parsed
                else:
                    payload_data = {"data": parsed}
            except Exception as e:
                print(f"Error parsing JSON payload: {e}", file=sys.stderr)
                return 1
        elif os.path.exists(raw_p):
            try:
                with open(raw_p, "r", encoding="utf-8") as f:
                    payload_data = json.load(f)
            except Exception as e:
                print(f"Error reading payload file: {e}", file=sys.stderr)
                return 1
        else:
            payload_data = {"text": raw_p}

    job = GPUJob(
        id=args.id or "",
        kind=args.kind,
        model_id=args.model,
        payload=payload_data,
        priority=args.priority,
    )
    job_id = queue.enqueue(job)
    print(f"Enqueued job: {job_id} (kind={job.kind}, model={job.model_id}, priority={job.priority})")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = _get_store(args)
    queue = GPUQueue(store=store)
    jobs = queue.list_jobs(status=args.status)

    if args.json:
        print(json.dumps([j.to_dict() for j in jobs], indent=2))
        return 0

    if not jobs:
        status_msg = f" with status '{args.status}'" if args.status else ""
        print(f"No jobs found{status_msg}.")
        return 0

    header = f"{'JOB ID':<18} {'PRIO':<6} {'STATUS':<11} {'KIND':<16} {'MODEL':<18} {'CREATED AT'}"
    print(header)
    print("-" * len(header))
    for j in jobs:
        created_short = j.created_at[:19] if j.created_at else ""
        print(
            f"{j.id:<18} {j.priority:<6} {j.status:<11} {j.kind:<16} {j.model_id:<18} {created_short}"
        )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # No real job handlers are wired into the CLI yet. Running without them
    # would mark every pending job completed without doing the work, so only
    # the explicit dry run (mock handler) is allowed.
    if not args.dry_run:
        print(
            "Error: no job handlers are wired into the CLI yet. Use --dry-run to exercise "
            "the queue with the mock handler (jobs are marked completed without doing any "
            "work), or run the queue from Python with GPUQueueRunner.register_handler().",
            file=sys.stderr,
        )
        return 1

    store = _get_store(args)
    queue = GPUQueue(store=store)

    is_running_fn = (lambda: False) if args.ignore_madden else is_madden_running

    def print_log(msg: str):
        print(f"[gpu_queue] {msg}")

    runner = GPUQueueRunner(
        queue=queue,
        is_madden_running_fn=is_running_fn,
        log_fn=print_log,
        default_handler=mock_job_handler,
    )

    if not args.no_requeue_stale:
        requeued = queue.requeue_running()
        if requeued:
            print_log(f"Requeued {requeued} job(s) left running by a previous run.")

    if args.once:
        did_work = runner.run_once()
        print(f"Run once finished. Job executed: {did_work}")
        return 0

    stop_on_empty = not args.no_stop_on_empty
    total = runner.run_loop(
        poll_interval=args.poll_interval,
        max_iterations=args.max_iterations,
        stop_on_empty=stop_on_empty,
    )
    print(f"Queue runner exited. Total jobs processed: {total}")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    store = _get_store(args)
    queue = GPUQueue(store=store)
    count = queue.clear(status=args.status)
    status_msg = f" with status '{args.status}'" if args.status else ""
    print(f"Cleared {count} jobs{status_msg}.")
    return 0


def cmd_check_madden(args: argparse.Namespace) -> int:
    running = is_madden_running()
    if running:
        print("Madden NFL 27 is RUNNING (GPU is active with the game; batch queue paused)")
    else:
        print("Madden NFL 27 is NOT running (GPU is available for batch queue)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.gpu_queue.cli",
        description="Standalone Nightly GPU Queue CLI for Project Mu.",
    )
    parser.add_argument(
        "--store-type",
        choices=["sqlite", "json"],
        default="sqlite",
        help="Storage backend to use (default: sqlite)",
    )
    parser.add_argument(
        "--db-path",
        default="data/gpu_queue.db",
        help="Path to SQLite database file (default: data/gpu_queue.db)",
    )
    parser.add_argument(
        "--json-path",
        default="data/gpu_queue.json",
        help="Path to JSON store file (default: data/gpu_queue.json)",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to execute")

    # enqueue
    p_enq = subparsers.add_parser("enqueue", help="Enqueue a new job")
    p_enq.add_argument("--kind", default="text", help="Job kind (text, headshot, portrait, audio_tts)")
    p_enq.add_argument("--model", default="gemma4:12b", help="Model identifier")
    p_enq.add_argument("--payload", default="{}", help="JSON payload string or path to JSON file")
    p_enq.add_argument("--priority", type=int, default=None, help="Priority tier (1=text, 2=image, 3=audio)")
    p_enq.add_argument("--id", default=None, help="Custom job ID")
    p_enq.set_defaults(func=cmd_enqueue)

    # list
    p_list = subparsers.add_parser("list", help="List jobs in the queue")
    p_list.add_argument("--status", choices=sorted(VALID_STATUSES), help="Filter status")
    p_list.add_argument("--json", action="store_true", help="Output raw JSON array")
    p_list.set_defaults(func=cmd_list)

    # run
    p_run = subparsers.add_parser("run", help="Run the queue processor")
    p_run.add_argument("--once", action="store_true", help="Process only one job and exit")
    p_run.add_argument("--poll-interval", type=float, default=2.0, help="Polling interval in seconds")
    p_run.add_argument("--max-iterations", type=int, default=None, help="Max iterations before exiting")
    p_run.add_argument("--ignore-madden", action="store_true", help="Ignore Madden running check")
    p_run.add_argument("--no-stop-on-empty", action="store_true", help="Keep running even when queue is empty")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Required for now: run jobs through the mock handler (marks them completed without doing work)",
    )
    p_run.add_argument(
        "--no-requeue-stale",
        action="store_true",
        help="Don't return jobs left 'running' by a crashed run to 'pending' (use if another runner is active)",
    )
    p_run.set_defaults(func=cmd_run)

    # clear
    p_clear = subparsers.add_parser("clear", help="Clear jobs from queue")
    p_clear.add_argument("--status", choices=sorted(VALID_STATUSES), help="Filter status")
    p_clear.set_defaults(func=cmd_clear)

    # check-madden
    p_chk = subparsers.add_parser("check-madden", help="Check if Madden 27 process is running")
    p_chk.set_defaults(func=cmd_check_madden)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
