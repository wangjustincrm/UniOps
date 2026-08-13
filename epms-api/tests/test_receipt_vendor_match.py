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
import uuid

from app.schemas.agreement_receipt import (
    is_vendor_mismatch,
    normalize_vendor_name,
    receipt_vendor_mismatch,
)


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


# ── Review round 1 (Minor 1): dropping digit words erases an ALL-DIGIT
# merchant name entirely, and an empty side short-circuits to "no mismatch" —
# which made a 7-11 slip on a Princess Auto house account invisible, i.e. the
# exact mis-posting this whole field exists to catch. When digit-dropping
# erased a side, the digits ARE the name: compare with them kept. ──────────

def test_an_all_digit_merchant_name_is_still_compared():
    assert is_vendor_mismatch("7-11", "Princess Auto") is True
    assert normalize_vendor_name("7-11") == ""   # 这就是当初的盲区所在


def test_the_all_digit_fallback_stays_tolerant_for_the_same_merchant():
    """回落只是"别丢数字",不是"改判严格" —— 同一家的两种写法仍不算冲突。"""
    assert is_vendor_mismatch("7-11", "7-11 Inc") is False
    assert is_vendor_mismatch("7-11", "7 11") is False


def test_the_fallback_does_not_loosen_the_store_number_case():
    """回落只在某一侧被清空时才触发;门店号那条主路径不受影响。"""
    assert is_vendor_mismatch("Princess Auto #12", "Princess Auto Ltd") is False
    assert is_vendor_mismatch("Canadian Tire #241", "Princess Auto") is True



# ── Task 14: the three-tier verdict (receipt_vendor_mismatch) ───────────────
#
# is_vendor_mismatch above is UNCHANGED — every assertion in this file up to
# here still describes it exactly. What Task 14 added is a caller that decides
# WHETHER to consult it: once both sides name a row in the vendor master, the
# question "same party or not?" has an exact answer and no spelling is
# involved. The tests below have to be able to FAIL for the right reason, so
# each id-tier case is deliberately built so that the TEXT rule would give the
# OPPOSITE verdict — that is the only way to prove the ids are what decided it.

_VENDOR_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
_VENDOR_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_same_vendor_id_is_never_a_mismatch_however_the_names_are_spelled():
    """第一层:两边都有 id 且相同 → 不冲突,**且根本不看文本**。

    名字取的是文本比对必定判 True 的一对("Canadian Tire" vs "Princess Auto",
    上面 test_two_genuinely_different_merchants_are_a_mismatch 钉死的那对)——
    所以这条只要还绿,就证明它走的不是文本那条路。"""
    assert is_vendor_mismatch("Canadian Tire", "Princess Auto") is True   # 文本口径的对照
    assert receipt_vendor_mismatch(
        receipt_vendor_id=_VENDOR_A, receipt_vendor_name="Canadian Tire",
        agreement_vendor_id=_VENDOR_A, agreement_vendor_name="Princess Auto",
    ) is False


def test_different_vendor_ids_are_a_mismatch_even_when_the_names_are_identical():
    """第二层:两边都有 id 但不同 → 冲突,**名字一模一样也照样冲突**。

    主数据里的两行就是两个交易对手,不管它们叫什么 —— 同名不同行是真实存在的
    (同一集团的两个法人、录重了的两条供应商)。文本口径对这一对必判 False,
    所以这条同样只在 id 优先时才成立。"""
    assert is_vendor_mismatch("Princess Auto", "Princess Auto") is False  # 文本口径的对照
    assert receipt_vendor_mismatch(
        receipt_vendor_id=_VENDOR_A, receipt_vendor_name="Princess Auto",
        agreement_vendor_id=_VENDOR_B, agreement_vendor_name="Princess Auto",
    ) is True


def test_an_unbound_receipt_falls_back_to_the_text_comparison():
    """第三层:凭证没绑主数据(匹配不到 = 常态)→ 回落到既有的文本比对,
    逐条沿用上面那批断言,一个字都不改。"""
    for receipt_name, agreement_name in [
        ("Canadian Tire", "Princess Auto"),        # True
        ("Princess Auto #12", "Princess Auto Ltd"),  # False(门店号)
        ("7-11", "Princess Auto"),                 # True(全数字回落)
        ("7-11", "7-11 Inc"),                      # False
        ("***", "Princess Auto"),                  # False(标点无信息)
    ]:
        assert receipt_vendor_mismatch(
            receipt_vendor_id=None, receipt_vendor_name=receipt_name,
            agreement_vendor_id=_VENDOR_A, agreement_vendor_name=agreement_name,
        ) is is_vendor_mismatch(receipt_name, agreement_name)


def test_a_receipt_with_neither_an_id_nor_a_name_is_not_a_mismatch():
    """没填不等于填错 —— 沿用现规则,空凭证商家永远不报冲突。"""
    assert receipt_vendor_mismatch(
        receipt_vendor_id=None, receipt_vendor_name=None,
        agreement_vendor_id=_VENDOR_A, agreement_vendor_name="Princess Auto",
    ) is False
    assert receipt_vendor_mismatch(
        receipt_vendor_id=None, receipt_vendor_name="   ",
        agreement_vendor_id=_VENDOR_A, agreement_vendor_name="Princess Auto",
    ) is False


def test_a_bound_receipt_still_falls_back_when_the_agreement_has_no_vendor_id():
    """id 比对要求**两边**都有。协议的 vendor_id 在库里非空,但这个函数不拿
    "调用方保证"当前提:少了任何一边就回落文本,而不是拿 None 跟 uuid 比大小
    (那会把每一行都判成冲突)。"""
    assert receipt_vendor_mismatch(
        receipt_vendor_id=_VENDOR_A, receipt_vendor_name="Princess Auto #12",
        agreement_vendor_id=None, agreement_vendor_name="Princess Auto Ltd",
    ) is False
    assert receipt_vendor_mismatch(
        receipt_vendor_id=_VENDOR_A, receipt_vendor_name="Canadian Tire",
        agreement_vendor_id=None, agreement_vendor_name="Princess Auto",
    ) is True
