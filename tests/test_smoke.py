"""Harness smoke test — validates the shared fixtures + run_test wiring.

Not part of the characterization coverage; it just proves a pure-unit call, a
stubbed connect, and a full burst turn all work before the suite is fanned out.
"""

from textual import work

import castcode.format as fmt
from castcode.app import CastcodeApp
from castcode.ui.messages import AssistantMessage

import fixtures as fx


async def _noop(self):
    pass


def test_pure_unit_format_helpers():
    assert fmt.format_elapsed(5) == "5s"
    assert fmt.format_elapsed(65) == "1m 05s"


async def test_full_burst_turn(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(fx.text_turn("hello world"))
        app._connected_ok = True
        w = app.send("hi")
        await w.wait()
        await pilot.pause()
        rendered = " ".join(m.body.source for m in app.query(AssistantMessage))
        assert "hello world" in rendered
        assert app.conversation.session.client.queries == ["hi"]
