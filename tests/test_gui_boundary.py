"""The window exists, and the command line never pays for it."""
import subprocess
import sys

import dirarttool as dt
from conftest import ROOT


def test_cli_never_imports_tkinter(fixture):
    p = fixture("baracuda-dir-file.d64")
    code = ("import sys, dirarttool as dt; "
            "dt.main([%r, '--replace', 'D6=D1', '--dry-run']); "
            "print('tk' + 'inter' in sys.modules)" % str(p))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("False")


def test_window_class_exists():
    assert callable(dt.run_gui)
    assert isinstance(dt.App, type)
    for method in ("open_path", "set_from", "set_to", "apply", "undo", "save", "save_as"):
        assert callable(getattr(dt.App, method))
