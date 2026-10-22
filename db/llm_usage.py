"""
Gemini token logging (docs/PIPELINE_MIGRATION.md, phase 3) into the llm_usage table.

  meter = UsageMeter(client)          # counts every model call this client makes
  with meter.labelled("01_passing_000.png"):
      client.models.generate_content(...)
  meter.record("extract", capture_folder="2026-10-14_0146")

  resp = client.models.generate_content(...)
  log_response("agent_memory", "summary @handle", model, resp)   # a one-off call, no meter

The meter wraps the client's per-request method, so the calls the SDK makes inside automatic
function calling (a newsroom article's tool loop) are counted too; the response a chat returns
only carries the last call's usage. Logging never breaks a run: without a database it warns and
returns the totals.
"""
import os
import threading
from contextlib import contextmanager

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def _usage(resp):
    u = getattr(resp, "usage_metadata", None)
    get = (lambda k: getattr(u, k, None) or 0) if u else (lambda k: 0)
    return {"input_tokens": get("prompt_token_count"), "cached_tokens": get("cached_content_token_count"),
            "output_tokens": get("candidates_token_count"), "thinking_tokens": get("thoughts_token_count")}


def log_response(source, label, model, resp):
    """Write one response's tokens to llm_usage, for a single call made outside a UsageMeter. Never raises."""
    row = _usage(resp)
    try:
        import psycopg
        with psycopg.connect(DSN, connect_timeout=5) as conn:
            conn.execute("INSERT INTO llm_usage (source, label, model, input_tokens, cached_tokens, output_tokens, "
                         "thinking_tokens) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                         (source, (label or "")[:255], (model or "")[:64], row["input_tokens"], row["cached_tokens"],
                          row["output_tokens"], row["thinking_tokens"]))
    except Exception as e:
        print(f"  (token usage not logged: {e})")
    return row


class UsageMeter:
    def __init__(self, client):
        self.calls = []
        self._lock = threading.Lock()
        self._local = threading.local()
        models = client.models
        inner = models._generate_content          # one HTTP request per call, AFC loop included

        def counted(*args, **kwargs):
            resp = inner(*args, **kwargs)
            row = _usage(resp)
            row["model"] = kwargs.get("model") or (args[0] if args else None)
            row["label"] = getattr(self._local, "label", None)
            with self._lock:
                self.calls.append(row)
            return resp

        models._generate_content = counted

    @contextmanager
    def labelled(self, label):
        old = getattr(self._local, "label", None)
        self._local.label = label
        try:
            yield
        finally:
            self._local.label = old

    def totals(self):
        with self._lock:
            calls = list(self.calls)
        keys = ("input_tokens", "cached_tokens", "output_tokens", "thinking_tokens")
        return {k: sum(c[k] for c in calls) for k in keys} | {"calls": len(calls)}

    def take(self):
        with self._lock:
            calls, self.calls = self.calls, []
        return calls

    def record(self, source, label=None, model=None, capture_folder=None, mode=None):
        """Write the calls counted so far to llm_usage (and, for a capture, the run's totals to its
        captures row, created as 'extracted' if the loader hasn't made it yet). Returns the totals."""
        totals = self.totals()
        calls = self.take()
        if not calls:
            return totals
        try:
            import psycopg
            with psycopg.connect(DSN, connect_timeout=5) as conn:
                cur = conn.cursor()
                cid = None
                if capture_folder:
                    cur.execute("SELECT id FROM captures WHERE folder = %s", (capture_folder,))
                    row = cur.fetchone()
                    if row:
                        cid = row[0]
                    else:
                        cur.execute("INSERT INTO captures (folder, mode, status, extracted_at) VALUES (%s, %s, "
                                    "'extracted', now()) RETURNING id", (capture_folder, mode or "unknown"))
                        cid = cur.fetchone()[0]
                    cur.execute("UPDATE captures SET input_tokens = COALESCE(input_tokens, 0) + %s, "
                                "cached_tokens = COALESCE(cached_tokens, 0) + %s, output_tokens = COALESCE(output_tokens, 0) + %s, "
                                "thinking_tokens = COALESCE(thinking_tokens, 0) + %s, extracted_at = now() WHERE id = %s",
                                (totals["input_tokens"], totals["cached_tokens"], totals["output_tokens"],
                                 totals["thinking_tokens"], cid))
                cur.executemany(
                    "INSERT INTO llm_usage (source, capture_id, label, model, input_tokens, cached_tokens, output_tokens, "
                    "thinking_tokens) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [(source, cid, (c["label"] or label or "")[:255], (c["model"] or model or "")[:64], c["input_tokens"],
                      c["cached_tokens"], c["output_tokens"], c["thinking_tokens"]) for c in calls])
        except Exception as e:                       # never let logging break a capture or an article
            print(f"  (token usage not logged: {e})")
        return totals
