"""
Observability & Explainability Layer

Makes multi-agent systems transparent and debuggable:
  - ReasoningTrace: records every decision with confidence score and alternatives
  - SpanTracer:     links traces into a distributed DAG (like OpenTelemetry)
  - DecisionLog:    queryable append-only log of all agent decisions
  - ConfidenceScorer: computes normalized confidence from multiple signals
  - DebuggingHooks: tap into any decision point for inspection
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


from src._logging import get_logger

logger = get_logger(__name__)


class DecisionType(str, Enum):
    DELEGATION = "DELEGATION"
    TOOL_CALL = "TOOL_CALL"
    MEMORY_READ = "MEMORY_READ"
    MEMORY_WRITE = "MEMORY_WRITE"
    LOCAL_EXECUTION = "LOCAL_EXECUTION"
    FALLBACK = "FALLBACK"
    ESCALATION = "ESCALATION"
    RETRY = "RETRY"
    PROTOCOL_SELECTION = "PROTOCOL_SELECTION"


@dataclass
class Alternative:
    """An option that was considered but not chosen during a decision."""
    option: str
    score: float
    rejected_reason: str


@dataclass
class ReasoningTrace:
    """
    A single, atomic decision record emitted by an agent.

    WHY: Makes agent behavior auditable and explainable. Users can trace
    exactly why an agent delegated to specialist X instead of Y, or why
    it chose tool A over B. Required for regulatory compliance and debugging.
    """
    trace_id: str
    span_id: str
    agent_id: str
    decision: str
    decision_type: DecisionType
    reasoning: str
    confidence: float
    protocol_used: str
    inputs: dict[str, Any]
    output: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    parent_span_id: str = ""
    alternatives_considered: list[Alternative] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    memory_reads: list[str] = field(default_factory=list)
    memory_writes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
            "duration_ms": round(self.duration_ms, 2),
            "decision": self.decision,
            "decision_type": self.decision_type.value,
            "reasoning": self.reasoning,
            "confidence": round(self.confidence, 4),
            "protocol_used": self.protocol_used,
            "alternatives_considered": [
                {
                    "option": a.option,
                    "score": round(a.score, 4),
                    "rejected_reason": a.rejected_reason,
                }
                for a in self.alternatives_considered
            ],
            "inputs": self.inputs,
            "output": self.output,
            "tool_calls": self.tool_calls,
            "memory_reads": self.memory_reads,
            "memory_writes": self.memory_writes,
            "tags": self.tags,
        }

    def explain(self) -> str:
        """Human-readable explanation of this decision."""
        lines = [
            f"[{self.decision_type.value}] Agent '{self.agent_id}' decided: {self.decision}",
            f"  Confidence: {self.confidence:.0%}",
            f"  Protocol: {self.protocol_used}",
            f"  Reasoning: {self.reasoning}",
        ]
        if self.alternatives_considered:
            lines.append("  Alternatives rejected:")
            for alt in self.alternatives_considered:
                lines.append(f"    - {alt.option} (score={alt.score:.2f}): {alt.rejected_reason}")
        if self.tool_calls:
            lines.append(f"  Tools called: {', '.join(self.tool_calls)}")
        if self.memory_reads:
            lines.append(f"  Memory reads: {', '.join(self.memory_reads)}")
        lines.append(f"  Duration: {self.duration_ms:.1f}ms")
        return "\n".join(lines)


class SpanTracer:
    """
    Lightweight distributed tracing — links ReasoningTraces into a DAG.
    Compatible with OpenTelemetry span model (trace_id + span_id + parent_span_id).

    In production: export spans to Jaeger/Zipkin/OTLP.
    """

    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or f"trace-{uuid.uuid4().hex[:12]}"
        self._spans: list[ReasoningTrace] = []
        self._active_span_id: str = ""

    def start_span(self, agent_id: str, operation: str) -> str:
        span_id = f"span-{uuid.uuid4().hex[:8]}"
        logger.debug(
            "span_started",
            trace_id=self.trace_id,
            span_id=span_id,
            agent=agent_id,
            operation=operation,
        )
        self._active_span_id = span_id
        return span_id

    def record(self, trace: ReasoningTrace) -> None:
        self._spans.append(trace)
        logger.info(
            "reasoning_trace",
            trace_id=self.trace_id,
            span_id=trace.span_id,
            agent=trace.agent_id,
            decision=trace.decision,
            confidence=trace.confidence,
            duration_ms=trace.duration_ms,
        )

    def get_trace_tree(self) -> list[dict[str, Any]]:
        """Build a flat list of spans sorted by timestamp."""
        return [t.to_dict() for t in sorted(self._spans, key=lambda x: x.timestamp)]

    def get_execution_path(self) -> list[str]:
        """Return a simplified human-readable execution path."""
        return [
            f"{t.agent_id}::{t.decision} [{t.protocol_used}] ({t.confidence:.0%})"
            for t in sorted(self._spans, key=lambda x: x.timestamp)
        ]

    def total_duration_ms(self) -> float:
        if not self._spans:
            return 0.0
        earliest = min(t.timestamp for t in self._spans)
        latest = max(t.timestamp + t.duration_ms / 1000 for t in self._spans)
        return (latest - earliest) * 1000

    def lowest_confidence_step(self) -> ReasoningTrace | None:
        if not self._spans:
            return None
        return min(self._spans, key=lambda t: t.confidence)

    def span_count(self) -> int:
        return len(self._spans)


class ConfidenceScorer:
    """
    Computes a normalized 0–1 confidence score for agent decisions.

    Factors:
    - capability_match: how closely agent capabilities match the required task
    - version_score: newer agents score higher
    - load_score: agents under high load score lower
    - historical_success: past success rate
    - latency_score: agents with lower latency score higher
    """

    @staticmethod
    def score_agent_for_capability(
        required_capability: str,
        agent_capabilities: list[str],
        agent_version: str,
        agent_load: float = 0.0,
        historical_success_rate: float = 1.0,
        avg_latency_ms: float = 100.0,
    ) -> float:
        capability_match = 1.0 if required_capability in agent_capabilities else 0.0

        try:
            major, minor, patch = (int(x) for x in (agent_version + ".0.0").split(".")[:3])
            version_score = min(1.0, (major * 100 + minor * 10 + patch) / 300)
        except (ValueError, TypeError):
            version_score = 0.5

        load_score = max(0.0, 1.0 - agent_load)
        latency_score = max(0.0, 1.0 - (avg_latency_ms / 5000))

        weights = {
            "capability": 0.50,
            "success_rate": 0.25,
            "version": 0.10,
            "load": 0.10,
            "latency": 0.05,
        }

        score = (
            weights["capability"] * capability_match
            + weights["success_rate"] * historical_success_rate
            + weights["version"] * version_score
            + weights["load"] * load_score
            + weights["latency"] * latency_score
        )
        return round(min(1.0, max(0.0, score)), 4)

    @staticmethod
    def score_tool_call(
        tool_name: str,
        available_tools: list[str],
        past_success: bool = True,
        latency_ms: float = 0.0,
    ) -> float:
        availability = 1.0 if tool_name in available_tools else 0.0
        success = 1.0 if past_success else 0.3
        latency_penalty = max(0.0, 1.0 - latency_ms / 10000)
        return round((availability * 0.6 + success * 0.3 + latency_penalty * 0.1), 4)


class DecisionLog:
    """
    Persistent, queryable log of all agent decisions across the system.
    Used for post-hoc analysis, compliance, and debugging.
    """

    def __init__(self) -> None:
        self._entries: list[ReasoningTrace] = []

    def append(self, trace: ReasoningTrace) -> None:
        self._entries.append(trace)

    def query(
        self,
        agent_id: str | None = None,
        decision_type: DecisionType | None = None,
        min_confidence: float | None = None,
        max_confidence: float | None = None,
        since: float | None = None,
        protocol: str | None = None,
    ) -> list[ReasoningTrace]:
        results = self._entries
        if agent_id:
            results = [t for t in results if t.agent_id == agent_id]
        if decision_type:
            results = [t for t in results if t.decision_type == decision_type]
        if min_confidence is not None:
            results = [t for t in results if t.confidence >= min_confidence]
        if max_confidence is not None:
            results = [t for t in results if t.confidence <= max_confidence]
        if since:
            results = [t for t in results if t.timestamp >= since]
        if protocol:
            results = [t for t in results if t.protocol_used == protocol]
        return results

    def low_confidence_decisions(self, threshold: float = 0.5) -> list[ReasoningTrace]:
        return self.query(max_confidence=threshold)

    def summary(self) -> dict[str, Any]:
        if not self._entries:
            return {"total": 0}
        confidences = [t.confidence for t in self._entries]
        by_type: dict[str, int] = {}
        by_agent: dict[str, int] = {}
        for t in self._entries:
            by_type[t.decision_type.value] = by_type.get(t.decision_type.value, 0) + 1
            by_agent[t.agent_id] = by_agent.get(t.agent_id, 0) + 1
        return {
            "total": len(self._entries),
            "avg_confidence": round(sum(confidences) / len(confidences), 4),
            "min_confidence": round(min(confidences), 4),
            "max_confidence": round(max(confidences), 4),
            "by_decision_type": by_type,
            "by_agent": by_agent,
        }

    def count(self) -> int:
        return len(self._entries)


DebuggingHook = Callable[[ReasoningTrace], None]


class ObservabilityEngine:
    """
    Central observability engine — wires together tracing, logging, and debugging.

    Usage pattern:
        engine = ObservabilityEngine()
        tracer = engine.new_tracer()
        span_id = tracer.start_span("my-agent", "decide_delegation")
        trace = engine.record_decision(...)
        tracer.record(trace)
    """

    def __init__(self) -> None:
        self.decision_log = DecisionLog()
        self.scorer = ConfidenceScorer()
        self._hooks: list[DebuggingHook] = []
        self._tracers: dict[str, SpanTracer] = {}

    def new_tracer(self, trace_id: str | None = None) -> SpanTracer:
        tracer = SpanTracer(trace_id)
        self._tracers[tracer.trace_id] = tracer
        return tracer

    def get_tracer(self, trace_id: str) -> SpanTracer | None:
        return self._tracers.get(trace_id)

    def add_debug_hook(self, hook: DebuggingHook) -> None:
        self._hooks.append(hook)

    def record_decision(
        self,
        agent_id: str,
        decision: str,
        decision_type: DecisionType,
        reasoning: str,
        confidence: float,
        protocol_used: str,
        inputs: dict[str, Any],
        output: dict[str, Any],
        span_id: str = "",
        parent_span_id: str = "",
        trace_id: str = "",
        alternatives: list[Alternative] | None = None,
        tool_calls: list[str] | None = None,
        memory_reads: list[str] | None = None,
        memory_writes: list[str] | None = None,
        duration_ms: float = 0.0,
        tags: list[str] | None = None,
    ) -> ReasoningTrace:
        trace = ReasoningTrace(
            trace_id=trace_id or f"trace-{uuid.uuid4().hex[:8]}",
            span_id=span_id or f"span-{uuid.uuid4().hex[:8]}",
            parent_span_id=parent_span_id,
            agent_id=agent_id,
            decision=decision,
            decision_type=decision_type,
            reasoning=reasoning,
            confidence=confidence,
            protocol_used=protocol_used,
            inputs=inputs,
            output=output,
            alternatives_considered=alternatives or [],
            tool_calls=tool_calls or [],
            memory_reads=memory_reads or [],
            memory_writes=memory_writes or [],
            duration_ms=duration_ms,
            tags=tags or [],
        )
        self.decision_log.append(trace)
        for hook in self._hooks:
            try:
                hook(trace)
            except Exception:
                pass
        return trace

    def print_trace_tree(self, tracer: SpanTracer) -> None:
        print(f"\n{'='*60}")
        print(f"Trace: {tracer.trace_id}  |  Spans: {tracer.span_count()}  |  Total: {tracer.total_duration_ms():.1f}ms")
        print("="*60)
        for step in tracer.get_execution_path():
            print(f"  → {step}")
        lowest = tracer.lowest_confidence_step()
        if lowest and lowest.confidence < 0.7:
            print(f"\n⚠ Lowest confidence step: {lowest.agent_id}::{lowest.decision} ({lowest.confidence:.0%})")
        print("="*60)
