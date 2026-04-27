"""
Pattern: Agent Swarm
====================
N agents tackle a problem in parallel, each from a different angle.
Results are merged using a configurable aggregation strategy.

When to use:
  - Data analysis at scale (each agent processes a partition)
  - Ensemble LLM reasoning (multiple agents vote on best answer)
  - Redundant execution for high-reliability systems
  - Broad web research (agents search different sub-queries)

Architecture:
  SwarmCoordinator
    ├─[A2A]→ SwarmAgent-1 (partition 1)
    ├─[A2A]→ SwarmAgent-2 (partition 2)
    ├─[A2A]→ SwarmAgent-3 (partition 3)
    └─ aggregator: merge(result-1, result-2, result-3) → final

Aggregation strategies:
  - first:      return the first successful result
  - majority:   return the most common result (voting)
  - merge:      combine all results into one response
  - best_conf:  return the result with highest confidence score
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


from src._logging import get_logger

from src.agent import AgentConfig, BaseAgent, SpecialistAgent, TaskResult
from src.memory import MemoryManager
from src.observability import DecisionType, ObservabilityEngine
from src.protocols.a2a import AgentRegistry, TaskPriority
from src.security import AgentRole, SecurityGateway

logger = get_logger(__name__)


class AggregationStrategy(str, Enum):
    FIRST = "first"
    MAJORITY = "majority"
    MERGE = "merge"
    BEST_CONFIDENCE = "best_confidence"
    WEIGHTED_MERGE = "weighted_merge"


@dataclass
class SwarmResult:
    """Aggregated result from all swarm agents."""
    swarm_id: str
    query: str
    strategy: AggregationStrategy
    individual_results: list[TaskResult]
    aggregated_output: dict[str, Any]
    success: bool
    total_agents: int
    successful_agents: int
    failed_agents: int
    duration_ms: float
    confidence: float = 0.0


class SwarmAgent(SpecialistAgent):
    """
    An individual agent in a swarm.
    Identical interface to SpecialistAgent — the "swarm" is a coordination pattern,
    not a different agent type. Any BaseAgent can participate in a swarm.
    """

    def __init__(self, config: AgentConfig, swarm_id: str = "", **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.swarm_id = swarm_id
        self._swarm_executions = 0

    async def _execute_locally(
        self,
        query: str,
        task_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        self._swarm_executions += 1
        partition = context.get("partition", {})
        partition_id = partition.get("id", "unknown")
        await asyncio.sleep(0.02 + (hash(self.agent_id) % 3) * 0.01)

        confidence = 0.7 + (hash(self.agent_id + query) % 30) / 100

        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=True,
            output={
                "agent": self.agent_id,
                "swarm_id": self.swarm_id,
                "partition_id": partition_id,
                "result": f"[{self.config.name}] Analysis of '{query[:60]}' (partition {partition_id})",
                "confidence": confidence,
                "capability": self.config.capabilities[0] if self.config.capabilities else "general",
                "swarm_executions": self._swarm_executions,
            },
            protocol_used="local",
        )


class SwarmCoordinator(BaseAgent):
    """
    Swarm Coordinator — dispatches a task to N agents in parallel
    and aggregates their results.

    The coordinator:
    1. Partitions the problem (or broadcasts same query to all agents)
    2. Dispatches to all swarm agents simultaneously via A2A
    3. Waits for all results (with individual timeouts)
    4. Applies the aggregation strategy
    5. Returns merged SwarmResult
    """

    def __init__(
        self,
        config: AgentConfig,
        swarm_agents: list[SwarmAgent],
        strategy: AggregationStrategy = AggregationStrategy.MERGE,
        agent_timeout_seconds: float = 30.0,
        min_success_ratio: float = 0.5,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self._swarm_agents = swarm_agents
        self.strategy = strategy
        self.agent_timeout_seconds = agent_timeout_seconds
        self.min_success_ratio = min_success_ratio
        self._swarm_runs: list[SwarmResult] = []

        for agent in swarm_agents:
            self.register_known_agent(agent.agent_card())

        logger.info(
            "swarm_coordinator_init",
            coordinator=self.agent_id,
            swarm_size=len(swarm_agents),
            strategy=strategy.value,
        )

    async def run_swarm(
        self,
        query: str,
        session_id: str = "",
        partitioned: bool = False,
    ) -> SwarmResult:
        """
        Main swarm execution.
        If `partitioned=True`, splits the query into N partitions (one per agent).
        Otherwise, broadcasts the same query to all agents.
        """
        swarm_id = f"swarm-{uuid.uuid4().hex[:8]}"
        start = time.monotonic()
        tracer = self.obs.new_tracer()

        partitions = (
            self._partition_query(query, len(self._swarm_agents))
            if partitioned
            else [{"id": i, "query": query} for i in range(len(self._swarm_agents))]
        )

        logger.info(
            "swarm_start",
            swarm_id=swarm_id,
            agents=len(self._swarm_agents),
            partitioned=partitioned,
        )

        span_id = tracer.start_span(self.agent_id, "swarm_dispatch")
        dispatch_trace = self.obs.record_decision(
            agent_id=self.agent_id,
            decision=f"dispatch_swarm_{len(self._swarm_agents)}_agents",
            decision_type=DecisionType.DELEGATION,
            reasoning=(
                f"Broadcasting query to {len(self._swarm_agents)} swarm agents "
                f"using {self.strategy.value} aggregation. "
                f"Partitioned: {partitioned}."
            ),
            confidence=0.92,
            protocol_used="A2A",
            inputs={"query": query[:100], "swarm_size": len(self._swarm_agents)},
            output={"swarm_id": swarm_id},
            span_id=span_id,
            trace_id=tracer.trace_id,
        )
        tracer.record(dispatch_trace)

        tasks = [
            self._dispatch_to_agent(agent, query, partitions[i], swarm_id)
            for i, agent in enumerate(self._swarm_agents)
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        individual_results: list[TaskResult] = []
        for i, result in enumerate(raw_results):
            if isinstance(result, Exception):
                individual_results.append(TaskResult(
                    task_id=f"swarm-task-{i}",
                    agent_id=self._swarm_agents[i].agent_id,
                    success=False,
                    output={},
                    error=str(result),
                    protocol_used="A2A",
                ))
            else:
                individual_results.append(result)

        aggregated = self._aggregate(individual_results, query)
        duration_ms = (time.monotonic() - start) * 1000
        successful = sum(1 for r in individual_results if r.success)
        success = (successful / len(individual_results)) >= self.min_success_ratio

        swarm_result = SwarmResult(
            swarm_id=swarm_id,
            query=query,
            strategy=self.strategy,
            individual_results=individual_results,
            aggregated_output=aggregated,
            success=success,
            total_agents=len(individual_results),
            successful_agents=successful,
            failed_agents=len(individual_results) - successful,
            duration_ms=duration_ms,
            confidence=aggregated.get("aggregated_confidence", 0.0),
        )
        self._swarm_runs.append(swarm_result)

        agg_trace = self.obs.record_decision(
            agent_id=self.agent_id,
            decision=f"aggregate_{self.strategy.value}",
            decision_type=DecisionType.LOCAL_EXECUTION,
            reasoning=(
                f"{successful}/{len(individual_results)} agents succeeded. "
                f"Applied {self.strategy.value} aggregation."
            ),
            confidence=swarm_result.confidence,
            protocol_used="local",
            inputs={"successful_agents": successful, "total": len(individual_results)},
            output={"aggregated_keys": list(aggregated.keys())},
            trace_id=tracer.trace_id,
            duration_ms=duration_ms,
        )
        tracer.record(agg_trace)
        self.obs.print_trace_tree(tracer)

        return swarm_result

    async def _dispatch_to_agent(
        self,
        agent: SwarmAgent,
        query: str,
        partition: dict[str, Any],
        swarm_id: str,
    ) -> TaskResult:
        try:
            result = await asyncio.wait_for(
                agent.run(
                    query=query,
                    context={"partition": partition, "swarm_id": swarm_id},
                ),
                timeout=self.agent_timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning("swarm_agent_timeout", agent=agent.agent_id, swarm_id=swarm_id)
            return TaskResult(
                task_id=f"swarm-timeout-{agent.agent_id}",
                agent_id=agent.agent_id,
                success=False,
                output={},
                error=f"Agent {agent.agent_id} timed out after {self.agent_timeout_seconds}s",
                protocol_used="A2A",
            )

    def _aggregate(self, results: list[TaskResult], query: str) -> dict[str, Any]:
        successful = [r for r in results if r.success]

        if not successful:
            return {"error": "All swarm agents failed", "query": query}

        if self.strategy == AggregationStrategy.FIRST:
            return {"strategy": "first", "result": successful[0].output, "source": successful[0].agent_id}

        if self.strategy == AggregationStrategy.BEST_CONFIDENCE:
            best = max(successful, key=lambda r: r.output.get("confidence", 0.0))
            return {
                "strategy": "best_confidence",
                "result": best.output,
                "source": best.agent_id,
                "confidence": best.output.get("confidence", 0.0),
            }

        if self.strategy == AggregationStrategy.MAJORITY:
            result_texts = [str(r.output.get("result", ""))[:50] for r in successful]
            from collections import Counter
            most_common_text, _ = Counter(result_texts).most_common(1)[0]
            majority_result = next(r for r in successful if str(r.output.get("result", ""))[:50] == most_common_text)
            return {
                "strategy": "majority",
                "result": majority_result.output,
                "votes": len(result_texts),
                "source": majority_result.agent_id,
            }

        merged_results = [r.output for r in successful]
        confidences = [r.output.get("confidence", 0.8) for r in successful]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "strategy": "merge",
            "query": query,
            "individual_results": merged_results,
            "agent_count": len(successful),
            "aggregated_confidence": round(avg_confidence, 4),
            "summary": f"Merged results from {len(successful)} agents for: {query[:60]}",
        }

    def _partition_query(self, query: str, n: int) -> list[dict[str, Any]]:
        words = query.split()
        chunk_size = max(1, len(words) // n)
        partitions = []
        for i in range(n):
            start = i * chunk_size
            end = start + chunk_size if i < n - 1 else len(words)
            partitions.append({
                "id": i,
                "query": " ".join(words[start:end]),
                "partition_index": i,
                "total_partitions": n,
            })
        return partitions

    def swarm_stats(self) -> dict[str, Any]:
        if not self._swarm_runs:
            return {"total_swarm_runs": 0}
        success_rate = sum(1 for r in self._swarm_runs if r.success) / len(self._swarm_runs)
        avg_conf = sum(r.confidence for r in self._swarm_runs) / len(self._swarm_runs)
        return {
            "total_swarm_runs": len(self._swarm_runs),
            "success_rate": round(success_rate, 3),
            "avg_confidence": round(avg_conf, 3),
            "strategy": self.strategy.value,
            "swarm_size": len(self._swarm_agents),
        }


def build_research_swarm(
    n_agents: int = 3,
    strategy: AggregationStrategy = AggregationStrategy.MERGE,
    registry: AgentRegistry | None = None,
    **kwargs: Any,
) -> SwarmCoordinator:
    """
    Factory: Build a research swarm with N identical agents.
    Each agent independently answers the query; results are merged.
    """
    shared_registry = registry or AgentRegistry()
    shared_memory = MemoryManager()
    shared_security = SecurityGateway()
    shared_obs = ObservabilityEngine()

    agents = [
        SwarmAgent(
            config=AgentConfig(
                agent_id=f"research-agent-{i}",
                name=f"ResearchAgent-{i}",
                version="1.0.0",
                role=AgentRole.SPECIALIST,
                capabilities=["general", "web_search", "data_analysis"],
                description=f"Swarm research agent #{i}",
            ),
            swarm_id="research-swarm",
            a2a_registry=shared_registry,
            memory_manager=shared_memory,
            security_gateway=shared_security,
            observability_engine=shared_obs,
        )
        for i in range(n_agents)
    ]

    coordinator_config = AgentConfig(
        agent_id="swarm-coordinator",
        name="SwarmCoordinator",
        version="1.0.0",
        role=AgentRole.ORCHESTRATOR,
        capabilities=["coordination", "aggregation"],
        description="Coordinates research swarm and aggregates results",
    )

    return SwarmCoordinator(
        config=coordinator_config,
        swarm_agents=agents,
        strategy=strategy,
        a2a_registry=shared_registry,
        memory_manager=shared_memory,
        security_gateway=shared_security,
        observability_engine=shared_obs,
    )
