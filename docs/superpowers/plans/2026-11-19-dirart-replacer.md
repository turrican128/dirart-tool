# dirart-tool Replacer (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stdlib-only Python tool, `dirarttool.py`, that replaces one PETSCII character with another across the directory-art rows of a `.d64`, from a Tkinter window or the command line, without touching any byte outside the 16-byte name fields.

**Architecture:** One source file in three sections. The engine (1541 geometry, directory reader, `replace`, atomic save) is copied from txt2dirart 1.0.2 and extended; it is pure functions over `bytes` and is what the tests exercise. The CLI wraps the engine with argparse. The GUI imports tkinter lazily, draws the directory and a PETSCII palette on Canvases from an embedded character-ROM blob, and calls the same engine functions behind every button.

**Tech Stack:** Python 3.9+ stdlib (`argparse`, `os`, `sys`, `tkinter`, `zlib`, `base64`), pytest for tests, PyInstaller on Python 3.11 in GitHub Actions for the Windows and Linux binaries.

**Spec:** `docs/superpowers/specs/2026-11-19-dirart-replacer-design.md`

## Global Constraints

- Python 3.9 is the floor; CI tests 3.9 and 3.12. Release binaries are built with 3.11.
- Standard library only. `tkinter` is imported inside the GUI entry function, never at module top.
- One source file, `dirarttool.py`, with `__version__ = "1.0.0"`.
- Only bytes `entry_offset + 5 .. entry_offset + 20` of selected entries may ever differ between an input image and an output image.
- Accepted image sizes and their track counts: `174848: 35, 175531: 35, 196608: 40, 197376: 40`. Anything else is refused.
- Directory chain must stay on track 18, sectors 1 to 18, and must not loop; otherwise refuse, never repair.
- Exit codes: 0 ok, 2 bad arguments, 3 bad image or refused disk, 4 cannot write.
- Default scope is art rows (closed DEL, 0 blocks). `--all-rows` is the consent flag for real-file rows.
- Credits text, verbatim: `code DR.J/Delysid, idea and test disk Baracuda/Blazon`. Licence MIT, copyright `2026 DR.J/Delysid`.
- Repository: `C:\Claude Workspace\C64 Demo and Games projects\dirart-tool`, GitHub `turrican128/dirart-tool`, public. Work on branch `feat/replacer`, merged by PR.
- `*.d64`, `*.prg`, `*.bin` are marked `binary` in `.gitattributes` before any fixture is committed.

## Review Focus

1. A `.d64` with the trailing error-info block (175531 or 197376 bytes): the block must come back byte-identical after `replace` and `save`. Test added in Task 2.
2. A directory that spans more than one sector on track 18 (nine or more rows): rows in the second sector must be replaced too, and the sector links left alone. `examples.d64` has 11 entries, so Task 2 asserts a change in a row with index 8 or higher.
3. `--rows` with an index past the last row, or a negative one: exit 2 with the valid range in the message, nothing written. Test added in Task 3.
4. An output path that cannot be written (directory does not exist): exit 4, the input disk untouched, no stray temporary file left behind. Test added in Task 3.
5. A real file with an $A0 inside its 16 name bytes selected with `--all-rows` and a mapping that targets `A0`: the change applies (the user asked for $A0 by hex) and the dry-run listing shows the moved closing quote. Test added in Task 4 (listing) and Task 3 (change applies).

---

### Task 1: Repository bootstrap and the independent test reader

**Files:**
- Create: `.gitattributes` (copy of txt2dirart's), `.gitignore` (copy of txt2dirart's), `LICENSE` (MIT, `Copyright (c) 2026 DR.J/Delysid`)
- Create: `tests/fixtures/baracuda-dir-file.d64` (copy of `C:\Users\Administrator\.claude\jobs\2f626718\tmp\baracuda-disk\baracuda-dir-file.d64`, 174848 bytes)
- Create: `tests/fixtures/examples.d64`, `tests/fixtures/testdisk.d64`, `tests/fixtures/three_files.d64` (copies from `C:\Claude Workspace\txt2dirart\` root and `tests/fixtures/`)
- Create: `tests/d64reader.py` (copy of txt2dirart's `tests/d64reader.py`, plus `read_file`)
- Create: `tests/conftest.py`
- Test: `tests/test_fixtures.py`

**Interfaces:**
- Produces: `d64reader.read_directory(path_or_bytes) -> list[Entry]` with `Entry.name_exact: bytes`, `Entry.is_art`, `Entry.blocks`, `Entry.track`, `Entry.sector`; `d64reader.read_file(path_or_bytes, entry) -> bytes` (the file's data by walking its track/sector chain, 254 payload bytes per full sector, last sector's byte 1 = count); `conftest` fixtures `fixture(name) -> Path` (copies `tests/fixtures/<name>` into `tmp_path` and returns the copy) and `run_tool(*args, expect_ok=None) -> CompletedProcess` (txt2dirart's, pointing at `dirarttool.py`).

- [ ] **Step 1: `git init`, create the repo, copy the dotfiles and licence**

```bash
cd "C:\Claude Workspace\C64 Demo and Games projects\dirart-tool"
git init -b main
cp ../../txt2dirart/.gitattributes ../../txt2dirart/.gitignore .
```
Write `LICENSE` as txt2dirart's with the year and name above. Commit `chore: bootstrap repo (gitattributes, gitignore, MIT licence)`, then `gh repo create turrican128/dirart-tool --public --source . --remote origin --push`, then `git switch -c feat/replacer`.

- [ ] **Step 2: Copy the four fixture disks and the test reader, add `read_file`**

`read_file(path_or_bytes, entry)` in `tests/d64reader.py`: start at `(entry.track, entry.sector)`, follow bytes 0 and 1 of each sector, append bytes `2:256` for a linked sector and `2:block[1] + 1` for the last one (track byte 0). Raise `ValueError` on a loop.

- [ ] **Step 3: Write `tests/conftest.py` and the failing fixture test**

```python
# tests/test_fixtures.py
def test_baracuda_disk_is_as_the_spec_says(fixture):
    rows = d64reader.read_directory(fixture("baracuda-dir-file.d64"))
    assert [e.name_exact for e in rows] == [
        b"\xd1\xd1  BARACUDA  \xd1\xd1",
        b"\xd1\xd6  BARACUDA  \xd6\xd1",
        b"\xd1\xd7  BARACUDA  \xd7\xd1",
    ]
    assert all(e.is_art for e in rows)

def test_examples_disk_has_art_and_files(fixture):
    rows = d64reader.read_directory(fixture("examples.d64"))
    assert len(rows) == 11
    assert sorted(e.name for e in rows if not e.is_art) == ["DEMO", "README"]
    demo = next(e for e in rows if e.name == "DEMO")
    assert len(d64reader.read_file(fixture("examples.d64"), demo)) > 0
```

- [ ] **Step 4: Run, expect PASS** (`python -m pytest tests/test_fixtures.py -q`; these tests check the fixtures, not the tool, so they pass once the files are in place). If the `examples.d64` count is not 11, correct the assertion to what the reader reports and note it in the commit message.

- [ ] **Step 5: Commit** `test: fixtures and independent d64 reader`

---

### Task 2: Engine, disk core and `replace`

**Files:**
- Create: `dirarttool.py` (module docstring, `__version__`, engine section)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: Task 1 fixtures and reader.
- Produces, in `dirarttool.py`:
  - constants `DIR_TRACK, ENTRY_SIZE, ENTRIES_PER_SECTOR, NAME_LEN, PAD_SHIFTED, D64_SIZES` and functions `sectors_on, offset, track_count, read_chain, is_art_entry, display_name, write_atomically`, copied from `txt2dirart.py` lines 37-57, 89-182 and 600-657 with their comments. `read_chain` is changed in one way: each entry it returns also carries its absolute byte offset (return `list[tuple[int, bytearray]]`, `(entry_offset, entry)`).
  - exceptions `DirArtError(code)`, `BadArgs(code=2)`, `BadImage(code=3)`, `CannotWrite(code=4)`.
  - `class Row: index: int, kind: str ("art" | "file"), name: bytes (16), ftype: int, blocks: int, entry_offset: int`
  - `load(path: str) -> bytes` (reads, `track_count` validates, `BadImage` on OSError or size)
  - `directory(data: bytes) -> list[Row]`
  - `header(data: bytes) -> tuple[bytes, bytes, bytes]` (name 16 bytes at BAM `+0x90`, id 2 bytes at `+0xA2`, dos 2 bytes at `+0xA5`)
  - `replace(data: bytes, mapping: dict[int, int], rows: Iterable[int]) -> tuple[bytes, int]`
  - `save(path: str, data: bytes) -> None` (wraps `write_atomically`; any `OSError` becomes `CannotWrite`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_engine.py
import dirarttool as dt

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
    assert n == 2 and dt.directory(out)[2].name == data_row(data, 2)

def test_mapping_is_single_pass_swap(fixture):
    data = dt.load(fixture("baracuda-dir-file.d64"))
    out, n = dt.replace(data, {0xD6: 0xD7, 0xD7: 0xD6}, [1, 2])
    assert dt.directory(out)[1].name == b"\xd1\xd7  BARACUDA  \xd7\xd1"
    assert dt.directory(out)[2].name == b"\xd1\xd6  BARACUDA  \xd6\xd1"
    assert n == 4

def test_files_untouched_on_examples(fixture):
    path = fixture("examples.d64")
    data = dt.load(path)
    art = [r.index for r in dt.directory(data) if r.kind == "art"]
    assert max(art) >= 8                                    # second directory sector
    out, n = dt.replace(data, {0x20: 0x2D}, art)            # space -> minus on art rows
    assert n > 0
    before = d64reader.read_directory(data); after = d64reader.read_directory(out)
    for b, a in zip(before, after):
        if not b.is_art:
            assert b.raw == a.raw
            assert d64reader.read_file(data, b) == d64reader.read_file(out, a)
    allowed = {o for r in dt.directory(data) if r.kind == "art" for o in range(r.entry_offset + 5, r.entry_offset + 21)}
    assert all(i in allowed for i in range(len(data)) if data[i] != out[i])

def test_error_info_block_preserved(fixture, tmp_path):
    raw = (fixture("baracuda-dir-file.d64")).read_bytes() + bytes(range(256)) * 2 + bytes(171)
    assert len(raw) == 175531
    out, _ = dt.replace(raw, {0xD6: 0xD1}, [1])
    assert out[174848:] == raw[174848:]

def test_refusals(fixture, tmp_path):
    with pytest.raises(dt.BadImage): dt.load(tmp_path / "missing.d64")
    (tmp_path / "short.d64").write_bytes(b"\0" * 1000)
    with pytest.raises(dt.BadImage): dt.load(tmp_path / "short.d64")
    data = bytearray(dt.load(fixture("baracuda-dir-file.d64")))
    data[dt.offset(18, 1)] = 17; data[dt.offset(18, 1) + 1] = 0     # chain leaves track 18
    with pytest.raises(dt.BadImage): dt.directory(bytes(data))

def test_save_is_atomic_and_keeps_mode(fixture, tmp_path):
    p = fixture("baracuda-dir-file.d64")
    dt.save(str(p), b"\0" * 174848)
    assert p.read_bytes() == b"\0" * 174848
    assert not [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
    with pytest.raises(dt.CannotWrite): dt.save(str(tmp_path / "nope" / "x.d64"), b"\0" * 174848)
```
`data_row(data, i)` is a two-line helper in the test file returning `dt.directory(data)[i].name`.

- [ ] **Step 2: Run, expect FAIL** with `ModuleNotFoundError: dirarttool` (`python -m pytest tests/test_engine.py -q`).

- [ ] **Step 3: Create `dirarttool.py`: docstring, `__version__`, copied disk core, the four exceptions, `Row`, `load`, `directory`, `header`, `replace`, `save`**

`replace` builds a `bytearray`, and for each selected row and each of its 16 name bytes looks the byte up in `mapping` once; a hit writes the mapped value and counts one cell. Unknown row indexes raise `BadArgs` with the valid range `0..len(rows)-1`.

- [ ] **Step 4: Run, expect PASS.** Then `python -c "import sys, dirarttool; assert 'tkinter' not in sys.modules"` prints nothing.

- [ ] **Step 5: Commit** `feat: engine - disk core from txt2dirart, directory(), replace(), save()`

---

### Task 3: Command line

**Files:**
- Modify: `dirarttool.py` (CLI section: `parse_char`, `build_parser`, `print_listing`, `main`, `__main__` block)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 2 engine.
- Produces: `parse_char(text: str) -> int` (two hex digits -> that byte; one printable ASCII character -> its code, letters uppercased; anything else `BadArgs`); `select_rows(rows: list[Row], wanted: list[int] | None, all_rows: bool) -> list[int]` (the consent rule); `print_listing(data: bytes, rows: list[Row], out=sys.stdout)` (txt2dirart's format plus an index column on the left: `  1  0 "..." del`); `main(argv=None) -> int`. `main` with an empty argument list, or with exactly one positional argument and no options, calls `run_gui(path_or_none)` from Task 5 (until Task 5 exists, a stub that raises `BadArgs("GUI not built yet")`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
def test_replace_in_place(fixture, run_tool):
    p = fixture("baracuda-dir-file.d64")
    r = run_tool(p, "--replace", "D6=D1", "--replace", "d7=d1", expect_ok=True)
    assert [e.name_exact for e in d64reader.read_directory(p)] == [b"\xd1\xd1  BARACUDA  \xd1\xd1"] * 3
    assert "4 cells changed" in r.stdout

def test_output_file_leaves_input_alone(fixture, run_tool, tmp_path):
    p = fixture("baracuda-dir-file.d64"); before = p.read_bytes()
    run_tool(p, "--replace", "D6=D1", "-o", tmp_path / "out.d64", expect_ok=True)
    assert p.read_bytes() == before and (tmp_path / "out.d64").exists()

def test_dry_run_writes_nothing_and_lists(fixture, run_tool):
    p = fixture("baracuda-dir-file.d64"); before = p.read_bytes()
    r = run_tool(p, "--replace", "D6=D1", "--dry-run", expect_ok=True)
    assert p.read_bytes() == before
    assert '"' in r.stdout and "del" in r.stdout and "dry run" in r.stdout

def test_rows_subset_and_range(fixture, run_tool):
    p = fixture("baracuda-dir-file.d64")
    run_tool(p, "--replace", "D6=D1", "--rows", "1", expect_ok=True)
    assert d64reader.read_directory(p)[2].name_exact.count(b"\xd7") == 2
    r = run_tool(p, "--replace", "D6=D1", "--rows", "7", expect_ok=False)
    assert r.returncode == 2 and "0..2" in r.stderr

def test_file_rows_need_consent(fixture, run_tool):
    p = fixture("examples.d64")
    idx = next(i for i, e in enumerate(d64reader.read_directory(p)) if e.name == "DEMO")
    r = run_tool(p, "--replace", "D=X", "--rows", str(idx), expect_ok=False)
    assert r.returncode == 2 and "DEMO" in r.stderr and "--all-rows" in r.stderr
    run_tool(p, "--replace", "D=X", "--rows", str(idx), "--all-rows", expect_ok=True)
    assert d64reader.read_directory(p)[idx].name.startswith("XEMO")

def test_default_scope_skips_files(fixture, run_tool):
    p = fixture("testdisk.d64"); before = p.read_bytes()
    r = run_tool(p, "--replace", "A0=20", expect_ok=True)
    assert p.read_bytes() == before and "0 cells changed" in r.stdout

def test_a0_can_be_targeted_with_consent(fixture, run_tool):
    p = fixture("three_files.d64")
    run_tool(p, "--replace", "A0=2E", "--all-rows", expect_ok=True)
    assert all(b"\xa0" not in e.name_exact for e in d64reader.read_directory(p))

def test_bad_arguments(fixture, run_tool, tmp_path):
    p = fixture("baracuda-dir-file.d64")
    assert run_tool(p, "--replace", "D6", expect_ok=False).returncode == 2
    assert run_tool(p, "--replace", "D6=D6", expect_ok=False).returncode == 2
    assert run_tool(p, "--replace", "ZZ=D1", expect_ok=False).returncode == 2
    assert run_tool(p, expect_ok=False).returncode == 2            # GUI stub, until Task 5
    assert run_tool(tmp_path / "none.d64", "--replace", "D6=D1", expect_ok=False).returncode == 3
    r = run_tool(p, "--replace", "D6=D1", "-o", tmp_path / "nodir" / "o.d64", expect_ok=False)
    assert r.returncode == 4 and p.read_bytes() == fixture("baracuda-dir-file.d64").read_bytes()

def test_version(run_tool):
    assert "1.0.0" in run_tool("--version", expect_ok=True).stdout
```
The `-o` assertion in `test_bad_arguments` compares against a fresh fixture copy, so give `fixture` a `name=` argument to copy under another file name, or read the original from `tests/fixtures` directly.

- [ ] **Step 2: Run, expect FAIL** (`python -m pytest tests/test_cli.py -q`): the script has no `main`.

- [ ] **Step 3: Implement the CLI section**

`build_parser()`: positional `d64` (nargs `?`), `--replace FROM=TO` (`action="append"`), `--rows` (comma list of ints), `--all-rows`, `-o/--out`, `--dry-run`, `-q/--quiet`, `--version`. `main` order: parse, decide GUI vs CLI, `load`, `directory`, `select_rows`, build the mapping (`BadArgs` if any FROM equals its TO or if a FROM repeats), `replace`, then dry-run listing or `save`, printing `[*] N cells changed` and `[*] DONE -> path`. `__main__` block is txt2dirart's (flush, `!! message` on stderr, `exc.code`, 130 on Ctrl-C).

- [ ] **Step 4: Run, expect PASS.**

- [ ] **Step 5: Commit** `feat: command line - --replace, --rows, --all-rows consent, -o, --dry-run`

---

### Task 4: Character ROM blob and PETSCII rendering rules

**Files:**
- Create: `tools/make_charset.py`
- Modify: `dirarttool.py` (render section, pure functions, no tkinter)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: nothing from the engine.
- Produces: `CHARSET_B64: str` (zlib-compressed, base64 text of the first 2048 bytes of `chargen-901225-01.bin`, wrapped at 76 columns); `charset() -> bytes` (2048 bytes, cached); `glyph(screen_code: int) -> bytes` (8 bytes); `petscii_to_screen(b: int) -> tuple[int, bool]` (screen code, reversed) per the spec table; `listing_cells(name: bytes) -> list[tuple[int, bool, bool]]` (per cell: PETSCII byte, reversed, `after_quote`), where cells at and after the first `$A0` are `after_quote=True` and later `$A0` render as `$20`; `quote_column(name: bytes) -> int` (index of the first `$A0`, or 16).

- [ ] **Step 1: Write `tools/make_charset.py`**

Usage `python tools/make_charset.py <chargen.bin>`; reads 4096 bytes, takes `[:2048]`, prints `CHARSET_B64 = (` followed by quoted 76-column lines of `base64.b64encode(zlib.compress(data, 9))` and `)`. Run it against `C:\C64\GTK3VICE-3.7-win64\C64\chargen-901225-01.bin` and paste the output into `dirarttool.py`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_render.py
def test_charset_is_the_rom_half():
    cs = dt.charset()
    assert len(cs) == 2048
    assert dt.glyph(1) == bytes([0x18, 0x3C, 0x66, 0x7E, 0x66, 0x66, 0x66, 0x00])   # "A"
    assert dt.glyph(0x20) == bytes(8)                                              # space

@pytest.mark.parametrize("b,code,rev", [
    (0x41, 0x01, False), (0x20, 0x20, False), (0x30, 0x30, False),
    (0xD1, 0x51, False), (0xA0, 0x60, False), (0x61, 0x41, False),
    (0x0D, 0x8D, True), (0x93, 0xD3, True), (0xDE, 0x5E, False), (0xFF, 0x5E, False),
])
def test_petscii_to_screen(b, code, rev):
    # $FF is pi and shows as screen code $5E: the one exception to the "-128" rule
    assert dt.petscii_to_screen(b) == (code, rev)

def test_quote_rule():
    assert dt.quote_column(b"\xd1\xd1  BARACUDA  \xd1\xd1") == 16
    name = b"XX BARACUDA" + b"\xa0" * 5
    assert dt.quote_column(name) == 11
    cells = dt.listing_cells(name)
    assert [c[2] for c in cells] == [False] * 11 + [True] * 5
    assert all(c[0] == 0x20 for c in cells[11:])
    hidden = b"AB\xa0CD" + b"\xa0" * 11
    assert [c[0] for c in dt.listing_cells(hidden)][:5] == [0x41, 0x42, 0x20, 0x43, 0x44]
```
Check the two glyph byte rows against the ROM before trusting them; if the ROM differs, correct the test to the ROM's bytes.

- [ ] **Step 3: Run, expect FAIL.** **Step 4: Implement the render section.** **Step 5: Run, expect PASS.**

- [ ] **Step 6: Commit** `feat: embedded character ROM and PETSCII listing rules`

---

### Task 5: Tkinter window

**Files:**
- Modify: `dirarttool.py` (GUI section: `run_gui(path: str | None) -> int`, class `App`)
- Test: `tests/test_gui_boundary.py` (import boundary only) and a manual checklist below

**Interfaces:**
- Consumes: `load, directory, header, replace, save, Row` (Task 2), `print_listing` style for the header line (Task 3), `glyph, listing_cells, quote_column, petscii_to_screen` (Task 4).
- Produces: `run_gui(path)`; Task 3's stub is replaced. Everything else is private to `App`.

- [ ] **Step 1: Write the boundary test**

```python
# tests/test_gui_boundary.py
def test_cli_never_imports_tkinter(fixture, run_tool):
    p = fixture("baracuda-dir-file.d64")
    code = "import sys, dirarttool as dt; dt.main([%r, '--replace', 'D6=D1', '--dry-run']); print('tk' + 'inter' in sys.modules)" % str(p)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert out.stdout.strip().endswith("False")

def test_run_gui_exists():
    assert callable(dt.run_gui)
```

- [ ] **Step 2: Run, expect PASS for the boundary test already** (the CLI path never touched tkinter in Task 3). The failing signal for this task is manual: `python dirarttool.py` must open a window instead of printing `!! GUI not built yet`. Delete the line `assert run_tool(p, expect_ok=False).returncode == 2            # GUI stub, until Task 5` from `tests/test_cli.py`, since a bare path now opens the window.

- [ ] **Step 3: Build `App(tk.Tk)`** with, top to bottom: a toolbar (Open, Save, Save As, Undo, file name label, `*unsaved` label); a `ttk.Notebook` whose first tab holds the Replace panel beside the Directory canvas and whose second tab is `Mask` with `state="disabled"` and the text `coming in v2`; the PETSCII palette canvas across the bottom; a status bar.
  - Directory canvas: header line from `header()` drawn as `print_listing` prints it, then one line per `Row`: a `Checkbutton` (art rows on, file rows off), a grey index number, the blocks count, the 16 cells drawn with `glyph()` at scale 2 (16x16 px), Pepto palette: light blue `#867ade` (colour 14) on blue `#40318d` (colour 6); a reversed cell swaps the two. Quote glyphs drawn at column 0 and at `quote_column()`. Cells after the quote still occupy their column. Then `prg`/`del`/... and the `art`/`file` tag.
  - Palette canvas: 16 x 16 cells, screen codes 0..255, drawn with `glyph()`, the hovered cell's PETSCII value shown as `$NN` in the status bar. Palette cells represent PETSCII bytes `0x00..0xFF` (map through `petscii_to_screen` for the glyph), so a click yields a PETSCII byte directly.
  - Left click on any cell (directory or palette) sets From; right click sets To. Each picker shows the glyph at scale 3 and `$NN`. Apply is enabled only when both are set and differ.
  - Rows radio: `ticked rows` (default) or `all rows`.
  - Apply: computes the row list, if it includes a file row and the warning has not been shown this session, asks `This changes the LOAD name of <NAME>. Continue?`; calls `replace`; if `n == 0` sets status `0 cells changed` and returns; else pushes the previous image, redraws, sets `last: N cells changed`, marks dirty.
  - Undo pops the stack; dirty is `image != image_on_disk`.
  - Save calls `save(path, image)`; Save As uses `filedialog.asksaveasfilename` with initial file `<stem>-dirart.d64`; errors (`DirArtError`) go to `messagebox.showerror`, never a traceback. Open with unsaved changes and window close with unsaved changes ask Save / Discard / Cancel.
  - Shortcuts: Ctrl+O, Ctrl+S, Ctrl+Shift+S, Ctrl+Z. Help > About shows `dirarttool <version>` and the credits line from Global Constraints.
  - `run_gui(path)` creates `App`, loads `path` if given (errors into a dialog), `mainloop()`, returns 0.

- [ ] **Step 4: Manual checklist, run `python dirarttool.py tests/fixtures/baracuda-dir-file.d64` (on a copy)**
  1. Three rows shown as on the C64, quotes at both ends, header line present.
  2. Left click the cross in row 1 -> From shows `$D6`; right click the circle in row 0 -> To `$D1`; Apply -> `2 cells changed`, row 1 now equals row 0.
  3. Undo restores row 1; Apply again; Save; reopen the file with `--dry-run` from the CLI and see the change.
  4. Open `tests/fixtures/examples.d64` copy: file rows unticked; tick DEMO, replace `D`->`X`, warning appears, Cancel changes nothing, Continue changes it.
  5. Open a `.txt` file: an error dialog, the window stays alive.
  6. Close with unsaved changes: the prompt appears.
  Record the outcome of each line in the PR description.

- [ ] **Step 5: Run the whole suite, expect PASS** (`python -m pytest -q`).

- [ ] **Step 6: Commit** `feat: Tkinter window - directory canvas, palette, From/To, Apply, Undo, Save`

---

### Task 6: CI, release workflow, README, CHANGELOG, PR

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `README.md`, `CHANGELOG.md`

**Interfaces:** none new.

- [ ] **Step 1: `ci.yml`** = txt2dirart's with the tool name changed; the "no packages installed" step runs `python dirarttool.py --version`, `python dirarttool.py tests/fixtures/baracuda-dir-file.d64 --replace D6=D1 --dry-run` and `python -c "import tkinter"`. Drop the `examples` job.

- [ ] **Step 2: `release.yml`** = txt2dirart's with: name `dirarttool`, `--console`, `--exclude-module tkinter` removed and `--hidden-import tkinter` not needed (PyInstaller finds it; keep `--collect-all tkinter` out unless the smoke test fails), the other excludes kept except `zlib`-adjacent ones (`lzma`, `bz2` may stay excluded; `zlib` and `base64` must not be excluded). Smoke test: `--version`, the dry run above, an in-place replace on a copy checked with `cmp` against a second copy produced by `python dirarttool.py`, a `--rows 7` call that must exit 2. Zip: binaries, `dirarttool.py`, `README.md`, `CHANGELOG.md`, `LICENSE`, `tests/fixtures/baracuda-dir-file.d64` as `example-baracuda.d64`.

- [ ] **Step 3: `README.md`** sections, in this order: one-paragraph pitch with the Baracuda example as a before/after listing; What directory art is (two paragraphs, link to txt2dirart for stamping); The two ideas (Replacer now, Mask coming); Install (binaries, or `python dirarttool.py`); The window (screenshot placeholder `docs/window.png`, the From/To click rule, rows, Undo, Save As); Command line (the four example lines from the spec, `--replace` forms incl. the `--replace=-=0` note, `--rows`, `--all-rows` consent rule, `--dry-run`, `-o`); Exit codes table; How it works (only name bytes change; chain rules; atomic write); Where the glyphs come from (ROM, VICE, `tools/make_charset.py`); Limitations (35/40-track d64 only, track 18 only, no header editing, no row add/remove); Building from source; Credits (the verbatim credits line, MIT).

- [ ] **Step 4: `CHANGELOG.md`** with `## 1.0.0 - unreleased` and one line per user-visible feature.

- [ ] **Step 5: Take the screenshot** `docs/window.png` of the window loaded with the Baracuda disk after one Replace, commit it.

- [ ] **Step 6: Push the branch, open the PR** `feat: dirart-tool 1.0.0 - Replacer` with the manual checklist results from Task 5, watch `ci` go green on both OSes. Do **not** tag `v1.0.0`; Alex tags after he has run the exe himself.

- [ ] **Step 7: Commit** `docs: README, CHANGELOG, CI and release workflows` (before Step 6).
