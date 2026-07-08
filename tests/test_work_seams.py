"""Characterization tests for the no-network test seam under the new layout.

In the old single-file app the seam lived on CastcodeApp: connect/send were @work
methods, stubbable so the app mounted without touching the network. That seam now
splits in two:

  - The SDK-control seam moves into Session: connect/query/receive_response/interrupt
    drive a fake sdk.ClaudeSDKClient with NO network, which is what this file pins.
  - CastcodeApp.connect/CastcodeApp.send stay @work methods that wrap Session (so headless
    tests can still stub them); those app-shell @work seam tests are reintroduced by
    the app-shell slice and live in the app's own characterization test.

connect's signature is the load-bearing change: it takes a prebuilt options object
plus a LIVE desired-mode source, so a Shift-Tab during connect still lands and a
future conversation-switch can set resume/model on the options.
"""

import inspect

from castcode import session as session_mod
from castcode.session import Session, build_options

import fixtures as fx


async def _noop_can_use_tool(tool_name, input_data, context):
    return None


class _FakeClient:
    def __init__(self, options):
        self.options = options
        self.connected = False
        self.disconnected = False
        self.queries = []
        self.interrupts = 0
        self.modes = []
        self.messages = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def set_permission_mode(self, mode):
        self.modes.append(mode)

    async def get_server_info(self):
        return fx.server_info()

    async def query(self, text):
        self.queries.append(text)

    async def interrupt(self):
        self.interrupts += 1

    def receive_response(self):
        msgs = self.messages

        async def _gen():
            for m in msgs:
                yield m

        return _gen()


# --- static structure: Session exposes the full control surface --------------

def test_session_exposes_control_surface():
    for name in ("connect", "query", "receive_response", "interrupt",
                 "set_permission_mode", "set_model", "get_server_info", "disconnect"):
        assert callable(getattr(Session, name))


def test_connect_signature_takes_options_and_live_mode():
    params = list(inspect.signature(Session.connect).parameters)
    assert params == ["self", "options", "desired_mode"]


def test_query_signature_takes_text():
    params = list(inspect.signature(Session.query).parameters)
    assert params == ["self", "text"]


# --- the seam: a fake client drives a full turn with no network --------------

async def test_session_drives_a_turn_against_a_fake_client(monkeypatch):
    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _FakeClient)
    s = Session()
    label = await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")
    assert label == "Opus 4.8 with 1M context"
    assert s.client.connected is True

    s.client.messages = fx.text_turn("hello world")
    await s.query("hi")
    collected = [m async for m in s.receive_response()]
    assert s.client.queries == ["hi"]
    assert collected == s.client.messages


async def test_session_interrupt_and_disconnect_forward(monkeypatch):
    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _FakeClient)
    s = Session()
    await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")
    await s.interrupt()
    await s.disconnect()
    assert s.client.interrupts == 1
    assert s.client.disconnected is True
