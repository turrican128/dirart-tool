"""The window, driven through its public methods. Skipped where Tk has no display."""
import pytest

import dirarttool as dt


@pytest.fixture
def app():
    import tkinter
    try:
        a = dt.App()
    except tkinter.TclError as exc:   # no display (Linux CI): the engine tests still run
        pytest.skip(f"no Tk display: {exc}")
    yield a
    try:
        a.root.destroy()
    except Exception:
        pass


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
