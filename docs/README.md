# Documentation Index

Thirteen deep-dive modules covering every concept and implementation in the agent communication system.
Each document includes: conceptual foundation → mental models → JSON schemas → Python implementation → production considerations → interview Q&A.

---

## Protocol Deep Dives

| Doc | Title | Sections |
|-----|-------|---------|
| [01](01_mcp_deep_dive.md) | **MCP — Model Context Protocol** | JSON-RPC 2.0 wire format, tool schema, scope RBAC, tool call lifecycle, content types, custom tools, advanced patterns, **pitfalls**, conceptual model, **interview Q&A** |
| [02](02_a2a_deep_dive.md) | **A2A — Agent-to-Agent Protocol** | AgentCard schema, task state machine (8 states), SSE streaming, capability scoring, delegation depth, advanced patterns, **anti-patterns**, conceptual model, **interview Q&A** |
| [03](03_acp_deep_dive.md) | **ACP — Agent Communication Protocol** | Message envelope schema, 10 message types, DAG workflow execution, async vs sync, TTL/expiry, retry policies, correlation IDs, Kafka comparison |
| [04](04_anp_deep_dive.md) | **ANP — Agent Network Protocol** | DID methods, DID Document schema, Verifiable Credentials, Ed25519 signatures, cross-org discovery flow, centralized vs decentralized |

## Infrastructure Deep Dives

| Doc | Title | Sections |
|-----|-------|---------|
| [05](05_memory_architecture.md) | **Memory Architecture** | 3-tier model (Working / Episodic / Semantic), `MemoryObject` schema, access control, lazy TTL eviction, tag indexing, vector search, production upgrade path |
| [06](06_security_governance.md) | **Security & Governance** | Threat model, JWT token structure, RS256 upgrade path, RBAC scope table, prompt injection defense, nonce replay prevention, hash-chained `AuditLog`, production checklist |
| [07](07_observability.md) | **Observability & Explainability** | `ReasoningTrace` schema, `SpanTracer` DAG, confidence scoring formula, `DecisionLog` analytics API, debug hooks, OpenTelemetry / OTLP export, EU AI Act compliance |
| [08](08_design_patterns.md) | **Design Patterns** | Router Agent, Planner+Executor, Swarm, Tool-Augmented, Hybrid Enterprise, Self-Healing, **pattern selection guide**, **anti-patterns**, **interview Q&A** |
| [09](09_failure_resilience.md) | **Failure Resilience** | Failure taxonomy, exponential backoff math, circuit breaker FSM, `FallbackChain`, Dead Letter Queue, human escalation, timeout hierarchy, production checklist |

## Synthesis & Reference

| Doc | Title | Purpose |
|-----|-------|---------|
| [10](10_protocol_comparison_matrix.md) | **Protocol Comparison Matrix** | Full side-by-side feature comparison, decision framework (flowchart), trust models, performance characteristics, when NOT to use each protocol, implementation priority guide |
| [11](11_end_to_end_walkthrough.md) | **End-to-End Walkthrough** | Complete traced scenario: ANP discovery → ACP orchestration → A2A delegation → MCP tool calls, including all message payloads, state transitions, memory operations, security checks, and failure scenarios |
| [12](12_mental_models.md) | **Mental Models & Analogies** | Hospital analogy (complete system), power outlet model (MCP), postal mail model (A2A), project manager model (ACP), TCP/IP model (ANP), brain layer model (memory), Swiss cheese model (security), 12 common misconceptions |
| [13](13_quick_reference.md) | **Quick Reference Cheat Sheet** | All error codes, all scope patterns, capability scoring formula, confidence thresholds, state machines, message types, memory TTL guidelines, RBAC table, backoff formula, circuit breaker states, timeout guidelines, source code map |

---

## Learning Paths

### Path 1: First-Time Builder (Building your first multi-agent system)
```
12 (Mental Models) → 13 (Quick Reference) → 01 (MCP) → 02 (A2A) → 08 (Patterns)
```
**Goal**: Understand the concepts, build a working Router+Specialist system in 1–2 days.

### Path 2: Protocol Engineer (Deep protocol understanding)
```
10 (Comparison Matrix) → 01 (MCP) → 02 (A2A) → 03 (ACP) → 04 (ANP) → 11 (E2E Walkthrough)
```
**Goal**: Understand how all 4 protocols interact. Implement end-to-end scenarios.

### Path 3: Production Engineer (Building production-grade systems)
```
06 (Security) → 07 (Observability) → 09 (Failure) → 05 (Memory) → 11 (E2E Walkthrough)
```
**Goal**: Harden an existing system for production: security, resilience, observability.

### Path 4: Interview Preparation
```
12 (Mental Models) → 13 (Quick Reference) → 10 (Comparison) → 11 (E2E Walkthrough)
```
**Goal**: Master the "why" behind each protocol, prepare for system design interviews.

### Path 5: Architecture Review (Evaluating design decisions)
```
10 (Comparison Matrix) → 08 (Patterns) → 11 (E2E Walkthrough) → 09 (Failure)
```
**Goal**: Evaluate your architecture: right protocol choices, right patterns, failure handling.

---

## Quick Navigation by Question

**"Which protocol should I use for my use case?"**
→ [10 Protocol Comparison Matrix](10_protocol_comparison_matrix.md) §3 — Decision Framework flowchart

**"How does the agent call a tool?"**
→ [01 MCP Deep Dive](01_mcp_deep_dive.md) — tool registration, scope enforcement, JSON-RPC lifecycle, pitfalls

**"How does one agent delegate to another?"**
→ [02 A2A Deep Dive](02_a2a_deep_dive.md) — AgentCard schema, task lifecycle, streaming, anti-patterns

**"How does the orchestrator coordinate a multi-step workflow?"**
→ [03 ACP Deep Dive](03_acp_deep_dive.md) — workflow DAG, message envelope, retry policy, TTL

**"How do agents across companies discover each other?"**
→ [04 ANP Deep Dive](04_anp_deep_dive.md) — DIDs, Verifiable Credentials, Ed25519 signatures

**"How is agent context shared across a session?"**
→ [05 Memory Architecture](05_memory_architecture.md) — Working / Episodic / Semantic tiers, access control

**"How do I prevent prompt injection, spoofing, and replay attacks?"**
→ [06 Security & Governance](06_security_governance.md) — tokens, RBAC, nonce cache, audit chain

**"How do I debug why an agent made a particular decision?"**
→ [07 Observability](07_observability.md) — ReasoningTrace, SpanTracer, low-confidence queries

**"Which architecture pattern fits my use case?"**
→ [08 Design Patterns](08_design_patterns.md) §7 — Pattern Selection Guide (decision framework)

**"How do I make the system resilient to failures?"**
→ [09 Failure Resilience](09_failure_resilience.md) — retry, circuit breaker, fallback chain, DLQ

**"I need to understand everything in 30 minutes"**
→ [13 Quick Reference](13_quick_reference.md) — all error codes, formulas, schemas, patterns in one page

**"How do all the protocols fit together end-to-end?"**
→ [11 End-to-End Walkthrough](11_end_to_end_walkthrough.md) — complete traced request through all 4 protocols

**"I understand the code but not the WHY"**
→ [12 Mental Models](12_mental_models.md) — analogies, common misconceptions, conceptual models

---

## Schema Reference

The canonical JSON schemas for all 8 core objects are in [`../agent_protocol_concepts.md`](../agent_protocol_concepts.md):

| Schema | Protocol | Key Fields |
|--------|----------|-----------|
| `AgentCard` | A2A | `agent_id`, `capabilities`, `endpoint`, `input_schema` |
| `TaskLifecycle` | A2A | `task_id`, `status`, `history`, `delegation_depth` |
| `MessageEnvelope` | ACP | `message_id`, `type`, `correlation_id`, `reply_to`, `ttl_seconds` |
| `MemoryObject` | SAMEP | `memory_id`, `tier`, `content`, `access_control`, `ttl_seconds` |
| `AgentIdentityToken` | AIP/JWT | `sub`, `exp`, `agent_claims.capabilities`, `nonce` |
| `ReasoningTrace` | Observability | `trace_id`, `decision`, `confidence`, `alternatives_considered` |
| `MCPToolCall` | MCP | `name`, `arguments`, `result`, `scope_used` |
| `DIDDocument` | ANP | `id`, `verificationMethod`, `authentication`, `service` |

---

## How the Docs Relate to Source Code

```
docs/01_mcp_deep_dive.md           →  src/protocols/mcp.py
docs/02_a2a_deep_dive.md           →  src/protocols/a2a.py
docs/03_acp_deep_dive.md           →  src/protocols/acp.py
docs/04_anp_deep_dive.md           →  src/protocols/anp.py
docs/05_memory_architecture.md     →  src/memory.py
docs/06_security_governance.md     →  src/security.py
docs/07_observability.md           →  src/observability.py
docs/08_design_patterns.md         →  src/patterns/ (router_agent, planner_executor, swarm)
docs/09_failure_resilience.md      →  src/failure/handlers.py
docs/10_protocol_comparison_matrix.md → (conceptual — spans all protocols)
docs/11_end_to_end_walkthrough.md  → (conceptual — spans all src/ modules)
docs/12_mental_models.md           → (conceptual — no single source file)
docs/13_quick_reference.md         → (reference — spans all src/ modules)
```

Each doc's **implementation** section walks through the corresponding source file class by class.

---

## Document Enhancement Summary

| Doc | Added Sections |
|-----|---------------|
| `01_mcp_deep_dive.md` | §10 Advanced Tool Patterns, §11 Common Pitfalls, §12 Conceptual Model, §13 Interview Q&A |
| `02_a2a_deep_dive.md` | §10 Advanced A2A Patterns, §11 Anti-Patterns, §12 Conceptual Model, §13 Interview Q&A |
| `08_design_patterns.md` | Pattern 6 Self-Healing, Pattern 7 Selection Guide, Pattern 8 Anti-Patterns, Pattern 9 Interview Q&A |
| `10_protocol_comparison_matrix.md` | **NEW** — Full comparison, decision framework, performance characteristics |
| `11_end_to_end_walkthrough.md` | **NEW** — Complete traced scenario with all protocols |
| `12_mental_models.md` | **NEW** — 12 mental models, analogies, misconceptions |
| `13_quick_reference.md` | **NEW** — Complete cheat sheet for all protocols, patterns, formulas |
