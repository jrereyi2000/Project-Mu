import struct

import pytest

from pipeline import ingame_resume as ir


def tag(name):
    v = 0
    for c in name.ljust(4):
        v = (v << 6) | (ord(c) - 0x20)
    return v.to_bytes(3, "big")


def varint(n):
    neg, n = n < 0, abs(n)
    out = [(n & 0x3F) | (0x40 if neg else 0)]
    n >>= 6
    while n:
        out[-1] |= 0x80
        out.append(n & 0x7F)
        n >>= 7
    return bytes(out)


def f_int(name, n):
    return tag(name) + b"\x00" + varint(n)


def f_str(name, s):
    raw = s.encode() + b"\x00"
    return tag(name) + b"\x01" + varint(len(raw)) + raw


def f_struct(name, *fields):
    return tag(name) + b"\x03" + b"".join(fields) + b"\x00"


def f_float(name, x):
    return tag(name) + b"\x0a" + struct.pack(">f", x)


def f_map(name, entries):
    """Map of int keys to structs."""
    body = b"".join(varint(k) + b"".join(v) + b"\x00" for k, v in entries.items())
    return tag(name) + b"\x05\x00\x03" + varint(len(entries)) + body


def resume_file(*fields):
    head = bytearray(ir.PAYLOAD_OFFSET)
    head[:8] = ir.MAGIC
    struct.pack_into("<6H", head, 0x16, 2026, 10, 30, 1, 35, 36)
    head[0x22:0x22 + 21] = b"Madden-27-RL4-9244223"
    head[0x42:0x42 + 15] = b"Ultimate Career"
    return bytes(head) + b"".join(fields) + b"\x00" * 16


def sample():
    return resume_file(
        f_struct("PROG",
                 f_int("QRTR", 1), f_float("GMCL", 281.3), f_int("DOWN", 2),
                 f_float("BLSY", -19.45), f_float("FDLN", -12.45),
                 f_struct("TGPI", f_int("PTSC", 3), f_int("TIME", 3)),
                 f_struct("TJFH", f_int("PTSC", 6), f_int("TIME", 2))),
        f_struct("AWAY", f_int("TEID", 21),
                 f_struct("ROST", f_map("PINF", {21574: [f_str("FRST", "Chuba"), f_str("LAST", "Hubbard")]}))),
        f_struct("HOME", f_int("TEID", 14)),
        f_map("PLRS", {5: [f_int("PLNM", 5), f_int("QRTR", 1), f_int("CSEC", 284), f_int("DOWN", 1),
                           f_int("YDFS", 10), f_int("YARD", 3), f_int("RNID", 21574), f_int("RCID", ir.NO_ID)]}),
        f_map("EVTS", {0: [f_int("EVTP", 1), f_int("PLID", 21574), f_int("PLNM", 4), f_int("YARD", -11)]}),
        f_struct("ASTT", f_map("PLST", {21574: [f_int("PS01", 21574), f_int("PS11", 1), f_int("PS12", 3),
                                                 f_int("PS99", 7), f_int("PS84", 1)]})),
    )


def test_tag_names_and_varints_round_trip():
    assert ir.tag_name(*tag("QRTR")) == "QRTR"
    for n in (0, 3, 63, 64, 1325, 21574, ir.NO_ID, -30, -2144397148):
        assert ir._Reader(varint(n), 0).varint() == n


def test_read_header_and_tree(tmp_path):
    path = tmp_path / "INGAME-RESUMEDATA"
    path.write_bytes(sample())
    header, tree = ir.read_file(path)
    assert header == {"written": "2026-10-30T01:35:36", "build": "Madden-27-RL4-9244223", "mode": "Ultimate Career"}
    assert tree["PROG"]["TJFH"]["PTSC"] == 6
    assert tree["PLRS"][5]["RNID"] == 21574


def test_summary_labels_known_codes_and_keeps_unknown():
    data = sample()
    s = ir.summarize(ir.read_header(data), ir.read_tree(data))
    assert s["teams"]["away"] == {"team_id": 21, "score": 6, "timeouts": 2, "challenges": None}
    assert s["teams"]["home"]["score"] == 3
    assert s["state"] == {"quarter": 1, "clock": "4:41", "down": 2, "to_go": 7.0, "just_scored": False}
    play = s["plays"][0]
    assert play["runner"] == "Chuba Hubbard" and play["yards"] == 3 and play["clock"] == "4:44"
    assert "receiver" not in play  # empty id slot
    assert s["events"] == [{"play": 4, "type": "interception", "player": "Chuba Hubbard", "yards": -11}]
    line = s["players"]["away"][0]
    assert line["rush_att"] == 1 and line["rush_yds"] == 3 and line["PS99"] == 7 and "PS01" not in line
    text = ir.format_summary(s)
    assert "Away 6  Home 3   Q1 4:41  2 & 7" in text


def test_rejects_other_files():
    with pytest.raises(ir.ResumeFormatError):
        ir.read_header(b"not a save" + b"\x00" * 400)
