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
