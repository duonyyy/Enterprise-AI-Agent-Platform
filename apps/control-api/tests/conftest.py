import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TESTS_DIR = str(_REPO_ROOT / "tests")

if "tests" in sys.modules and _TESTS_DIR not in sys.modules["tests"].__path__:
    sys.modules["tests"].__path__.append(_TESTS_DIR)
