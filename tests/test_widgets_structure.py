"""Characterization: chat-row structure (coverage gap).

Pins the "marker and body are two SEPARATE widgets" invariant (so CSS can color only
the marker) and the per-subclass marker text. A refactor merging marker+body into one
Static would otherwise pass the whole suite. (Format-helper units live in test_format.)

Also pins Chat.rebuild(): conversation switching wipes-and-repopulates the surface,
and rebuild() must remove only ChatMessage rows while preserving the Banner that
lives inside Chat. Chat.records() must skip transient rows (permission prompts
mount in the chat but are not transcript and have no to_record()).
"""

from textual import work

from castcode.app import CastcodeApp
from castcode.records import UserRecord
from castcode.ui.layout import Banner
from castcode.ui.messages import (
    AssistantMessage,
    ChatMessage,
    NoticeMessage,
    ToolMessage,
    UserMessage,
)
from castcode.ui.prompts import Choice, ToolApprovalPrompt


async def _noop(self):
    pass


# --- marker/body are two distinct widgets, one marker text per subclass -------

async def test_rows_have_separate_marker_and_body_with_glyph(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    rows = [
        (UserMessage("hi"), ">"),
        (AssistantMessage(), "●"),
        (ToolMessage("Bash"), "●\n└─"),
        (NoticeMessage("Interrupted"), "⊘"),
    ]
    async with app.run_test() as pilot:
        chat = app.query_one("#chat")
        for row, _ in rows:
            await chat.mount(row)
        await pilot.pause()
        for row, glyph in rows:
            markers = row.query(".marker")
            bodies = row.query(".body")
            assert len(markers) == 1, f"{type(row).__name__} marker count"
            assert len(bodies) == 1, f"{type(row).__name__} body count"
            # marker and body are DISTINCT widgets
            assert markers.first() is not bodies.first()
            # the marker is a Static carrying just the row marker text
            assert str(markers.first().render()) == glyph


# --- Chat.rebuild() removes only ChatMessage rows, preserving the Banner -----

async def test_chat_rebuild_replaces_messages_but_keeps_banner(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        chat = app.query_one("#chat")
        for row in (UserMessage("hi"), AssistantMessage(), ToolMessage("Bash")):
            await chat.mount(row)
        await pilot.pause()
        assert len(chat.query(ChatMessage)) == 3
        assert len(chat.query(Banner)) == 1  # Banner lives inside Chat

        await chat.rebuild([])
        await pilot.pause()

        assert len(chat.query(ChatMessage)) == 0
        assert len(chat.query(Banner)) == 1  # survived the rebuild


# --- Chat.records() skips transient rows (mounted permission prompts) --------

async def test_chat_records_skip_mounted_permission_prompts(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        chat = app.query_one("#chat")
        await chat.mount(UserMessage("hi"))
        await chat.mount(ToolApprovalPrompt("t", "d", [Choice("allow", "Allow once")]))
        await pilot.pause()

        assert chat.records() == [UserRecord("hi")]
