"""CRUD for the plant area tree (mdm-api owns writes).

The one operation with a trap in it is renaming. `path` is a materialized path
built from names, so a rename has to rewrite the path of the node and every
node beneath it — and the rewrite has to be done on the prefix, not by string
replacement, or a building called "Line" would corrupt every descendant that
happens to contain that word.
"""
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.location import Location


async def get_all(db: AsyncSession, *, active_only: bool = False) -> list[Location]:
    q = select(Location)
    if active_only:
        q = q.where(Location.is_active.is_(True))
    return list((await db.execute(
        q.order_by(Location.depth, Location.sort_order, Location.name))).scalars().all())


async def get(db: AsyncSession, location_id: uuid.UUID) -> Location | None:
    return (await db.execute(
        select(Location).where(Location.id == location_id))).scalar_one_or_none()


async def children_of(db: AsyncSession, location_id: uuid.UUID) -> list[Location]:
    return list((await db.execute(
        select(Location).where(Location.parent_id == location_id))).scalars().all())


async def _under(db: AsyncSession, prefix: str) -> list[Location]:
    """Everything beneath a path, by prefix.

    Takes the prefix rather than the node on purpose. A rename has to find the
    descendants by the path they still have, and reading it off the node after
    the node has been renamed finds nothing at all — which is exactly how the
    first version of this silently left a whole subtree pointing at a name that
    no longer existed.

    The trailing slash matters too: without it "Filling" would also match
    "Filling Room" elsewhere in the tree.
    """
    rows = (await db.execute(
        select(Location).where(Location.path.like(f"{prefix}%")))).scalars()
    return list(rows)


def _derive_code(name: str, parent: Location | None) -> str:
    """A code nobody has to think of.

    HSE names areas; they do not want to invent identifiers, and a code typed
    by hand is a code that eventually collides or contradicts its parent. The
    caller may still supply one — the ERP-facing codes came from elsewhere.
    """
    slug = re.sub(r"[^A-Z0-9]+", "", name.upper())[:12] or "AREA"
    return f"{parent.code}-{slug}" if parent else slug


async def _unique_code(db: AsyncSession, base: str) -> str:
    code, n = base[:50], 1
    while (await db.execute(select(Location.id).where(Location.code == code))).first():
        n += 1
        suffix = str(n)
        code = f"{base[:50 - len(suffix) - 1]}-{suffix}"
    return code


async def sibling_named(
    db: AsyncSession, parent_id: uuid.UUID | None, name: str,
    *, excluding: uuid.UUID | None = None,
) -> Location | None:
    """Another area with this name under the same parent.

    Uniqueness of `code` does not prevent this — codes are derived and the
    deriver will happily suffix a second "Line 1" into existence. What the
    plant sees is two identical entries in the picker on every incident form,
    and half the records filed against one of them: not an error anybody would
    notice, and not something that can be merged afterwards.
    """
    q = select(Location).where(
        Location.parent_id.is_(None) if parent_id is None else Location.parent_id == parent_id,
        func.lower(Location.name) == name.strip().lower(),
    )
    if excluding:
        q = q.where(Location.id != excluding)
    return (await db.execute(q)).scalars().first()


async def create(db: AsyncSession, data: dict) -> Location:
    parent = await get(db, data["parent_id"]) if data.get("parent_id") else None
    name = data["name"].strip()

    node = Location(
        id=uuid.uuid4(),
        code=await _unique_code(db, (data.get("code") or _derive_code(name, parent)).upper()),
        name=name,
        parent_id=parent.id if parent else None,
        level=data["level"],
        path=f"{parent.path}/{name}" if parent else name,
        depth=(parent.depth + 1) if parent else 0,
        access_area=data.get("access_area") or (parent.access_area if parent else None),
        department_id=data.get("department_id") or (parent.department_id if parent else None),
        is_selectable=data.get("is_selectable", True),
        sort_order=data.get("sort_order", 0),
    )
    # Issued on creation rather than by a later sweep, so the sticker for a new
    # area can be printed the moment it is added. Same shape as migration 0021.
    node.qr_token = f"L-{node.id.hex[:12]}"
    db.add(node)
    await db.flush()
    return node


async def update(db: AsyncSession, node: Location, fields: dict) -> Location:
    renamed = "name" in fields and fields["name"].strip() != node.name
    old_prefix = f"{node.path}/"

    for key, value in fields.items():
        setattr(node, key, value.strip() if key == "name" else value)

    if renamed:
        # Found before the node's own path is rewritten, or there is nothing
        # left to match on.
        descendants = await _under(db, old_prefix)
        parent = await get(db, node.parent_id) if node.parent_id else None
        node.path = f"{parent.path}/{node.name}" if parent else node.name
        new_prefix = f"{node.path}/"
        # Prefix replacement, not `str.replace`: a node named "Line" would
        # otherwise rewrite every occurrence of that word further down the path.
        for child in descendants:
            if child.path.startswith(old_prefix):
                child.path = new_prefix + child.path[len(old_prefix):]

    await db.flush()
    return node


async def set_active(db: AsyncSession, node: Location, active: bool) -> Location:
    """Retiring an area, and bringing one back.

    Never a delete. Every incident, inspection and corrective action ever filed
    against this area references it, and the area is also on the QR sticker by
    the door. Retiring takes it off the list offered for new records and leaves
    the history intact.

    Children follow the parent down: an area that is no longer part of the
    plant cannot have live lines inside it. They do not follow it back up,
    because which of them were retired separately beforehand is not recorded.
    """
    node.is_active = active
    if not active:
        for child in await _under(db, f"{node.path}/"):
            child.is_active = False
    await db.flush()
    return node
