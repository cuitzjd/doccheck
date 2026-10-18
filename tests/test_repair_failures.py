"""Font repair failures must be visible in FixResult instead of being swallowed."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import docx
import yaml

from doccheck.models import Issue, IssueStatus, Severity
from doccheck.repair import apply_fixes


with tempfile.TemporaryDirectory(prefix="doccheck_repair_") as td:
    doc_path = os.path.join(td, "input.docx")
    config_path = os.path.join(td, "config.yaml")
    document = docx.Document()
    document.add_paragraph("需要修复字体")
    document.save(doc_path)
    with open(config_path, "w", encoding="utf-8") as stream:
        yaml.safe_dump({
            "format_baseline": {"body_font_size": "not-a-number"},
            "output": {"backup_dir": os.path.join(td, "backups")},
        }, stream)

    issue = Issue(
        id="FORMAT-01-1", rule_id="FORMAT-01", rule_name="字体",
        category="格式", severity=Severity.MEDIUM, doc_name="input.docx",
        location="段落 0", original="需要修复字体",
    )
    result = apply_fixes(doc_path, [issue], config_path)

    assert not result.succeeded
    assert result.failed and result.failed[0]["id"] == issue.id
    assert issue.status == IssueStatus.FIX_FAILED
print("Repair failure tests passed")
