"""The one-off scripts' shared entry (app/scripts/base.py, 2026-11-10)."""
import json

from app.scripts.base import ScriptArgs, script_main


def test_a_dry_run_unless_apply_and_flags_values_positionals():
    a = ScriptArgs(("2026-11-04", "--order", "5", "--relink"), takes_values=("order",))
    assert (a.apply, a.flag("relink"), a.value("order"), a.positional) == (False, True, "5", ["2026-11-04"])
    assert ScriptArgs(("--apply",)).apply


def test_prints_the_result_with_apply_as_one_json_line(capsys):
    async def run(a):
        return {"orders": 2}
    assert script_main(run, argv=["--apply"]) == 0
    assert json.loads(capsys.readouterr().out) == {"apply": True, "orders": 2}


def test_a_failure_prints_only_its_type_never_its_text(capsys):
    async def run(a):
        raise ValueError("customer Jane Doe, 1 Main St")
    assert script_main(run, argv=[]) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"apply": False, "error": "ValueError"} and "Jane" not in out
