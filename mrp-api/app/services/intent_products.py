"""Intent product lifecycle (design §5.3, decisions D7/D8/D11)."""
import secrets

INTENT_CODE_PREFIX = "INTENT-"


def generate_intent_code() -> str:
    """`INTENT-` + 8 lowercase hex chars.

    Random rather than sequential so two planners creating a row at the same
    moment never collide on a counter; the DB unique index is the backstop.
    """
    return f"{INTENT_CODE_PREFIX}{secrets.token_hex(4)}"


def is_intent_code(code: str) -> bool:
    return code.startswith(INTENT_CODE_PREFIX)
