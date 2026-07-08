import asyncio
import os
import shutil

import claude_agent_sdk as sdk
import pytest

from castcode.commands import CommandRow
from castcode.session import ModelRow, Session, build_options


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("CASTCODE_LIVE") != "1",
        reason="set CASTCODE_LIVE=1 to run real Claude SDK tests",
    ),
    pytest.mark.skipif(shutil.which("claude") is None, reason="claude binary not on PATH"),
]


async def _deny_tool(tool_name, input_data, context):
    return {"behavior": "deny", "message": "live test denies tool use"}


@pytest.fixture()
def _real_claude_config(monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.fixture()
async def live_session(_real_claude_config):
    session = Session()
    try:
        await asyncio.wait_for(
            session.connect(build_options("plan", _deny_tool), lambda: "plan"),
            30,
        )
        yield session
    finally:
        await session.disconnect()


async def test_live_connect_reports_commands_and_models(live_session):
    assert isinstance(live_session.commands, list)
    assert all(isinstance(row, CommandRow) for row in live_session.commands)
    rows = await asyncio.wait_for(live_session.model_rows(), 30)
    assert rows
    assert all(isinstance(row, ModelRow) for row in rows)
    assert all(row.model_id and row.title for row in rows)


async def test_live_context_usage_shape(live_session):
    text = await asyncio.wait_for(live_session.context_usage(), 30)
    assert isinstance(text, str)
    assert text


async def test_live_session_history_shapes(live_session):
    sessions = live_session.list_sessions()
    assert isinstance(sessions, list)
    if sessions:
        row = live_session.session_row(sessions[0])
        assert row.session_id
        token = live_session.session_last_modified(row.session_id)
        assert token is None or isinstance(token, (int, float))


async def test_live_query_receive_round_trip(_real_claude_config):
    session = Session()
    try:
        await asyncio.wait_for(
            session.connect(build_options("plan", _deny_tool), lambda: "plan"),
            30,
        )
        await asyncio.wait_for(session.query("Reply with only the word pong."), 30)
        saw_assistant = False
        saw_result = False
        saw_replayed_user = False
        async with asyncio.timeout(60):
            async for message in session.receive_response():
                if isinstance(message, sdk.AssistantMessage):
                    saw_assistant = True
                    assert hasattr(message, "uuid")
                    assert hasattr(message, "model")
                elif isinstance(message, sdk.UserMessage):
                    if message.parent_tool_use_id is None and isinstance(message.content, str):
                        saw_replayed_user = True
                        assert message.uuid
                elif isinstance(message, sdk.ResultMessage):
                    saw_result = True
                    assert isinstance(message.session_id, str)
        assert saw_assistant
        assert saw_replayed_user
        assert saw_result
    finally:
        await session.disconnect()
