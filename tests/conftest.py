import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOOL = ROOT / "dirarttool.py"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture
def fixture(tmp_path):
    """Copy tests/fixtures/<name> into tmp_path and return the copy's path.

    Tests may write to the copy; the committed fixture is never touched.
    `name=` picks a different file name for the copy.
    """
    def _copy(src, name=None):
        dst = tmp_path / (name or src)
        shutil.copy(FIXTURES / src, dst)
        return dst
    return _copy


@pytest.fixture
def run_tool():
    """Invoke the CLI exactly as a user would, returning the CompletedProcess."""
    def _run(*args, expect_ok=None):
        proc = subprocess.run(
            [sys.executable, str(TOOL), *[str(a) for a in args]],
            capture_output=True, text=True, cwd=ROOT,
        )
        if expect_ok is True:
            assert proc.returncode == 0, f"expected success:\n{proc.stdout}\n{proc.stderr}"
        if expect_ok is False:
            assert proc.returncode != 0, f"expected failure but it passed:\n{proc.stdout}"
        return proc
    return _run
