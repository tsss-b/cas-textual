"""Shared test fixtures for the castcode characterization suite.

Builders mirror the installed claude_agent_sdk dataclasses (sdk 0.2.110). Required
positional fields are filled with throwaway defaults so tests only specify what they
assert on. Two fake clients are provided:

- BurstFakeClient — yields a prebuilt message list in one synchronous burst. Good for
  dispatch / tool-lifecycle / context-% tests. CANNOT exercise interrupt-drain or
  scroll-follow (everything lands before any key can; HANDOFF Testing §2).
- GatedFakeClient — queue-driven; the test feeds messages one at a time and pumps the
  UI with pilot.pause() between them, so Esc / scroll can interleave. Has async
  interrupt() that enqueues the terminal error ResultMessage, like the real CLI.
"""

import asyncio

import claude_agent_sdk as sdk

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_MODEL_KEY = "claude-opus-4-8[1m]"          # model_usage key (the [1m] variant)
DEFAULT_CONTEXT_WINDOW = 1_000_000


# --- block builders ---------------------------------------------------------

def text_block(text):
    return sdk.TextBlock(text=text)


def tool_use_block(id, name, input):
    return sdk.ToolUseBlock(id=id, name=name, input=input)


def tool_result_block(tool_use_id, content=None, is_error=None):
    return sdk.ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)


def server_tool_use_block(id, name, input):
    return sdk.ServerToolUseBlock(id=id, name=name, input=input)


def server_tool_result_block(tool_use_id, content):
    return sdk.ServerToolResultBlock(tool_use_id=tool_use_id, content=content)


# --- raw stream-event `event` dicts (inside StreamEvent.event) ---------------

def ev_text_start(index):
    return {"type": "content_block_start", "index": index,
            "content_block": {"type": "text"}}


def ev_tool_start(index, id, name):
    return {"type": "content_block_start", "index": index,
            "content_block": {"type": "tool_use", "id": id, "name": name}}


def ev_text_delta(index, text):
    return {"type": "content_block_delta", "index": index,
            "delta": {"type": "text_delta", "text": text}}


def ev_input_delta(index, partial_json):
    return {"type": "content_block_delta", "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial_json}}


def ev_stop(index):
    return {"type": "content_block_stop", "index": index}


# --- message builders -------------------------------------------------------

def stream_event(event, parent_tool_use_id=None, *, uuid="u", session_id="s"):
    return sdk.StreamEvent(uuid=uuid, session_id=session_id, event=event,
                           parent_tool_use_id=parent_tool_use_id)


def assistant_message(content, *, model=DEFAULT_MODEL, usage=None,
                      parent_tool_use_id=None, uuid=None):
    return sdk.AssistantMessage(content=content, model=model, usage=usage,
                                parent_tool_use_id=parent_tool_use_id, uuid=uuid)


def user_message(content, *, parent_tool_use_id=None, tool_use_result=None, uuid=None):
    return sdk.UserMessage(
        content=content,
        uuid=uuid,
        parent_tool_use_id=parent_tool_use_id,
        tool_use_result=tool_use_result,
    )


def result_message(*, subtype="success", is_error=False, usage=None, model_usage=None,
                   session_id="s", total_cost_usd=None):
    return sdk.ResultMessage(
        subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error,
        num_turns=1, session_id=session_id, usage=usage, model_usage=model_usage,
        total_cost_usd=total_cost_usd,
    )


def task_started_message(task_id, tool_use_id, description, *, task_type="local_agent"):
    return sdk.TaskStartedMessage(
        subtype="task_started",
        data={},
        task_id=task_id,
        description=description,
        uuid="u",
        session_id="s",
        tool_use_id=tool_use_id,
        task_type=task_type,
    )


def task_progress_message(task_id, tool_use_id, description, *,
                          last_tool_name=None, task_usage=None):
    return sdk.TaskProgressMessage(
        subtype="task_progress",
        data={},
        task_id=task_id,
        description=description,
        usage=task_usage or {"total_tokens": 0, "tool_uses": 0, "duration_ms": 0},
        uuid="u",
        session_id="s",
        tool_use_id=tool_use_id,
        last_tool_name=last_tool_name,
    )


def task_notification_message(task_id, tool_use_id, status, summary, *, task_usage=None):
    return sdk.TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id=task_id,
        status=status,
        output_file="",
        summary=summary,
        uuid="u",
        session_id="s",
        tool_use_id=tool_use_id,
        usage=task_usage,
    )


def task_updated_message(task_id, patch, *, status=None, data=None):
    return sdk.TaskUpdatedMessage(
        subtype="task_updated",
        data=data or {},
        task_id=task_id,
        patch=patch,
        status=status,
        uuid="u",
        session_id="s",
    )


def usage(input_tokens=0, cache_read=0, cache_creation=0):
    return {
        "input_tokens": input_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }


def model_usage(model_key=DEFAULT_MODEL_KEY, context_window=DEFAULT_CONTEXT_WINDOW):
    return {model_key: {"contextWindow": context_window}}


def server_info(label="Opus 4.8 with 1M context"):
    return {"models": [{"description": f"{label} · extra", "displayName": label}]}


# --- convenience turn builders ----------------------------------------------

def text_turn(text, *, model=DEFAULT_MODEL, index=0, turn_usage=None, mu=None):
    """Stream events + main-agent AssistantMessage + ResultMessage for one text reply."""
    return [
        stream_event(ev_text_start(index)),
        stream_event(ev_text_delta(index, text)),
        stream_event(ev_stop(index)),
        assistant_message([text_block(text)], model=model, usage=turn_usage),
        result_message(model_usage=mu),
    ]


# --- fake clients -----------------------------------------------------------

class _BaseFake:
    def __init__(self):
        self.queries = []
        self.connected = False
        self.disconnected = False
        self.interrupted = False
        self._server_info = server_info()

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def get_server_info(self):
        return self._server_info

    async def query(self, text):
        self.queries.append(text)


class BurstFakeClient(_BaseFake):
    def __init__(self, messages=()):
        super().__init__()
        self.messages = list(messages)

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        for m in self.messages:
            yield m
            if isinstance(m, sdk.ResultMessage):
                return


class GatedFakeClient(_BaseFake):
    """Test feeds messages via feed(); generator parks on the queue between yields."""

    def __init__(self):
        super().__init__()
        self._queue = asyncio.Queue()

    async def feed(self, message):
        await self._queue.put(message)

    async def interrupt(self):
        self.interrupted = True
        await self._queue.put(result_message(subtype="error_during_execution", is_error=True))

    async def receive_response(self):
        while True:
            msg = await self._queue.get()
            yield msg
            if isinstance(msg, sdk.ResultMessage):
                return
