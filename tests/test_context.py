"""Characterization tests for context-% math.

Pins the CURRENT behavior of _update_context / _context_window / context_text:

- The % derives from the LAST main-agent AssistantMessage.usage, NOT ResultMessage.usage.
- Subagent AssistantMessage usage (parent_tool_use_id set) is IGNORED.
- _context_window matches the model_usage key for both the bare model id and the
  model[...] variant, but rejects an unrelated longer prefix.
- A turn with no main-agent usage leaves the previous context % UNCHANGED
  (because last_usage is reset per send()).
"""

from textual import work

from castcode.app import CastcodeApp

import fixtures as fx


async def _noop(self):
    pass


# --------------------------------------------------------------------------
# Pure-unit tests of _context_window (no UI / run_test).
# --------------------------------------------------------------------------

def _bare_app():
    """A CastcodeApp instance usable for pure-unit method calls (not mounted)."""
    return CastcodeApp()


def test_context_window_matches_bare_model_id():
    app = _bare_app()
    app.conversation.last_model = "claude-opus-4-8"
    mu = {"claude-opus-4-8": {"contextWindow": 200_000}}
    assert app.conversation._context_window(mu) == 200_000


def test_context_window_matches_bracket_variant():
    # bare model id matches the '[1m]' variant key.
    app = _bare_app()
    app.conversation.last_model = "claude-opus-4-8"
    mu = {"claude-opus-4-8[1m]": {"contextWindow": 1_000_000}}
    assert app.conversation._context_window(mu) == 1_000_000


def test_context_window_rejects_unrelated_longer_prefix():
    # 'claude-opus-4-8-extra' shares a prefix but is NOT 'claude-opus-4-8[...'.
    app = _bare_app()
    app.conversation.last_model = "claude-opus-4-8"
    mu = {"claude-opus-4-8-extra": {"contextWindow": 500_000}}
    assert app.conversation._context_window(mu) is None


def test_context_window_none_when_nolast_model():
    app = _bare_app()
    app.conversation.last_model = None
    mu = {"claude-opus-4-8[1m]": {"contextWindow": 1_000_000}}
    assert app.conversation._context_window(mu) is None


def test_context_window_no_matching_key():
    app = _bare_app()
    app.conversation.last_model = "claude-opus-4-8"
    mu = {"claude-sonnet-4-5[1m]": {"contextWindow": 1_000_000}}
    assert app.conversation._context_window(mu) is None


def test_context_text_formatting():
    from castcode.ui.status import context_text
    app = _bare_app()
    assert app.conversation.context_pct is None
    assert context_text(app.conversation.context_pct) == "context: —"
    app.conversation.context_pct = 42
    assert context_text(app.conversation.context_pct) == "context: 42%"


# --------------------------------------------------------------------------
# Full-turn tests via run_test (assert the rendered footer text).
# --------------------------------------------------------------------------

def _status(app):
    # Full-tier text of the data the app last pushed to #status. Formatting at
    # unbounded width (instead of reading the rendered Static) keeps these
    # assertions independent of the checkout path: a long cwd drops the live
    # render to a compact tier ("ctx 12%" instead of "context: 12%").
    from castcode.ui.status import StatusLine, status_line
    return status_line(app.query_one("#status", StatusLine).data, None)


async def _run_turn(app, pilot, messages, text="hi"):
    app.conversation.session.client = fx.BurstFakeClient(messages)
    app._connected_ok = True
    w = app.send(text)
    await w.wait()
    await pilot.pause()


async def test_pct_from_assistant_usage_not_result_usage(monkeypatch):
    # Main-agent assistant usage = 200_000 used out of 1_000_000 window -> 20%.
    # ResultMessage carries DIFFERENT usage that must be IGNORED.
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        messages = [
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "hello")),
            fx.stream_event(fx.ev_stop(0)),
            fx.assistant_message(
                [fx.text_block("hello")],
                model=fx.DEFAULT_MODEL,
                usage=fx.usage(input_tokens=200_000),
            ),
            fx.result_message(
                # Different (much larger) usage that, if used, would give ~99%.
                usage=fx.usage(input_tokens=990_000),
                model_usage=fx.model_usage(),
            ),
        ]
        await _run_turn(app, pilot, messages)
        assert app.conversation.context_pct == 20
        assert "context: 20%" in _status(app)


async def test_pct_rounds(monkeypatch):
    # 123_456 / 1_000_000 = 12.3456% -> rounds to 12.
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        messages = [
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "x")),
            fx.stream_event(fx.ev_stop(0)),
            fx.assistant_message(
                [fx.text_block("x")],
                usage=fx.usage(input_tokens=100_000, cache_read=20_000, cache_creation=3_456),
            ),
            fx.result_message(model_usage=fx.model_usage()),
        ]
        await _run_turn(app, pilot, messages)
        assert app.conversation.context_pct == 12
        assert "context: 12%" in _status(app)


async def test_subagent_usage_ignored(monkeypatch):
    # A subagent assistant_message (parent_tool_use_id set) with usage must NOT
    # set last_usage; with no main-agent usage the % stays UNCHANGED ('—').
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        messages = [
            # Subagent message carrying usage — ignored for context math.
            fx.assistant_message(
                [fx.text_block("sub")],
                usage=fx.usage(input_tokens=500_000),
                parent_tool_use_id="toolu_parent",
            ),
            fx.result_message(model_usage=fx.model_usage()),
        ]
        await _run_turn(app, pilot, messages)
        assert app.conversation.context_pct is None
        assert "context: —" in _status(app)


async def test_no_main_agent_usage_leaves_pct_unchanged(monkeypatch):
    # First turn sets a real %, second turn has NO main-agent usage; because
    # send() resets last_usage to None each time, _update_context computes
    # used=0 and does NOT touch context_pct -> previous % retained.
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        first = [
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "a")),
            fx.stream_event(fx.ev_stop(0)),
            fx.assistant_message([fx.text_block("a")], usage=fx.usage(input_tokens=300_000)),
            fx.result_message(model_usage=fx.model_usage()),
        ]
        await _run_turn(app, pilot, first, text="first")
        assert app.conversation.context_pct == 30
        assert "context: 30%" in _status(app)

        # Second turn: assistant message WITHOUT usage (usage=None default).
        second = [
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "b")),
            fx.stream_event(fx.ev_stop(0)),
            fx.assistant_message([fx.text_block("b")], usage=None),
            fx.result_message(model_usage=fx.model_usage()),
        ]
        await _run_turn(app, pilot, second, text="second")
        # last_usage reset to None -> used == 0 -> no update -> stays at 30%.
        assert app.conversation.context_pct == 30
        assert "context: 30%" in _status(app)


async def test_last_main_usage_wins_within_a_turn(monkeypatch):
    # Two main-agent AssistantMessages in one turn (e.g. across a tool round-trip):
    # the LAST main-agent usage wins (it is the final request's prompt = what's in
    # the window now), not the first.
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([fx.text_block("first")], usage=fx.usage(input_tokens=100_000)),
            fx.assistant_message([fx.text_block("second")], usage=fx.usage(input_tokens=400_000)),
            fx.result_message(model_usage=fx.model_usage()),
        ]
        await _run_turn(app, pilot, messages)
        assert app.conversation.context_pct == 40   # 400k/1M, the LAST main usage, not 100k


async def test_subagent_usage_does_not_overwrite_main_usage(monkeypatch):
    # Main-agent usage is set, THEN a subagent message (parent_tool_use_id set)
    # arrives with much larger usage; it must NOT overwrite last_usage.
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([fx.text_block("main")], usage=fx.usage(input_tokens=250_000)),
            fx.assistant_message(
                [fx.text_block("sub")],
                usage=fx.usage(input_tokens=900_000),
                parent_tool_use_id="toolu_sub",
            ),
            fx.result_message(model_usage=fx.model_usage()),
        ]
        await _run_turn(app, pilot, messages)
        assert app.conversation.context_pct == 25   # 250k main usage retained, subagent ignored


async def test_bracket_variant_window_via_full_turn(monkeypatch):
    # End-to-end: main-agent model is the bare id, model_usage key is the [1m]
    # variant; the window must still resolve (DEFAULT_MODEL_KEY).
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        messages = [
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "y")),
            fx.stream_event(fx.ev_stop(0)),
            fx.assistant_message(
                [fx.text_block("y")],
                model="claude-opus-4-8",
                usage=fx.usage(input_tokens=250_000),
            ),
            fx.result_message(model_usage={"claude-opus-4-8[1m]": {"contextWindow": 1_000_000}}),
        ]
        await _run_turn(app, pilot, messages)
        assert app.conversation.context_pct == 25
        assert "context: 25%" in _status(app)


async def test_unrelated_prefix_window_via_full_turn_leaves_unchanged(monkeypatch):
    # Full turn where model_usage key is an unrelated longer prefix: window is
    # None -> no update -> stays at '—'.
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        messages = [
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "z")),
            fx.stream_event(fx.ev_stop(0)),
            fx.assistant_message(
                [fx.text_block("z")],
                model="claude-opus-4-8",
                usage=fx.usage(input_tokens=250_000),
            ),
            fx.result_message(model_usage={"claude-opus-4-8-extra": {"contextWindow": 1_000_000}}),
        ]
        await _run_turn(app, pilot, messages)
        assert app.conversation.context_pct is None
        assert "context: —" in _status(app)
