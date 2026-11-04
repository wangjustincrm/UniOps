"""What shipped of one item, spread over its lines (app/domain/lifecycle.spread)."""
import pytest

from app.domain.lifecycle import spread


@pytest.mark.parametrize(("shipped", "quantities", "want"), [
    (7, [1, 6], [1, 6]), (1, [1, 1], [1, 0]), (0, [3], [0]), (9, [1, 6], [3, 6]), (5, [], []), (4, [0, 6], [0, 4]),
])
def test_spread(shipped, quantities, want):
    assert spread(shipped, quantities) == want
