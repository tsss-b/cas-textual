from textual import events
from textual.app import ComposeResult
from textual.containers import Center, Vertical, VerticalScroll
from textual.widgets import Static

from castcode.format import gradient_banner
from castcode.ui.messages import ChatMessage
from castcode.ui.messages import from_record


class Banner(Vertical):
    def compose(self) -> ComposeResult:
        with Center():
            yield Static("", classes="banner-art")

    def on_mount(self) -> None:
        self.refresh_banner()

    def on_resize(self, event: events.Resize) -> None:
        self.refresh_banner()

    def refresh_banner(self) -> None:
        theme = self.app.current_theme
        width = self.content_size.width or self.size.width or None
        art = gradient_banner(theme.primary, theme.accent, width=width)
        self.query_one(".banner-art", Static).update(art)


class Chat(VerticalScroll):
    follow = True

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self.follow = new_value >= self.max_scroll_y - 1

    async def rebuild(self, records) -> None:
        messages = list(self.query(ChatMessage))
        if messages:
            await self.query(ChatMessage).remove()
        widgets = [from_record(record) for record in records]
        if widgets:
            await self.mount(*widgets)
        self.follow = True

    def records(self):
        return [
            message.to_record()
            for message in self.query(ChatMessage)
            if not message.transient
        ]
