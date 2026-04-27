"""
A2A — Agent-to-Agent Protocol
Scope: Agent ↔ Agent collaboration and task delegation

Defines how two agents:
  1. Discover each other via Agent Cards
  2. Negotiate tasks (submit/accept/reject)
  3. Stream intermediate results via SSE
  4. Exchange final results

Built on HTTP with optional Server-Sent Events for streaming.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable


from src._logging import get_logger

logger = get_logger(__name__)


class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    WORKING = "working"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class AgentCard:
    """
    Self-describing advertisement published by every agent.
    Enables dynamic discovery without hard-coded routing.
    """
    agent_id: str
    name: str
    version: str
    description: str
    capabilities: list[str]
    endpoint: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    trust_level: str = "internal"
    auth_type: str = "bearer"
    auth_scopes: list[str] = field(default_factory=list)
    rate_limit_rpm: int = 60
    max_concurrent_tasks: int = 10
    health_endpoint: str = ""
    registered_at: float = field(default_factory=time.time)
    ttl_seconds: int = 3600

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": self.capabilities,
            "endpoint": self.endpoint,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "trust_level": self.trust_level,
            "auth": {"type": self.auth_type, "scope": " ".join(self.auth_scopes)},
            "rate_limits": {
                "requests_per_minute": self.rate_limit_rpm,
                "max_concurrent_tasks": self.max_concurrent_tasks,
            },
            "health_endpoint": self.health_endpoint,
            "registered_at": self.registered_at,
            "ttl_seconds": self.ttl_seconds,
        }

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass
class TaskHistoryEntry:
    timestamp: float
    status: TaskStatus
    note: str


@dataclass
class A2ATask:
    """
    Complete task object for the A2A lifecycle.
    Tracks every state transition for full auditability.
    """
    task_id: str
    sender_id: str
    receiver_id: str
    capability: str
    input: dict[str, Any]
    correlation_id: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    deadline_iso: str = ""
    stream: bool = False
    status: TaskStatus = TaskStatus.SUBMITTED
    output: dict[str, Any] | None = None
    error: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    history: list[TaskHistoryEntry] = field(default_factory=list)
    delegation_depth: int = 0
    parent_task_id: str | None = None
    retry_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self._append_history(TaskStatus.SUBMITTED, "Task created")

    def transition(self, new_status: TaskStatus, note: str = "") -> None:
        self.status = new_status
        self.updated_at = time.time()
        self._append_history(new_status, note)
        logger.info(
            "a2a_task_transition",
            task_id=self.task_id,
            new_status=new_status.value,
            note=note,
        )

    def complete(self, output: dict[str, Any]) -> None:
        self.output = output
        self.transition(TaskStatus.COMPLETED, "Task completed successfully")

    def fail(self, reason: str) -> None:
        self.error = reason
        self.transition(TaskStatus.FAILED, f"Task failed: {reason}")

    def _append_history(self, status: TaskStatus, note: str) -> None:
        self.history.append(TaskHistoryEntry(
            timestamp=time.time(),
            status=status,
            note=note,
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "capability": self.capability,
            "status": self.status.value,
            "priority": self.priority.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline_iso": self.deadline_iso,
            "stream": self.stream,
            "input": self.input,
            "output": self.output,
            "error": self.error,
            "artifacts": self.artifacts,
            "history": [
                {"timestamp": h.timestamp, "status": h.status.value, "note": h.note}
                for h in self.history
            ],
            "metadata": {
                "delegation_depth": self.delegation_depth,
                "parent_task_id": self.parent_task_id,
                "retry_count": self.retry_count,
            },
        }


AgentHandlerFn = Callable[[A2ATask], "asyncio.Coroutine[Any, Any, dict[str, Any]]"]


class AgentRegistry:
    """
    Central registry of Agent Cards.
    Supports capability-based discovery and scoring.
    """

    def __init__(self) -> None:
        self._cards: dict[str, AgentCard] = {}

    def register(self, card: AgentCard) -> None:
        self._cards[card.agent_id] = card
        logger.info("agent_registered", agent_id=card.agent_id, capabilities=card.capabilities)

    def deregister(self, agent_id: str) -> None:
        self._cards.pop(agent_id, None)

    def find_by_capability(
        self,
        capability: str,
        exclude_ids: list[str] | None = None,
    ) -> list[AgentCard]:
        """Return all agents supporting a capability, sorted by version descending."""
        exclude = set(exclude_ids or [])
        candidates = [
            card for card in self._cards.values()
            if card.supports(capability) and card.agent_id not in exclude
        ]
        candidates.sort(key=lambda c: c.version, reverse=True)
        return candidates

    def get(self, agent_id: str) -> AgentCard | None:
        return self._cards.get(agent_id)

    def all_cards(self) -> list[AgentCard]:
        return list(self._cards.values())


class A2AClient:
    """
    A2A Client — used by one agent to delegate tasks to another.

    In production: HTTP POST to agent endpoint with JWT bearer token.
    Here: direct in-process call for simulation.
    """

    def __init__(
        self,
        caller_id: str,
        registry: AgentRegistry,
        max_delegation_depth: int = 3,
    ) -> None:
        self.caller_id = caller_id
        self.registry = registry
        self.max_delegation_depth = max_delegation_depth
        self._active_tasks: dict[str, A2ATask] = {}

    async def delegate(
        self,
        capability: str,
        input_data: dict[str, Any],
        correlation_id: str = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        stream: bool = False,
        current_depth: int = 0,
        exclude_agents: list[str] | None = None,
    ) -> A2ATask:
        """
        Discover the best agent for a capability and delegate the task.
        Enforces max delegation depth to prevent infinite loops.
        """
        if current_depth >= self.max_delegation_depth:
            raise RuntimeError(
                f"Max delegation depth ({self.max_delegation_depth}) exceeded "
                f"for capability '{capability}'"
            )

        candidates = self.registry.find_by_capability(capability, exclude_ids=exclude_agents)
        if not candidates:
            raise LookupError(f"No agent found with capability: {capability}")

        target = candidates[0]
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        task = A2ATask(
            task_id=task_id,
            sender_id=self.caller_id,
            receiver_id=target.agent_id,
            capability=capability,
            input=input_data,
            correlation_id=correlation_id or task_id,
            priority=priority,
            stream=stream,
            delegation_depth=current_depth,
        )
        self._active_tasks[task_id] = task

        logger.info(
            "a2a_delegating",
            from_agent=self.caller_id,
            to_agent=target.agent_id,
            capability=capability,
            task_id=task_id,
            depth=current_depth,
        )

        agent_handler = self.registry.get(target.agent_id)
        if hasattr(agent_handler, "_a2a_handler"):
            task.transition(TaskStatus.ACCEPTED, f"Accepted by {target.agent_id}")
            task.transition(TaskStatus.WORKING, f"{target.agent_id} processing")
            try:
                result = await agent_handler._a2a_handler(task)
                task.complete(result)
            except Exception as exc:
                task.fail(str(exc))
        else:
            task.complete({"message": f"[mock] {target.agent_id} completed {capability}", "input_echo": input_data})

        return task

    async def stream_task(
        self,
        task: A2ATask,
        agent_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Stream SSE-style updates from a long-running task.
        Yields partial result dicts as the agent produces them.
        """
        task.transition(TaskStatus.STREAMING, "Streaming updates started")
        chunks = [
            {"type": "progress", "pct": 25, "note": "Fetching data"},
            {"type": "progress", "pct": 50, "note": "Analyzing data"},
            {"type": "progress", "pct": 75, "note": "Generating report"},
            {"type": "result", "pct": 100, "note": "Complete"},
        ]
        for chunk in chunks:
            await asyncio.sleep(0.05)
            yield {"task_id": task.task_id, "agent_id": agent_id, "chunk": chunk}

    def get_task(self, task_id: str) -> A2ATask | None:
        return self._active_tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        task = self._active_tasks.get(task_id)
        if task and task.status in (TaskStatus.SUBMITTED, TaskStatus.WORKING):
            task.transition(TaskStatus.CANCELLED, "Cancelled by caller")
            return True
        return False
