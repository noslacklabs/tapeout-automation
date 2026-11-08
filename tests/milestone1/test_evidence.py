from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from m1_collect import drc_evidence, extraction_evidence, proof_evidence, report_rows, xunit_evidence


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='m1 evidence ')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'report.xml'

    def write(self, text):
        self.path.write_text(text, encoding='utf-8')
        return self.path

    def test_positive_drc_has_expected_executed_categories(self):
        text = '<report-database><categories><category><name>width</name></category></categories><items/></report-database>'
        self.assertEqual(drc_evidence(self.write(text), {'width'}), {'categories': 1, 'markers': 0})

    def test_empty_missing_or_changed_drc_is_not_clean(self):
        texts = ['<report-database><categories/><items/></report-database>',
                 '<report-database><categories><category><name>another_rule</name></category></categories><items/></report-database>',
                 '<report-database><categories>']
        for text in texts:
            with self.subTest(text=text), self.assertRaises(Exception):
                drc_evidence(self.write(text), {'width'})

    def test_real_drc_markers_fail(self):
        text = '<report-database><categories><category><name>width</name></category></categories><items><item/></items></report-database>'
        with self.assertRaisesRegex(ValueError, 'markers'):
            drc_evidence(self.write(text), {'width'})

    def test_xunit_requires_exact_nonzero_complete_tests(self):
        valid = '<testsuite><testcase classname="suite" name="one"/><testcase classname="suite" name="two"/></testsuite>'
        self.assertEqual(xunit_evidence(self.write(valid), 2)['tests'], 2)
        for text in ['<testsuite/>', valid.replace('name="two"', 'name="one"'),
                     valid.replace('name="two"/>', 'name="two"><skipped/></testcase>'),
                     valid.replace('name="two"/>', 'name="two"><failure/></testcase>')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                xunit_evidence(self.write(text), 2)

    def test_known_extraction_defect_stays_fail(self):
        data = dict(openrcx_total_pF=0.1, rescaled_total_pF=20.0, nets_matched=2, nets_unmatched=0)
        spef = '*C_UNIT 1 PF\n*D_NET a 0.06\n*CAP\n*END\n*D_NET b 0.04\n*CAP\n*END\n'
        result = extraction_evidence(data, spef, dict(raw_extraction_ratio_min=100, raw_extraction_ratio_max=1000))
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['full_reference_result'], 1)
        self.assertEqual(result['estimate_electrical_validation'], 'NOT RUN')

    def test_crash_missing_or_bad_parasitics_are_not_expected_failure(self):
        data = dict(openrcx_total_pF=0.1, rescaled_total_pF=20.0, nets_matched=2, nets_unmatched=0)
        spef = '*C_UNIT 1 PF\n*D_NET a 0.06\n*END\n*D_NET b 0.04\n*END\n'
        cases = [(data, ''), (data, spef.replace('PF', 'FF')), (data, spef.replace('*END\n', '', 1)),
                 ({**data, 'nets_unmatched': 1}, spef), ({**data, 'openrcx_total_pF': float('nan')}, spef),
                 ({**data, 'rescaled_total_pF': 0.12}, spef), (data, spef.replace('b 0.04', 'a 0.04')),
                 (data, spef.replace('b 0.04', 'b 0.02'))]
        for row, text in cases:
            with self.subTest(row=row, text=text), self.assertRaises(ValueError):
                extraction_evidence(row, text, dict(raw_extraction_ratio_min=100, raw_extraction_ratio_max=1000))

    def test_report_cannot_be_empty_or_duplicate(self):
        row = '| Extraction | **FAIL** | known failure |'
        self.assertEqual(report_rows(row)[0]['result'], 'FAIL')
        for text in ('', row + '\n' + row):
            with self.assertRaises(ValueError):
                report_rows(text)

    def test_proof_status_metadata_and_real_completion_are_both_required(self):
        result = proof_evidence('PASS 0 21\n', 'SBY [lec] DONE (PASS, rc=0)\n')
        self.assertEqual(result['status_record'], 'PASS 0 21')
        for status, log in [('PASS', ''), ('PASS 0 21', 'DONE (PASS, rc=1)'),
                            ('PASSING', 'DONE (PASS, rc=0)'), ('FAIL 0 21', 'DONE (PASS, rc=0)')]:
            with self.subTest(status=status, log=log), self.assertRaises(ValueError):
                proof_evidence(status, log)


if __name__ == '__main__':
    unittest.main()
