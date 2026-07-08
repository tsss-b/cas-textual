from textual import work

from castcode.app import CastcodeApp
from castcode.records import AssistantRecord, UserRecord
from castcode.sidecar import read_sidecar
from castcode.ui.layout import Chat

import fixtures as fx


async def _noop(self):
    pass


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


async def test_checkpoint_captured_from_replayed_user_and_stamped_on_widget(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("hi", uuid="u1"),
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "reply")),
            fx.stream_event(fx.ev_stop(0)),
            fx.assistant_message([fx.text_block("reply")], uuid="a1"),
            fx.result_message(session_id="s1"),
        ])
        app._connected_ok = True
        worker = app.send("hi")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}
        ]
        assert app.conversation.last_assistant_uuid == "a1"
        assert app.conversation.last_message_uuid == "a1"
        assert app.query_one(Chat).records() == [
            UserRecord("hi", "u1"),
            AssistantRecord("reply"),
        ]


async def test_checkpoint_keep_uuid_uses_previous_main_assistant(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app._connected_ok = True
        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("first", uuid="u1"),
            fx.assistant_message([fx.text_block("one")], uuid="a1"),
            fx.result_message(session_id="s1"),
        ])
        first = app.send("first")
        await first.wait()
        await pilot.pause()

        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("second", uuid="u2"),
            fx.assistant_message([fx.text_block("two")], uuid="a2"),
            fx.result_message(session_id="s1"),
        ])
        second = app.send("second")
        await second.wait()
        await pilot.pause()

        assert app.conversation.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
            {
                "turn_index": 1,
                "user_uuid": "u2",
                "keep_uuid": "a1",
                "assistant_uuid": "a1",
            },
        ]
        assert app.conversation.last_assistant_uuid == "a2"
        assert app.conversation.last_message_uuid == "a2"


async def test_checkpoint_after_assistantless_turn_uses_previous_user_leaf(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app._connected_ok = True
        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("first", uuid="u1"),
            fx.assistant_message([fx.text_block("one")], uuid="a1"),
            fx.result_message(session_id="s1"),
        ])
        first = app.send("first")
        await first.wait()
        await pilot.pause()

        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("interrupted", uuid="u2"),
            fx.result_message(session_id="s1"),
        ])
        interrupted = app.send("interrupted")
        await interrupted.wait()
        await pilot.pause()

        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("third", uuid="u3"),
            fx.assistant_message([fx.text_block("three")], uuid="a3"),
            fx.result_message(session_id="s1"),
        ])
        third = app.send("third")
        await third.wait()
        await pilot.pause()

        assert app.conversation.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
            {
                "turn_index": 1,
                "user_uuid": "u2",
                "keep_uuid": "a1",
                "assistant_uuid": "a1",
            },
            {
                "turn_index": 2,
                "user_uuid": "u3",
                "keep_uuid": "u2",
                "assistant_uuid": "a1",
            },
        ]
        assert app.conversation.last_assistant_uuid == "a3"
        assert app.conversation.last_message_uuid == "a3"


async def test_local_command_replay_ignored_for_checkpoints(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("<local-command-help>", uuid="u-local"),
            fx.result_message(session_id="s1"),
        ])
        app._connected_ok = True
        worker = app.send("normal")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.checkpoints == []


async def test_nested_user_message_ignored_for_checkpoints(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("nested", uuid="u-nested", parent_tool_use_id="agent-1"),
            fx.result_message(session_id="s1"),
        ])
        app._connected_ok = True
        worker = app.send("normal")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.checkpoints == []


async def test_duplicate_replayed_user_message_does_not_orphan_checkpoint(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("hi", uuid="u1"),
            fx.user_message("hi duplicate", uuid="u2"),
            fx.result_message(session_id="s1"),
        ])
        app._connected_ok = True
        worker = app.send("hi")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}
        ]
        assert app.conversation.last_message_uuid == "u1"
        assert app.query_one(Chat).records() == [UserRecord("hi", "u1")]


async def test_checkpoint_state_written_to_sidecar(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("hi", uuid="u1"),
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "reply")),
            fx.stream_event(fx.ev_stop(0)),
            fx.assistant_message([fx.text_block("reply")], uuid="a1"),
            fx.result_message(session_id="s1"),
        ])
        app._connected_ok = True
        worker = app.send("hi")
        await worker.wait()
        await pilot.pause()

        env = read_sidecar(app.conversation.session.sidecar_path("s1"))
        assert env.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}
        ]
        assert env.last_assistant_uuid == "a1"
        assert env.last_message_uuid == "a1"
