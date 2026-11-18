#!/usr/bin/env python3
"""
Map hand-styled <button>s to Shell Button / MenuItem by their (static) class
string. Only exact, reviewed prefixes are converted; everything else is listed
for a human.

    python3 scripts/ui-codemod-buttons.py [--dry] <file-or-dir> ...

Each rule: class-string prefix -> (component, props, kept layout classes,
default aria-label for icon-only buttons). Children are kept as they are.
"""
import re
import sys
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('el', Path(__file__).with_name('ui-codemod-elements.py'))
_el = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_el)
find_tag_end, add_imports = _el.find_tag_end, _el.add_imports

# (prefix, component, props, keep-classes, default aria-label or None)
RULES = [
    ('absolute right-3 top-1/2 -translate-y-1/2 text-fg-muted hover:text-fg-secondary', 'Button',
     'variant="subtle" size="icon-xs"', 'absolute right-1.5 top-1/2 -translate-y-1/2', 'Clear'),
    ('flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-primary-50 text-left', 'MenuItem', '', '', None),
    ('flex w-full items-center justify-between px-3 py-2 text-sm hover:bg-primary-50 text-left', 'MenuItem', '', 'justify-between', None),
    ('flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-primary-50 transition-colors', 'MenuItem', '', 'gap-3', None),
    ('block w-full text-left px-3 py-2 hover:bg-surface-subtle border-b border-line-subtle', 'MenuItem', '', 'block border-b border-line-subtle', None),
    ('flex w-full items-center gap-2 px-3 py-2 text-sm text-warning-fg hover:bg-warning-soft', 'MenuItem', 'tone="warning"', '', None),
    ('flex w-full items-center gap-2 px-3 py-2 text-sm text-danger-fg hover:bg-danger-soft', 'MenuItem', 'tone="danger"', '', None),
    ('text-fg-muted hover:text-danger-fg', 'Button', 'variant="danger-ghost" size="icon-xs"', '', 'Remove'),
    ('text-fg-faint hover:text-danger-fg', 'Button', 'variant="danger-ghost" size="icon-xs"', '', 'Remove'),
    ('flex h-7 w-7 items-center justify-center rounded text-fg-faint hover:bg-danger-soft', 'Button', 'variant="danger-ghost" size="icon-xs"', '', 'Remove'),
    ('rounded-lg p-1.5 text-fg-muted hover:bg-surface-sunken', 'Button', 'variant="subtle" size="icon-sm"', '', 'Close'),
    ('inline-flex items-center gap-2 h-9 px-5 rounded-lg bg-success-solid text-white', 'Button', 'variant="success"', '', None),
    ('inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary-600 px-3 text-sm font-medium text-white', 'Button', 'size="sm"', '', None),
    ('inline-flex h-8 items-center gap-1.5 rounded-lg bg-success-solid px-3 text-sm font-medium text-white', 'Button', 'variant="success" size="sm"', '', None),
    ('inline-flex h-8 items-center gap-1.5 rounded-lg border border-primary-600 px-3 text-sm font-medium', 'Button', 'variant="outline" size="sm"', '', None),
    ('inline-flex items-center gap-1.5 rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5', 'Button', 'variant="secondary" size="sm"', '', None),
    ('mt-1 text-xs text-primary-600 hover:underline', 'Button', 'variant="link"', 'mt-1 text-xs', None),
    ('text-xs text-primary-600 hover:underline', 'Button', 'variant="link"', 'text-xs', None),
    ('flex w-full items-center justify-center gap-2 rounded-lg bg-primary-600 py-2 text-sm font-medium', 'Button', '', 'w-full', None),
    ('inline-flex items-center gap-1 text-sm text-primary-600 hover:text-primary-700', 'Button', 'variant="link"', 'text-sm', None),
    ('text-xs font-medium text-fg-muted hover:text-fg-secondary', 'Button', 'variant="subtle" size="xs"', '', None),
    ('flex items-center gap-1.5 text-xs font-medium text-fg-muted hover:text-primary-600', 'Button', 'variant="subtle" size="xs"', '', None),
]


def convert(src):
    out, i, used, manual = [], 0, set(), []
    for m in re.finditer(r'<button\b', src):
        if m.start() < i:
            continue
        end = find_tag_end(src, m.start())
        tag = src[m.start():end]
        cm = re.search(r'\sclassName="([^"]*)"', tag)
        rule = None
        if cm:
            rule = next((r for r in RULES if cm.group(1).startswith(r[0])), None)
        elif not re.search(r'\sclassName=', tag):
            # bare <button> holding only an icon: quiet icon button
            rule = ('', 'Button', 'variant="subtle" size="icon-xs"', '', 'Clear')
        close = _find_close(src, end)
        if not rule or close < 0:
            line = src.count('\n', 0, m.start()) + 1
            manual.append((line, (cm.group(1) if cm else '{dynamic}')[:70]))
            continue
        prefix, comp, props, keep, label = rule
        body = tag[len('<button'):]
        body = re.sub(r'\sclassName="[^"]*"', f' className="{keep}"' if keep else '', body, count=1)
        inner = src[end:close]
        icon_only = not re.sub(r'<[^>]*/>|\{[^}]*\}|\s', '', inner)
        if label and icon_only and not re.search(r'\saria-label=', body):
            props = (props + f' aria-label="{label}"').strip()
        # No type is added: a <button> without one is a submit button, and Button keeps that.
        out.append(src[i:m.start()])
        out.append(f'<{comp}{(" " + props) if props else ""}{body}{inner}</{comp}>')
        i = close + len('</button>')
        used.add(comp)
    out.append(src[i:])
    return ''.join(out), used, manual


def _find_close(src, start):
    """Matching </button> for a <button> whose open tag ends at start."""
    depth, k = 1, start
    while depth:
        nxt_open = re.compile(r'<button\b').search(src, k)
        nxt_close = src.find('</button>', k)
        if nxt_close < 0:
            return -1
        if nxt_open and nxt_open.start() < nxt_close:
            depth += 1
            k = find_tag_end(src, nxt_open.start())
        else:
            depth -= 1
            if depth == 0:
                return nxt_close
            k = nxt_close + len('</button>')
    return -1


def main(argv):
    dry = '--dry' in argv
    files = []
    for a in (x for x in argv if not x.startswith('--')):
        p = Path(a)
        files += [p] if p.is_file() else sorted(f for f in p.rglob('*.tsx') if '_retired' not in f.parts)
    for f in files:
        with open(f, encoding='utf-8', newline='') as fh:
            src = fh.read()
        nl = '\r\n' if '\r\n' in src else '\n'
        body = src.replace('\r\n', '\n')
        new, used, manual = convert(body)
        local_ok = {n for n in used if re.search(rf"import\s*\{{[^}}]*\b{n}\b[^}}]*\}}\s*from\s*'@/components/ui/", new)}
        new = add_imports(new, used, local_ok)
        if new != body and not dry:
            with open(f, 'w', encoding='utf-8', newline='') as fh:
                fh.write(new.replace('\n', nl))
        if new != body or manual:
            print(f'{f}: converted {sorted(used)}' + ''.join(f'\n    MANUAL line {ln}: {c}' for ln, c in manual))


if __name__ == '__main__':
    main(sys.argv[1:])
