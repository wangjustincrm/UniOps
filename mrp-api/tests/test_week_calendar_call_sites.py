"""app/ 里对 week_calendar 五个函数的每一次调用都必须显式传 start_dow。

`start_dow=0` 这个默认值是给测试和历史 run 回放用的便利。生产代码路径上漏传
= 整个周网格静默回落成周一制（工厂实际是周六→周五），排产结果整体错位，而且
**没有任何报错**——这正是 week_calendar 模块拒绝给 `mode` 设默认值的同一条理由。

用 AST 检查而不是 grep：grep 会被换行的调用、注释里的示例和字符串骗过去。
"""
import ast
from pathlib import Path

import pytest

_GUARDED = {"week_start_of", "owning_month", "weeks_of_month", "shift_weeks", "week_label"}
_APP = Path(__file__).resolve().parents[1] / "app"


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


@pytest.mark.xfail(
    strict=True,
    reason="expected red until the run's week_start_dow is threaded through "
           "mps.py / mps_engine.py / mps_export.py (plan Task 4); strict=True "
           "so it fails loudly the moment that task lands and this marker "
           "must be removed",
)
def test_every_app_call_passes_start_dow_explicitly():
    offenders: list[str] = []
    for path in sorted(_APP.rglob("*.py")):
        if path.name == "week_calendar.py":
            continue          # 模块内部调用自己的实现，不受此约束
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node) in _GUARDED and not any(
                    kw.arg == "start_dow" for kw in node.keywords):
                offenders.append(f"{path.relative_to(_APP.parent)}:{node.lineno} "
                                 f"{_call_name(node)}()")
    assert not offenders, (
        "these calls would silently fall back to Monday-start weeks; pass "
        "start_dow=<the run's own week_start_dow> explicitly:\n  "
        + "\n  ".join(offenders))


def test_the_guard_actually_detects_a_missing_argument():
    """守卫自身的自检：伪造一段漏传的代码，确认它会被抓到。

    没有这条，`_GUARDED` 名字打错或 AST 遍历写歪都会让上面那条测试永远绿——
    典型的假门禁。"""
    tree = ast.parse("weeks_of_month('2026-08', mode)\nweeks_of_month('2026-08', mode, start_dow=5)\n")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    missing = [c for c in calls
               if _call_name(c) in _GUARDED and not any(k.arg == "start_dow" for k in c.keywords)]
    assert len(calls) == 2
    assert len(missing) == 1
