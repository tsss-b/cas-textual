"""Characterization: Esc priority for idle rewind and in-flight interrupts."""

import pytest
import asyncio

from textual import work
from textual.actions import SkipAction

import castcode.app
from castcode.app import CastcodeApp
from castcode.ui.input import Prompt
from castcode.ui.pickers import RewindPicker


async def _noop(self):
    pass


class RecordingFake:
    """Minimal fake client that records interrupt() calls (none expected)."""

    def __init__(self):
        self.interrupt_calls = 0
        self.disconnected = False

    async def interrupt(self):
        self.interrupt_calls += 1

    async def disconnect(self):
        self.disconnected = True


class RaisingInterruptFake(RecordingFake):
    async def interrupt(self):
        self.interrupt_calls += 1
        raise RuntimeError("interrupt failed")


class HangingInterruptFake(RecordingFake):
    async def interrupt(self):
        self.interrupt_calls += 1
        await asyncio.Event().wait()


# --- action_interrupt called directly (await) -------------------------------

async def test_action_interrupt_idle_arms_rewind(monkeypatch):
    """Fresh idle empty prompt: Esc arms rewind, no interrupt call."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        client = RecordingFake()
        app.conversation.session.client = client
        # default idle state
        assert app._can_interrupt is False
        assert app._interrupting is False

        await app.action_interrupt()

        assert client.interrupt_calls == 0
        assert app._interrupting is False
        assert app._can_interrupt is False
        assert app.interaction.rewind_armed is True


async def test_rewind_arm_timer_expires(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        await app.action_interrupt()
        assert app.interaction.rewind_armed is True

        for _ in range(100):
            if not app.interaction.rewind_armed:
                break
            await asyncio.sleep(0.05)
        await pilot.pause()

        assert app.interaction.rewind_armed is False


async def test_stale_rewind_timer_cannot_clear_fresh_arm(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        app._arm_rewind()
        stale_generation = app.interaction.rewind_generation
        app._arm_rewind()

        app._expire_rewind(stale_generation)

        assert app.interaction.rewind_armed is True


async def test_nonempty_prompt_disarms_rewind(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        app._arm_rewind()
        app.query_one(Prompt).insert("draft")

        with pytest.raises(SkipAction):
            await app.action_interrupt()

        assert app.interaction.rewind_armed is False


async def test_sending_state_disarms_rewind(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        app._arm_rewind()
        app._sending = True

        with pytest.raises(SkipAction):
            await app.action_interrupt()

        assert app.interaction.rewind_armed is False


async def test_action_interrupt_connected_only_arms_rewind(monkeypatch):
    """Connected but idle empty prompt: Esc arms rewind, no interrupt call."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        client = RecordingFake()
        app.conversation.session.client = client
        app._connected_ok = True
        # _can_interrupt remains False since no send() turn is active
        assert app._can_interrupt is False

        await app.action_interrupt()

        assert client.interrupt_calls == 0
        assert app._interrupting is False
        assert app.interaction.rewind_armed is True


async def test_second_idle_escape_opens_rewind_picker(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        await app.action_interrupt()
        await app.action_interrupt()
        await pilot.pause()

        assert app.interaction.picker_open is True
        assert app.query_one(RewindPicker).rows == []


async def test_action_interrupt_resets_interrupting_when_client_interrupt_raises(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        client = RaisingInterruptFake()
        app.conversation.session.client = client
        app._can_interrupt = True

        with pytest.raises(RuntimeError, match="interrupt failed"):
            await app.action_interrupt()

        assert client.interrupt_calls == 1
        assert app._interrupting is False
        assert app._can_interrupt is True


async def test_action_interrupt_resets_interrupting_when_client_interrupt_times_out(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    monkeypatch.setattr(castcode.app, "_INTERRUPT_TIMEOUT", 0.01)
    app = CastcodeApp()
    async with app.run_test():
        client = HangingInterruptFake()
        app.conversation.session.client = client
        app._can_interrupt = True

        with pytest.raises(TimeoutError):
            await app.action_interrupt()

        assert client.interrupt_calls == 1
        assert app._interrupting is False
        assert app._can_interrupt is True


# --- Esc pressed through the pilot ------------------------------------------

async def test_escape_press_idle_does_not_interrupt(monkeypatch):
    """pilot.press('escape') while idle arms rewind and never interrupts."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        client = RecordingFake()
        app.conversation.session.client = client
        assert app._can_interrupt is False

        await pilot.press("escape")
        await pilot.pause()

        assert client.interrupt_calls == 0
        assert app._interrupting is False
        assert app._can_interrupt is False


async def test_escape_press_connected_only_does_not_interrupt(monkeypatch):
    """Connected, no turn: Esc still must not interrupt."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        client = RecordingFake()
        app.conversation.session.client = client
        app._connected_ok = True

        await pilot.press("escape")
        await pilot.pause()

        assert client.interrupt_calls == 0
        assert app._interrupting is False


async def test_repeated_escape_idle_never_interrupts(monkeypatch):
    """Multiple Esc presses while idle never call client interrupt."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        client = RecordingFake()
        app.conversation.session.client = client

        for _ in range(3):
            await pilot.press("escape")
            await pilot.pause()

        assert client.interrupt_calls == 0
        assert app._interrupting is False
