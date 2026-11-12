"""Context watchdog for agent sessions (CLAUDE.md; /context-check).

Reads this session's token use and reports the context used by its last request, as tokens and
as a percentage of the model's window. Builders run it every couple of board messages; at the
threshold (default 60%) they post the hand-over NOTICE, release their claims, stop the listener
and stop, so the next builder session starts from the hand-over instead of a blown-up context.

    python3 -m tools.context_check [--limit N] [--threshold PCT] [--harness auto|claude|opencode]
                                   [--session ID] [--transcript PATH] [--json]

Two harnesses are supported. Sources are chosen in this order: --transcript (a Claude Code
transcript); an explicit --harness (it beats the environment); --session (a Claude Code session
id, or an OpenCode session when it starts ses_); MU_OPENCODE_SESSION; MU_AGENT_HARNESS
(opencode or claude-code); and finally auto, where a Claude Code session wins over an
`opencode` binary found on PATH. A session counts as Claude Code when CLAUDE_CODE_SESSION_ID or
CLAUDECODE is set, or when this directory already has a transcript under ~/.claude/projects
(its own Claude Code session), so an OpenCode install on PATH can no longer hijack it.

Claude Code: context is input_tokens + cache_read_input_tokens + cache_creation_input_tokens of
the newest assistant entry with a usage block. The transcript is found by CLAUDE_CODE_SESSION_ID
under ~/.claude/projects/*/; --session or --transcript override it.

OpenCode: context is input + cache.read + cache.write of the newest message with type
"assistant" carrying a tokens object in `opencode session export <id>`. The id is --session, else
MU_OPENCODE_SESSION, else the newest entry of `opencode session list --format json` with a
warning (every session in the shared service reports the same directory, so that fallback is
ambiguous). A session with no assistant usage yet is exit 1, not 0.

Exit 0 under the threshold, 2 at or above it, 1 when there is no usable source or no usage yet.

Env: MU_CONTEXT_LIMIT (tokens, default 1,000,000 for the DeepSeek v4.1 Flash routing),
MU_CONTEXT_THRESHOLD (percent, default 60), MU_OPENCODE_BIN (default opencode),
MU_OPENCODE_SESSION, MU_AGENT_HARNESS.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_LIMIT = 1_000_000
DEFAULT_THRESHOLD = 60.0
PROJECTS = Path.home() / ".claude" / "projects"
HARNESSES = ("auto", "claude", "opencode")


class SourceError(Exception):
    """No usable context source: a missing transcript, OpenCode session or export."""


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


def opencode_bin():
    return os.environ.get("MU_OPENCODE_BIN") or "opencode"


def run_opencode(argv):
    """Run the OpenCode CLI and return its stdout, or raise SourceError."""
    cmd = [opencode_bin(), *argv]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise SourceError(f"cannot run {cmd[0]}: {exc}") from exc
    if result.returncode != 0:
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        detail = lines[-1] if lines else f"exit {result.returncode}"
        raise SourceError(f"opencode {' '.join(argv)} failed: {detail}")
    return result.stdout


def newest_opencode_session():
    out = run_opencode(["session", "list", "--format", "json"])
    try:
        sessions = json.loads(out or "[]")
    except json.JSONDecodeError as exc:
        raise SourceError(f"opencode session list returned invalid JSON: {exc}") from exc
    if not isinstance(sessions, list) or not sessions:
        raise SourceError(
            "no OpenCode sessions to read; pass --session ses_… or set MU_OPENCODE_SESSION"
        )
    session_id = (sessions[0] or {}).get("id")
    if not session_id:
        raise SourceError("the newest OpenCode session has no id")
    return session_id


def opencode_session_id(session=None, accept_any=False):
    """The OpenCode session to export: --session ses_…, else the env id, else the newest."""
    if session and (accept_any or session.startswith("ses_")):
        return session
    env_id = os.environ.get("MU_OPENCODE_SESSION")
    if env_id:
        return env_id
    session_id = newest_opencode_session()
    print(
        "context_check: warning: no --session or MU_OPENCODE_SESSION; using the newest "
        f"OpenCode session {session_id} (the shared service gives every session the same "
        "directory, so this may be another window's)",
        file=sys.stderr,
    )
    return session_id


def opencode_export(session_id):
    out = run_opencode(["session", "export", session_id])
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise SourceError(
            f"opencode session export {session_id} returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise SourceError(
            f"opencode session export {session_id} returned {type(data).__name__}, not an object"
        )
    return data


def last_opencode_tokens(export):
    """Tokens of the newest assistant message that carries usage."""
    usage = None
    for message in export.get("messages") or []:
        if message.get("type") == "assistant" and isinstance(message.get("tokens"), dict):
            usage = message["tokens"]
    return usage


def opencode_context_tokens(tokens):
    cache = tokens.get("cache")
    if not isinstance(cache, dict):
        cache = {}
    return sum(
        int(value or 0) for value in (tokens.get("input"), cache.get("read"), cache.get("write"))
    )


def has_own_transcript():
    """True when this directory already has a Claude Code transcript (its own session)."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd().resolve()))
    return any((PROJECTS / slug).glob("*.jsonl"))


def choose_harness(args):
    """Return (harness, strict). strict means a source was named, so there is no auto fallback."""
    if args.harness != "auto":
        return args.harness, True
    if args.session:
        return ("opencode" if args.session.startswith("ses_") else "claude"), True
    if os.environ.get("MU_OPENCODE_SESSION"):
        return "opencode", True
    env = os.environ.get("MU_AGENT_HARNESS", "").strip().lower()
    if env == "opencode":
        return "opencode", True
    if env in ("claude", "claude-code"):
        return "claude", True
    # A Claude Code session never sets the MU_* markers, so its own signals come before the
    # binary probe: an `opencode` on PATH must not make it read another window's session.
    if os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDECODE"):
        return "claude", False
    if has_own_transcript():
        return "claude", False
    if shutil.which(opencode_bin()):
        return "opencode", False
    return "claude", False


def resolve_source(args):
    """Return (harness, source label, usage or None) for the source this session should read."""
    if args.transcript:
        path = find_transcript(args.session, args.transcript)
        return "claude", str(path), last_usage(path)
    harness, strict = choose_harness(args)
    if harness == "opencode":
        try:
            session_id = opencode_session_id(args.session, accept_any=args.harness == "opencode")
            export = opencode_export(session_id)
        except SourceError:
            if strict:
                raise
            # auto found the binary but it has no session to serve: use Claude instead.
        else:
            return "opencode", f"opencode {session_id}", last_opencode_tokens(export)
    path = find_transcript(args.session, None)
    return "claude", str(path), last_usage(path)


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
    parser.add_argument(
        "--harness",
        choices=HARNESSES,
        default="auto",
        help="context source: auto, claude or opencode (default auto)",
    )
    parser.add_argument(
        "--session",
        help="Claude Code session id, or an OpenCode ses_… id",
    )
    parser.add_argument("--transcript", help="Claude Code transcript .jsonl path")
    parser.add_argument("--json", action="store_true", help="one JSON object on stdout")
    args = parser.parse_args(argv)

    try:
        harness, source, usage = resolve_source(args)
    except (OSError, ValueError, SourceError) as exc:
        print(f"context_check: {exc}", file=sys.stderr)
        return 1
    if usage is None:
        if harness == "opencode":
            print(f"context_check: no usage yet in {source}", file=sys.stderr)
        else:
            print(f"context_check: no usage block in {source}", file=sys.stderr)
        return 1

    tokens = opencode_context_tokens(usage) if harness == "opencode" else context_tokens(usage)
    result = report(tokens, args.limit, args.threshold)
    result["source"] = source
    result["harness"] = harness
    if harness == "claude":
        result["transcript"] = source
    if args.json:
        print(json.dumps(result))
    elif result["over"]:
        print(
            f"context: {result['tokens']:,} / {result['limit']:,} tokens "
            f"({result['percent']}%) - OVER {result['threshold']:g}%: hand over now ({source})"
        )
    else:
        print(
            f"context: {result['tokens']:,} / {result['limit']:,} tokens "
            f"({result['percent']}%) - under {result['threshold']:g}% ({source})"
        )
    return 2 if result["over"] else 0


if __name__ == "__main__":
    sys.exit(main())
