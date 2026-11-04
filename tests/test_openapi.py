import json
from pathlib import Path

from app.main import create_app

SPEC = Path(__file__).parent.parent / "openapi.json"


def test_committed_openapi_json_is_up_to_date():
    """Fails when the API changes without regenerating the spec: run `python scripts/export_openapi.py`."""
    committed = json.loads(SPEC.read_text(encoding="utf-8"))
    assert committed == create_app().openapi()
