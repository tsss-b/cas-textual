from castcode.interaction import (
    InteractionState,
    can_cycle_permission,
    can_interrupt,
    can_open_rewind_from_esc,
    can_submit,
    full_idle,
    others_idle,
)


def test_full_idle_predicate():
    assert full_idle(InteractionState()) is True
    assert full_idle(InteractionState(sending=True)) is False


def test_submit_blocked_by_busy_states():
    assert can_submit(InteractionState(picker_open=True)) is False
    assert can_submit(InteractionState(permission_prompt=object())) is False
    assert can_submit(InteractionState(transaction="model")) is False
    assert can_submit(InteractionState(sending=True)) is False
    assert can_submit(InteractionState(interruptible=True)) is False
    assert can_submit(InteractionState(interrupting=True)) is False
    assert can_submit(InteractionState(local_command_pending=True)) is False


def test_others_idle_ignores_local_command_pending_only():
    assert others_idle(InteractionState(local_command_pending=True)) is True
    assert full_idle(InteractionState(local_command_pending=True)) is False
    assert others_idle(InteractionState(sending=True)) is False
    assert others_idle(InteractionState(picker_open=True)) is False
    assert others_idle(InteractionState(transaction="model")) is False
    assert others_idle(InteractionState(permission_prompt=object())) is False


def test_permission_cycle_allowed_during_send():
    assert can_cycle_permission(InteractionState(sending=True)) is True


def test_permission_cycle_blocked_by_exclusive_states():
    assert can_cycle_permission(InteractionState(picker_open=True)) is False
    assert can_cycle_permission(InteractionState(transaction="rewind")) is False
    assert can_cycle_permission(InteractionState(local_command_pending=True)) is False


def test_interrupt_allowed_only_when_interruptible_and_not_interrupting():
    assert can_interrupt(InteractionState(interruptible=True)) is True
    assert can_interrupt(InteractionState(interruptible=False)) is False
    assert can_interrupt(InteractionState(interruptible=True, interrupting=True)) is False


def test_esc_rewind_gate_requires_empty_prompt_and_idle_enough():
    assert can_open_rewind_from_esc(InteractionState(), True) is True
    assert can_open_rewind_from_esc(InteractionState(), False) is False
    assert can_open_rewind_from_esc(InteractionState(sending=True), True) is False
    assert can_open_rewind_from_esc(InteractionState(transaction="rewind"), True) is False
    assert can_open_rewind_from_esc(
        InteractionState(local_command_pending=True), True
    ) is False
    assert can_open_rewind_from_esc(
        InteractionState(permission_prompt=object()), True
    ) is False
