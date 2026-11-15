"""Run the same constraints and aura lifecycle checks on the merged backend."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import autocombat_backend as backend
import tests.test_five_balance as regression

regression.be = backend
regression.cd = backend
result = unittest.TextTestRunner(verbosity=1).run(
    unittest.defaultTestLoader.loadTestsFromModule(regression))
raise SystemExit(not result.wasSuccessful())
