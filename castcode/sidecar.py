import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from castcode.records import (
    AssistantRecord,
    BlockPreview,
    NoticeRecord,
    Record,
    ToolRecord,
    UserRecord,
)


@dataclass
class SidecarEnvelope:
    version: int
    token: int | None
    records: list[Record]
    checkpoints: list[dict] = field(default_factory=list)
    last_assistant_uuid: str | None = None
    last_message_uuid: str | None = None
    model_id: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    draft: str = ""


def dump_records(
    records,
    token,
    checkpoints=None,
    last_assistant_uuid=None,
    last_message_uuid=None,
    model_id=None,
    model=None,
    permission_mode=None,
    draft=None,
) -> str:
    checkpoints = list(checkpoints or [])
    data = {
        "version": 2 if checkpoints else 1,
        "token": token,
        "records": [_dump_record(record) for record in records],
        "last_assistant_uuid": last_assistant_uuid,
        "last_message_uuid": last_message_uuid,
        "model_id": model_id,
        "model": model,
        "permission_mode": permission_mode,
        "draft": draft or "",
    }
    if checkpoints:
        data["checkpoints"] = checkpoints
    return json.dumps(data, indent=2, sort_keys=True)


def load_records(text: str) -> SidecarEnvelope:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("sidecar root must be an object")
    version = data.get("version")
    if version not in (1, 2):
        raise ValueError(f"unknown sidecar version: {version!r}")
    records = data.get("records")
    if not isinstance(records, list):
        raise ValueError("sidecar records must be a list")
    checkpoints = data.get("checkpoints", [])
    if checkpoints is None:
        checkpoints = []
    if not isinstance(checkpoints, list):
        raise ValueError("sidecar checkpoints must be a list")
    checkpoints = [_load_checkpoint(checkpoint) for checkpoint in checkpoints]
    permission_mode = data.get("permission_mode")
    if permission_mode is not None and not isinstance(permission_mode, str):
        raise ValueError("sidecar permission_mode must be a string or null")
    return SidecarEnvelope(
        version=version,
        token=data.get("token"),
        records=[_load_record(record) for record in records],
        checkpoints=checkpoints,
        last_assistant_uuid=data.get("last_assistant_uuid"),
        last_message_uuid=data.get("last_message_uuid"),
        model_id=data.get("model_id"),
        model=data.get("model"),
        permission_mode=permission_mode,
        draft=str(data.get("draft") or ""),
    )


def write_sidecar(
    path,
    token,
    records,
    checkpoints,
    last_assistant_uuid,
    last_message_uuid,
    model_id,
    model,
    permission_mode=None,
    draft=None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dump_records(
        records,
        token,
        checkpoints,
        last_assistant_uuid,
        last_message_uuid,
        model_id,
        model,
        permission_mode,
        draft,
    )
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def read_sidecar(path) -> SidecarEnvelope:
    return load_records(Path(path).read_text(encoding="utf-8"))


def _dump_record(record):
    if isinstance(record, UserRecord):
        return {"__type__": "user", "text": record.text, "uuid": record.uuid}
    if isinstance(record, AssistantRecord):
        return {"__type__": "assistant", "text": record.text}
    if isinstance(record, NoticeRecord):
        return {"__type__": "notice", "text": record.text}
    if isinstance(record, ToolRecord):
        return {
            "__type__": "tool",
            "tool_name": record.tool_name,
            "title": record.title,
            "detail": record.detail,
            "preview": _dump_preview(record.preview),
            "state": record.state,
            "subtools": _dump_json_native(record.subtools),
            "error": record.error,
            "detail_error": record.detail_error,
        }
    raise TypeError(f"unsupported record: {record!r}")


def _load_checkpoint(data):
    if not isinstance(data, dict):
        raise ValueError("checkpoint must be an object")
    turn_index = data.get("turn_index")
    user_uuid = data.get("user_uuid")
    keep_uuid = data.get("keep_uuid")
    if not isinstance(turn_index, int):
        raise ValueError("checkpoint turn_index must be an int")
    if not isinstance(user_uuid, str) or not user_uuid:
        raise ValueError("checkpoint user_uuid must be a non-empty string")
    if keep_uuid is not None and not isinstance(keep_uuid, str):
        raise ValueError("checkpoint keep_uuid must be a string or null")
    checkpoint = {
        "turn_index": turn_index,
        "user_uuid": user_uuid,
        "keep_uuid": keep_uuid,
    }
    if "assistant_uuid" in data:
        assistant_uuid = data.get("assistant_uuid")
        if assistant_uuid is not None and not isinstance(assistant_uuid, str):
            raise ValueError("checkpoint assistant_uuid must be a string or null")
        checkpoint["assistant_uuid"] = assistant_uuid
    return checkpoint


def _load_record(data):
    if not isinstance(data, dict):
        raise ValueError("record must be an object")
    kind = data.get("__type__")
    if kind == "user":
        return UserRecord(str(data.get("text", "")), data.get("uuid"))
    if kind == "assistant":
        return AssistantRecord(str(data.get("text", "")))
    if kind == "notice":
        return NoticeRecord(str(data.get("text", "")))
    if kind == "tool":
        return ToolRecord(
            tool_name=str(data.get("tool_name", "")),
            title=str(data.get("title", "")),
            detail=str(data.get("detail", "")),
            preview=_load_preview(data.get("preview", "")),
            state=str(data.get("state", "running")),
            subtools=list(data.get("subtools") or []),
            error=str(data.get("error", "")),
            detail_error=bool(data.get("detail_error", False)),
        )
    raise ValueError(f"unknown record type: {kind!r}")


def _dump_preview(preview):
    if isinstance(preview, str):
        return preview
    if isinstance(preview, list):
        return {"__preview__": "changes", "rows": preview}
    if isinstance(preview, BlockPreview) or (
        hasattr(preview, "metrics") and hasattr(preview, "text")
    ):
        return {
            "__preview__": "block",
            "metrics": str(preview.metrics),
            "text": str(preview.text),
        }
    return str(preview)


def _dump_json_native(value):
    return json.loads(json.dumps(value))


def _load_preview(data):
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return str(data)
    kind = data.get("__preview__")
    if kind == "changes":
        rows = data.get("rows", [])
        if not isinstance(rows, list):
            raise ValueError("change preview rows must be a list")
        return rows
    if kind == "block":
        return BlockPreview(str(data.get("metrics", "")), str(data.get("text", "")))
    raise ValueError(f"unknown preview tag: {kind!r}")
