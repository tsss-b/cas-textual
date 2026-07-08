from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    names: tuple[str, ...]
    description: str
    handler: str
    exclusive: bool = False


@dataclass(frozen=True)
class CommandRoute:
    command: str
    args: str
    spec: CommandSpec | None = None
    passthrough: bool = False


@dataclass(frozen=True)
class CommandRow:
    name: str
    description: str
    source: str
    handler: str = ""
    exclusive: bool = False


LOCAL_COMMANDS = (
    CommandSpec(("/switch", "/resume", "/sessions"), "Resume a conversation", "switcher"),
    CommandSpec(("/new", "/clear"), "Start a new conversation", "new", True),
    CommandSpec(("/model",), "Pick a model", "model"),
    CommandSpec(("/context",), "Show live context usage", "context"),
    CommandSpec(("/rewind", "/checkpoint"), "Rewind to a checkpoint", "rewind"),
    CommandSpec(("/fork",), "Branch this conversation", "fork", True),
    CommandSpec(("/help",), "Show local commands", "help"),
)

_BY_NAME = {name: spec for spec in LOCAL_COMMANDS for name in spec.names}


def normalize_name(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        return ""
    return name if name.startswith("/") else f"/{name}"


def route(text: str) -> CommandRoute | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split(maxsplit=1)
    command = normalize_name(parts[0])
    args = parts[1].strip() if len(parts) > 1 else ""
    spec = _BY_NAME.get(command)
    if spec is None:
        return CommandRoute(command, args, passthrough=True)
    return CommandRoute(command, args, spec)


def spec_for(name: str) -> CommandSpec | None:
    return _BY_NAME.get(normalize_name(name))


def local_rows() -> list[CommandRow]:
    return [
        CommandRow(name, spec.description, "local", spec.handler, spec.exclusive)
        for spec in LOCAL_COMMANDS
        for name in spec.names
    ]


def help_text() -> str:
    lines = ["Local commands:"]
    for spec in LOCAL_COMMANDS:
        names = ", ".join(spec.names)
        lines.append(f"{names} - {spec.description}")
    lines.append("")
    lines.append("Keys:")
    lines.append("ctrl+r - Resume a conversation")
    lines.append("shift+tab - Cycle permission mode")
    lines.append("escape - Cancel, interrupt, or arm rewind")
    return "\n".join(lines)


def popup_rows(sdk_commands=(), prefix: str = "/") -> list[CommandRow]:
    query = normalize_name(prefix).lower()
    rows = _filter_rows(local_rows(), query)
    local_names = {name for spec in LOCAL_COMMANDS for name in spec.names}
    sdk_rows = []
    skill_rows = []
    plugin_rows = []
    for item in sdk_commands or ():
        row = _sdk_row(item)
        if row.name in local_names:
            continue
        source = _popup_source(row)
        if source != row.source:
            row = CommandRow(
                row.name, row.description, source, row.handler, row.exclusive
            )
        if source == "plugin":
            plugin_rows.append(row)
        elif source == "skill":
            skill_rows.append(row)
        else:
            sdk_rows.append(row)
    rows.extend(_filter_rows(sdk_rows, query))
    rows.extend(_filter_rows(skill_rows, query))
    rows.extend(_filter_rows(plugin_rows, query))
    return rows


def _filter_rows(rows, query: str) -> list[CommandRow]:
    if query in ("", "/"):
        return list(rows)
    return [row for row in rows if row.name.lower().startswith(query)]


def _sdk_row(item) -> CommandRow:
    if isinstance(item, CommandRow):
        return item
    if isinstance(item, dict):
        name = item.get("name") or item.get("command") or item.get("id") or ""
        description = item.get("description") or item.get("summary") or ""
    else:
        name = getattr(item, "name", "") or getattr(item, "command", "")
        description = getattr(item, "description", "")
    return CommandRow(normalize_name(name), str(description), "sdk")


def _popup_source(row: CommandRow) -> str:
    if row.source == "plugin" or ":" in row.name:
        return "plugin"
    if row.source == "skill":
        return "skill"
    return "sdk"
