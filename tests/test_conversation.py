"""Characterization: conversation helper ownership + send() exception-path cleanup.

Pins the explicit helper structure and that send()'s finally block cleans up the
turn flags / activity line even when the SDK client raises.
"""

import inspect
import pytest
from textual import work
from textual.worker import WorkerFailed

from castcode.app import CastcodeApp
import castcode.conversation as conversation

import fixtures as fx


async def _noop(self):
    pass


# --- ownership ---------------------------------------------------------------

def test_turn_loop_is_explicit_helpers_not_mro():
    assert "send" in CastcodeApp.__dict__
    assert "send" not in conversation.__dict__
    for name in (
        "run_turn",
        "_on_message",
        "_on_stream_event",
        "_finalize_message",
        "_render_complete",
        "_apply_tool_results",
    ):
        assert inspect.iscoroutinefunction(getattr(conversation, name)), name
    assert [n for n in vars(conversation) if n.startswith("on_")] == []
    assert not hasattr(conversation, "BINDINGS")
    assert "_stick" in CastcodeApp.__dict__


# --- send() exception-path cleanup -------------------------------------------

class _QueryRaises(fx.BurstFakeClient):
    async def query(self, text):
        self.queries.append(text)
        raise RuntimeError("boom in query")


class _RecvRaises(fx.BurstFakeClient):
    async def receive_response(self):
        raise RuntimeError("boom in receive_response")
        yield  # unreachable; makes this an async generator


async def _run_failing(monkeypatch, client):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    state = {}
    # The error surfaces ("no fallbacks"): the worker fails and run_test re-raises
    # it on shutdown. We CAPTURE the post-finally state inside, then assert OUTSIDE
    # the block -- asserting inside would be masked by the exit's WorkerFailed (a
    # new exception in __aexit__ replaces the AssertionError -> vacuous test).
    with pytest.raises(WorkerFailed):
        async with app.run_test() as pilot:
            app.conversation.session.client = client
            app._connected_ok = True
            app._sending = True   # on_prompt_submitted sets this before dispatching send
            w = app.send("x")
            with pytest.raises(WorkerFailed):
                await w.wait()
            await pilot.pause()
            state.update(
                sending=app._sending,
                can_interrupt=app._can_interrupt,
                interrupting=app._interrupting,
                timer=app._activity_timer,
                activity=str(app._activity.render()),
            )
    # finally ran despite the raise: turn flags reset, activity stopped/blanked.
    assert state["sending"] is False
    assert state["can_interrupt"] is False
    assert state["interrupting"] is False
    assert state["timer"] is None
    assert state["activity"] == ""


async def test_cleanup_when_query_raises(monkeypatch):
    # query() raises before _can_interrupt is armed; finally still resets state.
    await _run_failing(monkeypatch, _QueryRaises())


async def test_cleanup_when_receive_response_raises(monkeypatch):
    # receive_response() raises after _can_interrupt is armed; finally clears it.
    await _run_failing(monkeypatch, _RecvRaises())


async def test_turn_flags_reset_when_stream_cleanup_raises(monkeypatch):
    async def _stop_raises(self):
        raise RuntimeError("stop failed")

    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    monkeypatch.setattr(conversation.TurnState, "stop_streams", _stop_raises)
    app = CastcodeApp()
    state = {}
    with pytest.raises(WorkerFailed):
        async with app.run_test() as pilot:
            app.conversation.session.client = fx.BurstFakeClient([fx.result_message()])
            app._connected_ok = True
            app._sending = True
            app._interrupting = True
            w = app.send("x")
            with pytest.raises(WorkerFailed):
                await w.wait()
            await pilot.pause()
            state.update(
                sending=app._sending,
                can_interrupt=app._can_interrupt,
                interrupting=app._interrupting,
                timer=app._activity_timer,
                activity=str(app._activity.render()),
            )
    assert state["sending"] is False
    assert state["can_interrupt"] is False
    assert state["interrupting"] is False
    assert state["timer"] is None
    assert state["activity"] == ""


async def test_stale_interrupting_does_not_poison_next_turn(monkeypatch):
    # A leftover _interrupting=True (e.g. from a prior permission deny_stop whose
    # terminal result never reset it) must not suppress the next turn's rendering.
    from castcode.ui.messages import AssistantMessage

    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(fx.text_turn("hello"))
        app._connected_ok = True
        app._interrupting = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        assert len(app.query(AssistantMessage)) == 1
        assert app._interrupting is False
