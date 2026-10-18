"""身份表述检查 IDENTITY-01~04。

按合同基准字段建立"单位名 → 角色"映射；含人称代词/角色词的句子做归属分析，
拿不准的一律标记待人工裁决（规格书 3.11）。
"""
import re

from ..models import Severity
from ..registry import rule, make_issue
from .common import location_of, split_sentences

_FIRST_PERSON = re.compile(r"我们|我院|我司|我方|本单位|我公司")
_DIRECTIVE = re.compile(r"应当|必须|不得|务必|须按|按要求|予以|责令|接受.{0,6}考核|限期|整改")
_EXEMPT_REF = re.compile(r"合同|依据|根据|约定|条款|规定")


def _role_map(ctx):
    """单位名 → 角色；另返回 (employer, contractors, supervisor)。"""
    c = ctx.contract
    if not c:
        return {}
    m = {}
    if c.employer:
        m[c.employer] = "建设单位"
    for x in c.contractors or []:
        if x:
            m[x] = "承建单位"
    if c.supervisor:
        m[c.supervisor] = "监理单位"
    return m


def _sentences(doc):
    for p in doc.paragraphs:
        for s in split_sentences(p.text):
            if len(s) >= 6:
                yield p, s


def _names_in(sent, role_map):
    return [(n, r) for n, r in role_map.items() if n in sent]


@rule("IDENTITY-01", "第一人称归属检查", "身份", Severity.HIGH)
def check_first_person(doc, ctx):
    role_map = _role_map(ctx)
    employer = ctx.contract.employer if ctx.contract else ""
    if not employer:
        return
    for p, s in _sentences(doc):
        if not _FIRST_PERSON.search(s):
            continue
        bound = False
        # 第一人称与建设单位/甲方显式绑定：我们…是建设单位 / 我院（甲方）等
        if re.search(r"(我们|我院|我司|我方|本单位).{0,12}(建设单位|业主|甲方)", s) or \
           re.search(r"(建设单位|业主|甲方).{0,8}(我们|我院|我司|我方|本单位)", s):
            bound = True
        # 第一人称与建设单位全称同句
        if employer in s:
            bound = True
        if bound:
            yield make_issue(
                "IDENTITY-01", doc, location_of(p), s[:80],
                expected=f"第一人称必须指向承建单位（{'、'.join(ctx.contract.contractors)}）",
                suggestion="第一人称疑似指向建设单位（甲方），身份归属错误，人工裁决")


@rule("IDENTITY-02", "角色错位检查", "身份", Severity.HIGH)
def check_role_swap(doc, ctx):
    role_map = _role_map(ctx)
    if not role_map:
        return
    wrong_role = {"建设单位": r"承建单位|乙方|受托方|施工单位",
                  "承建单位": r"建设单位|业主单位|业主|甲方|委托方",
                  "监理单位": r"承建单位|乙方|建设单位|甲方"}
    for p, s in _sentences(doc):
        for name, role in _names_in(s, role_map):
            pat = wrong_role.get(role)
            if not pat:
                continue
            # "X公司作为建设单位" / "建设单位X公司" 等搭配
            if re.search(re.escape(name) + r"[（(]?(?:作为|系|为)?[^。；，]{0,6}(?:" + pat + ")", s) or \
               re.search(r"(?:" + pat + r")[（(]?" + re.escape(name), s):
                yield make_issue(
                    "IDENTITY-02", doc, location_of(p), s[:80],
                    expected=f"「{name}」的角色应为{role}",
                    suggestion="己方/对方角色错位，人工裁决")


@rule("IDENTITY-03", "甲方口吻检查", "身份", Severity.HIGH)
def check_employer_tone(doc, ctx):
    role_map = _role_map(ctx)
    if not role_map:
        return
    in_basis_section = False
    for p in doc.paragraphs:
        if p.is_heading:
            in_basis_section = bool(re.search(r"编制依据|参考文件|合同条款|引用", p.text))
        for s in split_sentences(p.text):
            if len(s) < 8 or in_basis_section:
                continue
            if _EXEMPT_REF.search(s):
                continue  # 合同条款引用语境豁免
            if re.search(r"承建单位|乙方|受托方", s) and _DIRECTIVE.search(s):
                # 排除乙方自述职责（"乙方应…"为引用合同义务的客观陈述时通常含"合同"，已豁免）
                yield make_issue(
                    "IDENTITY-03", doc, location_of(p), s[:80],
                    expected="交付文档应以乙方口吻行文，不应对承建单位发指令/考核",
                    suggestion="疑似甲方口吻（指令/考核性表述），人工裁决")


@rule("IDENTITY-04", "第三人称一致性", "身份", Severity.MEDIUM)
def check_third_person_role(doc, ctx):
    role_map = _role_map(ctx)
    if not role_map:
        return
    role_words = {"建设单位": r"建设单位|业主单位|业主|甲方|委托方",
                  "承建单位": r"承建单位|乙方|受托方",
                  "监理单位": r"监理单位|监理方"}
    for p, s in _sentences(doc):
        hits = _names_in(s, role_map)
        if not hits:
            continue
        mentioned_roles = set()
        for role, pat in role_words.items():
            if re.search(pat, s):
                mentioned_roles.add(role)
        for name, role in hits:
            wrong = mentioned_roles - {role}
            # 同句出现多家单位时角色词可能各指各家，仅在句中仅此一家单位时判定
            others = [n for n, r in hits if n != name]
            if wrong and not any(re.search(rw, s) and o in s
                                 for o in others for rw in [role_words[role_map[o]]]):
                yield make_issue(
                    "IDENTITY-04", doc, location_of(p), s[:80],
                    expected=f"「{name}」应搭配角色 {role}",
                    suggestion=f"单位名与角色搭配疑似错位（句中出现 {'/'.join(sorted(wrong))}），人工裁决")
