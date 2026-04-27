"""
MCP — Model Context Protocol
Scope: Agent ↔ Tool/Data (single-agent scope)

Implements JSON-RPC 2.0 over HTTP for standardized tool access.
Acts as a universal "USB driver" for AI tools — any compliant tool
can be plugged into any compliant agent without custom integration.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from src._logging import get_logger

logger = get_logger(__name__)


class MCPErrorCode(int, Enum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    TOOL_NOT_FOUND = -32001
    TOOL_EXECUTION_ERROR = -32002
    UNAUTHORIZED = -32003
    RATE_LIMITED = -32004


@dataclass
class MCPToolCall:
    """Represents a single tool invocation via MCP."""
    call_id: str
    agent_id: str
    task_id: str
    tool_name: str
    arguments: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    scope_required: str = ""

    def to_jsonrpc(self) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": self.call_id,
            "method": "tools/call",
            "params": {
                "name": self.tool_name,
                "arguments": self.arguments,
            },
        }


@dataclass
class MCPResult:
    """Result from an MCP tool call."""
    call_id: str
    content: list[dict[str, Any]]
    is_error: bool
    latency_ms: float
    tool_name: str
    error_code: int | None = None
    error_message: str | None = None

    @classmethod
    def success(cls, call_id: str, tool_name: str, text: str, latency_ms: float) -> "MCPResult":
        return cls(
            call_id=call_id,
            content=[{"type": "text", "text": text}],
            is_error=False,
            latency_ms=latency_ms,
            tool_name=tool_name,
        )

    @classmethod
    def error(cls, call_id: str, tool_name: str, code: MCPErrorCode, message: str, latency_ms: float) -> "MCPResult":
        return cls(
            call_id=call_id,
            content=[],
            is_error=True,
            latency_ms=latency_ms,
            tool_name=tool_name,
            error_code=code.value,
            error_message=message,
        )

    def to_jsonrpc_response(self) -> dict[str, Any]:
        if self.is_error:
            return {
                "jsonrpc": "2.0",
                "id": self.call_id,
                "error": {
                    "code": self.error_code,
                    "message": self.error_message,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": self.call_id,
            "result": {
                "content": self.content,
                "isError": False,
            },
        }


ToolHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, str]]


@dataclass
class ToolDefinition:
    """Registered tool on an MCP server."""
    name: str
    description: str
    input_schema: dict[str, Any]
    required_scope: str
    handler: ToolHandler


class MCPServer:
    """
    MCP Server — hosts tools that agents can invoke.

    Each tool is registered with:
    - A name (unique identifier)
    - An input JSON schema (validated before execution)
    - A required scope (checked against caller's token)
    - A handler coroutine (the actual tool logic)
    """

    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        self._tools: dict[str, ToolDefinition] = {}
        self._call_count: dict[str, int] = {}
        logger.info("mcp_server_init", server_id=server_id)

    def register_tool(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool
        self._call_count[tool.name] = 0
        logger.info("mcp_tool_registered", tool=tool.name, scope=tool.required_scope)

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
                "requiredScope": t.required_scope,
            }
            for t in self._tools.values()
        ]

    async def handle_request(
        self,
        request: dict[str, Any],
        caller_scopes: list[str],
    ) -> dict[str, Any]:
        """Process a JSON-RPC 2.0 request from an agent."""
        req_id = request.get("id", "unknown")
        method = request.get("method", "")
        params = request.get("params", {})

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.list_tools()}}

        if method == "tools/call":
            return await self._handle_tool_call(req_id, params, caller_scopes)

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": MCPErrorCode.METHOD_NOT_FOUND.value, "message": f"Unknown method: {method}"},
        }

    async def _handle_tool_call(
        self,
        req_id: str,
        params: dict[str, Any],
        caller_scopes: list[str],
    ) -> dict[str, Any]:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        start = time.monotonic()

        if tool_name not in self._tools:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": MCPErrorCode.TOOL_NOT_FOUND.value, "message": f"Tool not found: {tool_name}"},
            }

        tool = self._tools[tool_name]

        if tool.required_scope and tool.required_scope not in caller_scopes:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": MCPErrorCode.UNAUTHORIZED.value,
                    "message": f"Missing scope: {tool.required_scope}",
                },
            }

        try:
            result_text = await tool.handler(arguments)
            latency_ms = (time.monotonic() - start) * 1000
            self._call_count[tool_name] += 1
            logger.info("mcp_tool_called", tool=tool_name, latency_ms=round(latency_ms, 2))
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": False,
                },
            }
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            logger.error("mcp_tool_error", tool=tool_name, error=str(exc))
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": MCPErrorCode.TOOL_EXECUTION_ERROR.value,
                    "message": str(exc),
                },
            }


class MCPClient:
    """
    MCP Client — used by agents to call tools on an MCP server.

    In production this would be HTTP. Here we use direct server reference
    for simulation; swap `_server` with an HTTP transport for real deployment.
    """

    def __init__(self, agent_id: str, scopes: list[str]) -> None:
        self.agent_id = agent_id
        self.scopes = scopes
        self._servers: dict[str, MCPServer] = {}

    def connect_server(self, server: MCPServer) -> None:
        self._servers[server.server_id] = server

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        task_id: str = "",
        server_id: str | None = None,
    ) -> MCPResult:
        """Call a tool via MCP and return a structured result."""
        call_id = f"mcp-{uuid.uuid4().hex[:8]}"
        tool_call = MCPToolCall(
            call_id=call_id,
            agent_id=self.agent_id,
            task_id=task_id,
            tool_name=tool_name,
            arguments=arguments,
        )

        target_server = self._resolve_server(tool_name, server_id)
        if target_server is None:
            return MCPResult.error(
                call_id, tool_name, MCPErrorCode.TOOL_NOT_FOUND,
                f"No server found for tool: {tool_name}", 0.0
            )

        start = time.monotonic()
        response = await target_server.handle_request(tool_call.to_jsonrpc(), self.scopes)
        latency_ms = (time.monotonic() - start) * 1000

        if "error" in response:
            return MCPResult.error(
                call_id, tool_name,
                MCPErrorCode(response["error"]["code"]),
                response["error"]["message"],
                latency_ms,
            )

        content = response["result"]["content"]
        text = content[0]["text"] if content else ""
        return MCPResult.success(call_id, tool_name, text, latency_ms)

    async def list_tools(self, server_id: str) -> list[dict[str, Any]]:
        server = self._servers.get(server_id)
        if not server:
            return []
        response = await server.handle_request(
            {"jsonrpc": "2.0", "id": "list-1", "method": "tools/list", "params": {}},
            self.scopes,
        )
        return response.get("result", {}).get("tools", [])

    def _resolve_server(self, tool_name: str, server_id: str | None) -> MCPServer | None:
        if server_id:
            return self._servers.get(server_id)
        for server in self._servers.values():
            if tool_name in {t.name for t in server._tools.values()}:
                return server
        return None


def build_default_mcp_server() -> MCPServer:
    """
    Builds an MCP server pre-loaded with common tools for demonstration.
    In production, each tool connects to a real backend.
    """
    server = MCPServer("default-mcp-server")

    async def web_search(args: dict[str, Any]) -> str:
        query = args.get("query", "")
        max_results = args.get("max_results", 5)
        await asyncio.sleep(0.01)
        return json.dumps({
            "results": [
                {"title": f"Result {i} for '{query}'", "url": f"https://example.com/{i}", "snippet": f"Relevant info #{i}"}
                for i in range(1, max_results + 1)
            ]
        })

    async def database_query(args: dict[str, Any]) -> str:
        table = args.get("table", "unknown")
        filters = args.get("filters", {})
        fields = args.get("fields", ["*"])
        await asyncio.sleep(0.01)
        return json.dumps({
            "table": table,
            "filters": filters,
            "rows": [{"id": 1, **{f: f"mock_value_{f}" for f in fields}}],
            "row_count": 1,
        })

    async def calculator(args: dict[str, Any]) -> str:
        expression = args.get("expression", "0")
        try:
            allowed_names = {"__builtins__": {}, "abs": abs, "round": round, "min": min, "max": max}
            result = eval(expression, allowed_names)  # noqa: S307 — sandboxed eval
            return json.dumps({"expression": expression, "result": result})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    async def file_reader(args: dict[str, Any]) -> str:
        path = args.get("path", "")
        await asyncio.sleep(0.005)
        return json.dumps({"path": path, "content": f"[mock content of {path}]", "size_bytes": 1024})

    server.register_tool(ToolDefinition(
        name="web_search",
        description="Search the web for information",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
        },
        required_scope="search:read",
        handler=web_search,
    ))

    server.register_tool(ToolDefinition(
        name="database_query",
        description="Query internal database tables",
        input_schema={
            "type": "object",
            "required": ["table"],
            "properties": {
                "table": {"type": "string"},
                "filters": {"type": "object"},
                "fields": {"type": "array", "items": {"type": "string"}},
            },
        },
        required_scope="db:read",
        handler=database_query,
    ))

    server.register_tool(ToolDefinition(
        name="calculator",
        description="Evaluate a mathematical expression",
        input_schema={
            "type": "object",
            "required": ["expression"],
            "properties": {
                "expression": {"type": "string"},
            },
        },
        required_scope="",
        handler=calculator,
    ))

    server.register_tool(ToolDefinition(
        name="file_reader",
        description="Read content from a file path",
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
            },
        },
        required_scope="files:read",
        handler=file_reader,
    ))

    return server
