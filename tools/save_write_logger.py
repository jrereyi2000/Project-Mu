"""
tools/save_write_logger.py: Logs when Madden writes to its saves folder, to learn whether the franchise
save changes during a game (docs/in_progress/V2_MASTER_PLAN.md §5, docs/in_progress/PC_MIGRATION_PLAN.md §9).

Polls the saves folder once a second and logs every file that appears, changes (modification time, size or
content) or disappears, plus when Madden27.exe starts and exits. It only reads: files are hashed in place, and
with --snapshot-dir each changed file is copied out once it has stopped changing, so a mid-game copy can be fed
to pipeline/save_reader/pc_save_reader.mjs later. Ctrl-C prints a summary that splits the writes into those
made while Madden was running and those made after it exited.

Usage:
  python3 -m tools.save_write_logger [PATH] [OPTIONS]

Options:
  PATH                     Saves folder to watch (default: MADDEN_SAVES_DIR or ".")
  --pattern GLOB           Files to watch (default: "*", so temporary files Madden writes are seen too)
  --interval SECONDS       Polling interval (default: 1)
  --process-interval SECS  How often to check whether Madden is running (default: 5; tasklist.exe is slow)
  --snapshot-dir DIR       Copy each changed file here once it has been stable for --settle seconds
  --settle SECONDS         Stable time before a snapshot is taken (default: 3)
  --log PATH               Log file (default: verify_output/save_writes_<timestamp>.log)
"""
import argparse
import datetime
import glob
import hashlib
import os
import shutil
import sys
import time

from pipeline.gpu_queue.process_check import is_madden_running
from pipeline.notify import env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def file_sha1(path):
    """SHA-1 of a file's contents, or None if it can't be read (Madden may hold it mid-write)."""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def scan(folder, pattern="*"):
    """{file name: (mtime_ns, size)} for the regular files in folder matching pattern."""
    state = {}
    for path in glob.glob(os.path.join(folder, pattern)):
        try:
            st = os.stat(path)
        except OSError:
            continue  # removed between glob and stat
        if os.path.isfile(path):
            state[os.path.basename(path)] = (st.st_mtime_ns, st.st_size)
    return state


def diff(prev, cur):
    """Events between two scans: (kind, name, old, new) with kind 'new', 'changed' or 'removed'."""
    events = []
    for name in sorted(set(prev) | set(cur)):
        old, new = prev.get(name), cur.get(name)
        if old is None:
            events.append(("new", name, None, new))
        elif new is None:
            events.append(("removed", name, old, None))
        elif old != new:
            events.append(("changed", name, old, new))
    return events


def fmt_mtime(mtime_ns):
    return datetime.datetime.fromtimestamp(mtime_ns / 1e9).strftime("%H:%M:%S.%f")[:-3]


def describe(kind, name, old, new, old_hash=None, new_hash=None):
    """One log line's text for an event."""
    if kind == "new":
        return f"NEW      {name}  size={new[1]:,}  mtime={fmt_mtime(new[0])}"
    if kind == "removed":
        return f"REMOVED  {name}"
    parts = [f"CHANGED  {name}"]
    if old[1] != new[1]:
        parts.append(f"size {old[1]:,} -> {new[1]:,}")
    else:
        parts.append(f"size={new[1]:,}")
    parts.append(f"mtime {fmt_mtime(old[0])} -> {fmt_mtime(new[0])}")
    if old_hash and new_hash:
        parts.append("content changed" if old_hash != new_hash else "content SAME")
    elif new_hash is None:
        parts.append("content unreadable")
    return "  ".join(parts)


def summarize(writes, madden_spans):
    """Summary lines. writes: [(time, kind, name)]; madden_spans: [(start, end or None)] in epoch seconds."""
    def during(t):
        return any(s <= t and (e is None or t <= e) for s, e in madden_spans)

    lines = [f"Madden sessions seen: {len(madden_spans)}"]
    for s, e in madden_spans:
        end = datetime.datetime.fromtimestamp(e).strftime("%H:%M:%S") if e else "still running"
        lines.append(f"  {datetime.datetime.fromtimestamp(s):%H:%M:%S} -> {end}")
    inside = [w for w in writes if during(w[0])]
    outside = [w for w in writes if not during(w[0])]
    lines.append(f"File events while Madden was running: {len(inside)}")
    for t, kind, name in inside:
        lines.append(f"  {datetime.datetime.fromtimestamp(t):%H:%M:%S}  {kind:<8} {name}")
    lines.append(f"File events while Madden was not running: {len(outside)}")
    for t, kind, name in outside:
        lines.append(f"  {datetime.datetime.fromtimestamp(t):%H:%M:%S}  {kind:<8} {name}")
    return lines


class Logger:
    def __init__(self, log_path):
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        self.f = open(log_path, "a")

    def __call__(self, msg):
        line = f"[{datetime.datetime.now():%H:%M:%S.%f}"[:-3] + f"] {msg}"
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def run(folder, pattern, interval, process_interval, snapshot_dir, settle, log):
    state = scan(folder, pattern)
    hashes = {name: file_sha1(os.path.join(folder, name)) for name in state}
    log(f"Watching {folder} (pattern {pattern!r}, every {interval}s); {len(state)} files:")
    for name, (mtime_ns, size) in sorted(state.items()):
        log(f"  {name}  size={size:,}  mtime={fmt_mtime(mtime_ns)}")

    writes, madden_spans = [], []
    madden_on, last_process_check = None, 0.0
    pending = {}  # name -> (mtime_ns, size) waiting to settle before a snapshot
    try:
        while True:
            now = time.time()
            if now - last_process_check >= process_interval:
                last_process_check = now
                running = is_madden_running()
                if running != madden_on:
                    if running:
                        madden_spans.append((now, None))
                        log("MADDEN   running")
                    elif madden_on is not None:
                        if madden_spans:
                            madden_spans[-1] = (madden_spans[-1][0], now)
                        log("MADDEN   exited")
                    else:
                        log("MADDEN   not running")
                    madden_on = running

            cur = scan(folder, pattern)
            for kind, name, old, new in diff(state, cur):
                new_hash = file_sha1(os.path.join(folder, name)) if new else None
                log(describe(kind, name, old, new, hashes.get(name), new_hash))
                writes.append((now, kind, name))
                if new:
                    hashes[name] = new_hash
                    pending[name] = new
                else:
                    hashes.pop(name, None)
                    pending.pop(name, None)
            state = cur

            if snapshot_dir:
                for name, stamp in list(pending.items()):
                    if cur.get(name) != stamp:
                        pending.pop(name)  # vanished; a later change re-queues it
                    elif now - stamp[0] / 1e9 >= settle:
                        os.makedirs(snapshot_dir, exist_ok=True)
                        dest = os.path.join(snapshot_dir, f"{datetime.datetime.now():%H%M%S}_{name}")
                        try:
                            shutil.copy2(os.path.join(folder, name), dest)
                            log(f"SNAPSHOT {name} -> {dest}")
                        except OSError as e:
                            log(f"SNAPSHOT {name} failed: {e}")
                        pending.pop(name)
            time.sleep(interval)
    except KeyboardInterrupt:
        log("Stopped. Summary:")
        for line in summarize(writes, madden_spans):
            log(line)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Log writes to the Madden saves folder")
    ap.add_argument("path", nargs="?", help="Saves folder (default: MADDEN_SAVES_DIR or .)")
    ap.add_argument("--pattern", default="*")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--process-interval", type=float, default=5.0)
    ap.add_argument("--snapshot-dir")
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--log")
    args = ap.parse_args(argv)

    folder = args.path or env("MADDEN_SAVES_DIR", ".")
    if not os.path.isdir(folder):
        print(f"Not a folder: {folder}", file=sys.stderr)
        return 1
    log_path = args.log or os.path.join(ROOT, "verify_output", f"save_writes_{datetime.datetime.now():%Y%m%d_%H%M%S}.log")
    log = Logger(log_path)
    log(f"Log file: {log_path}")
    run(folder, args.pattern, args.interval, args.process_interval, args.snapshot_dir, args.settle, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
