import base64
import os

import castcode.format as fmt
from castcode.tool_render import (
    ERROR_MAX,
    BlockPreview,
    PREVIEW_LINES,
    ToolDisplay,
    render_read_result,
    render_tool_error,
    render_tool_input,
    render_tool_result,
    render_tool_result_detail,
    render_tool_result_soft_error,
)


def test_read_input_title_and_detail():
    path = os.path.join(fmt.CWD, "src", "main.py")
    display = render_tool_input("Read", {
        "file_path": path,
        "offset": 10,
        "limit": 20,
        "pages": [1, 2],
    })
    assert display == ToolDisplay(
        title="Read",
        detail="src/main.py · offset 10 · limit 20 · pages 1, 2",
    )


def test_read_text_result_shows_line_count_not_contents():
    preview = render_read_result("secret token\nsecond line\n")
    assert preview == "2 lines read"
    assert "secret" not in preview
    assert "second" not in preview


def test_read_text_result_uses_lines_returned_metadata_not_contents():
    preview = render_read_result(None, {
        "content": "secret token\nsecond line\n",
        "total_lines": 10,
        "lines_returned": 2,
    })
    assert preview == "2 lines read"
    assert "secret" not in preview


def test_read_text_result_preserves_zero_lines_returned_metadata():
    preview = render_read_result(None, {
        "content": "",
        "total_lines": 10,
        "lines_returned": 0,
    })
    assert preview == "0 lines read"


def test_read_text_result_uses_tool_use_result_text_block_line_count():
    preview = render_read_result(None, {"type": "text", "text": "secret\nsecond\n"})
    assert preview == "2 lines read"
    assert "secret" not in preview


def test_read_text_result_metadata_avoids_scanning_content():
    class NoCountString(str):
        def count(self, *args, **kwargs):
            raise AssertionError("content should not be counted when metadata is present")

        def find(self, *args, **kwargs):
            raise AssertionError("content should not be previewed when metadata is present")

    preview = render_read_result(NoCountString("secret"), {"lines_returned": 1})
    assert preview == "1 line read"
    assert "secret" not in preview


def test_read_text_result_metadata_avoids_iterating_content_blocks():
    class NoIterList(list):
        def __iter__(self):
            raise AssertionError("content should not be iterated when metadata is present")

    preview = render_read_result(
        NoIterList([{"type": "text", "text": "secret"}]),
        {"lines_returned": 1},
    )
    assert preview == "1 line read"
    assert "secret" not in preview


def test_read_large_text_result_uses_bounded_line_summary_not_contents():
    class NoDirectCountString(str):
        def count(self, *args, **kwargs):
            raise AssertionError("raw content should not be counted directly")

    preview = render_read_result(NoDirectCountString("secret" * 5000))
    assert preview == "1+ lines read"
    assert "secret" not in preview


def test_read_image_result_shows_mime_and_size_not_base64():
    data = base64.b64encode(b"abcdef").decode()
    preview = render_read_result([
        {"type": "image", "source": {"media_type": "image/png", "data": data}},
    ])
    assert preview == "image: image/png, 6 B"
    assert data not in preview


def test_read_image_result_handles_python_shape():
    data = base64.b64encode(b"abcdef").decode()
    preview = render_read_result({"image": data, "mime_type": "image/webp", "file_size": 6})
    assert preview == "image: image/webp, 6 B"
    assert data not in preview


def test_read_image_result_handles_file_shape():
    data = base64.b64encode(b"abcdef").decode()
    preview = render_read_result({
        "type": "image",
        "file": {"base64": data, "type": "image/gif", "originalSize": 6},
    })
    assert preview == "image: image/gif, 6 B"
    assert data not in preview


def test_read_image_result_handles_camel_case_mime_type():
    data = base64.b64encode(b"abc").decode()
    preview = render_read_result({"type": "image", "data": data, "mimeType": "image/png"})
    assert preview == "image: image/png, 3 B"
    assert data not in preview


def test_read_image_result_handles_tool_use_result_content_envelope():
    data = base64.b64encode(b"abc").decode()
    preview = render_read_result(None, {
        "content": [{"type": "image", "data": data, "mimeType": "image/png"}],
    })
    assert preview == "image: image/png, 3 B"
    assert data not in preview


def test_write_input_shows_tool_path_and_added_lines():
    path = os.path.join(fmt.CWD, "created.py")
    display = render_tool_input("Write", {
        "file_path": path,
        "content": "alpha\nbeta\n",
    })
    assert display.title == "Write"
    assert display.detail == "created.py"
    assert display.preview == [
        {"kind": "add", "line": "1", "marker": "+", "text": "alpha"},
        {"kind": "add", "line": "2", "marker": "+", "text": "beta"},
    ]


def test_edit_input_shows_tool_and_path_only_until_result_patch():
    path = os.path.join(fmt.CWD, "edited.py")
    display = render_tool_input("Edit", {
        "file_path": path,
        "old_string": "old",
        "new_string": "new",
    })
    assert display == ToolDisplay(title="Edit", detail="edited.py")


def test_bash_input_shows_tool_and_command_detail():
    display = render_tool_input("Bash", {
        "command": "cd repo\nuv sync\npytest\nruff check\n",
        "description": "print values",
    })
    assert display == ToolDisplay(
        title="Bash",
        detail="cd repo\nuv sync\npytest\nruff check",
    )


def test_bash_input_long_multiline_command_uses_head_tail():
    display = render_tool_input("Bash", {
        "command": "line 0\nline 1\nline 2\nline 3\nline 4\n",
    })
    assert display.detail.splitlines() == ["line 0", "line 1", "…", "line 4"]


def test_bash_input_crops_long_command_detail():
    display = render_tool_input("Bash", {"command": "x" * 500})
    assert display.title == "Bash"
    assert len(display.detail) == 320
    assert display.detail.endswith("…")


def test_agent_input_shows_type_and_description_not_prompt():
    display = render_tool_input("Agent", {
        "description": "Read probe.txt",
        "prompt": "secret prompt\n" * 50,
        "subagent_type": "general-purpose",
    })
    assert display == ToolDisplay(
        title="Agent",
        detail="general-purpose - Read probe.txt",
    )
    assert "secret" not in display.detail


def test_agent_task_alias_uses_agent_renderer():
    display = render_tool_input("Task", {
        "description": "Explore files",
        "subagent_type": "Explore",
    })
    assert display == ToolDisplay(title="Agent", detail="Explore - Explore files")


def test_skill_input_is_plain_title():
    display = render_tool_input("Skill", {
        "skill": "verify",
    })
    assert display == ToolDisplay(title="Skill")


def test_skill_success_result_shows_loaded_skill_detail():
    assert render_tool_result("Skill", "Launching skill: verify") == ""
    assert render_tool_result_detail("Skill", "Launching skill: verify") == (
        "Successfully loaded verify skill"
    )
    assert render_tool_result_detail("Skill", "", {"skill": "github:yeet"}) == (
        "Successfully loaded github:yeet skill"
    )
    assert render_tool_result_detail("Skill", "") == "Successfully loaded skill"


def test_ask_user_question_input_is_plain_title():
    display = render_tool_input("AskUserQuestion", {
        "questions": [
            {"question": "Which area?"},
            {"question": "Which task?"},
            {"question": "Anything else?"},
        ],
    })
    assert display == ToolDisplay(title="AskUserQuestion")


def test_ask_user_question_result_shows_question_answers():
    payload = {
        "questions": [
            {"question": "Which area?"},
            {"question": "Which task?"},
            {"question": "Anything else?"},
        ],
        "answers": {
            "Which area?": "UI",
            "Which task?": ["Tests", "Docs"],
            "Anything else?": "Nope",
        },
    }

    assert render_tool_result("AskUserQuestion", "", payload) == (
        "1. Which area?\n"
        "└─ UI\n"
        "2. Which task?\n"
        "└─ Tests, Docs\n"
        "3. Anything else?\n"
        "└─ Nope"
    )
    assert render_tool_result_detail("AskUserQuestion", "", payload) == ""


def test_ask_user_question_result_is_bounded():
    payload = {
        "questions": [
            {"question": f"Question {index}?"}
            for index in range(5)
        ],
        "answers": {
            f"Question {index}?": f"Answer {index}"
            for index in range(5)
        },
    }

    preview = render_tool_result("AskUserQuestion", "", payload)

    assert preview == (
        "1. Question 0?\n"
        "└─ Answer 0\n"
        "2. Question 1?\n"
        "└─ Answer 1\n"
        "3. Question 2?\n"
        "└─ Answer 2\n"
        "… 2 more answers"
    )


def test_ask_user_question_result_shows_general_response():
    payload = {
        "questions": [{"question": "Which area?"}],
        "response": "Let's keep this high level.",
    }

    assert render_tool_result("AskUserQuestion", "", payload) == (
        "1. Response\n"
        "└─ Let's keep this high level."
    )


def test_tool_search_input_strips_select_and_omits_limit():
    assert render_tool_input("ToolSearch", {
        "query": "select:NotebookEdit",
        "max_results": 1,
    }) == ToolDisplay(title="ToolSearch", detail="NotebookEdit")

    assert render_tool_input("ToolSearch", {
        "query": "select:Glob,Grep",
        "max_results": 5,
    }) == ToolDisplay(title="ToolSearch", detail="Glob, Grep")


def test_tool_search_input_falls_back_to_plain_query():
    assert render_tool_input("ToolSearch", {
        "query": "notebook editing tools",
        "max_results": 5,
    }) == ToolDisplay(title="ToolSearch", detail="notebook editing tools")


def test_tool_search_results_show_tool_names_only():
    assert render_tool_result("ToolSearch", "", {
        "matches": ["WebFetch", "WebSearch"],
        "query": "web",
        "total_deferred_tools": 20,
    }) == ""
    assert render_tool_result_detail("ToolSearch", "", {
        "matches": ["WebFetch", "WebSearch"],
        "query": "web",
        "total_deferred_tools": 20,
    }) == "WebFetch, WebSearch"

    assert render_tool_result_detail("ToolSearch", "", {
        "matches": [
            {"name": "NotebookEdit", "description": "Edit notebook cells"},
            {"tool": {"name": "Workflow"}},
        ],
    }) == "NotebookEdit, Workflow"
    assert render_tool_result_soft_error("ToolSearch", "", {
        "matches": ["WebFetch", "WebSearch"],
    }) is False


def test_tool_search_no_match_results_show_clean_empty_state():
    assert render_tool_result("ToolSearch", "No matching deferred tools found") == ""
    assert render_tool_result_detail(
        "ToolSearch",
        "No matching deferred tools found",
    ) == "No matching tools found"
    assert render_tool_result_detail("ToolSearch", "", {"matches": []}) == (
        "No matching tools found"
    )
    assert render_tool_result_soft_error("ToolSearch", "", {"matches": []}) is True
    assert render_tool_result_soft_error(
        "ToolSearch",
        "No matching deferred tools found",
    ) is True
    assert render_tool_result_soft_error(
        "Bash",
        "No matching deferred tools found",
    ) is False


def test_task_tool_inputs_are_compact():
    assert render_tool_input("TaskCreate", {
        "subject": "Wire up tool result rendering",
        "description": "Longer details stay in the persistent todo strip",
    }) == ToolDisplay(title="TaskCreate", detail="Wire up tool result rendering")

    assert render_tool_input("TaskUpdate", {
        "taskId": "1",
        "status": "in_progress",
        "activeForm": "Wiring up tool result rendering",
    }) == ToolDisplay(
        title="TaskUpdate",
        detail="#1 → in progress · active: Wiring up tool result rendering",
    )

    assert render_tool_input("TaskUpdate", {
        "task_id": "3",
        "addBlockedBy": ["1"],
    }) == ToolDisplay(title="TaskUpdate", detail="#3 · blocked by #1")

    assert render_tool_input("TaskUpdate", {
        "taskId": "2",
        "subject": "Renamed permission mode picker",
        "metadata": {"kind": "ui"},
    }) == ToolDisplay(
        title="TaskUpdate",
        detail="#2 · subject: Renamed permission mode picker · metadata",
    )

    assert render_tool_input("TaskGet", {"id": "2"}) == ToolDisplay(
        title="TaskGet",
        detail="#2",
    )
    assert render_tool_input("TaskList", {}) == ToolDisplay(
        title="TaskList",
        detail="current tasks",
    )
    assert render_tool_input("TodoWrite", {
        "todos": [
            {"content": "done", "status": "completed", "activeForm": "doing"},
            {"content": "active", "status": "in_progress", "activeForm": "activating"},
            {"content": "open", "status": "pending", "activeForm": "opening"},
        ],
    }) == ToolDisplay(title="TodoWrite", detail="3 tasks (1 done, 1 active, 1 open)")


def test_write_result_uses_created_content_as_added_lines():
    preview = render_tool_result("Write", "File created successfully", {
        "type": "create",
        "filePath": "/tmp/created.py",
        "content": "alpha\nbeta\n",
    })
    assert preview == [
        {"kind": "add", "line": "1", "marker": "+", "text": "alpha"},
        {"kind": "add", "line": "2", "marker": "+", "text": "beta"},
    ]


def test_edit_result_uses_structured_patch_line_numbers():
    preview = render_tool_result("Edit", "updated", {
        "structuredPatch": [{
            "oldStart": 10,
            "newStart": 10,
            "lines": [
                " context",
                "-old token",
                "+new token",
            ],
        }],
    })
    assert preview == [
        {"kind": "remove", "line": "11", "marker": "-", "text": "old token"},
        {"kind": "add", "line": "11", "marker": "+", "text": "new token"},
    ]


def test_bash_result_uses_structured_stdout_not_dict_summary():
    preview = render_tool_result("Bash", "raw content", {
        "stdout": "fixture.txt\n",
        "stderr": "",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
    })
    assert preview == BlockPreview("", "fixture.txt")
    assert "stdout" not in preview.text


def test_bash_result_includes_stderr_label():
    preview = render_tool_result("Bash", "", {
        "stdout": "ok\n",
        "stderr": "warning\n",
    })
    assert preview == BlockPreview("", "ok\nstderr: warning")


def test_bash_result_falls_back_to_content_without_structured_output():
    preview = render_tool_result("Bash", "plain output\n")
    assert preview == BlockPreview("", "plain output")


def test_bash_result_large_stdout_is_bounded_head_tail():
    stdout = "\n".join(f"line {i}" for i in range(10))
    preview = render_tool_result("Bash", "", {"stdout": stdout, "stderr": ""})
    assert preview == BlockPreview(
        "",
        "line 0\nline 1\n…\nline 9",
    )


def test_bash_result_empty_output_stays_hidden():
    preview = render_tool_result("Bash", "raw fallback", {"stdout": "", "stderr": ""})
    assert preview == ""


def test_web_tool_inputs_are_compact_with_optional_metadata_line():
    assert render_tool_input("WebSearch", {
        "query": "textual python tui",
    }) == ToolDisplay(title="WebSearch", detail="textual python tui")

    assert render_tool_input("WebSearch", {
        "query": "textual python tui",
        "allowed_domains": ["textual.textualize.io", "github.com"],
        "blocked_domains": ["example.com"],
    }) == ToolDisplay(
        title="WebSearch",
        detail=(
            "textual python tui\n"
            "allowed: textual.textualize.io, github.com · blocked: example.com"
        ),
    )

    assert render_tool_input("WebFetch", {
        "url": "https://textual.textualize.io",
    }) == ToolDisplay(title="WebFetch", detail="https://textual.textualize.io")

    assert render_tool_input("WebFetch", {
        "url": "https://textual.textualize.io",
        "prompt": "Summarize the installation docs.",
    }) == ToolDisplay(
        title="WebFetch",
        detail=(
            "https://textual.textualize.io\n"
            "prompt: Summarize the installation docs."
        ),
    )


def test_web_tool_success_results_stay_quiet():
    assert render_tool_result("WebSearch", "many search results with snippets") == ""
    assert render_tool_result(
        "WebFetch",
        [{"type": "text", "text": "full fetched page contents"}],
    ) == ""


def test_agent_result_uses_structured_summary_and_content():
    preview = render_tool_result("Agent", "", {
        "status": "completed",
        "agentId": "afb9a43aee3928a1f",
        "totalToolUseCount": 1,
        "totalTokens": 8407,
        "totalDurationMs": 5623,
        "content": [{"type": "text", "text": "subagent saw 3 lines"}],
    })
    assert preview == BlockPreview(
        metrics="Completed · Tools: 1 · Tokens: 8.4k · Time: 5.6s",
        text="subagent saw 3 lines",
    )
    assert "Output truncated" not in preview.text


def test_agent_result_tolerates_malformed_total_tokens():
    preview = render_tool_result("Agent", "", {
        "status": "completed",
        "totalToolUseCount": 1,
        "totalTokens": "8407",
        "totalDurationMs": 5623,
        "content": [{"type": "text", "text": "done"}],
    })
    assert preview.metrics == "Completed · Tools: 1 · Time: 5.6s"
    assert preview.text == "done"


def test_agent_result_fallback_strips_agent_id_usage_block():
    preview = render_tool_result("Agent", [
        {"type": "text", "text": "useful result"},
        {"type": "text", "text": "agentId: abc\n<usage>secret</usage>"},
    ])
    assert preview == BlockPreview(metrics="", text="useful result")
    assert "agentId" not in preview.text


def test_agent_result_block_output_is_bounded():
    text = "\n".join(f"## Section {i}" for i in range(20))
    preview = render_tool_result("Agent", "", {
        "status": "completed",
        "totalToolUseCount": 4,
        "totalTokens": 13500,
        "totalDurationMs": 10200,
        "content": [{"type": "text", "text": text}],
    })
    assert preview.metrics == "Completed · Tools: 4 · Tokens: 13.5k · Time: 10.2s"
    lines = preview.text.splitlines()
    assert lines[:4] == ["## Section 0", "## Section 1", "## Section 2", "…"]
    assert len(lines) == 5
    assert "## Section 19" in preview.text


def test_agent_result_long_single_line_uses_ellipsis_only():
    preview = render_tool_result("Agent", "", {
        "status": "completed",
        "content": [{"type": "text", "text": "x" * 2000}],
    })
    assert preview.text.endswith("…")
    assert "Output truncated" not in preview.text


def test_task_tool_results_are_compact_summaries():
    assert render_tool_result("TaskCreate", "", {
        "task": {"id": "1", "subject": "Wire up tool result rendering"},
    }) == ""
    assert render_tool_result_detail("TaskCreate", "", {
        "task": {"id": "1", "subject": "Wire up tool result rendering"},
    }) == "Created #1 · Wire up tool result rendering"
    assert render_tool_result(
        "TaskCreate",
        "Task #2 created successfully: Add permission mode picker",
    ) == ""
    assert render_tool_result_detail(
        "TaskCreate",
        "Task #2 created successfully: Add permission mode picker",
    ) == "Created #2 · Add permission mode picker"

    assert render_tool_result("TaskUpdate", "", {
        "success": True,
        "taskId": "1",
        "updatedFields": ["status"],
        "statusChange": {"from": "pending", "to": "completed"},
    }) == ""
    assert render_tool_result_detail("TaskUpdate", "", {
        "success": True,
        "taskId": "1",
        "updatedFields": ["status"],
        "statusChange": {"from": "pending", "to": "completed"},
    }) == ""
    assert render_tool_result("TaskUpdate", "", {
        "success": True,
        "taskId": "3",
        "updatedFields": ["blockedBy"],
    }) == ""
    assert render_tool_result("TaskUpdate", "", {
        "success": False,
        "taskId": "3",
        "error": "blocked task missing",
    }) == "Update failed: blocked task missing"

    assert render_tool_result("TaskGet", "", {
        "task": {
            "id": "2",
            "subject": "Add permission mode picker",
            "status": "pending",
        }
    }) == ""
    assert render_tool_result_detail("TaskGet", "", {
        "task": {
            "id": "2",
            "subject": "Add permission mode picker",
            "status": "pending",
        }
    }) == "#2 pending: Add permission mode picker"
    assert render_tool_result("TaskGet", "", {"task": None}) == ""
    assert render_tool_result_detail("TaskGet", "", {"task": None}) == "Task not found"

    assert render_tool_result("TaskList", "", {
        "tasks": [
            {"id": "1", "subject": "done", "status": "completed", "blockedBy": []},
            {"id": "2", "subject": "active", "status": "in_progress", "blockedBy": []},
            {"id": "3", "subject": "blocked", "status": "pending", "blockedBy": ["1"]},
            {"id": "4", "subject": "open", "status": "pending", "blockedBy": []},
        ]
    }) == ""
    assert render_tool_result_detail("TaskList", "", {
        "tasks": [
            {"id": "1", "subject": "done", "status": "completed", "blockedBy": []},
            {"id": "2", "subject": "active", "status": "in_progress", "blockedBy": []},
            {"id": "3", "subject": "blocked", "status": "pending", "blockedBy": ["1"]},
            {"id": "4", "subject": "open", "status": "pending", "blockedBy": []},
        ]
    }) == "4 tasks (1 done, 1 active, 1 blocked, 1 open)"

    assert render_tool_result("TodoWrite", "", {
        "newTodos": [
            {"content": "done", "status": "completed", "activeForm": "doing"},
            {"content": "open", "status": "pending", "activeForm": "opening"},
        ]
    }) == ""
    assert render_tool_result_detail("TodoWrite", "", {
        "newTodos": [
            {"content": "done", "status": "completed", "activeForm": "doing"},
            {"content": "open", "status": "pending", "activeForm": "opening"},
        ]
    }) == "2 tasks (1 done, 1 open)"


def test_read_large_image_result_omits_base64_and_uncomputed_size():
    data = base64.b64encode(b"x" * 7000).decode()
    preview = render_read_result([
        {"type": "image", "source": {"media_type": "image/png", "data": data}},
    ])
    assert preview == "image: image/png"
    assert data not in preview


def test_large_generic_input_uses_compact_detail_not_payload():
    script = "echo secret\n" * 200
    display = render_tool_input("Workflow", {"script": script})
    assert display.title == "Workflow"
    assert display.detail == "script: 200 lines, 2.3 KB"
    assert "secret" not in display.detail


def test_large_generic_blob_input_uses_compact_detail_not_payload():
    data = base64.b64encode(b"secret payload" * 100).decode()
    display = render_tool_input("CustomUpload", {"data": data})
    assert display.title == "CustomUpload"
    assert display.detail == "data: 1 line, 1.8 KB"
    assert data[:20] not in display.detail


def test_huge_generic_blob_input_uses_character_count_not_payload():
    data = "secret" * 5000
    display = render_tool_input("CustomUpload", {"data": data})
    assert display.title == "CustomUpload"
    assert display.detail == "data: 30,000 chars+"
    assert "secret" not in display.detail


def test_generic_result_text_is_bounded_head_tail_lines():
    text = "\n".join(f"line {i}" for i in range(10))
    preview = render_tool_result("Mystery", text)
    lines = preview.splitlines()
    assert len(lines) == PREVIEW_LINES
    assert lines == ["line 0", "line 1", "line 2", "…", "line 8", "line 9"]


def test_generic_result_huge_single_line_is_cropped_before_display():
    class BoundedFindString(str):
        def find(self, sub, start=0, end=None):
            assert end is not None
            assert end - start <= 1457
            return super().find(sub, start, end)

    preview = render_tool_result("Mystery", BoundedFindString("x" * 10000))
    assert len(preview) == 1200
    assert preview.endswith("…")


def test_text_block_result_preview_obeys_combined_line_cap():
    preview = render_tool_result("Mystery", [
        {"type": "text", "text": "a1\na2\na3\na4\na5\na6"},
        {"type": "text", "text": "b1\nb2\nb3\nb4\nb5\nb6"},
    ])
    assert len(preview.splitlines()) == PREVIEW_LINES


def test_text_block_error_obeys_combined_error_line_cap():
    preview = render_tool_error([
        {"type": "text", "text": "a1\na2\na3\na4"},
        {"type": "text", "text": "b1\nb2\nb3\nb4"},
    ])
    assert len(preview.splitlines()) == 4


def test_non_text_blocks_get_compact_summary_not_payload():
    data = base64.b64encode(b"abcdef").decode()
    preview = render_tool_result("Mystery", [
        {"type": "image", "source": {"media_type": "image/jpeg", "data": data}},
        {"type": "resource", "uri": "file:///tmp/a", "mime_type": "text/plain"},
    ])
    assert "image: image/jpeg, 6 B" in preview
    assert "resource: uri, mime_type" in preview
    assert data not in preview


def test_non_text_error_blocks_use_error_caps():
    preview = render_tool_error([
        {"type": "image", "source": {"media_type": "image/png", "data": "YWJj"}},
        {"type": "resource", "uri": "a"},
        {"type": "resource", "uri": "b"},
        {"type": "resource", "uri": "c"},
        {"type": "resource", "uri": "d"},
    ])
    assert len(preview.splitlines()) == 4


def test_error_text_strips_markup_ansi_controls_and_caps():
    text = "\x1b[31m<tool_use_error>" + ("x" * (ERROR_MAX + 10)) + "\x00</tool_use_error>"
    preview = render_tool_error(text)
    assert "\x1b" not in preview
    assert "\x00" not in preview
    assert "<tool_use_error>" not in preview
    assert len(preview) == ERROR_MAX
    assert preview.endswith("…")
