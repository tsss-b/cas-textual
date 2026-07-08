"""Characterization: ToolMessage widget contract.

ToolMessage was previously exercised only through the receive-loop (streaming /
tool-id / tool-results). These pin its widget contract directly.
"""

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from castcode.tool_render import BlockPreview
from castcode.ui.messages import ToolChangeLine, ToolMessage, ToolSubtoolLine


# --- ToolMessage widget contract (mounted) -----------------------------------

class _Host(App):
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="host")


def _text(tm, selector):
    return str(tm.query_one(selector, Static).render())


def _shown(tm, selector):
    return tm.query_one(selector, Static).display is True


async def _mount(app, tm, pilot):
    await app.query_one("#host").mount(tm)
    await pilot.pause()


async def test_bare_name_while_streaming():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Bash")          # constructed with just the name (streaming)
        await _mount(app, tm, pilot)
        assert _text(tm, ".tool-title") == "Bash"
        assert not _shown(tm, ".tool-detail")
        assert not _shown(tm, ".tool-preview")
        assert not _shown(tm, ".tool-error")
        assert tm.state == "running"
        assert str(tm.query_one(".marker", Static).render()) == "●\n└─"


async def test_with_input_shows_name_and_summary():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Bash", "ls -la", tool_name="Bash")
        await _mount(app, tm, pilot)
        assert _text(tm, ".tool-title") == "Bash"
        assert _text(tm, ".tool-detail") == "ls -la"
        assert tm.tool_name == "Bash"
        assert tm.state == "running"


async def test_empty_input_is_bare_name():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Foo")
        await _mount(app, tm, pilot)
        assert _text(tm, ".tool-title") == "Foo"


async def test_update_rewrites_body_in_place():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Bash")          # streamed placeholder
        await _mount(app, tm, pilot)
        tm.update_tool("Bash", "Bash", "echo hi")
        await pilot.pause()
        assert _text(tm, ".tool-title") == "Bash"
        assert _text(tm, ".tool-detail") == "echo hi"
        assert tm.tool_name == "Bash"


async def test_stable_tool_nodes_update_in_place():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Read")
        await _mount(app, tm, pilot)
        title = tm.query_one(".tool-title", Static)
        detail = tm.query_one(".tool-detail", Static)
        preview = tm.query_one(".tool-preview", Static)
        error = tm.query_one(".tool-error", Static)

        tm.update_tool("Read", "Read", "/tmp/x.py · offset 10 · limit 20")
        tm.show_result("3 lines read")
        tm.show_error("denied [literal]")
        await pilot.pause()

        assert tm.query_one(".tool-title", Static) is title
        assert tm.query_one(".tool-detail", Static) is detail
        assert tm.query_one(".tool-preview", Static) is preview
        assert tm.query_one(".tool-error", Static) is error
        assert _text(tm, ".tool-title") == "Read"
        assert _text(tm, ".tool-detail") == "/tmp/x.py · offset 10 · limit 20"
        assert _text(tm, ".tool-preview") == "3 lines read"
        assert _text(tm, ".tool-error") == "denied [literal]"
        assert all(_shown(tm, selector) for selector in (
            ".tool-detail", ".tool-preview", ".tool-error"
        ))


async def test_structured_change_preview_uses_change_rows():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage(
            "Write",
            "created.py",
            [
                {"kind": "add", "line": "1", "marker": "+", "text": "alpha"},
                {"kind": "remove", "line": "2", "marker": "-", "text": "beta"},
            ],
            tool_name="Write",
        )
        await _mount(app, tm, pilot)

        assert not _shown(tm, ".tool-preview")
        assert tm.query_one(".tool-changes").display is True
        rows = list(tm.query(ToolChangeLine))
        assert rows[0].display is True
        assert rows[0].has_class("-add")
        assert _text(rows[0], ".tool-change-line-no") == "1"
        assert _text(rows[0], ".tool-change-marker") == " + "
        assert _text(rows[0], ".tool-change-text") == "alpha"
        assert rows[1].display is True
        assert rows[1].has_class("-remove")
        assert _text(rows[1], ".tool-change-line-no") == "2"
        assert _text(rows[1], ".tool-change-marker") == " - "
        assert _text(rows[1], ".tool-change-text") == "beta"


async def test_structured_change_preview_can_return_to_plain_preview():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage(
            "Write",
            "created.py",
            [{"kind": "add", "line": "1", "marker": "+", "text": "alpha"}],
            tool_name="Write",
        )
        await _mount(app, tm, pilot)

        tm.show_result("File created successfully")
        await pilot.pause()

        assert tm.query_one(".tool-changes").display is False
        assert _shown(tm, ".tool-preview")
        assert _text(tm, ".tool-preview") == "File created successfully"


async def test_block_preview_uses_metrics_and_plain_block():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Agent", "Explore - ui", tool_name="Agent")
        await _mount(app, tm, pilot)

        tm.show_result(BlockPreview(
            metrics="Completed · Tools: 4 · Tokens: 13.5k · Time: 10.2s",
            text="## Summary\nDetails",
        ))
        await pilot.pause()

        assert not _shown(tm, ".tool-preview")
        assert _shown(tm, ".tool-metrics")
        assert tm.query_one(".tool-block-preview", Static).display is True
        assert _text(tm, ".tool-metrics") == (
            "└─ Completed · Tools: 4 · Tokens: 13.5k · Time: 10.2s"
        )
        assert tm._block_preview == "## Summary\nDetails"
        assert _text(tm, ".tool-block-preview") == "## Summary\nDetails"
        assert len(tm.query(".tool-output-label")) == 0


async def test_plain_block_preview_has_no_output_label():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Bash", "echo '# title'", tool_name="Bash")
        await _mount(app, tm, pilot)

        tm.show_result(BlockPreview("", "# title"))
        await pilot.pause()

        assert not _shown(tm, ".tool-preview")
        assert not _shown(tm, ".tool-metrics")
        assert _text(tm, ".tool-block-preview") == "# title"
        assert len(tm.query(".tool-output-label")) == 0


async def test_subtool_preview_uses_last_three_rows():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Agent", "Explore files", tool_name="Agent")
        await _mount(app, tm, pilot)

        tm.show_subtools([
            {"state": "done", "title": "Read", "detail": "a.py"},
            {"state": "done", "title": "Grep", "detail": "needle"},
            {"state": "running", "title": "Bash", "detail": "pytest"},
            {"state": "error", "title": "WebFetch", "detail": "failed"},
        ])
        await pilot.pause()

        assert tm.query_one(".tool-subtools").display is True
        rows = [row for row in tm.query(ToolSubtoolLine) if row.display]
        assert len(rows) == 4
        assert [_text(row, ".tool-subtool-title") for row in rows] == [
            "…",
            "Grep",
            "Bash",
            "WebFetch",
        ]
        assert rows[0].has_class("-overflow")
        assert rows[1].has_class("-done")
        assert rows[2].has_class("-running")
        assert rows[3].has_class("-error")
        assert _text(rows[0], ".tool-subtool-status") == "└─"
        assert _text(rows[0], ".tool-subtool-detail") == "1 earlier tool call hidden"
        assert _text(rows[2], ".tool-subtool-detail") == "pytest"


async def test_subtool_preview_with_three_rows_has_no_overflow_indicator():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Agent", "Explore files", tool_name="Agent")
        await _mount(app, tm, pilot)

        tm.show_subtools([
            {"state": "done", "title": "Read", "detail": "a.py"},
            {"state": "running", "title": "Bash", "detail": "pytest"},
            {"state": "error", "title": "WebFetch", "detail": "failed"},
        ])
        await pilot.pause()

        rows = [row for row in tm.query(ToolSubtoolLine) if row.display]
        assert len(rows) == 3
        assert [_text(row, ".tool-subtool-title") for row in rows] == [
            "Read",
            "Bash",
            "WebFetch",
        ]
        assert not any(row.has_class("-overflow") for row in rows)


async def test_set_state_toggles_done_error_classes():
    app = _Host()
    async with app.run_test() as pilot:
        tm = ToolMessage("Bash", "ls", tool_name="Bash")
        await _mount(app, tm, pilot)
        # running: neither status class set
        assert not tm.has_class("-done") and not tm.has_class("-error")
        tm.set_state("done")
        assert tm.state == "done"
        assert tm.has_class("-done") and not tm.has_class("-error")
        tm.set_state("error")
        assert tm.state == "error"
        assert tm.has_class("-error") and not tm.has_class("-done")
