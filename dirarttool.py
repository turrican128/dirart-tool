#!/usr/bin/env python3
"""dirarttool -- batch-edit the directory art on a .d64.

Directory art is a picture drawn with the 16-character names of directory
entries. Artists edit it one cell at a time in DirMaster; this tool changes
one character into another across every row at once (the Replacer), fills a
placeholder character with signs taken from other lines (the Mask), and
carries a disk's header and ID over to another disk, from a window or from
the command line.

    dirarttool                              open the window
    dirarttool disk.d64                     open the window with this disk
    dirarttool disk.d64 --replace D6=D1     cross -> filled circle, art rows, in place
    dirarttool disk.d64 --replace 0=- --rows 1,2,3 -o out.d64 --dry-run
    dirarttool disk.d64 --mask 1 --mask-from 1-7      fill every 1 with signs of lines 1-7
    dirarttool disk.d64 --header-from other.d64       take the header of another disk

Only the 16 name bytes of the rows you pick are ever written, plus the 16
header and 5 ID bytes when you ask for those. File types, block counts,
sector links, the block map and every data sector stay exactly as they
were, and the disk is written atomically so a crash cannot leave half an
image behind.

The 1541 geometry, the directory reader with its refusal rules and the
atomic writer are txt2dirart 1.0.2's, copied with their comments.

Idea and test disk: Baracuda/Blazon. Code: DR.J/Delysid. MIT.
"""
import argparse
import os
import sys

__version__ = "1.1.0"

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


MASK_TRIES = 50     # re-rolls of one line before two equal lines are accepted


def mask(data, placeholder, rows, sources, seed=None):
    """Fill the placeholder character with signs taken from other lines.

    rows     listing indexes of the lines to fill (the stamp)
    sources  listing indexes of the lines to take signs from. There is no
             default on purpose: a directory holds separators, text and
             test rows next to the art, and a mix of all of them is not
             what an artist is after. The artist says which lines look right.
    seed     fixes the mix; None shuffles differently every run

    Each placeholder cell takes the sign in the same column of a randomly
    picked source line, so a column keeps the character of the art above it.
    A line that comes out equal to one already filled is mixed again (up to
    MASK_TRIES times), so every line looks different whenever the sources
    allow it. Source cells holding the placeholder, the $A0 pad or $00 are
    never taken. A column with no candidate is left unchanged and counted.

    Returns (new image, cells filled, cells left). Like replace(), only the
    16 name bytes of the target rows can differ between data and the result.
    """
    import random                     # lazy: the release build's module list is tuned

    listing = directory(data)
    wanted = sorted(set(rows))
    if wanted and (wanted[0] < 0 or wanted[-1] >= len(listing)):
        bad = [i for i in wanted if i < 0 or i >= len(listing)]
        raise BadArgs(f"no such row {bad[0]}: this disk has rows 0..{len(listing) - 1}")
    picked = sorted(set(sources or ()))
    if not picked:
        raise BadArgs("the Mask needs source lines: say which lines to take the signs from")
    if picked[0] < 0 or picked[-1] >= len(listing):
        bad = [i for i in picked if i < 0 or i >= len(listing)]
        raise BadArgs(f"no such source row {bad[0]}: this disk has rows "
                      f"0..{len(listing) - 1}")

    columns = [[] for _ in range(NAME_LEN)]
    for i in picked:
        for col in range(NAME_LEN):
            b = listing[i].name[col]
            if b not in (placeholder, PAD_SHIFTED, 0x00):
                columns[col].append(b)

    rng = random.Random(seed)
    out = bytearray(data)
    filled = left = 0
    seen = set()
    for i in wanted:
        start = listing[i].entry_offset + 5
        name = bytes(out[start:start + NAME_LEN])
        holes = [col for col in range(NAME_LEN) if name[col] == placeholder]
        fillable = [col for col in holes if columns[col]]
        left += len(holes) - len(fillable)
        if not fillable:
            continue
        for _ in range(MASK_TRIES):
            new = bytearray(name)
            for col in fillable:
                new[col] = rng.choice(columns[col])
            if bytes(new) not in seen:
                break
        seen.add(bytes(new))
        out[start:start + NAME_LEN] = new
        filled += len(fillable)
    return bytes(out), filled, left


# The header line of a listing: 16 name bytes, then the five characters the
# drive prints after the closing quote -- two ID bytes, a pad and the two DOS
# type bytes. Dir artists draw in all five, so "the ID" here is all five.
HEADER_AT, HEADER_LEN = 0x90, 16
ID_AT, ID_LEN = 0xA2, 5


def read_header(data):
    """The 16 header (disk name) bytes."""
    track_count(data)
    at = offset(DIR_TRACK, 0) + HEADER_AT
    return bytes(data[at:at + HEADER_LEN])


def read_id(data):
    """The 5 bytes a listing shows after the header: ID, pad, DOS type."""
    track_count(data)
    at = offset(DIR_TRACK, 0) + ID_AT
    return bytes(data[at:at + ID_LEN])


def write_header(data, name=None, disk_id=None):
    """Put a header and/or an ID on a disk; None leaves that part alone.

    Returns (new image, bytes changed). Only the 16 header bytes and the 5 ID
    bytes in the BAM sector can differ between `data` and the result; the
    block map around them is not touched.
    """
    track_count(data)
    bam = offset(DIR_TRACK, 0)
    out = bytearray(data)
    if name is not None:
        if len(name) != HEADER_LEN:
            raise BadArgs(f"a header is {HEADER_LEN} bytes, got {len(name)}")
        out[bam + HEADER_AT:bam + HEADER_AT + HEADER_LEN] = name
    if disk_id is not None:
        if len(disk_id) != ID_LEN:
            raise BadArgs(f"an ID is {ID_LEN} bytes, got {len(disk_id)}")
        out[bam + ID_AT:bam + ID_AT + ID_LEN] = disk_id
    changed = sum(1 for x, y in zip(data, out) if x != y)
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
            # A read-only target hands its mode to the temporary file, and
            # Windows will not delete a read-only file: make it ours again
            # first, or every refused save leaves a full disk image behind.
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# rendering rules: name bytes -> cells, and the glyphs to draw them with
# --------------------------------------------------------------------------

# The uppercase/graphics half of the C64 character ROM (901225-01), 2048
# bytes, zlib-compressed and base64-encoded so the binary needs no file next
# to it. Regenerate with tools/make_charset.py <chargen.bin>.
CHARSET_B64 = (
    "eNpdVD1rG0sUnSCQUggpvGoKEf2FhYBRMUhdfoewYV4RY1w5Lsx+oMJxJYxTvAEH/Y7XSWwhDIb9"
    "CwoqpnpEQkVWIHLfuXdnbSVHuzt7P8+5w6yMvbgYnxqljY2tterGWr6UsePx2Bp1fQ7v+bWKx+Pr"
    "8TgO65jjFxZxRKXOaIZR79vA+UCh5hK1aiyI1dnn5NPZ2ZmyV3F8wfmWYYSv6sdmR2yuQ3/TRDyW"
    "vlrZkF8tWqHXp+Qz+sFArRIv8uJmW0fgMxHDqPZf0U10elCGdbWNwqDSUr2LkiR6p2qITwmP2NYS"
    "flbpIXTcaHVq0dd+hC4z+NuOFGgkD94o0m2Fu413FBoyVhrGWr80R0DFR2RKNUQo+l1c8VxaD6AA"
    "um2zLfpts8fzNzvvbdJsYt9vmmxjX244P4YelozZ+eJ1yHGeQQmvFt4OSCLdEXoWcKnbIL4UHl0l"
    "EuHxtjdMkt5Q6YDa/+dSv0YB7QC4VxseQfe6LRlxsFlhnQuI5qvLQa/TajRand7gcsWeEGkIlIkB"
    "80JwkiTJsPc2nB8cEKVa3Z7WuY+NiX2OfJw83jdr+dw1A3gOroMQIjzm8yji+3WuxvDq5ERRMup3"
    "W4039UybgIqejqatFM0Dnp8/fOC7EcBBtunXodys5mntB1W/X+2j6gJiV73KUtcnQ2gkT/TWzsqW"
    "vJp3FdAKeE0+Ukr1blZOnoe5w/kuy+A7juXu/n7mcvK5y5xzNHGOL8rdbMb+2wd4H24pm81uZ7Ms"
    "rDOO3zvEEZW63DNy+r4DHpaEmilqaSbI6PFL+vXx8ZHcXZbdc75j5MJX9WNzKzbXoX++RzyTvp5c"
    "yK8WT+j1Nf2CfjBQS+JFXrbf+QJ8ecHIafdfMSm+NShnXbucMKi0pB9FmhY/qIb4SHjEdk7h58gv"
    "oGPi6ZtDX/cvdOXLf9ycQCN58BaF3xHuHd5RmKvcScPM+5fmCFB2REZ0EKHod3/Hc3m/hALodvud"
    "6Hf7Nc+/33536X6PfZ/s2ca+TDg/gx6WjNn54nXBcZ6BhNcL7xYkhd8KPQuY+h2Ip8Ljq0Q5Nz/X"
    "izRdL8gH1P4/l/q1CNgFwN3v8gh+vSllxGW3j3UkUGrUny7X2/JwKLfr5bTPnhA5CCjPgPyF4ClN"
    "08X6Zzg/OCBE5WbtvdFZnmfaIB8nj/fNOT53+wCeg+sgRCk8RqOi4Pt1rsPi7umJVDpfbcrDr3qm"
    "bsDLp0+/Iagdjaqv//n5EMBBttWbRqvbHyW1H1SrVbWPxN+a2FWvVsvXJ0NoJE/01s7Klryatx9Q"
    "BrwmHylV9W5WTp6HucP55j8NqmesY/8D59b8Kg=="
)

_CHARSET = None


def charset():
    """The 2048 ROM bytes, decoded once."""
    global _CHARSET
    if _CHARSET is None:
        import base64
        import zlib
        _CHARSET = zlib.decompress(base64.b64decode("".join(CHARSET_B64.split())))
        if len(_CHARSET) != 2048:
            raise RuntimeError("embedded charset is damaged")
    return _CHARSET


def glyph(screen_code):
    """The 8 bitmap rows of a screen code, bit set = foreground."""
    cs = charset()
    code = screen_code & 0x7F                 # the ROM half holds 128 glyphs;
    return cs[code * 8:code * 8 + 8]          # bit 7 of a code means reversed


def petscii_to_screen(b):
    """PETSCII byte -> (screen code, reversed), as a listing shows it in quotes.

    This is the C64's own table. Control codes ($00-1F, $80-9F) have no glyph
    of their own; inside quotes the machine prints them as the reversed form
    of the character 64 (or 128) places along, which is what the second
    value says to do. $FF is pi and shows as $5E, the one exception.
    """
    if b == 0xFF:
        return 0x5E, False
    if b < 0x20:
        return b + 0x80, True
    if b < 0x40:
        return b, False
    if b < 0x60:
        return b - 0x40, False
    if b < 0x80:
        return b - 0x20, False
    if b < 0xA0:
        return b + 0x40, True
    if b < 0xC0:
        return b - 0x40, False
    return b - 0x80, False


def quote_column(name):
    """Where the 1541 puts the closing quote: at the first $A0, else after cell 16."""
    cut = bytes(name).find(bytes([PAD_SHIFTED]))
    return 16 if cut == -1 else cut


def listing_cells(name):
    """The 16 cells a listing draws for a name: (petscii byte, reversed, after_quote).

    The drive prints the name up to the first $A0, then the closing quote,
    then whatever bytes follow -- that is the hidden-name trick. Every $A0
    is shown as a space. A cell after the quote still occupies its column,
    so the window can keep one click target per name byte.
    """
    q = quote_column(name)
    cells = []
    for i, b in enumerate(bytes(name)):
        shown = 0x20 if b == PAD_SHIFTED else b
        _, rev = petscii_to_screen(shown)
        cells.append((shown, rev, i >= q))
    return cells


def listing_columns(name):
    """Where each piece of a directory line sits, in character columns.

    Column 0 is the opening quote. The 16 name bytes always occupy columns
    1..16: the drive prints the closing quote *in place of* the first $A0,
    so a padded name never shifts anything. A full 16-character name gets
    its quote appended at column 17 and pushes the file type one column
    right, exactly as a real LIST does.

    Returns (cells, quote_col, type_col) with one cell per name byte:
    (column, raw byte, shown byte, reversed). The raw byte is what a click
    picks ($A0 stays $A0); the shown byte is what gets drawn.
    """
    q = quote_column(name)
    cells = []
    for i, (shown, rev, _after) in enumerate(listing_cells(name)):
        cells.append((1 + i, bytes(name)[i], shown, rev))
    if q < 16:
        return cells, 1 + q, 18
    return cells, 17, 19


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

def parse_char(text):
    """One side of a --replace: two hex digits, or one printable character.

    Hex wins when it parses ("10" is $10, not the two characters 1 and 0),
    because hex is how dir artists talk about PETSCII. A lone letter is
    uppercased, the C64's default charset having no lowercase.
    """
    if len(text) == 2 and all(c in "0123456789abcdefABCDEF" for c in text):
        return int(text, 16)
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


MAX_ROWS = 144      # 18 directory sectors of 8 entries: no disk has more


def parse_rows(text):
    """'1,2,5-7' -> [1, 2, 5, 6, 7]. Raises ValueError on anything else."""
    rows = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        lo, dash, hi = part.partition("-")
        if dash:
            first, last = int(lo), int(hi)
            if last < first or last - first > MAX_ROWS:
                raise ValueError(part)
            rows.extend(range(first, last + 1))
        else:
            rows.append(int(part))
    if not rows:
        raise ValueError("no rows")
    return rows


def format_rows(rows):
    """[1, 2, 3, 7] -> '1-3,7', the shortest way to write a row list."""
    rows = sorted(set(rows))
    parts, i = [], 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1] == rows[j] + 1:
            j += 1
        parts.append(str(rows[i]) if i == j else f"{rows[i]}-{rows[j]}")
        i = j + 1
    return ",".join(parts)


def _rows_arg(text):
    try:
        return parse_rows(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"wants row numbers like 1,2,3 or 1-7, got '{text}'")


def build_parser():
    p = argparse.ArgumentParser(
        prog="dirarttool",
        description="Replace one PETSCII character with another across the "
                    "directory art of a .d64. With no arguments, opens the window.")
    p.add_argument("d64", nargs="?", help="the disk image (rewritten in place unless -o)")
    p.add_argument("--replace", metavar="FROM=TO", action="append",
                   help="character to replace and what with: two hex digits (D6) or "
                        "one printable character (X); repeatable")
    p.add_argument("--mask", metavar="CHAR",
                   help="the Mask: fill every CHAR in the chosen rows with a sign "
                        "taken from the same column of the --mask-from lines")
    p.add_argument("--mask-from", type=_rows_arg, metavar="N-N,...",
                   help="the lines the Mask takes its signs from, like 1-7 "
                        "(needed with --mask)")
    p.add_argument("--seed", type=int, metavar="N",
                   help="fix the Mask's mix, so a run can be repeated exactly")
    p.add_argument("--header-from", metavar="OTHER.D64",
                   help="put the header (disk name) of OTHER.D64 on this disk")
    p.add_argument("--id-from", metavar="OTHER.D64",
                   help="put the ID of OTHER.D64 (the 5 characters after the "
                        "header) on this disk")
    p.add_argument("--rows", type=_rows_arg, metavar="N-N,...",
                   help="only these rows (listing index, 0-based, like 1,2,5-7); "
                        "default: every art row")
    p.add_argument("--all-rows", action="store_true",
                   help="allow real files too (changes their LOAD names)")
    p.add_argument("-o", "--out", metavar="OUT.D64", help="write here instead of in place")
    p.add_argument("--dry-run", action="store_true",
                   help="print the resulting listing, write nothing")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress lines")
    p.add_argument("--version", action="version", version=f"dirarttool {__version__}")
    return p


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------

BLUE = "#40318d"        # Pepto colour 6, the listing background
LIGHT_BLUE = "#867ade"  # Pepto colour 14, the listing ink
GREY = "#808080"
CELL = 8                # glyph size in C64 pixels
CREDITS = "code DR.J/Delysid, idea and test disk Baracuda/Blazon"


class App:
    """The window: the Replacer, the Mask and the header buffer. Holds a tk
    root rather than subclassing it, so the module imports without tkinter
    and the CLI never pays for Tk.

    Every button ends in an engine call: open_path -> load/directory,
    apply -> replace, apply_mask -> mask, paste_header -> write_header,
    save/save_as -> save. The methods are public so a script can drive the
    window the way a person would.
    """

    def __init__(self, scale=2, master=None):
        """`master`: an existing Tk root to hang the window on (then it is a
        Toplevel). Tests use it so one process never builds a second Tcl
        interpreter, which Tcl does not always survive. Normal runs leave it
        None and get their own root."""
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.scale = scale
        self.master = master
        self.path = None
        self.image = None           # the in-memory .d64
        self.on_disk = None         # what the file held when opened or last saved
        self.rows = []
        self.undo_stack = []
        self.src = None             # From, a PETSCII byte
        self.dst = None             # To
        self.mask_char = None       # the Mask's placeholder byte
        self._mask_preview = None   # the image the last Mask made, for "mix again"
        self.clip_header = None     # header buffer: 16 bytes, survives opening disks
        self.clip_id = None         # ID buffer: 5 bytes
        self.row_vars = []          # one BooleanVar per row, made in _reset_ticks
        self.row_checks = []        # and its Checkbutton, kept across redraws
        self.warned_about_files = False
        self._glyphs = {}           # (screen code, reversed) -> PhotoImage

        self.root = tk.Tk() if master is None else tk.Toplevel(master)
        self.root.title(f"dirarttool {__version__}")
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._drop_targets = {}     # hwnd -> the window procedure we replaced (Windows)
        self._dropped = []          # path lists parked by the window procedure
        self._build()
        self._refresh_title()
        self.root.update_idletasks()        # realise the widgets so they have windows to register
        self._install_dropfiles()

    # ---- building the widgets ------------------------------------------

    def _build(self):
        tk, ttk = self.tk, self.ttk
        root = self.root

        menubar = tk.Menu(root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_dialog)
        filemenu.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        filemenu.add_command(label="Save As...", accelerator="Ctrl+Shift+S",
                             command=self.save_as_dialog)
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.quit)
        menubar.add_cascade(label="File", menu=filemenu)
        editmenu = tk.Menu(menubar, tearoff=0)
        editmenu.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        menubar.add_cascade(label="Edit", menu=editmenu)
        headmenu = tk.Menu(menubar, tearoff=0)
        for label, key, command in (
                ("Copy header", "F1", self.copy_header),
                ("Write header", "F3", self.paste_header),
                (None, None, None),
                ("Copy ID", "F5", self.copy_id),
                ("Write ID", "F7", self.paste_id),
                (None, None, None),
                ("Copy header + ID", "F2", self.copy_both),
                ("Write header + ID", "F4", self.paste_both)):
            if label is None:
                headmenu.add_separator()
                continue
            headmenu.add_command(label=label, accelerator=key, command=command)
            root.bind(f"<{key}>", lambda e, c=command: c())
        menubar.add_cascade(label="Header", menu=headmenu)
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="About", command=self.about)
        menubar.add_cascade(label="Help", menu=helpmenu)
        root.config(menu=menubar)
        root.bind("<Control-o>", lambda e: self.open_dialog())
        root.bind("<Control-s>", lambda e: self.save())
        root.bind("<Control-S>", lambda e: self.save_as_dialog())
        root.bind("<Control-z>", lambda e: self.undo())

        bar = ttk.Frame(root, padding=(6, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open...", command=self.open_dialog).pack(side="left")
        self.save_btn = ttk.Button(bar, text="Save", command=self.save)
        self.save_btn.pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Save As...", command=self.save_as_dialog).pack(side="left", padx=(4, 0))
        self.undo_btn = ttk.Button(bar, text="Undo", command=self.undo)
        self.undo_btn.pack(side="left", padx=(12, 0))
        self.file_label = ttk.Label(bar, text="no disk open", foreground=GREY)
        self.file_label.pack(side="right")

        work = ttk.Frame(root)
        work.pack(fill="both", expand=True, padx=6)
        self.notebook = ttk.Notebook(work)
        self.notebook.pack(side="right", fill="y", padx=(6, 0))
        replace_tab = ttk.Frame(self.notebook, padding=4)
        mask_tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(replace_tab, text="Replace")
        self.notebook.add(mask_tab, text="Mask")

        # replace panel
        panel = ttk.LabelFrame(replace_tab, text="Replace", padding=8)
        panel.pack(side="left", fill="y")
        self.from_box = self._picker(panel, "From", 0)
        self.to_box = self._picker(panel, "To", 1)
        self.scope = tk.StringVar(value="ticked")
        ttk.Label(panel, text="Rows").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Radiobutton(panel, text="ticked rows", variable=self.scope, value="ticked",
                        command=self._refresh_apply).grid(row=2, column=1, sticky="w", pady=(10, 0))
        ttk.Radiobutton(panel, text="all rows", variable=self.scope, value="all",
                        command=self._refresh_apply).grid(row=3, column=1, sticky="w")
        self.apply_btn = ttk.Button(panel, text="Apply", command=self.apply)
        self.apply_btn.grid(row=4, column=0, columnspan=2, pady=(14, 4), sticky="ew")
        self.last_label = ttk.Label(panel, text="", foreground=GREY)
        self.last_label.grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Label(panel, text="left click a cell = From\nright click = To",
                  foreground=GREY, justify="left").grid(row=6, column=0, columnspan=2,
                                                        sticky="w", pady=(16, 0))

        # mask panel
        mpanel = ttk.LabelFrame(mask_tab, text="Mask", padding=8)
        mpanel.pack(side="left", fill="y")
        self.mask_box = self._picker(mpanel, "Fill", 0)
        ttk.Label(mpanel, text="Take from").grid(row=1, column=0, sticky="w", pady=2)
        self.mask_sources = tk.StringVar(value="")
        self.mask_sources.trace_add("write", lambda *a: self._sources_changed())
        ttk.Entry(mpanel, textvariable=self.mask_sources, width=14).grid(
            row=1, column=1, sticky="w", pady=2)
        ttk.Label(mpanel, text="Mix no.").grid(row=2, column=0, sticky="w", pady=2)
        self.mask_seed = tk.StringVar(value="1")
        ttk.Entry(mpanel, textvariable=self.mask_seed, width=8).grid(
            row=2, column=1, sticky="w", pady=2)
        self.mask_btn = ttk.Button(mpanel, text="Mix and fill", command=self.apply_mask)
        self.mask_btn.grid(row=3, column=0, columnspan=2, pady=(14, 4), sticky="ew")
        self.mask_label = ttk.Label(mpanel, text="", foreground=GREY)
        self.mask_label.grid(row=4, column=0, columnspan=2, sticky="w")
        ttk.Label(mpanel, text="left click a sign = the sign to fill\n"
                               "right click a line = take signs\n"
                               "from it (or type lines: 1-7)\n\n"
                               "The ticked lines are filled. Press\n"
                               "the button again for another mix.",
                  foreground=GREY, justify="left").grid(row=5, column=0, columnspan=2,
                                                        sticky="w", pady=(16, 0))

        # directory pane, shared by both tabs: a canvas that scrolls if the
        # directory is long
        dir_frame = ttk.LabelFrame(work, text="Directory", padding=4)
        dir_frame.pack(side="left", fill="both", expand=True)
        self.dir_canvas = tk.Canvas(dir_frame, bg=BLUE, highlightthickness=0,
                                    width=self._x(29) + 120, height=self._y(12))
        vbar = ttk.Scrollbar(dir_frame, orient="vertical", command=self.dir_canvas.yview)
        self.dir_canvas.configure(yscrollcommand=vbar.set)
        self.dir_canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")
        self.dir_canvas.bind("<Button-1>", lambda e: self._pick(e, self.dir_canvas, "from"))
        self.dir_canvas.bind("<Button-3>", lambda e: self._pick(e, self.dir_canvas, "to"))
        self.dir_canvas.bind("<Motion>", lambda e: self._hover(e, self.dir_canvas))

        # palette
        pal_frame = ttk.LabelFrame(
            root, text="PETSCII  (Replace: left click = From, right click = To;  "
                       "Mask: left click = the sign to fill)", padding=4)
        pal_frame.pack(fill="x", padx=6, pady=(4, 0))
        side = 16 * (CELL * self.scale + 4) + 4
        self.pal_canvas = tk.Canvas(pal_frame, bg=BLUE, highlightthickness=0,
                                    width=side, height=side)
        self.pal_canvas.pack()
        self.pal_canvas.bind("<Button-1>", lambda e: self._pick(e, self.pal_canvas, "from"))
        self.pal_canvas.bind("<Button-3>", lambda e: self._pick(e, self.pal_canvas, "to"))
        self.pal_canvas.bind("<Motion>", lambda e: self._hover(e, self.pal_canvas))
        self._draw_palette()

        hint = "open a .d64 to begin"
        if sys.platform == "win32":
            hint += ", or drop one on the window"
        self.status = ttk.Label(root, text=hint, anchor="w", padding=(6, 3))
        self.status.pack(fill="x")
        self._refresh_apply()

    def _picker(self, parent, label, row):
        tk, ttk = self.tk, self.ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, sticky="w", pady=2)
        box = tk.Canvas(frame, width=CELL * 3 + 4, height=CELL * 3 + 4, bg=BLUE,
                        highlightthickness=1, highlightbackground=GREY)
        box.pack(side="left")
        text = ttk.Label(frame, text="$--", width=5)
        text.pack(side="left", padx=(6, 0))
        return box, text

    # ---- drawing ------------------------------------------------------------

    def _x(self, cells):
        return cells * CELL * self.scale

    def _y(self, lines):
        return lines * CELL * self.scale

    def _glyph_image(self, screen_code, reversed_, scale=None):
        """A PhotoImage of one glyph, drawn once from the ROM and cached."""
        scale = scale or self.scale
        key = (screen_code & 0x7F, bool(reversed_), scale)
        img = self._glyphs.get(key)
        if img is None:
            rows = glyph(screen_code)
            ink, paper = (BLUE, LIGHT_BLUE) if reversed_ else (LIGHT_BLUE, BLUE)
            img = self.tk.PhotoImage(width=CELL * scale, height=CELL * scale)
            lines = []
            for byte in rows:
                px = " ".join(ink if byte & (0x80 >> bit) else paper for bit in range(CELL))
                lines.append("{" + px + "}")
            img.put(" ".join(lines), to=(0, 0))
            if scale > 1:
                img = img.zoom(scale, scale)
            self._glyphs[key] = img
        return img

    def _put_petscii(self, canvas, b, col, line, tags, reversed_=None, x0=0, y0=0):
        code, rev = petscii_to_screen(b)
        if reversed_ is not None:
            rev = reversed_
        img = self._glyph_image(code, rev)
        canvas.create_image(x0 + self._x(col), y0 + self._y(line), image=img,
                            anchor="nw", tags=tags)

    def _draw_palette(self):
        """All 256 PETSCII bytes in a 16 x 16 grid, a small gap between cells."""
        c = self.pal_canvas
        c.delete("all")
        pitch = CELL * self.scale + 4
        for b in range(256):
            code, rev = petscii_to_screen(b)
            c.create_image(2 + (b % 16) * pitch, 2 + (b // 16) * pitch,
                           image=self._glyph_image(code, rev), anchor="nw",
                           tags=("pal", f"b{b}"))

    def _reset_ticks(self):
        """One checkbox per row, made when a disk is opened and kept across
        redraws so a user's ticks survive Apply and Undo (and nothing leaks)."""
        for cb in self.row_checks:
            cb.destroy()
        self.row_vars, self.row_checks = [], []
        for r in self.rows:
            var = self.tk.BooleanVar(value=(r.kind == "art"))
            var.trace_add("write", lambda *a: self._refresh_apply())
            self.row_vars.append(var)
            self.row_checks.append(self.ttk.Checkbutton(self.dir_canvas, variable=var))

    def _redraw_directory(self):
        c = self.dir_canvas
        c.delete("all")
        if self.image is None:
            return
        name, disk_id, dos = header(self.image)
        x_text = 4                              # checkbox + index + blocks live here
        x_name = 120                            # where the opening quote starts
        quote = ord('"')
        # header line, as LIST prints it
        c.create_text(x_text + 60, self._y(0) + CELL, text="0", fill=LIGHT_BLUE,
                      anchor="e", font=("TkFixedFont",))
        self._put_petscii(c, quote, 0, 0, ("hdr",), x0=x_name)
        for i, b in enumerate(name):
            self._put_petscii(c, 0x20 if b == PAD_SHIFTED else b, 1 + i, 0, ("hdr",), x0=x_name)
        self._put_petscii(c, quote, 17, 0, ("hdr",), x0=x_name)
        for i, b in enumerate(disk_id + b" " + dos):
            self._put_petscii(c, 0x20 if b == PAD_SHIFTED else b, 19 + i, 0, ("hdr",), x0=x_name)
        # one line per row
        for r, cb in zip(self.rows, self.row_checks):
            line = r.index + 1
            y = self._y(line)
            c.create_window(x_text, y + CELL * self.scale // 2, window=cb, anchor="w")
            c.create_text(x_text + 40, y + CELL, text=str(r.index), fill=GREY, anchor="e",
                          font=("TkFixedFont",))
            c.create_text(x_text + 90, y + CELL, text=str(r.blocks), fill=LIGHT_BLUE,
                          anchor="e", font=("TkFixedFont",))
            cells, quote_col, type_col = listing_columns(r.name)
            self._put_petscii(c, quote, 0, line, ("q",), x0=x_name)
            for i, (col, raw, shown, rev) in enumerate(cells):
                # The cell that holds the first $A0 is drawn as the closing quote,
                # as the drive prints it; it stays a click target for $A0.
                drawn = quote if col == quote_col else shown
                self._put_petscii(c, drawn, col, line, ("cell", f"r{r.index}", f"i{i}", f"b{raw}"),
                                  reversed_=rev, x0=x_name)
            if quote_col == 17:
                self._put_petscii(c, quote, 17, line, ("q",), x0=x_name)
            c.create_text(x_name + self._x(type_col), y + CELL, text=r.type_text,
                          fill=LIGHT_BLUE, anchor="w", font=("TkFixedFont",))
            c.create_text(x_name + self._x(type_col) + 40, y + CELL, text=r.kind,
                          fill=GREY, anchor="w", font=("TkFixedFont",))
        c.configure(scrollregion=(0, 0, x_name + self._x(30), self._y(len(self.rows) + 1) + 4))
        self._draw_source_marks()
        self._register_drop_targets()       # the row checkboxes are new windows

    def source_rows(self):
        """The Mask's source lines as the box has them, or None while it does
        not parse or names a line this disk does not have."""
        text = self.mask_sources.get().strip()
        if not text:
            return []
        try:
            rows = sorted(set(parse_rows(text)))
        except ValueError:
            return None
        if rows[-1] >= len(self.rows):
            return None
        return rows

    def _draw_source_marks(self):
        """Label the lines the Mask takes its signs from, right of the row."""
        c = self.dir_canvas
        c.delete("srcmark")
        if self.image is None:
            return
        for i in self.source_rows() or ():
            _cells, _quote, type_col = listing_columns(self.rows[i].name)
            c.create_text(120 + self._x(type_col) + 76, self._y(i + 1) + CELL, text="< take",
                          fill=LIGHT_BLUE, anchor="w", font=("TkFixedFont",),
                          tags=("srcmark",))

    def _sources_changed(self):
        self._draw_source_marks()
        self._refresh_apply()

    def toggle_source(self, row):
        """Right click on a line in the Mask tab: take signs from it, or stop."""
        rows = set(self.source_rows() or ())
        rows.symmetric_difference_update({row})
        self.mask_sources.set(format_rows(rows))

    # ---- drag and drop (Windows) --------------------------------------------

    def _install_dropfiles(self):
        """Accept files dropped from Explorer, with nothing but the stdlib.

        Tk has no drop support of its own. On Windows the shell will post
        WM_DROPFILES to any window flagged with DragAcceptFiles, so every
        window of ours is flagged and given a window procedure that turns
        that one message into drop_paths() and passes everything else on.
        Tk makes a real window per widget, so the children are registered
        too, or a drop on the canvas would show the no-entry cursor.
        Elsewhere this does nothing; Open and the command line remain.
        """
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes
        user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        set_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR,
                                           wintypes.UINT]
        shell32.DragFinish.argtypes = [wintypes.HANDLE]
        WM_DROPFILES, GWLP_WNDPROC = 0x0233, -4

        def proc(hwnd, msg, wparam, lparam):
            # Runs inside Windows' message dispatch, which Tk is in the middle
            # of. Touching Tk from here (even root.after) re-enters _tkinter
            # while it has released the GIL and kills the process. So: read
            # the paths, park them, return. _poll_drops picks them up.
            if msg == WM_DROPFILES:
                paths = []
                for i in range(shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)):
                    n = shell32.DragQueryFileW(wparam, i, None, 0) + 1
                    buf = ctypes.create_unicode_buffer(n)
                    shell32.DragQueryFileW(wparam, i, buf, n)
                    paths.append(buf.value)
                shell32.DragFinish(wparam)
                self._dropped.append(paths)
                return 0
            return user32.CallWindowProcW(self._drop_targets[hwnd], hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(proc)       # kept on self: ctypes must not collect it
        self._wndproc_addr = ctypes.cast(self._wndproc, ctypes.c_void_p).value
        # Windows drops messages sent from a less privileged process to a more
        # privileged one. Explorer is never elevated; if this tool is (started
        # from an admin shell), a drop would silently show the no-entry
        # cursor. Let the three drop-related messages through regardless.
        MSGFLT_ALLOW = 1
        allow = getattr(user32, "ChangeWindowMessageFilterEx", None)
        if allow is not None:
            allow.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]

        def register(hwnd):
            if hwnd and hwnd not in self._drop_targets:
                shell32.DragAcceptFiles(hwnd, True)
                if allow is not None:
                    for m in (WM_DROPFILES, 0x0049, 0x004A):    # + WM_COPYGLOBALDATA, WM_COPYDATA
                        allow(hwnd, m, MSGFLT_ALLOW, None)
                self._drop_targets[hwnd] = set_long(hwnd, GWLP_WNDPROC, self._wndproc_addr)

        ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self._enum_children = ENUM(lambda hwnd, _l: (register(hwnd), True)[1])
        user32.EnumChildWindows.argtypes = [wintypes.HWND, ENUM, wintypes.LPARAM]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self._drop_root = user32.GetAncestor(self.root.winfo_id(), 2)
        self._register_one = register
        self._user32 = user32
        self._register_drop_targets()
        # Widgets inside a notebook tab get their window only when the tab is
        # first shown, after this ran; whenever one of ours is mapped, look again.
        for w in (self.root, self.notebook, self.dir_canvas, self.pal_canvas):
            w.bind("<Map>", lambda e: self.root.after_idle(self._register_drop_targets), add="+")
        self._poll_drops()

    DROP_POLL_MS = 100

    def _poll_drops(self):
        """Open whatever the window procedure parked, from Tk's own timer."""
        import tkinter
        try:
            if not self.root.winfo_exists():
                return
            while self._dropped:
                self.drop_paths(self._dropped.pop(0))
            self.root.after(self.DROP_POLL_MS, self._poll_drops)
        except tkinter.TclError:
            return                      # the window is gone; stop polling

    def _register_drop_targets(self):
        """Flag every window we own, including ones made since the last call."""
        if not self._drop_targets and not hasattr(self, "_register_one"):
            return
        self._register_one(self._drop_root)
        self._user32.EnumChildWindows(self._drop_root, self._enum_children, 0)

    def drop_paths(self, paths):
        """Open the first .d64 among dropped paths; say so when there is none."""
        disks = [p for p in paths if p.lower().endswith(".d64")]
        if not disks:
            names = ", ".join(os.path.basename(p) for p in paths) or "nothing"
            self._error(BadImage(f"drop a .d64 on the window (got {names})"))
            return False
        if not self._ask_discard():
            return False
        return self.open_path(disks[0])

    def _draw_picker(self, box_text, b):
        box, text = box_text
        box.delete("all")
        if b is None:
            text.configure(text="$--")
            return
        code, rev = petscii_to_screen(b)
        box.create_image(2, 2, image=self._glyph_image(code, rev, scale=3), anchor="nw")
        text.configure(text=f"${b:02X}")

    # ---- picking -----------------------------------------------------------

    def _tag_byte(self, canvas):
        for tag in canvas.gettags("current"):
            if tag.startswith("b") and tag[1:].isdigit():
                return int(tag[1:])
        return None

    def _tag_row(self, canvas):
        """The directory row of the cell under the pointer, or None."""
        for tag in canvas.gettags("current"):
            if tag.startswith("r") and tag[1:].isdigit():
                return int(tag[1:])
        return None

    def _pick(self, event, canvas, which):
        if self.notebook.index(self.notebook.select()) == 1:    # the Mask tab
            if which == "to":
                row = self._tag_row(canvas) if canvas is self.dir_canvas else None
                if row is not None:
                    self.toggle_source(row)
                return
            b = self._tag_byte(canvas)
            if b is not None:
                self.set_mask(b)
            return
        b = self._tag_byte(canvas)
        if b is None:
            return
        if which == "from":
            self.set_from(b)
        else:
            self.set_to(b)

    def _hover(self, event, canvas):
        b = self._tag_byte(canvas)
        if b is not None:
            self.status.configure(text=f"${b:02X}")

    def set_from(self, b):
        self.src = b
        self._draw_picker(self.from_box, b)
        self._refresh_apply()

    def set_to(self, b):
        self.dst = b
        self._draw_picker(self.to_box, b)
        self._refresh_apply()

    def set_mask(self, b):
        self.mask_char = b
        self._draw_picker(self.mask_box, b)
        self._refresh_apply()

    # ---- state ------------------------------------------------------------

    @property
    def dirty(self):
        return self.image is not None and self.image != self.on_disk

    def chosen_rows(self):
        if self.scope.get() == "all":
            return [r.index for r in self.rows]
        return [r.index for r, v in zip(self.rows, self.row_vars) if v.get()]

    def ticked_rows(self):
        return [r.index for r, v in zip(self.rows, self.row_vars) if v.get()]

    def _refresh_apply(self):
        ok = (self.image is not None and self.src is not None and self.dst is not None
              and self.src != self.dst and bool(self.chosen_rows()))
        self.apply_btn.configure(state="normal" if ok else "disabled")
        mask_ok = (self.image is not None and self.mask_char is not None
                   and bool(self.ticked_rows()) and bool(self.source_rows()))
        again = self._mask_preview is not None and self.image is self._mask_preview
        self.mask_btn.configure(state="normal" if mask_ok else "disabled",
                                text="Mix again" if again else "Mix and fill")
        self.undo_btn.configure(state="normal" if self.undo_stack else "disabled")
        self.save_btn.configure(state="normal" if self.dirty else "disabled")
        self._refresh_title()

    def _refresh_title(self):
        name = os.path.basename(self.path) if self.path else ""
        flag = "  *unsaved" if self.dirty else ""
        self.root.title(f"dirarttool {__version__}" + (f" - {name}{flag}" if name else ""))
        self.file_label.configure(text=(name + flag) if name else "no disk open")

    # ---- actions -----------------------------------------------------------

    def _error(self, exc):
        from tkinter import messagebox
        messagebox.showerror("dirarttool", str(exc), parent=self.root)

    def _ask_discard(self):
        """True when it is fine to drop the unsaved image."""
        if not self.dirty:
            return True
        from tkinter import messagebox
        answer = messagebox.askyesnocancel(
            "dirarttool", "The disk has unsaved changes. Save them first?", parent=self.root)
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def open_dialog(self):
        from tkinter import filedialog
        if not self._ask_discard():
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Open a .d64",
            filetypes=[("C64 disk image", "*.d64"), ("all files", "*.*")])
        if path:
            self.open_path(path)

    def open_path(self, path):
        """Load a disk; a refused one leaves the window as it was."""
        try:
            image = load(path)
            rows = directory(image)
        except DirArtError as exc:
            self._error(exc)
            return False
        self.path, self.image, self.on_disk, self.rows = path, image, image, rows
        self.undo_stack = []
        self._mask_preview = None
        self.warned_about_files = False
        self.last_label.configure(text="")
        self.mask_label.configure(text="")
        self._reset_ticks()
        if self.source_rows() is None:          # lines the last disk had, this one has not
            self.mask_sources.set("")
        self._redraw_directory()
        self.status.configure(text=f"{os.path.basename(path)}: {len(rows)} rows")
        self._refresh_apply()
        return True

    def apply(self):
        if self.image is None or self.src is None or self.dst is None:
            return 0
        rows = self.chosen_rows()
        files = [r for r in self.rows if r.index in rows and r.kind == "file"
                 and self.src in r.name]
        if files and not self.warned_about_files:
            from tkinter import messagebox
            if not messagebox.askokcancel(
                    "dirarttool",
                    f"This changes the LOAD name of {files[0].load_name}"
                    + (f" and {len(files) - 1} more" if len(files) > 1 else "")
                    + ". Continue?", parent=self.root):
                return 0
            self.warned_about_files = True
        try:
            new, n = replace(self.image, {self.src: self.dst}, rows)
        except DirArtError as exc:
            self._error(exc)
            return 0
        if n == 0:
            self.last_label.configure(text="0 cells changed")
            self.status.configure(text="0 cells changed")
            return 0
        note = f"{n} cells changed"
        self.undo_stack.append(self.image)
        self.image = new
        self.rows = directory(new)
        self._redraw_directory()
        self.last_label.configure(text=f"last: {note}")
        self.status.configure(text=note)
        self._refresh_apply()
        return n

    def apply_mask(self):
        """The Mask button: fill the placeholder in the ticked lines with
        signs mixed from the source lines.

        Pressed again straight after a fill, it takes that fill back and
        mixes anew with the next mix number, so "not this one, another" is
        one click and Undo still returns to the stamp.
        """
        if self.image is None or self.mask_char is None:
            return 0
        sources = self.source_rows()
        if not sources:
            self._error(BadArgs(
                "Say which lines to take the signs from: right click them in the "
                "directory, or type them in 'Take from', like 1-7."))
            return 0
        again = (self._mask_preview is not None and self.image is self._mask_preview
                 and bool(self.undo_stack))
        base = self.undo_stack[-1] if again else self.image
        try:
            text = self.mask_seed.get().strip()
            seed = int(text) if text else 1
        except ValueError:
            self._error(BadArgs("The mix number is a whole number, like 1."))
            return 0
        if again:
            seed += 1
        listing = directory(base)
        ticked = set(self.ticked_rows())
        rows = [r.index for r in listing if r.index in ticked and self.mask_char in r.name]
        files = [listing[i] for i in rows if listing[i].kind == "file"]
        if files and not self.warned_about_files:
            from tkinter import messagebox
            if not messagebox.askokcancel(
                    "dirarttool",
                    f"This changes the LOAD name of {files[0].load_name}"
                    + (f" and {len(files) - 1} more" if len(files) > 1 else "")
                    + ". Continue?", parent=self.root):
                return 0
            self.warned_about_files = True
        try:
            new, filled, left = mask(base, self.mask_char, rows, sources=sources, seed=seed)
        except DirArtError as exc:
            self._error(exc)
            return 0
        note = f", {left} left (no sign to take in that column)" if left else ""
        if filled == 0:
            self.mask_label.configure(text=f"0 cells filled{note}")
            self.status.configure(text=f"0 cells filled{note}")
            return 0
        if not again:
            self.undo_stack.append(self.image)
        self.mask_seed.set(str(seed))
        self.image = self._mask_preview = new
        self.rows = directory(new)
        self._redraw_directory()
        self.mask_label.configure(text=f"mix {seed}: {filled} cells filled{note}")
        self.status.configure(text=f"mask, mix {seed}: {filled} cells filled{note}")
        self._refresh_apply()
        return filled

    # ---- the header buffer -------------------------------------------------

    @staticmethod
    def _shown(raw):
        """Buffer bytes as plain text for the status line."""
        return display_name(bytes(5) + bytes(raw) + bytes(NAME_LEN))[:len(raw)]

    def _copy(self, header, disk_id):
        if self.image is None:
            self.status.configure(text="open a disk first, then copy its header")
            return False
        said = []
        if header:
            self.clip_header = read_header(self.image)
            said.append(f'header "{self._shown(self.clip_header)}"')
        if disk_id:
            self.clip_id = read_id(self.image)
            said.append(f'ID "{self._shown(self.clip_id)}"')
        self.status.configure(text="copied " + " and ".join(said)
                              + " -- open the other disk and write it there")
        return True

    def _paste(self, header, disk_id):
        if self.image is None:
            self.status.configure(text="open a disk first")
            return False
        missing = [what for what, wanted, have in (("header", header, self.clip_header),
                                                   ("ID", disk_id, self.clip_id))
                   if wanted and have is None]
        if missing:
            self.status.configure(text=f"no {' or '.join(missing)} copied yet -- "
                                       f"copy one from a disk first")
            return False
        try:
            new, changed = write_header(self.image,
                                        self.clip_header if header else None,
                                        self.clip_id if disk_id else None)
        except DirArtError as exc:
            self._error(exc)
            return False
        what = " and ".join(w for w, on in (("header", header), ("ID", disk_id)) if on)
        if changed == 0:
            self.status.configure(text=f"this disk already has that {what}")
            return False
        self.undo_stack.append(self.image)
        self.image = new
        self._redraw_directory()
        self.status.configure(text=f"{what} written -- Save to keep it")
        self._refresh_apply()
        return True

    def copy_header(self):
        return self._copy(True, False)

    def paste_header(self):
        return self._paste(True, False)

    def copy_id(self):
        return self._copy(False, True)

    def paste_id(self):
        return self._paste(False, True)

    def copy_both(self):
        return self._copy(True, True)

    def paste_both(self):
        return self._paste(True, True)

    def undo(self):
        if not self.undo_stack:
            return False
        self.image = self.undo_stack.pop()
        self.rows = directory(self.image)
        self._redraw_directory()
        self.last_label.configure(text="undone")
        self._refresh_apply()
        return True

    def save(self):
        if self.image is None or self.path is None:
            return False
        return self.save_as(self.path)

    def save_as_dialog(self):
        from tkinter import filedialog
        if self.image is None:
            return
        stem, _ = os.path.splitext(os.path.basename(self.path))
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save the disk as", defaultextension=".d64",
            initialdir=os.path.dirname(os.path.abspath(self.path)),
            initialfile=f"{stem}-dirart.d64",
            filetypes=[("C64 disk image", "*.d64")])
        if path:
            return self.save_as(path)
        return False

    def save_as(self, path):
        """Write the image atomically; on failure the old file is untouched."""
        try:
            save(path, self.image)
        except DirArtError as exc:
            self._error(exc)
            return False
        self.path, self.on_disk = path, self.image
        self.status.configure(text=f"saved {os.path.basename(path)}")
        self._refresh_apply()
        return True

    def about(self):
        from tkinter import messagebox
        messagebox.showinfo("About dirarttool",
                            f"dirarttool {__version__}\n\n{CREDITS}\n\nMIT licence.",
                            parent=self.root)

    def quit(self):
        if self._ask_discard():
            self.root.destroy()


def run_gui(path):
    """Open the window, with `path` loaded when given. Returns the exit status."""
    app = App()
    if path:
        app.open_path(path)
    app.root.mainloop()
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return run_gui(None)
    if len(argv) == 1 and not argv[0].startswith("-"):
        return run_gui(argv[0])

    args = build_parser().parse_args(argv)
    def say(text):
        # A path can hold characters the output's code page has no place for
        # (a piped run under cp1252, a Japanese file name). The disk is
        # already written by then; a progress line must not turn that into a
        # crash, so such characters are printed escaped.
        try:
            print(text)
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "ascii"
            print(text.encode(enc, "backslashreplace").decode(enc))

    log = (lambda *a, **k: None) if args.quiet else say
    if not args.d64:
        raise BadArgs("which disk? give a .d64")

    mapping = parse_replace(args.replace) if args.replace else {}
    heading = args.header_from is not None or args.id_from is not None
    if not mapping and args.mask is None and not heading:
        raise BadArgs("nothing to do: give --replace FROM=TO, --mask CHAR, "
                      "--header-from or --id-from")
    if args.mask is None and (args.mask_from is not None or args.seed is not None):
        raise BadArgs("--mask-from and --seed only make sense with --mask")
    if args.mask is not None and args.mask_from is None:
        raise BadArgs("--mask needs --mask-from: the lines to take the signs from, "
                      "like --mask-from 1-7")
    data = load(args.d64)
    rows = directory(data)
    chosen = select_rows(rows, args.rows, args.all_rows)
    result, changed = replace(data, mapping, chosen) if mapping else (data, 0)
    filled = left = 0
    if args.mask is not None:
        placeholder = parse_char(args.mask)
        # The Mask fills the stamp: the chosen rows that hold the placeholder.
        now = directory(result)
        mrows = [i for i in chosen if placeholder in now[i].name]
        result, filled, left = mask(result, placeholder, mrows,
                                    sources=args.mask_from, seed=args.seed)
    head = 0
    if heading:
        name = read_header(load(args.header_from)) if args.header_from else None
        disk_id = read_id(load(args.id_from)) if args.id_from else None
        result, head = write_header(result, name, disk_id)

    if args.dry_run:
        print_listing(result, directory(result))
    if mapping:
        log(f"[*] {changed} cells changed")
    if args.mask is not None:
        log(f"[*] mask: {filled} cells filled"
            + (f", {left} left (no sign to take in that column)" if left else ""))
    if heading:
        what = " and ".join(w for w, on in (("header", args.header_from),
                                            ("ID", args.id_from)) if on)
        log(f"[*] {what} taken over, {head} bytes changed")

    if args.dry_run:
        log("[*] dry run -- nothing written")
        return 0

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
