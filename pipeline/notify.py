"""
Phone notices for the weekly run (docs/PIPELINE_MIGRATION.md, phase 5) through ntfy.

  python3 -m pipeline.notify "test message"          # send one by hand

NTFY_TOPIC (required) and NTFY_SERVER (default https://ntfy.sh) come from the environment or the
project-root .env. A notice that can't be sent is printed and never stops the run.
"""
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def env(key, default=None):
    """A setting from the environment, else from the project-root .env."""
    v = os.environ.get(key)
    if v:
        return v
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def notify(message, title="Project Mu", priority="default", tags=()):
    """Send one notice; True when ntfy accepted it. priority: min, low, default, high, urgent."""
    topic = env("NTFY_TOPIC")
    print(f"[notice] {title}: {message}", flush=True)
    if not topic:
        print("[notice] NTFY_TOPIC isn't set; notice not sent", flush=True)
        return False
    url = f"{env('NTFY_SERVER', 'https://ntfy.sh').rstrip('/')}/{topic}"
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    req = urllib.request.Request(url, data=message.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print(f"[notice] couldn't send to ntfy: {e}", flush=True)
        return False


if __name__ == "__main__":
    sys.exit(0 if notify(" ".join(sys.argv[1:]) or "Test notice from Project Mu") else 1)
