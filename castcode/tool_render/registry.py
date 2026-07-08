from dataclasses import dataclass
from typing import Callable

from castcode.tool_render.renderers import (
    render_agent_input,
    render_agent_result,
    render_ask_user_question_input,
    render_ask_user_question_result,
    render_bash_input,
    render_bash_result,
    render_edit_input,
    render_edit_result,
    render_quiet_result,
    render_read_input,
    render_read_result,
    render_skill_input,
    render_skill_result_detail,
    render_task_create_input,
    render_task_create_result_detail,
    render_task_get_input,
    render_task_get_result_detail,
    render_task_list_input,
    render_task_list_result_detail,
    render_task_update_input,
    render_task_update_result,
    render_todo_write_input,
    render_todo_write_result_detail,
    render_tool_search_input,
    render_tool_search_result_detail,
    render_web_fetch_input,
    render_web_search_input,
    render_write_input,
    render_write_result,
)


@dataclass(frozen=True)
class ToolRenderer:
    input: Callable | None = None
    result: Callable | None = None
    detail: Callable | None = None


RENDERERS: dict[str, ToolRenderer] = {
    "Read": ToolRenderer(render_read_input, render_read_result),
    "Write": ToolRenderer(render_write_input, render_write_result),
    "Edit": ToolRenderer(render_edit_input, render_edit_result),
    "Bash": ToolRenderer(render_bash_input, render_bash_result),
    "WebSearch": ToolRenderer(render_web_search_input, render_quiet_result),
    "WebFetch": ToolRenderer(render_web_fetch_input, render_quiet_result),
    "Agent": ToolRenderer(render_agent_input, render_agent_result),
    "Task": ToolRenderer(render_agent_input, render_agent_result),
    "Skill": ToolRenderer(
        render_skill_input, render_quiet_result, render_skill_result_detail
    ),
    "AskUserQuestion": ToolRenderer(
        render_ask_user_question_input,
        render_ask_user_question_result,
    ),
    "ToolSearch": ToolRenderer(
        render_tool_search_input,
        render_quiet_result,
        render_tool_search_result_detail,
    ),
    "TaskCreate": ToolRenderer(
        render_task_create_input,
        render_quiet_result,
        render_task_create_result_detail,
    ),
    "TaskUpdate": ToolRenderer(
        render_task_update_input,
        render_task_update_result,
    ),
    "TaskGet": ToolRenderer(
        render_task_get_input,
        render_quiet_result,
        render_task_get_result_detail,
    ),
    "TaskList": ToolRenderer(
        render_task_list_input,
        render_quiet_result,
        render_task_list_result_detail,
    ),
    "TodoWrite": ToolRenderer(
        render_todo_write_input,
        render_quiet_result,
        render_todo_write_result_detail,
    ),
}
