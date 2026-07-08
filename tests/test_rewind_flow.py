import asyncio

from textual import work

import castcode.app as app_mod
from castcode.app import CastcodeApp
from castcode.records import AssistantRecord, NoticeRecord, UserRecord
from castcode.session import ForkResult, Session
from castcode.sidecar import read_sidecar
from castcode.state import TodoState
from castcode.ui.input import Prompt
from castcode.ui.layout import Chat
from castcode.ui.pickers import RewindPicker

import fixtures as fx


async def _noop(self):
    pass


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


async def _install_records(
    app,
    records,
    checkpoints,
    last_assistant_uuid,
    session_id="s1",
    last_message_uuid=None,
):
    app.conversation.session_id = session_id
    app.conversation.transcript = list(records)
    app.conversation.checkpoints = list(checkpoints)
    app.conversation.last_assistant_uuid = last_assistant_uuid
    app.conversation.last_message_uuid = (
        last_message_uuid if last_message_uuid is not None else last_assistant_uuid
    )
    await app.query_one(Chat).rebuild(records)


async def _wait_for(pilot, predicate) -> None:
    for _ in range(100):
        await pilot.pause()
        if predicate():
            return
    assert predicate()


async def test_rewind_picker_empty_state(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/rewind")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.interaction.picker_open)

        picker = app.query_one(RewindPicker)
        assert picker.rows == []
        assert "No checkpoints captured yet." in str(picker.render())


async def test_checkpoint_alias_opens_rewind_picker(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/checkpoint")

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.interaction.picker_open)

        assert app.query_one(RewindPicker).rows == []


async def test_rewind_picker_labels_show_prompt_text(monkeypatch):
    monkeypatch.setattr(app_mod.time, "time", lambda: 1000)
    monkeypatch.setattr(Session, "session_last_modified", lambda self, session_id: 880)
    app = _make_app(monkeypatch)
    records = [
        UserRecord("first prompt", "u1"),
        AssistantRecord("one"),
        UserRecord("second prompt\nmore detail", "u2"),
    ]
    checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {"turn_index": 1, "user_uuid": "u2", "keep_uuid": "a1"},
    ]
    async with app.run_test():
        await _install_records(app, records, checkpoints, "a1")

        await app._open_rewind_picker()

        picker = app.query_one(RewindPicker)
        assert [row["title"] for row in picker.rows] == [
            "first prompt",
            "second prompt",
            "current",
        ]
        assert picker.index == 1
        assert "› second prompt" in str(picker.render())
        assert {row["subtitle"] for row in picker.rows} == {"2m ago"}
        assert "2m ago" in str(picker.render())


async def test_rewind_picker_long_list_keeps_highlight_visible(monkeypatch):
    app = _make_app(monkeypatch)
    rows = [
        {"title": f"checkpoint {index}", "checkpoint": {"turn_index": index}}
        for index in range(15)
    ]
    async with app.run_test() as pilot:
        await app._open_bottom_takeover(RewindPicker(rows))
        for _ in range(14):
            await pilot.press("down")
        await pilot.pause()

        rendered = str(app.query_one(RewindPicker).render())
        assert "› checkpoint 14" in rendered
        assert "checkpoint 0" not in rendered


async def test_rewind_picker_current_row_cancels(monkeypatch):
    app = _make_app(monkeypatch)
    records = [UserRecord("first", "u1")]
    checkpoints = [{"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}]
    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, None)
        await app._open_rewind_picker()

        await pilot.press("down", "enter")
        await pilot.pause()

        assert app.interaction.picker_open is False


async def test_rewind_picker_escape_from_operation_cancels(monkeypatch):
    app = _make_app(monkeypatch)
    records = [UserRecord("first", "u1")]
    checkpoints = [{"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}]
    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, None)
        await app._open_rewind_picker()

        await pilot.press("enter")
        await pilot.pause()
        picker = app.query_one(RewindPicker)
        assert picker.selected_checkpoint == checkpoints[0]
        assert "files unchanged" in str(picker.render())

        await pilot.press("escape")
        await pilot.pause()

        assert app.interaction.picker_open is False


async def test_first_turn_restore_makes_fresh_session(monkeypatch):
    def _fork_result(*args):
        raise AssertionError("first-turn rewind must not fork")

    monkeypatch.setattr(Session, "fork_result", _fork_result)
    app = _make_app(monkeypatch)
    records = [UserRecord("first", "u1"), AssistantRecord("one")]
    checkpoints = [{"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}]
    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, "a1")
        app.conversation.context_pct = 50
        app.conversation.todos._upsert("t1", subject="Task")

        worker = app.rewind_checkpoint(checkpoints[0], "restore")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.session_id is None
        assert app.conversation.transcript == []
        assert app.conversation.checkpoints == []
        assert app.conversation.last_assistant_uuid is None
        assert app.conversation.last_message_uuid is None
        assert app.conversation.context_pct is None
        assert app.conversation.todos == TodoState()
        assert app.interaction.transaction is None


async def test_rewind_waits_in_flight_connect_worker(monkeypatch):
    app = _make_app(monkeypatch)
    records = [UserRecord("first", "u1")]
    checkpoints = [{"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}]

    class _Worker:
        waited = False

        async def wait(self):
            self.waited = True

    waiting = _Worker()
    async with app.run_test():
        await _install_records(app, records, checkpoints, None)
        app.conversation.connect_worker = waiting

        worker = app.rewind_checkpoint(checkpoints[0], "restore")
        await worker.wait()

        assert waiting.waited is True


async def test_restore_forks_remaps_and_writes_sidecar(monkeypatch):
    monkeypatch.setattr(
        Session,
        "fork_result",
        lambda self, session_id, up_to_message_id: ForkResult(
            "forked",
            {"u1": "u1b", "a1": "a1b"},
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
    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, "a2")

        worker = app.rewind_checkpoint(checkpoints[1], "restore")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.session_id == "forked"
        assert app.conversation.transcript == [
            UserRecord("first", "u1b"),
            AssistantRecord("one"),
        ]
        assert app.conversation.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1b", "keep_uuid": None}
        ]
        assert app.conversation.last_assistant_uuid == "a1b"
        assert app.conversation.last_message_uuid == "a1b"
        env = read_sidecar(app.conversation.session.sidecar_path("forked"))
        assert env.records == app.conversation.transcript
        assert env.checkpoints == app.conversation.checkpoints


async def test_restore_fork_failure_becomes_notice(monkeypatch):
    def _raise(self, session_id, up_to_message_id):
        raise ValueError("missing uuid map for a1")

    monkeypatch.setattr(Session, "fork_result", _raise)
    app = _make_app(monkeypatch)
    records = [
        UserRecord("first", "u1"),
        AssistantRecord("one"),
        UserRecord("second", "u2"),
    ]
    checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {"turn_index": 1, "user_uuid": "u2", "keep_uuid": "a1"},
    ]
    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, "a1")
        app.interaction.transaction = "rewind"

        worker = app.rewind_checkpoint(checkpoints[1], "restore")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.transcript[-1] == NoticeRecord(
            "missing uuid map for a1"
        )
        assert app.interaction.transaction is None


async def test_rewind_leaves_source_sidecar_untouched(monkeypatch):
    monkeypatch.setattr(
        Session,
        "fork_result",
        lambda self, session_id, up_to_message_id: ForkResult(
            "forked",
            {"u1": "u1b", "a1": "a1b"},
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
        {
            "turn_index": 1,
            "user_uuid": "u2",
            "keep_uuid": "a1",
            "assistant_uuid": "a1",
        },
    ]
    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, "a2", last_message_uuid="a2")
        app._write_sidecar()
        source_path = app.conversation.session.sidecar_path("s1")
        before = read_sidecar(source_path)

        worker = app.rewind_checkpoint(checkpoints[1], "restore")
        await worker.wait()
        await pilot.pause()

        after = read_sidecar(source_path)
        assert after.records == before.records
        assert after.checkpoints == before.checkpoints
        assert after.last_assistant_uuid == before.last_assistant_uuid
        assert after.last_message_uuid == before.last_message_uuid


async def test_first_post_rewind_checkpoint_uses_remapped_keep_uuid(monkeypatch):
    monkeypatch.setattr(
        Session,
        "fork_result",
        lambda self, session_id, up_to_message_id: ForkResult(
            "forked",
            {"u1": "u1b", "a1": "a1b"},
        ),
    )
    app = _make_app(monkeypatch)
    records = [
        UserRecord("first", "u1"),
        AssistantRecord("one"),
        UserRecord("second", "u2"),
    ]
    checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {
            "turn_index": 1,
            "user_uuid": "u2",
            "keep_uuid": "a1",
            "assistant_uuid": "a1",
        },
    ]
    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, "a1")

        rewind = app.rewind_checkpoint(checkpoints[1], "restore")
        await rewind.wait()
        await pilot.pause()

        app._connected_ok = True
        app.conversation.session.client = fx.BurstFakeClient([
            fx.user_message("next", uuid="u2-new"),
            fx.assistant_message([fx.text_block("two")], uuid="a2-new"),
            fx.result_message(session_id="forked"),
        ])
        send = app.send("next")
        await send.wait()
        await pilot.pause()

        assert app.conversation.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1b", "keep_uuid": None},
            {
                "turn_index": 1,
                "user_uuid": "u2-new",
                "keep_uuid": "a1b",
                "assistant_uuid": "a1b",
            },
        ]


async def test_restore_after_assistantless_turn_forks_to_previous_user_leaf(monkeypatch):
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
        UserRecord("third", "u3"),
    ]
    checkpoints = [
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
    async with app.run_test() as pilot:
        await _install_records(
            app,
            records,
            checkpoints,
            "a1",
            last_message_uuid="u3",
        )

        worker = app.rewind_checkpoint(checkpoints[2], "restore")
        await worker.wait()
        await pilot.pause()

        assert seen == [("s1", "u2")]
        assert app.conversation.session_id == "forked"
        assert app.conversation.transcript == [
            UserRecord("first", "u1b"),
            AssistantRecord("one"),
            UserRecord("interrupted", "u2b"),
            NoticeRecord("Interrupted"),
        ]
        assert app.conversation.checkpoints == [
            {"turn_index": 0, "user_uuid": "u1b", "keep_uuid": None},
            {
                "turn_index": 1,
                "user_uuid": "u2b",
                "keep_uuid": "a1b",
                "assistant_uuid": "a1b",
            },
        ]
        assert app.conversation.last_assistant_uuid == "a1b"
        assert app.conversation.last_message_uuid == "u2b"


async def test_reload_rewinds_and_restores_prompt_text(monkeypatch):
    monkeypatch.setattr(
        Session,
        "fork_result",
        lambda self, session_id, up_to_message_id: ForkResult(
            "forked",
            {"u1": "u1b", "a1": "a1b"},
        ),
    )
    app = _make_app(monkeypatch)
    records = [
        UserRecord("first", "u1"),
        AssistantRecord("one"),
        UserRecord("second prompt", "u2"),
    ]
    checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {"turn_index": 1, "user_uuid": "u2", "keep_uuid": "a1"},
    ]
    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, "a1")

        worker = app.rewind_checkpoint(checkpoints[1], "reload")
        await worker.wait()
        await pilot.pause()

        assert app.query_one(Prompt).text == "second prompt"
        assert app.conversation.transcript == [
            UserRecord("first", "u1b"),
            AssistantRecord("one"),
        ]


async def test_rewind_sidecar_write_failure_keeps_reconnect_and_view(monkeypatch):
    monkeypatch.setattr(
        Session,
        "fork_result",
        lambda self, session_id, up_to_message_id: ForkResult(
            "forked",
            {"u1": "u1b", "a1": "a1b"},
        ),
    )
    app = _make_app(monkeypatch)
    records = [
        UserRecord("first", "u1"),
        AssistantRecord("one"),
        UserRecord("second", "u2"),
    ]
    checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {
            "turn_index": 1,
            "user_uuid": "u2",
            "keep_uuid": "a1",
            "assistant_uuid": "a1",
        },
    ]

    def _raise(*args, **kwargs):
        raise RuntimeError("write boom")

    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, "a1")
        monkeypatch.setattr(app, "_write_sidecar", _raise)

        worker = app.rewind_checkpoint(checkpoints[1], "restore")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.session_id == "forked"
        assert app.conversation.connect_worker is not None
        assert app.conversation.transcript == [
            UserRecord("first", "u1b"),
            AssistantRecord("one"),
            NoticeRecord("write boom"),
        ]
        assert app.query_one(Chat).records() == app.conversation.transcript


async def test_rewind_transaction_blocks_prompt_gap(monkeypatch):
    app = _make_app(monkeypatch)
    checkpoint = {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}
    entered = asyncio.Event()
    release = asyncio.Event()
    sent = []
    rewinds = []

    async def _close():
        entered.set()
        await release.wait()

    async def _send(self, text):
        sent.append(text)

    def _rewind(checkpoint, operation):
        rewinds.append((checkpoint, operation))

    monkeypatch.setattr(app, "_close_bottom_takeover", _close)
    monkeypatch.setattr(app, "rewind_checkpoint", _rewind)
    monkeypatch.setattr(CastcodeApp, "send", work(_send))
    async with app.run_test() as pilot:
        task = asyncio.create_task(
            app.on_rewind_picker_selected(
                RewindPicker.Selected(checkpoint, "restore")
            )
        )
        await asyncio.wait_for(entered.wait(), 1)

        old = app.conversation
        app.on_prompt_submitted(Prompt.Submitted("should not send"))
        app.on_prompt_submitted(Prompt.Submitted("/new"))
        await pilot.pause()

        assert app.interaction.transaction == "rewind"
        assert sent == []
        assert app.conversation is old
        assert rewinds == []

        release.set()
        await task
        assert rewinds == [(checkpoint, "restore")]
        assert app.interaction.transaction == "rewind"
        app.interaction.transaction = None


async def test_rewind_transaction_clears_after_error(monkeypatch):
    app = _make_app(monkeypatch)
    records = [UserRecord("first", "u1")]
    checkpoints = [{"turn_index": 0, "user_uuid": "missing", "keep_uuid": None}]
    async with app.run_test():
        await _install_records(app, records, checkpoints, None)
        app.interaction.transaction = "rewind"

        worker = app.rewind_checkpoint(checkpoints[0], "restore")
        try:
            await worker.wait()
        except Exception:
            pass

        assert app.interaction.transaction is None


async def test_double_selected_runs_a_single_rewind(monkeypatch):
    calls = []
    release = asyncio.Event()

    def _fork_result(self, session_id, up_to_message_id):
        calls.append((session_id, up_to_message_id))
        return ForkResult("f1", {"u1": "u1b", "a1": "a1b"})

    async def _parked_disconnect(self):
        await release.wait()

    monkeypatch.setattr(Session, "fork_result", _fork_result)
    monkeypatch.setattr(Session, "disconnect", _parked_disconnect)
    app = _make_app(monkeypatch)
    records = [
        UserRecord("first", "u1"),
        AssistantRecord("one"),
        UserRecord("second", "u2"),
        AssistantRecord("two"),
    ]
    checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {"turn_index": 1, "user_uuid": "u2", "keep_uuid": "a1", "assistant_uuid": "a1"},
    ]
    async with app.run_test() as pilot:
        await _install_records(app, records, checkpoints, "a2")
        await app._open_rewind_picker()

        # Key auto-repeat delivers a second Selected before the first handler's
        # worker finishes; only one rewind may execute.
        await app.on_rewind_picker_selected(
            RewindPicker.Selected(checkpoints[1], "restore")
        )
        await _wait_for(pilot, lambda: calls)
        assert app.interaction.transaction == "rewind"
        await app.on_rewind_picker_selected(
            RewindPicker.Selected(checkpoints[1], "restore")
        )
        release.set()
        await _wait_for(pilot, lambda: app.interaction.transaction is None)

        assert calls == [("s1", "a1")]
        assert app.conversation.session_id == "f1"
        assert app.conversation.transcript == [
            UserRecord("first", "u1b"),
            AssistantRecord("one"),
        ]


async def test_rewind_resets_cost_and_lands_at_transcript_tail(monkeypatch):
    def _fork_result(self, session_id, up_to_message_id):
        return ForkResult("f1", {"u1": "u1b", "a1": "a1b"})

    monkeypatch.setattr(Session, "fork_result", _fork_result)
    app = _make_app(monkeypatch)
    records = (
        [UserRecord("first", "u1")]
        + [AssistantRecord(f"reply {i}") for i in range(40)]
        + [UserRecord("second", "u2")]
    )
    checkpoints = [
        {"turn_index": 0, "user_uuid": "u1", "keep_uuid": None},
        {"turn_index": 1, "user_uuid": "u2", "keep_uuid": "a1", "assistant_uuid": "a1"},
    ]
    async with app.run_test(size=(80, 24)) as pilot:
        await _install_records(app, records, checkpoints, "a1")
        app.conversation.cost_usd = 2.5

        worker = app.rewind_checkpoint(checkpoints[1], "restore")
        await worker.wait()
        chat = app.query_one(Chat)
        await _wait_for(
            pilot,
            lambda: chat.max_scroll_y > 0 and chat.scroll_y >= chat.max_scroll_y - 1,
        )

        assert app.conversation.cost_usd == 0.0
        assert chat.max_scroll_y > 0
        assert chat.follow is True
        assert chat.scroll_y >= chat.max_scroll_y - 1


async def test_selected_clears_transaction_when_close_fails(monkeypatch):
    import pytest

    async def _boom(self):
        raise RuntimeError("close boom")

    monkeypatch.setattr(CastcodeApp, "_close_bottom_takeover", _boom)
    app = _make_app(monkeypatch)
    async with app.run_test():
        with pytest.raises(RuntimeError, match="close boom"):
            await app.on_rewind_picker_selected(
                RewindPicker.Selected({"turn_index": 0}, "restore")
            )

        assert app.interaction.transaction is None
