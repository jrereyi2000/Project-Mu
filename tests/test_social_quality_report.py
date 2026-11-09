"""Unit tests for tools/social_quality_report.py (AGENT_WIRE_VOICE_PLAN §2.6, §2.7 rule 6). No database."""
import pytest

from tools import social_quality_report as q


def post(text, kind="REACTION", persona="DIE_HARD_FAN", agent="A", season=2036, week=1):
    return {"content": text, "post_kind": kind, "persona": persona, "agent_id": agent,
            "season": season, "week": week}


class TestTwoWordOpener:
    def test_first_two_words_lowercased(self):
        assert q.two_word_opener("The Quick brown fox") == "the quick"

    def test_digits_collapse_so_scores_share_an_opener(self):
        assert q.two_word_opener("Up 14 with five left") == "up #"
        assert q.two_word_opener("Up 20 with five left") == "up #"
        assert q.two_word_opener("18 seconds and a timeout") == "# seconds"

    def test_punctuation_and_apostrophes_are_dropped(self):
        assert q.two_word_opener("Can't stop, won't stop!") == "cant stop"
        assert q.two_word_opener("...leading by three") == "leading by"

    def test_empty_and_short_posts(self):
        assert q.two_word_opener("") == ""
        assert q.two_word_opener("   ...   ") == ""
        assert q.two_word_opener("Bills") == "bills"


class TestShapeMetrics:
    def test_word_and_number_counts(self):
        assert q.word_count("Two touchdowns and a pick") == 5
        assert q.number_count("27-24, 4 TDs, 3rd down") == 4
        assert q.number_count("no numbers here") == 0

    def test_closing_punctuation(self):
        assert q.has_closing_punctuation("We lost.")
        assert q.has_closing_punctuation("WHAT A KICK!")
        assert q.has_closing_punctuation("really?")
        assert not q.has_closing_punctuation("we lost")
        assert not q.has_closing_punctuation('he said "go"')

    def test_forward_looking_kind_and_words(self):
        assert q.is_forward_looking({"post_kind": "PICKS", "content": "..."})
        assert q.is_forward_looking({"post_kind": "SEASON_CALL", "content": "..."})
        assert q.is_forward_looking({"post_kind": "PREVIEW", "content": "..."})
        assert q.is_forward_looking({"post_kind": "HOT_TAKE", "content": "..."})
        assert q.is_forward_looking({"post_kind": "REACTION", "content": "Next week will be different"})
        assert q.is_forward_looking({"post_kind": "REACTION", "content": "Calling it now, we win out"})
        assert not q.is_forward_looking({"post_kind": "REACTION", "content": "We lost again"})


class TestTrigram:
    def test_overlap(self):
        assert q.trigram_overlap("the quick brown fox", "the quick brown fox") == 1.0
        assert q.trigram_overlap("the quick brown fox", "we lost the game") < 0.2
        assert q.trigram_overlap("", "anything") == 0.0

    def test_mean_pairwise_overlap_is_within_a_week(self):
        same = [post("the quick brown fox", week=1, agent="A"), post("the quick brown fox", week=1, agent="B")]
        assert q.mean_pairwise_overlap(same) == 1.0
        across = [post("the quick brown fox", week=1), post("the quick brown fox", week=2)]
        assert q.mean_pairwise_overlap(across) is None


class TestMetrics:
    def make_posts(self):
        return [
            post("We lost.", kind="REACTION", agent="A"),
            post("We lost again", kind="REACTION", agent="A"),
            post("Next week will be different, 3 picks, 2 scores, 1 upset", kind="HOT_TAKE", agent="B", persona="PUNDIT"),
            post("2 numbers 4 life", kind="COMPARISON", agent="C", persona="PUNDIT"),
        ]

    def test_overall_metrics(self):
        m = q.metrics(self.make_posts())
        assert m["posts"] == 4
        assert m["distinct_openers"] == 3          # "we lost" twice, then "next week", "2 numbers"
        assert m["opener_ratio"] == pytest.approx(0.75)
        assert m["forward_looking"] == 1           # the HOT_TAKE kind only
        assert m["forward_share"] == pytest.approx(0.25)
        assert m["posts_3plus_numbers"] == 1       # the HOT_TAKE (3, 2, 1)
        assert m["share_3plus_numbers"] == pytest.approx(0.25)
        assert m["numbers_per_post_mean"] == pytest.approx(1.25)
        assert m["words_per_post_median"] == pytest.approx(3.5)
        assert m["no_closing_punctuation_share"] == pytest.approx(0.75)

    def test_kind_mix(self):
        mix = q.kind_mix(self.make_posts())
        assert mix["REACTION"]["posts"] == 2
        assert mix["REACTION"]["share"] == pytest.approx(0.5)
        assert mix["HOT_TAKE"]["posts"] == 1

    def test_account_counts(self):
        acc = q.account_counts(self.make_posts())
        assert acc["distinct_accounts"] == 3
        assert acc["posts_per_account_mean"] == pytest.approx(4 / 3, abs=0.001)
        assert acc["top"][0] == ["A", 2]

    def test_median_words_per_archetype(self):
        arch = q.archetype_metrics(self.make_posts())
        assert arch["DIE_HARD_FAN"]["posts"] == 2
        assert arch["DIE_HARD_FAN"]["words_per_post_median"] == pytest.approx(2.5)
        assert arch["PUNDIT"]["posts"] == 2


class TestReport:
    def test_parse_weeks(self):
        assert q.parse_weeks(None) is None
        assert q.parse_weeks("3-9") == (3, 9)
        assert q.parse_weeks("7") == (7, 7)
        with pytest.raises(ValueError):
            q.parse_weeks("9-3")
        with pytest.raises(ValueError):
            q.parse_weeks("nope-x")

    def test_baseline_check_wiring(self, monkeypatch):
        monkeypatch.setattr(q, "S5_BASELINE", {"REACTION": (2, 2)})
        archive = [post("same opener here", agent="A"), post("another opener there", agent="B")]
        report = q.build_report([], archive_posts=archive, db="x", season=2036)
        check = report["baseline"]["checks"]["REACTION"]
        assert check["posts"] == 2 and check["openers"] == 2
        assert check["match"] is True

    def test_print_report_shows_the_baseline(self, monkeypatch, capsys):
        monkeypatch.setattr(q, "S5_BASELINE", {"REACTION": (2, 2)})
        archive = [post("same opener here", agent="A"), post("another opener there", agent="B")]
        q.print_report(q.build_report([], archive_posts=archive, db="x", season=2036))
        out = capsys.readouterr().out
        assert "REACTION" in out
        assert "2 posts / 2 distinct two-word openers" in out
        assert "PASS" in out

    def test_empty_report_does_not_crash(self):
        report = q.build_report([], archive_posts=[], db="x", season=2036)
        assert report["posts"] == 0
        assert report["overall"]["opener_ratio"] == 0.0
        assert report["overall"]["mean_trigram_overlap"] is None


class TestMissingSchema:
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            raise q.SchemaMissing('relation "news_feed" does not exist')

    class FakeConn:
        def cursor(self):
            return TestMissingSchema.FakeCursor()

    def test_load_posts_raises_schema_missing(self):
        """The caller can print a readable message instead of a traceback; the report still renders."""
        with pytest.raises(q.SchemaMissing):
            q.load_posts(self.FakeConn(), season=2036)
        report = q.build_report([], archive_posts=None, db="x", season=2036)
        report["missing"] = 'relation "news_feed" does not exist'
        q.print_report(report)

    def test_schema_errors_are_classified(self):
        class UndefinedTable(Exception):
            pass

        assert q._is_schema_error(UndefinedTable('relation "news_feed" does not exist'))
        assert not q._is_schema_error(ValueError("bad dsn"))
