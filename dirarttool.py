#!/usr/bin/env python3
"""dirarttool -- batch-edit the directory art on a .d64.

Directory art is a picture drawn with the 16-character names of directory
entries. Artists edit it one cell at a time in DirMaster; this tool changes
one character into another across every row at once (the Replacer), from a
window or from the command line.

    dirarttool                              open the window
    dirarttool disk.d64                     open the window with this disk
    dirarttool disk.d64 --replace D6=D1     cross -> filled circle, art rows, in place
    dirarttool disk.d64 --replace 0=- --rows 1,2,3 -o out.d64 --dry-run

Only the 16 name bytes of the rows you pick are ever written. File types,
block counts, sector links, the BAM and every data sector stay exactly as
they were, and the disk is written atomically so a crash cannot leave half
an image behind.

The 1541 geometry, the directory reader with its refusal rules and the
atomic writer are txt2dirart 1.0.2's, copied with their comments.

Idea and test disk: Baracuda/Blazon. Code: DR.J/Delysid. MIT.
"""
import argparse
import os
import sys

__version__ = "1.0.0"

DIR_TRACK = 18
ENTRY_SIZE = 32
ENTRIES_PER_SECTOR = 8
NAME_LEN = 16
PAD_SHIFTED = 0xA0              # CBM pads real filenames with $A0

# Every .d64 length we accept, mapped to its track count. The odd sizes carry
# a trailing error-info block (one byte per sector); we never touch it, but we
# must not mistake it for corruption either.
D64_SIZES = {
    174848: 35,     # 35 tracks
    175531: 35,     # 35 tracks + error info
    196608: 40,     # 40 tracks
    197376: 40,     # 40 tracks + error info
}


# --------------------------------------------------------------------------
# errors -- one class per exit code, so scripts can branch on what went wrong
# --------------------------------------------------------------------------

class DirArtError(Exception):
    """Base for every refusal. `code` becomes the process exit status."""
    code = 1


class BadArgs(DirArtError):
    """The request itself is wrong: bad --replace, bad row, missing consent."""
    code = 2


class BadImage(DirArtError):
    """The .d64 is not a shape we can work with."""
    code = 3


class CannotWrite(DirArtError):
    """The output could not be written; the input is untouched."""
    code = 4


# --------------------------------------------------------------------------
# 1541 geometry
# --------------------------------------------------------------------------

def sectors_on(track):
    """Sectors in a track. Zones are the same on 35- and 40-track disks."""
    if track <= 17:
        return 21
    if track <= 24:
        return 19
    if track <= 30:
        return 18
    return 17


def offset(track, sector):
    """Byte offset of a sector, counted from track 1 sector 0."""
    n = sum(sectors_on(t) for t in range(1, track))
    return (n + sector) * 256


def track_count(data):
    """Track count for this image, or refuse it."""
    size = len(data)
    if size not in D64_SIZES:
        known = ", ".join(str(s) for s in sorted(D64_SIZES))
        raise BadImage(
            f"{size} bytes is not a .d64 -- expected one of: {known} "
            f"(35 or 40 tracks, with or without error info)")
    return D64_SIZES[size]


# --------------------------------------------------------------------------
# reading the directory
# --------------------------------------------------------------------------

def read_chain(d):
    """Walk track 18's chain, returning [(track, sector), ...] and its entries.

    Each entry comes back as (byte offset of the entry in the image, the 32
    entry bytes), so a caller can write a name back to exactly where it came
    from.
    """
    chain, entries = [], []
    t, s = DIR_TRACK, 1
    seen = set()
    while t:
        if (t, s) in seen:
            raise BadImage("directory chain loops -- refusing to touch this disk")
        # The chain is only trusted while it stays where a directory lives:
        # track 18, sectors 1-18. Sector 0 is the BAM. "Somewhere on the disk"
        # is not good enough -- txt2dirart v1.0 checked only that, and a
        # corrupt link to t17/s13 made it overwrite the last data sector of a
        # real file.
        if t != DIR_TRACK or not 1 <= s < sectors_on(DIR_TRACK):
            raise BadImage(
                f"directory chain points at t{t}/s{s}; directory sectors must be "
                f"track {DIR_TRACK}, sectors 1-{sectors_on(DIR_TRACK) - 1}. This "
                f"disk's directory is damaged or non-standard -- refusing to touch it")
        seen.add((t, s))
        chain.append((t, s))
        base = offset(t, s)
        for i in range(ENTRIES_PER_SECTOR):
            at = base + i * ENTRY_SIZE
            e = bytearray(d[at: at + ENTRY_SIZE])
            if e[2]:                       # byte 2 = file type; 0 = slot never used
                e[0] = e[1] = 0            # strip the link bytes off entry 0
                entries.append((at, e))
        t, s = d[base], d[base + 1]
    return chain, entries


def is_art_entry(e):
    """Art rows are DELs that own no block. Real files are everything else."""
    return (e[2] & 0x0F) == 0 and (e[30] | e[31]) == 0


def entry_name(e):
    """The name LOAD would match: up to the first $A0, trailing spaces dropped.

    A 1541 filename ends at the first $A0. Anything after it is the hidden-name
    trick, shown after the closing quote in a listing but not part of the name.
    """
    raw = bytes(e[5:NAME_LEN + 5])
    cut = raw.find(bytes([PAD_SHIFTED]))
    if cut != -1:
        raw = raw[:cut]
    return raw.rstrip(b" ").decode("latin1").upper()


def display_name(e):
    """The 16 name bytes rendered for a terminal listing."""
    raw = bytes(e[5:NAME_LEN + 5]).replace(bytes([PAD_SHIFTED]), b" ")
    return "".join(chr(b) if 32 <= b < 127 else "." for b in raw)


# --------------------------------------------------------------------------
# the engine: rows in, rows out, nothing else touched
# --------------------------------------------------------------------------

class Row:
    """One directory entry as the tool sees it.

    index         position in the listing, what --rows and the window refer to
    kind          "art" (closed DEL owning no block) or "file"
    name          the 16 raw name bytes
    ftype         the raw file-type byte
    blocks        the block count the listing shows
    entry_offset  byte offset of the 32-byte entry in the image
    """
    __slots__ = ("index", "kind", "name", "ftype", "blocks", "entry_offset")

    def __init__(self, index, kind, name, ftype, blocks, entry_offset):
        self.index = index
        self.kind = kind
        self.name = name
        self.ftype = ftype
        self.blocks = blocks
        self.entry_offset = entry_offset

    @property
    def load_name(self):
        """The name LOAD would match, for warnings about real files."""
        return entry_name(bytes(5) + self.name + bytes(11))

    @property
    def type_text(self):
        if self.kind == "art":
            return "del"
        return {1: "seq", 2: "prg", 3: "usr", 4: "rel"}.get(self.ftype & 0x0F, "del")

    def __repr__(self):
        return f"<Row {self.index} {self.kind} {self.name!r}>"


def load(path):
    """Read a .d64, refusing anything that is not one."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise BadImage(f"cannot read {path}: {exc}")
    track_count(data)
    return data


def directory(data):
    """The listing as a list of Row, in listing order."""
    track_count(data)
    _, entries = read_chain(data)
    rows = []
    for index, (at, e) in enumerate(entries):
        kind = "art" if is_art_entry(e) else "file"
        rows.append(Row(index, kind, bytes(e[5:5 + NAME_LEN]), e[2],
                        e[30] + e[31] * 256, at))
    return rows


def header(data):
    """Disk name, ID and DOS type from the BAM, for the header line only."""
    bam = offset(DIR_TRACK, 0)
    return (bytes(data[bam + 0x90:bam + 0xA0]),
            bytes(data[bam + 0xA2:bam + 0xA4]),
            bytes(data[bam + 0xA5:bam + 0xA7]))


def replace(data, mapping, rows):
    """Replace characters in the names of the given rows.

    mapping  {source byte: target byte}; each cell is looked up once, so a
             mapping that swaps two characters swaps them and nothing chains
    rows     listing indexes of the rows to touch

    Returns (new image, number of cells changed). Only the 16 name bytes of
    the named rows can differ between `data` and the result.
    """
    listing = directory(data)
    wanted = sorted(set(rows))
    if wanted and (wanted[0] < 0 or wanted[-1] >= len(listing)):
        bad = [i for i in wanted if i < 0 or i >= len(listing)]
        raise BadArgs(f"no such row {bad[0]}: this disk has rows 0..{len(listing) - 1}")
    out = bytearray(data)
    changed = 0
    for i in wanted:
        start = listing[i].entry_offset + 5
        for k in range(start, start + NAME_LEN):
            new = mapping.get(out[k])
            if new is not None and new != out[k]:
                out[k] = new
                changed += 1
    return bytes(out), changed


def save(path, data):
    """Write the image atomically; any failure leaves the old file as it was."""
    try:
        write_atomically(path, data)
    except OSError as exc:
        raise CannotWrite(f"cannot write {path}: {exc}")


def write_atomically(out, data):
    """Write `data` to `out` so that a crash can never leave half a disk image.

    The temporary file is created with O_EXCL under a random name. O_EXCL
    refuses an existing path and never follows a symlink, so neither a file
    the user happens to own nor a link someone planted can be written through.

    This is deliberately not tempfile.mkstemp(): the tool imports argparse, os
    and sys and nothing else at startup, and the release build's module
    exclusions are tuned to exactly that.
    """
    folder = os.path.dirname(os.path.abspath(out))
    try:
        mode = os.stat(out).st_mode & 0o7777       # replacing a file: keep its permissions
    except OSError:
        umask = os.umask(0)                        # new file: what open() would have given
        os.umask(umask)
        mode = 0o666 & ~umask

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(16):
        tmp = os.path.join(
            folder, "." + os.path.basename(out) + "." + os.urandom(6).hex() + ".tmp")
        try:
            fd = os.open(tmp, flags, 0o600)
            break
        except FileExistsError:
            continue
    else:
        raise CannotWrite(f"could not create a temporary file next to {out}")

    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

def parse_char(text):
    """One side of a --replace: two hex digits, or one printable character.

    Hex wins when it parses ("10" is $10, not the two characters 1 and 0),
    because hex is how dir artists talk about PETSCII. A lone letter is
    uppercased, the C64's default charset having no lowercase.
    """
    if len(text) == 2:
        try:
            return int(text, 16)
        except ValueError:
            pass
    if len(text) == 1 and 32 <= ord(text) < 127:
        return ord(text.upper())
    raise BadArgs(f"'{text}' is not a character: give two hex digits like D6 "
                  f"or one printable character like X")


def parse_replace(specs):
    """--replace FROM=TO pairs into one {from: to} mapping."""
    mapping = {}
    for spec in specs or ():
        cut = spec.find("=", 1)             # "==-" replaces '=' itself
        if cut == -1 or cut == len(spec) - 1:
            raise BadArgs(f"--replace wants FROM=TO, got '{spec}'")
        src, dst = parse_char(spec[:cut]), parse_char(spec[cut + 1:])
        if src == dst:
            raise BadArgs(f"--replace {spec}: FROM and TO are the same character")
        if src in mapping:
            raise BadArgs(f"--replace {spec}: ${src:02X} is already being replaced")
        mapping[src] = dst
    if not mapping:
        raise BadArgs("nothing to do: give at least one --replace FROM=TO")
    return mapping


def select_rows(rows, wanted, all_rows):
    """Which rows a run may touch, under the consent rule.

    Default is every art row. `wanted` narrows that to listing indexes. A real
    file is only ever included with --all-rows: naming one in --rows without
    it is refused, so a build script cannot rename a file by accident.
    """
    if wanted is not None:
        bad = [i for i in wanted if i < 0 or i >= len(rows)]
        if bad:
            raise BadArgs(f"no such row {bad[0]}: this disk has rows 0..{len(rows) - 1}")
        chosen = sorted(set(wanted))
        if not all_rows:
            files = [rows[i] for i in chosen if rows[i].kind == "file"]
            if files:
                raise BadArgs(
                    f"row {files[0].index} is the real file {files[0].load_name}; "
                    f"add --all-rows if you really want to change its name")
        return chosen
    return [r.index for r in rows if all_rows or r.kind == "art"]


def print_listing(data, rows, out=None):
    """What a LIST would show, with the row index the tool uses on the left."""
    out = out or sys.stdout
    name, disk_id, dos = header(data)
    fake = bytes(5)
    print(file=out)
    print(f'     {0:5} "{display_name(fake + name)}" '
          f'{display_name(fake + disk_id + bytes(14))[:2]} '
          f'{display_name(fake + dos + bytes(14))[:2]}', file=out)
    for r in rows:
        print(f'{r.index:3}  {r.blocks:5} "{display_name(fake + r.name)}" {r.type_text}',
              file=out)
    print(file=out)


def _rows_arg(text):
    try:
        return [int(x) for x in text.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"--rows wants numbers like 1,2,3, got '{text}'")


def build_parser():
    p = argparse.ArgumentParser(
        prog="dirarttool",
        description="Replace one PETSCII character with another across the "
                    "directory art of a .d64. With no arguments, opens the window.")
    p.add_argument("d64", nargs="?", help="the disk image (rewritten in place unless -o)")
    p.add_argument("--replace", metavar="FROM=TO", action="append",
                   help="character to replace and what with: two hex digits (D6) or "
                        "one printable character (X); repeatable")
    p.add_argument("--rows", type=_rows_arg, metavar="N,N,...",
                   help="only these rows (listing index, 0-based); default: every art row")
    p.add_argument("--all-rows", action="store_true",
                   help="allow real files too (changes their LOAD names)")
    p.add_argument("-o", "--out", metavar="OUT.D64", help="write here instead of in place")
    p.add_argument("--dry-run", action="store_true",
                   help="print the resulting listing, write nothing")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress lines")
    p.add_argument("--version", action="version", version=f"dirarttool {__version__}")
    return p


def run_gui(path):
    """The window. Built in Task 5; until then the CLI says so."""
    raise BadArgs("GUI not built yet")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return run_gui(None)
    if len(argv) == 1 and not argv[0].startswith("-"):
        return run_gui(argv[0])

    args = build_parser().parse_args(argv)
    log = (lambda *a, **k: None) if args.quiet else print
    if not args.d64:
        raise BadArgs("which disk? give a .d64")

    mapping = parse_replace(args.replace)
    data = load(args.d64)
    rows = directory(data)
    chosen = select_rows(rows, args.rows, args.all_rows)
    result, changed = replace(data, mapping, chosen)

    if args.dry_run:
        print_listing(result, directory(result))
        log(f"[*] {changed} cells changed")
        log("[*] dry run -- nothing written")
        return 0

    log(f"[*] {changed} cells changed")
    out = args.out or args.d64
    save(out, result)
    log(f"[*] DONE -> {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DirArtError as exc:
        # stdout is block-buffered when piped and stderr is not, so without
        # this flush the error jumps ahead of the progress lines that explain
        # what the tool was doing when it hit the problem.
        sys.stdout.flush()
        print(f"!! {exc}", file=sys.stderr)
        sys.exit(exc.code)
    except KeyboardInterrupt:
        sys.exit(130)
