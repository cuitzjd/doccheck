"""自动修复（REPORT-04/05）：仅作用于 docx 源文件。

修复前备份原件到 backups/（带时间戳），日志写 backups/fixlog_时间戳.json，
支持 rollback(backup_path_or_id) 回退。单项失败不中断其他项。
修复约定：PUNC-01/02/04/05 的 original 含一对【】标出待改片段，
expected 为替换文本（空串=删除）；PUNC-03 段末补句号；CONTRACT-01/02/04 全文替换。
"""
import glob
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field

from .config import load_config
from .models import IssueStatus

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class FixResult:
    doc_path: str
    backup_path: str = ""
    log_path: str = ""
    backup_id: str = ""
    backup_dir: str = ""
    succeeded: list = field(default_factory=list)  # [{id, before, after}]
    failed: list = field(default_factory=list)     # [{id, reason}]
    skipped: list = field(default_factory=list)    # [{id, reason}]
    fixed_path: str = ""                           # .doc 修复后的输出 docx（原件不动）

    @property
    def summary(self):
        return f"成功 {len(self.succeeded)} / 失败 {len(self.failed)} / 跳过 {len(self.skipped)}"


# ---------------- docx 段落定位（与 docloader 枚举顺序一致） ----------------

def _iter_paragraphs(d):
    """按文档顺序产出非空段落（正文 + 表格单元格），与 docloader 一致。"""
    import docx.table
    import docx.text.paragraph
    for child in d.element.body.iterchildren():
        if child.tag == _W + "p":
            p = docx.text.paragraph.Paragraph(child, d)
            if p.text.strip():
                yield p
        elif child.tag == _W + "tbl":
            tbl = docx.table.Table(child, d)
            for row in tbl.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        if p.text.strip():
                            yield p


def _paragraph_by_index(d, idx: int):
    for i, p in enumerate(_iter_paragraphs(d)):
        if i == idx:
            return p
    return None


def _para_index_from_location(location: str):
    m = re.search(r"段落\s*(\d+)", location or "")
    return int(m.group(1)) if m else None


# ---------------- 跨 run 文本替换 ----------------

def replace_in_paragraph(para, old: str, new: str, count: int = 1) -> int:
    """在段落内替换文本（跨 run 合并处理），返回替换次数。"""
    done = 0
    while done < count:
        full = "".join(r.text for r in para.runs)
        pos = full.find(old)
        if pos < 0:
            break
        end = pos + len(old)
        # 位置 → run 映射，重写受影响 run
        off = 0
        pieces = []
        for r in para.runs:
            rlen = len(r.text)
            rstart, rend = off, off + rlen
            off = rend
            if rend <= pos or rstart >= end:
                continue  # 不受影响
            before = r.text[:max(0, pos - rstart)]
            after = r.text[max(0, end - rstart):]
            pieces.append((r, before, after))
        if not pieces:  # 目标在空 run/域代码中，放弃
            break
        first = True
        for r, before, after in pieces:
            if first:
                r.text = before + new + after
                first = False
            else:
                r.text = after  # 区间内部分删除，保留区间外残留
        done += 1
    return done


def _replace_global(d, old: str, new: str) -> int:
    n = 0
    for p in _iter_paragraphs(d):
        while True:
            k = replace_in_paragraph(p, old, new)
            if not k:
                break
            n += k
    return n


# ---------------- 各类修复 ----------------

def _canon(s: str) -> str:
    """全/半角标点归一（1:1 字符映射，长度不变），用于跨修复的上下文定位。"""
    return s.translate(str.maketrans("（），。；：！？", "(),.;:!?"))


def _fix_bracket(d, issue):
    """修复 original 含【】标注的问题项：needle = 左文+目标+右文 定位后替换。"""
    m = re.search(r"【(.+?)】", issue.original or "")
    if not m:
        return False, "无法从 original 解析待改片段"
    target = m.group(1)
    left = (issue.original[:m.start()]).lstrip("…")
    right = issue.original[m.end():]
    needle = _canon(left + target + right)
    idx = _para_index_from_location(issue.location)
    paras = []
    if idx is not None:
        p = _paragraph_by_index(d, idx)
        if p is not None:
            paras.append(p)
    paras.extend(p for p in _iter_paragraphs(d) if not paras or p is not paras[0])
    for p in paras:
        full = p.text
        pos = _canon(full).find(needle)
        if pos < 0 and target and not left and not right:
            pos = full.find(target)  # 无上下文时按首次出现
            start = pos
        else:
            start = pos + len(left)
        if pos >= 0 and _canon(full)[start:start + len(target)] == _canon(target):
            _replace_range(p, start, start + len(target), issue.expected)
            return True, f"{target} → {issue.expected or '（删除）'}"
    return False, "未找到待改文本"


def _replace_range(para, start, end, new):
    """按字符区间替换段落文本（跨 run）。"""
    off = 0
    pieces = []
    for r in para.runs:
        rlen = len(r.text)
        rstart, rend = off, off + rlen
        off = rend
        if rend <= start or rstart >= end:
            continue
        pieces.append((r, r.text[:max(0, start - rstart)],
                       r.text[max(0, end - rstart):]))
    for i, (r, before, after) in enumerate(pieces):
        r.text = (before + new + after) if i == 0 else after


def _fix_append_period(d, issue):
    idx = _para_index_from_location(issue.location)
    if idx is None:
        return False, "无法定位段落"
    p = _paragraph_by_index(d, idx)
    if p is None:
        return False, f"段落 {idx} 不存在"
    if p.runs:
        p.runs[-1].text += "。"
    else:
        p.add_run("。")
    return True, "段末补「。」"


def _fix_global_replace(d, issue):
    if not issue.original or not issue.expected:
        return False, "缺少替换基准值"
    n = _replace_global(d, issue.original, issue.expected)
    if n:
        return True, f"全文替换 {n} 处：{issue.original} → {issue.expected}"
    return False, "全文未找到待替换文本"


def _fix_font(d, issue, cfg):
    idx = _para_index_from_location(issue.location)
    if idx is None:
        return False, "无法定位段落"
    p = _paragraph_by_index(d, idx)
    if p is None:
        return False, f"段落 {idx} 不存在"
    base = cfg.get("format_baseline") or {}
    # 优先配置基准；否则从 issue.expected 的"主流 X"描述解析
    font_cn = base.get("body_font_cn") or ""
    font_en = base.get("body_font_en") or ""
    size = base.get("body_font_size") or ""
    m = re.search(r"中文字体 \S+ ≠ 主流 (\S+)", issue.expected or "")
    font_cn = font_cn or (m.group(1) if m else "")
    m = re.search(r"西文字体 \S+ ≠ 主流 (\S+)", issue.expected or "")
    font_en = font_en or (m.group(1) if m else "")
    m = re.search(r"主流 ([\d.]+) 磅", issue.expected or "")
    size = size or (m.group(1) if m else "")
    if not (font_cn or font_en or size):
        return False, "无字体基准（config.format_baseline 为空且无法从问题项解析）"
    import docx.shared
    targets = list(p.runs)
    if p.style is not None:
        targets_style = p.style
    else:
        targets_style = None
    for r in targets:
        if font_cn or font_en:
            rpr = r.font.element.get_or_add_rPr()
            rf = rpr.get_or_add_rFonts()
            if font_cn:
                rf.set(_W + "eastAsia", font_cn)
                r.font.name = font_en or font_cn
            if font_en:
                rf.set(_W + "ascii", font_en)
                rf.set(_W + "hAnsi", font_en)
        if size:
            r.font.size = docx.shared.Pt(float(size))
    if targets_style is not None:
        try:
            if font_cn:
                targets_style.element.get_or_add_rPr().get_or_add_rFonts().set(
                    _W + "eastAsia", font_cn)
            if size:
                targets_style.font.size = docx.shared.Pt(float(size))
        except Exception:
            pass
    return True, f"字体统一为 {font_cn or '-'} / {font_en or '-'} / {size or '-'} 磅"


_DISPATCH = {
    "CONTRACT-01": _fix_global_replace,
    "CONTRACT-02": _fix_global_replace,
    "CONTRACT-04": _fix_global_replace,
    "PUNC-01": _fix_bracket,
    "PUNC-02": _fix_bracket,
    "PUNC-04": _fix_bracket,
    "PUNC-05": _fix_bracket,
}


# ---------------- 入口 ----------------

def apply_fixes(doc_path: str, issues: list, config_path: str = None) -> FixResult:
    """对 docx 执行修复。issues 为本文档的待修复问题项（调用方先做用户确认过滤）。

    .doc 老格式：原件不动，先转 docx 再修复，输出到 同目录 `<原名>_修复.docx`。
    """
    result = FixResult(doc_path=doc_path)
    ext = os.path.splitext(doc_path)[1].lower()
    work_path = doc_path
    if ext == ".doc":
        from .docloader import doc_to_docx
        try:
            work_path = doc_to_docx(doc_path)
        except Exception as e:
            for i in issues:
                result.failed.append({"id": i.id, "reason": f".doc 转换失败: {e}"})
            return result
        result.fixed_path = os.path.splitext(doc_path)[0] + "_修复.docx"
    elif ext != ".docx":
        for i in issues:
            result.skipped.append({"id": i.id, "reason": "仅支持 doc/docx 修复"})
        return result
    cfg = load_config(config_path) if config_path else load_config()
    ts = time.strftime("%Y%m%d-%H%M%S")
    result.backup_id = ts
    backup_dir = (cfg.get("output") or {}).get("backup_dir", "backups")
    if not os.path.isabs(backup_dir):
        # 相对路径以配置文件所在目录为基准，与运行 cwd 无关
        from .config import DEFAULT_PATH
        backup_dir = os.path.join(
            os.path.dirname(os.path.abspath(config_path or DEFAULT_PATH)), backup_dir)
    result.backup_dir = backup_dir
    os.makedirs(backup_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(doc_path))[0]
    result.backup_path = os.path.join(backup_dir, f"{stem}_{ts}{ext}")
    shutil.copy2(doc_path, result.backup_path)

    import docx
    d = docx.Document(work_path)
    dirty = False
    for issue in issues:
        if issue.status in (IssueStatus.FALSE_ALARM, IssueStatus.IGNORED):
            result.skipped.append({"id": issue.id, "reason": f"状态为{issue.status.value}"})
            continue
        try:
            if issue.rule_id == "PUNC-03":
                ok, msg = _fix_append_period(d, issue)
            elif issue.rule_id == "FORMAT-01":
                ok, msg = _fix_font(d, issue, cfg)
            elif issue.rule_id in _DISPATCH:
                ok, msg = _DISPATCH[issue.rule_id](d, issue)
            else:
                result.skipped.append({"id": issue.id, "reason": "该规则不支持自动修复（需人工）"})
                continue
            if ok:
                dirty = True
                result.succeeded.append({"id": issue.id, "rule_id": issue.rule_id,
                                         "location": issue.location,
                                         "before": issue.original, "after": msg})
                issue.status = IssueStatus.FIXED
                issue.fix_detail = msg
            else:
                result.failed.append({"id": issue.id, "reason": msg})
                issue.status = IssueStatus.FIX_FAILED
        except Exception as e:  # 单项失败不中断
            result.failed.append({"id": issue.id, "reason": f"异常: {e}"})
            issue.status = IssueStatus.FIX_FAILED
    if dirty:
        save_to = result.fixed_path or work_path
        d.save(save_to)
    result.log_path = os.path.join(backup_dir, f"fixlog_{ts}.json")
    with open(result.log_path, "w", encoding="utf-8") as f:
        json.dump({"doc_path": os.path.abspath(doc_path),
                   "fixed_path": os.path.abspath(result.fixed_path) if result.fixed_path else "",
                   "backup_path": os.path.abspath(result.backup_path),
                   "backup_id": ts, "time": ts,
                   "succeeded": result.succeeded,
                   "failed": result.failed,
                   "skipped": result.skipped}, f, ensure_ascii=False, indent=1)
    return result


def rollback(backup_path_or_id: str, backup_dir: str = "backups") -> tuple:
    """回退修复：恢复原件。返回 (ok, message)。

    backup_path_or_id 支持三种形态：
    - backup_id（时间戳，如 20261016-234645）：在 backup_dir 找 fixlog_<id>.json；
    - 备份文件路径（…/xxx_时间戳.docx）：按同目录修复日志定位原件；
    - 修复日志路径（…/fixlog_时间戳.json）。
    backup_dir 为相对路径时以 config 默认位置（项目根）为基准解析。
    """
    arg = backup_path_or_id
    log = None
    if os.path.isfile(arg) and arg.endswith(".json"):
        log = arg
    elif os.path.isfile(arg) and arg.endswith((".docx", ".doc")):
        # 备份文件 → 同目录下找引用它的修复日志
        target = os.path.abspath(arg)
        for f in glob.glob(os.path.join(os.path.dirname(target), "fixlog_*.json")):
            try:
                with open(f, encoding="utf-8") as fp:
                    if json.load(fp).get("backup_path") == target:
                        log = f
                        break
            except Exception:
                continue
        if log is None:
            return False, f"未找到引用该备份的修复日志: {arg}"
    else:
        if not os.path.isabs(backup_dir):
            from .config import DEFAULT_PATH
            backup_dir = os.path.join(os.path.dirname(DEFAULT_PATH), backup_dir)
        cand = os.path.join(backup_dir, f"fixlog_{arg}.json")
        if os.path.exists(cand):
            log = cand
        else:
            return False, f"修复日志不存在: {cand}"
    with open(log, encoding="utf-8") as f:
        rec = json.load(f)
    bak, orig = rec["backup_path"], rec["doc_path"]
    fixed = rec.get("fixed_path") or ""
    if fixed and os.path.exists(fixed):
        # .doc 修复未改动原件，回滚 = 删除修复产出
        os.remove(fixed)
        return True, f"已回退：删除修复产出 {fixed}（原件未改动）"
    if not os.path.exists(bak):
        return False, f"备份文件不存在: {bak}"
    shutil.copy2(bak, orig)
    return True, f"已回退 {orig} ← {bak}"
