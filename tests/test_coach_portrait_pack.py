"""tools/coach_portrait_pack.py: the per-offseason coach portrait pack (COACH_SAVE_WRITES_PLAN §2.8, U6).

Offline: an in-memory SQLite coach_madden_profiles behind the shared psycopg-like FakeCursor, plus a temporary DDS
folder. No PostgreSQL connection is opened and no real headshot is touched.
"""
import csv
import pathlib
import sqlite3

import pytest

from tests.test_inbox_store import FakeCursor
from tools import coach_portrait_pack as pack

SEASON = 2027

DDL = """
CREATE TABLE coach_madden_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_kind TEXT, person_id INT, first_name TEXT, last_name TEXT,
    portrait_id INT DEFAULT 0, portrait_status TEXT DEFAULT 'NONE'
);
"""


def _db(*rows, status="GENERATED"):
    """A profile table with one row per (person_kind, person_id)."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(DDL)
    for kind, ident in rows:
        conn.execute("INSERT INTO coach_madden_profiles (person_kind, person_id, first_name, last_name, "
                     "portrait_id, portrait_status) VALUES (?, ?, 'A', 'B', 0, ?)", (kind, ident, status))
    return FakeCursor(conn)


def _profile(cur, kind, ident):
    cur.execute("SELECT portrait_id, portrait_status FROM coach_madden_profiles "
                "WHERE person_kind = %s AND person_id = %s", (kind, ident))
    return cur.fetchone()


def _dds(root, *keys):
    root.mkdir(parents=True, exist_ok=True)
    for key in keys:
        (root / f"{key}.dds").write_bytes(b"DDS " + key.encode())


def _quiet(*_a, **_k):
    pass


def test_ids_come_from_the_range_and_never_repeat(tmp_path):
    cur = _db(("COACH", 1), ("COACH", 2), ("COORD", 3))
    report = pack.run_pack(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)
    ids = [int(r["portrait_id"]) for r in report["assignments"]]
    assert ids == [pack.COACH_PORTRAIT_RANGE[0], pack.COACH_PORTRAIT_RANGE[0] + 1,
                   pack.COACH_PORTRAIT_RANGE[0] + 2]
    assert all(pack.COACH_PORTRAIT_RANGE[0] <= i <= pack.COACH_PORTRAIT_RANGE[1] for i in ids)
    assert [r["coach_key"] for r in report["assignments"]] == ["coach-1", "coach-2", "coord-3"]
    assert [_profile(cur, kind, ident) for kind, ident in
            (("COACH", 1), ("COACH", 2), ("COORD", 3))] == [(ids[0], "PACKED"), (ids[1], "PACKED"),
                                                           (ids[2], "PACKED")]

    # A rerun assigns nothing new, and a coach generated later gets the next free id, not a used one.
    assert pack.run_pack(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)["assigned"] == 0
    cur.execute("INSERT INTO coach_madden_profiles (person_kind, person_id, first_name, last_name, portrait_id, "
                "portrait_status) VALUES ('COACH', 9, 'C', 'D', 0, 'GENERATED')")
    later = pack.run_pack(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)
    assert [int(r["portrait_id"]) for r in later["assignments"]] == [ids[-1] + 1]
    assert len({ids[0], ids[1], ids[2], int(later["assignments"][0]["portrait_id"])}) == 4


def test_the_manifest_columns_and_the_pack_folder(tmp_path):
    cur = _db(("COACH", 1), ("COORD", 2))
    _dds(tmp_path, "coach-1", "coord-2")
    report = pack.run_pack(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)

    manifest = tmp_path / f"pack_{SEASON}" / pack.MANIFEST_NAME
    assert manifest == pathlib.Path(report["manifest"]) and manifest.exists()
    with open(manifest, newline="") as fh:
        rows = list(csv.DictReader(fh))
        fh.seek(0)
        assert fh.readline().strip().split(",") == list(pack.MANIFEST_COLUMNS)
    assert [(r["coach_key"], r["dds_file"]) for r in rows] == [
        ("coach-1", f"{pack.COACH_PORTRAIT_RANGE[0]}.dds"),
        ("coord-2", f"{pack.COACH_PORTRAIT_RANGE[0] + 1}.dds")]
    # Each coach's DDS is copied in under its portrait id, ready for /build-mod.
    assert (tmp_path / f"pack_{SEASON}" / f"{pack.COACH_PORTRAIT_RANGE[0]}.dds").read_bytes() == b"DDS coach-1"
    assert (tmp_path / f"pack_{SEASON}" / f"{pack.COACH_PORTRAIT_RANGE[0] + 1}.dds").read_bytes() == b"DDS coord-2"


def test_the_status_moves_generated_to_packed_and_a_rerun_assigns_nothing(tmp_path):
    cur = _db(("COACH", 1))
    first = pack.run_pack(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)
    assert first["assigned"] == 1 and _profile(cur, "COACH", 1)[1] == "PACKED"
    second = pack.run_pack(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)
    assert second["assigned"] == 0 and second["assignments"] == []
    assert _profile(cur, "COACH", 1) == (pack.COACH_PORTRAIT_RANGE[0], "PACKED")


def test_a_dry_run_prints_and_writes_nothing(tmp_path):
    cur = _db(("COACH", 1))
    _dds(tmp_path, "coach-1")
    report = pack.run_pack(cur, SEASON, dds_root=tmp_path, dry_run=True, log_fn=_quiet)
    assert report["assigned"] == 1 and report["dry_run"] is True
    assert not (tmp_path / f"pack_{SEASON}").exists()
    assert _profile(cur, "COACH", 1) == (0, "GENERATED")


def test_ids_already_in_an_earlier_manifest_are_never_reused(tmp_path):
    # The database lost the allocation (a failed run), but the pack folder still names it: skip that id.
    pack_dir = tmp_path / f"pack_{SEASON}"
    pack_dir.mkdir(parents=True)
    (pack_dir / pack.MANIFEST_NAME).write_text("portrait_id,dds_file,coach_key\n5324,5324.dds,coach-7\n")
    cur = _db(("COACH", 1))
    report = pack.run_pack(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)
    assert [int(r["portrait_id"]) for r in report["assignments"]] == [pack.COACH_PORTRAIT_RANGE[0] + 1]


def test_mark_installed_flips_the_packs_coaches_only(tmp_path):
    cur = _db(("COACH", 1), ("COACH", 2))
    packed = pack.run_pack(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)
    assert packed["assigned"] == 2
    # A coach packed for another season is not in this pack's manifest and stays PACKED.
    cur.execute("INSERT INTO coach_madden_profiles (person_kind, person_id, first_name, last_name, portrait_id, "
                "portrait_status) VALUES ('COACH', 3, 'E', 'F', 6000, 'PACKED')")

    report = pack.mark_installed(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)
    assert report["installed"] == 2
    assert _profile(cur, "COACH", 1)[1] == "INSTALLED" and _profile(cur, "COACH", 2)[1] == "INSTALLED"
    assert _profile(cur, "COACH", 3)[1] == "PACKED"
    # Idempotent: a second confirmation changes nothing.
    assert pack.mark_installed(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)["installed"] == 0


def test_a_dry_run_mark_installed_changes_nothing(tmp_path):
    cur = _db(("COACH", 1))
    pack.run_pack(cur, SEASON, dds_root=tmp_path, log_fn=_quiet)
    pack.mark_installed(cur, SEASON, dds_root=tmp_path, dry_run=True, log_fn=_quiet)
    assert _profile(cur, "COACH", 1)[1] == "PACKED"


def test_the_range_running_out_is_reported_not_guessed():
    rows = [{"person_kind": "COACH", "person_id": i} for i in range(3)]
    with pytest.raises(pack.PortraitRangeExhausted):
        pack.plan_assignments(rows, used=set(), id_range=(5324, 5325))
