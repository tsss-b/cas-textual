import re

from castcode.format import rel_path
from castcode.tool_render.content import (
    DETAIL_LINES,
    DETAIL_MAX,
    PREVIEW_LINES,
    PREVIEW_MAX,
    TITLE_MAX,
    _content_preview,
    _dict_blocks,
    _image_like,
    _image_summary,
    _large_string_summary,
    _read_content_line_count,
    _read_line_summary,
    _read_metadata_line_count,
    _read_result_line_count,
    _text_blocks,
)
from castcode.tool_render.sanitize import (
    _bounded_lines,
    _bounded_lines_with_overflow,
    _clean_line,
    _join_capped,
    _raw_lines,
    one_line,
)
from castcode.tool_render.types import BlockPreview, ToolDisplay


_INPUT_KEY = {
    "Read": "file_path", "Write": "file_path", "Edit": "file_path",
    "NotebookEdit": "notebook_path",
    "Bash": "command", "Grep": "pattern", "Glob": "pattern",
    "WebFetch": "url", "WebSearch": "query",
    "web_search": "query", "web_fetch": "url",
}
_LARGE_INPUT_KEYS = {
    "base64", "blob", "body", "content", "data", "image", "new_source",
    "new_string", "old_string", "payload", "plan", "prompt", "script",
}
_CHANGE_ROWS = 7
_CHANGE_TEXT_MAX = 220
_BASH_DETAIL_LINES = 4
_BASH_DETAIL_MAX = 320
_BASH_PREVIEW_LINES = 4
_AGENT_PREVIEW_LINES = 5
_ASK_QUESTION_ROWS = 3
_TASK_CREATE_RE = re.compile(
    r"Task #(?P<id>\S+) created successfully:\s*(?P<subject>.+)"
)
_TOOL_SEARCH_NO_MATCH = "no matching deferred tools found"


def _path_detail(tool_input: dict, key: str = "file_path") -> str:
    path = tool_input.get(key)
    return _bounded_lines(
        rel_path(str(path)) if path else "",
        max_lines=DETAIL_LINES,
        max_chars=DETAIL_MAX,
    )


def _read_detail(tool_input: dict) -> str:
    parts = []
    path = tool_input.get("file_path")
    if path:
        parts.append(rel_path(str(path)))
    for key in ("offset", "limit", "pages"):
        value = tool_input.get(key)
        if value not in (None, "", []):
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            parts.append(f"{key} {value}")
    return _bounded_lines(" · ".join(parts), max_lines=DETAIL_LINES, max_chars=DETAIL_MAX)


def render_read_input(tool_input: dict) -> ToolDisplay:
    return ToolDisplay(title="Read", detail=_read_detail(tool_input))


def _change_row(kind: str, line: object, marker: str, text: object) -> dict[str, str]:
    return {
        "kind": kind,
        "line": str(line),
        "marker": marker,
        "text": _clean_line(text, _CHANGE_TEXT_MAX),
    }


def _change_ellipsis() -> dict[str, str]:
    return {"kind": "context", "line": "", "marker": "…", "text": ""}


def _append_change_row(rows: list[dict[str, str]], row: dict[str, str]) -> bool:
    if len(rows) >= _CHANGE_ROWS:
        return False
    rows.append(row)
    return True


def _added_lines(content: object, *, start_line: int = 1) -> list[dict[str, str]]:
    if not isinstance(content, str):
        return []
    rows = []
    line_no = start_line
    for raw, truncated in _raw_lines(content, line_limit=_CHANGE_TEXT_MAX):
        if len(rows) >= _CHANGE_ROWS:
            rows.append(_change_ellipsis())
            break
        rows.append({
            "kind": "add",
            "line": str(line_no),
            "marker": "+",
            "text": _clean_line(raw, _CHANGE_TEXT_MAX, truncated=truncated),
        })
        line_no += 1
    return rows


def render_write_input(tool_input: dict) -> ToolDisplay:
    return ToolDisplay(
        title="Write",
        detail=_path_detail(tool_input),
        preview=_added_lines(tool_input.get("content")),
    )


def render_edit_input(tool_input: dict) -> ToolDisplay:
    return ToolDisplay(title="Edit", detail=_path_detail(tool_input))


def render_bash_input(tool_input: dict) -> ToolDisplay:
    command = tool_input.get("command")
    detail = _bounded_lines(
        command if command else _input_detail(tool_input),
        max_lines=_BASH_DETAIL_LINES,
        max_chars=_BASH_DETAIL_MAX,
        first_last=True,
    )
    return ToolDisplay(title="Bash", detail=detail)


def _domain_values(tool_input: dict, *keys: str) -> str:
    value = next(
        (tool_input.get(key) for key in keys if tool_input.get(key) not in (None, "", [], {})),
        "",
    )
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value) if value else ""


def render_web_search_input(tool_input: dict) -> ToolDisplay:
    lines = []
    query = one_line(tool_input.get("query", ""), DETAIL_MAX)
    if query:
        lines.append(query)
    metadata = []
    allowed = _domain_values(tool_input, "allowed_domains", "allowedDomains")
    blocked = _domain_values(tool_input, "blocked_domains", "blockedDomains")
    if allowed:
        metadata.append(f"allowed: {allowed}")
    if blocked:
        metadata.append(f"blocked: {blocked}")
    if metadata:
        lines.append(one_line(" · ".join(metadata), DETAIL_MAX))
    return ToolDisplay(
        title="WebSearch",
        detail=_bounded_lines("\n".join(lines), max_lines=DETAIL_LINES, max_chars=DETAIL_MAX),
    )


def render_web_fetch_input(tool_input: dict) -> ToolDisplay:
    lines = []
    url = one_line(tool_input.get("url", ""), DETAIL_MAX)
    if url:
        lines.append(url)
    prompt = one_line(tool_input.get("prompt", ""), DETAIL_MAX - len("prompt: "))
    if prompt:
        lines.append(f"prompt: {prompt}")
    return ToolDisplay(
        title="WebFetch",
        detail=_bounded_lines("\n".join(lines), max_lines=DETAIL_LINES, max_chars=DETAIL_MAX),
    )


def render_agent_input(tool_input: dict) -> ToolDisplay:
    subagent = tool_input.get("subagent_type") or tool_input.get("agent")
    line = str(subagent) if subagent else ""
    description = tool_input.get("description") or tool_input.get("name")
    if description:
        summary = one_line(description, DETAIL_MAX)
        line = f"{line} - {summary}" if line else summary
    elif prompt := tool_input.get("prompt"):
        if isinstance(prompt, str) and prompt:
            summary = _large_string_summary("prompt", prompt)
            line = f"{line} - {summary}" if line else summary

    flags = []
    for key, label in (
        ("model", "model"),
        ("resume", "resume"),
        ("max_turns", "max turns"),
        ("mode", "mode"),
        ("isolation", "isolation"),
    ):
        value = tool_input.get(key)
        if value not in (None, "", [], {}):
            flags.append(f"{label} {value}")
    if tool_input.get("run_in_background"):
        flags.append("background")
    if flags:
        flag_line = " · ".join(flags)
        line = f"{line}\n{flag_line}" if line else flag_line

    return ToolDisplay(
        title="Agent",
        detail=_bounded_lines(line, max_lines=DETAIL_LINES, max_chars=DETAIL_MAX),
    )


def render_ask_user_question_input(tool_input: dict) -> ToolDisplay:
    return ToolDisplay(title="AskUserQuestion")


def render_skill_input(tool_input: dict) -> ToolDisplay:
    return ToolDisplay(title="Skill")


def _skill_name_from_result(content, tool_use_result: dict | None) -> str:
    if isinstance(tool_use_result, dict):
        for key in ("skill", "name", "skillName", "skill_name"):
            value = tool_use_result.get(key)
            if value not in (None, "", [], {}):
                return one_line(value, 72)
    if isinstance(content, str):
        match = re.search(
            r"(?:launching|loaded|loading|successfully loaded)\s+skill:?\s+(.+)",
            content,
            re.IGNORECASE,
        )
        if match:
            return one_line(match.group(1), 72)
    return ""


def _tool_search_query_names(value) -> list[str]:
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text.lower().startswith("select:"):
        return []
    return [
        one_line(part, 72)
        for part in text[len("select:"):].split(",")
        if one_line(part, 72)
    ]


def _tool_search_match_name(value) -> str:
    if isinstance(value, str):
        return one_line(value, 72)
    if isinstance(value, dict):
        for key in ("name", "tool_name", "toolName", "id"):
            if value.get(key) not in (None, "", [], {}):
                return one_line(value.get(key), 72)
        tool = value.get("tool")
        if isinstance(tool, dict):
            return _tool_search_match_name(tool)
    return ""


def _tool_search_names(values) -> list[str]:
    if not isinstance(values, list):
        return []
    names = []
    seen = set()
    for value in values:
        name = _tool_search_match_name(value)
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _tool_search_names_summary(names: list[str]) -> str:
    return one_line(", ".join(names), DETAIL_MAX)


def _tool_search_content_no_match(content) -> bool:
    return isinstance(content, str) and content.strip().lower() == _TOOL_SEARCH_NO_MATCH


def tool_result_soft_error(
    name: str, content, tool_use_result: dict | None = None
) -> bool:
    if name != "ToolSearch":
        return False
    if isinstance(tool_use_result, dict) and tool_use_result.get("matches") == []:
        return True
    return _tool_search_content_no_match(content)


def render_tool_search_input(tool_input: dict) -> ToolDisplay:
    query = tool_input.get("query")
    detail = _tool_search_names_summary(_tool_search_query_names(query))
    if not detail and query not in (None, "", [], {}):
        detail = one_line(query, DETAIL_MAX)
    return ToolDisplay(title="ToolSearch", detail=detail)


def _task_id(tool_input: dict) -> str:
    value = tool_input.get("taskId") or tool_input.get("id") or tool_input.get("task_id")
    return f"#{value}" if value not in (None, "", [], {}) else ""


def _task_status(value: object) -> str:
    return str(value).replace("_", " ")


def _task_ids(values) -> str:
    if not isinstance(values, list):
        return ""
    return ", ".join(f"#{value}" for value in values)


def _task_subject(value) -> str:
    return one_line(value, 72) if value not in (None, "", [], {}) else ""


def _task_count_summary(tasks: list) -> str:
    total = 0
    done = active = blocked = open_count = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        total += 1
        status = task.get("status")
        blocked_by = task.get("blockedBy") or task.get("blocked_by") or []
        if status == "completed":
            done += 1
        elif status == "in_progress":
            active += 1
        elif blocked_by:
            blocked += 1
        else:
            open_count += 1
    if not total:
        return ""
    counts = []
    for count, label in (
        (done, "done"),
        (active, "active"),
        (blocked, "blocked"),
        (open_count, "open"),
    ):
        if count:
            counts.append(f"{count} {label}")
    label = f"{total} task{'s' if total != 1 else ''}"
    return f"{label} ({', '.join(counts)})" if counts else label


def render_task_create_input(tool_input: dict) -> ToolDisplay:
    subject = one_line(tool_input.get("subject", ""), DETAIL_MAX)
    return ToolDisplay(title="TaskCreate", detail=subject)


def render_task_update_input(tool_input: dict) -> ToolDisplay:
    task_id = _task_id(tool_input)
    parts = []
    status = tool_input.get("status")
    if status:
        parts.append(f"→ {_task_status(status)}")
    blocked_by = _task_ids(
        tool_input.get("addBlockedBy") or tool_input.get("add_blocked_by")
    )
    if blocked_by:
        parts.append(f"blocked by {blocked_by}")
    blocks = _task_ids(tool_input.get("addBlocks") or tool_input.get("add_blocks"))
    if blocks:
        parts.append(f"blocks {blocks}")
    if subject := _task_subject(tool_input.get("subject")):
        parts.append(f"subject: {subject}")
    if description := _task_subject(tool_input.get("description")):
        parts.append(f"description: {description}")
    active_form = tool_input.get("activeForm") or tool_input.get("active_form")
    if active := _task_subject(active_form):
        parts.append(f"active: {active}")
    if owner := _task_subject(tool_input.get("owner")):
        parts.append(f"owner: {owner}")
    if tool_input.get("metadata") not in (None, "", [], {}):
        parts.append("metadata")
    if task_id and parts and parts[0].startswith("→ "):
        detail = " · ".join([f"{task_id} {parts[0]}", *parts[1:]])
    else:
        detail = " · ".join([part for part in (task_id, *parts) if part])
    return ToolDisplay(title="TaskUpdate", detail=one_line(detail, DETAIL_MAX))


def render_task_get_input(tool_input: dict) -> ToolDisplay:
    return ToolDisplay(title="TaskGet", detail=_task_id(tool_input))


def render_task_list_input(tool_input: dict) -> ToolDisplay:
    return ToolDisplay(title="TaskList", detail="current tasks")


def render_todo_write_input(tool_input: dict) -> ToolDisplay:
    todos = tool_input.get("todos")
    detail = _task_count_summary(todos) if isinstance(todos, list) else ""
    return ToolDisplay(title="TodoWrite", detail=detail)


def _input_value(tool_input: dict, key: str | None):
    if key:
        value = tool_input.get(key)
        if value not in (None, "", [], {}):
            return value
    for value in tool_input.values():
        if value not in (None, "", [], {}):
            return value
    return ""


def _value_summary(key: str, value) -> str:
    if isinstance(value, str):
        if key in _LARGE_INPUT_KEYS:
            return _large_string_summary(key, value)
        return f"{key}: {one_line(value, 72)}"
    if isinstance(value, (int, float, bool)):
        return f"{key}: {value}"
    if isinstance(value, list):
        return f"{key}: {len(value)} items"
    if isinstance(value, dict):
        return f"{key}: {len(value)} keys"
    return f"{key}: {type(value).__name__}"


def _input_detail(tool_input: dict) -> str:
    lines = []
    for key, value in tool_input.items():
        if value in (None, "", [], {}):
            continue
        lines.append(_value_summary(str(key), value))
        if len(lines) >= DETAIL_LINES:
            break
    return _join_capped(lines, DETAIL_MAX)


def render_generic_input(name: str, tool_input: dict) -> ToolDisplay:
    key = _INPUT_KEY.get(name)
    value = _input_value(tool_input, key) if key else ""
    if value and key not in _LARGE_INPUT_KEYS:
        if key in ("file_path", "notebook_path"):
            summary = one_line(rel_path(str(value)), TITLE_MAX - len(name) - 2)
        else:
            summary = one_line(value, TITLE_MAX - len(name) - 2)
        if summary:
            return ToolDisplay(title=one_line(f"{name}  {summary}", TITLE_MAX))
    return ToolDisplay(title=one_line(name, TITLE_MAX), detail=_input_detail(tool_input))


def _int_value(value, default: int) -> int:
    return value if isinstance(value, int) else default


def _structured_patch_rows(tool_use_result: dict | None) -> list[dict[str, str]]:
    if not isinstance(tool_use_result, dict):
        return []
    patches = tool_use_result.get("structuredPatch")
    if not isinstance(patches, list):
        return []
    rows = []
    overflow = False
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        old_line = _int_value(patch.get("oldStart"), 1)
        new_line = _int_value(patch.get("newStart"), 1)
        lines = patch.get("lines")
        if not isinstance(lines, list):
            continue
        for line in lines:
            line = str(line)
            if not line:
                continue
            marker = line[0]
            text = line[1:]
            if marker == "-":
                if not _append_change_row(rows, _change_row("remove", old_line, "-", text)):
                    overflow = True
                    break
                old_line += 1
            elif marker == "+":
                if not _append_change_row(rows, _change_row("add", new_line, "+", text)):
                    overflow = True
                    break
                new_line += 1
            elif marker == " ":
                old_line += 1
                new_line += 1
        if overflow:
            break
    if overflow:
        rows.append(_change_ellipsis())
    return rows


def render_write_result(content, tool_use_result: dict | None = None):
    if isinstance(tool_use_result, dict):
        rows = _added_lines(tool_use_result.get("content"))
        if rows:
            return rows
    return _content_preview(content)


def render_edit_result(content, tool_use_result: dict | None = None):
    rows = _structured_patch_rows(tool_use_result)
    if rows:
        return rows
    return _content_preview(content)


def _prefix_stderr(text: str) -> str:
    lines = _bounded_lines(
        text,
        max_lines=_BASH_PREVIEW_LINES,
        max_chars=PREVIEW_MAX,
        first_last=True,
    ).splitlines()
    return "\n".join(f"stderr: {line}" for line in lines)


def _bash_output_block(preview: str):
    if not preview.strip():
        return ""
    return BlockPreview("", preview)


def render_bash_result(content, tool_use_result: dict | None = None):
    if isinstance(tool_use_result, dict):
        stdout = tool_use_result.get("stdout")
        stderr = tool_use_result.get("stderr")
        has_output = "stdout" in tool_use_result or "stderr" in tool_use_result
        parts = []
        if isinstance(stdout, str) and stdout.strip():
            parts.append(_bounded_lines(
                stdout,
                max_lines=_BASH_PREVIEW_LINES,
                max_chars=PREVIEW_MAX,
                first_last=True,
            ))
        if isinstance(stderr, str) and stderr.strip():
            parts.append(_prefix_stderr(stderr))
        if parts:
            preview = _bounded_lines(
                "\n".join(parts),
                max_lines=_BASH_PREVIEW_LINES,
                max_chars=PREVIEW_MAX,
                first_last=True,
            )
            return _bash_output_block(preview)
        if has_output:
            return ""
    preview = _bounded_lines(
        _content_preview(content),
        max_lines=_BASH_PREVIEW_LINES,
        max_chars=PREVIEW_MAX,
        first_last=True,
    )
    return _bash_output_block(preview)


def _compact_number(value: int) -> str:
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1000:.1f}k"
    return f"{value / 1_000_000:.1f}m"


def _duration_ms(value: object) -> str:
    if not isinstance(value, int):
        return ""
    if value < 1000:
        return f"{value}ms"
    seconds = value / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m {int(seconds % 60):02d}s"


def _agent_text_preview(content) -> tuple[str, bool]:
    if isinstance(content, str):
        return _bounded_lines_with_overflow(
            content,
            max_lines=_AGENT_PREVIEW_LINES,
            max_chars=PREVIEW_MAX,
            first_last=True,
        )
    if isinstance(content, dict) and content.get("type") == "text":
        return _bounded_lines_with_overflow(
            str(content.get("text", "")),
            max_lines=_AGENT_PREVIEW_LINES,
            max_chars=PREVIEW_MAX,
            first_last=True,
        )
    if isinstance(content, list):
        lines = []
        total = 0
        truncated = False
        for text in _text_blocks(content):
            if len(lines) >= _AGENT_PREVIEW_LINES or total >= PREVIEW_MAX:
                truncated = True
                break
            remaining_lines = _AGENT_PREVIEW_LINES - len(lines)
            remaining_chars = PREVIEW_MAX - total - (1 if lines else 0)
            preview, text_truncated = _bounded_lines_with_overflow(
                text,
                max_lines=remaining_lines,
                max_chars=remaining_chars,
                first_last=True,
            )
            new_lines = preview.splitlines()
            if not new_lines:
                continue
            lines.extend(new_lines)
            total += len(preview) + (1 if total else 0)
            truncated = truncated or text_truncated
            if text_truncated:
                break
        return _join_capped(lines[:_AGENT_PREVIEW_LINES], PREVIEW_MAX), truncated
    return "", False


def _agent_content_preview(content) -> str:
    if isinstance(content, list):
        filtered = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue
            text = str(block.get("text", ""))
            if text.startswith("agentId:") or "<usage>" in text:
                continue
            filtered.append(block)
        preview, _ = _agent_text_preview(filtered)
        return preview
    preview, _ = _agent_text_preview(content)
    return preview or _content_preview(content)


def _agent_status(value: object) -> str:
    text = str(value or "completed").replace("_", " ")
    return text[:1].upper() + text[1:]


def _agent_metrics(tool_use_result: dict) -> str:
    status = tool_use_result.get("status")
    parts = []
    count = tool_use_result.get("totalToolUseCount")
    if isinstance(count, int):
        parts.append(f"Tools: {count}")
    tokens = tool_use_result.get("totalTokens")
    if isinstance(tokens, int):
        parts.append(f"Tokens: {_compact_number(tokens)}")
    duration = _duration_ms(tool_use_result.get("totalDurationMs"))
    if duration:
        parts.append(f"Time: {duration}")
    if status == "async_launched":
        output = tool_use_result.get("outputFile")
        if output:
            parts.append(f"Output: {rel_path(str(output))}")
    return " · ".join([_agent_status(status)] + parts)


def render_agent_result(content, tool_use_result: dict | None = None) -> BlockPreview:
    if isinstance(tool_use_result, dict):
        preview = _agent_content_preview(tool_use_result.get("content"))
        if not preview and (message := tool_use_result.get("message")):
            preview = _bounded_lines(
                message,
                max_lines=_AGENT_PREVIEW_LINES,
                max_chars=PREVIEW_MAX,
                first_last=True,
            )
        return BlockPreview(_agent_metrics(tool_use_result), preview)

    preview = _agent_content_preview(content)
    return BlockPreview("", preview or _content_preview(content))


def _read_image_preview(content, tool_use_result: dict | None) -> str:
    for block in _dict_blocks(content):
        if _image_like(block):
            return _image_summary(block)
    for block in _dict_blocks(tool_use_result):
        if _image_like(block):
            return _image_summary(block)
    return ""


def render_read_result(content, tool_use_result: dict | None = None) -> str:
    metadata_count = _read_metadata_line_count(tool_use_result)
    if metadata_count is not None:
        return _read_line_summary(metadata_count)
    image = _read_image_preview(content, tool_use_result)
    if image:
        return _bounded_lines(image, max_lines=PREVIEW_LINES, max_chars=PREVIEW_MAX)
    count = _read_content_line_count(content)
    if count is not None:
        return _read_line_summary(*count)
    if isinstance(tool_use_result, dict):
        lines_read = _read_result_line_count(tool_use_result)
        if lines_read is not None:
            return _read_line_summary(*lines_read)
        return _content_preview(tool_use_result)
    return _content_preview(content)


def render_quiet_result(content, tool_use_result: dict | None = None) -> str:
    return ""


def _ask_user_question_payload(content, tool_use_result: dict | None) -> dict | None:
    for value in (tool_use_result, content):
        if isinstance(value, dict) and (
            isinstance(value.get("questions"), list)
            or isinstance(value.get("answers"), dict)
            or value.get("response") not in (None, "", [], {})
        ):
            return value
    return None


def _answer_text(value) -> str:
    if isinstance(value, list):
        text = ", ".join(str(item) for item in value if item not in (None, "", [], {}))
    elif isinstance(value, dict):
        text = ", ".join(
            f"{key}: {answer}"
            for key, answer in value.items()
            if answer not in (None, "", [], {})
        )
    else:
        text = str(value) if value not in (None, "", [], {}) else ""
    return one_line(text or "No answer", DETAIL_MAX)


def _question_text(question: object) -> str:
    if isinstance(question, dict):
        text = question.get("question") or question.get("header") or "Question"
        return one_line(text, DETAIL_MAX)
    return one_line(question, DETAIL_MAX)


def _answer_pairs(payload: dict) -> list[tuple[str, object]]:
    if payload.get("response") not in (None, "", [], {}):
        return [("Response", payload.get("response"))]
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return []
    questions = payload.get("questions")
    pairs = []
    used = set()
    if isinstance(questions, list):
        for question in questions:
            key = question.get("question") if isinstance(question, dict) else str(question)
            if key in answers:
                pairs.append((_question_text(question), answers.get(key)))
                used.add(key)
    for key, value in answers.items():
        if key not in used:
            pairs.append((one_line(key, DETAIL_MAX), value))
    return pairs


def render_ask_user_question_result(content, tool_use_result: dict | None = None):
    payload = _ask_user_question_payload(content, tool_use_result)
    if not payload:
        return ""
    pairs = _answer_pairs(payload)
    lines = []
    for index, (question, answer) in enumerate(pairs[:_ASK_QUESTION_ROWS], start=1):
        lines.extend([f"{index}. {question}", f"└─ {_answer_text(answer)}"])
    remaining = len(pairs) - _ASK_QUESTION_ROWS
    if remaining > 0:
        lines.append(f"… {remaining} more answer{'s' if remaining != 1 else ''}")
    return _join_capped(lines, PREVIEW_MAX) if lines else ""


def render_skill_result_detail(content, tool_use_result: dict | None = None) -> str:
    skill = _skill_name_from_result(content, tool_use_result)
    if skill:
        return f"Successfully loaded {skill} skill"
    return "Successfully loaded skill"


def render_tool_search_result_detail(content, tool_use_result: dict | None = None) -> str:
    if isinstance(tool_use_result, dict):
        matches = tool_use_result.get("matches")
        if isinstance(matches, list):
            if not matches:
                return "No matching tools found"
            names = _tool_search_names(matches)
            return _tool_search_names_summary(names) if names else ""
    if _tool_search_content_no_match(content):
        return "No matching tools found"
    return ""


def render_task_create_result_detail(content, tool_use_result: dict | None = None) -> str:
    task = tool_use_result.get("task") if isinstance(tool_use_result, dict) else None
    if isinstance(task, dict):
        task_id = _task_id(task)
        subject = _task_subject(task.get("subject"))
        return one_line(f"Created {task_id} · {subject}".strip(" ·"), DETAIL_MAX)
    if isinstance(content, str):
        match = _TASK_CREATE_RE.search(content)
        if match:
            return one_line(
                f"Created #{match.group('id')} · {match.group('subject')}",
                DETAIL_MAX,
            )
    return ""


def render_task_update_result(content, tool_use_result: dict | None = None) -> str:
    if isinstance(tool_use_result, dict):
        if tool_use_result.get("success") is False:
            error = one_line(tool_use_result.get("error", ""), DETAIL_MAX)
            return f"Update failed: {error}" if error else "Update failed"
    return ""


def render_task_get_result_detail(content, tool_use_result: dict | None = None) -> str:
    if isinstance(tool_use_result, dict):
        task = tool_use_result.get("task")
        if task is None:
            return "Task not found"
        if isinstance(task, dict):
            task_id = _task_id(task)
            status = _task_status(task.get("status"))
            subject = one_line(task.get("subject", ""), DETAIL_MAX)
            return one_line(f"{task_id} {status}: {subject}".strip(), DETAIL_MAX)
    return ""


def render_task_list_result_detail(content, tool_use_result: dict | None = None) -> str:
    tasks = tool_use_result.get("tasks") if isinstance(tool_use_result, dict) else None
    if isinstance(tasks, list):
        return _task_count_summary(tasks)
    return ""


def render_todo_write_result_detail(content, tool_use_result: dict | None = None) -> str:
    if isinstance(tool_use_result, dict):
        todos = tool_use_result.get("newTodos")
        if isinstance(todos, list):
            return _task_count_summary(todos)
        stats = tool_use_result.get("stats")
        if isinstance(stats, dict) and isinstance(stats.get("total"), int):
            total = stats["total"]
            counts = []
            for key, label in (
                ("completed", "done"),
                ("in_progress", "active"),
                ("pending", "open"),
            ):
                value = stats.get(key)
                if isinstance(value, int) and value:
                    counts.append(f"{value} {label}")
            label = f"{total} task{'s' if total != 1 else ''}"
            return f"{label} ({', '.join(counts)})" if counts else label
    return ""
