from collections import namedtuple
from dataclasses import dataclass, field


BlockPreview = namedtuple("BlockPreview", "metrics text")


@dataclass
class UserRecord:
    text: str
    uuid: str | None = None


@dataclass
class AssistantRecord:
    text: str


@dataclass
class NoticeRecord:
    text: str


@dataclass
class ToolRecord:
    tool_name: str
    title: str
    detail: str = ""
    preview: object = ""
    state: str = "running"
    subtools: list = field(default_factory=list)
    error: str = ""
    detail_error: bool = False


Record = UserRecord | AssistantRecord | NoticeRecord | ToolRecord
