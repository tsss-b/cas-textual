"""Characterization: activity line + spinner (coverage gap — app.py shell).

Pins the four state-selected labels in CastcodeApp._tick and the format_elapsed formatting,
plus that _stop_activity blanks the row. These live in the CastcodeApp shell (app.py) and a
refactor could reorder the if/elif label ladder undetected.
"""

from textual import work

import castcode.format as fmt
from castcode.app import CastcodeApp


async def _noop(self):
    pass


# --- format_elapsed boundaries (pure unit) ----------------------------------

def test_fmt_elapsed_seconds_and_minute_boundary():
    assert fmt.format_elapsed(0) == "0s"
    assert fmt.format_elapsed(5) == "5s"
    assert fmt.format_elapsed(59) == "59s"
    assert fmt.format_elapsed(60) == "1m 00s"
    assert fmt.format_elapsed(65) == "1m 05s"
    assert fmt.format_elapsed(125) == "2m 05s"


# --- state-dependent labels --------------------------------------------------

def _label(app):
    return str(app._activity.render())


async def test_activity_labels_by_state(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))
    app = CastcodeApp()
    async with app.run_test():
        app._start_activity()  # sets _spin / _activity_start and renders once

        # Not connected yet -> Connecting…
        app._connected_ok = False
        app._can_interrupt = False
        app._interrupting = False
        app._tick()
        assert "Connecting…" in _label(app)
        assert "esc to interrupt" not in _label(app)

        # Connected, turn not interruptible -> Working… WITHOUT the esc hint
        app._connected_ok = True
        app._tick()
        assert "Working…" in _label(app)
        assert "esc to interrupt" not in _label(app)

        # Interruptible -> Working… WITH the esc hint (hint shows only here)
        app._can_interrupt = True
        app._tick()
        assert "Working…" in _label(app)
        assert "esc to interrupt" in _label(app)

        # Interrupting wins over everything -> Stopping…
        app._interrupting = True
        app._tick()
        assert "Stopping…" in _label(app)
        assert "esc to interrupt" not in _label(app)

        # Stop blanks the row.
        app._stop_activity()
        assert _label(app) == ""


async def test_activity_tick_ignores_missing_row_during_teardown(monkeypatch):
    monkeypatch.setattr(CastcodeApp, "connect", work(_noop))

    class _Timer:
        stopped = False

        def stop(self):
            self.stopped = True

    app = CastcodeApp()
    async with app.run_test() as pilot:
        app._start_activity()
        app._activity_timer.stop()
        await app._activity.remove()
        await pilot.pause()

        timer = _Timer()
        app._activity_timer = timer
        app._tick()

        assert timer.stopped is True
        assert app._activity_timer is None
