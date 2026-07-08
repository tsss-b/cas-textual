from textual import work
from textual.widgets import Static

from castcode.app import CastcodeApp
from castcode.commands import CommandRow
from castcode.records import ToolRecord, UserRecord
from castcode.sidecar import read_sidecar
from castcode.ui.input import CommandPopup, Prompt
from castcode.ui.layout import Chat
from castcode.ui.messages import ToolMessage, UserMessage

import fixtures as fx


async def _noop(self):
    pass


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


async def _wait_for(pilot, predicate) -> None:
    for _ in range(100):
        await pilot.pause()
        if predicate():
            return
    assert predicate()


async def test_help_is_local_echo_and_not_sent(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(fx.text_turn("reply"))
        app._connected_ok = True
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/help")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.conversation.transcript)

        assert app.conversation.session.client.queries == []
        tool = app.query_one(ToolMessage)
        assert str(tool.query_one(".marker", Static).render()) == "●\n└─"
        assert tool.to_record().tool_name == "/help"
        assert tool.to_record().title == "/help"
        assert tool.to_record().detail.startswith("Local commands:")
        assert app.conversation.transcript == [tool.to_record()]


async def test_unknown_slash_passes_through(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(fx.text_turn("reply"))
        app._connected_ok = True
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/unknown arg")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.conversation.session.client.queries)

        assert app.conversation.session.client.queries == ["/unknown arg"]


async def test_plugin_slash_command_passes_through(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(fx.text_turn("reply"))
        app.conversation.commands = [
            CommandRow("/plugin:run", "Run plugin", "sdk")
        ]
        app._connected_ok = True
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/plugin:run arg")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.conversation.session.client.queries)

        assert app.conversation.session.client.queries == ["/plugin:run arg"]


async def test_local_command_gate_sets_pending_and_transaction_before_worker(monkeypatch):
    seen = {}

    def _capture(self, command, handler, exclusive):
        seen["state"] = (
            command,
            handler,
            exclusive,
            self.interaction.local_command_pending,
            self.interaction.transaction,
        )

    app = _make_app(monkeypatch)
    monkeypatch.setattr(CastcodeApp, "local_command", _capture)
    async with app.run_test():
        app.on_prompt_submitted(Prompt.Submitted("/clear"))

    assert seen["state"] == ("/clear", "new", True, True, "/clear")


async def test_local_command_blocked_while_not_idle(monkeypatch):
    calls = []

    def _capture(self, command, handler, exclusive):
        calls.append(command)

    app = _make_app(monkeypatch)
    monkeypatch.setattr(CastcodeApp, "local_command", _capture)
    async with app.run_test():
        app._sending = True
        app.on_prompt_submitted(Prompt.Submitted("/help"))

    assert calls == []


async def test_clear_starts_fresh_conversation(monkeypatch):
    app = _make_app(monkeypatch)
    old_conversation = app.conversation
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/clear")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.conversation is not old_conversation)

        assert app.conversation.transcript == [
            ToolRecord("/clear", "/clear", "Started new conversation", state="done")
        ]
        assert app.conversation.session_id is None
        assert app.interaction.transaction is None


async def test_new_starts_fresh_conversation_and_echoes(monkeypatch):
    app = _make_app(monkeypatch)
    old_conversation = app.conversation
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/new")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.conversation is not old_conversation)

        assert app.conversation is not old_conversation
        assert app.conversation.transcript == [
            ToolRecord("/new", "/new", "Started new conversation", state="done")
        ]
        assert app.conversation.session_id is None
        assert app.interaction.transaction is None


async def test_new_disconnects_outgoing_session(monkeypatch):
    app = _make_app(monkeypatch)
    old_client = fx.BurstFakeClient()
    old = app.conversation
    async with app.run_test() as pilot:
        old.session.client = old_client
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/new")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: old_client.disconnected)

        assert old_client.disconnected is True
        assert app.conversation is not old


async def test_new_writes_outgoing_sidecar_and_refreshes_todos(monkeypatch):
    app = _make_app(monkeypatch)
    old = app.conversation
    old.session_id = "old-session"
    async with app.run_test() as pilot:
        await app.query_one(Chat).mount(UserMessage("old", uuid="u1"))
        old.todos._upsert("t1", subject="Task")
        app._refresh_todos()
        assert app.query_one("#todos").display is True

        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/new")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.query_one("#todos").display is False)

        env = read_sidecar(old.session.sidecar_path("old-session"))
        assert env.records == [UserRecord("old", "u1")]
        assert app.query_one("#todos").display is False


async def test_popup_rows_group_local_sdk_plugin(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test():
        app.conversation.commands = [
            CommandRow("/doctor", "Diagnostics", "sdk"),
            CommandRow("/review", "Review code", "skill"),
            CommandRow("/plugin:run", "Run plugin", "sdk"),
        ]
        app._refresh_prompt_commands()
        rows = app.query_one(Prompt).command_rows
        names = [row["name"] for row in rows]

    assert names.index("/help") < names.index("/doctor")
    assert names.index("/doctor") < names.index("/review")
    assert names.index("/review") < names.index("/plugin:run")
    assert rows[names.index("/review")]["source"] == "skill"
    assert rows[names.index("/plugin:run")]["source"] == "plugin"


async def test_popup_render_sections_and_multiline_descriptions(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.set_command_rows([
            {"name": "/help", "description": "Show commands", "source": "local"},
            {
                "name": "/review",
                "description": "Review code\nwith extra context",
                "source": "skill",
            },
            {"name": "/plugin:run", "description": "Run plugin", "source": "plugin"},
        ])
        prompt.insert("/")
        prompt._refresh_command_popup()
        await pilot.pause()

        await pilot.press("down")
        await pilot.pause()

        renderable = app.query_one(CommandPopup).query_one(Static).render()
        body = str(renderable)
        assert "  == Castcode commands ==" in body
        assert "  == Skills ==" in body
        assert "  == Plugins ==" in body
        assert "› /review  Review code with extra context" in body
        assert "\nwith extra context" not in body
        bold_ranges = {
            renderable.plain[span.start:span.end]
            for span in renderable.spans
            if str(span.style) == "bold"
        }
        assert bold_ranges == {
            "  == Castcode commands ==",
            "  == Skills ==",
            "  == Plugins ==",
        }


async def test_popup_section_headers_keep_selected_row_in_height_budget(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.set_command_rows([
            {"name": "/local-a", "description": "Local A", "source": "local"},
            {"name": "/local-b", "description": "Local B", "source": "local"},
            {"name": "/local-c", "description": "Local C", "source": "local"},
            {"name": "/doctor", "description": "Diagnostics", "source": "sdk"},
            {"name": "/review", "description": "Review code", "source": "skill"},
            {"name": "/plugin:run", "description": "Run plugin", "source": "plugin"},
        ])
        prompt.insert("/")
        prompt._refresh_command_popup()
        await pilot.pause()

        for _ in range(5):
            await pilot.press("down")
        await pilot.pause()

        body = str(app.query_one(CommandPopup).query_one(Static).render())
        assert len(body.splitlines()) == 10
        assert "  == Castcode commands ==" in body
        assert "  == Claude commands ==" in body
        assert "  == Skills ==" in body
        assert "  == Plugins ==" in body
        assert "› /plugin:run  Run plugin" in body


async def test_popup_opens_for_row_zero_slash_and_accepts_without_submit(monkeypatch):
    sent = []

    async def _send(self, text):
        sent.append(text)

    app = _make_app(monkeypatch)
    monkeypatch.setattr(CastcodeApp, "send", work(_send))
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        app._refresh_prompt_commands()
        prompt.insert("/h")
        prompt._refresh_command_popup()
        await pilot.pause()

        popup = app.query_one(CommandPopup)
        assert popup.display is True
        assert "/help" in str(popup.query_one(Static).render())

        await pilot.press("enter")
        await pilot.pause()

        assert prompt.text == "/help "
        assert sent == []
        assert len(app.query(UserMessage)) == 0


async def test_popup_accept_preserves_arguments(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        app._refresh_prompt_commands()
        prompt.insert("/mo sonnet")
        prompt._refresh_command_popup()
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert prompt.text == "/model sonnet"


async def test_popup_char_by_char_complete_command_submits(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        app._refresh_prompt_commands()

        await pilot.press("/", "h", "e", "l", "p")
        await pilot.pause()
        assert app.query_one(CommandPopup).display is True

        await pilot.press("enter")
        await pilot.pause()
        record = app.query_one(ToolMessage).to_record()
        assert record == ToolRecord("/help", "/help", record.detail, state="done")
        assert record.detail.startswith("Local commands:")


async def test_popup_tab_completes_partial_name_without_submit(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        app._refresh_prompt_commands()

        await pilot.press("/", "m", "o")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()

        assert prompt.text == "/model "
        assert len(app.query(ToolMessage)) == 0


async def test_popup_enter_submits_completed_plugin_command_with_args(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(fx.text_turn("reply"))
        app.conversation.commands = [
            CommandRow("/plugin:run", "Run plugin", "sdk")
        ]
        app._connected_ok = True
        app._refresh_prompt_commands()
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/plugin:run arg")
        prompt._refresh_command_popup()
        await pilot.pause()

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.conversation.session.client.queries)

        assert app.conversation.session.client.queries == ["/plugin:run arg"]


async def test_app_close_command_popup_clears_prompt_popup_state(monkeypatch):
    sent = []

    async def _send(self, text):
        sent.append(text)

    monkeypatch.setattr(CastcodeApp, "send", work(_send))
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        app._refresh_prompt_commands()
        prompt.insert("/h")
        prompt._refresh_command_popup()
        await pilot.pause()

        assert prompt._popup_rows
        assert app.query_one(CommandPopup).display is True

        app._close_command_popup()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert prompt._popup_rows == []
        assert prompt.text == ""
        assert sent == ["/h"]


async def test_popup_long_list_keeps_highlight_visible(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.set_command_rows([
            {"name": f"/cmd{i}", "description": f"Command {i}", "source": "sdk"}
            for i in range(10)
        ])
        prompt.insert("/")
        prompt._refresh_command_popup()
        await pilot.pause()

        for _ in range(9):
            await pilot.press("down")
        await pilot.pause()

        body = str(app.query_one(CommandPopup).query_one(Static).render())
        assert "› /cmd9" in body
        assert "/cmd0" not in body


async def test_popup_escape_closes_before_app_interrupt(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        app._refresh_prompt_commands()
        prompt.insert("/h")
        prompt._refresh_command_popup()
        await pilot.pause()
        assert app.query_one(CommandPopup).display is True

        await pilot.press("escape")
        await pilot.pause()

        assert app.query_one(CommandPopup).display is False
        assert app._interrupting is False


async def test_popup_does_not_open_during_interruptible_turn(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app._can_interrupt = True
        prompt = app.query_one(Prompt)
        prompt.focus()
        app._refresh_prompt_commands()
        prompt.insert("/h")
        prompt._refresh_command_popup()
        await pilot.pause()

        assert app.query_one(CommandPopup).display is False


async def test_escape_dismissed_popup_stays_closed_until_text_changes(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        await pilot.press("/", "m")
        assert prompt._popup_rows

        await pilot.press("escape")
        assert prompt._popup_rows == []

        # Cursor movement and a second escape leave the text unchanged: the
        # dismissal must hold (the old behavior reopened on any keypress).
        await pilot.press("left", "right", "escape")
        assert prompt._popup_rows == []

        # Editing the text lifts the latch.
        await pilot.press("o")
        assert prompt._popup_rows


async def test_paste_over_slash_text_refreshes_popup_and_submits(monkeypatch):
    from textual import events

    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        await pilot.press("/", "m", "o")
        assert prompt._popup_rows

        prompt.select_all()
        await prompt._on_paste(events.Paste("hello there, please fix the login bug"))
        await pilot.pause()

        assert prompt.text == "hello there, please fix the login bug"
        assert prompt._popup_rows == []

        # Enter must submit the pasted text, not rewrite it via a stale
        # popup accept.
        await pilot.press("enter")
        await _wait_for(
            pilot, lambda: app.query("#chat UserMessage") and prompt.text == ""
        )
        user = app.query_one(UserMessage)
        assert user.to_record().text == "hello there, please fix the login bug"
