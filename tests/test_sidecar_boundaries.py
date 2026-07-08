from textual import work

from castcode.app import CastcodeApp
from castcode.records import AssistantRecord, NoticeRecord, ToolRecord, UserRecord
from castcode.sidecar import read_sidecar
from castcode.ui.input import Prompt
from castcode.ui.messages import NoticeMessage, UserMessage

import fixtures as fx


async def _noop(self):
    pass


async def _wait_for(pilot, predicate) -> None:
    for _ in range(100):
        await pilot.pause()
        if predicate():
            return
    assert predicate()


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


async def test_sidecar_written_after_send(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(fx.text_turn("hello"))
        app._connected_ok = True
        worker = app.send("hi")
        await worker.wait()
        await pilot.pause()

        path = app.conversation.session.sidecar_path("s")
        env = read_sidecar(path)
        assert env.token is None
        assert env.records == [UserRecord("hi"), AssistantRecord("hello")]
        assert env.model == app.conversation.model


async def test_sidecar_written_after_failed_send_on_resumed_session(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session_id = "resumed"
        app._connected_ok = False
        app._connect_error = "Connection failed: nope"
        worker = app.send("hi")
        await worker.wait()
        await pilot.pause()

        env = read_sidecar(app.conversation.session.sidecar_path("resumed"))
        assert env.records == [
            UserRecord("hi"),
            NoticeRecord("Connection failed: nope"),
        ]


async def test_clear_writes_outgoing_sidecar(monkeypatch):
    app = _make_app(monkeypatch)
    old = app.conversation
    async with app.run_test() as pilot:
        old.session_id = "s1"
        await app.query_one("#chat").mount(UserMessage("hi", uuid="u1"))
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/clear")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.conversation is not old)

        env = read_sidecar(old.session.sidecar_path("s1"))
        assert env.records == [UserRecord("hi", "u1")]
        assert app.conversation.transcript == [
            ToolRecord("/clear", "/clear", "Started new conversation", state="done")
        ]


async def test_idle_quit_snapshots_and_writes_sidecar(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test():
        app.conversation.session_id = "s1"
        await app.query_one("#chat").mount(UserMessage("hi", uuid="u1"))
        await app.action_quit()

        env = read_sidecar(app.conversation.session.sidecar_path("s1"))
        assert env.records == [UserRecord("hi", "u1")]


async def test_non_idle_quit_does_not_snapshot_partial_state(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test():
        app.conversation.session_id = "s1"
        await app.query_one("#chat").mount(UserMessage("hi", uuid="u1"))
        app._sending = True
        await app.action_quit()

        assert not app.conversation.session.sidecar_path("s1").exists()


async def test_no_sidecar_write_without_session_id(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient([
            fx.assistant_message([fx.text_block("hello")]),
            fx.result_message(session_id=""),
        ])
        app._connected_ok = True
        worker = app.send("hi")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.session_id in (None, "")
        assert not app.conversation.session.sidecar_path("s").parent.exists()


async def test_connect_failure_notice_reconciles_with_snapshot(monkeypatch):
    class _Failing:
        def __init__(self, options=None, *args, **kwargs):
            self.options = options

        async def connect(self):
            raise RuntimeError("boom")

        async def disconnect(self):
            pass

    monkeypatch.setattr(CastcodeApp, "connect", CastcodeApp.connect)
    monkeypatch.setattr("castcode.app.sdk.ClaudeSDKClient", _Failing)
    app = CastcodeApp()
    async with app.run_test() as pilot:
        await app._connect_worker.wait()
        await pilot.pause()
        app._snapshot_transcript()

        assert app.conversation.transcript == [NoticeRecord("Connection failed: boom")]
        assert [notice._body for notice in app.query(NoticeMessage)] == [
            "Connection failed: boom"
        ]


async def test_cancelled_send_still_snapshots_and_writes_sidecar(monkeypatch):
    import pytest
    from textual.worker import WorkerCancelled

    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        client = fx.GatedFakeClient()
        app.conversation.session.client = client
        app.conversation.session_id = "s1"
        app._connected_ok = True

        from castcode.ui.messages import ToolMessage

        worker = app.send("hi")
        await client.feed(fx.stream_event(fx.ev_tool_start(0, "t1", "Read")))
        for _ in range(100):
            await pilot.pause()
            if app.query("#chat ToolMessage"):
                break
        assert app.query_one(ToolMessage).state == "running"

        worker.cancel()
        with pytest.raises(WorkerCancelled):
            await worker.wait()
        await pilot.pause()

        env = read_sidecar(app.conversation.session.sidecar_path("s1"))
        assert env.records[0] == UserRecord("hi")
        tool = env.records[1]
        assert isinstance(tool, ToolRecord)
        assert tool.state == "error"


async def test_cancelled_send_with_open_permission_prompt_still_writes_sidecar(monkeypatch):
    """Quit mid-turn while a permission prompt is showing: the prompt is still
    mounted in #chat when run_turn's finally snapshots (its removal lives in the
    SDK-side task's finally, which has not run) — the snapshot must skip it and
    the sidecar write must still land."""
    import asyncio

    import claude_agent_sdk as sdk
    import pytest
    from textual.worker import WorkerCancelled

    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        client = fx.GatedFakeClient()
        app.conversation.session.client = client
        app.conversation.session_id = "s1"
        app._connected_ok = True

        from castcode.ui.messages import ToolMessage

        worker = app.send("hi")
        await client.feed(fx.stream_event(fx.ev_tool_start(0, "t1", "Bash")))
        await _wait_for(pilot, lambda: bool(app.query("#chat ToolMessage")))
        assert app.query_one(ToolMessage).state == "running"

        task = asyncio.create_task(
            app._can_use_tool("Bash", {"command": "ls"}, sdk.ToolPermissionContext())
        )
        await _wait_for(
            pilot,
            lambda: app._permission_prompt is not None
            and app._permission_prompt.is_mounted,
        )

        worker.cancel()
        with pytest.raises(WorkerCancelled):
            await worker.wait()
        await pilot.pause()

        env = read_sidecar(app.conversation.session.sidecar_path("s1"))
        assert env.records == [
            UserRecord("hi"),
            ToolRecord("Bash", "Bash", state="error"),
        ]

        app._permission_prompt.cancel()
        result = await asyncio.wait_for(task, timeout=2)
        assert isinstance(result, sdk.PermissionResultDeny)
