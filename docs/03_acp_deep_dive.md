# ACP — Agent Communication Protocol: Deep Dive

## 1. What Is ACP and Why Does It Exist?

A2A handles one-to-one agent delegation. But enterprise workflows need *orchestration* — coordinating many agents across a multi-step process with dependencies, timeouts, partial failures, and result aggregation.

ACP is the **orchestration layer**: it governs how a central coordinator dispatches work, correlates messages, manages workflow state, and collects results from a fleet of agents.

**Analogy**: If A2A is a phone call between two doctors, ACP is the hospital's patient management system — tracking every appointment, test, and referral in a complex treatment plan.

---

## 2. Message Envelope — The Core Primitive

Every ACP message is wrapped in an envelope that separates routing metadata from business payload:

```json
{
  "message_id": "msg-xyz-789",
  "type": "TASK_DISPATCH",
  "from": "orchestrator",
  "to": "finance-agent-v2",
  "correlation_id": "workflow-2024-001",
  "timestamp": 1735689600.0,
  "ttl_seconds": 300,
  "priority": 8,
  "reply_to": "orchestrator/inbox",
  "requires_ack": true,
  "retry_policy": {
    "max_retries": 3,
    "backoff_strategy": "exponential",
    "base_delay_ms": 500
  },
  "trace_context": {
    "trace_id": "trace-001",
    "span_id": "span-003",
    "parent_span_id": "span-002"
  },
  "payload": {
    "task_id": "task-abc-123",
    "capability": "financial_analysis",
    "input": {"query": "Analyze Q3 revenue for ACME Corp"},
    "context_ref": "ctx-session-42"
  }
}
```

**Why envelope + payload separation?**
- Message brokers (Redis, Kafka, RabbitMQ) can route messages using envelope fields without parsing payload
- Correlation IDs allow grouping all messages in a workflow for debugging
- TTL prevents stale messages from being processed after deadline
- `retry_policy` in envelope means the broker (not the handler) controls retries

---

## 3. Message Types

| Type | Direction | Purpose |
|------|-----------|---------|
| `TASK_DISPATCH` | Orchestrator → Agent | Send a task to an agent |
| `TASK_RESULT` | Agent → Orchestrator | Return completed result |
| `TASK_ACK` | Agent → Orchestrator | Confirm task receipt (before starting) |
| `TASK_CANCEL` | Orchestrator → Agent | Cancel an in-flight task |
| `STATUS_UPDATE` | Agent → Orchestrator | Intermediate progress report |
| `HEALTH_CHECK` | Orchestrator → Agent | Liveness ping |
| `CAPABILITY_QUERY` | Orchestrator → Agent | Ask what capabilities agent has |
| `ERROR_REPORT` | Agent → Orchestrator | Report unrecoverable error |
| `WORKFLOW_START` | Orchestrator → System | Begin a workflow |
| `WORKFLOW_COMPLETE` | Orchestrator → System | Workflow finished |

---

## 4. Async vs Sync Communication

### Fire-and-Forget (Async)

```python
# Send a message without waiting for response
envelope = await orchestrator.send(
    to_agent="finance-agent",
    payload={"task": "analyze_revenue", "ticker": "ACME"},
    msg_type=ACPMessageType.TASK_DISPATCH,
    requires_ack=True,   # agent must send TASK_ACK
    ttl_seconds=300,
)
# Continue immediately; result arrives via reply_to inbox
```

**Use when:**
- Task takes > 5 seconds
- Result is not needed immediately
- Parallel execution is desired

### Request-Reply (Sync over Async)

```python
# Block until agent responds or timeout
result = await orchestrator.send_and_wait(
    to_agent="calculator-agent",
    payload={"expression": "42 * 1000 + 200"},
    timeout=10.0,
)
print(result)  # {"result": 42200}
```

**Use when:**
- Task completes quickly (< 5 seconds)
- Result is needed before proceeding
- Sequential steps with data dependencies

---

## 5. Workflow Execution — DAG Model

ACP workflows are Directed Acyclic Graphs (DAGs) where steps can have dependencies:

```python
workflow = Workflow(
    workflow_id="wf-finance-report",
    name="FinancialReportWorkflow",
    steps=[
        # Step 1: no dependencies → runs immediately
        WorkflowStep(
            step_id="fetch_financials",
            name="Fetch Financial Data",
            capability="financial_analysis",
            assigned_agent="finance-agent",
            depends_on=[],
        ),
        # Step 2: no dependencies → runs in parallel with step 1
        WorkflowStep(
            step_id="check_legal",
            name="Legal Compliance Check",
            capability="legal_review",
            assigned_agent="legal-agent",
            depends_on=[],
        ),
        # Step 3: waits for BOTH steps 1 and 2
        WorkflowStep(
            step_id="generate_report",
            name="Generate Combined Report",
            capability="report_generation",
            assigned_agent="report-agent",
            depends_on=["fetch_financials", "check_legal"],
        ),
    ],
)

# Execute: steps 1 & 2 run in parallel; step 3 starts after both complete
results = await orchestrator.execute_workflow(workflow, input_data={...})
```

**Execution timeline:**
```
t=0ms:   fetch_financials  ─────────────────► done at t=200ms
          check_legal       ─────────────►    done at t=150ms
t=200ms: generate_report                 ─────────────► done at t=350ms
```

**Ready step algorithm:**
```python
def get_ready_steps(self) -> list[WorkflowStep]:
    completed_ids = {s.step_id for s in self.steps if s.status == "completed"}
    return [
        s for s in self.steps
        if s.status == "pending"
        and all(dep in completed_ids for dep in s.depends_on)
    ]
```

---

## 6. Correlation and Tracing

Every message in a workflow shares the same `correlation_id`. This enables:

```python
# Retrieve all messages for a workflow
messages = orchestrator.get_message_log(correlation_id="workflow-2024-001")

# Output:
# [
#   {event: "sent",    envelope: {to: "finance-agent", type: "TASK_DISPATCH", ...}},
#   {event: "replied", correlation_id: "workflow-2024-001", result: {...}},
#   {event: "sent",    envelope: {to: "legal-agent",   type: "TASK_DISPATCH", ...}},
#   ...
# ]
```

**Trace context propagation:**
```python
trace = TraceContext(
    trace_id="trace-abc",
    span_id="span-003",
    parent_span_id="span-002",
)
# Propagated in every envelope's trace_context field
# Enables reconstruction of full execution DAG in Jaeger/Zipkin
```

---

## 7. TTL and Expiry

Messages have a `ttl_seconds` field. Expired messages go to the dead letter queue:

```python
def is_expired(self) -> bool:
    return time.time() > (self.timestamp + self.ttl_seconds)

# In Inbox.put():
async def put(self, envelope: MessageEnvelopeACP) -> None:
    if envelope.is_expired():
        logger.warning("acp_message_expired_on_arrival", msg_id=envelope.message_id)
        self._dead_letter.append(envelope)
        return
    await self._queue.put(envelope)
```

**Why TTL matters:**
- Tasks have deadlines; processing a task after its deadline is wasteful
- Prevents message queues from filling up with stale work
- Forces explicit re-submission rather than silent processing of old requests

---

## 8. ACP vs Message Queues (Kafka/RabbitMQ)

| Feature | ACP (this impl.) | Kafka | RabbitMQ |
|---------|-----------------|-------|---------|
| Persistence | In-process | Disk | Disk |
| Ordering | FIFO per channel | Partition-based | Queue-based |
| Replay | No | Yes (log replay) | No |
| Pub/Sub | Via MessageBus | Native | Via exchanges |
| Workflow DAG | Built-in | No | No |
| Correlation | Built-in | Application | Application |
| Production use | Dev/test | High-throughput | General messaging |

**Production upgrade path:**
Replace `Inbox._queue` with a Redis List or Kafka topic. The `ACPOrchestrator` interface remains unchanged — only the transport layer changes.

---

## 9. Retry Policy

```python
@dataclass
class RetryPolicy:
    max_retries: int = 3
    backoff_strategy: str = "exponential"  # or "linear", "fixed"
    base_delay_ms: int = 500

    def compute_delay(self, attempt: int) -> float:
        if self.backoff_strategy == "exponential":
            return (self.base_delay_ms * (2 ** attempt)) / 1000
        return self.base_delay_ms / 1000

# Delays: attempt 0 → 0.5s, attempt 1 → 1.0s, attempt 2 → 2.0s
```

Retry policy is carried in the envelope itself — the broker (or caller) handles retries, not the handler. This separates retry concerns from business logic.
