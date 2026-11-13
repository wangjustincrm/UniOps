"""A person whose token carries no name (user 2026-11-13: "resolved by" showed an id): the name comes from the users
table; with a name in the token, or no users table (this test schema), the actor is left as it is."""
import uuid

from app.core.deps import named
from app.platform.actors import Actor, actor_from_token


async def test_a_token_name_is_kept_and_a_missing_one_never_stops_the_request():
    uid = uuid.uuid4()
    with_name = actor_from_token({"sub": str(uid), "full_name": "Op One", "role": "sales_operator"})
    assert (await named(with_name)) is with_name
    bare = actor_from_token({"sub": str(uid), "role": "sales_operator"})
    assert bare.name == str(uid)                    # what the token gives
    out = await named(bare)                         # no users table in the test schema: unchanged, no error
    assert out.user_id == uid and out.name in (str(uid),) and isinstance(out, Actor)
