# Protocol Comparison Matrix — MCP vs A2A vs ACP vs ANP

## 1. The Core Problem Each Protocol Solves

Before comparing, understand that these protocols are **not alternatives** — they are **layers** of a complete agent communication stack. Each solves a different scoping problem:

```
┌─────────────────────────────────────────────────────────────────────┐
│                      AGENT NETWORK LAYER                            │
│  ANP — How agents across ORGANIZATIONS discover and trust each other │
├─────────────────────────────────────────────────────────────────────┤
│                    ORCHESTRATION LAYER                              │
│  ACP — How an orchestrator coordinates MULTIPLE agents in workflows  │
├─────────────────────────────────────────────────────────────────────┤
│                    DELEGATION LAYER                                 │
│  A2A — How ONE agent hands off a task to ANOTHER agent              │
├─────────────────────────────────────────────────────────────────────┤
│                      TOOL ACCESS LAYER                              │
│  MCP — How an agent calls TOOLS and accesses DATA                   │
└─────────────────────────────────────────────────────────────────────┘
```

**Key insight**: In a real system, a single request might traverse all four protocols in sequence:
```
User → [ANP resolves external agent] → [ACP dispatches workflow] → [A2A delegates] → [MCP calls tool]
```

---

## 2. Side-by-Side Feature Comparison

| Dimension | MCP | A2A | ACP | ANP |
|-----------|-----|-----|-----|-----|
| **Primary purpose** | Agent ↔ Tool | Agent ↔ Agent | Orchestrator ↔ Fleet | Cross-org discovery |
| **Communication model** | Request-response | Task delegation | Async messaging | Signed peer messages |
| **Participants** | 1 agent + 1 tool server | 2 agents | 1 orchestrator + N agents | Agents across orgs |
| **Protocol base** | JSON-RPC 2.0 | HTTP REST + SSE | Message queue | HTTP + Crypto |
| **Discovery mechanism** | Tool list via `tools/list` | Agent Card in registry | Agent Card + capability | DID + Verifiable Credential |
| **Identity** | Scope tokens | Bearer + Agent Card | Correlation IDs | Cryptographic DID |
| **Statefulness** | Stateless (per call) | Stateful (task lifecycle) | Stateful (workflow) | Stateless (per message) |
| **Streaming** | Via SSE | Native SSE | Status updates | No native streaming |
| **Error handling** | JSON-RPC error codes | Task status: `failed` | `ERROR_REPORT` message | Signature rejection |
| **Authorization** | Scope-based (`db:read`) | Trust level + RBAC | Role permissions | VC capability claims |
| **Cancellation** | Not supported | `CANCELLED` status | `TASK_CANCEL` message | Not applicable |
| **Retry policy** | Client-side | Caller-side | Envelope-level | Application-level |
| **Audit trail** | Per tool call | Task history | Message log | Signed message chain |
| **Scalability unit** | Tool server | Agent instance | Orchestrator | DID document |
| **Central authority** | Tool registry | Agent registry | Orchestrator | None (decentralized) |
| **Cross-org support** | No | No | No | Native |

---

## 3. Decision Framework — Which Protocol to Use

### Step 1: What is the communication target?

```
Is the target a TOOL, DATABASE, or DATA SOURCE?
    → MCP

Is the target ANOTHER AGENT (same org)?
    → A2A

Is the target a FLEET OF AGENTS to coordinate?
    → ACP (orchestration layer)

Is the target an agent in a DIFFERENT ORGANIZATION?
    → ANP
```

### Step 2: Refine by pattern

```
Within MCP:
    Simple tool call          → tools/call
    Data retrieval            → resources/read
    Parameterized prompt      → prompts/get

Within A2A:
    Fire-and-forget task      → delegate() with stream=false
    Long-running with updates → delegate() with stream=true
    Best agent selection      → ConfidenceScorer + registry

Within ACP:
    Sequential workflow       → WorkflowStep depends_on chain
    Parallel fan-out          → depends_on=[] (all start together)
    Fan-out/fan-in            → last step depends_on all others
    Async message passing     → fire-and-forget + reply_to inbox

Within ANP:
    Discover external agent   → discover_by_capability()
    Send signed request       → send_message() with signature
    Cross-org task            → ANP handshake → switch to A2A
```

### Step 3: Decision tree (visual)

```
START: I need to communicate with something
         │
         ▼
    Is it within my org?
    ├── YES → Is it a tool/data source?
    │         ├── YES → MCP (tools/call or resources/read)
    │         └── NO  → Is it one specific agent?
    │                   ├── YES → A2A (delegate task)
    │                   └── NO  → Is it a multi-step workflow?
    │                             ├── YES → ACP (orchestrate workflow)
    │                             └── NO  → A2A (delegate to best agent)
    └── NO  → ANP (DID-based cross-org discovery)
              After trust established → use A2A/MCP normally
```

---

## 4. Message Format Comparison

### MCP — JSON-RPC 2.0

```json
{
  "jsonrpc": "2.0",
  "id": "call-001",
  "method": "tools/call",
  "params": {
    "name": "database_query",
    "arguments": { "table": "users", "filters": {"active": "true"} }
  }
}
```

**Characteristics**: Minimal wrapper, synchronous by design, method-centric.

---

### A2A — Task Object

```json
{
  "task_id": "task-001",
  "sender_id": "planner-agent",
  "receiver_id": "finance-agent",
  "capability": "financial_analysis",
  "status": "submitted",
  "priority": "high",
  "input": { "query": "Q3 revenue for ACME" },
  "stream": false,
  "metadata": { "delegation_depth": 1 }
}
```

**Characteristics**: Rich state tracking, history append-only log, explicit lifecycle.

---

### ACP — Message Envelope

```json
{
  "message_id": "msg-001",
  "type": "TASK_DISPATCH",
  "from": "orchestrator",
  "to": "finance-agent",
  "correlation_id": "wf-001",
  "ttl_seconds": 300,
  "retry_policy": { "max_retries": 3, "backoff_strategy": "exponential" },
  "trace_context": { "trace_id": "trace-001", "span_id": "span-002" },
  "payload": { "task_id": "task-001", "capability": "financial_analysis" }
}
```

**Characteristics**: Routing metadata separate from payload, built-in retry, TTL, distributed tracing.

---

### ANP — Signed Message

```json
{
  "message_id": "anp-001",
  "sender_did": "did:web:agents.acme.com:planner",
  "receiver_did": "did:web:agents.partner.org:data-agent",
  "message_type": "CAPABILITY_REQUEST",
  "timestamp": 1735689600.0,
  "payload": { "capability": "data_analysis", "task": "Analyze ACME Q3" },
  "signature": "H8zPq7..."
}
```

**Characteristics**: Every field signed, no central authority, DID-based identity.

---

## 5. Trust Model Comparison

| Protocol | Trust Basis | Verification Method | Scope |
|----------|------------|--------------------|----|
| MCP | Scope token | Server checks `required_scope` | Per tool call |
| A2A | Agent Card trust level + JWT | Registry + token signature | Per agent |
| ACP | Role + correlation | Orchestrator role validation | Per workflow |
| ANP | Verifiable Credential | Ed25519 signature + VC expiry | Per organization |

### Trust Escalation Path

```
No trust            Minimal trust        Moderate trust       Full trust
     │                    │                    │                  │
     ▼                    ▼                    ▼                  ▼
  Unknown         scope:read only        JWT + RBAC         DID + VC signed
  (reject)        (MCP tool call)     (A2A/ACP agent)      (ANP cross-org)
```

---

## 6. Failure Mode Comparison

| Failure | MCP behavior | A2A behavior | ACP behavior | ANP behavior |
|---------|-------------|-------------|-------------|-------------|
| Tool unavailable | Error code `-32001` | Task `failed` | `ERROR_REPORT` message | Signature fails |
| Timeout | Client-side exception | Task `failed`, retry | TTL expires → DLQ | No response |
| Auth failure | Error code `-32003` | Task rejected | Message discarded | VC invalid |
| Schema invalid | Error code `-32602` | Task rejected pre-start | Payload validation error | Message rejected |
| Infinite loop | N/A | `delegation_depth` limit | Workflow cycle detection | N/A |

---

## 7. Performance Characteristics

| Protocol | Latency | Throughput | State overhead |
|----------|---------|-----------|---------------|
| MCP | Lowest (~ms) | Highest (stateless) | None |
| A2A | Low-Medium | High (per task) | Task object (~2KB) |
| ACP | Medium | Medium (workflow mgmt) | Workflow state (~10KB) |
| ANP | Highest (DID resolution) | Low (crypto overhead) | DID document + VC |

**Latency breakdown for a full request:**
```
ANP discovery:       50–200ms  (HTTP DID resolution, cacheable)
ACP workflow setup:  5–20ms    (in-process)
A2A delegation:      10–50ms   (network hop to agent)
MCP tool call:       5–500ms   (tool execution time)
─────────────────────────────
Uncached total:      70–770ms
Cached total:        20–570ms  (ANP cached, ACP in-memory)
```

---

## 8. When NOT to Use Each Protocol

### Don't use MCP when...
- The target is another AI agent, not a tool (use A2A)
- You need stateful conversation across multiple calls (use ACP with context)
- You need cross-org trust (use ANP first, then MCP through established channel)

### Don't use A2A when...
- You need to coordinate more than 2–3 agents with complex dependencies (use ACP)
- You're calling a non-agent tool or data source (use MCP)
- You need decentralized, cross-org discovery (use ANP → A2A)

### Don't use ACP when...
- You have a simple 1:1 agent delegation (overkill; use A2A)
- You need external agents you don't control (ANP handles discovery)
- Response time is critical — workflow overhead adds latency

### Don't use ANP when...
- All agents are in your org (simpler: A2A registry is sufficient)
- You're building a prototype or MVP (ANP complexity is production-grade)
- Agents have no stable domain (DIDs require HTTP endpoints or blockchain)

---

## 9. Protocol Combination Patterns

### Pattern A: Tool-Augmented Specialist (MCP only)
```
Agent → MCP → [web_search, db_query, file_read]
```
Use when: Single-domain tasks, no delegation needed.

### Pattern B: Peer Delegation (A2A only)
```
GeneralistAgent → A2A → SpecialistAgent
```
Use when: Two-agent system, clear domain handoff.

### Pattern C: Orchestrated Workflow (ACP + A2A + MCP)
```
Orchestrator → ACP → WorkflowSteps
                         │ A2A
                    FinanceAgent → MCP → db_query
                    LegalAgent   → MCP → doc_search
```
Use when: Multi-step, multi-domain enterprise task.

### Pattern D: Full Stack (All 4 Protocols)
```
External partner → ANP → TrustHandshake
                            │ ACP
                         Orchestrator → Workflow
                                           │ A2A
                                       SpecialistAgent → MCP → Tools
```
Use when: Cross-organizational enterprise system.

---

## 10. Implementation Priority Guide

If you're building a multi-agent system from scratch, implement in this order:

1. **MCP first** — Tools are the foundation. Without tools, agents can't do anything.
2. **A2A second** — Once agents can use tools, enable them to delegate to each other.
3. **ACP third** — Once you have multiple A2A agents, add orchestration for complex workflows.
4. **ANP last** — Only needed when you need cross-org collaboration.

```
Week 1–2:  MCP tool server + 2–3 tools → single agent with capabilities
Week 3–4:  A2A registry + Agent Cards → agents can delegate to each other
Week 5–6:  ACP orchestrator + workflows → multi-step business processes
Week 7+:   ANP DIDs + VCs → external partner agents
```

---

## 11. Interview Questions: Protocol Comparison

**Q: "Why do we need four protocols? Can't one protocol do everything?"**
> Each protocol optimizes for a different scope. MCP is stateless and fast (tool calls). A2A has rich task state (delegation). ACP adds workflow orchestration (multi-agent coordination). ANP is cryptographically decentralized (cross-org). Using one protocol for all would mean either bloating simple tool calls with workflow state, or lacking the security model needed for cross-org trust.

**Q: "How do MCP and A2A interact?"**
> They operate at different layers. An A2A task triggers an agent to execute, and that agent uses MCP internally to call its tools. A2A delegates *what* to do; MCP handles *how* to do it with tools.

**Q: "What's the difference between A2A and ACP?"**
> A2A = doctor-to-doctor referral (1:1 with full task handoff and history). ACP = hospital administration (1:N orchestration with workflow DAG, TTL, correlation IDs, and async fan-out). ACP coordinates multiple A2A delegations in a structured workflow.

**Q: "When would ANP degrade to A2A?"**
> ANP is the discovery and trust-establishment layer. Once an external agent is discovered via ANP (DID resolved, VC verified), subsequent communication can use A2A for task delegation — the trust is already established. ANP doesn't run on every message; it runs once to bootstrap trust.
