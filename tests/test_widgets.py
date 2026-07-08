"""Characterization: direct widget-boundary tests for castcode.widgets.

These exercise the widgets DIRECTLY (their own compose / message / watcher),
not only transitively through CastcodeApp, so a castcode.widgets refactor is guarded on
its own terms.
"""

from textual import work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from castcode.app import CastcodeApp
from castcode.ui.input import Prompt
from castcode.ui.layout import Banner, Chat
from castcode.ui.messages import AssistantMessage

import castcode.format as fmt


async def _noop(self):
    pass


class _Host(App):
    """Bare host: a scroll container to mount a single widget under test into."""

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="host")


# --- AssistantMessage --------------------------------------------------------

async def test_assistant_message_body_is_empty_markdown():
    app = _Host()
    async with app.run_test() as pilot:
        msg = AssistantMessage()
        await app.query_one("#host").mount(msg)
        await pilot.pause()
        body = msg.body  # the @property
        assert isinstance(body, Markdown)
        assert body.has_class("body")
        assert body.source == ""
        # marker is a SEPARATE Static carrying just the ● glyph
        marker = msg.query_one(".marker", Static)
        assert marker is not body
        assert str(marker.render()) == "●"


# --- Banner ------------------------------------------------------------------

async def test_banner_composition():
    app = _Host()
    async with app.run_test() as pilot:
        banner = Banner()
        await app.query_one("#host").mount(banner)
        await pilot.pause()
        # gradient art (a Static built from gradient_banner)
        assert banner.query_one(".banner-art", Static) is not None
        assert len(banner.query("#banner-model")) == 0
        assert len(banner.query(".banner-cwd")) == 0


async def test_banner_stacks_logo_when_narrow():
    app = _Host()
    async with app.run_test(size=(48, 30)) as pilot:
        banner = Banner()
        await app.query_one("#host").mount(banner)
        await pilot.pause()

        art = banner.query_one(".banner-art", Static).render()
        assert art.plain == fmt.BANNER_STACKED


async def test_banner_uses_full_logo_when_wide():
    app = _Host()
    async with app.run_test(size=(140, 30)) as pilot:
        banner = Banner()
        await app.query_one("#host").mount(banner)
        await pilot.pause()

        art = banner.query_one(".banner-art", Static).render()
        assert art.plain == fmt.BANNER


# --- Prompt (isolated harness, not via CastcodeApp) -------------------------------

class _PromptHost(App):
    def __init__(self) -> None:
        super().__init__()
        self.submitted = []

    def compose(self) -> ComposeResult:
        yield Prompt()

    def on_prompt_submitted(self, event: Prompt.Submitted) -> None:
        self.submitted.append(event.text)


async def test_prompt_submits_stripped_text_without_self_clearing():
    app = _PromptHost()
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("  hello world  ")
        await pilot.press("enter")
        await pilot.pause()
        # Submitted carries the STRIPPED text; the Prompt does NOT clear itself
        # (the app clears on accept).
        assert app.submitted == ["hello world"]
        assert prompt.text == "  hello world  "


async def test_prompt_shift_enter_inserts_newline_no_submit():
    app = _PromptHost()
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        prompt.insert("abc")
        await pilot.press("shift+enter")
        await pilot.pause()
        assert app.submitted == []
        assert prompt.text == "abc\n"


async def test_prompt_empty_or_whitespace_does_not_submit():
    app = _PromptHost()
    async with app.run_test() as pilot:
        prompt = app.query_one(Prompt)
        prompt.focus()
        await pilot.press("enter")        # empty
        prompt.insert("   ")
        await pilot.press("enter")        # whitespace only
        await pilot.pause()
        assert app.submitted == []


# --- Chat.watch_scroll_y (direct unit; integration cases in test_scroll) -----

async def test_chat_watch_scroll_y_follow_threshold(monkeypatch):
    # Host in the real CastcodeApp so #chat gets its 1fr height from app.tcss and
    # overflows; then call watch_scroll_y directly with controlled values to pin
    # the `follow = new_value >= max_scroll_y - 1` threshold.
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        chat = app.query_one(Chat)
        for i in range(60):
            await chat.mount(Static(f"line {i}"))
        await pilot.pause()
        m = chat.max_scroll_y
        assert m > 1
        chat.watch_scroll_y(0, m)         # at the bottom -> follow
        assert chat.follow is True
        chat.watch_scroll_y(m, m - 1)     # within 1 of bottom -> still follow
        assert chat.follow is True
        chat.watch_scroll_y(m, m - 5)     # scrolled up -> stop following
        assert chat.follow is False
        chat.watch_scroll_y(m - 5, m)     # back to bottom -> resume
        assert chat.follow is True
