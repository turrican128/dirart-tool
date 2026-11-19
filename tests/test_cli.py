"""The command line, driven as a real subprocess."""
import d64reader


def names(path):
    return [e.name_exact for e in d64reader.read_directory(path)]


def test_replace_in_place(fixture, run_tool):
    p = fixture("baracuda-dir-file.d64")
    r = run_tool(p, "--replace", "D6=D1", "--replace", "d7=d1", expect_ok=True)
    assert names(p) == [b"\xd1\xd1  BARACUDA  \xd1\xd1"] * 3
    assert "4 cells changed" in r.stdout


def test_output_file_leaves_input_alone(fixture, run_tool, tmp_path):
    p = fixture("baracuda-dir-file.d64")
    before = p.read_bytes()
    run_tool(p, "--replace", "D6=D1", "-o", tmp_path / "out.d64", expect_ok=True)
    assert p.read_bytes() == before
    assert (tmp_path / "out.d64").exists()
    assert names(tmp_path / "out.d64")[1] == b"\xd1\xd1  BARACUDA  \xd1\xd1"


def test_dry_run_writes_nothing_and_lists(fixture, run_tool):
    p = fixture("baracuda-dir-file.d64")
    before = p.read_bytes()
    r = run_tool(p, "--replace", "D6=D1", "--dry-run", expect_ok=True)
    assert p.read_bytes() == before
    assert '"' in r.stdout and "del" in r.stdout and "dry run" in r.stdout
    assert "2 cells changed" in r.stdout


def test_rows_subset_and_range(fixture, run_tool):
    p = fixture("baracuda-dir-file.d64")
    run_tool(p, "--replace", "D6=D1", "--rows", "1", expect_ok=True)
    assert names(p)[2].count(b"\xd7") == 2
    r = run_tool(p, "--replace", "D6=D1", "--rows", "7", expect_ok=False)
    assert r.returncode == 2 and "0..2" in r.stderr


def test_file_rows_need_consent(fixture, run_tool):
    p = fixture("examples.d64")
    idx = next(i for i, e in enumerate(d64reader.read_directory(p)) if e.name == "DEMO")
    r = run_tool(p, "--replace", "D=X", "--rows", str(idx), expect_ok=False)
    assert r.returncode == 2 and "DEMO" in r.stderr and "--all-rows" in r.stderr
    assert d64reader.read_directory(p)[idx].name == "DEMO"
    run_tool(p, "--replace", "D=X", "--rows", str(idx), "--all-rows", expect_ok=True)
    assert d64reader.read_directory(p)[idx].name.startswith("XEMO")


def test_default_scope_skips_files(fixture, run_tool):
    p = fixture("testdisk.d64")
    before = p.read_bytes()
    r = run_tool(p, "--replace", "A0=20", expect_ok=True)
    assert p.read_bytes() == before
    assert "0 cells changed" in r.stdout


def test_a0_can_be_targeted_with_consent(fixture, run_tool):
    p = fixture("three_files.d64")
    run_tool(p, "--replace", "A0=2E", "--all-rows", expect_ok=True)
    assert all(b"\xa0" not in n for n in names(p))


def test_bad_arguments(fixture, run_tool, tmp_path):
    p = fixture("baracuda-dir-file.d64")
    original = fixture("baracuda-dir-file.d64", name="original.d64").read_bytes()
    assert run_tool(p, "--replace", "D6", expect_ok=False).returncode == 2
    assert run_tool(p, "--replace", "D6=D6", expect_ok=False).returncode == 2
    assert run_tool(p, "--replace", "ZZ=D1", expect_ok=False).returncode == 2
    assert run_tool(p, "--replace", "D6=D1", "--replace", "D6=D7", expect_ok=False).returncode == 2
    assert run_tool(p, expect_ok=False).returncode == 2            # GUI stub, until Task 5
    assert run_tool(tmp_path / "none.d64", "--replace", "D6=D1", expect_ok=False).returncode == 3
    r = run_tool(p, "--replace", "D6=D1", "-o", tmp_path / "nodir" / "o.d64", expect_ok=False)
    assert r.returncode == 4
    assert p.read_bytes() == original
    assert not [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]


def test_single_character_form(fixture, run_tool):
    p = fixture("examples.d64")
    r = run_tool(p, "--replace", "==-", "--dry-run", expect_ok=True)   # '=' -> '-' on art rows
    assert "cells changed" in r.stdout and '"----------------"' in r.stdout


def test_version(run_tool):
    assert "1.0.0" in run_tool("--version", expect_ok=True).stdout
