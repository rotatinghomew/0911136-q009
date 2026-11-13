"""验证现有领域摘要的业务边界。"""
import copy
import json
import unittest
from pathlib import Path

from raincheck.analysis import assignment_profile


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tables = json.loads((Path(__file__).resolve().parents[1]/'fixtures/dataset.json').read_text())['tables']

    def test_fixture_respects_restricted_sequences(self):
        self.assertEqual([],assignment_profile(self.tables)['design_violations'])

    def test_unresolved_is_distinct_from_completed(self):
        result = assignment_profile(self.tables)['item_counts']
        self.assertEqual(32,result['unresolved'])
        self.assertEqual(160,result['completed'])

    def test_illegal_sequence_is_reported(self):
        tables = copy.deepcopy(self.tables)
        tables['assignments'][0]['assigned_mode'] = 'A'
        self.assertIn('b0',assignment_profile(tables)['design_violations'])

    def test_actual_treatment_does_not_change_design_check(self):
        tables = copy.deepcopy(self.tables)
        for row in tables['items']:
            row['actual_mode'] = 'B'
        self.assertEqual(assignment_profile(self.tables),assignment_profile(tables))
