"""Run the same Y17 regressions against the generated backend in isolation."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import tests.test_y17_balance as tests
import autocombat_backend as backend

tests.be = backend
tests.cd = backend
tests.trainer = backend
result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(tests))
raise SystemExit(not result.wasSuccessful())
