"""验证试点效果分析的设计内推断、降级规则与删失处理。"""

import copy
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from raincheck.datasource import SourceClient
from raincheck.pilot import pilot_effect
from raincheck.service import DataRepository, serve

ROOT = Path(__file__).resolve().parents[1]


def load_dataset():
    return json.loads((ROOT / "fixtures/dataset.json").read_text())


class PilotTests(unittest.TestCase):
    def setUp(self):
        data = load_dataset()
        self.tables = data["tables"]
        self.metadata = data["metadata"]

    def test_confirmatory_finds_no_difference_on_fixture(self):
        report = pilot_effect(self.tables, self.metadata)
        confirmatory = report["confirmatory"]
        self.assertTrue(confirmatory["available"])
        self.assertEqual(256, confirmatory["duration"]["permutations"])
        self.assertAlmostEqual(0.0, confirmatory["duration"]["estimate"])
        self.assertEqual(1.0, confirmatory["duration"]["p_two_sided"])
        self.assertAlmostEqual(0.0, confirmatory["missed"]["estimate"])
        self.assertEqual(1.0, confirmatory["missed"]["p_two_sided"])
        self.assertFalse(report["conclusion"]["attributable_difference_found"])
        self.assertIn("不建议", report["conclusion"]["text"])

    def test_censoring_is_not_zero_and_not_dropped(self):
        report = pilot_effect(self.tables, self.metadata)
        arms = report["confirmatory"]["duration"]["arms"]
        for mode in ("A", "B"):
            self.assertEqual(96, arms[mode]["items"])
            self.assertEqual(16, arms[mode]["censored"])
            self.assertAlmostEqual(2.5, arms[mode]["rmst_hours"])
            self.assertAlmostEqual(80 / 96, arms[mode]["completion_within_horizon"])
        naive = report["exploratory"]["completed_only_naive"]["means"]
        self.assertAlmostEqual(1.8, naive["A"])
        self.assertGreater(arms["A"]["rmst_hours"], naive["A"])

    def test_itt_ignores_actual_mode(self):
        changed = copy.deepcopy(self.tables)
        for row in changed["items"]:
            row["actual_mode"] = "B" if row["actual_mode"] == "A" else "A"
        base = pilot_effect(self.tables, self.metadata)["confirmatory"]
        flipped = pilot_effect(changed, self.metadata)["confirmatory"]
        self.assertEqual(base, flipped)

    def test_design_violation_downgrades_block(self):
        changed = copy.deepcopy(self.tables)
        changed["assignments"][0]["assigned_mode"] = "A"
        report = pilot_effect(changed, self.metadata)
        self.assertNotIn("b0", report["design"]["included_blocks"])
        downgraded = {entry["block_id"]: entry["reasons"] for entry in report["design"]["downgraded_blocks"]}
        self.assertIn("b0", downgraded)
        self.assertTrue(any("允许集合" in reason for reason in downgraded["b0"]))
        self.assertEqual(128, report["confirmatory"]["duration"]["permutations"])

    def test_insufficient_items_downgrade_block(self):
        changed = copy.deepcopy(self.tables)
        removed = {"b0-s1-i0", "b0-s1-i1", "b0-s1-i2", "b0-s1-i3", "b0-s1-i4",
                   "b0-s2-i0", "b0-s2-i1", "b0-s2-i2", "b0-s2-i3", "b0-s2-i4"}
        changed["items"] = [row for row in changed["items"] if row["item_id"] not in removed]
        report = pilot_effect(changed, self.metadata)
        self.assertNotIn("b0", report["design"]["included_blocks"])
        reasons = report["design"]["downgraded_blocks"][0]["reasons"]
        self.assertTrue(any("事项数低于下限" in reason for reason in reasons))

    def test_all_blocks_downgraded_disables_confirmatory(self):
        changed = copy.deepcopy(self.tables)
        for row in changed["assignments"]:
            row["assigned_mode"] = "A"
        report = pilot_effect(changed, self.metadata)
        self.assertFalse(report["confirmatory"]["available"])
        self.assertEqual([], report["design"]["included_blocks"])
        self.assertFalse(report["conclusion"]["attributable_difference_found"])
        self.assertIn("不应", report["conclusion"]["text"])

    def test_known_at_filter_excludes_future_records(self):
        changed = copy.deepcopy(self.tables)
        changed["items"][0]["known_at"] = "2025-10-01T00:00:00+00:00"
        report = pilot_effect(changed, self.metadata)
        self.assertEqual(1, report["design"]["records_skipped_after_cutoff"])
        total = sum(arm["items"] for arm in report["confirmatory"]["duration"]["arms"].values())
        self.assertEqual(191, total)

    def test_missed_unknown_excluded_from_risk_denominator(self):
        report = pilot_effect(self.tables, self.metadata)
        arms = report["confirmatory"]["missed"]["arms"]
        for mode in ("A", "B"):
            self.assertEqual(16, arms[mode]["status_unknown"])
            self.assertEqual(80, arms[mode]["status_known"])
            self.assertAlmostEqual(0.2, arms[mode]["risk"])

    def test_strong_duration_effect_is_detected(self):
        changed = copy.deepcopy(self.tables)
        assignments = {row["shift_id"]: row for row in changed["assignments"]}
        for row in changed["items"]:
            if row["completed_at"] is None:
                continue
            if assignments[row["shift_id"]]["assigned_mode"] == "B":
                finished = datetime.fromisoformat(row["completed_at"]) + timedelta(hours=2)
                row["completed_at"] = finished.isoformat()
        report = pilot_effect(changed, self.metadata)
        duration = report["confirmatory"]["duration"]
        self.assertGreater(duration["estimate"], 0)
        self.assertLessEqual(duration["p_two_sided"], 0.05)
        self.assertTrue(report["conclusion"]["attributable_difference_found"])

    def test_carryover_and_shared_team_are_quantified(self):
        report = pilot_effect(self.tables, self.metadata)
        carryover = report["comparability"]["carryover"]
        self.assertEqual(32, carryover["items_open_at_shift_end"])
        self.assertEqual(16, carryover["carried_into_opposite_mode"])
        self.assertEqual(14, carryover["carried_into_same_mode"])
        self.assertEqual(6, carryover["carried_across_block_boundary"])
        self.assertEqual(32, carryover["still_open_at_cutoff"])
        shared = report["comparability"]["shared_team"]
        self.assertEqual(["team0", "team1"], shared["multi_town_teams"])
        censoring = report["comparability"]["censoring"]
        self.assertEqual(32, censoring["censored_items"])
        self.assertEqual(32, censoring["missed_status_unknown_for_censored"])

    def test_exploratory_is_separated_and_labeled(self):
        report = pilot_effect(self.tables, self.metadata)
        self.assertIn("不能作为试点效果证据", report["exploratory"]["warning"])
        self.assertEqual(8, len(report["exploratory"]["per_block"]))
        self.assertIn("as_treated", report["exploratory"])

    def test_missing_cutoff_fails(self):
        with self.assertRaises(ValueError):
            pilot_effect(self.tables, {})

    def test_end_to_end_over_http(self):
        data = load_dataset()
        schema = json.loads((ROOT / "raincheck/schema.json").read_text())
        with serve(DataRepository(data, schema)) as address:
            snapshot = SourceClient(address).read()
        report = pilot_effect(snapshot.tables, snapshot.metadata)
        self.assertEqual("pilot_effect_analysis", report["kind"])
        self.assertTrue(report["confirmatory"]["available"])


if __name__ == "__main__":
    unittest.main()
