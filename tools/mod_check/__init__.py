"""Visual mod check: did a mod's assets appear on Madden 27's screen? (VISUAL_MOD_CHECK_PLAN U1.)

One command loads a ``CAREER-TUNE-*`` clone of its own, records named segments of a game, and
answers "did this mod's assets appear?" with a deterministic pass / fail / unknown per asset
(``tools.madden_pilot.assets.check_frame``). The vision model only describes a segment; it never
decides a verdict and never presses a key.

Modules:

* ``segments`` - ``segments.json`` and ``checks.json`` for one mod, validated with ``PlanError``.
* ``runner`` - ``run`` (clone, drive, record, score, report) and ``capture`` (the pass/native
  reference pair), plus the real ``PilotDriver``.
* ``report`` - the ``report.json`` shape of plan section 2.6 and its ``report.md`` rendering.
* ``__main__`` - ``python3 -m tools.mod_check run|capture``.

Everything under ``data/mod_checks/`` is git-ignored: segment plans, ``refs/`` crops and
``runs/<stamp>_<slug>/`` reports stay local, like the spike's ``data/spikes/``.
"""
from __future__ import annotations

from tools.mod_check.report import build_report, markdown, notice_line, verdict_for, write_report
from tools.mod_check.runner import CueMissed, DriverError, PilotDriver, capture, run
from tools.mod_check.segments import CUES, Plan, PlanError, Segment, load_checks, load_plan

__all__ = [
    "CUES",
    "CueMissed",
    "DriverError",
    "Plan",
    "PlanError",
    "PilotDriver",
    "Segment",
    "build_report",
    "capture",
    "load_checks",
    "load_plan",
    "markdown",
    "notice_line",
    "run",
    "verdict_for",
    "write_report",
]
