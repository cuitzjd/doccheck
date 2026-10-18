"""盖章 PDF 首页单位名称叠加（PDFSTAMP-01~04）。

约束：仅新增内容层，不修改/删除原有内容；输出新文件，页数与页面尺寸不变。
中文字体用 PyMuPDF 内置 CJK 字体 "china-s"。
"""
import os

import fitz

_FONT = "china-s"  # PyMuPDF 内置简体中文字体


def _positions(page, lines, position, font_size, x, y):
    """计算每行插入点（bottom-center 默认：页面底部居中、向上逐行）。"""
    rect = page.rect
    line_h = font_size * 1.6
    if x is not None and y is not None:
        base_x, base_y = x, y
    elif position == "bottom-center":
        base_x = rect.width / 2
        base_y = rect.height * 0.92
    elif position == "top-center":
        base_x = rect.width / 2
        base_y = rect.height * 0.15 + line_h * (len(lines) - 1)
    else:
        raise ValueError(f"未知锚点: {position}")
    pts = []
    for i, line in enumerate(lines):
        yy = base_y - line_h * (len(lines) - 1 - i)
        if x is None:  # 居中
            w = fitz.get_text_length(line, fontname=_FONT, fontsize=font_size)
            xx = base_x - w / 2
        else:
            xx = base_x
        pts.append((fitz.Point(xx, yy), line))
    return pts


def already_stamped(pdf_path: str, lines: list) -> bool:
    """首页是否已存在相同文本（PDFSTAMP-04，避免重复叠加）。"""
    pdf = fitz.open(pdf_path)
    text = pdf[0].get_text().replace(" ", "") if pdf.page_count else ""
    pdf.close()
    return any(l.replace(" ", "") and l.replace(" ", "") in text for l in lines)


def _stamp_doc(pdf, lines, position, font_size, x, y):
    pg = pdf[0]
    for pt, line in _positions(pg, lines, position, font_size, x, y):
        pg.insert_text(pt, line, fontname=_FONT, fontsize=font_size,
                       color=(0, 0, 0))


def stamp_first_page(pdf_path: str, out_path: str, lines: list,
                     position: str = "bottom-center", font_size: int = 14,
                     x=None, y=None) -> tuple:
    """首页叠加多行文本，输出新文件（PDFSTAMP-01/03）。"""
    if os.path.abspath(pdf_path) == os.path.abspath(out_path):
        return False, "输出路径不得覆盖原盖章 PDF（PDFSTAMP-03 原文件保护）"
    if already_stamped(pdf_path, lines):
        return False, "首页已存在相同文本，跳过叠加（PDFSTAMP-04）"
    pdf = fitz.open(pdf_path)
    _stamp_doc(pdf, lines, position, font_size, x, y)
    pdf.save(out_path)  # 增量内容层：页数/尺寸/原有内容不变
    n = pdf.page_count
    pdf.close()
    return True, f"已叠加 {len(lines)} 行 → {out_path}（页数 {n} 不变）"


def preview(pdf_path: str, lines: list, position: str = "bottom-center",
            font_size: int = 14, x=None, y=None) -> bytes:
    """生成首页叠加预览 PNG（PDFSTAMP-02），不落盘。"""
    pdf = fitz.open(pdf_path)
    _stamp_doc(pdf, lines, position, font_size, x, y)
    png = pdf[0].get_pixmap(dpi=100).tobytes("png")
    pdf.close()
    return png
