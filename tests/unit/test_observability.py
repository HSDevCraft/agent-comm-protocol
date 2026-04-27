"""
Unit tests for Observability & Explainability Layer
"""
from __future__ import annotations

import time
import pytest

from src.observability import (
    Alternative,
    ConfidenceScorer,
    DecisionLog,
    DecisionType,
    ObservabilityEngine,
    ReasoningTrace,
    SpanTracer,
)


# ── ReasoningTrace Tests ──────────────────────────────────────────────────────

class TestReasoningTrace:
    def _make_trace(self, confidence=0.9, decision_type=DecisionType.DELEGATION) -> ReasoningTrace:
        return ReasoningTrace(
            trace_id="trace-001",
            span_id="span-001",
            agent_id="planner-agent",
            decision="delegate_to_finance",
            decision_type=decision_type,
            reasoning="Finance agent has highest capability score",
            confidence=confidence,
            protocol_used="A2A",
            inputs={"capability": "financial_analysis"},
            output={"delegated_to": "finance-agent"},
        )

    def test_to_dict_has_required_fields(self):
        trace = self._make_trace()
        d = trace.to_dict()
        assert d["trace_id"] == "trace-001"
        assert d["decision_type"] == "DELEGATION"
        assert d["confidence"] == 0.9
        assert "alternatives_considered" in d
        assert "tool_calls" in d
        assert "memory_reads" in d

    def test_explain_returns_string(self):
        trace = self._make_trace()
        explanation = trace.explain()
        assert isinstance(explanation, str)
        assert "planner-agent" in explanation
        assert "delegate_to_finance" in explanation
        assert "90%" in explanation

    def test_explain_includes_alternatives(self):
        trace = self._make_trace()
        trace.alternatives_considered = [
            Alternative("other-agent", 0.65, "lower success rate")
        ]
        explanation = trace.explain()
        assert "other-agent" in explanation
        assert "lower success rate" in explanation

    def test_explain_includes_tools(self):
        trace = self._make_trace()
        trace.tool_calls = ["web_search", "db_query"]
        explanation = trace.explain()
        assert "web_search" in explanation
        assert "db_query" in explanation


# ── SpanTracer Tests ──────────────────────────────────────────────────────────

class TestSpanTracer:
    def test_new_tracer_has_unique_trace_id(self):
        t1 = SpanTracer()
        t2 = SpanTracer()
        assert t1.trace_id != t2.trace_id

    def test_start_span_returns_span_id(self):
        tracer = SpanTracer()
        span_id = tracer.start_span("agent-1", "some_operation")
        assert span_id.startswith("span-")

    def test_record_adds_trace(self):
        tracer = SpanTracer("trace-abc")
        trace = ReasoningTrace(
            trace_id="trace-abc", span_id="span-1", agent_id="agent",
            decision="decide", decision_type=DecisionType.LOCAL_EXECUTION,
            reasoning="test", confidence=0.8, protocol_used="local",
            inputs={}, output={},
        )
        tracer.record(trace)
        assert tracer.span_count() == 1

    def test_get_trace_tree_sorted_by_timestamp(self):
        tracer = SpanTracer("trace-abc")
        for i, confidence in enumerate([0.9, 0.7, 0.85]):
            trace = ReasoningTrace(
                trace_id="trace-abc", span_id=f"span-{i}", agent_id="agent",
                decision=f"decision-{i}", decision_type=DecisionType.DELEGATION,
                reasoning="r", confidence=confidence, protocol_used="A2A",
                inputs={}, output={}, timestamp=time.time() + i,
            )
            tracer.record(trace)
        tree = tracer.get_trace_tree()
        assert len(tree) == 3
        timestamps = [t["timestamp"] for t in tree]
        assert timestamps == sorted(timestamps)

    def test_get_execution_path(self):
        tracer = SpanTracer()
        trace = ReasoningTrace(
            trace_id=tracer.trace_id, span_id="s1", agent_id="planner",
            decision="delegate", decision_type=DecisionType.DELEGATION,
            reasoning="r", confidence=0.9, protocol_used="A2A",
            inputs={}, output={},
        )
        tracer.record(trace)
        path = tracer.get_execution_path()
        assert len(path) == 1
        assert "planner" in path[0]
        assert "A2A" in path[0]

    def test_total_duration_ms(self):
        tracer = SpanTracer()
        trace = ReasoningTrace(
            trace_id=tracer.trace_id, span_id="s1", agent_id="a",
            decision="d", decision_type=DecisionType.TOOL_CALL,
            reasoning="r", confidence=0.8, protocol_used="MCP",
            inputs={}, output={}, duration_ms=50.0,
        )
        tracer.record(trace)
        assert tracer.total_duration_ms() >= 0

    def test_lowest_confidence_step(self):
        tracer = SpanTracer()
        for conf in [0.9, 0.4, 0.7]:
            trace = ReasoningTrace(
                trace_id=tracer.trace_id, span_id=f"s{conf}", agent_id="a",
                decision="d", decision_type=DecisionType.DELEGATION,
                reasoning="r", confidence=conf, protocol_used="A2A",
                inputs={}, output={},
            )
            tracer.record(trace)
        lowest = tracer.lowest_confidence_step()
        assert lowest is not None
        assert lowest.confidence == 0.4

    def test_empty_tracer_returns_zero_duration(self):
        tracer = SpanTracer()
        assert tracer.total_duration_ms() == 0.0

    def test_empty_tracer_lowest_confidence_none(self):
        tracer = SpanTracer()
        assert tracer.lowest_confidence_step() is None


# ── ConfidenceScorer Tests ────────────────────────────────────────────────────

class TestConfidenceScorer:
    def test_perfect_agent_scores_high(self):
        score = ConfidenceScorer.score_agent_for_capability(
            required_capability="financial_analysis",
            agent_capabilities=["financial_analysis"],
            agent_version="3.0.0",
            agent_load=0.0,
            historical_success_rate=1.0,
            avg_latency_ms=50.0,
        )
        assert score > 0.8

    def test_missing_capability_scores_low(self):
        score = ConfidenceScorer.score_agent_for_capability(
            required_capability="financial_analysis",
            agent_capabilities=["web_search"],  # wrong capability
            agent_version="1.0.0",
            agent_load=0.0,
            historical_success_rate=1.0,
            avg_latency_ms=100.0,
        )
        assert score < 0.5

    def test_high_load_reduces_score(self):
        score_low_load = ConfidenceScorer.score_agent_for_capability(
            "cap", ["cap"], "1.0.0", agent_load=0.0, historical_success_rate=1.0
        )
        score_high_load = ConfidenceScorer.score_agent_for_capability(
            "cap", ["cap"], "1.0.0", agent_load=1.0, historical_success_rate=1.0
        )
        assert score_low_load > score_high_load

    def test_score_tool_call_available(self):
        score = ConfidenceScorer.score_tool_call(
            tool_name="web_search",
            available_tools=["web_search", "db_query"],
            past_success=True,
            latency_ms=50.0,
        )
        assert score > 0.8

    def test_score_tool_call_unavailable(self):
        score = ConfidenceScorer.score_tool_call(
            tool_name="nonexistent",
            available_tools=["web_search"],
        )
        assert score < 0.5

    def test_score_normalized_0_to_1(self):
        for load in [0.0, 0.5, 1.0]:
            score = ConfidenceScorer.score_agent_for_capability(
                "cap", ["cap"], "2.0.0", agent_load=load
            )
            assert 0.0 <= score <= 1.0


# ── DecisionLog Tests ─────────────────────────────────────────────────────────

class TestDecisionLog:
    def _trace(self, agent_id="a", decision_type=DecisionType.DELEGATION, confidence=0.8) -> ReasoningTrace:
        return ReasoningTrace(
            trace_id="t", span_id="s", agent_id=agent_id,
            decision="d", decision_type=decision_type,
            reasoning="r", confidence=confidence, protocol_used="A2A",
            inputs={}, output={},
        )

    def test_append_and_count(self):
        log = DecisionLog()
        log.append(self._trace())
        assert log.count() == 1

    def test_query_by_agent(self):
        log = DecisionLog()
        log.append(self._trace(agent_id="agent-a"))
        log.append(self._trace(agent_id="agent-b"))
        results = log.query(agent_id="agent-a")
        assert len(results) == 1
        assert results[0].agent_id == "agent-a"

    def test_query_by_decision_type(self):
        log = DecisionLog()
        log.append(self._trace(decision_type=DecisionType.DELEGATION))
        log.append(self._trace(decision_type=DecisionType.TOOL_CALL))
        results = log.query(decision_type=DecisionType.TOOL_CALL)
        assert len(results) == 1

    def test_query_by_confidence_range(self):
        log = DecisionLog()
        log.append(self._trace(confidence=0.3))
        log.append(self._trace(confidence=0.7))
        log.append(self._trace(confidence=0.95))
        results = log.query(min_confidence=0.5, max_confidence=0.9)
        assert len(results) == 1
        assert results[0].confidence == 0.7

    def test_low_confidence_decisions(self):
        log = DecisionLog()
        log.append(self._trace(confidence=0.3))
        log.append(self._trace(confidence=0.9))
        low = log.low_confidence_decisions(threshold=0.5)
        assert len(low) == 1
        assert low[0].confidence == 0.3

    def test_summary_structure(self):
        log = DecisionLog()
        log.append(self._trace(agent_id="planner", decision_type=DecisionType.DELEGATION, confidence=0.9))
        log.append(self._trace(agent_id="finance", decision_type=DecisionType.TOOL_CALL, confidence=0.8))
        s = log.summary()
        assert s["total"] == 2
        assert "avg_confidence" in s
        assert "DELEGATION" in s["by_decision_type"]
        assert "planner" in s["by_agent"]

    def test_summary_empty_log(self):
        log = DecisionLog()
        s = log.summary()
        assert s["total"] == 0


# ── ObservabilityEngine Tests ─────────────────────────────────────────────────

class TestObservabilityEngine:
    def test_new_tracer_registered(self, observability_engine):
        tracer = observability_engine.new_tracer()
        found = observability_engine.get_tracer(tracer.trace_id)
        assert found is not None
        assert found.trace_id == tracer.trace_id

    def test_record_decision_adds_to_log(self, observability_engine):
        trace = observability_engine.record_decision(
            agent_id="test-agent",
            decision="test_decision",
            decision_type=DecisionType.LOCAL_EXECUTION,
            reasoning="test reasoning",
            confidence=0.85,
            protocol_used="local",
            inputs={},
            output={},
        )
        assert observability_engine.decision_log.count() == 1
        assert trace.agent_id == "test-agent"
        assert trace.confidence == 0.85

    def test_debug_hook_fires(self, observability_engine):
        fired = []
        observability_engine.add_debug_hook(lambda t: fired.append(t.agent_id))
        observability_engine.record_decision(
            agent_id="hook-agent",
            decision="d", decision_type=DecisionType.DELEGATION,
            reasoning="r", confidence=0.5, protocol_used="A2A",
            inputs={}, output={},
        )
        assert "hook-agent" in fired

    def test_record_decision_returns_trace(self, observability_engine):
        trace = observability_engine.record_decision(
            "a", "d", DecisionType.TOOL_CALL, "r", 0.75, "MCP", {}, {}
        )
        assert isinstance(trace, ReasoningTrace)
        assert trace.confidence == 0.75
