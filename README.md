# Agent Communication Protocol

> Production-grade, explainable, and extensible multi-agent communication system implementing **MCP**, **A2A**, **ACP**, and **ANP** — four complementary protocols that together form a complete agent ecosystem.

[![CI](https://github.com/your-org/agent-comm-protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/agent-comm-protocol/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-blue.svg)](https://www.python.org)
[![Tests](https://img.shields.io/badge/tests-291%20passed-brightgreen.svg)](#testing)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/core%20deps-zero-green.svg)](#quick-start)

---

## Table of Contents

- [Overview](#overview)
- [Why Multiple Protocols](#why-multiple-protocols)
- [Architecture](#architecture)
- [Protocol Breakdown](#protocol-breakdown)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Development](#development)
- [Testing](#testing)
- [Documentation](#documentation)
- [Execution Flow](#execution-flow)
- [Security & Governance](#security--governance)
- [Observability & Explainability](#observability--explainability)
- [Design Patterns](#design-patterns)
- [Failure Handling](#failure-handling)

---

## Overview

Enterprise AI requires networks of specialized agents that collaborate on complex tasks no single model can handle. This project provides:

- **Complete protocol implementations** for MCP, A2A, ACP, and ANP — zero external dependencies required
- **Production patterns** — Router Agent, Planner+Executor, Agent Swarm, Tool-Augmented Agent
- **Security** — HMAC-signed tokens, RBAC, prompt injection detection, tamper-evident audit log
- **Observability** — per-decision `ReasoningTrace`, confidence scoring, `SpanTracer`, `DecisionLog`
- **Failure resilience** — Retry (backoff+jitter), CircuitBreaker (FSM), FallbackChain, DLQ, human escalation
- **291 tests** covering every module (unit + integration)

### Why Multi-Agent Systems

| Need | Single Agent | Multi-Agent |
|------|-------------|-------------|
| Complex task decomposition | Manual prompt engineering | Planner delegates sub-tasks |
| Specialized capability | One model handles all | Domain-specific agents |
| Scalability | Vertical only | Horizontal swarm |
| Fault tolerance | Total failure | Graceful degradation per agent |
| Auditing | Single log | Per-agent audit trail + chain verification |

---

## Why Multiple Protocols

Each protocol solves a **different scoping problem**. They are complementary, not competing:

```
Protocol  │ Scope                    │ Analogy
──────────┼──────────────────────────┼────────────────────────────────────
MCP       │ Agent ↔ Tool / Data      │ USB: plug any tool into any agent
A2A       │ Agent ↔ Agent            │ Referral letter: hand off a patient
ACP       │ Orchestrator ↔ Agents    │ Project manager assigning tasks
ANP       │ Agent ↔ Internet (DID)   │ TCP/IP: decentralized cross-org net
```

> **Hospital analogy:**
> ACP = hospital admin routing patients to departments  
> A2A = doctor-to-doctor referral with patient record handoff  
> MCP = doctor using standardized lab / imaging tools  
> ANP = federated hospital network across cities (decentralized)  
> SAMEP = shared patient medical history  
> AIP = doctor credentials and license verification

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        APPLICATION LAYER                            │
│  User Interface │ REST API │ WebSocket │ CLI │ SDK Clients          │
└────────────────────────────┬────────────────────────────────────────┘
                             │ HTTP / WebSocket
┌────────────────────────────▼────────────────────────────────────────┐
│                      ORCHESTRATION LAYER                            │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐     │
│  │   ACP Orchestrator   │  │     A2A Task Delegation Bus      │     │
│  │  - Workflow planning │  │  - Agent discovery (AgentCard)   │     │
│  │  - Task assignment   │  │  - Task lifecycle management     │     │
│  │  - Result aggregation│  │  - Streaming updates (SSE)       │     │
│  └──────────────────────┘  └──────────────────────────────────┘     │
└────────────────────────────┬────────────────────────────────────────┘
                             │ Async message queue / direct call
┌────────────────────────────▼────────────────────────────────────────┐
│                     COMMUNICATION LAYER                             │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │              Protocol Router (Decision Engine)             │     │
│  │   local_exec? → run here   │  tool_call? → MCP client     │     │
│  │   agent_call? → A2A client │  broadcast? → ANP network    │     │
│  └────────────────────────────────────────────────────────────┘     │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────┐    │
│  │ MCP Client  │  │ A2A Client  │  │     ANP Client           │    │
│  └─────────────┘  └─────────────┘  └──────────────────────────┘    │
└──────┬──────────────────────────────────────┬───────────────────────┘
       │ JSON-RPC 2.0                          │ A2A HTTP / ANP P2P
┌──────▼──────────────┐          ┌────────────▼──────────────────────┐
│     TOOL LAYER      │          │         AGENT NETWORK              │
│  (MCP Protocol)     │          │     (A2A + ANP Protocols)         │
│  ┌───────────────┐  │          │  ┌────────────┐  ┌─────────────┐  │
│  │ web_search    │  │          │  │ CodeAgent  │  │ LegalAgent  │  │
│  │ calculator    │  │          │  │FinanceAgent│  │ DataAgent   │  │
│  │ database_query│  │          │  └────────────┘  └─────────────┘  │
│  │ file_reader   │  │          │  Discovery via AgentCard registry  │
│  └───────────────┘  │          └───────────────────────────────────┘
└─────────────────────┘                       │
┌─────────────────────────────────────────────▼───────────────────────┐
│                        MEMORY LAYER  (SAMEP-style)                  │
│  Working Memory (in-flight) │ Episodic (history) │ Semantic (facts) │
└─────────────────────────────────────────────────────────────────────┘
                                              │
┌─────────────────────────────────────────────▼───────────────────────┐
│              IDENTITY & SECURITY LAYER  (AIP-style)                 │
│  HMAC-signed tokens │ RBAC │ Audit Log │ Nonce cache │ Injection det│
└─────────────────────────────────────────────────────────────────────┘
```

---

## Protocol Breakdown

### MCP — Model Context Protocol

Standardizes agent-to-tool access via JSON-RPC 2.0. Universal plugin standard.

**Tool call:**
```json
{
  "jsonrpc": "2.0", "id": "req-001", "method": "tools/call",
  "params": { "name": "web_search", "arguments": { "query": "LLM benchmarks 2024", "max_results": 5 } }
}
```
**Response:**
```json
{
  "jsonrpc": "2.0", "id": "req-001",
  "result": { "content": [{ "type": "text", "text": "1. MMLU: Massive Multitask..." }], "isError": false }
}
```
**Built-in tools:** `web_search` (scope: `search:read`), `database_query` (scope: `db:read`), `calculator`, `file_reader`

→ **Deep dive:** [`docs/01_mcp_deep_dive.md`](docs/01_mcp_deep_dive.md)

---

### A2A — Agent-to-Agent Protocol

Defines agent discovery (AgentCard), task lifecycle, and streaming delegation.

**Agent Card:**
```json
{
  "agent_id": "finance-agent-v2",
  "name": "FinanceAgent",
  "version": "2.1.0",
  "capabilities": ["financial_analysis", "portfolio_optimization", "risk_assessment"],
  "endpoint": "https://agents.internal/finance",
  "trust_level": "internal",
  "auth": { "type": "bearer", "scope": "finance:read" }
}
```
**Task lifecycle:** `SUBMITTED → ACCEPTED → WORKING → (STREAMING) → COMPLETED | FAILED | CANCELLED`

→ **Deep dive:** [`docs/02_a2a_deep_dive.md`](docs/02_a2a_deep_dive.md)

---

### ACP — Agent Communication Protocol

Governs orchestration-level messaging, workflow DAG execution, TTL, retry policies, and correlation.

**Message envelope:**
```json
{
  "message_id": "msg-xyz-789",
  "type": "TASK_DISPATCH",
  "from": "orchestrator",
  "to": "finance-agent-v2",
  "correlation_id": "workflow-2024-001",
  "ttl_seconds": 300,
  "retry_policy": { "max_retries": 3, "backoff_strategy": "exponential" },
  "payload": { "task": "financial_analysis", "params": { "query": "Q3 revenue" } }
}
```

→ **Deep dive:** [`docs/03_acp_deep_dive.md`](docs/03_acp_deep_dive.md)

---

### ANP — Agent Network Protocol

Decentralized, cross-org agent discovery using W3C DIDs and Verifiable Credentials.

**DID-based identity:**
```json
{
  "did": "did:web:agents.acme.com:finance-agent",
  "service": [{ "type": "AgentEndpoint", "serviceEndpoint": "https://agents.acme.com/finance" }],
  "verificationMethod": [{ "type": "Ed25519VerificationKey2020", "publicKeyMultibase": "z6Mkf5r..." }]
}
```

→ **Deep dive:** [`docs/04_anp_deep_dive.md`](docs/04_anp_deep_dive.md)

---

## Project Structure

```
agent-comm-protocol/
├── README.md
├── agent_protocol_concepts.md    ← Canonical schema reference (8 schemas)
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── Makefile                      ← make test-fast | test | lint | format
├── pyproject.toml                ← Build config, linting, coverage settings
├── requirements.txt              ← Optional deps (core runs on stdlib)
├── .env.example
├── .gitignore
│
├── src/
│   ├── _logging.py               ← structlog / stdlib logging shim
│   ├── agent.py                  ← BaseAgent + SpecialistAgent
│   ├── protocol_router.py        ← 6-way routing decision engine
│   ├── messaging.py              ← MessageBus, Channel, StreamChannel
│   ├── memory.py                 ← Working / Episodic / Semantic memory
│   ├── security.py               ← Tokens, RBAC, AuditLog, injection detection
│   ├── observability.py          ← ReasoningTrace, SpanTracer, DecisionLog
│   ├── protocols/
│   │   ├── mcp.py                ← Model Context Protocol (JSON-RPC 2.0)
│   │   ├── a2a.py                ← Agent-to-Agent Protocol
│   │   ├── acp.py                ← Agent Communication Protocol
│   │   └── anp.py                ← Agent Network Protocol (DID + VC)
│   ├── patterns/
│   │   ├── router_agent.py       ← Router Agent pattern
│   │   ├── planner_executor.py   ← Planner + Executor pattern
│   │   └── swarm.py              ← Agent Swarm pattern
│   └── failure/
│       └── handlers.py           ← Retry, CircuitBreaker, Fallback, DLQ, Escalation
│
├── examples/
│   ├── basic_delegation.py       ← Demo: MCP, A2A, security, retry, memory
│   └── swarm_example.py          ← Demo: swarm, planner-executor, router, ANP
│
├── tests/
│   ├── conftest.py               ← Shared fixtures
│   ├── unit/
│   │   ├── test_mcp.py           ← 40 tests
│   │   ├── test_a2a.py           ← 35 tests
│   │   ├── test_acp.py           ← 42 tests
│   │   ├── test_agent.py         ← 38 tests
│   │   ├── test_memory.py        ← 32 tests
│   │   ├── test_security.py      ← 34 tests
│   │   ├── test_observability.py ← 28 tests
│   │   ├── test_protocol_router.py ← 35 tests
│   │   └── test_failure_handlers.py ← 38 tests
│   └── integration/
│       └── test_full_pipeline.py ← 29 end-to-end tests
│
└── docs/
    ├── README.md                 ← Documentation index
    ├── 01_mcp_deep_dive.md       ← MCP: JSON-RPC 2.0, scopes, tool schema
    ├── 02_a2a_deep_dive.md       ← A2A: AgentCard, task lifecycle, streaming
    ├── 03_acp_deep_dive.md       ← ACP: DAG workflows, envelopes, TTL
    ├── 04_anp_deep_dive.md       ← ANP: DIDs, VCs, cross-org discovery
    ├── 05_memory_architecture.md ← Memory: 3 tiers, access control, TTL
    ├── 06_security_governance.md ← Security: tokens, RBAC, audit chain
    ├── 07_observability.md       ← Observability: traces, confidence, logging
    ├── 08_design_patterns.md     ← Patterns: Router, Planner, Swarm, Hybrid
    └── 09_failure_resilience.md  ← Resilience: retry, circuit breaker, DLQ
```

---

## Quick Start

No external dependencies required — runs on Python 3.10+ stdlib:

```bash
git clone https://github.com/your-org/agent-comm-protocol.git
cd agent-comm-protocol

# Run examples immediately (zero pip install needed)
python examples/basic_delegation.py
python examples/swarm_example.py

# Or with optional structured logging
pip install structlog
python examples/swarm_example.py
```

**Install all optional dependencies:**
```bash
pip install -r requirements.txt
```

---

## Development

```bash
# Full dev setup
pip install -e ".[dev]"

# Available make targets
make help

make lint         # ruff
make format       # black + isort
make type-check   # mypy
make test-fast    # unit tests only  (< 10s)
make test         # full suite
make test-cov     # with HTML coverage report
make examples     # run both example scripts
make clean        # remove build artifacts
```

---

## Testing

```
262 unit tests  +  29 integration tests  =  291 total  ✅  0 failures
```

**Run tests:**
```bash
# Unit tests (fast)
python -m pytest tests/unit -v

# Integration tests (full pipeline wiring)
python -m pytest tests/integration -v

# All tests with coverage
python -m pytest tests/ --cov=src --cov-report=html
```

**Coverage by module:**

| Module | Tests |
|--------|-------|
| `src/protocols/mcp.py` | `tests/unit/test_mcp.py` |
| `src/protocols/a2a.py` | `tests/unit/test_a2a.py` |
| `src/protocols/acp.py` | `tests/unit/test_acp.py` |
| `src/protocols/anp.py` | `tests/integration/test_full_pipeline.py` |
| `src/agent.py` | `tests/unit/test_agent.py` |
| `src/memory.py` | `tests/unit/test_memory.py` |
| `src/security.py` | `tests/unit/test_security.py` |
| `src/observability.py` | `tests/unit/test_observability.py` |
| `src/protocol_router.py` | `tests/unit/test_protocol_router.py` |
| `src/failure/handlers.py` | `tests/unit/test_failure_handlers.py` |
| Full pipeline | `tests/integration/test_full_pipeline.py` |

---

## Documentation

Nine deep-dive modules — each covers concepts + implementation internals + production considerations:

| # | Module | Topics |
|---|--------|--------|
| 01 | [MCP Deep Dive](docs/01_mcp_deep_dive.md) | JSON-RPC 2.0 spec, tool schema, scope auth, tool lifecycle, custom tools |
| 02 | [A2A Deep Dive](docs/02_a2a_deep_dive.md) | AgentCard schema, task state machine, discovery flow, streaming (SSE), delegation depth |
| 03 | [ACP Deep Dive](docs/03_acp_deep_dive.md) | Message types, async vs sync, DAG workflow, TTL/expiry, retry policy |
| 04 | [ANP Deep Dive](docs/04_anp_deep_dive.md) | DID methods, DID Document schema, Verifiable Credentials, cross-org flow |
| 05 | [Memory Architecture](docs/05_memory_architecture.md) | 3 memory tiers, access control model, lazy eviction, vector search upgrade |
| 06 | [Security & Governance](docs/06_security_governance.md) | Token structure, RS256 upgrade, RBAC table, injection defense layers, audit chain |
| 07 | [Observability](docs/07_observability.md) | ReasoningTrace schema, SpanTracer DAG, confidence scoring, DecisionLog, OTLP export |
| 08 | [Design Patterns](docs/08_design_patterns.md) | Router, Planner+Executor, Swarm, Tool-Augmented, Hybrid Enterprise — with full code |
| 09 | [Failure Resilience](docs/09_failure_resilience.md) | Failure taxonomy, backoff math, circuit breaker FSM, DLQ, production checklist |

Also see: [`agent_protocol_concepts.md`](agent_protocol_concepts.md) — canonical JSON schemas for all 8 core objects.

---

## Execution Flow

```
User Query: "Analyze ACME Corp's Q3 financials and suggest actions"
     │
     ▼ [ACP] HTTP POST /orchestrate
┌────────────────────────────────────────────────────────┐
│  PlannerAgent  (Orchestration Layer)                   │
│  - Decomposes: [financial_analysis, legal_check,       │
│                 market_research, report_generation]    │
│  - Selects agents via AgentCard registry (A2A)         │
└──────────┬──────────┬──────────────┬───────────────────┘
           │ [A2A]    │ [A2A]        │ [A2A]
           ▼          ▼              ▼
    FinanceAgent  LegalAgent    DataAgent
    [MCP:db_query] [MCP:doc_search] [MCP:web_search]
           │          │              │
           └──────────┴──────[SAMEP]─┘
                      ▼  shared working memory
              ReportAgent [MCP: template_render]
                      │
                      ▼ [ACP] callback
              Orchestrator → User Response
```

**Protocol at each step:**

| Step | From → To | Protocol | Why |
|------|-----------|----------|-----|
| 1 | User → Orchestrator | ACP | Async task envelope with TTL + correlation |
| 2 | Orchestrator → PlannerAgent | ACP | Workflow dispatch with DAG definition |
| 3 | PlannerAgent → Specialists | A2A | Dynamic discovery via AgentCard registry |
| 4 | SpecialistAgent → Tools | MCP | JSON-RPC 2.0 with scope enforcement |
| 5 | Agents → MemoryStore | SAMEP | Read/write shared context with access control |
| 6 | Specialists → ReportAgent | A2A | Task handoff with correlation ID |
| 7 | ReportAgent → Orchestrator | ACP | Result envelope |
| 8 | Orchestrator → User | HTTP | Final response |

---

## Security & Governance

| Threat | Defense | Implementation |
|--------|---------|----------------|
| Agent spoofing | HMAC-signed identity tokens | `AgentIdentityToken` in `security.py` |
| Prompt injection | Regex + schema validation | `SecurityGateway.sanitize_input()` |
| Unauthorized tool access | RBAC scopes | `MCPServer` scope enforcement |
| Replay attacks | Nonce + token TTL | `NonceCache.check_and_consume()` |
| Delegation loops | Max depth enforcement | `SecurityGateway.authorize_delegation()` |
| Data tampering | Hash-chained audit log | `AuditLog.verify_chain()` |
| Man-in-the-middle | mTLS (production) | Service mesh layer |

**Upgrade to production security:**
```python
# Replace HMAC-SHA256 with RS256 (asymmetric)
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
token = jwt.encode(payload, private_key, algorithm="RS256")
```

→ **Deep dive:** [`docs/06_security_governance.md`](docs/06_security_governance.md)

---

## Observability & Explainability

Every agent decision emits a `ReasoningTrace`:

```json
{
  "trace_id": "trace-abc-001",
  "agent_id": "planner-agent",
  "decision": "delegate_to_finance_agent",
  "decision_type": "DELEGATION",
  "reasoning": "finance-agent-v2 scored 0.94 (capability match + version + load)",
  "confidence": 0.94,
  "protocol_used": "A2A",
  "alternatives_considered": [
    { "option": "finance-agent-v1", "score": 0.71, "rejected_reason": "older version" }
  ],
  "duration_ms": 12.4
}
```

```python
# Print full trace tree for any run
obs.print_trace_tree(obs.get_tracer(result.trace_id))

# Find low-confidence decisions
risky = obs.decision_log.low_confidence_decisions(threshold=0.6)

# Summary analytics
print(obs.decision_log.summary())
```

→ **Deep dive:** [`docs/07_observability.md`](docs/07_observability.md)

---

## Design Patterns

| Pattern | File | Use Case |
|---------|------|---------|
| **Router Agent** | `src/patterns/router_agent.py` | Classify + route to specialists |
| **Planner + Executor** | `src/patterns/planner_executor.py` | Decompose complex tasks into sub-tasks |
| **Agent Swarm** | `src/patterns/swarm.py` | Parallel N-agent execution + aggregation |
| **Tool-Augmented Agent** | `src/agent.py` | Single agent with rich MCP tool set |
| **Hybrid Enterprise** | `examples/swarm_example.py` | All patterns + ANP cross-org |

→ **Deep dive:** [`docs/08_design_patterns.md`](docs/08_design_patterns.md)

---

## Failure Handling

| Failure Type | Strategy | Implementation |
|-------------|---------|----------------|
| Transient error | Retry with exponential backoff + jitter | `RetryHandler` |
| Cascading failure | Circuit breaker (CLOSED/HALF_OPEN/OPEN FSM) | `CircuitBreaker` |
| Primary unavailable | Ordered fallback chain | `FallbackChain` |
| Unprocessable message | Dead-letter queue | `DeadLetterQueue` |
| Critical failure | Human escalation webhook | `HumanEscalationHook` |

```python
fo = FailureOrchestrator(
    agent_id="planner-agent",
    retry_config=RetryConfig(max_retries=3, base_delay_seconds=0.5),
    circuit_config=CircuitBreakerConfig(failure_threshold=5),
    fallback_options=[FallbackOption("general-agent", general_fn, "Fallback")],
    escalation_hook=escalation,
)
result = await fo.execute(specialist_call, task_data, operation="analysis")
```

→ **Deep dive:** [`docs/09_failure_resilience.md`](docs/09_failure_resilience.md)

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for setup, coding standards, testing requirements, and how to add new protocols or patterns.

## License

[MIT](LICENSE)
