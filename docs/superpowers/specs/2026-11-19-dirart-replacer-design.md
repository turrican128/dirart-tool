# dirart-tool: Replacer (v1) design

Date: 2026-11-19. Author: Alex (DR.J/Delysid) with Claude. Idea: Carsten (Baracuda/Blazon).

## Intent and why

Carsten asked for a PC tool, with a GUI, that batch-edits directory art on a `.d64`.
He has two old ideas that one tool should cover:

- **Dirart Replacer**: existing characters are replaced by other characters, across all
  directory lines at once. Example: every `0` becomes `-`.
- **Dirart Mask**: the free positions of a line are filled with a chosen character.
  Example: `xx baracuda xx` becomes `xxxbaracudaxxx` or `xx-baracuda-xx`.

Today he does this by hand, cell by cell, in DirMaster. On a 20-row piece of art, changing
one sign everywhere is tedious and error-prone. Nobody has written the tool, so Alex builds
it, the same way he built txt2dirart, c64lint and tap2pdf: a free, stdlib-only Python tool
with Windows and Linux binaries, released on GitHub and CSDb.

**This document covers v1 = the Replacer.** The Mask is v2. The engine and the window are
laid out so the Mask drops in without reshaping anything.

### The test case that defines v1

Carsten's test disk `Baracuda - dir file.d64` has three art rows (closed DEL, 0 blocks):

```
row 0:  D1 D1 20 20 "BARACUDA" 20 20 D1 D1
row 1:  D1 D6 20 20 "BARACUDA" 20 20 D6 D1
row 2:  D1 D7 20 20 "BARACUDA" 20 20 D7 D1
```

$D1 is the filled circle, $D6 the cross, $D7 the open circle. His request "change lines 2
and 3 to be like line 1" is two Replacer passes, $D6 to $D1 and $D7 to $D1, over all rows.
After them the three rows are byte-identical. That is the acceptance test. The disk goes
into `tests/fixtures/` (it was sent for exactly this purpose).

## Goals (v1)

1. Open a `.d64`, show its directory the way `LOAD"$",8` lists it, with real PETSCII glyphs.
2. Replace character X with character Y in the 16 name bytes of the chosen rows, in one click.
3. Live preview, Undo, Save, Save As.
4. The same operation from the command line, for build scripts.
5. Never change any byte outside the 16-byte name field of a directory entry.
6. Tests that prove 2 and 5 on Carsten's disk and on txt2dirart's test disks.
7. Single-file Windows exe and Linux binary built by GitHub Actions on a tag.
8. README, CHANGELOG, LICENSE (MIT, DR.J/Delysid), CSDb submission text at release time.

## Non-goals (v1)

- The Mask operation (v2; see "Room for the Mask" below).
- Editing the disk header name and ID on the BAM sector.
- Adding, removing or reordering rows, or placing real files inside art. txt2dirart does that.
- Editing file types, block counts or track/sector links.
- D71, D81, G64 and other image formats.
- Free-hand painting of cells. That is a PETSCII editor, not this tool.
- Lowercase/business charset rendering. Directory art is drawn for the uppercase set.

## Anti-goals (a failure even if it "works")

- A disk that no longer loads, or whose files changed, after a save.
- A real file's LOAD name changed silently.
- A tool that needs Python, Tk or a font installed on Carsten's machine.
- A window that shows the art differently from the C64 (wrong glyphs, wrong spacing).

## Constraints

- Python 3.9+ (the floor the other tools promise and test in CI), standard library only:
  `argparse`, `os`, `sys`, `tkinter`, `zlib`, `base64`. No pip dependencies. PyInstaller
  single-file builds on Python 3.11, as in txt2dirart's `release.yml`, with `tkinter` taken
  off that workflow's exclude list and a headless `import tkinter` smoke step before packaging.
- One source file, `dirarttool.py`, like the other tools: engine section, CLI section, GUI
  section. Tkinter is imported only when the GUI starts, so the CLI runs headless.
- The 1541 geometry, image-size table, directory-chain reader and its refusal rules, and
  `write_atomically` are copied from txt2dirart 1.0.2 verbatim, with their comments.
  A chain that leaves track 18 sectors 1 to 18, or loops, is refused, not repaired.
- Glyphs come from the C64 character ROM (uppercase/graphics half, 2048 bytes), embedded
  in the source as a zlib+base64 blob so the exe needs no file next to it.
  `tools/make_charset.py <chargen.bin>` regenerates the blob. Source on this machine:
  `C:\C64\GTK3VICE-3.7-win64\C64\chargen-901225-01.bin`. The ROM is Commodore's; it ships
  inside every VICE download and inside most PETSCII tools, so embedding it is accepted
  practice, but the README says where it comes from.

## Approaches considered

1. **Single-page web app** (HTML + JavaScript, GitHub Pages). No install, any OS, but the
   d64 code would be a second implementation in a second language, with its own tests, and
   a browser is a worse home for a tool that will one day run from a build script.
2. **Python Tkinter desktop tool built on txt2dirart's code, shipped as exe.** Chosen by
   Alex. Same toolchain and release pipeline as his three published tools, the d64 code is
   reused as is, pytest guards it, and the CLI comes almost for free.
3. **New flags on txt2dirart.** Smallest work, but it has no GUI, which is the one thing
   Carsten asked for, and txt2dirart's job (stamp art from a text file) is a different
   workflow from editing a directory in place.

## The design

### Engine (pure functions, no Tk)

```
load(path) -> bytes                     # size check as txt2dirart, else BadImage
directory(data) -> list[Row]            # Row(index, kind, name: bytes[16], ftype, blocks,
                                        #     entry_offset)  kind = "art" | "file"
replace(data, mapping, rows) -> (bytes, int)
        # mapping: {src_byte: dst_byte}, applied in one pass per cell (no chaining:
        #   with {D6: D1, D1: D6} both swap, nothing double-converts)
        # rows: iterable of row indexes; only bytes entry_offset+5 .. +20 of those
        #   entries may differ between input and output; returns the new image and
        #   the number of cells changed
header(data) -> (name16: bytes, disk_id: bytes, dos: bytes)   # for display only
save(path, data)                        # txt2dirart.write_atomically
```

Replace works on raw PETSCII byte values. $20 (space) and $A0 (shifted-space padding) are
two different characters and both can be a source or a target. This is what makes real
file names safe to reason about: a $A0 is only touched if the user asks for $A0.

`Row.kind` is txt2dirart's rule: a closed DEL owning no block is art, everything else is a
file. Every row keeps its position; nothing is ever inserted or dropped.

### Command line

```
dirarttool disk.d64 --replace D6=D1 --replace D7=D1 [-o out.d64]
dirarttool disk.d64 --replace 0=- --rows 1,2,3 --dry-run
dirarttool disk.d64 --replace A0=20 --all-rows
dirarttool                       # no arguments: open the GUI
dirarttool disk.d64              # one argument: open the GUI with that disk loaded
```

- Each side of `--replace` is a two-digit hex byte or a single printable character;
  the hex form is the one the README leads with, because that is what dir artists know.
- Default scope is art rows only. `--rows` picks rows by their listing index (0-based, as
  the GUI shows them in its index column); `--all-rows` includes real files.
- **Consent rule for real files (my call, from the anti-goal "a LOAD name changed
  silently"):** `--all-rows` is the consent flag. If `--rows` names a real-file row and
  `--all-rows` is absent, the tool refuses with exit 2 and names the file. `--rows` and
  `--all-rows` combine: `--rows 3 --all-rows` changes row 3 even though it is a file.
- A side that starts with `-` (replacing the minus sign) must be written in hex or as
  `--replace=-=0`, because argparse reads `-=0` as an option. The README says so.
- `--dry-run` prints the resulting listing (txt2dirart's `print_listing` style) and the
  cell count, writes nothing.
- Without `-o` the input disk is rewritten in place, atomically, like txt2dirart.
- Exit codes: 0 ok, 2 bad arguments, 3 bad image / refused disk, 4 cannot write.
  Each is a distinct exception class so scripts can branch.

### Window

Text sketch of the main window, loaded with Carsten's disk after one Replace:

```
 dirarttool 1.0                                                   [-][o][x]
 [Open...] [Save] [Save As...] [Undo]        Baracuda - dir file.d64  *unsaved
 +-- Directory --------------------------------+  +-- Replace -----------------+
 |    0 "                "    2A               |  |  From  [ X ]  $D6           |
 | [x] 0 "**  BARACUDA  **" del   art          |  |  To    [ * ]  $D1           |
 | [x] 1 "**  BARACUDA  **" del   art          |  |                             |
 | [x] 2 "*o  BARACUDA  o*" del   art          |  |  Rows  (o) ticked rows      |
 |                                             |  |        ( ) all rows         |
 |                                             |  |                             |
 |                                             |  |      [   Apply   ]          |
 +---------------------------------------------+  |  last: 2 cells changed      |
 +-- PETSCII (click = From, right-click = To) ---------------------------------+
 |  16 x 16 grid of glyphs from the ROM, $00 .. $FF, hex of the hovered cell   |
 +-----------------------------------------------------------------------------+
 | Mask |  (tab, greyed: "coming in v2")                                        |
```

(`*` and `o` stand in for the real circle glyphs in this sketch.)

- **Directory pane** is a Canvas. Each row is drawn with ROM glyphs at 2x (16x16 px per
  cell), light blue on C64 blue, so it looks like the real listing. Header row is drawn
  but has no checkbox. Each other row has a checkbox, a small grey **index column** (the
  number `--rows` refers to; the block count in the listing is not it) and a tag `art` or
  `file`.
- Art rows start ticked, file rows start unticked. Ticking a file row is allowed; the
  first Apply that would change a file row shows one warning dialog ("this changes the
  LOAD name of CRACKTRO") with Continue / Cancel.
- **Picking From and To**: left-click any cell in the directory pane or the palette sets
  From; right-click sets To. The two boxes show the glyph and its hex. Both must be set
  and differ before Apply is enabled.
- **Apply** calls `engine.replace` on the in-memory image, redraws, pushes the previous
  image on the Undo stack (full 175 KB snapshots; a few dozen are nothing), and updates
  "last: N cells changed". With N = 0 the status says so and nothing is pushed.
- **Save** rewrites the opened file atomically; **Save As** suggests `<stem>-dirart.d64`
  in the same folder. Title shows `*unsaved` while the image differs from the file.
  Closing with unsaved changes asks. Open with unsaved changes asks.
- The Mask tab exists, disabled, with the text "coming in v2", so Carsten sees the shape.

### PETSCII to glyph

A name byte is shown as the C64 shows it inside the quotes of a listing: the standard
PETSCII-to-screen-code mapping ($00-1F: +128 reversed, $20-3F: same, $40-5F: -64,
$60-7F: -32, $80-9F: +64 reversed, $A0-BF: -64, $C0-DF: -128, $E0-FF: -128), glyph
bitmap = 8 bytes at `screen_code * 8` in the embedded 2048-byte set, bit set = light blue.
Control codes ($00-1F, $80-9F) are drawn reversed, which is what a real listing does.

**The closing quote follows the 1541's rule (my call, from Goal 1).** The drive puts the
closing `"` at the first $A0 in the name, shows every later $A0 as a space, and prints any
other byte after that $A0 *after* the quote (the hidden-name trick). The directory pane
emulates this: the quote moves, the trailing bytes are still drawn with the mapping above
(a control code after the quote is drawn reversed rather than executed, since the pane
cannot execute it), and every one of the 16 name bytes stays a clickable cell at a fixed
column, so click-to-pick never depends on where the quote landed. A row padded by
DirMaster with $A0 therefore looks on screen as it looks on the C64, which matters
because those are exactly the rows the v2 Mask (`which="pad"`) will be used on.

### Room for the Mask (v2, designed now, not built)

`mask(data, fill_byte, rows, which) -> (bytes, int)` where `which` is `"space"` ($20
only), `"pad"` ($A0 only) or `"both"`. Carsten's "xx baracuda xx" to "xxxbaracudaxxx" is
`mask(fill=ord("X"), which="space")`. The tab gets a Fill picker, the same Rows choice and
Apply. CLI: `--mask X [--mask-what space|pad|both]`. Nothing in v1 needs to move for it.

### Tests (pytest, run in CI before any build)

- `tests/fixtures/baracuda-dir-file.d64`: `replace({D6:D1, D7:D1}, all rows)` makes rows
  0, 1, 2 byte-identical, changes exactly 4 cells, and the output differs from the input
  at exactly those 4 offsets and nowhere else (full-image comparison).
- txt2dirart's `examples.d64` (9 art rows and 2 real files, DEMO and README, interleaved):
  a replace on art rows leaves every real file's entry, start sector, block count and
  every data sector unchanged. The proof is the full-image comparison (only the expected
  name-byte offsets differ) plus txt2dirart's `tests/d64reader.py` still listing the same
  files; a small chain walker is added to that reader so the file data is read back too.
  `testdisk.d64` (files only) covers the no-op case and the `--all-rows` case.
- Scope: `--rows` honours indexes, default excludes file rows, `--all-rows` includes them.
- Mapping is applied in a single pass (a swap mapping swaps, it does not chain).
- Refusals: wrong size, chain leaving track 18, looping chain, unreadable file.
- CLI: `--dry-run` writes nothing; `-o` leaves the input untouched; in-place write is
  atomic (temporary file, rename); exit codes.
- The GUI is not unit-tested; the engine behind every button is.

### Repository

```
C:\Claude Workspace\C64 Demo and Games projects\dirart-tool\            github.com/turrican128/dirart-tool (public)
  dirarttool.py          engine + CLI + GUI, one file, __version__
  README.md              what dir art is, the two ideas, screenshots, CLI, exit codes,
                         how the glyphs are rendered and where the ROM comes from, credits
  CHANGELOG.md           1.0.0 = Replacer
  LICENSE                MIT, DR.J/Delysid
  .gitattributes         txt2dirart's: *.d64 binary, or a Windows checkout CRLF-corrupts fixtures
  tests/                 pytest, fixtures/ (Carsten's disk, copies of txt2dirart's)
  tools/make_charset.py  ROM -> embedded blob
  docs/csdb-submission.md            written at release time, as in the other repos
  docs/superpowers/specs/            this document
  docs/superpowers/plans/            the implementation plan
  .github/workflows/ci.yml           pytest on push and PR
  .github/workflows/release.yml      txt2dirart's, renamed; console build (see below)
```

The folder holds only this document today: it is not yet a git repository and
`turrican128/dirart-tool` does not exist on GitHub, so the plan starts with `git init` and
creating the public repo. Nothing to fetch first.

The release builds a **console** executable, not a windowed one: a windowed build on
Windows swallows all CLI output. The cost is a console window appearing behind the GUI
when Carsten double-clicks the exe. That is accepted for 1.0; if he minds, 1.1 ships a
second, windowed `dirarttool-gui.exe` from the same source.

Credits in the window's About and in the README: "code DR.J/Delysid, idea and test disk
Baracuda/Blazon".

## Left to the builder

- Exact Tk widgets, paddings, fonts for the non-PETSCII text, and the canvas scale if 2x
  is too small on a high-DPI screen (a View menu with 2x/3x is welcome, not required).
- How the palette shows the reversed control-code glyphs.
- Dialog wording, keyboard shortcuts (Ctrl+O, Ctrl+S, Ctrl+Z are expected).
- The exact exception class names and the argparse help text.
- Whether the header row in the directory pane is drawn with the disk ID and DOS type
  exactly as `print_listing` does (preferred) or simplified.
