"""Characterization tests for Chat(VerticalScroll) scroll-follow behavior.

Chat.follow starts True and watch_scroll_y flips it based on whether scroll_y is
within 1 of max_scroll_y. _stick(chat) calls scroll_end only while follow is True.

These must use GRADUAL mounts (mount + await pilot.pause() per step) so that the
container reflows and watch_scroll_y fires between mounts — a one-shot burst never
gives the layout a chance to flip follow.
"""

from textual import work
from textual.widgets import Static

from castcode.app import CastcodeApp
from castcode.ui.layout import Banner, Chat
from castcode.ui.messages import ToolMessage, UserMessage


async def _noop(self):
    pass


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


async def _grow(chat, pilot, n, widget_factory=lambda i: Static("x")):
    """Mount n widgets one at a time, pumping the UI after each."""
    for i in range(n):
        await chat.mount(widget_factory(i))
        await pilot.pause()


async def _settle_at_bottom(app, chat, pilot, steps=6):
    """Pump until the scroll position stops climbing.

    _stick calls scroll_end right after a mount, before the new row has reflowed,
    so scroll_y can trail max_scroll_y by a row (a full margin-bearing ChatMessage
    is 2 rows, which exceeds the 1-row follow tolerance). Re-stick until scroll_y
    is stable so assertions see the settled steady state, not a mid-reflow frame.
    This converges only while follow is True, so it can't mask a stuck-follow bug.
    """
    prev = None
    for _ in range(steps):
        app._stick(chat)
        await pilot.pause()
        if chat.scroll_y == prev:
            break
        prev = chat.scroll_y


# --- (a) short content: stays at top, follow stays True, no negative scroll ---

async def test_short_content_does_not_drop(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test(size=(80, 40)) as pilot:
        chat = app.query_one("#chat", Chat)
        await pilot.pause()

        # Banner alone fits in a 40-row viewport -> nothing to scroll.
        assert chat.scroll_y == 0
        assert chat.follow is True
        assert chat.max_scroll_y == 0

        # Mount a couple of short widgets; still fits.
        await _grow(chat, pilot, 2)

        assert chat.scroll_y == 0
        assert chat.follow is True
        # never went negative
        assert chat.scroll_y >= 0
        # Banner is still the first child, at the top.
        assert isinstance(chat.children[0], Banner)
        banner = chat.query_one(Banner)
        assert banner.region.y == chat.content_region.y


# --- (b) overflow while at bottom: _stick follows, scroll_y tracks max --------

async def test_overflow_sticks_to_bottom(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test(size=(80, 12)) as pilot:
        chat = app.query_one("#chat", Chat)
        await pilot.pause()

        # Grow past the viewport, calling _stick like the real send() loop does
        # after every mount.
        for i in range(40):
            await chat.mount(Static(f"line {i}"))
            app._stick(chat)
            await pilot.pause()

        await _settle_at_bottom(app, chat, pilot)
        assert chat.max_scroll_y > 0
        assert chat.follow is True
        # scroll_y tracked the growing max (within the 1-row tolerance).
        assert chat.scroll_y >= chat.max_scroll_y - 1


# --- (c) after scrolling UP, follow goes False and new mounts do not stick ----

async def test_scroll_up_disables_follow_and_sticking(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test(size=(80, 12)) as pilot:
        chat = app.query_one("#chat", Chat)
        await pilot.pause()

        for i in range(40):
            await chat.mount(Static(f"line {i}"))
            app._stick(chat)
            await pilot.pause()

        assert chat.max_scroll_y > 0
        assert chat.follow is True

        # User scrolls up toward the top.
        chat.scroll_to(y=0, animate=False)
        await pilot.pause()

        assert chat.scroll_y == 0
        assert chat.follow is False

        # New mounts + _stick must NOT yank us back to the bottom.
        before = chat.scroll_y
        for i in range(10):
            await chat.mount(Static(f"more {i}"))
            app._stick(chat)
            await pilot.pause()

        assert chat.follow is False
        assert chat.scroll_y == before
        # we are nowhere near the (now larger) bottom
        assert chat.scroll_y < chat.max_scroll_y - 1


# --- (d) returning to bottom re-enables follow; sticking resumes --------------

async def test_scroll_end_reenables_follow(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test(size=(80, 12)) as pilot:
        chat = app.query_one("#chat", Chat)
        await pilot.pause()

        for i in range(40):
            await chat.mount(Static(f"line {i}"))
            app._stick(chat)
            await pilot.pause()

        # Scroll up -> follow off.
        chat.scroll_to(y=0, animate=False)
        await pilot.pause()
        assert chat.follow is False

        # Return to the bottom.
        chat.scroll_end(animate=False)
        await pilot.pause()

        assert chat.follow is True
        assert chat.scroll_y >= chat.max_scroll_y - 1

        # Sticking resumes: new mounts keep us pinned to the bottom.
        for i in range(10):
            await chat.mount(Static(f"tail {i}"))
            app._stick(chat)
            await pilot.pause()

        await _settle_at_bottom(app, chat, pilot)
        assert chat.follow is True
        assert chat.scroll_y >= chat.max_scroll_y - 1


# --- extra: UserMessage widgets grow the chat the same way --------------------

async def test_usermessage_mounts_stick(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test(size=(80, 12)) as pilot:
        chat = app.query_one("#chat", Chat)
        await pilot.pause()

        for i in range(30):
            await chat.mount(UserMessage(f"message number {i}"))
            app._stick(chat)
            await pilot.pause()

        await _settle_at_bottom(app, chat, pilot)
        assert chat.max_scroll_y > 0
        assert chat.follow is True
        assert chat.scroll_y >= chat.max_scroll_y - 1


async def test_tall_tool_message_first_overflow_sticks_after_layout(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test(size=(80, 12)) as pilot:
        chat = app.query_one("#chat", Chat)
        await pilot.pause()

        await chat.mount(ToolMessage(
            "Write",
            "file.txt",
            [{"kind": "add", "line": "1", "marker": "+", "text": "x"}],
            tool_name="Write",
        ))
        app._stick(chat)
        await pilot.pause()
        await pilot.pause()

        assert chat.max_scroll_y > 0
        assert chat.follow is True
        assert chat.scroll_y >= chat.max_scroll_y - 1
