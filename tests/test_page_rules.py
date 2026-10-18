"""页码规则回归测试：起始页、X/X 页脚、目录首项和目录顺序。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz

from doccheck.docloader import load
from doccheck.models import Paragraph, UnifiedDoc
from doccheck.registry import CheckContext
from doccheck.rules.page import (
    check_body_start_page,
    check_page_sequence,
    check_toc_pages,
    check_total_pages,
)


def pdf_doc(labels, fractions=None, toc=None, page_count=None, paragraphs=None):
    fractions = fractions or []
    return UnifiedDoc(
        path="/tmp/page-rules.pdf", name="page-rules.pdf", doc_type="技术设计方案",
        fmt="pdf", page_count=page_count or len(labels),
        footer_page_numbers=[(i + 1, n) for i, n in enumerate(labels)],
        footer_page_fractions=fractions,
        footer_page_totals=[(p, total) for p, _, total in fractions],
        toc_entries=toc or [], paragraphs=paragraphs or [],
    )


def issues(fn, doc):
    return list(fn(doc, CheckContext({}, None, [doc])) or [])


good = pdf_doc(
    [1, 2, 3],
    fractions=[(1, 1, 3), (2, 2, 3), (3, 3, 3)],
    toc=[("第一章 总则", 1), ("第二章 实施", 2)],
    paragraphs=[Paragraph(index=0, text="第一章 总则", page=1),
                Paragraph(index=1, text="第二章 实施", page=2)],
)
assert not issues(check_page_sequence, good)
assert not issues(check_total_pages, good)
assert not issues(check_body_start_page, good)
assert not issues(check_toc_pages, good)

starts_at_two = pdf_doc([2, 3, 4], fractions=[(1, 2, 4), (2, 3, 4), (3, 4, 4)])
assert any("未从第 1 页开始" in i.original for i in issues(check_page_sequence, starts_at_two))
assert any("未从第 1 页起编" in i.original for i in issues(check_body_start_page, starts_at_two))

restarts = pdf_doc([1, 2, 1, 2], fractions=[(1, 1, 4), (2, 2, 4), (3, 1, 4), (4, 2, 4)])
assert any("重新从第 1 页开始" in i.original for i in issues(check_page_sequence, restarts))

wrong_total = pdf_doc([1, 2, 3], fractions=[(1, 1, 4), (2, 2, 4), (3, 3, 4)])
total_issues = issues(check_total_pages, wrong_total)
assert any("最大当前页为 3" in i.original for i in total_issues)

bad_toc = pdf_doc(
    [1, 2, 3], fractions=[(1, 1, 3), (2, 2, 3), (3, 3, 3)],
    toc=[("第一章 总则", 2), ("第二章 实施", 1)],
    paragraphs=[Paragraph(index=0, text="第一章 总则", page=2),
                Paragraph(index=1, text="第二章 实施", page=1)],
)
toc_issues = issues(check_toc_pages, bad_toc)
assert any("未从第 1 页开始" in i.original for i in toc_issues)
assert any("顺序倒退" in i.original for i in toc_issues)


tmp = "/tmp/doccheck_page_fraction_test.pdf"
pdf = fitz.open()
for n in range(1, 4):
    page = pdf.new_page(width=595, height=842)
    page.insert_text((72, 100), f"第 {n} 章 测试")
    page.insert_text((250, 820), f"{n}/3页")
pdf.save(tmp)
pdf.close()
parsed = load(tmp)
assert parsed.footer_page_fractions == [(1, 1, 3), (2, 2, 3), (3, 3, 3)]
assert parsed.footer_page_numbers == [(1, 1), (2, 2), (3, 3)]
print("Page rule tests passed")
