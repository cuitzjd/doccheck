"""问题上下文定位：PDF 页渲染高亮截图 / docx 文字上下文。

供 webapp 页面2"上下文"浮层使用。截图缓存于 workdir/context/<issue_id>.png；
draw_rect 只改内存中的 PDF 文档，绝不保存回原文件。
"""
import os
import re
import threading

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

from . import docloader

# 由 webapp 在启动时设置；缺省项目根 workdir/context
CONTEXT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "workdir", "context")

_DOC_CACHE = {}
_DOC_LOCK = threading.Lock()

_BRACKETS = re.compile(r"【(.+?)】")
_PAGE_RE = re.compile(r"第\s*(\d+)\s*页")
_PARA_RE = re.compile(r"段落\s*(\d+)")


def _load_doc(path):
    with _DOC_LOCK:
        if path not in _DOC_CACHE:
            _DOC_CACHE[path] = docloader.load(path)
        return _DOC_CACHE[path]


def clear_cache():
    """重新检查前调用：清空截图与文档解析缓存。"""
    with _DOC_LOCK:
        _DOC_CACHE.clear()
    if os.path.isdir(CONTEXT_DIR):
        for name in os.listdir(CONTEXT_DIR):
            if name.endswith(".png"):
                try:
                    os.remove(os.path.join(CONTEXT_DIR, name))
                except OSError:
                    pass


def _to_fullwidth(s):
    out = []
    for ch in s:
        o = ord(ch)
        if ch == " ":
            out.append("　")
        elif 0x21 <= o <= 0x7E:
            out.append(chr(o + 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _to_halfwidth(s):
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "　":
            out.append(" ")
        elif 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _search_variants(original):
    """从 issue.original 生成搜索片段候选：优先【】内目标，再做归一化变体。"""
    m = _BRACKETS.search(original or "")
    target = m.group(1) if m else ""
    plain = _BRACKETS.sub(lambda x: x.group(1), original or "")
    cands = []
    for base in (target, plain):
        if not base:
            continue
        cands.append(base)
        cands.append(re.sub(r"\s+", "", base))
        cands.append(_to_fullwidth(base))
        cands.append(_to_halfwidth(base))
        cands.append(re.sub(r"\s+", "", _to_fullwidth(base)))
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _find_on_page(page, snippets):
    for s in snippets:
        try:
            rects = page.search_for(s)
        except Exception:
            rects = []
        if rects:
            return rects, s
    return [], ""


def _pdf_context(issue, pdf_path):
    if fitz is None:
        return {"type": "error", "msg": "PyMuPDF 未安装，无法渲染 PDF 页"}
    iid = re.sub(r"[^\w\-]", "_", str(issue.get("id", "unknown")))
    os.makedirs(CONTEXT_DIR, exist_ok=True)
    cache_path = os.path.join(CONTEXT_DIR, iid + ".png")
    url = f"/api/context_img/{iid}"
    if os.path.isfile(cache_path):
        return {"type": "image", "url": url, "cached": True}

    snippets = _search_variants(issue.get("original", ""))
    m = _PAGE_RE.search(issue.get("location", ""))
    doc = fitz.open(pdf_path)
    try:
        page, rects = None, []
        if m:
            pno = int(m.group(1)) - 1  # PDF 物理页（1 起）
            if 0 <= pno < len(doc):
                page = doc[pno]
                rects, _ = _find_on_page(page, snippets)
        if page is None:
            # 无页码：全文搜索定位首个命中页
            for pno in range(len(doc)):
                r, _ = _find_on_page(doc[pno], snippets)
                if r:
                    page, rects = doc[pno], r
                    break
        if page is None:
            return {"type": "error", "msg": "PDF 页码超出范围"}

        found = bool(rects)
        if found:
            for r in rects:
                page.draw_rect(r, color=(1, 0, 0), fill=(1, 1, 0),
                               fill_opacity=0.3, width=1, overlay=True)
            clip = rects[0]
            for r in rects[1:]:
                clip = clip | r
            ph = page.rect.height
            clip = fitz.Rect(0, max(0, clip.y0 - ph * 0.35),
                             page.rect.width, min(ph, clip.y1 + ph * 0.35))
        else:
            page.insert_text((36, 50), "未定位到原句，显示整页",
                             fontname="china-s", fontsize=14, color=(1, 0, 0))
            clip = page.rect
        pix = page.get_pixmap(dpi=150, clip=clip)
        pix.save(cache_path)  # 只写缓存 PNG，内存文档不保存
        return {"type": "image", "url": url, "page": page.number + 1, "found": found}
    finally:
        doc.close()


def _text_context(issue, doc_path):
    if not os.path.isfile(doc_path):
        return {"type": "error", "msg": f"文档不存在：{doc_path}"}
    try:
        doc = _load_doc(doc_path)
    except Exception as e:
        return {"type": "error", "msg": f"文档解析失败：{e}"}
    m = _PARA_RE.search(issue.get("location", ""))
    paras = doc.paragraphs
    if m:
        idx = int(m.group(1))
        pos = next((i for i, p in enumerate(paras) if p.index == idx), None)
    else:
        pos = None
    if pos is None:
        return {"type": "error", "msg": "无法从定位信息解析段落号"}
    lo, hi = max(0, pos - 3), min(len(paras), pos + 4)
    out = [{"text": paras[i].text, "hit": i == pos} for i in range(lo, hi)]
    return {"type": "text", "paragraphs": out,
            "note": "docx 无渲染页，仅提供文字上下文"}


def get_context(issue, files_list):
    """issue: Issue.to_dict()；files_list: 会话文件列表（含 name/path）。"""
    doc_name = issue.get("doc_name", "")
    entry = next((f for f in files_list if f.get("name") == doc_name), None)
    if not entry:
        # .doc 会被转换为临时 .docx 解析，问题里的文档名可能与会话文件名只差扩展名
        stem = os.path.splitext(doc_name)[0]
        entry = next((f for f in files_list
                      if os.path.splitext(f.get("name", ""))[0] == stem), None)
    if not entry:
        return {"type": "error", "msg": f"会话中找不到文档：{doc_name}"}
    path = entry["path"]
    paired = ""
    if not path.lower().endswith(".pdf"):
        stem = os.path.splitext(path)[0]
        for f in files_list:
            fp = f.get("path", "")
            if fp.lower().endswith(".pdf") and os.path.splitext(fp)[0] == stem:
                paired = fp
                break
    pdf_path = path if path.lower().endswith(".pdf") else paired
    if pdf_path and os.path.isfile(pdf_path):
        return _pdf_context(issue, pdf_path)
    return _text_context(issue, path)
