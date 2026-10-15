"""Unit tests for the Apple Vision OCR prospect extractor."""

import os
import pytest
from unittest.mock import patch

from pipeline.extract_prospects_ocr import (
    clean_unicode,
    find_prospect_frames,
    order_by_screen_pick,
    parse_pick_label,
    parse_prospect_frame,
    extract_prospects_from_frames,
    extract_prospects_ocr,
    HAS_APPLE_VISION,
    OUT_COLS
)
from pipeline.ingest_prospects import load_prospects_from_csv


def test_clean_unicode():
    assert clean_unicode("BƯCHANAN") == "BUCHANAN"
    assert clean_unicode("  DAVE  ") == "DAVE"
    assert clean_unicode("MÖLLER") == "MOLLER"


def test_parse_prospect_frame_mock():
    mock_tokens = [
        ("DAVE", (970, 180, 1220, 240)),
        ("VOGEL", (960, 230, 1250, 320)),
        ("HT/WT: 6'5\", 280 LBS", (960, 345, 1260, 400)),
        ("COLLEGE: GEORGIA", (960, 403, 1200, 440)),
        ("EXP: ROOKIE", (960, 455, 1130, 495)),
        ("LEDG #72 | OVR75", (120, 500, 380, 540)),
        ("Round 1, Pick 1", (130, 790, 300, 840)),
        ("D.Vogel", (750, 790, 850, 840)),
        ("LEDG", (965, 790, 1040, 830)),
        ("22", (1185, 790, 1230, 830)),
        ("75", (1375, 790, 1420, 830)),
    ]

    with patch("pipeline.extract_prospects_ocr.ocr_tokens", return_value=mock_tokens), \
         patch("PIL.Image.open") as mock_open:
        mock_open.return_value.size = (3840, 2160)
        rec = parse_prospect_frame("fake_path.png", 1)

    assert rec["PROJECTED_PICK"] == "1"
    assert rec["PROJECTED_ROUND"] == "1"
    assert rec["ROUND_PICK"] == "1"
    assert rec["FULL_NAME"] == "DAVE VOGEL"
    assert rec["POSITION"] == "LEDG"
    assert rec["COLLEGE"] == "GEORGIA"
    assert rec["HEIGHT"] == "6'5\""
    assert rec["WEIGHT"] == "280"
    assert rec["AGE"] == "22"
    assert rec["OVR"] == "75"


def test_font_fixes_mock():
    mock_tokens = [
        ("CONNOR", (970, 180, 1220, 240)),
        ("REYNOLDS", (960, 230, 1250, 320)),
        ("HT/WT: 6'T\", 827 LBS", (960, 345, 1260, 400)),  # 6'T" -> 6'1", 827 -> 327
        ("COLLEGE: TEXASA&M", (960, 403, 1200, 440)),
        ("LT #52 | 0VR77", (120, 500, 380, 540)),          # 0VR -> OVR
        ("C.Reynolds", (750, 790, 850, 840)),
        ("23", (1185, 790, 1230, 830)),
    ]

    with patch("pipeline.extract_prospects_ocr.ocr_tokens", return_value=mock_tokens), \
         patch("PIL.Image.open") as mock_open:
        mock_open.return_value.size = (3840, 2160)
        rec = parse_prospect_frame("fake_path.png", 3)

    assert rec["HEIGHT"] == "6'1\""
    assert rec["WEIGHT"] == "327"
    assert rec["COLLEGE"] == "TEXAS A&M"
    assert rec["OVR"] == "77"
    assert rec["POSITION"] == "LT"
    assert rec["AGE"] == "23"


def test_digit_letter_fixups_mock():
    mock_tokens = [
        ("JOSH", (970, 180, 1220, 240)),
        ("WRIGHT", (960, 230, 1250, 320)),
        ("HT/WT: E'11\".", (962, 355, 1142, 390)),   # E'11" -> 5'11"
        (", 3U2 LBS", (1125, 360, 1256, 400)),       # 3U2 -> 302
    ]
    with patch("pipeline.extract_prospects_ocr.ocr_tokens", return_value=mock_tokens), \
         patch("PIL.Image.open") as mock_open:
        mock_open.return_value.size = (3024, 1888)
        rec = parse_prospect_frame("fake_path.png", 1)
    assert rec["HEIGHT"] == "5'11\""
    assert rec["WEIGHT"] == "302"


def test_join_name_lines():
    from pipeline.extract_prospects_ocr import join_name_lines
    # Wide-tracked first name split in two; tall last-name box starts just below it
    lines, garbled = join_name_lines([(210, 975, 1155, "MAL"), (210, 1177, 1409, "ACHI"),
                                      (290, 957, 1476, "SHERWOOD")])
    assert [t for _, t in lines] == ["MALACHI", "SHERWOOD"] and not garbled
    lines, garbled = join_name_lines([(212, 984, 1063, "RC"), (210, 1050, 1357, "ONALD"),
                                      (290, 956, 1310, "JORDAN")])
    assert garbled


def test_parse_pick_label():
    assert parse_pick_label("Round 1, Pick 1") == ("1", "1")
    assert parse_pick_label("Round 2. Pick 14") == ("2", "14")
    assert parse_pick_label("Round 7, Pick 3l") == ("7", "31")
    assert parse_pick_label("Undrafted") == ("UDFA", "")
    assert parse_pick_label("") == ("", "")


def test_highlighted_row_prefers_initial_and_full_last_name():
    # Card is BOBBY WHITE; T.Whitehead sits above B.White and also contains WHITE.
    mock_tokens = [
        ("BOBBY", (970, 180, 1220, 240)),
        ("WHITE", (960, 230, 1250, 320)),
        ("Round 2, Pick 6", (131, 938, 300, 980)),
        ("T.Whitehead", (698, 939, 850, 980)),
        ("21", (1195, 939, 1230, 980)),
        ("Round 2, Pick 9", (131, 1166, 300, 1208)),
        ("B.White", (738, 1167, 850, 1208)),
        ("23", (1182, 1167, 1230, 1208)),
    ]
    with patch("pipeline.extract_prospects_ocr.ocr_tokens", return_value=mock_tokens), \
         patch("PIL.Image.open") as mock_open:
        mock_open.return_value.size = (3024, 1888)
        rec = parse_prospect_frame("fake_path.png", 1)
    assert rec["AGE"] == "23"
    assert (rec["PROJECTED_ROUND"], rec["ROUND_PICK"]) == ("2", "9")


def test_frames_sort_by_number(tmp_path):
    d = tmp_path / "prospects"
    d.mkdir()
    for n in ["000", "001", "100", "101", "1000", "2000"]:
        (d / f"prospect_{n}.png").write_bytes(b"")
    names = [os.path.basename(p) for p in find_prospect_frames(str(tmp_path))]
    assert names == ["prospect_000.png", "prospect_001.png", "prospect_100.png",
                     "prospect_101.png", "prospect_1000.png", "prospect_2000.png"]


def _rec(name, rnd, pk, pos="CB"):
    return {"PROJECTED_PICK": "", "PROJECTED_ROUND": rnd, "ROUND_PICK": pk, "FULL_NAME": name,
            "POSITION": pos, "COLLEGE": "X", "HEIGHT": "6'0\"", "WEIGHT": "200", "AGE": "22",
            "OVR": str(60 + ord(name[0]) - ord("A")), "ARCHETYPE": "", "NOTES": ""}


def test_order_by_screen_pick():
    recs = [
        _rec("A", "1", "3"),
        _rec("B", "1", "1"),
        _rec("C", "", ""),            # unreadable PICK cell: stays after B
        _rec("D", "2", "1"),
        _rec("E", "UDFA", ""),
        _rec("E", "UDFA", ""),        # resumed run repeats its first row
        _rec("F", "UDFA", ""),
    ]
    out = order_by_screen_pick(recs)
    assert [(r["FULL_NAME"], r["PROJECTED_PICK"]) for r in out] == [
        ("B", "1"), ("C", "2"), ("A", "3"), ("D", "4"), ("E", "5"), ("F", "6")]


@pytest.mark.skipif(not HAS_APPLE_VISION, reason="Requires Apple Vision framework on macOS")
def test_real_capture_extraction_if_present(tmp_path):
    test_dir = "captures/prospects" if os.path.exists("captures/prospects/prospect_000.png") else "captures/test_prospects"
    if not (os.path.exists(os.path.join(test_dir, "prospect_000.png")) or
            os.path.exists(os.path.join(test_dir, "prospects", "prospect_000.png"))):
        pytest.skip("Test prospect frames not found")

    csv_path = extract_prospects_ocr(test_dir)
    assert csv_path is not None
    assert os.path.exists(csv_path)

    # Ingest should succeed without error
    prospects = load_prospects_from_csv(csv_path)
    assert len(prospects) >= 32
    assert prospects[0].full_name == "DAVE VOGEL"
    assert prospects[0].overall_rating == 75
    assert prospects[4].full_name == "C.J. BUCHANAN"
    assert prospects[4].age == 23
