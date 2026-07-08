"""Characterization: interrupt drain via GatedFakeClient.

Pins the Esc-interrupt path in CastcodeApp.send's finally block:
 - action_interrupt flips _interrupting True and calls client.interrupt()
 - GatedFakeClient.interrupt() enqueues the terminal error ResultMessage
 - draining to that ResultMessage ends the loop; the finally block then sets
   every still-running ToolMessage to 'error', mounts exactly one
   NoticeMessage('Interrupted'), and clears _interrupting / _sending.
"""

from textual import work

from castcode.app import CastcodeApp
from castcode.ui.messages import NoticeMessage, ToolMessage

import fixtures as fx


async def _noop(self):
    pass


async def test_interrupt_drains_to_result_and_errors_running_tools(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.GatedFakeClient()
        app._connected_ok = True

        w = app.send("do a long thing")

        # Feed two stream events that mount a running ToolMessage, then pause
        # so the UI processes them while the send worker parks on the queue.
        await app.conversation.session.client.feed(fx.stream_event(fx.ev_tool_start(0, "t1", "Bash")))
        await app.conversation.session.client.feed(fx.stream_event(fx.ev_tool_start(1, "t2", "Read")))
        await pilot.pause()
        await pilot.pause()

        # Two running ToolMessages are mounted, nothing finished yet.
        tools = list(app.query(ToolMessage))
        assert len(tools) == 2
        assert all(t.state == "running" for t in tools)

        # Loop has started: query was sent and _can_interrupt is True.
        # NOTE: _sending is set in on_prompt_submitted, not in send(); calling
        # app.send() directly leaves it False, so it is NOT asserted True here.
        assert app.conversation.session.client.queries == ["do a long thing"]
        assert app._can_interrupt is True
        assert app._interrupting is False
        assert app.conversation.session.client.interrupted is False

        # Press Esc -> action_interrupt. interrupt() was invoked on the client.
        # (The flip of _interrupting to True is observed in isolation in
        # test_interrupt_action_calls_interrupt_when_can; here the gated fake
        # immediately enqueues the terminal ResultMessage so the send worker may
        # drain fully before the press returns, re-clearing _interrupting.)
        await pilot.press("escape")
        assert app.conversation.session.client.interrupted is True

        # interrupt() enqueued the terminal error ResultMessage; let the send
        # worker drain to it and run its finally block.
        await w.wait()

        # Every still-running ToolMessage was set to 'error'.
        tools_after = list(app.query(ToolMessage))
        assert len(tools_after) == 2
        assert all(t.state == "error" for t in tools_after)
        for t in tools_after:
            assert t.has_class("-error")
            assert not t.has_class("-done")

        # Exactly one NoticeMessage('Interrupted') is mounted.
        notices = list(app.query(NoticeMessage))
        assert len(notices) == 1
        assert notices[0]._body == "Interrupted"

        # _interrupting and _sending are cleared; _can_interrupt is cleared too.
        assert app._interrupting is False
        assert app._sending is False
        assert app._can_interrupt is False


async def test_interrupt_action_skips_when_cannot_interrupt(monkeypatch):
    """Esc before _can_interrupt is a no-op: no interrupt(), no flip."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.GatedFakeClient()
        app._connected_ok = True
        # _can_interrupt is False by default; no send worker running.
        assert app._can_interrupt is False

        await pilot.press("escape")

        assert app._interrupting is False
        assert app.conversation.session.client.interrupted is False
        assert list(app.query(NoticeMessage)) == []


async def test_interrupt_action_skips_when_already_interrupting(monkeypatch):
    """action_interrupt is a no-op (SkipAction) when _interrupting is already
    True, so it does not re-call client.interrupt()."""
    from textual.actions import SkipAction

    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        app.conversation.session.client = fx.GatedFakeClient()
        # Even with _can_interrupt True, _interrupting already True must skip.
        app._can_interrupt = True
        app._interrupting = True

        raised = False
        try:
            await app.action_interrupt()
        except SkipAction:
            raised = True

        assert raised is True
        assert app.conversation.session.client.interrupted is False


async def test_interrupt_action_calls_interrupt_when_can(monkeypatch):
    """Direct action_interrupt with _can_interrupt True flips _interrupting and
    calls client.interrupt() exactly once (no SkipAction)."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        app.conversation.session.client = fx.GatedFakeClient()
        app._can_interrupt = True
        app._interrupting = False

        await app.action_interrupt()

        assert app._interrupting is True
        assert app.conversation.session.client.interrupted is True


async def test_sending_cleared_after_interrupted_notice_mounts(monkeypatch):
    """_sending is cleared LAST -- AFTER the Interrupted notice is mounted -- so a
    queued Enter can't start a new turn that races the mount. Pins the ORDER (not
    just the end value): at the instant _sending flips to False, the notice must
    already be in the DOM. A `_sending` property records DOM membership at that
    moment, which is robust to when the notice's own on_mount handler runs."""

    class _OrderApp(CastcodeApp):
        notice_present_when_cleared = None

        @property
        def _sending(self):
            return getattr(self, "_sending_value", False)

        @_sending.setter
        def _sending(self, value):
            self._sending_value = value
            if value is False:
                self.notice_present_when_cleared = bool(self.query(NoticeMessage))

    monkeypatch.setattr(_OrderApp, "connect", work(_noop))
    app = _OrderApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.GatedFakeClient()
        app._connected_ok = True
        app._sending = True   # on_prompt_submitted sets this before dispatching send
        w = app.send("hi")
        await pilot.pause()
        await app.run_action("interrupt")
        await w.wait()
        await pilot.pause()

        assert bool(app.query(NoticeMessage)) is True       # the notice did mount
        assert app.notice_present_when_cleared is True       # ...before _sending cleared
        assert app._sending is False
