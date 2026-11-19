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
