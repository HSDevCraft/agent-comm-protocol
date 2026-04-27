# Failure Resilience: Deep Dive

## 1. Failure Taxonomy in Multi-Agent Systems

Multi-agent systems fail in ways single-process systems don't:

| Failure Type | Root Cause | Naive Handling | Production Handling |
|-------------|-----------|----------------|---------------------|
| Transient error | Network blip, temporary overload | Crash | Retry with backoff |
| Permanent error | Bug, invalid input, missing capability | Hang | Fail fast + fallback |
| Cascading failure | One agent's failure triggers others | Total outage | Circuit breaker |
| Deadline exceeded | Task too slow | Timeout + crash | Partial result + escalation |
| Silent failure | Agent returns wrong result | Undetected | Confidence scoring + validation |
| Agent unreachable | Agent crashed, network partition | Hang | Circuit breaker + fallback |
| Memory failure | Memory store unavailable | Data loss | Local fallback + async re-sync |

---

## 2. RetryHandler — Exponential Backoff with Jitter

**Why exponential backoff?** If 100 agents all retry at t=1s after a server recovers, you create a thundering herd. Exponential backoff spreads retries over time.

**Why jitter?** Even with backoff, if all clients use the same formula, they retry in sync. Jitter (random noise) desynchronizes retries:

```
Without jitter: retry at 0.5s, 1.0s, 2.0s, 4.0s (all agents simultaneously)
With jitter:    retry at 0.47s, 1.12s, 1.83s, 4.2s (staggered)
```

```python
def compute_delay(self, attempt: int) -> float:
    delay = min(
        self.base_delay_seconds * (self.backoff_multiplier ** attempt),
        self.max_delay_seconds,
    )
    jitter = delay * self.jitter_fraction * random.random()  # 10% random jitter
    return delay + jitter

# Attempts: 0 → ~0.5s, 1 → ~1.0s, 2 → ~2.0s, 3 → ~4.0s (max: 30s)
```

**Usage:**

```python
retry = RetryHandler(RetryConfig(
    max_retries=3,
    base_delay_seconds=0.5,
    backoff_multiplier=2.0,
    jitter_fraction=0.1,
    retryable_exceptions=(ConnectionError, TimeoutError),  # only retry these
))

result = await retry.execute(
    call_finance_agent,
    task_data,
    operation_name="finance_analysis",
)
```

**Retry only specific exceptions:**
```python
# Only retry transient errors, not programming errors
retryable_exceptions=(
    ConnectionError,      # network issues
    TimeoutError,         # slow downstream
    aiohttp.ServerError,  # 5xx responses
    # NOT: ValueError, KeyError, etc. (these are bugs, not transient)
)
```

---

## 3. Circuit Breaker — Fail Fast, Recover Gracefully

The circuit breaker prevents an already-failing agent from being called repeatedly. It "trips" after too many failures and stops all calls until the agent recovers.

```
State Machine:

         failure_count >= threshold
  CLOSED ─────────────────────────► OPEN
    ▲                                 │
    │                                 │ timeout_seconds elapsed
    │                                 ▼
    │              success_count   HALF_OPEN
    └──────────────>= threshold ◄─────┘
                               OR
                           any failure → back to OPEN
```

**Implementation:**

```python
class CircuitBreaker:
    async def call(self, fn, *args, **kwargs):
        if self.is_open():
            raise RuntimeError(f"Circuit OPEN for '{self.name}'")
        
        if self.state == CircuitState.HALF_OPEN:
            # Only allow a few probe calls through
            if self._half_open_calls >= self.config.half_open_max_calls:
                raise RuntimeError("HALF_OPEN max probe calls reached")
            self._half_open_calls += 1
        
        try:
            result = await fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure()  # may trip the breaker
            raise

    def _on_failure(self):
        self._failure_count += 1
        if self._failure_count >= self.config.failure_threshold:
            self._transition(CircuitState.OPEN)  # TRIP!

    def _on_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.config.success_threshold:
                self._transition(CircuitState.CLOSED)  # RECOVER!
```

**Configuration guidelines:**

```python
# For critical agents (finance, legal)
CircuitBreakerConfig(
    failure_threshold=3,      # trip after 3 failures
    success_threshold=2,      # need 2 successes to recover
    timeout_seconds=30.0,     # wait 30s before probing
    half_open_max_calls=3,    # allow 3 probes in half-open
)

# For best-effort agents (search, recommendations)
CircuitBreakerConfig(
    failure_threshold=10,     # more tolerant
    timeout_seconds=10.0,     # recover faster
)
```

**Circuit breaker per agent endpoint:**
```python
# Each downstream agent gets its own circuit breaker
circuit_breakers = {
    "finance-agent": CircuitBreaker("finance-agent"),
    "legal-agent":   CircuitBreaker("legal-agent"),
    "search-tool":   CircuitBreaker("search-tool"),
}

async def call_agent(agent_id: str, task: dict) -> dict:
    cb = circuit_breakers[agent_id]
    return await cb.call(actual_agent_call, agent_id, task)
```

---

## 4. FallbackChain — Graceful Degradation

When the primary path fails, fall through a list of alternatives rather than returning an error:

```python
fallback = FallbackChain([
    FallbackOption(
        name="specialist-agent",
        fn=lambda ctx: call_finance_specialist(ctx),
        description="Primary: specialist finance agent",
    ),
    FallbackOption(
        name="general-agent",
        fn=lambda ctx: call_general_agent(ctx),
        description="Fallback: general purpose agent",
    ),
    FallbackOption(
        name="cached-result",
        fn=lambda ctx: get_cached_answer(ctx),
        description="Fallback: use last cached result",
    ),
    FallbackOption(
        name="static-default",
        fn=lambda ctx: {"result": "Analysis unavailable", "degraded": True},
        description="Last resort: static fallback response",
    ),
])

result = await fallback.execute(context={"query": "Analyze ACME Corp"})
# Returns result of first option that doesn't raise
```

**Conditional fallback** — only fall back on specific exceptions:
```python
FallbackOption(
    name="cached-result",
    fn=lambda ctx: get_cached(ctx),
    condition=lambda exc: isinstance(exc, (TimeoutError, ConnectionError)),
    # Only use cache if it's a network issue, not a data issue
)
```

---

## 5. Dead Letter Queue — Preserve Unprocessable Messages

```python
dlq = DeadLetterQueue(max_size=1000)

# On unrecoverable failure:
entry = dlq.enqueue(
    source="planner-agent",
    message={"task_id": "task-abc", "query": "..."},
    error="All 3 specialist agents timed out",
    retry_count=3,
)

# Ops team can inspect and reprocess:
unresolved = dlq.unresolved()
for entry in unresolved:
    print(f"[{entry.entry_id}] {entry.source}: {entry.error}")
    # Manually reprocess or mark resolved
    dlq.resolve(entry.entry_id)
```

**DLQ integration with monitoring:**
```python
# Alert when DLQ exceeds threshold
if len(dlq.unresolved()) > 10:
    await escalation.escalate(
        reason="DLQ overflow — 10+ unprocessable messages",
        context={"dlq_stats": dlq.stats()},
        severity="critical",
    )
```

---

## 6. Human Escalation Hook

```python
escalation = HumanEscalationHook("production-system")

# Register handlers (PagerDuty, Slack, email, ticketing)
async def send_pagerduty(reason: str, context: dict) -> None:
    await pagerduty_client.trigger_incident(
        summary=f"Agent system escalation: {reason}",
        details=context,
        severity="high",
    )

async def post_slack(reason: str, context: dict) -> None:
    await slack_client.post_message(
        channel="#ai-ops-alerts",
        text=f"🚨 *Escalation*: {reason}\n```{json.dumps(context, indent=2)}```",
    )

escalation.register_handler(send_pagerduty)
escalation.register_handler(post_slack)

# Trigger manually
await escalation.escalate(
    reason="Finance agent down for 15 minutes, 47 tasks queued",
    context={"agent": "finance-agent", "queue_depth": 47, "dlq_size": 12},
    severity="critical",
)
```

---

## 7. FailureOrchestrator — Unified Resilience

The `FailureOrchestrator` wires all primitives together:

```
call fn()
    │
    ▼ CircuitBreaker.call()
    │   is_open? → RuntimeError (blocked)
    │
    ▼ RetryHandler.execute()
    │   attempt 1 → success? → return result ✓
    │   attempt 1 → fail → wait (backoff) → attempt 2 → ...
    │   all retries exhausted → raise last exception
    │
    ▼ (if circuit RuntimeError OR retries exhausted)
      FallbackChain.execute()  (if configured)
    │   option 1 → success? → return result ✓
    │   option 1 → fail → option 2 → ...
    │   all options exhausted → raise
    │
    ▼ DeadLetterQueue.enqueue(original message, error)
    │
    ▼ (if DLQ threshold exceeded)
      HumanEscalationHook.escalate()
```

```python
fo = FailureOrchestrator(
    agent_id="planner-agent",
    retry_config=RetryConfig(max_retries=3, base_delay_seconds=0.5),
    circuit_config=CircuitBreakerConfig(failure_threshold=5),
    fallback_options=[
        FallbackOption("general-fallback", general_agent_call, "Use general agent"),
    ],
    escalation_hook=escalation,
)

result = await fo.execute(
    specialized_agent_call,
    task_data,
    operation="financial_analysis",
    message={"task": task_data},
)

# Check overall health
print(fo.overall_stats())
# {
#   "total_executions": 142,
#   "success_rate": 0.978,
#   "circuit_stats": {"state": "closed", "failure_count": 2},
#   "dlq_stats": {"total": 3, "unresolved": 1},
#   "escalations": 0
# }
```

---

## 8. Timeout Strategies

| Timeout | Where | Recommended value |
|---------|-------|-------------------|
| Individual tool call | MCP client | 5–10 seconds |
| Single agent task | A2A client | 30–60 seconds |
| Workflow step | ACP orchestrator | 60–120 seconds |
| Full workflow | Top-level caller | 5 minutes |
| User-facing request | API gateway | 30 seconds |

```python
# Hierarchical timeouts
async with asyncio.timeout(300):          # 5min: full workflow
    result = await acp.execute_workflow(
        workflow,
        input_data,
    )
    # Each step internally has 60s timeout
    # Each tool call internally has 10s timeout
```

---

## 9. Production Resilience Checklist

- [ ] Every external call wrapped in `RetryHandler`
- [ ] Every downstream agent has a `CircuitBreaker`
- [ ] `FallbackChain` defined for critical operations
- [ ] `DeadLetterQueue` monitored and alerted
- [ ] `HumanEscalationHook` connected to PagerDuty/OpsGenie
- [ ] Hierarchical timeouts at every layer
- [ ] Circuit breaker states exported to Prometheus
- [ ] DLQ size exported to Prometheus
- [ ] Escalation count tracked and trended
