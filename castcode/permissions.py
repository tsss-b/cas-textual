import asyncio
from typing import Any, Protocol, runtime_checkable

import claude_agent_sdk as sdk

from castcode.tool_render import render_tool_input
from castcode.ui.input import Prompt
from castcode.ui.layout import Chat
from castcode.ui.prompts import (
    Choice,
    PromptResult,
    QuestionPrompt,
    ToolApprovalPrompt,
)


@runtime_checkable
class PermissionHost(Protocol):
    """What the permission flow reaches for off the app. CastcodeApp satisfies this
    structurally (duck-typed); no explicit inheritance. runtime_checkable so
    test_host_protocols can assert CastcodeApp still satisfies it."""

    _permission_lock: asyncio.Lock
    _permission_prompt: Any
    _interrupting: bool

    def query_one(self, selector, expect_type=None): ...
    def call_after_refresh(self, callback, *args, **kwargs): ...
    def _tick(self) -> None: ...
    def _stick(self, chat) -> None: ...


async def can_use_tool(app: PermissionHost, tool_name: str, input_data: dict,
                       context: sdk.ToolPermissionContext):
    # One prompt at a time: holding the lock across the human prompt makes
    # queued permission requests serialize behind whichever prompt is showing.
    async with app._permission_lock:
        if app._interrupting:
            return sdk.PermissionResultDeny(
                message="User denied this tool request",
                interrupt=True,
            )
        if tool_name == "AskUserQuestion":
            questions = input_data.get("questions")
            if not _valid_questions(questions):
                return sdk.PermissionResultDeny(
                    message="AskUserQuestion was called with no valid questions"
                )
            prompt = QuestionPrompt(questions)
            result = await prompt_for_permission(app, prompt)
            if result.action == "questions":
                return sdk.PermissionResultAllow(
                    updated_input={
                        "questions": questions,
                        "answers": result.answers or {},
                    }
                )
            return sdk.PermissionResultDeny(message="User cancelled this question")

        local_suggestions = [
            suggestion for suggestion in context.suggestions
            if suggestion.destination == "localSettings"
        ]
        prompt = ToolApprovalPrompt(
            _approval_title(tool_name, context),
            _approval_description(tool_name, input_data, context),
            _approval_choices(tool_name, input_data, bool(local_suggestions)),
        )
        result = await prompt_for_permission(app, prompt)
        if result.action == "allow":
            return sdk.PermissionResultAllow(updated_input=input_data)
        if result.action == "allow_always":
            return sdk.PermissionResultAllow(
                updated_input=input_data,
                updated_permissions=local_suggestions,
            )
        if result.action == "deny_stop":
            app._interrupting = True
            return sdk.PermissionResultDeny(
                message="User denied this tool request",
                interrupt=True,
            )
        if result.action == "cancel":
            return sdk.PermissionResultDeny(message="User cancelled this tool request")
        return sdk.PermissionResultDeny(message="User denied this tool request")


def _valid_questions(questions) -> bool:
    # QuestionPrompt indexes questions/options structurally and keys answers by
    # the question text; anything model-authored that breaks that shape is
    # denied here rather than crashing the prompt mid-turn.
    if not isinstance(questions, list) or not questions:
        return False
    for question in questions:
        if not isinstance(question, dict):
            return False
        if not isinstance(question.get("question", ""), str):
            return False
        options = question.get("options", [])
        if not isinstance(options, list):
            return False
        if not all(isinstance(option, dict) for option in options):
            return False
    return True


def _approval_title(tool_name: str, context: sdk.ToolPermissionContext) -> str:
    return context.title or f"Claude wants to use {tool_name}"


def _approval_description(tool_name: str, input_data: dict,
                         context: sdk.ToolPermissionContext) -> str:
    description = (
        context.description
        or context.decision_reason
        or _tool_display(tool_name, input_data)
    )
    if context.blocked_path:
        blocked = f"Blocked path: {context.blocked_path}"
        return f"{description}\n{blocked}" if description else blocked
    return description


def _approval_choices(tool_name: str, input_data: dict,
                     has_persistent_rule: bool) -> list[Choice]:
    display = _tool_display(tool_name, input_data)
    choices = [Choice("allow", "Allow once", f"Run {display} this time.")]
    if has_persistent_rule:
        choices.append(
            Choice(
                "allow_always",
                "Allow always",
                "Persist the suggested local permission rule.",
            )
        )
    choices.extend([
        Choice("deny", "No", "Do not run this tool."),
        Choice(
            "deny_stop",
            "No and stop",
            "Do not run this tool and stop the current turn.",
        ),
    ])
    return choices


def _tool_display(tool_name: str, input_data: dict) -> str:
    display = render_tool_input(tool_name, input_data)
    text = " ".join(display.title.split())
    if display.detail:
        detail = " ".join(display.detail.splitlines()[0].split())
        text = f"{text} ({detail})"
    return text


async def prompt_for_permission(app: PermissionHost, prompt) -> PromptResult:
    chat = app.query_one("#chat", Chat)
    entry = app.query_one(Prompt)
    entry.display = False
    app._permission_prompt = prompt
    try:
        app._tick()
        await chat.mount(prompt)
        app._stick(chat)
        chat.scroll_end(animate=False)

        def scroll_after_layout() -> None:
            chat.scroll_end(animate=False)
            app.call_after_refresh(chat.scroll_end, animate=False)

        app.call_after_refresh(scroll_after_layout)
        prompt.focus()
        return await prompt.wait()
    finally:
        if prompt.is_mounted:
            await prompt.remove()
        app._permission_prompt = None
        entry.display = True
        entry.focus()
        app._tick()
