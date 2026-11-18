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


def comment_spans(src):
    """(start, end) of // line comments, /* */ and {/* */} blocks — tags in them are prose."""
    spans = []
    for m in re.finditer(r'\{/\*.*?\*/\}|/\*.*?\*/|(?<![:"\'`\\])//[^\n]*', src, re.S):
        spans.append((m.start(), m.end()))
    return spans


def in_spans(i, spans):
    return any(a <= i < b for a, b in spans)
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('el', Path(__file__).with_name('ui-codemod-elements.py'))
_el = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_el)
find_tag_end, add_imports = _el.find_tag_end, _el.add_imports

# (prefix, component, props, keep-classes, default aria-label or None)
HEUR_LOG = []

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
    # ── E3 additions ──
    ('text-2xs text-primary-600 hover:underline', 'Button', 'variant="link"', 'text-2xs', None),
    ('mt-1 text-2xs text-primary-600 hover:underline', 'Button', 'variant="link"', 'mt-1 text-2xs', None),
    ('mt-1 block text-2xs font-medium text-primary-700 hover:underline', 'Button', 'variant="link"', 'mt-1 text-2xs', None),
    ('inline-flex items-center gap-1 text-primary-700 hover:underline', 'Button', 'variant="link"', 'gap-1', None),
    ('inline-flex shrink-0 items-center gap-1 font-medium text-primary-700 hover:underline', 'Button', 'variant="link"', 'shrink-0 gap-1', None),
    ('text-primary-600 underline', 'Button', 'variant="link"', 'underline', None),
    ('text-xs font-medium text-primary-600 underline hover:text-primary-700', 'Button', 'variant="link"', 'text-xs underline', None),
    ('inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700 font-medium', 'Button', 'variant="link"', 'gap-1 text-xs', None),
    ('flex items-center gap-1 text-xs font-medium text-primary-600 hover:text-primary-700', 'Button', 'variant="link"', 'gap-1 text-xs', None),
    ('self-start text-xs text-warning-fg underline-offset-2 hover:underline', 'Button', 'variant="link"', 'self-start text-xs text-warning-fg', None),
    ('text-xs font-medium text-danger-fg hover:text-danger-800', 'Button', 'variant="link"', 'text-xs text-danger-fg', None),
    ('text-xs text-fg-muted hover:text-fg-secondary', 'Button', 'variant="subtle" size="xs"', '', None),
    ('absolute right-2 top-1/2 -translate-y-1/2 text-fg-muted hover:text-fg-secondary', 'Button', 'variant="subtle" size="icon-xs"', 'absolute right-1.5 top-1/2 -translate-y-1/2', 'Clear'),
    ('absolute right-2.5 top-1/2 -translate-y-1/2 text-fg-muted hover:text-fg-secondary', 'Button', 'variant="subtle" size="icon-xs"', 'absolute right-1.5 top-1/2 -translate-y-1/2', 'Clear'),
    ('text-fg-faint hover:text-fg-muted', 'Button', 'variant="subtle" size="icon-xs"', '', None),
    ('w-full text-left px-4 py-3 hover:bg-primary-50 border-b border-line-subtle last:border-0', 'MenuItem', '', 'px-4 py-3 border-b border-line-subtle last:border-0', None),
    ('block w-full px-3 py-2 text-left text-sm text-fg-secondary hover:bg-surface-subtle', 'MenuItem', '', 'block', None),
    ('inline-flex items-center gap-1 rounded-lg border border-line-strong bg-surface px-2.5 py-1 text-xs font-medium text-fg-secondary', 'Button', 'variant="secondary" size="xs"', '', None),
    ('flex items-center gap-1 rounded-lg border border-line px-2 py-1.5 text-xs font-medium text-fg-secondary', 'Button', 'variant="secondary" size="xs"', 'shrink-0', None),
    ('inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium text-fg-secondary', 'Button', 'variant="secondary" size="xs"', '', None),
    ('inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-xs text-fg-secondary', 'Button', 'variant="secondary" size="xs"', '', None),
    ('inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-fg-secondary', 'Button', 'variant="secondary" size="xs"', '', None),
    ('flex items-center gap-1 rounded-lg bg-primary-50 border border-primary-200 px-2 py-1.5', 'Button', 'variant="outline" size="xs"', 'shrink-0', None),
    ('inline-flex items-center gap-1.5 rounded-lg border border-primary-300 bg-primary-50 px-2.5 py-1', 'Button', 'variant="outline" size="xs"', '', None),
    ('inline-flex items-center rounded-lg border border-danger-line bg-danger-soft px-2.5 py-1', 'Button', 'variant="danger-outline" size="xs"', '', None),
    ('inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-xs font-medium text-danger-fg', 'Button', 'variant="danger-outline" size="xs"', '', None),
    ('inline-flex items-center gap-1.5 rounded-lg border border-warning-line bg-warning-soft px-2.5 py-1', 'Button', 'variant="warning-outline" size="xs"', '', None),
    ('mt-1 inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-danger-fg hover:bg-danger-soft', 'Button', 'variant="danger-ghost" size="sm"', 'mt-1 text-danger-fg', None),
    ('rounded-md p-1.5 text-fg-muted hover:bg-danger-soft hover:text-danger-fg', 'Button', 'variant="danger-ghost" size="icon-sm"', '', 'Remove'),
    ('rounded-lg p-1 text-fg-faint hover:text-danger-fg hover:bg-danger-soft', 'Button', 'variant="danger-ghost" size="icon-xs"', '', 'Remove'),
    ('rounded p-1 text-fg-faint hover:text-danger-fg hover:bg-danger-soft', 'Button', 'variant="danger-ghost" size="icon-xs"', '', 'Remove'),
    ('rounded p-1 text-fg-muted hover:text-danger-fg hover:bg-danger-soft', 'Button', 'variant="danger-ghost" size="icon-xs"', '', 'Remove'),
    ('py-1.5 text-fg-faint hover:text-danger-fg sm:py-0', 'Button', 'variant="danger-ghost" size="icon-xs"', '', 'Remove'),
    ('ml-auto text-fg-muted hover:text-danger-fg', 'Button', 'variant="danger-ghost" size="icon-xs"', 'ml-auto', 'Remove'),
    ('rounded p-1 text-fg-muted transition-colors hover:bg-surface-sunken hover:text-fg-secondary', 'Button', 'variant="subtle" size="icon-xs"', '', 'Close'),
    ('inline-flex items-center gap-1.5 rounded-lg bg-inverse-raised px-3 py-1.5 text-xs font-medium text-fg-on-inverse', 'Button', 'variant="inverse" size="xs"', '', None),
    ('rounded-lg bg-inverse-raised p-1.5 text-fg-on-inverse hover:bg-inverse-hover', 'Button', 'variant="inverse" size="icon-sm"', '', None),
    ('absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-inverse-raised/90 p-2 text-white', 'Button', 'variant="inverse" size="icon"', 'absolute left-2 top-1/2 -translate-y-1/2 rounded-full disabled:opacity-20', 'Previous page'),
    ('absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-inverse-raised/90 p-2 text-white', 'Button', 'variant="inverse" size="icon"', 'absolute right-2 top-1/2 -translate-y-1/2 rounded-full disabled:opacity-20', 'Next page'),
    ('rounded p-1 hover:bg-inverse-raised disabled:opacity-30', 'Button', 'variant="inverse-ghost" size="icon-xs"', '', None),
]


LAYOUT = re.compile(r'^(?:-?m[trblxy]?-[\w.]+|self-[\w-]+|shrink-0|grow|flex-1|w-full|w-\d+|max-w-[\w\[\].-]+|min-w-[\w\[\].-]+|'
                    r'absolute|relative|top-[\w/.-]+|left-[\w/.-]+|right-[\w/.-]+|bottom-[\w/.-]+|-?translate-[xy]-[\w/.-]+|'
                    r'truncate|ml-auto|mr-auto|order-[\w-]+|hidden|(?:sm|md|lg):[\w-]+|justify-[\w-]+|z-[\w\[\]-]+)$')


def heuristic(cls, icon_only):
    c = cls.split()
    has = lambda *xs: any(x in c for x in xs)
    pre = lambda p: any(x.startswith(p) for x in c)
    keep = ' '.join(x for x in c if LAYOUT.match(x))
    # Classes that change what the text looks like survive (mono figures, size).
    looks = ' '.join(x for x in c if x in ('font-mono', 'tabular-nums', 'text-sm', 'text-xs', 'text-2xs', 'text-right', 'text-left'))
    sq = next((x[2:] for x in c if re.fullmatch(r'w-\d+', x) and f'h-{x[2:]}' in c), None)  # square button
    if has('w-full') and has('px-3', 'px-4') and has('py-2', 'py-2.5', 'py-3') and not has('border', 'rounded-lg') and not pre('bg-primary') and not pre('border-l'):
        tone = 'tone="danger"' if pre('text-danger') else 'tone="warning"' if pre('text-warning') else ''
        return 'MenuItem', tone, ' '.join(x for x in c if LAYOUT.match(x) and x != 'w-full'), None
    small = has('text-xs', 'text-2xs') or has('py-1', 'py-0.5', 'h-7')
    medium = has('py-2', 'py-2.5', 'h-9', 'h-10')
    size = 'xs' if small else 'md' if medium else 'sm'
    def solid(v):
        return v, f'size="{size}"'
    if sq and not (pre('bg-primary-6') or pre('bg-primary-7') or has('bg-success-solid', 'bg-danger-solid', 'bg-warning-solid')):
        isz = 'icon' if sq in ('9', '10') else 'icon-sm' if sq == '8' else 'icon-xs'
        keep_sq = ' '.join(x for x in keep.split() if not re.fullmatch(r'w-\d+|justify-center', x))
        if has('border'):
            return 'Button', f'variant="secondary" size="{isz}"', (keep_sq + ' ' + looks).strip(), None
        if pre('text-danger'):
            return 'Button', f'variant="subtle" size="{isz}"', (keep_sq + ' text-danger-fg hover:bg-danger-soft').strip(), None
        if pre('text-primary') or pre('text-accent'):
            return 'Button', f'variant="ghost" size="{isz}"', keep_sq, None
        if any(x.startswith('hover:text-danger') or x.startswith('hover:bg-danger') for x in c):
            return 'Button', f'variant="danger-ghost" size="{isz}"', keep_sq, 'Remove'
        return 'Button', f'variant="subtle" size="{isz}"', keep_sq, None
    if pre('bg-primary-6') or pre('bg-primary-7') or pre('bg-accent') and (has('text-white') or pre('text-fg-on')):
        v, sz = solid('primary')
    elif has('bg-success-solid'):
        v, sz = solid('success')
    elif has('bg-danger-solid'):
        v, sz = solid('danger')
    elif has('bg-warning-solid'):
        v, sz = solid('warning')
    elif has('border-warning-line'):
        v, sz = 'warning-outline', f'size="{size}"'
    elif (icon_only or sq) and False:
        pass
    elif has('border-danger-line') or (has('border') and pre('text-danger')):
        v, sz = 'danger-outline', f'size="{size}"'
    elif has('border') and (pre('border-primary') or pre('border-accent')):
        v, sz = 'outline', f'size="{size}"'
    elif has('border') and (pre('border-line') or True) and has('border'):
        v, sz = 'secondary', f'size="{size}"'
    elif icon_only or sq:
        isz = 'icon' if sq in ('9', '10') else 'icon-sm' if (sq == '8' or has('p-1.5', 'p-2')) else 'icon-xs'
        keep_sq = ' '.join(x for x in keep.split() if not re.fullmatch(r'w-\d+|justify-center', x))
        if pre('text-danger'):            # always red (delete): keep the colour
            return 'Button', f'variant="subtle" size="{isz}"', (keep_sq + ' text-danger-fg hover:bg-danger-soft').strip(), None
        if pre('text-primary') or pre('text-accent'):
            return 'Button', f'variant="ghost" size="{isz}"', keep_sq, None
        if any(x.startswith('hover:text-danger') or x.startswith('hover:bg-danger') for x in c):
            return 'Button', f'variant="danger-ghost" size="{isz}"', keep_sq, 'Remove'
        if sq and has('border'):
            return 'Button', f'variant="secondary" size="{isz}"', (keep_sq + ' ' + looks).strip(), None
        return 'Button', f'variant="subtle" size="{isz}"', keep_sq, None
    elif not has('border') and any(x.startswith('hover:text-danger') for x in c) and not pre('text-danger'):
        return 'Button', 'variant="danger-ghost" size="xs"', keep, None
    elif (pre('text-primary') or pre('text-accent')) and any(re.fullmatch(r'p[xy]-[\d.]+', x) for x in c) and pre('rounded'):
        return 'Button', 'variant="ghost" size="xs"', keep, None
    elif pre('text-primary') or pre('text-accent') or any(x in ('underline', 'hover:underline') for x in c):
        tone = ' text-danger-fg' if pre('text-danger') else ' text-fg-muted' if pre('text-fg-muted') else ''
        txt = looks
        return 'Button', 'variant="link"', ' '.join(filter(None, [keep, txt + tone])), None
    elif pre('text-fg'):
        return 'Button', 'variant="subtle" size="xs"', (keep + ' ' + ' '.join(x for x in looks.split() if x != 'text-xs')).strip(), None
    else:
        return None
    props = f'variant="{v}" {sz}' if v != 'primary' else sz
    return 'Button', props, keep, None


def convert(src, use_heuristic=False):
    out, i, used, manual = [], 0, set(), []
    spans = comment_spans(src)
    for m in re.finditer(r'<button\b', src):
        if m.start() < i or in_spans(m.start(), spans):
            continue
        end = find_tag_end(src, m.start())
        tag = src[m.start():end]
        cm = re.search(r'\sclassName="([^"]*)"', tag)
        rule = None
        inner_preview = src[end:_find_close(src, end)] if _find_close(src, end) > 0 else ''
        if cm:
            rule = next((r for r in RULES if cm.group(1).startswith(r[0])), None)
            if not rule and use_heuristic:
                h = heuristic(cm.group(1), not re.sub(r'<[^>]*/>|\s', '', inner_preview))
                if h:
                    rule = (cm.group(1),) + h
                    HEUR_LOG.append((src.count('\n', 0, m.start()) + 1, cm.group(1)[:80], h[0], h[1], h[2]))
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
        # Icon-only = nothing but self-closing elements (an {expr} may be text).
        icon_only = not re.sub(r'<[^>]*/>|\s', '', inner)
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
    # --heuristic only writes with an explicit --write: its guesses must be reviewed first.
    dry = '--dry' in argv or ('--heuristic' in argv and '--write' not in argv)
    files = []
    for a in (x for x in argv if not x.startswith('--')):
        p = Path(a)
        files += [p] if p.is_file() else sorted(f for f in p.rglob('*.tsx') if '_retired' not in f.parts)
    for f in files:
        with open(f, encoding='utf-8', newline='') as fh:
            src = fh.read()
        nl = '\r\n' if '\r\n' in src else '\n'
        body = src.replace('\r\n', '\n')
        HEUR_LOG.clear()
        new, used, manual = convert(body, '--heuristic' in argv)
        local_ok = {n for n in used if re.search(rf"import\s*\{{[^}}]*\b{n}\b[^}}]*\}}\s*from\s*'@/components/ui/", new)}
        new = add_imports(new, used, local_ok)
        if new != body and not dry:
            with open(f, 'w', encoding='utf-8', newline='') as fh:
                fh.write(new.replace('\n', nl))
        if new != body or manual:
            print(f'{f}: converted {sorted(used)}' + ''.join(f'\n    MANUAL line {ln}: {c}' for ln, c in manual)
                  + ''.join(f'\n    HEUR  line {ln}: {c}  ->  {comp} {props} [{keep}]' for ln, c, comp, props, keep in HEUR_LOG))


if __name__ == '__main__':
    main(sys.argv[1:])
