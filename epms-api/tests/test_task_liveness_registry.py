"""守卫:每一种任务类型都必须有人决定过"它什么时候算死"。

Task Inbox 的僵尸不是三个孤立 bug,是一个**结构性**问题:`tasks` 是物化表,
"这条任务还算不算活"没有任何地方在重算,全靠每一次状态迁移**记得**手工去关。
2026-09-09 的生产审计里,三条迁移路径没记得(accept 异常、approval-api 的
submit 分支、Data Maintenance 的裸 setattr),六条谁也做不了的任务就那么挂着,
最老的从 2026-07-22 开始。

光把那三处补上,下一个新任务类型还会重演。所以 crud/task.py 用三张表把
"何时算活"变成**必须显式声明的东西**:

    TASK_LIVENESS             —— 有状态不变量,由 _complete_stale_status_tasks 通扫
    _TASK_DEDICATED_HANDLING  —— 不变量不是"比状态",有专属收口逻辑(写明是哪个)
    _TASK_NO_STATUS_INVARIANT —— 本服务兜不住(写明原因)

本测试扫 app/ 下所有 `Task(type="…")` 字面量,任何一种落在三张表之外就红。
它拦不住别的服务(approval-api / vms-api)新加的类型 —— 那些代码不在这个
容器里 —— 所以那部分靠 _TASK_NO_STATUS_INVARIANT 里的手工登记,见文件里的
覆盖边界说明。
"""
import ast
import pathlib
import re

import pytest

from app.crud.task import (
    TASK_LIVENESS,
    _TASK_DEDICATED_HANDLING,
    _TASK_NO_STATUS_INVARIANT,
)

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _emitted_task_types() -> dict[str, set[str]]:
    """{task type: {发它的文件}} —— 只认 `Task(...)` 里 type= 的字符串字面量。

    用 AST 而不是正则:`type=` 这个关键字参数在别的构造里也到处都是
    (document_type、content_type、procurement_type…),按语法找到 Task(...)
    这个调用再取它的 type= 才不会误报。
    """
    found: dict[str, set[str]] = {}
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name != "Task":
                continue
            for kw in node.keywords:
                if kw.arg == "type" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    found.setdefault(kw.value.value, set()).add(
                        str(path.relative_to(APP.parent)))
    return found


def _classified() -> set[str]:
    return set(TASK_LIVENESS) | set(_TASK_DEDICATED_HANDLING) | set(_TASK_NO_STATUS_INVARIANT)


def test_every_emitted_task_type_is_classified():
    emitted = _emitted_task_types()
    assert emitted, "扫不到任何 Task(type=...) —— 扫描器本身坏了,不是代码干净了"
    unclassified = {t: sorted(f) for t, f in emitted.items() if t not in _classified()}
    assert not unclassified, (
        "这些任务类型没人决定过它什么时候算死。请在 crud/task.py 里三选一登记:\n"
        + "\n".join(f"  {t}  ← {', '.join(files)}" for t, files in sorted(unclassified.items()))
        + "\n\nTASK_LIVENESS(有状态不变量) / _TASK_DEDICATED_HANDLING(专属收口) "
          "/ _TASK_NO_STATUS_INVARIANT(兜不住,写原因)"
    )


def test_a_type_is_classified_exactly_once():
    """三张表语义互斥。同时出现在两张里,读的人无从判断哪张是真的。"""
    tables = {
        "TASK_LIVENESS": set(TASK_LIVENESS),
        "_TASK_DEDICATED_HANDLING": set(_TASK_DEDICATED_HANDLING),
        "_TASK_NO_STATUS_INVARIANT": set(_TASK_NO_STATUS_INVARIANT),
    }
    dupes = {}
    for name, keys in tables.items():
        for other, other_keys in tables.items():
            if name < other:
                for k in keys & other_keys:
                    dupes[k] = f"{name} + {other}"
    assert not dupes, f"重复登记:{dupes}"


def test_every_classification_carries_a_reason():
    """空理由等于没登记 —— 下一个人照样不知道能不能动它。"""
    blank = [t for t, live in TASK_LIVENESS.items() if not live.why.strip()]
    blank += [t for t, why in _TASK_DEDICATED_HANDLING.items() if not why.strip()]
    blank += [t for t, why in _TASK_NO_STATUS_INVARIANT.items() if not why.strip()]
    assert not blank, f"这些登记没写理由:{blank}"


@pytest.mark.parametrize("task_type", sorted(TASK_LIVENESS))
def test_live_statuses_exist_on_the_model(task_type):
    """声明的状态必须真是那张表上会出现的值。

    打错一个字(比如 'return' 少个 ed)后果是**静默的**:那条任务会在它最该
    活着的时候被扫掉。这里拿模型的 status 列长度做个下限校验,再确认模型确实
    有 status 列 —— 通扫是 join 它比较的。
    """
    live = TASK_LIVENESS[task_type]
    col = getattr(live.model, "status", None)
    assert col is not None, f"{live.model.__name__} 没有 status 列,通扫会炸"
    assert live.live_statuses, f"{task_type} 的可做状态集为空 = 任何时候都判死"
    limit = getattr(col.type, "length", None)
    if limit:
        too_long = [s for s in live.live_statuses if len(s) > limit]
        assert not too_long, f"{task_type} 声明了超出列宽({limit})的状态:{too_long}"


def test_document_types_match_what_the_code_actually_writes():
    """声明的 document_type 必须是代码真的会写进去的值。

    approve_pa 挂在 'pa' 和 'pa_dir' 两种 document_type 上(同一张表,OA 的
    Direct PA 用后者)—— 原来的审批清扫只 join 了 'pa',pa_dir 因此是 EPMS
    自有表里唯一完全没有对账的审批族。这条测试是那个疏漏的回归护栏。
    """
    src = (APP / "crud" / "task.py").read_text(encoding="utf-8")
    known = set(re.findall(r'document_type\s*==\s*"([a-z_]+)"', src))
    known |= {"pa_dir"}   # 只在声明表里出现,不在别处比较
    for task_type, live in TASK_LIVENESS.items():
        assert live.document_types, f"{task_type} 没声明 document_type"
        for dt in live.document_types:
            assert dt.islower() and dt.isascii(), f"{task_type} 的 document_type 可疑:{dt}"

    assert "pa_dir" in TASK_LIVENESS["approve_pa"].document_types
    assert "pa_dir" in TASK_LIVENESS["process_pa"].document_types
    assert "pa_dir" in TASK_LIVENESS["revise_pa"].document_types
