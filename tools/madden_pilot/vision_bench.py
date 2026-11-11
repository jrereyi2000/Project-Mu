"""Vision model comparison test for the Madden spike agent (MADDEN_SPIKE_AGENT_PLAN section 2.10).

    python3 -m tools.madden_pilot.vision_bench --models ollama:gemma4:e4b,ollama:qwen3.5:4b \
        [--labels data/spikes/bench_labels.json] [--out data/spikes/bench/]

``build_labels()`` makes the label set from what the project already knows: every pilot
``steps/NNNN_<SCREEN>.png`` with a catalogued screen gives the expected ``kind``, ``options``
(from ``catalog.json``) and ``selected`` (from ``border_highlight`` where the screen reads the
border); the hand-written ``data/spikes/bench_presentation_labels.json`` adds the presentation
frames and their expected scorebug ``layout`` when it exists. A missing presentation file is
skipped quietly.

Each model is scored on kind accuracy, options recall, selected exact match, scorebug-layout
accuracy, median and 95th-percentile seconds, and video memory. The layout score uses its own
targeted prompt (S-A: models name the scorebug but not the network from the small logo). A
model that refuses the image is reported "no vision" and skipped. The bench writes ``bench.md``
and ``vision_choice.json``: the live pick is the best local score within the 5 GB memory
budget, and the heavy pick is the best local score overall.

``--help`` performs no live calls.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from tools.madden_pilot import vision
from tools.madden_pilot.screens import UNKNOWN, Frame, border_highlight, load_catalog

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LABELS = ROOT / "data" / "spikes" / "bench_labels.json"
DEFAULT_PRESENTATION = ROOT / "data" / "spikes" / "bench_presentation_labels.json"
DEFAULT_OUT = ROOT / "data" / "spikes" / "bench"
DEFAULT_BUDGET_GB = 5.0

# The screen kind each catalogued screen belongs to. S-A's hand mapping is the base; screens
# it did not label use the closest kind. A screen missing from this map is not labelled.
KIND_BY_SCREEN: dict[str, str] = {
    "BOOT_SPLASH": "loading",
    "TITLE_SCREEN": "menu",
    "MAIN_MENU": "menu",
    "MAIN_MENU_RAIL": "menu",
    "FRANCHISE_LANDING": "menu",
    "FRANCHISE_LIST": "menu",
    "CHOOSE_TEAM": "menu",
    "ADVANCE_MENU": "menu",
    "DRAFT_LAUNCH": "menu",
    "COACH_PROGRESS": "menu",
    "DRAFT_HUB": "hub",
    "HUB_GAMEDAY": "hub",
    "HUB_WEEKLY": "hub",
    "HUB_OTHER": "hub",
    "HUB_BYE": "hub",
    "HUB_WEEKLY_DRAFT_CARD": "hub",
    "LIVE_EVENT_FRENZY": "hub",
    "LIVE_EVENT_FRENZY_MODAL": "dialog",
    "DELETE_CONFIRM": "dialog",
    "EXIT_MADDEN": "dialog",
    "INCOMPLETE_TASKS": "dialog",
    "QUIT_CONFIRM": "dialog",
    "SAVEPOINT_OVERWRITE": "dialog",
    "SAVEPOINT_DIALOG": "dialog",
    "PROGRESSION_WARNING": "dialog",
    "ROSTER_DECISION_PROMPT": "dialog",
    "FORCE_ADVANCE_CONFIRM": "dialog",
    "ADVANCE_LOCKED": "dialog",
    "FREE_AGENCY_ENDED": "dialog",
    "ACCOUNT_ERROR": "dialog",
    "WEEKLY_RECAP": "replay_or_cutscene",
}

LAYOUT_PROMPT = (
    "This is a Madden NFL frame with a broadcast scorebug. Which scorebug layout is it? "
    "Reply with exactly one of: standard, sn, mn, tn, none."
)
LAYOUT_WORDS = {
    "standard": ("standard", "cbs", "fox"),
    "sn": ("sn", "snf", "sunday"),
    "mn": ("mn", "mnf", "monday"),
    "tn": ("tn", "tnf", "thursday", "amazon", "prime"),
}


# --------------------------------------------------------------------------- labels
def _normalise(value: Any) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).split())


def _resolve(path: str | Path) -> str:
    candidate = Path(path)
    return str(candidate if candidate.is_absolute() else ROOT / candidate)


def build_labels(pilot_root: str | Path | None = None,
                 presentation_path: str | Path | None = None,
                 catalog: dict | None = None) -> list[dict]:
    """Labelled frames from pilot runs plus the hand-written presentation labels, if present."""
    runs = Path(pilot_root or ROOT / "data" / "pilot_runs")
    cat = catalog or load_catalog()
    labels: list[dict] = []

    for png in sorted(runs.glob("*/steps/*.png")):
        match = re.match(r"^\d+_(.+)\.png$", png.name)
        if not match:
            continue
        screen = match.group(1)
        if screen == UNKNOWN or screen not in KIND_BY_SCREEN:
            continue
        spec = (cat.get("screens") or {}).get(screen) or {}
        options = [str(option) for option in (spec.get("options") or [])]
        selected = None
        if spec.get("highlight") == "border" and options:
            words, lines = [], None
            meta = png.with_suffix(".json")
            try:
                payload = json.loads(meta.read_text(encoding="utf-8"))
                words, lines = payload.get("words") or [], payload.get("lines")
            except (OSError, ValueError, AttributeError):
                pass
            try:
                selected = border_highlight(Frame(str(png), words, lines), options,
                                            reach=spec.get("border_reach", 0.06))
            except (OSError, ValueError):
                selected = None
        labels.append({"png": str(png), "source": "catalog", "screen": screen,
                       "kind": KIND_BY_SCREEN[screen], "options": options,
                       "selected": selected, "layout": None})

    presentation = Path(presentation_path or DEFAULT_PRESENTATION)
    if presentation.exists():
        try:
            extra = json.loads(presentation.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            extra = []
        if isinstance(extra, dict):
            extra = extra.get("frames") or []
        for entry in extra if isinstance(extra, list) else []:
            if not isinstance(entry, dict):
                continue
            png = entry.get("png") or entry.get("frame")
            if not png:
                continue
            labels.append({
                "png": _resolve(png),
                "source": "presentation",
                "screen": None,
                "kind": str(entry.get("kind") or "gameplay"),
                "options": [str(option) for option in (entry.get("options") or [])],
                "selected": entry.get("selected"),
                "layout": entry.get("layout"),
            })
    return labels


def load_labels(path: str | Path) -> list[dict]:
    """Read a prebuilt label manifest (like S-A's ``manifest.json``)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("frames") or data.get("labels") or []
    if not isinstance(data, list):
        raise ValueError(f"{path} does not hold a list of labels")
    out = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        png = entry.get("png") or entry.get("frame")
        if not png:
            continue
        out.append({"png": _resolve(png), "source": entry.get("source") or "label",
                    "screen": entry.get("screen"),
                    "kind": str(entry.get("kind") or "other"),
                    "options": [str(option) for option in (entry.get("options") or [])],
                    "selected": entry.get("selected"),
                    "layout": entry.get("layout")})
    return out


# --------------------------------------------------------------------------- scoring
def _field(result: Any, name: str) -> Any:
    if result is None:
        return None
    if isinstance(result, dict):
        return result.get(name)
    return getattr(result, name, None)


def _option_hit(expected: str, got: list) -> bool:
    want = _normalise(expected)
    return bool(want) and any(want in _normalise(value) or _normalise(value) in want
                              for value in got or [])


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return ordered[index]


def score_frames(per: list[tuple[dict, Any, str | None]]) -> dict:
    """Score aligned (label, ScreenDescription|None, layout answer) tuples."""
    kind_hits, recalls, selected, layouts, times = [], [], [], [], []
    failed = 0
    for label, result, layout in per:
        if result is None:
            failed += 1
        kind_hits.append(bool(result is not None and
                              str(_field(result, "kind") or "").lower() ==
                              str(label.get("kind") or "").lower()))
        expected_options = label.get("options") or []
        if expected_options:
            got = _field(result, "options") or ()
            recalls.append(sum(1 for option in expected_options
                               if _option_hit(option, got)) / len(expected_options))
        expected_selected = label.get("selected")
        if expected_selected:
            got = _field(result, "selected")
            selected.append(bool(got) and _normalise(got) == _normalise(expected_selected))
        expected_layout = label.get("layout")
        if expected_layout:
            layouts.append(bool(layout) and _normalise(layout) == _normalise(expected_layout))
        seconds = _field(result, "seconds")
        if seconds is not None:
            times.append(float(seconds))

    def mean(values: list) -> float | None:
        return sum(values) / len(values) if values else None

    scores = {
        "frames": len(per) - failed,
        "failed": failed,
        "kind_acc": mean(kind_hits),
        "options_recall": mean(recalls),
        "selected_acc": mean(selected),
        "layout_acc": mean(layouts),
        "median_s": statistics.median(times) if times else None,
        "p95_s": _p95(times),
    }
    parts = [value for value in (scores["kind_acc"], scores["options_recall"],
                                 scores["selected_acc"], scores["layout_acc"])
             if value is not None]
    scores["composite"] = mean(parts) if parts else 0.0
    return scores


def _sort_key(entry: dict) -> tuple:
    median = entry.get("median_s")
    return (float(entry.get("composite") or 0.0), -(median if median is not None else 1e9))


def pick(scores: dict[str, dict], budget_gb: float = DEFAULT_BUDGET_GB) -> tuple[str | None, str | None]:
    """(live, heavy): best local model within the memory budget, and the best local overall."""
    answered = [name for name, entry in scores.items() if entry.get("status") == "ok"]
    local = [name for name in answered if name.startswith("ollama:")]
    pool = local or answered
    if not pool:
        return None, None
    within = [name for name in pool
              if scores[name].get("vram_gb") is None or scores[name]["vram_gb"] <= budget_gb]
    live = max(within, key=lambda name: _sort_key(scores[name])) if within else None
    heavy = max(pool, key=lambda name: _sort_key(scores[name]))
    return live, heavy


# --------------------------------------------------------------------------- GPU memory
def nvidia_smi_used_gb() -> float | None:
    """Total GPU memory in use, in GB, or None when nvidia-smi is unavailable."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        try:
            return float(line.strip()) / 1024.0
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- layout prompt
def parse_layout(text: str) -> str | None:
    """The scorebug layout named in a targeted answer: standard, sn, mn, tn, or None."""
    low = (text or "").lower()
    token = re.search(r"\b(standard|sn|mn|tn)\b", low)
    if token:
        return token.group(1)
    for layout, words in LAYOUT_WORDS.items():
        if any(re.search(rf"\b{re.escape(word)}\b", low) for word in words):
            return layout
    return None


def describe_layout(png: str | Path, *, backend: str | None = None, model: str | None = None,
                    timeout: float = 30.0, client: Any = None) -> str | None:
    """Ask the model the targeted scorebug-layout question (its own prompt, per S-A)."""
    try:
        text, _, _ = vision.request_text(png, backend=backend, model=model,
                                          prompt=LAYOUT_PROMPT, timeout=timeout, client=client)
    except (vision.VisionUnavailable, ValueError):
        return None
    return parse_layout(text)


# --------------------------------------------------------------------------- the bench
def bench(models: list[str], labels: list[dict], *,
          describe_fn: Callable[..., Any] | None = None,
          layout_fn: Callable[..., str | None] | None = None,
          vram_fn: Callable[[str], float | None] | None = None,
          timeout: float = 30.0, budget_gb: float = DEFAULT_BUDGET_GB,
          choice: dict | None = None) -> dict:
    """Run every model over the labels and return scores plus the live/heavy picks.

    ``describe_fn``, ``layout_fn`` and ``vram_fn`` are injection points for offline tests. No
    files are written here; ``main`` writes the report.
    """
    describe_fn = describe_fn or vision.describe
    layout_fn = layout_fn or describe_layout
    current_choice = choice if choice is not None else vision.read_vision_choice()
    baseline = nvidia_smi_used_gb() if vram_fn is None else None

    scores: dict[str, dict] = {}
    for model in models:
        per: list[tuple[dict, Any, str | None]] = []
        last_error = None
        for label in labels:
            result = None
            layout = None
            try:
                result = describe_fn(label["png"], backend=model, timeout=timeout)
            except (vision.VisionUnavailable, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                result = None
            if label.get("layout"):
                try:
                    layout = layout_fn(label["png"], backend=model, timeout=timeout)
                except (vision.VisionUnavailable, ValueError):
                    layout = None
            per.append((label, result, layout))
        entry = score_frames(per)
        if vram_fn is not None:
            entry["vram_gb"] = vram_fn(model)
        elif baseline is not None:
            used = nvidia_smi_used_gb()
            entry["vram_gb"] = None if used is None else max(used - baseline, 0.0)
        else:
            entry["vram_gb"] = None
        if per and entry["frames"] == 0:
            entry["status"] = "no vision"
            if last_error:
                entry["note"] = last_error
        elif entry["failed"]:
            entry["status"] = "ok"
            entry["note"] = f"{entry['failed']} frame(s) refused"
        else:
            entry["status"] = "ok"
        scores[model] = entry

    live, heavy = pick(scores, budget_gb)
    deepseek_models = [model for model in models if model.startswith("deepseek:")]
    if deepseek_models:
        deepseek_images = any(scores[model].get("status") == "ok" and
                              scores[model].get("frames", 0) > 0 for model in deepseek_models)
    else:
        deepseek_images = bool(current_choice.get("deepseek_images"))
    fallback = (str(current_choice.get("fallback") or vision.DEFAULT_FALLBACK)
                if deepseek_images else "none")
    result = {"models": scores, "live": live, "heavy": heavy, "fallback": fallback,
              "deepseek_images": deepseek_images, "labels": len(labels),
              "budget_gb": budget_gb}
    if current_choice.get("max_record_fps") is not None:
        # JR's S-A step 3 sets this; the bench must not wipe it when it rewrites the file.
        result["max_record_fps"] = current_choice["max_record_fps"]
    return result


# --------------------------------------------------------------------------- output files
def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def bench_markdown(result: dict) -> str:
    lines = ["# Vision bench", "",
             f"Written {dt.datetime.now().isoformat(timespec='seconds')} over "
             f"{result.get('labels', 0)} labelled frames.", "",
             "| model | status | frames | kind | options recall | selected exact | layout | "
             "median s | p95 s | VRAM GB |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for model, entry in result["models"].items():
        lines.append("| " + " | ".join([
            model, entry.get("status", "-"), str(entry.get("frames", 0)),
            _fmt(entry.get("kind_acc")), _fmt(entry.get("options_recall")),
            _fmt(entry.get("selected_acc")), _fmt(entry.get("layout_acc")),
            _fmt(entry.get("median_s"), 2), _fmt(entry.get("p95_s"), 2),
            _fmt(entry.get("vram_gb"), 2),
        ]) + " |")
    lines += ["", f"Live pick (best local score within {result.get('budget_gb', 5)} GB): "
                  f"**{result.get('live') or 'none'}**.",
              f"Heavy pick (best local score): **{result.get('heavy') or 'none'}**.",
              f"Fallback: **{result.get('fallback') or 'none'}** "
              f"(DeepSeek images: {result.get('deepseek_images')}).", ""]
    return "\n".join(lines)


def choice_document(result: dict) -> dict:
    document = {
        "_written_by": "tools/madden_pilot/vision_bench.py",
        "_note": ("Live pick = best local model within the memory budget; heavy pick = best "
                  "local model overall. Written by the comparison test (plan section 2.10)."),
        "live": result.get("live"),
        "heavy": result.get("heavy"),
        "fallback": result.get("fallback"),
        "deepseek_images": bool(result.get("deepseek_images")),
        "scores": result.get("models", {}),
    }
    if result.get("max_record_fps") is not None:
        document["max_record_fps"] = result["max_record_fps"]
    return document


def write_outputs(result: dict, out_dir: str | Path) -> tuple[Path, Path]:
    """Write ``bench.md`` and ``vision_choice.json``; returns their paths."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    markdown = directory / "bench.md"
    choice = directory / "vision_choice.json"
    markdown.write_text(bench_markdown(result), encoding="utf-8")
    choice.write_text(json.dumps(choice_document(result), indent=2), encoding="utf-8")
    return markdown, choice


# --------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m tools.madden_pilot.vision_bench",
        description="Compare vision models over labelled Madden frames (plan section 2.10).")
    parser.add_argument("--models", default="",
                        help="comma-separated backends, e.g. ollama:gemma4:e4b,deepseek:deepseek-flash "
                             "(default: the choice file's live and heavy picks)")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS),
                        help="label manifest to read, or to write after building it when absent "
                             f"(default {DEFAULT_LABELS})")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"directory for bench.md and vision_choice.json (default {DEFAULT_OUT})")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="seconds allowed for one describe (default 30)")
    parser.add_argument("--budget-gb", type=float, default=DEFAULT_BUDGET_GB,
                        help="memory budget for the live pick in GB (default 5)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    if not models:
        choice = vision.read_vision_choice()
        models = [str(item) for item in (choice.get("live"), choice.get("heavy")) if item]
    if not models:
        models = [vision.default_backend()]

    labels_path = Path(args.labels)
    if labels_path.exists():
        labels = load_labels(labels_path)
    else:
        labels = build_labels()
        if labels:
            labels_path.parent.mkdir(parents=True, exist_ok=True)
            labels_path.write_text(json.dumps(labels, indent=2), encoding="utf-8")
    if not labels:
        print("no labelled frames found (no pilot steps with catalogued screens and no "
              "presentation labels)")
        return 2

    result = bench(models, labels, timeout=args.timeout, budget_gb=args.budget_gb)
    markdown, choice = write_outputs(result, args.out)
    canonical = vision.choice_path()
    if canonical.resolve() != choice.resolve():
        # The live default reads data/spikes/vision_choice.json (section 2.7), not --out.
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_text(choice.read_text(encoding="utf-8"), encoding="utf-8")
    print(bench_markdown(result))
    print(f"wrote {markdown} and {choice} (live pick also written to {canonical})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
