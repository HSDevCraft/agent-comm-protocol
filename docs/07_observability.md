# Observability & Explainability: Deep Dive

## 1. Why Observability Is Non-Negotiable

Multi-agent systems are opaque by default. Without instrumentation:

```
User: "Why did the system give me this answer?"
Engineer: "I don't know — it went through 7 agents and 12 tool calls."
```

With full observability:
```
User: "Why did you choose FinanceAgent over DataAgent?"
System: "FinanceAgent had capability_match=1.0 and success_rate=0.94.
         DataAgent had capability_match=1.0 but success_rate=0.71.
         Confidence: 92% for FinanceAgent."
```

Observability serves three masters:
- **Developers**: Debug why agents made wrong decisions
- **Users**: Explain AI behavior (regulatory requirement in EU AI Act)
- **Ops teams**: Monitor system health, detect anomalies

---

## 2. ReasoningTrace — The Core Observability Primitive

Every decision an agent makes emits a `ReasoningTrace`:

```python
@dataclass
class ReasoningTrace:
    trace_id: str          # links to a top-level request
    span_id: str           # unique ID for this decision
    parent_span_id: str    # links to parent span (builds a tree)
    agent_id: str          # which agent made this decision
    decision: str          # what was decided ("delegate_to_finance_agent")
    decision_type: DecisionType  # DELEGATION | TOOL_CALL | LOCAL_EXECUTION | ...
    reasoning: str         # human-readable WHY
    confidence: float      # 0.0–1.0 how confident the agent is
    protocol_used: str     # "A2A" | "MCP" | "ACP" | "ANP" | "local"
    inputs: dict           # what the agent received
    output: dict           # what the agent produced
    alternatives_considered: list[Alternative]  # options that were NOT chosen
    tool_calls: list[str]  # MCP tools called during this decision
    memory_reads: list[str]    # memory keys read
    memory_writes: list[str]   # memory keys written
    duration_ms: float     # how long this decision took
    tags: list[str]
```

**Complete example:**
```json
{
  "trace_id": "trace-abc-001",
  "span_id": "span-003",
  "parent_span_id": "span-002",
  "agent_id": "planner-agent",
  "timestamp": 1735689600.001,
  "duration_ms": 12.4,
  "decision": "delegate_to_finance_agent",
  "decision_type": "DELEGATION",
  "reasoning": "Task requires 'financial_analysis' capability. Registry: 2 candidates. Selected finance-agent-v2 (score=0.94) over finance-agent-v1 (score=0.71) based on version and load.",
  "confidence": 0.94,
  "protocol_used": "A2A",
  "alternatives_considered": [
    {"option": "finance-agent-v1", "score": 0.71, "rejected_reason": "older version, higher latency"},
    {"option": "local_llm_fallback", "score": 0.45, "rejected_reason": "insufficient domain knowledge"}
  ],
  "inputs": {"capability_needed": "financial_analysis", "priority": "high"},
  "output": {"delegated_to": "finance-agent-v2", "task_id": "task-abc-123"},
  "tool_calls": [],
  "memory_reads": ["registry:finance-agents"],
  "memory_writes": ["task:abc-123:delegation-record"]
}
```

---

## 3. SpanTracer — Distributed Trace Tree

The `SpanTracer` links individual `ReasoningTrace` spans into a DAG (Directed Acyclic Graph) representing the full execution tree:

```
trace-abc-001
│
├── span-001: planner-agent::decompose [LOCAL] (88%) — 5ms
│
├── span-002: planner-agent::delegate_to_finance [A2A] (94%) — 12ms
│   │
│   └── span-003: finance-agent::fetch_data [MCP:db_query] (90%) — 45ms
│
├── span-004: planner-agent::delegate_to_legal [A2A] (87%) — 8ms
│   │
│   └── span-005: legal-agent::review_contract [MCP:search] (82%) — 33ms
│
└── span-006: report-agent::generate [LOCAL] (95%) — 18ms

Total: 121ms | Min confidence: 82% (legal review)
```

**Building the tree:**
```python
tracer = obs.new_tracer()

# Planner decides to decompose
span_id = tracer.start_span("planner-agent", "decompose")
trace = obs.record_decision(
    agent_id="planner-agent",
    decision="decompose_into_3_subtasks",
    span_id=span_id,
    parent_span_id="",   # root span
    ...
)
tracer.record(trace)

# Finance agent executes (nested)
span_id2 = tracer.start_span("finance-agent", "fetch_data")
trace2 = obs.record_decision(
    agent_id="finance-agent",
    decision="call_db_query_tool",
    span_id=span_id2,
    parent_span_id=span_id,  # child of planner's span
    ...
)
tracer.record(trace2)

# Print the full tree
obs.print_trace_tree(tracer)
```

---

## 4. Confidence Scoring

Confidence is a normalized 0–1 score that answers: "How sure is the agent about this decision?"

### Agent Selection Confidence

```python
def score_agent_for_capability(
    required_capability: str,
    agent_capabilities: list[str],
    agent_version: str,
    agent_load: float = 0.0,
    historical_success_rate: float = 1.0,
    avg_latency_ms: float = 100.0,
) -> float:
    capability_match = 1.0 if required_capability in agent_capabilities else 0.0
    version_score = min(1.0, int(major) / 10)
    load_score = max(0.0, 1.0 - agent_load)
    latency_score = max(0.0, 1.0 - avg_latency_ms / 5000)
    
    return (
        0.50 * capability_match
      + 0.25 * historical_success_rate
      + 0.10 * version_score
      + 0.10 * load_score
      + 0.05 * latency_score
    )
```

**Score interpretation:**
- `>0.90`: High confidence — proceed normally
- `0.70–0.90`: Moderate confidence — proceed with monitoring
- `0.50–0.70`: Low confidence — consider fallback or human review
- `<0.50`: Very low confidence — escalate to human

### Tool Call Confidence

```python
def score_tool_call(
    tool_name: str,
    available_tools: list[str],
    past_success: bool = True,
    latency_ms: float = 0.0,
) -> float:
    availability = 1.0 if tool_name in available_tools else 0.0
    success_score = 1.0 if past_success else 0.3
    latency_penalty = max(0.0, 1.0 - latency_ms / 10000)
    return availability * 0.6 + success_score * 0.3 + latency_penalty * 0.1
```

---

## 5. DecisionLog — Queryable Analytics

All `ReasoningTrace` objects are appended to a `DecisionLog`:

```python
log = obs.decision_log

# Find all low-confidence decisions (potential problems)
risky = log.low_confidence_decisions(threshold=0.6)

# Find all delegations
delegations = log.query(decision_type=DecisionType.DELEGATION)

# Find decisions by a specific agent in the last hour
recent = log.query(agent_id="finance-agent", since=time.time()-3600)

# Summary statistics
print(log.summary())
# {
#   "total": 47,
#   "avg_confidence": 0.83,
#   "min_confidence": 0.45,
#   "by_decision_type": {"DELEGATION": 12, "TOOL_CALL": 23, "LOCAL_EXECUTION": 12},
#   "by_agent": {"planner-agent": 8, "finance-agent": 15, "legal-agent": 12, ...}
# }
```

---

## 6. Debugging Hooks

Add a debugging hook that fires on every decision:

```python
def my_debug_hook(trace: ReasoningTrace) -> None:
    if trace.confidence < 0.6:
        print(f"⚠ LOW CONFIDENCE: {trace.agent_id}::{trace.decision} ({trace.confidence:.0%})")
        print(f"  Reasoning: {trace.reasoning}")
        print(trace.explain())

obs.add_debug_hook(my_debug_hook)
```

**The `explain()` method** produces a human-readable explanation:

```
[DELEGATION] Agent 'planner-agent' decided: delegate_to_finance_agent
  Confidence: 94%
  Protocol: A2A
  Reasoning: Task requires 'financial_analysis' capability...
  Alternatives rejected:
    - finance-agent-v1 (score=0.71): older version, higher latency
    - local_llm_fallback (score=0.45): insufficient domain knowledge
  Duration: 12.4ms
```

---

## 7. Production Observability Stack

```
Agent Code
    │ emit ReasoningTrace
    ▼
ObservabilityEngine
    │ SpanTracer.record()
    │ DecisionLog.append()
    ▼
OpenTelemetry SDK
    │ export spans via OTLP
    ▼
┌───────────────────────────────────┐
│  Jaeger  │  Zipkin  │  Grafana   │
│  (traces)│  (traces)│  (metrics) │
└───────────────────────────────────┘
```

**OTLP export integration:**
```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4317"))
)
trace.set_tracer_provider(provider)

# In SpanTracer.record():
otel_tracer = trace.get_tracer("agent-comm-protocol")
with otel_tracer.start_as_current_span(reasoning_trace.span_id) as span:
    span.set_attribute("agent.id", reasoning_trace.agent_id)
    span.set_attribute("decision.type", reasoning_trace.decision_type.value)
    span.set_attribute("confidence", reasoning_trace.confidence)
```

---

## 8. Explainability for Compliance

Under EU AI Act and similar regulations, AI systems must explain decisions:

```python
# Generate an explanation for a user
def explain_decision_for_user(trace_id: str) -> str:
    tracer = obs.get_tracer(trace_id)
    path = tracer.get_execution_path()
    
    explanation = ["Your request was processed as follows:"]
    for step in path:
        agent, decision, protocol, confidence = parse_step(step)
        explanation.append(f"  • {agent} {decision} via {protocol} (confidence: {confidence:.0%})")
    
    lowest = tracer.lowest_confidence_step()
    if lowest and lowest.confidence < 0.7:
        explanation.append(
            f"\nNote: The step '{lowest.decision}' had lower confidence ({lowest.confidence:.0%}). "
            "A human reviewer was flagged."
        )
    return "\n".join(explanation)
```
