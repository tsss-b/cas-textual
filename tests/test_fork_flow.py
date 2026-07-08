from textual import work

from castcode.app import CastcodeApp
from castcode.records import AssistantRecord, NoticeRecord, ToolRecord, UserRecord
from castcode.session import ForkResult, Session
from castcode.sidecar import read_sidecar
from castcode.ui.input import Prompt
from castcode.ui.layout import Chat


async def _noop(self):
    pass


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


async def _submit(app, pilot, text: str) -> None:
    prompt = app.query_one(Prompt)
    prompt.focus()
    prompt.insert(text)
    await pilot.press("enter")
    await pilot.pause()


async def test_fork_empty_checkpoints_notice(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/fork")

        assert app.conversation.transcript == [
            NoticeRecord("No checkpoints captured yet.")
        ]
        assert app.interaction.transaction is None


async def test_fork_no_conversation_point_notice(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.checkpoints = [
            {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}
        ]

        await _submit(app, pilot, "/fork")

        assert app.conversation.transcript == [
            NoticeRecord("No conversation point to fork at.")
        ]
        assert app.interaction.transaction is None


async def test_fork_error_becomes_notice_and_clears_transaction(monkeypatch):
    def _raise(self, session_id, up_to_message_id):
        raise RuntimeError("fork boom")

    monkeypatch.setattr(Session, "fork_result", _raise)
    app = _make_app(monkeypatch)
    app.conversation.session_id = "source"
    app.conversation.transcript = [UserRecord("first", "u1")]
    app.conversation.checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}
    ]
    app.conversation.last_assistant_uuid = "a1"
    async with app.run_test() as pilot:
        await app.query_one(Chat).rebuild(app.conversation.transcript)

        await _submit(app, pilot, "/fork")

        assert app.conversation.transcript[-1] == NoticeRecord("fork boom")
        assert app.interaction.transaction is None


async def test_fork_branches_full_transcript_remaps_and_writes_sidecar(monkeypatch):
    monkeypatch.setattr(
        Session,
        "fork_result",
        lambda self, session_id, up_to_message_id: ForkResult(
            "forked",
            {
                "u1": "u1b",
                "u2": "u2b",
                "a1": "a1b",
                "a2": "a2b",
            },
        ),
    )
    app = _make_app(monkeypatch)
    records = [
        UserRecord("first", "u1"),
        AssistantRecord("one"),
        UserRecord("second", "u2"),
        AssistantRecord("two"),
    ]
    checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {"turn_index": 1, "user_uuid": "u2", "keep_uuid": "a1"},
    ]
    app.conversation.session_id = "source"
    app.conversation.transcript = list(records)
    app.conversation.checkpoints = list(checkpoints)
    app.conversation.last_assistant_uuid = "a2"
    app.conversation.permission_mode = "plan"
    app.conversation.model = "Sonnet"
    app.conversation.model_id = "claude-sonnet"
    app.conversation.cost_usd = 1.25
    app.conversation.todos._upsert("t1", subject="Task")
    parent = app.conversation
    async with app.run_test() as pilot:
        await app.query_one(Chat).rebuild(records)

        await _submit(app, pilot, "/fork")

        assert app.conversation is not parent
        assert app.conversation.session_id == "forked"
        assert app.conversation.transcript == [
            UserRecord("first", "u1b"),
            AssistantRecord("one"),
            UserRecord("second", "u2b"),
            AssistantRecord("two"),
            ToolRecord("/fork", "/fork", "Branched conversation", state="done"),
        ]
        assert app.conversation.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1b", "keep_uuid": None},
            {"turn_index": 1, "user_uuid": "u2b", "keep_uuid": "a1b"},
        ]
        assert app.conversation.last_assistant_uuid == "a2b"
        assert app.conversation.permission_mode == "plan"
        assert app.conversation.model == "Sonnet"
        assert app.conversation.model_id == "claude-sonnet"
        assert app.conversation.cost_usd == 0.0
        assert app.conversation.todos == parent.todos
        assert app.conversation.todos is not parent.todos
        assert app.conversation.todos.tasks["t1"] is not parent.todos.tasks["t1"]
        assert parent.transcript == records
        assert parent.checkpoints == checkpoints

        env = read_sidecar(app.conversation.session.sidecar_path("forked"))
        assert env.records == app.conversation.transcript
        assert env.checkpoints == app.conversation.checkpoints
        assert env.last_assistant_uuid == "a2b"

        source_env = read_sidecar(parent.session.sidecar_path("source"))
        assert source_env.records == records
        assert source_env.checkpoints == checkpoints
        assert source_env.last_assistant_uuid == "a2"
        assert source_env.model_id == "claude-sonnet"
        assert source_env.model == "Sonnet"


async def test_fork_uses_latest_user_leaf_after_assistantless_turn(monkeypatch):
    seen = []

    def _fork_result(self, session_id, up_to_message_id):
        seen.append((session_id, up_to_message_id))
        return ForkResult(
            "forked",
            {"u1": "u1b", "a1": "a1b", "u2": "u2b"},
        )

    monkeypatch.setattr(Session, "fork_result", _fork_result)
    app = _make_app(monkeypatch)
    records = [
        UserRecord("first", "u1"),
        AssistantRecord("one"),
        UserRecord("interrupted", "u2"),
        NoticeRecord("Interrupted"),
    ]
    app.conversation.session_id = "source"
    app.conversation.transcript = list(records)
    app.conversation.checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {
            "turn_index": 1,
            "user_uuid": "u2",
            "keep_uuid": "a1",
            "assistant_uuid": "a1",
        },
    ]
    app.conversation.last_assistant_uuid = "a1"
    app.conversation.last_message_uuid = "u2"
    async with app.run_test() as pilot:
        await app.query_one(Chat).rebuild(records)

        await _submit(app, pilot, "/fork")

        assert seen == [("source", "u2")]
        assert app.conversation.session_id == "forked"
        assert app.conversation.transcript == [
            UserRecord("first", "u1b"),
            AssistantRecord("one"),
            UserRecord("interrupted", "u2b"),
            NoticeRecord("Interrupted"),
            ToolRecord("/fork", "/fork", "Branched conversation", state="done"),
        ]
        assert app.conversation.last_assistant_uuid == "a1b"
        assert app.conversation.last_message_uuid == "u2b"


async def test_fork_of_fork_remaps_through_two_generations(monkeypatch):
    calls = []

    def _fork_result(self, session_id, up_to_message_id):
        calls.append((session_id, up_to_message_id))
        if len(calls) == 1:
            return ForkResult(
                "fork-1",
                {
                    "u1": "u1b",
                    "u2": "u2b",
                    "a1": "a1b",
                    "a2": "a2b",
                },
            )
        return ForkResult(
            "fork-2",
            {
                "u1b": "u1c",
                "u2b": "u2c",
                "a1b": "a1c",
                "a2b": "a2c",
            },
        )

    monkeypatch.setattr(Session, "fork_result", _fork_result)
    app = _make_app(monkeypatch)
    records = [
        UserRecord("first", "u1"),
        AssistantRecord("one"),
        UserRecord("second", "u2"),
        AssistantRecord("two"),
    ]
    app.conversation.session_id = "source"
    app.conversation.transcript = list(records)
    app.conversation.checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {
            "turn_index": 1,
            "user_uuid": "u2",
            "keep_uuid": "a1",
            "assistant_uuid": "a1",
        },
    ]
    app.conversation.last_assistant_uuid = "a2"
    app.conversation.last_message_uuid = "a2"
    async with app.run_test() as pilot:
        await app.query_one(Chat).rebuild(records)

        await _submit(app, pilot, "/fork")
        await _submit(app, pilot, "/fork")

        assert calls == [("source", "a2"), ("fork-1", "a2b")]
        assert app.conversation.session_id == "fork-2"
        assert app.conversation.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1c", "keep_uuid": None},
            {
                "turn_index": 1,
                "user_uuid": "u2c",
                "keep_uuid": "a1c",
                "assistant_uuid": "a1c",
            },
        ]
        assert app.conversation.last_assistant_uuid == "a2c"
        assert app.conversation.last_message_uuid == "a2c"
