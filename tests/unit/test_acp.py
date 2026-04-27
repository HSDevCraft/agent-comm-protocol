"""
Unit tests for ACP — Agent Communication Protocol (Orchestration Layer)
"""
from __future__ import annotations

import asyncio
import time
import pytest

from src.protocols.acp import (
    ACPMessageType,
    ACPOrchestrator,
    Inbox,
    MessageEnvelopeACP,
    RetryPolicy,
    TraceContext,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)


# ── RetryPolicy Tests ─────────────────────────────────────────────────────────

class TestRetryPolicy:
    def test_exponential_delay_increases(self):
        policy = RetryPolicy(base_delay_ms=500, backoff_strategy="exponential")
        d0 = policy.compute_delay(0)
        d1 = policy.compute_delay(1)
        d2 = policy.compute_delay(2)
        assert d1 > d0
        assert d2 > d1

    def test_linear_delay_constant(self):
        policy = RetryPolicy(base_delay_ms=200, backoff_strategy="linear")
        d0 = policy.compute_delay(0)
        d1 = policy.compute_delay(1)
        assert d0 == d1  # linear: same delay each attempt


# ── MessageEnvelopeACP Tests ──────────────────────────────────────────────────

class TestMessageEnvelopeACP:
    def test_dispatch_factory(self):
        env = MessageEnvelopeACP.dispatch(
            from_agent="orchestrator",
            to_agent="finance-agent",
            payload={"task": "analyze"},
            correlation_id="corr-1",
            ttl_seconds=300,
        )
        assert env.type == ACPMessageType.TASK_DISPATCH
        assert env.from_agent == "orchestrator"
        assert env.to_agent == "finance-agent"
        assert env.correlation_id == "corr-1"
        assert env.ttl_seconds == 300

    def test_not_expired_when_fresh(self):
        env = MessageEnvelopeACP.dispatch("a", "b", {}, ttl_seconds=300)
        assert env.is_expired() is False

    def test_expired_when_old(self):
        env = MessageEnvelopeACP.dispatch("a", "b", {}, ttl_seconds=1)
        env.timestamp = time.time() - 5
        assert env.is_expired() is True

    def test_to_dict_structure(self):
        env = MessageEnvelopeACP.dispatch(
            "orch", "agent", {"key": "val"},
            correlation_id="c1",
            trace_context=TraceContext("t1", "s1", "s0"),
        )
        d = env.to_dict()
        assert d["from"] == "orch"
        assert d["to"] == "agent"
        assert d["payload"] == {"key": "val"}
        assert d["trace_context"]["trace_id"] == "t1"

    def test_message_id_unique(self):
        e1 = MessageEnvelopeACP.dispatch("a", "b", {})
        e2 = MessageEnvelopeACP.dispatch("a", "b", {})
        assert e1.message_id != e2.message_id

    def test_correlation_id_auto_generated(self):
        env = MessageEnvelopeACP.dispatch("a", "b", {})
        assert env.correlation_id != ""


# ── Inbox Tests ───────────────────────────────────────────────────────────────

class TestInbox:
    @pytest.mark.asyncio
    async def test_put_and_get(self):
        inbox = Inbox("agent-1")
        env = MessageEnvelopeACP.dispatch("orch", "agent-1", {"data": "x"})
        await inbox.put(env)
        received = await inbox.get(timeout=1.0)
        assert received is not None
        assert received.payload == {"data": "x"}

    @pytest.mark.asyncio
    async def test_get_timeout_returns_none(self):
        inbox = Inbox("agent-1")
        received = await inbox.get(timeout=0.05)
        assert received is None

    @pytest.mark.asyncio
    async def test_expired_message_goes_to_dlq(self):
        inbox = Inbox("agent-1")
        env = MessageEnvelopeACP.dispatch("orch", "agent-1", {}, ttl_seconds=1)
        env.timestamp = time.time() - 5  # already expired
        await inbox.put(env)
        assert inbox.size() == 0  # not queued
        assert inbox.dead_letter_count() == 1

    @pytest.mark.asyncio
    async def test_size_reflects_queue(self):
        inbox = Inbox("agent-1")
        assert inbox.size() == 0
        env = MessageEnvelopeACP.dispatch("a", "agent-1", {})
        await inbox.put(env)
        assert inbox.size() == 1


# ── Workflow Tests ────────────────────────────────────────────────────────────

class TestWorkflow:
    def _make_workflow(self) -> Workflow:
        return Workflow(
            workflow_id="wf-test",
            name="TestWorkflow",
            steps=[
                WorkflowStep("step-1", "fetch", "data_fetching", "data-agent", depends_on=[]),
                WorkflowStep("step-2", "analyze", "data_analysis", "analysis-agent", depends_on=["step-1"]),
                WorkflowStep("step-3", "report", "report_gen", "report-agent", depends_on=["step-1", "step-2"]),
            ],
        )

    def test_initial_status_pending(self):
        wf = self._make_workflow()
        assert wf.status == WorkflowStatus.PENDING

    def test_get_ready_steps_initially_only_no_deps(self):
        wf = self._make_workflow()
        ready = wf.get_ready_steps()
        assert len(ready) == 1
        assert ready[0].step_id == "step-1"

    def test_get_ready_steps_after_first_complete(self):
        wf = self._make_workflow()
        wf.steps[0].status = "completed"  # step-1 done
        ready = wf.get_ready_steps()
        assert len(ready) == 1
        assert ready[0].step_id == "step-2"

    def test_get_ready_steps_after_first_two_complete(self):
        wf = self._make_workflow()
        wf.steps[0].status = "completed"
        wf.steps[1].status = "completed"
        ready = wf.get_ready_steps()
        assert len(ready) == 1
        assert ready[0].step_id == "step-3"

    def test_is_complete_false_initially(self):
        wf = self._make_workflow()
        assert wf.is_complete() is False

    def test_is_complete_true_when_all_done(self):
        wf = self._make_workflow()
        for step in wf.steps:
            step.status = "completed"
        assert wf.is_complete() is True

    def test_has_failures(self):
        wf = self._make_workflow()
        wf.steps[0].status = "failed"
        assert wf.has_failures() is True

    def test_no_failures_when_all_complete(self):
        wf = self._make_workflow()
        for step in wf.steps:
            step.status = "completed"
        assert wf.has_failures() is False


# ── ACPOrchestrator Tests ─────────────────────────────────────────────────────

class TestACPOrchestrator:
    @pytest.mark.asyncio
    async def test_send_creates_envelope(self, acp_orchestrator):
        env = await acp_orchestrator.send(
            to_agent="finance-agent",
            payload={"task": "analyze"},
            correlation_id="c1",
        )
        assert env.message_id.startswith("msg-")
        assert env.to_agent == "finance-agent"
        assert env.correlation_id == "c1"

    @pytest.mark.asyncio
    async def test_send_logs_message(self, acp_orchestrator):
        await acp_orchestrator.send("agent", {"x": 1}, correlation_id="c1")
        log = acp_orchestrator.get_message_log("c1")
        assert len(log) == 1

    @pytest.mark.asyncio
    async def test_send_and_wait_with_handler(self, acp_orchestrator):
        async def my_handler(envelope: MessageEnvelopeACP) -> dict:
            return {"processed": envelope.payload.get("value", 0) * 2}

        acp_orchestrator.register_handler("calculator", my_handler)
        result = await acp_orchestrator.send_and_wait(
            to_agent="calculator",
            payload={"value": 21},
            timeout=5.0,
        )
        assert result == {"processed": 42}

    @pytest.mark.asyncio
    async def test_send_and_wait_timeout_returns_none(self, acp_orchestrator):
        async def slow_handler(envelope: MessageEnvelopeACP):
            await asyncio.sleep(10)
            return {}

        acp_orchestrator.register_handler("slow-agent", slow_handler)
        result = await acp_orchestrator.send_and_wait(
            to_agent="slow-agent",
            payload={},
            timeout=0.05,  # very short timeout
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_execute_workflow_all_complete(self, acp_orchestrator):
        async def step_handler(envelope: MessageEnvelopeACP) -> dict:
            return {"step_done": envelope.payload.get("step_id")}

        acp_orchestrator.register_handler("worker-agent", step_handler)

        workflow = Workflow(
            workflow_id="wf-1",
            name="TestWF",
            steps=[
                WorkflowStep("s1", "step1", "cap1", "worker-agent", depends_on=[]),
                WorkflowStep("s2", "step2", "cap2", "worker-agent", depends_on=["s1"]),
            ],
        )
        results = await acp_orchestrator.execute_workflow(workflow, {"input": "data"})
        assert workflow.status == WorkflowStatus.COMPLETED
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_execute_workflow_parallel_steps(self, acp_orchestrator):
        """Steps without dependencies should execute in parallel."""
        execution_order = []

        async def tracking_handler(envelope: MessageEnvelopeACP) -> dict:
            step_id = envelope.payload.get("step_id", "?")
            execution_order.append(step_id)
            return {"done": step_id}

        acp_orchestrator.register_handler("parallel-agent", tracking_handler)

        workflow = Workflow(
            workflow_id="wf-parallel",
            name="ParallelWF",
            steps=[
                WorkflowStep("p1", "parallel1", "cap", "parallel-agent", depends_on=[]),
                WorkflowStep("p2", "parallel2", "cap", "parallel-agent", depends_on=[]),
                WorkflowStep("merge", "merge", "cap", "parallel-agent", depends_on=["p1", "p2"]),
            ],
        )
        results = await acp_orchestrator.execute_workflow(workflow, {})
        assert workflow.status == WorkflowStatus.COMPLETED
        # merge should run last
        assert execution_order[-1] == "merge"

    def test_get_workflow(self, acp_orchestrator):
        wf = Workflow("wf-test", "WF", steps=[])
        acp_orchestrator._workflows["wf-test"] = wf
        found = acp_orchestrator.get_workflow("wf-test")
        assert found is not None

    def test_get_workflow_missing(self, acp_orchestrator):
        assert acp_orchestrator.get_workflow("nonexistent") is None

    @pytest.mark.asyncio
    async def test_register_agent_inbox(self, acp_orchestrator):
        inbox = acp_orchestrator.register_agent_inbox("new-agent")
        assert isinstance(inbox, Inbox)
        assert inbox.agent_id == "new-agent"

    @pytest.mark.asyncio
    async def test_send_delivers_to_inbox(self, acp_orchestrator):
        inbox = acp_orchestrator.register_agent_inbox("inbox-agent")
        await acp_orchestrator.send(
            to_agent="inbox-agent",
            payload={"msg": "hello"},
            ttl_seconds=300,
        )
        received = await inbox.get(timeout=1.0)
        assert received is not None
        assert received.payload["msg"] == "hello"
