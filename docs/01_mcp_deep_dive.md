# MCP — Model Context Protocol: Deep Dive

## 1. What Is MCP and Why Does It Exist?

Before MCP, every AI agent had to write custom integration code to call each tool: a custom HTTP client for web search, a custom adapter for a database, a custom wrapper for file access. This created:

- **N×M problem**: N agents × M tools = N×M custom integrations
- **No standardization**: error formats, auth, schema validation — all different
- **No reuse**: switching tools meant rewriting agent code

MCP solves this with a **universal plugin standard** for AI tools. It is to AI agents what USB is to hardware: a single interface that any tool can implement and any agent can use.

```
Without MCP:                     With MCP:
Agent ──custom──► SearchAPI      Agent ──MCP──► MCPServer ──► SearchTool
Agent ──custom──► Database                              ──► DatabaseTool
Agent ──custom──► Calculator                            ──► CalcTool
```

**Key insight**: MCP is scoped to a single agent's tool access. It does NOT handle agent-to-agent communication (that's A2A/ACP).

---

## 2. Protocol Specification

MCP uses **JSON-RPC 2.0** over HTTP (or stdio in local mode).

### JSON-RPC 2.0 Envelope

Every MCP message is a JSON-RPC 2.0 object:

```json
// Request
{
  "jsonrpc": "2.0",
  "id": "unique-request-id",
  "method": "method/name",
  "params": { ... }
}

// Success Response
{
  "jsonrpc": "2.0",
  "id": "unique-request-id",
  "result": { ... }
}

// Error Response
{
  "jsonrpc": "2.0",
  "id": "unique-request-id",
  "error": {
    "code": -32001,
    "message": "Tool not found: web_search"
  }
}
```

### Supported Methods

| Method | Description |
|--------|-------------|
| `tools/list` | Returns all available tools with schemas |
| `tools/call` | Invoke a tool with arguments |
| `resources/list` | List available data resources |
| `resources/read` | Read a resource |
| `prompts/list` | List available prompt templates |
| `prompts/get` | Get a specific prompt template |

### Error Codes

| Code | Name | When |
|------|------|------|
| -32700 | PARSE_ERROR | Invalid JSON in request |
| -32600 | INVALID_REQUEST | Request not conforming to JSON-RPC spec |
| -32601 | METHOD_NOT_FOUND | Method does not exist |
| -32602 | INVALID_PARAMS | Method params invalid |
| -32001 | TOOL_NOT_FOUND | Named tool not registered |
| -32002 | TOOL_EXECUTION_ERROR | Tool handler raised an exception |
| -32003 | UNAUTHORIZED | Caller missing required scope |
| -32004 | RATE_LIMITED | Request rate exceeded |

---

## 3. Tool Definition Schema

Every tool registered on an MCP server has:

```json
{
  "name": "database_query",
  "description": "Query internal database tables with filters",
  "inputSchema": {
    "type": "object",
    "required": ["table"],
    "properties": {
      "table": {
        "type": "string",
        "description": "Name of the table to query"
      },
      "filters": {
        "type": "object",
        "description": "Key-value filter conditions",
        "additionalProperties": {"type": "string"}
      },
      "fields": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Fields to return (empty = all)"
      },
      "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 1000,
        "default": 100
      }
    }
  },
  "requiredScope": "db:read"
}
```

**Design principle**: Input schemas are validated BEFORE the handler is called. This prevents injection attacks and provides clear error messages.

---

## 4. Scope-Based Authorization

MCP tools declare a `requiredScope`. Callers must present a token with that scope.

```
Scopes follow the pattern: resource:action

Examples:
  search:read     → can call web_search
  db:read         → can call database_query (read-only)
  db:write        → can call database_insert, database_update
  files:read      → can call file_reader
  files:write     → can call file_writer
  admin:*         → all admin tools
```

**Why scopes matter**: Without scopes, any agent can call any tool. With scopes:
- A `SPECIALIST` agent can read data but cannot write
- An `ORCHESTRATOR` can read and write
- An `OBSERVER` has no tool access at all

```python
# Server enforces scopes
async def _handle_tool_call(self, req_id, params, caller_scopes):
    tool = self._tools[tool_name]
    if tool.required_scope and tool.required_scope not in caller_scopes:
        return error_response(UNAUTHORIZED, f"Missing scope: {tool.required_scope}")
    return await tool.handler(arguments)
```

---

## 5. Tool Call Lifecycle

```
Agent                    MCP Client               MCP Server
  │                          │                        │
  │──call_tool("web_search")─►│                        │
  │                          │──JSON-RPC POST─────────►│
  │                          │   tools/call            │
  │                          │   {name, arguments}     │
  │                          │                        │──validate_schema()
  │                          │                        │──check_scope()
  │                          │                        │──handler(args)
  │                          │◄──{result, isError}────│
  │◄──MCPResult(content)─────│                        │
  │                          │                        │
```

**Key data transformation at each step:**

1. Agent calls `mcp_client.call_tool("web_search", {"query": "LLM benchmarks"})`
2. Client wraps in JSON-RPC: `{"jsonrpc":"2.0", "method":"tools/call", "params":{"name":"web_search","arguments":{...}}}`
3. Server validates JSON schema, checks scope `search:read`
4. Handler executes and returns text string
5. Server wraps in `{"result":{"content":[{"type":"text","text":"..."}],"isError":false}}`
6. Client unwraps to `MCPResult(content=[...], is_error=False, latency_ms=45)`

---

## 6. Content Types

MCP tool results can return multiple content types:

```json
{
  "content": [
    {"type": "text", "text": "Analysis complete: revenue grew 12%"},
    {"type": "image", "data": "base64...", "mimeType": "image/png"},
    {"type": "resource", "resource": {"uri": "file:///report.pdf"}}
  ]
}
```

---

## 7. Building a Custom MCP Tool

```python
from src.protocols.mcp import MCPServer, ToolDefinition

server = MCPServer("my-server")

async def sentiment_analyzer(args: dict) -> str:
    text = args["text"]
    # In production: call an LLM or ML model
    positive_words = {"good", "great", "excellent", "amazing"}
    negative_words = {"bad", "terrible", "awful", "poor"}
    words = set(text.lower().split())
    score = len(words & positive_words) - len(words & negative_words)
    sentiment = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    return json.dumps({"text": text, "sentiment": sentiment, "score": score})

server.register_tool(ToolDefinition(
    name="sentiment_analyzer",
    description="Analyze the sentiment of a text passage",
    input_schema={
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "maxLength": 10000}
        }
    },
    required_scope="analysis:read",
    handler=sentiment_analyzer,
))
```

---

## 8. MCP in Production

**Transport options:**
- `stdio`: Local process (agent spawns tool server as subprocess)
- `HTTP/SSE`: Remote tool server over network
- `WebSocket`: Bidirectional streaming

**Scaling considerations:**
- Tool servers are stateless → easy horizontal scaling
- Use connection pooling for HTTP transport
- Cache tool list responses (tools don't change frequently)
- Rate limiting at server level (`MCPErrorCode.RATE_LIMITED`)

**Security hardening:**
- Always use `required_scope` — never leave it empty for sensitive tools
- Validate and sanitize all string arguments before passing to backend
- Log every tool call to audit trail: who called what with what args
- Use short-lived tokens for MCP clients (30-minute TTL)

---

## 9. Comparison with Other Tool Protocols

| Feature | MCP | OpenAI Function Calling | LangChain Tools |
|---------|-----|------------------------|-----------------|
| Standard | Open spec | Proprietary | Library-specific |
| Transport | JSON-RPC/HTTP | HTTP (OpenAI API) | Python calls |
| Schema | JSON Schema | JSON Schema | Python type hints |
| Auth/Scopes | Built-in | None | None |
| Multi-tool | Yes | Yes | Yes |
| Error codes | Standardized | Partial | Exception |
| Streaming | Via SSE | Limited | No |

**When to use MCP:**
- Any production multi-agent system requiring tool standardization
- When you want tools to be reusable across different agents/frameworks
- When you need scope-based tool authorization

---

## 10. Advanced Tool Patterns

### Pattern A: Tool Chaining (Multi-Step Tool Pipeline)

When one tool's output feeds the next, chain calls inside the handler:

```python
async def research_and_summarize(args: dict) -> str:
    query = args["query"]
    
    # Step 1: search
    search_result = await mcp_client.call_tool("web_search", {"query": query})
    raw_text = search_result.content[0]["text"]
    
    # Step 2: summarize (another tool or LLM call)
    summary_result = await mcp_client.call_tool("text_summarizer", {
        "text": raw_text, "max_words": 200
    })
    
    return summary_result.content[0]["text"]

server.register_tool(ToolDefinition(
    name="research_and_summarize",
    description="Search the web and return a summarized answer",
    input_schema={"type": "object", "required": ["query"],
                  "properties": {"query": {"type": "string"}}},
    required_scope="search:read",
    handler=research_and_summarize,
))
```

### Pattern B: Parameterized Tool Variants (Tool Factory)

Create multiple tools with the same logic but different configurations:

```python
def make_db_query_tool(table_name: str, allowed_fields: list[str]) -> ToolDefinition:
    async def handler(args: dict) -> str:
        fields = args.get("fields", allowed_fields)
        # Validate: only return allowed_fields
        safe_fields = [f for f in fields if f in allowed_fields]
        return await db.query(table_name, args.get("filters", {}), safe_fields)
    
    return ToolDefinition(
        name=f"query_{table_name}",
        description=f"Query the {table_name} table",
        input_schema={
            "type": "object",
            "properties": {
                "filters": {"type": "object"},
                "fields": {"type": "array", "items": {"type": "string"}},
            }
        },
        required_scope="db:read",
        handler=handler,
    )

# Register one tool per table — scope-safe
server.register_tool(make_db_query_tool("users",        ["id", "name", "email"]))
server.register_tool(make_db_query_tool("orders",       ["id", "user_id", "amount", "status"]))
server.register_tool(make_db_query_tool("products",     ["id", "name", "price", "category"]))
```

### Pattern C: Resource vs Tool — When to Use Which

```
Tool (tools/call):          Resource (resources/read):
───────────────────         ─────────────────────────
Has side effects?  YES      Read-only data?         YES
Returns computed   YES      Static or cacheable?    YES
result?                     URI-addressable?        YES
Needs arguments?   YES      
Example: web_search,        Example: company_profile,
         db_insert                   config_file,
         send_email                  knowledge_base_entry
```

---

## 11. Common Pitfalls and How to Avoid Them

### Pitfall 1: Missing `required_scope` on Sensitive Tools

```python
# WRONG — any agent can call this tool
ToolDefinition(
    name="delete_user",
    required_scope="",       # ← no scope = no authorization!
    handler=delete_user_fn,
)

# RIGHT
ToolDefinition(
    name="delete_user",
    required_scope="admin:write",   # must explicitly grant this scope
    handler=delete_user_fn,
)
```

**Rule**: Every tool that modifies state MUST have a `required_scope`. Read-only tools SHOULD have `resource:read`.

### Pitfall 2: Returning Raw Exceptions to Callers

```python
# WRONG — leaks internal structure
async def bad_handler(args: dict) -> str:
    result = db.query(args["table"])    # might raise psycopg2.OperationalError
    return result                        # exception propagates as unhandled

# RIGHT — structured error
async def good_handler(args: dict) -> str:
    try:
        result = db.query(args["table"])
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc), "success": False})
        # OR: raise ToolExecutionError(str(exc)) → maps to -32002
```

### Pitfall 3: Tool Handlers With Side Effects on `tools/list`

```python
# WRONG — tools/list should be read-only
async def list_tools_handler():
    log_access()        # ← side effect in list → breaks idempotency
    return self._tools

# RIGHT — list is pure read
async def list_tools_handler():
    return self._tools
```

### Pitfall 4: Ignoring the Input Schema

```python
# WRONG — no schema = no validation = injection risk
ToolDefinition(
    name="db_query",
    input_schema={},             # ← empty schema accepts anything
    handler=execute_raw_sql,     # ← dangerous!
)

# RIGHT — strict schema prevents bad inputs
ToolDefinition(
    name="db_query",
    input_schema={
        "type": "object",
        "required": ["table"],
        "properties": {
            "table": {
                "type": "string",
                "enum": ["users", "orders", "products"]   # ← allowlist!
            }
        },
        "additionalProperties": False   # ← reject unknown fields
    },
    handler=execute_safe_query,
)
```

### Pitfall 5: Blocking Handlers in Async Context

```python
# WRONG — blocks the event loop
async def slow_handler(args: dict) -> str:
    time.sleep(2)       # ← blocks! all other coroutines stall
    return do_work()

# RIGHT — use asyncio.sleep or run_in_executor
async def fast_handler(args: dict) -> str:
    await asyncio.sleep(0)    # yield control
    return await asyncio.get_event_loop().run_in_executor(None, do_work)
```

---

## 12. MCP Conceptual Model — How to Think About It

### The "function registry" model

MCP server = dictionary of callable functions + their documentation:

```
MCPServer._tools = {
    "web_search":     ToolDefinition(schema, scope, handler),
    "database_query": ToolDefinition(schema, scope, handler),
    "file_reader":    ToolDefinition(schema, scope, handler),
}

tools/list  → return keys + schemas (show the menu)
tools/call  → validate args → check scope → run handler (place the order)
```

### The "middleware pipeline" model

Every tool call goes through a validation pipeline before hitting the handler:

```
Raw JSON-RPC request
        │
        ▼ Parse JSON-RPC envelope
        │
        ▼ Look up tool by name (→ TOOL_NOT_FOUND if missing)
        │
        ▼ Validate args against input_schema (→ INVALID_PARAMS if fails)
        │
        ▼ Check required_scope against caller_scopes (→ UNAUTHORIZED if fails)
        │
        ▼ Execute handler(args)
        │
        ▼ Wrap result in JSON-RPC response
```

Each step is a gate — fail early, fail with a specific error code.

---

## 13. Interview Questions: MCP

**Q: "What problem does MCP solve? Why not just use REST APIs?"**
> REST APIs are tool-specific: each tool has its own endpoint, its own auth scheme, its own error format, its own schema. With N agents and M tools, you write N×M integrations. MCP is a single standard: one protocol, one auth model (scopes), one error code set, one schema format (JSON Schema). Any agent that speaks MCP can call any tool that speaks MCP.

**Q: "How does MCP handle authorization?"**
> Scope-based access control. Each tool declares a `required_scope` (e.g., `db:read`). The caller presents a token with their granted scopes. The server checks if `required_scope` is in the caller's scopes before executing the handler. This enforces least privilege — a specialist agent with `search:read` cannot accidentally call a tool requiring `db:write`.

**Q: "What happens if a tool handler throws an exception?"**
> The MCP server catches it and returns a JSON-RPC error response with code `-32002` (TOOL_EXECUTION_ERROR). The client receives `MCPResult(is_error=True)`. The agent can decide whether to retry, use a fallback tool, or surface the error. Exceptions should NOT propagate to callers — they should be caught and converted to structured errors.

**Q: "How does MCP differ from LangChain's tool system?"**
> LangChain tools are Python objects — they only work within Python and have no standard wire protocol. MCP is a network protocol (JSON-RPC 2.0 over HTTP/stdio) — tools can be implemented in any language, run as separate processes, and called by any MCP-compatible client. MCP also has built-in scope authorization and standardized error codes, which LangChain lacks.

**Q: "Can MCP handle streaming tool results?"**
> Yes, via SSE (Server-Sent Events) for HTTP transport. The tool server can emit multiple `content` events before the final result. This is useful for long-running tools like code execution or large data exports where you want progressive feedback.
