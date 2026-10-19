import pytest

from pipeline.money import parse_money


@pytest.mark.parametrize("s, want", [
    ("$16.8M", 16_800_000),
    ("$200K", 200_000),
    ("$0", 0),
    ("-$29.5M", -29_500_000),
    ("$-29.5M", -29_500_000),
    ("($29.5M)", -29_500_000),
    ("−$1.2M", -1_200_000),
    ("–$1.2M", -1_200_000),
    ("$3.93M", 3_930_000),
    ("$1,250,000", 1_250_000),
    ("$1.25M", 1_250_000),
    ("$0.7M", 700_000),
    ("$1.1B", 1_100_000_000),
    (" $ 16.8 M ", 16_800_000),
    ("$16.8m", 16_800_000),
])
def test_parses(s, want):
    assert parse_money(s) == want


@pytest.mark.parametrize("s", [None, "", " ", "-", "--", "N/A", "—"])
def test_blank_is_none(s):
    assert parse_money(s) is None


@pytest.mark.parametrize("s", ["S16.8M", "16.8M", "$16.8X", "$", "$1.2.3M", "abc", "-$-1M", "$0.0000001K"])
def test_junk_raises(s):
    with pytest.raises(ValueError):
        parse_money(s)


def test_no_float_drift():
    # 0.1 + 0.2 style drift would give 699999 or 700000.0000001
    assert parse_money("$0.7M") == 700_000
    assert parse_money("$2.3M") == 2_300_000
    assert parse_money("$33.3M") == 33_300_000
