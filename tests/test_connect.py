"""Connection-failure visibility (app.py connect + conversation.run_turn).

A failed connect must not die silently. connect() catches the error, records it,
and surfaces a NoticeMessage; a send while disconnected still mounts the user's
message and a notice instead of the send worker dying before mounting anything
(the original bug: prompt clears, UI says nothing).
"""

import claude_agent_sdk as sdk

from castcode.app import CastcodeApp
from castcode.ui.messages import NoticeMessage, UserMessage


class _FailingClient:
    def __init__(self, options):
        self.options = options

    async def connect(self):
        raise RuntimeError("claude CLI not found")

    async def disconnect(self):
        pass


def _notice_texts(app):
    return [str(n._body) for n in app.query(NoticeMessage)]


async def test_failed_connect_shows_notice(monkeypatch):
    monkeypatch.setattr(sdk, "ClaudeSDKClient", _FailingClient)
    app = CastcodeApp()
    async with app.run_test() as pilot:
        await app._connect_worker.wait()  # connect catches the failure and returns cleanly
        await pilot.pause()
        await pilot.pause()

        assert app._connected_ok is False
        assert "claude CLI not found" in (app._connect_error or "")
        assert any("Connection failed" in text for text in _notice_texts(app))


async def test_send_while_disconnected_mounts_message_and_notice(monkeypatch):
    monkeypatch.setattr(sdk, "ClaudeSDKClient", _FailingClient)
    app = CastcodeApp()
    async with app.run_test() as pilot:
        await app._connect_worker.wait()
        await pilot.pause()

        worker = app.send("hello there")
        await worker.wait()
        await pilot.pause()

        chat = app.query_one("#chat")
        # the user's message is mounted, not lost before mounting
        assert any(u._body == "hello there" for u in chat.query(UserMessage))
        # the turn ended cleanly and re-enabled sending
        assert app._sending is False
        # visible feedback exists (proactive notice + the send-path notice)
        assert len(chat.query(NoticeMessage)) >= 1
