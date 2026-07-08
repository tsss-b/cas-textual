import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import claude_agent_sdk as sdk
from claude_agent_sdk._internal.session_mutations import _find_session_file_with_dir

from castcode.commands import CommandRow, normalize_name
from castcode.format import CWD, model_label


_SYSTEM_APPEND = (
    "You are running inside castcode, a Textual TUI that wraps "
    "the Claude Agent SDK. Its code lives in the castcode/ package "
    "(castcode/app.py, castcode/format.py, …) with styles in castcode/app.tcss and a "
    "thin root app.py shim. The user is talking to you through that "
    "terminal UI, so you can edit and test the TUI from within itself."
)


# The Python SDK needs a PreToolUse hook returning {"continue_": True} to keep
# the stream open while can_use_tool waits on a (possibly long) human prompt;
# "continue_" tells Claude to proceed after the hook. See HANDOFF.md.
async def _permission_keepalive_hook(input_data, tool_use_id, context):
    return {"continue_": True}


@dataclass(frozen=True)
class ModelRow:
    model_id: str
    title: str
    detail: str = ""


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    title: str
    subtitle: str = ""
    last_modified: int | None = None
    cwd: str | None = None
    git_branch: str | None = None


@dataclass(frozen=True)
class ForkResult:
    session_id: str
    uuid_map: dict[str, str]


def build_options(
    permission_mode,
    can_use_tool,
    *,
    resume=None,
    model=None,
):
    return sdk.ClaudeAgentOptions(
        cwd=CWD,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": _SYSTEM_APPEND,
        },
        permission_mode=permission_mode,
        can_use_tool=can_use_tool,
        hooks={
            "PreToolUse": [
                sdk.HookMatcher(matcher=None, hooks=[_permission_keepalive_hook])
            ]
        },
        include_partial_messages=True,
        resume=resume,
        model=model,
        extra_args={"replay-user-messages": None},
    )


class Session:
    def __init__(self):
        self.client: sdk.ClaudeSDKClient | None = None
        self.server_info = None
        self.commands: list[CommandRow] = []

    async def connect(self, options, desired_mode: Callable[[], str]) -> str | None:
        self.client = sdk.ClaudeSDKClient(options)
        await self.client.connect()
        sdk_mode = options.permission_mode
        while desired_mode() != sdk_mode:
            sdk_mode = desired_mode()
            await self.client.set_permission_mode(sdk_mode)
        self.server_info = await self.get_server_info()
        self.commands = command_rows_from_info(self.server_info)
        if not self.server_info:
            return None
        if options.model:
            return _label_for_model(self.server_info, options.model)
        return model_label(self.server_info)

    async def query(self, text: str) -> None:
        await self.client.query(text)

    def receive_response(self):
        return self.client.receive_response()

    async def interrupt(self) -> None:
        await self.client.interrupt()

    async def set_permission_mode(self, mode: str) -> None:
        await self.client.set_permission_mode(mode)

    async def set_model(self, model: str) -> str | None:
        await self.client.set_model(model)
        self.server_info = await self.get_server_info()
        return _label_for_model(self.server_info, model)

    async def get_server_info(self):
        return await self.client.get_server_info()

    async def model_rows(self) -> list[ModelRow]:
        self.server_info = await self.get_server_info()
        return model_rows_from_info(self.server_info)

    async def context_usage(self) -> str:
        return _format_context_usage(await self.client.get_context_usage())

    async def disconnect(self) -> None:
        if self.client is not None:
            await self.client.disconnect()

    def sidecar_path(self, session_id: str) -> Path:
        return _claude_config_dir() / "castcode-sidecars" / _project_key() / f"{session_id}.json"

    def session_last_modified(self, session_id: str) -> int | None:
        info = sdk.get_session_info(session_id, directory=CWD)
        return info.last_modified if info is not None else None

    def list_sessions(self):
        return sdk.list_sessions(directory=CWD, include_worktrees=False)

    def session_drifted(self, session_id: str, anchor_uuid: str | None) -> bool:
        # Drift means the session gained assistant messages castcode never saw
        # (someone continued it outside). mtime cannot express this: the CLI
        # appends housekeeping entries (last-prompt, ai-title) and flushes the
        # turn's own assistant entry after the sidecar token is captured.
        if anchor_uuid is None:
            return False
        result = _find_session_file_with_dir(session_id, CWD)
        if result is None:
            return False
        path, _ = result
        assistant_after_anchor = False
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("uuid") == anchor_uuid:
                return assistant_after_anchor
            if entry.get("type") == "assistant" and entry.get("uuid"):
                assistant_after_anchor = True
        return False

    def session_row(self, info) -> SessionRow:
        title = getattr(info, "summary", "") or getattr(info, "first_prompt", "") or info.session_id
        parts = []
        branch = getattr(info, "git_branch", None)
        cwd = getattr(info, "cwd", None)
        tag = getattr(info, "tag", None)
        if branch:
            parts.append(branch)
        if tag:
            parts.append(f"#{tag}")
        if cwd and cwd != CWD:
            parts.append(cwd)
        return SessionRow(
            session_id=info.session_id,
            title=title,
            subtitle=" · ".join(parts),
            last_modified=getattr(info, "last_modified", None),
            cwd=cwd,
            git_branch=branch,
        )

    def fork(self, session_id, up_to_message_id):
        return sdk.fork_session(
            session_id,
            directory=CWD,
            up_to_message_id=up_to_message_id,
        ).session_id

    def fork_result(self, session_id, up_to_message_id) -> ForkResult:
        forked_session_id = self.fork(session_id, up_to_message_id)
        return ForkResult(
            forked_session_id,
            _fork_uuid_map(session_id, forked_session_id),
        )


def _model_row(model, index: int) -> ModelRow:
    if not isinstance(model, dict):
        text = str(model)
        return ModelRow(text, text)
    model_id = str(
        model.get("id")
        or model.get("value")
        or model.get("model")
        or model.get("name")
        or model.get("displayName")
        or f"model-{index}"
    )
    title = (
        str(model.get("description", "")).split("·")[0].strip()
        or model.get("displayName")
        or model_id
    )
    detail = str(model.get("description", "")).strip()
    if detail == title:
        detail = ""
    return ModelRow(model_id, str(title), detail)


def model_rows_from_info(info) -> list[ModelRow]:
    models = info.get("models") if isinstance(info, dict) else []
    return [_model_row(model, index) for index, model in enumerate(models or [])]


def _label_for_model(info, model_id: str) -> str | None:
    if not isinstance(info, dict):
        return None
    for model in info.get("models") or []:
        row = _model_row(model, 0)
        if row.model_id == model_id:
            return row.title
    return None


def command_rows_from_info(info) -> list[CommandRow]:
    return command_rows(_command_payload(info), skills=_skill_names(info))


def command_rows(commands, *, skills=()) -> list[CommandRow]:
    rows = []
    skill_names = {normalize_name(name) for name in skills}
    for item in _command_items(commands):
        row = _command_row(item, skill_names)
        if row.name:
            rows.append(row)
    return rows


def _command_payload(info):
    if not isinstance(info, dict):
        return []
    for key in ("slash_commands", "slashCommands", "commands"):
        if key in info:
            return info[key]
    return []


def _command_items(commands):
    if commands is None:
        return []
    if isinstance(commands, dict):
        if "slash_commands" in commands or "slashCommands" in commands:
            return _command_items(_command_payload(commands))
        return [
            {"name": name, **(value if isinstance(value, dict) else {})}
            for name, value in commands.items()
        ]
    if isinstance(commands, (list, tuple)):
        return list(commands)
    return [commands]


def _command_row(item, skill_names=()) -> CommandRow:
    if isinstance(item, CommandRow):
        return item
    if isinstance(item, str):
        name = normalize_name(item)
        return CommandRow(name, "", _command_source(name, skill_names))
    if isinstance(item, dict):
        name = item.get("name") or item.get("command") or item.get("id") or ""
        description = item.get("description") or item.get("summary") or ""
        source = item.get("source") or item.get("type") or ""
        name = normalize_name(name)
        return CommandRow(name, str(description), _command_source(name, skill_names, source))
    name = getattr(item, "name", "") or getattr(item, "command", "")
    description = getattr(item, "description", "")
    source = getattr(item, "source", "") or getattr(item, "type", "")
    name = normalize_name(name)
    return CommandRow(name, str(description), _command_source(name, skill_names, source))


def _skill_names(info) -> set[str]:
    if not isinstance(info, dict):
        return set()
    skills = info.get("skills") or ()
    if isinstance(skills, dict):
        return {normalize_name(name) for name in skills}
    if isinstance(skills, str):
        return {normalize_name(skills)}
    if isinstance(skills, (list, tuple, set)):
        return {normalize_name(item) for item in skills}
    return set()


def _command_source(name: str, skill_names, source: str = "") -> str:
    source = str(source or "").lower()
    if source in ("local", "plugin", "skill"):
        return source
    if name in skill_names:
        return "plugin" if ":" in name else "skill"
    return "plugin" if source == "plugin-command" or ":" in name else "sdk"


def _format_context_usage(usage) -> str:
    if isinstance(usage, str):
        return usage
    if not isinstance(usage, dict):
        return str(usage)
    percentage = usage.get("percentage")
    total = usage.get("totalTokens")
    maximum = usage.get("maxTokens") or usage.get("rawMaxTokens")
    model = usage.get("model")
    parts = []
    if isinstance(percentage, (int, float)):
        parts.append(f"{percentage:.1f}% context used")
    if isinstance(total, int) and isinstance(maximum, int) and maximum:
        parts.append(f"{total:,} / {maximum:,} tokens")
    elif isinstance(total, int):
        parts.append(f"{total:,} tokens")
    if model:
        parts.append(str(model))
    lines = [" · ".join(parts) if parts else "context usage unavailable"]
    for category in usage.get("categories") or []:
        if not isinstance(category, dict):
            continue
        tokens = category.get("tokens")
        name = category.get("name")
        if isinstance(tokens, int) and name:
            lines.append(f"{name}: {tokens:,} tokens")
    return "\n".join(lines)


def _claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def _project_key() -> str:
    return sdk.project_key_for_directory(CWD)


def _fork_uuid_map(source_session_id: str, forked_session_id: str) -> dict[str, str]:
    result = _find_session_file_with_dir(forked_session_id, CWD)
    if result is None:
        raise FileNotFoundError(f"Session {forked_session_id} not found")
    path, _ = result
    uuid_map = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        forked_from = entry.get("forkedFrom")
        if not isinstance(forked_from, dict):
            continue
        if forked_from.get("sessionId") != source_session_id:
            continue
        old_uuid = forked_from.get("messageUuid")
        new_uuid = entry.get("uuid")
        if isinstance(old_uuid, str) and isinstance(new_uuid, str):
            uuid_map[old_uuid] = new_uuid
    return uuid_map
