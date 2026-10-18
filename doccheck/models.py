"""数据模型：问题项、文档对象、合同基准。所有规则与 UI 共用此模型。"""
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"


class IssueStatus(str, Enum):
    PENDING = "待判定"      # 待用户判定
    CONFIRMED = "是问题"    # 用户确认，进入修复队列
    FALSE_ALARM = "误报"
    IGNORED = "忽略"
    FIXED = "已修复"
    FIX_FAILED = "修复失败"
    SKIPPED = "跳过修复"    # 不可自动修复（需人工）


@dataclass
class Issue:
    """一条检查发现的问题项。id 由引擎分配（规则编号-序号）。"""
    id: str
    rule_id: str            # 如 PAGE-01
    rule_name: str
    category: str           # 页码/格式/标点/合同/依据/阶段/身份
    severity: Severity
    doc_name: str           # 文档文件名
    location: str           # 定位：页码/段落号/章节
    original: str           # 原文片段
    expected: str = ""      # 基准值/期望值
    suggestion: str = ""    # 修改建议
    fixable: bool = False   # 是否支持自动修复
    status: IssueStatus = IssueStatus.PENDING
    fix_detail: str = ""    # 修复结果说明（修复前后对照）
    error_probability: float = 0.5  # 结合上下文判断为实际错误的概率
    probability_source: str = "heuristic"  # heuristic / jev
    probability_reason: str = ""  # 评分依据，供人工复核
    context_validation: dict = field(default_factory=dict)  # 长期记忆与短期上下文的交叉验证

    def to_dict(self):
        d = asdict(self)
        d["severity"] = self.severity.value
        d["status"] = self.status.value
        return d


@dataclass
class Paragraph:
    """统一段落模型（docx/pdf 共用）。page 对 docx 为估算值或 -1。"""
    index: int              # 段落序号
    text: str
    page: int = -1          # 所在页（pdf 精确；docx 估算，-1 未知）
    style: str = ""         # 样式名（docx），如 "Heading 1"
    font_cn: str = ""       # 中文字体（已沿样式链解析后的生效值）
    font_en: str = ""
    font_size: float = 0.0  # 磅
    bold: bool = False
    is_heading: bool = False
    is_table_cell: bool = False
    alignment: str = ""
    first_line_indent: Optional[float] = None
    line_spacing: Optional[float] = None
    # DOCX paragraph properties. PDF cannot provide these reliably.
    num_id: str = ""
    num_level: int = -1
    is_list_item: bool = False
    keep_with_next: bool = False
    page_break_before: bool = False
    outline_level: int = -1


@dataclass
class UnifiedDoc:
    """统一文档模型。规则只面向它编程，不关心源格式。"""
    path: str
    name: str                       # 文件名
    doc_type: str                   # 文档类型（由文件名前缀识别），如 需求规格说明书
    fmt: str                        # docx / doc / pdf
    page_count: int = 0             # 物理页数（pdf 精确；docx 由分节/分页符估算，0 未知）
    paragraphs: list = field(default_factory=list)   # list[Paragraph]
    footer_page_numbers: list = field(default_factory=list)  # pdf: 每页页脚解析出的页码 [(物理页, 标注页码)]
    footer_page_totals: list = field(default_factory=list)    # pdf: 页脚解析出的总页数 [(物理页, 总页数)]
    footer_page_fractions: list = field(default_factory=list)  # pdf: X/X 页脚 [(物理页, 当前页, 总页数)]
    toc_entries: list = field(default_factory=list)          # 目录条目 [(标题, 页码)]
    cover_text: str = ""            # 封面文本
    paired_pdf: str = ""            # 同名盖章 PDF 路径（用于页码类检查）
    meta: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n".join(p.text for p in self.paragraphs)


@dataclass
class ContractProfile:
    """合同关键信息（页面1 抽取 → 用户确认 → 入库 config.yaml）。"""
    project_name: str = ""
    employer: str = ""
    contractors: list = field(default_factory=list)
    supervisor: str = ""
    amount_number: str = ""
    amount_cn: str = ""
    payment_terms: list = field(default_factory=list)
    service_period: str = ""
    contract_no: str = ""
    confidence: dict = field(default_factory=dict)  # 字段 → 置信度说明/OCR 低置信标记
    source: str = ""                                # 来源文件
    needs_review: bool = True                       # 含 OCR 低置信字段时为 True

    def to_dict(self):
        return asdict(self)
