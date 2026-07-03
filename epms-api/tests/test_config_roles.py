"""Unit tests for supervisor/director default permissions in the role matrix."""
from app.crud.config import get_effective_role_permissions
from app.models.config import CompanyConfig


def test_supervisor_and_director_default_perms():
    cfg = CompanyConfig(role_permissions={}, custom_roles=[])
    matrix = get_effective_role_permissions(cfg)
    assert matrix["supervisor"]["view_pr"] is True
    assert matrix["director"]["view_pr"] is True
    assert matrix["director"]["view_pa"] is True
