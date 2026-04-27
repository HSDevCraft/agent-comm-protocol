"""
Failure Handling — Production-grade resilience primitives

Covers:
  - RetryHandler:          exponential backoff with jitter
  - FallbackChain:         ordered list of fallback strategies
  - CircuitBreaker:        open/half-open/closed state machine per agent endpoint
  - DeadLetterQueue:       captures unprocessable messages for later inspection
  - HumanEscalationHook:   fires when automated recovery is exhausted
  - FailureOrchestrator:   wires all primitives together for an agent
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, TypeVar

from src._logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")
AsyncFn = Callable[..., Coroutine[Any, Any, T]]


@dataclass
class RetryConfig:
    max_retries: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    jitter_fraction: float = 0.1
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)

    def compute_delay(self, attempt: int) -> float:
        """Exponential backoff with random jitter."""
        delay = min(
            self.base_delay_seconds * (self.backoff_multiplier ** attempt),
            self.max_delay_seconds,
        )
        jitter = delay * self.jitter_fraction * random.random()
        return delay + jitter


class RetryHandler:
    """
    Exponential backoff retry handler.

    Wraps any async callable and retries it on failure up to `max_retries` times.
    Between attempts, waits with exponential backoff + jitter to avoid thundering herd.

    WHY jitter: Without jitter, all retrying agents wake at the same time
    after a transient failure, flooding the recovering service again.
    """

    def __init__(self, config: RetryConfig | None = None) -> None:
        self.config = config or RetryConfig()
        self._retry_stats: dict[str, int] = {"attempts": 0, "successes": 0, "failures": 0}

    async def execute(
        self,
        fn: AsyncFn,
        *args: Any,
        operation_name: str = "operation",
        **kwargs: Any,
    ) -> Any:
        last_exception: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            self._retry_stats["attempts"] += 1
            try:
                result = await fn(*args, **kwargs)
                self._retry_stats["successes"] += 1
                if attempt > 0:
                    logger.info(
                        "retry_succeeded",
                        operation=operation_name,
                        attempt=attempt,
                    )
                return result
            except self.config.retryable_exceptions as exc:
                last_exception = exc
                if attempt < self.config.max_retries:
                    delay = self.config.compute_delay(attempt)
                    logger.warning(
                        "retry_attempt",
                        operation=operation_name,
                        attempt=attempt + 1,
                        max_retries=self.config.max_retries,
                        delay_s=round(delay, 2),
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)

        self._retry_stats["failures"] += 1
        logger.error(
            "retry_exhausted",
            operation=operation_name,
            total_attempts=self.config.max_retries + 1,
            error=str(last_exception),
        )
        raise last_exception  # type: ignore[misc]

    def stats(self) -> dict[str, int]:
        return dict(self._retry_stats)


@dataclass
class FallbackOption:
    """A single fallback strategy in the chain."""
    name: str
    fn: AsyncFn
    description: str
    condition: Callable[[Exception], bool] = field(default=lambda _: True)


class FallbackChain:
    """
    Ordered fallback chain — tries each strategy in order until one succeeds.

    Use cases:
    - Primary: call specialist agent → Fallback: call general agent → Fallback: local LLM
    - Primary: real-time API → Fallback: cached data → Fallback: static defaults
    - Primary: vector search → Fallback: keyword search → Fallback: "I don't know"

    WHY: Graceful degradation is better than total failure.
    Users get partial results rather than errors.
    """

    def __init__(self, options: list[FallbackOption]) -> None:
        self._options = options
        self._attempt_counts: dict[str, int] = {o.name: 0 for o in options}
        self._success_at: dict[str, int] = {}

    async def execute(self, context: dict[str, Any] | None = None) -> Any:
        ctx = context or {}
        last_exc: Exception | None = None

        for i, option in enumerate(self._options):
            self._attempt_counts[option.name] += 1
            try:
                logger.info("fallback_trying", option=option.name, index=i)
                result = await option.fn(ctx)
                self._success_at[option.name] = self._success_at.get(option.name, 0) + 1
                logger.info("fallback_succeeded", option=option.name, index=i)
                return result
            except Exception as exc:
                last_exc = exc
                if not option.condition(exc):
                    continue
                logger.warning(
                    "fallback_option_failed",
                    option=option.name,
                    index=i,
                    error=str(exc),
                )

        logger.error("fallback_chain_exhausted", tried=len(self._options))
        raise RuntimeError(
            f"All {len(self._options)} fallback options exhausted. Last error: {last_exc}"
        )

    def stats(self) -> dict[str, Any]:
        return {
            "options": len(self._options),
            "attempt_counts": self._attempt_counts,
            "success_counts": self._success_at,
        }


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout_seconds: float = 60.0
    half_open_max_calls: int = 3


class CircuitBreaker:
    """
    Circuit Breaker — protects downstream agents/services from cascading failures.

    State machine:
      CLOSED → (failure_threshold failures) → OPEN
      OPEN   → (timeout_seconds elapsed)   → HALF_OPEN
      HALF_OPEN → (success_threshold OK)   → CLOSED
      HALF_OPEN → (any failure)            → OPEN

    WHY: When an agent is failing, continuing to call it wastes resources
    and prolongs recovery. The circuit breaker "trips" and redirects traffic
    until the downstream service recovers.
    """

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_calls = 0
        self._total_calls = 0
        self._total_failures = 0
        self._total_opens = 0

    def is_open(self) -> bool:
        if self.state == CircuitState.OPEN:
            if self._last_failure_time and (time.time() - self._last_failure_time) >= self.config.timeout_seconds:
                self._transition(CircuitState.HALF_OPEN)
                return False
            return True
        return False

    async def call(self, fn: AsyncFn, *args: Any, **kwargs: Any) -> Any:
        self._total_calls += 1

        if self.is_open():
            raise RuntimeError(
                f"CircuitBreaker '{self.name}' is OPEN — calls blocked. "
                f"Retry after {self.config.timeout_seconds}s."
            )

        if self.state == CircuitState.HALF_OPEN:
            if self._half_open_calls >= self.config.half_open_max_calls:
                raise RuntimeError(f"CircuitBreaker '{self.name}' HALF_OPEN — max probe calls reached.")
            self._half_open_calls += 1

        try:
            result = await fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        self._failure_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.config.success_threshold:
                self._transition(CircuitState.CLOSED)
        elif self.state == CircuitState.CLOSED:
            pass

    def _on_failure(self) -> None:
        self._total_failures += 1
        self._failure_count += 1
        self._last_failure_time = time.time()
        self._success_count = 0

        if self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
            if self._failure_count >= self.config.failure_threshold:
                self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        old_state = self.state
        self.state = new_state
        if new_state == CircuitState.OPEN:
            self._total_opens += 1
        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._success_count = 0
        if new_state == CircuitState.CLOSED:
            self._failure_count = 0
        logger.warning(
            "circuit_breaker_transition",
            name=self.name,
            from_state=old_state.value,
            to_state=new_state.value,
        )

    def stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "total_calls": self._total_calls,
            "total_failures": self._total_failures,
            "total_opens": self._total_opens,
            "last_failure_at": self._last_failure_time,
        }


@dataclass
class DeadLetterEntry:
    entry_id: str
    timestamp: float
    source: str
    message: dict[str, Any]
    error: str
    retry_count: int
    resolved: bool = False


class DeadLetterQueue:
    """
    Dead Letter Queue — captures messages/tasks that could not be processed
    after all retry and fallback attempts.

    WHY: Unprocessable messages must not be silently dropped. The DLQ:
    - Preserves the original message for manual inspection
    - Allows ops teams to reprocess or escalate
    - Provides visibility into systemic failures
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._entries: list[DeadLetterEntry] = []
        self.max_size = max_size

    def enqueue(
        self,
        source: str,
        message: dict[str, Any],
        error: str,
        retry_count: int = 0,
    ) -> DeadLetterEntry:
        entry = DeadLetterEntry(
            entry_id=f"dlq-{uuid.uuid4().hex[:8]}",
            timestamp=time.time(),
            source=source,
            message=message,
            error=error,
            retry_count=retry_count,
        )
        if len(self._entries) >= self.max_size:
            self._entries.pop(0)
        self._entries.append(entry)
        logger.error(
            "dead_letter_enqueued",
            entry_id=entry.entry_id,
            source=source,
            error=error[:100],
        )
        return entry

    def resolve(self, entry_id: str) -> bool:
        for entry in self._entries:
            if entry.entry_id == entry_id:
                entry.resolved = True
                logger.info("dead_letter_resolved", entry_id=entry_id)
                return True
        return False

    def unresolved(self) -> list[DeadLetterEntry]:
        return [e for e in self._entries if not e.resolved]

    def stats(self) -> dict[str, Any]:
        return {
            "total": len(self._entries),
            "unresolved": len(self.unresolved()),
            "resolved": sum(1 for e in self._entries if e.resolved),
        }


EscalationHandler = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]


class HumanEscalationHook:
    """
    Human Escalation Hook — fires when automated recovery is exhausted.

    In production: sends to PagerDuty, Slack, email, ticketing system, etc.
    Here: logs and invokes registered handlers.

    WHY: Some failures require human judgment. The escalation hook ensures
    humans are notified with full context rather than discovering failures passively.
    """

    def __init__(self, system_name: str = "multi-agent-system") -> None:
        self.system_name = system_name
        self._handlers: list[EscalationHandler] = []
        self._escalations: list[dict[str, Any]] = []

    def register_handler(self, handler: EscalationHandler) -> None:
        self._handlers.append(handler)

    async def escalate(
        self,
        reason: str,
        context: dict[str, Any],
        severity: str = "high",
    ) -> None:
        escalation_id = f"esc-{uuid.uuid4().hex[:8]}"
        record = {
            "escalation_id": escalation_id,
            "timestamp": time.time(),
            "system": self.system_name,
            "severity": severity,
            "reason": reason,
            "context": context,
        }
        self._escalations.append(record)

        logger.critical(
            "human_escalation_triggered",
            escalation_id=escalation_id,
            severity=severity,
            reason=reason[:200],
        )

        for handler in self._handlers:
            try:
                await handler(reason, context)
            except Exception as exc:
                logger.error("escalation_handler_failed", error=str(exc))

    def count(self) -> int:
        return len(self._escalations)

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        return self._escalations[-n:]


class FailureOrchestrator:
    """
    Wires RetryHandler + CircuitBreaker + FallbackChain + DeadLetterQueue
    + HumanEscalationHook into a single coherent failure strategy per operation.

    Usage:
        fo = FailureOrchestrator("finance-agent")
        result = await fo.execute(my_async_fn, arg1, arg2, operation="fetch_data")
    """

    def __init__(
        self,
        agent_id: str,
        retry_config: RetryConfig | None = None,
        circuit_config: CircuitBreakerConfig | None = None,
        fallback_options: list[FallbackOption] | None = None,
        escalation_hook: HumanEscalationHook | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.retry = RetryHandler(retry_config)
        self.circuit = CircuitBreaker(agent_id, circuit_config)
        self.fallback = FallbackChain(fallback_options or []) if fallback_options else None
        self.dlq = DeadLetterQueue()
        self.escalation = escalation_hook or HumanEscalationHook(agent_id)
        self._executions = 0
        self._failures = 0

    async def execute(
        self,
        fn: AsyncFn,
        *args: Any,
        operation: str = "operation",
        message: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute fn with full resilience stack:
        1. Circuit breaker check (fail-fast if endpoint is known-bad)
        2. Retry with exponential backoff
        3. Fallback chain if retries exhausted
        4. Dead letter queue if fallback also fails
        5. Human escalation if DLQ threshold exceeded
        """
        self._executions += 1
        msg = message or {"operation": operation, "args_count": len(args)}

        try:
            return await self.circuit.call(
                self.retry.execute, fn, *args, operation_name=operation, **kwargs
            )
        except RuntimeError as circuit_exc:
            logger.warning("circuit_open_blocking", operation=operation, error=str(circuit_exc))
            if self.fallback:
                try:
                    return await self.fallback.execute({"operation": operation, **msg})
                except RuntimeError as fallback_exc:
                    pass
            self._failures += 1
            self.dlq.enqueue(self.agent_id, msg, str(circuit_exc))
            if len(self.dlq.unresolved()) > 10:
                await self.escalation.escalate(
                    reason=f"Circuit breaker open for '{operation}' and fallback exhausted",
                    context={"agent": self.agent_id, "dlq_size": self.dlq.stats()["unresolved"]},
                    severity="critical",
                )
            raise

        except Exception as exc:
            self._failures += 1
            logger.error("failure_orchestrator_error", operation=operation, error=str(exc))
            self.dlq.enqueue(self.agent_id, msg, str(exc))
            if len(self.dlq.unresolved()) > 5:
                await self.escalation.escalate(
                    reason=f"Repeated failures for operation '{operation}'",
                    context={"agent": self.agent_id, "error": str(exc), "dlq_unresolved": len(self.dlq.unresolved())},
                    severity="high",
                )
            raise

    def overall_stats(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "total_executions": self._executions,
            "total_failures": self._failures,
            "success_rate": round(1 - (self._failures / max(self._executions, 1)), 3),
            "retry_stats": self.retry.stats(),
            "circuit_stats": self.circuit.stats(),
            "dlq_stats": self.dlq.stats(),
            "escalations": self.escalation.count(),
        }
