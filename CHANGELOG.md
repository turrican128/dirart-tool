# Changelog

## 1.0.0 - unreleased

The Replacer.

- Window: open a `.d64`, see its directory drawn with the real C64 glyphs, left-click a
  character to pick it as From, right-click one as To, tick the rows, Apply.
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
