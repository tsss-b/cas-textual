from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Static, TextArea

from castcode.ui.selectors import selector_lines


COMMAND_SECTION_LABELS = {
    "local": "Castcode commands",
    "sdk": "Claude commands",
    "skill": "Skills",
    "plugin": "Plugins",
}
COMMAND_POPUP_WINDOW = 6


class CommandPopup(Vertical):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.display = False

    def compose(self) -> ComposeResult:
        yield Static("", markup=False, classes="command-popup-body")

    def update_rows(self, rows, index: int = 0) -> None:
        rows = list(rows or [])
        self.display = bool(rows)
        body = self.query_one(".command-popup-body", Static)
        body.update(selector_lines(
            rows,
            index,
            title_key="name",
            detail_key="description",
            size=COMMAND_POPUP_WINDOW,
            section_labels=COMMAND_SECTION_LABELS,
            section_style="bold",
            section_prefix="  == ",
            section_suffix=" ==",
        ))

    def close(self) -> None:
        self.display = False
        if self.is_mounted:
            self.query_one(".command-popup-body", Static).update("")


class Prompt(TextArea):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class CommandPopupChanged(Message):
        def __init__(self, rows: list[dict], index: int) -> None:
            self.rows = rows
            self.index = index
            super().__init__()

    class CommandPopupClosed(Message):
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.command_rows: list[dict] = []
        self._popup_rows: list[dict] = []
        self._popup_index = 0
        self._popup_dismissed_text: str | None = None

    def set_command_rows(self, rows) -> None:
        self.command_rows = [dict(row) for row in rows]
        self._refresh_command_popup()

    def close_command_popup(self) -> None:
        self._close_command_popup()

    async def _on_key(self, event: events.Key) -> None:
        if self._popup_rows and event.key == "tab":
            event.stop()
            event.prevent_default()
            self._accept_command_popup()
            return
        if self._popup_rows and event.key == "enter" and not self._popup_exact_match():
            event.stop()
            event.prevent_default()
            self._accept_command_popup()
            return
        if self._popup_rows and event.key == "escape":
            event.stop()
            event.prevent_default()
            self._popup_dismissed_text = self.text
            self._close_command_popup()
            return
        if self._popup_rows and event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            delta = -1 if event.key == "up" else 1
            self._popup_index = (self._popup_index + delta) % len(self._popup_rows)
            self.post_message(self.CommandPopupChanged(self._popup_rows, self._popup_index))
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            self._refresh_command_popup()
            return
        if event.key != "enter":
            await super()._on_key(event)
            self._refresh_command_popup()
            return
        event.stop()
        event.prevent_default()
        if text := self.text.strip():
            self.post_message(self.Submitted(text))

    async def _on_paste(self, event: events.Paste) -> None:
        await super()._on_paste(event)
        self._refresh_command_popup()

    def _refresh_command_popup(self) -> None:
        text = self.text
        row = self.cursor_location[0] if self.cursor_location else 0
        state = getattr(self.app, "interaction", None)
        if state and (
            state.sending
            or state.interruptible
            or state.interrupting
            or state.picker_open
            or state.transaction is not None
            or state.local_command_pending
        ):
            self._close_command_popup()
            return
        if row != 0 or not text.startswith("/"):
            self._popup_dismissed_text = None
            self._close_command_popup()
            return
        if self._popup_dismissed_text is not None:
            if text == self._popup_dismissed_text:
                self._close_command_popup()
                return
            self._popup_dismissed_text = None
        prefix = text.split()[0].lower()
        self._popup_rows = [
            row for row in self.command_rows
            if row.get("name", "").lower().startswith(prefix)
        ]
        self._popup_index = min(self._popup_index, max(len(self._popup_rows) - 1, 0))
        self.post_message(self.CommandPopupChanged(self._popup_rows, self._popup_index))

    def _accept_command_popup(self) -> None:
        row = self._popup_rows[self._popup_index]
        parts = self.text.split(maxsplit=1)
        suffix = f" {parts[1]}" if len(parts) > 1 else " "
        self.clear()
        self.insert(f"{row.get('name', '')}{suffix}")
        self._close_command_popup()

    def _popup_exact_match(self) -> bool:
        token = self.text.split(maxsplit=1)[0].lower()
        return any(row.get("name", "").lower() == token for row in self._popup_rows)

    def _close_command_popup(self) -> None:
        if self._popup_rows:
            self._popup_rows = []
            self._popup_index = 0
            self.post_message(self.CommandPopupClosed())


class InlineInput(Input):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    class Navigate(Message):
        def __init__(self, direction: str) -> None:
            self.direction = direction
            super().__init__()

    class NavigateQuestion(Message):
        def __init__(self, direction: str) -> None:
            self.direction = direction
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        if event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            self.post_message(self.Navigate(event.key))
            return
        if event.key == "left" and self.cursor_at_start:
            event.stop()
            event.prevent_default()
            self.post_message(self.NavigateQuestion(event.key))
            return
        if event.key == "right" and self.cursor_at_end:
            event.stop()
            event.prevent_default()
            self.post_message(self.NavigateQuestion(event.key))
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            if text := self.value.strip():
                self.post_message(self.Submitted(text))
            return
        await super()._on_key(event)
