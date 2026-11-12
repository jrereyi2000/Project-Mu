"""The visual mod check report (VISUAL_MOD_CHECK_PLAN section 2.6).

``report.json`` is exactly::

    {"save": str, "mod": str,
     "segments": [{"name": str, "cue": str, "recorded_s": int, "frames": [str, ...],
                   "checks": {asset: {"status": "pass|fail|unknown",
                                      "measures": {name: number},
                                      "frame": str|null, "why": str}},
                   "summary": str}],
     "verdict": "pass|fail|unknown", "minutes": float,
     "pack": str|null, "autosave_written": bool|null}

``save`` is the clone the run actually scored (the run never scores a save it did not make);
``pack`` is the pack the launch used (plan R6); ``autosave_written`` says whether quitting wrote
``<save>-AUTOSAVE`` (plan R1/D1). ``recorded_s`` is the requested length of the segment, and
``frames`` lists what was actually written, relative to the run directory, in recording order;
each asset's ``frame`` names the frame the verdict came from (the latest decisive one).
``summary`` is the vision model's description, or exactly ``model unavailable`` when the backend
could not answer.

Verdict: ``fail`` when any checked asset fails, ``pass`` when at least one asset was checked and
every one passed, otherwise ``unknown`` (no checks, or every check inconclusive). One ntfy line
follows the pilot's style: ``mod_check <slug>: 2/3 pass (halftime fail)``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

STATUSES = ("pass", "fail", "unknown")
MODEL_UNAVAILABLE = "model unavailable"
REPORT_JSON = "report.json"
REPORT_MD = "report.md"


def check_result(status: str, measures: Mapping[str, Any] | None, frame: str | None,
                 why: str) -> dict:
    """One asset's entry, in the report's exact shape."""
    return {"status": status if status in STATUSES else "unknown",
            "measures": dict(measures or {}), "frame": frame, "why": str(why or "")}


def segment_result(name: str, cue: str | None, recorded_s: int, frames: Iterable[str],
                   checks: Mapping[str, dict], summary: str | None,
                   preroll_s: float = 0.0) -> dict:
    """One segment's entry, in the report's exact shape."""
    return {"name": name, "cue": cue, "recorded_s": int(recorded_s),
            "preroll_s": round(float(preroll_s), 3), "frames": list(frames),
            "checks": dict(checks), "summary": summary or MODEL_UNAVAILABLE}


def _statuses(segments: Iterable[Mapping[str, Any]]) -> list[str]:
    return [str(entry.get("status", "unknown"))
            for segment in segments for entry in (segment.get("checks") or {}).values()]


def verdict_for(segments: Iterable[Mapping[str, Any]]) -> str:
    """``fail`` if anything failed, ``pass`` if something passed and nothing did not, else unknown."""
    segments = list(segments)
    statuses = _statuses(segments)
    if any(status == "fail" for status in statuses):
        return "fail"
    if statuses and all(status == "pass" for status in statuses):
        return "pass"
    return "unknown"


def build_report(save: str, mod: str, segments: Iterable[Mapping[str, Any]], minutes: float,
                 pack: str | None = None, autosave_written: bool | None = None, *,
                 pack_required: str | None = None, pack_actual: str | None = None,
                 pack_verified: bool = False, launch: Mapping[str, Any] | None = None,
                 provenance: Mapping[str, Any] | None = None,
                 quit: bool | None = None, quit_route: str | None = None,
                 end_screen: str | None = None,
                 operational: Mapping[str, Any] | None = None) -> dict:
    """Assemble ``report.json``. ``segments`` are ``segment_result`` entries.

    ``verdict`` is the pixels alone (``verdict_for``); ``operational`` carries whether the run
    actually finished and released the PC. A caller must not read ``verdict`` as completion.
    """
    entries = list(segments)
    status = dict(operational or {})
    status.setdefault("status", "not_started")
    status.setdefault("why", "")
    status.setdefault("recovery", None)
    status.setdefault("job_status", None)
    status.setdefault("exit_code", None)
    return {"save": str(save), "mod": str(mod), "segments": entries,
            "verdict": verdict_for(entries), "minutes": round(float(minutes), 2),
            "pack": pack, "pack_required": pack_required, "pack_actual": pack_actual,
            "pack_verified": bool(pack_verified),
            "launch": dict(launch) if launch is not None else None,
            "provenance": dict(provenance) if provenance is not None else None,
            "quit": quit, "quit_route": quit_route, "end_screen": end_screen,
            "operational": status, "autosave_written": autosave_written}


def operational_complete(report: Mapping[str, Any]) -> bool:
    """True only when the run both scored its pixels and released the PC cleanly."""
    return str((report.get("operational") or {}).get("status")) == "complete"


# --------------------------------------------------------------------------- markdown
def _number(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _measures_text(measures: Mapping[str, Any] | None) -> str:
    if not measures:
        return "-"
    return "; ".join(f"{key}={_number(value)}" for key, value in measures.items())


def _cell(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "\\|")


def _worst(checks: Mapping[str, dict]) -> str:
    statuses = [str(entry.get("status", "unknown")) for entry in (checks or {}).values()]
    if any(status == "fail" for status in statuses):
        return "fail"
    if statuses and all(status == "pass" for status in statuses):
        return "pass"
    return "unknown"


def markdown(report: Mapping[str, Any]) -> str:
    """``report.md``: one table per segment, then the model's description."""
    autosave = report.get("autosave_written")
    autosave_text = "unknown" if autosave is None else ("yes" if autosave else "no")
    operational = report.get("operational") or {}
    quit_text = "unknown" if report.get("quit") is None else ("yes" if report.get("quit") else "no")
    pack_text = (f"- pack: `{report.get('pack')}` (required `{report.get('pack_required')}`, "
                 f"verified: {'yes' if report.get('pack_verified') else 'no'})"
                 if report.get("pack") else
                 f"- pack: unknown (required `{report.get('pack_required')}`; the launch did not verify one)")
    provenance = report.get("provenance") or {}
    process = provenance.get("process") or {}
    applied = provenance.get("applied") or {}
    provenance_text = (
        "- launch provenance: "
        + ("**reused** the verified process" if provenance.get("reused") else "cold launch recorded")
        + f" (process identity {'verified' if provenance.get('process_verified') else 'UNVERIFIED'}"
        + (f", pid {process.get('pid')}" if process.get("pid") else "")
        + f"; applied pack {'verified' if provenance.get('applied_verified') else 'UNVERIFIED'}"
        + (f", fingerprint {str(applied.get('fingerprint'))[:12]}" if applied.get("fingerprint") else "")
        + ")")
    lines = [
        f"# Mod check: {report.get('mod')}",
        "",
        f"- save: `{report.get('save')}`",
        pack_text,
        provenance_text,
        f"- autosave written: {autosave_text}",
        f"- game quit: {quit_text}"
        + (f" via {report.get('quit_route')}" if report.get("quit_route") else "")
        + (f", end screen {report.get('end_screen')}" if report.get("end_screen") else ""),
        f"- run status: **{operational.get('status', 'not_started')}**"
        + (f" ({operational.get('why')})" if operational.get("why") else ""),
        f"- verdict (pixels): **{report.get('verdict')}**",
        f"- minutes: {report.get('minutes')}",
        "",
    ]
    if operational.get("recovery"):
        lines += [f"> Recovery: {operational['recovery']}", ""]
    for segment in report.get("segments") or []:
        cue = segment.get("cue") or "no cue"
        lines += [f"## {segment.get('name')}", "",
                  f"cue `{cue}`; {segment.get('recorded_s')} s requested; "
                  f"preroll {segment.get('preroll_s', 0.0):g} s; "
                  f"{len(segment.get('frames') or [])} frame(s).", "",
                  "| asset | status | measures | frame | why |",
                  "|---|---|---|---|---|"]
        checks = segment.get("checks") or {}
        if not checks:
            lines.append("| - | - | - | - | no assets were checked |")
        for asset, entry in checks.items():
            lines.append(
                f"| {_cell(asset)} | {_cell(entry.get('status'))} "
                f"| {_cell(_measures_text(entry.get('measures')))} "
                f"| {_cell(entry.get('frame'))} | {_cell(entry.get('why'))} |")
        lines += ["", segment.get("summary") or MODEL_UNAVAILABLE, ""]
    return "\n".join(lines).rstrip() + "\n"


def notice_line(report: Mapping[str, Any]) -> str:
    """One ntfy line in the pilot's style: ``mod_check <slug>: 2/3 pass (halftime fail)``.

    Each segment that did not fully pass is named with its worst status, so a missed halftime
    reads as a missed halftime rather than a general failure.
    """
    statuses = [str(entry.get("status", "unknown"))
                for segment in report.get("segments") or []
                for entry in (segment.get("checks") or {}).values()]
    passed = sum(1 for status in statuses if status == "pass")
    parts = []
    for segment in report.get("segments") or []:
        worst = _worst(segment.get("checks") or {})
        if worst != "pass":
            parts.append(f"{segment.get('name')} {worst}")
    line = f"mod_check {report.get('mod')}: {passed}/{len(statuses)} pass"
    if parts:
        line += f" ({', '.join(parts)})"
    elif not statuses:
        line += " (no assets checked)"
    operational = report.get("operational") or {}
    if operational.get("status") not in (None, "complete"):
        line += f"; run {operational.get('status')}"
        if operational.get("why"):
            line += f": {operational['why']}"
        if operational.get("recovery"):
            line += f" — {operational['recovery']}"
    return line


def write_report(report: Mapping[str, Any], run_dir: str | Path) -> tuple[Path, Path]:
    """Write ``report.json`` and ``report.md`` into ``run_dir``; return both paths."""
    folder = Path(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    json_path, md_path = folder / REPORT_JSON, folder / REPORT_MD
    json_path.write_text(json.dumps(dict(report), indent=1) + "\n", encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return json_path, md_path
