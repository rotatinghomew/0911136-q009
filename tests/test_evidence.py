import unittest

from raincheck.evidence import Evidence, comparable


class EvidenceTests(unittest.TestCase):
    def test_missing_source_value_is_not_a_disagreement(self):
        self.assertFalse(comparable(Evidence("radar", None, False), Evidence("gauge", 3.0, True)))
