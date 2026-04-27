# Contributing to Agent Communication Protocol

Thank you for your interest in contributing! This guide covers everything you need to get started.

---

## Table of Contents

- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Adding a New Protocol](#adding-a-new-protocol)
- [Adding a New Design Pattern](#adding-a-new-design-pattern)

---

## Development Setup

```bash
# Clone the repo
git clone https://github.com/your-org/agent-comm-protocol.git
cd agent-comm-protocol

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
.venv\Scripts\activate             # Windows

# Install with dev extras
pip install -e ".[dev]"

# Verify setup
make test-fast
```

---

## Project Structure

```
src/
├── _logging.py          ← Logging shim (structlog or stdlib fallback)
├── agent.py             ← BaseAgent: core decision engine
├── protocol_router.py   ← ProtocolRouter: decides MCP/A2A/ACP/ANP/local
├── messaging.py         ← MessageBus, Channel, StreamChannel
├── memory.py            ← MemoryManager (Working/Episodic/Semantic)
├── security.py          ← SecurityGateway, AuditLog, token issuance
├── observability.py     ← ObservabilityEngine, SpanTracer, ReasoningTrace
├── protocols/
│   ├── mcp.py           ← Model Context Protocol (JSON-RPC 2.0)
│   ├── a2a.py           ← Agent-to-Agent Protocol
│   ├── acp.py           ← Agent Communication Protocol (orchestration)
│   └── anp.py           ← Agent Network Protocol (decentralized)
├── patterns/
│   ├── router_agent.py      ← Router Agent pattern
│   ├── planner_executor.py  ← Planner + Executor pattern
│   └── swarm.py             ← Agent Swarm pattern
└── failure/
    └── handlers.py      ← Retry, CircuitBreaker, Fallback, DLQ, Escalation
```

---

## Coding Standards

- **Python 3.10+** — use `from __future__ import annotations`, `match/case` where appropriate
- **Type hints** — all public functions and class attributes must be typed
- **Docstrings** — every public class and method must have a docstring explaining WHY, not just what
- **No mutable defaults** — use `field(default_factory=...)` in dataclasses
- **Logging** — always use `from src._logging import get_logger`, never `print()` in library code
- **Error messages** — be specific; include what was expected vs what was received
- **Line length** — 100 characters max

Format before committing:
```bash
make format   # black + isort
make lint     # ruff
make type-check  # mypy
```

---

## Testing

Every contribution **must** include tests. We use pytest with asyncio support.

```bash
make test-fast   # unit tests only (< 5s)
make test        # full suite + coverage
make test-cov    # html coverage report
```

### Test file naming

| Source file | Test file |
|-------------|-----------|
| `src/protocols/mcp.py` | `tests/unit/test_mcp.py` |
| `src/patterns/swarm.py` | `tests/unit/test_swarm.py` |

### Test categories

- **Unit** (`tests/unit/`): test a single class/function in isolation; mock all external dependencies
- **Integration** (`tests/integration/`): test the full pipeline end-to-end

### Writing async tests

```python
import pytest

@pytest.mark.asyncio
async def test_my_async_thing():
    result = await my_async_function()
    assert result.success
```

---

## Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes (code + tests + docs)
4. Run the full test suite: `make test`
5. Commit with a conventional commit message:
   - `feat: add ANP cross-org broadcast`
   - `fix: circuit breaker half-open count reset`
   - `docs: add MCP tool schema examples`
   - `test: add unit tests for memory access control`
6. Push and open a Pull Request using the PR template

---

## Adding a New Protocol

1. Create `src/protocols/your_protocol.py`
2. Implement at minimum: client class, message/request/response dataclasses, and error types
3. Register in `src/protocols/__init__.py`
4. Add a routing case in `src/protocol_router.py` → `ProtocolRouter.route()`
5. Add dispatch in `src/agent.py` → `BaseAgent._dispatch()`
6. Write `tests/unit/test_your_protocol.py`
7. Add a docs entry in `docs/`
8. Update `README.md` and `agent_protocol_concepts.md`

---

## Adding a New Design Pattern

1. Create `src/patterns/your_pattern.py`
2. Subclass `BaseAgent` and override `_execute_locally()`
3. Register in `src/patterns/__init__.py`
4. Write `tests/unit/test_your_pattern.py`
5. Add an example in `examples/`
6. Document in `docs/08_design_patterns.md`
