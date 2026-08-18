"""Loss rates — pure functions plus the planning-parameter resolver.

**BOMs are treated as exact data.** The plant's decision (2026-08-04) is
that loss is not written into `bom_lines`; it is applied at MRP time by
inflating the requirement with a configured rate. There are exactly two
rates -- raw material and packaging -- and a component takes the rate for
its OWN category at every level of the explosion, never a compounded
product of its ancestors'.

## ★ The packaging rate starts at 0 and that is not an oversight

Powder and dry-mix BOMs carry no loss, but **some packaging BOMs already
have it baked into the quantity**, graded by packaging type. Measured on
`S0093` (700g x 6, 420 kg batch): 420000/700 = 600 cans in theory, the BOM
says **610** (+1.67%); the same BOM's cartons are 100 in theory and **105**
(+5%). Applying a packaging rate on top of that inflates an already
inflated number and buys packaging nobody needs.

So the packaging rate stays 0 until NC's packaging BOMs are made exact --
at which point only the setting changes, not this code. The parameter
screen carries this explanation, so that nobody seeing a 0 helpfully
"fixes" it.
"""
from decimal import Decimal

# NC material code prefixes. `CP` is packaging; `CR` (raw and auxiliary
# materials) and `CS`/`CW` (semi-finished powder) are consumed materials and
# take the raw rate.
_PACKAGING_PREFIX = "CP"

RAW_MATERIAL_LOSS_RATE_KEY = "raw_material_loss_rate"
PACKAGING_LOSS_RATE_KEY = "packaging_loss_rate"


def applicable_loss_rate(material_code: str, rates: dict[str, Decimal]) -> Decimal:
    """The rate to apply to `material_code`, by its own category.

    Anything that is not packaging takes the raw rate, including codes with
    an unfamiliar prefix: under-buying stops a line, over-buying by a small
    percentage does not, so the fallback leans the safe way.
    """
    key = "packaging" if material_code.upper().startswith(_PACKAGING_PREFIX) else "raw"
    return rates[key]


def inflate_for_loss(quantity: Decimal, rate: Decimal) -> Decimal:
    """`quantity x (1 + rate)`.

    A negative rate is refused rather than quietly shrinking the
    requirement: buying less than the BOM calls for stops production, and a
    misconfigured parameter must not be able to do that silently.
    """
    if rate < 0:
        raise ValueError(f"loss rate must not be negative, got {rate}")
    return quantity * (Decimal("1") + rate)


async def resolve_loss_rates(db) -> dict[str, Decimal]:
    """Both configured rates, defaulting to 0 when unset.

    0 means "no inflation", which is exactly the right behaviour for a plant
    that has not supplied its numbers yet -- the requirement then equals the
    BOM, which is a defensible plan, unlike a guessed rate.
    """
    from app.api.v1.params import get_param

    async def _rate(key: str) -> Decimal:
        value = await get_param(db, key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return Decimal("0")
        return Decimal(str(value))

    return {
        "raw": await _rate(RAW_MATERIAL_LOSS_RATE_KEY),
        "packaging": await _rate(PACKAGING_LOSS_RATE_KEY),
    }
