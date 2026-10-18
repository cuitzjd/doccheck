"""问题实际错误概率：本地上下文评分 + 可选 TypeSafe Jev 适配器。"""
import json
import os
import re
from urllib import error as url_error
from urllib import request as url_request


_PARA_RE = re.compile(r"段落\s*(\d+)")
_ROLE_RE = re.compile(r"建设单位|承建单位|施工单位|监理单位|委托方|受托方|被委托方|乙方|甲方")
_LEGACY_RE = re.compile(
    r"沿用|复制|旧文档|上一项目|上个项目|原项目|历史项目|原有系统|既有成果|"
    r"原有成果|此前项目|去年项目|另一个项目|遗留|旧系统")
_PLAN_RE = re.compile(
    r"里程碑|进度计划|计划安排|实施计划|阶段安排|工作计划|时间安排|预期成果|"
    r"后续安排|后续将|将进入|拟于|计划于|预计|拟|将|预留|目标")
_OBJECT_RE = re.compile(
    r"服务对象|业务对象|服务于|面向|为.{0,12}提供服务|用户单位|使用单位|"
    r"需求单位|主管部门|协作单位|配合|协同|对接|负责与|联系|支撑|服务单位")
_VENDOR_RE = re.compile(
    r"供应商|厂商|厂家|品牌|设备|产品|软件|平台|技术支持|采购|第三方|测评|"
    r"检测|测试机构|评估机构|无人机|测绘仪器")
_REFERENCE_RE = re.compile(r"依据|参考|引用|来源|资料|文献|标准|规范|现状|既有")
_TEST_RE = re.compile(r"测试环境|内部测试|联调|压力测试|性能测试|测试结果|测试问题|"
                      r"服务器资源|验收条件|上线条件|功能开发|开发完成")
_FACT_RE = re.compile(r"已上线|已经上线|正式上线|目前用户|当前用户|生产环境|正式运行|"
                      r"已完成|已经完成|当前运行|正常运行|运行稳定|对外提供服务|"
                      r"上线运行|投入使用|顺利上线|成功上线|顺利投入使用")
_PLAN_DOC_RE = re.compile(r"进度计划|月进度|实施方案|技术设计方案|设计方案|里程碑|工作计划")
_PERCENT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*[%％](?!\d)")
_SPLIT_PERCENT_RE = re.compile(
    r"(?:支付给|分配给)\s*乙方\s*[0-9一二三四五六七八九十]+|"
    r"乙方\s*[0-9一二三四五六七八九十]+\s*(?:的金额|金额|比例)")
_MEMORY_RULE_PREFIXES = ("CONTRACT-", "IDENTITY-", "STAGE-")


def _config(cfg):
    cfg = cfg or {}
    jev = cfg.get("jev") or {}
    def integer(env_name, config_name, default):
        try:
            return int(os.getenv(env_name, jev.get(config_name, default)) or default)
        except (TypeError, ValueError):
            return default
    return {
        "base_url": os.getenv("JEV_BASE_URL", jev.get("base_url", "https://api.typesafe.ai/v1")).strip(),
        # TypeSafe documents TYPESAFE_API_KEY; keep JEV_API_KEY as a compatibility alias.
        "api_key": (os.getenv("TYPESAFE_API_KEY") or os.getenv("JEV_API_KEY") or "").strip(),
        "model": os.getenv("JEV_MODEL", jev.get("model", "jev-latest")).strip(),
        "timeout": integer("JEV_TIMEOUT", "timeout", 20),
        "batch_size": integer("JEV_BATCH_SIZE", "batch_size", 12),
        "enabled": str(os.getenv("JEV_ENABLED", jev.get("enabled", True))).lower()
        not in {"0", "false", "no", "off"},
    }


def _paragraph_context(doc, issue):
    """取命中段落、最近标题和相邻段落，作为评分上下文。"""
    paragraphs = getattr(doc, "paragraphs", []) or []
    match = _PARA_RE.search(issue.location or "")
    pos = None
    if match:
        target = int(match.group(1))
        pos = next((i for i, p in enumerate(paragraphs) if p.index == target), None)
    if pos is None:
        original = (issue.original or "").strip("「」『』")
        pos = next((i for i, p in enumerate(paragraphs) if original and original[:20] in p.text), None)
    if pos is None:
        return issue.original or "", ""
    heading = ""
    for p in reversed(paragraphs[:pos + 1]):
        if getattr(p, "is_heading", False):
            heading = p.text
            break
    nearby = "\n".join(p.text for p in paragraphs[max(0, pos - 2):pos + 3] if p.text)
    return nearby, heading


def _current_paragraph(doc, issue):
    """只取问题所在段落，作为短期记忆的主证据，避免邻段关键词污染当前判断。"""
    paragraph = _paragraph_for_issue(doc, issue)
    return paragraph.text if paragraph is not None else (issue.original or "")


def _paragraph_for_issue(doc, issue):
    """按段落号定位原始 Paragraph，保留字号/编号等格式证据。"""
    paragraphs = getattr(doc, "paragraphs", []) or []
    match = _PARA_RE.search(issue.location or "")
    if match:
        target = int(match.group(1))
        paragraph = next((p for p in paragraphs if p.index == target), None)
        if paragraph is not None:
            return paragraph
    original = (issue.original or "").strip("「」『』")
    if original:
        paragraph = next((p for p in paragraphs if original[:20] in p.text), None)
        if paragraph is not None:
            return paragraph
    return None


def _signals(issue, doc):
    nearby, heading = _paragraph_context(doc, issue)
    text = "\n".join(filter(None, [issue.original, nearby, heading]))
    return text, nearby, heading


def _score(value):
    return round(max(0.02, min(0.98, value)), 2)


def _heuristic_score_base(issue, doc, cfg=None):
    """按规则证据和文档语境估计实际错误概率，不再把严重级别当概率。"""
    text, nearby, heading = _signals(issue, doc)
    doc_type = getattr(doc, "doc_type", "") or ""
    rid = issue.rule_id
    current = _current_paragraph(doc, issue)
    sentence = _candidate_sentence(current, issue.original)
    if rid == "CONTRACT-03":
        text = "\n".join(filter(None, [current, heading]))
    elif rid.startswith(_MEMORY_RULE_PREFIXES):
        # 语义规则只看命中句和标题；相邻句只进入证据展示，不能改变当前句的结论。
        text = "\n".join(filter(None, [sentence, heading]))
    context = "\n".join(filter(None, [text, issue.expected, issue.suggestion]))
    planned = bool(_PLAN_RE.search(text) or _PLAN_DOC_RE.search(doc_type + heading))
    test_context = bool(_TEST_RE.search(context))
    reference = bool(_REFERENCE_RE.search(context))
    role_context = bool(_ROLE_RE.search(text))

    if rid == "CONTRACT-05":
        if _ROLE_RE.search(text):
            return 0.94, "出现委托/承建/建设/监理角色归属，陌生单位可能替代了合同基准单位"
        if _LEGACY_RE.search(text):
            return 0.9, "出现沿用旧成果、原有系统或历史项目语境，复制残留可能性高"
        if _VENDOR_RE.search(text):
            return 0.08, "单位出现在供应商、设备、厂商或第三方测评语境，通常是业务对象"
        if _OBJECT_RE.search(text):
            return 0.06, "单位出现在服务对象、用户、协作或对接语境，通常不是合同角色错误"
        if _REFERENCE_RE.search(text):
            return 0.12, "单位出现在依据、参考或现状引用语境，不能据此认定为项目角色错误"
        return 0.45, "只发现陌生机构名称，缺少角色或旧项目语境，需要人工结合上下文判断"

    if rid.startswith("STAGE-"):
        # 阶段问题要以命中句和标题为主，不能让相邻段落的“测试”字样覆盖当前句的事实语气。
        stage_text = "\n".join(filter(None, [issue.original, heading]))
        stage_planned = bool(_PLAN_RE.search(stage_text) or
                             _PLAN_DOC_RE.search(doc_type))
        stage_test_context = bool(_TEST_RE.search(stage_text))
        if stage_planned:
            return 0.04, "命中里程碑/进度计划/未来安排语境，属于计划目标而非已发生事实"
        if stage_test_context:
            return 0.08, "命中测试、联调或上线条件语境，不等同于生产环境已上线"
        if "模糊表述" in (issue.expected or ""):
            return 0.53, "规则只标记了试运行等模糊表述，缺少明确违规事实，需人工裁决"
        if _FACT_RE.search(issue.original or text):
            return 0.92, "命中已上线/已完成/正式运行等事实性表述，并与当前未上线阶段冲突"
        return 0.39, "阶段语句与当前进度的关系不充分明确，证据不足，建议人工复核"

    # 合同基准类规则通常有明确的期望值，但计划或引用语境会降低“当前文档错误”的可能性。
    if rid in {"CONTRACT-01", "CONTRACT-02", "CONTRACT-03", "CONTRACT-04"}:
        if planned and not role_context:
            return 0.42, "合同差异出现在计划/方案语境，可能是待定目标或示例，不能直接视为合同错误"
        if reference and not role_context:
            return 0.18, "差异位于依据、引用或现状语境，可能是在描述外部资料"
        if rid == "CONTRACT-03" and re.search(r"付款|支付|首期款|尾款|进度款", text):
            return 0.96, "付款节点和合同基准比例直接冲突，属于高证据合同差异"
        if rid == "CONTRACT-02" and re.search(r"总金额|合同金额|合同价|合同额|总投资", text):
            return 0.97, "总金额语境中的数值与合同基准不一致，属于可核验的确定性差异"
        if rid == "CONTRACT-04" and re.search(r"项目名称|年", issue.original or text):
            return 0.95, "项目名称或年号与合同基准不一致，且命中项目名称声明语境"
        if rid == "CONTRACT-01" and role_context:
            return 0.94, "单位名称差异出现在建设/承建/监理角色子句内，合同角色归属证据充分"
        if issue.expected:
            return 0.74, "规则给出了合同基准差异，但当前片段缺少更强的角色或金额语境"
        return 0.58, "发现合同类差异但缺少可核验基准片段，需人工确认"

    if rid.startswith("IDENTITY-"):
        if reference:
            return 0.16, "身份表述位于合同、依据或引用语境，可能是在转述外部条款"
        if rid == "IDENTITY-03":
            return 0.78, "承建单位同时被发出指令/考核性要求，存在甲方口吻证据但仍需结合文体判断"
        if role_context:
            return 0.91, "单位名称与角色词在同一句中冲突，角色归属证据充分"
        return 0.62, "出现身份表述问题，但缺少同句角色绑定，需人工结合上下文判断"

    # 标点/重复字是局部、可验证的文本证据；段末句号和重复词仍保留一定不确定性。
    punctuation_scores = {
        "PUNC-01": (0.97, "同一片段出现连续重复标点，属于可直接核验的文本错误"),
        "PUNC-02": (0.94, "出现句首或相邻多余标点，属于可直接核验的文本错误"),
        "PUNC-03": (0.68, "段末缺少终止符，但条目、引用和排版语境仍可能合法"),
        "PUNC-04": (0.89, "中文语境出现半角标点，且已排除主要代码/版本号场景"),
        "PUNC-05": (0.71, "检测到重复字词，但少量中文叠词或专名仍可能是合法写法"),
    }
    if rid in punctuation_scores:
        probability, reason = punctuation_scores[rid]
        paragraph = _paragraph_for_issue(doc, issue)
        if rid == "PUNC-03" and paragraph is not None:
            if (getattr(paragraph, "is_heading", False) or
                    getattr(paragraph, "is_table_cell", False) or
                    getattr(paragraph, "is_list_item", False) or
                    getattr(paragraph, "outline_level", -1) >= 0):
                probability = 0.12
                reason = "段落格式显示为标题、表格或编号条目，缺少句号可能是排版语义而非正文错误"
            elif getattr(paragraph, "keep_with_next", False) and len(current) <= 60:
                probability = 0.16
                reason = "段落设置为与下段保持，且篇幅较短，可能是小标题或标签行"
        if rid == "PUNC-01":
            probability += min(0.02, max(0, len(re.findall(r"【([^】]+)】", issue.original or "")) - 1) * 0.01)
        elif rid == "PUNC-04" and issue.expected in {"，", "；", "（", "）"}:
            probability += 0.02
        elif rid == "PUNC-05":
            probability += min(0.06, max(0, len(issue.expected or "") - 1) * 0.02)
        if reference:
            probability -= 0.18
            reason += "；同时处于引用/资料语境，概率下调"
        return _score(probability), reason

    format_scores = {
        "FORMAT-01": (0.73, "正文与文档主流字体/字号不一致，较可能是格式错误，但需确认模板要求"),
        "FORMAT-02": (0.76, "同级标题格式与多数标题不一致，较可能是排版错误"),
        "FORMAT-03": (0.64, "段落行距或缩进偏离主流，可能是格式错误，也可能是特殊段落样式"),
        "FORMAT-04": (0.93, "章节、图表或多级编号出现可核验的重复/跳号"),
    }
    if rid in format_scores:
        probability, reason = format_scores[rid]
        diff_count = len(re.findall(r"≠|不一致|偏离", issue.expected or ""))
        if diff_count:
            probability += min(0.12, (diff_count - 1) * 0.04)
            reason += f"；检测到 {diff_count} 项格式差异"
        if rid == "FORMAT-03" and planned:
            probability -= 0.05
            reason += "；位于方案/计划章节，可能存在特殊段落模板"
        return _score(probability), reason

    if rid.startswith("PAGE-"):
        return 0.9, "页脚页码序列、目录、X/X 当前页/总页数或物理页数存在可复核的结构差异，建议结合盖章 PDF 确认"

    if rid.startswith("BASIS-"):
        if "全文" in (issue.location or "") or not issue.original:
            return 0.57, "规则发现依据声明缺失，但这是全文结构判断，不能仅凭局部片段确定错误"
        return 0.72, "依据清单或服务范围与合同基准关联不足，存在内容错误可能"

    # 兜底只使用证据强弱做小幅调整，不再读取 severity 直接映射概率。
    probability = 0.5
    if issue.expected and issue.original:
        probability += 0.12
    elif issue.original:
        probability += 0.04
    if planned:
        probability -= 0.12
    if test_context:
        probability -= 0.08
    return _score(probability), "未匹配专用规则评分，依据原文/期望值和上下文证据给出保守估计"


def _is_workload_percent(text, match):
    prefix = text[max(0, match.start() - 14):match.start()]
    suffix = text[match.end():match.end() + 10]
    return bool(re.search(r"(?:完成|项目|服务|工作|进度|任务)\s*$", prefix) or
                re.match(r"(?:的)?(?:服务|工作|进度)", suffix))


def _payment_percent_values(text):
    return [m.group(1) for m in _PERCENT_RE.finditer(text or "")
            if not _is_workload_percent(text, m)]


def _as_percent_set(values):
    out = set()
    for value in values:
        try:
            out.add(round(float(value), 4))
        except (TypeError, ValueError):
            continue
    return out


def _long_term_memory(cfg):
    """把用户确认的合同和时间线整理成跨文档复用的长期记忆。"""
    cfg = cfg or {}
    contract = cfg.get("contract") or {}
    timeline = cfg.get("timeline") or {}
    payment_terms = contract.get("payment_terms") or []
    payment_percentages = sorted(
        _as_percent_set(value for term in payment_terms
                        for value in _payment_percent_values(str(term)))
    )
    return {
        "confirmed": bool(contract.get("confirmed")),
        "project_name": contract.get("project_name", ""),
        "roles": {
            "建设单位": contract.get("employer", ""),
            "承建单位": [x for x in contract.get("contractors", []) or [] if x],
            "监理单位": contract.get("supervisor", ""),
        },
        "amount": contract.get("amount_number") or contract.get("amount_cn", ""),
        "payment_percentages": payment_percentages,
        "service_period": contract.get("service_period", ""),
        "timeline": {
            "contract_signed": timeline.get("contract_signed", ""),
            "work_order": timeline.get("work_order", ""),
            "system_online": timeline.get("system_online", ""),
        },
    }


def _short_term_memory(issue, doc):
    """短期记忆以当前段落为主，相邻段落和标题只作为辅助证据。"""
    if doc is None:
        current = issue.original or ""
        nearby, heading = current, ""
        doc_type = ""
        paragraph = None
    else:
        paragraph = _paragraph_for_issue(doc, issue)
        current = _current_paragraph(doc, issue)
        nearby, heading = _paragraph_context(doc, issue)
        doc_type = getattr(doc, "doc_type", "") or ""
    sentence = _candidate_sentence(current, issue.original)
    paragraph_format = {}
    if paragraph is not None:
        paragraph_format = {
            "style": getattr(paragraph, "style", ""),
            "font_size": getattr(paragraph, "font_size", 0.0),
            "bold": bool(getattr(paragraph, "bold", False)),
            "is_heading": bool(getattr(paragraph, "is_heading", False)),
            "is_table_cell": bool(getattr(paragraph, "is_table_cell", False)),
            "is_list_item": bool(getattr(paragraph, "is_list_item", False)),
            "num_level": getattr(paragraph, "num_level", -1),
            "keep_with_next": bool(getattr(paragraph, "keep_with_next", False)),
            "outline_level": getattr(paragraph, "outline_level", -1),
        }
    return {
        "document_type": doc_type,
        "location": issue.location or "",
        "heading": heading,
        "current_paragraph": current[:1200],
        "current_sentence": sentence[:500],
        "nearby_excerpt": nearby[:500],
        "paragraph_format": paragraph_format,
    }


def _candidate_sentence(text, candidate):
    """只取包含问题对象的句子，避免同段其他机构的角色词污染判断。"""
    if not text:
        return ""
    if not candidate:
        return text
    parts = re.split(r"(?<=[。！？；])", text)
    exact = next((part for part in parts if candidate in part), None)
    if exact:
        return exact
    # 规则为避免页面过长会截断原文，截断片段仍可定位到当前句。
    for size in (40, 20):
        prefix = candidate[:size]
        if prefix:
            match = next((part for part in parts if prefix in part), None)
            if match:
                return match
    return text


def _context_validation(issue, doc, cfg):
    """交叉验证长期合同事实与当前段落语义，返回可解释证据。"""
    long_term = _long_term_memory(cfg)
    short_term = _short_term_memory(issue, doc)
    current = short_term["current_paragraph"] or issue.original or ""
    sentence = short_term["current_sentence"] or current
    short_text = "\n".join(filter(None, [sentence, short_term["heading"]]))
    candidate_text = sentence
    rid = issue.rule_id
    memory_relevant = rid.startswith(_MEMORY_RULE_PREFIXES)
    relation = "insufficient" if memory_relevant else "not_applicable"
    signals = []
    if long_term["confirmed"]:
        signals.append("合同基准已确认")
    else:
        signals.append("合同基准未确认")
    if not memory_relevant:
        signals.append("该规则只依赖局部文本/格式，不适用合同长期记忆")

    if rid == "CONTRACT-03":
        percentages = list(_PERCENT_RE.finditer(current))
        split_values = []
        normal_values = []
        for match in percentages:
            if _is_workload_percent(current, match):
                continue
            prefix = current[max(0, match.start() - 60):match.end()]
            (split_values if _SPLIT_PERCENT_RE.search(prefix) else normal_values).append(match.group(1))
        expected = set(long_term["payment_percentages"])
        normal_set = _as_percent_set(normal_values)
        mismatches = sorted(normal_set - expected)
        if split_values and not mismatches and not normal_set - expected:
            relation = "exempt"
            signals.append(f"短期段落识别到乙方分摊比例 {','.join(split_values)}%")
            signals.append("分摊比例不属于长期合同付款节点")
        elif mismatches:
            relation = "conflict"
            signals.append(f"长期付款节点 {sorted(expected)}% 与短期付款比例 {mismatches}% 冲突")
        elif normal_values and expected:
            relation = "consistent"
            signals.append(f"短期付款比例与长期节点 {sorted(expected)}% 一致")
    elif rid == "CONTRACT-05":
        if _LEGACY_RE.search(candidate_text):
            relation = "conflict"
            signals.append("当前段落出现旧项目/既有成果语义，优先视为长期记忆冲突")
        elif _OBJECT_RE.search(candidate_text) or _VENDOR_RE.search(candidate_text) or _REFERENCE_RE.search(candidate_text):
            relation = "exempt"
            signals.append("当前段落将机构作为服务对象、供应商或引用资料")
        elif _ROLE_RE.search(candidate_text) and issue.original:
            relation = "conflict"
            signals.append("当前段落将机构放入合同角色，但机构不在长期合同角色名单中")
    elif rid.startswith("STAGE-"):
        plan = bool(_PLAN_RE.search(short_text) or _PLAN_DOC_RE.search(short_term["document_type"]))
        test = bool(_TEST_RE.search(short_text))
        fact = bool(_FACT_RE.search(sentence))
        if not long_term["timeline"]["system_online"] and (plan or test):
            relation = "exempt"
            signals.append("长期时间线显示尚未上线，短期段落是计划或测试语境")
        elif not long_term["timeline"]["system_online"] and fact:
            relation = "conflict"
            signals.append("长期时间线显示尚未上线，短期段落却使用已上线/生产事实")
    elif rid in {"CONTRACT-01", "CONTRACT-02", "CONTRACT-04"}:
        if not long_term["confirmed"]:
            relation = "insufficient"
        elif rid == "CONTRACT-04" and _REFERENCE_RE.search(short_text):
            relation = "exempt"
            signals.append("当前句位于参考资料/依据语境，引用文件名不等于当前项目名称")
        elif issue.expected:
            relation = "conflict"
            signals.append("当前片段的期望值与长期合同基准存在差异")
    elif rid.startswith("IDENTITY-"):
        identity_text = sentence
        if _OBJECT_RE.search(identity_text) or _REFERENCE_RE.search(identity_text):
            relation = "exempt"
            signals.append("当前段落是服务/引用语境，不是角色归属声明")
        elif _ROLE_RE.search(identity_text):
            relation = "conflict"
            signals.append("当前段落包含角色词，需与长期角色名单核对")

    if (rid.startswith(_MEMORY_RULE_PREFIXES) and not long_term["confirmed"] and
            relation == "conflict"):
        relation = "insufficient"
        signals.append("合同基准尚未确认，不能形成长期记忆与短期记忆的确定冲突")

    return {
        "long_term": {
            "confirmed": long_term["confirmed"],
            "project_name": long_term["project_name"],
            "roles": long_term["roles"],
            "amount": long_term["amount"],
            "payment_percentages": long_term["payment_percentages"],
            "service_period": long_term["service_period"],
            "timeline": long_term["timeline"],
        },
        "short_term": short_term,
        "validation": {
            "scope": "contract_context" if memory_relevant else "local_text",
            "relation": relation,
            "signals": signals,
        },
    }


def _memory_summary(evidence):
    long_term = evidence["long_term"]
    short_term = evidence["short_term"]
    validation = evidence["validation"]
    scope = validation.get("scope", "contract_context")
    project = long_term["project_name"] or "未记录项目名称"
    online = long_term["timeline"]["system_online"] or "未确认上线"
    short = short_term.get("current_sentence") or short_term["current_paragraph"] or "未定位当前段落"
    signal = "；".join(validation["signals"]) or "没有足够的交叉证据"
    if scope != "contract_context":
        fmt = short_term.get("paragraph_format") or {}
        flags = [key for key in
                 ("is_heading", "is_table_cell", "is_list_item", "keep_with_next")
                 if fmt.get(key)]
        fmt_note = f"段落格式证据={','.join(flags)}。" if flags else ""
        return f"短期记忆：{short[:160]}。{fmt_note}局部验证={validation['relation']}：{signal}"
    return (
        f"长期记忆：项目={project[:50]}；上线节点={online}；"
        f"付款节点={long_term['payment_percentages'] or '未记录'}。"
        f"短期记忆：{short[:160]}。"
        f"交叉验证={validation['relation']}：{signal}"
    )


def _validated_score(issue, doc, cfg=None):
    evidence = _context_validation(issue, doc, cfg)
    score, reason = _heuristic_score_base(issue, doc, cfg)
    relation = evidence["validation"]["relation"]
    if relation == "exempt":
        score = min(score, 0.12)
    elif relation == "consistent":
        score = min(score, 0.15)
    elif relation == "conflict":
        score = max(score, 0.92 if issue.rule_id.startswith(("CONTRACT-", "IDENTITY-"))
                    else min(0.98, score + 0.02))
    elif relation == "insufficient" and issue.rule_id.startswith(_MEMORY_RULE_PREFIXES):
        # 没有长期基准或没有命中当前句时，只能给出待复核概率，不能把规则命中当成结论。
        score = min(score, 0.64)
    return _score(score), _memory_summary(evidence) + "。" + reason, evidence


def heuristic_score(issue, doc, cfg=None):
    """用长期合同记忆和短期段落记忆交叉验证后返回概率。"""
    score, reason, _evidence = _validated_score(issue, doc, cfg)
    return score, reason


def _jev_endpoint(base_url):
    base = base_url.rstrip("/")
    return base if base.endswith("/systemone") else base + "/systemone"


def _parse_jev_response(payload):
    """解析 TypeSafe systemone 的 answers[id].noul 响应。"""
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        return {}
    out = {}
    for issue_id, answer in answers.items():
        if not isinstance(answer, dict):
            continue
        try:
            probability = max(0.0, min(1.0, float(answer["noul"])))
        except (KeyError, TypeError, ValueError):
            continue
        confidence = answer.get("confidence")
        reason = "TypeSafe Jev Noul 判断为真实错误"
        if confidence is not None:
            try:
                reason += f"；模型置信度 {float(confidence):.0%}"
            except (TypeError, ValueError):
                pass
        out[str(issue_id)] = {"probability": probability, "reason": reason}
    return out


def _jev_batch(items, base_url, api_key, model, timeout, cfg=None):
    questions = {}
    for issue, doc in items:
        evidence = _context_validation(issue, doc, cfg)
        short_term = evidence["short_term"]
        context_parts = [
            f"短期记忆（当前句）：{short_term.get('current_sentence', '')}",
            f"短期记忆（当前段落）：{short_term['current_paragraph']}",
            f"短期记忆（标题/相邻段落）：{short_term['heading']}\n{short_term['nearby_excerpt']}",
            f"短期记忆（段落格式）：{json.dumps(short_term.get('paragraph_format', {}), ensure_ascii=False)}",
        ]
        if evidence["validation"].get("scope") == "contract_context":
            context_parts.insert(
                0, f"长期记忆（已确认合同/时间线）：{json.dumps(evidence['long_term'], ensure_ascii=False)}")
        context_parts.extend([
            f"规则：{issue.rule_id} {issue.rule_name}",
            f"基准/期望：{issue.expected}",
            f"交叉验证结果：{json.dumps(evidence['validation'], ensure_ascii=False)}",
        ])
        context = "\n".join(filter(None, context_parts))
        questions[issue.id] = {
            "type": "noul",
            "instructions": (
                "判断这条文档质检提示是否构成真实错误。返回 1 表示确实错误，"
                "返回 0 表示误报。必须先用长期记忆核对合同角色、金额、付款节点和时间线，"
                "再用短期记忆核对当前段落；两者一致才提高概率，两者冲突或短期明确是例外语境则降低概率。"
                "不要仅因单位名称陌生就判错。\n" + context
            ),
        }
    body = {"state": "文档质检问题批量判断", "model": model, "questions": questions}
    req = url_request.Request(
        _jev_endpoint(base_url),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with url_request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return _parse_jev_response(payload)


def _merge_jev_probability(model_probability, local_probability, issue):
    """让 Jev 的语言判断服从已验证的本地证据，避免模型重新引入误报。"""
    evidence = issue.context_validation or {}
    relation = (evidence.get("validation") or {}).get("relation")
    model_probability = _score(model_probability)
    local_probability = _score(local_probability)
    if relation == "exempt":
        return min(model_probability, 0.12)
    if relation == "consistent":
        return min(model_probability, 0.15)
    if relation == "conflict":
        return max(model_probability, local_probability, 0.92)
    if relation == "insufficient" and issue.rule_id.startswith(_MEMORY_RULE_PREFIXES):
        return _score(min(0.64, (model_probability + local_probability) / 2))
    return model_probability


def apply_probability_scores(issues, docs, cfg=None):
    """长期合同记忆与短期段落记忆交叉评分；JEV 配置完整时再覆盖概率。"""
    by_name = {d.name: d for d in docs}
    for issue in issues:
        doc = by_name.get(issue.doc_name)
        if doc:
            issue.error_probability, issue.probability_reason, issue.context_validation = \
                _validated_score(issue, doc, cfg)
        else:
            issue.error_probability = 0.5
            issue.probability_reason = "无法定位原文档，长期记忆与短期记忆无法交叉验证，使用中性概率"
            issue.context_validation = {
                "long_term": _long_term_memory(cfg),
                "short_term": {"current_paragraph": "", "nearby_excerpt": "", "heading": ""},
                "validation": {
                    "scope": "contract_context" if issue.rule_id.startswith(_MEMORY_RULE_PREFIXES)
                    else "local_text",
                    "relation": "insufficient" if issue.rule_id.startswith(_MEMORY_RULE_PREFIXES)
                    else "not_applicable",
                    "signals": ["无法定位原文档"],
                },
            }
        issue.probability_source = "heuristic"

    jev = _config(cfg)
    if not (jev["enabled"] and jev["base_url"] and jev["api_key"] and jev["model"]):
        return {"source": "heuristic", "configured": False}

    batch_size = max(1, jev["batch_size"])
    updated = 0
    errors = []
    pairs = [(i, by_name.get(i.doc_name)) for i in issues]
    pairs = [(i, d) for i, d in pairs if d is not None]
    for offset in range(0, len(pairs), batch_size):
        batch = pairs[offset:offset + batch_size]
        try:
            result = _jev_batch(batch, jev["base_url"], jev["api_key"], jev["model"], jev["timeout"], cfg)
        except (OSError, ValueError, KeyError, url_error.URLError) as exc:
            errors.append(str(exc))
            continue
        for issue, _doc in batch:
            item = result.get(issue.id)
            if not item:
                continue
            issue.error_probability = _merge_jev_probability(
                item["probability"], issue.error_probability, issue)
            issue.probability_source = "jev"
            issue.probability_reason = item["reason"] + "；" + _memory_summary(
                issue.context_validation or _context_validation(issue, _doc, cfg))
            updated += 1
    return {"source": "jev" if updated else "heuristic", "configured": True,
            "updated": updated, "errors": errors[:3]}
