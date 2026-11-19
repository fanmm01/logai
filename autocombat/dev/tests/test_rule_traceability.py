"""Keep rule identities aligned between the plan, observations and decisions."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
ID = r'(?:Y\d+|C)-\d+[a-z]?'


class RuleTraceabilityTests(unittest.TestCase):
    def test_catalogue_has_one_observation_and_one_current_decision_per_id(self):
        plan = (ROOT / 'docs/声明与执行差异_弥合实施计划.md').read_text(encoding='utf-8')
        expected = re.findall(r'^\| (' + ID + r') \|', plan, re.M)
        self.assertEqual(len(expected), len(set(expected)))
        observations = (ROOT / 'docs/声明与执行差异_1115.md').read_text(encoding='utf-8')
        headers = re.findall(r'^### \[([^]]+)\]', observations, re.M)
        actual = [identity for header in headers for identity in header.split('/')]
        self.assertEqual(len(actual), len(set(actual)), 'An ID must not name two different observations')
        self.assertEqual(set(expected), {identity for identity in actual if not identity.endswith('a')})
        audit = (ROOT / 'docs/规则弥合_阶段3验收.md').read_text(encoding='utf-8')
        anchors = re.findall(r'<a id="([^"]+)"', audit)
        self.assertEqual(len(anchors), len(set(anchors)))
        self.assertEqual({identity.lower() for identity in actual}, set(anchors))
        for identity in actual:
            self.assertIn(f'阶段3验收.md#{identity.lower()}', observations)
        for identity in expected:
            self.assertRegex(audit, re.escape(identity) + r' / (LOCK|ALIGN|SELECTED|OPEN|EVIDENCE)')
        self.assertIn('Y12-06 / OPEN', audit)
        self.assertIn('Y17-10 / EVIDENCE', audit)


if __name__ == '__main__':
    unittest.main()
