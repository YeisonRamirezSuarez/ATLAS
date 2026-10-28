"""tools/ci_annotate.py turns a unittest -v log into one GitHub annotation per failing test."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import ci_annotate

LOG = """test_a (t.A.test_a) ... ok
test_b (t.A.test_b) ... FAIL

======================================================================
ERROR: test_c (t.A.test_c)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "t.py", line 9, in test_c
    boom()
OSError: no sound server

======================================================================
FAIL: test_b (t.A.test_b)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "t.py", line 5, in test_b
AssertionError: 1 != 2

----------------------------------------------------------------------
Ran 3 tests in 0.1s

FAILED (failures=1, errors=1)
"""


class CiAnnotateTest(unittest.TestCase):
    def test_one_annotation_per_failure_with_its_error(self):
        got = ci_annotate.annotations(LOG)
        self.assertEqual(len(got), 2)
        self.assertTrue(got[0].startswith("::error title=ERROR - test_c (t.A.test_c)::"))
        self.assertTrue(got[0].endswith("OSError: no sound server"))
        self.assertIn("AssertionError: 1 != 2", got[1])
        self.assertEqual(ci_annotate.annotations("Ran 3 tests\n\nOK\n"), [])


if __name__ == "__main__":
    unittest.main()
