#!/usr/bin/env python3
"""The QA gate: run this after any change, read the last line.

    python tools/qa.py            everything
    python tools/qa.py --quick    skip the window checklist

Four stages, in order; the first failure stops the run:

  1. pytest          the whole suite (engine, CLI, rendering rules, window)
  2. stdlib smoke    the tool run as a user would, with nothing installed:
                     --version, a dry run, and `import tkinter`
  3. golden disk     the Replacer on Baracuda's disk must produce exactly the
                     committed golden image, byte for byte, through the CLI,
                     and must leave a copy run with -o untouched
  4. window          tools/qa_window.py, the nine-item release checklist,
                     skipped (not failed) when Tk has no display

Ends with `QA: PASS` or `QA: FAIL <stage>`. Nothing else is a pass.
"""
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "dirarttool.py")
FIX = os.path.join(ROOT, "tests", "fixtures")
GOLDEN = os.path.join(FIX, "golden-baracuda-replaced.d64")


def run(*args, **kw):
    return subprocess.run([sys.executable, *args], cwd=ROOT, text=True,
                          capture_output=True, **kw)


def fail(stage, detail=""):
    if detail:
        print(detail.rstrip())
    print(f"QA: FAIL {stage}")
    sys.exit(1)


def stage_pytest():
    print("[1/4] pytest")
    r = run("-m", "pytest", "tests", "-q")
    print("      " + r.stdout.strip().splitlines()[-1])
    if r.returncode != 0:
        fail("pytest", r.stdout + r.stderr)


def stage_smoke():
    print("[2/4] stdlib smoke")
    r = run(TOOL, "--version")
    if r.returncode != 0 or "dirarttool" not in r.stdout:
        fail("smoke --version", r.stdout + r.stderr)
    r = run(TOOL, os.path.join(FIX, "baracuda-dir-file.d64"),
            "--replace", "D6=D1", "--replace", "D7=D1", "--dry-run")
    if r.returncode != 0 or "4 cells changed" not in r.stdout:
        fail("smoke dry run", r.stdout + r.stderr)
    r = run("-c", "import tkinter; print(tkinter.TkVersion)")
    if r.returncode != 0:
        fail("smoke import tkinter", r.stdout + r.stderr)
    print(f"      version ok, dry run ok, tkinter {r.stdout.strip()}")


def stage_golden():
    print("[3/4] golden disk")
    if not os.path.exists(GOLDEN):
        fail("golden", f"missing {GOLDEN}; make it with tools/make_golden.py")
    tmp = tempfile.mkdtemp(prefix="dirarttool-qa-")
    try:
        src = shutil.copy(os.path.join(FIX, "baracuda-dir-file.d64"), tmp)
        out = os.path.join(tmp, "out.d64")
        r = run(TOOL, src, "--replace", "D6=D1", "--replace", "D7=D1", "-o", out)
        if r.returncode != 0:
            fail("golden -o run", r.stdout + r.stderr)
        if not filecmp.cmp(src, os.path.join(FIX, "baracuda-dir-file.d64"), shallow=False):
            fail("golden", "-o changed the input disk")
        if not filecmp.cmp(out, GOLDEN, shallow=False):
            fail("golden", "the -o result differs from the golden image")
        r = run(TOOL, src, "--replace", "D6=D1", "--replace", "D7=D1")
        if r.returncode != 0 or not filecmp.cmp(src, GOLDEN, shallow=False):
            fail("golden in-place", r.stdout + r.stderr)
        leftovers = [f for f in os.listdir(tmp) if f.endswith(".tmp")]
        if leftovers:
            fail("golden", f"temporary files left behind: {leftovers}")
        print("      -o and in-place both byte-identical to the golden image, no temp files")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def stage_window():
    print("[4/4] window checklist")
    probe = run("-c", "import tkinter; tkinter.Tk().destroy()")
    if probe.returncode != 0:
        print("      skipped: Tk has no display here")
        return
    r = run(os.path.join(ROOT, "tools", "qa_window.py"))
    for line in r.stdout.strip().splitlines():
        print("      " + line)
    if r.returncode != 0:
        fail("window", r.stderr)


def main(argv):
    quick = "--quick" in argv
    stage_pytest()
    stage_smoke()
    stage_golden()
    if quick:
        print("[4/4] window checklist: skipped (--quick)")
    else:
        stage_window()
    print("QA: PASS")


if __name__ == "__main__":
    main(sys.argv[1:])
