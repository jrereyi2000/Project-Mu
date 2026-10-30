import os

from pipeline.gpu_queue.process_check import mock_process_running
from tools import save_write_logger as swl


def test_scan_and_diff(tmp_path):
    (tmp_path / "CAREER-A").write_bytes(b"one")
    (tmp_path / "CAREER-B").write_bytes(b"two")
    (tmp_path / "sub").mkdir()
    before = swl.scan(str(tmp_path))
    assert set(before) == {"CAREER-A", "CAREER-B"}

    (tmp_path / "CAREER-A").write_bytes(b"one!")
    os.remove(tmp_path / "CAREER-B")
    (tmp_path / "CAREER-C.tmp").write_bytes(b"x")
    events = swl.diff(before, swl.scan(str(tmp_path)))
    assert [(k, n) for k, n, _, _ in events] == [
        ("changed", "CAREER-A"), ("removed", "CAREER-B"), ("new", "CAREER-C.tmp")]


def test_describe_reports_same_content_on_touch():
    line = swl.describe("changed", "CAREER-A", (1_000_000_000, 10), (2_000_000_000, 10), "aa", "aa")
    assert "content SAME" in line and "size=10" in line
    line = swl.describe("changed", "CAREER-A", (1_000_000_000, 10), (2_000_000_000, 12), "aa", "bb")
    assert "size 10 -> 12" in line and "content changed" in line


def test_summarize_splits_by_madden_session():
    lines = swl.summarize(
        [(5, "changed", "CAREER-A"), (15, "changed", "CAREER-A"), (25, "new", "CAREER-B")],
        [(10, 20), (24, None)])
    text = "\n".join(lines)
    assert "Madden sessions seen: 2" in text
    assert "File events while Madden was running: 2" in text
    assert "File events while Madden was not running: 1" in text


def test_run_logs_change_and_snapshot(tmp_path, monkeypatch):
    saves, snaps = tmp_path / "saves", tmp_path / "snaps"
    saves.mkdir()
    save = saves / "CAREER-A"
    save.write_bytes(b"before")
    os.utime(save, (1, 1))

    lines = []
    ticks = iter(range(3))

    def fake_sleep(_):
        i = next(ticks, None)
        if i == 0:
            save.write_bytes(b"after!")
            os.utime(save, (2, 2))  # old mtime, so it counts as settled at once
        elif i is None:
            raise KeyboardInterrupt

    monkeypatch.setattr(swl.time, "sleep", fake_sleep)
    with mock_process_running(True):
        swl.run(str(saves), "*", 0, 0, str(snaps), 3, lines.append)

    text = "\n".join(lines)
    assert "MADDEN   running" in text
    assert "CHANGED  CAREER-A" in text and "content changed" in text
    copies = list(snaps.iterdir())
    assert len(copies) == 1 and copies[0].read_bytes() == b"after!"
    assert "File events while Madden was running: 1" in text
