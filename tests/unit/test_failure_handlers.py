"""
Unit tests for Failure Resilience — Retry, CircuitBreaker, Fallback, DLQ, Escalation
"""
from __future__ import annotations

import asyncio
import time
import pytest

from src.failure.handlers import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    DeadLetterQueue,
    FailureOrchestrator,
    FallbackChain,
    FallbackOption,
    HumanEscalationHook,
    RetryConfig,
    RetryHandler,
)


# ── RetryHandler Tests ────────────────────────────────────────────────────────

class TestRetryHandler:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self, retry_config_fast):
        handler = RetryHandler(retry_config_fast)
        async def always_ok(x):
            return x * 2
        result = await handler.execute(always_ok, 21, operation_name="ok_op")
        assert result == 42
        assert handler.stats()["successes"] == 1
        assert handler.stats()["attempts"] == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self, retry_config_fast):
        call_count = 0
        async def flaky(x):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("transient")
            return x + 10

        handler = RetryHandler(RetryConfig(max_retries=3, base_delay_seconds=0.001))
        result = await handler.execute(flaky, 5, operation_name="flaky")
        assert result == 15
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self, retry_config_fast):
        async def always_fails():
            raise ValueError("permanent error")

        handler = RetryHandler(retry_config_fast)
        with pytest.raises(ValueError, match="permanent error"):
            await handler.execute(always_fails, operation_name="always_fails")

    @pytest.mark.asyncio
    async def test_stats_tracks_failures(self, retry_config_fast):
        handler = RetryHandler(retry_config_fast)
        async def fail():
            raise RuntimeError("x")
        try:
            await handler.execute(fail, operation_name="f")
        except RuntimeError:
            pass
        assert handler.stats()["failures"] == 1

    def test_exponential_delay_increases(self):
        config = RetryConfig(base_delay_seconds=0.1, backoff_multiplier=2.0, jitter_fraction=0.0)
        d0 = config.compute_delay(0)
        d1 = config.compute_delay(1)
        d2 = config.compute_delay(2)
        assert d1 > d0
        assert d2 > d1

    def test_delay_capped_at_max(self):
        config = RetryConfig(
            base_delay_seconds=1.0,
            backoff_multiplier=10.0,
            max_delay_seconds=5.0,
            jitter_fraction=0.0,
        )
        delay = config.compute_delay(10)
        assert delay <= 5.0


# ── CircuitBreaker Tests ──────────────────────────────────────────────────────

class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_starts_closed(self, circuit_config_sensitive):
        cb = CircuitBreaker("test", circuit_config_sensitive)
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_success_stays_closed(self, circuit_config_sensitive):
        cb = CircuitBreaker("test", circuit_config_sensitive)
        async def ok():
            return "ok"
        result = await cb.call(ok)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self, circuit_config_sensitive):
        cb = CircuitBreaker("test", circuit_config_sensitive)
        async def fail():
            raise RuntimeError("error")

        for _ in range(circuit_config_sensitive.failure_threshold):
            try:
                await cb.call(fail)
            except RuntimeError:
                pass

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_blocks_calls(self, circuit_config_sensitive):
        cb = CircuitBreaker("test", circuit_config_sensitive)
        async def fail():
            raise RuntimeError("x")

        for _ in range(circuit_config_sensitive.failure_threshold):
            try:
                await cb.call(fail)
            except RuntimeError:
                pass

        assert cb.state == CircuitState.OPEN
        with pytest.raises(RuntimeError, match="OPEN"):
            await cb.call(fail)

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self, circuit_config_sensitive):
        cb = CircuitBreaker("test", circuit_config_sensitive)
        async def fail():
            raise RuntimeError("x")

        for _ in range(circuit_config_sensitive.failure_threshold):
            try:
                await cb.call(fail)
            except RuntimeError:
                pass

        assert cb.state == CircuitState.OPEN
        # Simulate timeout elapsed
        cb._last_failure_time = time.time() - (circuit_config_sensitive.timeout_seconds + 1)
        assert cb.is_open() is False
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_recovers_to_closed_from_half_open(self, circuit_config_sensitive):
        cb = CircuitBreaker("test", circuit_config_sensitive)
        async def fail():
            raise RuntimeError("x")
        async def succeed():
            return "ok"

        for _ in range(circuit_config_sensitive.failure_threshold):
            try:
                await cb.call(fail)
            except RuntimeError:
                pass

        cb._last_failure_time = time.time() - (circuit_config_sensitive.timeout_seconds + 1)
        cb.is_open()  # triggers transition to HALF_OPEN

        for _ in range(circuit_config_sensitive.success_threshold):
            await cb.call(succeed)

        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_stats_structure(self, circuit_config_sensitive):
        cb = CircuitBreaker("test", circuit_config_sensitive)
        stats = cb.stats()
        assert stats["name"] == "test"
        assert stats["state"] == CircuitState.CLOSED.value
        assert "total_calls" in stats
        assert "total_failures" in stats


# ── FallbackChain Tests ───────────────────────────────────────────────────────

class TestFallbackChain:
    @pytest.mark.asyncio
    async def test_first_option_succeeds(self):
        async def option1(ctx):
            return {"source": "option1"}

        chain = FallbackChain([
            FallbackOption("opt1", option1, "First option"),
        ])
        result = await chain.execute({"input": "data"})
        assert result == {"source": "option1"}

    @pytest.mark.asyncio
    async def test_falls_through_to_second(self):
        async def fail(ctx):
            raise RuntimeError("first failed")

        async def succeed(ctx):
            return {"source": "option2"}

        chain = FallbackChain([
            FallbackOption("opt1", fail, "First"),
            FallbackOption("opt2", succeed, "Second"),
        ])
        result = await chain.execute({})
        assert result["source"] == "option2"

    @pytest.mark.asyncio
    async def test_all_options_fail_raises(self):
        async def fail(ctx):
            raise RuntimeError("all failed")

        chain = FallbackChain([
            FallbackOption("opt1", fail, "Option 1"),
            FallbackOption("opt2", fail, "Option 2"),
        ])
        with pytest.raises(RuntimeError, match="all.*options exhausted"):
            await chain.execute({})

    @pytest.mark.asyncio
    async def test_stats_tracks_attempts(self):
        async def succeed(ctx):
            return {}

        chain = FallbackChain([FallbackOption("o", succeed, "opt")])
        await chain.execute({})
        stats = chain.stats()
        assert stats["attempt_counts"]["o"] == 1


# ── DeadLetterQueue Tests ─────────────────────────────────────────────────────

class TestDeadLetterQueue:
    def test_enqueue_adds_entry(self):
        dlq = DeadLetterQueue()
        entry = dlq.enqueue("source-agent", {"task": "data"}, "Error message")
        assert entry.source == "source-agent"
        assert entry.error == "Error message"
        assert entry.resolved is False

    def test_unresolved_returns_unresolved(self):
        dlq = DeadLetterQueue()
        e1 = dlq.enqueue("a", {}, "err1")
        e2 = dlq.enqueue("b", {}, "err2")
        dlq.resolve(e1.entry_id)
        unresolved = dlq.unresolved()
        assert len(unresolved) == 1
        assert unresolved[0].entry_id == e2.entry_id

    def test_resolve_returns_true_for_existing(self):
        dlq = DeadLetterQueue()
        entry = dlq.enqueue("a", {}, "err")
        assert dlq.resolve(entry.entry_id) is True
        assert entry.resolved is True

    def test_resolve_returns_false_for_missing(self):
        dlq = DeadLetterQueue()
        assert dlq.resolve("nonexistent-id") is False

    def test_max_size_evicts_oldest(self):
        dlq = DeadLetterQueue(max_size=3)
        for i in range(5):
            dlq.enqueue("a", {}, f"error-{i}")
        assert len(dlq._entries) == 3

    def test_stats(self):
        dlq = DeadLetterQueue()
        e = dlq.enqueue("a", {}, "err")
        dlq.resolve(e.entry_id)
        stats = dlq.stats()
        assert stats["total"] == 1
        assert stats["resolved"] == 1
        assert stats["unresolved"] == 0


# ── HumanEscalationHook Tests ─────────────────────────────────────────────────

class TestHumanEscalationHook:
    @pytest.mark.asyncio
    async def test_escalation_fires_handlers(self):
        hook = HumanEscalationHook("test-system")
        fired = []

        async def my_handler(reason: str, context: dict) -> None:
            fired.append(reason)

        hook.register_handler(my_handler)
        await hook.escalate("Test escalation", {"agent": "test"}, severity="high")
        assert len(fired) == 1
        assert fired[0] == "Test escalation"

    @pytest.mark.asyncio
    async def test_escalation_count_increments(self):
        hook = HumanEscalationHook("test")
        assert hook.count() == 0
        await hook.escalate("reason", {})
        assert hook.count() == 1

    @pytest.mark.asyncio
    async def test_recent_returns_last_n(self):
        hook = HumanEscalationHook("test")
        for i in range(5):
            await hook.escalate(f"reason-{i}", {})
        recent = hook.recent(n=3)
        assert len(recent) == 3

    @pytest.mark.asyncio
    async def test_handler_exception_doesnt_crash(self):
        hook = HumanEscalationHook("test")

        async def bad_handler(reason, ctx):
            raise ValueError("handler failed")

        hook.register_handler(bad_handler)
        # Should not raise
        await hook.escalate("test", {})
        assert hook.count() == 1


# ── FailureOrchestrator Tests ─────────────────────────────────────────────────

class TestFailureOrchestrator:
    @pytest.mark.asyncio
    async def test_successful_execution(self):
        fo = FailureOrchestrator(
            "test-agent",
            retry_config=RetryConfig(max_retries=1, base_delay_seconds=0.001),
        )
        async def succeed(x):
            return {"result": x * 2}

        result = await fo.execute(succeed, 21, operation="succeed")
        assert result == {"result": 42}

    @pytest.mark.asyncio
    async def test_retry_then_succeed(self):
        call_count = 0
        fo = FailureOrchestrator(
            "test-agent",
            retry_config=RetryConfig(max_retries=3, base_delay_seconds=0.001),
            circuit_config=CircuitBreakerConfig(failure_threshold=10),
        )

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return {"ok": True}

        result = await fo.execute(flaky, operation="flaky")
        assert result == {"ok": True}
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_stats_structure(self):
        fo = FailureOrchestrator(
            "test-agent",
            retry_config=RetryConfig(max_retries=1, base_delay_seconds=0.001),
        )
        async def ok():
            return {}

        await fo.execute(ok, operation="ok")
        stats = fo.overall_stats()
        assert stats["agent_id"] == "test-agent"
        assert stats["total_executions"] == 1
        assert stats["success_rate"] == 1.0
        assert "circuit_stats" in stats
        assert "dlq_stats" in stats

    @pytest.mark.asyncio
    async def test_fallback_used_when_circuit_open(self):
        async def fallback_fn(ctx):
            return {"fallback": True}

        fo = FailureOrchestrator(
            "test-agent",
            retry_config=RetryConfig(max_retries=1, base_delay_seconds=0.001),
            circuit_config=CircuitBreakerConfig(failure_threshold=1, timeout_seconds=60),
            fallback_options=[
                FallbackOption("fallback", fallback_fn, "Emergency fallback"),
            ],
        )

        async def always_fail():
            raise RuntimeError("always fails")

        # Trigger circuit open
        try:
            await fo.execute(always_fail, operation="fail")
        except Exception:
            pass

        # Circuit should now be open; with fallback it should succeed
        fo.circuit._last_failure_time = time.time() - 100  # don't transition yet
        fo.circuit.state = CircuitState.OPEN

        result = await fo.execute(always_fail, operation="fail_with_fallback")
        assert result == {"fallback": True}
