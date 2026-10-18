"""本地上下文概率评分回归测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doccheck.config import load_config
from doccheck.models import Issue, Paragraph, Severity, UnifiedDoc
from doccheck.probability import (
    _merge_jev_probability,
    apply_probability_scores,
    heuristic_score,
)


CFG = load_config(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"))
CFG["contract"] = {
    **CFG.get("contract", {}),
    "confirmed": True,
    "project_name": "测试项目",
    "employer": "建设单位",
    "contractors": ["承建单位"],
    "payment_terms": [
        "首期款：合同总价30%",
        "尾款：完成全部工作后支付不超过合同总价20%",
    ],
}


def make_doc(text, doc_type="技术设计方案"):
    return UnifiedDoc(
        path="/tmp/probability.docx",
        name="probability.docx",
        doc_type=doc_type,
        fmt="docx",
        paragraphs=[Paragraph(index=0, text=text)],
    )


def make_issue(rule_id, original, expected="", location="段落 0"):
    return Issue(
        id=f"{rule_id}-1",
        rule_id=rule_id,
        rule_name=rule_id,
        category="测试",
        severity=Severity.HIGH,
        doc_name="probability.docx",
        location=location,
        original=original,
        expected=expected,
    )


planned = make_doc("里程碑：系统运行稳定，后续将进入试运行阶段。", "月进度计划报告")
fact = make_doc("系统已上线，目前用户使用正常，正式运行稳定。", "阶段性服务总结报告")
service = make_doc("省土地调查规划院作为服务对象，使用系统查询成果。")
role = make_doc("建设单位为国土资源技术中心，负责项目统筹。")

plan_score, plan_reason = heuristic_score(
    make_issue("STAGE-01", planned.paragraphs[0].text), planned)
fact_score, fact_reason = heuristic_score(
    make_issue("STAGE-01", fact.paragraphs[0].text), fact, CFG)
safe_score, safe_reason = heuristic_score(
    make_issue("CONTRACT-05", service.paragraphs[0].text), service, CFG)
role_score, role_reason = heuristic_score(
    make_issue("CONTRACT-05", role.paragraphs[0].text), role, CFG)
punc_score, _ = heuristic_score(
    make_issue("PUNC-01", "本次检查。。请整改。"), make_doc("本次检查。。请整改。"))
format_score, _ = heuristic_score(
    make_issue("FORMAT-03", "本段存在行距差异", "行距 1.5 ≠ 主流 1.25"),
    make_doc("本段存在行距差异"))

assert plan_score < 0.1, (plan_score, plan_reason)
assert fact_score > 0.85, (fact_score, fact_reason)
assert safe_score < 0.15, (safe_score, safe_reason)
assert role_score > 0.85, (role_score, role_reason)
assert "长期记忆：" in safe_reason and "交叉验证=exempt" in safe_reason
assert punc_score > format_score
assert len({plan_score, fact_score, safe_score, role_score, punc_score, format_score}) >= 5
assert 0.82 not in {plan_score, fact_score, safe_score, role_score, punc_score, format_score}

split_doc = make_doc("应付合同总金额20%，支付给乙方1的金额为尾款支付金额×20.83%，支付给乙方2的金额为尾款支付金额×79.17%。")
split_issue = make_issue("CONTRACT-03", split_doc.paragraphs[0].text)
apply_probability_scores([split_issue], [split_doc], CFG)
assert split_issue.error_probability < 0.15
assert split_issue.context_validation["validation"]["relation"] == "exempt"

# 同一段内的测试句不能把当前“已上线”句豁免掉；短期记忆必须定位到命中句。
mixed_doc = make_doc("内部测试正常。系统已上线，用户使用正常。", "阶段性服务总结报告")
mixed_issue = make_issue("STAGE-01", "系统已上线，用户使用正常。")
apply_probability_scores([mixed_issue], [mixed_doc], CFG)
mixed_score = mixed_issue.error_probability
assert mixed_score > 0.85, (mixed_score, mixed_issue.probability_reason)
assert mixed_issue.context_validation["validation"]["relation"] == "conflict"
assert "系统已上线" in mixed_issue.context_validation["short_term"]["current_sentence"]

# 合同语义证据不足时不输出高确定性结论。
unconfirmed = dict(CFG)
unconfirmed["contract"] = dict(CFG["contract"], confirmed=False)
unknown_issue = make_issue("CONTRACT-05", "某陌生单位")
unknown_doc = make_doc("某陌生单位出现在说明中。")
apply_probability_scores([unknown_issue], [unknown_doc], unconfirmed)
unknown_score = unknown_issue.error_probability
assert unknown_score <= 0.64
assert unknown_issue.context_validation["validation"]["relation"] == "insufficient"

mismatch_doc = make_doc("签订合同生效后支付合同总价的40%。")
mismatch_issue = make_issue("CONTRACT-03", mismatch_doc.paragraphs[0].text)
apply_probability_scores([mismatch_issue], [mismatch_doc], CFG)
assert mismatch_issue.error_probability > 0.85
assert mismatch_issue.context_validation["validation"]["relation"] == "conflict"

# Jev 不能覆盖本地已经确认的豁免关系。
assert _merge_jev_probability(0.99, 0.06, split_issue) <= 0.12
assert _merge_jev_probability(0.01, 0.94, mismatch_issue) >= 0.92
print("Heuristic probability context tests passed")
