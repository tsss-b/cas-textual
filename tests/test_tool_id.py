"""Characterization: tool placeholder is keyed by tool id, not content-block index.

Pins the behavior of CastcodeApp.send / _on_stream_event / _finalize_message when the SDK
splits one turn into multiple main-agent AssistantMessages whose content-block indices
BOTH start at 0. The streamed ToolMessage placeholder (created by content_block_start)
must be UPDATED IN PLACE when the matching full ToolUseBlock arrives (same id), and a
later tool that reuses index 0 but has a different id must create a DISTINCT row.

Assumption: tool ids are globally unique per the SDK, so duplicate ids never
occur. The real gotcha is REUSED content-block indices across AssistantMessages
with DISTINCT ids (the index space resets per message) — that is what these pin.
"""

from textual import work
from textual.widgets import Static

from castcode.app import CastcodeApp
from castcode.ui.messages import ToolMessage

import fixtures as fx


async def _noop(self):
    pass


def _bodies(app):
    return [str(tm.query_one(".tool-title", Static).render()) for tm in app.query(ToolMessage)]


def _details(app):
    return [str(tm.query_one(".tool-detail", Static).render()) for tm in app.query(ToolMessage)]


async def _run_burst(monkeypatch, messages):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(messages)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()
        yield app


# --- the placeholder is keyed by id and updated in place --------------------

async def test_placeholder_updated_in_place_single_id(monkeypatch):
    messages = [
        fx.stream_event(fx.ev_tool_start(index=0, id="t1", name="Bash")),
        fx.assistant_message([fx.tool_use_block("t1", "Bash", {"command": "ls -la"})]),
        fx.result_message(),
    ]
    async for app in _run_burst(monkeypatch, messages):
        tools = app.query(ToolMessage)
        # exactly one row for t1: placeholder reused, no orphan, no duplicate
        assert len(tools) == 1
        tm = tools.first()
        # body was updated in place with the finalized command detail.
        body = str(tm.query_one(".tool-title", Static).render())
        detail = str(tm.query_one(".tool-detail", Static).render())
        assert body == "Bash"
        assert detail == "ls -la"
        # the placeholder object is what got mounted (state still tracked)
        assert tm.state == "running"


async def test_placeholder_index_collision_two_distinct_ids(monkeypatch):
    """Both tool_use blocks arrive at content-block index 0 but with different ids."""
    messages = [
        fx.stream_event(fx.ev_tool_start(index=0, id="t1", name="Bash")),
        fx.assistant_message([fx.tool_use_block("t1", "Bash", {"command": "ls -la"})]),
        fx.stream_event(fx.ev_tool_start(index=0, id="t2", name="Read")),
        fx.assistant_message([fx.tool_use_block("t2", "Read", {"file_path": "/tmp/x.py"})]),
        fx.result_message(),
    ]
    async for app in _run_burst(monkeypatch, messages):
        tools = list(app.query(ToolMessage))
        # two distinct rows despite the shared index 0
        assert len(tools) == 2
        bodies = [str(tm.query_one(".tool-title", Static).render()) for tm in tools]
        details = _details(app)
        assert bodies == ["Bash", "Read"]
        assert details == ["ls -la", "/tmp/x.py"]


async def test_no_orphan_empty_row_after_update(monkeypatch):
    """After finalize updates the placeholder, there is no leftover bare-name row."""
    messages = [
        fx.stream_event(fx.ev_tool_start(index=0, id="t1", name="Bash")),
        fx.assistant_message([fx.tool_use_block("t1", "Bash", {"command": "echo hi"})]),
        fx.result_message(),
    ]
    async for app in _run_burst(monkeypatch, messages):
        bodies = _bodies(app)
        # no orphan row: the streamed placeholder now carries the command detail.
        details = _details(app)
        assert bodies == ["Bash"]
        assert details == ["echo hi"]


async def test_finalize_without_placeholder_mounts_new_row(monkeypatch):
    """If a main-agent ToolUseBlock has no streamed placeholder, finalize mounts one."""
    messages = [
        fx.assistant_message([fx.tool_use_block("t9", "Bash", {"command": "pwd"})]),
        fx.result_message(),
    ]
    async for app in _run_burst(monkeypatch, messages):
        tools = list(app.query(ToolMessage))
        assert len(tools) == 1
        assert str(tools[0].query_one(".tool-title", Static).render()) == "Bash"
        assert str(tools[0].query_one(".tool-detail", Static).render()) == "pwd"


async def test_two_placeholders_same_message_distinct_indices(monkeypatch):
    """Two placeholders at distinct indices, both updated by one AssistantMessage."""
    messages = [
        fx.stream_event(fx.ev_tool_start(index=0, id="t1", name="Bash")),
        fx.stream_event(fx.ev_tool_start(index=1, id="t2", name="Read")),
        fx.assistant_message([
            fx.tool_use_block("t1", "Bash", {"command": "ls"}),
            fx.tool_use_block("t2", "Read", {"file_path": "/tmp/y.py"}),
        ]),
        fx.result_message(),
    ]
    async for app in _run_burst(monkeypatch, messages):
        tools = list(app.query(ToolMessage))
        assert len(tools) == 2
        bodies = [str(tm.query_one(".tool-title", Static).render()) for tm in tools]
        details = _details(app)
        assert bodies == ["Bash", "Read"]
        assert details == ["ls", "/tmp/y.py"]


async def test_id_keyed_not_index_keyed_when_content_order_differs(monkeypatch):
    """The decisive id-vs-index case: a single AssistantMessage lists its two
    ToolUseBlocks in an order that does NOT match their stream-start indices.

    Stream order:  index 0 -> t1 (Bash),  index 1 -> t2 (Read)
    Content order: [t2 (Read), t1 (Bash)]  (reversed)

    Id-keyed finalize matches each block to its own placeholder, so the row mounted
    first (index 0 = t1) shows Bash and the second (index 1 = t2) shows Read. An
    index-keyed lookup would pair content[0]=t2 with placeholder index 0 (t1's row),
    swapping the summaries to ["Read …", "Bash …"]. This is the exact gotcha.
    """
    messages = [
        fx.stream_event(fx.ev_tool_start(index=0, id="t1", name="Bash")),
        fx.stream_event(fx.ev_tool_start(index=1, id="t2", name="Read")),
        fx.assistant_message([
            fx.tool_use_block("t2", "Read", {"file_path": "/tmp/y.py"}),
            fx.tool_use_block("t1", "Bash", {"command": "ls -la"}),
        ]),
        fx.result_message(),
    ]
    async for app in _run_burst(monkeypatch, messages):
        tools = list(app.query(ToolMessage))
        assert len(tools) == 2
        bodies = [str(tm.query_one(".tool-title", Static).render()) for tm in tools]
        # Mount order is stream/index order: row 0 == t1 (Bash), row 1 == t2 (Read).
        details = _details(app)
        assert bodies == ["Bash", "Read"]
        assert details == ["ls -la", "/tmp/y.py"]


async def test_placeholder_count_for_t1_is_exactly_one(monkeypatch):
    """Explicit: count ToolMessages whose body mentions the Bash/t1 summary == 1."""
    messages = [
        fx.stream_event(fx.ev_tool_start(index=0, id="t1", name="Bash")),
        fx.assistant_message([fx.tool_use_block("t1", "Bash", {"command": "ls -la"})]),
        fx.stream_event(fx.ev_tool_start(index=0, id="t2", name="Read")),
        fx.assistant_message([fx.tool_use_block("t2", "Read", {"file_path": "/tmp/x.py"})]),
        fx.result_message(),
    ]
    async for app in _run_burst(monkeypatch, messages):
        bodies = _bodies(app)
        details = _details(app)
        assert bodies.count("Bash") == 1
        assert details.count("ls -la") == 1
        assert bodies.count("Read") == 1
        assert details.count("/tmp/x.py") == 1
        assert len(app.query(ToolMessage)) == 2


async def test_input_json_delta_is_ignored_until_complete_tool_use(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.GatedFakeClient()
        app._connected_ok = True
        w = app.send("go")
        await pilot.pause()

        await app.conversation.session.client.feed(fx.stream_event(fx.ev_tool_start(0, "t1", "Bash")))
        await app.conversation.session.client.feed(
            fx.stream_event(fx.ev_input_delta(0, '{"command":"partial"}'))
        )
        await pilot.pause()

        tool = app.query(ToolMessage).first()
        assert str(tool.query_one(".tool-title", Static).render()) == "Bash"

        await app.conversation.session.client.feed(
            fx.assistant_message([
                fx.tool_use_block("t1", "Bash", {"command": "echo final"}),
            ])
        )
        await app.conversation.session.client.feed(fx.result_message())
        await w.wait()
        await pilot.pause()

        assert str(tool.query_one(".tool-title", Static).render()) == "Bash"
        assert str(tool.query_one(".tool-detail", Static).render()) == "echo final"
