#!/usr/bin/env python3
"""Reproduce the Mask acceptance evidence without writing the input disk."""
import hashlib
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dirarttool as dt

p = Path(__file__).resolve().parents[1] / 'tests/fixtures/baracuda-mask-disk.d64'
data = p.read_bytes()
rows = dt.directory(data)
targets = [r.index for r in rows if r.kind == 'art' and 0x31 in r.name]
print('Mask acceptance evidence. All indexes are zero-based.')
print('Input SHA256:', hashlib.sha256(data).hexdigest())
print('Input length:', len(data))
print('Targets:', targets)
for label, chosen, sources in [('Single target', [28], [1]), ('Full stamp', targets, list(range(1, 8)))]:
    result, filled, left = dt.mask(data, 0x31, chosen, sources=sources, seed=7)
    after = dt.directory(result)
    allowed = {r.entry_offset + 5 + j for r in rows if r.index in chosen
               for j, b in enumerate(r.name) if b == 0x31}
    changed = {j for j, (a,b) in enumerate(zip(data,result)) if a != b}
    assert changed == allowed
    assert all(a.name == b.name for a,b in zip(rows,after) if a.index not in chosen)
    assert all(a.name[j] == b.name[j] for a,b in zip(rows,after)
               for j in range(16) if a.name[j] != 0x31)
    assert all(a.name == b.name for a,b in zip(rows,after) if a.kind == 'file')
    assert p.read_bytes() == data
    print('\n' + label + ': sources=' + ','.join(map(str,sources)) + '; seed=7')
    print('Filled:', filled, 'Unfilled:', left, 'Actual changed bytes:', len(changed))
    print('Every changed byte is a target $31. All other bytes are identical: PASS')
    print('Input remains identical: PASS')
    print('Before/after raw PETSCII hex:')
    for i in chosen[:3] + [51,52,53]:
        print('Row', i, rows[i].kind)
        print('  before:', rows[i].name.hex(' ').upper())
        print('  after: ', after[i].name.hex(' ').upper())
    if label == 'Full stamp':
        print('Distinct result rows:', len({after[i].name for i in chosen}), '/', len(chosen))
print('\nNo input disk was written.')
