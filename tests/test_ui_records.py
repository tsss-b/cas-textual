from textual.app import App, ComposeResult
from textual import work
from textual.widgets import Static

from castcode.app import CastcodeApp
from castcode.records import (
    AssistantRecord,
    BlockPreview,
    NoticeRecord,
    ToolRecord,
    UserRecord,
)
from castcode.ui.layout import Banner, Chat
from castcode.ui.messages import (
    AssistantMessage,
    NoticeMessage,
    ToolMessage,
    UserMessage,
    from_record,
)
import fixtures as fx


class _Host(App):
    def compose(self) -> ComposeResult:
        yield Chat(Banner(), id="chat")


async def test_user_message_round_trips_uuid():
    app = _Host()
    async with app.run_test() as pilot:
        msg = UserMessage("hello", uuid="u1")
        await app.query_one(Chat).mount(msg)
        await pilot.pause()

        assert msg.to_record() == UserRecord("hello", "u1")


async def test_user_and_notice_messages_render_literal_brackets():
    app = _Host()
    async with app.run_test() as pilot:
        user = UserMessage("list[i] and [/]", uuid="u1")
        notice = NoticeMessage("notice [x] [/]")
        await app.query_one(Chat).mount(user)
        await app.query_one(Chat).mount(notice)
        await pilot.pause()

        bodies = [str(widget.query_one(".body", Static).render()) for widget in (user, notice)]
        assert bodies == ["list[i] and [/]", "notice [x] [/]"]
        assert user.to_record() == UserRecord("list[i] and [/]", "u1")
        assert notice.to_record() == NoticeRecord("notice [x] [/]")


async def test_user_message_stamped_uuid_round_trips():
    app = _Host()
    async with app.run_test() as pilot:
        msg = UserMessage("hello")
        await app.query_one(Chat).mount(msg)
        msg.set_uuid("u2")
        await pilot.pause()

        assert msg.to_record() == UserRecord("hello", "u2")


async def test_assistant_message_round_trips_source():
    app = _Host()
    async with app.run_test() as pilot:
        msg = AssistantMessage()
        await app.query_one(Chat).mount(msg)
        await msg.update_text("reply")
        await pilot.pause()

        assert msg.to_record() == AssistantRecord("reply")


async def test_streamed_assistant_message_snapshots_source(monkeypatch):
    async def _noop(self):
        pass

    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient([
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "hello ")),
            fx.stream_event(fx.ev_text_delta(0, "world")),
            fx.stream_event(fx.ev_stop(0)),
            fx.assistant_message([fx.text_block("hello world")]),
            fx.result_message(),
        ])
        app._connected_ok = True
        worker = app.send("hi")
        await worker.wait()
        await pilot.pause()

        assert AssistantRecord("hello world") in app.query_one(Chat).records()


async def test_notice_message_round_trips():
    assert from_record(NoticeRecord("notice")).to_record() == NoticeRecord("notice")


async def test_tool_message_round_trips_string_preview_and_error():
    app = _Host()
    async with app.run_test() as pilot:
        msg = ToolMessage(
            "Bash",
            "pytest",
            "ok",
            tool_name="Bash",
            state="error",
            error="boom",
            detail_error=True,
        )
        await app.query_one(Chat).mount(msg)
        await pilot.pause()

        assert msg.to_record() == ToolRecord(
            "Bash",
            "Bash",
            "pytest",
            "ok",
            "error",
            [],
            "boom",
            True,
        )
        assert msg.query_one(".tool-error", Static).display is True
        assert msg.has_class("-error")
        assert msg.has_class("-detail-error")


async def test_tool_message_round_trips_change_preview():
    rows = [{"kind": "add", "line": "1", "marker": "+", "text": "alpha"}]
    msg = ToolMessage("Write", preview=rows, tool_name="Write", state="done")

    assert msg.to_record() == ToolRecord("Write", "Write", preview=rows, state="done")


async def test_tool_message_preserves_empty_preview_kinds():
    assert ToolMessage("Write", preview=[]).to_record().preview == []
    assert ToolMessage("Read", preview=BlockPreview("", "")).to_record().preview == (
        BlockPreview("", "")
    )


async def test_tool_message_round_trips_block_preview():
    preview = BlockPreview("2 lines", "alpha\nbeta")
    msg = ToolMessage("Read", preview=preview, tool_name="Read", state="done")

    assert msg.to_record() == ToolRecord("Read", "Read", preview=preview, state="done")


async def test_tool_message_stores_full_subtool_rows():
    rows = [
        {"state": "done", "title": f"tool {index}", "detail": "ok"}
        for index in range(5)
    ]
    msg = ToolMessage("Agent", tool_name="Agent", subtools=rows)

    assert msg.to_record().subtools == rows


async def test_tool_message_snapshot_excludes_subtool_overflow_row():
    rows = [
        {"state": "done", "title": f"tool {index}", "detail": "ok"}
        for index in range(5)
    ]
    app = _Host()
    async with app.run_test() as pilot:
        msg = ToolMessage("Agent", tool_name="Agent", subtools=rows)
        await app.query_one(Chat).mount(msg)
        await pilot.pause()

        rendered_titles = [
            str(widget.render())
            for widget in msg.query(".tool-subtool-title")
            if str(widget.render())
        ]
        assert "…" in rendered_titles
        assert msg.to_record().subtools == rows


async def test_tool_message_snapshot_deep_copies_rows():
    row = {"kind": "add", "line": "1", "marker": "+", "text": "alpha"}
    msg = ToolMessage("Write", preview=[row], subtools=[{"title": "Read"}])
    record = msg.to_record()

    row["text"] = "changed"
    record.subtools[0]["title"] = "changed"

    assert msg.to_record().preview[0]["text"] == "alpha"
    assert msg.to_record().subtools[0]["title"] == "Read"


def test_from_record_builds_expected_message_types():
    assert isinstance(from_record(UserRecord("hi", "u1")), UserMessage)
    assert isinstance(from_record(AssistantRecord("reply")), AssistantMessage)
    assert isinstance(from_record(NoticeRecord("notice")), NoticeMessage)
    assert isinstance(from_record(ToolRecord("Read", "Read")), ToolMessage)


async def test_chat_rebuild_preserves_banner_and_snapshots_records():
    records = [
        UserRecord("hi", "u1"),
        AssistantRecord("reply"),
        NoticeRecord("notice"),
        ToolRecord("Read", "Read", "file.py", "1 line read", "done"),
    ]
    app = _Host()
    async with app.run_test() as pilot:
        chat = app.query_one(Chat)
        await chat.rebuild(records)
        await pilot.pause()

        assert len(chat.query(Banner)) == 1
        assert chat.records() == records


async def test_chat_rebuild_restores_tool_block_preview_and_subtools():
    record = ToolRecord(
        "Agent",
        "Agent",
        preview=BlockPreview("2 lines", "alpha\nbeta"),
        state="done",
        subtools=[{"state": "done", "title": "Read", "detail": "file.py"}],
    )
    app = _Host()
    async with app.run_test() as pilot:
        chat = app.query_one(Chat)
        await chat.rebuild([record])
        await pilot.pause()
        tool = chat.query_one(ToolMessage)

        assert str(tool.query_one(".tool-block-preview", Static).render()) == "alpha\nbeta"
        assert tool.query_one(".tool-block-preview", Static).display is True
        assert tool.query_one(".tool-subtools").display is True
        assert tool.to_record() == record
