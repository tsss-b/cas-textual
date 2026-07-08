from dataclasses import dataclass, field
import re

from textual.worker import Worker

from castcode.records import Record
from castcode.session import Session


_TASK_LINE_RE = re.compile(
    r"#(?P<id>\S+)\s+\[(?P<status>[^\]]+)\]\s+"
    r"(?P<subject>.*?)(?=\s+#\S+\s+\[|$)"
)
_TASK_CREATE_RE = re.compile(
    r"Task #(?P<id>\S+) created successfully:\s*(?P<subject>.+)"
)
_BLOCKED_RE = re.compile(r"\s+\[blocked by (?P<ids>[^\]]+)\]\s*$")


@dataclass
class TodoTask:
    id: str
    subject: str
    status: str = "pending"
    description: str = ""
    active_form: str = ""
    owner: str = ""
    blocked_by: list[str] = field(default_factory=list)


@dataclass
class TodoState:
    tasks: dict[str, TodoTask] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    pending: dict[str, dict] = field(default_factory=dict)

    def record_tool_use(self, tool_use_id: str, name: str, tool_input: dict) -> bool:
        if name == "TaskCreate":
            self.pending[tool_use_id] = dict(tool_input)
        elif name == "TaskUpdate":
            self.pending[tool_use_id] = dict(tool_input)
        elif name == "TodoWrite":
            return self._replace_todos(tool_input.get("todos"))
        return False

    def record_tool_result(self, tool_use_id: str, name: str, content,
                           tool_use_result: dict | None, is_error: bool) -> bool:
        if is_error:
            self.pending.pop(tool_use_id, None)
            return False
        if name == "TaskCreate":
            return self._apply_create(tool_use_id, content, tool_use_result)
        if name == "TaskUpdate":
            return self._apply_update(tool_use_id, tool_use_result)
        if name == "TaskGet":
            return self._apply_get(tool_use_result)
        if name == "TaskList":
            return self._apply_list(content, tool_use_result)
        if name == "TodoWrite" and isinstance(tool_use_result, dict):
            return self._replace_todos(tool_use_result.get("newTodos"))
        return False

    def record_tool_results(self, results: list[tuple[str, str, object, dict | None, bool]]) -> bool:
        changed = False
        for _, result in sorted(
            enumerate(results),
            key=lambda item: (self._result_priority(item[1][1]), item[0]),
        ):
            changed = self.record_tool_result(*result) or changed
        return changed

    def visible(self) -> list[TodoTask]:
        return [self.tasks[task_id] for task_id in self.order if task_id in self.tasks]

    def _upsert(self, task_id: str, **fields) -> TodoTask:
        task = self.tasks.get(task_id)
        if task is None:
            task = TodoTask(id=task_id, subject=str(fields.pop("subject", "")))
            self.tasks[task_id] = task
            self.order.append(task_id)
        for key, value in fields.items():
            if value not in (None, "", [], {}):
                setattr(task, key, value)
        return task

    def _remove(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        self.order = [existing for existing in self.order if existing != task_id]
        for task in self.tasks.values():
            task.blocked_by = [blocked for blocked in task.blocked_by if blocked != task_id]

    def _apply_create(self, tool_use_id: str, content, result: dict | None) -> bool:
        tool_input = self.pending.pop(tool_use_id, {})
        task_data = result.get("task") if isinstance(result, dict) else None
        if isinstance(task_data, dict):
            task_id = str(task_data.get("id") or "")
            subject = task_data.get("subject") or tool_input.get("subject") or ""
        else:
            task_id, subject = self._parse_create_text(content)
            subject = subject or tool_input.get("subject") or ""
        if not task_id:
            task_id = tool_use_id
        self._upsert(
            task_id,
            subject=str(subject),
            description=str(tool_input.get("description") or ""),
            active_form=str(
                tool_input.get("activeForm") or tool_input.get("active_form") or ""
            ),
            status="pending",
        )
        return True

    def _apply_update(self, tool_use_id: str, result: dict | None) -> bool:
        tool_input = self.pending.pop(tool_use_id, {})
        if isinstance(result, dict) and result.get("success") is False:
            return False
        task_id = self._task_id(tool_input)
        if not task_id and isinstance(result, dict):
            task_id = str(result.get("taskId") or "")
        if not task_id:
            return False
        status = tool_input.get("status")
        if status == "deleted":
            self._remove(task_id)
            return True
        task = self._upsert(task_id)
        for source, target in (
            ("subject", "subject"),
            ("description", "description"),
            ("activeForm", "active_form"),
            ("active_form", "active_form"),
            ("owner", "owner"),
        ):
            value = tool_input.get(source)
            if value not in (None, "", [], {}):
                setattr(task, target, str(value))
        if status:
            task.status = str(status)
            if status == "completed":
                self._remove_blocker(task_id)
        self._apply_block_edges(task_id, tool_input)
        return True

    def _apply_get(self, result: dict | None) -> bool:
        if not isinstance(result, dict):
            return False
        task = result.get("task")
        if not isinstance(task, dict):
            return False
        self._merge_task(task)
        return True

    def _apply_list(self, content, result: dict | None) -> bool:
        tasks = result.get("tasks") if isinstance(result, dict) else None
        if not isinstance(tasks, list):
            tasks = self._parse_list_text(content)
        if not isinstance(tasks, list):
            return False
        seen = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            seen.append(task_id)
            self._merge_task(task)
        self.order = [task_id for task_id in seen if task_id in self.tasks]
        self.tasks = {task_id: self.tasks[task_id] for task_id in self.order}
        return True

    def _replace_todos(self, todos) -> bool:
        if not isinstance(todos, list):
            return False
        self.tasks.clear()
        self.order.clear()
        for index, todo in enumerate(todos, start=1):
            if not isinstance(todo, dict):
                continue
            task_id = str(todo.get("id") or index)
            subject = str(todo.get("content") or todo.get("subject") or "")
            self._upsert(
                task_id,
                subject=subject,
                status=str(todo.get("status") or "pending"),
                active_form=str(
                    todo.get("activeForm") or todo.get("active_form") or ""
                ),
            )
        return True

    def _merge_task(self, data: dict) -> None:
        task_id = str(data.get("id") or data.get("taskId") or "")
        if not task_id:
            return
        blocked_by = data.get("blockedBy") or data.get("blocked_by") or []
        task = self._upsert(
            task_id,
            subject=str(data.get("subject") or ""),
            description=str(data.get("description") or ""),
            active_form=str(data.get("activeForm") or data.get("active_form") or ""),
            owner=str(data.get("owner") or ""),
        )
        if data.get("status"):
            task.status = str(data["status"])
        if isinstance(blocked_by, list):
            task.blocked_by = [str(item) for item in blocked_by]

    def _apply_block_edges(self, task_id: str, tool_input: dict) -> None:
        blocked_by = tool_input.get("addBlockedBy") or tool_input.get("add_blocked_by")
        if isinstance(blocked_by, list):
            task = self._upsert(task_id)
            for blocker in blocked_by:
                blocker = str(blocker)
                if blocker not in task.blocked_by:
                    task.blocked_by.append(blocker)
        blocks = tool_input.get("addBlocks") or tool_input.get("add_blocks")
        if isinstance(blocks, list):
            for blocked_id in blocks:
                task = self._upsert(str(blocked_id))
                if task_id not in task.blocked_by:
                    task.blocked_by.append(task_id)

    def _remove_blocker(self, task_id: str) -> None:
        for task in self.tasks.values():
            task.blocked_by = [blocked for blocked in task.blocked_by if blocked != task_id]

    def _task_id(self, data: dict) -> str:
        return str(data.get("taskId") or data.get("id") or data.get("task_id") or "")

    def _result_priority(self, name: str) -> int:
        if name in ("TaskList", "TaskGet"):
            return 0
        if name == "TodoWrite":
            return 1
        if name == "TaskCreate":
            return 2
        if name == "TaskUpdate":
            return 3
        return 4

    def _parse_create_text(self, content) -> tuple[str, str]:
        if not isinstance(content, str):
            return "", ""
        match = _TASK_CREATE_RE.search(content)
        if not match:
            return "", ""
        return match.group("id"), match.group("subject").strip()

    def _parse_list_text(self, content) -> list[dict] | None:
        if not isinstance(content, str):
            return None
        tasks = []
        for match in _TASK_LINE_RE.finditer(content):
            subject = match.group("subject").strip()
            blocked_by = []
            blocked = _BLOCKED_RE.search(subject)
            if blocked:
                subject = subject[:blocked.start()].strip()
                blocked_by = [
                    item.lstrip("#")
                    for item in re.split(r"[\s,]+", blocked.group("ids"))
                    if item.strip()
                ]
            tasks.append({
                "id": match.group("id"),
                "status": match.group("status"),
                "subject": subject,
                "blockedBy": blocked_by,
            })
        return tasks or None


@dataclass
class Conversation:
    session: Session = field(default_factory=Session)
    session_id: str | None = None

    transcript: list[Record] = field(default_factory=list)
    checkpoints: list[dict] = field(default_factory=list)
    last_assistant_uuid: str | None = None
    last_message_uuid: str | None = None

    todos: TodoState = field(default_factory=TodoState)
    cost_usd: float = 0.0
    model: str = "…"
    model_id: str | None = None
    context_pct: int | None = None
    permission_mode: str = "auto"
    commands: list = field(default_factory=list)

    draft: str = ""

    connect_worker: Worker | None = None
    connected_ok: bool = False
    connect_error: str | None = None
    last_usage: dict | None = None
    last_model: str | None = None

    def record_result(self, result) -> None:
        cost = result.total_cost_usd
        if isinstance(cost, (int, float)) and cost >= 0:
            self.cost_usd = float(cost)
        usage = self.last_usage or {}
        used = sum(usage.get(k, 0) for k in (
            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"
        ))
        window = self._context_window(result.model_usage or {})
        if used and window:
            self.context_pct = round(100 * used / window)

    def _context_window(self, model_usage: dict) -> int | None:
        if not self.last_model:
            return None
        for key, entry in model_usage.items():
            if key == self.last_model or key.startswith(self.last_model + "["):
                return entry.get("contextWindow")
        return None
