"""Baracuda's report on 1.1.0: five bugs, the Edit and Extras wishes, the Mask
ideas. One section per item, in the order he wrote them."""
import struct
import zlib

import pytest

import dirarttool as dt

DISK2 = "baracuda-mask-disk.d64"
ONE = 0x31


def entry(data, index):
    row = dt.directory(data)[index]
    return row.entry_offset


# ---- bug 1: the ID is shown like it is ------------------------------------------

def test_all_five_id_signs_are_listed(fixture, run_tool):
    """The disk's ID bytes are $80 2 0 2 6. 1.1.0 dropped the third one and
    printed a space, so 2026 read "2 26" and KOELN read "KO LN"."""
    p = fixture(DISK2)
    assert dt.read_id(p.read_bytes()) == b"\x802026"
    out = run_tool(p, "--replace", "00=01", "--dry-run", expect_ok=True).stdout
    assert '" .2026' in out and "2 26" not in out
    header_line = dt.listing_screen(p.read_bytes())[0]
    assert [c for c, _ in header_line[-4:]] == [0x32, 0x30, 0x32, 0x36]


def test_a_five_letter_id_keeps_its_middle(fixture):
    data = dt.load(fixture(DISK2))
    data, _ = dt.write_header(data, disk_id=b"KOELN")
    codes = [c for c, _ in dt.listing_screen(data)[0][-5:]]
    assert codes == [dt.petscii_to_screen(b)[0] for b in b"KOELN"]


# ---- bug 2: the directory runs down to blocks free --------------------------------

def test_listing_ends_with_blocks_free(fixture, run_tool):
    p = fixture(DISK2)
    assert dt.blocks_free(p.read_bytes()) == 664
    out = run_tool(p, "--replace", "00=01", "--dry-run", expect_ok=True).stdout
    assert "664 blocks free." in out
    last = dt.listing_screen(p.read_bytes())[-1]
    text = "".join(chr(c + 0x40) if c < 0x20 else chr(c) for c, _ in last)
    assert text == "664 BLOCKS FREE."
    assert dt.blocks_free(dt.load(fixture("examples.d64"))) == 662


# ---- bug 4: art is whatever owns no data, of any file type -------------------------

def test_zero_block_rows_of_any_type_are_art(fixture):
    data = dt.load(fixture(DISK2))
    rows = dt.directory(data)
    assert [r.kind for r in rows] == ["art"] * 79           # the four PRG rows included
    assert [rows[i].type_text for i in (50, 51, 63)] == ["del", "prg", "prg"]
    for type_byte, text in ((0x81, "seq"), (0x83, "usr"), (0x84, "rel"), (0x8F, "???"),
                            (0x02, "prg"), (0xC2, "prg")):
        changed = bytearray(data)
        changed[entry(data, 5) + 2] = type_byte
        row = dt.directory(bytes(changed))[5]
        assert (row.kind, row.type_text) == ("art", text)


def test_a_hidden_size_file_is_still_a_file(fixture):
    """0 blocks but a real start track: the size is faked, the file loads.
    Renaming it would break whatever loads it by name."""
    data = bytearray(dt.load(fixture(DISK2)))
    at = entry(bytes(data), 51)
    data[at + 3], data[at + 4] = 17, 0
    assert dt.directory(bytes(data))[51].kind == "file"
    data[at + 3], data[at + 4] = 18, 0             # at the directory track: art again
    assert dt.directory(bytes(data))[51].kind == "art"
    real = dt.directory(dt.load(fixture("examples.d64")))
    assert [r.kind for r in real][7:9] == ["file", "file"]      # one block each


# ---- the Mask ideas: any column, whole pieces --------------------------------------

def test_mask_any_column_takes_signs_from_everywhere(fixture):
    data = dt.load(fixture(DISK2))
    rows = dt.directory(data)
    pool = {b for i in range(1, 8) for b in rows[i].name}
    crossed = 0
    for seed in range(5):
        out, filled, left = dt.mask(data, ONE, range(28, 49), sources=range(1, 8),
                                    seed=seed, mode="any")
        assert (filled, left) == (255, 0)
        after = dt.directory(out)
        assert len({after[i].name for i in range(28, 49)}) == 21
        for i in range(28, 49):
            for col in range(16):
                if rows[i].name[col] == ONE:
                    assert after[i].name[col] in pool
                    crossed += after[i].name[col] not in {rows[s].name[col] for s in range(1, 8)}
                else:
                    assert after[i].name[col] == rows[i].name[col]
    assert crossed > 0                  # signs really did leave their own column


def test_mask_whole_pieces_keeps_a_stretch_of_one_line_together(fixture):
    data = dt.load(fixture(DISK2))
    rows = dt.directory(data)
    out, filled, left = dt.mask(data, ONE, range(28, 49), sources=range(1, 8),
                                seed=4, mode="piece")
    assert (filled, left) == (255, 0)
    after = dt.directory(out)
    assert len({after[i].name for i in range(28, 49)}) == 21
    for i in range(28, 49):
        before, name = rows[i].name, after[i].name
        col = 0
        while col < 16:
            if before[col] != ONE:
                assert name[col] == before[col]
                col += 1
                continue
            end = col
            while end < 16 and before[end] == ONE:
                end += 1
            # the whole run comes from the same columns of one single source line
            assert any(rows[s].name[col:end] == name[col:end] for s in range(1, 8))
            col = end


def test_mask_modes_are_checked(fixture, run_tool):
    data = dt.load(fixture(DISK2))
    with pytest.raises(dt.BadArgs):
        dt.mask(data, ONE, [28], sources=[1], mode="diagonal")
    p = fixture(DISK2)
    assert run_tool(p, "--mask-mode", "any", "--replace", "00=01").returncode == 2
    r = run_tool(p, "--mask", "1", "--mask-from", "1-7", "--mask-mode", "piece", "--seed", "4",
                 "--rows", "28-48", "--dry-run", expect_ok=True)
    assert "255 cells filled" in r.stdout


# ---- the tag: BUMSDIBUMS/DELYSID -----------------------------------------------------

def names(data, *rows):
    listing = dt.directory(data)
    return [dt.display_name(bytes(5) + listing[i].name) for i in rows]


def test_copy_set_and_change_a_tag(fixture):
    data = dt.load(fixture(DISK2))
    rows = dt.directory(data)
    assert names(data, 54, 55) == [" /BLZ           ", "  /BLZ          "]
    tag = dt.tag_at(rows[54].name, 1)                 # click the "/" of " /BLZ"
    assert tag == b"/BLZ"
    assert dt.name_text(rows[55].name) == (b"  /BLZ", 0xA0)

    # set: behind the name, only where it is not there yet and fits
    plain = bytearray(data)
    for i, text in ((0, b"FICKIFICKI"), (1, b"A VERY LONG NAME"), (2, b"HAS IT/BLZ")):
        at = rows[i].entry_offset + 5
        plain[at:at + 16] = text.ljust(16, b"\xa0")
    out, done, no_room = dt.set_tag(bytes(plain), [0, 1, 2], tag)
    assert (done, no_room) == (1, 1)
    assert dt.directory(out)[0].name == b"FICKIFICKI/BLZ\xa0\xa0"
    assert dt.directory(out)[1].name == b"A VERY LONG NAME"          # no room: untouched
    assert dt.directory(out)[2].name == b"HAS IT/BLZ" + b"\xa0" * 6  # had it already

    # change: wherever a line ends with the old tag
    out2, done, no_room = dt.change_tag(out, range(79), b"/BLZ", b"-TRIAD")
    # our two lines and ten of the twelve /BLZ lines; the last two stand at the
    # right edge, where a tag two signs longer does not fit
    assert (done, no_room) == (2 + 10, 2)
    assert dt.directory(out2)[0].name == b"FICKIFICKI-TRIAD"
    assert names(out2, 54, 63, 65) == [" -TRIAD         ", "          -TRIAD",
                                       "            /BLZ"]
    # nothing but name bytes of those lines moved
    allowed = {o for r in rows for o in range(r.entry_offset + 5, r.entry_offset + 21)}
    assert all(i in allowed for i in range(len(data)) if bytes(plain)[i] != out2[i])


def test_the_padding_of_a_line_is_kept(fixture):
    data = dt.load(fixture(DISK2))
    out, done, _ = dt.set_tag(data, [54, 55], b"!")
    listing = dt.directory(out)
    assert done == 2
    assert listing[54].name == b" /BLZ!" + b"\x20" * 10      # was padded with spaces
    assert listing[55].name == b"  /BLZ!" + b"\xa0" * 9      # was padded with $A0


def test_blanks_at_the_end_of_a_tag_are_not_part_of_it(fixture):
    """A tag typed as "/X " once got added again on every click: the blank
    looks like padding, so the line never seemed to end with the tag."""
    data = dt.load(fixture("examples.d64"))
    out = data
    for _ in range(3):
        out, done, _ = dt.set_tag(out, [9], b"/X " + bytes([0xA0]))
    assert dt.directory(out)[9].name == b"================"       # full line: no room, untouched
    out, done, _ = dt.set_tag(data, [2], b"/X  ")
    assert done == 1 and dt.directory(out)[2].name == b"     V1.0/X     "
    again, done, _ = dt.set_tag(out, [2], b"/X  ")
    assert done == 0 and again == out
    assert dt.parse_tag("/x  ") == b"/X"
    with pytest.raises(dt.BadArgs):
        dt.set_tag(data, [2], b"   ")                             # nothing but blanks


def test_tag_refusals(fixture):
    data = dt.load(fixture(DISK2))
    rows = dt.directory(data)
    for bad in (b"", b"X" * 17, None):
        with pytest.raises(dt.BadArgs):
            dt.set_tag(data, [54], bad)
    with pytest.raises(dt.BadArgs):
        dt.change_tag(data, [54], b"/BLZ", b"/BLZ")
    with pytest.raises(dt.BadArgs):
        dt.tag_at(rows[54].name, 9)                  # behind the end of the name
    with pytest.raises(dt.BadArgs):
        dt.set_tag(data, [99], b"X")
    assert dt.parse_tag("/delysid") == b"/DELYSID"
    assert dt.parse_tag("hex:2F44") == b"/D"
    for bad in ("", "hex:zz", "x" * 17, "é"):
        with pytest.raises(dt.BadArgs):
            dt.parse_tag(bad)


def test_cli_tags(fixture, run_tool, tmp_path):
    p = fixture(DISK2)
    before = p.read_bytes()
    out = tmp_path / "out.d64"
    r = run_tool(p, "--change-tag", "/blz=-triad", "-o", out, expect_ok=True)
    assert "tag changed in 10 lines, 2 with no room" in r.stdout
    assert p.read_bytes() == before
    assert names(out.read_bytes(), 54) == [" -TRIAD         "]
    r = run_tool(out, "--set-tag", "!", "--rows", "54,55", expect_ok=True)
    assert "tag set in 2 lines" in r.stdout
    assert names(out.read_bytes(), 54) == [" -TRIAD!        "]
    assert run_tool(p, "--change-tag", "nothing").returncode == 2
    # a real file is only renamed with --all-rows
    ex = fixture("examples.d64")
    assert run_tool(ex, "--set-tag", "/dj", "--rows", "7").returncode == 2
    run_tool(ex, "--set-tag", "/dj", "--rows", "7", "--all-rows", expect_ok=True)
    assert dt.directory(ex.read_bytes())[7].load_name == "DEMO/DJ"


# ---- Extras: the directory as a picture -----------------------------------------------

def png_size(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[12:16] == b"IHDR"
    return struct.unpack(">II", png[16:24])


def test_picture_is_a_real_png_of_the_whole_listing(fixture):
    data = dt.load(fixture(DISK2))
    png = dt.picture_png(data, scale=2)
    lines = 1 + 79 + 1                                  # header, rows, blocks free
    assert png_size(png) == (40 * 16 + 32, lines * 16 + 32)
    # every chunk carries a correct checksum, and the pixels unpack to the full size
    at, body = 8, b""
    while at < len(png):
        size = struct.unpack(">I", png[at:at + 4])[0]
        kind, chunk = png[at + 4:at + 8], png[at + 8:at + 8 + size]
        assert struct.unpack(">I", png[at + 8 + size:at + 12 + size])[0] == zlib.crc32(kind + chunk)
        if kind == b"IDAT":
            body += chunk
        at += 12 + size
    width, height = png_size(png)
    raw = zlib.decompress(body)
    assert len(raw) == height * (1 + width * 3)
    paper, ink = bytes.fromhex("40318d"), bytes.fromhex("867ade")
    assert {raw[i:i + 3] for i in range(1, 1 + width * 3, 3)} == {paper}      # top margin
    pixels = {raw[row * (1 + width * 3) + 1 + x * 3:][:3]
              for row in range(16, 32) for x in range(width)}
    assert pixels == {paper, ink}
    other = dt.picture_png(data, ink="#ffffff", paper="#000000", scale=1)
    assert png_size(other) == (40 * 8 + 16, lines * 8 + 16)


def test_cli_picture_alone_leaves_the_disk_alone(fixture, run_tool, tmp_path):
    p = fixture(DISK2)
    before, stamp = p.read_bytes(), p.stat().st_mtime_ns
    shot = tmp_path / "dir.png"
    r = run_tool(p, "--picture", shot, expect_ok=True)
    assert png_size(shot.read_bytes())[0] == 672
    assert p.read_bytes() == before and p.stat().st_mtime_ns == stamp
    assert "DONE" not in r.stdout
    assert run_tool(p, "--picture", tmp_path / "no" / "such" / "x.png").returncode == 4


# ---- the window ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def master():
    import tkinter
    try:
        root = tkinter.Tk()
    except tkinter.TclError as exc:
        pytest.skip(f"no Tk display: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(master):
    a = dt.App(master=master)
    yield a
    a.root.destroy()


def cell(app, row, index):
    """Put the pointer on one sign of the directory, as a click would find it."""
    c = app.dir_canvas
    c.dtag("current", "current")
    item = next(i for i in c.find_withtag(f"r{row}") if f"i{index}" in c.gettags(i))
    c.addtag("current", "withtag", item)


def test_window_shows_the_id_and_blocks_free(app, fixture):
    app.open_path(str(fixture(DISK2)))
    c = app.dir_canvas
    assert len(c.find_withtag("hdr")) == 1 + 16 + 1 + 5       # quote, name, quote, five ID signs
    assert len(c.find_withtag("free")) == len("BLOCKS FREE.")
    texts = [c.itemcget(i, "text") for i in c.find_all() if c.type(i) == "text"]
    assert "664" in texts
    # the scroll region reaches past the blocks-free line
    assert float(c.cget("scrollregion").split()[3]) >= app._y(79 + 2)


def test_window_fits_a_1024_by_768_screen(app):
    app.root.update_idletasks()
    assert app.root.winfo_reqwidth() <= 1010
    assert app.root.winfo_reqheight() <= 700                  # leaves room for title and task bar


def test_every_line_of_the_disk_starts_ticked(app, fixture):
    """Bug 4: his PRG lines were left unticked at start."""
    app.open_path(str(fixture(DISK2)))
    assert all(v.get() for v in app.row_vars)
    app.open_path(str(fixture("examples.d64")))
    assert [v.get() for v in app.row_vars][7:9] == [False, False]     # DEMO and README


def test_undo_goes_grey_when_everything_is_undone(app, fixture):
    """Bug 3: Undo could be pressed again and again."""
    app.open_path(str(fixture("baracuda-dir-file.d64")))

    def states():
        return (str(app.undo_btn["state"]), app.editmenu.entrycget("Undo", "state"),
                str(app.redo_btn["state"]), app.editmenu.entrycget("Redo", "state"))

    assert states() == ("disabled", "disabled", "disabled", "disabled")
    app.set_from(0xD6)
    app.set_to(0xD1)
    app.apply()
    assert states() == ("normal", "normal", "disabled", "disabled")
    assert app.undo()
    assert states() == ("disabled", "disabled", "normal", "normal")
    assert "nothing more to undo" in app.status.cget("text")
    assert not app.undo()                                     # and it says so
    assert app.status.cget("text") == "nothing to undo"


def test_redo_brings_an_undone_step_back(app, fixture):
    app.open_path(str(fixture("baracuda-dir-file.d64")))
    app.set_from(0xD6)
    app.set_to(0xD1)
    app.apply()
    first = app.image
    app.set_from(0xD7)
    app.apply()
    second = app.image
    app.undo()
    app.undo()
    assert app.image == app.on_disk and not app.dirty
    assert app.redo() and app.image == first
    assert app.redo() and app.image == second
    assert not app.redo()
    app.undo()
    app.set_from(0xD1)
    app.set_to(0x2A)
    app.apply()                                               # a new step: Redo starts over
    assert not app.redo_stack and not app.redo()


def test_undo_takes_the_mix_number_back(app, fixture):
    """Bug 5."""
    app.open_path(str(fixture(DISK2)))
    app.set_mask(ONE)
    app.mask_sources.set("1-7")
    app.mask_seed.set("5")
    app.apply_mask()
    app.apply_mask()
    app.apply_mask()
    third = app.image
    assert app.mask_seed.get() == "7"
    app.undo()
    assert app.mask_seed.get() == "5" and app.image == app.on_disk
    app.redo()
    assert app.mask_seed.get() == "7" and app.image == third
    assert app.mask_btn["text"] == "Mix again"
    app.apply_mask()
    assert app.mask_seed.get() == "8" and len(app.undo_stack) == 1


def test_tick_all_and_untick_all(app, fixture):
    app.open_path(str(fixture("examples.d64")))
    assert app.untick_all() and not any(v.get() for v in app.row_vars)
    assert str(app.apply_btn["state"]) == "disabled"
    assert app.tick_all() and all(v.get() for v in app.row_vars)

    class Key:
        def __init__(self, widget):
            self.widget = widget

    app._plain_key(Key(app.dir_canvas), app.untick_all)        # U pressed over the window
    assert not any(v.get() for v in app.row_vars)
    box = app.ttk.Entry(app.root)
    app._plain_key(Key(box), app.tick_all)                     # T typed into a box: nothing
    assert not any(v.get() for v in app.row_vars)
    for key in ("t", "T", "u", "U"):
        assert app.root.bind(f"<Key-{key}>")


def test_colours_from_the_extras_menu(app, fixture):
    app.open_path(str(fixture(DISK2)))
    assert len(dt.C64_COLOURS) == 16
    assert app.set_colours(paper="#000000", ink="#ffffff")
    assert (app.paper, app.ink) == ("#000000", "#ffffff")
    assert app.dir_canvas.cget("bg") == app.pal_canvas.cget("bg") == "#000000"
    assert len(app.dir_canvas.find_withtag("cell")) == 79 * 16        # redrawn, all there
    assert len(app.pal_canvas.find_withtag("pal")) == 256
    assert not app.set_colours(paper="#ffffff")                       # same as the letters
    assert app.paper == "#000000"
    assert app.set_colours(ink=dt.LIGHT_BLUE) and app.paper == "#000000"


def test_picture_from_the_window_uses_the_colours_on_screen(app, fixture, tmp_path):
    app.open_path(str(fixture(DISK2)))
    app.set_colours(paper="#000000", ink="#ffffff")
    shot = tmp_path / "shot.png"
    assert app.save_picture(str(shot))
    assert shot.read_bytes() == dt.picture_png(app.image, ink="#ffffff", paper="#000000")
    assert "picture saved" in app.status.cget("text")


def test_mouse_wheel_scrolls_the_directory(app, fixture):
    app.open_path(str(fixture(DISK2)))
    app.root.update()

    class Wheel:
        num = 0

        def __init__(self, widget, delta):
            self.x_root = widget.winfo_rootx() + 30
            self.y_root = widget.winfo_rooty() + 30
            self.delta = delta

    if app.root.winfo_containing(app.dir_canvas.winfo_rootx() + 30,
                                 app.dir_canvas.winfo_rooty() + 30) is None:
        pytest.skip("the test window is covered by another one")
    top = app.dir_canvas.yview()[0]
    assert app._wheel(Wheel(app.dir_canvas, -120)) == "break"
    assert app.dir_canvas.yview()[0] > top
    app._wheel(Wheel(app.dir_canvas, 120))
    assert app.dir_canvas.yview()[0] == top
    assert app._wheel(Wheel(app.pal_canvas, -120)) is None      # not over the directory
    assert app.root.bind("<MouseWheel>")


def test_mask_mode_from_the_window(app, fixture):
    app.open_path(str(fixture(DISK2)))
    app.set_mask(ONE)
    app.mask_sources.set("1-7")
    for i, var in enumerate(app.row_vars):
        var.set(28 <= i <= 48)
    app.mask_mode.set("piece")
    assert app.apply_mask() == 255
    want, _, _ = dt.mask(app.on_disk, ONE, range(28, 49), sources=range(1, 8),
                         seed=1, mode="piece")
    assert app.image == want


def test_tag_tab_copy_set_change(app, fixture, monkeypatch):
    import tkinter.messagebox as mb
    asked = []
    monkeypatch.setattr(mb, "askokcancel", lambda *a, **k: asked.append(a[1]) or True)
    app.open_path(str(fixture("examples.d64")))
    app.notebook.select(2)
    assert str(app.set_tag_btn["state"]) == "disabled"
    cell(app, 10, 6)                                   # the "/" of "  DR.J/DELYSID"
    app._pick(None, app.dir_canvas, "from")
    assert app.tag == b"/DELYSID" and app.tag_text.get() == "/DELYSID"
    assert app.src is None and app.mask_char is None   # the other tabs are not touched
    assert len(app.tag_strip.find_all()) == 8

    for i, var in enumerate(app.row_vars):
        var.set(i == 7)                                # the real file DEMO
    assert app.apply_set_tag() == 1
    assert asked and "DEMO" in asked[0]                # renaming a file asks first
    assert app.rows[7].load_name == "DEMO/DELYSID" and app.dirty

    app.new_tag_text.set("-triad")                     # typed, lower case
    assert app.new_tag == b"-TRIAD" and str(app.change_tag_btn["state"]) == "normal"
    app.tick_all()
    assert app.apply_change_tag() == 2                 # DEMO and the DR.J line
    assert app.rows[7].load_name == "DEMO-TRIAD"
    assert app.rows[10].name == b"  DR.J-TRIAD" + b"\x20" * 4
    assert app.tag == b"-TRIAD"                        # the lines carry the new one now
    assert "changed in 2 lines" in app.tag_label.cget("text")
    assert app.undo() and app.undo() and not app.dirty

    cell(app, 4, 9)                                    # right click: new tag from a line
    app._pick(None, app.dir_canvas, "to")
    assert app.new_tag == b"FROM A"
    app.tag_text.set("")
    assert app.tag is None and str(app.set_tag_btn["state"]) == "disabled"


def test_tag_with_no_room_is_counted_not_forced(app, fixture):
    app.open_path(str(fixture("examples.d64")))
    app.tag_text.set("/a very long one")
    app.untick_all()
    app.row_vars[4].set(True)                          # " DIR ART FROM A ": 15 signs used
    assert app.apply_set_tag() == 0
    assert "1 have no room" in app.tag_label.cget("text")
    assert not app.dirty and not app.undo_stack
