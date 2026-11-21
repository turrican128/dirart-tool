"""The Mask: placeholder cells filled from the same column of chosen source lines."""
import pytest

import d64reader
import dirarttool as dt

ONE = 0x31          # the "1" Baracuda's stamp uses as its placeholder
TEXTURE = [1, 2, 3, 4, 5, 6, 7]     # "the other lines (line 2 f.e.)": the finished art above


def load_mask_disk(fixture):
    return dt.load(fixture("baracuda-mask-disk.d64"))


def stamp_rows(data):
    """The art rows that still hold the placeholder, in listing order."""
    return [r.index for r in dt.directory(data) if r.kind == "art" and ONE in r.name]


def test_acceptance_baracuda_mask(fixture):
    """Baracuda's second test disk: every 1 of the stamp looks like the lines
    above it, mixed, and no two lines come out the same."""
    data = load_mask_disk(fixture)
    rows = dt.directory(data)
    targets = stamp_rows(data)
    assert targets == list(range(28, 49))
    out, filled, left = dt.mask(data, ONE, targets, sources=TEXTURE, seed=7)
    assert left == 0
    assert filled == sum(r.name.count(ONE) for r in rows if r.index in targets) == 255

    after = dt.directory(out)
    for r, a in zip(rows, after):
        if r.index in targets:
            for col in range(16):
                if r.name[col] == ONE:
                    # every filled sign is one a source line shows in that column
                    assert a.name[col] in {rows[s].name[col] for s in TEXTURE}
                else:
                    assert a.name[col] == r.name[col]     # the stamp's own signs stay
        else:
            assert a.name == r.name          # the real files keep their "1"s too

    assert len({after[i].name for i in targets}) == len(targets)

    # only name bytes of the target rows changed anywhere in the image
    allowed = {o for r in rows if r.index in targets
               for o in range(r.entry_offset + 5, r.entry_offset + 21)}
    assert all(i in allowed for i in range(len(data)) if data[i] != out[i])


def test_nothing_comes_from_lines_that_were_not_chosen(fixture):
    """The disk also holds separators, /BLZ text and test rows. With lines 1-7
    as the source, no letter, digit or space can land in the stamp."""
    data = load_mask_disk(fixture)
    targets = stamp_rows(data)
    before = dt.directory(data)
    for seed in range(40):
        out, _, _ = dt.mask(data, ONE, targets, sources=TEXTURE, seed=seed)
        for r in dt.directory(out):
            if r.index in targets:
                new = [b for col, b in enumerate(r.name) if before[r.index].name[col] == ONE]
                assert not any(b == 0x20 or 0x30 <= b <= 0x39 or 0x41 <= b <= 0x5A for b in new)


def test_every_line_looks_different_for_every_seed(fixture):
    data = load_mask_disk(fixture)
    targets = stamp_rows(data)
    for seed in range(200):
        out, _, _ = dt.mask(data, ONE, targets, sources=TEXTURE, seed=seed)
        after = dt.directory(out)
        assert len({after[i].name for i in targets}) == len(targets)


def test_equal_lines_are_mixed_again(fixture):
    """Two source lines and one free cell give only two possible lines. Two
    targets must get one each, for every seed, rather than the same one twice
    half of the time."""
    data = bytearray(dt.load(fixture("examples.d64")))
    rows = dt.directory(bytes(data))
    for i, name in ((0, b"A" * 16), (1, b"B" * 16),
                    (2, b"1" + b"C" * 15), (3, b"1" + b"C" * 15)):
        at = rows[i].entry_offset + 5
        data[at:at + 16] = name
    for seed in range(60):
        out, filled, left = dt.mask(bytes(data), ONE, [2, 3], sources=[0, 1], seed=seed)
        after = dt.directory(out)
        assert filled == 2 and left == 0
        assert {after[2].name[0], after[3].name[0]} == {0x41, 0x42}


def test_seed_makes_the_mix_reproducible(fixture):
    data = load_mask_disk(fixture)
    targets = stamp_rows(data)
    a = dt.mask(data, ONE, targets, sources=TEXTURE, seed=3)[0]
    b = dt.mask(data, ONE, targets, sources=TEXTURE, seed=3)[0]
    c = dt.mask(data, ONE, targets, sources=TEXTURE, seed=4)[0]
    assert a == b
    assert a != c


def test_one_source_row_copies_that_row_column_by_column(fixture):
    data = load_mask_disk(fixture)
    top = dt.directory(data)[0].name                      # "-=............=-"
    before = dt.directory(data)[28].name                  # "11..111111111111"
    out, filled, left = dt.mask(data, ONE, [28], sources=[0])
    assert left == 0 and filled == before.count(ONE)
    result = dt.directory(out)[28].name
    for col in range(16):
        if before[col] == ONE:
            assert result[col] == top[col]                # the only candidate there
        else:
            assert result[col] == before[col]


def test_a_column_with_nothing_to_take_is_left(fixture):
    """A row as its own only source: its placeholders stay, and are counted."""
    data = load_mask_disk(fixture)
    out, filled, left = dt.mask(data, ONE, [28], sources=[28])
    assert filled == 0
    assert left == dt.directory(data)[28].name.count(ONE)
    assert out == data


def test_placeholder_and_pad_are_never_taken(fixture):
    """Sources never hand out the placeholder itself, $A0 or $00."""
    data = load_mask_disk(fixture)
    targets = stamp_rows(data)
    # rows 55-62 are padded with $A0, row 51 holds the placeholder
    out, _, _ = dt.mask(data, ONE, targets, sources=[51, 55, 56, 57, 58, 59, 60, 61, 62],
                        seed=11)
    for r in dt.directory(out):
        if r.index in targets:
            assert 0xA0 not in r.name and 0x00 not in r.name


def test_mask_refusals(fixture):
    data = load_mask_disk(fixture)
    with pytest.raises(dt.BadArgs) as exc:
        dt.mask(data, ONE, [99], sources=[1])
    assert "0..78" in str(exc.value)
    with pytest.raises(dt.BadArgs) as exc:
        dt.mask(data, ONE, [28], sources=[99])
    assert "source row 99" in str(exc.value)
    for nothing in (None, [], ()):
        with pytest.raises(dt.BadArgs) as exc:
            dt.mask(data, ONE, [28], sources=nothing)
        assert "source lines" in str(exc.value)


def test_single_target_keeps_every_other_byte(fixture):
    data = load_mask_disk(fixture)
    row = dt.directory(data)[28]
    out, filled, left = dt.mask(data, ONE, [28], sources=[1], seed=7)
    assert filled == 14 and left == 0
    allowed = {row.entry_offset + 5 + j for j, b in enumerate(row.name) if b == ONE}
    assert {j for j, (a, b) in enumerate(zip(data, out)) if a != b} == allowed


# ---- row lists ---------------------------------------------------------------

def test_row_lists_take_ranges():
    assert dt.parse_rows("1-7") == [1, 2, 3, 4, 5, 6, 7]
    assert dt.parse_rows("0, 3-5 ,9") == [0, 3, 4, 5, 9]
    assert dt.format_rows([7, 1, 2, 3, 9, 10]) == "1-3,7,9-10"
    assert dt.format_rows([]) == ""
    for bad in ("", ",", " ", "a", "1-", "-1", "7-1", "1-99999", "1--3"):
        with pytest.raises(ValueError):
            dt.parse_rows(bad)


# ---- command line --------------------------------------------------------------

def test_cli_mask_dry_run_writes_nothing(fixture, run_tool):
    p = fixture("baracuda-mask-disk.d64")
    before = p.read_bytes()
    r = run_tool(p, "--mask", "1", "--mask-from", "1-7", "--seed", "7", "--dry-run",
                 expect_ok=True)
    assert p.read_bytes() == before
    assert "255 cells filled" in r.stdout and "dry run" in r.stdout


def test_cli_mask_needs_its_source_lines(fixture, run_tool):
    p = fixture("baracuda-mask-disk.d64")
    before = p.read_bytes()
    r = run_tool(p, "--mask", "1", expect_ok=False)
    assert r.returncode == 2 and "--mask-from" in r.stderr
    assert p.read_bytes() == before


def test_cli_mask_in_place_and_to_a_copy_agree(fixture, run_tool, tmp_path):
    p = fixture("baracuda-mask-disk.d64")
    before = p.read_bytes()
    out = tmp_path / "out.d64"
    run_tool(p, "--mask", "31", "--mask-from", "1-7", "--seed", "7", "-o", out, expect_ok=True)
    assert p.read_bytes() == before                         # -o leaves the input alone
    for src, dst in zip(d64reader.read_directory(p), d64reader.read_directory(out)):
        if not src.is_art:
            assert dst.raw == src.raw                        # PRG rows keep their "1"s
        else:
            assert ONE not in dst.name_raw
    run_tool(p, "--mask", "31", "--mask-from", "1-7", "--seed", "7", expect_ok=True)
    assert p.read_bytes() == out.read_bytes()                # in place, like the Replacer


def test_cli_quiet_mask_prints_nothing(fixture, run_tool, tmp_path):
    p = fixture("baracuda-mask-disk.d64")
    r = run_tool(p, "--mask", "1", "--mask-from", "1-7", "-o", tmp_path / "o.d64", "-q",
                 expect_ok=True)
    assert r.stdout == ""


def test_cli_mask_is_reproducible_with_a_seed(fixture, run_tool, tmp_path):
    p = fixture("baracuda-mask-disk.d64")
    common = (p, "--mask", "1", "--mask-from", "1-7")
    run_tool(*common, "--seed", "7", "-o", tmp_path / "a.d64", expect_ok=True)
    run_tool(*common, "--seed", "7", "-o", tmp_path / "b.d64", expect_ok=True)
    run_tool(*common, "--seed", "8", "-o", tmp_path / "c.d64", expect_ok=True)
    assert (tmp_path / "a.d64").read_bytes() == (tmp_path / "b.d64").read_bytes()
    assert (tmp_path / "a.d64").read_bytes() != (tmp_path / "c.d64").read_bytes()


def test_cli_mask_options_need_mask(fixture, run_tool):
    p = fixture("baracuda-mask-disk.d64")
    r = run_tool(p, "--seed", "7", expect_ok=False)
    assert r.returncode == 2 and "--mask" in r.stderr
    r = run_tool(p, "--mask-from", "1,2", expect_ok=False)
    assert r.returncode == 2 and "--mask" in r.stderr


def test_cli_nothing_to_do_without_a_job(fixture, run_tool):
    p = fixture("baracuda-mask-disk.d64")
    r = run_tool(p, "--rows", "1", expect_ok=False)     # a disk alone opens the window
    assert r.returncode == 2 and "nothing to do" in r.stderr


def test_cli_empty_row_lists_are_refused(fixture, run_tool):
    """An empty variable in a build script must not pass as 'no rows, all fine'."""
    p = fixture("baracuda-mask-disk.d64")
    before = p.read_bytes()
    for rows in ("", ",", " "):
        r = run_tool(p, "--replace", "2D=5F", "--rows", rows, expect_ok=False)
        assert r.returncode == 2
        r = run_tool(p, "--mask", "1", "--mask-from", rows, expect_ok=False)
        assert r.returncode == 2
    assert p.read_bytes() == before


def test_cli_file_rows_still_need_consent(fixture, run_tool):
    p = fixture("baracuda-mask-disk.d64")
    before = p.read_bytes()
    idx = next(i for i, e in enumerate(d64reader.read_directory(p))
               if not e.is_art and ONE in e.name_raw)
    r = run_tool(p, "--mask", "1", "--mask-from", "1-7", "--rows", str(idx), "--dry-run",
                 expect_ok=False)
    assert r.returncode == 2 and "--all-rows" in r.stderr
    assert p.read_bytes() == before


def test_cli_replace_then_mask_in_one_run(fixture, run_tool, tmp_path):
    p = fixture("baracuda-mask-disk.d64")
    out = tmp_path / "out.d64"
    run_tool(p, "--replace", "2D=5F", "--mask", "1", "--mask-from", "1-7", "--seed", "7",
             "-o", out, expect_ok=True)
    rows = d64reader.read_directory(out)
    assert rows[0].name_raw[:2] == b"\x5f\x3d"              # the replace landed
    assert all(ONE not in e.name_raw for e in rows[:51] if e.is_art)
