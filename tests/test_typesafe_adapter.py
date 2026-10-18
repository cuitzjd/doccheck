"""TypeSafe Jev native protocol checks."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doccheck.probability import _jev_endpoint, _parse_jev_response


payload = {
    "model": "jev-1.13.0",
    "answers": {
        "CONTRACT-01-1": {"type": "noul", "noul": 0.91, "confidence": 0.88},
        "STAGE-01-1": {"type": "noul", "noul": 0.04},
    },
}
parsed = _parse_jev_response(payload)
assert parsed["CONTRACT-01-1"]["probability"] == 0.91
assert "置信度 88%" in parsed["CONTRACT-01-1"]["reason"]
assert parsed["STAGE-01-1"]["probability"] == 0.04
assert _jev_endpoint("https://api.typesafe.ai/v1") == "https://api.typesafe.ai/v1/systemone"
assert _jev_endpoint("https://api.typesafe.ai/v1/systemone") == "https://api.typesafe.ai/v1/systemone"
print("TypeSafe Jev adapter tests passed")
