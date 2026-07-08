import os
from importlib.resources import files

from textual.color import Gradient
from rich.text import Text


BANNER_GAP = ""
LOGO_CAST = files("castcode.assets").joinpath("logo1.txt").read_text(encoding="utf-8")
LOGO_CODE = files("castcode.assets").joinpath("logo2.txt").read_text(encoding="utf-8")


def _art_lines(art: str) -> list[str]:
    lines = [line.rstrip() for line in art.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _art_width(lines: list[str]) -> int:
    return max(len(line) for line in lines)


def _join_art(left: str, right: str) -> str:
    left_lines = _art_lines(left)
    right_lines = _art_lines(right)
    left_width = _art_width(left_lines)
    height = max(len(left_lines), len(right_lines))
    rows = []
    for i in range(height):
        left_line = left_lines[i] if i < len(left_lines) else ""
        right_line = right_lines[i] if i < len(right_lines) else ""
        rows.append(f"{left_line.ljust(left_width)}{BANNER_GAP}{right_line}".rstrip())
    return "\n".join(rows)


def _stack_art(top: str, bottom: str) -> str:
    return "\n".join(_art_lines(top) + [""] + _art_lines(bottom))


BANNER = _join_art(LOGO_CAST, LOGO_CODE)
BANNER_STACKED = _stack_art(LOGO_CAST, LOGO_CODE)
BANNER_WIDTH = _art_width(BANNER.splitlines())


def banner_for_width(width: int | None) -> str:
    if width is None or width >= BANNER_WIDTH:
        return BANNER
    return BANNER_STACKED


def gradient_banner(color_a: str, color_b: str, *, width: int | None = None) -> Text:
    lines = banner_for_width(width).split("\n")
    art_width = max(len(line) for line in lines)
    gradient = Gradient.from_colors(color_a, color_b)
    text = Text(no_wrap=True, overflow="crop")
    last = len(lines) - 1
    for i, line in enumerate(lines):
        for col, char in enumerate(line):
            text.append(char, style=gradient.get_color(col / max(art_width - 1, 1)).hex)
        if i < last:
            text.append("\n")
    return text


_HOME = os.path.expanduser("~")
CWD = os.getcwd()


def tilde_path(path: str) -> str:
    if path == _HOME:
        return "~"
    if path.startswith(_HOME + os.sep):
        return "~" + path[len(_HOME):]
    return path


def rel_path(path: str) -> str:
    if path == CWD:
        return "."
    if path.startswith(CWD + os.sep):
        return path[len(CWD) + 1:]
    return tilde_path(path)


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def format_elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s"


def model_label(info: dict) -> str | None:
    models = info.get("models") or []
    if not models or not isinstance(models[0], dict):
        return None
    model = models[0]
    label = str(model.get("description") or "").split("·")[0].strip()
    return label or str(model.get("displayName") or "") or None
