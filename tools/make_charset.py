#!/usr/bin/env python3
r"""Turn a C64 character ROM into the CHARSET_B64 blob embedded in dirarttool.py.

    python tools/make_charset.py C:\C64\GTK3VICE-3.7-win64\C64\chargen-901225-01.bin

Reads the 4096-byte ROM, keeps the first 2048 bytes (the uppercase/graphics
set, the one a directory listing uses), compresses them and prints a Python
assignment ready to paste over the existing one. The ROM is Commodore's and
ships inside every VICE download; the tool embeds it so the binary needs no
file next to it.
"""
import base64
import sys
import zlib


def main(path):
    with open(path, "rb") as fh:
        rom = fh.read()
    if len(rom) not in (2048, 4096):
        sys.exit(f"{path}: {len(rom)} bytes is not a C64 character ROM (2048 or 4096)")
    blob = base64.b64encode(zlib.compress(rom[:2048], 9)).decode("ascii")
    print("CHARSET_B64 = (")
    for i in range(0, len(blob), 76):
        print(f'    "{blob[i:i + 76]}"')
    print(")")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
