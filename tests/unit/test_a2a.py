"""
Unit tests for A2A — Agent-to-Agent Protocol
"""
from __future__ import annotations

import time
import pytest

from src.protocols.a2a import (
    A2AClient,
    A2ATask,
    AgentCard,
    AgentRegistry,
    TaskPriority,
    TaskStatus,
)


# ── AgentCard Tests ───────────────────────────────────────────────────────────

class TestAgentCard:
    def test_supports_capability_true(self, sample_agent_card):
        assert sample_agent_card.supports("financial_analysis") is True

    def test_supports_capability_false(self, sample_agent_card):
        assert sample_agent_card.supports("web_scraping") is False

    def test_to_dict_has_required_fields(self, sample_agent_card):
        d = sample_agent_card.to_dict()
        assert d["agent_id"] == "test-finance-agent"
        assert "capabilities" in d
        assert "endpoint" in d
        assert "rate_limits" in d
        assert "auth" in d


# ── AgentRegistry Tests ───────────────────────────────────────────────────────

class TestAgentRegistry:
    def test_register_and_get(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        found = registry.get("test-finance-agent")
        assert found is not None
        assert found.agent_id == "test-finance-agent"

    def test_get_missing_returns_none(self):
        registry = AgentRegistry()
        assert registry.get("nonexistent") is None

    def test_deregister(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        registry.deregister("test-finance-agent")
        assert registry.get("test-finance-agent") is None

    def test_find_by_capability_found(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        results = registry.find_by_capability("financial_analysis")
        assert len(results) == 1
        assert results[0].agent_id == "test-finance-agent"

    def test_find_by_capability_not_found(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        results = registry.find_by_capability("unknown_capability")
        assert results == []

    def test_find_by_capability_excludes_ids(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        results = registry.find_by_capability(
            "financial_analysis",
            exclude_ids=["test-finance-agent"],
        )
        assert results == []

    def test_find_sorts_by_version_descending(self):
        registry = AgentRegistry()
        card_v1 = AgentCard(
            agent_id="finance-v1", name="F1", version="1.0.0",
            description="", capabilities=["financial_analysis"],
            endpoint="http://v1", input_schema={}, output_schema={},
        )
        card_v2 = AgentCard(
            agent_id="finance-v2", name="F2", version="2.0.0",
            description="", capabilities=["financial_analysis"],
            endpoint="http://v2", input_schema={}, output_schema={},
        )
        registry.register(card_v1)
        registry.register(card_v2)
        results = registry.find_by_capability("financial_analysis")
        assert results[0].version == "2.0.0"  # newer first

    def test_all_cards(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        cards = registry.all_cards()
        assert len(cards) == 1


# ── A2ATask Tests ─────────────────────────────────────────────────────────────

class TestA2ATask:
    def test_initial_status_is_submitted(self):
        task = A2ATask(
            task_id="t1", sender_id="sender", receiver_id="receiver",
            capability="test", input={"query": "hello"},
        )
        assert task.status == TaskStatus.SUBMITTED

    def test_initial_history_has_one_entry(self):
        task = A2ATask(
            task_id="t1", sender_id="s", receiver_id="r",
            capability="test", input={},
        )
        assert len(task.history) == 1
        assert task.history[0].status == TaskStatus.SUBMITTED

    def test_transition_updates_status(self):
        task = A2ATask(task_id="t1", sender_id="s", receiver_id="r",
                       capability="test", input={})
        task.transition(TaskStatus.WORKING, "Processing")
        assert task.status == TaskStatus.WORKING
        assert len(task.history) == 2

    def test_complete_sets_output(self):
        task = A2ATask(task_id="t1", sender_id="s", receiver_id="r",
                       capability="test", input={})
        task.complete({"result": "done"})
        assert task.status == TaskStatus.COMPLETED
        assert task.output == {"result": "done"}

    def test_fail_sets_error(self):
        task = A2ATask(task_id="t1", sender_id="s", receiver_id="r",
                       capability="test", input={})
        task.fail("something went wrong")
        assert task.status == TaskStatus.FAILED
        assert task.error == "something went wrong"

    def test_to_dict_structure(self):
        task = A2ATask(task_id="t1", sender_id="s", receiver_id="r",
                       capability="test", input={"q": "hello"})
        d = task.to_dict()
        assert d["task_id"] == "t1"
        assert "history" in d
        assert "metadata" in d
        assert d["metadata"]["delegation_depth"] == 0

    def test_transition_updates_updated_at(self):
        task = A2ATask(task_id="t1", sender_id="s", receiver_id="r",
                       capability="test", input={})
        before = task.updated_at
        time.sleep(0.01)
        task.transition(TaskStatus.WORKING)
        assert task.updated_at >= before


# ── A2AClient Tests ───────────────────────────────────────────────────────────

class TestA2AClient:
    @pytest.mark.asyncio
    async def test_delegate_success(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        client = A2AClient(caller_id="planner", registry=registry)

        task = await client.delegate(
            capability="financial_analysis",
            input_data={"query": "Analyze ACME"},
            correlation_id="corr-1",
        )
        assert task.status == TaskStatus.COMPLETED
        assert task.output is not None
        assert task.sender_id == "planner"
        assert task.receiver_id == "test-finance-agent"

    @pytest.mark.asyncio
    async def test_delegate_no_agent_raises(self):
        registry = AgentRegistry()
        client = A2AClient(caller_id="planner", registry=registry)

        with pytest.raises(LookupError, match="No agent found"):
            await client.delegate(
                capability="nonexistent_capability",
                input_data={"query": "test"},
            )

    @pytest.mark.asyncio
    async def test_delegate_max_depth_exceeded(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        client = A2AClient(caller_id="planner", registry=registry, max_delegation_depth=1)

        with pytest.raises(RuntimeError, match="Max delegation depth"):
            await client.delegate(
                capability="financial_analysis",
                input_data={},
                current_depth=1,  # already at max
            )

    @pytest.mark.asyncio
    async def test_delegate_stores_active_task(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        client = A2AClient(caller_id="planner", registry=registry)

        task = await client.delegate(capability="financial_analysis", input_data={})
        retrieved = client.get_task(task.task_id)
        assert retrieved is not None
        assert retrieved.task_id == task.task_id

    @pytest.mark.asyncio
    async def test_cancel_working_task(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        client = A2AClient(caller_id="planner", registry=registry)

        task = await client.delegate(capability="financial_analysis", input_data={})
        task.transition(TaskStatus.WORKING)
        cancelled = client.cancel_task(task.task_id)
        assert cancelled is True
        assert task.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_completed_task_returns_false(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        client = A2AClient(caller_id="planner", registry=registry)

        task = await client.delegate(capability="financial_analysis", input_data={})
        assert task.status == TaskStatus.COMPLETED
        cancelled = client.cancel_task(task.task_id)
        assert cancelled is False  # can't cancel a completed task

    @pytest.mark.asyncio
    async def test_custom_handler_invoked(self):
        registry = AgentRegistry()
        called_with = []

        card = AgentCard(
            agent_id="custom-agent", name="Custom", version="1.0.0",
            description="", capabilities=["custom_cap"],
            endpoint="http://custom", input_schema={}, output_schema={},
        )

        async def custom_handler(task: A2ATask) -> dict:
            called_with.append(task.input)
            return {"custom_result": "ok"}

        card._a2a_handler = custom_handler
        registry.register(card)

        client = A2AClient(caller_id="caller", registry=registry)
        task = await client.delegate(
            capability="custom_cap",
            input_data={"key": "value"},
        )
        assert task.status == TaskStatus.COMPLETED
        assert task.output == {"custom_result": "ok"}
        assert called_with[0] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_stream_task_yields_chunks(self, sample_agent_card):
        registry = AgentRegistry()
        registry.register(sample_agent_card)
        client = A2AClient(caller_id="caller", registry=registry)

        task = A2ATask(
            task_id="stream-t1", sender_id="caller",
            receiver_id="test-finance-agent", capability="financial_analysis",
            input={}, stream=True,
        )
        chunks = []
        async for chunk in client.stream_task(task, "test-finance-agent"):
            chunks.append(chunk)

        assert len(chunks) > 0
        assert all("chunk" in c for c in chunks)
        assert chunks[-1]["chunk"]["pct"] == 100
