"""内容依据符合性检查 BASIS-01~02。

仅适用于阶段性服务总结报告 / 月进度计划报告（BASIS-01）及
含"编制依据/参考文件"章节的文档（BASIS-02）。
"""
import re

from ..models import Severity
from ..registry import rule, make_issue
from .common import location_of

_CONTRACT_REF = re.compile(r"(根据|依据|按照|按|遵照|符合)[^。；\n]{0,15}合同|合同约定|合同要求|合同规定")
_PLAN_REF = re.compile(r"[项目服务]?实施方案")
_BASIS_HEAD = re.compile(r"^(编制依据|参考文件|编写依据|依据文件|参考依据)")
_BOOK = re.compile(r"《([^》]{4,60})》")


@rule("BASIS-01", "报告编写依据检查", "依据", Severity.MEDIUM,
      doc_types=["阶段性服务总结报告", "月进度计划报告"])
def check_report_basis(doc, ctx):
    text = doc.full_text
    has_contract = bool(_CONTRACT_REF.search(text))
    plan_hits = len(_PLAN_REF.findall(text))
    if not has_contract and plan_hits == 0:
        yield make_issue("BASIS-01", doc, "全文", "",
                         expected="应声明依据合同编写（如「根据项目合同约定」）",
                         suggestion="补充报告编写依据声明，整体依据合同")
    elif not has_contract and plan_hits > 0:
        yield make_issue(
            "BASIS-01", doc, "全文",
            f"全文引用实施方案 {plan_hits} 处，未见依据合同的声明",
            expected="报告整体应依据合同编写；软开内容可依据实施方案",
            suggestion="通篇仅引用实施方案，疑似依据错误，应改为依据合同")
    else:
        # 服务内容描述是否与合同服务范围关联
        m = re.search(r"(服务内容|服务范围)(.{0,400})", text, re.S)
        if m and not _CONTRACT_REF.search(m.group(2)) and _PLAN_REF.search(m.group(2)):
            p0 = next((p for p in doc.paragraphs if re.search(r"服务内容|服务范围", p.text)), None)
            yield make_issue(
                "BASIS-01", doc, location_of(p0) if p0 else "全文",
                m.group(2)[:60],
                expected="服务内容描述应与合同服务范围关联",
                suggestion="服务内容描述仅关联实施方案，应核对合同服务范围")


@rule("BASIS-02", "编制依据清单校验", "依据", Severity.MEDIUM, fixable=True)
def check_basis_list(doc, ctx):
    proj = (ctx.contract.project_name if ctx.contract else "") or \
           (ctx.config.get("project") or {}).get("name", "")
    years = set(re.findall(r"（(\d{4})年）", proj))
    paras = doc.paragraphs
    in_basis = False
    for i, p in enumerate(paras):
        t = p.text
        if _BASIS_HEAD.match(t) and len(t) < 30:
            in_basis = True
            continue
        if in_basis:
            if p.is_heading and not _BASIS_HEAD.match(t):
                in_basis = False
                continue
            for m in _BOOK.finditer(t):
                name = m.group(1)
                if "实施方案" in name or "合同" in name or "项目" in name:
                    cand_years = set(re.findall(r"[（(](\d{4})年[)）]", name))
                    wrong_years = {y for y in cand_years if years and y not in years}
                    if wrong_years:
                        yield make_issue(
                            "BASIS-02", doc, location_of(p),
                            f"《{name}》",
                            expected=f"基准文档年号：{'/'.join(sorted(years))}",
                            suggestion="编制依据清单中文档名称年号与项目不符，疑似复制旧文档")
                    elif "合同" in name and proj and not name.startswith(proj[:10]) \
                            and "合同" not in name[:4]:
                        # 名称与项目主干对不上（如写了别的项目名）
                        core = proj[:8]
                        if core and core not in name and len(name) > 12:
                            yield make_issue(
                                "BASIS-02", doc, location_of(p),
                                f"《{name}》", expected=proj,
                                suggestion="编制依据清单中文档名称与项目基准不符，人工核对")
