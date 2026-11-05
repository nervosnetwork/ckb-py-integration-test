"""Small input helpers; execution uses the project's normal pytest entry point."""

import hashlib
import json
from pathlib import Path

from framework.util import get_project_root

ROOT = Path(get_project_root())
BASELINE = ROOT / "test_cases/network_verification/fixtures/vm-cycle-baseline.json"


def script_path(name):
    """Check the fixed ELF input without regenerating expected VM cycles."""
    expected = json.loads(BASELINE.read_text())["script_sha256"]
    assert name in expected, name
    path = ROOT / "source/contract" / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected[name], name
    return path
