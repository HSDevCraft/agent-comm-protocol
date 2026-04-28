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

---

## 10. Advanced A2A Patterns

### Pattern A: Capability-Aware Dynamic Routing

Instead of hardcoding the receiver, let the registry find the best agent at runtime:

```python
async def delegate_dynamically(capability: str, input_data: dict) -> dict:
    candidates = registry.find_by_capability(capability)
    if not candidates:
        raise ValueError(f"No agents registered for capability: {capability}")
    
    scored = [
        (
            scorer.score_agent_for_capability(
                required_capability=capability,
                agent_capabilities=card.capabilities,
                agent_version=card.version,
                agent_load=get_current_load(card.agent_id),
                historical_success_rate=get_success_rate(card.agent_id),
            ),
            card
        )
        for card in candidates
    ]
    
    best_score, best_card = max(scored, key=lambda x: x[0])
    
    if best_score < 0.5:
        raise ValueError(f"No sufficiently capable agent found (best: {best_score:.2f})")
    
    return await a2a_client.delegate(
        capability=capability,
        input_data=input_data,
        receiver_agent_id=best_card.agent_id,
    )
```

### Pattern B: Fan-Out Delegation (Sub-Task Parallelism)

One agent delegates to N agents simultaneously, then aggregates:

```python
async def fan_out_research(topics: list[str]) -> dict:
    tasks = await asyncio.gather(*[
        a2a_client.delegate(
            capability="research",
            input_data={"topic": topic},
            correlation_id="research-batch-001",
        )
        for topic in topics
    ])
    
    # Wait for all tasks to complete
    completed = await asyncio.gather(*[
        wait_for_task_completion(task.task_id, timeout=60)
        for task in tasks
    ])
    
    return {
        "results": [t.output for t in completed if t.status == TaskStatus.COMPLETED],
        "failed": [t.task_id for t in completed if t.status == TaskStatus.FAILED],
    }
```

### Pattern C: Hierarchical Delegation with Depth Tracking

When an agent receives a task, it can sub-delegate while respecting depth limits:

```python
async def handle_task_with_sub_delegation(task: A2ATask) -> dict:
    current_depth = task.delegation_depth
    
    if current_depth >= MAX_DEPTH:
        return execute_locally(task.input)   # must execute locally
    
    # Sub-delegate if beneficial
    sub_result = await a2a_client.delegate(
        capability="data_fetching",
        input_data=task.input,
        current_depth=current_depth + 1,     # increment depth
    )
    
    return process_with_sub_result(task.input, sub_result.output)
```

### Pattern D: Timeout with Partial Result Acceptance

```python
async def delegate_with_timeout(capability: str, input_data: dict, timeout: float = 30.0):
    task = await a2a_client.delegate(capability=capability, input_data=input_data, stream=True)
    
    last_partial_result = None
    deadline = asyncio.get_event_loop().time() + timeout
    
    async for chunk in a2a_client.stream_task(task, task.receiver_id):
        last_partial_result = chunk
        if asyncio.get_event_loop().time() > deadline:
            break   # accept partial result
    
    if last_partial_result and last_partial_result.get("pct", 0) >= 50:
        return {"result": last_partial_result, "partial": True}
    
    raise TimeoutError(f"Task {task.task_id} timed out with < 50% completion")
```

---

## 11. A2A Anti-Patterns to Avoid

### Anti-Pattern 1: Hardcoding Receiver IDs

```python
# WRONG — brittle coupling to specific agent
task = await a2a_client.delegate(
    capability="financial_analysis",
    input_data=data,
    receiver_agent_id="finance-agent-v1",  # ← hardcoded, breaks when v2 deploys
)

# RIGHT — dynamic discovery by capability
candidates = registry.find_by_capability("financial_analysis")
best = score_and_select(candidates)
task = await a2a_client.delegate(
    capability="financial_analysis",
    input_data=data,
    receiver_agent_id=best.agent_id,
)
```

### Anti-Pattern 2: Ignoring Task History

```python
# WRONG — just checking status, losing valuable debug info
task = await a2a_client.delegate(...)
if task.status == TaskStatus.FAILED:
    raise RuntimeError("Task failed")

# RIGHT — inspect history for root cause
task = await a2a_client.delegate(...)
if task.status == TaskStatus.FAILED:
    last_event = task.history[-1]
    logger.error("task_failed",
                 task_id=task.task_id,
                 last_status=last_event["status"],
                 note=last_event["note"],
                 retry_count=task.metadata.get("retry_count", 0))
    raise TaskFailedError(task.task_id, task.error)
```

### Anti-Pattern 3: Unlimited Delegation Depth

```python
# WRONG — no depth enforcement
async def delegate(self, capability, input_data):
    # No depth check → possible infinite loops!
    task = await self._client.delegate(capability, input_data)
    return task

# RIGHT — always track and enforce depth
async def delegate(self, capability, input_data, current_depth=0):
    if current_depth >= self.max_delegation_depth:
        raise DelegationDepthExceededError(current_depth)
    task = await self._client.delegate(
        capability, input_data, current_depth=current_depth
    )
    return task
```

### Anti-Pattern 4: Using A2A for Tool Calls

```python
# WRONG — using A2A to call what should be an MCP tool
task = await a2a_client.delegate(
    capability="database_query",     # ← this is a TOOL, not an agent capability
    input_data={"table": "users"},
    receiver_agent_id="db-agent",    # ← a "wrapper agent" for a DB
)

# RIGHT — use MCP directly
result = await mcp_client.call_tool("database_query", {"table": "users"})
```

**Rule**: If the "agent" is stateless, has no reasoning, and just wraps a single resource/API → use MCP tool. If the target performs reasoning, maintains state, or can sub-delegate → use A2A.

### Anti-Pattern 5: Synchronous Polling on Long Tasks

```python
# WRONG — busy-polling wastes resources
while True:
    task = await fetch_task_status(task_id)
    if task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
        break
    time.sleep(0.1)     # polling every 100ms

# RIGHT — use streaming (SSE) or callback
async for chunk in a2a_client.stream_task(task, receiver_id):
    if chunk["chunk"]["pct"] == 100:
        return chunk["chunk"]["result"]
```

---

## 12. A2A Conceptual Model — How to Think About It

### The "postal mail" model

A2A tasks are like certified mail with tracking:

```
Traditional function call:          A2A task delegation:
────────────────────────            ────────────────────
Phone call: immediate response      Certified letter: tracked journey
No record of conversation           Full delivery history (task.history)
Can't cancel mid-call               Cancel at any point (CANCELLED status)
No forwarding possible              Sub-delegate (delegation_depth)
No progress updates                 Streaming progress events
Caller blocks until answer          Caller can do other work (async)
```

### The "task board" model

Agent registry = team's shared task board:

```
AgentRegistry = {
    "financial_analysis": [FinanceAgentCard, FinanceAgentBackupCard],
    "legal_review":       [LegalAgentCard],
    "report_generation":  [ReportAgentCard],
}

Posting a task (delegate):   "I need someone to do financial_analysis"
Board checks:                Who can do it? Who's available? Who's fastest?
Best match assigned:         FinanceAgentCard (score=0.91, load=30%)
Task created on board:       Tracked from submission to completion
```

### The "state machine" in plain English

```
SUBMITTED:  "I've handed the letter to the post office"
ACCEPTED:   "The recipient confirmed delivery"
WORKING:    "They're reading it and preparing a reply"
STREAMING:  "They're sending you progress updates along the way"
COMPLETED:  "Reply received, letter delivered"
FAILED:     "Letter lost, no reply, or reply says 'can't help'"
ESCALATED:  "Post office gave up — manager intervention needed"
CANCELLED:  "You pulled the letter back before it was delivered"
```

---

## 13. Interview Questions: A2A

**Q: "How is A2A different from a simple HTTP request?"**
> HTTP is stateless: you send a request and get a response. A2A is stateful: the task object has a full lifecycle (SUBMITTED → WORKING → COMPLETED), an append-only history, support for cancellation, streaming progress, and delegation depth tracking. A2A also includes dynamic agent discovery (AgentCard registry) and capability-based routing — HTTP has none of these.

**Q: "What is an AgentCard and why is it needed?"**
> An AgentCard is a self-describing advertisement that an agent publishes so others can discover it. It contains the agent's capabilities, endpoint, input/output schemas, trust level, and rate limits. Without AgentCards, orchestrators must hardcode knowledge of every agent. With AgentCards, you can add new agents to the registry and existing orchestrators discover them automatically.

**Q: "What is delegation depth and what problem does it solve?"**
> Delegation depth is a counter (starting at 0) that increments each time a task is re-delegated. When it reaches `max_delegation_depth`, the agent must execute locally or fail. Without this, agents can form loops: A→B→C→A→... consuming infinite resources. The depth limit forces termination.

**Q: "How does streaming work in A2A?"**
> The sender creates a task with `stream=true`. The receiver sends Server-Sent Events (SSE) as it processes: each event contains a percentage complete and an optional partial result. The sender consumes events via an async generator. The final event (pct=100) contains the complete result and transitions the task to COMPLETED.

**Q: "How does A2A handle agent failure?"**
> The task transitions to FAILED with an error message in `task.error`. The `task.history` records the final state transition. The caller can then: retry the task with a different agent (re-delegate), trigger a FallbackChain (from the failure resilience layer), send to the Dead Letter Queue for manual review, or escalate to a human via the HumanEscalationHook.

**Q: "Can an agent delegate to itself? How is this prevented?"**
> Yes, without protection. Delegation depth limits prevent infinite loops, but they don't prevent self-delegation specifically. In production, add a `receiver_id != sender_id` guard and track the delegation chain in `task.metadata` to detect cycles explicitly.

