"""Guard against alembic revision ids that don't fit in the version_num column.

Alembic's default `version_num` column (created by `alembic_version*` tables,
see each service's env.py `version_table=...`) is `VARCHAR(32)`. A revision id
longer than 32 characters passes `alembic revision` and `alembic upgrade`
fine locally against a script-only check, but the final INSERT/UPDATE into
`version_num` truncation-fails against Postgres, so `alembic upgrade head`
raises and the migration can never be stamped as applied — the migration
(and everything after it in the chain) becomes permanently unappliable in
any real database.

This bit us for real: 0013_material_suppliers_one_primary (35 chars) sat in
the repo for a while with 0014 chained after it, and neither had ever
actually been applied to any database — `alembic current` was stuck at 0012
everywhere. This test walks every version file's `revision = "..."` value
and asserts it fits, so a future long id fails CI instead of silently
breaking deploys.
"""
import ast
from pathlib import Path

import pytest

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Alembic's default `alembic_version.version_num` column width (see
# op.create_table("alembic_version", ...) in alembic's own runtime/migration.py).
MAX_REVISION_LENGTH = 32


def _iter_revision_ids():
    """Yield (file, revision_id) for every `revision = "..."` module-level
    assignment in alembic/versions/*.py, without importing the modules
    (importing would require the app's full dependency graph)."""
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "revision" for t in node.targets):
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                yield path.name, value.value


def test_versions_dir_exists():
    assert VERSIONS_DIR.is_dir(), f"expected alembic versions dir at {VERSIONS_DIR}"


@pytest.mark.parametrize("filename,revision_id", list(_iter_revision_ids()))
def test_revision_id_fits_version_num_column(filename, revision_id):
    assert len(revision_id) <= MAX_REVISION_LENGTH, (
        f"{filename}: revision id {revision_id!r} is {len(revision_id)} chars, "
        f"exceeds alembic_version.version_num's VARCHAR({MAX_REVISION_LENGTH}) — "
        "this migration can never be stamped as applied. Shorten the revision id "
        "(and update any down_revision referencing it)."
    )
