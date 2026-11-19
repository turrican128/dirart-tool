#!/usr/bin/env python3
"""Drive the window through the release checklist, no human at the mouse.

    python tools/qa_window.py

Opens the real window on copies of the fixture disks, calls the same public
methods the buttons call, and prints one line per checklist item. Dialogs
are stubbed (a script has no finger), so what this proves is everything
behind them: the engine calls, the row ticks, Undo, Save, and that a bad
file or an unsaved quit never kills the window. Exit status 0 = all passed.

Needs a display. Run by tools/qa.py; skipped there when Tk has none.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import d64reader                     # noqa: E402
import dirarttool as dt              # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


def check(n, ok, text):
    print(f"{'ok ' if ok else 'FAIL'} {n}. {text}")
    if not ok:
        sys.exit(1)


def main():
    tmp = tempfile.mkdtemp(prefix="dirarttool-qa-")
    bara = shutil.copy(os.path.join(FIX, "baracuda-dir-file.d64"), tmp)
    exam = shutil.copy(os.path.join(FIX, "examples.d64"), tmp)
    txt = os.path.join(tmp, "not-a-disk.txt")
    with open(txt, "w") as fh:
        fh.write("hello")

    import tkinter.messagebox as mb
    asked = []
    mb.showerror = lambda *a, **k: asked.append(("error", a[1]))
    mb.askokcancel = lambda *a, **k: asked.append(("okcancel", a[1])) or True
    mb.askyesnocancel = lambda *a, **k: asked.append(("yesnocancel", a[1])) or None

    app = dt.App()
    app.root.update()

    ok = app.open_path(bara)
    app.root.update()
    cells = app.dir_canvas.find_withtag("cell")
    check(1, ok and len(app.rows) == 3 and len(cells) == 48 and app.dir_canvas.find_withtag("hdr"),
          f"Baracuda disk drawn: {len(app.rows)} rows, {len(cells)} cells, header present")

    app.set_from(0xD6)
    app.set_to(0xD1)
    n = app.apply()
    app.root.update()
    check(2, n == 2 and app.rows[1].name == app.rows[0].name and app.dirty,
          f"From $D6 To $D1, Apply: {n} cells, row 1 now equals row 0, window marked unsaved")

    undone = app.undo()
    restored = app.rows[1].name != app.rows[0].name and not app.dirty
    n2 = app.apply()
    saved = app.save()
    on_disk = d64reader.read_directory(bara)[1].name_exact == b"\xd1\xd1  BARACUDA  \xd1\xd1"
    cli = subprocess.run([sys.executable, os.path.join(ROOT, "dirarttool.py"), bara,
                          "--replace", "D7=D1", "--dry-run"], capture_output=True, text=True)
    check(3, undone and restored and n2 == 2 and saved and not app.dirty and on_disk
          and cli.returncode == 0,
          "Undo restores, Apply again, Save writes it, the CLI dry run reads it back")

    ok = app.open_path(exam)
    app.root.update()
    files = [r.index for r in app.rows if r.kind == "file"]
    ticked = [r.index for r, v in zip(app.rows, app.row_vars) if v.get()]
    demo = next(r for r in app.rows if r.load_name == "DEMO")
    app.row_vars[demo.index].set(True)
    app.set_from(ord("D"))
    app.set_to(ord("X"))
    asked.clear()
    n = app.apply()
    warned = any(k == "okcancel" and "LOAD name of DEMO" in msg for k, msg in asked)
    check(4, ok and not set(files) & set(ticked) and warned and app.rows[demo.index].load_name == "XEMO",
          f"examples.d64: file rows {files} start unticked; ticking DEMO warns, then DEMO -> XEMO")

    asked.clear()
    ok = app.open_path(txt)
    check(5, ok is False and app.root.winfo_exists() == 1 and len(app.rows) == 12
          and any(k == "error" for k, _ in asked),
          "opening a .txt shows an error dialog, the window and the loaded disk survive")

    asked.clear()
    app.quit()
    alive = app.root.winfo_exists() == 1
    mb.askyesnocancel = lambda *a, **k: False        # Discard
    app.quit()
    try:
        gone = not app.root.winfo_exists()
    except Exception:
        gone = True
    check(6, alive and gone and any(k == "yesnocancel" for k, _ in asked),
          "quit with unsaved changes asks; Cancel keeps the window, Discard closes it")

    print("window checklist: 6/6 passed")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
