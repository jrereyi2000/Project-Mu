"""Context watchdog for agent sessions (CLAUDE.md; /context-check).

Reads this session's transcript and reports the context used by its last request, as tokens and
as a percentage of the model's window. Builders run it every couple of board messages; at the
threshold (default 60%) they post the hand-over NOTICE, release their claims, stop the listener
and stop, so the next builder session starts from the hand-over instead of a blown-up context.

    python3 -m tools.context_check [--limit N] [--threshold PCT] [--session ID] [--transcript PATH] [--json]

Context is input_tokens + cache_read_input_tokens + cache_creation_input_tokens of the newest
assistant entry with a usage block: the prompt the API last saw, i.e. the chat as it stands.
The transcript is found by CLAUDE_CODE_SESSION_ID under ~/.claude/projects/*/; --session or
--transcript override it. Exit 0 under the threshold, 2 at or above it, 1 when there is no
usable transcript.

Env: MU_CONTEXT_LIMIT (tokens, default 1,000,000 for the DeepSeek v4.1 Flash routing),
MU_CONTEXT_THRESHOLD (percent, default 60).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_LIMIT = 1_000_000
DEFAULT_THRESHOLD = 60.0
PROJECTS = Path.home() / ".claude" / "projects"


def find_transcript(session_id=None, transcript=None):
    if transcript:
        return Path(transcript)
    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        hits = list(PROJECTS.glob(f"*/{sid}.jsonl"))
        if hits:
            return max(hits, key=lambda p: p.stat().st_mtime)
        raise FileNotFoundError(f"no transcript for session {sid} under {PROJECTS}")
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
    candidates = list((PROJECTS / slug).glob("*.jsonl"))
    if not candidates:
        raise FileNotFoundError(
            f"no session id and no transcripts in {PROJECTS / slug}; pass --transcript"
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def last_usage(path):
    usage = None
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            found = (entry.get("message") or {}).get("usage")
            if found:
                usage = found
    return usage


def context_tokens(usage):
    return sum(
        int(usage.get(key) or 0)
        for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )


def report(tokens, limit, threshold):
    percent = 100.0 * tokens / limit if limit else 0.0
    return {
        "tokens": tokens,
        "limit": limit,
        "threshold": threshold,
        "percent": round(percent, 1),
        "over": percent >= threshold,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.context_check",
        description="Report this session's context use, and trip at the hand-over threshold.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("MU_CONTEXT_LIMIT", DEFAULT_LIMIT)),
        help=f"model context window in tokens (default {DEFAULT_LIMIT:,})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=float(os.environ.get("MU_CONTEXT_THRESHOLD", DEFAULT_THRESHOLD)),
        help=f"hand-over threshold in percent (default {DEFAULT_THRESHOLD:g})",
    )
    parser.add_argument("--session", help="session id (default: CLAUDE_CODE_SESSION_ID)")
    parser.add_argument("--transcript", help="transcript .jsonl path")
    parser.add_argument("--json", action="store_true", help="one JSON object on stdout")
    args = parser.parse_args(argv)

    try:
        path = find_transcript(args.session, args.transcript)
        usage = last_usage(path)
    except (FileNotFoundError, OSError) as exc:
        print(f"context_check: {exc}", file=sys.stderr)
        return 1
    if usage is None:
        print(f"context_check: no usage block in {path}", file=sys.stderr)
        return 1

    result = report(context_tokens(usage), args.limit, args.threshold)
    result["transcript"] = str(path)
    if args.json:
        print(json.dumps(result))
    elif result["over"]:
        print(
            f"context: {result['tokens']:,} / {result['limit']:,} tokens "
            f"({result['percent']}%) - OVER {result['threshold']:g}%: hand over now"
        )
    else:
        print(
            f"context: {result['tokens']:,} / {result['limit']:,} tokens "
            f"({result['percent']}%) - under {result['threshold']:g}%"
        )
    return 2 if result["over"] else 0


if __name__ == "__main__":
    sys.exit(main())
