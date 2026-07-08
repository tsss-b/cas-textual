from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Vertical
from textual.widgets import Static

from castcode.ui.input import CommandPopup, Prompt
from castcode.ui.layout import Banner, Chat
from castcode.ui.status import StatusLine


class ChatScreen(Screen):
    def compose(self) -> ComposeResult:
        app = self.app
        yield Chat(Banner(), id="chat")
        activity = Static("", id="activity")
        activity.display = False
        yield activity
        todos = Static("", id="todos")
        todos.display = False
        yield todos
        yield CommandPopup(id="command-popup")
        takeover = Vertical(id="bottom-takeover")
        takeover.display = False
        yield takeover
        yield Prompt(id="prompt")
        yield StatusLine(app._status_data(), id="status")

    def on_mount(self) -> None:
        self.query_one(Prompt).focus()
