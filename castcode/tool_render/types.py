from dataclasses import dataclass


@dataclass(frozen=True)
class BlockPreview:
    metrics: str
    text: str


ToolPreview = str | list[dict[str, str]] | BlockPreview


@dataclass(frozen=True)
class ToolDisplay:
    title: str
    detail: str = ""
    preview: ToolPreview = ""
