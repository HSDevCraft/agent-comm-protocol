# Documentation Index

Nine deep-dive modules covering every concept and implementation in the agent communication system.
Each document includes: conceptual foundation → JSON schemas → Python implementation → production considerations.

---

## Protocol Deep Dives

| Doc | Title | Key Concepts |
|-----|-------|-------------|
| [01](01_mcp_deep_dive.md) | **MCP — Model Context Protocol** | JSON-RPC 2.0 wire format, tool registration schema, scope-based RBAC, content types (`text`, `image`, `resource`), custom tool building, batch calls |
| [02](02_a2a_deep_dive.md) | **A2A — Agent-to-Agent Protocol** | AgentCard discovery schema, task state machine (8 states), streaming via SSE, capability scoring algorithm, delegation depth enforcement |
| [03](03_acp_deep_dive.md) | **ACP — Agent Communication Protocol** | Message envelope schema, 7 message types, DAG workflow execution, async vs sync communication, TTL/expiry, retry policies, correlation IDs |
| [04](04_anp_deep_dive.md) | **ANP — Agent Network Protocol** | W3C DID methods (`did:web`, `did:key`), DID Document schema, Verifiable Credentials, signed ANP messages, cross-org discovery flow |

## Infrastructure Deep Dives

| Doc | Title | Key Concepts |
|-----|-------|-------------|
| [05](05_memory_architecture.md) | **Memory Architecture** | 3-tier model (Working / Episodic / Semantic), `MemoryObject` schema, access control (`owner`, `readers`, `public`), lazy TTL eviction, vector store upgrade path |
| [06](06_security_governance.md) | **Security & Governance** | `AgentIdentityToken` structure, HMAC-SHA256 → RS256 upgrade path, RBAC scope table, prompt injection defense layers, `NonceCache` replay prevention, hash-chained `AuditLog` |
| [07](07_observability.md) | **Observability & Explainability** | `ReasoningTrace` schema, `SpanTracer` DAG, confidence scoring formula, `DecisionLog` analytics API, debug hooks, OpenTelemetry / OTLP export |
| [08](08_design_patterns.md) | **Design Patterns** | Router Agent, Planner + Executor, Agent Swarm, Tool-Augmented Agent, Hybrid Enterprise System — each with full Python implementation |
| [09](09_failure_resilience.md) | **Failure Resilience** | Failure taxonomy, exponential backoff math, circuit breaker FSM (CLOSED / HALF_OPEN / OPEN), `FallbackChain`, Dead Letter Queue, human escalation webhook, production checklist |

---

## Quick Navigation by Topic

**"How does the agent call a tool?"**
→ [01 MCP Deep Dive](01_mcp_deep_dive.md) — tool registration, scope enforcement, JSON-RPC call lifecycle

**"How does one agent delegate to another?"**
→ [02 A2A Deep Dive](02_a2a_deep_dive.md) — AgentCard schema, task lifecycle, streaming, max delegation depth

**"How does the orchestrator coordinate a multi-step workflow?"**
→ [03 ACP Deep Dive](03_acp_deep_dive.md) — workflow DAG, message envelope, retry policy, TTL

**"How do agents across companies discover each other?"**
→ [04 ANP Deep Dive](04_anp_deep_dive.md) — DIDs, Verifiable Credentials, signed cross-org messages

**"How is agent context shared across a session?"**
→ [05 Memory Architecture](05_memory_architecture.md) — Working / Episodic / Semantic tiers, access control

**"How do I prevent prompt injection, spoofing, and replay attacks?"**
→ [06 Security & Governance](06_security_governance.md) — tokens, RBAC, nonce cache, audit chain

**"How do I debug why an agent made a particular decision?"**
→ [07 Observability](07_observability.md) — ReasoningTrace, SpanTracer, low-confidence queries

**"Which architecture pattern fits my use case?"**
→ [08 Design Patterns](08_design_patterns.md) — Router, Planner+Executor, Swarm, Hybrid

**"How do I make the system resilient to failures?"**
→ [09 Failure Resilience](09_failure_resilience.md) — retry, circuit breaker, fallback chain, DLQ

---

## Schema Reference

The canonical JSON schemas for all 8 core objects are in [`../agent_protocol_concepts.md`](../agent_protocol_concepts.md):

| Schema | Description |
|--------|-------------|
| `AgentCard` | Agent discovery and capability advertisement |
| `TaskLifecycle` | A2A task state transitions |
| `MessageEnvelope` | ACP orchestration message wrapper |
| `MemoryObject` | SAMEP-style shared memory entry |
| `AgentIdentityToken` | AIP-style identity and authorization token |
| `ReasoningTrace` | Per-decision explainability record |
| `MCPToolCall` | JSON-RPC 2.0 tool invocation |
| `DIDDocument` | ANP decentralized identifier document |

---

## How the Docs Relate to Source Code

```
docs/01_mcp_deep_dive.md       →  src/protocols/mcp.py
docs/02_a2a_deep_dive.md       →  src/protocols/a2a.py
docs/03_acp_deep_dive.md       →  src/protocols/acp.py
docs/04_anp_deep_dive.md       →  src/protocols/anp.py
docs/05_memory_architecture.md →  src/memory.py
docs/06_security_governance.md →  src/security.py
docs/07_observability.md       →  src/observability.py
docs/08_design_patterns.md     →  src/patterns/  (router_agent, planner_executor, swarm)
docs/09_failure_resilience.md  →  src/failure/handlers.py
```

Each doc's **implementation** section walks through the corresponding source file class by class.
