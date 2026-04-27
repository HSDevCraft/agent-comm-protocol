"""
Core Agent Class — The fundamental building block of the multi-agent system.

Every agent in the ecosystem is an instance of (or subclass of) BaseAgent.

Responsibilities:
  A. Decision Engine    — decide how to handle each incoming task
  B. Protocol Dispatch  — route via MCP / A2A / ACP / ANP using ProtocolRouter
  C. Tool Calling       — execute MCP tool calls and parse results
  D. Agent Delegation   — delegate sub-tasks to specialist agents via A2A
  E. Memory Integration — read/write shared memory before/after tasks
  F. Observability      — emit ReasoningTraces for every decision
  G. Security           — validate tokens, sanitize inputs
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


from src._logging import get_logger

from src.memory import MemoryManager
from src.messaging import MessageBus
from src.observability import (
    ObservabilityEngine,
    DecisionType,
    Alternative,
    SpanTracer,
)
from src.protocol_router import KnownAgent, KnownTool, ProtocolRouter, RouteType, RoutingDecision
from src.protocols.a2a import A2AClient, AgentCard, AgentRegistry, A2ATask, TaskPriority
from src.protocols.acp import ACPOrchestrator, Workflow, WorkflowStep
from src.protocols.mcp import MCPClient, MCPServer, MCPResult
from src.security import AgentIdentityToken, AgentRole, SecurityGateway

logger = get_logger(__name__)


class AgentStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    SHUTTING_DOWN = "shutting_down"


@dataclass
class AgentConfig:
    """Configuration for an Agent instance."""
    agent_id: str
    name: str
    version: str
    role: AgentRole
    capabilities: list[str]
    description: str = ""
    organization: str = "default"
    environment: str = "production"
    max_concurrent_tasks: int = 10
    task_timeout_seconds: float = 60.0
    max_delegation_depth: int = 3
    mcp_scopes: list[str] = field(default_factory=lambda: ["search:read", "db:read"])


@dataclass
class TaskResult:
    """Standardized result returned by any agent after completing a task."""
    task_id: str
    agent_id: str
    success: bool
    output: dict[str, Any]
    error: str | None = None
    duration_ms: float = 0.0
    protocol_used: str = "none"
    trace_id: str = ""
    tool_calls_made: list[str] = field(default_factory=list)
    agents_delegated_to: list[str] = field(default_factory=list)


class BaseAgent:
    """
    The core agent class. Subclass this to create domain-specific agents.

    Key design decisions:
    - Protocol selection is fully delegated to ProtocolRouter (separation of concerns)
    - Every decision emits a ReasoningTrace (explainability by design)
    - All inputs pass through SecurityGateway before processing (security by default)
    - Memory is checked before tool calls to prevent redundant external calls

    Override `_execute_locally()` in subclasses to add domain-specific logic.
    """

    def __init__(
        self,
        config: AgentConfig,
        mcp_server: MCPServer | None = None,
        a2a_registry: AgentRegistry | None = None,
        acp_orchestrator: ACPOrchestrator | None = None,
        memory_manager: MemoryManager | None = None,
        security_gateway: SecurityGateway | None = None,
        observability_engine: ObservabilityEngine | None = None,
        message_bus: MessageBus | None = None,
    ) -> None:
        self.config = config
        self.agent_id = config.agent_id
        self.status = AgentStatus.IDLE
        self._active_task_count = 0
        self._completed_task_count = 0
        self._failed_task_count = 0

        self.memory = memory_manager or MemoryManager()
        self.security = security_gateway or SecurityGateway()
        self.obs = observability_engine or ObservabilityEngine()
        self.bus = message_bus or MessageBus()

        self.router = ProtocolRouter(agent_id=self.agent_id)
        self._a2a_registry = a2a_registry or AgentRegistry()
        self._acp = acp_orchestrator or ACPOrchestrator()

        self._mcp_client = MCPClient(
            agent_id=self.agent_id,
            scopes=config.mcp_scopes,
        )
        if mcp_server:
            self._mcp_client.connect_server(mcp_server)

        self._a2a_client = A2AClient(
            caller_id=self.agent_id,
            registry=self._a2a_registry,
            max_delegation_depth=config.max_delegation_depth,
        )

        self._identity_token: AgentIdentityToken | None = None
        self._tracer: SpanTracer | None = None

        self._register_self()
        logger.info("agent_initialized", agent_id=self.agent_id, role=config.role.value)

    def _register_self(self) -> None:
        card = AgentCard(
            agent_id=self.agent_id,
            name=self.config.name,
            version=self.config.version,
            description=self.config.description,
            capabilities=self.config.capabilities,
            endpoint=f"http://localhost/agents/{self.agent_id}",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            trust_level="internal",
        )
        self._a2a_registry.register(card)
        card._a2a_handler = self._handle_a2a_task

        for capability in self.config.capabilities:
            self.router.register_local_capability(capability)

    def connect_mcp_server(self, server: MCPServer) -> None:
        self._mcp_client.connect_server(server)
        for tool_name, tool_def in server._tools.items():
            self.router.register_tool(KnownTool(
                name=tool_name,
                description_keywords=tool_name.split("_"),
                required_scope=tool_def.required_scope,
            ))

    def register_known_agent(self, card: AgentCard, avg_latency_ms: float = 100.0) -> None:
        self._a2a_registry.register(card)
        self.router.register_agent(KnownAgent(
            agent_id=card.agent_id,
            capabilities=card.capabilities,
            version=card.version,
            trust_level=card.trust_level,
            avg_latency_ms=avg_latency_ms,
        ))

    def issue_token(self) -> AgentIdentityToken:
        """Issue (or renew) this agent's identity token."""
        self._identity_token = self.security.issue_token(
            agent_id=self.agent_id,
            agent_version=self.config.version,
            role=self.config.role,
            capabilities=self.config.capabilities,
            organization=self.config.organization,
            environment=self.config.environment,
        )
        return self._identity_token

    async def run(
        self,
        query: str,
        session_id: str = "",
        correlation_id: str = "",
        stream: bool = False,
        context: dict[str, Any] | None = None,
        tracer: SpanTracer | None = None,
    ) -> TaskResult:
        """
        Main entry point. Given a natural-language query or structured task:
        1. Sanitize input (security)
        2. Check memory for cached result
        3. Route to correct protocol (ProtocolRouter)
        4. Execute via chosen protocol
        5. Store result in memory
        6. Emit reasoning trace
        7. Return TaskResult

        This is the "decision engine" — the brain of the agent.
        """
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        corr_id = correlation_id or task_id
        sess_id = session_id or f"sess-{uuid.uuid4().hex[:8]}"
        self._tracer = tracer or self.obs.new_tracer()
        span_id = self._tracer.start_span(self.agent_id, "run")

        self._active_task_count += 1
        self.status = AgentStatus.BUSY
        start = time.monotonic()

        logger.info("agent_run_start", agent=self.agent_id, task_id=task_id, query=query[:80])

        sanitized_query, is_clean = self.security.sanitize_input(query, self.agent_id, context="query")
        if not is_clean:
            logger.warning("agent_input_sanitized", agent=self.agent_id)

        cached = self.memory.read_working(f"result:{sanitized_query[:64]}", self.agent_id)
        if cached:
            logger.info("agent_cache_hit", agent=self.agent_id)
            duration_ms = (time.monotonic() - start) * 1000
            return TaskResult(
                task_id=task_id,
                agent_id=self.agent_id,
                success=True,
                output=cached,
                duration_ms=duration_ms,
                protocol_used="memory_cache",
                trace_id=self._tracer.trace_id,
            )

        decision = self.router.route(
            task_description=sanitized_query,
            stream=stream,
            context=context,
        )

        try:
            result = await self._dispatch(decision, sanitized_query, task_id, corr_id, context or {})
        except Exception as exc:
            logger.error("agent_run_error", agent=self.agent_id, error=str(exc))
            self._failed_task_count += 1
            self.status = AgentStatus.IDLE
            self._active_task_count -= 1
            return TaskResult(
                task_id=task_id,
                agent_id=self.agent_id,
                success=False,
                output={},
                error=str(exc),
                duration_ms=(time.monotonic() - start) * 1000,
                protocol_used=decision.protocol,
                trace_id=self._tracer.trace_id,
            )

        duration_ms = (time.monotonic() - start) * 1000

        if result.success:
            self.memory.write_working(
                key=f"result:{sanitized_query[:64]}",
                content=result.output,
                agent_id=self.agent_id,
                session_id=sess_id,
                readable_by=[self.agent_id],
            )

        trace = self.obs.record_decision(
            agent_id=self.agent_id,
            decision=decision.route_type.value,
            decision_type=self._route_to_decision_type(decision.route_type),
            reasoning=decision.reasoning,
            confidence=decision.confidence,
            protocol_used=decision.protocol,
            inputs={"query": sanitized_query[:200], "context": str(context)[:200]},
            output={"success": result.success, "output_keys": list(result.output.keys())},
            span_id=span_id,
            trace_id=self._tracer.trace_id,
            alternatives=[
                Alternative(a.get("agent_id", a.get("tool", "?")), a.get("score", 0.0), "not selected")
                for a in decision.alternatives
            ],
            duration_ms=duration_ms,
        )
        self._tracer.record(trace)

        self._completed_task_count += 1
        self.status = AgentStatus.IDLE
        self._active_task_count -= 1

        result.duration_ms = duration_ms
        result.trace_id = self._tracer.trace_id
        return result

    async def _dispatch(
        self,
        decision: RoutingDecision,
        query: str,
        task_id: str,
        corr_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        """Dispatch to the correct protocol implementation based on routing decision."""
        if decision.route_type == RouteType.LOCAL_EXECUTION:
            return await self._execute_locally(query, task_id, context)

        elif decision.route_type == RouteType.TOOL_CALL:
            return await self._call_tool(decision.target, query, task_id, context)

        elif decision.route_type == RouteType.AGENT_DELEGATION:
            return await self._delegate_to_agent(decision, query, task_id, corr_id)

        elif decision.route_type == RouteType.ORCHESTRATION:
            return await self._orchestrate(query, task_id, corr_id, context)

        elif decision.route_type == RouteType.NETWORK_BROADCAST:
            return await self._broadcast_anp(query, task_id, context)

        else:
            return TaskResult(
                task_id=task_id,
                agent_id=self.agent_id,
                success=False,
                output={},
                error=f"No route available for task: {query[:80]}",
                protocol_used="fallback",
            )

    async def _execute_locally(
        self,
        query: str,
        task_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        """
        Local execution — override in subclasses for domain-specific logic.
        Default: simple echo with context summary.
        """
        logger.info("agent_local_exec", agent=self.agent_id, task_id=task_id)
        await asyncio.sleep(0.01)
        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=True,
            output={
                "result": f"[{self.agent_id}] Local result for: {query}",
                "context_keys": list(context.keys()),
                "agent_version": self.config.version,
            },
            protocol_used="local",
        )

    async def _call_tool(
        self,
        tool_name: str,
        query: str,
        task_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        """
        MCP Tool Call — calls a registered tool and returns structured result.
        Handles tool errors and surfaces them cleanly.
        """
        logger.info("agent_mcp_call", agent=self.agent_id, tool=tool_name, task_id=task_id)

        arguments = self._build_tool_arguments(tool_name, query, context)
        mcp_result: MCPResult = await self._mcp_client.call_tool(
            tool_name=tool_name,
            arguments=arguments,
            task_id=task_id,
        )

        if mcp_result.is_error:
            return TaskResult(
                task_id=task_id,
                agent_id=self.agent_id,
                success=False,
                output={},
                error=mcp_result.error_message,
                protocol_used="MCP",
                tool_calls_made=[tool_name],
            )

        content_text = mcp_result.content[0]["text"] if mcp_result.content else ""
        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=True,
            output={
                "tool": tool_name,
                "result": content_text,
                "latency_ms": mcp_result.latency_ms,
            },
            protocol_used="MCP",
            tool_calls_made=[tool_name],
        )

    async def _delegate_to_agent(
        self,
        decision: RoutingDecision,
        query: str,
        task_id: str,
        corr_id: str,
    ) -> TaskResult:
        """
        A2A Delegation — delegates task to a specialist agent.
        Extracts the capability from the router's target (agent_id).
        """
        target_agent_id = decision.target
        target_card = self._a2a_registry.get(target_agent_id)
        if not target_card:
            return TaskResult(
                task_id=task_id,
                agent_id=self.agent_id,
                success=False,
                output={},
                error=f"Agent not found in registry: {target_agent_id}",
                protocol_used="A2A",
            )

        capability = target_card.capabilities[0] if target_card.capabilities else "general"

        a2a_task: A2ATask = await self._a2a_client.delegate(
            capability=capability,
            input_data={"query": query, "requester": self.agent_id},
            correlation_id=corr_id,
            priority=TaskPriority.MEDIUM,
        )

        if a2a_task.output:
            return TaskResult(
                task_id=task_id,
                agent_id=self.agent_id,
                success=True,
                output=a2a_task.output,
                protocol_used="A2A",
                agents_delegated_to=[target_agent_id],
            )
        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=False,
            output={},
            error=a2a_task.error or "Delegation failed with no output",
            protocol_used="A2A",
            agents_delegated_to=[target_agent_id],
        )

    async def _orchestrate(
        self,
        query: str,
        task_id: str,
        corr_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        """
        ACP Orchestration — dispatches a multi-step workflow.
        Builds a simple two-step workflow as demonstration.
        """
        logger.info("agent_orchestrate", agent=self.agent_id, task_id=task_id)

        workflow = Workflow(
            workflow_id=f"wf-{uuid.uuid4().hex[:8]}",
            name=f"workflow-for-{task_id}",
            steps=[
                WorkflowStep(
                    step_id="step-1",
                    name="primary_execution",
                    capability="general",
                    assigned_agent=self.agent_id,
                ),
                WorkflowStep(
                    step_id="step-2",
                    name="result_aggregation",
                    capability="general",
                    assigned_agent=self.agent_id,
                    depends_on=["step-1"],
                ),
            ],
        )

        self._acp.register_handler(self.agent_id, self._acp_handler)
        results = await self._acp.execute_workflow(workflow, {"query": query})

        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=True,
            output={"workflow_id": workflow.workflow_id, "results": results},
            protocol_used="ACP",
        )

    async def _broadcast_anp(
        self,
        query: str,
        task_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        """ANP Network Broadcast — discovers cross-org agents (stub for demo)."""
        logger.info("agent_anp_broadcast", agent=self.agent_id, task_id=task_id)
        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=True,
            output={
                "protocol": "ANP",
                "message": "Cross-org agent discovery initiated",
                "query": query,
            },
            protocol_used="ANP",
        )

    async def _acp_handler(self, envelope: Any) -> dict[str, Any]:
        """ACP message handler — processes orchestrator dispatches."""
        payload = envelope.payload if hasattr(envelope, "payload") else envelope
        return {
            "agent_id": self.agent_id,
            "step_id": payload.get("step_id", "unknown"),
            "status": "completed",
            "result": f"Step completed by {self.agent_id}",
        }

    async def _handle_a2a_task(self, task: A2ATask) -> dict[str, Any]:
        """
        A2A task handler — called when another agent delegates a task to this agent.
        Routes the task through the local decision engine.
        """
        query = task.input.get("query", str(task.input))
        result = await self.run(
            query=query,
            correlation_id=task.correlation_id,
            context={"delegated_from": task.sender_id, "capability": task.capability},
        )
        return result.output if result.success else {"error": result.error}

    def _build_tool_arguments(
        self,
        tool_name: str,
        query: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Map tool names to their expected argument shapes."""
        base = {"query": query}
        if tool_name == "web_search":
            return {"query": query, "max_results": 5}
        if tool_name == "database_query":
            return {
                "table": context.get("table", "default_table"),
                "filters": context.get("filters", {}),
                "fields": context.get("fields", ["*"]),
            }
        if tool_name == "calculator":
            expression = context.get("expression", query)
            return {"expression": expression}
        if tool_name == "file_reader":
            return {"path": context.get("path", query)}
        return base

    def _route_to_decision_type(self, route_type: RouteType) -> DecisionType:
        mapping = {
            RouteType.LOCAL_EXECUTION: DecisionType.LOCAL_EXECUTION,
            RouteType.TOOL_CALL: DecisionType.TOOL_CALL,
            RouteType.AGENT_DELEGATION: DecisionType.DELEGATION,
            RouteType.ORCHESTRATION: DecisionType.DELEGATION,
            RouteType.NETWORK_BROADCAST: DecisionType.DELEGATION,
            RouteType.FALLBACK: DecisionType.FALLBACK,
        }
        return mapping.get(route_type, DecisionType.LOCAL_EXECUTION)

    def agent_card(self) -> AgentCard:
        return self._a2a_registry.get(self.agent_id) or AgentCard(
            agent_id=self.agent_id,
            name=self.config.name,
            version=self.config.version,
            description=self.config.description,
            capabilities=self.config.capabilities,
            endpoint=f"http://localhost/agents/{self.agent_id}",
            input_schema={},
            output_schema={},
        )

    def stats(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.config.role.value,
            "status": self.status.value,
            "active_tasks": self._active_task_count,
            "completed_tasks": self._completed_task_count,
            "failed_tasks": self._failed_task_count,
            "routing_stats": self.router.routing_stats(),
            "memory_stats": self.memory.stats(),
            "decision_log_count": self.obs.decision_log.count(),
        }


class SpecialistAgent(BaseAgent):
    """
    A domain-specialist agent with custom local execution logic.
    Subclass BaseAgent and override `_execute_locally()` for real LLM calls.
    """

    def __init__(self, config: AgentConfig, domain_knowledge: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._domain_knowledge = domain_knowledge or {}

    async def _execute_locally(
        self,
        query: str,
        task_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        logger.info("specialist_exec", agent=self.agent_id, capability=self.config.capabilities)
        await asyncio.sleep(0.02)

        knowledge_hit = next(
            (v for k, v in self._domain_knowledge.items() if k.lower() in query.lower()),
            None,
        )
        result_text = knowledge_hit or f"[{self.config.name}] Processed: {query}"

        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=True,
            output={
                "specialist": self.config.name,
                "capabilities": self.config.capabilities,
                "result": result_text,
                "confidence": 0.87,
            },
            protocol_used="local",
        )
