"""pipeline/save_reader/prune_capacity.mjs: the capacity prune must re-deflate the rebuilt
image into the slot's fixed tail offset, and it refused a fresh clone because it deflated
with zlib's defaults (check-d, 2026-11-12, M-0855 finding 1: "rebuilt stream (3137978) is
longer than the original (3056682)").

These tests pin the compression choice and the read-back on a real save: node only, no PC,
no Madden and no network. The save is copied to a temporary directory first — the fixture
at the repository root is only ever read.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_READER = os.path.join(ROOT, "pipeline", "save_reader")
PRUNER = os.path.join(SAVE_READER, "prune_capacity.mjs")
READER = os.path.join(SAVE_READER, "pc_save_reader.mjs")
# The sample save sits at the main checkout's root (a worktree has no copy of its own).
SAMPLE_SAVE = os.environ.get("MU_SAMPLE_SAVE") or next(
    (p for p in (os.path.join(ROOT, "CAREER-GIANTS-AUTOSAVE"), "/home/jrereyi/Project-Mu/CAREER-GIANTS-AUTOSAVE")
     if os.path.exists(p)), os.path.join(ROOT, "CAREER-GIANTS-AUTOSAVE"))
TWENTY_MIB = 20 * 1024 * 1024
SLOT_SIZE = 5662392


def _node():
    return shutil.which("node")


def _ready():
    return bool(_node() and os.path.exists(PRUNER) and os.path.exists(SAMPLE_SAVE)
                and os.path.isdir(os.path.join(SAVE_READER, "node_modules", "madden-franchise")))


pytestmark = pytest.mark.skipif(
    not _ready(), reason="node, pipeline/save_reader node_modules or the sample save is missing")

NODE_HELPERS = """
import fs from 'node:fs';
import zlib from 'node:zlib';
const [file, mode] = process.argv.slice(1);
const raw = fs.readFileSync(file);
const len = raw[0x4a] | (raw[0x4b] << 8) | (raw[0x4c] << 16);
const image = zlib.inflateSync(raw.subarray(0x52, 0x52 + len));
if (mode === 'image') process.stdout.write(String(image.length));
else if (mode === 'default') process.stdout.write(String(zlib.deflateSync(image, { level: 6, memLevel: 8, windowBits: 15 }).length));
else throw new Error(`unknown mode ${mode}`);
"""


def _node_facts(path, mode):
    """`image` = the stream's unpacked length, `default` = zlib's default re-deflate length."""
    res = subprocess.run([_node(), "--input-type=module", "-e", NODE_HELPERS, path, mode],
                         capture_output=True, text=True, timeout=300, cwd=SAVE_READER)
    assert res.returncode == 0, res.stderr[-2000:]
    return int(res.stdout.strip())


@pytest.fixture(scope="module")
def pruned(tmp_path_factory):
    """One prune of the sample save, kept for every test in the module."""
    d = tmp_path_factory.mktemp("prune_capacity")
    source = d / "CAREER-MU2026"
    out = d / "CAREER-TUNE-X"
    report = d / "report.json"
    shutil.copyfile(SAMPLE_SAVE, source)
    res = subprocess.run([_node(), PRUNER, str(source), "--out", str(out), "--report", str(report)],
                         capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    assert res.returncode == 0, (res.stdout[-3000:] + res.stderr[-3000:])
    return {"dir": d, "source": source, "out": out, "report": json.loads(report.read_text()),
            "stdout": res.stdout, "before": source.read_bytes(), "after": out.read_bytes()}


def test_a_fresh_clone_prunes_without_the_length_refusal(pruned):
    """The reported failure: the rebuilt stream came out longer than the original."""
    rep = pruned["report"]
    assert rep["problems"] == [], rep["problems"]
    assert rep["streamAfter"] <= rep["streamBefore"], (
        f"rebuilt stream {rep['streamAfter']} > original {rep['streamBefore']}")
    assert len(pruned["after"]) == len(pruned["before"]) == SLOT_SIZE, "the slot keeps its fixed size"


def test_the_prune_actually_reclaims_capacity_and_headroom(pruned):
    rep = pruned["report"]
    assert rep["saved"] > 500_000, f"expected the Highlight cut, saved {rep['saved']}"
    headroom_before = TWENTY_MIB - rep["unpackedBefore"]
    headroom_after = TWENTY_MIB - rep["unpackedAfter"]
    assert headroom_after > headroom_before + 500_000, (headroom_before, headroom_after)
    # A fresh CAREER-MU2026 clone has ~513 KB of the 20 MiB limit left; the prune must lift
    # that towards the ~1.0 MB the plan measured (SAVE_CAPACITY_PRUNE_PLAN section 9).
    assert headroom_after > 1_000_000, headroom_after
    target = next(t for t in rep["tables"] if t["name"] == "Highlight")
    assert (target["capacity"], target["newCapacity"]) == (3200, 512)


def test_zlibs_defaults_would_have_been_refused_so_the_settings_are_the_fix(pruned):
    """The root cause, measured on this same rebuilt image: with zlib's default settings the
    stream is longer than the original, which is what the pruner used to do; the settings it
    now reports fit."""
    rep = pruned["report"]
    default_length = _node_facts(str(pruned["out"]), "default")
    assert default_length > rep["streamBefore"], (default_length, rep["streamBefore"])
    assert rep["streamAfter"] < default_length
    assert (rep["compression"]["level"], rep["compression"]["memLevel"]) != (6, 8)


def test_the_written_stream_is_the_rebuilt_image_and_the_tail_never_moved(pruned):
    rep = pruned["report"]
    before, after = pruned["before"], pruned["after"]
    assert after[:0x4a] == before[:0x4a], "the FBCHUNKS header is preserved"
    stream_length = after[0x4a] | (after[0x4b] << 8) | (after[0x4c] << 16)
    assert stream_length == rep["streamAfter"]
    assert _node_facts(str(pruned["out"]), "image") == rep["unpackedAfter"]
    # The tail begins at the first non-zero byte after the original stream end (the
    # RelocationTracker region, at a fixed absolute offset) and is byte-identical to EOF.
    old_end = 0x52 + rep["streamBefore"]
    tail = next((i for i in range(old_end, len(before)) if before[i]), -1)
    assert tail > 0 and after[tail:] == before[tail:]
    assert after[0x52 + stream_length:tail].count(0) == tail - (0x52 + stream_length), \
        "the gap left by the shorter stream is zero-filled"


def test_the_reader_reads_the_pruned_save_as_the_same_image(pruned):
    """No data is lost: the reader's snapshot is identical except its meta block."""
    snapshots = {}
    for name, path in (("before", pruned["source"]), ("after", pruned["out"])):
        snap = pruned["dir"] / f"snap-{name}.json"
        res = subprocess.run([_node(), READER, str(path), "--out", str(snap)],
                             capture_output=True, text=True, timeout=600, cwd=SAVE_READER)
        assert res.returncode == 0, res.stderr[-3000:]
        snapshots[name] = json.loads(snap.read_text())
    before, after = snapshots["before"], snapshots["after"]
    assert before.keys() == after.keys()
    assert after["players"], "the snapshot has players"
    differing = [k for k in before if k != "meta" and before[k] != after[k]]
    assert differing == [], f"the prune changed {differing}"


def test_no_deflate_setting_fitting_is_a_refusal_not_a_longer_stream(tmp_path):
    """The guard the prune must keep: when no candidate fits, say so and write nothing."""
    script = tmp_path / "refusal.mjs"
    script.write_text("""
import zlib from 'node:zlib';
import { pathToFileURL } from 'node:url';
const mod = await import(pathToFileURL(process.argv[2]).href);
const image = Buffer.alloc(200000, 7);
const fits = mod.deflateImage(image, 10 ** 9);
const refused = mod.deflateImage(image, 1);
process.stdout.write(JSON.stringify({
  fits: fits.fits,
  fitsInflates: zlib.inflateSync(fits.stream).equals(image),
  refused: refused.fits,
  refusedIsSmallest: refused.stream.length === Math.min(...refused.tried.map((t) => t.bytes)),
  candidates: mod.DEFLATE_CANDIDATES.length,
  triedAll: refused.tried.length,
}));
""")
    res = subprocess.run([_node(), str(script), PRUNER], capture_output=True, text=True, timeout=300)
    assert res.returncode == 0, res.stderr[-3000:]
    facts = json.loads(res.stdout)
    assert facts["fits"] is True and facts["fitsInflates"] is True
    assert facts["refused"] is False
    assert facts["refusedIsSmallest"] is True
    assert facts["triedAll"] == facts["candidates"] > 1
