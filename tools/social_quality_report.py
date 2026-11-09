"""Agent Wire quality report (docs/in_progress/AGENT_WIRE_VOICE_PLAN.md §2.6, §2.7 rule 6).

Measures one season of Agent Wire posts against the S5 baseline: repetition (distinct two-word openers and
within-week trigram overlap), how much of the week looks ahead, how number-heavy posts are, the kind mix and
posts per account, and the real-tweet shape metrics (median words per archetype, posts without closing
punctuation, numbers per post). Reads the database only; never writes.

  python3 -m tools.social_quality_report --db "host=localhost dbname=madden_2036 user=postgres password=postgres" --season 2036
  python3 -m tools.social_quality_report --db "$DSN" --season 2036 --weeks 3-9 --json /tmp/report.json

The S5 baseline was measured over the whole archive stored in `madden_2036` (both the 2035 and 2036 seasons):
2,467 posts, REACTION 553 posts with 317 distinct two-word openers, PLAYER 160/72, BEAT_INJURY 88/40. The
baseline check at the end of the report is therefore archive-wide; the per-kind table above it is the requested
season (and week range, when given).
"""
import argparse
import datetime
import json
import os
import re
import statistics
import sys

CLOSING_PUNCTUATION = ".?!…"
FORWARD_KINDS = {"PICKS", "SEASON_CALL", "PREVIEW", "HOT_TAKE"}
FORWARD_WORDS = re.compile(r"\b(?:will|next\s+week|pick\w*|calling\s+it|predict\w*)", re.I)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
WORD = re.compile(r"[a-z0-9']+")
UNSAFE = re.compile(r"[^a-z0-9 ]")

# S5 (AGENT_WIRE_VOICE_PLAN §0, measured 2026-10-31 over the whole archive): kind -> (posts, distinct openers).
S5_BASELINE = {"REACTION": (553, 317), "PLAYER": (160, 72), "BEAT_INJURY": (88, 40)}


class SchemaMissing(Exception):
    """The Agent Wire columns or the news_feed table are not in this database."""


# ---------------------------------------------------------------------------------------------------- metric ---
def two_word_opener(text: str) -> str:
    """The first two words of a post, normalised the way the S5 baseline counted them.

    Lowercase; drop punctuation and apostrophes ("can't" -> "cant"); collapse every digit run to "#" so scores
    and records ("up 14", "up 20") share an opener; take the first two tokens."""
    t = UNSAFE.sub("", (text or "").lower())
    t = re.sub(r"\d+", "#", t)
    return " ".join(t.split()[:2])


def word_count(text: str) -> int:
    return len(WORD.findall((text or "").lower()))


def number_count(text: str) -> int:
    """Numeric tokens in a post (scores, records, stats, ranks; decimals count once)."""
    return len(NUMBER.findall(text or ""))


def has_closing_punctuation(text: str) -> bool:
    """True when the post ends with . ? ! or … (surrounding whitespace and closing quotes ignored)."""
    t = (text or "").strip().rstrip("\"'”’)]}")
    return bool(t) and t[-1] in CLOSING_PUNCTUATION


def is_forward_looking(post: dict) -> bool:
    """PICKS, SEASON_CALL, PREVIEW and HOT_TAKE always look ahead; any other post does when its text does."""
    if (post.get("kind") or post.get("post_kind")) in FORWARD_KINDS:
        return True
    return bool(FORWARD_WORDS.search(post.get("content") or post.get("text") or ""))


def _trigrams(text: str) -> set:
    words = WORD.findall((text or "").lower())
    if len(words) >= 3:
        return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}
    return {tuple(words)} if words else set()


def trigram_overlap(a: str, b: str) -> float:
    """Word-trigram Jaccard between two posts (0 when either has no words)."""
    ga, gb = _trigrams(a), _trigrams(b)
    return len(ga & gb) / len(ga | gb) if ga and gb else 0.0


def mean_pairwise_overlap(posts: list):
    """Mean trigram Jaccard over every pair of posts published in the same week; None when no week has a pair."""
    weeks = {}
    for p in posts:
        weeks.setdefault((p.get("season"), p.get("week")), []).append(p.get("content") or p.get("text") or "")
    values = []
    for texts in weeks.values():
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                values.append(trigram_overlap(texts[i], texts[j]))
    return round(statistics.mean(values), 4) if values else None


def _kind(post: dict) -> str:
    return post.get("kind") or post.get("post_kind") or "UNKNOWN"


def metrics(posts: list) -> dict:
    """Every scalar metric for a list of posts."""
    n = len(posts)
    openers = [two_word_opener(p.get("content") or p.get("text") or "") for p in posts]
    words = [word_count(p.get("content") or p.get("text") or "") for p in posts]
    numbers = [number_count(p.get("content") or p.get("text") or "") for p in posts]
    forward = sum(1 for p in posts if is_forward_looking(p))
    three = sum(1 for c in numbers if c >= 3)
    no_end = sum(1 for p in posts if not has_closing_punctuation(p.get("content") or p.get("text") or ""))
    return {
        "posts": n,
        "distinct_openers": len(set(openers)),
        "opener_ratio": round(len(set(openers)) / n, 4) if n else 0.0,
        "forward_looking": forward,
        "forward_share": round(forward / n, 4) if n else 0.0,
        "posts_3plus_numbers": three,
        "share_3plus_numbers": round(three / n, 4) if n else 0.0,
        "numbers_per_post_mean": round(statistics.mean(numbers), 3) if n else 0.0,
        "words_per_post_median": statistics.median(words) if words else 0,
        "no_closing_punctuation_share": round(no_end / n, 4) if n else 0.0,
    }


def summarize(posts: list) -> dict:
    """Overall metrics plus the within-week trigram overlap."""
    out = metrics(posts)
    out["mean_trigram_overlap"] = mean_pairwise_overlap(posts)
    return out


def kind_mix(posts: list) -> dict:
    counts = {}
    for p in posts:
        counts[_kind(p)] = counts.get(_kind(p), 0) + 1
    total = len(posts)
    return {k: {"posts": v, "share": round(v / total, 4) if total else 0.0}
            for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))}


def account_counts(posts: list, top: int = 15) -> dict:
    counts = {}
    for p in posts:
        agent = p.get("agent_id")
        if agent:
            counts[agent] = counts.get(agent, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "distinct_accounts": len(counts),
        "posts_per_account_mean": round(len([p for p in posts if p.get("agent_id")]) / len(counts), 3) if counts else 0.0,
        "top": [[a, n] for a, n in ranked[:top]],
    }


def archetype_metrics(posts: list) -> dict:
    """§2.7 rule 6: median words per archetype plus the shape metrics next to each archetype."""
    groups = {}
    for p in posts:
        groups.setdefault(p.get("persona") or "UNKNOWN", []).append(p)
    out = {}
    for name, group in sorted(groups.items()):
        m = metrics(group)
        out[name] = {"posts": m["posts"], "words_per_post_median": m["words_per_post_median"],
                     "numbers_per_post_mean": m["numbers_per_post_mean"],
                     "no_closing_punctuation_share": m["no_closing_punctuation_share"]}
    return out


def by_week(posts: list) -> dict:
    groups = {}
    for p in posts:
        groups.setdefault(f"{p.get('season')}:{p.get('week')}", []).append(p)
    return {key: {"posts": len(group), "distinct_openers": metrics(group)["distinct_openers"],
                  "opener_ratio": metrics(group)["opener_ratio"],
                  "mean_trigram_overlap": mean_pairwise_overlap(group)}
            for key, group in sorted(groups.items())}


def build_report(posts: list, archive_posts: list = None, db: str = None, season: int = None,
                 weeks=None) -> dict:
    """The full JSON report: the requested scope plus the archive-wide S5 baseline check."""
    report = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "db": db,
        "season": season,
        "weeks": f"{weeks[0]}-{weeks[1]}" if weeks else None,
        "posts": len(posts),
        "overall": summarize(posts),
        "by_kind": {kind: summarize(group) for kind, group in _by_kind(posts).items()},
        "kind_mix": kind_mix(posts),
        "accounts": account_counts(posts),
        "by_archetype": archetype_metrics(posts),
        "by_week": by_week(posts),
    }
    if archive_posts is not None:
        archive = summarize(archive_posts)
        archive_by_kind = {kind: summarize(group) for kind, group in _by_kind(archive_posts).items()}
        checks = {}
        for kind, (expected_posts, expected_openers) in S5_BASELINE.items():
            got = archive_by_kind.get(kind, {})
            checks[kind] = {"posts": got.get("posts", 0), "openers": got.get("distinct_openers", 0),
                            "expected_posts": expected_posts, "expected_openers": expected_openers,
                            "match": got.get("posts", 0) == expected_posts and
                                     got.get("distinct_openers", 0) == expected_openers}
        report["baseline"] = {"posts": archive["posts"], "overall": archive,
                              "by_kind": archive_by_kind, "checks": checks}
    return report


def _by_kind(posts: list) -> dict:
    groups = {}
    for p in posts:
        groups.setdefault(_kind(p), []).append(p)
    return groups


# ------------------------------------------------------------------------------------------------ database ---
def _is_schema_error(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in ("UndefinedTable", "UndefinedColumn"):
        return True
    msg = str(exc).lower()
    return ("does not exist" in msg or "undefined" in msg) and ("relation" in msg or "column" in msg or "news_feed" in msg)


def load_posts(conn, season: int = None, weeks=None) -> list:
    """Agent Wire posts for a scope (all seasons when season is None), as dicts.

    Raises SchemaMissing when news_feed or one of the Agent Wire columns is absent, so the caller can print a
    readable message instead of a traceback."""
    where = ["content_type = 'TWEET'", "slot LIKE %s"]
    params = ["Agent Wire%"]
    if season is not None:
        where.append("season = %s")
        params.append(season)
    if weeks:
        where.append("week BETWEEN %s AND %s")
        params.extend([weeks[0], weeks[1]])
    sql = ("SELECT season, week, post_kind, content, persona, agent_id FROM news_feed WHERE "
           + " AND ".join(where) + " ORDER BY season, week, id")
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [d.name for d in cur.description]
            return [{c: v for c, v in zip(columns, row)} for row in cur.fetchall()]
    except Exception as exc:                      # noqa: BLE001 - classified, not swallowed
        if isinstance(exc, SchemaMissing) or _is_schema_error(exc):
            raise SchemaMissing(str(exc)) from exc
        raise


def parse_weeks(value: str):
    """'3-9' -> (3, 9); '7' -> (7, 7); None -> None."""
    if not value:
        return None
    parts = str(value).replace(",", "-").split("-")
    if len(parts) == 1 and parts[0].strip():
        week = int(parts[0])
        return week, week
    if len(parts) == 2 and all(p.strip() for p in parts):
        start, end = int(parts[0]), int(parts[1])
        if start > end:
            raise ValueError("week range must be low-high")
        return start, end
    raise ValueError("weeks must look like 3-9 or 7")


# -------------------------------------------------------------------------------------------------- printing ---
def _pct(value) -> str:
    return f"{value * 100:.1f}%" if value is not None else "-"


def print_report(report: dict, out=None):
    out = out or sys.stdout
    print(f"Agent Wire quality report — season {report['season']}"
          f"{' weeks ' + report['weeks'] if report.get('weeks') else ''}, {report['posts']} posts", file=out)
    if report.get("missing"):
        print(f"cannot read news_feed: {report['missing']}", file=out)
        print("news_feed needs content_type, slot, post_kind, content, persona and agent_id.", file=out)
    print(file=out)
    header = f"{'kind':<14}{'posts':>7}{'openers':>9}{'open%':>8}{'fwd%':>7}{'3+num%':>8}{'words':>7}{'no-end%':>9}"
    print(header, file=out)
    for kind, m in report.get("by_kind", {}).items():
        print(f"{kind:<14}{m['posts']:>7}{m['distinct_openers']:>9}{_pct(m['opener_ratio']):>8}"
              f"{_pct(m['forward_share']):>7}{_pct(m['share_3plus_numbers']):>8}"
              f"{m['words_per_post_median']:>7.0f}{_pct(m['no_closing_punctuation_share']):>9}", file=out)
    overall = report.get("overall", {})
    if overall:
        print(f"{'OVERALL':<14}{overall['posts']:>7}{overall['distinct_openers']:>9}"
              f"{_pct(overall['opener_ratio']):>8}{_pct(overall['forward_share']):>7}"
              f"{_pct(overall['share_3plus_numbers']):>8}{overall['words_per_post_median']:>7.0f}"
              f"{_pct(overall['no_closing_punctuation_share']):>9}", file=out)
    if overall:
        overlap = overall.get("mean_trigram_overlap")
        print(f"\nmean pairwise trigram overlap within a week: {overlap:.3f}" if overlap is not None
              else "\nmean pairwise trigram overlap within a week: n/a", file=out)
        print(f"numbers per post (mean): {overall['numbers_per_post_mean']:.2f}", file=out)
    mix = report.get("kind_mix", {})
    if mix:
        print("\nkind mix: " + ", ".join(f"{k} {v['posts']} ({_pct(v['share'])})" for k, v in mix.items()), file=out)
    acc = report.get("accounts", {})
    if acc:
        top = ", ".join(f"{a} {n}" for a, n in acc.get("top", [])[:5])
        print(f"posts per account: {acc['distinct_accounts']} accounts, {acc['posts_per_account_mean']:.1f} each; "
              f"most: {top}", file=out)
    arch = report.get("by_archetype", {})
    if arch:
        print("median words by archetype: " + ", ".join(
            f"{k} {v['words_per_post_median']:.0f}" for k, v in sorted(arch.items())), file=out)
    baseline = report.get("baseline")
    if baseline:
        print("\nnote: S5 counted the whole archive stored in madden_2036 (both seasons); the table above is the "
              "requested season only.", file=out)
        print(f"\nS5 baseline (whole database, all seasons; {baseline['posts']} posts, measured 2026-10-31):", file=out)
        for kind, c in baseline["checks"].items():
            status = "PASS" if c["match"] else "FAIL"
            print(f"  {kind:<13} {c['posts']} posts / {c['openers']} distinct two-word openers "
                  f"(expected {c['expected_posts']}/{c['expected_openers']})  {status}", file=out)


def _read_only(conn):
    """Make every statement on this connection read-only.

    Autocommit plus a session-level `default_transaction_read_only` keeps each query in its own read-only
    transaction: a missing table never aborts the rest of the report."""
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET default_transaction_read_only = on")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Measure Agent Wire post repetition, mix and shape (read-only).")
    ap.add_argument("--db", required=True, help="Postgres DSN (e.g. 'host=localhost dbname=madden_2036 user=postgres password=postgres')")
    ap.add_argument("--season", type=int, required=True, help="season to report")
    ap.add_argument("--weeks", help="week range to report, e.g. 3-9 or 7")
    ap.add_argument("--json", help="write the full report to this JSON file")
    args = ap.parse_args(argv)

    try:
        weeks = parse_weeks(args.weeks)
    except ValueError as exc:
        ap.error(str(exc))

    import psycopg

    conn = psycopg.connect(args.db)
    try:
        _read_only(conn)
        posts = load_posts(conn, args.season, weeks)
        try:
            archive = load_posts(conn, None, None)
        except SchemaMissing:
            archive = None
    except SchemaMissing as exc:
        report = {"generated": datetime.datetime.now().isoformat(timespec="seconds"), "db": args.db,
                  "season": args.season, "weeks": None, "posts": 0, "missing": str(exc), "overall": {},
                  "by_kind": {}, "kind_mix": {}, "accounts": {}, "by_archetype": {}, "by_week": {}}
        print_report(report)
        if args.json:
            _write_json(args.json, report)
        return 0
    finally:
        conn.close()

    report = build_report(posts, archive, db=args.db, season=args.season, weeks=weeks)
    print_report(report)
    if args.json:
        _write_json(args.json, report)
    return 0


def _write_json(path: str, report: dict):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w") as f:
        json.dump(report, f, indent=1, ensure_ascii=False, default=str)
    print(f"JSON: {path}")


if __name__ == "__main__":
    sys.exit(main())
