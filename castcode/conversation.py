from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import claude_agent_sdk as sdk

from textual.widgets import Markdown
from textual.worker import Worker

from castcode.format import format_elapsed
from castcode.tool_render import (
    render_tool_error,
    render_tool_input,
    render_tool_result,
    render_tool_result_detail,
    render_tool_result_soft_error,
)
from castcode.ui.layout import Chat
from castcode.ui.messages import (
    AssistantMessage,
    NoticeMessage,
    ToolMessage,
    UserMessage,
)


_AGENT_TOOLS = {"Agent", "Task"}
_NESTED_DETAIL_MAX = 160


@runtime_checkable
class ConversationHost(Protocol):
    """What the receive-loop flow reaches for off the app. CastcodeApp satisfies
    this structurally (duck-typed); no explicit inheritance. runtime_checkable
    so test_host_protocols can assert CastcodeApp still satisfies it — the contract
    fails loudly if a future rename (e.g. conversation switching moving state
    onto Conversation) drops a member."""

    conversation: Any
    _connect_worker: Worker
    _connect_error: str | None
    _connected_ok: bool
    _can_interrupt: bool
    _interrupting: bool
    _sending: bool

    def query_one(self, selector, expect_type=None): ...
    def _start_activity(self) -> None: ...
    def _stop_activity(self) -> None: ...
    def _stick(self, chat) -> None: ...
    def _update_status_line(self) -> None: ...
    def _snapshot_transcript(self, conv=None) -> None: ...
    def _write_sidecar(self, conv=None) -> None: ...
    def _record_tool_use(self, tool_use_id: str, name: str, tool_input: dict) -> None: ...
    def _record_tool_results(
        self,
        results: list[tuple[str, str, object, dict | None, bool]],
    ) -> None: ...


@dataclass
class SubagentView:
    """Turn-scoped nested-tool state for agent parents, keyed by parent
    tool_use_id: the subtool rows per parent, their reverse map to the parent,
    the per-parent progress fallback line, and the task_id→parent map."""
    subtools: dict = field(default_factory=dict)
    subtool_parent: dict = field(default_factory=dict)
    agent_progress: dict = field(default_factory=dict)
    agent_tasks: dict = field(default_factory=dict)

    def upsert_subtool(self, parent_id: str, block) -> dict:
        display = render_tool_input(block.name, block.input)
        subtool = {
            "id": block.id,
            "name": block.name,
            "title": display.title,
            "detail": display.detail,
            "preview": "",
            "state": "running",
        }
        tools = self.subtools.setdefault(parent_id, [])
        for index, existing in enumerate(tools):
            if existing.get("id") == block.id:
                tools[index] = subtool
                break
        else:
            tools.append(subtool)
        self.subtool_parent[block.id] = parent_id
        return subtool

    def set_progress(self, parent_id: str, row: dict, task_id: str | None = None) -> None:
        if task_id is not None:
            self.agent_tasks[task_id] = parent_id
        self.agent_progress[parent_id] = row

    def parent_for_task(self, task_id: str) -> str | None:
        return self.agent_tasks.get(task_id)

    def rows_for(self, parent_id: str) -> list[dict[str, str]]:
        rows = []
        for subtool in self.subtools.get(parent_id, []):
            rows.append({
                "state": subtool.get("state", "running"),
                "title": subtool.get("title", subtool.get("name", "")),
                "detail": _nested_detail(subtool.get("detail", "")),
            })
        if not rows and parent_id in self.agent_progress:
            rows.append(self.agent_progress[parent_id])
        return rows

    def result_for(self, tool_use_id: str):
        parent_id = self.subtool_parent.get(tool_use_id)
        if not parent_id:
            return None, None
        for subtool in self.subtools.get(parent_id, []):
            if subtool.get("id") == tool_use_id:
                return parent_id, subtool
        return parent_id, None


@dataclass
class TurnState:
    blocks: dict = field(default_factory=dict)
    streams: dict = field(default_factory=dict)
    wrote: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)
    subagents: SubagentView = field(default_factory=SubagentView)
    terminal_error: bool = False
    user_message: UserMessage | None = None
    captured_user_uuid: str | None = None
    final_assistant_ids: set[str] = field(default_factory=set)

    async def stop_streams(self) -> None:
        for stream in self.streams.values():
            await stream.stop()
        self.streams.clear()


async def run_turn(app: ConversationHost, text: str) -> None:
    chat = app.query_one("#chat", Chat)
    session = app.conversation.session
    turn = TurnState()
    completed = False
    # Each turn starts un-interrupted: a prior turn that stopped via a permission
    # deny (can_use_tool sets _interrupting) must not poison this one.
    app._interrupting = False
    app._start_activity()
    try:
        await app._connect_worker.wait()
        turn.user_message = UserMessage(text)
        await chat.mount(turn.user_message)
        app._stick(chat)

        if not app._connected_ok:
            await chat.mount(NoticeMessage(app._connect_error or "Connection failed"))
            app._stick(chat)
        else:
            app.conversation.last_usage = None
            app.conversation.last_model = None
            await session.query(text)
            app._can_interrupt = True
            async for message in session.receive_response():
                await _on_message(app, chat, message, turn)
        completed = True
    finally:
        app._can_interrupt = False
        app._stop_activity()
        try:
            await turn.stop_streams()
            if app._interrupting or turn.terminal_error:
                for msg in turn.tools.values():
                    if msg.state == "running":
                        msg.set_state("error")
            if app._interrupting:
                await chat.mount(NoticeMessage("Interrupted"))
                app._stick(chat)
        finally:
            app._interrupting = False
            app._sending = False
            # Sync-only from here: this also runs when the send worker is
            # cancelled (quit mid-turn) or the receive loop raised, so the
            # saved view keeps the turn instead of trailing the session.
            if not completed:
                for msg in turn.tools.values():
                    if msg.state == "running":
                        msg.set_state("error")
            app._snapshot_transcript()
            app._write_sidecar()


async def _on_message(app, chat, message, turn: TurnState) -> None:
    if isinstance(message, sdk.StreamEvent):
        if message.parent_tool_use_id is None:
            await _on_stream_event(app, chat, message.event, turn)
    elif isinstance(message, sdk.AssistantMessage):
        if message.parent_tool_use_id is None:
            if message.usage:
                app.conversation.last_usage = message.usage
                app.conversation.last_model = message.model
            await _finalize_message(app, chat, message, turn)
        elif _is_agent_parent(turn, message.parent_tool_use_id):
            await _render_subagent_message(app, chat, message, turn)
        else:
            await _render_complete(app, chat, message, turn)
    elif isinstance(message, sdk.UserMessage):
        if message.parent_tool_use_id and _is_agent_parent(turn, message.parent_tool_use_id):
            await _apply_subagent_tool_results(app, chat, message, turn)
        else:
            _capture_checkpoint(app, turn, message)
            await _apply_tool_results(app, chat, message, turn)
    elif isinstance(message, sdk.TaskStartedMessage):
        _apply_task_started(app, chat, turn, message)
    elif isinstance(message, sdk.TaskProgressMessage):
        _apply_task_progress(app, chat, turn, message)
    elif isinstance(message, sdk.TaskNotificationMessage):
        _apply_task_notification(app, chat, turn, message)
    elif isinstance(message, sdk.TaskUpdatedMessage):
        _apply_task_updated(app, chat, turn, message)
    elif isinstance(message, sdk.ResultMessage):
        # Key off is_error, not the subtype prefix: a limit-termination
        # (error_max_turns / error_max_tokens, is_error False) must not paint
        # still-running tools red — only a genuine error or an interrupt does.
        turn.terminal_error = bool(message.is_error)
        if message.session_id:
            app.conversation.session_id = message.session_id
        app.conversation.record_result(message)
        app._update_status_line()


async def _on_stream_event(app, chat, event, turn: TurnState) -> None:
    kind = event["type"]
    if kind == "content_block_start":
        if app._interrupting:
            return
        index = event["index"]
        block = event["content_block"]
        if block["type"] == "text":
            msg = AssistantMessage()
            turn.blocks[index] = msg
            turn.wrote[index] = False
        elif block["type"] == "tool_use":
            msg = ToolMessage(block["name"], tool_name=block["name"])
            turn.blocks[index] = msg
            turn.tools[block["id"]] = msg
        else:
            return
        await chat.mount(msg)
        app._stick(chat)
    elif kind == "content_block_delta":
        index = event["index"]
        delta = event["delta"]
        if delta["type"] == "text_delta" and delta["text"] and not app._interrupting and index in turn.blocks:
            if index not in turn.streams:
                turn.streams[index] = Markdown.get_stream(turn.blocks[index].body)
            if not turn.wrote[index] and delta["text"].strip():
                turn.wrote[index] = True
            turn.blocks[index].append_source(delta["text"])
            await turn.streams[index].write(delta["text"])
            app._stick(chat)
    elif kind == "content_block_stop":
        index = event["index"]
        stream = turn.streams.pop(index, None)
        if stream is not None:
            await stream.stop()
        msg = turn.blocks.get(index)
        if isinstance(msg, AssistantMessage) and not turn.wrote.pop(index, False):
            turn.blocks.pop(index, None)
            await msg.remove()


async def _finalize_message(app, chat, message, turn: TurnState) -> None:
    final_id = message.uuid or message.message_id
    if final_id:
        if final_id in turn.final_assistant_ids:
            return
        turn.final_assistant_ids.add(final_id)
    if message.parent_tool_use_id is None and message.uuid:
        app.conversation.last_assistant_uuid = message.uuid
        app.conversation.last_message_uuid = message.uuid
    streamed_text = [
        msg for msg in turn.blocks.values() if isinstance(msg, AssistantMessage)
    ]
    streamed_index = 0
    final_texts = [
        block.text for block in message.content
        if isinstance(block, sdk.TextBlock)
    ]
    final_text_index = 0
    for block in message.content:
        if isinstance(block, sdk.TextBlock):
            remaining_texts = final_texts[final_text_index + 1:]
            should_render, streamed_index, before = _final_text_action(
                block.text,
                remaining_texts,
                streamed_text,
                streamed_index,
            )
            final_text_index += 1
            if (
                block.text.strip()
                and not app._interrupting
                and should_render
            ):
                msg = AssistantMessage()
                await chat.mount(msg, before=before)
                await msg.update_text(block.text)
                app._stick(chat)
        elif isinstance(block, (sdk.ToolUseBlock, sdk.ServerToolUseBlock)):
            await _render_tool_use(app, chat, turn, block)
        elif isinstance(block, sdk.ServerToolResultBlock):
            await _apply_server_tool_result(app, chat, turn, block)
    turn.blocks.clear()


def _final_text_action(
    text: str,
    remaining_texts: list[str],
    streamed_text: list[AssistantMessage],
    streamed_index: int,
) -> tuple[bool, int, AssistantMessage | None]:
    if not text.strip() or streamed_index >= len(streamed_text):
        return True, streamed_index, None
    source = _message_source(streamed_text[streamed_index])
    if _final_text_matches_stream(text, source):
        return False, streamed_index + 1, None
    if any(_final_text_matches_stream(item, source) for item in remaining_texts):
        return True, streamed_index, streamed_text[streamed_index]
    return False, streamed_index + 1, None


def _final_text_matches_stream(text: str, source: str) -> bool:
    return bool(source.strip()) and text.startswith(source)


def _message_source(message: AssistantMessage) -> str:
    if message.is_mounted:
        return message.body.source
    return getattr(message, "_source", "")


async def _render_complete(app, chat, message, turn: TurnState) -> None:
    if app._interrupting:
        return
    for block in message.content:
        if isinstance(block, sdk.TextBlock) and block.text.strip():
            msg = AssistantMessage()
            await chat.mount(msg)
            await msg.update_text(block.text)
            app._stick(chat)
        elif isinstance(block, (sdk.ToolUseBlock, sdk.ServerToolUseBlock)):
            await _render_tool_use(app, chat, turn, block)
        elif isinstance(block, sdk.ServerToolResultBlock):
            await _apply_server_tool_result(app, chat, turn, block)


def _is_agent_parent(turn: TurnState, tool_use_id: str) -> bool:
    msg = turn.tools.get(tool_use_id)
    return isinstance(msg, ToolMessage) and msg.tool_name in _AGENT_TOOLS


def _capture_checkpoint(app, turn: TurnState, message) -> None:
    if message.parent_tool_use_id is not None:
        return
    if not message.uuid or not isinstance(message.content, str):
        return
    if message.content.startswith("<local-command-"):
        return
    if turn.captured_user_uuid is not None:
        return
    checkpoint = {
        "turn_index": len(app.conversation.checkpoints),
        "user_uuid": message.uuid,
        "keep_uuid": app.conversation.last_message_uuid,
    }
    if app.conversation.last_assistant_uuid is not None:
        checkpoint["assistant_uuid"] = app.conversation.last_assistant_uuid
    app.conversation.checkpoints.append(checkpoint)
    turn.captured_user_uuid = message.uuid
    app.conversation.last_message_uuid = message.uuid
    if turn.user_message is None:
        raise RuntimeError("checkpoint captured before user row mounted")
    turn.user_message.set_uuid(message.uuid)


def _nested_detail(value) -> str:
    if isinstance(value, list):
        return f"{len(value)} changes"
    text = " ".join(str(value).split())
    if len(text) > _NESTED_DETAIL_MAX:
        return text[:_NESTED_DETAIL_MAX - 1] + "…"
    return text


def _sync_agent_rows(app, chat, turn: TurnState, parent_id: str) -> None:
    msg = turn.tools.get(parent_id)
    if isinstance(msg, ToolMessage):
        msg.show_subtools(turn.subagents.rows_for(parent_id))
        app._stick(chat)


async def _render_subagent_message(app, chat, message, turn: TurnState) -> None:
    if app._interrupting:
        return
    parent_id = message.parent_tool_use_id
    for block in message.content:
        if isinstance(block, sdk.TextBlock) and block.text.strip():
            turn.subagents.set_progress(parent_id, {
                "state": "running",
                "title": "message",
                "detail": _nested_detail(block.text),
            })
            _sync_agent_rows(app, chat, turn, parent_id)
        elif isinstance(block, (sdk.ToolUseBlock, sdk.ServerToolUseBlock)):
            turn.subagents.upsert_subtool(parent_id, block)
            _sync_agent_rows(app, chat, turn, parent_id)
        elif isinstance(block, sdk.ToolResultBlock):
            _apply_subtool_result(app, chat, turn, block, message, 1)
        elif isinstance(block, sdk.ServerToolResultBlock):
            _apply_subtool_server_result(app, chat, turn, block)


async def _render_tool_use(app, chat, turn: TurnState, block) -> None:
    app._record_tool_use(block.id, block.name, block.input)
    display = render_tool_input(block.name, block.input)
    placeholder = turn.tools.get(block.id)
    if placeholder is not None:
        placeholder.update_tool(block.name, display.title, display.detail, display.preview)
        app._stick(chat)
    elif not app._interrupting:
        msg = ToolMessage(
            display.title,
            display.detail,
            display.preview,
            tool_name=block.name,
        )
        turn.tools[block.id] = msg
        await chat.mount(msg)
        app._stick(chat)


async def _apply_server_tool_result(app, chat, turn: TurnState, block) -> None:
    msg = turn.tools.get(block.tool_use_id)
    if msg is None:
        return
    msg.set_state("done")
    msg.show_result(render_tool_result(msg.tool_name, block.content))
    app._stick(chat)


def _usage_summary(usage: dict | None) -> str:
    if not isinstance(usage, dict):
        return ""
    parts = []
    tool_uses = usage.get("tool_uses")
    if isinstance(tool_uses, int):
        parts.append(f"{tool_uses} tool{'' if tool_uses == 1 else 's'}")
    tokens = usage.get("total_tokens")
    if isinstance(tokens, int):
        parts.append(f"{tokens:,} tokens")
    duration = usage.get("duration_ms")
    if isinstance(duration, int):
        parts.append(format_elapsed(duration // 1000))
    return " · ".join(parts)


def _apply_task_started(app, chat, turn: TurnState, message) -> None:
    if message.task_type != "local_agent" or not message.tool_use_id:
        return
    if not _is_agent_parent(turn, message.tool_use_id):
        return
    turn.subagents.set_progress(message.tool_use_id, {
        "state": "running",
        "title": "starting",
        "detail": _nested_detail(message.description),
    }, task_id=message.task_id)
    _sync_agent_rows(app, chat, turn, message.tool_use_id)


def _apply_task_progress(app, chat, turn: TurnState, message) -> None:
    if not message.tool_use_id or not _is_agent_parent(turn, message.tool_use_id):
        return
    parts = [message.description]
    usage = _usage_summary(message.usage)
    if usage:
        parts.append(usage)
    turn.subagents.set_progress(message.tool_use_id, {
        "state": "running",
        "title": message.last_tool_name or "working",
        "detail": _nested_detail(" · ".join(part for part in parts if part)),
    }, task_id=message.task_id)
    _sync_agent_rows(app, chat, turn, message.tool_use_id)


def _apply_task_notification(app, chat, turn: TurnState, message) -> None:
    if not message.tool_use_id or not _is_agent_parent(turn, message.tool_use_id):
        return
    state = "done" if message.status == "completed" else "error"
    parts = [message.summary]
    usage = _usage_summary(message.usage)
    if usage:
        parts.append(usage)
    turn.subagents.set_progress(message.tool_use_id, {
        "state": state,
        "title": message.status,
        "detail": _nested_detail(" · ".join(part for part in parts if part)),
    }, task_id=message.task_id)
    _sync_agent_rows(app, chat, turn, message.tool_use_id)


def _apply_task_updated(app, chat, turn: TurnState, message) -> None:
    tool_use_id = turn.subagents.parent_for_task(message.task_id) or message.data.get("tool_use_id")
    if not tool_use_id or not _is_agent_parent(turn, tool_use_id):
        return
    status = message.status or message.patch.get("status")
    if status not in ("failed", "killed"):
        return
    detail = message.patch.get("error") or message.patch.get("description", "")
    turn.subagents.set_progress(tool_use_id, {
        "state": "error",
        "title": status,
        "detail": _nested_detail(detail),
    })
    _sync_agent_rows(app, chat, turn, tool_use_id)


def _apply_subtool_result(
    app, chat, turn: TurnState, block, message, result_count: int
) -> None:
    parent_id, subtool = turn.subagents.result_for(block.tool_use_id)
    if subtool is None:
        return
    if block.is_error:
        subtool["state"] = "error"
        subtool["preview"] = render_tool_error(block.content)
    else:
        subtool["state"] = "done"
        result = _message_tool_use_result(message, block.tool_use_id, result_count)
        subtool["preview"] = render_tool_result(subtool["name"], block.content, result)
    _sync_agent_rows(app, chat, turn, parent_id)


def _apply_subtool_server_result(app, chat, turn: TurnState, block) -> None:
    parent_id, subtool = turn.subagents.result_for(block.tool_use_id)
    if subtool is None:
        return
    subtool["state"] = "done"
    subtool["preview"] = render_tool_result(subtool["name"], block.content)
    _sync_agent_rows(app, chat, turn, parent_id)


def _message_tool_use_result(message, tool_use_id: str, result_count: int) -> dict | None:
    result = getattr(message, "tool_use_result", None)
    if not isinstance(result, dict):
        return None
    keyed = result.get(tool_use_id)
    if isinstance(keyed, dict):
        return keyed
    for key in ("tool_use_results", "results"):
        values = result.get(key)
        if isinstance(values, dict) and isinstance(values.get(tool_use_id), dict):
            return values[tool_use_id]
    if result_count == 1:
        return result
    return None


async def _apply_tool_results(app, chat, message, turn: TurnState) -> None:
    content = message.content
    if not isinstance(content, list):
        return
    result_count = sum(isinstance(block, sdk.ToolResultBlock) for block in content)
    results = {}
    todo_results = []
    for block in content:
        if isinstance(block, sdk.ToolResultBlock):
            msg = turn.tools.get(block.tool_use_id)
            if msg is None:
                continue
            result = _message_tool_use_result(message, block.tool_use_id, result_count)
            results[id(block)] = (msg, result)
            todo_results.append((
                block.tool_use_id,
                msg.tool_name,
                block.content,
                result,
                bool(block.is_error),
            ))
    app._record_tool_results(todo_results)
    for block in content:
        if isinstance(block, sdk.ToolResultBlock):
            values = results.get(id(block))
            if values is None:
                continue
            msg, result = values
            if block.is_error:
                msg.set_state("error")
                msg.show_error(render_tool_error(block.content))
            else:
                detail = render_tool_result_detail(msg.tool_name, block.content, result)
                if detail:
                    msg.update_tool(msg.tool_name, msg.tool_name, detail)
                msg.show_result(render_tool_result(msg.tool_name, block.content, result))
                soft_error = render_tool_result_soft_error(
                    msg.tool_name,
                    block.content,
                    result,
                )
                msg.set_state("error" if soft_error else "done")
                msg.set_detail_error(soft_error)
            if msg.tool_name in _AGENT_TOOLS:
                msg.show_subtools([])
            app._stick(chat)
        elif isinstance(block, sdk.ServerToolResultBlock):
            await _apply_server_tool_result(app, chat, turn, block)


async def _apply_subagent_tool_results(app, chat, message, turn: TurnState) -> None:
    content = message.content
    if not isinstance(content, list):
        return
    result_count = sum(isinstance(block, sdk.ToolResultBlock) for block in content)
    for block in content:
        if isinstance(block, sdk.ToolResultBlock):
            _apply_subtool_result(app, chat, turn, block, message, result_count)
        elif isinstance(block, sdk.ServerToolResultBlock):
            _apply_subtool_server_result(app, chat, turn, block)
