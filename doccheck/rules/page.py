"""页码检查 PAGE-01~05。

docx 的页码是页脚 PAGE 域代码，精确页序列依赖配对盖章 PDF（doc.paired_pdf）；
无配对 PDF 时只做 docx 结构检查（PAGE-03/05），其余跳过并在 doc.meta 记录原因。
"""
import os
import re

from .. import docloader
from ..models import Severity
from ..registry import rule, make_issue

_TOTAL_PAGES = re.compile(r"共\s*(\d+)\s*页")
_TOC_FRONT_MATTER = re.compile(
    r"^(目录|目次|contents?|摘要|前言|引言|编制说明|说明|封面|扉页)$",
    re.I,
)


def _note(doc, msg):
    doc.meta.setdefault("page_check_notes", []).append(msg)


def _render_doc(doc):
    """页码类规则的目标文档：docx 用配对盖章 PDF，PDF 用自身。无则 None。"""
    if doc.fmt == "pdf":
        return doc
    if "_paired_doc" not in doc.meta:
        pd = None
        if doc.paired_pdf and os.path.exists(doc.paired_pdf):
            try:
                pd = docloader.load(doc.paired_pdf)
            except Exception as e:
                _note(doc, f"配对 PDF 加载失败: {e}")
        doc.meta["_paired_doc"] = pd
    return doc.meta["_paired_doc"]


def _labels(rd):
    """页脚标注序列 [(物理页, 标注页码)]，按物理页排序去重。"""
    seq, seen = [], set()
    for phys, label in sorted(rd.footer_page_numbers):
        if phys not in seen:
            seen.add(phys)
            seq.append((phys, label))
    return seq


def _fractions(rd):
    """页脚 X/X 标注 [(物理页, 当前页, 总页数)]。"""
    return sorted(getattr(rd, "footer_page_fractions", []) or [])


def _footer_totals(rd):
    """页脚右侧总页数 [(物理页, 总页数)]。"""
    return sorted(getattr(rd, "footer_page_totals", []) or [])


def _body_toc_entries(rd):
    """排除目录自身、摘要等前置项，返回可用于正文起始判断的目录条目。"""
    entries = []
    for title, label in getattr(rd, "toc_entries", []) or []:
        norm = re.sub(r"\s+", "", title).strip("·.…")
        if norm and not _TOC_FRONT_MATTER.fullmatch(norm):
            entries.append((title, label))
    return entries


def _body_start_label(rd):
    """优先用目录首个正文条目确定正文页码，否则用首个页脚标注。"""
    entries = _body_toc_entries(rd)
    if entries:
        return entries[0][1]
    seq = _labels(rd)
    return seq[0][1] if seq else None


@rule("PAGE-01", "缺页/断号检查", "页码", Severity.HIGH, needs_paired_pdf=True)
def check_page_sequence(doc, ctx):
    rd = _render_doc(doc)
    if rd is None or not rd.footer_page_numbers:
        _note(doc, "PAGE-01 跳过：无配对盖章 PDF 或未提取到页脚页码")
        return
    seq = _labels(rd)
    if rd.page_count and len(seq) < rd.page_count * 0.5:
        _note(doc, f"PAGE-01：页脚文本层不完整，仅覆盖 {len(seq)}/{rd.page_count} 页"
                   "（部分盖章 PDF 页脚缺字），未覆盖页请人工抽查")
    first_phys, first_label = seq[0]
    if first_label != 1:
        yield make_issue("PAGE-01", doc, f"第 {first_phys} 页（页脚标注）",
                         f"页脚页码从第 {first_label} 页开始，未从第 1 页开始",
                         expected="首个有效页脚标注应为第 1 页",
                         suggestion="核对正文分节的页码起始值和封面/目录是否误编页码")
    for i in range(1, len(seq)):
        phys, label = seq[i]
        pphys, plabel = seq[i - 1]
        if label == plabel + 1:
            continue  # 正常续编
        if label == 1 and plabel != 1:
            yield make_issue("PAGE-01", doc, f"第 {phys} 页（页脚标注）",
                             f"页脚页码在第 {plabel} 页后重新从第 1 页开始",
                             suggestion="确认是否为附录独立编号；若不是，请取消分节重编号")
            continue
        if label == plabel:
            yield make_issue("PAGE-01", doc, f"第 {phys} 页（页脚标注）",
                             f"页脚页码重复：第 {plabel} 页在物理第 {pphys}、{phys} 页出现两次",
                             suggestion="人工核对是否重复插页")
        elif label > plabel + 1:
            yield make_issue("PAGE-01", doc, f"第 {phys} 页（页脚标注）",
                             f"页脚页码跳号：第 {plabel} 页之后为第 {label} 页，缺第 {plabel + 1}~{label - 1} 页",
                             suggestion="人工核对是否缺页")
        else:
            yield make_issue("PAGE-01", doc, f"第 {phys} 页（页脚标注）",
                             f"页脚页码断号/倒序：第 {plabel} 页之后为第 {label} 页",
                             suggestion="人工核对页码编排")


@rule("PAGE-02", "页码总数一致性", "页码", Severity.HIGH, fixable=True,
      needs_paired_pdf=True)
def check_total_pages(doc, ctx):
    rd = _render_doc(doc)
    if rd is None:
        _note(doc, "PAGE-02 跳过：无配对盖章 PDF")
        return
    max_label = max((l for _, l in rd.footer_page_numbers), default=0)
    fractions = _fractions(rd)
    footer_totals = _footer_totals(rd)
    if fractions:
        seen_totals = {}
        for phys, current, total in fractions:
            seen_totals.setdefault(total, []).append(phys)
            if current < 1 or total < 1 or current > total:
                yield make_issue(
                    "PAGE-02", doc, f"第 {phys} 页（页脚 X/X）",
                    f"页脚当前页/总页数为 {current}/{total}，当前页不在有效范围内",
                    expected=f"当前页应为 1 至 {total} 之间",
                    suggestion="核对页脚左侧当前页码和右侧总页数")
        if len(seen_totals) > 1:
            detail = "、".join(f"{n}页（物理第{p[0]}页起）"
                               for n, p in sorted(seen_totals.items()))
            yield make_issue(
                "PAGE-02", doc, "页脚 X/X 总页数",
                f"页脚右侧总页数不一致：{detail}",
                expected="所有页脚右侧总页数应一致",
                suggestion="统一页脚字段中的总页数")
        # X/X 的右侧是总页数：末页左侧当前页必须达到该总数。
        if len(seen_totals) == 1:
            total = next(iter(seen_totals))
            if max_label and max_label != total:
                last_phys = max(phys for phys, _, _ in fractions)
                yield make_issue(
                    "PAGE-02", doc, f"第 {last_phys} 页（页脚 X/X）",
                    f"页脚右侧总页数为 {total}，但检测到的最大当前页为 {max_label}",
                    expected=f"末页应为 {total}/{total}页",
                    suggestion="核对页脚左侧当前页码、右侧总页数及是否存在漏页")
        return
    if footer_totals and len({n for _, n in footer_totals}) > 1:
        yield make_issue(
            "PAGE-02", doc, "页脚总页数",
            "不同页面的页脚总页数不一致",
            expected="所有页面的页脚总页数应一致",
            suggestion="统一页脚中的总页数")
    accept = {rd.page_count}
    if max_label:
        accept.add(max_label)
    hits = set()
    for p in rd.paragraphs:
        for m in _TOTAL_PAGES.finditer(p.text):
            n = int(m.group(1))
            if n in accept or n in hits:  # 同一错误标注只报一次
                continue
            hits.add(n)
            yield make_issue(
                "PAGE-02", doc, f"第 {p.page} 页",
                f"标注总页数「共 {n} 页」",
                expected=f"实际物理页数 {rd.page_count} 页" +
                         (f" / 页脚最大标注 {max_label} 页" if max_label else ""),
                suggestion="核对总页数标注并修正")


@rule("PAGE-03", "正文起始页检查", "页码", Severity.MEDIUM, fixable=True)
def check_body_start_page(doc, ctx):
    rd = _render_doc(doc)
    if rd is not None and rd.footer_page_numbers:
        body_label = _body_start_label(rd)
        if body_label == 1:
            return
        yield make_issue("PAGE-03", doc, "页脚页码序列",
                         f"正文页脚标注从第 {body_label} 页起编，未从第 1 页起编",
                         expected="正文第一章从「第 1 页」起编",
                         suggestion="调整正文分节页码起始值为 1")
        return
    if doc.fmt in ("docx", "doc"):
        restarts = doc.meta.get("sect_page_restarts") or []
        if any(str(s) == "1" for s in restarts if s is not None):
            return
        if doc.meta.get("footer_has_page_field"):
            yield make_issue("PAGE-03", doc, "分节设置",
                             "未发现从 1 起编页码的分节（pgNumType start=1）",
                             expected="正文第一章所在节页码从 1 起编",
                             suggestion="在正文首节设置页码起始值为 1")
        else:
            _note(doc, "PAGE-03 跳过：docx 页脚无 PAGE 域且无配对 PDF")
    else:
        _note(doc, "PAGE-03 跳过：无页脚页码序列")


@rule("PAGE-04", "目录页码编排检查", "页码", Severity.MEDIUM, fixable=True,
      needs_paired_pdf=True)
def check_toc_pages(doc, ctx):
    rd = _render_doc(doc)
    if rd is None or not rd.toc_entries:
        _note(doc, "PAGE-04 跳过：无配对 PDF 或文档无目录条目")
        return
    label2phys = {l: ph for ph, l in _labels(rd)}
    body_entries = _body_toc_entries(rd)
    if body_entries and body_entries[0][1] != 1:
        title, label = body_entries[0]
        yield make_issue(
            "PAGE-04", doc, f"目录首个正文条目「{title}」",
            f"目录首个正文条目标注为第 {label} 页，未从第 1 页开始",
            expected="目录首个正文条目应标注第 1 页",
            suggestion="核对正文分节页码起始值与目录更新状态")
    previous = None
    for title, label in body_entries:
        if previous is not None and label < previous:
            yield make_issue(
                "PAGE-04", doc, f"目录条目「{title}」",
                f"目录页码顺序倒退：第 {previous} 页之后为第 {label} 页",
                suggestion="更新目录并核对章节实际页码")
        previous = label
    page_text = {}
    for p in rd.paragraphs:
        page_text[p.page] = (page_text.get(p.page, "") + p.text).replace(" ", "")
    for title, label in rd.toc_entries:
        norm = re.sub(r"\s+", "", title)
        if not norm or len(norm) < 2:
            continue
        phys = label2phys.get(label)
        if phys is None:
            yield make_issue("PAGE-04", doc, f"目录条目「{title}」",
                             f"目录标注页码 {label} 不在页脚页码序列中",
                             suggestion="核对目录页码与正文页脚")
        elif norm[:20] not in page_text.get(phys, ""):
            yield make_issue("PAGE-04", doc, f"目录条目「{title}」",
                             f"目录标注第 {label} 页（物理第 {phys} 页），但该页未找到此标题",
                             suggestion="核对目录页码与正文实际位置")


@rule("PAGE-05", "封面/目录无页码检查", "页码", Severity.MEDIUM, fixable=True)
def check_cover_no_pageno(doc, ctx):
    rd = _render_doc(doc)
    if rd is not None and rd.footer_page_numbers:
        seq = _labels(rd)
        # 正文起点：首个标注为 1 的物理页；无重起编则以首个标注页之前视为前置部分
        body_start = next((ph for ph, l in seq if l == 1), None)
        flagged = set()
        for phys, label in seq:
            if phys == 1:
                flagged.add((phys, label, "封面（第 1 页）出现页码"))
            elif body_start and phys < body_start:
                flagged.add((phys, label, f"正文起始页（物理第 {body_start} 页）之前出现页码"))
        for phys, label, why in sorted(flagged):
            yield make_issue("PAGE-05", doc, f"第 {phys} 页（页脚标注第 {label} 页）",
                             f"{why}：封面/扉页/目录不应编页码",
                             suggestion="调整分节，封面/目录节取消页脚页码")
        return
    if doc.fmt in ("docx", "doc"):
        by_sect = doc.meta.get("footer_page_field_by_section")
        if by_sect is None:
            _note(doc, "PAGE-05 跳过：缺少分节页脚域信息")
            return
        if len(by_sect) > 1 and by_sect[0]:
            yield make_issue("PAGE-05", doc, "第 1 节（封面）页脚",
                             "封面所在节页脚含 PAGE 域，封面/扉页不应编页码",
                             suggestion="封面节设置「首页不同」或取消链接前一节并删除页码")
        elif not any(by_sect):
            _note(doc, "PAGE-05 跳过：docx 各节页脚均无 PAGE 域且无配对 PDF")
    else:
        _note(doc, "PAGE-05 跳过：无页脚页码序列")
