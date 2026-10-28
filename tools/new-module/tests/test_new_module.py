"""Tests for tools/new-module. Run with pytest from tools/new-module/."""
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("new_module", HERE / "new_module.py")
nm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nm)


def _args(service, frontend, platform, repo):
    return [
        "--service", service, "--frontend", frontend,
        "--module-key", "demo_mod", "--label", "Demo Module",
        "--api-port", "8099", "--web-port", "5199",
        "--env-prefix", "DEMO", "--table-prefix", "dmo_",
        "--perms", "demo_mod.read,demo_mod.config",
        "--platform", platform, "--repo", str(repo),
    ]


def _files(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text(encoding="utf-8")
        for p in root.rglob("*") if p.is_file()
    }


def test_inplace_refused_for_any_other_target(tmp_path, capsys):
    rc = nm.main(_args("demo-api", "demo", "inplace", tmp_path))
    assert rc == 2
    assert "only for orders-api / order-bridge" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("service,frontend", [("orders-api", "demo"), ("demo-api", "order-bridge")])
def test_inplace_refused_for_half_matching_target(tmp_path, service, frontend):
    assert nm.main(_args(service, frontend, "inplace", tmp_path)) == 2
    assert list(tmp_path.iterdir()) == []


def test_inplace_accepted_for_order_bridge(tmp_path):
    assert nm.main(_args("orders-api", "order-bridge", "inplace", tmp_path)) == 0
    files = _files(tmp_path)
    assert "from app.platform.errors import install_error_handlers" in files["orders-api/app/main.py"]
    assert "from '@/platform-ui'" in files["order-bridge/src/App.tsx"]
    assert "src/platform-ui/**" in files["order-bridge/eslint.config.js"]
    # The template carries no platform code: the folders are written by hand.
    assert not (tmp_path / "orders-api/app/platform").exists()
    assert not (tmp_path / "order-bridge/src/platform-ui").exists()


def test_none_is_the_default_and_has_no_platform_references(tmp_path):
    args = _args("demo-api", "demo", "none", tmp_path)
    i = args.index("--platform")
    del args[i:i + 2]
    assert nm.main(args) == 0
    for rel, text in _files(tmp_path).items():
        assert "platform" not in text.lower(), rel
    # Same imports the plain skeleton needs: the non-platform error handler.
    main = (tmp_path / "demo-api/app/main.py").read_text()
    assert "unhandled_exception_handler" in main
    assert "from fastapi.responses import JSONResponse" in main


def test_only_the_two_folders_are_written(tmp_path):
    (tmp_path / "existing.txt").write_text("keep")
    assert nm.main(_args("demo-api", "demo", "none", tmp_path)) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo", "demo-api", "existing.txt"]
    assert (tmp_path / "existing.txt").read_text() == "keep"


def test_existing_folder_is_never_overwritten(tmp_path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "keep.txt").write_text("x")
    assert nm.main(_args("demo-api", "demo", "none", tmp_path)) == 2
    assert not (tmp_path / "demo-api").exists()
    assert [p.name for p in (tmp_path / "demo").iterdir()] == ["keep.txt"]


def test_values_reach_the_files(tmp_path):
    assert nm.main(_args("demo-api", "demo", "none", tmp_path)) == 0
    f = _files(tmp_path)
    assert 'version_table="alembic_version_demo"' in f["demo-api/alembic/env.py"]
    assert 'os.getenv("TEST_DEMO_DB", "demo_test")' in f["demo-api/tests/conftest.py"]
    assert "DEMO_SCHEDULER_ENABLED: bool = False" in f["demo-api/app/core/config.py"]
    assert "MODULE_PERMS = ['demo_mod.read', 'demo_mod.config']" in f["demo/src/hooks/usePermissions.ts"]
    assert "'demo-auth'" in f["demo/src/store/auth.ts"]
    assert '"port": 5199' not in f["demo/vite.config.ts"] and "port: 5199" in f["demo/vite.config.ts"]
    assert not any("%%" in t for t in f.values())


def test_render_sections_and_unknown_names():
    out = nm.render("a%%#x%%B%%/x%%%%^x%%C%%/x%%%%v%%\n", {"x": True}, {"v": "!"})
    assert out == "aB!\n"
    out = nm.render("%%#x%%\nline\n%%/x%%\nend\n", {"x": False}, {})
    assert out == "end\n"
    out = nm.render("a, b%%^x%%, c%%/x%%\nnext\n", {"x": True}, {})
    assert out == "a, b\nnext\n"  # an inline section keeps the line break
    out = nm.render("%%#x%%\nline\n%%/x%%\nend\n", {"x": True}, {})
    assert out == "line\nend\n"
    with pytest.raises(nm.TemplateError):
        nm.render("%%nope%%", {}, {})
    with pytest.raises(nm.TemplateError):
        nm.render("%%#nope%%x%%/nope%%", {}, {})


def test_bad_names_rejected(tmp_path):
    with pytest.raises(SystemExit):
        nm.main(_args("Demo_API", "demo", "none", tmp_path))
    args = _args("demo-api", "demo", "none", tmp_path)
    args[args.index("--perms") + 1] = "other.read"
    with pytest.raises(SystemExit):
        nm.main(args)
    assert list(tmp_path.iterdir()) == []
