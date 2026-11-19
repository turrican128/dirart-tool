#!/usr/bin/env python3
"""Rebuild tests/fixtures/golden-baracuda-replaced.d64 and prove it is right.

    python tools/make_golden.py            rebuild and check
    python tools/make_golden.py --check    only check the committed copy

The golden image is Baracuda's test disk after the two Replacer passes that
define v1 ($D6 -> $D1 and $D7 -> $D1 on every row). tools/qa.py compares the
tool's output against it byte for byte, so it must never be edited by hand:
this script derives it from the engine and then proves, with the independent
test reader, that it is exactly the fixture with four bytes changed.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import d64reader                     # noqa: E402
import dirarttool as dt              # noqa: E402

SRC = os.path.join(ROOT, "tests", "fixtures", "baracuda-dir-file.d64")
DST = os.path.join(ROOT, "tests", "fixtures", "golden-baracuda-replaced.d64")


def expected():
    data = dt.load(SRC)
    out, n = dt.replace(data, {0xD6: 0xD1, 0xD7: 0xD1}, [0, 1, 2])
    assert n == 4, n
    rows = d64reader.read_directory(out)
    assert [r.name_exact for r in rows] == [b"\xd1\xd1  BARACUDA  \xd1\xd1"] * 3
    diff = [i for i in range(len(data)) if data[i] != out[i]]
    assert len(diff) == 4, diff
    return out


def main(argv):
    want = expected()
    if "--check" in argv:
        with open(DST, "rb") as fh:
            got = fh.read()
        if got != want:
            sys.exit("golden image is stale or hand-edited: rebuild with tools/make_golden.py")
        print("golden image verified")
        return
    with open(DST, "wb") as fh:
        fh.write(want)
    print(f"wrote {DST} ({len(want)} bytes, 4 bytes differ from the source disk)")


if __name__ == "__main__":
    main(sys.argv[1:])
