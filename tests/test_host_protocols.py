"""Enforces the flow-host contracts: CastcodeApp must satisfy the ConversationHost and
PermissionHost Protocols the flow modules type their entry points against.

The Protocols are runtime_checkable documentation of what conversation.py /
permissions.py reach for off the app. With no type checker in the repo, this test
is what keeps them honest: rename or drop a member CastcodeApp exposes (a real risk
once conversation switching moves state onto Conversation) and the contract fails
here instead of silently drifting.
"""

from textual import work

from castcode.app import CastcodeApp
from castcode.conversation import ConversationHost
from castcode.permissions import PermissionHost


async def _noop(self):
    pass


async def test_castcodeapp_satisfies_host_protocols(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():  # on_mount assigns the instance attrs (conversation, locks, worker)
        for proto in (ConversationHost, PermissionHost):
            assert isinstance(app, proto)  # covers the declared methods
            for member in proto.__annotations__:  # covers the declared data attributes
                assert hasattr(app, member), f"{proto.__name__}: CastcodeApp missing '{member}'"
