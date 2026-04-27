"""
Pattern: Router Agent
=====================
A single entry-point agent that classifies incoming requests and
routes them to the correct specialist agent — without performing
any domain logic itself.

When to use:
  - Customer service triage (billing → BillingAgent, tech → TechAgent)
  - API gateway for an agent network
  - Domain-agnostic front-door that shields users from agent topology

Architecture:
  User → RouterAgent → [FinanceAgent | LegalAgent | CodeAgent | DataAgent]

Protocol used: A2A (capability-based delegation)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


from src._logging import get_logger

from src.agent import AgentConfig, BaseAgent, TaskResult
from src.observability import DecisionType, Alternative
from src.protocols.a2a import AgentCard, AgentRegistry, TaskPriority
from src.security import AgentRole

logger = get_logger(__name__)


@dataclass
class RoutingRule:
    """
    A declarative rule that maps keywords or conditions to a target agent.
    Rules are evaluated in priority order (higher = checked first).
    """
    rule_id: str
    keywords: list[str]
    target_agent_id: str
    target_capability: str
    priority: int = 5
    description: str = ""
    match_mode: str = "any"

    def matches(self, text: str) -> bool:
        text_lower = text.lower()
        if self.match_mode == "all":
            return all(kw in text_lower for kw in self.keywords)
        return any(kw in text_lower for kw in self.keywords)


class RouterAgent(BaseAgent):
    """
    Router Agent Pattern.

    Maintains a registry of routing rules + connected specialist agents.
    For each incoming query:
    1. Classify the query against all rules (keyword matching, ML classifier, etc.)
    2. Select the best-matching rule
    3. Delegate to the target agent via A2A
    4. Return the specialist's result

    The router NEVER executes domain logic — it is purely a traffic controller.
    This allows the specialist topology to change without affecting clients.
    """

    def __init__(
        self,
        config: AgentConfig,
        routing_rules: list[RoutingRule] | None = None,
        fallback_agent_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self._rules: list[RoutingRule] = sorted(
            routing_rules or [],
            key=lambda r: r.priority,
            reverse=True,
        )
        self._fallback_agent_id = fallback_agent_id
        self._routing_stats: dict[str, int] = {}
        logger.info(
            "router_agent_init",
            agent_id=self.agent_id,
            rules=len(self._rules),
            fallback=fallback_agent_id,
        )

    def add_rule(self, rule: RoutingRule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)
        logger.info("router_rule_added", rule_id=rule.rule_id, target=rule.target_agent_id)

    def classify(self, query: str) -> RoutingRule | None:
        """Return the first matching rule (highest priority first)."""
        for rule in self._rules:
            if rule.matches(query):
                return rule
        return None

    async def _execute_locally(
        self,
        query: str,
        task_id: str,
        context: dict[str, Any],
    ) -> TaskResult:
        """
        Override local execution: RouterAgent always tries to classify and delegate.
        Falls back to local execution only if no rule matches and no fallback is set.
        """
        start = time.monotonic()
        tracer = self._tracer or self.obs.new_tracer()

        matched_rule = self.classify(query)
        alternatives = [
            Alternative(
                r.target_agent_id,
                float(r.priority) / 10,
                "lower priority" if matched_rule and r.rule_id != matched_rule.rule_id else "not matched",
            )
            for r in self._rules
            if not matched_rule or r.rule_id != matched_rule.rule_id
        ]

        if matched_rule:
            self._routing_stats[matched_rule.target_agent_id] = (
                self._routing_stats.get(matched_rule.target_agent_id, 0) + 1
            )

            a2a_task = await self._a2a_client.delegate(
                capability=matched_rule.target_capability,
                input_data={"query": query, "routed_by": self.agent_id},
                correlation_id=task_id,
                priority=TaskPriority.MEDIUM,
            )

            duration_ms = (time.monotonic() - start) * 1000
            span_id = tracer.start_span(self.agent_id, "route")
            trace = self.obs.record_decision(
                agent_id=self.agent_id,
                decision=f"route_to_{matched_rule.target_agent_id}",
                decision_type=DecisionType.DELEGATION,
                reasoning=(
                    f"Rule '{matched_rule.rule_id}' matched query. "
                    f"Routing to '{matched_rule.target_agent_id}' "
                    f"for capability '{matched_rule.target_capability}'."
                ),
                confidence=0.90,
                protocol_used="A2A",
                inputs={"query": query[:100], "rule_id": matched_rule.rule_id},
                output={"target": matched_rule.target_agent_id, "task_id": a2a_task.task_id},
                span_id=span_id,
                trace_id=tracer.trace_id,
                alternatives=alternatives,
                duration_ms=duration_ms,
            )
            tracer.record(trace)

            return TaskResult(
                task_id=task_id,
                agent_id=self.agent_id,
                success=a2a_task.output is not None,
                output=a2a_task.output or {"error": a2a_task.error},
                error=a2a_task.error,
                protocol_used="A2A",
                agents_delegated_to=[matched_rule.target_agent_id],
                duration_ms=duration_ms,
            )

        if self._fallback_agent_id:
            logger.info("router_using_fallback", fallback=self._fallback_agent_id)
            a2a_task = await self._a2a_client.delegate(
                capability="general",
                input_data={"query": query, "routed_by": self.agent_id, "reason": "no_rule_matched"},
                correlation_id=task_id,
            )
            return TaskResult(
                task_id=task_id,
                agent_id=self.agent_id,
                success=a2a_task.output is not None,
                output=a2a_task.output or {},
                protocol_used="A2A",
                agents_delegated_to=[self._fallback_agent_id],
            )

        return TaskResult(
            task_id=task_id,
            agent_id=self.agent_id,
            success=False,
            output={},
            error=f"No routing rule matched and no fallback configured for: {query[:80]}",
            protocol_used="local",
        )

    def routing_stats(self) -> dict[str, Any]:
        total = sum(self._routing_stats.values())
        return {
            "total_routed": total,
            "by_target": self._routing_stats,
            "rules_count": len(self._rules),
            "fallback_agent": self._fallback_agent_id,
        }


def build_customer_service_router(
    registry: AgentRegistry,
    **kwargs: Any,
) -> RouterAgent:
    """
    Factory: build a customer service RouterAgent with standard domain rules.
    """
    config = AgentConfig(
        agent_id="customer-service-router",
        name="CustomerServiceRouter",
        version="1.0.0",
        role=AgentRole.PLANNER,
        capabilities=["routing", "classification"],
        description="Routes customer queries to specialist agents",
    )

    rules = [
        RoutingRule(
            rule_id="billing-rule",
            keywords=["invoice", "billing", "payment", "charge", "refund", "subscription"],
            target_agent_id="billing-agent",
            target_capability="billing_support",
            priority=9,
            description="Route billing-related queries to BillingAgent",
        ),
        RoutingRule(
            rule_id="tech-rule",
            keywords=["error", "bug", "crash", "not working", "broken", "issue", "technical"],
            target_agent_id="tech-agent",
            target_capability="technical_support",
            priority=8,
            description="Route technical issues to TechAgent",
        ),
        RoutingRule(
            rule_id="legal-rule",
            keywords=["contract", "legal", "compliance", "gdpr", "privacy", "policy", "terms"],
            target_agent_id="legal-agent",
            target_capability="legal_review",
            priority=7,
            description="Route legal queries to LegalAgent",
        ),
        RoutingRule(
            rule_id="data-rule",
            keywords=["report", "analytics", "data", "dashboard", "metrics", "statistics"],
            target_agent_id="data-agent",
            target_capability="data_analysis",
            priority=6,
            description="Route data/analytics queries to DataAgent",
        ),
    ]

    return RouterAgent(
        config=config,
        routing_rules=rules,
        fallback_agent_id="general-agent",
        a2a_registry=registry,
        **kwargs,
    )
