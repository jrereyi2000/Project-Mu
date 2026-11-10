"""Check results and their PASS / FAIL table (pure: no database, no save)."""
import json
from dataclasses import asdict, dataclass, field

PASS, FAIL, INFO, KNOWN = "PASS", "FAIL", "INFO", "KNOWN-OPEN"


@dataclass
class Check:
    """One check at one point of a run. `status` is PASS, FAIL, INFO (reported, never fails the run) or KNOWN-OPEN
    (a FAIL the caller listed with --known-open: shown, but not counted against the run)."""
    name: str
    stage: str
    status: str
    detail: str = ""
    data: dict = field(default_factory=dict)


def passed(ok):
    return PASS if ok else FAIL


def apply_known_open(checks, known):
    """FAILs whose name is in `known` become KNOWN-OPEN."""
    known = set(known or ())
    for c in checks:
        if c.status == FAIL and c.name in known:
            c.status = KNOWN
    return checks


def overall(checks):
    return FAIL if any(c.status == FAIL for c in checks) else PASS


def table(checks, width=100):
    """A fixed-width PASS/FAIL table, one line per check, details cut to `width`."""
    rows = [("STATUS", "STAGE", "CHECK", "DETAIL")]
    rows += [(c.status, c.stage, c.name, c.detail) for c in checks]
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows)
    w2 = max(len(r[2]) for r in rows)
    lines = []
    for i, (s, st, n, d) in enumerate(rows):
        d = d if len(d) <= width else d[:width - 3] + "..."
        lines.append(f"{s:<{w0}}  {st:<{w1}}  {n:<{w2}}  {d}")
        if i == 0:
            lines.append("-" * min(len(lines[0]) + 10, w0 + w1 + w2 + 6 + width))
    counts = {k: sum(1 for c in checks if c.status == k) for k in (PASS, FAIL, KNOWN, INFO)}
    lines.append(f"OVERALL {overall(checks)}: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v))
    return "\n".join(lines)


def to_json(checks, **extra):
    return json.dumps(dict(extra, overall=overall(checks), checks=[asdict(c) for c in checks]), indent=1,
                      default=str)


def timelines_checks(results):
    """One Check per timeline result (from timelines.run_one)."""
    out = []
    for r in results:
        if "error" in r:
            out.append(Check(r["key"], "timeline", FAIL, r["error"]))
            continue
        bits = []
        if r.get("issues"):
            bits.append(f"{len(r['issues'])} invariant issues: {r['issues'][0]}")
        if r.get("held_unpaid"):
            u = r["held_unpaid"][0]
            bits.append(f"{len(r['held_unpaid'])} held-unpaid seasons, e.g. {u['year']} {u['team']} {u['kind']} "
                        f"{u['who']}")
        if r.get("live_duplicates"):
            bits.append(f"live duplicates {r['live_duplicates']}")
        if r.get("team_checks"):
            bits.append(f"{len(r['team_checks'])} payroll mismatches")
        if r.get("expected"):
            bits.append("expected totals: " + "; ".join(r["expected"]))
        detail = "; ".join(bits) if bits else r.get("label", "")
        out.append(Check(r["key"], "timeline", passed(r["ok"]), detail))
    return out
