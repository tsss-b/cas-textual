"""Characterization tests for the tool-result lifecycle (both outcomes).

Pins _apply_tool_results: a ToolMessage keyed by tool_use_id transitions
to 'done' on a success ToolResultBlock and to 'error' on an is_error block, with
inline error text updated on the correct id-keyed tool row.
"""

from textual import work

from textual.widgets import Static
from castcode.tool_render import render_tool_error
from castcode.app import CastcodeApp
from castcode.ui.messages import NoticeMessage, ToolMessage

import fixtures as fx


async def _noop(self):
    pass


def _body_text(msg):
    return str(msg.query_one(".tool-error", Static).render())


def _preview_text(msg):
    return str(msg.query_one(".tool-preview", Static).render())


def _detail_text(msg):
    return str(msg.query_one(".tool-detail", Static).render())


def _block_preview_text(msg):
    return str(msg.query_one(".tool-block-preview", Static).render())


def _image_payload():
    return {
        "type": "image",
        "source": {"media_type": "image/png", "data": "YWJjZGVm"},
    }


def _build_messages():
    # Two tool_use blocks started via stream events so they get keyed into the
    # `tools` dict by their id, then a result for each carried by a UserMessage.
    return [
        fx.stream_event(fx.ev_tool_start(0, "tA", "Read")),
        fx.stream_event(fx.ev_tool_start(1, "tB", "Bash")),
        fx.assistant_message([
            fx.tool_use_block("tA", "Read", {"file_path": "/tmp/a"}),
            fx.tool_use_block("tB", "Bash", {"command": "false"}),
        ]),
        fx.user_message([
            fx.tool_result_block("tA", content="all good", is_error=None),
            fx.tool_result_block(
                "tB",
                content="<tool_use_error>boom   failed</tool_use_error>",
                is_error=True,
            ),
        ]),
        fx.result_message(),
    ]


async def _run(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    return app


async def test_success_tool_goes_done(monkeypatch):
    app = await _run(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(_build_messages())
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tools = list(app.query(ToolMessage))
        assert len(tools) == 2
        ta = tools[0]
        # tA is the Read tool, success outcome -> state 'done' and -done class.
        assert ta.state == "done"
        assert ta.has_class("-done")
        assert not ta.has_class("-error")
        assert _preview_text(ta) == "1 line read"


async def test_error_tool_goes_error_with_inline_error(monkeypatch):
    app = await _run(monkeypatch)
    async with app.run_test() as pilot:
        app.conversation.session.client = fx.BurstFakeClient(_build_messages())
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tools = list(app.query(ToolMessage))
        tb = tools[1]
        # tB is the Bash tool, error outcome -> state 'error' and -error class.
        assert tb.state == "error"
        assert tb.has_class("-error")
        assert not tb.has_class("-done")

        assert tb.query_one(".tool-error", Static).display is True

        # Error text comes from render_tool_error of the block content: tags stripped,
        # whitespace collapsed.
        expected = render_tool_error("<tool_use_error>boom   failed</tool_use_error>")
        assert expected == "boom failed"
        assert _body_text(tb) == expected


async def test_error_message_after_correct_id_keyed_tool(monkeypatch):
    # When the success tool comes AFTER the error tool in document order, the
    # error text must still update the error tool (tB), not the last/first tool.
    # Here tB (error) is mounted before tA (success).
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tB", "Bash")),
            fx.stream_event(fx.ev_tool_start(1, "tA", "Read")),
            fx.assistant_message([
                fx.tool_use_block("tB", "Bash", {"command": "false"}),
                fx.tool_use_block("tA", "Read", {"file_path": "/tmp/a"}),
            ]),
            fx.user_message([
                fx.tool_result_block("tA", content="ok", is_error=None),
                fx.tool_result_block("tB", content="kaboom", is_error=True),
            ]),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tools = list(app.query(ToolMessage))
        # Document order: tB first, tA second.
        assert tools[0].state == "error"
        assert tools[1].state == "done"
        tb = tools[0]
        ta = tools[1]
        assert _body_text(tb) == "kaboom"
        assert _body_text(ta) == ""
        assert tb.query_one(".tool-error", Static).display is True
        assert ta.query_one(".tool-error", Static).display is False


async def test_success_result_mounts_no_error_message(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tA", "Read")),
            fx.assistant_message([
                fx.tool_use_block("tA", "Read", {"file_path": "/tmp/a"}),
            ]),
            fx.user_message([
                fx.tool_result_block("tA", content="fine", is_error=None),
            ]),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        assert list(app.query(ToolMessage))[0].state == "done"
        assert list(app.query(ToolMessage))[0].query_one(".tool-error", Static).display is False


async def test_error_with_empty_result_text_mounts_no_error_message(monkeypatch):
    # is_error=True but render_tool_error returns "" (non-str/list content) -> state
    # still flips to error, but no inline error row is shown.
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tB", "Bash")),
            fx.assistant_message([
                fx.tool_use_block("tB", "Bash", {"command": "false"}),
            ]),
            fx.user_message([
                fx.tool_result_block("tB", content=None, is_error=True),
            ]),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tb = list(app.query(ToolMessage))[0]
        assert tb.state == "error"
        assert tb.has_class("-error")
        assert render_tool_error(None) == ""
        assert tb.query_one(".tool-error", Static).display is False


async def test_single_result_uses_unambiguous_message_tool_use_result(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tA", "Read")),
            fx.assistant_message([
                fx.tool_use_block("tA", "Read", {"file_path": "/tmp/a.png"}),
            ]),
            fx.user_message(
                [fx.tool_result_block("tA", content=None, is_error=None)],
                tool_use_result=_image_payload(),
            ),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tool = list(app.query(ToolMessage))[0]
        assert _preview_text(tool) == "image: image/png, 6 B"
        assert "YWJjZGVm" not in _preview_text(tool)


async def test_single_result_prefers_keyed_tool_use_result(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tA", "Read")),
            fx.assistant_message([
                fx.tool_use_block("tA", "Read", {"file_path": "/tmp/a.png"}),
            ]),
            fx.user_message(
                [fx.tool_result_block("tA", content=None, is_error=None)],
                tool_use_result={"results": {"tA": _image_payload()}},
            ),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tool = list(app.query(ToolMessage))[0]
        assert _preview_text(tool) == "image: image/png, 6 B"


async def test_multi_result_message_does_not_smear_message_level_tool_use_result(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tA", "Read")),
            fx.stream_event(fx.ev_tool_start(1, "tB", "Read")),
            fx.assistant_message([
                fx.tool_use_block("tA", "Read", {"file_path": "/tmp/a.png"}),
                fx.tool_use_block("tB", "Read", {"file_path": "/tmp/b.png"}),
            ]),
            fx.user_message(
                [
                    fx.tool_result_block("tA", content=None, is_error=None),
                    fx.tool_result_block("tB", content=None, is_error=None),
                ],
                tool_use_result=_image_payload(),
            ),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tools = list(app.query(ToolMessage))
        assert [_preview_text(tool) for tool in tools] == ["", ""]
        assert all(tool.query_one(".tool-preview", Static).display is False for tool in tools)


async def test_multi_result_message_uses_keyed_tool_use_result(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tA", "Read")),
            fx.stream_event(fx.ev_tool_start(1, "tB", "Read")),
            fx.assistant_message([
                fx.tool_use_block("tA", "Read", {"file_path": "/tmp/a.png"}),
                fx.tool_use_block("tB", "Read", {"file_path": "/tmp/b.png"}),
            ]),
            fx.user_message(
                [
                    fx.tool_result_block("tA", content=None, is_error=None),
                    fx.tool_result_block("tB", content=None, is_error=None),
                ],
                tool_use_result={"results": {"tA": _image_payload()}},
            ),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tools = list(app.query(ToolMessage))
        assert [_preview_text(tool) for tool in tools] == ["image: image/png, 6 B", ""]


async def test_result_growth_calls_stick_after_preview_update(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    calls = []
    original = app._stick

    def spy(chat):
        calls.append([_preview_text(tool) for tool in app.query(ToolMessage)])
        original(chat)

    app._stick = spy
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tA", "Read")),
            fx.assistant_message([
                fx.tool_use_block("tA", "Read", {"file_path": "/tmp/a"}),
            ]),
            fx.user_message([
                fx.tool_result_block("tA", content="one\ntwo\n", is_error=None),
            ]),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        assert ["2 lines read"] in calls


async def test_terminal_error_marks_running_tools_without_interrupted_notice(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tA", "Read")),
            fx.result_message(subtype="error_during_execution", is_error=True),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tool = list(app.query(ToolMessage))[0]
        assert tool.state == "error"
        assert tool.has_class("-error")
        assert len(app.query(NoticeMessage)) == 0


async def test_limit_termination_leaves_running_tool_unmarked(monkeypatch):
    # error_max_turns is a limit-termination (is_error False): an in-flight tool
    # must NOT be painted red, and no Interrupted notice should appear.
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.stream_event(fx.ev_tool_start(0, "tA", "Read")),
            fx.result_message(subtype="error_max_turns", is_error=False),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tool = list(app.query(ToolMessage))[0]
        assert tool.state == "running"
        assert not tool.has_class("-error")
        assert len(app.query(NoticeMessage)) == 0


async def test_server_tool_use_and_result_render_through_generic_path(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.assistant_message([
                fx.server_tool_use_block("srv-1", "web_search", {"query": "textual"}),
                fx.server_tool_result_block(
                    "srv-1", {"type": "text", "text": "found [literal]"}
                ),
            ]),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tool = list(app.query(ToolMessage))[0]
        assert str(tool.query_one(".tool-title", Static).render()) == "web_search  textual"
        assert _preview_text(tool) == "found [literal]"
        assert tool.state == "done"


async def test_web_fetch_error_strips_error_wrapper(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.assistant_message([
                fx.tool_use_block("web-1", "WebFetch", {
                    "url": "https://this-domain-definitely-does-not-exist.example/page",
                    "prompt": "Summarize this page.",
                }),
            ]),
            fx.user_message([
                fx.tool_result_block(
                    "web-1",
                    content="<error>ECONNREFUSED</error>",
                    is_error=True,
                ),
            ]),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tool = list(app.query(ToolMessage))[0]
        assert str(tool.query_one(".tool-title", Static).render()) == "WebFetch"
        assert _detail_text(tool).splitlines() == [
            "https://this-domain-definitely-does-not-exist.example/page",
            "prompt: Summarize this page.",
        ]
        assert tool.state == "error"
        assert tool.has_class("-error")
        assert _body_text(tool) == "ECONNREFUSED"
        assert _preview_text(tool) == ""
        assert tool.query_one(".tool-preview", Static).display is False


async def test_skill_result_renders_success_detail_and_error_row(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.assistant_message([
                fx.tool_use_block("skill-1", "Skill", {"skill": "verify"}),
                fx.tool_use_block("skill-2", "Skill", {"skill": "missing"}),
            ]),
            fx.user_message([
                fx.tool_result_block(
                    "skill-1",
                    content="Launching skill: verify",
                    is_error=None,
                ),
                fx.tool_result_block(
                    "skill-2",
                    content="<tool_use_error>Skill not found: missing</tool_use_error>",
                    is_error=True,
                ),
            ]),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tools = list(app.query(ToolMessage))
        assert [str(tool.query_one(".tool-title", Static).render()) for tool in tools] == [
            "Skill",
            "Skill",
        ]
        assert _detail_text(tools[0]) == "Successfully loaded verify skill"
        assert _preview_text(tools[0]) == ""
        assert tools[0].query_one(".tool-preview", Static).display is False
        assert tools[0].state == "done"
        assert tools[0].has_class("-done")

        assert _detail_text(tools[1]) == ""
        assert _body_text(tools[1]) == "Skill not found: missing"
        assert tools[1].query_one(".tool-error", Static).display is True
        assert tools[1].state == "error"
        assert tools[1].has_class("-error")


async def test_tool_search_result_replaces_detail_with_clean_names(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        msgs = [
            fx.assistant_message([
                fx.tool_use_block("search-1", "ToolSearch", {
                    "query": "select:WebFetch,WebSearch",
                    "max_results": 2,
                }),
                fx.tool_use_block("search-2", "ToolSearch", {
                    "query": "select:Glob,Grep",
                    "max_results": 5,
                }),
            ]),
            fx.user_message(
                [
                    fx.tool_result_block("search-1", content="", is_error=None),
                    fx.tool_result_block(
                        "search-2",
                        content="No matching deferred tools found",
                        is_error=None,
                    ),
                ],
                tool_use_result={
                    "results": {
                        "search-1": {
                            "matches": [
                                {"name": "WebFetch", "description": "Fetch a URL"},
                                {"name": "WebSearch", "description": "Search web"},
                            ],
                            "query": "select:WebFetch,WebSearch",
                            "total_deferred_tools": 20,
                        }
                    }
                },
            ),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tools = list(app.query(ToolMessage))
        assert [str(tool.query_one(".tool-title", Static).render()) for tool in tools] == [
            "ToolSearch",
            "ToolSearch",
        ]
        assert [_detail_text(tool) for tool in tools] == [
            "WebFetch, WebSearch",
            "No matching tools found",
        ]
        assert [_preview_text(tool) for tool in tools] == ["", ""]
        assert tools[0].state == "done"
        assert tools[0].has_class("-done")
        assert not tools[0].has_class("-detail-error")
        assert tools[1].state == "error"
        assert tools[1].has_class("-error")
        assert tools[1].has_class("-detail-error")
        assert all(
            tool.query_one(".tool-preview", Static).display is False
            for tool in tools
        )


async def test_ask_user_question_result_renders_answers(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    questions = [
        {"question": "Which area?"},
        {"question": "Which task?"},
        {"question": "Anything else?"},
    ]
    result = {
        "questions": questions,
        "answers": {
            "Which area?": "UI",
            "Which task?": ["Tests", "Docs"],
            "Anything else?": "Nope",
        },
    }
    async with app.run_test() as pilot:
        msgs = [
            fx.assistant_message([
                fx.tool_use_block("ask-1", "AskUserQuestion", {"questions": questions}),
            ]),
            fx.user_message(
                [fx.tool_result_block("ask-1", content=None, is_error=None)],
                tool_use_result=result,
            ),
            fx.result_message(),
        ]
        app.conversation.session.client = fx.BurstFakeClient(msgs)
        app._connected_ok = True
        w = app.send("go")
        await w.wait()
        await pilot.pause()

        tool = list(app.query(ToolMessage))[0]
        assert str(tool.query_one(".tool-title", Static).render()) == "AskUserQuestion"
        assert _detail_text(tool) == ""
        assert tool.query_one(".tool-detail", Static).display is False
        assert _preview_text(tool) == (
            "1. Which area?\n"
            "└─ UI\n"
            "2. Which task?\n"
            "└─ Tests, Docs\n"
            "3. Anything else?\n"
            "└─ Nope"
        )
        assert tool.query_one(".tool-preview", Static).display is True
        assert _block_preview_text(tool) == ""
        assert tool.query_one(".tool-block-preview", Static).display is False
