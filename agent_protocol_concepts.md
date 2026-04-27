# 📂 Agent Protocol Concepts — Complete Schema Reference

This file is the canonical schema reference for every core concept in the multi-agent protocol ecosystem.
Each concept includes: **WHY it exists**, a **JSON example**, and **when to use it**.

---

## 1. Agent Card (A2A Protocol)

### WHY it exists
Agents in a distributed network cannot hard-code awareness of each other. The **Agent Card** is a self-describing advertisement that any agent publishes so others can discover it, understand its capabilities, and know how to communicate with it. It is the equivalent of an API spec + business card combined.

Without Agent Cards:
- Orchestrators must be reconfigured every time an agent is added
- Capability matching is manual and brittle
- No versioning of agent contracts

### JSON Example
```json
{
  "agent_card": {
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
        "query": {"type": "string", "description": "Financial question or task description"},
        "ticker": {"type": "string", "description": "Optional stock ticker"},
        "time_range": {"type": "string", "enum": ["Q1", "Q2", "Q3", "Q4", "YTD", "1Y"]}
      }
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "report": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "data_sources": {"type": "array", "items": {"type": "string"}}
      }
    },
    "trust_level": "internal",
    "auth": {
      "type": "bearer",
      "scope": "finance:read finance:write"
    },
    "rate_limits": {
      "requests_per_minute": 60,
      "max_concurrent_tasks": 10
    },
    "health_endpoint": "https://agents.internal/finance/health",
    "registered_at": "2025-01-01T00:00:00Z",
    "ttl_seconds": 3600
  }
}
```

### When to Use
- **Agent registration**: Every agent publishes its card to the registry on startup
- **Capability discovery**: Orchestrator queries registry to find agents matching required capability
- **Routing decisions**: ProtocolRouter reads cards to select delegation target
- **Versioning**: Use `version` field to enforce compatibility constraints

---

## 2. Task Lifecycle Schema (A2A Protocol)

### WHY it exists
A task in a multi-agent system is not instantaneous — it passes through states. The **Task Lifecycle** schema standardizes these states and the transitions between them so:
- Any agent can track task progress independently
- Streaming results can be emitted at intermediate states
- Failures are explicitly captured with reasons
- Audit trails are complete

### States
```
submitted → working → [streaming] → completed
                    ↘ failed
                    ↘ cancelled
                    ↘ escalated
```

### JSON Example
```json
{
  "task": {
    "task_id": "task-abc-123",
    "correlation_id": "workflow-2024-001",
    "sender_id": "planner-agent",
    "receiver_id": "finance-agent-v2",
    "capability": "financial_analysis",
    "status": "working",
    "priority": "high",
    "created_at": "2025-01-01T10:00:00Z",
    "updated_at": "2025-01-01T10:00:05Z",
    "deadline_iso": "2025-01-01T10:05:00Z",
    "stream": true,
    "input": {
      "query": "Analyze Q3 revenue trend for ACME Corp",
      "ticker": "ACME",
      "time_range": "Q3"
    },
    "output": null,
    "error": null,
    "artifacts": [],
    "history": [
      {
        "timestamp": "2025-01-01T10:00:00Z",
        "status": "submitted",
        "note": "Task received by orchestrator"
      },
      {
        "timestamp": "2025-01-01T10:00:02Z",
        "status": "working",
        "note": "FinanceAgent accepted task, querying database"
      }
    ],
    "metadata": {
      "delegation_depth": 1,
      "parent_task_id": null,
      "retry_count": 0
    }
  }
}
```

### Completed Task Example
```json
{
  "task": {
    "task_id": "task-abc-123",
    "status": "completed",
    "updated_at": "2025-01-01T10:00:18Z",
    "output": {
      "report": "ACME Corp Q3 revenue grew 12% YoY to $4.2B...",
      "confidence": 0.91,
      "data_sources": ["edgar_db", "bloomberg_api"]
    },
    "artifacts": [
      {
        "type": "chart",
        "url": "https://storage.internal/charts/acme-q3.png",
        "mime_type": "image/png"
      }
    ]
  }
}
```

### When to Use
- Every delegated task follows this schema
- Streaming agents emit partial `output` payloads with `status: "working"`
- Failed tasks populate `error` and set `status: "failed"`
- `metadata.delegation_depth` enforces max delegation depth to prevent infinite loops

---

## 3. Message Envelope Format (ACP Protocol)

### WHY it exists
The **Message Envelope** is the wrapper for all orchestration-layer communication. It exists to:
- Separate routing metadata from business payload
- Support async fire-and-forget AND request-reply patterns
- Enable correlation of messages across distributed workflows
- Provide TTL and retry semantics at the protocol layer

Think of it as HTTP headers vs body — the envelope handles "how to deliver" while the payload handles "what to deliver".

### JSON Example — Task Dispatch
```json
{
  "envelope": {
    "message_id": "msg-xyz-789",
    "type": "TASK_DISPATCH",
    "from": "orchestrator",
    "to": "finance-agent-v2",
    "correlation_id": "workflow-2024-001",
    "timestamp": "2025-01-01T10:00:00Z",
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
      "span_id": "span-002",
      "parent_span_id": "span-001"
    },
    "payload": {
      "task_id": "task-abc-123",
      "capability": "financial_analysis",
      "input": {"query": "Analyze Q3 revenue for ACME Corp"},
      "context_ref": "ctx-session-42"
    }
  }
}
```

### Message Types
```json
{
  "message_types": {
    "TASK_DISPATCH": "Orchestrator sends task to agent",
    "TASK_RESULT": "Agent returns completed result",
    "TASK_ACK": "Agent acknowledges task receipt",
    "TASK_CANCEL": "Orchestrator cancels in-flight task",
    "STATUS_UPDATE": "Agent reports intermediate progress",
    "HEALTH_CHECK": "Orchestrator pings agent liveness",
    "CAPABILITY_QUERY": "Dynamic capability lookup",
    "ERROR_REPORT": "Agent reports unrecoverable error"
  }
}
```

### When to Use
- Use `TASK_DISPATCH` for async fire-and-forget with `reply_to`
- Use `TASK_DISPATCH` with `requires_ack: true` for at-least-once delivery
- Use `correlation_id` to group all messages in a single workflow
- TTL ensures messages do not linger in queues after task deadline

---

## 4. Memory Object Schema (SAMEP-style)

### WHY it exists
Agents in a multi-step workflow need to share state without coupling to each other directly. The **Memory Object** is a structured, versioned, typed record stored in a shared memory layer that any authorized agent can read or write.

This prevents:
- Re-fetching the same data multiple times across agents
- Context loss when tasks are delegated
- Inconsistent views of shared state

### Memory Tiers
```
Working Memory   → in-flight data for current task (TTL: session)
Episodic Memory  → history of past interactions (TTL: days/weeks)
Semantic Memory  → vector-indexed facts and knowledge (TTL: indefinite)
```

### JSON Example
```json
{
  "memory_object": {
    "memory_id": "mem-acme-q3-001",
    "type": "episodic",
    "created_by": "finance-agent-v2",
    "created_at": "2025-01-01T10:00:18Z",
    "updated_at": "2025-01-01T10:00:18Z",
    "session_id": "session-42",
    "correlation_id": "workflow-2024-001",
    "ttl_seconds": 86400,
    "version": 1,
    "tags": ["finance", "ACME", "Q3", "revenue"],
    "content": {
      "type": "financial_analysis_result",
      "data": {
        "company": "ACME Corp",
        "ticker": "ACME",
        "period": "Q3-2024",
        "revenue": 4200000000,
        "growth_yoy": 0.12,
        "summary": "Q3 revenue grew 12% YoY to $4.2B driven by cloud segment"
      }
    },
    "access_control": {
      "readable_by": ["planner-agent", "report-agent", "legal-agent"],
      "writable_by": ["finance-agent-v2"],
      "visibility": "workflow"
    },
    "embedding_ref": "vec-store://semantic/mem-acme-q3-001"
  }
}
```

### When to Use
- **Working memory**: Store intermediate results that other agents in the same workflow need
- **Episodic memory**: Cache results that may be reused across sessions (e.g., same company queried again)
- **Semantic memory**: Index factual knowledge for retrieval-augmented agent responses
- Always set `ttl_seconds` — never store unbounded memory objects
- `access_control.readable_by` limits which agents can retrieve this memory

---

## 5. Identity Token Schema (AIP-style)

### WHY it exists
In a multi-agent system, any agent can impersonate another unless identity is cryptographically verified. The **Identity Token** establishes:
- WHO the agent is (identity)
- WHAT it can do (capabilities/scopes)
- HOW LONG it is trusted (expiry)
- WHO vouched for it (issuer)

Without identity tokens, a malicious process can inject fake agent responses, bypass authorization, or exfiltrate data.

### JWT Payload Example
```json
{
  "identity_token": {
    "header": {
      "alg": "RS256",
      "typ": "JWT",
      "kid": "agent-key-2025-01"
    },
    "payload": {
      "iss": "https://identity.internal/agent-auth",
      "sub": "finance-agent-v2",
      "aud": ["orchestrator", "memory-store", "mcp-server"],
      "iat": 1735689600,
      "exp": 1735693200,
      "nbf": 1735689600,
      "jti": "jwt-unique-id-abc123",
      "agent_claims": {
        "agent_id": "finance-agent-v2",
        "agent_version": "2.1.0",
        "agent_type": "specialist",
        "trust_level": "internal",
        "capabilities": ["financial_analysis", "portfolio_optimization"],
        "mcp_tool_scopes": ["db:read", "search:read"],
        "delegation_allowed": true,
        "max_delegation_depth": 2,
        "organization": "acme-corp",
        "environment": "production"
      },
      "nonce": "random-nonce-xyz"
    }
  }
}
```

### DID-based Identity (ANP)
```json
{
  "did_document": {
    "id": "did:web:agents.acme.com:finance-agent",
    "controller": "did:web:identity.acme.com",
    "verificationMethod": [
      {
        "id": "did:web:agents.acme.com:finance-agent#key-1",
        "type": "Ed25519VerificationKey2020",
        "controller": "did:web:agents.acme.com:finance-agent",
        "publicKeyMultibase": "z6Mkf5rGMoatrSj1f9iBnSGaLwubGVA4rmSZ4kpwaBQJEb7Q"
      }
    ],
    "authentication": ["did:web:agents.acme.com:finance-agent#key-1"],
    "service": [
      {
        "id": "#agent-endpoint",
        "type": "AgentEndpoint",
        "serviceEndpoint": "https://agents.acme.com/finance"
      },
      {
        "id": "#agent-card",
        "type": "AgentCard",
        "serviceEndpoint": "https://agents.acme.com/finance/.well-known/agent.json"
      }
    ]
  }
}
```

### When to Use
- **JWT tokens**: Every inter-agent API call carries a signed JWT in `Authorization: Bearer <token>`
- **Token validation**: Each agent validates signature, expiry, and scopes before processing a task
- **DIDs**: Use in ANP contexts for cross-organizational agent discovery without central authority
- **`nonce`**: Prevent replay attacks — each token has a unique nonce stored in a short-lived cache
- **`max_delegation_depth`**: Prevents recursive delegation loops — enforced by ProtocolRouter

---

## 6. Reasoning Trace Schema (Observability)

### WHY it exists
Multi-agent systems are inherently opaque — a user sees a final answer but cannot tell which agents contributed, what decisions were made, or why. The **Reasoning Trace** makes every decision auditable.

### JSON Example
```json
{
  "reasoning_trace": {
    "trace_id": "trace-abc-001",
    "span_id": "span-003",
    "parent_span_id": "span-002",
    "agent_id": "planner-agent",
    "timestamp": "2025-01-01T10:00:01Z",
    "duration_ms": 12,
    "decision": "delegate_to_finance_agent",
    "decision_type": "DELEGATION",
    "reasoning": "Task requires 'financial_analysis' capability. Checked registry: 2 candidates found. Selected finance-agent-v2 (confidence: 0.94) over finance-agent-v1 (confidence: 0.71) based on version and load.",
    "confidence": 0.94,
    "protocol_used": "A2A",
    "alternatives_considered": [
      {"option": "finance-agent-v1", "score": 0.71, "rejected_reason": "older version, higher latency"},
      {"option": "local_llm_fallback", "score": 0.45, "rejected_reason": "insufficient domain knowledge"}
    ],
    "inputs": {"capability_needed": "financial_analysis", "priority": "high"},
    "output": {"delegated_to": "finance-agent-v2", "task_id": "task-abc-123"},
    "tool_calls": [],
    "memory_reads": ["mem-registry-finance-agents"],
    "memory_writes": ["mem-task-abc-123-delegation-record"]
  }
}
```

### When to Use
- Emit a trace for every decision node (delegation, tool call, memory access, fallback)
- Link spans via `parent_span_id` to reconstruct full execution DAG
- Store traces in append-only audit log — never mutate historical traces
- Surface `confidence` scores to users in explainability dashboards

---

## 7. Tool Call Schema (MCP Protocol)

### WHY it exists
MCP standardizes tool calls so any tool can be plugged in without custom integration code. The **Tool Call Schema** ensures every tool invocation is:
- Auditable (what was called, with what args)
- Safe (schema-validated inputs)
- Traceable (linked to agent and task)

### JSON Example
```json
{
  "mcp_tool_call": {
    "call_id": "mcp-call-001",
    "agent_id": "finance-agent-v2",
    "task_id": "task-abc-123",
    "timestamp": "2025-01-01T10:00:05Z",
    "request": {
      "jsonrpc": "2.0",
      "id": "mcp-call-001",
      "method": "tools/call",
      "params": {
        "name": "database_query",
        "arguments": {
          "table": "financial_reports",
          "filters": {"company": "ACME", "period": "Q3-2024"},
          "fields": ["revenue", "ebitda", "net_income"]
        }
      }
    },
    "response": {
      "jsonrpc": "2.0",
      "id": "mcp-call-001",
      "result": {
        "content": [
          {
            "type": "text",
            "text": "{\"revenue\": 4200000000, \"ebitda\": 840000000, \"net_income\": 420000000}"
          }
        ],
        "isError": false
      }
    },
    "latency_ms": 45,
    "scope_used": "db:read"
  }
}
```

---

## Quick Reference — Schema Summary

| Schema | Protocol | Layer | Key Fields |
|--------|----------|-------|------------|
| Agent Card | A2A | Discovery | `agent_id`, `capabilities`, `endpoint`, `input_schema` |
| Task | A2A | Execution | `task_id`, `status`, `input`, `output`, `history` |
| Message Envelope | ACP | Orchestration | `message_id`, `type`, `correlation_id`, `reply_to`, `ttl_seconds` |
| Memory Object | SAMEP | Memory | `memory_id`, `type`, `content`, `access_control`, `ttl_seconds` |
| Identity Token | AIP/JWT | Security | `sub`, `exp`, `agent_claims.capabilities`, `nonce` |
| DID Document | ANP | Identity | `id`, `verificationMethod`, `service` |
| Reasoning Trace | Internal | Observability | `trace_id`, `decision`, `confidence`, `protocol_used` |
| MCP Tool Call | MCP | Tools | `name`, `arguments`, `result`, `scope_used` |
