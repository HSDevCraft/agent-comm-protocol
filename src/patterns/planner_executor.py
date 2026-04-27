"""
Pattern: Planner + Executor
===========================
A two-tier pattern where a PlannerAgent decomposes complex tasks
into sub-tasks and assigns them to ExecutorAgents.

When to use:
  - Complex research tasks (plan: search + analyze + summarize)
  - Multi-step data pipelines
  - Any task that requires sequential or parallel step decomposition

Architecture:
  User → PlannerAgent
           ├─[A2A]→ ExecutorAgent-1 (sub-task 1)
           ├─[A2A]→ ExecutorAgent-2 (sub-task 2)
           └─[ACP]→ AggregationStep → Final Result

Protocol flow:
  ACP: user-to-planner dispatch
  A2A: planner-to-executor delegation
  MCP: executor-to-tool calls
  SAMEP: shared memory between steps
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


from src._logging import get_logger

from src.agent import AgentConfig, BaseAgent, SpecialistAgent, TaskResult
from src.memory import MemoryManager
from src.observability import DecisionType, ObservabilityEngine
from src.protocols.a2a import AgentRegistry, TaskPriority
from src.protocols.acp import ACPOrchestrator, Workflow, WorkflowStep
from src.security import AgentRole, SecurityGateway

logger = get_logger(__name__)


@dataclass
class SubTask:
    """A single decomposed sub-task from a plan."""
    sub_task_id: str
    description: str
    required_capability: str
    depends_on: list[str] = field(default_factory=list)
    priority: TaskPriority = TaskPriority.MEDIUM
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] | None = None
    error: str | None = None
    assigned_to: str = ""
    status: str = "pending"
    started_at: float | None = None
    completed_at: float | None = None


@dataclass
class ExecutionPlan:
    """A structured plan created by the PlannerAgent."""
    plan_id: str
    original_query: str
    sub_tasks: list[SubTask]
    reasoning: str
    estimated_total_ms: float = 0.0
    created_at: float = field(default_factory=time.time)

    def get_ready_tasks(self) -> list[SubTask]:
        """Return sub-tasks whose dependencies are all completed."""
        completed_ids = {t.sub_task_id for t in self.sub_tasks if t.status == "completed"}
        return [
            t for t in self.sub_tasks
            if t.status == "pending" and all(dep in completed_ids for dep in t.depends_on)
        ]

    def is_complete(self) -> bool:
        return all(t.status in ("completed", "failed") for t in self.sub_tasks)

    def has_failures(self) -> bool:
        return any(t.status == "failed" for t in self.sub_tasks)

    def collect_results(self) -> dict[str, Any]:
        return {t.sub_task_id: t.output for t in self.sub_tasks if t.output}

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "original_query": self.original_query,
            "reasoning": self.reasoning,
            "sub_tasks": [
                {
                    "sub_task_id": t.sub_task_id,
                    "description": t.description,
                    "capability": t.required_capability,
                    "depends_on": t.depends_on,
                    "status": t.status,
                    "assigned_to": t.assigned_to,
                }
                for t in self.sub_tasks
            ],
        }


class PlannerAgent(BaseAgent):
    """
    Planner Agent — decomposes complex queries into sub-tasks and coordinates execution.

    The planner:
    1. Analyzes the incoming query to understand what needs to be done
    2. Decomposes it into ordered sub-tasks with dependency relationships
    3. Dispatches sub-tasks to ExecutorAgents via A2A (in parallel where possible)
    4. Aggregates results from all executors
    5. Produces a coherent final response

    The key insight: the planner NEVER executes domain logic — it only plans and coordinates.
    This separation allows swapping executors without touching the planner.
    """

    def __init__(
        self,
        config: AgentConfig,
        decomposition_rules: dict[str, list[str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self._decomposition_rules = decomposition_rules or {}
        self._plans: dict[str, ExecutionPlan] = {}

    async def _execute_locally(
        self,
        query: str,
        task_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        """
        Planner's local execution = plan decomposition + executor dispatch.
        """
        start = time.monotonic()
        tracer = self._tracer or self.obs.new_tracer()

        plan = self._decompose(query, task_id)
        self._plans[plan.plan_id] = plan

        logger.info(
            "planner_plan_created",
            plan_id=plan.plan_id,
            sub_tasks=len(plan.sub_tasks),
            query=query[:80],
        )

        span_id = tracer.start_span(self.agent_id, "plan")
        trace = self.obs.record_decision(
            agent_id=self.agent_id,
            decision=f"decompose_into_{len(plan.sub_tasks)}_subtasks",
            decision_type=DecisionType.DELEGATION,
            reasoning=plan.reasoning,
            confidence=0.88,
            protocol_used="ACP+A2A",
            inputs={"query": query[:100]},
            output={"plan_id": plan.plan_id, "sub_task_count": len(plan.sub_tasks)},
            span_id=span_id,
            trace_id=tracer.trace_id,
        )
        tracer.record(trace)

        await self._execute_plan(plan, tracer)

        duration_ms = (time.monotonic() - start) * 1000
        results = plan.collect_results()

        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=not plan.has_failures(),
            output={
                "plan_id": plan.plan_id,
                "query": query,
                "sub_task_results": results,
                "aggregated_summary": self._aggregate(results, query),
                "total_sub_tasks": len(plan.sub_tasks),
                "failed_sub_tasks": sum(1 for t in plan.sub_tasks if t.status == "failed"),
            },
            duration_ms=duration_ms,
            protocol_used="ACP+A2A",
        )

    def _decompose(self, query: str, task_id: str) -> ExecutionPlan:
        """
        Decompose a query into sub-tasks.
        In production: use an LLM to generate the plan.
        Here: rule-based decomposition for demonstration.
        """
        sub_tasks: list[SubTask] = []
        reasoning_parts: list[str] = []
        query_lower = query.lower()

        if any(kw in query_lower for kw in ["research", "analyze", "report", "study"]):
            sub_tasks.append(SubTask(
                sub_task_id="search",
                description=f"Search for information about: {query}",
                required_capability="web_search",
                input={"query": query},
                priority=TaskPriority.HIGH,
            ))
            sub_tasks.append(SubTask(
                sub_task_id="analyze",
                description="Analyze the search results",
                required_capability="data_analysis",
                depends_on=["search"],
                input={"query": query},
            ))
            sub_tasks.append(SubTask(
                sub_task_id="summarize",
                description="Summarize findings into a coherent report",
                required_capability="general",
                depends_on=["analyze"],
                input={"query": query},
            ))
            reasoning_parts.append("Research task decomposed into: search → analyze → summarize")

        elif any(kw in query_lower for kw in ["finance", "revenue", "cost", "profit", "budget"]):
            sub_tasks.append(SubTask(
                sub_task_id="fetch_data",
                description=f"Fetch financial data for: {query}",
                required_capability="financial_analysis",
                input={"query": query},
                priority=TaskPriority.HIGH,
            ))
            sub_tasks.append(SubTask(
                sub_task_id="risk_check",
                description="Assess risk profile",
                required_capability="risk_assessment",
                input={"query": query},
                priority=TaskPriority.MEDIUM,
            ))
            sub_tasks.append(SubTask(
                sub_task_id="report",
                description="Generate financial report",
                required_capability="general",
                depends_on=["fetch_data", "risk_check"],
                input={"query": query},
            ))
            reasoning_parts.append("Finance task: parallel data fetch + risk assessment, then report")

        else:
            sub_tasks.append(SubTask(
                sub_task_id="execute",
                description=f"Execute task: {query}",
                required_capability="general",
                input={"query": query},
            ))
            reasoning_parts.append("Simple task: single executor step")

        return ExecutionPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            original_query=query,
            sub_tasks=sub_tasks,
            reasoning=" | ".join(reasoning_parts),
        )

    async def _execute_plan(self, plan: ExecutionPlan, tracer: Any) -> None:
        """Execute sub-tasks respecting dependency ordering."""
        context: dict[str, Any] = {}

        while not plan.is_complete():
            ready = plan.get_ready_tasks()
            if not ready:
                break

            coro_list = [self._execute_sub_task(t, plan, context) for t in ready]
            results = await asyncio.gather(*coro_list, return_exceptions=True)

            for task_obj, result in zip(ready, results):
                if isinstance(result, Exception):
                    task_obj.status = "failed"
                    task_obj.error = str(result)
                    logger.error("planner_sub_task_failed", sub_task=task_obj.sub_task_id, error=str(result))
                else:
                    task_obj.status = "completed"
                    task_obj.output = result
                    task_obj.completed_at = time.time()
                    context[task_obj.sub_task_id] = result

    async def _execute_sub_task(
        self,
        sub_task: SubTask,
        plan: ExecutionPlan,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        sub_task.status = "running"
        sub_task.started_at = time.time()
        enriched_input = {**sub_task.input, "context": context}

        try:
            a2a_task = await self._a2a_client.delegate(
                capability=sub_task.required_capability,
                input_data=enriched_input,
                correlation_id=plan.plan_id,
                priority=sub_task.priority,
            )
            sub_task.assigned_to = a2a_task.receiver_id
            return a2a_task.output or {"sub_task_id": sub_task.sub_task_id, "status": "no_output"}
        except LookupError:
            result = await self._execute_locally_simple(sub_task.description)
            return result

    async def _execute_locally_simple(self, description: str) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return {"result": f"[local] Completed: {description}", "confidence": 0.75}

    def _aggregate(self, results: dict[str, Any], original_query: str) -> str:
        parts = [f"Results for: {original_query}"]
        for step_id, result in results.items():
            summary = str(result)[:100] if result else "no result"
            parts.append(f"  [{step_id}]: {summary}")
        return "\n".join(parts)

    def get_plan(self, plan_id: str) -> ExecutionPlan | None:
        return self._plans.get(plan_id)


class ExecutorAgent(SpecialistAgent):
    """
    Executor Agent — receives sub-tasks from the PlannerAgent and executes them.
    Specialized to a single capability domain.
    Communicates results back via A2A task completion.
    """

    def __init__(self, config: AgentConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._tasks_executed = 0

    async def _execute_locally(
        self,
        query: str,
        task_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        self._tasks_executed += 1
        await asyncio.sleep(0.015)
        cap_context = context.get("context", {})
        prior_results = {k: v for k, v in cap_context.items() if k != "query"}

        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=True,
            output={
                "executor": self.agent_id,
                "capability": self.config.capabilities[0] if self.config.capabilities else "general",
                "result": f"[{self.config.name}] Executed: {query[:80]}",
                "uses_prior_results": len(prior_results) > 0,
                "tasks_executed_total": self._tasks_executed,
            },
            protocol_used="local",
        )


@dataclass
class PlannerExecutorSystem:
    """
    Convenience container that wires a PlannerAgent to a set of ExecutorAgents.
    Factory method `build()` creates a complete system ready for use.
    """
    planner: PlannerAgent
    executors: list[ExecutorAgent]
    registry: AgentRegistry

    @classmethod
    def build(
        cls,
        executor_configs: list[AgentConfig],
        registry: AgentRegistry | None = None,
        memory: MemoryManager | None = None,
        security: SecurityGateway | None = None,
        observability: ObservabilityEngine | None = None,
    ) -> "PlannerExecutorSystem":
        shared_registry = registry or AgentRegistry()
        shared_memory = memory or MemoryManager()
        shared_security = security or SecurityGateway()
        shared_obs = observability or ObservabilityEngine()

        executors = [
            ExecutorAgent(
                config=cfg,
                a2a_registry=shared_registry,
                memory_manager=shared_memory,
                security_gateway=shared_security,
                observability_engine=shared_obs,
            )
            for cfg in executor_configs
        ]

        planner_config = AgentConfig(
            agent_id="planner-agent",
            name="PlannerAgent",
            version="1.0.0",
            role=AgentRole.PLANNER,
            capabilities=["planning", "coordination"],
            description="Decomposes complex tasks and coordinates specialist executors",
        )
        planner = PlannerAgent(
            config=planner_config,
            a2a_registry=shared_registry,
            memory_manager=shared_memory,
            security_gateway=shared_security,
            observability_engine=shared_obs,
        )

        return cls(planner=planner, executors=executors, registry=shared_registry)

    async def run(self, query: str, **kwargs: Any) -> TaskResult:
        return await self.planner.run(query, **kwargs)
