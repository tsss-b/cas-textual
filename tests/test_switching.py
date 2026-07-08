import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from textual import work
import claude_agent_sdk as sdk

import castcode.app as app_mod
from castcode.app import CastcodeApp
from castcode.format import CWD
from castcode.records import NoticeRecord, UserRecord
from castcode.session import Session
from castcode.sidecar import dump_records, read_sidecar, write_sidecar
from castcode.switching import (
    CORRUPT_VIEW,
    MISSING_SESSION,
    MISSING_VIEW,
)
from castcode.ui.input import Prompt
from castcode.ui.layout import Chat
from castcode.ui.messages import NoticeMessage, UserMessage
from castcode.ui.pickers import Switcher

import fixtures as fx


async def _noop(self):
    pass


async def _wait_for(pilot, predicate) -> None:
    for _ in range(100):
        await pilot.pause()
        if predicate():
            return
    assert predicate()


def _info(session_id, summary="Session", last_modified=100):
    return SimpleNamespace(
        session_id=session_id,
        summary=summary,
        first_prompt=summary,
        last_modified=last_modified,
        cwd=None,
        git_branch=None,
        tag=None,
    )


def _make_app(monkeypatch, infos):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    monkeypatch.setattr(Session, "list_sessions", lambda self: infos)
    return CastcodeApp()


def _track(app, session_id, records=None, token=100):
    write_sidecar(
        app.conversation.session.sidecar_path(session_id),
        token,
        records or [UserRecord("saved", "u1")],
        [],
        None,
        None,
        None,
        None,
    )


def _track_state(
    app,
    session_id,
    *,
    token=100,
    records=None,
    checkpoints=None,
    last_assistant_uuid=None,
    last_message_uuid=None,
):
    write_sidecar(
        app.conversation.session.sidecar_path(session_id),
        token,
        records or [UserRecord("saved", "u1")],
        checkpoints or [],
        last_assistant_uuid,
        last_message_uuid,
        None,
        None,
    )


def _session_file(session_id, entries):
    root = (
        Path(os.environ["CLAUDE_CONFIG_DIR"])
        / "projects"
        / sdk.project_key_for_directory(CWD)
    )
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{session_id}.jsonl"
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
    )
    return path


async def test_switcher_opens_from_command_and_escape_restores_regions(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second"), _info("s3", "Untracked")])
    _track(app, "s2")
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("/switch")

        await pilot.press("enter")
        await pilot.pause()

        assert app.interaction.picker_open is True
        assert app.query_one(Chat).can_focus is False
        assert prompt.display is False
        assert app.query_one("#status").display is False
        assert [row["session_id"] for row in app.query_one(Switcher).rows] == ["s2"]

        await pilot.press("escape")
        await pilot.pause()

        assert app.interaction.picker_open is False
        assert app.query_one(Chat).can_focus is True
        assert prompt.display is True
        assert app.query_one("#status").display is True


async def test_switcher_opens_from_ctrl_r_and_excludes_current_session(monkeypatch):
    app = _make_app(monkeypatch, [_info("s1", "Current"), _info("s2", "Second")])
    app.conversation.session_id = "s1"
    _track(app, "s1")
    _track(app, "s2")
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()

        rows = app.query_one(Switcher).rows
        assert [row["session_id"] for row in rows] == ["s2"]


async def test_switcher_hides_sessions_without_sidecar(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()

        switcher = app.query_one(Switcher)
        assert switcher.rows == []
        assert str(switcher.render()) == "No saved sessions"


async def test_switcher_marks_out_of_band_drift(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second", last_modified=200)])
    _track_state(app, "s2", last_assistant_uuid="a1", last_message_uuid="a1")
    _session_file("s2", [
        {"type": "user", "uuid": "u1"},
        {"type": "assistant", "uuid": "a1"},
        {"type": "user", "uuid": "u2"},
        {"type": "assistant", "uuid": "a2"},
        {"type": "last-prompt"},
    ])
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()

        rows = app.query_one(Switcher).rows
        assert rows[0]["subtitle"] == "changed outside castcode"


async def test_switcher_ignores_cli_housekeeping_after_anchor(monkeypatch):
    # The CLI flushes the turn's assistant entry after the sidecar token is
    # captured and appends last-prompt/ai-title on disconnect; an interrupted
    # turn also leaves a trailing user entry. None of that is drift.
    app = _make_app(monkeypatch, [
        _info("s2", "Second", last_modified=200),
        _info("s3", "Third", last_modified=200),
    ])
    _track_state(app, "s2", last_assistant_uuid="a1", last_message_uuid="a1")
    _session_file("s2", [
        {"type": "user", "uuid": "u1"},
        {"type": "assistant", "uuid": "a1"},
        {"type": "user", "uuid": "u2", "message": {"content": [
            {"type": "text", "text": "[Request interrupted by user]"},
        ]}},
        {"type": "last-prompt"},
        {"type": "ai-title"},
    ])
    _track(app, "s3", token=None)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()

        rows = {row["session_id"]: row for row in app.query_one(Switcher).rows}
        assert rows["s2"]["subtitle"] == ""
        assert rows["s3"]["subtitle"] == ""


async def test_switcher_no_drift_when_anchor_or_file_missing(monkeypatch):
    app = _make_app(monkeypatch, [
        _info("s2", "Second", last_modified=200),
        _info("s3", "Third", last_modified=200),
    ])
    _track_state(app, "s2", last_assistant_uuid="gone", last_message_uuid="gone")
    _session_file("s2", [
        {"type": "user", "uuid": "u1"},
        {"type": "assistant", "uuid": "a1"},
    ])
    _track_state(app, "s3", last_assistant_uuid="a1", last_message_uuid="a1")
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()

        rows = {row["session_id"]: row for row in app.query_one(Switcher).rows}
        assert rows["s2"]["subtitle"] == ""
        assert rows["s3"]["subtitle"] == ""


async def test_resume_missing_sidecar_notice_for_direct_row(monkeypatch):
    app = _make_app(monkeypatch, [])
    async with app.run_test():
        await app.on_switcher_selected(Switcher.Selected({"session_id": "s2"}))

        assert app.conversation.session_id == "s2"
        assert app.conversation.transcript == [NoticeRecord(MISSING_VIEW)]


async def test_resume_corrupt_sidecar_notice(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    app.conversation.session.sidecar_path("s2").parent.mkdir(parents=True, exist_ok=True)
    app.conversation.session.sidecar_path("s2").write_text("{bad", encoding="utf-8")
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.conversation.transcript == [NoticeRecord(CORRUPT_VIEW)]
        assert app.query_one(NoticeMessage)._body == CORRUPT_VIEW


async def test_resume_drift_advanced_keeps_saved_view_without_notice(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    path = app.conversation.session.sidecar_path("s2")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        dump_records([UserRecord("saved", "u1")], 100),
        encoding="utf-8",
    )
    monkeypatch.setattr(Session, "session_last_modified", lambda self, session_id: 200)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.conversation.transcript == [UserRecord("saved", "u1")]


async def test_resume_drift_vanished_notice(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    _track_state(
        app,
        "s2",
        records=[UserRecord("saved", "u1")],
        checkpoints=[{"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}],
        last_assistant_uuid="a1",
        last_message_uuid="a1",
    )
    monkeypatch.setattr(Session, "session_last_modified", lambda self, session_id: None)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.conversation.transcript == [
            UserRecord("saved", "u1"),
            NoticeRecord(MISSING_SESSION),
        ]
        assert app.conversation.session_id is None
        assert app.conversation.checkpoints == []
        assert app.conversation.last_assistant_uuid is None
        assert app.conversation.last_message_uuid is None


async def test_resume_restores_sidecar_state_for_next_checkpoint(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    path = app.conversation.session.sidecar_path("s2")
    checkpoints = [{"turn_index": 0, "user_uuid": "u1", "keep_uuid": None}]
    write_sidecar(
        path,
        100,
        [UserRecord("saved", "u1")],
        checkpoints,
        "a1",
        "a1",
        "m1",
        "Model One",
    )
    monkeypatch.setattr(Session, "session_last_modified", lambda self, session_id: 100)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        conv = app.conversation
        assert conv.session_id == "s2"
        assert conv.transcript == [UserRecord("saved", "u1")]
        assert conv.checkpoints == checkpoints
        assert conv.last_assistant_uuid == "a1"
        assert conv.last_message_uuid == "a1"
        assert conv.model_id == "m1"
        assert conv.model == "Model One"

        app._connected_ok = True
        conv.session.client = fx.BurstFakeClient([
            fx.user_message("next", uuid="u2"),
            fx.assistant_message([fx.text_block("reply")], uuid="a2"),
            fx.result_message(session_id="s2"),
        ])
        worker = app.send("next")
        await worker.wait()
        await pilot.pause()

        assert conv.checkpoints[-1] == {
            "turn_index": 1,
            "user_uuid": "u2",
            "keep_uuid": "a1",
            "assistant_uuid": "a1",
        }


async def test_switch_writes_outgoing_sidecar_and_preserves_draft(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    app.conversation.session_id = "s1"
    _track(app, "s2")
    old = app.conversation
    async with app.run_test() as pilot:
        await app.query_one("#chat").mount(UserMessage("old", uuid="u1"))
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("draft text")

        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        env = read_sidecar(old.session.sidecar_path("s1"))
        assert env.records == [UserRecord("old", "u1")]
        assert old.draft == "draft text"


async def test_switch_transaction_blocks_send_gap(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def _resume(app, row):
        started.set()
        await release.wait()

    async def _send(self, text):
        sent.append(text)

    monkeypatch.setattr(app_mod, "resume_cold", _resume)
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    monkeypatch.setattr(CastcodeApp, "send", work(_send))
    async with app.run_test() as pilot:
        task = asyncio.create_task(
            app.on_switcher_selected(Switcher.Selected({"session_id": "s2"}))
        )
        await asyncio.wait_for(started.wait(), 1)

        app.on_prompt_submitted(Prompt.Submitted("should not send"))
        await pilot.pause()

        assert app.interaction.transaction == "switch"
        assert sent == []
        release.set()
        await task
        assert app.interaction.transaction is None


async def test_switch_error_becomes_notice_and_clears_transaction(monkeypatch):
    async def _resume(app, row):
        raise RuntimeError("switch boom")

    monkeypatch.setattr(app_mod, "resume_cold", _resume)
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    async with app.run_test():
        await app.on_switcher_selected(Switcher.Selected({"session_id": "s2"}))

        assert app.conversation.transcript == [NoticeRecord("switch boom")]
        assert app.interaction.transaction is None


async def test_switcher_long_list_keeps_highlight_visible(monkeypatch):
    infos = [_info(f"s{i}", f"Session {i}") for i in range(12)]
    app = _make_app(monkeypatch, infos)
    for info in infos:
        _track(app, info.session_id)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()

        for _ in range(11):
            await pilot.press("down")
        await pilot.pause()

        rendered = str(app.query_one(Switcher).render())
        assert "› Session 11" in rendered
        assert "Session 0" not in rendered


async def test_resume_restores_permission_mode_and_draft(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    write_sidecar(
        app.conversation.session.sidecar_path("s2"),
        None,
        [UserRecord("saved", "u1")],
        [],
        None,
        None,
        None,
        None,
        "plan",
        "unsent draft",
    )
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.conversation.session_id == "s2"
        assert app.conversation.permission_mode == "plan"
        assert app.conversation.draft == "unsent draft"
        assert app.query_one(Prompt).text == "unsent draft"


async def test_resume_ignores_unknown_permission_mode(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    write_sidecar(
        app.conversation.session.sidecar_path("s2"),
        None,
        [UserRecord("saved", "u1")],
        [],
        None,
        None,
        None,
        None,
        "bogus-mode",
        "",
    )
    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.conversation.permission_mode == "auto"


async def test_outgoing_sidecar_persists_permission_mode_and_draft(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    app.conversation.session_id = "s1"
    app.conversation.permission_mode = "plan"
    _track(app, "s2")
    old = app.conversation
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("draft text")

        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        env = read_sidecar(old.session.sidecar_path("s1"))
        assert env.permission_mode == "plan"
        assert env.draft == "draft text"


async def test_resume_lands_at_transcript_tail_with_follow(monkeypatch):
    app = _make_app(monkeypatch, [_info("s2", "Second")])
    write_sidecar(
        app.conversation.session.sidecar_path("s2"),
        None,
        [UserRecord(f"message {i}", f"u{i}") for i in range(40)],
        [],
        None,
        None,
        None,
        None,
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("enter")
        chat = app.query_one("#chat", Chat)
        await _wait_for(
            pilot,
            lambda: chat.max_scroll_y > 0 and chat.scroll_y >= chat.max_scroll_y - 1,
        )

        assert chat.max_scroll_y > 0
        assert chat.follow is True
        assert chat.scroll_y >= chat.max_scroll_y - 1
