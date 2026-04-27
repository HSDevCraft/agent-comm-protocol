"""
Shared pytest fixtures for unit and integration tests.
"""
from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.agent import AgentConfig, BaseAgent, SpecialistAgent
from src.failure.handlers import CircuitBreakerConfig, RetryConfig
from src.memory import MemoryManager
from src.messaging import MessageBus
from src.observability import ObservabilityEngine
from src.protocol_router import KnownAgent, KnownTool, ProtocolRouter
from src.protocols.a2a import AgentCard, AgentRegistry, A2AClient
from src.protocols.acp import ACPOrchestrator
from src.protocols.mcp import MCPServer, MCPClient, ToolDefinition, build_default_mcp_server
from src.security import AgentRole, SecurityGateway


@pytest.fixture
def default_mcp_server() -> MCPServer:
    return build_default_mcp_server()


@pytest.fixture
def mcp_server_empty() -> MCPServer:
    return MCPServer("test-server")


@pytest.fixture
def agent_registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture
def memory_manager() -> MemoryManager:
    return MemoryManager()


@pytest.fixture
def security_gateway() -> SecurityGateway:
    return SecurityGateway()


@pytest.fixture
def observability_engine() -> ObservabilityEngine:
    return ObservabilityEngine()


@pytest.fixture
def message_bus() -> MessageBus:
    return MessageBus()


@pytest.fixture
def acp_orchestrator() -> ACPOrchestrator:
    return ACPOrchestrator("test-orchestrator")


@pytest.fixture
def planner_config() -> AgentConfig:
    return AgentConfig(
        agent_id="test-planner",
        name="TestPlanner",
        version="1.0.0",
        role=AgentRole.PLANNER,
        capabilities=["planning", "coordination"],
        mcp_scopes=["search:read", "db:read"],
    )


@pytest.fixture
def specialist_config() -> AgentConfig:
    return AgentConfig(
        agent_id="test-specialist",
        name="TestSpecialist",
        version="1.0.0",
        role=AgentRole.SPECIALIST,
        capabilities=["financial_analysis", "risk_assessment"],
        mcp_scopes=["db:read", "search:read"],
    )


@pytest.fixture
def base_agent(
    planner_config,
    default_mcp_server,
    agent_registry,
    memory_manager,
    security_gateway,
    observability_engine,
) -> BaseAgent:
    agent = BaseAgent(
        config=planner_config,
        mcp_server=default_mcp_server,
        a2a_registry=agent_registry,
        memory_manager=memory_manager,
        security_gateway=security_gateway,
        observability_engine=observability_engine,
    )
    return agent


@pytest.fixture
def specialist_agent(
    specialist_config,
    agent_registry,
    memory_manager,
    security_gateway,
    observability_engine,
) -> SpecialistAgent:
    return SpecialistAgent(
        config=specialist_config,
        domain_knowledge={
            "revenue": "Q3 revenue grew 12% YoY to $4.2B",
            "risk": "Moderate risk profile: debt-to-equity 0.35",
        },
        a2a_registry=agent_registry,
        memory_manager=memory_manager,
        security_gateway=security_gateway,
        observability_engine=observability_engine,
    )


@pytest.fixture
def sample_agent_card() -> AgentCard:
    return AgentCard(
        agent_id="test-finance-agent",
        name="TestFinanceAgent",
        version="2.0.0",
        description="Test finance agent",
        capabilities=["financial_analysis", "risk_assessment"],
        endpoint="http://test/agents/finance",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        trust_level="internal",
    )


@pytest.fixture
def retry_config_fast() -> RetryConfig:
    return RetryConfig(max_retries=2, base_delay_seconds=0.001, jitter_fraction=0.0)


@pytest.fixture
def circuit_config_sensitive() -> CircuitBreakerConfig:
    return CircuitBreakerConfig(
        failure_threshold=2,
        success_threshold=1,
        timeout_seconds=0.1,
        half_open_max_calls=2,
    )
