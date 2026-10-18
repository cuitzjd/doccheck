"""OCR subprocess failures must not be mistaken for an empty page."""
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doccheck.ocr import _ocr_image


failed = SimpleNamespace(returncode=1, stdout="", stderr="tesseract failed")
with patch("doccheck.ocr.subprocess.run", return_value=failed):
    try:
        _ocr_image("tesseract", "/tmp/page.png")
    except RuntimeError as exc:
        assert "exit code 1" in str(exc)
        assert "tesseract failed" in str(exc)
    else:
        raise AssertionError("OCR failure was not propagated")
print("OCR failure tests passed")
