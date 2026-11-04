"""Which line each 945 row goes to (FR-OB-25a): the real dev2 shapes."""
import pytest

from app.domain.row_matching import assign_rows


def run(lines, rows):
    a = assign_rows([(f"r{i}", q) for i, q in enumerate(rows)], [(f"l{i}", q) for i, q in enumerate(lines)])
    return [lines[int(a.links[f"r{i}"][1:])] for i in range(len(rows))], len(a.did_not_fit)


@pytest.mark.parametrize(("lines", "rows", "linked", "misfits"), [
    ([1, 6], [6, 1], [6, 1], 0),   # SHP-9533
    ([6, 1], [6, 1], [6, 1], 0),
    ([1, 4], [1, 4], [1, 4], 0),
    ([1, 3], [1, 3], [1, 3], 0),
    ([1, 1], [1, 1], [1, 1], 0),
    ([1, 1], [1], [1], 0),         # SHP-9451: short by 1
    ([1, 6], [3, 4], [6, 6], 1),   # the 3 does not fit: the largest line
    ([6], [14], [6], 1),           # over-shipped
    ([0, 0], [5], [0], 1),
])
def test_rows_to_lines(lines, rows, linked, misfits):
    assert run(lines, rows) == (linked, misfits)


def test_ties_keep_line_order():
    a = assign_rows([("a", 1), ("b", 1)], [("x", 1), ("y", 1)])
    assert a.links == {"a": "x", "b": "y"}
