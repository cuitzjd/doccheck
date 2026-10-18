"""阶段内容逻辑检查 STAGE-01~03（偏业务，系统只标记并给出理由，由用户裁决）。

阶段判定：timeline.system_online 为空 = 系统未上线，启用表述黑白名单。
"""
import re
from datetime import date

from ..models import Severity
from ..registry import rule, make_issue
from .common import location_of, split_sentences

_PERIOD = re.compile(
    r"[（(](\d{4})年(\d{1,2})月(\d{1,2})日?至(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日[)）]")
_DATE = re.compile(r"(\d{4})年(\d{1,2})月(?:(\d{1,2})日)?")
_PLAN_DOC = re.compile(r"进度计划|月进度|实施方案|技术设计方案|设计方案|里程碑|工作计划")
_PLAN_CONTEXT = re.compile(
    r"里程碑|进度计划|计划安排|实施计划|阶段安排|工作计划|时间安排|预期成果|"
    r"后续安排|后续将|将进入|拟于|计划于|预计|拟|将|预留|目标")
_REQUIREMENT_CONTEXT = re.compile(
    r"应当|应|必须|需要|要求|确保|采用|支持|定义|参数|接口|模块|"
    r"具备.{0,8}(?:条件|能力)|上线条件")
_TEST_CONTEXT = re.compile(r"测试环境|压力测试|并发用户|性能测试|服务器资源|测试结果|测试问题")


def _wording(ctx):
    cfg = ctx.config.get("stage_wording") or {}
    return (cfg.get("blacklist") or [], cfg.get("whitelist") or [],
            cfg.get("review") or [])


def _online(ctx) -> bool:
    return bool((ctx.config.get("timeline") or {}).get("system_online"))


def _scan(doc, blacklist, whitelist, review):
    """逐句扫描，返回 [(类别, 命中词, 句子, paragraph)]。whitelist 覆盖处不标记。"""
    hits = []
    for p in doc.paragraphs:
        for sent in split_sentences(p.text):
            if _is_planned_context(doc, p, sent):
                continue
            wl_spans = [m.span() for w in whitelist
                        for m in [re.search(re.escape(w), sent)] if m]
            def covered(span):
                return any(s <= span[0] and span[1] <= e for s, e in wl_spans)
            for w in blacklist:
                for m in re.finditer(re.escape(w), sent):
                    if covered(m.span()):
                        continue
                    hits.append(("black", w, sent, p))
                    break
            for w in review:
                if re.search(re.escape(w), sent) and not covered(
                        re.search(re.escape(w), sent).span()):
                    hits.append(("review", w, sent, p))
    return hits


def _paragraph_context(doc, paragraph):
    paragraphs = doc.paragraphs
    pos = next((i for i, p in enumerate(paragraphs) if p is paragraph), None)
    if pos is None:
        return paragraph.text
    heading = ""
    for p in reversed(paragraphs[:pos + 1]):
        if p.is_heading:
            heading = p.text
            break
    nearby = "\n".join(p.text for p in paragraphs[max(0, pos - 2):pos + 1] if p.text)
    return "\n".join(x for x in (heading, nearby) if x)


def _is_planned_context(doc, paragraph, sentence):
    """里程碑/计划中的未来目标不是当前进度事实，不作为超进度错误。"""
    context = _paragraph_context(doc, paragraph)
    if _PLAN_DOC.search(doc.doc_type or ""):
        return True
    if _PLAN_CONTEXT.search(context) or _PLAN_CONTEXT.search(sentence):
        return True
    doc_type = doc.doc_type or ""
    if re.search(r"需求|设计|技术|说明书|方案|源代码|代码|开发说明", doc_type) and _REQUIREMENT_CONTEXT.search(
            context + sentence):
        return True
    if re.search(r"测试报告", doc_type) and _TEST_CONTEXT.search(context + sentence):
        return True
    return False


@rule("STAGE-01", "超进度表述检测（含未上线表述黑名单）", "阶段", Severity.HIGH)
def check_stage_wording(doc, ctx):
    if _online(ctx):
        doc.meta.setdefault("stage_notes", []).append(
            "STAGE-01：系统已上线，黑白名单未启用")
        return
    blacklist, whitelist, review = _wording(ctx)
    for kind, w, sent, p in _scan(doc, blacklist, whitelist, review):
        if _is_planned_context(doc, p, sent):
            continue
        if kind == "black":
            yield make_issue(
                "STAGE-01", doc, location_of(p), sent[:80],
                expected=f"系统未上线阶段禁用表述「{w}」（命中黑名单）",
                suggestion="超进度表述：中期验收阶段系统尚未上线，请改为白名单口径（如「内部测试正常」）")
        else:
            yield make_issue(
                "STAGE-01", doc, location_of(p), sent[:80],
                expected=f"模糊表述「{w}」，待人工裁决",
                suggestion="「试运行」类模糊表述是否合规由用户裁决")


def _report_period(name: str):
    m = _PERIOD.search(name)
    if not m:
        return None
    y1, m1, d1 = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y2 = int(m.group(4)) if m.group(4) else y1
    return date(y1, m1, d1), date(y2, int(m.group(5)), int(m.group(6)))


@rule("STAGE-02", "超报告期内容检测", "阶段", Severity.HIGH,
      doc_types=["阶段性服务总结报告", "月进度计划报告"])
def check_out_of_period(doc, ctx):
    period = _report_period(doc.name)
    if not period:
        doc.meta.setdefault("stage_notes", []).append(
            f"STAGE-02 跳过：无法从文件名解析报告期（{doc.name}）")
        return
    start, end = period
    flagged = set()
    for p in doc.paragraphs:
        t = p.text
        if re.search(r"报告期|本期为|报告周期", t):
            continue  # 报告期声明行本身
        for m in _DATE.finditer(t):
            try:
                d = date(int(m.group(1)), int(m.group(2)),
                         int(m.group(3)) if m.group(3) else 1)
            except ValueError:
                continue
            if d < start and (d.year, d.month) not in flagged:
                flagged.add((d.year, d.month))
                yield make_issue(
                    "STAGE-02", doc, location_of(p), t[:70],
                    expected=f"报告期 {start} ~ {end}",
                    suggestion=f"出现报告期之前的日期 {m.group(0)}，所述事项疑似超出报告期，人工裁决")


@rule("STAGE-03", "跨文档表述矛盾", "阶段", Severity.MEDIUM)
def check_cross_doc_conflict(doc, ctx):
    if _online(ctx) or not ctx.all_docs:
        return
    blacklist, whitelist, review = _wording(ctx)
    if "_stage_scan" not in ctx.config:
        ctx.config["_stage_scan"] = {
            d.name: _scan(d, blacklist, whitelist, review) for d in ctx.all_docs}
    scan = ctx.config["_stage_scan"]
    my_black = [h for h in scan.get(doc.name, []) if h[0] == "black"]
    if not my_black:
        return
    # 仅由第一份命中黑名单的文档产出并列提示，避免重复
    black_docs = [n for n, hits in scan.items() if any(h[0] == "black" for h in hits)]
    if doc.name != sorted(black_docs)[0]:
        return
    others = []
    for d in ctx.all_docs:
        if d.name == doc.name:
            continue
        wl_sents = [s for p in d.paragraphs for s in split_sentences(p.text)
                    if not _is_planned_context(d, p, s) and any(w in s for w in whitelist)]
        if wl_sents:
            others.append((d.name, wl_sents[0]))
    for name, sent in others:
        yield make_issue(
            "STAGE-03", doc, f"{doc.name} ⇄ {name}",
            f"本文档：「{my_black[0][2][:50]}」（命中「{my_black[0][1]}」）；"
            f"{name}：「{sent[:50]}」",
            expected="同批文档对上线/测试状态表述应一致",
            suggestion="同一时期不同文档表述矛盾，人工裁决")
