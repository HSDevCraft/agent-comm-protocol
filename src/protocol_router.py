"""
Protocol Router — Decision Engine
Decides which protocol to use for each operation:
  - LOCAL_EXECUTION: agent handles it locally
  - TOOL_CALL (MCP): requires a tool/data source
  - AGENT_DELEGATION (A2A): requires a specialist agent
  - ORCHESTRATION (ACP): multi-step workflow dispatch
  - NETWORK_BROADCAST (ANP): decentralized cross-org discovery

The router is the "traffic controller" that keeps protocol concerns
out of business logic. Agents declare intent; the router picks the transport.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


from src._logging import get_logger

logger = get_logger(__name__)


class RouteType(str, Enum):
    LOCAL_EXECUTION = "local_execution"
    TOOL_CALL = "tool_call"
    AGENT_DELEGATION = "agent_delegation"
    ORCHESTRATION = "orchestration"
    NETWORK_BROADCAST = "network_broadcast"
    FALLBACK = "fallback"


@dataclass
class RoutingDecision:
    """
    The output of the ProtocolRouter for a given task.
    Contains the chosen route, target, reasoning, and confidence.
    """
    route_type: RouteType
    protocol: str
    target: str
    confidence: float
    reasoning: str
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    estimated_latency_ms: float = 0.0
    requires_streaming: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_type": self.route_type.value,
            "protocol": self.protocol,
            "target": self.target,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "alternatives": self.alternatives,
            "estimated_latency_ms": self.estimated_latency_ms,
            "requires_streaming": self.requires_streaming,
            "metadata": self.metadata,
        }


_TOOL_KEYWORDS = {
    "search", "find", "lookup", "query", "fetch", "retrieve",
    "calculate", "compute", "read file", "write file", "database",
    "get data", "api call", "http request",
}

_LOCAL_KEYWORDS = {
    "summarize", "format", "parse", "convert", "validate",
    "classify", "rank", "filter", "sort", "merge",
}

_ORCHESTRATION_KEYWORDS = {
    "plan", "coordinate", "schedule", "workflow", "pipeline",
    "multi-step", "parallel", "aggregate",
}


@dataclass
class KnownTool:
    name: str
    description_keywords: list[str]
    required_scope: str
    estimated_latency_ms: float = 50.0


@dataclass
class KnownAgent:
    agent_id: str
    capabilities: list[str]
    version: str
    trust_level: str
    avg_latency_ms: float = 100.0
    current_load: float = 0.0
    success_rate: float = 1.0


class ProtocolRouter:
    """
    Central routing brain of the multi-agent system.

    Decision priority (in order):
    1. Is a specific tool registered for this task? → MCP TOOL_CALL
    2. Is a specialist agent registered for this capability? → A2A AGENT_DELEGATION
    3. Is this a multi-step workflow? → ACP ORCHESTRATION
    4. Is this a cross-org discovery request? → ANP NETWORK_BROADCAST
    5. Can the current agent handle it locally? → LOCAL_EXECUTION
    6. None of the above → FALLBACK (escalate or return error)

    This priority ensures:
    - Tools are always preferred over agents for atomic operations (cheaper)
    - Agents are preferred over orchestration for single-capability tasks
    - Orchestration is only used when parallelism/coordination is needed
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self._tools: dict[str, KnownTool] = {}
        self._agents: dict[str, KnownAgent] = {}
        self._local_capabilities: set[str] = set()
        self._routing_history: list[dict[str, Any]] = []

    def register_tool(self, tool: KnownTool) -> None:
        self._tools[tool.name] = tool
        logger.info("router_tool_registered", tool=tool.name)

    def register_agent(self, agent: KnownAgent) -> None:
        self._agents[agent.agent_id] = agent
        logger.info("router_agent_registered", agent_id=agent.agent_id, caps=agent.capabilities)

    def register_local_capability(self, capability: str) -> None:
        self._local_capabilities.add(capability)

    def route(
        self,
        task_description: str,
        required_capability: str = "",
        preferred_tool: str | None = None,
        is_workflow: bool = False,
        cross_org: bool = False,
        stream: bool = False,
        context: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        """
        Main routing method. Returns the best RoutingDecision for the request.
        Logs reasoning and records routing history for observability.
        """
        start = time.monotonic()

        if cross_org:
            decision = self._route_anp(task_description, context)
        elif is_workflow:
            decision = self._route_acp(task_description, context)
        elif preferred_tool and preferred_tool in self._tools:
            decision = self._route_mcp(preferred_tool)
        elif required_capability:
            decision = self._route_by_capability(required_capability, task_description, stream)
        else:
            decision = self._route_by_keywords(task_description, stream)

        decision.metadata["routing_agent"] = self.agent_id
        decision.metadata["routing_latency_ms"] = round((time.monotonic() - start) * 1000, 2)

        self._routing_history.append({
            "timestamp": time.time(),
            "task": task_description[:80],
            "decision": decision.to_dict(),
        })

        logger.info(
            "routing_decision",
            route=decision.route_type.value,
            protocol=decision.protocol,
            target=decision.target,
            confidence=decision.confidence,
        )
        return decision

    def _route_by_capability(
        self,
        capability: str,
        task_description: str,
        stream: bool,
    ) -> RoutingDecision:
        if capability in self._local_capabilities:
            return RoutingDecision(
                route_type=RouteType.LOCAL_EXECUTION,
                protocol="none",
                target=self.agent_id,
                confidence=0.95,
                reasoning=f"Capability '{capability}' is registered locally",
                requires_streaming=stream,
            )

        tool_match = self._find_tool_for_capability(capability)
        if tool_match:
            return RoutingDecision(
                route_type=RouteType.TOOL_CALL,
                protocol="MCP",
                target=tool_match.name,
                confidence=0.90,
                reasoning=f"Tool '{tool_match.name}' matches capability '{capability}'",
                estimated_latency_ms=tool_match.estimated_latency_ms,
            )

        agent_matches = self._find_agents_for_capability(capability)
        if agent_matches:
            best = agent_matches[0]
            alts = [
                {
                    "agent_id": a.agent_id,
                    "score": round(self._score_agent(a, capability), 3),
                    "version": a.version,
                }
                for a in agent_matches[1:]
            ]
            return RoutingDecision(
                route_type=RouteType.AGENT_DELEGATION,
                protocol="A2A",
                target=best.agent_id,
                confidence=self._score_agent(best, capability),
                reasoning=(
                    f"Delegating to '{best.agent_id}' (v{best.version}) "
                    f"for capability '{capability}'. "
                    f"{len(agent_matches)} candidates evaluated."
                ),
                alternatives=alts,
                estimated_latency_ms=best.avg_latency_ms,
                requires_streaming=stream,
            )

        return RoutingDecision(
            route_type=RouteType.FALLBACK,
            protocol="none",
            target="",
            confidence=0.0,
            reasoning=f"No local capability, tool, or agent found for '{capability}'",
        )

    def _route_by_keywords(self, task_description: str, stream: bool) -> RoutingDecision:
        desc_lower = task_description.lower()

        for tool_name, tool in self._tools.items():
            if any(kw in desc_lower for kw in tool.description_keywords):
                return RoutingDecision(
                    route_type=RouteType.TOOL_CALL,
                    protocol="MCP",
                    target=tool_name,
                    confidence=0.80,
                    reasoning=f"Keyword match for tool '{tool_name}'",
                    estimated_latency_ms=tool.estimated_latency_ms,
                )

        if any(kw in desc_lower for kw in _LOCAL_KEYWORDS):
            return RoutingDecision(
                route_type=RouteType.LOCAL_EXECUTION,
                protocol="none",
                target=self.agent_id,
                confidence=0.75,
                reasoning="Task appears to be a local transformation (no external data needed)",
            )

        if any(kw in desc_lower for kw in _ORCHESTRATION_KEYWORDS):
            return RoutingDecision(
                route_type=RouteType.ORCHESTRATION,
                protocol="ACP",
                target="orchestrator",
                confidence=0.70,
                reasoning="Task involves multi-step coordination — routing to ACP orchestrator",
            )

        all_agents = list(self._agents.values())
        if all_agents:
            best = all_agents[0]
            return RoutingDecision(
                route_type=RouteType.AGENT_DELEGATION,
                protocol="A2A",
                target=best.agent_id,
                confidence=0.40,
                reasoning="No clear route found — delegating to most-capable registered agent",
                requires_streaming=stream,
            )

        if self._local_capabilities:
            return RoutingDecision(
                route_type=RouteType.LOCAL_EXECUTION,
                protocol="none",
                target=self.agent_id,
                confidence=0.60,
                reasoning=(
                    "No tool/agent keyword match — falling back to local execution "
                    f"(registered capabilities: {sorted(self._local_capabilities)})"
                ),
                requires_streaming=stream,
            )

        return RoutingDecision(
            route_type=RouteType.FALLBACK,
            protocol="none",
            target="",
            confidence=0.0,
            reasoning="No tools, agents, or local capabilities registered for this task",
        )

    def _route_mcp(self, tool_name: str) -> RoutingDecision:
        tool = self._tools[tool_name]
        return RoutingDecision(
            route_type=RouteType.TOOL_CALL,
            protocol="MCP",
            target=tool_name,
            confidence=0.99,
            reasoning=f"Explicit tool '{tool_name}' requested",
            estimated_latency_ms=tool.estimated_latency_ms,
        )

    def _route_acp(self, task_description: str, context: dict[str, Any] | None) -> RoutingDecision:
        return RoutingDecision(
            route_type=RouteType.ORCHESTRATION,
            protocol="ACP",
            target="orchestrator",
            confidence=0.92,
            reasoning="Multi-step workflow detected — routing to ACP orchestrator for decomposition",
            metadata={"context": context or {}},
        )

    def _route_anp(self, task_description: str, context: dict[str, Any] | None) -> RoutingDecision:
        return RoutingDecision(
            route_type=RouteType.NETWORK_BROADCAST,
            protocol="ANP",
            target="anp-network",
            confidence=0.80,
            reasoning="Cross-organizational task — using ANP for decentralized agent discovery",
            metadata={"context": context or {}},
        )

    def _find_tool_for_capability(self, capability: str) -> KnownTool | None:
        cap_lower = capability.lower()
        for tool in self._tools.values():
            if any(kw in cap_lower for kw in tool.description_keywords):
                return tool
        return None

    def _find_agents_for_capability(self, capability: str) -> list[KnownAgent]:
        matches = [
            agent for agent in self._agents.values()
            if capability in agent.capabilities
        ]
        matches.sort(key=lambda a: self._score_agent(a, capability), reverse=True)
        return matches

    def _score_agent(self, agent: KnownAgent, capability: str) -> float:
        cap_score = 1.0 if capability in agent.capabilities else 0.0
        try:
            parts = agent.version.split(".")
            version_score = min(1.0, int(parts[0]) / 10)
        except (ValueError, IndexError):
            version_score = 0.5
        load_score = max(0.0, 1.0 - agent.current_load)
        success_score = agent.success_rate
        latency_score = max(0.0, 1.0 - agent.avg_latency_ms / 5000)
        return round(
            0.5 * cap_score
            + 0.25 * success_score
            + 0.10 * version_score
            + 0.10 * load_score
            + 0.05 * latency_score,
            4,
        )

    def routing_stats(self) -> dict[str, Any]:
        if not self._routing_history:
            return {"total_routes": 0}
        by_type: dict[str, int] = {}
        by_protocol: dict[str, int] = {}
        for entry in self._routing_history:
            rt = entry["decision"]["route_type"]
            proto = entry["decision"]["protocol"]
            by_type[rt] = by_type.get(rt, 0) + 1
            by_protocol[proto] = by_protocol.get(proto, 0) + 1
        return {
            "total_routes": len(self._routing_history),
            "by_route_type": by_type,
            "by_protocol": by_protocol,
        }
