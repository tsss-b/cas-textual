"""Characterization tests for the streaming markdown lifecycle.

Pins the exact behavior of _on_stream_event / _finalize_message when
driven by hand-built StreamEvents through BurstFakeClient. See the streaming
coverage brief: lazy stream open, delta accumulation, empty-block removal
without cancelling the turn, and no re-render on finalize.
"""

from textual import work

from castcode.app import CastcodeApp
from castcode.ui.messages import AssistantMessage

import fixtures as fx


async def _noop(self):
    pass


async def _run_turn(monkeypatch, messages, query_text="hi"):
    """Boot the app with a stubbed connect, run one send() burst, return the app."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(messages)
        app._connected_ok = True
        w = app.send(query_text)
        await w.wait()
        await pilot.pause()
        yield app


# --- (a) lazy stream open ----------------------------------------------------

async def test_start_mounts_empty_then_first_delta_writes(monkeypatch):
    """The real lazy-open lifecycle, observed mid-turn with a GatedFakeClient:
    a content_block_start mounts an AssistantMessage whose Markdown body is still
    EMPTY (the stream is not opened yet), and only the first truthy text_delta
    writes into that SAME body. (Uses a proper start->delta->stop sequence, not a
    protocol-incomplete dangling block.)"""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.GatedFakeClient()
        app._connected_ok = True
        w = app.send("hi")
        await pilot.pause()

        # start: an empty AssistantMessage is mounted, nothing written yet (lazy).
        await app.conversation.session.client.feed(fx.stream_event(fx.ev_text_start(0)))
        await pilot.pause()
        msgs = app.query(AssistantMessage)
        assert len(msgs) == 1
        assert msgs.first().body.source == ""

        # first truthy delta writes into the SAME body.
        await app.conversation.session.client.feed(fx.stream_event(fx.ev_text_delta(0, "hello")))
        await pilot.pause()
        assert app.query(AssistantMessage).first().body.source == "hello"

        # finish the turn cleanly.
        await app.conversation.session.client.feed(fx.stream_event(fx.ev_stop(0)))
        await app.conversation.session.client.feed(fx.assistant_message([fx.text_block("hello")]))
        await app.conversation.session.client.feed(fx.result_message())
        await w.wait()
        assert app._sending is False


async def test_stream_opens_only_on_first_truthy_delta(monkeypatch):
    """The Markdown stream is created lazily: a single truthy text_delta after
    a start accumulates into the body. (Proves the start alone did not write.)"""
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "hello")),
        fx.stream_event(fx.ev_stop(0)),
        fx.assistant_message([fx.text_block("hello")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert len(widgets) == 1
    assert widgets[0].body.source == "hello"
    await gen.aclose()


# --- (b) multiple deltas accumulate into the SAME body -----------------------

async def test_multiple_deltas_accumulate_same_body(monkeypatch):
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "foo ")),
        fx.stream_event(fx.ev_text_delta(0, "bar ")),
        fx.stream_event(fx.ev_text_delta(0, "baz")),
        fx.stream_event(fx.ev_stop(0)),
        fx.assistant_message([fx.text_block("foo bar baz")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert len(widgets) == 1
    assert widgets[0].body.source == "foo bar baz"
    await gen.aclose()


async def test_empty_string_delta_is_ignored(monkeypatch):
    """A falsy ('') text_delta does not open the stream nor write; only the
    truthy deltas land in the body."""
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "")),
        fx.stream_event(fx.ev_text_delta(0, "real")),
        fx.stream_event(fx.ev_stop(0)),
        fx.assistant_message([fx.text_block("real")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert len(widgets) == 1
    assert widgets[0].body.source == "real"
    await gen.aclose()


# --- (c) whitespace-only / zero-delta block removed on stop ------------------

async def test_zero_delta_block_removed_on_stop_turn_completes(monkeypatch):
    """A text block that receives ZERO deltas is REMOVED on content_block_stop
    and does NOT cancel the turn: the following ResultMessage is processed and
    context updates. Crucially exercises a never-written stream (no stop() on a
    stream object), which must not cancel the worker."""
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_stop(0)),
        fx.assistant_message(
            [fx.text_block("")],
            usage=fx.usage(input_tokens=10),
        ),
        fx.result_message(model_usage=fx.model_usage()),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    # The empty block was removed.
    assert list(app.query(AssistantMessage)) == []
    # The turn completed and ResultMessage was processed: context updated.
    assert app.conversation.context_pct is not None
    # send() ran to completion -> _sending reset.
    assert app._sending is False
    await gen.aclose()


async def test_zero_delta_block_then_real_block_both_processed(monkeypatch):
    """A zero-delta block at index 0 is removed, but a real streamed block at
    index 1 survives -- proving the turn was not cancelled by the removal."""
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_stop(0)),
        fx.stream_event(fx.ev_text_start(1)),
        fx.stream_event(fx.ev_text_delta(1, "survivor")),
        fx.stream_event(fx.ev_stop(1)),
        fx.assistant_message([fx.text_block(""), fx.text_block("survivor")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert len(widgets) == 1
    assert widgets[0].body.source == "survivor"
    await gen.aclose()


async def test_whitespace_only_delta_block_removed_on_stop(monkeypatch):
    """A block that receives only a whitespace-only (but truthy) delta opens the
    stream and writes, yet wrote[index] stays False (delta.strip() is falsy), so
    it is REMOVED on content_block_stop. The stream WAS written, so stop() is a
    clean no-cancel. The turn still completes."""
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "   ")),
        fx.stream_event(fx.ev_stop(0)),
        fx.assistant_message([fx.text_block("   ")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    assert list(app.query(AssistantMessage)) == []
    assert app._sending is False
    await gen.aclose()


async def test_whitespace_block_does_not_drop_following_real_block(monkeypatch):
    """Whitespace-only block at index 0 removed; real block at index 1 kept."""
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "  \n ")),
        fx.stream_event(fx.ev_stop(0)),
        fx.stream_event(fx.ev_text_start(1)),
        fx.stream_event(fx.ev_text_delta(1, "kept")),
        fx.stream_event(fx.ev_stop(1)),
        fx.assistant_message([fx.text_block("  \n "), fx.text_block("kept")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert len(widgets) == 1
    assert widgets[0].body.source == "kept"
    await gen.aclose()


# --- (d) _finalize_message does NOT re-render streamed text ------------------

async def test_finalize_renders_text_when_no_streamed_row(monkeypatch):
    msgs = [
        fx.assistant_message([fx.text_block("final only")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()

    widgets = list(app.query(AssistantMessage))
    assert len(widgets) == 1
    assert widgets[0].body.source == "final only"
    await gen.aclose()


async def test_finalize_does_not_rerender_streamed_text(monkeypatch):
    """After streaming "streamed text" into the body, the AssistantMessage that
    arrives in the burst (with the same text) does NOT mount a second
    AssistantMessage nor double the source: the streamed body is kept as-is."""
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "streamed text")),
        fx.stream_event(fx.ev_stop(0)),
        fx.assistant_message([fx.text_block("streamed text")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    # Exactly one AssistantMessage: the streamed one, not a finalize duplicate.
    assert len(widgets) == 1
    assert widgets[0].body.source == "streamed text"
    await gen.aclose()


async def test_finalize_does_not_rerender_sparse_streamed_text_index(monkeypatch):
    msgs = [
        fx.stream_event({
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking"},
        }),
        fx.stream_event(fx.ev_stop(0)),
        fx.stream_event(fx.ev_text_start(1)),
        fx.stream_event(fx.ev_text_delta(1, "streamed text")),
        fx.stream_event(fx.ev_stop(1)),
        fx.assistant_message([fx.text_block("streamed text")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert len(widgets) == 1
    assert widgets[0].body.source == "streamed text"
    await gen.aclose()


async def test_finalize_renders_unstreamed_text_before_sparse_streamed_text(monkeypatch):
    msgs = [
        fx.stream_event(fx.ev_text_start(1)),
        fx.stream_event(fx.ev_text_delta(1, "second")),
        fx.stream_event(fx.ev_stop(1)),
        fx.assistant_message([fx.text_block("first"), fx.text_block("second")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert [widget.body.source for widget in widgets] == ["first", "second"]
    await gen.aclose()


async def test_finalize_does_not_rerender_after_omitted_mid_stream_block(monkeypatch):
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "first")),
        fx.stream_event(fx.ev_stop(0)),
        fx.stream_event({
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "thinking"},
        }),
        fx.stream_event(fx.ev_stop(1)),
        fx.stream_event(fx.ev_text_start(2)),
        fx.stream_event(fx.ev_text_delta(2, "second")),
        fx.stream_event(fx.ev_stop(2)),
        fx.assistant_message([fx.text_block("first"), fx.text_block("second")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert [widget.body.source for widget in widgets] == ["first", "second"]
    await gen.aclose()


async def test_repeated_final_assistant_uuid_renders_once(monkeypatch):
    """The CLI can re-deliver the same complete AssistantMessage; the
    final_assistant_ids uuid dedupe must render it exactly once."""
    msgs = [
        fx.assistant_message([fx.text_block("only once")], uuid="dup"),
        fx.assistant_message([fx.text_block("only once")], uuid="dup"),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert [widget.body.source for widget in widgets] == ["only once"]
    await gen.aclose()


async def test_finalize_renders_extra_nonstreamed_text_block(monkeypatch):
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "streamed")),
        fx.stream_event(fx.ev_stop(0)),
        fx.assistant_message([fx.text_block("streamed"), fx.text_block("final only")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert [widget.body.source for widget in widgets] == ["streamed", "final only"]
    await gen.aclose()


async def test_multi_assistant_message_turn_does_not_rerender_text(monkeypatch):
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "first")),
        fx.stream_event(fx.ev_stop(0)),
        fx.assistant_message([fx.text_block("first")], uuid="a1"),
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "second")),
        fx.stream_event(fx.ev_stop(0)),
        fx.assistant_message([fx.text_block("second")], uuid="a2"),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert [widget.body.source for widget in widgets] == ["first", "second"]
    await gen.aclose()


async def test_finalize_keeps_streamed_source_not_full_message_text(monkeypatch):
    """If the final AssistantMessage text differs from what was streamed, the
    body keeps the STREAMED source (finalize never overwrites already-streamed
    text). Pins that _finalize_message ignores main-agent text blocks entirely."""
    msgs = [
        fx.stream_event(fx.ev_text_start(0)),
        fx.stream_event(fx.ev_text_delta(0, "partial")),
        fx.stream_event(fx.ev_stop(0)),
        # Final message claims more text, but body must stay "partial".
        fx.assistant_message([fx.text_block("partial and then some more")]),
        fx.result_message(),
    ]
    gen = _run_turn(monkeypatch, msgs)
    app = await gen.__anext__()
    widgets = list(app.query(AssistantMessage))
    assert len(widgets) == 1
    assert widgets[0].body.source == "partial"
    await gen.aclose()
