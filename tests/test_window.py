"""The window, driven through its public methods. Skipped where Tk has no display.

One hidden Tk root serves the whole module and every test's window is a
Toplevel on it. Building a second Tcl interpreter in a process while the
first is being torn down is a race Tcl loses now and then ("Can't find a
usable tk.tcl", 'invalid command name "tcl_findLibrary"'), so the tests
never do it; `App(master=...)` exists for exactly this.
"""
import pytest

import dirarttool as dt


@pytest.fixture(scope="module")
def master():
    import tkinter
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:   # no display (Linux CI): the engine tests still run
        pytest.skip(f"no Tk display: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(master):
    a = dt.App(master=master)
    yield a
    a.root.destroy()


def test_window_hangs_on_the_master_root(app, master):
    assert app.root.master is master
    assert app.root.winfo_toplevel() is app.root


def test_ticks_survive_apply_and_undo(app, fixture):
    assert app.open_path(str(fixture("baracuda-dir-file.d64")))
    app.row_vars[2].set(False)
    app.set_from(0xD6)
    app.set_to(0xD1)
    assert app.apply() == 2
    assert [v.get() for v in app.row_vars] == [True, True, False]
    assert app.undo()
    assert [v.get() for v in app.row_vars] == [True, True, False]


def test_mask_fills_the_stamp_and_undoes(app, fixture):
    assert app.open_path(str(fixture("baracuda-mask-disk.d64")))
    app.set_mask(0x31)                                   # the "1" placeholders
    app.mask_sources.set("1-7")
    n = app.apply_mask()
    assert n == 271                                      # the stamp and lines 51-53
    assert all(0x31 not in r.name for r in app.rows)
    assert app.dirty
    assert app.undo()
    assert any(0x31 in r.name for r in app.rows if r.kind == "art")
    assert not app.dirty


def test_a0_cell_is_picked_as_a0(app, fixture):
    assert app.open_path(str(fixture("examples.d64")))
    demo = next(r for r in app.rows if r.load_name == "DEMO")
    items = app.dir_canvas.find_withtag(f"r{demo.index}")
    bytes_tagged = {int(t[1:]) for i in items for t in app.dir_canvas.gettags(i)
                    if t.startswith("b") and t[1:].isdigit()}
    assert 0xA0 in bytes_tagged and 0x20 not in bytes_tagged


def test_apply_does_not_leak_widgets(app, fixture):
    assert app.open_path(str(fixture("baracuda-dir-file.d64")))
    app.set_from(0xD6)
    app.set_to(0xD1)
    before = len(app.dir_canvas.winfo_children())
    assert app.apply() == 2
    assert app.undo()
    assert app.apply() == 2
    assert len(app.dir_canvas.winfo_children()) == before


def test_drop_paths_opens_the_first_d64_and_ignores_the_rest(app, fixture):
    import tkinter.messagebox as mb
    shown = []
    mb.showerror = lambda *a, **k: shown.append(a[1])
    disk = str(fixture("baracuda-dir-file.d64"))
    assert app.drop_paths([disk, str(fixture("examples.d64"))]) is True
    assert app.path == disk and len(app.rows) == 3
    assert app.drop_paths([disk.replace(".d64", ".txt")]) is False     # not a disk: told, not crashed
    assert shown and app.path == disk


def test_shell_drop_message_opens_the_disk(app, fixture):
    """A real WM_DROPFILES, built the way Explorer builds it, lands on the canvas."""
    import ctypes
    import sys
    if sys.platform != "win32":
        pytest.skip("shell drop messages are a Windows thing")
    from ctypes import wintypes
    disk = str(fixture("baracuda-dir-file.d64"))
    app.root.update()
    payload = (disk + "\0\0").encode("utf-16-le")
    header = ctypes.create_string_buffer(20)
    ctypes.memmove(header, (20).to_bytes(4, "little") + bytes(12) + (1).to_bytes(4, "little"), 20)
    size = 20 + len(payload)
    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    hglobal = kernel32.GlobalAlloc(0x0042, size)                       # GMEM_MOVEABLE | GMEM_ZEROINIT
    p = kernel32.GlobalLock(hglobal)
    ctypes.memmove(p, header.raw, 20)
    ctypes.memmove(p + 20, payload, len(payload))
    kernel32.GlobalUnlock(hglobal)
    user32.SendMessageW(app.dir_canvas.winfo_id(), 0x0233, hglobal, 0)   # WM_DROPFILES
    import time
    for _ in range(10):                      # the handler parks the paths; a Tk timer opens them
        app.root.update()
        time.sleep(0.03)
    assert app.path == disk and len(app.rows) == 3


def test_drop_posted_from_another_process(app, fixture):
    """The crash case: a drop that arrives through Tk's own message dispatch.

    An in-process SendMessage runs the handler with Python on the stack; a
    real drop from Explorer does not. The first version called back into Tk
    from the handler and died with a fatal GIL error on every real drop.
    """
    import subprocess
    import sys
    import time
    if sys.platform != "win32":
        pytest.skip("shell drop messages are a Windows thing")
    disk = str(fixture("baracuda-dir-file.d64"))
    app.root.update()
    sender = r'''
import ctypes, sys
from ctypes import wintypes
k, u = ctypes.windll.kernel32, ctypes.windll.user32
disk, hwnd = sys.argv[1], int(sys.argv[2])
payload = (disk + "\0\0").encode("utf-16-le")
hdr = (20).to_bytes(4, "little") + bytes(12) + (1).to_bytes(4, "little")
k.GlobalAlloc.restype = wintypes.HGLOBAL; k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
k.GlobalLock.restype = ctypes.c_void_p; k.GlobalLock.argtypes = [wintypes.HGLOBAL]
k.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
h = k.GlobalAlloc(0x42, 20 + len(payload)); p = k.GlobalLock(h)
ctypes.memmove(p, hdr, 20); ctypes.memmove(p + 20, payload, len(payload)); k.GlobalUnlock(h)
sys.exit(0 if u.PostMessageW(hwnd, 0x0233, h, 0) else 1)
'''
    proc = subprocess.Popen([sys.executable, "-c", sender, disk, str(app.dir_canvas.winfo_id())])
    deadline = time.time() + 5
    while time.time() < deadline and app.path != disk:
        app.root.update()                   # keep pumping: a posted drop needs a live loop
        time.sleep(0.02)
    assert proc.wait(timeout=5) == 0
    assert app.path == disk and len(app.rows) == 3


def _stub_dialogs(monkeypatch):
    import tkinter.messagebox as mb
    said = []
    monkeypatch.setattr(mb, "showerror", lambda *a, **k: said.append(a[1]))
    return said


def test_mask_without_source_lines_does_nothing(app, fixture, monkeypatch):
    """No silent default: the disk's separators and text rows are not art."""
    said = _stub_dialogs(monkeypatch)
    app.open_path(str(fixture("baracuda-mask-disk.d64")))
    app.set_mask(0x31)
    assert str(app.mask_btn["state"]) == "disabled"
    assert app.apply_mask() == 0
    assert not app.dirty and "Take from" in said[0]
    app.mask_sources.set("1-7")
    assert str(app.mask_btn["state"]) == "normal"
    app.mask_sources.set("1-7,200")                  # a line this disk does not have
    assert str(app.mask_btn["state"]) == "disabled"


def test_right_click_on_a_line_in_the_mask_tab_takes_signs_from_it(app, fixture):
    app.open_path(str(fixture("baracuda-mask-disk.d64")))
    app.notebook.select(1)
    c = app.dir_canvas
    for row in (1, 2, 3, 5):
        c.dtag("current", "current")
        c.addtag("current", "withtag", c.find_withtag(f"r{row}")[0])
        app._pick(None, c, "to")
    assert app.mask_sources.get() == "1-3,5"
    assert len(c.find_withtag("srcmark")) == 4
    c.dtag("current", "current")
    c.addtag("current", "withtag", c.find_withtag("r2")[0])
    app._pick(None, c, "to")                         # again: stop taking from it
    assert app.mask_sources.get() == "1,3,5"
    c.dtag("current", "current")
    c.addtag("current", "withtag", c.find_withtag("r28")[1])
    app._pick(None, c, "from")                       # left click: the sign to fill
    assert app.mask_char == app.rows[28].name[1] == 0x31
    assert app.src is None                           # the Replacer's From is not touched


def test_pressing_the_mask_button_again_gives_another_mix(app, fixture):
    app.open_path(str(fixture("baracuda-mask-disk.d64")))
    app.set_mask(0x31)
    app.mask_sources.set("1-7")
    assert app.apply_mask() == 271
    first = app.image
    assert app.mask_seed.get() == "1" and app.mask_btn["text"] == "Mix again"
    assert app.apply_mask() == 271
    assert app.image != first and app.mask_seed.get() == "2"
    assert len(app.undo_stack) == 1                  # one Undo still returns to the stamp
    app.undo()
    assert app.image == app.on_disk and app.mask_btn["text"] == "Mix and fill"
    app.mask_seed.set("1")
    assert app.apply_mask() == 271
    assert app.image == first                        # a mix number names one mix


def test_mask_saves_like_any_other_edit(app, fixture, tmp_path):
    p = fixture("baracuda-mask-disk.d64")
    before = p.read_bytes()
    app.open_path(str(p))
    app.set_mask(0x31)
    app.mask_sources.set("1-7")
    app.apply_mask()
    assert p.read_bytes() == before                  # nothing is written before Save
    copy = tmp_path / "masked.d64"
    assert app.save_as(str(copy))
    assert p.read_bytes() == before and copy.read_bytes() == app.image
    app.notebook.select(0)
    app.set_from(0xB0)
    app.set_to(0xAE)
    assert app.apply() > 0
    assert app.save()                                # plain Save, no dialog in the way
    assert copy.read_bytes() == app.image and not app.dirty


def test_mask_follows_the_ticks(app, fixture):
    app.open_path(str(fixture("baracuda-mask-disk.d64")))
    for i, var in enumerate(app.row_vars):
        var.set(i == 29)
    app.set_mask(0x31)
    app.mask_sources.set("1")
    assert app.apply_mask() == 14
    assert 0x31 in app.rows[28].name
    assert 0x31 not in app.rows[29].name


def test_header_buffer_goes_from_one_disk_to_another(app, fixture):
    """F1 on one disk, F3 on the next: Baracuda's insert, copy, insert, write."""
    src, dst = fixture("baracuda-mask-disk.d64"), fixture("baracuda-dir-file.d64")
    want_header = dt.read_header(src.read_bytes())
    want_id = dt.read_id(src.read_bytes())
    assert not app.paste_header()                    # nothing open, nothing copied
    app.open_path(str(src))
    assert not app.paste_header()                    # still nothing copied
    assert app.copy_header() and app.clip_header == want_header and app.clip_id is None
    app.open_path(str(dst))                          # the buffer survives the next disk
    old_id = dt.read_id(app.image)
    assert app.paste_header()
    assert dt.read_header(app.image) == want_header and dt.read_id(app.image) == old_id
    assert app.dirty and not app.paste_header()      # already there: nothing to do
    assert not app.paste_id()                        # no ID copied yet
    assert app.undo() and not app.dirty

    app.open_path(str(src))
    assert app.copy_both() and app.clip_id == want_id
    app.open_path(str(dst))
    assert app.paste_id() and dt.read_id(app.image) == want_id
    assert dt.read_header(app.image) != want_header
    app.undo()
    assert app.paste_both()
    assert dt.read_header(app.image) == want_header and dt.read_id(app.image) == want_id
    assert app.save() and dst.read_bytes() == app.image


def _press(widget, key):
    """Run what Tk runs when `key` reaches the window. A real key event needs
    the window to hold the keyboard focus, which a test run cannot count on;
    the binding itself can be run directly."""
    import re
    script = widget.bind(f"<{key}>")
    assert script, f"nothing is bound to {key}"
    widget.tk.eval(re.sub(r"%[#A-Za-z]", "0", script))


def test_function_keys_drive_the_header_buffer(app, fixture):
    src, dst = fixture("baracuda-mask-disk.d64"), fixture("baracuda-dir-file.d64")
    header, disk_id = dt.read_header(src.read_bytes()), dt.read_id(src.read_bytes())
    app.open_path(str(src))
    _press(app.root, "F1")
    assert app.clip_header == header and app.clip_id is None
    _press(app.root, "F5")
    assert app.clip_id == disk_id
    app.open_path(str(dst))
    _press(app.root, "F3")
    assert dt.read_header(app.image) == header and dt.read_id(app.image) != disk_id
    _press(app.root, "F7")
    assert dt.read_id(app.image) == disk_id
    app.undo()
    app.undo()
    app.clip_header = app.clip_id = None
    app.open_path(str(src))
    _press(app.root, "F2")
    assert (app.clip_header, app.clip_id) == (header, disk_id)
    app.open_path(str(dst))
    _press(app.root, "F4")
    assert (dt.read_header(app.image), dt.read_id(app.image)) == (header, disk_id)


def test_opening_another_disk_asks_about_its_files_again(app, fixture, monkeypatch):
    """1.0.0 asked once per session; a second disk's files were renamed unasked."""
    import tkinter.messagebox as mb
    asked = []
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: asked.append(a[1]) or True)
    for name in ("examples.d64", "three_files.d64"):
        app.open_path(str(fixture(name)))
        app.scope.set("all")
        first = next(r for r in app.rows if r.kind == "file")
        app.set_from(first.name[0])
        app.set_to(0x5A if first.name[0] != 0x5A else 0x59)
        assert app.apply() > 0
        app.undo()
    assert len(asked) == 2
