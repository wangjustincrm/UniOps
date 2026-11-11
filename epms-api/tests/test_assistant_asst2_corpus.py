"""Assistant round 2 fix (2026-11-11): what the tester had passed is not
routed differently by the round-2 code.

Round 2 put several decisions in code before the planner is asked — splitting
a message at its question marks (question_routes.split), conclusions for rule
questions (rule_verdicts.match), code-written rule answers
(rule_registry.pinned_for), the Marked list (marked_view), the conversation
(question_routes.recall_position) and "what's next" (next_step.route). Run over
the questions Cassie had marked as passed, eight came out split in two with a
second half that has no subject ("PA-20261013-0003 什么时候付的？用的哪个账
户？"), and invented look-alikes were caught by rules meant for something else
("怎么把一张发票加到 PA 里？" given "why invoices cannot share a PA").

So the passed questions are kept here as data (taken from the tracker's
comments, two pieces of interface text left out): none is split or routed by
code unless it is listed below with the route it had when it passed. A rule
change that reroutes one of them fails here.
"""
import pytest

from app.services import (marked_view, next_step, oa_guide, question_routes, rule_registry,
                          rule_verdicts)
from tests.test_assistant_iss_batch4 import _stub

# (issue, the question as the tester asked it and passed it)
PASSED = [
    ('ISS-0001', "采购协议的审批流程是怎样的？"),
    ('ISS-0002', "协议 AGR-202608-0007 的有效期到什么时候？"),
    ('ISS-0002', "协议【AGR-202608-0007】的 NTE 上限用掉了多少？还剩多少？"),
    ('ISS-0002', "哪些协议在 1 年内到期？"),
    ('ISS-0003', "¿Cuándo vence el acuerdo AGR-202608-0007?"),
    ('ISS-0004', "PR-20261015-0003 的服务完成日期是哪天？负责人是谁？"),
    ('ISS-0004', "这张单的供应商是谁？金额多少？"),
    ('ISS-0005', "现在超预算的审批模式是什么？"),
    ('ISS-0007', "AP Clerk 能执行付款吗？"),
    ('ISS-0007', "付款申请的审批流程是怎样的？"),
    ('ISS-0008', "系统里有哪几种采购类型？"),
    ('ISS-0008', "每种采购类型各有多少张请购单？"),
    ('ISS-0008', "有哪几种采购类型"),
    ('ISS-0009', "能在 Teams 里或者回复邮件来审批吗？"),
    ('ISS-0009', "怎么在 Teams 里审批？怎么回复邮件来审批？"),
    ('ISS-0010', "三单匹配会比较收货数量吗？"),
    ('ISS-0010', "采购协议付款需要收货检查吗？"),
    ('ISS-0010', "有 Override Receipt 权限的人可以填理由跳过收货检查"),
    ('ISS-0011', "我现在有几条待办？"),
    ('ISS-0011', "我有哪些紧急待办？"),
    ('ISS-0011', "我现在该做什么？"),
    ('ISS-0011', "几条待办"),
    ('ISS-0011', "该做什么"),
    ('ISS-0013', "List all issued purchase orders"),
    ('ISS-0013', "There are 485 issued purchase orders in total; here are the first 10 ... Click Export to Excel below for the full list."),
    ('ISS-0013', "有哪些发票已经匹配了但货还没收到？"),
    ('ISS-0013', "哪些 PO 已经过了预计交期还没有全部收货？"),
    ('ISS-0013', "哪些发票已经逾期未付？"),
    ('ISS-0013', "现在有多少张 Issued 状态的采购订单？"),
    ('ISS-0013', "Issued 状态的 PO 有多少张"),
    ('ISS-0014', "成本中心 MOH-ENG 科目 CRM0090202 今年还有多少预算可用？"),
    ('ISS-0014', "本财年的预算用了多少？"),
    ('ISS-0014', "本财年预算用了多少"),
    ('ISS-0015', "供应商 Interfax Systems Inc 还有多少发票没有付？总额多少？"),
    ('ISS-0016', "PO-535-2609-02 的发票金额有没有超过 PO？差异多少？"),
    ('ISS-0017', "为什么我不能提交这张 PO 的签署？"),
    ('ISS-0017', "这张单批了没？"),
    ('ISS-0018', "PR 退回后重新提交会从第一步重新审批吗"),
    ('ISS-0019', "GR 确认超时会升级给谁？"),
    ('ISS-0019', "收货确认超时会升级给经理吗？"),
    ('ISS-0019', "收货确认超时升级给谁"),
    ('ISS-0020', "上周一共给供应商付了多少钱？"),
    ('ISS-0020', "How much was paid last week?"),
    ('ISS-0021', "Dairy Farmers of Ontario 还有多少可用贷项？"),
    ('ISS-0021', "系统里有几个承运商？"),
    ('ISS-0022', "NC 里的订单为什么在 EPMS 里找不到？"),
    ('ISS-0022', "能在邮件里修改通知模板吗？"),
    ('ISS-0022', "PA 上的运费要交税吗？"),
    ('ISS-0022', "发票上的运费行怎么处理？"),
    ('ISS-0022', "这张发票能取消匹配吗？"),
    ('ISS-0022', "NC 导入的 PO 由谁来建付款申请？"),
    ('ISS-0022', "NC 订单多久同步一次？"),
    ('ISS-0022', "PO 单号后面带 -2 是什么意思？"),
    ('ISS-0022', "每日摘要邮件什么时候发？"),
    ('ISS-0022', "我能连续催审批人两次吗？PA 能催办吗？"),
    ('ISS-0022', "没有发票时 PA 的税额是怎么算的？"),
    ('ISS-0022', "同一张发票能被两张 PA 付款吗？"),
    ('ISS-0022', "登录验证码多久过期？"),
    ('ISS-0022', "多久不操作会自动退出？"),
    ('ISS-0022', "PO-ABC-2604-03 这个编号是什么意思？"),
    ('ISS-0022', "谁可以对 PO 执行 Place Order？"),
    ('ISS-0022', "怎么基于一张旧的请购单快速建一张新的？"),
    ('ISS-0022', "批准后的请购单 PDF 在哪里？"),
    ('ISS-0022', "总额是负数的请购单会被判定为超预算吗？"),
    ('ISS-0022', "录入手工贷项需要什么？"),
    ('ISS-0023', "采购协议有哪几种？"),
    ('ISS-0023', "什么是挂账账户？"),
    ('ISS-0023', "协议超过 NTE 上限后还能付款吗？"),
    ('ISS-0023', "可以…只是一个警告，不会阻止付款"),
    ('ISS-0023', "过期的协议还能匹配新发票吗？"),
    ('ISS-0023', "超过 NTE 上限还能付款吗"),
    ('ISS-0023', "可以，只是提醒"),
    ('ISS-0023', "过期协议还能匹配发票吗"),
    ('ISS-0024', "pr20260928-0001 现在是什么状态？"),
    ('ISS-0024', "20260928-0001 现在是什么状态？"),
    ('ISS-0027', "我休假时代理人能帮我确认收货吗？"),
    ('ISS-0027', "我可以描述这些审批流程……"),
    ('ISS-0028', "发票匹配的容差是多少？在哪里改？"),
    ('ISS-0028', "同一个供应商的同一个发票号能上传两次吗？"),
    ('ISS-0028', "whether or not you can open it"),
    ('ISS-0028', "容差是多少、在哪里改"),
    ('ISS-0028', "同一供应商同一发票号能上传两次吗"),
    ('ISS-0029', "我可以描述这些批准流程……"),
    ('ISS-0029', "服务类 PO 谁来确认？怎么确认？"),
    ('ISS-0029', "项目类 PR 提交前必须填哪些"),
    ('ISS-0029', "服务类 PO 谁来确认"),
    ('ISS-0030', "PR-20260928-0001 从请购到付款的完整进度是怎样的？"),
    ('ISS-0031', "PO-535-2609-02 收过几次货？每次收了多少？"),
    ('ISS-0032', "今年采购金额前 10 的供应商是哪些？"),
    ('ISS-0032', "这 10 家供应商合计采购金额是多少？"),
    ('ISS-0033', "今年每个月的采购金额是多少？"),
    ('ISS-0034', "发票 INV-2026-0658 的到期日是怎么算的？"),
    ('ISS-0034', "按什么规则定的？"),
    ('ISS-0034', "INV-2026-0658 的到期日是怎么算的？"),
    ('ISS-0034', "系统不记录用了哪种情况"),
    ('ISS-0034', "按什么规则"),
    ('ISS-0035', "所有 PO 的总金额是多少？"),
    ('ISS-0039', "原材料 PO 能在 EPMS 里修改吗？"),
    ('ISS-0039', "可以部分修改"),
    ('ISS-0040', "一张 PA 能付多张 PO 吗？"),
    ('ISS-0041', "谁可以执行付款？AP Clerk 可以吗？"),
    ('ISS-0041', "我自己建的 PA 可以自己执行付款吗？"),
    ('ISS-0042', "现在等待付款的 PA 一共多少钱？"),
    ('ISS-0043', "PA-20261013-0003 什么时候付的？用的哪个账户？"),
    ('ISS-0044', "零件 M0392 的名称、供应商、单价、单位是什么？"),
    ('ISS-0046', "请购单上改了部门会发生什么？"),
    ('ISS-0046', "按申请人还是请购单上选的部门？"),
    ('ISS-0047', "PR-20261022-0005 现在在谁那里？"),
    ('ISS-0048', "10/11 那天收了哪些货？"),
    ('ISS-0048', "11月10日那天收了哪些货？"),
    ('ISS-0048', "19/10 那天收了哪些货？"),
    ('ISS-0049', "我有哪些待办已经超时了？"),
    ('ISS-0054', "系统里有多少个供应商？"),
    ('ISS-0054', "有采购订单的供应商有多少个？"),
    ('ISS-0056', "付款时贷项是怎么抵扣的？"),
    ('ISS-0056', "Amazon 现在有哪些可用的供应商贷项？"),
    ('ISS-0059', "已付款 PA 对应的发票是否都显示 paid？"),
    ('ISS-0061', "怎么催审批人？"),
    ('ISS-0061', "我打开审批链接时这张单已经被别人批了，会看到什么？"),
    ('ISS-0061', "怎么催审批人"),
    ('ISS-0061', "别人已批后看到什么"),
    ('ISS-0063', "System Health 显示 Healthy，系统真的健康吗？"),
    ('ISS-0063', "服务完成日到了会提醒吗？"),
    ('ISS-0063', "System Health 真的健康吗"),
    ('ISS-0063', "服务完成日会提醒吗"),
    ('ISS-0064', "PO 签署可以被拒绝吗？"),
    ('ISS-0064', "签署完成后 PO 的状态会变吗？"),
    ('ISS-0064', "签名在哪里设置？"),
    ('ISS-0067', "我在这次对话里问的第一个问题是什么？"),
    ('ISS-0067', "我问的第 50 个问题是什么？"),
    ('ISS-0067', "我问的第一个问题是什么"),
    ('ISS-0071', "今天收了哪些货？"),
    ('ISS-0071', "PO-079-2605-03 是什么状态"),
    ('ISS-0073', "我是谁？我有哪些角色？")
]
QUESTIONS = [q for _iss, q in PASSED]

# Passed as split in these parts.
SPLIT_WHEN_PASSED = {
    "怎么在 Teams 里审批？怎么回复邮件来审批？": ["怎么在 Teams 里审批？", "怎么回复邮件来审批？"],
    "我能连续催审批人两次吗？PA 能催办吗？": ["我能连续催审批人两次吗？", "PA 能催办吗？"],
    "我是谁？我有哪些角色？": ["我是谁？", "我有哪些角色？"],
}

# Passed with a route the code chose — kept.
ROUTED_WHEN_PASSED = {
    "PR 退回后重新提交会从第一步重新审批吗": {"pinned": ["resubmitted_after_return"]},
    "GR 确认超时会升级给谁？":
        {"pinned": ["gr_acknowledgement_reminders", "service_completion_reminders"]},
    "收货确认超时会升级给经理吗？":
        {"pinned": ["gr_acknowledgement_reminders", "service_completion_reminders"]},
    "收货确认超时升级给谁":
        {"pinned": ["gr_acknowledgement_reminders", "service_completion_reminders"]},
    "这张发票能取消匹配吗？": {"pinned": ["unmatching_an_invoice"]},
    "我能连续催审批人两次吗？PA 能催办吗？": {"pinned": ["send_reminder"]},
    "录入手工贷项需要什么？": {"pinned": ["recording_a_manual_credit"]},
    "发票 INV-2026-0658 的到期日是怎么算的？": {"pinned": ["invoice_due_date"]},
    "INV-2026-0658 的到期日是怎么算的？": {"pinned": ["invoice_due_date"]},
    "怎么催审批人？": {"pinned": ["send_reminder"]},
    "怎么催审批人": {"pinned": ["send_reminder"]},
    "服务完成日到了会提醒吗？": {"pinned": ["service_completion_reminders"]},
    "服务完成日会提醒吗": {"pinned": ["service_completion_reminders"]},
    "服务类 PO 谁来确认？怎么确认？": {"verdict": "service_receipt_is_the_owners_or_the_requesters"},
    "服务类 PO 谁来确认": {"verdict": "service_receipt_is_the_owners_or_the_requesters"},
    "项目类 PR 提交前必须填哪些": {"verdict": "service_and_project_prs_name_an_owner"},
    "PR-20260928-0001 从请购到付款的完整进度是怎样的？":
        {"next": ("chain", ("pr", "PR-20260928-0001"))},
    "我在这次对话里问的第一个问题是什么？": {"recall": 1},
    "我问的第 50 个问题是什么？": {"recall": 50},
    "我问的第一个问题是什么": {"recall": 1},
}
PAGE = ("po", "PO-576-2610-01")


def _routes(q: str) -> dict:
    v = rule_verdicts.match(q)
    return {
        "verdict": v.id if v else None,
        "pinned": rule_registry.pinned_for(q) or None,
        "recall": question_routes.recall_position(q),
        "next": next_step.route(q, None),
        "marked": marked_view.asks_for_marked(q) or None,
        "oa": oa_guide.is_oa_question(q, None) or oa_guide.is_oa_question(q, "portal") or None,
    }


def test_the_corpus_is_what_was_passed():
    assert len(PASSED) == 134 and len(set(QUESTIONS)) == 134
    assert set(SPLIT_WHEN_PASSED) <= set(QUESTIONS)
    assert set(ROUTED_WHEN_PASSED) <= set(QUESTIONS)


@pytest.mark.parametrize("iss, q", PASSED)
def test_a_passed_question_is_not_split_differently(iss, q):
    assert question_routes.split(q) == SPLIT_WHEN_PASSED.get(q, [q])


@pytest.mark.parametrize("iss, q", PASSED)
def test_a_passed_question_keeps_its_route(iss, q):
    expected = {"verdict": None, "pinned": None, "recall": None, "next": None,
                "marked": None, "oa": None, **ROUTED_WHEN_PASSED.get(q, {})}
    assert _routes(q) == expected
    if "next" not in ROUTED_WHEN_PASSED.get(q, {}):
        # On a document's own page as well.
        assert next_step.route(q, PAGE) is None


def test_the_parts_of_a_passed_split_keep_their_routes():
    assert rule_registry.pinned_for("我能连续催审批人两次吗？") == ["send_reminder"]
    assert rule_registry.pinned_for("PA 能催办吗？") == ["send_reminder"]
    for part in ("怎么在 Teams 里审批？", "怎么回复邮件来审批？", "我是谁？", "我有哪些角色？"):
        assert _routes(part) == dict.fromkeys(_routes(part)), part


# ── Elsewhere on the tracker: a question and its follow-on are one ───────────

@pytest.mark.parametrize("q", [
    # descriptions and other comments
    "PO-576-2610-01 是邮件下单还是在线下单？发给了谁？什么时候发的？",
    "PR-20261019-0014 现在在谁那里？已经等了几天？",
    "谁可以编辑我的请购单？什么状态下可以编辑？",
    "Service/Project Owner 可以不填吗？可以选其他部门的人吗？",
    "杂项/耗材类采购需要做预算检查吗？原材料类呢？",
    "什么是分解因子？什么时候必须填？",
    "我在这次对话里问的第一个问题是什么？你当时怎么回答的？",
    "这张收货单是哪个 PO 的？收了多少？",
    "NC 里刚批准的采购订单，多久能在 EPMS 里看到？你能马上查到吗？",
    "PO 已经下单了，货到了以后谁要做什么？我是申请人。",
    "这里的'上周'是哪几天？是按什么日期算的？",
    "这张发票付了没有？金额多少？",
    "When will I get paid? How do I know?",
    "收货报表（Receiving Report）里本月（10 月 1 日到今天）一共收了多少行、多少张收货单、涉及多少张 PO？"
    "平均交货周期是多少天？",
    # invented look-alikes
    "上个月停用了哪些供应商？采购金额多少？",
    "How do I save a draft PR? I have a question.",
    "为什么我的发票不在 PA 里，怎么加？",
    "帮我查一下，PO-576-2610-01 的状态是什么？",
    "PO-535-2609-02 的金额是多少，含税吗？",
    "PO-535-2609-02 和 INV-2026-0631 的金额一致吗？",
    "比较 PO-535-2609-02 与 INV-2026-0631 的差异是多少？",
])
def test_a_follow_on_without_a_subject_is_not_split_off(q):
    assert question_routes.split(q) == [q]


@pytest.mark.parametrize("q, parts", [
    ("最近有哪些快递单？有几个承运商？", ["最近有哪些快递单？", "有几个承运商？"]),
    ("有哪些供应商已经停用？停用后还能下单吗？", ["有哪些供应商已经停用？", "停用后还能下单吗？"]),
    ("GR-20261019-0001 是哪天几点收货的？10 月 18 日收了哪些货？",
     ["GR-20261019-0001 是哪天几点收货的？", "10 月 18 日收了哪些货？"]),
    ("怎么在 Teams 里审批？怎么回复邮件来审批？GR 确认超时会升级给谁？",
     ["怎么在 Teams 里审批？", "怎么回复邮件来审批？", "GR 确认超时会升级给谁？"]),
    ("who am I? what roles do I have?", ["who am I?", "what roles do I have?"]),
    ("a? and b?", ["a?", "and b?"]),
    ("PR-20261015-0006 和 PA-20260923-0017 现在是什么状态？",
     ["PR-20261015-0006 现在是什么状态？", "PA-20260923-0017 现在是什么状态？"]),
])
def test_a_question_with_its_own_subject_is_still_split(q, parts):
    assert question_routes.split(q) == parts


# ── Invented look-alikes: not caught by a rule meant for something else ──────

@pytest.mark.parametrize("q", [
    "PO 不在列表里怎么办？",
    "为什么我的发票不在 PA 里，怎么加？",
    "怎么把一张发票加到 PA 里？",
    "不同供应商的 PA 分别由谁审批？",
    "全部收货的 PO 还可以匹配发票吗？",
    "Fully received PO — can I still create a PA?",
    "全部收货的 PO 还能再开发票吗？",
    "货到了以后多久能付款？",
    "货到了但发票还没来，可以先建付款申请吗？",
    "到货后发现数量不对怎么办？",
    "服务类 PO 收货后谁来付款？",
    "What is the status of milk orders this week?",
    "milk 订单是什么状态的有几张",
    "我是第一审批人，提交后会收到邮件吗？",
])
def test_a_look_alike_gets_no_verdict(q):
    assert rule_verdicts.match(q) is None


@pytest.mark.parametrize("q", [
    "上个月停用了哪些供应商？采购金额多少？",
    "已停用的供应商还有哪些 PO 没收货？",
    "谁改了我的请购单？",
    "上个月录了多少笔手工贷项？",
    "上周有哪些发票被取消匹配了？",
    "上个月有多少张被退回后重新提交的 PR？",
    "停用供应商的采购金额是多少？",          # vendor.yaml: "采购" alone is not "can I order"
])
def test_a_data_question_is_not_given_a_rule_card(q):
    assert rule_registry.pinned_for(q) == []


@pytest.mark.parametrize("q, verdict", [
    # What the verdicts are for still gets them.
    ("我要休假，怎么让同事帮我审批？", "delegation_is_set_up_in_portal"),
    ("我要出差，怎么让同事帮我审批？", "delegation_is_set_up_in_portal"),
    ("Absence Cover 怎么设置？", "delegation_is_set_up_in_portal"),
    ("为什么这几张发票不能放在同一张 PA 里？", "why_invoices_cannot_share_a_pa"),
    ("我能把不同供应商的几张发票放进同一张付款申请吗？", "one_pa_one_vendor"),
    ("不同供应商的付款申请可以一起提交审批吗？", "one_pa_one_vendor"),
    ("已经全部收货的采购订单还能再建收货单吗？", "fully_received_po_takes_no_receipt"),
    ("全部收货的 PO 还能再建收货单吗", "fully_received_po_takes_no_receipt"),
    ("实物收货的流程是怎样的？", "the_requester_acknowledges_and_collects"),
    ("服务类采购由谁确认收货？", "service_receipt_is_the_owners_or_the_requesters"),
    ("服务类请购单的收货确认任务会发给谁？", "service_receipt_is_the_owners_or_the_requesters"),
    ("Milk / NC 状态是什么意思？", "what_milk_and_nc_pending_mean"),
    ("我自己就是第一审批人，提交后还需要自己再批吗？", "requester_who_is_first_approver_still_approves"),
    ("我自己是第一审批人，提交后还要自己批吗", "requester_who_is_first_approver_still_approves"),
])
def test_what_a_verdict_is_for_still_gets_it(q, verdict):
    v = rule_verdicts.match(q)
    assert v is not None and v.id == verdict


@pytest.mark.parametrize("q, card", [
    ("停用后还能下单吗？", "inactive_vendors"),
    ("can an inactive vendor still be ordered from", "inactive_vendors"),
    ("已经提交的请购单想修改怎么办？", "who_can_edit_a_pr"),
    ("谁可以编辑我的请购单？", "who_can_edit_a_pr"),
    ("INV-2026-0631 能取消匹配吗", "unmatching_an_invoice"),
])
def test_what_a_card_is_for_still_gets_it(q, card):
    assert card in rule_registry.pinned_for(q)


def test_look_alikes_of_the_other_code_routes():
    # next_step: not "what else", not any "who will".
    assert next_step.route("What else do I need to fill in for this PR?", PAGE) is None
    assert next_step.route("Who will receive the goods for this PO?", PAGE) is None
    assert next_step.route("付款申请已经批完了，什么时候付款？谁来付？", None) == ("flow", "process_pa")
    # marked_view: the person's own marked questions only.
    assert not marked_view.asks_for_marked("发票被标记为有问题怎么办？")
    assert not marked_view.asks_for_marked("How do I save a draft PR? I have a question.")
    assert marked_view.asks_for_marked("你可以查询到我mark问题有哪些吗？")
    # recall: the what-word ends the question.
    assert question_routes.recall_position("我第二个问题是哪张发票没付？") is None
    assert question_routes.recall_position("我的第一个问题是哪个供应商最贵？") is None
    assert question_routes.recall_position("我问的第二个问题是哪个？") == 2
    # OA: approval delegation asked on the Portal is not a claim question.
    assert not oa_guide.is_oa_question("我要出差，怎么让同事帮我审批？", "portal")
    assert not oa_guide.is_oa_question("我要出差，怎么让同事帮我审批？", None)
    assert oa_guide.is_oa_question("出差的餐费怎么报销？", "portal")


# ── Through the chat: the whole question reaches the planner ─────────────────

CHAT = "/api/v1/assistant/chat"


@pytest.mark.parametrize("q", [
    "PA-20261013-0003 什么时候付的？用的哪个账户？",    # passed whole, then split in round 2
    "上周有哪些发票被取消匹配了？",                     # data, not the unmatching rule card
])
async def test_the_planner_gets_the_question_whole(admin_client, monkeypatch, q):
    calls = _stub(monkeypatch, plans=[
        {"kind": "cannot", "reason": "too_ambiguous", "explanation": "planned"}])
    body = (await admin_client.post(CHAT, json={"message": q})).json()
    assert [c["message"] for c in calls["plan"]] == [q]
    assert body["kind"] != "parts" and not body.get("pinned_rules")
