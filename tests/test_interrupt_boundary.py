"""Characterization: interrupt POST-Esc boundary (GatedFakeClient).

Pins the asymmetry in CastcodeApp.send's receive loop once Esc has flipped
``_interrupting`` to True:

  * STREAM / ASSISTANT rendering is SUPPRESSED — a post-Esc text_delta does
    NOT grow the (absent) AssistantMessage, and a post-Esc main-agent text
    content_block_start does NOT mount a new AssistantMessage.
  * TOOL-RESULT application STILL happens — a post-Esc UserMessage carrying an
    error ToolResultBlock still flips its matched tool's state to "error" and
    still updates the tool row's inline error text.

The queue ordering trick: GatedFakeClient.interrupt() enqueues the terminal
ResultMessage. receive_response() stops the moment it yields a ResultMessage, so
any message we want processed *while interrupting* must already sit in the queue
AHEAD of that terminal result. We therefore feed() the post-Esc events (a text
content_block_start, a tool-result UserMessage) WITHOUT pumping, THEN press Esc
(which both sets _interrupting and
appends the terminal ResultMessage), THEN pump once to drain everything with
_interrupting already True.
"""

from textual import work
from textual.widgets import Static

from castcode.app import CastcodeApp
from castcode.ui.messages import AssistantMessage, NoticeMessage, ToolMessage

import fixtures as fx


async def _noop(self):
    pass


TOOL_ID = "toolu_boundary"


async def _start_with_running_tool(pilot, app):
    """send() a turn, feed a tool_use start, return the mounted running ToolMessage."""
    w = app.send("hi")
    # Let send() pass _connect_worker.wait(), mount UserMessage, query(), and set
    # _can_interrupt before the loop parks on the empty queue.
    await pilot.pause()
    await app.conversation.session.client.feed(
        fx.stream_event(fx.ev_tool_start(0, TOOL_ID, "Bash"))
    )
    await pilot.pause()
    tools = app.query(ToolMessage)
    assert len(tools) == 1
    tool = tools.first()
    assert tool.state == "running"
    return w, tool


async def test_post_esc_text_delta_does_not_grow_assistant_but_tool_result_applies(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.GatedFakeClient()
        app._connected_ok = True

        w, tool = await _start_with_running_tool(pilot, app)

        # Precondition: interrupt is armed, no AssistantMessage yet.
        assert app._can_interrupt is True
        assert app._interrupting is False
        assert len(app.query(AssistantMessage)) == 0

        # Queue the POST-Esc messages WITHOUT pumping so they sit ahead of the
        # terminal ResultMessage that Esc will append. A text content_block_start
        # (NOT a bare delta — that is ignored anyway for an unstarted index) is the
        # non-vacuous probe: absent the interrupt gate it WOULD mount an
        # AssistantMessage, so asserting none appears proves the suppression.
        await app.conversation.session.client.feed(
            fx.stream_event(fx.ev_text_start(1))
        )
        await app.conversation.session.client.feed(
            fx.user_message([
                fx.tool_result_block(TOOL_ID, content="boom failed", is_error=True)
            ])
        )

        # Esc -> sets _interrupting and enqueues the terminal error ResultMessage.
        # (The focused Prompt TextArea swallows a raw escape keypress, so we drive
        # the real action_interrupt directly — same code path the binding invokes.)
        await app.run_action("interrupt")
        assert app._interrupting is True
        assert app.conversation.session.client.interrupted is True

        # Drain: text_delta (ignored), UserMessage (applied), ResultMessage (terminal).
        await w.wait()
        await pilot.pause()

        # --- SUPPRESSION half -------------------------------------------------
        # The post-Esc text content_block_start was suppressed: no AssistantMessage.
        assert len(app.query(AssistantMessage)) == 0

        # --- TOOL-RESULT half -------------------------------------------------
        # The post-Esc error ToolResultBlock still flipped the tool to "error".
        assert tool.state == "error"
        assert tool.has_class("-error")
        assert not tool.has_class("-done")

        # ...and still updated the matched row's inline error text.
        error = tool.query_one(".tool-error", Static)
        assert str(error.render()) == "boom failed"
        assert error.display is True

        # The teardown still appended the "Interrupted" notice and cleared flags.
        notices = app.query(NoticeMessage)
        assert len(notices) == 1
        assert notices.first()._body == "Interrupted"
        assert app._interrupting is False
        assert app._sending is False
        assert app._can_interrupt is False


async def test_post_esc_main_agent_text_block_start_does_not_mount_assistant(monkeypatch):
    """A main-agent text content_block_start arriving after Esc is dropped.

    _on_stream_event returns early on content_block_start while _interrupting,
    so no AssistantMessage is mounted even though a text block "opened".
    """
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.GatedFakeClient()
        app._connected_ok = True

        w, tool = await _start_with_running_tool(pilot, app)
        assert len(app.query(AssistantMessage)) == 0

        # Queue a post-Esc text block START (would normally mount an
        # AssistantMessage) plus a delta into it, ahead of the terminal result.
        await app.conversation.session.client.feed(fx.stream_event(fx.ev_text_start(2)))
        await app.conversation.session.client.feed(
            fx.stream_event(fx.ev_text_delta(2, "late words"))
        )

        await app.run_action("interrupt")
        assert app._interrupting is True

        await w.wait()
        await pilot.pause()

        # content_block_start was suppressed -> no AssistantMessage mounted,
        # and the delta had no block to write into.
        assert len(app.query(AssistantMessage)) == 0
        # The running tool was forced to error by interrupt teardown.
        assert tool.state == "error"
        assert len(app.query(NoticeMessage)) == 1


async def test_pre_esc_assistant_is_not_grown_by_post_esc_delta(monkeypatch):
    """If an AssistantMessage already exists (opened pre-Esc), a post-Esc delta
    must NOT append to it. Pin the body stays exactly the pre-Esc text."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.GatedFakeClient()
        app._connected_ok = True

        w = app.send("hi")
        await pilot.pause()

        # Pre-Esc: open a text block and write into it so an AssistantMessage
        # exists and is non-empty.
        await app.conversation.session.client.feed(fx.stream_event(fx.ev_text_start(0)))
        await app.conversation.session.client.feed(fx.stream_event(fx.ev_text_delta(0, "before ")))
        await pilot.pause()

        msgs = app.query(AssistantMessage)
        assert len(msgs) == 1
        before_src = msgs.first().body.source
        assert "before" in before_src

        # Also need a tool to satisfy _can_interrupt timing? _can_interrupt was
        # set right after query(); arm-check below confirms.
        assert app._can_interrupt is True

        # Queue a post-Esc delta into the SAME index 0, ahead of terminal result.
        await app.conversation.session.client.feed(fx.stream_event(fx.ev_text_delta(0, "AFTER")))

        await app.run_action("interrupt")
        assert app._interrupting is True

        await w.wait()
        await pilot.pause()

        # Still exactly one AssistantMessage, and its body did NOT gain "AFTER".
        msgs = app.query(AssistantMessage)
        assert len(msgs) == 1
        assert "AFTER" not in msgs.first().body.source
        assert app.conversation.session.client.queries == ["hi"]


async def test_post_esc_finalized_new_tooluse_is_not_mounted(monkeypatch):
    """A completed MAIN-agent AssistantMessage arriving after Esc, carrying a NEW
    ToolUseBlock (no streamed placeholder), must NOT mount a ToolMessage -- pins the
    `elif not self._interrupting` guard in _finalize_message (absent the guard, a
    fresh tool row would appear during teardown)."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.GatedFakeClient()
        app._connected_ok = True

        w = app.send("hi")
        await pilot.pause()
        assert app._can_interrupt is True

        # Queue a finalized main-agent message with a brand-new ToolUseBlock,
        # ahead of the terminal result.
        await app.conversation.session.client.feed(
            fx.assistant_message([fx.tool_use_block("tnew", "Bash", {"command": "ls"})])
        )

        await app.run_action("interrupt")
        assert app._interrupting is True

        await w.wait()
        await pilot.pause()

        # The guard suppressed the new tool row; only the Interrupted notice mounts.
        assert len(app.query(ToolMessage)) == 0
        assert len(app.query(NoticeMessage)) == 1
