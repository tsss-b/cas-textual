from rich.text import Text


_TODO_VISIBLE = 5


def todo_text(tasks, nested: bool) -> Text | str:
    if not tasks:
        return ""
    done = sum(task.status == "completed" for task in tasks)
    active = sum(task.status == "in_progress" for task in tasks)
    blocked = sum(task.status != "completed" and bool(task.blocked_by) for task in tasks)
    open_count = sum(
        task.status not in ("completed", "in_progress") and not task.blocked_by
        for task in tasks
    )
    counts = []
    for count, label in (
        (done, "done"),
        (active, "active"),
        (blocked, "blocked"),
        (open_count, "open"),
    ):
        if count:
            counts.append(f"{count} {label}")
    text = Text()
    if nested:
        text.append("  └─ ")
    _append_todo_header(text, len(tasks), counts)
    for task in tasks[:_TODO_VISIBLE]:
        text.append("\n")
        if nested:
            text.append("     ")
        text.append(_todo_line(task))
    hidden = len(tasks) - _TODO_VISIBLE
    if hidden > 0:
        text.append("\n")
        if nested:
            text.append("     ")
        text.append("… ")
        text.append(str(hidden), style="bold")
        text.append(" more")
    return text


def _append_todo_header(text: Text, total: int, counts: list[str]) -> None:
    text.append(str(total), style="bold")
    text.append(f" task{'s' if total != 1 else ''}")
    if not counts:
        return
    text.append(" (")
    for index, item in enumerate(counts):
        if index:
            text.append(", ")
        number, label = item.split(" ", 1)
        text.append(number, style="bold")
        text.append(f" {label}")
    text.append(")")


def _todo_line(task) -> str:
    if task.status == "completed":
        return f"✔ {task.subject}"
    if task.status == "in_progress":
        return f"◉ {task.active_form or task.subject}"
    suffix = ""
    if task.blocked_by:
        blockers = ", ".join(f"#{item}" for item in task.blocked_by)
        suffix = f" [blocked by {blockers}]"
    return f"◻ {task.subject}{suffix}"
