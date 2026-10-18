"""Shared DOCX paragraph enumeration for loading and repair.

The loader and repair path must use the same non-empty paragraph indexes,
including paragraphs inside tables and floating text boxes.
"""
from dataclasses import dataclass


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True)
class DocxParagraph:
    paragraph: object
    in_table: bool = False
    page_breaks: int = 0


def _page_breaks(element) -> int:
    breaks = len(element.xpath(".//w:br[@w:type='page']"))
    return breaks + bool(element.xpath(".//w:lastRenderedPageBreak"))


def _paragraph(document, element, in_table=False, page_breaks=0):
    import docx.text.paragraph

    paragraph = docx.text.paragraph.Paragraph(element, document)
    if not paragraph.text.strip():
        return None
    return DocxParagraph(paragraph, in_table=in_table, page_breaks=page_breaks)


def iter_docx_paragraphs(document):
    """Yield non-empty body, table-cell, and floating-text-box paragraphs."""
    import docx.table

    body = document.element.body
    seen = set()
    for child in body.iterchildren():
        if child.tag == _W + "p":
            item = _paragraph(document, child, page_breaks=_page_breaks(child))
            if item is not None:
                seen.add(id(item.paragraph._p))
                yield item
        elif child.tag == _W + "tbl":
            table = docx.table.Table(child, document)
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        item = _paragraph(document, paragraph._p, in_table=True)
                        if item is not None:
                            seen.add(id(item.paragraph._p))
                            yield item

    for element in body.xpath(".//w:txbxContent/w:p"):
        if id(element) in seen:
            continue
        item = _paragraph(document, element)
        if item is not None:
            seen.add(id(item.paragraph._p))
            yield item
