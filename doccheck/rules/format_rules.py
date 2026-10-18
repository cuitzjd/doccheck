"""格式检查 FORMAT-01~04：字体/标题/段落格式一致性、编号连续性。

基准：config.format_baseline 非空时用配置值，否则取全文众数自动学习。
PDF 无字体元数据（Paragraph 未解析字体），版式类规则自动跳过。
"""
import re
from collections import Counter

from ..models import Severity
from ..registry import rule, make_issue
from .common import body_paragraphs, location_of, cn_to_int


def _baseline(doc, ctx, key, values):
    """配置基准优先，否则众数。"""
    cfg = (ctx.config.get("format_baseline") or {}).get(key)
    if cfg not in (None, ""):
        return cfg
    cnt = Counter(v for v in values if v not in (None, "", 0.0))
    return cnt.most_common(1)[0][0] if cnt else None


def _fmt_paras(doc):
    return [p for p in body_paragraphs(doc) if p.font_cn or p.font_size]


@rule("FORMAT-01", "正文字体一致性", "格式", Severity.MEDIUM, fixable=True)
def check_body_font(doc, ctx):
    paras = _fmt_paras(doc)
    if len(paras) < 10:
        doc.meta.setdefault("format_notes", []).append("FORMAT-01 跳过：无字体信息（PDF）或段落过少")
        return
    base_cn = _baseline(doc, ctx, "body_font_cn", [p.font_cn for p in paras])
    base_en = _baseline(doc, ctx, "body_font_en", [p.font_en for p in paras])
    base_size = _baseline(doc, ctx, "body_font_size",
                          [p.font_size for p in paras])
    if isinstance(base_size, str) and base_size:
        try:
            base_size = float(base_size)
        except ValueError:
            base_size = None
    for p in paras:
        if 0 < p.page <= 2 and doc.fmt in ("docx", "doc"):
            continue  # 封面/扉页（修改记录）不适用正文基准
        if len(p.text) < 10:
            continue  # 短行多为未套样式的标题/条目，由 FORMAT-02 覆盖
        diffs = []
        if base_cn and p.font_cn and p.font_cn != base_cn:
            diffs.append(f"中文字体 {p.font_cn} ≠ 主流 {base_cn}")
        # 纯中文段落的西文字体无意义，仅段落含西文时才比对
        if base_en and p.font_en and p.font_en != base_en and \
                re.search(r"[A-Za-z0-9]", p.text):
            diffs.append(f"西文字体 {p.font_en} ≠ 主流 {base_en}")
        if base_size and p.font_size and abs(p.font_size - float(base_size)) > 0.5:
            diffs.append(f"字号 {p.font_size} 磅 ≠ 主流 {base_size} 磅")
        if diffs:
            yield make_issue("FORMAT-01", doc, location_of(p),
                             p.text[:60], expected="；".join(diffs),
                             suggestion="统一为正文基准字体/字号")


_HEADING_NUM = re.compile(r"^(\d+(?:\.\d+)+)")
_CHAPTER = re.compile(r"^第\s*([一二三四五六七八九十百千万两\d]+)\s*章")


def _heading_groups(doc):
    """同级标题分组：优先样式名，否则编号形态（第X章 / X.Y / X.Y.Z）。"""
    groups = {}
    for p in doc.paragraphs:
        if not p.is_heading:
            continue
        if p.style:
            key = p.style
        elif _CHAPTER.match(p.text):
            key = "第X章"
        else:
            m = _HEADING_NUM.match(p.text)
            key = f"{m.group(1).count('.') + 1}级编号" if m else "其他标题"
        groups.setdefault(key, []).append(p)
    return groups


@rule("FORMAT-02", "标题格式一致性", "格式", Severity.MEDIUM, fixable=True)
def check_heading_format(doc, ctx):
    for key, paras in _heading_groups(doc).items():
        fmt = [(p.font_cn, p.font_size, p.bold) for p in paras]
        known = [f for f in fmt if f[0] or f[1]]
        if len(known) < 3:
            continue
        mode = Counter(known).most_common(1)[0][0]
        for p, f in zip(paras, fmt):
            if (f[0] or f[1]) and f != mode:
                yield make_issue(
                    "FORMAT-02", doc, location_of(p),
                    f"[{key}] {p.text[:50]}",
                    expected=f"同级主流：字体 {mode[0]} / {mode[1]} 磅 / 加粗={mode[2]}；"
                             f"本段：{f[0]} / {f[1]} 磅 / 加粗={f[2]}",
                    suggestion="套用同级标题样式")


@rule("FORMAT-03", "段落格式一致性", "格式", Severity.LOW, fixable=True)
def check_paragraph_format(doc, ctx):
    paras = [p for p in body_paragraphs(doc)
             if p.line_spacing is not None or p.first_line_indent is not None]
    if len(paras) < 10:
        doc.meta.setdefault("format_notes", []).append("FORMAT-03 跳过：无段落格式信息（PDF）或段落过少")
        return
    ls_mode = Counter(p.line_spacing for p in paras if p.line_spacing).most_common(1)
    ind_mode = Counter(round(p.first_line_indent or 0, 1) for p in paras).most_common(1)
    ls_mode = ls_mode[0][0] if ls_mode else None
    ind_mode = ind_mode[0][0] if ind_mode else None
    for p in paras:
        diffs = []
        if ls_mode and p.line_spacing and abs(p.line_spacing - ls_mode) > 0.05:
            diffs.append(f"行距 {p.line_spacing} ≠ 主流 {ls_mode}")
        if ind_mode is not None:
            ind = round(p.first_line_indent or 0, 1)
            if abs(ind - ind_mode) > 5:
                diffs.append(f"首行缩进 {ind} 磅 ≠ 主流 {ind_mode} 磅")
        if diffs:
            yield make_issue("FORMAT-03", doc, location_of(p),
                             p.text[:60], expected="；".join(diffs),
                             suggestion="统一正文行距/首行缩进")


_FIG_NUM = re.compile(r"^(图|表)\s*(\d+)[-—.]?\d*")


@rule("FORMAT-04", "编号连续性", "格式", Severity.MEDIUM)
def check_numbering(doc, ctx):
    # 章号序列
    chapters = []
    for p in doc.paragraphs:
        m = _CHAPTER.match(p.text)
        if m and (p.is_heading or len(p.text) < 30):
            n = cn_to_int(m.group(1))
            if n is not None:
                chapters.append((n, p))
    seen = set()
    for i, (n, p) in enumerate(chapters):
        if n in seen:
            yield make_issue("FORMAT-04", doc, location_of(p),
                             f"章号重复：第 {n} 章重复出现",
                             suggestion="核对章节编号")
        elif i > 0 and n > chapters[i - 1][0] + 1 and n - 1 not in seen:
            yield make_issue("FORMAT-04", doc, location_of(p),
                             f"章号跳号：第 {chapters[i - 1][0]} 章之后为第 {n} 章",
                             suggestion="核对是否缺章")
        seen.add(n)

    # 多级编号 X.Y / X.Y.Z：同级序列跳号/重复（仅标题段）
    levels = {}
    for p in doc.paragraphs:
        if not p.is_heading:
            continue
        m = _HEADING_NUM.match(p.text)
        if not m:
            continue
        parts = m.group(1).split(".")
        parent = ".".join(parts[:-1])
        levels.setdefault(parent, []).append((int(parts[-1]), p))
    for parent, items in levels.items():
        if len(items) < 2:
            continue
        seen = set()
        for i, (n, p) in enumerate(items):
            if n in seen:
                yield make_issue("FORMAT-04", doc, location_of(p),
                                 f"编号重复：{parent}.{n} 重复出现",
                                 suggestion="核对章节编号")
            elif i > 0 and n > items[i - 1][0] + 1 and not any(
                    k == n - 1 for k, _ in items):
                yield make_issue("FORMAT-04", doc, location_of(p),
                                 f"编号跳号：{parent}.{items[i - 1][0]} 之后为 {parent}.{n}",
                                 suggestion="核对是否缺节")
            seen.add(n)

    # 图表编号（图 N / 表 N 简单连续序列）
    for kind in ("图", "表"):
        nums = []
        for p in body_paragraphs(doc):
            m = _FIG_NUM.match(p.text)
            if m and m.group(1) == kind and len(p.text) < 60:
                nums.append((int(m.group(2)), p))
        # 只检查全局简单递增的文档；含分章编号（图3-1）时跳过
        if any(_FIG_NUM.match(p.text).group(0).count("-") for _, p in nums):
            continue
        seq = sorted(set(n for n, _ in nums))
        if len(seq) < 3:
            continue
        for a, b in zip(seq, seq[1:]):
            if b > a + 1:
                p = next(p for n, p in nums if n == b)
                yield make_issue("FORMAT-04", doc, location_of(p),
                                 f"{kind}编号跳号：{kind} {a} 之后为 {kind} {b}",
                                 suggestion="核对图表编号")
                break
        dup = [n for n in seq if sum(1 for x, _ in nums if x == n) > 1]
        if dup:
            p = next(p for n, p in nums if n == dup[0])
            yield make_issue("FORMAT-04", doc, location_of(p),
                             f"{kind}编号重复：{kind} {dup[0]} 出现多次",
                             suggestion="核对图表编号")
