"""合同关键信息抽取：合同文件 → ContractProfile。

流程：有文本层直接解析；无文本层（扫描件）走 ocr.ocr_pdf 本地 OCR；
都失败返回空 profile 且 needs_review=True。OCR 来源一律 needs_review=True，
要求用户核对关键字段后入库（规格书 CONVERT-02/03）。
"""
import os
import re

from . import docloader, ocr
from .models import ContractProfile

_ORG_SUFFIX = r"(?:有限公司|股份有限公司|研究院|设计院|测绘院|集团有限公司|公司)"
_CN_NUM = "〇零壹贰叁肆伍陆柒捌玖拾佰仟万亿圆角分"


def _clean(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _grab_after(lines, start_re, stop_re, max_lines=4):
    """从命中 start_re 的行开始收集字段文本（同行余量 + 后续行），遇 stop_re 停。"""
    out = []
    grabbing = False
    for line in lines:
        if not grabbing:
            m = start_re.search(line)
            if m:
                grabbing = True
                rest = line[m.end():].strip(" ：:")
                if rest:
                    out.append(rest)
        else:
            if stop_re.search(line):
                break
            out.append(line.strip())
            if len(out) >= max_lines:
                break
    return _clean("".join(out))


def _extract_fields(text: str, profile: ContractProfile):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    joined = "\n".join(lines)

    m = re.search(r"合同编号[:：]?\s*([A-Za-z0-9\-]{4,})", joined)
    if m:
        profile.contract_no = m.group(1)
        profile.confidence["contract_no"] = m.group(0)

    # 项目名称：封面"项目名称："后至"委托方"前
    v = _grab_after(lines, re.compile(r"[顶项]目名称[:：]"),
                    re.compile(r"委托方|受托方|签订"))
    if v:
        v = re.sub(r"[（(]\s*", "（", v)
        v = re.sub(r"\s*[)）]", "）", v)
        profile.project_name = v
        profile.confidence["project_name"] = v[:60]

    v = _grab_after(lines, re.compile(r"委托方[（(]甲.{0,2}[)）][:：]?"),
                    re.compile(r"受托方|签订"))
    if v:
        profile.employer = v
        profile.confidence["employer"] = v[:60]

    # 受托方（乙方）可多家（联合体）；只取封面区（"签订日期"之前），
    # 正文条款中的"受托方/乙方"泛指不是单位名
    cover_end = next((i for i, l in enumerate(lines) if re.search(r"签订日期|签订地点", l)),
                     min(len(lines), 60))
    contractors = []
    for i, line in enumerate(lines[:cover_end]):
        m0 = re.search(r"受托方[（(]乙", line)
        if m0:
            m1 = re.search(r"受托方[（(]乙.{0,3}[)）][:：]?", line)
            seg = _clean(line[m1.end():] if m1 else line[m0.end():])
            # 单位名被 OCR 断行时拼接下一行（未以"公司"结尾且下行不是新字段）
            j = i + 1
            while (j < cover_end and not seg.endswith("公司")
                   and not re.match(r"^(受托方|委托方|签订|法定|联系人)", lines[j])):
                seg += _clean(lines[j])
                j += 1
            seg = re.sub(r"(?:合同专用章|盖章|印章).*$", "", seg)
            if len(seg) >= 6 and seg not in contractors:
                contractors.append(seg)
    if contractors:
        profile.contractors = contractors
        profile.confidence["contractors"] = "；".join(contractors)[:80]

    # 监理单位：合同中多为"由甲方另行指定"，尝试找具名
    m = re.search(r"监理单位[是为：:]\s*([一-龥]{4,}?" + _ORG_SUFFIX + ")", joined)
    if m:
        profile.supervisor = m.group(1)
        profile.confidence["supervisor"] = m.group(0)

    # 合同金额：优先"合同总金额为（大写）"句（容忍 OCR 空格噪声）
    m = re.search(r"合同总金额[为是]?[（(]?大写[)）]?[:：]?\s*人民币?((?:[" + _CN_NUM + r"\s])+元[整]?)", joined)
    if m:
        profile.amount_cn = m.group(1)
        profile.confidence["amount_cn"] = m.group(0)[:60]
    m = re.search(r"[¥￥]\s*([\d,]+(?:\.\d+)?)\s*元?", joined)
    if m:
        profile.amount_number = re.sub(r"\.0+$", "", m.group(1).replace(",", ""))
        profile.confidence["amount_number"] = m.group(0)

    # 付款条件：提取"第N期款/首期款 … 合同总价/总额的 NN%"句式
    terms = []
    for m in re.finditer(r"[（(]\d+[)）]\s*((?:首期款|第[一二三四五六]期款|尾期款|尾款)[^。]{0,120}?)(\d+)\s*[%％]", joined):
        seg = re.sub(r"\s+", "", m.group(1))
        terms.append(f"{seg[:40]}…{m.group(2)}%")
    if not terms:
        for m in re.finditer(r"(支付[^。]{0,40}?)(\d+)\s*[%％]", joined):
            terms.append(f"{re.sub(r' ', '', m.group(1))[:40]}…{m.group(2)}%")
    if terms:
        profile.payment_terms = terms[:8]
        profile.confidence["payment_terms"] = f"共{len(terms)}个付款节点"

    # 服务期限：优先含具体期限表述的条款（避开术语定义条）
    cands = [m.group(1) for m in
             re.finditer(r"服务期限[^。]{0,20}?[:：]?\s*([^。]{4,80}。)", joined)]
    pick = next((c for c in cands if re.search(r"个月|自.*日|至.*日|\d{4}\s*年", c)),
                cands[-1] if cands else "")
    if pick:
        profile.service_period = re.sub(r"\s+", "", pick)[:80]
        profile.confidence["service_period"] = pick[:60]


def _has_text_layer(pdf_path: str) -> bool:
    import fitz
    pdf = fitz.open(pdf_path)
    n = sum(len(pdf[i].get_text().strip()) for i in range(min(5, pdf.page_count)))
    pdf.close()
    return n > 200


def extract(path: str, max_ocr_pages: int = 0) -> ContractProfile:
    """从合同文件抽取关键字段。max_ocr_pages>0 时限制 OCR 页数（调试用）。"""
    profile = ContractProfile(source=path, needs_review=True)
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            if _has_text_layer(path):
                text = docloader.load(path).full_text
                ocr_used = False
            else:
                backend = ocr.available()
                if not backend:
                    return profile  # 无文本层且无 OCR 后端：空 profile
                pages = range(max_ocr_pages) if max_ocr_pages else None
                text = ocr.ocr_pdf(path, pages=pages)
                ocr_used = True
                if not text.strip():
                    return profile
        elif ext in (".doc", ".docx"):
            text = docloader.load(path).full_text
            ocr_used = False
        else:
            return profile
    except Exception as e:
        profile.confidence["error"] = str(e)
        return profile

    _extract_fields(text, profile)
    if not ocr_used and (profile.employer or profile.contractors):
        profile.needs_review = False
    else:
        # OCR 来源：单位名/金额须人工核对一次方可入库
        profile.needs_review = True
        profile.confidence["ocr"] = "OCR 抽取，关键字段（单位名/金额/付款条件）须人工核对"
    return profile
