"""Audit log contributors (≈ ABP's AuditLogContributor, the Template Method + Observer patterns): a module adds facts
to every audit log without touching the middleware — `pre_contribute` when a scope begins, `post_contribute` before it
is saved. Registered once by the composition root."""
from typing import Any


class AuditLogContributor:
    def pre_contribute(self, scope: Any) -> None:  # noqa: B027 — optional hook
        pass

    def post_contribute(self, scope: Any) -> None:  # noqa: B027 — optional hook
        pass


_contributors: list[AuditLogContributor] = []


def add_contributor(c: AuditLogContributor) -> None:
    if all(type(x) is not type(c) for x in _contributors):
        _contributors.append(c)


def contributors() -> list[AuditLogContributor]:
    return list(_contributors)
