"""检查报告导出（REPORT-02）：Excel / JSON。"""
import json

from .models import Issue

_COLUMNS = [("规则编号", "rule_id"), ("规则名称", "rule_name"), ("类别", "category"),
            ("级别", "severity"), ("文档", "doc_name"), ("定位", "location"),
            ("原文", "original"), ("基准值", "expected"), ("建议", "suggestion"),
            ("状态", "status"), ("实际错误概率", "error_probability"),
            ("评分来源", "probability_source"), ("概率依据", "probability_reason")]


def export_excel(issues: list, out_path: str) -> str:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "问题清单"
    ws.append([c for c, _ in _COLUMNS])
    for i in issues:
        d = i.to_dict() if isinstance(i, Issue) else i
        ws.append([d.get(k, "") for _, k in _COLUMNS])
    for col, width in zip("ABCDEFGHIJKLM", (10, 20, 8, 6, 28, 18, 50, 40, 40, 8, 12, 14, 42)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    # 高级别置顶汇总表
    ws2 = wb.create_sheet("高级别汇总")
    ws2.append(["文档", "规则编号", "定位", "原文", "基准值"])
    for i in issues:
        d = i.to_dict() if isinstance(i, Issue) else i
        if d.get("severity") == "高":
            ws2.append([d.get("doc_name"), d.get("rule_id"), d.get("location"),
                        d.get("original"), d.get("expected")])
    wb.save(out_path)
    return out_path


def export_json(issues: list, out_path: str) -> str:
    data = [i.to_dict() if isinstance(i, Issue) else i for i in issues]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return out_path
