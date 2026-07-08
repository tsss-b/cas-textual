from dataclasses import dataclass

from textual import events
from textual.widgets import Static

from castcode.format import CWD, tilde_path


SEP = " │ "

_PERMISSION_LABELS = {
    "auto": "auto mode",
    "default": "default mode",
    "acceptEdits": "accept edits",
    "plan": "plan mode",
}


@dataclass(frozen=True)
class StatusData:
    model: str
    context_pct: int | None
    permission_mode: str
    cwd: str = CWD
    cost_usd: float = 0.0


def cost_text(cost: float, *, compact: bool = False) -> str:
    if cost <= 0:
        return "cost —" if compact else "cost: —"
    if cost < 0.001:
        return "$0.00"
    if cost < 0.01:
        return f"${cost:.4f}"
    if cost < 1:
        return f"${cost:.3f}"
    return f"${cost:.2f}"


def context_text(context_pct: int | None, *, compact: bool = False) -> str:
    if context_pct is None:
        return "ctx —" if compact else "context: —"
    return f"{context_pct}%" if compact else f"context: {context_pct}%"


def _tail_path(path: str) -> str:
    path = tilde_path(path)
    if path in ("", "~", "."):
        return path
    parts = path.split("/")
    if len(parts) <= 2:
        return path
    return "…/" + parts[-1]


def _crop(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else text[:width - 1] + "…"


def status_line(data: StatusData, width: int | None = None) -> str:
    cost = cost_text(data.cost_usd)
    compact_cost = cost_text(data.cost_usd, compact=True)
    full = [
        data.model,
        cost,
        context_text(data.context_pct),
        _PERMISSION_LABELS[data.permission_mode],
        tilde_path(data.cwd),
    ]
    medium = [
        data.model,
        compact_cost,
        context_text(data.context_pct, compact=True),
        data.permission_mode,
        _tail_path(data.cwd),
    ]
    narrow = [
        compact_cost,
        context_text(data.context_pct, compact=True),
        data.permission_mode,
    ]

    candidates = (
        _join(full),
        _join(medium),
        _join(narrow),
    )
    if width is None or width <= 0:
        return candidates[0]
    for candidate in candidates:
        if len(candidate) <= width:
            return candidate
    return _crop(narrow[0], width)


def _join(parts: list[str]) -> str:
    return SEP.join(part for part in parts if part)


class StatusLine(Static):
    def __init__(self, data: StatusData, *, id: str | None = None) -> None:
        super().__init__("", id=id, markup=False)
        self.data = data

    def on_mount(self) -> None:
        self.refresh_status()

    def on_resize(self, event: events.Resize) -> None:
        self.refresh_status()

    def update_data(self, data: StatusData) -> None:
        self.data = data
        self.refresh_status()

    def refresh_status(self) -> None:
        width = self.size.width if self.is_mounted else None
        self.update(status_line(self.data, width))
