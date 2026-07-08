from textual import work
from textual.widgets import Static

from castcode.app import CastcodeApp
from castcode.ui.messages import ToolMessage

import fixtures as fx


async def _noop(self):
    pass


def _make_app(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    return CastcodeApp()


def _todos_text(app):
    return str(app.query_one("#todos", Static).render())


def _todos_render(app):
    return app.query_one("#todos", Static).render()


def _tool_text(msg, selector):
    return str(msg.query_one(selector, Static).render())


async def _run_burst(app, messages):
    app.conversation.session.client = fx.BurstFakeClient(messages)
    app._connected_ok = True
    w = app.send("go")
    await w.wait()


def _task_result(task_id, subject):
    return {"task": {"id": task_id, "subject": subject}}


def _update_result(task_id):
    return {"success": True, "taskId": task_id, "updatedFields": ["status"]}


async def test_task_tools_render_compact_todo_strip(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("c1", "TaskCreate", {
                    "subject": "Wire up tool result rendering",
                    "description": "Render tool outputs",
                    "activeForm": "Wiring up tool result rendering",
                }),
                fx.tool_use_block("c2", "TaskCreate", {
                    "subject": "Add permission mode picker",
                    "description": "Cycle permission modes",
                }),
                fx.tool_use_block("c3", "TaskCreate", {
                    "subject": "Implement conversation rewind",
                    "description": "Restore prior turns",
                }),
            ]),
            fx.user_message(
                [
                    fx.tool_result_block("c1", content="", is_error=None),
                    fx.tool_result_block("c2", content="", is_error=None),
                    fx.tool_result_block("c3", content="", is_error=None),
                ],
                tool_use_result={
                    "results": {
                        "c1": _task_result("1", "Wire up tool result rendering"),
                        "c2": _task_result("2", "Add permission mode picker"),
                        "c3": _task_result("3", "Implement conversation rewind"),
                    }
                },
            ),
            fx.assistant_message([
                fx.tool_use_block("u1", "TaskUpdate", {
                    "taskId": "1",
                    "status": "in_progress",
                    "activeForm": "Wiring up tool result rendering",
                }),
            ]),
            fx.user_message(
                [fx.tool_result_block("u1", content="", is_error=None)],
                tool_use_result=_update_result("1"),
            ),
            fx.assistant_message([
                fx.tool_use_block("u2", "TaskUpdate", {
                    "taskId": "3",
                    "addBlockedBy": ["1"],
                }),
            ]),
            fx.user_message(
                [fx.tool_result_block("u2", content="", is_error=None)],
                tool_use_result={"success": True, "taskId": "3", "updatedFields": ["blockedBy"]},
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assert app.query_one("#todos", Static).display is True
        assert _todos_text(app).splitlines() == [
            "3 tasks (1 active, 1 blocked, 1 open)",
            "◉ Wiring up tool result rendering",
            "◻ Add permission mode picker",
            "◻ Implement conversation rewind [blocked by #1]",
        ]


async def test_task_completion_unblocks_and_deleted_task_disappears(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("c1", "TaskCreate", {"subject": "Wire up tool result rendering"}),
                fx.tool_use_block("c2", "TaskCreate", {"subject": "Add permission mode picker"}),
                fx.tool_use_block("c3", "TaskCreate", {"subject": "Implement conversation rewind"}),
            ]),
            fx.user_message(
                [
                    fx.tool_result_block("c1", content="", is_error=None),
                    fx.tool_result_block("c2", content="", is_error=None),
                    fx.tool_result_block("c3", content="", is_error=None),
                ],
                tool_use_result={
                    "results": {
                        "c1": _task_result("1", "Wire up tool result rendering"),
                        "c2": _task_result("2", "Add permission mode picker"),
                        "c3": _task_result("3", "Implement conversation rewind"),
                    }
                },
            ),
            fx.assistant_message([
                fx.tool_use_block("u1", "TaskUpdate", {
                    "taskId": "3",
                    "addBlockedBy": ["1"],
                }),
            ]),
            fx.user_message(
                [fx.tool_result_block("u1", content="", is_error=None)],
                tool_use_result={"success": True, "taskId": "3", "updatedFields": ["blockedBy"]},
            ),
            fx.assistant_message([
                fx.tool_use_block("u2", "TaskUpdate", {
                    "taskId": "1",
                    "status": "completed",
                }),
                fx.tool_use_block("u3", "TaskUpdate", {
                    "taskId": "2",
                    "status": "deleted",
                }),
            ]),
            fx.user_message(
                [
                    fx.tool_result_block("u2", content="", is_error=None),
                    fx.tool_result_block("u3", content="", is_error=None),
                ],
                tool_use_result={
                    "results": {
                        "u2": _update_result("1"),
                        "u3": _update_result("2"),
                    }
                },
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assert _todos_text(app).splitlines() == [
            "2 tasks (1 done, 1 open)",
            "✔ Wire up tool result rendering",
            "◻ Implement conversation rewind",
        ]


async def test_task_list_snapshot_is_authoritative(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("l1", "TaskList", {}),
            ]),
            fx.user_message(
                [fx.tool_result_block("l1", content="", is_error=None)],
                tool_use_result={
                    "tasks": [
                        {
                            "id": "1",
                            "subject": "Wire up tool result rendering",
                            "status": "completed",
                            "blockedBy": [],
                        },
                        {
                            "id": "2",
                            "subject": "Add permission mode picker",
                            "status": "pending",
                            "blockedBy": [],
                        },
                    ]
                },
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assert _todos_text(app).splitlines() == [
            "2 tasks (1 done, 1 open)",
            "✔ Wire up tool result rendering",
            "◻ Add permission mode picker",
        ]


async def test_task_tool_success_rows_stay_to_title_and_detail(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("c1", "TaskCreate", {
                    "subject": "Wire up tool result rendering",
                }),
                fx.tool_use_block("u1", "TaskUpdate", {
                    "taskId": "1",
                    "status": "in_progress",
                    "activeForm": "Wiring up tool result rendering",
                }),
                fx.tool_use_block("g1", "TaskGet", {"id": "2"}),
                fx.tool_use_block("l1", "TaskList", {}),
            ]),
            fx.user_message(
                [
                    fx.tool_result_block("c1", content="", is_error=None),
                    fx.tool_result_block("u1", content="", is_error=None),
                    fx.tool_result_block("g1", content="", is_error=None),
                    fx.tool_result_block("l1", content="", is_error=None),
                ],
                tool_use_result={
                    "results": {
                        "c1": _task_result("1", "Wire up tool result rendering"),
                        "u1": _update_result("1"),
                        "g1": {
                            "task": {
                                "id": "2",
                                "subject": "Add permission mode picker",
                                "status": "pending",
                            }
                        },
                        "l1": {
                            "tasks": [
                                {
                                    "id": "1",
                                    "subject": "Wire up tool result rendering",
                                    "status": "completed",
                                    "blockedBy": [],
                                },
                                {
                                    "id": "2",
                                    "subject": "Add permission mode picker",
                                    "status": "pending",
                                    "blockedBy": [],
                                },
                            ]
                        },
                    }
                },
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        tools = list(app.query(ToolMessage))
        assert [_tool_text(tool, ".tool-title") for tool in tools] == [
            "TaskCreate",
            "TaskUpdate",
            "TaskGet",
            "TaskList",
        ]
        assert [_tool_text(tool, ".tool-detail") for tool in tools] == [
            "Created #1 · Wire up tool result rendering",
            "#1 → in progress · active: Wiring up tool result rendering",
            "#2 pending: Add permission mode picker",
            "2 tasks (1 done, 1 open)",
        ]
        assert all(
            tool.query_one(".tool-preview", Static).display is False
            for tool in tools
        )


async def test_same_batch_update_wins_over_stale_task_list(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("c1", "TaskCreate", {
                    "subject": "Render task tool calls in the TUI",
                }),
                fx.tool_use_block("c2", "TaskCreate", {
                    "subject": "Add a status icon for completed tasks",
                }),
            ]),
            fx.user_message(
                [
                    fx.tool_result_block("c1", content="", is_error=None),
                    fx.tool_result_block("c2", content="", is_error=None),
                ],
                tool_use_result={
                    "results": {
                        "c1": _task_result("1", "Render task tool calls in the TUI"),
                        "c2": _task_result("2", "Add a status icon for completed tasks"),
                    }
                },
            ),
            fx.assistant_message([
                fx.tool_use_block("u1", "TaskUpdate", {
                    "taskId": "1",
                    "status": "in_progress",
                }),
            ]),
            fx.user_message(
                [fx.tool_result_block("u1", content="", is_error=None)],
                tool_use_result=_update_result("1"),
            ),
            fx.assistant_message([
                fx.tool_use_block("u2", "TaskUpdate", {
                    "taskId": "1",
                    "status": "completed",
                }),
                fx.tool_use_block("l1", "TaskList", {}),
            ]),
            fx.user_message(
                [
                    fx.tool_result_block("u2", content="", is_error=None),
                    fx.tool_result_block("l1", content="", is_error=None),
                ],
                tool_use_result={
                    "results": {
                        "u2": _update_result("1"),
                        "l1": {
                            "tasks": [
                                {
                                    "id": "1",
                                    "subject": "Render task tool calls in the TUI",
                                    "status": "in_progress",
                                    "blockedBy": [],
                                },
                                {
                                    "id": "2",
                                    "subject": "Add a status icon for completed tasks",
                                    "status": "pending",
                                    "blockedBy": [],
                                },
                            ]
                        },
                    }
                },
            ),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assert _todos_text(app).splitlines() == [
            "2 tasks (1 done, 1 open)",
            "✔ Render task tool calls in the TUI",
            "◻ Add a status icon for completed tasks",
        ]

        tools = list(app.query(ToolMessage))
        assert _tool_text(tools[-2], ".tool-detail") == "#1 → completed"
        assert _tool_text(tools[-1], ".tool-detail") == "2 tasks (1 active, 1 open)"


async def test_task_create_string_result_seeds_real_task_id(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("c1", "TaskCreate", {
                    "subject": "Wire up tool result rendering",
                    "activeForm": "Wiring up tool result rendering",
                }),
            ]),
            fx.user_message([
                fx.tool_result_block(
                    "c1",
                    content="Task #1 created successfully: Wire up tool result rendering",
                    is_error=None,
                ),
            ]),
            fx.assistant_message([
                fx.tool_use_block("u1", "TaskUpdate", {
                    "id": "1",
                    "status": "in_progress",
                    "active_form": "Wiring up tool result rendering",
                }),
            ]),
            fx.user_message([
                fx.tool_result_block("u1", content="Task #1 updated", is_error=None),
            ]),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assert _todos_text(app).splitlines() == [
            "1 task (1 active)",
            "◉ Wiring up tool result rendering",
        ]


async def test_legacy_todo_write_replaces_the_todo_strip(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        messages = [
            fx.assistant_message([
                fx.tool_use_block("tw1", "TodoWrite", {
                    "todos": [
                        {
                            "content": "Wire up tool result rendering",
                            "status": "completed",
                            "activeForm": "Wiring up tool result rendering",
                        },
                        {
                            "content": "Add permission mode picker",
                            "status": "pending",
                            "activeForm": "Adding permission mode picker",
                        },
                    ]
                }),
            ]),
            fx.user_message([
                fx.tool_result_block("tw1", content="updated todos", is_error=None),
            ]),
            fx.result_message(),
        ]
        await _run_burst(app, messages)
        await pilot.pause()

        assert _todos_text(app).splitlines() == [
            "2 tasks (1 done, 1 open)",
            "✔ Wire up tool result rendering",
            "◻ Add permission mode picker",
        ]


async def test_todo_strip_nests_under_visible_activity(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app._record_tool_use("tw1", "TodoWrite", {
            "todos": [
                {
                    "content": "Wire up tool result rendering",
                    "status": "in_progress",
                    "activeForm": "Wiring up tool result rendering",
                },
                {
                    "content": "Add permission mode picker",
                    "status": "pending",
                    "activeForm": "Adding permission mode picker",
                },
            ]
        })
        await pilot.pause()

        assert app.query_one("#activity", Static).display is False
        assert _todos_text(app).splitlines() == [
            "2 tasks (1 active, 1 open)",
            "◉ Wiring up tool result rendering",
            "◻ Add permission mode picker",
        ]

        app._start_activity()
        await pilot.pause()

        assert app.query_one("#activity", Static).display is True
        assert _todos_text(app).splitlines() == [
            "  └─ 2 tasks (1 active, 1 open)",
            "     ◉ Wiring up tool result rendering",
            "     ◻ Add permission mode picker",
        ]

        app._stop_activity()
        await pilot.pause()

        assert app.query_one("#activity", Static).display is False
        assert _todos_text(app).splitlines() == [
            "2 tasks (1 active, 1 open)",
            "◉ Wiring up tool result rendering",
            "◻ Add permission mode picker",
        ]


async def test_todo_counts_are_bold_without_marking_task_text(monkeypatch):
    app = _make_app(monkeypatch)
    async with app.run_test() as pilot:
        app._record_tool_use("tw1", "TodoWrite", {
            "todos": [
                {
                    "content": "Wire up [literal] rendering",
                    "status": "completed",
                    "activeForm": "Wiring up [literal] rendering",
                },
                {
                    "content": "Add permission mode picker",
                    "status": "pending",
                    "activeForm": "Adding permission mode picker",
                },
            ]
        })
        await pilot.pause()

        renderable = _todos_render(app)
        assert str(renderable).splitlines() == [
            "2 tasks (1 done, 1 open)",
            "✔ Wire up [literal] rendering",
            "◻ Add permission mode picker",
        ]

        bold_ranges = {
            renderable.plain[span.start:span.end]
            for span in renderable.spans
            if str(span.style) == "bold"
        }
        assert bold_ranges == {"1", "2"}
