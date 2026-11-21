"""Header and ID carried from one disk to another, and two 1.0.0 write bugs."""
import os
import stat
import subprocess
import sys

import pytest

import dirarttool as dt
from conftest import TOOL


def bam(data):
    at = dt.offset(18, 0)
    return data[at:at + 256]


def test_header_and_id_go_over_and_nothing_else(fixture):
    src = dt.load(fixture("baracuda-mask-disk.d64"))
    dst = dt.load(fixture("baracuda-dir-file.d64"))
    assert dt.read_header(src) != dt.read_header(dst)

    out, changed = dt.write_header(dst, dt.read_header(src), dt.read_id(src))
    assert dt.read_header(out) == dt.read_header(src)
    assert dt.read_id(out) == dt.read_id(src)
    at = dt.offset(18, 0)
    allowed = set(range(at + 0x90, at + 0xA0)) | set(range(at + 0xA2, at + 0xA7))
    diff = {i for i in range(len(dst)) if dst[i] != out[i]}
    assert diff and diff <= allowed and changed == len(diff)
    assert bam(out)[:0x90] == bam(dst)[:0x90]        # the block map is not touched
    assert [r.name for r in dt.directory(out)] == [r.name for r in dt.directory(dst)]


def test_header_alone_and_id_alone(fixture):
    src = dt.load(fixture("baracuda-mask-disk.d64"))
    dst = dt.load(fixture("baracuda-dir-file.d64"))
    only_header, _ = dt.write_header(dst, name=dt.read_header(src))
    assert dt.read_header(only_header) == dt.read_header(src)
    assert dt.read_id(only_header) == dt.read_id(dst)
    only_id, _ = dt.write_header(dst, disk_id=dt.read_id(src))
    assert dt.read_id(only_id) == dt.read_id(src)
    assert dt.read_header(only_id) == dt.read_header(dst)
    same, changed = dt.write_header(dst)
    assert same == dst and changed == 0


def test_header_refusals(fixture):
    dst = dt.load(fixture("baracuda-dir-file.d64"))
    with pytest.raises(dt.BadArgs):
        dt.write_header(dst, name=b"SHORT")
    with pytest.raises(dt.BadArgs):
        dt.write_header(dst, disk_id=b"2A")
    with pytest.raises(dt.BadImage):
        dt.read_header(b"not a disk")


def test_cli_header_from_and_id_from(fixture, run_tool, tmp_path):
    src = fixture("baracuda-mask-disk.d64")
    dst = fixture("baracuda-dir-file.d64")
    before = dst.read_bytes()
    out = tmp_path / "out.d64"

    r = run_tool(dst, "--header-from", src, "--dry-run", expect_ok=True)
    assert dst.read_bytes() == before and "header taken over" in r.stdout

    run_tool(dst, "--header-from", src, "-o", out, expect_ok=True)
    assert dst.read_bytes() == before
    assert dt.read_header(out.read_bytes()) == dt.read_header(src.read_bytes())
    assert dt.read_id(out.read_bytes()) == dt.read_id(before)

    run_tool(dst, "--header-from", src, "--id-from", src, expect_ok=True)     # option 3, in place
    now = dst.read_bytes()
    assert dt.read_header(now) == dt.read_header(src.read_bytes())
    assert dt.read_id(now) == dt.read_id(src.read_bytes())
    assert [r.name for r in dt.directory(now)] == [r.name for r in dt.directory(before)]


def test_cli_header_from_a_bad_disk_is_refused(fixture, run_tool, tmp_path):
    dst = fixture("baracuda-dir-file.d64")
    before = dst.read_bytes()
    junk = tmp_path / "junk.d64"
    junk.write_bytes(b"x" * 1000)
    assert run_tool(dst, "--header-from", junk, expect_ok=False).returncode == 3
    assert run_tool(dst, "--id-from", tmp_path / "missing.d64", expect_ok=False).returncode == 3
    assert dst.read_bytes() == before


# ---- two bugs the 1.0.0 QA found ---------------------------------------------

def test_refused_write_to_a_read_only_disk_leaves_no_temp_file(fixture, run_tool):
    p = fixture("baracuda-dir-file.d64")
    before = p.read_bytes()
    os.chmod(p, stat.S_IREAD)
    try:
        r = run_tool(p, "--replace", "D6=D1")
        if r.returncode == 0:
            pytest.skip("this system lets a read-only file be replaced")
        assert r.returncode == 4
        assert p.read_bytes() == before
        assert [f.name for f in p.parent.iterdir()] == [p.name]
    finally:
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


def test_a_name_the_code_page_cannot_print_does_not_fail_the_run(fixture):
    """The disk is written before the DONE line; a file name the output's
    code page lacks must not turn a finished write into exit 1."""
    name = "ディスク.d64"
    try:
        p = fixture("baracuda-dir-file.d64", name=name)
    except (OSError, UnicodeError):
        pytest.skip("this file system cannot hold the name")
    before = p.read_bytes()
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    r = subprocess.run([sys.executable, str(TOOL), str(p), "--replace", "D6=D1"],
                       capture_output=True, env=env)
    assert r.returncode == 0, r.stderr.decode("latin1")
    assert p.read_bytes() != before
    assert b"DONE" in r.stdout
