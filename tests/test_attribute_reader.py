"""pipeline/attribute_reader.py: digit templates on labelled crops of the wide roster list (tests/fixtures/attributes),
chip and header placement, sweep alignment and the OVR check. No screen, no database."""
import numpy as np
import pytest

from pipeline import attribute_reader as ar


@pytest.fixture(scope="module")
def templates():
    return ar.load_templates()


@pytest.fixture(scope="module")
def samples():
    return ar.fixture_samples()


def test_columns_are_the_mapped_list():
    assert len(ar.RATINGS) == 54
    assert ar.HEADERS[ar.FIRST_RATING] == "SPD" and ar.HEADERS[-1] == "INJ"
    assert ar.HEADERS.index("PUR") == 52 and ar.LAST == 62     # 11 columns end at INJ


def test_templates_read_every_training_cell(templates, samples):
    T, shape = templates
    train, _ = samples
    wrong = [(col, r, l, ar.read_number(m, s, T, shape)[0]) for m, s, l, lit, col, r in train
             if str(ar.read_number(m, s, T, shape)[0]) != l]
    assert not wrong


def test_templates_read_held_out_cells(templates, samples):
    """SPD..STR on another view were never trained on; the highlighted (bold) row is included."""
    T, shape = templates
    _, test = samples
    plain = [t for t in test if not t[3]]
    assert len(plain) >= 60
    for m, s, l, lit, col, r in test:
        assert str(ar.read_number(m, s, T, shape)[0]) == l, (col, r, lit)


def test_touching_digits_are_split(templates):
    """Two digits joined by a column of ink still read as two (bold 83, plain 74 seen on the Panthers)."""
    T, shape = templates
    size = (3024, 1888)
    seven, four = (T["7"] > 0.5), (T["4"] > 0.5)
    mask = np.zeros((60, 140), bool)
    h, w = seven.shape
    mask[14:14 + h, 50:50 + w] = seven
    mask[14:14 + h, 50 + w:50 + 2 * w] = four                 # no blank column between them
    assert ar.read_number(mask, size, T, shape)[0] == 74


def test_chip_ignores_spacing():
    assert ar.column_of_chip("STIFF ARM") == ar.INDEX["SFA"]
    assert ar.column_of_chip("Stiffarm") == ar.INDEX["SFA"]
    assert ar.column_of_chip("PLAYER NAME") == 0
    assert ar.column_of_chip("CONVERSATION") == 1
    assert ar.column_of_chip("SOMETHING ELSE") is None


def test_column_xs_from_headers():
    size = (3024, 1888)
    W = size[0]
    hits = [(ar.INDEX[h], 206 + i * 189.4) for i, h in enumerate(["PUR", "MCV", "ZCV", "PRS", "RET", "KPW", "KAC"])]
    xs = ar.column_xs(hits, size)
    assert set(xs) == set(range(ar.INDEX["PUR"], ar.LAST + 1))
    assert abs(xs[ar.INDEX["INJ"]] - (206 + 10 * 189.4)) < 3
    assert ar.column_xs(hits[:3], size) is None                # too few headers to place the grid
    bad = hits[:-1] + [(ar.INDEX["KAC"], 0.5 * W)]
    assert ar.column_xs(bad, size) is None                     # headers off the grid


def _page(rows, highlight=None, names=None):
    """A sweep page with rating columns SPD.. filled from `rows` (list of 14 tuples)."""
    cells = {ar.FIRST_RATING + k: [r[k] for r in rows] for k in range(len(rows[0]))}
    return {"cells": cells, "names": names, "positions": None, "highlight": highlight, "flags": []}


def _players(n):
    rng = np.random.default_rng(7)
    return [tuple(int(x) for x in rng.integers(20, 99, 6)) for _ in range(n)]


def test_align_shift_finds_the_move():
    pl = _players(30)
    a, b = _page(pl[0:14]), _page(pl[7:21])
    assert ar.align_shift(a, b)[:2] == (7, 7)
    c = _page(pl[11:25])                                       # a dropped press: 4 rows, not 7
    assert ar.align_shift(b, c)[0] == 4
    assert ar.align_shift(c, c)[0] == 0


def test_team_players_prefers_plain_reads():
    pl = _players(21)
    first = _page(pl[0:14], highlight=7)
    second_rows = list(pl[7:21])
    first["cells"][ar.FIRST_RATING][7] = 11                    # a bold misread on the highlighted row
    second = _page(second_rows, highlight=7)
    players, flags = ar.team_players([first, second])
    assert len(players) == 21 and not flags
    assert players[7]["values"][ar.FIRST_RATING] == pl[7][0]   # taken from sweep 2, where row 8 isn't highlighted
    only_lit = players[14]                                     # highlighted in sweep 2, not seen elsewhere
    assert any("only seen highlighted" in f for f in only_lit["flags"])


def test_frame_array_puts_the_crop_back(tmp_path):
    from PIL import Image
    crop = Image.fromarray(np.full((10, 20), 200, np.uint8))
    p = tmp_path / "v.png"
    crop.save(p)
    arr, size = ar.frame_array(str(p), {"window": [100, 50], "crop": [5, 7, 25, 17]})
    assert size == (100, 50) and arr.shape == (50, 100)
    assert arr[7, 5] == 200 and arr[6, 5] == 0 and arr[16, 24] == 200


def test_load_meta(tmp_path):
    t = tmp_path / "v.txt"
    t.write_text("screen: ROSTER_LIST\nwindow: 3024 1888\ncrop: 0 188 2222 1604\ncursor: 41\nPUR\t10,20,30,40\n")
    meta = ar.load_meta(str(t))
    assert meta == {"screen": "ROSTER_LIST", "window": [3024, 1888], "crop": [0, 188, 2222, 1604], "cursor": 41}
    assert ar.load_items(str(t)) == [("PUR", (10, 20, 30, 40))]


def test_madden_position():
    assert ar.madden_position("WILL") == "LOLB"
    assert ar.madden_position("LEDG") == "LE"
    assert ar.madden_position("MIKE") == "MLB"
    assert ar.madden_position("QB") == "QB"


def test_check_ovr_caps_at_99():
    from engines import ovr_weights as ow
    doc = ow.load_weights()
    elite = {ar.INDEX[c[0]]: 99 for c in ar.RATINGS}
    elite[ar.INDEX["OVR"]] = 99
    pred, chk = ar.check_ovr(doc, "WR", elite)
    assert pred >= 99 and chk == "ok"
    partial = {ar.INDEX["OVR"]: 80, ar.INDEX["SPD"]: 90}
    assert ar.check_ovr(doc, "WR", partial) == (None, "n/a")
