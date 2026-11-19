"""The engine: disk core from txt2dirart, directory(), replace(), save()."""
import pytest

import d64reader
import dirarttool as dt


def data_row(data, i):
    return dt.directory(data)[i].name


def test_acceptance_baracuda(fixture):
    data = dt.load(fixture("baracuda-dir-file.d64"))
    out, n = dt.replace(data, {0xD6: 0xD1, 0xD7: 0xD1}, [0, 1, 2])
    rows = [r.name for r in dt.directory(out)]
    assert rows == [b"\xd1\xd1  BARACUDA  \xd1\xd1"] * 3
    assert n == 4
    changed = [i for i in range(len(data)) if data[i] != out[i]]
    starts = [r.entry_offset + 5 for r in dt.directory(data)]
    assert changed == [starts[1] + 1, starts[1] + 14, starts[2] + 1, starts[2] + 14]


def test_only_selected_rows_change(fixture):
    data = dt.load(fixture("baracuda-dir-file.d64"))
    out, n = dt.replace(data, {0xD6: 0xD1}, [2])          # row 2 has no D6
    assert n == 0 and out == data
    out, n = dt.replace(data, {0xD6: 0xD1}, [1])
    assert n == 2 and data_row(out, 2) == data_row(data, 2)
    assert data_row(out, 1) == b"\xd1\xd1  BARACUDA  \xd1\xd1"


def test_mapping_is_single_pass_swap(fixture):
    data = dt.load(fixture("baracuda-dir-file.d64"))
    out, n = dt.replace(data, {0xD6: 0xD7, 0xD7: 0xD6}, [1, 2])
    assert data_row(out, 1) == b"\xd1\xd7  BARACUDA  \xd7\xd1"
    assert data_row(out, 2) == b"\xd1\xd6  BARACUDA  \xd6\xd1"
    assert n == 4


def test_files_untouched_on_examples(fixture):
    path = fixture("examples.d64")
    data = dt.load(path)
    rows = dt.directory(data)
    art = [r.index for r in rows if r.kind == "art"]
    assert max(art) >= 8                                    # second directory sector
    assert [r.kind for r in rows].count("file") == 2
    out, n = dt.replace(data, {0x20: 0x2D}, art)            # space -> minus on art rows
    assert n > 0
    before = d64reader.read_directory(data)
    after = d64reader.read_directory(out)
    for b, a in zip(before, after):
        if not b.is_art:
            assert b.raw == a.raw
            assert d64reader.read_file(data, b) == d64reader.read_file(out, a)
    allowed = {o for r in rows if r.kind == "art"
               for o in range(r.entry_offset + 5, r.entry_offset + 21)}
    assert all(i in allowed for i in range(len(data)) if data[i] != out[i])


def test_error_info_block_preserved(fixture):
    raw = fixture("baracuda-dir-file.d64").read_bytes() + bytes(range(256)) * 2 + bytes(171)
    assert len(raw) == 175531
    assert dt.track_count(raw) == 35
    out, n = dt.replace(raw, {0xD6: 0xD1}, [1])
    assert n == 2
    assert out[174848:] == raw[174848:]


def test_header_is_read_for_display(fixture):
    data = dt.load(fixture("examples.d64"))
    name, disk_id, dos = dt.header(data)
    assert len(name) == 16 and len(disk_id) == 2 and dos == b"2A"


def test_refusals(fixture, tmp_path):
    with pytest.raises(dt.BadImage):
        dt.load(str(tmp_path / "missing.d64"))
    (tmp_path / "short.d64").write_bytes(b"\0" * 1000)
    with pytest.raises(dt.BadImage):
        dt.load(str(tmp_path / "short.d64"))
    data = bytearray(dt.load(fixture("baracuda-dir-file.d64")))
    data[dt.offset(18, 1)] = 17
    data[dt.offset(18, 1) + 1] = 0                           # chain leaves track 18
    with pytest.raises(dt.BadImage):
        dt.directory(bytes(data))
    good = dt.load(fixture("baracuda-dir-file.d64"))
    with pytest.raises(dt.BadArgs) as exc:
        dt.replace(good, {0xD6: 0xD1}, [7])
    assert "0..2" in str(exc.value)


def test_exit_codes_on_exceptions():
    assert dt.BadArgs("x").code == 2
    assert dt.BadImage("x").code == 3
    assert dt.CannotWrite("x").code == 4
    assert issubclass(dt.BadArgs, dt.DirArtError)


def test_save_is_atomic_and_refuses_bad_paths(fixture, tmp_path):
    p = fixture("baracuda-dir-file.d64")
    dt.save(str(p), b"\0" * 174848)
    assert p.read_bytes() == b"\0" * 174848
    assert not [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
    with pytest.raises(dt.CannotWrite):
        dt.save(str(tmp_path / "nope" / "x.d64"), b"\0" * 174848)
    assert not [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
