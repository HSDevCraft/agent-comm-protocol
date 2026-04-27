# A2A — Agent-to-Agent Protocol: Deep Dive

## 1. What Is A2A and Why Does It Exist?

MCP solves agent-to-tool communication. But what happens when one agent needs to ask *another agent* to do something? You need:

- A way for Agent A to *discover* that Agent B exists and what it can do
- A way to *negotiate* and *hand off* a task
- A way to *receive results* (synchronously or via streaming)
- A trust mechanism so Agent A knows it's really talking to Agent B

A2A provides exactly this: a standardized HTTP-based protocol for agent-to-agent collaboration.

**Analogy**: MCP is the "tool calling" protocol (doctor using a stethoscope). A2A is the "referral" protocol (doctor sending a patient to a specialist with a referral letter and patient record).

---

## 2. Agent Card — The Discovery Mechanism

An **Agent Card** is the self-describing advertisement every agent publishes. It answers: "Who am I, what can I do, and how do you talk to me?"

```json
{
  "agent_id": "finance-agent-v2",
  "name": "FinanceAgent",
  "version": "2.1.0",
  "description": "Specialized agent for financial analysis, portfolio optimization, and risk assessment",
  "capabilities": [
    "financial_analysis",
    "portfolio_optimization",
    "risk_assessment",
    "earnings_summary"
  ],
  "endpoint": "https://agents.internal/finance",
  "protocol": "A2A",
  "input_schema": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "query": {"type": "string"},
      "ticker": {"type": "string"},
      "time_range": {"type": "string", "enum": ["Q1","Q2","Q3","Q4","YTD","1Y"]}
    }
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "report": {"type": "string"},
      "confidence": {"type": "number"},
      "data_sources": {"type": "array"}
    }
  },
  "trust_level": "internal",
  "auth": {"type": "bearer", "scope": "finance:read"},
  "rate_limits": {
    "requests_per_minute": 60,
    "max_concurrent_tasks": 10
  },
  "health_endpoint": "https://agents.internal/finance/health",
  "ttl_seconds": 3600
}
```

**Where Agent Cards are stored:**
- `AgentRegistry` — in-process registry for same-process agents
- Well-known URL (`/.well-known/agent.json`) — discoverable over HTTP
- Service mesh registry — for production microservice deployments
- ANP DID document — for decentralized cross-org discovery

---

## 3. Task Lifecycle — State Machine

An A2A task is not a simple function call. It has a full lifecycle:

```
                    ┌──────────────────┐
                    │    SUBMITTED     │  ← Task created, assigned task_id
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │    ACCEPTED      │  ← Target agent acknowledged receipt
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │    WORKING       │  ← Agent is processing
                    └────────┬─────────┘
                             │
               ┌─────────────┼─────────────┐
               │             │             │
      ┌────────▼───┐  ┌──────▼──────┐  ┌──▼──────┐
      │ STREAMING  │  │  COMPLETED  │  │ FAILED  │
      └────────────┘  └─────────────┘  └─────────┘
           │                                │
           └──────► COMPLETED          ┌────▼─────┐
                                       │ESCALATED │
                                       └──────────┘
```

**State transition rules:**
- `SUBMITTED → ACCEPTED`: target agent acknowledged the task
- `ACCEPTED → WORKING`: agent started processing
- `WORKING → STREAMING`: agent is streaming intermediate results
- `STREAMING → COMPLETED`: final result delivered
- `WORKING/STREAMING → FAILED`: unrecoverable error
- `FAILED → ESCALATED`: human intervention required
- Any state → `CANCELLED`: sender cancelled the task

---

## 4. Task Object — Complete Schema

```json
{
  "task_id": "task-abc-123",
  "correlation_id": "workflow-2024-001",
  "sender_id": "planner-agent",
  "receiver_id": "finance-agent-v2",
  "capability": "financial_analysis",
  "status": "completed",
  "priority": "high",
  "stream": false,
  "created_at": 1735689600.0,
  "updated_at": 1735689618.0,
  "deadline_iso": "2025-01-01T10:05:00Z",
  "input": {
    "query": "Analyze Q3 revenue trend for ACME Corp",
    "ticker": "ACME",
    "time_range": "Q3"
  },
  "output": {
    "report": "ACME Corp Q3 revenue grew 12% YoY to $4.2B...",
    "confidence": 0.91,
    "data_sources": ["edgar_db", "bloomberg_api"]
  },
  "error": null,
  "artifacts": [
    {
      "type": "chart",
      "url": "https://storage.internal/charts/acme-q3.png",
      "mime_type": "image/png"
    }
  ],
  "history": [
    {"timestamp": 1735689600.0, "status": "submitted", "note": "Task created"},
    {"timestamp": 1735689602.0, "status": "accepted", "note": "Accepted by finance-agent-v2"},
    {"timestamp": 1735689602.5, "status": "working", "note": "finance-agent-v2 processing"},
    {"timestamp": 1735689618.0, "status": "completed", "note": "Task completed successfully"}
  ],
  "metadata": {
    "delegation_depth": 1,
    "parent_task_id": null,
    "retry_count": 0
  }
}
```

**Field deep-dive:**
- `correlation_id`: Links all tasks in a single workflow (same for all sub-tasks)
- `stream`: If true, agent should emit partial results via SSE before final completion
- `artifacts`: Binary outputs (charts, PDFs, reports) referenced by URL
- `history`: Immutable append-only log — never mutate past entries
- `metadata.delegation_depth`: Incremented by 1 for each level of delegation; enforced max prevents loops

---

## 5. Agent Discovery Flow

```python
# Step 1: Orchestrator needs financial_analysis capability
candidates = registry.find_by_capability("financial_analysis")

# Step 2: Score candidates (confidence score)
scores = [
    (scorer.score_agent_for_capability(
        required_capability="financial_analysis",
        agent_capabilities=card.capabilities,
        agent_version=card.version,
        agent_load=get_current_load(card.agent_id),
        historical_success_rate=get_success_rate(card.agent_id),
    ), card)
    for card in candidates
]
best_card = max(scores, key=lambda x: x[0])[1]

# Step 3: Verify trust level
assert best_card.trust_level in ["internal", "trusted"]

# Step 4: Delegate task
task = await a2a_client.delegate(
    capability="financial_analysis",
    input_data={"query": "Analyze ACME Q3"},
    correlation_id="workflow-001",
    priority=TaskPriority.HIGH,
)
```

---

## 6. Streaming Pattern

For long-running tasks, A2A supports Server-Sent Events (SSE) streaming:

```
Sender                          Receiver (agent)
  │                                    │
  │──POST /agents/finance/tasks────────►│
  │   {stream: true, ...}              │
  │                                    │──start processing
  │◄──200 OK {task_id: "task-abc"}─────│
  │                                    │
  │◄──SSE: {pct: 25, note: "Fetching data"}
  │◄──SSE: {pct: 50, note: "Analyzing"}
  │◄──SSE: {pct: 75, note: "Generating report"}
  │◄──SSE: {pct: 100, result: {...}, status: "completed"}
```

```python
# Sender side: iterate over streaming chunks
async for chunk in a2a_client.stream_task(task, target_agent_id):
    print(f"Progress: {chunk['chunk']['pct']}% — {chunk['chunk']['note']}")
    if chunk["chunk"]["pct"] == 100:
        final_result = chunk["chunk"].get("result")
```

---

## 7. Delegation Depth Enforcement

Multi-agent systems can create infinite delegation loops:
```
PlannerAgent → ResearchAgent → DataAgent → ResearchAgent → ...
```

A2A prevents this via `delegation_depth` tracking:

```python
async def delegate(self, capability, input_data, current_depth=0):
    if current_depth >= self.max_delegation_depth:
        raise RuntimeError(
            f"Max delegation depth ({self.max_delegation_depth}) exceeded"
        )
    # ...
    task = A2ATask(
        delegation_depth=current_depth,  # stored in task
        ...
    )
```

The receiver increments depth when re-delegating:
```python
# In _handle_a2a_task, if re-delegating:
await self._a2a_client.delegate(
    ...,
    current_depth=task.delegation_depth + 1,
)
```

---

## 8. Capability Scoring Algorithm

The `ConfidenceScorer` computes a 0–1 score for each candidate agent:

```
score = 0.50 × capability_match
      + 0.25 × historical_success_rate
      + 0.10 × version_score
      + 0.10 × (1 - current_load)
      + 0.05 × (1 - avg_latency / 5000)
```

**Why these weights?**
- `capability_match` (50%): Binary — either the agent can do it or not. Most important.
- `historical_success_rate` (25%): Agents with a track record of failures should be deprioritized.
- `version_score` (10%): Newer agents have bug fixes and improvements.
- `current_load` (10%): Avoid overloading a single agent.
- `latency` (5%): Prefer faster agents when all else is equal.

---

## 9. A2A vs Direct Function Call

| Aspect | Direct Call | A2A |
|--------|-------------|-----|
| Discovery | Hard-coded | Dynamic via AgentCard |
| Versioning | Manual | Built-in version field |
| Failure | Exception propagates | Task status: `failed` |
| Visibility | None | Full task history |
| Cancellation | Not possible | `CANCELLED` status |
| Streaming | Not supported | SSE streaming |
| Load balancing | None | Score-based selection |
| Audit | None | Task history + AuditLog |
