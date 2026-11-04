"""Write the API's OpenAPI spec to openapi.json at the repo root. Doesn't need portal credentials.

Usage:  python scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402

spec = create_app().openapi()
(ROOT / "openapi.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Wrote {ROOT / 'openapi.json'} ({len(spec['paths'])} paths)")
