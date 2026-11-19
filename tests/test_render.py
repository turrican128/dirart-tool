"""The embedded character ROM and the rules that turn name bytes into cells."""
import pytest

import dirarttool as dt


def test_charset_is_the_rom_half():
    cs = dt.charset()
    assert len(cs) == 2048
    assert dt.glyph(1) == bytes([0x18, 0x3C, 0x66, 0x7E, 0x66, 0x66, 0x66, 0x00])   # "A"
    assert dt.glyph(0x20) == bytes(8)                                              # space
    assert len(dt.glyph(0xFF)) == 8


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
    assert len(cells) == 16
    assert [c[2] for c in cells] == [False] * 11 + [True] * 5
    assert all(c[0] == 0x20 for c in cells[11:])
    hidden = b"AB\xa0CD" + b"\xa0" * 11
    assert [c[0] for c in dt.listing_cells(hidden)][:5] == [0x41, 0x42, 0x20, 0x43, 0x44]
    assert [c[2] for c in dt.listing_cells(hidden)][:5] == [False, False, True, True, True]


def test_cells_carry_reverse_flag():
    cells = dt.listing_cells(b"\x12AB" + b"\x20" * 13)
    assert cells[0] == (0x12, True, False)
    assert cells[1] == (0x41, False, False)


def test_listing_columns_follow_the_drive():
    """The closing quote replaces the first $A0 in place; a full name pushes it out."""
    full = b"\xd1\xd1  BARACUDA  \xd1\xd1"
    cells, quote_col, type_col = dt.listing_columns(full)
    assert [c[0] for c in cells] == list(range(1, 17))          # cells at columns 1..16
    assert quote_col == 17 and type_col == 19
    padded = b"HELLO" + b"\xa0" * 11
    cells, quote_col, type_col = dt.listing_columns(padded)
    assert [c[0] for c in cells] == list(range(1, 17))          # no column is inserted
    assert quote_col == 6 and type_col == 18                     # the quote sits in the $A0 cell
    assert cells[5][1] == 0xA0 and cells[5][2] == 0x20           # (col, raw byte, shown byte, rev)
    assert cells[0][1] == ord("H") and cells[0][3] is False
