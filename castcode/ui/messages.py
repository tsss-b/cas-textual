from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Markdown, Static

from castcode.records import (
    AssistantRecord,
    BlockPreview,
    NoticeRecord,
    ToolRecord,
    UserRecord,
)


def from_record(record):
    if isinstance(record, UserRecord):
        return UserMessage(record.text, uuid=record.uuid)
    if isinstance(record, AssistantRecord):
        return AssistantMessage(record.text)
    if isinstance(record, NoticeRecord):
        return NoticeMessage(record.text)
    if isinstance(record, ToolRecord):
        return ToolMessage(
            record.title,
            record.detail,
            record.preview,
            tool_name=record.tool_name,
            state=record.state,
            subtools=record.subtools,
            error=record.error,
            detail_error=record.detail_error,
        )
    raise TypeError(f"unsupported record: {record!r}")


class ChatMessage(Horizontal):
    marker = ""
    # True for rows that live in the chat but are not transcript (permission
    # prompts): Chat.records() must skip them — they have no to_record().
    transient = False

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self._body = text

    def compose(self) -> ComposeResult:
        yield Static(self.marker, markup=False, classes="marker")
        yield self._make_body()

    def _make_body(self):
        return Static(self._body, markup=False, classes="body")


class UserMessage(ChatMessage):
    marker = ">"

    def __init__(self, text: str = "", *, uuid: str | None = None) -> None:
        super().__init__(text)
        self._uuid = uuid

    def set_uuid(self, uuid: str | None) -> None:
        self._uuid = uuid

    def to_record(self) -> UserRecord:
        return UserRecord(self._body, self._uuid)


class AssistantMessage(ChatMessage):
    marker = "●"

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._source = text

    def _make_body(self):
        return Markdown(self._source, open_links=False, classes="body")

    @property
    def body(self) -> Markdown:
        return self.query_one(Markdown)

    async def update_text(self, text: str) -> None:
        self._source = text
        await self.body.update(text)

    def append_source(self, text: str) -> None:
        self._source += text

    def to_record(self) -> AssistantRecord:
        if self.is_mounted:
            self._source = self.body.source
        return AssistantRecord(self._source)


class NoticeMessage(ChatMessage):
    marker = "⊘"

    def to_record(self) -> NoticeRecord:
        return NoticeRecord(self._body)


class ToolChangeLine(Horizontal):
    def __init__(self, row: dict[str, str] | None = None) -> None:
        super().__init__()
        self._kind = "context"
        self._line = ""
        self._marker = ""
        self._text = ""
        self.display = False
        if row is not None:
            self.update_line(row)

    def compose(self) -> ComposeResult:
        yield Static(self._line, markup=False, classes="tool-change-line-no")
        yield Static(self._marker, markup=False, classes="tool-change-marker")
        yield Static(self._text, markup=False, classes="tool-change-text")

    def update_line(self, row: dict[str, str]) -> None:
        self._kind = row.get("kind", "context")
        self._line = row.get("line", "")
        marker = row.get("marker", "")
        self._marker = f" {marker} " if self._line and marker else marker
        self._text = row.get("text", "")
        self.display = True
        self.set_class(self._kind == "add", "-add")
        self.set_class(self._kind == "remove", "-remove")
        self.set_class(self._kind == "context", "-context")
        if self.is_mounted:
            self.query_one(".tool-change-line-no", Static).update(self._line)
            self.query_one(".tool-change-marker", Static).update(self._marker)
            self.query_one(".tool-change-text", Static).update(self._text)

    def clear_line(self) -> None:
        self._kind = "context"
        self._line = ""
        self._marker = ""
        self._text = ""
        self.display = False
        self.set_class(False, "-add")
        self.set_class(False, "-remove")
        self.set_class(False, "-context")
        if self.is_mounted:
            self.query_one(".tool-change-line-no", Static).update("")
            self.query_one(".tool-change-marker", Static).update("")
            self.query_one(".tool-change-text", Static).update("")


class ToolSubtoolLine(Horizontal):
    def __init__(self, row: dict[str, str] | None = None) -> None:
        super().__init__()
        self._state = "running"
        self._title = ""
        self._detail = ""
        self.display = False
        if row is not None:
            self.update_line(row)

    def compose(self) -> ComposeResult:
        yield Static(
            "└─",
            markup=False,
            classes="tool-subtool-status",
        )
        yield Static(self._title, markup=False, classes="tool-subtool-title")
        yield Static(self._detail, markup=False, classes="tool-subtool-detail")

    def update_line(self, row: dict[str, str]) -> None:
        self._state = row.get("state", "running")
        self._title = row.get("title", "")
        self._detail = row.get("detail", "")
        self.display = True
        self.set_class(self._state == "done", "-done")
        self.set_class(self._state == "error", "-error")
        self.set_class(self._state == "running", "-running")
        self.set_class(self._state == "overflow", "-overflow")
        if self.is_mounted:
            self.query_one(".tool-subtool-status", Static).update("└─")
            self.query_one(".tool-subtool-title", Static).update(self._title)
            self.query_one(".tool-subtool-detail", Static).update(self._detail)

    def clear_line(self) -> None:
        self._state = "running"
        self._title = ""
        self._detail = ""
        self.display = False
        self.set_class(False, "-done")
        self.set_class(False, "-error")
        self.set_class(False, "-running")
        self.set_class(False, "-overflow")
        if self.is_mounted:
            self.query_one(".tool-subtool-status", Static).update("")
            self.query_one(".tool-subtool-title", Static).update("")
            self.query_one(".tool-subtool-detail", Static).update("")


class ToolMessage(ChatMessage):
    marker = "●\n└─"
    _change_row_count = 8
    _subtool_row_count = 4
    _subtool_visible_count = 3

    def __init__(
        self,
        title: str,
        detail: str = "",
        preview = "",
        *,
        tool_name: str | None = None,
        state: str = "running",
        subtools: list[dict[str, str]] | None = None,
        error: str = "",
        detail_error: bool = False,
    ) -> None:
        super().__init__()
        self.tool_name = tool_name or title
        self._title = title
        self._detail = detail
        self._preview = ""
        self._preview_kind = "string"
        self._metrics = ""
        self._block_preview = ""
        self._changes = []
        self._subtools = []
        self._display_subtools = []
        self._error = error
        self._detail_error = detail_error
        self.state = "running"
        self.change_rows = [ToolChangeLine() for _ in range(self._change_row_count)]
        self.subtool_rows = [
            ToolSubtoolLine() for _ in range(self._subtool_row_count)
        ]
        self._set_preview(preview)
        self._set_subtools(subtools or [])
        self.set_state(state)
        self.set_detail_error(detail_error)

    def _make_body(self):
        return Vertical(
            Static(self._title, markup=False, classes="tool-title"),
            Static(self._detail, markup=False, classes="tool-detail"),
            Static(self._preview, markup=False, classes="tool-preview"),
            Static(self._metrics_text(), markup=False, classes="tool-metrics"),
            Static(
                self._block_preview,
                markup=False,
                classes="tool-block-preview",
            ),
            Vertical(*self.subtool_rows, classes="tool-subtools"),
            Vertical(*self.change_rows, classes="tool-changes"),
            Static(self._error, markup=False, classes="tool-error"),
            classes="body",
        )

    def on_mount(self) -> None:
        self._sync_optional_rows()

    def _sync_optional_rows(self) -> None:
        for selector in (".tool-detail", ".tool-preview", ".tool-metrics", ".tool-error"):
            widget = self.query_one(selector, Static)
            widget.display = bool(str(widget.render()))
        self.query_one(".tool-block-preview", Static).display = bool(
            self._block_preview
        )
        self.query_one(".tool-changes", Vertical).display = any(
            row.display for row in self.change_rows
        )
        self.query_one(".tool-subtools", Vertical).display = any(
            row.display for row in self.subtool_rows
        )

    def _set_preview(self, preview) -> None:
        self._metrics = ""
        self._block_preview = ""
        if isinstance(preview, list):
            self._preview_kind = "changes"
            self._preview = ""
            self._changes = [
                dict(row) for row in preview[:self._change_row_count]
                if isinstance(row, dict)
            ]
        elif hasattr(preview, "metrics") and hasattr(preview, "text"):
            self._preview_kind = "block"
            self._preview = ""
            self._metrics = str(preview.metrics)
            self._block_preview = str(preview.text)
            self._changes = []
        else:
            self._preview_kind = "string"
            self._preview = str(preview)
            self._changes = []
        for index, row in enumerate(self.change_rows):
            if index < len(self._changes):
                row.update_line(self._changes[index])
            else:
                row.clear_line()

    def _metrics_text(self) -> str:
        return f"└─ {self._metrics}" if self._metrics else ""

    def _set_subtools(self, rows: list[dict[str, str]]) -> None:
        rows = [row for row in rows if isinstance(row, dict)]
        self._subtools = [dict(row) for row in rows]
        self._display_subtools = self._subtools[-self._subtool_visible_count:]
        hidden_count = len(self._subtools) - len(self._display_subtools)
        if hidden_count > 0:
            label = "tool call" if hidden_count == 1 else "tool calls"
            self._display_subtools = [
                {
                    "state": "overflow",
                    "title": "…",
                    "detail": f"{hidden_count} earlier {label} hidden",
                },
                *self._display_subtools,
            ]
        for index, row in enumerate(self.subtool_rows):
            if index < len(self._display_subtools):
                row.update_line(self._display_subtools[index])
            else:
                row.clear_line()

    def update_tool(self, name: str, title: str, detail: str = "", preview: str = "") -> None:
        self.tool_name = name
        self._title = title
        self._detail = detail
        self._set_preview(preview)
        self.query_one(".tool-title", Static).update(title)
        self.query_one(".tool-detail", Static).update(detail)
        self.query_one(".tool-preview", Static).update(self._preview)
        self.query_one(".tool-metrics", Static).update(self._metrics_text())
        self.query_one(".tool-block-preview", Static).update(self._block_preview)
        self._sync_optional_rows()

    def show_subtools(self, rows: list[dict[str, str]]) -> None:
        self._set_subtools(rows)
        self._sync_optional_rows()

    def show_result(self, preview = "") -> None:
        self._set_preview(preview)
        self.query_one(".tool-preview", Static).update(self._preview)
        self.query_one(".tool-metrics", Static).update(self._metrics_text())
        self.query_one(".tool-block-preview", Static).update(self._block_preview)
        self._sync_optional_rows()

    def show_error(self, text: str) -> None:
        self._error = text
        self.query_one(".tool-error", Static).update(text)
        self._sync_optional_rows()

    def set_state(self, state: str) -> None:
        self.state = state
        self.set_class(state == "done", "-done")
        self.set_class(state == "error", "-error")

    def set_detail_error(self, enabled: bool) -> None:
        self._detail_error = enabled
        self.set_class(enabled, "-detail-error")

    def to_record(self) -> ToolRecord:
        return ToolRecord(
            self.tool_name,
            self._title,
            self._detail,
            self._record_preview(),
            self.state,
            [dict(row) for row in self._subtools],
            self._error,
            self._detail_error,
        )

    def _record_preview(self):
        if self._preview_kind == "changes":
            return [dict(row) for row in self._changes]
        if self._preview_kind == "block":
            return BlockPreview(self._metrics, self._block_preview)
        return self._preview
