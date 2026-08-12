"""Unit tests for the receipt-vendor comparison (Task 13).

These are pure-function tests with no DB and no event loop, which is exactly
why they are NOT in tests/test_agreement_receipt_api.py where the rest of
Task 13's coverage lives: that module sets `pytestmark = pytest.mark.asyncio`
for every function in it, and a SYNC function caught by that mark makes
pytest-asyncio spin up a second event loop — after which the session-scoped
`test_engine` (bound to the first one) fails every following async test with
"attached to a different loop". Measured, not guessed: putting these eight
tests in that file broke 7 async tests that pass here.

The rule under test is deliberately fuzzy — see is_vendor_mismatch's
docstring. Fuzzy rules rot silently, so every case below asserts ONE verdict
(`is True` / `is False`), never `assert a or b`.
"""
from app.schemas.agreement_receipt import is_vendor_mismatch, normalize_vendor_name


def test_normalize_vendor_name_folds_case_and_punctuation():
    """归一化本身:casefold + 去掉非字母数字 + 丢掉纯数字词(门店号)。
    这是包含判定的前提。"""
    assert normalize_vendor_name("Princess Auto #12") == "princessauto"
    assert normalize_vendor_name("PRINCESS AUTO LTD.") == "princessautoltd"
    assert normalize_vendor_name("  princess   auto  ") == "princessauto"


# The six inputs the task brief names, each asserted to ONE verdict — never
# `assert a or b`, which would pass no matter which way the function went.

def test_vendor_identical_is_not_a_mismatch():
    assert is_vendor_mismatch("Princess Auto", "Princess Auto") is False


def test_vendor_differing_only_in_case_is_not_a_mismatch():
    assert is_vendor_mismatch("princess auto", "PRINCESS AUTO") is False


def test_vendor_with_a_store_number_suffix_is_not_a_mismatch():
    """柜台小票抬头常带门店号。这是**最常见**的写法差异 —— 如果它误报,
    警告一周内就没人看了,真正的错归属反而混过去。"""
    assert is_vendor_mismatch("Princess Auto #12", "Princess Auto Ltd") is False


def test_vendor_with_a_dropped_legal_suffix_is_not_a_mismatch():
    assert is_vendor_mismatch("Princess Auto", "Princess Auto Ltd") is False


def test_a_blank_receipt_vendor_is_never_a_mismatch():
    """没填不等于填错:OCR 抽不到抬头是正常情况,手工录入也允许留空。"""
    assert is_vendor_mismatch(None, "Princess Auto") is False
    assert is_vendor_mismatch("", "Princess Auto") is False
    assert is_vendor_mismatch("   ", "Princess Auto") is False


def test_two_genuinely_different_merchants_are_a_mismatch():
    """这是这个字段存在的全部理由 —— 它必须真的会为 True。"""
    assert is_vendor_mismatch("Canadian Tire", "Princess Auto") is True


def test_containment_is_checked_in_both_directions():
    """两个方向都要认:小票更长(带门店号)和协议更长(带 Ltd)各占一半,
    哪一边是长的那个不固定 —— 单向包含会漏掉一半的正常写法。"""
    assert is_vendor_mismatch("Princess Auto Ltd", "Princess Auto #12") is False
    assert is_vendor_mismatch("Princess Auto #12", "Princess Auto Ltd") is False


def test_a_punctuation_only_vendor_is_not_a_mismatch():
    """归一化后什么都不剩(例如小票抬头被 OCR 读成 "***")= 没有信息,
    不是"对不上" —— 否则 `"" in b` 会替我们随手做主。"""
    assert is_vendor_mismatch("***", "Princess Auto") is False


def test_a_store_number_alone_never_identifies_a_merchant():
    """纯数字词(门店号/收银台号)被丢弃 —— 它从不是商家名里能识别身份的部分。"""
    assert normalize_vendor_name("Princess Auto #12") == normalize_vendor_name("Princess Auto 7")
    assert is_vendor_mismatch("Princess Auto 7", "Princess Auto #12") is False
