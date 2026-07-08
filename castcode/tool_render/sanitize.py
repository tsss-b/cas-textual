from collections import deque
import re


_LINE_SCAN_EXTRA = 256
_BASE64_SCAN_MAX = 8192
_TAIL_SCAN_MAX = 50

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ERROR_WRAPPER_RE = re.compile(r"</?(?:tool_use_error|error)(?:\s[^>]*)?>")


def _crop(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _clean_line(line: str, limit: int, *, collapse: bool = False,
                truncated: bool = False) -> str:
    if limit <= 0:
        return ""
    line = _ERROR_WRAPPER_RE.sub("", line)
    line = _ANSI_RE.sub("", line)
    line = line.replace("\r", "").replace("\t", "    ")
    line = _CONTROL_RE.sub("", line)
    line = " ".join(line.split()) if collapse else line.rstrip()
    if truncated:
        line = _crop(line + "…", limit)
    return _crop(line, limit)


def _raw_lines(text: object, *, line_limit: int | None = None):
    # When line_limit is set, each `find` only scans a window of line_limit+1
    # chars, so a huge single line is cropped without ever scanning the whole
    # payload for a "\n" that may not exist.
    text = text if isinstance(text, str) else str(text)
    start = 0
    while start < len(text):
        if line_limit is None:
            end = text.find("\n", start)
        else:
            scan_end = min(len(text), start + line_limit + 1)
            end = text.find("\n", start, scan_end)
            if end == -1 and scan_end < len(text):
                yield text[start:start + line_limit], True
                break
        raw_end = len(text) if end == -1 else end
        if line_limit is not None and raw_end - start > line_limit:
            yield text[start:start + line_limit], True
        else:
            yield text[start:raw_end], False
        if end == -1:
            break
        start = end + 1


def _join_capped(lines: list[str], max_chars: int) -> str:
    # Invariant: len("\n".join(out)) <= max_chars. The "\n" that will precede
    # this line in the final join is one char, charged exactly once per gap:
    #   - reserved up front via `remaining` (1 if out else 0), so the crop fits
    #     the line *plus* its separator within max_chars, and
    #   - added to the running `total` via (1 if len(out) > 1 else 0).
    # Both guards fire on the same lines (every line after the first), so the
    # separator is reserved and counted exactly once — never double-charged,
    # never dropped.
    out = []
    total = 0
    for line in lines:
        remaining = max_chars - total - (1 if out else 0)
        if remaining <= 0:
            break
        cropped = _crop(line, remaining)
        out.append(cropped)
        total += len(cropped) + (1 if len(out) > 1 else 0)
        if total >= max_chars:
            break
    return "\n".join(out)


def _scan_clean(text: object, *, max_lines: int | None, max_chars: int,
                strip: bool = False, collapse: bool = False):
    """Bounded forward scan → (lines, overflow); never reads huge payloads whole."""
    lines = []
    total = 0
    overflow = False
    for raw, truncated in _raw_lines(text, line_limit=max_chars + _LINE_SCAN_EXTRA):
        if max_lines is not None and len(lines) >= max_lines:
            probe = _clean_line(raw, 1, collapse=collapse, truncated=truncated)
            overflow = bool(probe.strip())
            break
        remaining = max_chars - total - (1 if lines else 0)
        if remaining <= 0:
            overflow = True
            break
        source = raw.strip() if strip else raw
        line = _clean_line(source, remaining, collapse=collapse, truncated=truncated)
        if not line.strip():
            continue
        lines.append(line)
        total += len(line) + (1 if len(lines) > 1 else 0)
        if total >= max_chars:
            break
    return lines, overflow


def one_line(text: object, limit: int) -> str:
    lines, _ = _scan_clean(text, max_lines=None, max_chars=limit, strip=True)
    return _crop(" ".join(lines), limit)


def _bounded_lines_with_overflow(text: object, *, max_lines: int, max_chars: int,
                                 first_last: bool = False,
                                 collapse: bool = False) -> tuple[str, bool]:
    if max_lines <= 0 or max_chars <= 0:
        return "", False
    lines, overflow = _scan_clean(
        text, max_lines=max_lines, max_chars=max_chars, collapse=collapse
    )
    truncated = overflow or any(line.endswith("…") for line in lines)
    if first_last and overflow and max_lines >= 4:
        head_count = min(3, max_lines - 2)
        tail_count = max_lines - head_count - 1
        tail = _tail_lines(text, tail_count, max_chars=max_chars, collapse=collapse)
        return _join_capped(lines[:head_count] + ["…"] + tail, max_chars), True
    if first_last and overflow and lines:
        lines[-1] = "…"
    return _join_capped(lines[:max_lines], max_chars), truncated


def _bounded_lines(text: object, *, max_lines: int, max_chars: int,
                   first_last: bool = False, collapse: bool = False) -> str:
    return _bounded_lines_with_overflow(
        text, max_lines=max_lines, max_chars=max_chars,
        first_last=first_last, collapse=collapse,
    )[0]


def _tail_lines(text: object, count: int, *, max_chars: int,
                collapse: bool = False) -> list[str]:
    if count <= 0:
        return []
    text = text if isinstance(text, str) else str(text)
    lines = deque(maxlen=count)
    end = len(text)
    line_limit = max_chars + _LINE_SCAN_EXTRA
    # Each rfind scans at most a line_limit+1 window, and _TAIL_SCAN_MAX caps the
    # total number of scans, so tailing stays bounded even on a huge single line.
    scans = 0
    while end > 0 and len(lines) < count and scans < _TAIL_SCAN_MAX:
        scans += 1
        search_start = max(0, end - line_limit - 1)
        start = text.rfind("\n", search_start, end)
        raw_start = 0 if start == -1 else start + 1
        head_cut = start == -1 and search_start > 0
        if head_cut:
            raw_start = end - line_limit
        truncated = head_cut or end - raw_start > line_limit
        raw_end = min(end, raw_start + line_limit) if truncated else end
        line = _clean_line(
            text[raw_start:raw_end],
            max_chars,
            collapse=collapse,
            truncated=truncated,
        )
        if line.strip():
            lines.appendleft(line)
        if start == -1:
            break
        end = start
    return list(lines)


def _format_bytes(size: int | None) -> str:
    if size is None:
        return ""
    units = ("B", "KB", "MB")
    amount = float(size)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    if unit == "B":
        return f"{int(amount)} B"
    return f"{amount:.1f} {unit}"


def _string_size(text: str) -> str:
    return _format_bytes(len(text.encode()))


def _base64_size(data: str) -> int | None:
    if len(data) > _BASE64_SCAN_MAX:
        return None
    length = 0
    padding = 0
    for char in data:
        if char.isspace():
            continue
        length += 1
        if char == "=":
            padding += 1
        else:
            padding = 0
    return max(0, (length * 3) // 4 - padding)
