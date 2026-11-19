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


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------

BLUE = "#40318d"        # Pepto colour 6, the listing background
LIGHT_BLUE = "#867ade"  # Pepto colour 14, the listing ink
GREY = "#808080"
CELL = 8                # glyph size in C64 pixels
CREDITS = "code DR.J/Delysid, idea and test disk Baracuda/Blazon"


class App:
    """The Replacer window. Holds a tk root rather than subclassing it, so the
    module imports without tkinter and the CLI never pays for Tk.

    Every button ends in an engine call: open_path -> load/directory,
    apply -> replace, save/save_as -> save. The methods are public so a
    script can drive the window the way a person would.
    """

    def __init__(self, scale=2):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.scale = scale
        self.path = None
        self.image = None           # the in-memory .d64
        self.on_disk = None         # what the file held when opened or last saved
        self.rows = []
        self.undo_stack = []
        self.src = None             # From, a PETSCII byte
        self.dst = None             # To
        self.row_vars = []          # one BooleanVar per row
        self.warned_about_files = False
        self._glyphs = {}           # (screen code, reversed) -> PhotoImage

        self.root = tk.Tk()
        self.root.title(f"dirarttool {__version__}")
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._build()
        self._refresh_title()

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

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=6)
        replace_tab = ttk.Frame(self.notebook, padding=4)
        mask_tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(replace_tab, text="Replace")
        self.notebook.add(mask_tab, text="Mask", state="disabled")
        ttk.Label(mask_tab, text="coming in v2").pack()

        # directory pane: a canvas that scrolls if the directory is long
        dir_frame = ttk.LabelFrame(replace_tab, text="Directory", padding=4)
        dir_frame.pack(side="left", fill="both", expand=True)
        self.dir_canvas = tk.Canvas(dir_frame, bg=BLUE, highlightthickness=0,
                                    width=self._x(22) + 120, height=self._y(12))
        vbar = ttk.Scrollbar(dir_frame, orient="vertical", command=self.dir_canvas.yview)
        self.dir_canvas.configure(yscrollcommand=vbar.set)
        self.dir_canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")
        self.dir_canvas.bind("<Button-1>", lambda e: self._pick(e, self.dir_canvas, "from"))
        self.dir_canvas.bind("<Button-3>", lambda e: self._pick(e, self.dir_canvas, "to"))
        self.dir_canvas.bind("<Motion>", lambda e: self._hover(e, self.dir_canvas))

        # replace panel
        panel = ttk.LabelFrame(replace_tab, text="Replace", padding=8)
        panel.pack(side="left", fill="y", padx=(6, 0))
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

        # palette
        pal_frame = ttk.LabelFrame(root, text="PETSCII  (left click = From, right click = To)",
                                   padding=4)
        pal_frame.pack(fill="x", padx=6, pady=(4, 0))
        side = 16 * (CELL * self.scale + 4) + 4
        self.pal_canvas = tk.Canvas(pal_frame, bg=BLUE, highlightthickness=0,
                                    width=side, height=side)
        self.pal_canvas.pack()
        self.pal_canvas.bind("<Button-1>", lambda e: self._pick(e, self.pal_canvas, "from"))
        self.pal_canvas.bind("<Button-3>", lambda e: self._pick(e, self.pal_canvas, "to"))
        self.pal_canvas.bind("<Motion>", lambda e: self._hover(e, self.pal_canvas))
        self._draw_palette()

        self.status = ttk.Label(root, text="open a .d64 to begin", anchor="w", padding=(6, 3))
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

    def _redraw_directory(self):
        c = self.dir_canvas
        c.delete("all")
        for var in self.row_vars:
            del var
        self.row_vars = []
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
        for r in self.rows:
            line = r.index + 1
            y = self._y(line)
            var = self.tk.BooleanVar(value=(r.kind == "art"))
            var.trace_add("write", lambda *a: self._refresh_apply())
            self.row_vars.append(var)
            cb = self.ttk.Checkbutton(c, variable=var, style="Dir.TCheckbutton")
            c.create_window(x_text, y + CELL * self.scale // 2, window=cb, anchor="w")
            c.create_text(x_text + 40, y + CELL, text=str(r.index), fill=GREY, anchor="e",
                          font=("TkFixedFont",))
            c.create_text(x_text + 90, y + CELL, text=str(r.blocks), fill=LIGHT_BLUE,
                          anchor="e", font=("TkFixedFont",))
            q = quote_column(r.name)
            self._put_petscii(c, quote, 0, line, ("q",), x0=x_name)
            for i, (b, rev, after) in enumerate(listing_cells(r.name)):
                col = 1 + i + (1 if after else 0)      # the closing quote takes a column
                self._put_petscii(c, b, col, line, ("cell", f"r{r.index}", f"i{i}", f"b{b}"),
                                  reversed_=rev, x0=x_name)
            self._put_petscii(c, quote, 1 + q, line, ("q",), x0=x_name)
            tail = 1 + 16 + 1 + (1 if q < 16 else 0)
            c.create_text(x_name + self._x(tail) + 8, y + CELL, text=r.type_text,
                          fill=LIGHT_BLUE, anchor="w", font=("TkFixedFont",))
            c.create_text(x_name + self._x(tail) + 48, y + CELL, text=r.kind,
                          fill=GREY, anchor="w", font=("TkFixedFont",))
        c.configure(scrollregion=(0, 0, x_name + self._x(26), self._y(len(self.rows) + 1) + 4))

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

    def _pick(self, event, canvas, which):
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

    # ---- state ------------------------------------------------------------

    @property
    def dirty(self):
        return self.image is not None and self.image != self.on_disk

    def chosen_rows(self):
        if self.scope.get() == "all":
            return [r.index for r in self.rows]
        return [r.index for r, v in zip(self.rows, self.row_vars) if v.get()]

    def _refresh_apply(self):
        ok = (self.image is not None and self.src is not None and self.dst is not None
              and self.src != self.dst and bool(self.chosen_rows()))
        self.apply_btn.configure(state="normal" if ok else "disabled")
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
        self.last_label.configure(text="")
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
        self.undo_stack.append(self.image)
        self.image = new
        self.rows = directory(new)
        self._redraw_directory()
        self.last_label.configure(text=f"last: {n} cells changed")
        self.status.configure(text=f"{n} cells changed")
        self._refresh_apply()
        return n

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
            self.save_as(path)

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
