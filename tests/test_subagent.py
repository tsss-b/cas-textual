"""Characterization tests for the subagent rendering path.

A subagent AssistantMessage carries parent_tool_use_id != None. The send() worker:
- IGNORES StreamEvents whose parent_tool_use_id is set (only main-agent stream
  events, parent_tool_use_id is None, drive the live-streamed rows), and
- routes the completed subagent AssistantMessage to _render_complete, which mounts
  FRESH rows: each non-blank TextBlock -> an AssistantMessage whose Markdown body is
  populated via body.update (NOT a Markdown stream), and each ToolUseBlock -> a
  ToolMessage. The fresh rows are keyed independently of the main agent's index-keyed
  `blocks` dict, so there is no aliasing/overwrite/orphan when a main-agent text block
  at index 0 and a subagent message interleave.

These tests pin TODAY'S exact behavior of app.py.
"""

from textual import work

from textual.widgets import Static
import castcode.format as fmt
from castcode.app import CastcodeApp
from castcode.conversation import SubagentView
from castcode.tool_render import render_tool_result
from castcode.ui.messages import AssistantMessage, ToolMessage, ToolSubtoolLine, UserMessage

import fixtures as fx


async def _noop(self):
    pass


def _static_text(static):
    """The plain string a Static.update'd into a body widget (mangled attr)."""
    return static._Static__content


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


async def _run_burst(app, messages):
    app.conversation.session.client = fx.BurstFakeClient(messages)
    app._connected_ok = True
    w = app.send("go")
    await w.wait()


# --- SubagentView unit-tested off-stream, server-tool path ------------------


def test_subagentview_server_tool_lifecycle():
    """Drive SubagentView directly through a nested server-tool: a
    ServerToolUseBlock upserts a running subtool, the reverse-lookup resolves
    its ServerToolResultBlock back to that subtool, and marking it done flips
    the row state — mirroring _render_subagent_message + _apply_subtool_server_result
    without the SDK receive loop."""
    view = SubagentView()
    parent_id = "agent-1"

    use = fx.server_tool_use_block("srv-1", "WebSearch", {"query": "textual tui"})
    subtool = view.upsert_subtool(parent_id, use)
    assert subtool["id"] == "srv-1"
    assert subtool["name"] == "WebSearch"
    assert subtool["state"] == "running"
    assert view.subtool_parent["srv-1"] == parent_id

    rows = view.rows_for(parent_id)
    assert len(rows) == 1
    assert rows[0]["state"] == "running"
    assert rows[0]["title"] == "WebSearch"

    # An unknown id resolves to (None, None); the known id finds the live subtool.
    assert view.result_for("nope") == (None, None)
    found_parent, found = view.result_for("srv-1")
    assert found_parent == parent_id
    assert found is subtool

    # Apply the server result the way _apply_subtool_server_result does.
    result = fx.server_tool_result_block("srv-1", [{"type": "text", "text": "a hit"}])
    p, st = view.result_for(result.tool_use_id)
    assert p == parent_id
    st["state"] = "done"
    st["preview"] = render_tool_result(st["name"], result.content)
    assert view.rows_for(parent_id)[0]["state"] == "done"

    # A second server-tool use with the same id replaces in place (no dup row).
    view.upsert_subtool(parent_id, fx.server_tool_use_block("srv-1", "WebSearch", {"query": "again"}))
    assert len(view.subtools[parent_id]) == 1
    assert view.rows_for(parent_id)[0]["state"] == "running"


# --- subagent StreamEvents are ignored --------------------------------------


async def test_subagent_stream_events_produce_no_streamed_rows(monkeypatch):
    """StreamEvents with parent_tool_use_id set must not mount any row."""
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            # A full subagent text "stream" — every event carries a parent id.
            fx.stream_event(fx.ev_text_start(0), parent_tool_use_id="sub-1"),
            fx.stream_event(fx.ev_text_delta(0, "ghost text"), parent_tool_use_id="sub-1"),
            fx.stream_event(fx.ev_stop(0), parent_tool_use_id="sub-1"),
            # ... and a subagent tool stream start too.
            fx.stream_event(fx.ev_tool_start(1, "t1", "Bash"), parent_tool_use_id="sub-1"),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        # No assistant rows, no tool rows: the subagent stream events were dropped.
        assert len(app.query(AssistantMessage)) == 0
        assert len(app.query(ToolMessage)) == 0
        # The only chat-row produced is the echoed user prompt.
        assert len(app.query(UserMessage)) == 1


async def test_subagent_assistant_message_renders_fresh_text_row(monkeypatch):
    """A completed subagent AssistantMessage text block -> a fresh AssistantMessage."""
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            # Subagent stream events (ignored) ...
            fx.stream_event(fx.ev_text_start(0), parent_tool_use_id="sub-1"),
            fx.stream_event(fx.ev_text_delta(0, "ignored"), parent_tool_use_id="sub-1"),
            fx.stream_event(fx.ev_stop(0), parent_tool_use_id="sub-1"),
            # ... then the COMPLETE subagent message renders the row.
            fx.assistant_message(
                [fx.text_block("subagent answer")],
                parent_tool_use_id="sub-1",
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        rows = app.query(AssistantMessage)
        assert len(rows) == 1
        # Rendered via body.update (Markdown.source reflects it).
        assert rows[0].body.source == "subagent answer"


async def test_subagent_tool_use_block_renders_tool_message(monkeypatch):
    """A subagent ToolUseBlock -> a ToolMessage built with name+input (running)."""
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message(
                [fx.tool_use_block("tool-x", "Bash", {"command": "echo hi"})],
                parent_tool_use_id="sub-1",
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        tools = app.query(ToolMessage)
        assert len(tools) == 1
        tool = tools[0]
        assert tool.state == "running"
        # ToolMessage built from name+input -> title "Bash", detail "echo hi".
        title = tool.query_one(".tool-title", Static)
        detail = tool.query_one(".tool-detail", Static)
        assert _static_text(title) == "Bash"
        assert _static_text(detail) == "echo hi"
        # No assistant text row for a tool-only subagent message.
        assert len(app.query(AssistantMessage)) == 0


async def test_subagent_mixed_blocks_render_in_order(monkeypatch):
    """Mixed text + tool blocks each render their own fresh row, in order."""
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message(
                [
                    fx.text_block("first the text"),
                    fx.tool_use_block("tool-y", "Read", {"file_path": fmt.CWD}),
                    fx.text_block("then more text"),
                ],
                parent_tool_use_id="sub-1",
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assistants = app.query(AssistantMessage)
        tools = app.query(ToolMessage)
        assert len(assistants) == 2
        assert len(tools) == 1
        assert [a.body.source for a in assistants] == ["first the text", "then more text"]
        # Read of cwd renders the path in the detail row.
        title = tools[0].query_one(".tool-title", Static)
        detail = tools[0].query_one(".tool-detail", Static)
        assert _static_text(title) == "Read"
        assert _static_text(detail) == "."


async def test_subagent_blank_text_block_is_dropped(monkeypatch):
    """_render_complete skips text blocks whose stripped text is empty."""
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message(
                [
                    fx.text_block("   \n  "),   # blank -> dropped
                    fx.text_block("kept"),
                ],
                parent_tool_use_id="sub-1",
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assistants = app.query(AssistantMessage)
        assert len(assistants) == 1
        assert assistants[0].body.source == "kept"


# --- no aliasing between subagent fresh rows and main-agent index-keyed blocks ---


async def test_no_aliasing_main_index0_and_subagent(monkeypatch):
    """Interleave a main-agent text block at index 0 with a subagent message.

    Both must render correctly with no orphan/overwrite: the main-agent streamed row
    keeps its own text, and the subagent's fresh row is a distinct AssistantMessage.
    """
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            # Main-agent streamed text at index 0 (parent_tool_use_id None).
            fx.stream_event(fx.ev_text_start(0)),
            fx.stream_event(fx.ev_text_delta(0, "main agent says")),
            # Subagent message arrives mid-stream — its fresh row must NOT touch
            # the main agent's blocks[0] live stream.
            fx.assistant_message(
                [fx.text_block("subagent says")],
                parent_tool_use_id="sub-1",
            ),
            # Main-agent stream continues and stops; its row survives intact.
            fx.stream_event(fx.ev_text_delta(0, " more")),
            fx.stream_event(fx.ev_stop(0)),
            # Main-agent completion (no usage -> no context update needed).
            fx.assistant_message([fx.text_block("main agent says more")]),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assistants = app.query(AssistantMessage)
        sources = [a.body.source for a in assistants]
        # Two distinct assistant rows: one main (streamed), one subagent (fresh).
        assert len(assistants) == 2
        assert "main agent says more" in sources
        assert "subagent says" in sources
        # The subagent fresh row did not overwrite the main streamed row, and vice
        # versa: each text appears exactly once, no orphan blank rows.
        assert sources.count("subagent says") == 1
        assert sources.count("main agent says more") == 1


async def test_no_tool_id_aliasing_main_and_subagent_share_index(monkeypatch):
    """A main-agent tool at index 0 and a subagent tool both render as separate rows.

    _render_complete keys subagent tools into the shared `tools` dict by block id, but
    the main agent's tool was keyed by its own (different) id, so there's no collision.
    """
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            # Main-agent tool stream at index 0.
            fx.stream_event(fx.ev_tool_start(0, "main-tool", "Grep")),
            # Subagent message with its own tool (different id).
            fx.assistant_message(
                [fx.tool_use_block("sub-tool", "Glob", {"pattern": "*.py"})],
                parent_tool_use_id="sub-1",
            ),
            # Main-agent completes its tool with input.
            fx.assistant_message(
                [fx.tool_use_block("main-tool", "Grep", {"pattern": "needle"})],
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        tools = app.query(ToolMessage)
        assert len(tools) == 2
        summaries = {
            _static_text(t.query_one(".tool-title", Static)) for t in tools
        }
        assert summaries == {"Grep  needle", "Glob  *.py"}


async def test_two_subagent_messages_render_independently(monkeypatch):
    """Two separate subagent AssistantMessages each mount their own fresh rows."""
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message(
                [fx.text_block("sub one")], parent_tool_use_id="sub-1",
            ),
            fx.assistant_message(
                [fx.text_block("sub two")], parent_tool_use_id="sub-2",
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assistants = app.query(AssistantMessage)
        assert [a.body.source for a in assistants] == ["sub one", "sub two"]


async def test_agent_child_tools_render_inside_agent_row(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.stream_event(fx.ev_tool_start(0, "agent-1", "Agent")),
            fx.assistant_message([
                fx.tool_use_block("agent-1", "Agent", {
                    "description": "Read probe.txt",
                    "prompt": "Read probe.txt",
                    "subagent_type": "general-purpose",
                }),
            ]),
            fx.task_started_message("task-1", "agent-1", "Read probe.txt"),
            fx.task_progress_message(
                "task-1",
                "agent-1",
                "Reading probe.txt",
                last_tool_name="Read",
                task_usage={"total_tokens": 7726, "tool_uses": 1, "duration_ms": 3730},
            ),
            fx.assistant_message(
                [fx.tool_use_block("read-1", "Read", {"file_path": "/tmp/probe.txt"})],
                parent_tool_use_id="agent-1",
            ),
            fx.user_message(
                [fx.tool_result_block("read-1", content="1\talpha\n2\tbeta\n", is_error=None)],
                parent_tool_use_id="agent-1",
            ),
            fx.task_notification_message(
                "task-1",
                "agent-1",
                "completed",
                "Read probe.txt",
                task_usage={"total_tokens": 8407, "tool_uses": 1, "duration_ms": 5623},
            ),
            fx.user_message(
                [fx.tool_result_block("agent-1", content=[
                    {"type": "text", "text": "subagent saw 2 lines"},
                    {"type": "text", "text": "agentId: task-1\n<usage>hidden</usage>"},
                ], is_error=None)],
                tool_use_result={
                    "status": "completed",
                    "agentId": "task-1",
                    "totalToolUseCount": 1,
                    "totalTokens": 8407,
                    "totalDurationMs": 5623,
                    "content": [{"type": "text", "text": "subagent saw 2 lines"}],
                },
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        tools = app.query(ToolMessage)
        assert len(tools) == 1
        agent = tools.first()
        assert _static_text(agent.query_one(".tool-title", Static)) == "Agent"
        assert _static_text(agent.query_one(".tool-detail", Static)) == (
            "general-purpose - Read probe.txt"
        )
        assert agent.state == "done"

        rows = [row for row in agent.query(ToolSubtoolLine) if row.display]
        assert rows == []

        metrics = _static_text(agent.query_one(".tool-metrics", Static))
        assert metrics == "└─ Completed · Tools: 1 · Tokens: 8.4k · Time: 5.6s"
        assert _static_text(agent.query_one(".tool-preview", Static)) == ""
        assert agent._block_preview == "subagent saw 2 lines"
        assert "agentId:" not in agent._block_preview


async def test_agent_child_tool_row_shows_tool_input_while_running(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("agent-1", "Agent", {
                    "description": "Read probe.txt",
                    "prompt": "Read probe.txt",
                    "subagent_type": "general-purpose",
                }),
            ]),
            fx.assistant_message(
                [fx.tool_use_block("read-1", "Read", {"file_path": "/tmp/probe.txt"})],
                parent_tool_use_id="agent-1",
            ),
            fx.user_message(
                [fx.tool_result_block("read-1", content="1\talpha\n2\tbeta\n", is_error=None)],
                parent_tool_use_id="agent-1",
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        agent = app.query(ToolMessage).first()
        rows = [row for row in agent.query(ToolSubtoolLine) if row.display]
        assert len(rows) == 1
        assert rows[0].has_class("-done")
        assert str(rows[0].query_one(".tool-subtool-status", Static).render()) == "└─"
        assert str(rows[0].query_one(".tool-subtool-title", Static).render()) == "Read"
        assert str(rows[0].query_one(".tool-subtool-detail", Static).render()) == (
            "/tmp/probe.txt"
        )


async def test_agent_child_tool_rows_show_hidden_call_indicator(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("agent-1", "Agent", {
                    "description": "Explore repo",
                    "prompt": "Explore repo",
                    "subagent_type": "Explore",
                }),
            ]),
            fx.assistant_message(
                [fx.tool_use_block("read-1", "Read", {"file_path": "/tmp/a.py"})],
                parent_tool_use_id="agent-1",
            ),
            fx.assistant_message(
                [fx.tool_use_block("grep-1", "Grep", {"pattern": "needle"})],
                parent_tool_use_id="agent-1",
            ),
            fx.assistant_message(
                [fx.tool_use_block("bash-1", "Bash", {"command": "pytest"})],
                parent_tool_use_id="agent-1",
            ),
            fx.assistant_message(
                [fx.tool_use_block("glob-1", "Glob", {"pattern": "*.py"})],
                parent_tool_use_id="agent-1",
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        agent = app.query(ToolMessage).first()
        rows = [row for row in agent.query(ToolSubtoolLine) if row.display]
        assert len(rows) == 4
        assert [str(row.query_one(".tool-subtool-title", Static).render()) for row in rows] == [
            "…",
            "Grep  needle",
            "Bash",
            "Glob  *.py",
        ]
        assert rows[0].has_class("-overflow")
        assert str(rows[0].query_one(".tool-subtool-status", Static).render()) == "└─"
        assert str(rows[0].query_one(".tool-subtool-detail", Static).render()) == (
            "1 earlier tool call hidden"
        )


async def test_agent_task_updated_failure_uses_task_id_mapping(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("agent-1", "Agent", {
                    "description": "Explore repo",
                    "prompt": "Explore repo",
                    "subagent_type": "Explore",
                }),
            ]),
            fx.task_started_message("task-1", "agent-1", "Explore repo"),
            fx.task_updated_message(
                "task-1",
                {"status": "failed", "error": "subagent crashed"},
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        agent = app.query(ToolMessage).first()
        rows = [row for row in agent.query(ToolSubtoolLine) if row.display]
        assert len(rows) == 1
        assert rows[0].has_class("-error")
        assert str(rows[0].query_one(".tool-subtool-status", Static).render()) == "└─"
        assert str(rows[0].query_one(".tool-subtool-title", Static).render()) == "failed"
        assert str(rows[0].query_one(".tool-subtool-detail", Static).render()) == (
            "subagent crashed"
        )


async def test_agent_task_updated_killed_uses_message_status(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("agent-1", "Agent", {
                    "description": "Explore repo",
                    "prompt": "Explore repo",
                    "subagent_type": "Explore",
                }),
            ]),
            fx.task_progress_message("task-1", "agent-1", "Working"),
            fx.task_updated_message(
                "task-1",
                {"description": "interrupted by user"},
                status="killed",
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        agent = app.query(ToolMessage).first()
        rows = [row for row in agent.query(ToolSubtoolLine) if row.display]
        assert len(rows) == 1
        assert rows[0].has_class("-error")
        assert str(rows[0].query_one(".tool-subtool-title", Static).render()) == "killed"
        assert str(rows[0].query_one(".tool-subtool-detail", Static).render()) == (
            "interrupted by user"
        )
