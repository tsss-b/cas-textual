from castcode.tool_render.content import (
    DETAIL_LINES,
    DETAIL_MAX,
    ERROR_LINES,
    ERROR_MAX,
    PREVIEW_LINES,
    PREVIEW_MAX,
    TITLE_MAX,
    error_preview,
    result_preview,
)
from castcode.tool_render.registry import RENDERERS
from castcode.tool_render.renderers import (
    render_generic_input,
    render_read_result,
    tool_result_soft_error,
)
from castcode.tool_render.sanitize import one_line
from castcode.tool_render.types import BlockPreview, ToolDisplay, ToolPreview


def render_tool_input(name: str, tool_input: dict | None = None) -> ToolDisplay:
    if tool_input is None:
        return ToolDisplay(title=one_line(name, TITLE_MAX))
    record = RENDERERS.get(name)
    if record and record.input:
        return record.input(tool_input)
    return render_generic_input(name, tool_input)


def render_tool_result(
    name: str, content, tool_use_result: dict | None = None
) -> ToolPreview:
    record = RENDERERS.get(name)
    if record and record.result:
        return record.result(content, tool_use_result)
    return result_preview(content, tool_use_result)


def render_tool_result_detail(
    name: str, content, tool_use_result: dict | None = None
) -> str:
    record = RENDERERS.get(name)
    if record and record.detail:
        return record.detail(content, tool_use_result)
    return ""


def render_tool_result_soft_error(
    name: str, content, tool_use_result: dict | None = None
) -> bool:
    return tool_result_soft_error(name, content, tool_use_result)


def render_tool_error(content) -> str:
    return error_preview(content)


__all__ = [
    "ToolDisplay",
    "ToolPreview",
    "BlockPreview",
    "TITLE_MAX",
    "DETAIL_MAX",
    "DETAIL_LINES",
    "PREVIEW_MAX",
    "PREVIEW_LINES",
    "ERROR_MAX",
    "ERROR_LINES",
    "render_read_result",
    "render_tool_input",
    "render_tool_result",
    "render_tool_result_detail",
    "render_tool_result_soft_error",
    "render_tool_error",
]
