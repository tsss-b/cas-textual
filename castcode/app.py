import asyncio
from copy import deepcopy
from json import JSONDecodeError
import time
from itertools import cycle
from pathlib import Path

import claude_agent_sdk as sdk

from rich.text import Text
from textual import work
from textual.actions import SkipAction
from textual.app import App
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import Static
from textual.worker import WorkerCancelled

from castcode import permissions
from castcode.commands import help_text, popup_rows, route as route_command
from castcode.conversation import run_turn
from castcode.format import SPINNER, format_elapsed
from castcode.interaction import (
    PERMISSION_MODES,
    InteractionState,
    can_interrupt,
    can_open_rewind_from_esc,
    can_cycle_permission,
    can_start_local_command,
    can_submit,
    full_idle,
    others_idle,
)
from castcode.records import NoticeRecord, ToolRecord, UserRecord
from castcode.rewind import apply_rewind_uuid_map, build_branch_plan, plan_rewind
from castcode.session import Session, build_options
from castcode.switching import resume_cold, switch_to
from castcode.sidecar import read_sidecar, write_sidecar
from castcode.state import Conversation, TodoState
from castcode.ui.input import CommandPopup, Prompt
from castcode.ui.layout import Chat
from castcode.ui.messages import NoticeMessage, from_record
from castcode.ui.pickers import ModelPicker, RewindPicker, Switcher
from castcode.ui.screens import ChatScreen
from castcode.ui.status import StatusData, StatusLine
from castcode.ui.todos import todo_text


_INTERRUPT_TIMEOUT = 5


class CastcodeApp(App):
    CSS_PATH = Path(__file__).resolve().parent / "app.tcss"

    BINDINGS = [
        Binding("escape", "interrupt", "Interrupt", show=False, priority=True),
        Binding("ctrl+r", "switcher", "Resume", show=False, priority=True),
        Binding(
            "shift+tab", "cycle_permission_mode", "Permission Mode",
            show=False, priority=True,
        ),
    ]

    _activity_timer = None
    _stick_pending = False

    def __init__(self) -> None:
        super().__init__()
        self.interaction = InteractionState()
        self.conversation = Conversation(session=Session())
        self._active_picker = None
        self._rewind_timer = None

    def get_default_screen(self) -> ChatScreen:
        return ChatScreen()

    def on_mount(self) -> None:
        self._permission_lock = asyncio.Lock()
        self._connect_worker = self.connect()
        self.call_after_refresh(self._refresh_prompt_commands)

    @property
    def _connect_worker(self):
        return self.conversation.connect_worker

    @_connect_worker.setter
    def _connect_worker(self, value) -> None:
        self.conversation.connect_worker = value

    @property
    def _connect_error(self) -> str | None:
        return self.conversation.connect_error

    @_connect_error.setter
    def _connect_error(self, value: str | None) -> None:
        self.conversation.connect_error = value

    @property
    def _connected_ok(self) -> bool:
        return self.conversation.connected_ok

    @_connected_ok.setter
    def _connected_ok(self, value: bool) -> None:
        self.conversation.connected_ok = value

    @property
    def _sending(self) -> bool:
        return self.interaction.sending

    @_sending.setter
    def _sending(self, value: bool) -> None:
        self.interaction.sending = value

    @property
    def _interrupting(self) -> bool:
        return self.interaction.interrupting

    @_interrupting.setter
    def _interrupting(self, value: bool) -> None:
        self.interaction.interrupting = value

    @property
    def _can_interrupt(self) -> bool:
        return self.interaction.interruptible

    @_can_interrupt.setter
    def _can_interrupt(self, value: bool) -> None:
        self.interaction.interruptible = value

    @property
    def _permission_prompt(self):
        return self.interaction.permission_prompt

    @_permission_prompt.setter
    def _permission_prompt(self, value) -> None:
        self.interaction.permission_prompt = value

    @property
    def _activity(self) -> Static:
        return self.query_one("#activity", Static)

    @property
    def _todos(self) -> Static:
        return self.query_one("#todos", Static)

    @work
    async def connect(self) -> None:
        conv = self.conversation
        session = conv.session
        options = build_options(
            conv.permission_mode,
            self._can_use_tool,
            resume=conv.session_id,
            model=conv.model_id,
        )
        try:
            label = await session.connect(options, lambda: conv.permission_mode)
        except Exception as error:
            detail = str(error) or type(error).__name__
            conv.connect_error = f"Connection failed: {detail}"
            if conv is self.conversation:
                conv.transcript.append(NoticeRecord(conv.connect_error))
                self.call_after_refresh(self._show_connect_error, conv)
            return
        conv.connected_ok = True
        conv.connect_error = None
        conv.commands = list(session.commands)
        if label:
            conv.model = label
        if conv is self.conversation:
            self.call_after_refresh(self._update_status_line)
            self.call_after_refresh(self._refresh_prompt_commands)

    def _show_connect_error(self, conv=None) -> None:
        conv = conv or self.conversation
        if conv is not self.conversation:
            return
        chat = self.query_one("#chat", Chat)
        chat.mount(NoticeMessage(conv.connect_error))
        self._stick(chat)

    @work
    async def send(self, text: str) -> None:
        await run_turn(self, text)

    @work
    async def local_command(self, command: str, handler: str, exclusive: bool) -> None:
        try:
            if handler == "help":
                await self._echo_command(command, help_text())
            elif handler == "new":
                await self._new_conversation(command)
            elif handler == "context":
                await self._context_command(command)
            elif handler == "fork":
                await self._fork_conversation(command)
            elif handler == "rewind":
                await self._open_rewind_picker()
            elif handler == "model":
                await self._model_command()
            elif handler == "switcher":
                await self._open_switcher()
        except Exception as error:
            await self._notice(str(error) or type(error).__name__)
        finally:
            self.interaction.local_command_pending = False
            if exclusive:
                self.interaction.transaction = None

    @work
    async def rewind_checkpoint(self, checkpoint: dict, operation: str) -> None:
        try:
            await self._execute_rewind(checkpoint, operation)
        except Exception as error:
            await self._notice(str(error) or type(error).__name__)
        finally:
            self.interaction.transaction = None

    async def action_interrupt(self) -> None:
        if self.interaction.picker_open:
            await self._close_bottom_takeover()
            return
        if self._permission_prompt is not None:
            self._permission_prompt.cancel()
            return
        if not can_interrupt(self.interaction):
            prompt_empty = not self.query_one(Prompt).text.strip()
            if can_open_rewind_from_esc(self.interaction, prompt_empty):
                if self.interaction.rewind_armed:
                    await self._open_rewind_picker()
                else:
                    self._arm_rewind()
                return
            self._disarm_rewind()
            raise SkipAction()
        self._interrupting = True
        try:
            await asyncio.wait_for(
                self.conversation.session.interrupt(), timeout=_INTERRUPT_TIMEOUT
            )
        except Exception:
            self._interrupting = False
            raise

    def on_prompt_submitted(self, event: Prompt.Submitted) -> None:
        routed = route_command(event.text)
        if routed is not None and not routed.passthrough:
            if not can_start_local_command(self.interaction):
                return
            self._disarm_rewind()
            self._close_command_popup()
            self.query_one(Prompt).clear()
            self.interaction.local_command_pending = True
            exclusive = bool(routed.spec and routed.spec.exclusive)
            if exclusive:
                self.interaction.transaction = routed.command
            self.local_command(routed.command, routed.spec.handler, exclusive)
            return
        if not can_submit(self.interaction):
            return
        self._sending = True
        self._disarm_rewind()
        self._close_command_popup()
        self.query_one(Prompt).clear()
        self.send(event.text)

    async def on_prompt_command_popup_changed(
        self, event: Prompt.CommandPopupChanged
    ) -> None:
        self.query_one(CommandPopup).update_rows(event.rows, event.index)

    async def on_prompt_command_popup_closed(
        self, event: Prompt.CommandPopupClosed
    ) -> None:
        self._close_command_popup()

    def _refresh_prompt_commands(self) -> None:
        if not self.is_mounted:
            return
        rows = [
            {
                "name": row.name,
                "description": row.description,
                "source": row.source,
            }
            for row in popup_rows(self.conversation.commands)
        ]
        self.query_one(Prompt).set_command_rows(rows)

    def _close_command_popup(self) -> None:
        if self.is_mounted:
            self.query_one(Prompt).close_command_popup()
            self.query_one(CommandPopup).close()

    async def _append_record(self, record) -> None:
        self.conversation.transcript.append(record)
        chat = self.query_one("#chat", Chat)
        await chat.mount(from_record(record))
        self._stick(chat)

    async def _echo_command(self, command: str, result: str) -> None:
        await self._append_record(ToolRecord(command, command, result, state="done"))

    async def _notice(self, text: str) -> None:
        await self._append_record(NoticeRecord(text))

    async def _new_conversation(self, command: str) -> None:
        old = self.conversation
        self._snapshot_transcript(old)
        self._write_sidecar(old)
        if old.connect_worker is not None:
            await old.connect_worker.wait()
        await old.session.disconnect()
        self.conversation = Conversation(session=Session())
        self._connect_worker = self.connect()
        record = ToolRecord(command, command, "Started new conversation", state="done")
        self.conversation.transcript = [record]
        await self.query_one("#chat", Chat).rebuild(self.conversation.transcript)
        self._update_status_line()
        self._refresh_prompt_commands()
        self._refresh_todos()

    async def _context_command(self, command: str) -> None:
        if not self._connected_ok:
            await self._notice("not connected")
            return
        conv = self.conversation
        text = await conv.session.context_usage()
        if conv is self.conversation and others_idle(self.interaction):
            await self._echo_command(command, text)

    async def _model_command(self) -> None:
        if not self._connected_ok:
            await self._notice("not connected")
            return
        conv = self.conversation
        rows = await conv.session.model_rows()
        if conv is not self.conversation or not others_idle(self.interaction):
            return
        await self._open_bottom_takeover(ModelPicker([
            {
                "model_id": row.model_id,
                "title": row.title,
                "detail": row.detail,
            }
            for row in rows
        ]))

    async def _fork_conversation(self, command: str) -> None:
        conv = self.conversation
        if not conv.checkpoints:
            await self._notice("No checkpoints captured yet.")
            return
        fork_uuid = conv.last_message_uuid or conv.last_assistant_uuid
        if fork_uuid is None:
            await self._notice("No conversation point to fork at.")
            return
        self._snapshot_transcript(conv)
        self._write_sidecar(conv)
        result = conv.session.fork_result(conv.session_id, fork_uuid)
        plan = build_branch_plan(
            conv.transcript,
            conv.checkpoints,
            conv.last_assistant_uuid,
            fork_uuid,
            result.uuid_map,
        )
        branch = Conversation(session=Session())
        branch.session_id = result.session_id
        branch.transcript = plan.records
        branch.checkpoints = plan.checkpoints
        branch.last_assistant_uuid = plan.last_assistant_uuid
        branch.last_message_uuid = plan.last_message_uuid
        branch.permission_mode = conv.permission_mode
        branch.model = conv.model
        branch.model_id = conv.model_id
        branch.todos = deepcopy(conv.todos)
        await switch_to(self, branch)
        await self._echo_command(command, "Branched conversation")
        self._snapshot_transcript(branch)
        self._write_sidecar(branch)

    def _snapshot_transcript(self, conv=None) -> None:
        conv = conv or self.conversation
        if conv is not self.conversation or not self.is_mounted:
            return
        conv.transcript = self.query_one("#chat", Chat).records()

    def _write_sidecar(self, conv=None) -> None:
        conv = conv or self.conversation
        if not conv.session_id:
            return
        if conv is self.conversation and self.is_mounted:
            conv.draft = self.query_one(Prompt).text
        token = conv.session.session_last_modified(conv.session_id)
        write_sidecar(
            conv.session.sidecar_path(conv.session_id),
            token,
            conv.transcript,
            conv.checkpoints,
            conv.last_assistant_uuid,
            conv.last_message_uuid,
            conv.model_id,
            conv.model,
            conv.permission_mode,
            conv.draft,
        )

    def _snapshot_and_write_sidecar_if_idle(self) -> None:
        if not full_idle(self.interaction):
            return
        self._snapshot_transcript()
        self._write_sidecar()

    async def action_cycle_permission_mode(self) -> None:
        if not can_cycle_permission(self.interaction):
            raise SkipAction()
        index = PERMISSION_MODES.index(self.conversation.permission_mode)
        self.conversation.permission_mode = PERMISSION_MODES[(index + 1) % len(PERMISSION_MODES)]
        self._update_status_line()
        if self._connected_ok:
            await self.conversation.session.set_permission_mode(self.conversation.permission_mode)

    async def action_switcher(self) -> None:
        if not can_start_local_command(self.interaction):
            raise SkipAction()
        await self._open_switcher()

    async def action_quit(self) -> None:
        self._snapshot_and_write_sidecar_if_idle()
        self.exit()

    async def on_switcher_selected(self, event: Switcher.Selected) -> None:
        row = event.row
        self.interaction.transaction = "switch"
        try:
            await self._close_bottom_takeover()
            if row.get("session_id") == self.conversation.session_id:
                return
            await resume_cold(self, row)
        except Exception as error:
            await self._notice(str(error) or type(error).__name__)
        finally:
            self.interaction.transaction = None

    async def on_switcher_cancelled(self, event: Switcher.Cancelled) -> None:
        await self._close_bottom_takeover()

    async def on_rewind_picker_selected(self, event: RewindPicker.Selected) -> None:
        # A second Selected (key auto-repeat) must not spawn a second rewind
        # worker: the first handler owns the transaction until its worker ends.
        if self.interaction.transaction is not None:
            return
        self.interaction.transaction = "rewind"
        try:
            await self._close_bottom_takeover()
            self.rewind_checkpoint(event.checkpoint, event.operation)
        except BaseException:
            self.interaction.transaction = None
            raise

    async def on_rewind_picker_cancelled(self, event: RewindPicker.Cancelled) -> None:
        await self._close_bottom_takeover()

    async def on_model_picker_selected(self, event: ModelPicker.Selected) -> None:
        conv = self.conversation
        row = event.row
        self.interaction.transaction = "model"
        try:
            await self._close_bottom_takeover()
            label = await conv.session.set_model(row["model_id"])
            if conv is self.conversation:
                conv.model_id = row["model_id"]
                conv.model = label or row.get("title", row["model_id"])
                self._update_status_line()
                await self._echo_command("/model", conv.model)
                self._write_sidecar(conv)
        except Exception as error:
            if conv is self.conversation:
                await self._notice(str(error) or type(error).__name__)
        finally:
            self.interaction.transaction = None

    async def on_model_picker_cancelled(self, event: ModelPicker.Cancelled) -> None:
        await self._close_bottom_takeover()

    async def _open_switcher(self) -> None:
        rows = []
        for info in self.conversation.session.list_sessions():
            row = self.conversation.session.session_row(info)
            if row.session_id == self.conversation.session_id:
                continue
            path = self.conversation.session.sidecar_path(row.session_id)
            if not path.exists():
                continue
            rows.append(self._switcher_row(row, path))
        await self._open_bottom_takeover(Switcher(rows))

    def _switcher_row(self, row, path) -> dict:
        subtitle = row.subtitle
        if self._sidecar_drifted(path, row.session_id):
            suffix = "changed outside castcode"
            subtitle = f"{subtitle} · {suffix}" if subtitle else suffix
        return {
            "session_id": row.session_id,
            "title": row.title,
            "subtitle": subtitle,
            "last_modified": row.last_modified,
        }

    def _sidecar_drifted(self, path, session_id) -> bool:
        try:
            envelope = read_sidecar(path)
        except (OSError, ValueError, JSONDecodeError):
            return False
        return self.conversation.session.session_drifted(
            session_id, envelope.last_message_uuid
        )

    async def _open_rewind_picker(self) -> None:
        rows = self._rewind_rows()
        await self._open_bottom_takeover(RewindPicker(rows, index=max(len(rows) - 2, 0)))

    def _rewind_rows(self) -> list[dict]:
        if not self.conversation.checkpoints:
            return []
        last_modified = (
            self.conversation.session.session_last_modified(self.conversation.session_id)
            if self.conversation.session_id
            else None
        )
        subtitle = _relative_age(last_modified)
        by_uuid = {
            record.uuid: record.text
            for record in self.conversation.transcript
            if isinstance(record, UserRecord) and record.uuid
        }
        rows = []
        for checkpoint in self.conversation.checkpoints:
            text = by_uuid.get(checkpoint.get("user_uuid"), "")
            title = text.splitlines()[0] if text else checkpoint.get("user_uuid", "")
            rows.append({"title": title, "subtitle": subtitle, "checkpoint": checkpoint})
        rows.append({"title": "current", "current": True, "subtitle": subtitle})
        return rows

    async def _execute_rewind(self, checkpoint: dict, operation: str) -> None:
        conv = self.conversation
        if conv.connect_worker is not None:
            await conv.connect_worker.wait()
        self._snapshot_transcript(conv)
        plan = plan_rewind(
            conv.transcript,
            conv.checkpoints,
            checkpoint,
            operation,
        )
        forked_session_id = None
        uuid_map = {}
        if plan.fork_uuid is not None:
            result = conv.session.fork_result(conv.session_id, plan.fork_uuid)
            forked_session_id = result.session_id
            uuid_map = result.uuid_map
        applied = apply_rewind_uuid_map(plan, uuid_map)
        await conv.session.disconnect()
        conv.session = Session()
        conv.session_id = forked_session_id
        conv.connected_ok = False
        conv.connect_error = None
        conv.context_pct = None
        conv.cost_usd = 0.0
        conv.todos = TodoState()
        conv.transcript = applied.records
        conv.checkpoints = applied.checkpoints
        conv.last_assistant_uuid = applied.last_assistant_uuid
        conv.last_message_uuid = applied.last_message_uuid
        chat = self.query_one("#chat", Chat)
        await chat.rebuild(conv.transcript)
        self._stick(chat)
        self._update_status_line()
        self._refresh_todos()
        prompt = self.query_one(Prompt)
        prompt.clear()
        if plan.edit_text is not None:
            prompt.insert(plan.edit_text)
        self._connect_worker = self.connect()
        self._write_sidecar(conv)

    async def _open_bottom_takeover(self, picker) -> None:
        self._close_command_popup()
        self._disarm_rewind()
        self.interaction.picker_open = True
        self._active_picker = picker
        self.query_one("#chat", Chat).can_focus = False
        self.query_one(Prompt).display = False
        self.query_one("#status", StatusLine).display = False
        self.query_one("#todos", Static).display = False
        takeover = self.query_one("#bottom-takeover")
        await takeover.remove_children()
        takeover.display = True
        await takeover.mount(picker)
        picker.focus()

    async def _close_bottom_takeover(self) -> None:
        takeover = self.query_one("#bottom-takeover")
        await takeover.remove_children()
        takeover.display = False
        self.interaction.picker_open = False
        self._disarm_rewind()
        self._active_picker = None
        self.query_one("#chat", Chat).can_focus = True
        prompt = self.query_one(Prompt)
        prompt.display = True
        self.query_one("#status", StatusLine).display = True
        self._refresh_todos()
        prompt.focus()

    def _arm_rewind(self) -> None:
        self.interaction.rewind_generation += 1
        generation = self.interaction.rewind_generation
        self.interaction.rewind_armed = True
        if self._rewind_timer is not None:
            self._rewind_timer.stop()
        self._rewind_timer = self.set_timer(
            0.6,
            lambda: self._expire_rewind(generation),
        )

    def _disarm_rewind(self) -> None:
        self.interaction.rewind_generation += 1
        self.interaction.rewind_armed = False
        if self._rewind_timer is not None:
            self._rewind_timer.stop()
            self._rewind_timer = None

    def _expire_rewind(self, generation: int) -> None:
        if generation == self.interaction.rewind_generation:
            self.interaction.rewind_armed = False
            self._rewind_timer = None

    def _stick(self, chat) -> None:
        if self._permission_prompt is not None and self._permission_prompt.is_mounted:
            if chat.children and chat.children[-1] is not self._permission_prompt:
                chat.move_child(self._permission_prompt, after=chat.children[-1])
        if chat.follow:
            chat.scroll_end(animate=False)
            if not self._stick_pending:
                self._stick_pending = True
                self.call_after_refresh(self._stick_after_layout, chat)

    def _stick_after_layout(self, chat) -> None:
        self._stick_pending = False
        if chat.is_mounted and chat.follow:
            chat.scroll_end(animate=False)

    def _start_activity(self) -> None:
        self._activity_start = time.monotonic()
        self._spin = cycle(SPINNER)
        self._activity_timer = self.set_interval(0.1, self._tick)
        self._tick()

    def _tick(self) -> None:
        try:
            activity = self._activity
        except NoMatches:
            if self._activity_timer is not None:
                self._activity_timer.stop()
                self._activity_timer = None
            return
        elapsed = format_elapsed(time.monotonic() - self._activity_start)
        spin = next(self._spin)
        was_visible = activity.display
        if self._interrupting:
            activity.update(f"{spin} Stopping… {elapsed}")
        elif self._permission_prompt is not None:
            activity.update(f"{spin} Waiting for input… {elapsed}")
        elif self._can_interrupt:
            activity.update(f"{spin} Working… {elapsed}  ·  esc to interrupt")
        elif self._connected_ok:
            activity.update(f"{spin} Working… {elapsed}")
        else:
            activity.update(f"{spin} Connecting… {elapsed}")
        activity.display = True
        if not was_visible:
            self._refresh_todos()

    def _stop_activity(self) -> None:
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None
        self._activity.update("")
        if self._activity.display:
            self._activity.display = False
            self._refresh_todos()

    def _record_tool_use(self, tool_use_id: str, name: str, tool_input: dict) -> None:
        if self.conversation.todos.record_tool_use(tool_use_id, name, tool_input):
            self._refresh_todos()

    def _record_tool_results(
        self,
        results: list[tuple[str, str, object, dict | None, bool]],
    ) -> None:
        if self.conversation.todos.record_tool_results(results):
            self._refresh_todos()

    def _refresh_todos(self) -> None:
        if not self.is_mounted:
            return
        text = todo_text(self.conversation.todos.visible(), self._activity.display)
        todos = self._todos
        todos.update(text)
        todos.display = bool(text.plain if isinstance(text, Text) else text)

    async def _disconnect_client(self) -> None:
        await self.conversation.session.disconnect()

    async def _can_use_tool(self, tool_name: str, input_data: dict,
                            context: sdk.ToolPermissionContext):
        return await permissions.can_use_tool(self, tool_name, input_data, context)

    def _status_data(self) -> StatusData:
        return StatusData(
            model=self.conversation.model,
            context_pct=self.conversation.context_pct,
            permission_mode=self.conversation.permission_mode,
            cost_usd=self.conversation.cost_usd,
        )

    def _update_status_line(self) -> None:
        if self.is_mounted:
            self.query_one("#status", StatusLine).update_data(self._status_data())

    async def on_unmount(self) -> None:
        worker = self.conversation.connect_worker
        if worker is not None:
            try:
                await worker.wait()
            except WorkerCancelled:
                # App shutdown cancels workers before Unmount is dispatched.
                pass
        await self._disconnect_client()


def _relative_age(timestamp: int | float | None) -> str:
    if timestamp is None:
        return ""
    timestamp = float(timestamp)
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"
