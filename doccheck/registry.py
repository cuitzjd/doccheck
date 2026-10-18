"""规则注册表：所有检查规则用 @rule 装饰器注册，引擎按注册表调度。

规则函数签名：fn(doc: UnifiedDoc, ctx: CheckContext) -> Iterable[Issue]
ctx 提供：config（dict）、contract（ContractProfile）、all_docs（同批其他文档，供跨文档规则）。
"""
from typing import Callable, Iterable
from .models import Issue, Severity, UnifiedDoc


class CheckContext:
    def __init__(self, config: dict, contract=None, all_docs=None):
        self.config = config
        self.contract = contract
        self.all_docs = all_docs or []


_RULES: dict = {}


def rule(rule_id: str, name: str, category: str, severity: Severity,
         fixable: bool = False, doc_types: list = None, needs_paired_pdf: bool = False):
    """注册检查规则。

    doc_types: 适用文档类型（None=全部），如 ["阶段性服务总结报告", "月进度计划报告"]
    needs_paired_pdf: True 表示该规则需渲染页（页码类），无配对 PDF 时降级/跳过
    """
    def deco(fn: Callable[[UnifiedDoc, CheckContext], Iterable[Issue]]):
        _RULES[rule_id] = {
            "id": rule_id, "name": name, "category": category,
            "severity": severity, "fixable": fixable, "fn": fn,
            "doc_types": doc_types, "needs_paired_pdf": needs_paired_pdf,
        }
        return fn
    return deco


def all_rules() -> dict:
    return dict(_RULES)


def make_issue(rule_id: str, doc: UnifiedDoc, location: str, original: str,
               expected: str = "", suggestion: str = "", seq: int = 0) -> Issue:
    meta = _RULES[rule_id]
    return Issue(
        id=f"{rule_id}-{doc.name[:12]}-{seq}",
        rule_id=rule_id, rule_name=meta["name"], category=meta["category"],
        severity=meta["severity"], doc_name=doc.name, location=location,
        original=original, expected=expected, suggestion=suggestion,
        fixable=meta["fixable"],
    )
