"""
Unit tests for MCP — Model Context Protocol
"""
from __future__ import annotations

import json
import pytest

from src.protocols.mcp import (
    MCPClient,
    MCPErrorCode,
    MCPResult,
    MCPServer,
    MCPToolCall,
    ToolDefinition,
    build_default_mcp_server,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def echo_server() -> MCPServer:
    server = MCPServer("echo-server")

    async def echo(args: dict) -> str:
        return json.dumps({"echo": args.get("message", "")})

    async def privileged_tool(args: dict) -> str:
        return json.dumps({"secret": "data"})

    server.register_tool(ToolDefinition(
        name="echo",
        description="Echoes the message",
        input_schema={"type": "object", "properties": {"message": {"type": "string"}}},
        required_scope="",
        handler=echo,
    ))
    server.register_tool(ToolDefinition(
        name="privileged_tool",
        description="Requires admin scope",
        input_schema={"type": "object"},
        required_scope="admin:read",
        handler=privileged_tool,
    ))
    return server


@pytest.fixture
def mcp_client(echo_server) -> MCPClient:
    client = MCPClient(agent_id="test-agent", scopes=["search:read", "db:read"])
    client.connect_server(echo_server)
    return client


@pytest.fixture
def admin_client(echo_server) -> MCPClient:
    client = MCPClient(agent_id="admin-agent", scopes=["admin:read"])
    client.connect_server(echo_server)
    return client


# ── MCPServer Tests ───────────────────────────────────────────────────────────

class TestMCPServer:
    def test_register_tool(self, echo_server):
        assert "echo" in echo_server._tools
        assert "privileged_tool" in echo_server._tools

    def test_list_tools(self, echo_server):
        tools = echo_server.list_tools()
        names = [t["name"] for t in tools]
        assert "echo" in names
        assert "privileged_tool" in names

    @pytest.mark.asyncio
    async def test_tools_list_method(self, echo_server):
        response = await echo_server.handle_request(
            {"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}},
            caller_scopes=[],
        )
        assert response["jsonrpc"] == "2.0"
        assert "tools" in response["result"]

    @pytest.mark.asyncio
    async def test_unknown_method(self, echo_server):
        response = await echo_server.handle_request(
            {"jsonrpc": "2.0", "id": "1", "method": "unknown/method", "params": {}},
            caller_scopes=[],
        )
        assert "error" in response
        assert response["error"]["code"] == MCPErrorCode.METHOD_NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_tool_call_success(self, echo_server):
        response = await echo_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "test-1",
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"message": "hello"}},
            },
            caller_scopes=[],
        )
        assert "result" in response
        assert response["result"]["isError"] is False
        content = json.loads(response["result"]["content"][0]["text"])
        assert content["echo"] == "hello"

    @pytest.mark.asyncio
    async def test_tool_not_found(self, echo_server):
        response = await echo_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "test-2",
                "method": "tools/call",
                "params": {"name": "nonexistent_tool", "arguments": {}},
            },
            caller_scopes=[],
        )
        assert "error" in response
        assert response["error"]["code"] == MCPErrorCode.TOOL_NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_scope_enforcement_denied(self, echo_server):
        response = await echo_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "test-3",
                "method": "tools/call",
                "params": {"name": "privileged_tool", "arguments": {}},
            },
            caller_scopes=["search:read"],  # missing admin:read
        )
        assert "error" in response
        assert response["error"]["code"] == MCPErrorCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_scope_enforcement_allowed(self, echo_server):
        response = await echo_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "test-4",
                "method": "tools/call",
                "params": {"name": "privileged_tool", "arguments": {}},
            },
            caller_scopes=["admin:read"],
        )
        assert "result" in response
        assert response["result"]["isError"] is False

    @pytest.mark.asyncio
    async def test_tool_handler_exception(self, mcp_server_empty):
        async def failing_tool(args):
            raise ValueError("Intentional error")

        mcp_server_empty.register_tool(ToolDefinition(
            name="failing",
            description="Always fails",
            input_schema={"type": "object"},
            required_scope="",
            handler=failing_tool,
        ))
        response = await mcp_server_empty.handle_request(
            {"jsonrpc": "2.0", "id": "1", "method": "tools/call",
             "params": {"name": "failing", "arguments": {}}},
            caller_scopes=[],
        )
        assert "error" in response
        assert response["error"]["code"] == MCPErrorCode.TOOL_EXECUTION_ERROR.value


# ── MCPClient Tests ───────────────────────────────────────────────────────────

class TestMCPClient:
    @pytest.mark.asyncio
    async def test_call_tool_success(self, mcp_client):
        result = await mcp_client.call_tool("echo", {"message": "test"}, task_id="t1")
        assert result.is_error is False
        assert result.tool_name == "echo"
        assert "echo" in result.content[0]["text"]
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_call_tool_no_server(self):
        client = MCPClient(agent_id="orphan", scopes=[])
        result = await client.call_tool("echo", {})
        assert result.is_error is True
        assert result.error_code == MCPErrorCode.TOOL_NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_call_tool_scope_denied(self, mcp_client):
        result = await mcp_client.call_tool("privileged_tool", {})
        assert result.is_error is True
        assert result.error_code == MCPErrorCode.UNAUTHORIZED.value

    @pytest.mark.asyncio
    async def test_list_tools(self, mcp_client, echo_server):
        tools = await mcp_client.list_tools(echo_server.server_id)
        assert len(tools) == 2
        names = [t["name"] for t in tools]
        assert "echo" in names

    @pytest.mark.asyncio
    async def test_call_tool_with_admin_scope(self, admin_client):
        result = await admin_client.call_tool("privileged_tool", {})
        assert result.is_error is False


# ── MCPResult Tests ───────────────────────────────────────────────────────────

class TestMCPResult:
    def test_success_factory(self):
        result = MCPResult.success("c1", "echo", "hello", 10.0)
        assert result.is_error is False
        assert result.content[0]["text"] == "hello"
        assert result.latency_ms == 10.0

    def test_error_factory(self):
        result = MCPResult.error("c2", "echo", MCPErrorCode.TOOL_NOT_FOUND, "not found", 5.0)
        assert result.is_error is True
        assert result.error_code == MCPErrorCode.TOOL_NOT_FOUND.value
        assert result.error_message == "not found"

    def test_to_jsonrpc_response_success(self):
        result = MCPResult.success("c3", "echo", "ok", 1.0)
        rpc = result.to_jsonrpc_response()
        assert rpc["jsonrpc"] == "2.0"
        assert "result" in rpc

    def test_to_jsonrpc_response_error(self):
        result = MCPResult.error("c4", "echo", MCPErrorCode.UNAUTHORIZED, "denied", 1.0)
        rpc = result.to_jsonrpc_response()
        assert "error" in rpc


# ── Default MCP Server Tests ──────────────────────────────────────────────────

class TestDefaultMCPServer:
    @pytest.mark.asyncio
    async def test_web_search(self, default_mcp_server):
        client = MCPClient("agent", ["search:read"])
        client.connect_server(default_mcp_server)
        result = await client.call_tool("web_search", {"query": "test", "max_results": 3})
        assert result.is_error is False
        data = json.loads(result.content[0]["text"])
        assert "results" in data
        assert len(data["results"]) == 3

    @pytest.mark.asyncio
    async def test_calculator(self, default_mcp_server):
        client = MCPClient("agent", [])
        client.connect_server(default_mcp_server)
        result = await client.call_tool("calculator", {"expression": "2 + 2 * 3"})
        assert result.is_error is False
        data = json.loads(result.content[0]["text"])
        assert data["result"] == 8

    @pytest.mark.asyncio
    async def test_database_query(self, default_mcp_server):
        client = MCPClient("agent", ["db:read"])
        client.connect_server(default_mcp_server)
        result = await client.call_tool("database_query", {
            "table": "users", "filters": {"status": "active"}
        })
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_web_search_requires_scope(self, default_mcp_server):
        client = MCPClient("agent", [])  # no search:read scope
        client.connect_server(default_mcp_server)
        result = await client.call_tool("web_search", {"query": "test"})
        assert result.is_error is True
        assert result.error_code == MCPErrorCode.UNAUTHORIZED.value
