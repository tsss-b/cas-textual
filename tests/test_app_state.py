import asyncio

from textual import work
from textual.widgets import Static

import castcode.app
from castcode.app import CastcodeApp
from castcode.commands import CommandRow
from castcode.interaction import InteractionState
from castcode.records import NoticeRecord
from castcode.session import Session
from castcode.state import Conversation
from castcode.ui.messages import NoticeMessage


class _ConnectFake:
    server_info = {
        "models": [
            {
                "value": "claude-sonnet",
                "displayName": "Sonnet",
                "description": "Sonnet · balanced",
            }
        ],
        "slash_commands": [
            "compact",
            {"name": "plugin:run", "description": "Run plugin"},
        ],
    }
    options_seen = []
    info_calls = 0

    def __init__(self, options=None, *args, **kwargs):
        self.options = options
        self.connected = False
        _ConnectFake.options_seen.append(options)

    async def connect(self):
        self.connected = True

    async def get_server_info(self):
        type(self).info_calls += 1
        return self.server_info

    async def disconnect(self):
        pass


async def test_connect_uses_conversation_resume_and_model(monkeypatch):
    _ConnectFake.options_seen = []
    _ConnectFake.info_calls = 0
    monkeypatch.setattr(castcode.app.sdk, "ClaudeSDKClient", _ConnectFake)
    app = CastcodeApp()
    app.conversation.session_id = "session-1"
    app.conversation.model_id = "claude-opus"

    async with app.run_test() as pilot:
        await app._connect_worker.wait()
        await pilot.pause()

    opts = _ConnectFake.options_seen[0]
    assert opts.resume == "session-1"
    assert opts.model == "claude-opus"


async def test_connect_stores_commands_and_status_only_for_active(monkeypatch):
    _ConnectFake.options_seen = []
    _ConnectFake.info_calls = 0
    monkeypatch.setattr(castcode.app.sdk, "ClaudeSDKClient", _ConnectFake)
    app = CastcodeApp()
    app.conversation.connect_error = "stale"

    async with app.run_test() as pilot:
        await app._connect_worker.wait()
        await pilot.pause()

        assert app.conversation.connected_ok is True
        assert app.conversation.connect_error is None
        assert app.conversation.model == "Sonnet"
        assert app.conversation.model_id is None
        assert app.conversation.commands == [
            CommandRow("/compact", "", "sdk"),
            CommandRow("/plugin:run", "Run plugin", "plugin"),
        ]
        assert "Sonnet" in str(app.query_one("#status", Static).render())
        assert _ConnectFake.info_calls == 1


async def test_connect_failure_is_durable_only_for_active_conversation(monkeypatch):
    active_app = {}

    class _SwitchingFail:
        def __init__(self, options=None, *args, **kwargs):
            self.options = options

        async def connect(self):
            app = active_app["app"]
            app.conversation = Conversation(session=Session())
            raise RuntimeError("boom")

        async def disconnect(self):
            pass

    monkeypatch.setattr(castcode.app.sdk, "ClaudeSDKClient", _SwitchingFail)
    app = CastcodeApp()
    active_app["app"] = app
    original = app.conversation

    async with app.run_test() as pilot:
        await original.connect_worker.wait()
        await pilot.pause()

        assert original.connect_error == "Connection failed: boom"
        assert original.transcript == []
        assert app.conversation is not original
        assert app.conversation.transcript == []
        assert list(app.query(NoticeMessage)) == []


async def test_connect_failure_appends_durable_notice_for_active(monkeypatch):
    class _Failing:
        def __init__(self, options=None, *args, **kwargs):
            self.options = options

        async def connect(self):
            raise RuntimeError("boom")

        async def disconnect(self):
            pass

    monkeypatch.setattr(castcode.app.sdk, "ClaudeSDKClient", _Failing)
    app = CastcodeApp()

    async with app.run_test() as pilot:
        await app._connect_worker.wait()
        await pilot.pause()

        assert app.conversation.transcript == [
            NoticeRecord("Connection failed: boom")
        ]
        assert [notice._body for notice in app.query(NoticeMessage)] == [
            "Connection failed: boom"
        ]


def test_app_proxy_properties_use_interaction_and_conversation_state():
    app = CastcodeApp()
    app._sending = True
    app._can_interrupt = True
    app._interrupting = True
    app._connected_ok = True
    app._connect_error = "bad"

    assert app.interaction == InteractionState(
        sending=True,
        interruptible=True,
        interrupting=True,
    )
    assert app.conversation.connected_ok is True
    assert app.conversation.connect_error == "bad"


async def test_connect_pushes_mode_changed_during_connect(monkeypatch):
    active_app = {}

    class _ModeFlip:
        def __init__(self, options=None, *args, **kwargs):
            self.options = options
            self.modes = []

        async def connect(self):
            active_app["app"].conversation.permission_mode = "plan"

        async def set_permission_mode(self, mode):
            self.modes.append(mode)

        async def get_server_info(self):
            return {"models": []}

        async def disconnect(self):
            pass

    monkeypatch.setattr(castcode.app.sdk, "ClaudeSDKClient", _ModeFlip)
    app = CastcodeApp()
    active_app["app"] = app

    async with app.run_test():
        await app._connect_worker.wait()
        assert app.conversation.session.client.modes == ["plan"]
        assert app._connected_ok is True


async def test_on_unmount_waits_connect_worker_before_disconnect(monkeypatch):
    order = []

    async def _slow_connect(self):
        await asyncio.sleep(0.01)
        order.append("connect done")

    class _Client:
        async def disconnect(self):
            order.append("disconnect")

    monkeypatch.setattr(CastcodeApp, "connect", work(_slow_connect))
    app = CastcodeApp()

    async with app.run_test():
        app.conversation.session.client = _Client()
        await app.on_unmount()
        app.conversation.session.client = None

    assert order == ["connect done", "disconnect"]


async def test_connect_resolves_label_for_requested_model(monkeypatch):
    _ConnectFake.options_seen = []
    _ConnectFake.info_calls = 0
    monkeypatch.setattr(castcode.app.sdk, "ClaudeSDKClient", _ConnectFake)
    app = CastcodeApp()
    app.conversation.model_id = "claude-sonnet"
    app.conversation.model = "stale label"

    async with app.run_test() as pilot:
        await app._connect_worker.wait()
        await pilot.pause()

        assert app.conversation.model == "Sonnet"


async def test_connect_keeps_restored_label_for_unknown_model(monkeypatch):
    # A reconnect must not overwrite a sidecar-restored label with the
    # server's default row when the requested model has no matching row.
    _ConnectFake.options_seen = []
    _ConnectFake.info_calls = 0
    monkeypatch.setattr(castcode.app.sdk, "ClaudeSDKClient", _ConnectFake)
    app = CastcodeApp()
    app.conversation.session_id = "session-1"
    app.conversation.model_id = "claude-opus"
    app.conversation.model = "Opus 4.8"

    async with app.run_test() as pilot:
        await app._connect_worker.wait()
        await pilot.pause()

        assert app.conversation.model == "Opus 4.8"
