# Memory Architecture — SAMEP-style: Deep Dive

## 1. Why Agents Need Shared Memory

Without shared memory, multi-agent systems are wasteful and brittle:

```
Problem without memory:
  User: "Analyze ACME Corp"
  PlannerAgent → FinanceAgent: "Analyze ACME Corp"    (DB query: 200ms)
  PlannerAgent → LegalAgent: "Check ACME Corp"        (DB query: 200ms)
  PlannerAgent → ReportAgent: "Summarize ACME Corp"   (DB query: 200ms)
                                                       Total: 600ms + 3× DB load

With shared memory:
  FinanceAgent fetches ACME data, writes to WorkingMemory
  LegalAgent reads ACME data from WorkingMemory         (0ms)
  ReportAgent reads ACME data from WorkingMemory        (0ms)
                                                       Total: 200ms + 1× DB load
```

Additionally, agents need memory to:
- Maintain context across a multi-step workflow
- Learn from past interactions (episodic)
- Build up a knowledge base (semantic)
- Share partial results before final answers are ready

---

## 2. Three Memory Tiers

```
┌─────────────────────────────────────────────────────────────────┐
│  WORKING MEMORY  (in-flight data, session-scoped)               │
│  Key-value store │ TTL: session │ Access: per-workflow agents    │
│  Use: intermediate results, shared context between agents       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ promoted after session ends
┌──────────────────────────▼──────────────────────────────────────┐
│  EPISODIC MEMORY  (past interactions, tag-indexed)              │
│  Tag-indexed records │ TTL: days-weeks │ Access: any authorized │
│  Use: "we analyzed ACME last week, here are those results"      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ knowledge extracted and vectorized
┌──────────────────────────▼──────────────────────────────────────┐
│  SEMANTIC MEMORY  (factual knowledge, vector-indexed)           │
│  Vector store │ TTL: indefinite │ Access: public or scoped      │
│  Use: "ACME Corp is a cloud-first software company (fact)"      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Memory Object — Schema Deep Dive

```python
@dataclass
class MemoryObject:
    memory_id: str           # globally unique: "mem-abc-123"
    tier: MemoryTier         # WORKING | EPISODIC | SEMANTIC
    created_by: str          # agent_id of creator
    content: dict[str, Any]  # the actual data (typed by content["type"])
    session_id: str          # links to a workflow session
    correlation_id: str      # links to a workflow (same as task correlation_id)
    ttl_seconds: int         # when to evict (3600 for working, 604800 for episodic)
    version: int             # incremented on every update
    tags: list[str]          # for episodic search (e.g., ["ACME", "finance", "Q3"])
    access_control: AccessControl  # who can read/write
    content_hash: str        # SHA-256 of content (detect tampering)
```

**Content typing convention:**
```python
# Finance result
content = {
    "type": "financial_analysis_result",
    "data": {
        "company": "ACME Corp",
        "ticker": "ACME",
        "period": "Q3-2024",
        "revenue": 4_200_000_000,
        "growth_yoy": 0.12,
        "summary": "..."
    }
}

# Search result
content = {
    "type": "web_search_result",
    "data": {
        "query": "LLM benchmarks 2024",
        "results": [{"title": "...", "url": "...", "snippet": "..."}]
    }
}
```

---

## 4. Access Control Model

Every memory object has an `AccessControl` object:

```python
@dataclass
class AccessControl:
    readable_by: list[str]   # agent_ids that can read
    writable_by: list[str]   # agent_ids that can write
    visibility: str          # "public" | "workflow" | "private"
```

**Visibility levels:**
```
public   → any agent can read (no ID check)
workflow → only agents in readable_by list
private  → only the creator
```

**Why this matters:**
- Finance agent writes sensitive revenue data — only planner and report agents should read it
- Research agent writes public facts — any agent in the system should be able to use them
- Security agent writes audit findings — only security team agents can access

```python
# Example: write with access control
memory.write_working(
    key="acme-revenue",
    content={"revenue": 4.2e9, "source": "edgar_db"},
    agent_id="finance-agent",
    readable_by=["planner-agent", "report-agent"],  # NOT legal-agent
)

# Legal agent CANNOT read this
result = memory.read_working("acme-revenue", "legal-agent")  # Returns None
```

---

## 5. Working Memory — Implementation

```python
class WorkingMemory:
    def write(self, key, content, agent_id, session_id, readable_by, ttl_seconds):
        mem = MemoryObject(
            memory_id=f"wm-{uuid.uuid4().hex[:8]}",
            tier=MemoryTier.WORKING,
            ...
        )
        self._store[key] = mem  # dict-based: O(1) read/write

    def read(self, key, agent_id):
        mem = self._store.get(key)
        if not mem:
            return None
        if mem.is_expired():      # TTL check on every read (lazy eviction)
            del self._store[key]
            return None
        if not mem.access_control.can_read(agent_id):
            logger.warning("working_memory_read_denied", ...)
            return None
        return mem.content
```

**Lazy eviction**: Instead of a background cleanup task, expired entries are removed on the next read. This keeps the implementation simple. For production, add a background task that periodically scans for expired entries.

**Production upgrade**: Replace `dict` with Redis:
```python
await redis.setex(
    name=f"working:{session_id}:{key}",
    time=ttl_seconds,
    value=json.dumps(content)
)
```

---

## 6. Episodic Memory — Tag Index

```python
class EpisodicMemory:
    def __init__(self):
        self._store: dict[str, MemoryObject] = {}
        self._index: dict[str, list[str]] = {}  # tag → [memory_ids]

    def store(self, content, agent_id, tags, ...):
        mem = MemoryObject(...)
        self._store[mem.memory_id] = mem
        for tag in tags:
            self._index.setdefault(tag, []).append(mem.memory_id)

    def search_by_tags(self, tags, agent_id):
        # Union of all memories with any of the tags
        candidate_ids = set()
        for tag in tags:
            candidate_ids.update(self._index.get(tag, []))
        
        return [
            self._store[mid] for mid in candidate_ids
            if not self._store[mid].is_expired()
            and self._store[mid].access_control.can_read(agent_id)
        ]
```

**Example use case** — cache costly analysis across sessions:
```python
# Session 1: finance agent runs expensive ACME analysis
memory.store_episodic(
    content={"company": "ACME", "period": "Q3-2024", "report": "..."},
    agent_id="finance-agent",
    tags=["ACME", "finance", "Q3", "2024"],
    ttl_seconds=604800,  # 1 week
    readable_by=["*"],   # any agent
)

# Session 2 (next day): user asks about ACME again
cached = memory.search_episodic(["ACME", "Q3"], "planner-agent")
if cached:
    # Use cached result, skip expensive DB query
    return cached[0].content
```

---

## 7. Semantic Memory — Vector Search

```python
class SemanticMemory:
    def search(self, query, agent_id, top_k=5):
        """
        Production: embed query with SentenceTransformer,
        use ANN search (HNSW) in Qdrant/FAISS.
        
        Dev: keyword overlap scoring for zero-dependency operation.
        """
        query_terms = set(query.lower().split())
        scored = []
        for mem in self._store:
            if not mem.access_control.can_read(agent_id):
                continue
            tag_text = " ".join(mem.tags).lower()
            content_text = json.dumps(mem.content).lower()
            combined = f"{tag_text} {content_text}"
            overlap = sum(1 for term in query_terms if term in combined)
            if overlap > 0:
                score = overlap / max(len(query_terms), 1)
                scored.append((score, mem))
        return [m for _, m in sorted(scored, reverse=True)[:top_k]]
```

**Production upgrade — vector search:**
```python
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient

encoder = SentenceTransformer("all-MiniLM-L6-v2")
qdrant = QdrantClient("localhost", port=6333)

def index_semantic(content, tags):
    text = f"{' '.join(tags)} {json.dumps(content)}"
    vector = encoder.encode(text).tolist()
    qdrant.upsert(collection_name="agent_memory", points=[
        PointStruct(id=str(uuid.uuid4()), vector=vector, payload=content)
    ])

def search_semantic(query, top_k=5):
    query_vector = encoder.encode(query).tolist()
    results = qdrant.search(
        collection_name="agent_memory",
        query_vector=query_vector,
        limit=top_k,
    )
    return [r.payload for r in results]
```

---

## 8. Memory Manager — Unified Interface

```python
# All agents use MemoryManager — never access tiers directly
memory = MemoryManager()

# Working: fast in-session data sharing
memory.write_working("result:acme", data, agent_id="finance-agent",
                     readable_by=["planner-agent"])
cached = memory.read_working("result:acme", agent_id="planner-agent")

# Episodic: cross-session caching with tags
memory.store_episodic(data, agent_id="finance-agent", tags=["ACME", "Q3"])
hits = memory.search_episodic(["ACME"], agent_id="planner-agent")

# Semantic: knowledge base
memory.index_semantic(fact, agent_id="research-agent", tags=["cloud", "AI"])
results = memory.search_semantic("cloud enterprise AI", agent_id="any-agent")

# Stats
print(memory.stats())
# {'working': 3, 'episodic': 12, 'semantic': 150}
```

---

## 9. Memory TTL Guidelines

| Use Case | Tier | TTL |
|----------|------|-----|
| In-flight task data | Working | 1 hour |
| Session context | Working | 4 hours |
| Recent analysis results | Episodic | 1 week |
| User conversation history | Episodic | 30 days |
| Company facts | Semantic | 1 year |
| Static knowledge | Semantic | Indefinite |
