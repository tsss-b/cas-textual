import os

from textual import work
from textual.app import App, ComposeResult

from castcode.app import CastcodeApp
from castcode.state import Conversation
from castcode.ui.status import StatusData, StatusLine, context_text, cost_text, status_line

import fixtures as fx


async def _noop(self):
    pass


def _data(**kwargs) -> StatusData:
    values = {
        "model": "Opus 4.8 with 1M context",
        "context_pct": 42,
        "permission_mode": "acceptEdits",
        "cwd": os.path.expanduser("~/Code/castcode"),
        "cost_usd": 0.0134,
    }
    values.update(kwargs)
    return StatusData(**values)


def test_cost_text_matches_statusline_style_thresholds():
    assert cost_text(0) == "cost: —"
    assert cost_text(0, compact=True) == "cost —"
    assert cost_text(0.0005) == "$0.00"
    assert cost_text(0.0042) == "$0.0042"
    assert cost_text(0.42) == "$0.420"
    assert cost_text(12.345) == "$12.35"


def test_context_text_has_full_and_compact_forms():
    assert context_text(None) == "context: —"
    assert context_text(None, compact=True) == "ctx —"
    assert context_text(37) == "context: 37%"
    assert context_text(37, compact=True) == "37%"


def test_status_line_uses_rich_form_when_width_allows():
    text = status_line(_data(), 120)
    assert text == (
        "Opus 4.8 with 1M context │ $0.013 │ context: 42% │ "
        "accept edits │ ~/Code/castcode"
    )


def test_status_line_drops_labels_and_path_at_narrow_width():
    text = status_line(_data(), 32)
    assert text == "$0.013 │ 42% │ acceptEdits"
    assert len(text) <= 32


def test_status_line_keeps_cost_placeholder_before_cost_is_known():
    assert status_line(_data(cost_usd=0), 120) == (
        "Opus 4.8 with 1M context │ cost: — │ context: 42% │ "
        "accept edits │ ~/Code/castcode"
    )
    assert status_line(_data(cost_usd=0), 32) == "cost — │ 42% │ acceptEdits"


def test_status_line_never_exceeds_width():
    data = _data(model="Very Long Model Name " * 4, cwd="/a/really/long/path/name")
    for width in range(1, 80):
        text = status_line(data, width)
        assert "\n" not in text
        assert len(text) <= width


class _Host(App):
    def compose(self) -> ComposeResult:
        yield StatusLine(_data(), id="status")


async def test_status_widget_refreshes_when_data_changes():
    app = _Host()
    async with app.run_test() as pilot:
        status = app.query_one(StatusLine)
        status.update_data(_data(cost_usd=1.25, context_pct=7, permission_mode="plan"))
        await pilot.pause()

        text = str(status.render())
        assert "$1.25" in text
        assert "context: 7%" in text
        assert "plan mode" in text


def test_record_result_keeps_cost_on_missing_or_negative_value():
    # The guard's reject branch: a ResultMessage without a usable cost must not
    # clobber the last known value.
    conv = Conversation()
    conv.record_result(fx.result_message(total_cost_usd=0.5))
    assert conv.cost_usd == 0.5

    conv.record_result(fx.result_message(total_cost_usd=None))
    assert conv.cost_usd == 0.5

    conv.record_result(fx.result_message(total_cost_usd=-1.0))
    assert conv.cost_usd == 0.5


async def test_app_uses_latest_session_cost_on_conversation(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        first = [
            fx.assistant_message([fx.text_block("a")], usage=fx.usage(input_tokens=100_000)),
            fx.result_message(model_usage=fx.model_usage(), total_cost_usd=0.0012),
        ]
        app.conversation.session.client = fx.BurstFakeClient(first)
        app._connected_ok = True
        worker = app.send("first")
        await worker.wait()
        await pilot.pause()

        second = [
            fx.assistant_message([fx.text_block("b")], usage=fx.usage(input_tokens=200_000)),
            fx.result_message(model_usage=fx.model_usage(), total_cost_usd=0.0023),
        ]
        app.conversation.session.client = fx.BurstFakeClient(second)
        worker = app.send("second")
        await worker.wait()
        await pilot.pause()

        assert round(app.conversation.cost_usd, 4) == 0.0023
        assert "$0.0023" in status_line(app.query_one("#status", StatusLine).data, None)

        third = [
            fx.assistant_message([fx.text_block("c")], usage=fx.usage(input_tokens=1)),
            fx.result_message(model_usage=fx.model_usage(), total_cost_usd=0.0),
        ]
        app.conversation.session.client = fx.BurstFakeClient(third)
        worker = app.send("third")
        await worker.wait()
        await pilot.pause()

        assert app.conversation.cost_usd == 0.0
        assert "cost: —" in status_line(app.query_one("#status", StatusLine).data, None)
