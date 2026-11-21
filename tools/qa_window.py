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

    maskdisk = shutil.copy(os.path.join(FIX, "baracuda-mask-disk.d64"), tmp)
    ok = app.open_path(maskdisk)
    app.root.update()
    files_before = [r.name for r in app.rows if r.kind == "file"]
    app.set_mask(0x31)                                   # the "1" placeholders in the stamp
    refused = app.apply_mask() == 0 and not app.dirty    # no source lines yet: nothing happens
    app.mask_sources.set("1-7")                          # the finished art above the stamp
    n = app.apply_mask()
    app.root.update()
    first = app.image
    stamp = [r for r in app.rows if 28 <= r.index <= 48]
    filled = (n == 255
              and all(0x31 not in r.name for r in app.rows if r.kind == "art")
              and len({r.name for r in stamp}) == len(stamp)
              and [r.name for r in app.rows if r.kind == "file"] == files_before
              and app.dirty)
    again = app.apply_mask() == 255 and app.image != first and len(app.undo_stack) == 1
    undone = app.undo()
    restored = any(0x31 in r.name for r in app.rows if r.kind == "art") and not app.dirty
    check(4, ok and refused and filled and again and undone and restored,
          f"Mask tab: Fill $31 from lines 1-7 fills the stamp ({n} cells), every line "
          f"different, files keep their names, the button again remixes, Undo restores")

    header, disk_id = dt.read_header(app.image), dt.read_id(app.image)
    copied = app.copy_both()
    ok = app.open_path(bara)
    app.root.update()
    names_before = [r.name for r in app.rows]
    wrote = app.paste_both()
    app.root.update()
    landed = (dt.read_header(app.image) == header and dt.read_id(app.image) == disk_id
              and [r.name for r in app.rows] == names_before and app.dirty)
    back = app.undo() and not app.dirty
    check(5, copied and ok and wrote and landed and back,
          "Header buffer: copy header + ID on one disk (F2), write them on another (F4), "
          "rows untouched, Undo restores")

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
    check(6, ok and not set(files) & set(ticked) and warned and app.rows[demo.index].load_name == "XEMO",
          f"examples.d64: file rows {files} start unticked; ticking DEMO warns, then DEMO -> XEMO")

    asked.clear()
    ok = app.open_path(txt)
    check(7, ok is False and app.root.winfo_exists() == 1 and len(app.rows) == 12
          and any(k == "error" for k, _ in asked),
          "opening a .txt shows an error dialog, the window and the loaded disk survive")

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        fresh = shutil.copy(os.path.join(FIX, "baracuda-dir-file.d64"), os.path.join(tmp, "dropped.d64"))
        app.row_vars[demo.index].set(False)          # leave examples.d64 clean, no save prompt
        app.undo()
        app.root.update()
        payload = (fresh + "\0\0").encode("utf-16-le")
        hdr = (20).to_bytes(4, "little") + bytes(12) + (1).to_bytes(4, "little")
        k, u = ctypes.windll.kernel32, ctypes.windll.user32
        k.GlobalAlloc.restype = wintypes.HGLOBAL
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        h = k.GlobalAlloc(0x42, 20 + len(payload))
        p = k.GlobalLock(h)
        ctypes.memmove(p, hdr, 20)
        ctypes.memmove(p + 20, payload, len(payload))
        k.GlobalUnlock(h)
        u.SendMessageW(app.dir_canvas.winfo_id(), 0x0233, h, 0)      # WM_DROPFILES, as Explorer sends it
        import time
        for _ in range(10):                  # the handler parks the paths; a Tk timer opens them
            app.root.update()
            time.sleep(0.03)
        check(8, app.path == fresh and len(app.rows) == 3,
              "a .d64 dropped on the directory canvas (shell WM_DROPFILES) opens")
        app.set_from(0xD6)
        app.set_to(0xD1)
        app.apply()                                  # unsaved again, so item 9 has something to ask about
    else:
        print("--   8. drop test skipped: not Windows")

    asked.clear()
    app.quit()
    alive = app.root.winfo_exists() == 1
    mb.askyesnocancel = lambda *a, **k: False        # Discard
    app.quit()
    try:
        gone = not app.root.winfo_exists()
    except Exception:
        gone = True
    check(9, alive and gone and any(k == "yesnocancel" for k, _ in asked),
          "quit with unsaved changes asks; Cancel keeps the window, Discard closes it")

    print("window checklist: all items passed")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
