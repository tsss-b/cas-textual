"""Session wraps sdk.ClaudeSDKClient; Conversation holds per-conversation state.

Pins the connect details ported from the old castcode/app.py:90-126 — system prompt
preset+append, PreToolUse keepalive hook, include_partial_messages, the
permission-mode reconcile loop (reading the LIVE desired mode), and the
get_server_info model label — plus the thin delegating wrappers.
"""

import asyncio
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

from castcode import session as session_mod
from castcode.session import (
    ForkResult,
    ModelRow,
    Session,
    SessionRow,
    build_options,
    _permission_keepalive_hook,
)
from castcode.state import Conversation

import fixtures as fx


# --- a fake client recording control calls; installed in place of the real one ---

class _FakeClient:
    def __init__(self, options):
        self.options = options
        self.connected = False
        self.disconnected = False
        self.queries = []
        self.interrupts = 0
        self.modes = []
        self.models = []
        self._server_info = fx.server_info()

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def set_permission_mode(self, mode):
        self.modes.append(mode)

    async def set_model(self, model):
        self.models.append(model)

    async def get_server_info(self):
        return self._server_info

    async def get_context_usage(self):
        return {
            "percentage": 12.5,
            "totalTokens": 1250,
            "maxTokens": 10000,
            "model": "claude-sonnet",
            "categories": [{"name": "Messages", "tokens": 1000, "color": "blue"}],
        }

    async def query(self, text):
        self.queries.append(text)

    async def interrupt(self):
        self.interrupts += 1

    def receive_response(self):
        async def _gen():
            return
            yield
        return _gen()


def _install_fake(monkeypatch):
    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _FakeClient)


async def _noop_can_use_tool(tool_name, input_data, context):
    return None


# --- build_options: the connect details live in session.py -------------------

def test_build_options_carries_connect_details():
    opts = build_options("auto", _noop_can_use_tool)
    assert opts.permission_mode == "auto"
    assert opts.can_use_tool is _noop_can_use_tool
    assert opts.include_partial_messages is True
    assert opts.extra_args == {"replay-user-messages": None}
    assert opts.system_prompt["type"] == "preset"
    assert opts.system_prompt["preset"] == "claude_code"
    assert "castcode" in opts.system_prompt["append"]


def test_build_options_wires_pretooluse_keepalive_hook():
    opts = build_options("default", _noop_can_use_tool)
    matchers = opts.hooks["PreToolUse"]
    assert len(matchers) == 1
    assert matchers[0].matcher is None
    assert matchers[0].hooks == [_permission_keepalive_hook]


def test_keepalive_hook_continues():
    out = asyncio.run(_permission_keepalive_hook({}, "tid", None))
    assert out == {"continue_": True}


def test_build_options_defaults_resume_and_fork_off():
    opts = build_options("auto", _noop_can_use_tool)
    assert opts.resume is None
    assert opts.fork_session is False
    assert opts.model is None


def test_build_options_signature_has_no_options_level_fork():
    assert "fork_session" not in inspect.signature(build_options).parameters


def test_build_options_accepts_resume_and_model():
    opts = build_options(
        "auto",
        _noop_can_use_tool,
        resume="sess-123",
        model="claude-sonnet",
    )
    assert opts.resume == "sess-123"
    assert opts.model == "claude-sonnet"


# --- session.py is the ONLY module that names the SDK client type ------------

def test_session_constructs_the_sdk_client_type(monkeypatch):
    seen = {}

    class _Probe(_FakeClient):
        def __init__(self, options):
            seen["options"] = options
            super().__init__(options)

    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _Probe)
    s = Session()
    opts = build_options("auto", _noop_can_use_tool)
    asyncio.run(s.connect(opts, lambda: "auto"))
    assert seen["options"] is opts
    assert s.client.connected is True


# --- connect: reconcile loop reads the LIVE desired mode ---------------------

async def test_connect_no_reconcile_when_mode_unchanged(monkeypatch):
    _install_fake(monkeypatch)
    s = Session()
    opts = build_options("auto", _noop_can_use_tool)
    await s.connect(opts, lambda: "auto")
    # desired matches what was sent in options -> never re-set the mode.
    assert s.client.modes == []


async def test_connect_reconciles_mid_connect_shift_tab(monkeypatch):
    # The desired mode flips AFTER connect started (a Shift-Tab while connecting).
    # The reconcile loop must read the live value, not the value frozen at start,
    # so it lands the new mode before finishing.
    desired = {"mode": "auto"}

    class _FlippingClient(_FakeClient):
        async def connect(self):
            await super().connect()
            desired["mode"] = "plan"  # user Shift-Tabbed during connect

    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _FlippingClient)
    s = Session()
    opts = build_options("auto", _noop_can_use_tool)
    await s.connect(opts, lambda: desired["mode"])
    assert s.client.modes == ["plan"]


async def test_connect_returns_model_label_from_server_info(monkeypatch):
    _install_fake(monkeypatch)
    s = Session()
    opts = build_options("auto", _noop_can_use_tool)
    label = await s.connect(opts, lambda: "auto")
    assert label == "Opus 4.8 with 1M context"


async def test_connect_returns_none_when_no_models(monkeypatch):
    class _NoModels(_FakeClient):
        def __init__(self, options):
            super().__init__(options)
            self._server_info = {"models": []}

    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _NoModels)
    s = Session()
    opts = build_options("auto", _noop_can_use_tool)
    assert await s.connect(opts, lambda: "auto") is None


# --- thin delegating wrappers forward to the client --------------------------

async def test_query_interrupt_modes_models_disconnect_forward(monkeypatch):
    _install_fake(monkeypatch)
    s = Session()
    await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")
    await s.query("hello")
    await s.interrupt()
    await s.set_permission_mode("plan")
    await s.set_model("claude-opus-4-8")
    info = await s.get_server_info()
    await s.disconnect()
    assert s.client.queries == ["hello"]
    assert s.client.interrupts == 1
    assert s.client.modes == ["plan"]
    assert s.client.models == ["claude-opus-4-8"]
    assert info == fx.server_info()
    assert s.client.disconnected is True


async def test_set_model_returns_display_label(monkeypatch):
    class _Models(_FakeClient):
        def __init__(self, options):
            super().__init__(options)
            self._server_info = {
                "models": [
                    {
                        "id": "claude-opus-4-8",
                        "displayName": "Opus",
                        "description": "Opus 4.8 with 1M context · extra",
                    }
                ]
            }

    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _Models)
    s = Session()
    await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")

    label = await s.set_model("claude-opus-4-8")

    assert label == "Opus 4.8 with 1M context"
    assert s.client.models == ["claude-opus-4-8"]


async def test_set_model_returns_none_for_unlisted_model(monkeypatch):
    _install_fake(monkeypatch)
    s = Session()
    await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")

    assert await s.set_model("missing-model") is None


async def test_receive_response_proxies_the_client_generator(monkeypatch):
    _install_fake(monkeypatch)
    s = Session()
    await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")
    collected = [m async for m in s.receive_response()]
    assert collected == []


async def test_disconnect_before_connect_is_noop():
    s = Session()
    assert s.client is None
    await s.disconnect()


async def test_model_rows_project_server_models(monkeypatch):
    class _Models(_FakeClient):
        def __init__(self, options):
            super().__init__(options)
            self._server_info = {
                "models": [
                    {
                        "value": "claude-sonnet",
                        "displayName": "Sonnet",
                        "description": "Sonnet · balanced",
                    },
                    {"name": "claude-opus", "displayName": "Opus"},
                ]
            }

    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _Models)
    s = Session()
    await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")

    assert await s.model_rows() == [
        ModelRow("claude-sonnet", "Sonnet", "Sonnet · balanced"),
        ModelRow("claude-opus", "Opus"),
    ]


async def test_model_rows_handles_missing_server_info(monkeypatch):
    class _NoInfo(_FakeClient):
        async def get_server_info(self):
            return None

    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _NoInfo)
    s = Session()
    await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")

    assert await s.model_rows() == []


async def test_connect_normalizes_server_command_rows(monkeypatch):
    class _Commands(_FakeClient):
        def __init__(self, options):
            super().__init__(options)
            self._server_info = {
                "models": [],
                "slash_commands": [
                    "compact",
                    {"name": "plugin:run", "description": "Run plugin"},
                ],
            }

    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _Commands)
    s = Session()
    await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")

    assert s.commands == [
        session_mod.CommandRow("/compact", "", "sdk"),
        session_mod.CommandRow("/plugin:run", "Run plugin", "plugin"),
    ]


def test_command_rows_normalize_nested_and_mapping_shapes():
    rows = session_mod.command_rows({
        "slashCommands": {
            "doctor": {"description": "Diagnostics"},
            "/skill:lint": {},
        }
    })

    assert rows == [
        session_mod.CommandRow("/doctor", "Diagnostics", "sdk"),
        session_mod.CommandRow("/skill:lint", "", "plugin"),
    ]


def test_command_rows_from_info_tags_skills_and_plugin_commands():
    rows = session_mod.command_rows_from_info({
        "slash_commands": [
            {"name": "compact", "description": "Compact context"},
            {"name": "review", "description": "Review code"},
            {"name": "github:yeet", "description": "Publish changes"},
            {"name": "plugin:run", "description": "Run plugin command"},
        ],
        "skills": ["review", "github:yeet"],
    })

    assert rows == [
        session_mod.CommandRow("/compact", "Compact context", "sdk"),
        session_mod.CommandRow("/review", "Review code", "skill"),
        session_mod.CommandRow("/github:yeet", "Publish changes", "plugin"),
        session_mod.CommandRow("/plugin:run", "Run plugin command", "plugin"),
    ]


async def test_context_usage_formats_sdk_response(monkeypatch):
    _install_fake(monkeypatch)
    s = Session()
    await s.connect(build_options("auto", _noop_can_use_tool), lambda: "auto")

    text = await s.context_usage()

    assert "12.5% context used" in text
    assert "1,250 / 10,000 tokens" in text
    assert "Messages: 1,000 tokens" in text


def test_context_usage_format_passthrough_edges():
    assert session_mod._format_context_usage("already formatted") == "already formatted"
    assert session_mod._format_context_usage(None) == "None"


def test_sidecar_path_uses_claude_config_dir_and_project_key(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(
        session_mod.sdk,
        "project_key_for_directory",
        lambda directory: "project-key",
    )

    assert Session().sidecar_path("sess-1") == (
        tmp_path / "castcode-sidecars" / "project-key" / "sess-1.json"
    )


def test_session_last_modified_uses_sdk_metadata(monkeypatch):
    monkeypatch.setattr(
        session_mod.sdk,
        "get_session_info",
        lambda session_id, directory=None: SimpleNamespace(last_modified=123)
        if session_id == "present"
        else None,
    )

    s = Session()
    assert s.session_last_modified("present") == 123
    assert s.session_last_modified("missing") is None


def test_list_sessions_and_session_row_projection(monkeypatch):
    info = SimpleNamespace(
        session_id="s1",
        summary="Build UI",
        first_prompt="first",
        last_modified=123,
        cwd="/tmp/worktree",
        git_branch="feature",
        tag="important",
    )
    seen = {}

    def _list_sessions(directory=None, include_worktrees=True):
        seen["directory"] = directory
        seen["include_worktrees"] = include_worktrees
        return [info]

    monkeypatch.setattr(session_mod.sdk, "list_sessions", _list_sessions)

    s = Session()
    assert s.list_sessions() == [info]
    assert seen == {"directory": session_mod.CWD, "include_worktrees": False}
    assert s.session_row(info) == SessionRow(
        "s1",
        "Build UI",
        "feature · #important · /tmp/worktree",
        123,
        "/tmp/worktree",
        "feature",
    )


def test_session_row_projection_minimal_info(monkeypatch):
    info = SimpleNamespace(
        session_id="s2",
        summary="",
        first_prompt="first prompt",
        last_modified=None,
        cwd=session_mod.CWD,
        git_branch=None,
        tag=None,
    )

    assert Session().session_row(info) == SessionRow(
        "s2",
        "first prompt",
        "",
        None,
        session_mod.CWD,
        None,
    )


def test_fork_result_extracts_uuid_map(monkeypatch, tmp_path):
    fork_path = tmp_path / "fork.jsonl"
    fork_path.write_text(
        "\n".join([
            '{"uuid":"new-u","forkedFrom":{"sessionId":"source","messageUuid":"old-u"}}',
            '{"uuid":"ignored","forkedFrom":{"sessionId":"other","messageUuid":"old"}}',
            '{"uuid":"title","type":"custom-title"}',
            'not json',
        ]),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        session_mod.sdk,
        "fork_session",
        lambda session_id, directory=None, up_to_message_id=None: calls.append(
            (session_id, directory, up_to_message_id)
        )
        or SimpleNamespace(session_id="forked"),
    )
    monkeypatch.setattr(
        session_mod,
        "_find_session_file_with_dir",
        lambda session_id, directory=None: (fork_path, tmp_path),
    )

    assert Session().fork_result("source", "old-u") == ForkResult(
        "forked",
        {"old-u": "new-u"},
    )
    assert calls == [("source", session_mod.CWD, "old-u")]


def test_fork_result_maps_real_sdk_fork_metadata():
    # conftest isolates CLAUDE_CONFIG_DIR, so this never touches real Claude history.
    config_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
    project_dir = (
        config_dir
        / "projects"
        / session_mod.sdk.project_key_for_directory(session_mod.CWD)
    )
    project_dir.mkdir(parents=True, exist_ok=True)

    source_session_id = str(uuid.uuid4())
    user_uuid = str(uuid.uuid4())
    assistant_uuid = str(uuid.uuid4())
    source_path = project_dir / f"{source_session_id}.jsonl"
    source_path.write_text(
        "\n".join(
            json.dumps(entry, separators=(",", ":"))
            for entry in [
                {
                    "type": "user",
                    "uuid": user_uuid,
                    "parentUuid": None,
                    "sessionId": source_session_id,
                    "timestamp": "2026-01-01T00:00:00.000Z",
                    "message": {"role": "user", "content": "hello"},
                },
                {
                    "type": "assistant",
                    "uuid": assistant_uuid,
                    "parentUuid": user_uuid,
                    "sessionId": source_session_id,
                    "timestamp": "2026-01-01T00:00:01.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hi"}],
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = Session().fork_result(source_session_id, assistant_uuid)

    assert result.session_id != source_session_id
    assert set(result.uuid_map) == {user_uuid, assistant_uuid}
    assert result.uuid_map[user_uuid] != user_uuid
    assert result.uuid_map[assistant_uuid] != assistant_uuid


def test_fork_returns_new_session_id(monkeypatch):
    monkeypatch.setattr(
        session_mod.sdk,
        "fork_session",
        lambda session_id, directory=None, up_to_message_id=None: SimpleNamespace(
            session_id="forked"
        ),
    )

    assert Session().fork("source", "old-u") == "forked"


def test_fork_result_raises_when_forked_file_missing(monkeypatch):
    monkeypatch.setattr(
        session_mod.sdk,
        "fork_session",
        lambda session_id, directory=None, up_to_message_id=None: SimpleNamespace(
            session_id="forked"
        ),
    )
    monkeypatch.setattr(
        session_mod,
        "_find_session_file_with_dir",
        lambda session_id, directory=None: None,
    )

    try:
        Session().fork_result("source", "old-u")
    except FileNotFoundError as error:
        assert "forked" in str(error)
    else:
        raise AssertionError("expected FileNotFoundError")


# --- Conversation state object -----------------------------------------------

def test_conversation_defaults():
    c = Conversation()
    assert isinstance(c.session, Session)
    assert c.cost_usd == 0.0
    assert c.transcript == []
    assert c.connected_ok is False


def test_conversation_holds_a_session():
    s = Session()
    c = Conversation(session=s)
    assert c.session is s


async def test_connect_returns_label_for_requested_model(monkeypatch):
    class _TwoModels(_FakeClient):
        def __init__(self, options):
            super().__init__(options)
            self._server_info = {"models": [
                {"value": "default", "description": "Opus 4.8 with 1M context · recommended"},
                {"value": "claude-haiku-4-5", "description": "Haiku 4.5 · fast"},
            ]}

    monkeypatch.setattr(session_mod.sdk, "ClaudeSDKClient", _TwoModels)
    s = Session()
    opts = build_options("auto", _noop_can_use_tool, model="claude-haiku-4-5")
    assert await s.connect(opts, lambda: "auto") == "Haiku 4.5"


async def test_connect_returns_none_for_unknown_requested_model(monkeypatch):
    # The caller keeps its restored label when the row lookup fails; returning
    # the default row's label here would mislabel resumed/forked sessions.
    _install_fake(monkeypatch)
    s = Session()
    opts = build_options("auto", _noop_can_use_tool, model="claude-mystery")
    assert await s.connect(opts, lambda: "auto") is None


def _drift_session_file(session_id, entries):
    import claude_agent_sdk as sdk
    from castcode.format import CWD

    root = (
        Path(os.environ["CLAUDE_CONFIG_DIR"])
        / "projects"
        / sdk.project_key_for_directory(CWD)
    )
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{session_id}.jsonl"
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
    )
    return path


def test_session_drifted_scans_back_to_anchor():
    path = _drift_session_file("drift-scan", [
        {"type": "user", "uuid": "u1"},
        {"type": "assistant", "uuid": "a1"},
        {"type": "user", "uuid": "u2"},
        {"type": "assistant", "uuid": "a2"},
    ])
    s = Session()
    assert s.session_drifted("drift-scan", "a1") is True
    assert s.session_drifted("drift-scan", "a2") is False
    path.write_text(
        path.read_text(encoding="utf-8")
        + "not json\n"
        + json.dumps({"type": "last-prompt"}) + "\n",
        encoding="utf-8",
    )
    assert s.session_drifted("drift-scan", "a2") is False
    assert s.session_drifted("drift-scan", None) is False
    assert s.session_drifted("drift-scan", "missing-anchor") is False
    assert s.session_drifted("no-such-session", "a1") is False
