"""Deduplication of re-sent operator commands."""

from __future__ import annotations

from datetime import timedelta

import pytest

from linecontrol.console.idempotency import DuplicateCommand, Idempotency


def test_a_repeated_command_id_runs_once() -> None:
    idem = Idempotency()
    calls = 0

    def action() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True, "position": 120.0}

    first, replay = idem.run("coil-change", "cmd-1", action)
    assert replay is False
    assert first == {"ok": True, "position": 120.0}
    second, replay = idem.run("coil-change", "cmd-1", action)
    assert replay is True
    assert second == {"ok": True, "position": 120.0}
    third, replay = idem.run("coil-change", "cmd-1", action)
    assert replay is True
    assert calls == 1


def test_replay_result_is_a_copy_not_shared_state() -> None:
    idem = Idempotency()
    stored = {"ok": True, "n": 1}
    idem.run("coil-change", "cmd-1", lambda: stored)
    replay, _ = idem.run("coil-change", "cmd-1", lambda: {})
    replay["n"] = 99
    again, _ = idem.run("coil-change", "cmd-1", lambda: {})
    assert again["n"] == 1


def test_distinct_ids_and_scopes_run_separately() -> None:
    idem = Idempotency()
    calls = 0

    def action() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    idem.run("coil-change", "a", action)
    idem.run("coil-change", "b", action)
    idem.run("other-scope", "a", action)
    assert calls == 3


def test_a_failing_command_stays_retryable() -> None:
    idem = Idempotency()
    attempts = 0

    def flaky() -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("track stale")
        return {"ok": True}

    with pytest.raises(RuntimeError):
        idem.run("coil-change", "cmd-9", flaky)
    result, replay = idem.run("coil-change", "cmd-9", flaky)
    assert result == {"ok": True}
    assert replay is False
    assert attempts == 2


def test_a_command_still_running_is_rejected() -> None:
    idem = Idempotency()

    def slow() -> dict[str, object]:
        return {"ok": True}

    idem._running["coil-change:cmd-2"] = "coil-change"
    with pytest.raises(DuplicateCommand) as caught:
        idem.run("coil-change", "cmd-2", slow)
    assert caught.value.command_id == "cmd-2"
    # the marker clears once the first run ends
    idem._running.clear()
    _, replay = idem.run("coil-change", "cmd-2", slow)
    assert replay is False


def test_records_expire_after_the_window() -> None:
    idem = Idempotency(window=timedelta(seconds=0))
    calls = 0

    def action() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    idem.run("coil-change", "old", action)
    _, replay = idem.run("coil-change", "old", action)
    assert replay is False
    assert calls == 2


def test_retained_records_are_bounded() -> None:
    idem = Idempotency(limit=3)
    for index in range(5):
        idem.run("coil-change", f"cmd-{index}", lambda: {"ok": True})
    assert len(idem._done) == 3
    # oldest entries were dropped and run again
    _, replay = idem.run("coil-change", "cmd-4", lambda: {"ok": True})
    assert replay is True
    _, replay = idem.run("coil-change", "cmd-0", lambda: {"ok": True})
    assert replay is False
