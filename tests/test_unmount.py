"""Characterization: disconnect on unmount.

Pins CastcodeApp.on_unmount -> _disconnect_client -> Session.disconnect():

    async def _disconnect_client(self) -> None:
        await self.conversation.session.disconnect()

- when a client is set, on_unmount calls client.disconnect() exactly once;
- the None guard now lives in Session.disconnect() (`if self.client is not None`),
  so on_unmount does NOT crash and does NOT call disconnect when the session has no
  client.
"""

from textual import work

from castcode.app import CastcodeApp
from castcode.session import Session

import fixtures as fx


async def _noop(self):
    pass


class CountingFake(fx.BurstFakeClient):
    """BurstFakeClient that counts disconnect() invocations."""

    def __init__(self, messages=()):
        super().__init__(messages)
        self.disconnect_calls = 0

    async def disconnect(self):
        self.disconnect_calls += 1
        await super().disconnect()


def test_on_unmount_calls_disconnect_once_via_run_test_exit(monkeypatch):
    """Letting the run_test block exit unmounts the app -> exactly one disconnect."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    client = CountingFake()

    async def body():
        async with app.run_test():
            app.conversation.session.client = client
            assert client.disconnect_calls == 0  # not yet, app still mounted

    import asyncio
    asyncio.run(body())

    assert client.disconnect_calls == 1
    assert client.disconnected is True


async def test_on_unmount_direct_invocation(monkeypatch):
    """Calling on_unmount() directly while mounted disconnects exactly once."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        client = CountingFake()
        app.conversation.session.client = client
        await app.on_unmount()
        await pilot.pause()
        assert client.disconnect_calls == 1
        # Prevent the run_test exit-unmount from disconnecting a second time on
        # THIS client; clear it so the guard short-circuits at teardown.
        app.conversation.session.client = None
    # After teardown the count is still 1 (we nulled the client before exit).
    assert client.disconnect_calls == 1


async def test_on_unmount_none_client_no_crash_no_call(monkeypatch):
    """The None guard: on_unmount with no client neither crashes nor disconnects."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        app.conversation.session.client = None
        # Must not raise (Session.disconnect's `if self.client is not None` guard).
        await app.on_unmount()
        assert app.conversation.session.client is None


def test_run_test_exit_with_none_client_does_not_crash(monkeypatch):
    """Full lifecycle with client never set: exit-unmount runs the guard cleanly."""
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()

    async def body():
        async with app.run_test():
            app.conversation.session.client = None

    import asyncio
    asyncio.run(body())  # would raise if the guard were absent / unmount crashed


def test_on_unmount_is_the_disconnect_handler():
    """Pin that on_unmount is the method doing the work, and a fresh Session has no
    client until connect() creates one (the None guard's default)."""
    assert hasattr(CastcodeApp, "on_unmount")
    assert Session().client is None


async def test_quit_during_connect_swallows_worker_cancellation(monkeypatch):
    import asyncio

    async def _slow(self):
        await asyncio.sleep(60)

    monkeypatch.setattr(CastcodeApp, "connect", work(_slow))
    app = CastcodeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app.conversation.connect_worker.is_finished
        await app.action_quit()
    # Exiting run_test drives shutdown: Textual cancels the in-flight connect
    # worker before Unmount, and on_unmount must absorb WorkerCancelled instead
    # of crashing the exit path.
