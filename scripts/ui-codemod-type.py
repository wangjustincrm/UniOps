#!/usr/bin/env python3
"""
Quiet the type: UPPERCASE tracked labels -> sentence case, 700+ weights -> 600.

    python3 scripts/ui-codemod-type.py <file-or-dir> ...

- A class string holding BOTH `uppercase` and a `tracking-*` class is a label
  style: both are removed (the source text is already Title/sentence case).
  `uppercase` alone is left: it is usually functional (codes typed in caps).
- font-bold / font-extrabold / font-black -> font-semibold.
"""
import re
import sys
from pathlib import Path

STR = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`")


def fix(lit):
    q, body = lit[0], lit[1:-1]
    cls = body.split(' ')
    if 'uppercase' in cls and any(re.fullmatch(r'tracking-[\w\[\].-]+', c) for c in cls):
        cls = [c for c in cls if c != 'uppercase' and not re.fullmatch(r'tracking-[\w\[\].-]+', c)]
    cls = ['font-semibold' if re.fullmatch(r'font-(?:bold|extrabold|black)', c) else c for c in cls]
    # collapse duplicate font-semibold from the mapping
    seen, out = set(), []
    for c in cls:
        if c == 'font-semibold' and c in seen:
            continue
        seen.add(c)
        out.append(c)
    return q + ' '.join(out) + q


def main(argv):
    files = []
    for a in argv:
        p = Path(a)
        files += [p] if p.is_file() else sorted(f for f in p.rglob('*.tsx') if '_retired' not in f.parts)
    n = 0
    for f in files:
        with open(f, encoding='utf-8', newline='') as fh:
            src = fh.read()
        new = STR.sub(lambda m: fix(m.group(0)) if re.search(r'uppercase|font-(?:bold|extrabold|black)\b', m.group(0)) else m.group(0), src)
        if new != src:
            n += 1
            with open(f, 'w', encoding='utf-8', newline='') as fh:
                fh.write(new)
    print(f'changed {n} files')


if __name__ == '__main__':
    main(sys.argv[1:])
