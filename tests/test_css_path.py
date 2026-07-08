"""Characterization: shim / CSS load (coverage category "css_path").

Pins two facts that must survive the single-file -> castcode/ package move:

1. Importing the root `app` module has NO run() side effect (the `CastcodeApp().run()`
   call is guarded by `if __name__ == "__main__"`). Importing must not start the app.
2. The stylesheet declared by `CastcodeApp.CSS_PATH` ACTUALLY LOADS and APPLIES: known
   rules from app.tcss resolve onto real widgets under run_test (asserting computed
   styles, not just a path string), and app.tcss is present in the live stylesheet
   source.
"""

import importlib
import os
import sys

from textual import work
from textual.geometry import Spacing

from app import CastcodeApp
from castcode.tool_render import BlockPreview
from castcode.ui.messages import ToolChangeLine, ToolMessage

import fixtures as fx  # noqa: F401  (kept for suite-wide import convention)


async def _noop(self):
    pass


# --- clause 1: importing the module does not run the app --------------------

def test_module_imports_without_running_app(monkeypatch):
    # Spy on run(): if the module body called CastcodeApp().run() at import time we'd see it.
    calls = []
    monkeypatch.setattr(CastcodeApp, "run", lambda self, *a, **k: calls.append((a, k)))

    # Force a fresh import of the root `app` module and confirm no run() happens.
    saved = sys.modules.pop("app")
    try:
        reimported = importlib.import_module("app")
        assert hasattr(reimported, "CastcodeApp")  # shim re-exported CastcodeApp on import
    finally:
        sys.modules["app"] = saved

    assert calls == []


def test_runpy_main_invokes_run(monkeypatch):
    # Executing app.py as a script (__main__) MUST call CastcodeApp().run() exactly
    # once -- i.e. the entrypoint path is wired and not broken. run() is stubbed
    # so this does not launch the hanging TUI in CI.
    import runpy

    calls = []
    monkeypatch.setattr(CastcodeApp, "run", lambda self, *a, **k: calls.append((a, k)))
    src = importlib.util.find_spec("app").origin
    runpy.run_path(src, run_name="__main__")
    assert len(calls) == 1


# --- clause 2: CSS_PATH is declared and points at app.tcss ------------------

def test_css_path_points_at_app_tcss():
    # Pin the BEHAVIOR (the stylesheet filename), not the literal class-attr form:
    # step 1 of the refactor intentionally rewrites this from the bare string
    # "app.tcss" to a root-relative Path(...) / "app.tcss". Both must satisfy this.
    assert os.path.basename(str(CastcodeApp.CSS_PATH)) == "app.tcss"


async def test_css_path_resolves_to_app_tcss(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        # Textual resolves CSS_PATH relative to the module defining the class.
        resolved = [str(p) for p in app.css_path]
        assert len(resolved) == 1
        assert resolved[0].endswith("/app.tcss")


# --- clause 2: the stylesheet ACTUALLY LOADS (source present) ---------------

async def test_app_tcss_in_live_stylesheet_source(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        source = app.stylesheet.source
        app_keys = [k for k in source if k[0].endswith("app.tcss")]
        assert len(app_keys) == 1
        css_text = source[app_keys[0]].content
        # Known selectors from app.tcss are present in the loaded source.
        for selector in ("#chat", "#activity", "#status", "#prompt", "Banner"):
            assert selector in css_text


# --- clause 2: known rules APPLY to real widgets ----------------------------

async def test_known_rules_apply_to_widgets(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        # #chat { height: 1fr; padding: 1 2; }
        chat = app.query_one("#chat")
        assert str(chat.styles.height) == "1fr"
        assert chat.styles.padding == Spacing(top=1, right=2, bottom=1, left=2)

        # #activity { height: 1; padding: 0 2; }
        activity = app.query_one("#activity")
        assert str(activity.styles.height) == "1"
        assert activity.styles.padding == Spacing(top=0, right=2, bottom=0, left=2)

        # guard against defaults: distinct values prove rules apply, not just present
        assert chat.styles.height != activity.styles.height

        # #status { height: 1; padding: 0 2; width: 1fr; }
        status = app.query_one("#status")
        assert str(status.styles.height) == "1"
        assert status.styles.padding == Spacing(top=0, right=2, bottom=0, left=2)
        assert str(status.styles.width) == "1fr"
        assert status.styles.overflow_x == "hidden"
        assert status.styles.text_wrap == "nowrap"
        assert status.styles.text_overflow == "ellipsis"

        # #prompt { max-height: 12; }
        prompt = app.query_one("#prompt")
        assert str(prompt.styles.max_height) == "12"


async def test_tool_content_rows_wrap_while_title_stays_compact(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        tool = ToolMessage(
            "Read",
            "missing.txt",
            "1 line read",
            tool_name="Read",
        )
        await app.query_one("#chat").mount(tool)
        tool.show_error(
            "File does not exist. Note: your current working directory is "
            "/home/user/project."
        )
        await pilot.pause()

        title = tool.query_one(".tool-title")
        assert title.styles.text_wrap == "nowrap"
        assert title.styles.text_overflow == "ellipsis"
        assert title.styles.overflow_x == "hidden"

        for selector in (".tool-detail", ".tool-preview"):
            row = tool.query_one(selector)
            assert row.styles.text_wrap == "wrap"
            assert row.styles.overflow_x == "hidden"

        error = tool.query_one(".tool-error")
        assert error.styles.text_wrap == "wrap"
        assert str(error.styles.max_height) == "4"


async def test_tool_detail_error_class_colors_detail_like_error(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        tool = ToolMessage(
            "ToolSearch",
            "No matching tools found",
            tool_name="ToolSearch",
        )
        await app.query_one("#chat").mount(tool)
        tool.set_detail_error(True)
        await pilot.pause()

        detail = tool.query_one(".tool-detail")
        error = tool.query_one(".tool-error")
        assert detail.styles.color == error.styles.color


async def test_tool_change_rows_have_background_and_muted_line_numbers(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        tool = ToolMessage(
            "Edit",
            "edited.py",
            [
                {"kind": "remove", "line": "4", "marker": "-", "text": "old"},
                {"kind": "add", "line": "4", "marker": "+", "text": "new"},
            ],
            tool_name="Edit",
        )
        await app.query_one("#chat").mount(tool)
        await pilot.pause()

        rows = [row for row in tool.query(ToolChangeLine) if row.display]
        remove, add = rows
        assert remove.styles.background.a == 1.0
        assert add.styles.background.a == 1.0
        assert remove.styles.background != add.styles.background
        assert tool.query_one(".tool-changes").styles.padding == Spacing(0, 0, 0, 0)
        for row in rows:
            assert str(row.styles.height) == "auto"
            assert row.styles.padding == Spacing(0, 0, 0, 2)
            line_no = row.query_one(".tool-change-line-no")
            marker = row.query_one(".tool-change-marker")
            text = row.query_one(".tool-change-text")
            assert str(line_no.styles.text_align) == "left"
            assert marker.styles.color == line_no.styles.color
            assert text.styles.text_wrap == "wrap"
            assert text.styles.overflow_x == "hidden"


async def test_tool_block_output_uses_left_edge(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        tool = ToolMessage("Agent", "Explore - ui", tool_name="Agent")
        await app.query_one("#chat").mount(tool)
        tool.show_result(BlockPreview("Completed · Tools: 1", "## Summary\nDone"))
        await pilot.pause()

        output = tool.query_one(".tool-block-preview")

        assert len(tool.query(".tool-output-label")) == 0
        assert output.display is True
        assert output.styles.border_left[0] == "heavy"
        assert output.styles.margin == Spacing(0, 0, 0, 0)
        assert output.styles.padding == Spacing(0, 0, 0, 2)


async def test_status_line_fits_at_narrow_width(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test(size=(40, 20)) as pilot:
        await pilot.pause()
        status = app.query_one("#status")
        text = str(status.render())

        assert "\n" not in text
        assert len(text) <= status.content_size.width
        assert status.styles.overflow_x == "hidden"
