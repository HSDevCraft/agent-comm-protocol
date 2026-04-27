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
