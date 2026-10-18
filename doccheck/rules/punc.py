"""标点与文字检查 PUNC-01~05。

修复约定（repair.py 依赖）：location 含"段落 N"，original 为待改片段，
expected 为替换文本；PUNC-03 的 expected 为空表示段末补句号。
PUNC-03 仅对 docx：PDF 按行切分，换行不等于段落结束，会大量误报。
"""
import re

from ..models import Severity
from ..registry import rule, make_issue
from .common import is_toc_line, location_of, repeated_lines

_REPEAT_PUNCT = re.compile(r"([，。、；：？！,;:?!~])\1+")
_LEAD_PUNCT = tuple("，。、；：？！,.;!?")
_ADJACENT = re.compile(r"[，、；：,.;](?=[，。、；：,.;!?？！])|。(?=[，、；：,.;:，])")
_HALF_WIDTH = re.compile(r"[,.;()()]")
_REPEATED_CHAR = re.compile(r"([的了是在我他你她说])\1")
_REPEATED_WORD = re.compile(r"([一-龥]{2,4})\1")
_LEGIT_REPEAT_1 = {"刚刚", "常常", "往往", "渐渐", "谢谢", "看看", "试试"}
_FIG_CAPTION = re.compile(r"^(图|表)\s*\d")
_LIST_MARKER = re.compile(
    r"^\s*(?:(?:\d{1,3}|[一二三四五六七八九十百千万两]+)[、.．。)）]|"
    r"[（(]\s*\d{1,3}\s*[）)]|[①②③④⑤⑥⑦⑧⑨⑩]|[A-Za-z][.)])\s*")
_TABLE_CAPTION = re.compile(r"^(?:图|表)\s*(?:\d+|[一二三四五六七八九十]+)|(?:表|方案|计划|安排|职责|架构|流程|目标|说明|概况)$")
_LABEL_SUFFIX = re.compile(
    r"(?:表|方案|计划|安排|职责|架构|流程|目标|说明|概况|领导|负责人|成员|频率|内容|"
    r"阶段|思路|分配|分解|要求|需求|方法|指标|标准|工具|能力|范围|组织|结构|任务|措施|"
    r"策略|风险|清单|情况|成果|组成|构成|定义|概念|背景|依据|条件|集成|组|制度)$")
_BODY_LEAD = re.compile(r"^(?:本|该|各|应|需|须|通过|根据|提供|实现|完成|针对|对于|系统|项目|本项目|其中|同时|如果|当)")
_FIELD_CODE = re.compile(r"(?:PAGE|NUMPAGES|SECTIONPAGES|MERGEFORMAT|STYLEREF)", re.I)
_LABEL_FIELDS = re.compile(r"^(?:出现概率|影响程度|优先级|责任人|负责人|完成情况|状态|类型)[：:]")
_CODE_MARKER = re.compile(
    r"(?:\b(?:return|public|private|protected|static|class|interface|new|void|boolean|String)\b|"
    r"[A-Za-z_$][\w$]*\s*[.=({;]|[{}();]|=>|//|/\*|\*/|"
    r"\"[^\"\n]{2,}\"\s*[,;)]|^\s*\*\s*@)")


def _dot_ellipsis(text, start, end):
    """ASCII 连续点号属于省略号、路径或数据占位，不是重复中文标点。"""
    if "." not in text[max(0, start - 2):min(len(text), end + 2)]:
        return False
    left = start
    right = end
    while left > 0 and text[left - 1] == ".":
        left -= 1
    while right < len(text) and text[right] == ".":
        right += 1
    return right - left >= 2


def _compact_table_row(doc, paragraph):
    """Word 转换丢失表格标记时，用字号和相邻行恢复表格语境。"""
    if paragraph.is_table_cell:
        return True
    text = paragraph.text.strip()
    if not text or len(text) > 80:
        return False
    paragraphs = doc.paragraphs
    pos = next((i for i, item in enumerate(paragraphs) if item is paragraph), None)
    if pos is None:
        return False
    nearby = paragraphs[max(0, pos - 3):pos + 4]
    compact = [p for p in nearby if p.text.strip() and len(p.text.strip()) <= 42]
    same_size = [p for p in compact if paragraph.font_size and
                 abs((p.font_size or 0) - paragraph.font_size) < 0.1]
    no_terminator = [p for p in compact if not re.search(r"[。！？；：]$", p.text.strip())]
    return (paragraph.font_size <= 11 and len(same_size) >= 3 and
            len(no_terminator) >= 3)


def _numbered_label(text):
    match = _LIST_MARKER.match(text)
    return match and text[match.end():].strip()


def _is_code_paragraph(doc, paragraph):
    """源代码说明中的代码行不是中文自然语言句子，不参与段末句号检查。"""
    text = paragraph.text.strip()
    if not text:
        return False
    doc_type = getattr(doc, "doc_type", "") or ""
    ascii_ratio = sum(ord(ch) < 128 for ch in text) / max(len(text), 1)
    code_doc = doc_type in {"源代码及说明", "代码编写规范"} or "代码" in doc_type
    marker = bool(_CODE_MARKER.search(text))
    comment_line = text.startswith(("*", "/")) and "设计说明" in doc_type
    return ((code_doc and ascii_ratio >= 0.35 or ascii_ratio >= 0.55) and marker) or \
        comment_line


def _looks_like_structured_line(doc, paragraph):
    """判断无句号是否由 Word 结构或排版语义造成，而非正文漏标点。"""
    text = paragraph.text.strip()
    if paragraph.is_heading or paragraph.outline_level >= 0 or paragraph.bold:
        return True
    if paragraph.keep_with_next and len(text) <= 60:
        return True
    label = _numbered_label(text) if paragraph.is_list_item or _LIST_MARKER.match(text) else None
    if label is not None:
        # 编号后的短语通常是条目标题；有完整分句的长条目仍交给正文规则。
        return (len(label) <= 60 and
                not re.search(r"[，；：,;:。！？]", label))
    if len(text) <= 48 and _LABEL_FIELDS.match(text):
        return True
    if (len(text) <= 36 and not re.search(r"[，、；：,;:。！？]", text)
            and _LABEL_SUFFIX.search(text)
            and not _BODY_LEAD.match(text)):
        return True
    return False


def _label_or_table_row(doc, paragraph):
    text = paragraph.text.strip()
    if _looks_like_structured_line(doc, paragraph) or _TABLE_CAPTION.search(text):
        return True
    if _compact_table_row(doc, paragraph) and not re.search(r"[。！？；：]$", text):
        return True
    if len(text) <= 40 and re.search(r"[/／|｜]", text) and not re.search(r"[。！？；：]$", text):
        return True
    if len(text) <= 24 and _LABEL_SUFFIX.search(text) and not re.search(r"[。！？；：]$", text):
        return True
    return False


def _loc(p):
    return location_of(p)


@rule("PUNC-01", "重复标点", "标点", Severity.MEDIUM, fixable=True)
def check_repeated_punct(doc, ctx):
    for p in doc.paragraphs:
        if is_toc_line(p.text) or _is_code_paragraph(doc, p):
            continue  # 目录引导点除外
        for m in _REPEAT_PUNCT.finditer(p.text):
            bad = m.group(0)
            yield make_issue("PUNC-01", doc, _loc(p),
                             f"{p.text[max(0, m.start() - 10):m.start()]}【{bad}】{p.text[m.end():m.end() + 10]}",
                             expected=bad[0],  # 修复：重复标点收敛为单个
                             suggestion=f"重复标点「{bad}」应为「{bad[0]}」")


@rule("PUNC-02", "多余标点", "标点", Severity.MEDIUM, fixable=True)
def check_redundant_punct(doc, ctx):
    for p in doc.paragraphs:
        t = p.text
        if _is_code_paragraph(doc, p) or _FIELD_CODE.search(t):
            continue
        if t.startswith(_LEAD_PUNCT):
            yield make_issue("PUNC-02", doc, _loc(p),
                             t[:40], expected="",
                             suggestion=f"句首多余标点「{t[0]}」")
        for m in _ADJACENT.finditer(t):
            pair = t[m.start():m.end() + 1]
            if "." in pair and _dot_ellipsis(t, m.start(), m.end() + 1):
                continue
            yield make_issue("PUNC-02", doc, _loc(p),
                             f"{t[max(0, m.start() - 10):m.start()]}【{pair}】{t[m.end() + 1:m.end() + 11]}",
                             expected=pair[-1],
                             suggestion=f"紧邻多余标点「{pair}」，疑似应保留「{pair[-1]}」")


@rule("PUNC-03", "段末句号缺失", "标点", Severity.MEDIUM, fixable=True)
def check_missing_period(doc, ctx):
    if doc.fmt == "pdf":
        doc.meta.setdefault("punc_notes", []).append(
            "PUNC-03 跳过：PDF 按行切分无法判定自然段结束，以 Word 源文件为准")
        return
    terms = (ctx.config.get("punctuation") or {}).get(
        "paragraph_terminators", "。；：？！”）】…")
    for p in doc.paragraphs:
        t = p.text
        if _is_code_paragraph(doc, p) or _FIELD_CODE.search(t):
            continue
        if (p.is_heading or p.is_table_cell or is_toc_line(t)
                or len(t) < 8 or _FIG_CAPTION.match(t) or _label_or_table_row(doc, p)):
            continue
        if 0 < p.page <= 2:
            continue  # 封面/扉页
        if t.endswith(("》", ";", "；")):
            continue  # 以书名号结尾（文献/文件名清单行）
        # 无功能词的短行多为未套标题样式的条目名（如"物联感知子系统升级"）
        if len(t) <= 12 and not re.search(r"[的了和与及或，、]", t):
            continue
        last = t[-1]
        if last in terms or last.isdigit() or last.isascii() and last.isalpha():
            continue
        yield make_issue("PUNC-03", doc, _loc(p),
                         f"……{t[-30:]}",
                         expected="",  # 修复：段末补「。」
                         suggestion=f"段末疑似缺终止符（现为「{last}」），逐条确认后补「。」")


_CJK = r"一-鿿"


def _halfwidth_fixes(text: str):
    """返回 (pos, char, replacement, context) 列表；仅中文语境。"""
    out = []
    n = len(text)
    for m in _HALF_WIDTH.finditer(text):
        i = m.start()
        ch = m.group(0)
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < n else ""
        prev_cjk = prev and re.match(f"[{_CJK}]", prev)
        nxt_cjk = nxt and re.match(f"[{_CJK}]", nxt)
        rep = None
        if ch == "," and (prev_cjk or nxt_cjk):
            rep = "，"
        elif ch == ";" and (prev_cjk or nxt_cjk):
            rep = "；"
        elif ch == "(" and (prev_cjk or nxt_cjk):
            rep = "（"
        elif ch == ")" and (prev_cjk or nxt_cjk):
            rep = "）"
        elif ch == ".":
            # 排除小数/版本号/网址/英文缩写
            if prev_cjk and not (nxt.isdigit() or nxt.isascii() and nxt.isalpha()):
                rep = "。"
        if rep:
            ctx_txt = text[max(0, i - 8):i] + f"【{ch}】" + text[i + 1:i + 9]
            out.append((i, ch, rep, ctx_txt))
    return out


@rule("PUNC-04", "中英文标点混用", "标点", Severity.LOW, fixable=True)
def check_halfwidth_punct(doc, ctx):
    for p in doc.paragraphs:
        t = p.text
        # 代码/命令行段落跳过（ASCII 占比过半）
        ascii_ratio = sum(1 for c in t if ord(c) < 128) / max(len(t), 1)
        if ascii_ratio > 0.6:
            continue
        for i, ch, rep, ctx_txt in _halfwidth_fixes(t):
            yield make_issue("PUNC-04", doc, _loc(p), ctx_txt,
                             expected=rep,
                             suggestion=f"中文语境半角标点「{ch}」应为「{rep}」")


@rule("PUNC-05", "重复字词", "标点", Severity.LOW, fixable=True)
def check_repeated_words(doc, ctx):
    for p in doc.paragraphs:
        t = p.text
        if _is_code_paragraph(doc, p):
            continue
        for m in _REPEATED_CHAR.finditer(t):
            if m.group(0) in _LEGIT_REPEAT_1:
                continue
            yield make_issue("PUNC-05", doc, _loc(p),
                             f"{t[max(0, m.start() - 10):m.start()]}【{m.group(0)}】{t[m.end():m.end() + 10]}",
                             expected=m.group(1),
                             suggestion=f"疑似重复字「{m.group(0)}」")
        for m in _REPEATED_WORD.finditer(t):
            w = m.group(1)
            # 排除被更长重复包含的误检（如 aaaa 中 aa+aa）与常见合法叠用
            if t[m.start() - 1:m.start()] == w[-1] or t[m.end():m.end() + 1] == w[0]:
                continue
            yield make_issue("PUNC-05", doc, _loc(p),
                             f"{t[max(0, m.start() - 10):m.start()]}【{m.group(0)}】{t[m.end():m.end() + 10]}",
                             expected=w,
                             suggestion=f"疑似重复词「{m.group(0)}」")
