from rich.text import Text


def visible_window(rows, index: int, size: int):
    rows = list(rows)
    if len(rows) <= size:
        return 0, rows
    start = max(0, min(index - size + 1, len(rows) - size))
    return start, rows[start:start + size]


def line_text(value) -> str:
    return " ".join(str(value or "").split())


def selector_lines(
    rows,
    index: int,
    *,
    title_key: str,
    fallback_key: str = "",
    detail_key: str = "",
    size: int | None = None,
    section_labels: dict[str, str] | None = None,
    section_style: str | None = None,
    section_prefix: str = "  - ",
    section_suffix: str = "",
) -> str | Text:
    rows = list(rows)
    if size is None:
        start, visible = 0, rows
    else:
        start, visible = visible_window(rows, index, size)
    lines = []
    text = Text() if section_style else None
    last_section = None
    def append_line(value: str, style: str | None = None) -> None:
        if text is None:
            lines.append(value)
            return
        if text.plain:
            text.append("\n")
        text.append(value, style=style)

    for offset, row in enumerate(visible):
        row_index = start + offset
        source = row.get("source", "")
        if section_labels and source != last_section:
            label = section_labels.get(source)
            if label:
                append_line(
                    f"{section_prefix}{line_text(label)}{section_suffix}",
                    section_style,
                )
            last_section = source
        marker = "›" if row_index == index else " "
        title = line_text(row.get(title_key) or row.get(fallback_key, ""))
        detail = line_text(row.get(detail_key, "")) if detail_key else ""
        suffix = f"  {detail}" if detail else ""
        append_line(f"{marker} {title}{suffix}")
    return text if text is not None else "\n".join(lines)
