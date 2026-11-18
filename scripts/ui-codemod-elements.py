#!/usr/bin/env python3
"""
Turn hand-drawn tables and form controls into @uniops/shell components.

    python3 scripts/ui-codemod-elements.py [--dry] <file-or-dir> ...

  <table>/<thead>/<tbody>/<tfoot>/<tr>/<th>/<td> -> Table/THead/TBody/TFoot/Tr/Th/Td
      (density="compact" when the cells used small padding)
  <input type=checkbox|radio|file>              -> Checkbox / Radio / FileInput
  <input> (text, number, date, email, …)        -> Input   (inputSize="sm" when small)
  <select> (not multiple)                       -> Select  (selectSize="sm" when small)
  <textarea>                                    -> Textarea

Only the static className="…" string is edited: classes the Shell component
now owns (padding, text size, borders, colours, focus rings, UPPERCASE, …) are
dropped; layout classes (width, flex, alignment, position, pl-/pr- for icons)
are kept. Dynamic className={…} is left as is (tailwind-merge resolves it).

Skipped and left for a human: elements with a spread of unknown props other
than react-hook-form's register(), <select multiple>, <input type="hidden">,
and any tag whose Shell name already exists in the file as something else.
Buttons are NOT touched here — their look varies too much; do them by hand.
"""
import re
import sys
from pathlib import Path

TABLE_TAGS = {'table': 'Table', 'thead': 'THead', 'tbody': 'TBody', 'tfoot': 'TFoot', 'tr': 'Tr', 'th': 'Th', 'td': 'Td'}

# classes the Shell owns, per element kind
CHROME_CONTROL = re.compile(
    r'^(?:[\w-]+:)*(?:h-\d+(?:\.\d+)?|rounded(?:-[\w]+)?|border(?:-[\w-]+)?|bg-[\w/-]+|px-[\d.]+|py-[\d.]+|'
    r'text-(?:xs|sm|base|2xs)|text-fg[\w-]*|placeholder:[\w/-]+|outline-none|ring(?:-[\w/-]+)?|'
    r'transition(?:-[\w]+)?|shadow(?:-[\w\[\]_(),./-]+)?|appearance-none|cursor-pointer|'
    r'focus(?:-visible|-within)?:[\w/\[\]().,_-]+|disabled:[\w/-]+|read-only:[\w/-]+|duration-\d+)$')
CHROME_TEXTAREA = re.compile(CHROME_CONTROL.pattern.replace(r'h-\d+(?:\.\d+)?|', ''))
CHROME_CHECK = re.compile(r'^(?:[\w-]+:)*(?:h-\d+|w-\d+|rounded(?:-[\w]+)?|border(?:-[\w-]+)?|text-[\w-]+|accent-[\w-]+|'
                          r'focus(?:-visible)?:[\w/\[\]-]+|ring(?:-[\w/-]+)?|cursor-pointer|shrink-0|bg-[\w/-]+)$')
CHROME_TH = re.compile(r'^(?:px|py|pt|pb|p)-[\d.]+$|^text-(?:xs|sm|2xs|left)$|^uppercase$|^tracking-[\w]+$|'
                       r'^font-(?:medium|semibold|bold)$|^text-fg(?:-[\w]+)?$|^whitespace-nowrap$|^align-[\w]+$')
CHROME_TD = re.compile(r'^(?:px|py|pt|pb|p)-[\d.]+$')
CHROME_THEAD = re.compile(r'^(?:[\w-]+:)*(?:bg-[\w/-]+|text-[\w-]+|uppercase|tracking-[\w]+|font-[\w]+|'
                          r'border(?:-[\w-]+)?|shadow(?:-[\w\[\]_(),./-]+)?)$')
CHROME_TBODY = re.compile(r'^(?:divide-[\w-]+|bg-surface)$')
CHROME_TR = re.compile(r'^(?:border-b|border-t|border-line[\w-]*|last:border-0|last:border-b-0)$')
CHROME_TABLE = re.compile(r'^(?:w-full|text-sm|text-xs|border-collapse)$')
SMALL_CELL = re.compile(r'(?<![\w-])(?:p[xy]?-(?:0|0\.5|1|1\.5|2)|px-3|py-2\.5)(?![\w.-])')


def find_tag_end(s, i):
    """Index just past the '>' that closes the JSX tag starting at s[i] == '<'."""
    depth, quote, j = 0, None, i + 1
    while j < len(s):
        c = s[j]
        if quote:
            if c == '\\':
                j += 2
                continue
            if c == quote:
                quote = None
        elif c in '"\'`':
            quote = c
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        elif c == '>' and depth == 0:
            return j + 1
        j += 1
    return -1


CLASS_RE = re.compile(r'(\sclassName=)"([^"]*)"')


def strip_classes(tag, pattern):
    m = CLASS_RE.search(tag)
    if not m:
        return tag, ''
    kept = [c for c in m.group(2).split() if not pattern.match(c)]
    original = m.group(2)
    if kept:
        return tag[:m.start()] + f'{m.group(1)}"{" ".join(kept)}"' + tag[m.end():], original
    return tag[:m.start()] + tag[m.end():], original


def attr(tag, name):
    m = re.search(rf'\s{name}="([^"]*)"', tag)
    return m.group(1) if m else None


def is_small(classes):
    return bool(re.search(r'(?<![\w-])(?:h-[6-9]|text-xs|text-2xs|py-1|py-0\.5)(?![\w.-])', classes))


def convert(src):
    used = set()
    taken = {n for n in ('Table', 'THead', 'TBody', 'TFoot', 'Tr', 'Th', 'Td', 'Input', 'Select', 'Textarea',
                         'Checkbox', 'Radio', 'FileInput')
             if re.search(rf'(?:function|const|class|interface|type)\s+{n}\b', src)
             # imported under that name from somewhere else (lucide's FileInput icon …)
             or re.search(rf"import\s*\{{[^}}]*\b{n}\b[^}}]*\}}\s*from\s*'(?!@uniops/shell'|@/components/ui/)", src)}
    out, i = [], 0
    tag_re = re.compile(r'<(/?)(table|thead|tbody|tfoot|tr|th|td|input|select|textarea)(?=[\s>/])')
    skipped = 0
    open_stack = {'select': [], 'textarea': []}  # was each open tag converted?
    while True:
        m = tag_re.search(src, i)
        if not m:
            out.append(src[i:])
            break
        out.append(src[i:m.start()])
        closing, name = m.group(1), m.group(2)
        end = find_tag_end(src, m.start())
        if end < 0:
            out.append(src[m.start():])
            break
        tag = src[m.start():end]
        new = None
        if name in TABLE_TAGS:
            comp = TABLE_TAGS[name]
            if comp not in taken:
                if closing:
                    new = f'</{comp}>'
                else:
                    body = tag[len(name) + 1:]
                    pat = {'th': CHROME_TH, 'td': CHROME_TD, 'thead': CHROME_THEAD, 'tbody': CHROME_TBODY,
                           'tr': CHROME_TR, 'table': CHROME_TABLE, 'tfoot': CHROME_THEAD}[name]
                    body2, orig = strip_classes(body, pat)
                    if name in ('th', 'td') and orig:
                        if re.search(r'(?<![\w-])text-right(?![\w-])', orig) and 'align=' not in body2:
                            body2 = re.sub(r'(\sclassName="[^"]*?)\btext-right\b\s?', r'\1', body2)
                            body2 = body2.replace(' className=""', '')
                            body2 = ' align="right"' + body2
                        elif re.search(r'(?<![\w-])text-center(?![\w-])', orig) and 'align=' not in body2:
                            body2 = re.sub(r'(\sclassName="[^"]*?)\btext-center\b\s?', r'\1', body2)
                            body2 = body2.replace(' className=""', '')
                            body2 = ' align="center"' + body2
                    new = f'<{comp}{body2}'
                used.add(comp)
        elif not closing:
            typ = (attr(tag, 'type') or 'text') if name == 'input' else None
            spread_ok = all(re.fullmatch(r'\{\.\.\.register\([^}]*\)\}', sp) for sp in re.findall(r'\{\.\.\.[^}]*\}', tag))
            if not spread_ok or (name == 'select' and re.search(r'\smultiple(?:[\s>=/])', tag)) or typ == 'hidden':
                skipped += 1
            elif name == 'input' and typ in ('checkbox', 'radio'):
                comp = 'Checkbox' if typ == 'checkbox' else 'Radio'
                if comp not in taken:
                    body = re.sub(r'\stype="(?:checkbox|radio)"', '', tag[len(name) + 1:])
                    body, _ = strip_classes(body, CHROME_CHECK)
                    new = f'<{comp}{body}'
                    used.add(comp)
            elif name == 'input' and typ == 'file':
                if 'FileInput' not in taken:
                    body = re.sub(r'\stype="file"', '', tag[len(name) + 1:])
                    new = f'<FileInput{body}'
                    used.add('FileInput')
            elif name == 'input' and re.search(r'className="[^"]*\bbg-transparent\b', tag):
                # Frameless input inside a composite control: keep its own sizing.
                if 'Input' not in taken:
                    body = tag[len(name) + 1:]
                    m2 = CLASS_RE.search(body)
                    kept = [c for c in m2.group(2).split() if c not in ('bg-transparent', 'focus:outline-none', 'outline-none', 'text-sm')]
                    body = body[:m2.start()] + (f'{m2.group(1)}"{" ".join(kept)}"' if kept else '') + body[m2.end():]
                    new = f'<Input bare{body}'
                    used.add('Input')
            elif name == 'input':
                if 'Input' not in taken:
                    body, orig = strip_classes(tag[len(name) + 1:], CHROME_CONTROL)
                    if is_small(orig) and 'inputSize=' not in body:
                        body = ' inputSize="sm"' + body
                    new = f'<Input{body}'
                    used.add('Input')
            elif name == 'select':
                if 'Select' not in taken:
                    body, orig = strip_classes(tag[len(name) + 1:], CHROME_CONTROL)
                    if is_small(orig):
                        body = ' selectSize="sm"' + body
                    new = f'<Select{body}'
                    used.add('Select')
            elif name == 'textarea':
                if 'Textarea' not in taken:
                    body, _ = strip_classes(tag[len(name) + 1:], CHROME_TEXTAREA)
                    new = f'<Textarea{body}'
                    used.add('Textarea')
        elif closing and name in open_stack:
            if open_stack[name] and open_stack[name].pop():
                new = '</Select>' if name == 'select' else '</Textarea>'
        if not closing and name in open_stack:
            open_stack[name].append(new is not None)
        out.append(new if new is not None else tag)
        i = end
    return ''.join(out), used, skipped


def add_imports(text, names, local_ok):
    names = sorted(n for n in names if n not in local_ok)
    if not names:
        return text
    m = re.search(r"import\s*\{([^}]*)\}\s*from\s*'@uniops/shell'", text)
    if m:
        have = {x.strip().split(' as ')[0] for x in m.group(1).split(',') if x.strip()}
        add = [n for n in names if n not in have]
        if not add:
            return text
        inner = m.group(1).rstrip()
        sep = ', ' if inner.strip() else ' '
        return text[:m.start(1)] + inner + sep + ', '.join(add) + ' ' + text[m.end(1):]
    imports = list(re.finditer(r"^import[^\n]*?(?:from\s*'[^']+'|'[^']+')\s*;?\s*$", text, re.M))
    line = f"import {{ {', '.join(names)} }} from '@uniops/shell'\n"
    if imports:
        at = imports[-1].end() + 1
        return text[:at] + line + text[at:]
    return line + text


def density_pass(original, converted):
    """Mark tables compact where the original cells used small padding."""
    tables_orig = re.findall(r'<table\b.*?</table>', original, flags=re.S)
    parts = re.split(r'(<Table\b)', converted)
    if (len(parts) - 1) // 2 != len(tables_orig):
        return converted  # cannot align; leave defaults
    out = [parts[0]]
    for k, t in enumerate(tables_orig):
        cells = re.findall(r'<t[dh]\b[^>]*className="([^"]*)"', t)
        small = sum(1 for c in cells if SMALL_CELL.search(c))
        tag_and_rest = parts[2 + 2 * k]
        if cells and small * 2 >= len(cells) and not tag_and_rest.lstrip().startswith('density='):
            out.append('<Table density="compact"')
        else:
            out.append('<Table')
        out.append(tag_and_rest)
    return ''.join(out)


def main(argv):
    dry = '--dry' in argv
    files = []
    for a in (x for x in argv if not x.startswith('--')):
        p = Path(a)
        files += [p] if p.is_file() else sorted(f for f in p.rglob('*.tsx')
                                                if 'node_modules' not in f.parts and '_retired' not in f.parts
                                                and not re.search(r'\.(test|spec)\.tsx$', f.name))
    total_skipped = 0
    for f in files:
        with open(f, encoding='utf-8', newline='') as fh:
            src = fh.read()
        nl = '\r\n' if '\r\n' in src else '\n'
        body = src.replace('\r\n', '\n')
        new, used, skipped = convert(body)
        new = density_pass(body, new)
        # names already imported from the EPMS re-export files count as present
        local_ok = {n for n in used if re.search(rf"import\s*\{{[^}}]*\b{n}\b[^}}]*\}}\s*from\s*'@/components/ui/", new)}
        new = add_imports(new, used, local_ok)
        total_skipped += skipped
        if new != body:
            print(f'{"would change" if dry else "changed"} {f}  [{", ".join(sorted(used))}]' + (f'  skipped {skipped}' if skipped else ''))
            if not dry:
                with open(f, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(new.replace('\n', nl))
        elif skipped:
            print(f'unchanged {f}  skipped {skipped}')
    print(f'skipped elements (need a human): {total_skipped}')


if __name__ == '__main__':
    main(sys.argv[1:])
