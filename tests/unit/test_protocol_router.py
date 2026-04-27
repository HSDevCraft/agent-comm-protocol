"""
Unit tests for ProtocolRouter — the central routing decision engine
"""
from __future__ import annotations

import pytest

from src.protocol_router import (
    KnownAgent,
    KnownTool,
    ProtocolRouter,
    RouteType,
    RoutingDecision,
)


@pytest.fixture
def router() -> ProtocolRouter:
    return ProtocolRouter(agent_id="test-router")


@pytest.fixture
def router_with_tools(router) -> ProtocolRouter:
    router.register_tool(KnownTool(
        name="web_search",
        description_keywords=["search", "find", "lookup", "web"],
        required_scope="search:read",
        estimated_latency_ms=80.0,
    ))
    router.register_tool(KnownTool(
        name="database_query",
        description_keywords=["database", "query", "db", "table"],
        required_scope="db:read",
        estimated_latency_ms=40.0,
    ))
    return router


@pytest.fixture
def router_with_agents(router) -> ProtocolRouter:
    router.register_agent(KnownAgent(
        agent_id="finance-agent",
        capabilities=["financial_analysis", "risk_assessment"],
        version="2.0.0",
        trust_level="internal",
        avg_latency_ms=150.0,
        current_load=0.1,
        success_rate=0.95,
    ))
    router.register_agent(KnownAgent(
        agent_id="legal-agent",
        capabilities=["legal_review", "compliance_check"],
        version="1.0.0",
        trust_level="internal",
        avg_latency_ms=200.0,
        current_load=0.2,
        success_rate=0.90,
    ))
    return router


# ── Registration Tests ────────────────────────────────────────────────────────

class TestRegistration:
    def test_register_tool(self, router):
        router.register_tool(KnownTool("my_tool", ["search"], "scope:read"))
        assert "my_tool" in router._tools

    def test_register_agent(self, router):
        router.register_agent(KnownAgent("agent-1", ["cap1"], "1.0.0", "internal"))
        assert "agent-1" in router._agents

    def test_register_local_capability(self, router):
        router.register_local_capability("planning")
        assert "planning" in router._local_capabilities


# ── Local Execution Routing ───────────────────────────────────────────────────

class TestLocalExecution:
    def test_route_to_local_when_capability_registered(self, router):
        router.register_local_capability("summarization")
        decision = router.route(
            task_description="summarize this text",
            required_capability="summarization",
        )
        assert decision.route_type == RouteType.LOCAL_EXECUTION
        assert decision.target == "test-router"
        assert decision.confidence > 0.9

    def test_route_local_by_keyword_match(self, router):
        router.register_local_capability("general")
        decision = router.route("summarize and format this document")
        assert decision.route_type == RouteType.LOCAL_EXECUTION

    def test_local_capabilities_fallback_before_fallback(self, router):
        router.register_local_capability("anything")
        decision = router.route("do something unrecognized xyz123")
        assert decision.route_type == RouteType.LOCAL_EXECUTION
        assert decision.confidence == 0.60


# ── Tool Call Routing ─────────────────────────────────────────────────────────

class TestToolCallRouting:
    def test_route_to_tool_by_preferred_tool(self, router_with_tools):
        decision = router_with_tools.route(
            task_description="some task",
            preferred_tool="web_search",
        )
        assert decision.route_type == RouteType.TOOL_CALL
        assert decision.protocol == "MCP"
        assert decision.target == "web_search"
        assert decision.confidence == 0.99

    def test_route_to_tool_by_keyword(self, router_with_tools):
        decision = router_with_tools.route("search for LLM benchmarks online")
        assert decision.route_type == RouteType.TOOL_CALL
        assert decision.target == "web_search"
        assert decision.confidence == 0.80

    def test_route_to_tool_by_capability(self, router_with_tools):
        decision = router_with_tools.route(
            task_description="need data",
            required_capability="web_search",
        )
        assert decision.route_type == RouteType.TOOL_CALL
        assert decision.target == "web_search"

    def test_preferred_tool_not_registered_falls_through(self, router):
        router.register_local_capability("general")
        decision = router.route("task", preferred_tool="nonexistent_tool")
        # preferred_tool not in _tools → falls to keyword routing → local
        assert decision.route_type != RouteType.TOOL_CALL

    def test_tool_latency_set_in_decision(self, router_with_tools):
        decision = router_with_tools.route(task_description="anything", preferred_tool="web_search")
        assert decision.estimated_latency_ms == 80.0


# ── Agent Delegation Routing ──────────────────────────────────────────────────

class TestAgentDelegationRouting:
    def test_route_to_agent_by_capability(self, router_with_agents):
        decision = router_with_agents.route(
            task_description="analyze the portfolio",
            required_capability="financial_analysis",
        )
        assert decision.route_type == RouteType.AGENT_DELEGATION
        assert decision.protocol == "A2A"
        assert decision.target == "finance-agent"

    def test_route_selects_best_scoring_agent(self, router_with_agents):
        router_with_agents.register_agent(KnownAgent(
            agent_id="finance-agent-v3",
            capabilities=["financial_analysis"],
            version="3.0.0",
            trust_level="internal",
            avg_latency_ms=100.0,
            current_load=0.0,
            success_rate=0.99,
        ))
        decision = router_with_agents.route(
            task_description="financial analysis task",
            required_capability="financial_analysis",
        )
        # v3 should score higher due to better version/success_rate
        assert decision.target == "finance-agent-v3"

    def test_alternatives_listed_in_decision(self, router_with_agents):
        router_with_agents.register_agent(KnownAgent(
            agent_id="finance-v2", capabilities=["financial_analysis"],
            version="1.0.0", trust_level="internal",
        ))
        decision = router_with_agents.route(
            task_description="",
            required_capability="financial_analysis",
        )
        assert decision.route_type == RouteType.AGENT_DELEGATION
        # Should have at least one alternative
        assert len(decision.alternatives) >= 1

    def test_streaming_propagated(self, router_with_agents):
        decision = router_with_agents.route(
            task_description="stream this",
            required_capability="financial_analysis",
            stream=True,
        )
        assert decision.requires_streaming is True


# ── Orchestration and Network Routing ────────────────────────────────────────

class TestOrchestrationRouting:
    def test_route_to_acp_when_workflow(self, router):
        decision = router.route(
            task_description="multi-step workflow",
            is_workflow=True,
        )
        assert decision.route_type == RouteType.ORCHESTRATION
        assert decision.protocol == "ACP"
        assert decision.confidence >= 0.9

    def test_route_to_anp_when_cross_org(self, router):
        decision = router.route(
            task_description="analyze external partner data",
            cross_org=True,
        )
        assert decision.route_type == RouteType.NETWORK_BROADCAST
        assert decision.protocol == "ANP"

    def test_orchestration_keyword_routing(self, router):
        decision = router.route("coordinate the pipeline workflow")
        assert decision.route_type == RouteType.ORCHESTRATION


# ── Fallback Routing ──────────────────────────────────────────────────────────

class TestFallbackRouting:
    def test_fallback_when_no_match(self, router):
        # No tools, no agents, no local capabilities
        decision = router.route("some unknown task xyz123")
        assert decision.route_type == RouteType.FALLBACK
        assert decision.confidence == 0.0

    def test_fallback_capability_not_found(self, router):
        decision = router.route(
            task_description="",
            required_capability="nonexistent_capability",
        )
        assert decision.route_type == RouteType.FALLBACK


# ── Routing Decision Structure ────────────────────────────────────────────────

class TestRoutingDecisionStructure:
    def test_decision_has_metadata(self, router):
        router.register_local_capability("general")
        decision = router.route("do something general")
        assert "routing_agent" in decision.metadata
        assert decision.metadata["routing_agent"] == "test-router"
        assert "routing_latency_ms" in decision.metadata

    def test_decision_to_dict(self, router):
        router.register_local_capability("general")
        decision = router.route("something")
        d = decision.to_dict()
        assert "route_type" in d
        assert "protocol" in d
        assert "confidence" in d
        assert "reasoning" in d

    def test_routing_history_populated(self, router):
        router.register_local_capability("general")
        router.route("first task")
        router.route("second task")
        stats = router.routing_stats()
        assert stats["total_routes"] == 2

    def test_routing_stats_by_type(self, router_with_tools):
        router_with_tools.route(task_description="search something", preferred_tool="web_search")
        stats = router_with_tools.routing_stats()
        assert stats["by_route_type"]["tool_call"] == 1


# ── Scoring Tests ─────────────────────────────────────────────────────────────

class TestAgentScoring:
    def test_score_increases_with_capability_match(self, router):
        agent_match = KnownAgent("a1", ["financial_analysis"], "1.0.0", "internal")
        agent_no_match = KnownAgent("a2", ["web_search"], "1.0.0", "internal")
        s_match = router._score_agent(agent_match, "financial_analysis")
        s_no_match = router._score_agent(agent_no_match, "financial_analysis")
        assert s_match > s_no_match

    def test_score_decreases_with_high_load(self, router):
        low_load = KnownAgent("a1", ["cap"], "1.0.0", "internal", current_load=0.0)
        high_load = KnownAgent("a2", ["cap"], "1.0.0", "internal", current_load=1.0)
        s_low = router._score_agent(low_load, "cap")
        s_high = router._score_agent(high_load, "cap")
        assert s_low > s_high

    def test_score_in_valid_range(self, router):
        agent = KnownAgent("a", ["cap"], "2.0.0", "internal",
                           current_load=0.5, success_rate=0.85)
        score = router._score_agent(agent, "cap")
        assert 0.0 <= score <= 1.0
