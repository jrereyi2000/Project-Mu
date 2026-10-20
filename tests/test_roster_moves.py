"""Pure tests for newsroom/roster_moves.py (step 5 of the team management plan). No database.

Run: python3 -m unittest tests.test_roster_moves
"""
import re
import unittest

from newsroom import roster_moves as rm


def row(**kw):
    base = {"season": 2036, "week": 4, "capture_week": 4, "type": "SIGNED", "position": "WR",
            "player_name": "DEVONTA TAYLOR", "full_name": None, "team": "Jaguars", "contract_years": 1,
            "contract_value": 3_930_000}
    base.update(kw)
    return base


class MoneyTests(unittest.TestCase):
    def test_money_text(self):
        self.assertEqual(rm.money_text(200_000), "$200K")
        self.assertEqual(rm.money_text(770_000), "$770K")
        self.assertEqual(rm.money_text(3_930_000), "$3.93M")
        self.assertEqual(rm.money_text(16_800_000), "$16.8M")
        self.assertEqual(rm.money_text(367_000_000), "$367M")
        self.assertEqual(rm.money_text(-29_500_000), "-$29.5M")
        self.assertEqual(rm.money_text(0), "$0")
        self.assertEqual(rm.money_text(None), "not known")

    def test_basis_labels(self):
        self.assertEqual(rm.cap_basis("Week 4", False), "scanned Week 4")
        est = rm.cap_basis("Week 4", True, 3)
        self.assertTrue(est.startswith("estimated: last cap scan Week 4 plus 3 roster moves since"))
        self.assertIn("dead money from in-season signings is unknown", est)
        self.assertIn("1 roster move since", rm.cap_basis("Week 4", True, 1))
        self.assertEqual(rm.contract_basis("SCAN", "Week 4"), "scanned Week 4")
        self.assertTrue(rm.contract_basis("TRANSACTION", "Week 4").startswith("from the signing"))
        self.assertEqual(rm.scan_label(4, None), "Week 4")
        self.assertEqual(rm.scan_label(None, "2026-10-20 05:59:45+00"), "2026-10-20")

    def test_derived_cap_row(self):
        self.assertFalse(rm.cap_is_derived("2026-10-20", None))
        self.assertTrue(rm.cap_is_derived("2026-10-20", "2026-10-21"))
        self.assertFalse(rm.cap_is_derived("2026-10-21", "2026-10-20"))

    def test_projected_years_always_carry_est(self):
        """JR, 2026-10-20: projected yearly hits are shown, each labelled est."""
        years = [(2036, 16_800_000, False), (2037, 14_200_000, True), (2038, 0, True), (2039, 9_000_000, False)]
        out = rm.year_hits(years)
        self.assertEqual(out, ["2036: $16.8M", "2037: $14.2M est.", "2039: $9M"])
        committed = rm.committed_by_year(years + [(2037, 20_000_000, False), (2037, 5_000_000, True)], 2036)
        self.assertEqual(committed["2037"], "$20M + $19.2M est. from 2 new deals")
        self.assertEqual(committed["2039"], "$9M")
        projected = {rm.money_text(h) for _, h, p in years if p and h} | {"$19.2M"}
        text = " | ".join(out + list(committed.values()))
        for m in projected:
            for hit in re.finditer(re.escape(m), text):
                self.assertTrue(text[hit.end():].startswith(" est."), f"{m} printed without est.")

    def test_extension_text(self):
        self.assertEqual(rm.extension_text(4, 88_000_000, 2038),
                         "new deal: 4 years, $88M total, starts 2038; year-by-year split is projected (est.)")
        self.assertEqual(rm.extension_text(None, None, 2038), "")


class VisibilityTests(unittest.TestCase):
    def test_no_context_sees_everything(self):
        self.assertTrue(rm.txn_visible(2036, 9, 9, None, None))

    def test_seasons(self):
        self.assertTrue(rm.txn_visible(2035, 17, 17, 2036, 1))
        self.assertFalse(rm.txn_visible(2037, 1, 1, 2036, 18))

    def test_same_week_depends_on_capture_week(self):
        self.assertTrue(rm.txn_visible(2036, 3, 4, 2036, 4))     # earlier week
        self.assertTrue(rm.txn_visible(2036, 4, 4, 2036, 4))     # Week N row loaded at N: before Week N's games
        self.assertFalse(rm.txn_visible(2036, 4, 5, 2036, 4))    # late Week N row loaded at N+1
        self.assertTrue(rm.txn_visible(2036, 4, None, 2036, 4))  # no capture (a confirmed trade)
        self.assertFalse(rm.txn_visible(2036, 5, None, 2036, 4))
        self.assertFalse(rm.txn_visible(2036, 3, 4, 2036, 3))    # Week 3 row loaded by the Week 4 capture

    def test_offseason_rows(self):
        self.assertFalse(rm.txn_visible(2036, None, 24, 2036, 23))
        self.assertTrue(rm.txn_visible(2036, None, 24, 2036, 24, True))
        self.assertFalse(rm.txn_visible(2036, None, 24, 2036, 24, False))

    def test_new_this_week(self):
        rows = [row(week=3, capture_week=3), row(week=4, capture_week=4), row(week=5, capture_week=5),
                row(week=2, capture_week=2)]
        self.assertEqual([r["week"] for r in rm.new_this_week(rows, 2036, 4)], [4])
        self.assertEqual([r["week"] for r in rm.new_this_week(rows, 2036, 5)], [5])
        late = [row(week=4, capture_week=5)]
        self.assertEqual(rm.new_this_week(late, 2036, 5), late)   # hidden from Week 4, new for Week 5
        off = [row(week=None, capture_week=24)]
        self.assertEqual(rm.new_this_week(off, 2036, 24, True), off)


class NotabilityTests(unittest.TestCase):
    def test_reasons(self):
        self.assertEqual(rm.notability(row(is_starter=True))[1], ["starter"])
        self.assertIn("popular", rm.notability(row(popularity=55))[1])
        self.assertIn("big signing", rm.notability(row(contract_value=5_000_000))[1])
        self.assertFalse(rm.notability(row(contract_value=4_990_000))[0])
        self.assertIn("big new deal", rm.notability(row(type="RE_SIGNED", contract_value=20_000_000))[1])
        self.assertFalse(rm.notability(row(type="RE_SIGNED", contract_value=19_000_000))[0])
        self.assertIn("big restructure", rm.notability(row(type="RESTRUCTURED", contract_value=5_000_000))[1])
        self.assertIn("big dead money", rm.notability(row(type="RELEASED", dead_money=5_000_000))[1])
        self.assertFalse(rm.notability(row(type="RELEASED", dead_money=None))[0])

    def test_ovr_ranks_but_is_never_a_reason(self):
        notable, reasons, _ = rm.notability(row(ovr=90))
        self.assertTrue(notable)
        self.assertEqual(reasons, [])
        self.assertNotIn("90", rm.move_line(dict(row(ovr=90), notable=True)))

    def test_annotate_puts_notable_first(self):
        rows = rm.annotate([row(week=4), row(week=3, popularity=70), row(week=2, contract_value=9_000_000)])
        self.assertEqual([r["notable"] for r in rows], [True, True, False])


class MoveLineTests(unittest.TestCase):
    def test_signing(self):
        line = rm.move_line(dict(row(depth_slot=4), notable=False))
        self.assertEqual(line, "Week 4 · SIGNED · Jaguars signed WR Devonta Taylor (free agent): 1 year, $3.93M · "
                               "now WR4 on the depth chart · notable: no")

    def test_release_and_later_signing(self):
        line = rm.move_line(row(type="RELEASED", week=3, team="Cowboys", dead_money=None), with_notable=False)
        self.assertIn("dead money not known", line)
        self.assertIn("free agent since Week 3", line)
        line = rm.move_line(row(type="RELEASED", week=3, team="Cowboys", dead_money=1_200_000, later_team="Dolphins",
                                later_week=4), with_notable=False)
        self.assertIn("dead money $1.2M", line)
        self.assertIn("since signed by the Dolphins (Week 4)", line)

    def test_re_signing_and_restructure(self):
        line = rm.move_line(row(type="RE_SIGNED", contract_years=4, contract_value=118_000_000, new_deal_start=2037))
        self.assertIn("re-signed WR Devonta Taylor: 4 years, $118M total, new deal starts 2037", line)
        line = rm.move_line(row(type="RESTRUCTURED", contract_value=3_440_000, contract_years=None))
        self.assertIn("freed $3.44M in cap space this season", line)

    def test_names(self):
        self.assertEqual(rm.display_name({"player_name": "MARCO STEPHENSON"}), "Marco Stephenson")
        self.assertEqual(rm.display_name({"full_name": "LT Overton", "player_name": "LT OVERTON"}), "LT Overton")

    def test_counts(self):
        self.assertEqual(rm.counts_by_type([row(), row(type="RELEASED"), row()]), {"RELEASED": 1, "SIGNED": 2})


class BriefBlockTests(unittest.TestCase):
    def test_brief_block(self):
        rows = [row(week=5, player_name=f"PLAYER {i}", contract_value=1_000_000 + i) for i in range(15)]
        rows.append(row(week=5, player_name="STAR GUY", popularity=80, ovr=97))
        caps = [{"team": "Colts", "cap_space": -29_500_000, "cap_penalty": 37_800_000, "total_spend": 398_000_000,
                 "basis": "scanned Week 4"},
                {"team": "Panthers", "cap_space": 161_000_000, "cap_penalty": 7_260_000, "total_spend": 250_000_000,
                 "basis": "scanned Week 4"}]
        block = rm.brief_block(rows, 5, caps)
        self.assertEqual(len(block["notable_moves"]), 12)
        self.assertIn("Star Guy", block["notable_moves"][0])
        self.assertEqual(block["counts"], {"SIGNED": 16})
        self.assertEqual(block["cap_watch"], ["Colts: -$29.5M in cap space, $37.8M dead money, $398M total spend "
                                              "(scanned Week 4)"])
        self.assertNotIn("97", " ".join(block["notable_moves"]))
        self.assertIn("most articles carry none", block["note"])

    def test_brief_block_omitted_without_moves(self):
        self.assertEqual(rm.brief_block([], 5, []), {})

    def test_team_block(self):
        block = rm.team_block([row(type="RELEASED", dead_money=6_000_000)], "Jaguars: $4M in cap space ...")
        self.assertIn("dead money $6M", block["moves_this_week"][0])
        self.assertIn("notable: yes", block["moves_this_week"][0])
        self.assertEqual(block["cap_snapshot"], "Jaguars: $4M in cap space ...")
        self.assertIn("never a figure", block["reporting_guidance"])


class OnRosterTests(unittest.TestCase):
    def test_on_roster(self):
        self.assertTrue(rm.on_roster(None, 5))
        self.assertTrue(rm.on_roster(6, 5))      # released after the article's week
        self.assertFalse(rm.on_roster(5, 5))     # released in Week 5 (before its games): gone for a Week 5 article
        self.assertFalse(rm.on_roster(2, 5))
        self.assertFalse(rm.on_roster(0, 5))     # preseason release
        self.assertFalse(rm.on_roster(24, None))  # no article context: every release counts
        self.assertEqual(rm.roster_week(None), rm.NO_CUTOFF_WEEK)
        self.assertEqual(rm.roster_week(4), 4)

    def test_free_agent_label(self):
        self.assertEqual(rm.free_agent_label("Texans", 3), "free agent (released by the Texans, Week 3)")
        self.assertEqual(rm.free_agent_label("Texans", 24), "free agent (released by the Texans, the offseason)")
        self.assertEqual(rm.free_agent_label("Texans", 0), "free agent (released by the Texans, the preseason)")


class StarterRankTests(unittest.TestCase):
    def test_slots(self):
        self.assertTrue(rm.is_starter_rank("QB", 1))
        self.assertFalse(rm.is_starter_rank("QB", 2))
        self.assertTrue(rm.is_starter_rank("HB", 2))
        self.assertTrue(rm.is_starter_rank("WR", 3))
        self.assertFalse(rm.is_starter_rank("WR", 4))
        self.assertFalse(rm.is_starter_rank("WR", None))


if __name__ == "__main__":
    unittest.main()
