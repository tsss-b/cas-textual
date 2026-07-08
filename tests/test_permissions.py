import asyncio

import claude_agent_sdk as sdk

from textual import work
from textual.geometry import Spacing
from textual.widgets import Static

from castcode.app import CastcodeApp
from castcode.ui.input import InlineInput, Prompt
from castcode.ui.prompts import InlineChoice, QuestionPrompt, ToolApprovalPrompt


async def _noop(self):
    pass


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


def _context(**kwargs):
    return sdk.ToolPermissionContext(**kwargs)


def _status_text(app):
    # Full-tier formatting of the pushed StatusData; the live render's tier
    # depends on terminal width vs the checkout path length (cwd).
    from castcode.ui.status import StatusLine, status_line
    return status_line(app.query_one("#status", StatusLine).data, None)


def _visible_text_area(app):
    return next(field for field in app.query(InlineInput) if field.display)


async def _start_permission(app, tool_name, input_data, context):
    app._connected_ok = True
    app._start_activity()
    return asyncio.create_task(app._can_use_tool(tool_name, input_data, context))


async def _finish(task):
    return await asyncio.wait_for(task, timeout=2)


class _ModeClient:
    def __init__(self):
        self.modes = []
        self.interrupted = False

    async def set_permission_mode(self, mode):
        self.modes.append(mode)

    async def interrupt(self):
        self.interrupted = True

    async def disconnect(self):
        pass


async def test_permission_mode_cycle_updates_footer_and_client(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        client = _ModeClient()
        app.conversation.session.client = client
        app._connected_ok = True

        seen = []
        for _ in range(8):
            await app.action_cycle_permission_mode()
            await pilot.pause()
            seen.append(app.conversation.permission_mode)

        assert seen == [
            "default", "acceptEdits", "plan", "auto",
            "default", "acceptEdits", "plan", "auto",
        ]
        assert client.modes == seen
        assert "dontAsk" not in seen
        assert "bypassPermissions" not in seen
        assert "auto mode" in _status_text(app)


async def test_shift_tab_cycles_mode_before_and_after_connect(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()

        await pilot.press("shift+tab")
        await pilot.pause()

        assert app.conversation.permission_mode == "default"
        assert "default mode" in _status_text(app)
        assert app.focused is prompt

        client = _ModeClient()
        app.conversation.session.client = client
        app._connected_ok = True

        await pilot.press("shift+tab")
        await pilot.pause()

        assert app.conversation.permission_mode == "acceptEdits"
        assert client.modes == ["acceptEdits"]


async def test_tool_permission_allow_hides_and_restores_prompt(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        entry = app.query_one(Prompt)
        entry.insert("draft")
        task = await _start_permission(
            app,
            "Bash",
            {"command": "echo hi"},
            _context(title="Run command", description="echo hi"),
        )
        await pilot.pause()

        assert entry.display is False
        assert entry.text == "draft"
        assert len(app.query(ToolApprovalPrompt)) == 1
        assert "Waiting for input" in str(app._activity.render())
        prompt = app.query_one(ToolApprovalPrompt)
        assert str(prompt.query_one(".inline-title", Static).render()) == "Run command"
        assert str(prompt.query_one(".inline-description", Static).render()) == "echo hi"

        await pilot.press("enter")
        result = await _finish(task)
        await pilot.pause()

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input == {"command": "echo hi"}
        assert len(app.query(ToolApprovalPrompt)) == 0
        assert entry.display is True
        assert entry.text == "draft"
        assert app.focused is entry
        app._stop_activity()


async def test_tool_permission_prompt_renders_blocked_path_and_local_choices(monkeypatch):
    app = _make_app(monkeypatch)
    suggestion = sdk.PermissionUpdate(
        type="addRules",
        behavior="allow",
        destination="localSettings",
    )
    async with app.run_test() as pilot:
        task = await _start_permission(
            app,
            "Write",
            {"file_path": "/tmp/out.txt"},
            _context(
                title="Write file",
                description="Create /tmp/out.txt",
                blocked_path="/tmp/out.txt",
                suggestions=[suggestion],
            ),
        )
        await pilot.pause()

        prompt = app.query_one(ToolApprovalPrompt)
        labels = [
            str(row.query_one(".inline-choice-label", Static).render())
            for row in prompt.rows
        ]

        assert str(prompt.query_one(".inline-title", Static).render()) == "Write file"
        assert "Create /tmp/out.txt" in str(
            prompt.query_one(".inline-description", Static).render()
        )
        assert "Blocked path: /tmp/out.txt" in str(
            prompt.query_one(".inline-description", Static).render()
        )
        assert labels == [
            "❯ 1. Allow once",
            "  2. Allow always",
            "  3. No",
            "  4. No and stop",
        ]

        prompt.cancel()
        await _finish(task)
        app._stop_activity()


async def test_tool_permission_prompt_uses_compact_large_input_display(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        task = await _start_permission(
            app,
            "Workflow",
            {"script": "echo secret\n" * 200},
            _context(title="Run workflow"),
        )
        await pilot.pause()

        prompt = app.query_one(ToolApprovalPrompt)
        choice = prompt.rows[0].query_one(".inline-choice-description", Static)
        text = str(choice.render())

        assert "Workflow (script: 200 lines, 2.3 KB)" in text
        assert "secret" not in text

        prompt.cancel()
        await _finish(task)
        app._stop_activity()


async def test_tool_permission_space_selects_active_choice(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        task = await _start_permission(
            app,
            "Bash",
            {"command": "echo hi"},
            _context(title="Run command"),
        )
        await pilot.pause()

        await pilot.press("space")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input == {"command": "echo hi"}
        app._stop_activity()


async def test_tool_permission_allow_always_returns_local_settings_only(monkeypatch):
    app = _make_app(monkeypatch)
    suggestion = sdk.PermissionUpdate(
        type="addRules",
        behavior="allow",
        destination="localSettings",
    )
    ignored = sdk.PermissionUpdate(
        type="addRules",
        behavior="allow",
        destination="session",
    )
    async with app.run_test() as pilot:
        task = await _start_permission(
            app,
            "Bash",
            {"command": "npm test"},
            _context(title="Run command", suggestions=[suggestion, ignored]),
        )
        await pilot.pause()

        await pilot.press("2")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input == {"command": "npm test"}
        assert result.updated_permissions == [suggestion]
        app._stop_activity()


async def test_allow_always_hidden_without_local_suggestion(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        task = await _start_permission(
            app,
            "Bash",
            {"command": "rm -rf tmp"},
            _context(title="Run command"),
        )
        await pilot.pause()

        await pilot.press("2")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultDeny)
        assert result.message == "User denied this tool request"
        assert result.interrupt is False
        assert app._interrupting is False
        app._stop_activity()


async def test_tool_permission_no_and_stop_interrupts_turn(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        task = await _start_permission(
            app,
            "Bash",
            {"command": "rm -rf tmp"},
            _context(title="Run command"),
        )
        await pilot.pause()

        await pilot.press("3")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultDeny)
        assert result.message == "User denied this tool request"
        assert result.interrupt is True
        assert app._interrupting is True
        app._stop_activity()


async def test_no_and_stop_suppresses_queued_permission_prompts(monkeypatch):
    """Permission requests serialize behind one prompt: while the first is
    showing, the second waits on the lock (only one prompt mounted), and
    'No and stop' interrupts the turn so the queued request is denied."""
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app._connected_ok = True
        app._start_activity()
        first = asyncio.create_task(
            app._can_use_tool("Bash", {"command": "rm -rf tmp"}, _context())
        )
        await pilot.pause()
        second = asyncio.create_task(
            app._can_use_tool("Bash", {"command": "echo later"}, _context())
        )
        await pilot.pause()

        assert len(app.query(ToolApprovalPrompt)) == 1

        await pilot.press("3")
        first_result = await _finish(first)
        second_result = await _finish(second)
        await pilot.pause()

        assert first_result.interrupt is True
        assert isinstance(second_result, sdk.PermissionResultDeny)
        assert second_result.interrupt is True
        assert len(app.query(ToolApprovalPrompt)) == 0
        app._stop_activity()


async def test_escape_cancels_prompt_without_interrupting_turn(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        client = _ModeClient()
        app.conversation.session.client = client
        app._can_interrupt = True
        task = await _start_permission(app, "Bash", {"command": "ls"}, _context())
        await pilot.pause()

        await pilot.press("escape")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultDeny)
        assert result.message == "User cancelled this tool request"
        assert client.interrupted is False
        app._stop_activity()


async def test_permission_prompt_scrolls_into_view_even_when_chat_not_following(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test(size=(80, 12)) as pilot:
        chat = app.query_one("#chat")
        for i in range(40):
            await chat.mount(Static(f"line {i}"))
            app._stick(chat)
            await pilot.pause()

        chat.scroll_to(y=0, animate=False)
        await pilot.pause()
        assert chat.follow is False

        task = await _start_permission(app, "Bash", {"command": "ls"}, _context())
        for _ in range(10):
            await pilot.pause()
            if chat.scroll_y >= chat.max_scroll_y - 1:
                break

        assert chat.scroll_y >= chat.max_scroll_y - 1
        app.query_one(ToolApprovalPrompt).cancel()
        await _finish(task)
        app._stop_activity()


async def test_inline_prompt_stays_last_when_chat_grows(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        task = await _start_permission(app, "Bash", {"command": "ls"}, _context())
        await pilot.pause()

        chat = app.query_one("#chat")
        await chat.mount(Static("later"))
        app._stick(chat)

        assert isinstance(chat.children[-1], ToolApprovalPrompt)
        app.query_one(ToolApprovalPrompt).cancel()
        await _finish(task)
        app._stop_activity()


async def test_ask_user_question_denies_malformed_question_payloads(monkeypatch):
    """Structural validation happens at the permission boundary: payloads that
    would crash QuestionPrompt (options: null at construction, non-dict options
    in _refresh, unhashable question text as the answers key) are denied."""
    payloads = [
        {},
        {"questions": []},
        {"questions": ["not a dict"]},
        {"questions": [{"question": "?", "options": None}]},
        {"questions": [{"question": "?", "options": ["Yes", "No"]}]},
        {"questions": [{"question": {"k": 1}, "options": [{"label": "A"}]}]},
    ]
    app = _make_app(monkeypatch)
    async with app.run_test():
        for data in payloads:
            task = await _start_permission(app, "AskUserQuestion", data, _context())
            result = await _finish(task)

            assert isinstance(result, sdk.PermissionResultDeny), data
            assert result.message == "AskUserQuestion was called with no valid questions"
            assert app._permission_prompt is None
            assert not app.query(QuestionPrompt)
        app._stop_activity()


async def test_ask_user_question_tolerates_non_string_option_description(monkeypatch):
    """Non-string option content degrades (str-coerced) instead of crashing the
    render; the structurally valid question still prompts and resolves."""
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "question": "Pick",
            "options": [{"label": "A", "description": {"weird": True}}],
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        assert app.query_one(QuestionPrompt)
        await pilot.press("1")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input["answers"] == {"Pick": "A"}
        app._stop_activity()


async def test_ask_user_question_single_select(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "header": "Task",
            "question": "What should we work on?",
            "options": [
                {"label": "Tools", "description": "Tool rendering"},
                {"label": "History", "description": "Conversation history"},
            ],
            "multiSelect": False,
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Task" in title
        assert "1/1" in title

        await pilot.press("2")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input == {
            "questions": data["questions"],
            "answers": {"What should we work on?": "History"},
        }
        app._stop_activity()


async def test_ask_user_question_multi_select(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "header": "Scope",
            "question": "Which sections?",
            "options": [
                {"label": "A", "description": "Alpha"},
                {"label": "B", "description": "Beta"},
                {"label": "C", "description": "Gamma"},
            ],
            "multiSelect": True,
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("1")
        await pilot.press("3")
        await pilot.press("enter")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input["answers"] == {"Which sections?": ["A", "C"]}
        app._stop_activity()


async def test_ask_user_question_advances_through_multiple_questions(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [
            {
                "header": "Area",
                "question": "Which area?",
                "options": [
                    {"label": "UI", "description": "Interface"},
                    {"label": "Core", "description": "Runtime"},
                ],
                "multiSelect": False,
            },
            {
                "header": "Tasks",
                "question": "Which tasks?",
                "options": [
                    {"label": "Tests", "description": "Coverage"},
                    {"label": "Docs", "description": "Notes"},
                ],
                "multiSelect": True,
            },
        ]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("2")
        await pilot.pause()
        prompt = app.query_one(QuestionPrompt)
        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Tasks" in title
        assert "2/2" in title

        await pilot.press("1")
        await pilot.press("2")
        await pilot.press("enter")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input == {
            "questions": data["questions"],
            "answers": {
                "Which area?": "Core",
                "Which tasks?": ["Tests", "Docs"],
            },
        }
        app._stop_activity()


async def test_ask_user_question_left_right_switches_between_questions(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [
            {
                "header": "Area",
                "question": "Which area?",
                "options": [{"label": "UI", "description": ""}],
                "multiSelect": False,
            },
            {
                "header": "Task",
                "question": "Which task?",
                "options": [{"label": "Tests", "description": ""}],
                "multiSelect": False,
            },
        ]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        await pilot.press("right")
        await pilot.pause()
        assert "Task" in str(prompt.query_one(".inline-title", Static).render())
        assert "2/2" in str(prompt.query_one(".inline-title", Static).render())

        await pilot.press("left")
        await pilot.pause()
        assert "Area" in str(prompt.query_one(".inline-title", Static).render())
        assert "1/2" in str(prompt.query_one(".inline-title", Static).render())

        prompt.cancel()
        await _finish(task)
        app._stop_activity()


async def test_ask_user_question_waits_for_all_questions_when_answering_out_of_order(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [
            {
                "header": "Area",
                "question": "Which area?",
                "options": [
                    {"label": "UI", "description": ""},
                    {"label": "Core", "description": ""},
                ],
                "multiSelect": False,
            },
            {
                "header": "Task",
                "question": "Which task?",
                "options": [
                    {"label": "Tests", "description": ""},
                    {"label": "Docs", "description": ""},
                ],
                "multiSelect": False,
            },
        ]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("right")
        await pilot.press("2")
        await pilot.pause()

        assert task.done() is False
        prompt = app.query_one(QuestionPrompt)
        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Area" in title
        assert "1/2" in title

        await pilot.press("1")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input["answers"] == {
            "Which task?": "Docs",
            "Which area?": "UI",
        }
        app._stop_activity()


async def test_ask_user_question_free_text_answer(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "header": "Task",
            "question": "What should we work on?",
            "options": [
                {"label": "Tools", "description": "Tool rendering"},
                {"label": "History", "description": "Conversation history"},
            ],
            "multiSelect": False,
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("3")
        prompt = app.query_one(QuestionPrompt)
        field = _visible_text_area(app)
        assert field.placeholder == "Type something."
        assert field.value == ""
        field.value = "Model picker"
        await pilot.hover(prompt.rows[2])
        await pilot.pause()
        field = _visible_text_area(app)
        assert field.value == "Model picker"
        await pilot.press("enter")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input["answers"] == {
            "What should we work on?": "Model picker"
        }
        app._stop_activity()


async def test_ask_user_question_free_text_activates_without_enter(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "header": "Task",
            "question": "What should we work on?",
            "options": [
                {"label": "Tools", "description": "Tool rendering"},
                {"label": "History", "description": "Conversation history"},
            ],
            "multiSelect": False,
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        field = _visible_text_area(app)
        assert app.focused is field
        assert field.placeholder == "Type something."
        assert prompt.rows[2].styles.margin == Spacing(0, 0, 0, 0)
        label = prompt.rows[2].query_one(".inline-choice-label", Static)
        assert label.display is True
        assert "Answer in your own words" in str(label.render())
        assert str(field.styles.height) == "1"
        assert field.styles.margin == Spacing(0, 0, 0, 0)
        assert field.styles.padding == Spacing(0, 0, 0, 5)

        field.value = "Model picker"
        await pilot.press("enter")
        result = await _finish(task)

        assert isinstance(result, sdk.PermissionResultAllow)
        assert result.updated_input["answers"] == {
            "What should we work on?": "Model picker"
        }
        app._stop_activity()


async def test_ask_user_question_free_text_hover_activates_input(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "header": "Task",
            "question": "What should we work on?",
            "options": [
                {"label": "Tools", "description": "Tool rendering"},
                {"label": "History", "description": "Conversation history"},
            ],
            "multiSelect": False,
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        await pilot.hover(prompt.rows[2])
        await pilot.pause()

        field = _visible_text_area(app)
        assert app.focused is field
        assert field.placeholder == "Type something."
        label = prompt.rows[2].query_one(".inline-choice-label", Static)
        assert label.display is True
        assert "Answer in your own words" in str(label.render())

        field.value = "Hovered answer"
        await pilot.press("enter")
        result = await _finish(task)

        assert result.updated_input["answers"] == {
            "What should we work on?": "Hovered answer"
        }
        app._stop_activity()


async def test_ask_user_question_hovering_elsewhere_preserves_free_text(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "header": "Task",
            "question": "What should we work on?",
            "options": [
                {"label": "Tools", "description": "Tool rendering"},
                {"label": "History", "description": "Conversation history"},
            ],
            "multiSelect": False,
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        await pilot.hover(prompt.rows[2])
        await pilot.pause()

        field = _visible_text_area(app)
        await pilot.press(
            *list("Keep"),
            "space",
            *list("this"),
            "space",
            *list("draft"),
        )
        await pilot.pause()
        assert field.value == "Keep this draft"

        await pilot.hover(prompt.rows[0])
        await pilot.pause()

        assert prompt.freeform[0] == "Keep this draft"
        assert app.focused is field
        assert field.display is True
        assert field.value == "Keep this draft"
        labels = [
            str(row.query_one(".inline-choice-label", Static).render())
            for row in prompt.rows
            if row.display
        ]
        assert labels == [
            "  1. Tools",
            "  2. History",
            "❯ 3. Answer in your own words.",
        ]

        field.value = "Submit kept draft"
        await pilot.press("enter")
        result = await _finish(task)

        assert result.updated_input["answers"] == {
            "What should we work on?": "Submit kept draft"
        }
        app._stop_activity()


async def test_ask_user_question_left_right_navigate_free_text_boundaries(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [
            {
                "header": "Area",
                "question": "Which area?",
                "options": [{"label": "UI", "description": ""}],
                "multiSelect": False,
            },
            {
                "header": "Task",
                "question": "Which task?",
                "options": [{"label": "Tests", "description": ""}],
                "multiSelect": False,
            },
        ]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("down")
        await pilot.pause()
        field = _visible_text_area(app)
        field.value = "Custom area"
        field.cursor_position = 6

        await pilot.press("left")
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Area" in title
        assert "1/2" in title
        assert app.focused is field
        assert field.value == "Custom area"
        assert field.cursor_position == 5

        await pilot.press("right")
        await pilot.pause()

        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Area" in title
        assert "1/2" in title
        assert app.focused is field
        assert field.cursor_position == 6

        field.cursor_position = len(field.value)
        await pilot.press("right")
        await pilot.pause()

        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Task" in title
        assert "2/2" in title
        assert prompt.freeform[0] == "Custom area"
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        await pilot.press("down")
        await pilot.pause()
        field = _visible_text_area(app)
        field.value = "Custom task"
        field.cursor_position = 0

        await pilot.press("left")
        await pilot.pause()

        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Area" in title
        assert "1/2" in title
        assert prompt.freeform[1] == "Custom task"
        field = _visible_text_area(app)
        assert app.focused is field
        assert field.value == "Custom area"

        await pilot.press("enter")
        await pilot.pause()
        field = _visible_text_area(app)
        assert field.value == "Custom task"
        await pilot.press("enter")
        result = await _finish(task)

        assert result.updated_input["answers"] == {
            "Which area?": "Custom area",
            "Which task?": "Custom task",
        }
        app._stop_activity()


async def test_ask_user_question_left_right_review_answered_free_text(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [
            {
                "header": "Area",
                "question": "Which area?",
                "options": [{"label": "UI", "description": ""}],
                "multiSelect": False,
            },
            {
                "header": "Task",
                "question": "Which task?",
                "options": [{"label": "Tests", "description": ""}],
                "multiSelect": False,
            },
            {
                "header": "Priority",
                "question": "Which priority?",
                "options": [{"label": "Now", "description": ""}],
                "multiSelect": False,
            },
        ]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("down")
        await pilot.pause()
        field = _visible_text_area(app)
        field.value = "Custom area"
        await pilot.press("enter")

        await pilot.press("down")
        await pilot.pause()
        field = _visible_text_area(app)
        field.value = "Custom task"
        await pilot.press("enter")
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Priority" in title
        assert "3/3" in title
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        await pilot.press("down")
        await pilot.pause()

        field = _visible_text_area(app)
        assert app.focused is field

        await pilot.press("up")
        await pilot.pause()

        labels = [
            str(row.query_one(".inline-choice-label", Static).render())
            for row in prompt.rows
            if row.display
        ]
        assert labels == [
            "❯ 1. Now",
            "  2. Answer in your own words.",
        ]
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        await pilot.press("left")
        await pilot.pause()

        title = str(prompt.query_one(".inline-title", Static).render())
        description = str(
            prompt.rows[1].query_one(".inline-choice-description", Static).render()
        )
        assert "Task" in title
        assert "2/3" in title
        assert description == "Custom task"
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        await pilot.press("right")
        await pilot.pause()

        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Priority" in title
        assert "3/3" in title
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        await pilot.press("left")
        await pilot.pause()

        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Task" in title
        assert "2/3" in title
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        await pilot.press("left")
        await pilot.pause()

        title = str(prompt.query_one(".inline-title", Static).render())
        description = str(
            prompt.rows[1].query_one(".inline-choice-description", Static).render()
        )
        assert "Area" in title
        assert "1/3" in title
        assert description == "Custom area"
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        await pilot.press("down")
        await pilot.pause()

        labels = [
            str(row.query_one(".inline-choice-label", Static).render())
            for row in prompt.rows
            if row.display
        ]
        assert labels == [
            "❯ 1. UI",
            "  2. Answer in your own words.",
        ]

        prompt.cancel()
        await _finish(task)
        app._stop_activity()


async def test_ask_user_question_arrow_after_free_text_submit_pages_question(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [
            {
                "header": "Area",
                "question": "Which area?",
                "options": [{"label": "UI", "description": ""}],
                "multiSelect": False,
            },
            {
                "header": "Task",
                "question": "Which task?",
                "options": [{"label": "Tests", "description": ""}],
                "multiSelect": False,
            },
            {
                "header": "Priority",
                "question": "Which priority?",
                "options": [{"label": "Now", "description": ""}],
                "multiSelect": False,
            },
        ]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("down")
        await pilot.pause()
        field = _visible_text_area(app)
        field.value = "Custom area"
        await pilot.press("enter")

        await pilot.press("down")
        await pilot.pause()
        field = _visible_text_area(app)
        field.value = "Custom task"
        await pilot.press("enter", "left")
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        title = str(prompt.query_one(".inline-title", Static).render())
        description = str(
            prompt.rows[1].query_one(".inline-choice-description", Static).render()
        )
        assert "Task" in title
        assert "2/3" in title
        assert description == "Custom task"
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        await pilot.press("left")
        await pilot.pause()

        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Area" in title
        assert "1/3" in title
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        prompt.cancel()
        await _finish(task)
        app._stop_activity()


async def test_ask_user_question_up_down_can_leave_free_text_input(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "header": "Area",
            "question": "Which area?",
            "options": [
                {"label": "UI", "description": ""},
                {"label": "Core", "description": ""},
            ],
            "multiSelect": False,
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        field = _visible_text_area(app)
        field.value = "Custom area"

        await pilot.press("up")
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        labels = [
            str(row.query_one(".inline-choice-label", Static).render())
            for row in prompt.rows
            if row.display
        ]
        assert labels == [
            "  1. UI",
            "❯ 2. Core",
            "  3. Answer in your own words.",
        ]
        description = str(
            prompt.rows[2].query_one(".inline-choice-description", Static).render()
        )
        assert description == "Custom area"
        assert app.focused is prompt

        await pilot.press("down")
        await pilot.pause()
        field = _visible_text_area(app)
        assert app.focused is field
        assert field.value == "Custom area"

        prompt.cancel()
        await _finish(task)
        app._stop_activity()


async def test_ask_user_question_stale_hover_does_not_reopen_free_text(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "header": "Area",
            "question": "Which area?",
            "options": [
                {"label": "UI", "description": ""},
                {"label": "Core", "description": ""},
            ],
            "multiSelect": False,
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        await pilot.hover(prompt.rows[2])
        await pilot.pause()

        field = _visible_text_area(app)
        field.value = "Custom area"

        await pilot.press("up")
        await pilot.pause()

        prompt.on_inline_choice_hovered(InlineChoice.Hovered(2))
        await pilot.pause()

        labels = [
            str(row.query_one(".inline-choice-label", Static).render())
            for row in prompt.rows
            if row.display
        ]
        assert labels == [
            "  1. UI",
            "❯ 2. Core",
            "  3. Answer in your own words.",
        ]
        assert prompt.freeform[0] == "Custom area"
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        prompt.on_inline_choice_hovered(InlineChoice.Hovered(2))
        await pilot.pause()

        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        prompt.on_inline_choice_left(InlineChoice.Left(2))
        prompt.on_inline_choice_hovered(InlineChoice.Hovered(2))
        await pilot.pause()

        field = _visible_text_area(app)
        assert app.focused is field
        assert field.value == "Custom area"

        prompt.cancel()
        await _finish(task)
        app._stop_activity()


async def test_ask_user_question_hidden_rows_hide_free_text_input(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [
            {
                "header": "Large",
                "question": "Large question?",
                "options": [
                    {"label": "One", "description": ""},
                    {"label": "Two", "description": ""},
                    {"label": "Three", "description": ""},
                    {"label": "Four", "description": ""},
                ],
                "multiSelect": False,
            },
            {
                "header": "Small",
                "question": "Small question?",
                "options": [{"label": "Only", "description": ""}],
                "multiSelect": False,
            },
        ]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("5")
        await pilot.pause()
        field = _visible_text_area(app)
        field.value = "Large custom"
        await pilot.press("enter")
        await pilot.pause()

        prompt = app.query_one(QuestionPrompt)
        title = str(prompt.query_one(".inline-title", Static).render())
        assert "Small" in title
        assert "2/2" in title
        assert prompt.rows[4].display is False
        assert prompt.rows[4]._input.display is False
        assert [field for field in app.query(InlineInput) if field.display] == []
        assert app.focused is prompt

        prompt.cancel()
        await _finish(task)
        app._stop_activity()


async def test_ask_user_question_free_text_row_after_max_documented_choices(monkeypatch):
    app = _make_app(monkeypatch)
    data = {
        "questions": [{
            "header": "Pick",
            "question": "Which?",
            "options": [{"label": f"Opt{i}", "description": ""} for i in range(4)],
            "multiSelect": False,
        }]
    }
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", data, _context())
        await pilot.pause()

        await pilot.press("5")
        field = _visible_text_area(app)
        field.value = "custom"
        await pilot.press("enter")
        result = await _finish(task)

        assert result.updated_input["answers"] == {"Which?": "custom"}
        app._stop_activity()


async def test_ask_user_question_empty_list_denies_without_crashing(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        task = await _start_permission(app, "AskUserQuestion", {"questions": []}, _context())
        result = await _finish(task)
        await pilot.pause()

        assert isinstance(result, sdk.PermissionResultDeny)
        assert len(app.query(QuestionPrompt)) == 0
        app._stop_activity()


async def test_ask_user_question_missing_or_malformed_questions_denies(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        for data in ({}, {"questions": "nope"}, {"questions": ["bad"]}):
            task = await _start_permission(app, "AskUserQuestion", data, _context())
            result = await _finish(task)
            await pilot.pause()
            assert isinstance(result, sdk.PermissionResultDeny)
        assert len(app.query(QuestionPrompt)) == 0
        app._stop_activity()


async def test_permission_mode_reconciles_with_mid_connect_cycle(monkeypatch):
    app = CastcodeApp()

    class _ConnectClient:
        def __init__(self, options):
            self.options = options
            self.modes = []

        async def connect(self):
            app.conversation.permission_mode = "plan"

        async def set_permission_mode(self, mode):
            self.modes.append(mode)

        async def get_server_info(self):
            return None

        async def disconnect(self):
            pass

    monkeypatch.setattr(sdk, "ClaudeSDKClient", _ConnectClient)
    async with app.run_test():
        await app._connect_worker.wait()

        assert app.conversation.session.client.options.permission_mode == "auto"
        assert app.conversation.session.client.modes == ["plan"]


async def test_permission_mode_reconciles_shift_tab_during_catchup(monkeypatch):
    app = CastcodeApp()
    first_set_started = asyncio.Event()
    release_first_set = asyncio.Event()

    class _ConnectClient:
        def __init__(self, options):
            self.options = options
            self.modes = []

        async def connect(self):
            app.conversation.permission_mode = "plan"

        async def set_permission_mode(self, mode):
            self.modes.append(mode)
            if mode == "plan":
                first_set_started.set()
                await release_first_set.wait()

        async def get_server_info(self):
            return None

        async def disconnect(self):
            pass

    monkeypatch.setattr(sdk, "ClaudeSDKClient", _ConnectClient)
    async with app.run_test():
        await asyncio.wait_for(first_set_started.wait(), timeout=2)
        assert app._connected_ok is False

        await app.action_cycle_permission_mode()
        assert app.conversation.permission_mode == "auto"
        release_first_set.set()
        await app._connect_worker.wait()

        assert app.conversation.session.client.options.permission_mode == "auto"
        assert app.conversation.session.client.modes == ["plan", "auto"]
        assert app._connected_ok is True
