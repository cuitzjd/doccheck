"""规则共用工具：句子切分、目录行判定、中文数字、编辑距离等。"""
import re

TOC_LINE = re.compile(r"^(.*?)[.·…︙‥]{2,}\s*(\d{1,4})\s*$")

# 机构名候选（CONTRACT-01/05 用）：贪婪匹配，避免在"研究院科技集团"中间截断
ORG_NAME = re.compile(
    r"[一-龥（）()]{3,40}(?:股份有限公司|有限责任公司|有限公司|"
    r"研究院|设计院|测绘院|规划院|事务所|集团|中心|局)")

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
             "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5,
             "陆": 6, "柒": 7, "捌": 8, "玖": 9}
_CN_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}


def cn_to_int(s: str):
    """中文数字（含十/百/千/万/亿及大写壹贰叁）转 int，失败返回 None。"""
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "亿" in s:
        hi, _, lo = s.partition("亿")
        h = cn_to_int(hi) if hi else 1
        l = cn_to_int(lo) if lo else 0
        return None if h is None or l is None else h * 100000000 + l
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            cur = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            total += (cur or 1) * _CN_UNIT[ch]
            cur = 0
        elif ch == "万":
            total = (total + cur) * 10000
            cur = 0
        else:
            return None
    return total + cur


def edit_distance(a: str, b: str, limit: int = 3) -> int:
    """Levenshtein 距离（带上限提前退出，超出返回 limit+1）。"""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(v)
            row_min = min(row_min, v)
        if row_min > limit:
            return limit + 1
        prev = cur
    return prev[-1]


_SENT_SPLIT = re.compile(r"(?<=[。！？；])|(?<=[。！？；”’』》])")


def split_sentences(text: str) -> list:
    """按中文句末标点切句，保留标点；返回非空句列表。"""
    return [s for s in (x.strip() for x in _SENT_SPLIT.split(text)) if s]


def is_toc_line(text: str) -> bool:
    return bool(TOC_LINE.match(text.strip()))


def repeated_lines(doc, min_pages: int = 4) -> set:
    """PDF 的页眉/页脚行：同一文本出现在 ≥min_pages 个不同页上（缓存于 doc.meta）。"""
    if "_repeated_lines" in doc.meta:
        return doc.meta["_repeated_lines"]
    pages = {}
    for p in doc.paragraphs:
        pages.setdefault(p.text, set()).add(p.page)
    rep = {t for t, ps in pages.items() if len(ps) >= min_pages}
    doc.meta["_repeated_lines"] = rep
    return rep


def location_of(p) -> str:
    if p.page and p.page > 0:
        return f"第 {p.page} 页 · 段落 {p.index}"
    return f"段落 {p.index}"


def body_paragraphs(doc):
    """正文段落：排除标题、表格单元格、目录引导行。"""
    return [p for p in doc.paragraphs
            if not p.is_heading and not p.is_table_cell and not is_toc_line(p.text)]
