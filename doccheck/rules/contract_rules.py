"""合同一致性检查 CONTRACT-01~05（最高优先级，宁可误报不可漏报）。

以 ctx.contract（用户确认的合同基准字段）为唯一权威基准；
基准字段为空时跳过对应规则（不产生问题项）。
"""
import re

from ..models import Severity
from ..registry import rule, make_issue
from .common import ORG_NAME, cn_to_int, edit_distance, location_of

_MONEY_NUM = re.compile(r"[¥￥]?\s*(\d[\d,]*(?:\.\d+)?)\s*(万)?元")
_CN_AMOUNT = re.compile(r"(?:人民币)?([〇零壹贰叁肆伍陆柒捌玖拾佰仟万亿圆]+元[整]?)")
# 支持小数百分比，并阻止从 79.17% 的末尾再次匹配出 17%。
_PERCENT = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*[%％](?!\d)")
_SPLIT_PERCENT_CONTEXT = re.compile(
    r"(?:支付给|分配给)\s*乙方\s*[12一二]|乙方\s*[12一二]\s*(?:的金额|金额|比例)")
_PROJECT_REFERENCE_HEADING = re.compile(r"参考资料|参考文件|编制依据|设计依据|引用|文献|相关标准")


def _payment_percent_values(text):
    """付款基准只收集支付语义比例，排除完成项目 50% 等工作量比例。"""
    values = []
    for match in _PERCENT.finditer(text or ""):
        prefix = text[max(0, match.start() - 14):match.start()]
        suffix = text[match.end():match.end() + 10]
        if re.search(r"(?:完成|项目|服务|工作|进度|任务)\s*$", prefix) or \
                re.match(r"(?:的)?(?:服务|工作|进度)", suffix):
            continue
        values.append(match.group(1))
    return values
_MONEY_CONTEXT = re.compile(r"合同|金额|总价|价款|费用|投资|付款|支付|款")
# 仅"总金额/合同金额"级语境才判定金额一致性（分期/按比例金额不判）
_MONEY_TOTAL_CONTEXT = re.compile(r"总金额|合同金额|合同价|合同额|项目金额|总投资|合同总金")
_ROLE_CLAUSE = re.compile(
    r"建设单位|承建单位|施工单位|监理单位|委托方|受托方|被委托方|乙方|甲方|"
    r"项目承担单位|服务承建单位")


def _baseline_orgs(ctx):
    c = ctx.contract
    if not c:
        return {}
    orgs = {}
    if c.employer:
        orgs[c.employer] = "建设单位"
    for x in c.contractors or []:
        if x:
            orgs[x] = "承建单位"
    if c.supervisor:
        orgs[c.supervisor] = "监理单位"
    return orgs


def _core(name: str) -> str:
    """机构名特征核：去掉行政区划前缀与后缀，用于近似匹配。"""
    s = re.sub(r"^(广东省|广州市|广东|广州)", "", name)
    s = re.sub(r"(股份有限公司|有限责任公司|有限公司|研究院|设计院|测绘院|规划院|集团|事务所)$", "", s)
    return s


def _has_explicit_role(cand, text):
    """只在同一角色子句内校验单位名，避免把服务对象当成合同单位变体。"""
    clauses = re.split(r"[，,；。！？\n]", text)
    return any(cand in clause and _ROLE_CLAUSE.search(clause) for clause in clauses)


def _clean_org_candidate(cand):
    return re.sub(
        r"^(?:建设单位|承建单位|施工单位|监理单位|委托方|受托方|被委托方|"
        r"项目承担单位|服务承建单位)\s*(?:为|是|系|作为|：|:)\s*", "", cand)


@rule("CONTRACT-01", "甲乙方名称一致性", "合同", Severity.HIGH, fixable=True)
def check_org_names(doc, ctx):
    orgs = _baseline_orgs(ctx)
    if not orgs:
        return
    names = list(orgs)
    cores = {n: _core(n) for n in names}
    for p in doc.paragraphs:
        t = p.text
        if not re.search(r"公司|研究院|设计院|测绘院|规划院|集团|院", t):
            continue
        for cand in set(_clean_org_candidate(m.group(0).strip("（）()"))
                       for m in ORG_NAME.finditer(t)):
            if len(cand) < 5:
                continue
            if not _has_explicit_role(cand, t):
                continue
            for base in names:
                if cand == base or cand in base or base in cand:
                    break
                # 特征核相近但全称不同 → 疑似变体/错别字
                if len(_core(cand)) >= 4 and edit_distance(_core(cand), cores[base], 2) <= 2:
                    yield make_issue(
                        "CONTRACT-01", doc, location_of(p),
                        cand, expected=base,
                        suggestion=f"疑似{orgs[base]}名称变体/错别字，应替换为合同标准全称")
                    break
        # 错别字级变体：基准名特征前缀命中、但后续字符与基准不同（如"绘测院"）
        for base in names:
            key = cores[base][:4]
            base_core = re.sub(r"(院|中心|局|所|公司)$", "", cores[base])
            if len(key) < 3 or key not in t:
                continue
            for m in re.finditer(re.escape(key) + r"[一-龥]{0,10}?(?:院|中心|局|所|公司)", t):
                cand = m.group(0)
                if cand in base or base.endswith(cand):
                    continue  # 正确写法
                if not _has_explicit_role(cand, t):
                    continue
                cand_core = re.sub(r"(院|中心|局|所|公司)$", "",
                                   re.sub(r"^(广东省|广州市|广东|广州)", "", cand))
                if cand_core != base_core and edit_distance(cand_core, base_core, 2) <= 2:
                    yield make_issue(
                        "CONTRACT-01", doc, location_of(p),
                        cand, expected=base,
                        suggestion=f"疑似{orgs[base]}名称错别字，应替换为合同标准全称")
        # 联合体漏写：承建单位语境只出现一家
        contractors = [n for n in names if orgs[n] == "承建单位"]
        if len(contractors) > 1 and re.search(r"承建单位|受托方|乙方", t):
            present = [n for n in contractors if n in t]
            if len(present) == 1:
                missing = [n for n in contractors if n not in t][0]
                yield make_issue(
                    "CONTRACT-01", doc, location_of(p),
                    t[:60], expected="、".join(contractors),
                    suggestion=f"承建单位为联合体，此处疑似漏写「{missing}」")


def _to_yuan(num_str: str, wan: bool) -> float:
    v = float(num_str.replace(",", ""))
    return v * 10000 if wan else v


def _cn_amount_to_yuan(s: str):
    """大写金额（叁佰柒拾捌万元整）转元。"""
    m = re.match(rf"([〇零壹贰叁肆伍陆柒捌玖拾佰仟]+)(万)?([〇零壹贰叁肆伍陆柒捌玖拾佰仟]*)元", s)
    if not m:
        return None
    hi = cn_to_int(m.group(1))
    lo = cn_to_int(m.group(3)) if m.group(3) else 0
    if hi is None or lo is None:
        return None
    return (hi * 10000 if m.group(2) else hi) + lo


def _baseline_yuan(ctx):
    c = ctx.contract
    raw = (c.amount_number or "").replace(",", "")
    m = re.match(r"([\d.]+)\s*(万)?", raw)
    if m:
        return _to_yuan(m.group(1), bool(m.group(2)))
    if c.amount_cn:
        return _cn_amount_to_yuan(c.amount_cn)
    return None


@rule("CONTRACT-02", "合同金额一致性", "合同", Severity.HIGH, fixable=True)
def check_amount(doc, ctx):
    base = _baseline_yuan(ctx)
    if not base:
        return
    base_desc = ctx.contract.amount_cn or ctx.contract.amount_number

    def wan_form():
        # 基准金额的"万元"写法（整万时）
        return f"{int(base / 10000)}万元" if base % 10000 == 0 else f"{base / 10000}万元"

    for p in doc.paragraphs:
        t = p.text
        if not _MONEY_CONTEXT.search(t):
            continue
        is_total_ctx = bool(_MONEY_TOTAL_CONTEXT.search(t))
        is_split_ctx = bool(re.search(r"乙方[12一二]|支付给乙方|联合体", t))
        for m in _MONEY_NUM.finditer(t):
            try:
                v = _to_yuan(m.group(1), bool(m.group(2)))
            except ValueError:
                continue
            if v < 10000:  # 忽略小额杂项
                continue
            if abs(v - base) > 0.01:
                # 分期付款等场景会引用合同金额的比例金额，属正常引用
                ratio = v / base
                if any(abs(ratio * 100 - k) < 0.5 for k in (5, 20, 30, 50, 70, 80, 100)):
                    continue
                if not is_total_ctx or is_split_ctx:
                    continue  # 非总金额语境/联合体各方分期金额不判，避免误伤
                replacement = wan_form() if m.group(2) else f"{int(base)}元"
                yield make_issue(
                    "CONTRACT-02", doc, location_of(p),
                    m.group(0), expected=replacement,
                    suggestion=f"金额与合同不一致（合同金额 {base_desc}），建议改为「{replacement}」，人工核对后修复")
        for m in _CN_AMOUNT.finditer(t):
            v = _cn_amount_to_yuan(m.group(1))
            if v and abs(v - base) > 0.01 and v >= 10000:
                ratio = v / base
                if any(abs(ratio * 100 - k) < 0.5 for k in (5, 20, 30, 50, 70, 80, 100)):
                    continue
                if not is_total_ctx or is_split_ctx:
                    continue
                yield make_issue(
                    "CONTRACT-02", doc, location_of(p),
                    m.group(1), expected=ctx.contract.amount_cn or base_desc,
                    suggestion=f"大写金额与合同不一致（合同为 {base_desc}），人工核对后修复")


@rule("CONTRACT-03", "付款条件一致性", "合同", Severity.HIGH)
def check_payment_terms(doc, ctx):
    terms = ctx.contract.payment_terms if ctx.contract else []
    if not terms:
        return
    base_pcts = set()
    for term in terms:
        base_pcts.update(_payment_percent_values(term))
    if not base_pcts:
        return
    for p in doc.paragraphs:
        t = p.text
        if not re.search(r"付款|支付|首期款|期款|尾款|进度款", t):
            continue
        for m in _PERCENT.finditer(t):
            near = t[max(0, m.start() - 25):m.end() + 25]
            if not re.search(r"付款|支付|价款|款项|首期款|期款|尾款|进度款", near):
                continue  # 进度百分比（如"完成项目50%"）不是付款比例
            split_near = t[max(0, m.start() - 60):m.end() + 60]
            if _SPLIT_PERCENT_CONTEXT.search(split_near):
                continue  # 乙方1/乙方2分摊比例不是合同付款节点
            before = t[max(0, m.start() - 6):m.start()]
            if re.search(r"(完成|进度|项目)$", before) and t[m.end():m.end() + 1] == "的":
                continue  # "完成项目50%的…"为工作量进度
            if m.group(1) not in base_pcts:
                yield make_issue(
                    "CONTRACT-03", doc, location_of(p),
                    t[:60], expected=f"合同付款比例节点：{'/'.join(sorted(base_pcts))}%",
                    suggestion=f"付款比例 {m.group(1)}% 与合同约定不一致，人工核对")


_PROJ_LIKE = re.compile(r"[一-龥（）()0-9]{6,60}?项目（[一-龥（）()0-9]{4,50}?服务）?")


@rule("CONTRACT-04", "项目名称一致性", "合同", Severity.HIGH, fixable=True)
def check_project_name(doc, ctx):
    base = (ctx.contract.project_name if ctx.contract else "") or \
           (ctx.config.get("project") or {}).get("name", "")
    if not base:
        return
    core = re.sub(r"[（(].*?[)）]", "", base)[:10]  # 主干前缀，如 省自然资源综合感知
    if len(core) < 6:
        core = base[:10]
    base_years = re.findall(r"[（(]\s*(\d{4})\s*年", base)

    def norm(s):
        return re.sub(r"\s+", "", s).replace("(", "（").replace(")", "）")

    def heading_before(paragraph):
        pos = next((i for i, item in enumerate(doc.paragraphs) if item is paragraph), None)
        if pos is None:
            return ""
        for item in reversed(doc.paragraphs[:pos + 1]):
            if item.is_heading or _PROJECT_REFERENCE_HEADING.fullmatch(item.text.strip()):
                return item.text
        return ""

    n_base = norm(base)
    for p in doc.paragraphs:
        t = p.text
        if core[:8] not in t:
            continue
        # 参考资料中的立项方案、合同书、需求书是被引用文件名，不是当前文档的项目名称。
        if _PROJECT_REFERENCE_HEADING.search(heading_before(p)):
            continue
        cands = []
        # 《》引用中的名称（如"项目名-需求规格说明书"属正常，项目名写错则标记）
        cands += [m.group(1) for m in re.finditer(r"《([^》]{8,90})》", t)
                  if core[:8] in m.group(1)]
        # "项目名称：XXX"声明
        m = re.search(r"项目名称(?:为)?[:：]?\s*([^。；\n]{6,90})", t)
        if m and core[:8] in m.group(1):
            cands.append(m.group(1).strip())
        for cand in cands:
            n_cand = norm(cand)
            if n_cand == n_base or n_base.startswith(n_cand) or n_cand.startswith(n_base):
                continue  # 一致或为项目名的合法前缀/后缀扩展（如文档名）
            cand_years = re.findall(r"（\s*(\d{4})\s*年", n_cand)
            wrong_year = bool(cand_years and base_years and cand_years != base_years)
            if wrong_year or edit_distance(n_cand, n_base, 4) <= 4:
                yield make_issue(
                    "CONTRACT-04", doc, location_of(p),
                    cand, expected=base,
                    suggestion="项目名称与合同不一致（含括号/年号差异），应使用合同全称")


# 常见可合法出现的政府/通用机构，不视为旧文档残留
_ORG_WHITELIST = re.compile(
    r"中华人民共和国|国务院|省政府|省政务|政务服务和数据管理|财政厅|发改委|"
    r"自然资源部|省自然资源厅|市场监督管理局|审计|财政部|国家税务总局")
_ORG_ROLE_CONTEXT = re.compile(
    r"建设单位|承建单位|施工单位|监理单位|委托方|受托方|被委托方|乙方|甲方|"
    r"项目承担单位|服务承建单位")
_ORG_LEGACY_CONTEXT = re.compile(
    r"沿用|复制|旧文档|上一项目|上个项目|原项目|历史项目|原有系统|既有成果|"
    r"原有成果|此前项目|去年项目|另一个项目|遗留|旧系统")
_ORG_SAFE_CONTEXT = re.compile(
    r"服务对象|业务对象|服务于|面向|为.{0,12}提供服务|用户单位|使用单位|需求单位|"
    r"主管部门|协作单位|配合|协同|对接|负责(?:与)?[一-龥（）()]{2,30}(?:院|中心|局|公司)|"
    r"联系|支撑|服务单位|供应商|厂商|厂家|品牌|设备|产品|软件|平台|技术支持|采购|"
    r"第三方|测评|检测|测试机构|评估机构|无人机|测绘仪器|依据|参考|引用|来源|资料|"
    r"文献|标准|规范|现状|未配套|模块|功能|能力|解析|管理|系统升级|业务要求")


def _candidate_context(text, cand):
    """优先在包含机构名的句子内判断语义，避免同段其他单位污染结果。"""
    parts = re.split(r"(?<=[。！？；])", text)
    return next((part for part in parts if cand in part), text)


def _is_role_or_legacy(cand, text):
    context = _candidate_context(text, cand)
    cand_start = context.find(cand)
    cand_end = cand_start + len(cand)
    for role in _ORG_ROLE_CONTEXT.finditer(context):
        if (0 <= cand_start - role.end() <= 60) or (0 <= role.start() - cand_end <= 60):
            return "role"
    if _ORG_SAFE_CONTEXT.search(context):
        return "safe"
    if _ORG_LEGACY_CONTEXT.search(context):
        return "legacy"
    return "unknown"


@rule("CONTRACT-05", "疑似旧文档复制检测", "合同", Severity.HIGH)
def check_legacy_residue(doc, ctx):
    orgs = _baseline_orgs(ctx)
    keywords = ctx.config.get("legacy_keywords") or []
    if not orgs and not keywords:
        return  # 合同基准字段为空时跳过
    for p in doc.paragraphs:
        t = p.text
        for kw in keywords:
            if kw and kw in t:
                yield make_issue(
                    "CONTRACT-05", doc, location_of(p),
                    f"{t[max(0, t.find(kw) - 15):t.find(kw) + len(kw) + 15]}",
                    expected=f"历史项目词库命中：{kw}",
                    suggestion="疑似复制旧文档残留，人工裁决")
        for cand in set(_clean_org_candidate(m.group(0).strip("（）()"))
                       for m in ORG_NAME.finditer(t)):
            if len(cand) < 6 or _ORG_WHITELIST.search(cand):
                continue
            if any(cand == b or cand in b or b in cand or
                   edit_distance(_core(cand), _core(b), 2) <= 2
                   for b in orgs):
                continue  # 基准单位本身及其变体由 CONTRACT-01 处理
            context_kind = _is_role_or_legacy(cand, t)
            if context_kind == "safe":
                continue  # 服务对象、供应商、第三方或引用单位不是角色错误
            if context_kind == "unknown":
                continue  # 仅出现机构名不足以认定是旧文档残留
            if context_kind == "role":
                expected = "本项目合同基准中的建设/承建/监理单位"
                suggestion = "该单位出现在委托/承建角色语境，疑似被委托方名称不一致，请核对合同基准"
            else:
                expected = "本项目不应沿用其他项目机构"
                suggestion = "疑似复制旧文档残留，结合项目角色和上下文人工裁决"
            yield make_issue(
                "CONTRACT-05", doc, location_of(p),
                cand, expected=expected, suggestion=suggestion)
