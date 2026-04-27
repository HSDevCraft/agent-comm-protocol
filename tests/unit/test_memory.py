"""
Unit tests for Memory Layer (SAMEP-style)
"""
from __future__ import annotations

import time
import pytest

from src.memory import (
    AccessControl,
    EpisodicMemory,
    MemoryManager,
    MemoryObject,
    MemoryTier,
    SemanticMemory,
    WorkingMemory,
)


# ── AccessControl Tests ───────────────────────────────────────────────────────

class TestAccessControl:
    def test_public_visibility_allows_any(self):
        ac = AccessControl(readable_by=[], writable_by=[], visibility="public")
        assert ac.can_read("any-agent") is True
        assert ac.can_read("unknown-agent") is True

    def test_workflow_visibility_checks_readable_by(self):
        ac = AccessControl(readable_by=["agent-a"], writable_by=[], visibility="workflow")
        assert ac.can_read("agent-a") is True
        assert ac.can_read("agent-b") is False

    def test_can_write(self):
        ac = AccessControl(readable_by=[], writable_by=["writer"], visibility="private")
        assert ac.can_write("writer") is True
        assert ac.can_write("reader") is False


# ── MemoryObject Tests ────────────────────────────────────────────────────────

class TestMemoryObject:
    def test_content_hash_computed_on_init(self):
        mem = MemoryObject(
            memory_id="m1", tier=MemoryTier.WORKING,
            created_by="agent", content={"key": "value"},
        )
        assert len(mem.content_hash) > 0

    def test_is_expired_false_when_fresh(self):
        mem = MemoryObject(
            memory_id="m1", tier=MemoryTier.WORKING,
            created_by="agent", content={}, ttl_seconds=3600,
        )
        assert mem.is_expired() is False

    def test_is_expired_true_when_old(self):
        mem = MemoryObject(
            memory_id="m1", tier=MemoryTier.WORKING,
            created_by="agent", content={}, ttl_seconds=1,
        )
        mem.created_at = time.time() - 2  # 2 seconds ago, TTL is 1
        assert mem.is_expired() is True

    def test_update_content_allowed(self):
        mem = MemoryObject(
            memory_id="m1", tier=MemoryTier.WORKING,
            created_by="agent",
            content={"val": 1},
            access_control=AccessControl(readable_by=[], writable_by=["agent"], visibility="private"),
        )
        old_hash = mem.content_hash
        success = mem.update_content({"val": 2}, "agent")
        assert success is True
        assert mem.content == {"val": 2}
        assert mem.version == 2
        assert mem.content_hash != old_hash

    def test_update_content_denied(self):
        mem = MemoryObject(
            memory_id="m1", tier=MemoryTier.WORKING,
            created_by="agent",
            content={"val": 1},
            access_control=AccessControl(readable_by=[], writable_by=["agent"], visibility="private"),
        )
        success = mem.update_content({"val": 99}, "other-agent")
        assert success is False
        assert mem.content == {"val": 1}


# ── WorkingMemory Tests ───────────────────────────────────────────────────────

class TestWorkingMemory:
    def test_write_and_read(self):
        wm = WorkingMemory()
        wm.write("key1", {"data": "value"}, "agent-a",
                  readable_by=["agent-a"])
        result = wm.read("key1", "agent-a")
        assert result == {"data": "value"}

    def test_read_returns_none_for_missing_key(self):
        wm = WorkingMemory()
        assert wm.read("missing", "agent-a") is None

    def test_read_denied_for_unauthorized_agent(self):
        wm = WorkingMemory()
        wm.write("key1", {"data": "secret"}, "agent-a",
                  readable_by=["agent-a"])
        result = wm.read("key1", "agent-b")
        assert result is None

    def test_read_expired_returns_none(self):
        wm = WorkingMemory()
        wm.write("key1", {"data": "value"}, "agent-a",
                  readable_by=["agent-a"], ttl_seconds=1)
        # Expire the entry manually
        wm._store["key1"].created_at = time.time() - 2
        result = wm.read("key1", "agent-a")
        assert result is None
        assert "key1" not in wm._store  # evicted

    def test_clear_session(self):
        wm = WorkingMemory()
        wm.write("k1", {}, "agent", session_id="sess-1", readable_by=["agent"])
        wm.write("k2", {}, "agent", session_id="sess-1", readable_by=["agent"])
        wm.write("k3", {}, "agent", session_id="sess-2", readable_by=["agent"])
        cleared = wm.clear_session("sess-1")
        assert cleared == 2
        assert wm.size() == 1

    def test_size(self):
        wm = WorkingMemory()
        assert wm.size() == 0
        wm.write("k1", {}, "a", readable_by=["a"])
        assert wm.size() == 1


# ── EpisodicMemory Tests ──────────────────────────────────────────────────────

class TestEpisodicMemory:
    def test_store_and_search_by_tags(self):
        em = EpisodicMemory()
        em.store(
            content={"report": "ACME Q3"},
            agent_id="finance-agent",
            tags=["ACME", "Q3", "finance"],
            readable_by=["finance-agent"],
        )
        results = em.search_by_tags(["ACME"], "finance-agent")
        assert len(results) == 1
        assert results[0].content == {"report": "ACME Q3"}

    def test_search_multiple_tags_union(self):
        em = EpisodicMemory()
        em.store({"q": "ACME"}, "a", tags=["ACME"], readable_by=["a"])
        em.store({"q": "TESLA"}, "a", tags=["TESLA"], readable_by=["a"])
        results = em.search_by_tags(["ACME", "TESLA"], "a")
        assert len(results) == 2

    def test_search_access_control(self):
        em = EpisodicMemory()
        em.store({"secret": "data"}, "a", tags=["finance"], readable_by=["a"])
        results = em.search_by_tags(["finance"], "b")  # b not in readable_by
        assert len(results) == 0

    def test_search_expired_excluded(self):
        em = EpisodicMemory()
        em.store({"data": "old"}, "a", tags=["tag1"],
                 readable_by=["a"], ttl_seconds=1)
        # Expire manually
        for mem_id in em._index.get("tag1", []):
            em._store[mem_id].created_at = time.time() - 2
        results = em.search_by_tags(["tag1"], "a")
        assert len(results) == 0

    def test_get_by_id(self):
        em = EpisodicMemory()
        mem = em.store({"data": "test"}, "a", tags=["t"], readable_by=["a"])
        found = em.get(mem.memory_id, "a")
        assert found is not None
        assert found.memory_id == mem.memory_id

    def test_get_by_id_unauthorized(self):
        em = EpisodicMemory()
        mem = em.store({"data": "secret"}, "a", tags=["t"], readable_by=["a"])
        found = em.get(mem.memory_id, "b")
        assert found is None


# ── SemanticMemory Tests ──────────────────────────────────────────────────────

class TestSemanticMemory:
    def test_index_and_search(self):
        sm = SemanticMemory()
        sm.index(
            content={"fact": "ACME Corp is a cloud software company"},
            agent_id="researcher",
            tags=["ACME", "cloud", "software"],
        )
        results = sm.search("ACME cloud company", "any-agent", top_k=5)
        assert len(results) == 1

    def test_search_returns_top_k(self):
        sm = SemanticMemory()
        for i in range(10):
            sm.index({"i": i, "tag": f"topic-{i}"}, "a", tags=[f"topic-{i}", "common"])
        results = sm.search("common topic", "a", top_k=3)
        assert len(results) <= 3

    def test_search_no_match_returns_empty(self):
        sm = SemanticMemory()
        sm.index({"fact": "Finance is complex"}, "a", tags=["finance"])
        results = sm.search("unrelated quantum physics", "a")
        assert results == []

    def test_index_size(self):
        sm = SemanticMemory()
        assert sm.size() == 0
        sm.index({"x": 1}, "a")
        assert sm.size() == 1


# ── MemoryManager Tests ───────────────────────────────────────────────────────

class TestMemoryManager:
    def test_write_and_read_working(self, memory_manager):
        memory_manager.write_working(
            "key", {"val": 42}, "agent-a",
            readable_by=["agent-a"],
        )
        result = memory_manager.read_working("key", "agent-a")
        assert result == {"val": 42}

    def test_store_and_search_episodic(self, memory_manager):
        memory_manager.store_episodic(
            {"event": "analysis done"}, "agent-a",
            tags=["done", "analysis"],
            readable_by=["agent-a"],
        )
        hits = memory_manager.search_episodic(["analysis"], "agent-a")
        assert len(hits) == 1

    def test_index_and_search_semantic(self, memory_manager):
        memory_manager.index_semantic(
            {"fact": "Python is a programming language"},
            "researcher",
            tags=["python", "programming"],
        )
        hits = memory_manager.search_semantic("python language", "anyone", top_k=3)
        assert len(hits) == 1

    def test_stats(self, memory_manager):
        memory_manager.write_working("k1", {}, "a", readable_by=["a"])
        memory_manager.store_episodic({}, "a", tags=["t"], readable_by=["a"])
        memory_manager.index_semantic({}, "a", tags=["s"])
        stats = memory_manager.stats()
        assert stats["working"] == 1
        assert stats["episodic"] == 1
        assert stats["semantic"] == 1
