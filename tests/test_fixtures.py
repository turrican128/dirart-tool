"""The fixture disks are what the spec says they are."""
import d64reader


def test_baracuda_disk_is_as_the_spec_says(fixture):
    rows = d64reader.read_directory(fixture("baracuda-dir-file.d64"))
    assert [e.name_exact for e in rows] == [
        b"\xd1\xd1  BARACUDA  \xd1\xd1",
        b"\xd1\xd6  BARACUDA  \xd6\xd1",
        b"\xd1\xd7  BARACUDA  \xd7\xd1",
    ]
    assert all(e.is_art for e in rows)


def test_examples_disk_has_art_and_files(fixture):
    path = fixture("examples.d64")
    rows = d64reader.read_directory(path)
    assert len(rows) == 12          # 10 art rows + DEMO + README, in two directory sectors
    assert sorted(e.name for e in rows if not e.is_art) == ["DEMO", "README"]
    demo = next(e for e in rows if e.name == "DEMO")
    assert len(d64reader.read_file(path, demo)) > 0
