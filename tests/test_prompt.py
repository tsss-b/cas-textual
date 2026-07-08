"""Characterization tests for prompt key handling + send serialization."""

from textual import work

from textual.widgets import Static
from castcode.app import CastcodeApp
from castcode.ui.input import Prompt
from castcode.ui.messages import UserMessage

import fixtures as fx


async def _noop(self):
    pass


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


def _install_client(app):
    app.conversation.session.client = fx.BurstFakeClient(fx.text_turn("reply"))
    app._connected_ok = True


# --- Enter with text submits ------------------------------------------------

async def test_enter_with_text_submits_and_clears(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("hello there")
        assert prompt.text == "hello there"

        await pilot.press("enter")
        await pilot.pause()

        # prompt cleared completely
        assert prompt.text == ""
        # a UserMessage was mounted
        users = app.query(UserMessage)
        assert len(users) == 1
        # the send worker queried the accepted text
        assert app.conversation.session.client.queries == ["hello there"]


async def test_enter_user_message_has_no_trailing_newline(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("exact text")
        await pilot.press("enter")
        await pilot.pause()

        # body of the UserMessage holds only the accepted text, no trailing newline
        user = app.query_one(UserMessage)
        assert user._body == "exact text"
        assert not user._body.endswith("\n")
        assert app.query_one(".body", Static).render() == "exact text"
        assert app.conversation.session.client.queries == ["exact text"]


async def test_enter_strips_surrounding_whitespace_in_query(monkeypatch):
    # Submitted carries self.text.strip(); pin that the stripped text is what flows.
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("  padded  ")
        await pilot.press("enter")
        await pilot.pause()

        assert app.conversation.session.client.queries == ["padded"]
        assert prompt.text == ""


# --- Shift+Enter inserts newline, does NOT submit ---------------------------

async def test_shift_enter_inserts_newline_no_submit(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("line1")

        await pilot.press("shift+enter")
        await pilot.pause()

        # newline inserted into the draft
        assert "\n" in prompt.text
        assert prompt.text == "line1\n"
        # no turn started: no UserMessage, no query
        assert len(app.query(UserMessage)) == 0
        assert app.conversation.session.client.queries == []
        assert app._sending is False


async def test_shift_enter_then_more_text_then_enter_submits_multiline(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("line1")
        await pilot.press("shift+enter")
        prompt.insert("line2")
        assert prompt.text == "line1\nline2"

        await pilot.press("enter")
        await pilot.pause()

        assert prompt.text == ""
        assert app.conversation.session.client.queries == ["line1\nline2"]
        assert len(app.query(UserMessage)) == 1


# --- Empty / whitespace-only input does nothing -----------------------------

async def test_enter_empty_does_nothing(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        assert prompt.text == ""

        await pilot.press("enter")
        await pilot.pause()

        assert prompt.text == ""
        assert len(app.query(UserMessage)) == 0
        assert app.conversation.session.client.queries == []
        assert app._sending is False


async def test_enter_whitespace_only_does_nothing(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("   \t  ")

        await pilot.press("enter")
        await pilot.pause()

        # whitespace draft is left untouched (no Submitted posted, so no clear)
        assert prompt.text == "   \t  "
        assert len(app.query(UserMessage)) == 0
        assert app.conversation.session.client.queries == []
        assert app._sending is False


# --- While _sending is True, Enter submit is dropped, draft preserved -------

async def test_enter_dropped_while_sending_preserves_draft(monkeypatch):
    sent = []

    async def _count_send(self, text):
        sent.append(text)

    app = _make_app(monkeypatch)
    monkeypatch.setattr(CastcodeApp, "send", work(_count_send))
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        app._sending = True
        prompt.insert("draft kept")

        await pilot.press("enter")
        await pilot.pause()

        # send NOT called, draft preserved (prompt not cleared)
        assert sent == []
        assert prompt.text == "draft kept"
        # no new turn / query started
        assert app.conversation.session.client.queries == []
        assert len(app.query(UserMessage)) == 0


async def test_submitted_message_dropped_while_sending_via_post(monkeypatch):
    # Drive on_prompt_submitted directly to pin the guard independent of key path.
    calls = []

    async def _count_send(self, text):
        calls.append(text)

    app = _make_app(monkeypatch)
    monkeypatch.setattr(CastcodeApp, "send", work(_count_send))
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("guarded")
        app._sending = True

        app.on_prompt_submitted(Prompt.Submitted("guarded"))
        await pilot.pause()

        assert calls == []
        # guard returns early before clear(); draft untouched
        assert prompt.text == "guarded"


async def test_on_prompt_submitted_when_idle_calls_send_and_clears(monkeypatch):
    calls = []

    async def _count_send(self, text):
        calls.append(text)

    app = _make_app(monkeypatch)
    monkeypatch.setattr(CastcodeApp, "send", work(_count_send))
    async with app.run_test() as pilot:
        _install_client(app)
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("anything")

        assert app._sending is False
        app.on_prompt_submitted(Prompt.Submitted("hello"))
        await pilot.pause()

        # idle path sets _sending, clears the prompt, and calls send with event.text
        assert app._sending is True
        assert prompt.text == ""
        assert calls == ["hello"]
