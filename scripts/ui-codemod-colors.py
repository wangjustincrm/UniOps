#!/usr/bin/env python3
"""
Rewrite Tailwind colour classes in .tsx/.ts files to UniOps semantic tokens.

    python3 scripts/ui-codemod-colors.py [--dry] <path> [<path> ...]

What it changes (inside any string — className, cn(), cva, template literals):

  neutral/gray/slate/zinc/stone  -> fg / fg-secondary / fg-muted / fg-faint,
                                    surface-* / inverse, line-*
  bg-white (no opacity)          -> bg-surface
  red/rose                       -> danger-*      amber/yellow/orange -> warning-*
  green/emerald/lime             -> success-*     blue/sky            -> info-*
  teal/cyan                      -> primary-*
  status scales (danger-600 …)   -> danger-fg / -soft / -line / -solid where the
                                    shade has an obvious role; other shades keep
                                    their primitive name (still a token)
  text-[9px] text-[10px] text-[11px] -> text-2xs
  #085E5E / #064A4A … in arbitrary classes -> primary-* utilities

What it leaves alone and REPORTS (needs a human): purple/violet/indigo/fuchsia/
pink classes, hex/rgb in style objects or chart props.

Variant prefixes (hover:, md:, group-hover:, data-[x]:) and opacity suffixes
(/40) are preserved. Idempotent: running twice changes nothing more.
"""
import re
import sys
from pathlib import Path

NEUTRALS = 'neutral|gray|slate|zinc|stone'
STATUS = {
    'red': 'danger', 'rose': 'danger',
    'amber': 'warning', 'yellow': 'warning', 'orange': 'warning',
    'green': 'success', 'emerald': 'success', 'lime': 'success',
    'blue': 'info', 'sky': 'info',
    'teal': 'primary', 'cyan': 'primary',
}
MANUAL_HUES = 'purple|violet|indigo|fuchsia|pink'
TEXTISH = {'text', 'fill', 'stroke', 'decoration', 'caret', 'placeholder'}
LINEISH = {'border', 'border-t', 'border-b', 'border-l', 'border-r', 'border-x', 'border-y',
           'divide', 'ring', 'ring-offset', 'outline', 'shadow'}
BGISH = {'bg', 'from', 'via', 'to', 'accent'}

UTIL = r'(?:text|bg|border(?:-[trblxy])?|divide|ring(?:-offset)?|outline|placeholder|fill|stroke|from|via|to|decoration|caret|shadow|accent)'
# prefix: variant chain like "hover:" "md:group-hover:" "data-[state=on]:" "[&>svg]:"
CLASS_RE = re.compile(
    r'(?P<pre>(?<![\w-])(?:[!\w\-\[\]&>=.:\'"*@()/]+?:)*)'
    r'(?P<util>' + UTIL + r')-(?P<hue>[a-z]+)-(?P<shade>50|[1-9]00|950)(?P<op>/\d{1,3})?(?![\w-])'
)


def neutral(util: str, shade: int) -> str | None:
    if util in TEXTISH:
        if shade >= 800: return 'fg'
        if shade >= 600: return 'fg-secondary'
        if shade >= 400: return 'fg-muted'
        return 'fg-faint'
    if util in LINEISH:
        if shade <= 100: return 'line-subtle'
        if shade <= 200: return 'line'
        if shade <= 500: return 'line-strong'
        return 'fg-muted'
    if util in BGISH:
        if shade <= 50: return 'surface-subtle'
        if shade <= 100: return 'surface-sunken'
        if shade <= 300: return 'surface-strong'
        if shade <= 600: return 'fg-faint'
        return 'inverse'
    return None


def status(util: str, scale: str, shade: int) -> str:
    """Status scale -> semantic role when the shade's role is unambiguous."""
    if util in TEXTISH:
        return f'{scale}-fg' if shade >= 500 else f'{scale}-{shade}'
    if util in LINEISH:
        if 100 <= shade <= 300: return f'{scale}-line'
        if shade >= 500: return f'{scale}-solid'
        return f'{scale}-{shade}'
    if util in BGISH:
        if shade <= 100: return f'{scale}-soft'
        if 500 <= shade <= 600: return f'{scale}-solid'
        return f'{scale}-{shade}'
    return f'{scale}-{shade}'


def rewrite_class(m: re.Match, report: list) -> str:
    pre, util, hue, shade, op = m['pre'], m['util'], m['hue'], int(m['shade']), m['op'] or ''
    if re.fullmatch(NEUTRALS, hue):
        role = neutral(util, shade)
        if role and util == 'text' and pre.endswith('placeholder:'):
            role = 'fg-faint'
        return f'{pre}{util}-{role}{op}' if role else m[0]
    if hue in STATUS:
        scale = STATUS[hue]
        if scale == 'primary':
            return f'{pre}{util}-primary-{shade}{op}'
        return f'{pre}{util}-{status(util, scale, shade)}{op}'
    if hue in ('danger', 'warning', 'success', 'info'):
        if op:  # keep e.g. bg-danger-600/10 as is
            return m[0]
        return f'{pre}{util}-{status(util, hue, shade)}'
    if re.fullmatch(MANUAL_HUES, hue):
        report.append(m[0])
    return m[0]


HEX_PRIMARY = {
    '085e5e': 'primary-700', '064a4a': 'primary-800', '0a7c7c': 'primary-600', '053838': 'primary-900',
    '0f9999': 'primary-500', 'e5f2f2': 'primary-50', 'c4e0e0': 'primary-100',
}
ARB_HEX_RE = re.compile(r'(?P<pre>(?:[\w\-\[\]&>=.:]+?:)*)(?P<util>text|bg|border|ring|fill|stroke|from|to|via)-\[#(?P<hex>[0-9a-fA-F]{6})\]')
SMALL_TEXT_RE = re.compile(r'(?<![\w-])((?:[\w\-\[\]&>=.:]+?:)*)text-\[(?:9|10|11)px\]')
BG_WHITE_RE = re.compile(r'(?<![\w/-])((?:[\w\-\[\]&>=.:]+?:)*)bg-white(?![\w/-])')
STYLE_HEX_RE = re.compile(r'["\']#[0-9a-fA-F]{3,8}["\']|rgba?\(')


def rewrite(text: str, report: list) -> str:
    text = CLASS_RE.sub(lambda m: rewrite_class(m, report), text)
    def hexsub(m):
        tok = HEX_PRIMARY.get(m['hex'].lower())
        return f"{m['pre']}{m['util']}-{tok}" if tok else m[0]
    text = ARB_HEX_RE.sub(hexsub, text)
    text = SMALL_TEXT_RE.sub(lambda m: f'{m[1]}text-2xs', text)
    text = BG_WHITE_RE.sub(lambda m: f'{m[1]}bg-surface', text)
    return fix_collapse(text)



# ── Hover collapse ────────────────────────────────────────────────────────────
# Two different shades (base vs hover:/active:) can land on the same role, e.g.
# bg-amber-500 hover:bg-amber-600 -> bg-warning-solid hover:bg-warning-solid,
# leaving the control with no hover feedback. When base and state share util +
# role inside one class string, the state class goes one step darker.
DARKER = {
    'fg-faint': 'fg-muted', 'fg-muted': 'fg-secondary', 'fg-secondary': 'fg',
    'surface': 'surface-subtle', 'surface-subtle': 'surface-sunken',
    'surface-sunken': 'surface-strong', 'surface-strong': 'line-strong',
    'line-subtle': 'line', 'line': 'line-strong', 'line-strong': 'fg-faint',
    'accent': 'accent-hover', 'accent-soft': 'primary-100', 'accent-fg': 'primary-800',
}
for _s in ('danger', 'warning', 'success', 'info'):
    DARKER.update({f'{_s}-fg': f'{_s}-800', f'{_s}-solid': f'{_s}-700',
                   f'{_s}-soft': f'{_s}-100', f'{_s}-line': f'{_s}-300'})
for _n in range(50, 900, 100):
    for _s in ('primary', 'danger', 'warning', 'success', 'info'):
        _k = f'{_s}-{_n}'
        DARKER.setdefault(_k, f'{_s}-{100 if _n == 50 else _n + 100}')

ROLE = r'(?:fg(?:-[a-z]+)?|surface(?:-[a-z]+)?|line(?:-[a-z]+)?|accent(?:-[a-z]+)?|(?:danger|warning|success|info|primary)-(?:fg|solid|soft|line|\d{2,3}))'
TOKEN_RE = re.compile(r'(?<![\w-])(?P<pre>(?:[\w\-\[\]&>=.:]+:)?)(?P<util>bg|text|border|ring|outline|divide)-(?P<role>' + ROLE + r')(?![\w/-])')
STRING_RE = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`")  # one line: a multi-line template is JSX, not a class string
STATE = ('hover:', 'active:')


def fix_collapse(text: str) -> str:
    def per_string(m):
        lit = m.group(0)
        base = {(t['util'], t['role']) for t in TOKEN_RE.finditer(lit) if t['pre'] == ''}
        if not base:
            return lit
        def sub(t):
            pre = t['pre']
            if any(pre.endswith(st) for st in STATE) and (t['util'], t['role']) in base and t['role'] in DARKER:
                return f"{pre}{t['util']}-{DARKER[t['role']]}"
            return t.group(0)
        return TOKEN_RE.sub(sub, lit)
    return STRING_RE.sub(per_string, text)

def main(argv):
    dry = '--dry' in argv
    paths = [Path(a) for a in argv if not a.startswith('--')]
    files = []
    for p in paths:
        files += [p] if p.is_file() else [f for f in p.rglob('*') if f.suffix in ('.tsx', '.ts')
                                          and 'node_modules' not in f.parts and '_retired' not in f.parts
                                          and not re.search(r'\.(test|spec)\.tsx?$', f.name)]
    changed = 0
    manual: dict[str, list] = {}
    leftovers: dict[str, int] = {}
    for f in sorted(files):
        with open(f, encoding='utf-8', newline='') as fh:  # keep CRLF/LF as found
            src = fh.read()
        report: list = []
        out = rewrite(src, report)
        if report:
            manual[str(f)] = sorted(set(report))
        n = len(STYLE_HEX_RE.findall(out))
        if n:
            leftovers[str(f)] = n
        if out != src:
            changed += 1
            if not dry:
                with open(f, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(out)
    print(f'{"would change" if dry else "changed"} {changed} of {len(files)} files')
    if manual:
        print('\nManual: hues with no semantic role')
        for f, cls in manual.items():
            print(f'  {f}: {" ".join(cls)}')
    if leftovers:
        print('\nManual: hex/rgb literals left (style objects, chart colours)')
        for f, n in sorted(leftovers.items(), key=lambda x: -x[1]):
            print(f'  {n:4d}  {f}')


if __name__ == '__main__':
    main(sys.argv[1:])
