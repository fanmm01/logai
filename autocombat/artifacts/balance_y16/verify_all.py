"""Run the full suite and separately report the reproduced baseline failure."""
import contextlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
KNOWN = 'test_ui_management.ManagementTests.test_invalid_enabled_membership_is_atomic_and_disabled_drafts_are_preserved'
out = Path(__file__).resolve().parent
with (out / 'final_tests.log').open('w', encoding='utf-8') as log:
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
        result = unittest.TextTestRunner(stream=log, verbosity=1).run(suite)
failures = [test.id() for test, _ in result.failures]
errors = [test.id() for test, _ in result.errors]
unexpected = [name for name in failures if name != KNOWN] + errors
summary = {'run': result.testsRun, 'failures': failures, 'errors': errors,
           'unexpected_failures': unexpected, 'known_baseline_failure': KNOWN}
(out / 'test_results.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
sys.exit(bool(unexpected))
