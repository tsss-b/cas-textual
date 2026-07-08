import asyncio

import pytest
from textual import work
from textual.css.query import NoMatches

from castcode.app import CastcodeApp
from castcode.records import NoticeRecord, ToolRecord
from castcode.session import ModelRow, Session
from castcode.state import Conversation
from castcode.ui.input import Prompt
from castcode.ui.pickers import ModelPicker


async def _noop(self):
    pass


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


async def _submit(app, pilot, text: str) -> None:
    app.on_prompt_submitted(Prompt.Submitted(text))
    await pilot.pause()


async def _wait_for(pilot, predicate) -> None:
    for _ in range(100):
        await pilot.pause()
        if predicate():
            return
    assert predicate()


async def test_model_not_connected_notice(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")

        assert app.conversation.transcript == [NoticeRecord("not connected")]


async def test_model_fetch_does_not_mark_picker_open_early(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _model_rows(self):
        started.set()
        await release.wait()
        return [ModelRow("m1", "Model One", "detail")]

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await asyncio.wait_for(started.wait(), 5)

        assert app.interaction.picker_open is False
        assert app.interaction.local_command_pending is True

        release.set()
        await _wait_for(pilot, lambda: app.interaction.picker_open)

        assert app.interaction.picker_open is True
        assert app.interaction.local_command_pending is False
        assert app.query_one(ModelPicker).rows == [
            {"model_id": "m1", "title": "Model One", "detail": "detail"}
        ]


async def test_model_picker_long_list_keeps_highlight_visible(monkeypatch):
    app = _make_app(monkeypatch)
    rows = [
        {"model_id": f"m{index}", "title": f"Model {index}", "detail": ""}
        for index in range(15)
    ]
    async with app.run_test() as pilot:
        await app._open_bottom_takeover(ModelPicker(rows))
        for _ in range(14):
            await pilot.press("down")
        await pilot.pause()

        rendered = str(app.query_one(ModelPicker).render())
        assert "› Model 14" in rendered
        assert "Model 0" not in rendered


async def test_model_picker_renders_literal_brackets(monkeypatch):
    app = _make_app(monkeypatch)
    rows = [{"model_id": "m1", "title": "Model [x] [/]", "detail": "detail[i]"}]
    async with app.run_test() as pilot:
        await app._open_bottom_takeover(ModelPicker(rows))
        await pilot.pause()

        assert str(app.query_one(ModelPicker).render()) == "› Model [x] [/]  detail[i]"


async def test_model_fetch_blocks_concurrent_submit(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def _model_rows(self):
        started.set()
        await release.wait()
        return [ModelRow("m1", "Model One")]

    async def _send(self, text):
        sent.append(text)

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    monkeypatch.setattr(CastcodeApp, "send", work(_send))
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await asyncio.wait_for(started.wait(), 5)

        app.on_prompt_submitted(Prompt.Submitted("hello"))
        await pilot.pause()

        assert sent == []
        assert app._sending is False
        assert app.interaction.local_command_pending is True

        release.set()
        await _wait_for(pilot, lambda: app.interaction.picker_open)


async def test_stale_model_rows_drop_if_conversation_changes(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _model_rows(self):
        started.set()
        await release.wait()
        return [ModelRow("m1", "Model One")]

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await asyncio.wait_for(started.wait(), 5)

        app.conversation = Conversation(session=Session())
        release.set()
        await pilot.pause()

        assert app.interaction.picker_open is False
        with pytest.raises(NoMatches):
            app.query_one(ModelPicker)
        app.interaction.transaction = None


async def test_stale_model_rows_drop_if_turn_starts(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _model_rows(self):
        started.set()
        await release.wait()
        return [ModelRow("m1", "Model One")]

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await asyncio.wait_for(started.wait(), 5)

        app._sending = True
        release.set()
        await pilot.pause()

        assert app.interaction.picker_open is False
        with pytest.raises(NoMatches):
            app.query_one(ModelPicker)


async def test_stale_model_rows_drop_if_transaction_starts(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _model_rows(self):
        started.set()
        await release.wait()
        return [ModelRow("m1", "Model One")]

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await asyncio.wait_for(started.wait(), 5)

        app.interaction.transaction = "other"
        release.set()
        await pilot.pause()

        assert app.interaction.picker_open is False
        with pytest.raises(NoMatches):
            app.query_one(ModelPicker)


async def test_model_picker_cancel_restores_prompt(monkeypatch):
    async def _model_rows(self):
        return [ModelRow("m1", "Model One")]

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await _wait_for(pilot, lambda: app.interaction.picker_open)

        await pilot.press("escape")
        await pilot.pause()

        assert app.interaction.picker_open is False
        assert app.query_one(Prompt).display is True
        assert app.interaction.transaction is None


async def test_model_selection_updates_status_and_echoes(monkeypatch):
    seen = []

    async def _model_rows(self):
        return [ModelRow("m1", "Model One", "detail")]

    async def _set_model(self, model_id):
        seen.append((model_id, app.interaction.transaction))
        return "Model One"

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    monkeypatch.setattr(Session, "set_model", _set_model)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await _wait_for(pilot, lambda: app.interaction.picker_open)
        await pilot.press("enter")
        await _wait_for(pilot, lambda: seen)

        assert seen == [("m1", "model")]
        assert app.conversation.model_id == "m1"
        assert app.conversation.model == "Model One"
        assert app.conversation.transcript == [
            ToolRecord("/model", "/model", "Model One", state="done")
        ]
        assert app.interaction.transaction is None


async def test_model_selection_uses_picker_title_when_sdk_returns_no_label(monkeypatch):
    async def _model_rows(self):
        return [ModelRow("m1", "Picker Title", "detail")]

    async def _set_model(self, model_id):
        return None

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    monkeypatch.setattr(Session, "set_model", _set_model)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await _wait_for(pilot, lambda: app.interaction.picker_open)
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.conversation.transcript)

        assert app.conversation.model == "Picker Title"
        assert app.conversation.transcript == [
            ToolRecord("/model", "/model", "Picker Title", state="done")
        ]


async def test_model_selection_error_becomes_notice_and_clears_transaction(monkeypatch):
    async def _model_rows(self):
        return [ModelRow("m1", "Model One")]

    async def _set_model(self, model_id):
        raise RuntimeError("model boom")

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    monkeypatch.setattr(Session, "set_model", _set_model)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await _wait_for(pilot, lambda: app.interaction.picker_open)

        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.conversation.transcript)

        assert app.conversation.transcript == [NoticeRecord("model boom")]
        assert app.interaction.transaction is None


async def test_context_not_connected_notice(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/context")

        assert app.conversation.transcript == [NoticeRecord("not connected")]


async def test_context_echoes_live_usage(monkeypatch):
    async def _context_usage(self):
        return "12.5% context used"

    monkeypatch.setattr(Session, "context_usage", _context_usage)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/context")

        assert app.conversation.transcript == [
            ToolRecord("/context", "/context", "12.5% context used", state="done")
        ]


async def test_context_fetch_holds_pending_and_blocks_submit(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def _context_usage(self):
        started.set()
        await release.wait()
        return "usage"

    async def _send(self, text):
        sent.append(text)

    monkeypatch.setattr(Session, "context_usage", _context_usage)
    monkeypatch.setattr(CastcodeApp, "send", work(_send))
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/context")
        await asyncio.wait_for(started.wait(), 5)

        app.on_prompt_submitted(Prompt.Submitted("hello"))
        await pilot.pause()

        assert sent == []
        assert app.interaction.local_command_pending is True

        release.set()
        await _wait_for(pilot, lambda: app.conversation.transcript)

        assert app.interaction.local_command_pending is False
        assert app.conversation.transcript == [
            ToolRecord("/context", "/context", "usage", state="done")
        ]


async def test_stale_context_output_drops_if_conversation_changes(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _context_usage(self):
        started.set()
        await release.wait()
        return "usage"

    monkeypatch.setattr(Session, "context_usage", _context_usage)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/context")
        await asyncio.wait_for(started.wait(), 5)

        app.conversation = Conversation(session=Session())
        release.set()
        await pilot.pause()

        assert app.conversation.transcript == []


async def test_stale_context_output_drops_if_turn_starts(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _context_usage(self):
        started.set()
        await release.wait()
        return "usage"

    monkeypatch.setattr(Session, "context_usage", _context_usage)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/context")
        await asyncio.wait_for(started.wait(), 5)

        app._sending = True
        release.set()
        await pilot.pause()

        assert app.conversation.transcript == []


async def test_stale_context_output_drops_if_picker_opens(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def _context_usage(self):
        started.set()
        await release.wait()
        return "usage"

    monkeypatch.setattr(Session, "context_usage", _context_usage)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/context")
        await asyncio.wait_for(started.wait(), 5)

        app.interaction.picker_open = True
        release.set()
        await pilot.pause()

        assert app.conversation.transcript == []
        app.interaction.picker_open = False


async def test_model_selection_writes_sidecar_at_its_boundary(monkeypatch):
    from castcode.sidecar import read_sidecar

    async def _model_rows(self):
        return [ModelRow("m1", "Model One", "detail")]

    async def _set_model(self, model_id):
        return "Model One"

    monkeypatch.setattr(Session, "model_rows", _model_rows)
    monkeypatch.setattr(Session, "set_model", _set_model)
    app = _make_app(monkeypatch)
    app._connected_ok = True
    app.conversation.session_id = "s1"
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/model")
        await _wait_for(pilot, lambda: app.interaction.picker_open)
        await pilot.press("enter")
        await _wait_for(pilot, lambda: app.interaction.transaction is None)

        env = read_sidecar(app.conversation.session.sidecar_path("s1"))
        assert env.model_id == "m1"
        assert env.model == "Model One"
