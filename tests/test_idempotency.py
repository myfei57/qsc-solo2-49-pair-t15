"""Idempotency guard for retried operator commands."""

from __future__ import annotations

from datetime import timedelta

from linecontrol.domain.records import utcnow
from linecontrol.line.idempotency import IdempotencyGuard


def test_first_command_runs_and_retry_is_held_back() -> None:
    guard = IdempotencyGuard()
    first = guard.begin("coil-change")
    assert first.allow is True
    assert first.duplicate is False

    retry = guard.begin("coil-change")
    assert retry.allow is False
    assert retry.duplicate is True
    assert guard.snapshot()["duplicates"] == 1


def test_retry_is_allowed_after_a_failure() -> None:
    guard = IdempotencyGuard()
    assert guard.begin("coil-change").allow is True
    guard.discard("coil-change")
    assert guard.begin("coil-change").allow is True


def test_completed_command_blocks_retries_until_the_window_passes() -> None:
    guard = IdempotencyGuard(bare_ttl=timedelta(seconds=30))
    assert guard.begin("coil-change").allow is True
    guard.commit("coil-change")
    assert guard.begin("coil-change").duplicate is True

    seen = guard._seen[("coil-change", "")]
    seen.completed_at = utcnow() - timedelta(seconds=31)
    assert guard.begin("coil-change").allow is True


def test_same_token_retries_are_blocked_long_after_completion() -> None:
    guard = IdempotencyGuard(
        token_ttl=timedelta(minutes=10),
        bare_ttl=timedelta(seconds=1),
    )
    assert guard.begin("coil-change", "cmd-1").allow is True
    guard.commit("coil-change", "cmd-1")

    seen = guard._seen[("coil-change", "cmd-1")]
    seen.completed_at = utcnow() - timedelta(seconds=5)
    assert guard.begin("coil-change", "cmd-1").duplicate is True


def test_distinct_tokens_run_independently() -> None:
    guard = IdempotencyGuard()
    assert guard.begin("coil-change", "cmd-1").allow is True
    guard.commit("coil-change", "cmd-1")
    assert guard.begin("coil-change", "cmd-2").allow is True


def test_different_operations_do_not_shadow_each_other() -> None:
    guard = IdempotencyGuard()
    assert guard.begin("coil-change").allow is True
    guard.commit("coil-change")
    assert guard.begin("taper-exit").allow is True
