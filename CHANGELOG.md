# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned
- HTTP transport layer for real inter-process A2A and MCP calls
- Redis-backed MessageBus for distributed deployments
- OpenTelemetry span export for Jaeger/Zipkin
- LLM-powered PlannerAgent decomposition (GPT-4o / Claude / Ollama)
- ANP DID resolution via real `did:web` HTTP lookup

---

## [1.0.0] — 2025-01-01

### Added

**Protocols**
- `MCP` — Model Context Protocol: JSON-RPC 2.0 tool server/client, scope-based authorization, 4 built-in tools (`web_search`, `database_query`, `calculator`, `file_reader`)
- `A2A` — Agent-to-Agent Protocol: `AgentCard` registry, `A2ATask` lifecycle (submitted→working→completed/failed/cancelled), async streaming via `StreamChannel`
- `ACP` — Agent Communication Protocol: `MessageEnvelopeACP` with TTL/retry/correlation, `ACPOrchestrator` with DAG-based workflow execution
- `ANP` — Agent Network Protocol: `DIDDocument` (W3C did:web), `VerifiableCredential` (W3C VC), signed `ANPMessage`, cross-org capability discovery

**Core Engine**
- `BaseAgent` — unified decision engine routing via `ProtocolRouter`; MCP tool calling, A2A delegation, ACP orchestration, ANP broadcast, memory caching
- `SpecialistAgent` — domain-specialist subclass with `domain_knowledge` lookup
- `ProtocolRouter` — 6-way routing: LOCAL_EXECUTION / TOOL_CALL / AGENT_DELEGATION / ORCHESTRATION / NETWORK_BROADCAST / FALLBACK

**Memory (SAMEP-style)**
- `WorkingMemory` — in-process, session-scoped, TTL-enforced, access-controlled
- `EpisodicMemory` — tag-indexed past interactions, cross-session persistence
- `SemanticMemory` — vector-ready factual knowledge store (keyword-based for dev)

**Security (AIP-style)**
- `SecurityGateway` — token issuance, validation, scope enforcement, delegation depth check
- `AgentIdentityToken` — HMAC-SHA256 signed JWT-style token with nonce, scopes, role
- `AuditLog` — append-only, hash-chained audit trail
- Prompt injection detection — 8 pattern regex filter with auto-sanitization
- `NonceCache` — replay attack prevention via short-lived nonce tracking

**Observability**
- `ReasoningTrace` — per-decision record: decision type, confidence, alternatives, protocol
- `SpanTracer` — OpenTelemetry-compatible span tree (trace_id / span_id / parent_span_id)
- `ConfidenceScorer` — weighted multi-signal agent scoring
- `DecisionLog` — queryable append-only log of all agent decisions

**Design Patterns**
- `RouterAgent` — declarative `RoutingRule`-based traffic controller
- `PlannerAgent` + `ExecutorAgent` — query decomposition with dependency-ordered parallel dispatch
- `SwarmCoordinator` + `SwarmAgent` — N-agent parallel execution with MERGE/MAJORITY/BEST_CONFIDENCE/FIRST aggregation

**Failure Resilience**
- `RetryHandler` — exponential backoff with jitter
- `FallbackChain` — ordered strategy chain
- `CircuitBreaker` — CLOSED/HALF_OPEN/OPEN FSM with configurable thresholds
- `DeadLetterQueue` — unprocessable message capture
- `HumanEscalationHook` — automated → human handoff trigger
- `FailureOrchestrator` — unified resilience wrapper

**Async Messaging**
- `MessageBus` — pub/subscribe broker (in-process, Redis-upgradeable)
- `Channel` — point-to-point async priority queue
- `StreamChannel` — async generator streaming (SSE simulation)

**Documentation & Examples**
- `README.md` — architecture diagram, protocol breakdown, execution flow
- `agent_protocol_concepts.md` — 8 full JSON schemas with WHY/when-to-use
- `docs/` — 9 deep-dive markdown modules
- `examples/basic_delegation.py` — 5 working demos
- `examples/swarm_example.py` — 6 working demos including full pipeline

### Fixed
- `ProtocolRouter._route_by_keywords()` — added local capabilities fallback before FALLBACK
- `src/__init__.py` — corrected export from `Agent` → `BaseAgent`
