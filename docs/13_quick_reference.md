# Quick Reference — Agent Communication Protocols

> One-page cheat sheet. All key schemas, error codes, formulas, patterns, and interview answers.

---

## Protocol Selection (30-second cheat sheet)

| If you need to... | Use |
|-------------------|-----|
| Call a tool, database, or data source | **MCP** `tools/call` |
| Delegate a task to another agent | **A2A** `client.delegate()` |
| Coordinate a multi-step workflow | **ACP** `orchestrator.execute_workflow()` |
| Discover agents in another org | **ANP** `client.discover_by_capability()` |
| Store intermediate results | **Working Memory** `memory.write_working()` |
| Cache cross-session results | **Episodic Memory** `memory.store_episodic()` |
| Build a knowledge base | **Semantic Memory** `memory.index_semantic()` |

---

## MCP — Quick Reference

### JSON-RPC Methods
```
tools/list       → list all registered tools
tools/call       → invoke a tool
resources/list   → list available data resources
resources/read   → read a resource
prompts/list     → list prompt templates
prompts/get      → get a prompt template
```

### Error Codes
```
-32700  PARSE_ERROR          Invalid JSON
-32600  INVALID_REQUEST      Not valid JSON-RPC
-32601  METHOD_NOT_FOUND     Method not registered
-32602  INVALID_PARAMS       Schema validation failed
-32001  TOOL_NOT_FOUND       Tool name not registered
-32002  TOOL_EXECUTION_ERROR Handler raised exception
-32003  UNAUTHORIZED         Missing required scope
-32004  RATE_LIMITED         Rate limit exceeded
```

### Scope Patterns
```
search:read     → web_search, document_search
db:read         → database_query (SELECT)
db:write        → database_insert, database_update
files:read      → file_reader
files:write     → file_writer, report_generator
code:execute    → code_executor
admin:*         → all admin tools
```

### Minimal Tool Definition
```python
ToolDefinition(
    name="my_tool",
    description="What this tool does for the LLM",
    input_schema={
        "type": "object",
        "required": ["param1"],
        "properties": {
            "param1": {"type": "string"}
        }
    },
    required_scope="resource:action",
    handler=async_handler_fn,
)
```

### Tool Call Lifecycle
```
Agent.call_tool(name, args)
  → MCPClient wraps in JSON-RPC
  → Server validates input_schema
  → Server checks required_scope
  → Handler executes
  → Returns MCPResult(content, is_error, latency_ms)
```

---

## A2A — Quick Reference

### Task States (state machine)
```
SUBMITTED → ACCEPTED → WORKING → COMPLETED
                              ↘ STREAMING → COMPLETED
                              ↘ FAILED → ESCALATED
                   Any → CANCELLED
```

### Task Priority Levels
```
LOW = 1    NORMAL = 5    HIGH = 8    CRITICAL = 10
```

### Capability Scoring Formula
```
score = 0.50 × capability_match      (binary: 0 or 1)
      + 0.25 × historical_success    (0.0–1.0)
      + 0.10 × version_score         (major_version / 10)
      + 0.10 × (1 - current_load)    (0.0–1.0)
      + 0.05 × (1 - latency / 5000)  (0.0–1.0)
```

### Confidence Interpretation
```
>0.90   High confidence     → proceed normally
0.70–0.90  Moderate         → proceed, monitor
0.50–0.70  Low confidence   → consider fallback
<0.50   Very low            → escalate to human
```

### Delegation API
```python
task = await a2a_client.delegate(
    capability="financial_analysis",
    input_data={"query": "..."},
    correlation_id="workflow-001",
    priority=TaskPriority.HIGH,
    stream=False,
    current_depth=0,
)
```

### Key Fields in Task Object
```
task_id          globally unique task identifier
correlation_id   links all tasks in one workflow
delegation_depth prevents delegation loops (enforced max)
history          append-only log of state transitions
artifacts        binary outputs (charts, PDFs) referenced by URL
stream           if true, receiver emits SSE progress events
deadline_iso     task must complete before this time
```

---

## ACP — Quick Reference

### Message Types
```
TASK_DISPATCH    orchestrator → agent  (send task)
TASK_RESULT      agent → orchestrator  (return result)
TASK_ACK         agent → orchestrator  (receipt confirmed)
TASK_CANCEL      orchestrator → agent  (cancel in-flight)
STATUS_UPDATE    agent → orchestrator  (progress report)
HEALTH_CHECK     orchestrator → agent  (liveness ping)
CAPABILITY_QUERY orchestrator → agent  (dynamic capability lookup)
ERROR_REPORT     agent → orchestrator  (unrecoverable error)
WORKFLOW_START   orchestrator → system (workflow initiated)
WORKFLOW_COMPLETE orchestrator → system (workflow finished)
```

### Retry Policy
```python
RetryPolicy(
    max_retries=3,
    backoff_strategy="exponential",  # or "linear", "fixed"
    base_delay_ms=500,
)
# Delays: attempt 0 → 0.5s, attempt 1 → 1.0s, attempt 2 → 2.0s
```

### DAG Workflow — Ready Step Algorithm
```python
def get_ready_steps(steps):
    completed = {s.step_id for s in steps if s.status == "completed"}
    return [
        s for s in steps
        if s.status == "pending"
        and all(dep in completed for dep in s.depends_on)
    ]
```

### Envelope Key Fields
```
message_id      unique message identifier
type            one of the message type enums above
correlation_id  ties all messages in a workflow together
ttl_seconds     message expires if not processed in time
reply_to        inbox address for response routing
requires_ack    agent must send TASK_ACK before processing
retry_policy    broker-level retry (not handler-level)
trace_context   { trace_id, span_id, parent_span_id }
```

---

## ANP — Quick Reference

### DID Methods
```
did:web    HTTP-resolvable (enterprise agents)
did:key    Derived from public key (ephemeral/test)
did:ion    Bitcoin-anchored (high assurance)
did:peer   Peer-to-peer (offline agents)
```

### DID Resolution
```
did:web:agents.acme.com:finance-agent
→ GET https://agents.acme.com/finance-agent/.well-known/did.json
```

### VC Validation Checklist
```
[✓] exp date is in the future
[✓] issuer DID resolves to valid DID document
[✓] Ed25519 signature verifies with issuer's public key
[✓] credentialSubject.id matches presenting agent
[✓] capabilities are within expected bounds
```

### ANP Message Signature Flow
```python
# Sign (sender)
message_hash = sha256(f"{sender_did}{receiver_did}{timestamp}{payload}")
signature = ed25519_private_key.sign(message_hash.encode())

# Verify (receiver)
public_key = resolve_did(message.sender_did).get_public_key()
public_key.verify(message.signature, message_hash.encode())
```

---

## Memory — Quick Reference

### Three Tiers
```
Working Memory   in-flight, session-scoped    TTL: 1–4 hours    dict (→ Redis)
Episodic Memory  past interactions, tagged    TTL: days/weeks   tag-indexed
Semantic Memory  facts, vector-indexed        TTL: indefinite   ANN search
```

### Access Control Levels
```
visibility: "public"    → any agent can read
visibility: "workflow"  → only agents in readable_by list
visibility: "private"   → only the creator
```

### Memory API
```python
# Working
memory.write_working(key, content, agent_id, readable_by, ttl_seconds)
memory.read_working(key, agent_id)

# Episodic
memory.store_episodic(content, agent_id, tags, ttl_seconds, readable_by)
memory.search_episodic(tags, agent_id)

# Semantic
memory.index_semantic(content, agent_id, tags)
memory.search_semantic(query, agent_id, top_k=5)
```

### TTL Guidelines
```
In-flight task data      Working     1 hour
Session context          Working     4 hours
Recent analysis results  Episodic    1 week
User conversation        Episodic    30 days
Company facts            Semantic    1 year
Static knowledge         Semantic    Indefinite
```

---

## Security — Quick Reference

### JWT Token Claims
```json
{
  "sub": "finance-agent-v2",
  "role": "specialist",
  "caps": ["financial_analysis"],
  "scopes": ["db:read", "search:read"],
  "exp": 1735693200,
  "jti": "unique-token-id",
  "nonce": "random-nonce",
  "delegation_allowed": true,
  "max_delegation_depth": 2
}
```

### RBAC Role Permissions
```
ORCHESTRATOR  all scopes, can delegate, max_depth=5
PLANNER       read scopes, can delegate, max_depth=3
SPECIALIST    narrow scopes, no delegation, max_depth=1
TOOL_CALLER   search:read only, no delegation
OBSERVER      no tool access, monitoring only
```

### Prompt Injection Patterns (blocked)
```regex
ignore\s+(previous|all|above|prior)\s+instructions?
you\s+are\s+now\s+a?\s*(different|new|evil|unrestricted)
(system\s+prompt|jailbreak|bypass|override)\s*:
pretend\s+(you\s+are|to\s+be)
do\s+anything\s+now
<\s*(script|iframe|object)\s*>
(exec|eval|subprocess|os\.system)\s*\(
```

### Audit Chain Verification
```python
def verify_chain(entries) -> bool:
    for i, entry in enumerate(entries):
        if entry.entry_hash != entry._compute_hash():  # tampered
            return False
        if i > 0 and entry.prev_hash != entries[i-1].entry_hash:  # deleted/inserted
            return False
    return True
```

---

## Failure Resilience — Quick Reference

### Backoff Formula
```python
delay = min(base_delay * (multiplier ** attempt), max_delay)
jitter = delay * jitter_fraction * random.random()
total_delay = delay + jitter

# Config: base=0.5s, mult=2.0, max=30s, jitter=0.1
# attempt 0 → ~0.5s,  attempt 1 → ~1.0s
# attempt 2 → ~2.0s,  attempt 3 → ~4.0s
```

### Circuit Breaker States
```
CLOSED   → normal operation, requests pass through
OPEN     → fast-fail all requests (RuntimeError)
HALF_OPEN → probe a few requests; success → CLOSED, failure → OPEN

Transition: CLOSED→OPEN when failure_count >= failure_threshold
Transition: OPEN→HALF_OPEN after timeout_seconds
Transition: HALF_OPEN→CLOSED when success_count >= success_threshold
Transition: HALF_OPEN→OPEN on any failure
```

### Config Recommendations
```python
# Critical agents (finance, legal)
CircuitBreakerConfig(failure_threshold=3, timeout_seconds=30, success_threshold=2)

# Best-effort agents (search, recommendations)
CircuitBreakerConfig(failure_threshold=10, timeout_seconds=10, success_threshold=1)
```

### Timeout Guidelines
```
MCP tool call        5–10 seconds
A2A single task      30–60 seconds
ACP workflow step    60–120 seconds
Full workflow        5 minutes
User-facing request  30 seconds
```

### FailureOrchestrator Pipeline
```
call fn()
  → CircuitBreaker (fast-fail if OPEN)
  → RetryHandler (retry with backoff on transient errors)
  → FallbackChain (degrade gracefully)
  → DeadLetterQueue (preserve unprocessable message)
  → HumanEscalationHook (alert ops if DLQ threshold exceeded)
```

---

## Observability — Quick Reference

### ReasoningTrace Key Fields
```
trace_id            top-level request identifier
span_id             unique ID for this decision
parent_span_id      parent span (builds trace tree)
decision            what was decided
decision_type       DELEGATION|TOOL_CALL|LOCAL_EXECUTION|FALLBACK
confidence          0.0–1.0 confidence score
reasoning           human-readable WHY
alternatives_considered  options that were NOT chosen
duration_ms         decision latency
```

### Decision Types
```
DELEGATION        agent delegated to another agent (A2A)
TOOL_CALL         agent called an MCP tool
LOCAL_EXECUTION   agent processed locally (no delegation)
FALLBACK          primary path failed, using alternative
ESCALATION        confidence too low, escalating to human
```

### Production Observability Stack
```
Agent Code → ObservabilityEngine → OpenTelemetry SDK → OTLP
                                                        ↓
                                               Jaeger / Zipkin (traces)
                                               Grafana (metrics)
                                               Loki (logs)
```

---

## Design Patterns — Quick Reference

| Pattern | Intent | Key Component | Use Case |
|---------|--------|--------------|---------|
| Router Agent | Classify + route | `RoutingRule` with keywords | Customer service triage |
| Planner + Executor | Decompose + dispatch | `ExecutionPlan` DAG | Complex research tasks |
| Agent Swarm | Parallel + aggregate | `AggregationStrategy` | High-confidence requirements |
| Tool-Augmented | Single agent + many tools | `MCPServer` with rich tools | Coding assistants |
| Hybrid Enterprise | All patterns combined | All components | Production enterprise |

### Aggregation Strategies (Swarm)
```
MERGE            combine all results           → comprehensive research
MAJORITY         most common answer            → factual Q&A
BEST_CONFIDENCE  highest confidence result     → trust one agent clearly
FIRST            first successful result       → speed-critical tasks
WEIGHTED_MERGE   confidence-weighted blend     → ensemble predictions
```

---

## Common Interview Q&A (30-second answers)

**Q: What is the N×M problem that MCP solves?**
> Without MCP: N agents × M tools = N×M custom integrations. With MCP: any agent can call any tool through one standard interface. Like USB replacing different proprietary connectors.

**Q: What's the difference between A2A and ACP?**
> A2A = 1:1 agent delegation with task state machine. ACP = 1:N orchestration with workflow DAG, TTL, async fan-out, and correlation across messages. ACP coordinates multiple A2A delegations.

**Q: How does ANP establish trust without a central authority?**
> Verifiable Credentials (VCs) signed by a known issuer + DID resolution. Receiver resolves sender's DID to get their public key, verifies the Ed25519 signature, checks the VC hasn't expired, and trusts the capability claims.

**Q: Why use three memory tiers instead of one database?**
> Working (dict → Redis): fast, session-scoped, low overhead. Episodic (tag-indexed): searchable past interactions, TTL decay. Semantic (vector store): concept-similarity retrieval for knowledge base. Each tier optimized for its access pattern.

**Q: How does the circuit breaker prevent cascading failure?**
> After N failures it opens and fast-fails all subsequent calls (< 1ms). Without it, each call waits for the full timeout (30s), backing up all agents. With it, fallback triggers immediately.

**Q: Why does the audit log chain hashes?**
> Each entry contains the hash of the previous entry. If any entry is modified, inserted, or deleted, the hash chain breaks. `verify_chain()` detects tampering — required for compliance audit logs.

**Q: What is delegation depth and why is it needed?**
> A counter incremented each time a task is re-delegated. Enforcing a maximum (e.g., 3) prevents agent loops: A→B→C→A→... without depth limit would exhaust resources infinitely.

**Q: How does working memory reduce database load?**
> Agents write intermediate results to working memory. Subsequent agents in the same workflow read from memory instead of re-querying the database. Classic read-through cache pattern at the agent coordination layer.

---

## Source Code Map

```
Protocol       Source file                  Key classes
─────────────────────────────────────────────────────────────────
MCP            src/protocols/mcp.py         MCPServer, MCPClient, ToolDefinition
A2A            src/protocols/a2a.py         A2AClient, A2ATask, AgentRegistry
ACP            src/protocols/acp.py         ACPOrchestrator, MessageEnvelopeACP
ANP            src/protocols/anp.py         ANPAgent, ANPClient, DIDDocument, VerifiableCredential
Memory         src/memory.py                MemoryManager, WorkingMemory, EpisodicMemory, SemanticMemory
Security       src/security.py              SecurityGateway, AuditLog, NonceCache
Observability  src/observability.py         ObservabilityEngine, ReasoningTrace, SpanTracer, DecisionLog
Failure        src/failure/handlers.py      RetryHandler, CircuitBreaker, FallbackChain, DeadLetterQueue
Routing        src/protocol_router.py       ProtocolRouter
Agent          src/agent.py                 BaseAgent, SpecialistAgent
Patterns       src/patterns/               RouterAgent, PlannerAgent, ExecutorAgent, SwarmCoordinator
```
