"""
ACP — Agent Communication Protocol
Scope: Orchestrator ↔ Agents (workflow coordination and messaging)

Handles:
  - Orchestration-level messaging (task dispatch, result collection)
  - Async fire-and-forget AND request-reply patterns
  - Workflow state management across multiple agents
  - Message correlation and TTL enforcement
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine


from src._logging import get_logger

logger = get_logger(__name__)


class ACPMessageType(str, Enum):
    TASK_DISPATCH = "TASK_DISPATCH"
    TASK_RESULT = "TASK_RESULT"
    TASK_ACK = "TASK_ACK"
    TASK_CANCEL = "TASK_CANCEL"
    STATUS_UPDATE = "STATUS_UPDATE"
    HEALTH_CHECK = "HEALTH_CHECK"
    CAPABILITY_QUERY = "CAPABILITY_QUERY"
    ERROR_REPORT = "ERROR_REPORT"
    WORKFLOW_START = "WORKFLOW_START"
    WORKFLOW_COMPLETE = "WORKFLOW_COMPLETE"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIALLY_COMPLETED = "partially_completed"


@dataclass
class RetryPolicy:
    max_retries: int = 3
    backoff_strategy: str = "exponential"
    base_delay_ms: int = 500

    def compute_delay(self, attempt: int) -> float:
        if self.backoff_strategy == "exponential":
            return (self.base_delay_ms * (2 ** attempt)) / 1000
        return self.base_delay_ms / 1000


@dataclass
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str = ""


@dataclass
class MessageEnvelopeACP:
    """
    ACP Message Envelope — the universal wrapper for all orchestration messages.

    Separates routing metadata from business payload so any message broker
    or transport layer can route messages without understanding the payload.
    """
    message_id: str
    type: ACPMessageType
    from_agent: str
    to_agent: str
    payload: dict[str, Any]
    correlation_id: str = ""
    timestamp: float = field(default_factory=time.time)
    ttl_seconds: int = 300
    priority: int = 5
    reply_to: str = ""
    requires_ack: bool = False
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    trace_context: TraceContext | None = None
    delivered: bool = False
    acked: bool = False

    @classmethod
    def dispatch(
        cls,
        from_agent: str,
        to_agent: str,
        payload: dict[str, Any],
        correlation_id: str = "",
        reply_to: str = "",
        priority: int = 5,
        ttl_seconds: int = 300,
        requires_ack: bool = True,
        trace_context: TraceContext | None = None,
    ) -> "MessageEnvelopeACP":
        return cls(
            message_id=f"msg-{uuid.uuid4().hex[:8]}",
            type=ACPMessageType.TASK_DISPATCH,
            from_agent=from_agent,
            to_agent=to_agent,
            payload=payload,
            correlation_id=correlation_id or f"corr-{uuid.uuid4().hex[:8]}",
            reply_to=reply_to,
            priority=priority,
            ttl_seconds=ttl_seconds,
            requires_ack=requires_ack,
            trace_context=trace_context,
        )

    def is_expired(self) -> bool:
        return time.time() > (self.timestamp + self.ttl_seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "type": self.type.value,
            "from": self.from_agent,
            "to": self.to_agent,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "ttl_seconds": self.ttl_seconds,
            "priority": self.priority,
            "reply_to": self.reply_to,
            "requires_ack": self.requires_ack,
            "retry_policy": {
                "max_retries": self.retry_policy.max_retries,
                "backoff_strategy": self.retry_policy.backoff_strategy,
                "base_delay_ms": self.retry_policy.base_delay_ms,
            },
            "trace_context": {
                "trace_id": self.trace_context.trace_id,
                "span_id": self.trace_context.span_id,
                "parent_span_id": self.trace_context.parent_span_id,
            } if self.trace_context else None,
            "payload": self.payload,
        }


@dataclass
class WorkflowStep:
    step_id: str
    name: str
    capability: str
    assigned_agent: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None


@dataclass
class Workflow:
    """Tracks state of a multi-step orchestrated workflow."""
    workflow_id: str
    name: str
    steps: list[WorkflowStep]
    status: WorkflowStatus = WorkflowStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    aggregated_result: dict[str, Any] | None = None

    def get_ready_steps(self) -> list[WorkflowStep]:
        """Return steps whose dependencies are all completed."""
        completed_ids = {s.step_id for s in self.steps if s.status == "completed"}
        return [
            s for s in self.steps
            if s.status == "pending" and all(dep in completed_ids for dep in s.depends_on)
        ]

    def is_complete(self) -> bool:
        return all(s.status in ("completed", "failed") for s in self.steps)

    def has_failures(self) -> bool:
        return any(s.status == "failed" for s in self.steps)


MessageHandler = Callable[[MessageEnvelopeACP], Coroutine[Any, Any, dict[str, Any] | None]]


class Inbox:
    """Per-agent async inbox — decouples sender from receiver timing."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self._queue: asyncio.Queue[MessageEnvelopeACP] = asyncio.Queue()
        self._dead_letter: list[MessageEnvelopeACP] = []

    async def put(self, envelope: MessageEnvelopeACP) -> None:
        if envelope.is_expired():
            logger.warning("acp_message_expired_on_arrival", msg_id=envelope.message_id)
            self._dead_letter.append(envelope)
            return
        await self._queue.put(envelope)

    async def get(self, timeout: float = 5.0) -> MessageEnvelopeACP | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def size(self) -> int:
        return self._queue.qsize()

    def dead_letter_count(self) -> int:
        return len(self._dead_letter)


class ACPOrchestrator:
    """
    ACP Orchestrator — central coordinator for multi-agent workflows.

    Responsibilities:
    - Maintain per-agent inboxes
    - Dispatch messages with retry logic
    - Execute multi-step workflows with dependency resolution
    - Aggregate results from parallel agent executions
    - Track correlation IDs for end-to-end workflow tracing
    """

    def __init__(self, orchestrator_id: str = "orchestrator") -> None:
        self.orchestrator_id = orchestrator_id
        self._inboxes: dict[str, Inbox] = {}
        self._handlers: dict[str, MessageHandler] = {}
        self._workflows: dict[str, Workflow] = {}
        self._message_log: list[dict[str, Any]] = []

    def register_agent_inbox(self, agent_id: str) -> Inbox:
        inbox = Inbox(agent_id)
        self._inboxes[agent_id] = inbox
        return inbox

    def register_handler(self, agent_id: str, handler: MessageHandler) -> None:
        self._handlers[agent_id] = handler

    async def send(
        self,
        to_agent: str,
        payload: dict[str, Any],
        msg_type: ACPMessageType = ACPMessageType.TASK_DISPATCH,
        correlation_id: str = "",
        priority: int = 5,
        ttl_seconds: int = 300,
        requires_ack: bool = True,
        from_agent: str | None = None,
        trace_context: TraceContext | None = None,
    ) -> MessageEnvelopeACP:
        """
        Send a message to an agent's inbox.
        Returns the envelope (including message_id for correlation tracking).
        """
        envelope = MessageEnvelopeACP(
            message_id=f"msg-{uuid.uuid4().hex[:8]}",
            type=msg_type,
            from_agent=from_agent or self.orchestrator_id,
            to_agent=to_agent,
            payload=payload,
            correlation_id=correlation_id or f"corr-{uuid.uuid4().hex[:8]}",
            reply_to=f"{from_agent or self.orchestrator_id}/inbox",
            priority=priority,
            ttl_seconds=ttl_seconds,
            requires_ack=requires_ack,
            trace_context=trace_context,
        )

        self._message_log.append({"event": "sent", "envelope": envelope.to_dict()})
        logger.info(
            "acp_message_sent",
            msg_id=envelope.message_id,
            type=msg_type.value,
            to=to_agent,
            correlation_id=envelope.correlation_id,
        )

        if to_agent in self._inboxes:
            await self._inboxes[to_agent].put(envelope)

        return envelope

    async def send_and_wait(
        self,
        to_agent: str,
        payload: dict[str, Any],
        correlation_id: str = "",
        timeout: float = 30.0,
        trace_context: TraceContext | None = None,
    ) -> dict[str, Any] | None:
        """
        Synchronous request-reply pattern.
        Sends a message and blocks until the agent's handler responds.
        """
        corr_id = correlation_id or f"corr-{uuid.uuid4().hex[:8]}"
        envelope = await self.send(
            to_agent=to_agent,
            payload=payload,
            correlation_id=corr_id,
            trace_context=trace_context,
        )

        handler = self._handlers.get(to_agent)
        if handler:
            try:
                result = await asyncio.wait_for(handler(envelope), timeout=timeout)
                self._message_log.append({
                    "event": "replied",
                    "correlation_id": corr_id,
                    "result": result,
                })
                return result
            except asyncio.TimeoutError:
                logger.warning("acp_send_and_wait_timeout", to=to_agent, timeout=timeout)
                return None

        return {"status": "delivered", "message_id": envelope.message_id}

    async def execute_workflow(
        self,
        workflow: Workflow,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute a multi-step workflow with dependency resolution.

        Steps with no dependencies run in parallel.
        Steps with dependencies run after their prerequisites complete.
        Results from earlier steps are injected into later steps as context.
        """
        self._workflows[workflow.workflow_id] = workflow
        workflow.status = WorkflowStatus.RUNNING
        context: dict[str, Any] = {"input": input_data}

        logger.info(
            "acp_workflow_start",
            workflow_id=workflow.workflow_id,
            steps=len(workflow.steps),
        )

        while not workflow.is_complete():
            ready = workflow.get_ready_steps()
            if not ready:
                break

            tasks = []
            for step in ready:
                step.status = "running"
                step.started_at = time.time()
                tasks.append(self._execute_step(step, context, workflow.workflow_id))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for step, result in zip(ready, results):
                if isinstance(result, Exception):
                    step.status = "failed"
                    step.error = str(result)
                    logger.error("acp_step_failed", step=step.step_id, error=str(result))
                else:
                    step.status = "completed"
                    step.result = result
                    step.completed_at = time.time()
                    context[step.step_id] = result
                    logger.info("acp_step_completed", step=step.step_id)

        workflow.completed_at = time.time()
        workflow.status = (
            WorkflowStatus.PARTIALLY_COMPLETED if workflow.has_failures()
            else WorkflowStatus.COMPLETED
        )

        aggregated = {
            s.step_id: s.result for s in workflow.steps if s.result
        }
        workflow.aggregated_result = aggregated

        logger.info(
            "acp_workflow_complete",
            workflow_id=workflow.workflow_id,
            status=workflow.status.value,
        )
        return aggregated

    async def _execute_step(
        self,
        step: WorkflowStep,
        context: dict[str, Any],
        workflow_id: str,
    ) -> dict[str, Any]:
        payload = {
            "workflow_id": workflow_id,
            "step_id": step.step_id,
            "capability": step.capability,
            "context": {k: v for k, v in context.items()},
        }

        result = await self.send_and_wait(
            to_agent=step.assigned_agent,
            payload=payload,
            correlation_id=f"{workflow_id}-{step.step_id}",
            timeout=60.0,
        )
        return result or {"step": step.step_id, "status": "no_response"}

    def get_message_log(self, correlation_id: str | None = None) -> list[dict[str, Any]]:
        if correlation_id is None:
            return self._message_log
        return [
            entry for entry in self._message_log
            if entry.get("envelope", {}).get("correlation_id") == correlation_id
        ]

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        return self._workflows.get(workflow_id)
