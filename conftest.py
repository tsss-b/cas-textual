import pathlib
import sys

import pytest

# Ensure the repo root (where app.py lives) is importable as `import app`,
# and tests/ (where fixtures.py lives) is importable as `import fixtures`.
_ROOT = pathlib.Path(__file__).resolve().parent
for p in (_ROOT, _ROOT / "tests"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


@pytest.fixture(autouse=True)
def _isolate_claude_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
