import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            # `from castcode import state` names the submodule in the alias, not in
            # node.module — expand it so the boundary can't be spelled around.
            if node.module == "castcode":
                modules.update(f"castcode.{alias.name}" for alias in node.names)
    return modules


def test_ui_imports_stay_sdk_state_and_tool_render_free():
    forbidden = ("claude_agent_sdk", "castcode.state", "castcode.tool_render")
    for path in (ROOT / "castcode" / "ui").rglob("*.py"):
        for name in _imports(path):
            assert not any(
                name == module or name.startswith(module + ".")
                for module in forbidden
            ), (path, name)


def test_session_is_only_sdk_client_surface():
    for path in (ROOT / "castcode").rglob("*.py"):
        if path.name == "session.py":
            continue
        assert "ClaudeSDKClient" not in path.read_text(encoding="utf-8"), path


def test_pure_foundation_modules_keep_imports_narrow():
    expected = {
        "castcode/records.py": {"collections", "dataclasses"},
        "castcode/commands.py": {"dataclasses"},
        "castcode/interaction.py": {"dataclasses"},
        "castcode/rewind.py": {"copy", "dataclasses", "castcode.records"},
    }
    for relative, allowed in expected.items():
        assert _imports(ROOT / relative) <= allowed
