from dataclasses import dataclass


PERMISSION_MODES = ("auto", "default", "acceptEdits", "plan")


@dataclass
class InteractionState:
    sending: bool = False
    interruptible: bool = False
    interrupting: bool = False
    picker_open: bool = False
    transaction: str | None = None
    permission_prompt: object | None = None
    local_command_pending: bool = False
    rewind_armed: bool = False
    rewind_generation: int = 0


def full_idle(state: InteractionState) -> bool:
    return (
        not state.sending
        and not state.interruptible
        and not state.interrupting
        and not state.picker_open
        and state.transaction is None
        and state.permission_prompt is None
        and not state.local_command_pending
    )


def others_idle(state: InteractionState) -> bool:
    return (
        not state.sending
        and not state.interruptible
        and not state.interrupting
        and not state.picker_open
        and state.transaction is None
        and state.permission_prompt is None
    )


can_submit = full_idle
can_start_local_command = full_idle


def can_cycle_permission(state: InteractionState) -> bool:
    return (
        not state.picker_open
        and state.transaction is None
        and not state.local_command_pending
    )


def can_interrupt(state: InteractionState) -> bool:
    return state.interruptible and not state.interrupting


def can_open_rewind_from_esc(state: InteractionState, prompt_empty: bool) -> bool:
    return (
        prompt_empty
        and state.permission_prompt is None
        and not state.picker_open
        and not state.sending
        and not state.interruptible
        and not state.interrupting
        and state.transaction is None
        and not state.local_command_pending
    )
