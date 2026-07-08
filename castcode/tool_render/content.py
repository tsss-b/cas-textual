"""Helpers for opaque tool/MCP result payloads exposed by the Python SDK."""

from castcode.tool_render.sanitize import (
    _base64_size,
    _bounded_lines,
    _clean_line,
    _format_bytes,
    _join_capped,
    _string_size,
)


TITLE_MAX = 118
DETAIL_MAX = 160
DETAIL_LINES = 2
PREVIEW_MAX = 1200
PREVIEW_LINES = 6
ERROR_MAX = 400
ERROR_LINES = 4
_SUMMARY_SCAN_MAX = 20000
_BLOCK_SCAN_MAX = 50
_SIZE_KEYS = ("size", "size_bytes", "bytes", "file_size", "originalSize", "original_size")


def _large_string_summary(key: str, value: str) -> str:
    if len(value) > _SUMMARY_SCAN_MAX:
        return f"{key}: {len(value):,} chars+"
    count = _line_count(value)
    return f"{key}: {count} line{'s' if count != 1 else ''}, {_string_size(value)}"


def _block_size(block: dict) -> int | None:
    for key in _SIZE_KEYS:
        value = block.get(key)
        if isinstance(value, int):
            return value
    for nested in ("source", "file"):
        nested_block = block.get(nested)
        if not isinstance(nested_block, dict):
            continue
        for key in _SIZE_KEYS:
            value = nested_block.get(key)
            if isinstance(value, int):
                return value
        data = nested_block.get("data") or nested_block.get("base64")
        if isinstance(data, str):
            return _base64_size(data)
    data = block.get("data") or block.get("base64") or block.get("image")
    return _base64_size(data) if isinstance(data, str) else None


def _image_like(block: dict) -> bool:
    if block.get("type") == "image" or "image" in block:
        return True
    file = block.get("file")
    return isinstance(file, dict) and (
        "base64" in file or str(file.get("type", "")).startswith("image/")
    )


def _mime(block: dict) -> str:
    for container in (block, block.get("source"), block.get("file")):
        if not isinstance(container, dict):
            continue
        for key in ("media_type", "mime_type", "mimeType"):
            value = container.get(key)
            if value:
                return str(value)
        value = container.get("type")
        if isinstance(value, str) and "/" in value:
            return value
    return "image"


def _image_summary(block: dict) -> str:
    mime = _mime(block)
    size = _format_bytes(_block_size(block))
    return f"image: {mime}, {size}" if size else f"image: {mime}"


def _dict_summary(block: dict) -> str:
    kind = block.get("type")
    if _image_like(block):
        return _image_summary(block)
    keys = [
        str(key) for key in block.keys()
        if key not in ("data", "content", "source", "base64", "image", "file")
    ]
    if kind:
        rest = ", ".join(key for key in keys if key != "type")
        return f"{kind}: {rest}" if rest else str(kind)
    return f"{len(keys)} keys: {', '.join(keys[:4])}" if keys else "object"


def _dict_blocks(content, *, limit: int = _BLOCK_SCAN_MAX):
    yielded = 0
    stack = [content]
    while stack and yielded < limit:
        value = stack.pop()
        if isinstance(value, dict):
            yield value
            yielded += 1
            nested = value.get("content")
            remaining = limit - yielded
            if isinstance(nested, dict) and remaining:
                stack.append(nested)
            elif isinstance(nested, list) and remaining:
                stack.extend(reversed(nested[:remaining]))
        elif isinstance(value, list):
            remaining = limit - yielded
            stack.extend(reversed(value[:remaining]))


def _text_blocks(content):
    for block in _dict_blocks(content):
        if block.get("type") == "text":
            yield str(block.get("text", ""))


def _blocks_summary(content: list, *, max_lines: int = PREVIEW_LINES,
                    max_chars: int = PREVIEW_MAX) -> str:
    lines = []
    for block in content:
        line = _dict_summary(block) if isinstance(block, dict) else type(block).__name__
        lines.append(_clean_line(line, max_chars))
        if len(lines) >= max_lines:
            break
    return _join_capped(lines, max_chars)


def _content_text_preview(content, *, max_lines: int, max_chars: int,
                          first_last: bool = True, collapse: bool = False) -> str:
    if isinstance(content, str):
        return _bounded_lines(
            content,
            max_lines=max_lines,
            max_chars=max_chars,
            first_last=first_last,
            collapse=collapse,
        )
    if isinstance(content, list):
        lines = []
        total = 0
        for text in _text_blocks(content):
            if len(lines) >= max_lines:
                break
            remaining = max_chars - total - (1 if lines else 0)
            if remaining <= 0:
                break
            preview = _bounded_lines(
                text,
                max_lines=max_lines - len(lines),
                max_chars=remaining,
                first_last=first_last,
                collapse=collapse,
            )
            new_lines = preview.splitlines()
            if not new_lines:
                continue
            lines.extend(new_lines)
            total += len(preview) + (1 if total else 0)
            if len(lines) >= max_lines or total >= max_chars:
                break
        return _join_capped(lines[:max_lines], max_chars)
    if isinstance(content, dict) and content.get("type") == "text":
        return _bounded_lines(
            str(content.get("text", "")),
            max_lines=max_lines,
            max_chars=max_chars,
            first_last=first_last,
            collapse=collapse,
        )
    return ""


def _content_preview(content) -> str:
    if isinstance(content, str):
        return _bounded_lines(content, max_lines=PREVIEW_LINES, max_chars=PREVIEW_MAX,
                              first_last=True)
    if isinstance(content, list):
        text = _content_text_preview(
            content,
            max_lines=PREVIEW_LINES,
            max_chars=PREVIEW_MAX,
        )
        if text.strip():
            return text
        return _blocks_summary(content)
    if isinstance(content, dict):
        text = _content_text_preview(
            content,
            max_lines=PREVIEW_LINES,
            max_chars=PREVIEW_MAX,
        )
        if text.strip():
            return text
        return _dict_summary(content)
    return ""


def result_preview(content, tool_use_result: dict | None = None) -> str:
    if tool_use_result:
        preview = _content_preview(tool_use_result)
        if preview:
            return preview
    return _content_preview(content)


def error_preview(content) -> str:
    text = _content_text_preview(
        content,
        max_lines=ERROR_LINES,
        max_chars=ERROR_MAX,
        collapse=True,
    )
    if text:
        return text
    if isinstance(content, list):
        return _blocks_summary(content, max_lines=ERROR_LINES, max_chars=ERROR_MAX)
    return ""


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _bounded_line_count(text: str) -> tuple[int, bool]:
    if not text:
        return 0, True
    exact = len(text) <= _SUMMARY_SCAN_MAX
    sample = text if exact else text[:_SUMMARY_SCAN_MAX]
    count = sample.count("\n") + (0 if sample.endswith("\n") else 1)
    return count, exact


def _read_content_line_count(content) -> tuple[int, bool] | None:
    if isinstance(content, str):
        return _bounded_line_count(content)
    if isinstance(content, list):
        total = 0
        exact = len(content) <= _BLOCK_SCAN_MAX
        found = False
        for block in content[:_BLOCK_SCAN_MAX]:
            if isinstance(block, dict) and block.get("type") == "text":
                found = True
                count, block_exact = _bounded_line_count(str(block.get("text", "")))
                total += count
                exact = exact and block_exact
        return (total, exact) if found else None
    if isinstance(content, dict) and content.get("type") == "text":
        return _bounded_line_count(str(content.get("text", "")))
    if isinstance(content, dict):
        value = content.get("content")
        if isinstance(value, (str, list, dict)):
            return _read_content_line_count(value)
    return None


def _read_line_summary(count: int, exact: bool = True) -> str:
    suffix = "" if exact else "+"
    plural = "s" if count != 1 or not exact else ""
    return f"{count}{suffix} line{plural} read"


def _read_result_line_count(tool_use_result: dict) -> tuple[int, bool] | None:
    metadata = _read_metadata_line_count(tool_use_result)
    if metadata is not None:
        return metadata, True
    if tool_use_result.get("type") == "text":
        return _read_content_line_count(tool_use_result)
    value = tool_use_result.get("content")
    if isinstance(value, (str, list, dict)):
        return _read_content_line_count(value)
    return None


def _read_metadata_line_count(tool_use_result: dict | None) -> int | None:
    if not isinstance(tool_use_result, dict):
        return None
    for key in ("lines_returned", "total_lines"):
        value = tool_use_result.get(key)
        if isinstance(value, int):
            return value
    return None
