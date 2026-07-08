from textual import events
from textual.message import Message
from textual.widgets import Static

from castcode.ui.selectors import selector_lines

PICKER_WINDOW = 10


class Switcher(Static):
    class Selected(Message):
        def __init__(self, row: dict) -> None:
            self.row = row
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(self, rows: list[dict], *args, **kwargs) -> None:
        kwargs.setdefault("markup", False)
        super().__init__(*args, **kwargs)
        self.rows = rows
        self.index = 0
        self.can_focus = True

    def on_mount(self) -> None:
        self._render_rows()
        self.focus()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.post_message(self.Cancelled())
        elif event.key in ("up", "down") and self.rows:
            event.stop()
            event.prevent_default()
            delta = -1 if event.key == "up" else 1
            self.index = (self.index + delta) % len(self.rows)
            self._render_rows()
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            if self.rows:
                self.post_message(self.Selected(self.rows[self.index]))

    def _render_rows(self) -> None:
        if not self.rows:
            self.update("No saved sessions")
            return
        self.update(selector_lines(
            self.rows,
            self.index,
            title_key="title",
            fallback_key="session_id",
            detail_key="subtitle",
            size=PICKER_WINDOW,
        ))


class RewindPicker(Static):
    class Selected(Message):
        def __init__(self, checkpoint: dict, operation: str) -> None:
            self.checkpoint = checkpoint
            self.operation = operation
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(self, rows: list[dict], *args, index: int = 0, **kwargs) -> None:
        kwargs.setdefault("markup", False)
        super().__init__(*args, **kwargs)
        self.rows = rows
        self.index = index
        self.selected_checkpoint: dict | None = None
        self.operations = [
            {
                "operation": "restore",
                "title": "restore before this turn",
                "subtitle": "files unchanged",
            },
            {
                "operation": "reload",
                "title": "reload/edit prompt",
                "subtitle": "files unchanged",
            },
        ]
        self.can_focus = True

    def on_mount(self) -> None:
        self._render_rows()
        self.focus()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.post_message(self.Cancelled())
        elif event.key in ("up", "down") and self._active_rows():
            event.stop()
            event.prevent_default()
            delta = -1 if event.key == "up" else 1
            self.index = (self.index + delta) % len(self._active_rows())
            self._render_rows()
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            if self.selected_checkpoint is not None:
                operation = self.operations[self.index]["operation"]
                self.post_message(self.Selected(self.selected_checkpoint, operation))
            elif self.rows:
                row = self.rows[self.index]
                if row.get("current"):
                    self.post_message(self.Cancelled())
                else:
                    self.selected_checkpoint = row["checkpoint"]
                    self.index = 0
                    self._render_rows()

    def _active_rows(self):
        return self.operations if self.selected_checkpoint is not None else self.rows

    def _render_rows(self) -> None:
        if not self.rows:
            self.update("No checkpoints captured yet.")
            return
        self.update(selector_lines(
            self._active_rows(),
            self.index,
            title_key="title",
            detail_key="subtitle",
            size=PICKER_WINDOW,
        ))


class ModelPicker(Static):
    class Selected(Message):
        def __init__(self, row: dict) -> None:
            self.row = row
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(self, rows: list[dict], *args, **kwargs) -> None:
        kwargs.setdefault("markup", False)
        super().__init__(*args, **kwargs)
        self.rows = rows
        self.index = 0
        self.can_focus = True

    def on_mount(self) -> None:
        self._render_rows()
        self.focus()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.post_message(self.Cancelled())
        elif event.key in ("up", "down") and self.rows:
            event.stop()
            event.prevent_default()
            delta = -1 if event.key == "up" else 1
            self.index = (self.index + delta) % len(self.rows)
            self._render_rows()
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            if self.rows:
                self.post_message(self.Selected(self.rows[self.index]))

    def _render_rows(self) -> None:
        if not self.rows:
            self.update("No models available")
            return
        self.update(selector_lines(
            self.rows,
            self.index,
            title_key="title",
            fallback_key="model_id",
            detail_key="detail",
            size=PICKER_WINDOW,
        ))
