"""检查引擎：加载文档 → 调度规则 → 汇总报告。UI 通过本模块的 API 驱动页面2。"""
import os
from typing import Callable

from . import docloader
from .config import load_config
from .models import ContractProfile, Issue, UnifiedDoc
from .probability import apply_probability_scores
from .registry import CheckContext, all_rules
from . import rules  # noqa: F401  触发各规则模块注册


def find_pairs(paths: list) -> list:
    """按文件名主体配对 Word 与盖章 PDF：同名（去扩展名）的 docx/doc 与 pdf 配对。"""
    docs = [p for p in paths if os.path.splitext(p)[1].lower() in (".doc", ".docx", ".pdf")]
    by_stem = {}
    for p in docs:
        by_stem.setdefault(os.path.splitext(p)[0], []).append(p)
    pairs = []
    for stem, group in by_stem.items():
        words = [g for g in group if g.lower().endswith((".doc", ".docx"))]
        pdfs = [g for g in group if g.lower().endswith(".pdf")]
        if words:
            for w in words:
                pairs.append((w, pdfs[0] if pdfs else ""))
        else:
            pairs.extend((p, "") for p in pdfs)
    return pairs


def load_all(paths: list) -> list:
    pairs = find_pairs(paths)
    docs = []
    for path, paired in pairs:
        docs.append(docloader.load(path, paired_pdf=paired))
    return docs


def run_checks(docs: list, config_path: str = None,
               progress_cb: Callable[[int, int, str], None] = None,
               enabled_rules: list = None) -> list:
    """对全部文档执行所有已注册规则，返回 Issue 列表。

    progress_cb(done, total, current_label)：供 UI 进度条。
    """
    cfg = load_config(config_path) if config_path else load_config()
    c = cfg.get("contract", {})
    contract = ContractProfile(
        project_name=c.get("project_name", ""), employer=c.get("employer", ""),
        contractors=c.get("contractors", []), supervisor=c.get("supervisor", ""),
        amount_number=c.get("amount_number", ""), amount_cn=c.get("amount_cn", ""),
        payment_terms=c.get("payment_terms", []), service_period=c.get("service_period", ""),
        contract_no=c.get("contract_no", ""),
    )
    registry = all_rules()
    if enabled_rules:
        registry = {k: v for k, v in registry.items() if k in enabled_rules}

    # 估算总步数用于进度条
    steps = []
    for doc in docs:
        for meta in registry.values():
            if meta["doc_types"] and doc.doc_type not in meta["doc_types"]:
                continue
            steps.append((doc, meta))
    issues: list = []
    seqs: dict = {}
    total = max(len(steps), 1)
    for i, (doc, meta) in enumerate(steps):
        if progress_cb:
            progress_cb(i, total, f"{doc.name} · {meta['id']}")
        ctx = CheckContext(cfg, contract, docs)
        try:
            for issue in meta["fn"](doc, ctx) or []:
                seqs[meta["id"]] = seqs.get(meta["id"], 0) + 1
                issue.id = f"{meta['id']}-{seqs[meta['id']]}"
                issues.append(issue)
        except Exception as e:  # 单条规则异常不中断整体检查
            issues.append(Issue(
                id=f"{meta['id']}-ERR", rule_id=meta["id"], rule_name=meta["name"],
                category=meta["category"], severity=meta["severity"],
                doc_name=doc.name, location="-", original="",
                suggestion=f"规则执行异常: {e}", fixable=False))
    if progress_cb:
        progress_cb(total, total, "完成")
    apply_probability_scores(issues, docs, cfg)
    order = {"高": 0, "中": 1, "低": 2}
    issues.sort(key=lambda x: (order[x.severity.value], x.rule_id))
    return issues
