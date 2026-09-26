"""Framework and product unit tests.

``unittest discover -s unit_tests`` temporarily puts this directory at the
front of ``sys.path``.  The tests contain packages named ``framework``,
``products`` and ``suites`` too, so without normalising the import path they
shadow the application packages under the repository root.
"""

import sys
from pathlib import Path


_TEST_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _TEST_ROOT.parent
try:
    sys.path.remove(str(_TEST_ROOT))
except ValueError:
    pass
sys.path.insert(0, str(_REPO_ROOT))
