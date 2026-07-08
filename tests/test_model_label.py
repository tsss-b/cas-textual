"""Characterization: model label from get_server_info().

The rest of the suite stubs CastcodeApp.connect to a no-op, so the real connect ->
get_server_info() -> label-update path is never exercised without this file.

Pins: _model_label() parsing, and that connect() drives #status.
"""

import castcode.format as fmt
import castcode.app
import castcode.session
from castcode.app import CastcodeApp

from textual.widgets import Static

import fixtures as fx


# --- model_label parsing (pure unit) ----------------------------------------

def test_model_label_splits_on_middot_and_strips():
    info = {"models": [{"description": "Opus 4.8 with 1M context · claude-opus-4-8[1m]",
                        "displayName": "Opus 4.8"}]}
    assert fmt.model_label(info) == "Opus 4.8 with 1M context"


def test_model_label_falls_back_to_displayname_when_description_empty():
    info = {"models": [{"description": "", "displayName": "Fallback Name"}]}
    assert fmt.model_label(info) == "Fallback Name"


def test_model_label_none_when_no_models():
    assert fmt.model_label({"models": []}) is None
    assert fmt.model_label({}) is None


def test_model_label_tolerates_null_description_and_non_dict_model():
    # A crash here surfaces as a false "Connection failed" notice on a healthy
    # connect, so opaque server_info payloads must degrade, not raise.
    assert fmt.model_label({"models": [{"description": None, "displayName": "X"}]}) == "X"
    assert fmt.model_label({"models": ["weird"]}) is None
    assert fmt.model_label({"models": [{"description": None}]}) is None


# --- connect() -> label update (real connect body, fake SDK client) ----------

class _ConnectFake:
    def __init__(self, options=None, *args, **kwargs):
        self.options = options  # the real sdk.ClaudeAgentOptions connect() built
        self.connected = False

    async def connect(self):
        self.connected = True

    async def get_server_info(self):
        return fx.server_info("Opus 4.8 with 1M context")

    async def disconnect(self):
        pass


async def test_connect_updates_status_model(monkeypatch):
    # Replace the SDK client where connect() resolves it so the REAL
    # connect() body runs without a network/CLI.
    monkeypatch.setattr(castcode.app.sdk, "ClaudeSDKClient", _ConnectFake)
    app = CastcodeApp()
    async with app.run_test() as pilot:
        await app._connect_worker.wait()
        await pilot.pause()
        assert app.conversation.model == "Opus 4.8 with 1M context"
        assert "Opus 4.8 with 1M context" in str(
            app.query_one("#status", Static).render()
        )
        assert app._connected_ok is True


async def test_connect_passes_expected_agent_options(monkeypatch):
    # connect() must build ClaudeAgentOptions with the cwd, claude_code preset,
    # permission callback, and partial-message streaming the app relies on.
    monkeypatch.setattr(castcode.app.sdk, "ClaudeSDKClient", _ConnectFake)
    app = CastcodeApp()
    async with app.run_test() as pilot:
        await app._connect_worker.wait()
        await pilot.pause()
        opts = app.conversation.session.client.options
        assert opts.cwd == fmt.CWD
        assert opts.permission_mode == "auto"
        assert opts.can_use_tool.__self__ is app
        assert opts.can_use_tool.__func__ is CastcodeApp._can_use_tool
        assert set(opts.hooks) == {"PreToolUse"}
        matcher = opts.hooks["PreToolUse"][0]
        assert matcher.matcher is None
        assert matcher.hooks == [castcode.session._permission_keepalive_hook]
        assert opts.include_partial_messages is True
        sp = opts.system_prompt
        assert sp["type"] == "preset"
        assert sp["preset"] == "claude_code"
        assert "castcode" in sp["append"]
