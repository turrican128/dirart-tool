# Changelog

## 1.2.0 - unreleased

Baracuda's report on 1.1.0: five bugs, his wishes for the Edit menu and an Extras menu, and
the next round of the Mask.

Fixed:

- The ID was not shown like it is. A listing prints five signs after the header; the window
  and the command line dropped the third one and showed a space, so `2026` read `2 26` and
  `KOELN` read `KO LN`.
- The directory now runs down to `BLOCKS FREE.`, in the window and on the command line, and
  the directory pane is taller, scrolls with the mouse wheel and grows with the window.
- Undo goes grey, in the toolbar and in the Edit menu, when everything is undone.
- Lines of every file type count as art when they own no data: 0 blocks and a DEL, or any
  type pointing at track 0 or the directory track. 1.1.0 only knew DEL, so PRG, SEQ, USR
  and REL art lines started unticked. Types are listed as they are (`???` for the strange
  ones). A 0-block entry pointing at a data track is still a real file.
- Undo after a mix puts the mix number back.

New:

- **Redo** (Ctrl+Y), and **Tick all lines** / **Untick all lines** (T / U), in the Edit menu.
- **Extras** menu: background colour, letter colour (the sixteen of the machine), and
  **Save directory as picture** (Ctrl+P), a PNG of the whole listing for bug reports.
- Mask: *Take* **same column** (as before), **any column**, or **whole pieces** of one line.
- **Tag** tab: copy the group sign behind a name (`/DELYSID`), set it behind other names,
  change it to a new one wherever it stands.
- Command line: `--mask-mode`, `--set-tag`, `--change-tag OLD=NEW`, `--picture OUT.PNG`.
- The PETSCII table is 32 signs wide and 8 high, so the window fits a 1024 x 768 screen.

## 1.1.0 - 2026-11-21

The Mask and the header buffer. Both asked for by Baracuda after testing 1.0.0.

- **Mask**, in its own tab: left-click the placeholder sign (the `1` of a stamp), right-click
  the lines to take signs from or type them (`1-7`), press Mix and fill. Every placeholder in
  the ticked lines gets the sign of the same column in one of those lines, and no two filled
  lines come out the same when the sources allow it.
- Pressing the button again gives the next mix; the mix number brings any mix back. Undo
  returns to the stamp.
- The source lines are always chosen, never guessed: separators, text and test rows on the
  same disk cannot end up in the stamp.
- **Header and ID buffer**: F1 copies the header of the open disk, F3 writes it onto the next
  one. F5/F7 do the same for the ID, F2/F4 for both. Also in the new Header menu.
- Command line: `--mask CHAR --mask-from ROWS [--seed N]`, `--header-from OTHER.D64`,
  `--id-from OTHER.D64`. Row lists take ranges everywhere: `--rows 1-7,21`.
- The directory pane is shared by both tabs.

Fixed:

- The window asked about renaming real files once per session; a second disk's files were
  then renamed without a question. It asks once per disk now.
- A refused save to a read-only disk left a full-size temporary file next to it (Windows).
- A file name the output's code page cannot print crashed the run after the disk was
  already written, with exit 1 (piped output). The name is printed escaped instead.
- An empty row list (`--rows ""`) passed as "nothing to change" with exit 0. It is exit 2.

## 1.0.0 - 2026-11-19

The Replacer.

- Window: open a `.d64`, or drop one onto the window (Windows), see its directory drawn with
  the real C64 glyphs, left-click a character to pick it as From, right-click one as To, tick
  the rows, Apply.
- Undo, Save, Save As (suggests `<name>-dirart.d64`), unsaved-changes prompts.
- Art rows are ticked by default; real files are not, and changing one asks first,
  because it changes the file's LOAD name.
- Command line for build scripts: `--replace FROM=TO` (repeatable, hex or a single
  character), `--rows`, `--all-rows` as the consent flag for real files, `-o`,
  `--dry-run`, exit codes 2/3/4.
- Only the 16 name bytes of the chosen rows are ever written. The directory chain is
  checked the way txt2dirart checks it, and the image is written atomically.
- The 1541's closing-quote rule is followed on screen: a name padded with $A0 shows
  its quote where the drive would print it.
- A greyed Mask tab marks where v2 goes.
- QA gate: `tools/qa.py` runs the suite, a stdlib smoke run, a golden-image comparison on
  Baracuda's disk and the window checklist, and ends with one PASS or FAIL line.
