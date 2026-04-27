"""
Memory Layer — SAMEP-style (Shared Agent Memory Exchange Protocol)

Three memory tiers:
  - WorkingMemory:  in-flight data for current task (TTL: session)
  - EpisodicMemory: history of past interactions  (TTL: days/weeks)
  - SemanticMemory: vector-indexed facts/knowledge (TTL: indefinite)

Any authorized agent in the same workflow can read/write memory objects.
Memory is the "shared whiteboard" that prevents redundant tool calls.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


from src._logging import get_logger

logger = get_logger(__name__)


class MemoryTier(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


@dataclass
class AccessControl:
    readable_by: list[str] = field(default_factory=list)
    writable_by: list[str] = field(default_factory=list)
    visibility: str = "workflow"

    def can_read(self, agent_id: str) -> bool:
        if self.visibility == "public":
            return True
        return agent_id in self.readable_by

    def can_write(self, agent_id: str) -> bool:
        return agent_id in self.writable_by


@dataclass
class MemoryObject:
    """
    A typed, versioned record in shared agent memory.

    WHY: Allows agents to share intermediate results without coupling
    to each other's interfaces. Like a shared whiteboard with access control.
    """
    memory_id: str
    tier: MemoryTier
    created_by: str
    content: dict[str, Any]
    session_id: str = ""
    correlation_id: str = ""
    ttl_seconds: int = 86400
    version: int = 1
    tags: list[str] = field(default_factory=list)
    access_control: AccessControl = field(default_factory=AccessControl)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        serialized = json.dumps(self.content, sort_keys=True).encode()
        return hashlib.sha256(serialized).hexdigest()[:16]

    def is_expired(self) -> bool:
        return time.time() > (self.created_at + self.ttl_seconds)

    def update_content(self, new_content: dict[str, Any], agent_id: str) -> bool:
        if not self.access_control.can_write(agent_id):
            logger.warning("memory_write_denied", memory_id=self.memory_id, agent=agent_id)
            return False
        self.content = new_content
        self.content_hash = self._compute_hash()
        self.version += 1
        self.updated_at = time.time()
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "type": self.tier.value,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "ttl_seconds": self.ttl_seconds,
            "version": self.version,
            "tags": self.tags,
            "content": self.content,
            "content_hash": self.content_hash,
            "access_control": {
                "readable_by": self.access_control.readable_by,
                "writable_by": self.access_control.writable_by,
                "visibility": self.access_control.visibility,
            },
        }


class WorkingMemory:
    """
    In-flight memory for the current task/session.
    Automatically evicted when session ends or TTL expires.
    """

    def __init__(self) -> None:
        self._store: dict[str, MemoryObject] = {}

    def write(
        self,
        key: str,
        content: dict[str, Any],
        agent_id: str,
        session_id: str = "",
        readable_by: list[str] | None = None,
        ttl_seconds: int = 3600,
    ) -> MemoryObject:
        mem = MemoryObject(
            memory_id=f"wm-{uuid.uuid4().hex[:8]}",
            tier=MemoryTier.WORKING,
            created_by=agent_id,
            content=content,
            session_id=session_id,
            ttl_seconds=ttl_seconds,
            access_control=AccessControl(
                readable_by=readable_by or [agent_id],
                writable_by=[agent_id],
                visibility="workflow" if readable_by else "private",
            ),
        )
        self._store[key] = mem
        logger.debug("working_memory_write", key=key, agent=agent_id)
        return mem

    def read(self, key: str, agent_id: str) -> dict[str, Any] | None:
        mem = self._store.get(key)
        if not mem:
            return None
        if mem.is_expired():
            del self._store[key]
            return None
        if not mem.access_control.can_read(agent_id):
            logger.warning("working_memory_read_denied", key=key, agent=agent_id)
            return None
        return mem.content

    def clear_session(self, session_id: str) -> int:
        to_delete = [k for k, v in self._store.items() if v.session_id == session_id]
        for k in to_delete:
            del self._store[k]
        return len(to_delete)

    def size(self) -> int:
        return len(self._store)


class EpisodicMemory:
    """
    Session history and past interaction records.
    Persisted across sessions; used for context-aware agent behavior.
    """

    def __init__(self) -> None:
        self._store: dict[str, MemoryObject] = {}
        self._index: dict[str, list[str]] = {}

    def store(
        self,
        content: dict[str, Any],
        agent_id: str,
        tags: list[str] | None = None,
        correlation_id: str = "",
        ttl_seconds: int = 604800,
        readable_by: list[str] | None = None,
    ) -> MemoryObject:
        mem_id = f"ep-{uuid.uuid4().hex[:8]}"
        mem = MemoryObject(
            memory_id=mem_id,
            tier=MemoryTier.EPISODIC,
            created_by=agent_id,
            content=content,
            tags=tags or [],
            correlation_id=correlation_id,
            ttl_seconds=ttl_seconds,
            access_control=AccessControl(
                readable_by=readable_by or [],
                writable_by=[agent_id],
                visibility="workflow" if readable_by else "private",
            ),
        )
        self._store[mem_id] = mem

        for tag in (tags or []):
            self._index.setdefault(tag, []).append(mem_id)

        logger.debug("episodic_memory_stored", mem_id=mem_id, tags=tags)
        return mem

    def search_by_tags(self, tags: list[str], agent_id: str) -> list[MemoryObject]:
        """Return all non-expired episodic memories matching any of the tags."""
        candidate_ids: set[str] = set()
        for tag in tags:
            candidate_ids.update(self._index.get(tag, []))

        results = []
        for mem_id in candidate_ids:
            mem = self._store.get(mem_id)
            if mem and not mem.is_expired() and mem.access_control.can_read(agent_id):
                results.append(mem)

        results.sort(key=lambda m: m.created_at, reverse=True)
        return results

    def get(self, mem_id: str, agent_id: str) -> MemoryObject | None:
        mem = self._store.get(mem_id)
        if not mem or mem.is_expired():
            return None
        if not mem.access_control.can_read(agent_id):
            return None
        return mem

    def size(self) -> int:
        return len(self._store)


class SemanticMemory:
    """
    Vector-indexed factual knowledge store.
    In production: backed by Qdrant/Pinecone/FAISS with real embeddings.
    Here: simplified keyword-based similarity for demonstration.
    """

    def __init__(self) -> None:
        self._store: list[MemoryObject] = []

    def index(
        self,
        content: dict[str, Any],
        agent_id: str,
        tags: list[str] | None = None,
        readable_by: list[str] | None = None,
    ) -> MemoryObject:
        mem = MemoryObject(
            memory_id=f"sm-{uuid.uuid4().hex[:8]}",
            tier=MemoryTier.SEMANTIC,
            created_by=agent_id,
            content=content,
            tags=tags or [],
            ttl_seconds=365 * 86400,
            access_control=AccessControl(
                readable_by=readable_by or [],
                writable_by=[agent_id],
                visibility="public" if not readable_by else "workflow",
            ),
        )
        self._store.append(mem)
        logger.debug("semantic_memory_indexed", mem_id=mem.memory_id)
        return mem

    def search(self, query: str, agent_id: str, top_k: int = 5) -> list[MemoryObject]:
        """
        Keyword-based similarity search (substitute with vector ANN in production).
        Scores memories by overlap of query terms with tags and content text.
        """
        query_terms = set(query.lower().split())
        scored: list[tuple[float, MemoryObject]] = []

        for mem in self._store:
            if not mem.access_control.can_read(agent_id) and mem.access_control.visibility != "public":
                continue
            tag_text = " ".join(mem.tags).lower()
            content_text = json.dumps(mem.content).lower()
            combined = f"{tag_text} {content_text}"
            overlap = sum(1 for term in query_terms if term in combined)
            if overlap > 0:
                score = overlap / max(len(query_terms), 1)
                scored.append((score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in scored[:top_k]]

    def size(self) -> int:
        return len(self._store)


class MemoryManager:
    """
    Unified interface over all three memory tiers.
    Agents interact with memory exclusively through this manager.
    """

    def __init__(self) -> None:
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()

    def write_working(
        self,
        key: str,
        content: dict[str, Any],
        agent_id: str,
        session_id: str = "",
        readable_by: list[str] | None = None,
    ) -> MemoryObject:
        return self.working.write(key, content, agent_id, session_id, readable_by)

    def read_working(self, key: str, agent_id: str) -> dict[str, Any] | None:
        return self.working.read(key, agent_id)

    def store_episodic(
        self,
        content: dict[str, Any],
        agent_id: str,
        tags: list[str] | None = None,
        correlation_id: str = "",
        readable_by: list[str] | None = None,
    ) -> MemoryObject:
        return self.episodic.store(content, agent_id, tags, correlation_id, readable_by=readable_by)

    def search_episodic(self, tags: list[str], agent_id: str) -> list[MemoryObject]:
        return self.episodic.search_by_tags(tags, agent_id)

    def index_semantic(
        self,
        content: dict[str, Any],
        agent_id: str,
        tags: list[str] | None = None,
    ) -> MemoryObject:
        return self.semantic.index(content, agent_id, tags)

    def search_semantic(self, query: str, agent_id: str, top_k: int = 5) -> list[MemoryObject]:
        return self.semantic.search(query, agent_id, top_k)

    def stats(self) -> dict[str, int]:
        return {
            "working": self.working.size(),
            "episodic": self.episodic.size(),
            "semantic": self.semantic.size(),
        }
