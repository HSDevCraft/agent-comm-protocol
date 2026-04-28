# Design Patterns: Deep Dive

## Pattern 1: Router Agent

### Intent
A single entry-point agent that classifies incoming requests and routes them to specialist agents — without performing any domain logic itself.

### When to Use
- Customer service triage (billing, technical, legal, data requests)
- API gateway for a multi-agent backend
- When you want to add new specialists without changing the caller

### Structure
```
User Query
    │
    ▼
RouterAgent
    │ classify(query) → RoutingRule
    ├──[A2A]──► BillingAgent     (rule: "invoice", "payment", "refund")
    ├──[A2A]──► TechAgent        (rule: "error", "bug", "crash")
    ├──[A2A]──► LegalAgent       (rule: "contract", "GDPR", "compliance")
    ├──[A2A]──► DataAgent        (rule: "report", "dashboard", "analytics")
    └──[A2A]──► GeneralAgent     (fallback: no rule matched)
```

### Implementation

```python
from src.patterns.router_agent import RouterAgent, RoutingRule

router = RouterAgent(
    config=AgentConfig(
        agent_id="router",
        name="Router",
        role=AgentRole.PLANNER,
        capabilities=["routing"],
        version="1.0.0",
    ),
    routing_rules=[
        RoutingRule(
            rule_id="billing-rule",
            keywords=["invoice", "billing", "payment", "charge", "refund"],
            target_agent_id="billing-agent",
            target_capability="billing_support",
            priority=9,
        ),
        RoutingRule(
            rule_id="tech-rule",
            keywords=["error", "bug", "crash", "not working", "500"],
            target_agent_id="tech-agent",
            target_capability="technical_support",
            priority=8,
        ),
    ],
    fallback_agent_id="general-agent",
)

result = await router.run("I haven't received my invoice for last month")
# Routes to billing-agent via A2A
```

### Key Design Decisions

1. **Rules are priority-ordered**: higher priority checked first. Prevents ambiguous queries from matching wrong rules.
2. **Fallback is mandatory**: always define a fallback to avoid "no route found" errors.
3. **Router never executes domain logic**: it's purely a traffic controller. Any domain logic in the router is a design smell.
4. **Match mode**: `"any"` (match if ANY keyword present) or `"all"` (match only if ALL keywords present).

### Performance Characteristics
- Routing decision: O(R × K) where R = rules, K = keywords per rule
- For 100 rules with 10 keywords each: ~1000 string comparisons = < 1ms
- For production with ML classifier: batch keywords into a single embedding lookup

---

## Pattern 2: Planner + Executor

### Intent
A PlannerAgent decomposes complex queries into ordered sub-tasks, dispatches them to ExecutorAgents, then aggregates results.

### When to Use
- Complex multi-step research tasks
- Tasks requiring specialized capabilities at each step
- When the plan structure itself is dynamic (varies per query)

### Structure
```
User Query
    │
    ▼
PlannerAgent (decomposes)
    │
    ├── SubTask 1: search      ──[A2A]──► SearchExecutor    ──[MCP]──► web_search
    │   (no deps)                                                           │
    │                                                                       ▼
    ├── SubTask 2: analysis    ──[A2A]──► AnalysisExecutor  ◄─── uses result 1
    │   (depends on 1)
    │
    └── SubTask 3: report      ──[A2A]──► ReportExecutor    ◄─── uses results 1+2
        (depends on 1 and 2)
```

### Decomposition Logic

```python
def _decompose(self, query: str, task_id: str) -> ExecutionPlan:
    # Simple rule-based (production: use LLM)
    if "research" in query.lower() or "analyze" in query.lower():
        sub_tasks = [
            SubTask(sub_task_id="search",   capability="web_search",    depends_on=[]),
            SubTask(sub_task_id="analyze",  capability="data_analysis", depends_on=["search"]),
            SubTask(sub_task_id="summarize",capability="general",       depends_on=["analyze"]),
        ]
    elif "finance" in query.lower():
        sub_tasks = [
            SubTask(sub_task_id="fetch_data", capability="financial_analysis", depends_on=[]),
            SubTask(sub_task_id="risk_check", capability="risk_assessment",    depends_on=[]),
            SubTask(sub_task_id="report",     capability="general",            depends_on=["fetch_data","risk_check"]),
        ]
    # ...
    return ExecutionPlan(plan_id=..., sub_tasks=sub_tasks, ...)
```

### LLM-Powered Decomposition (Production)

```python
async def _decompose_with_llm(self, query: str) -> ExecutionPlan:
    prompt = f"""
    Decompose this task into ordered sub-tasks. Output JSON.
    Task: {query}
    
    Available capabilities: {self._available_capabilities}
    
    Output format:
    {{
        "sub_tasks": [
            {{"id": "step-1", "capability": "web_search", "depends_on": []}},
            {{"id": "step-2", "capability": "data_analysis", "depends_on": ["step-1"]}}
        ],
        "reasoning": "why this decomposition"
    }}
    """
    response = await self._llm.complete(prompt)
    plan_data = json.loads(response)
    return ExecutionPlan(sub_tasks=[SubTask(**t) for t in plan_data["sub_tasks"]], ...)
```

### Parallel Execution

Steps without dependencies run in parallel:
```python
while not plan.is_complete():
    ready = plan.get_ready_steps()  # steps with all deps satisfied
    
    # Dispatch ALL ready steps simultaneously
    results = await asyncio.gather(
        *[self._execute_sub_task(step, context) for step in ready],
        return_exceptions=True,
    )
    
    # Update context with results for dependent steps
    for step, result in zip(ready, results):
        context[step.sub_task_id] = result
```

---

## Pattern 3: Agent Swarm

### Intent
N identical (or similar) agents tackle the same problem in parallel, and results are aggregated using a configurable strategy.

### When to Use
- High-confidence requirements (majority vote)
- Large-scale data processing (partition across agents)
- Ensemble reasoning (diverse perspectives)
- Redundancy for critical decisions

### Aggregation Strategies

| Strategy | Logic | Use case |
|----------|-------|---------|
| `MERGE` | Combine all results | Comprehensive research |
| `MAJORITY` | Most common result | Factual Q&A with disagreement risk |
| `BEST_CONFIDENCE` | Highest-confidence result | When one agent clearly has better data |
| `FIRST` | First successful result | Speed-critical, any answer is acceptable |
| `WEIGHTED_MERGE` | Confidence-weighted combination | Ensemble ML predictions |

```python
swarm = build_research_swarm(n_agents=5, strategy=AggregationStrategy.MAJORITY)
result = await swarm.run_swarm("What caused the 2008 financial crisis?")

print(f"Winner: {result.aggregated_output['source']}")
print(f"Confidence: {result.aggregated_output['confidence']:.0%}")
print(f"Agents: {result.successful_agents}/{result.total_agents} succeeded")
```

### When Swarms Don't Help
- Tasks with data dependencies (you can't parallelize step N if it needs step N-1)
- Tasks requiring a single authoritative answer (use specialist agent instead)
- Simple one-shot tool calls (overhead of spawning N agents > benefit)

---

## Pattern 4: Tool-Augmented Agent

### Intent
A single agent with a rich set of MCP tools — no delegation, but powerful because it can call any tool.

### When to Use
- Single-domain tasks that require many tool calls
- Coding assistants (search + execute + test)
- Research assistants (search + summarize + cite)

```python
mcp_server = build_default_mcp_server()
# Add custom tools
mcp_server.register_tool(ToolDefinition(
    name="code_executor",
    description="Execute Python code safely",
    input_schema={"type": "object", "required": ["code"],
                  "properties": {"code": {"type": "string"}}},
    required_scope="code:execute",
    handler=safe_code_executor,
))

agent = BaseAgent(
    config=AgentConfig(
        agent_id="coding-assistant",
        role=AgentRole.SPECIALIST,
        capabilities=["code_generation", "debugging"],
        mcp_scopes=["search:read", "files:read", "code:execute"],
    ),
    mcp_server=mcp_server,
)
```

---

## Pattern 5: Hybrid Enterprise System

### Intent
Combines all patterns in a production-grade enterprise deployment.

### Architecture
```
External User
    │ HTTPS
    ▼
API Gateway (rate limiting, auth)
    │
    ▼
RouterAgent ──[ACP]──► ACPOrchestrator
    │                       │ workflow dispatch
    │                       ▼
    │              ┌─── PlannerAgent
    │              │        │ A2A
    │              │   ┌────┴────┬──────────────┐
    │              │   ▼         ▼              ▼
    │              │ FinanceAgent LegalAgent  DataAgent
    │              │   │MCP       │MCP         │MCP
    │              │   ▼          ▼            ▼
    │              │ db_query  doc_search   web_search
    │              │
    │              └─── SwarmCoordinator ──► [N research agents]
    │
    └──[ANP]──► External Partner Agents (cross-org)
                    │DID
                    ▼
                Vetted suppliers, regulators, data vendors
```

### Sample orchestration code

```python
# 1. Router classifies the incoming enterprise request
router = build_customer_service_router(registry)
route_result = await router.run("Analyze ACME Corp's Q3 financials and legal exposure")

# 2. Planner decomposes the classified task
system = PlannerExecutorSystem.build(executor_configs=[
    AgentConfig("finance-exec", ..., capabilities=["financial_analysis"]),
    AgentConfig("legal-exec",   ..., capabilities=["legal_review"]),
    AgentConfig("report-exec",  ..., capabilities=["general"]),
])
result = await system.run("Analyze ACME Corp's Q3 financials and legal exposure")

# 3. High-priority decisions go through swarm for confidence
swarm = build_research_swarm(n_agents=3, strategy=AggregationStrategy.BEST_CONFIDENCE)
high_conf_result = await swarm.run_swarm("Critical: Is ACME in regulatory compliance?")

# 4. Cross-org: escalate to external compliance agent via ANP
if high_conf_result.confidence < 0.8:
    anp_result = await anp_client.send_message(
        from_agent=internal_agent,
        to_did="did:web:compliance.regulator.gov:check-agent",
        message_type="COMPLIANCE_CHECK",
        payload={"company": "ACME", "period": "Q3-2024"},
    )
```

---

## Pattern 6: Self-Healing Agent (Retry + Fallback + Observability)

### Intent
An agent that monitors its own execution, retries on transient failures, falls back to alternatives, and emits observability events — all transparently to the caller.

### Structure

```
Caller
  │
  ▼
SelfHealingAgent.run(query)
  │
  ▼ SecurityGateway.sanitize_input()
  │
  ▼ Check EpisodicMemory cache
  │   HIT → return cached result (record trace: "cache_hit")
  │
  ▼ MISS → FailureOrchestrator.execute()
            │
            ▼ CircuitBreaker.call(primary_agent)
            │   OPEN → skip to FallbackChain
            │
            ▼ RetryHandler.execute(primary_agent, backoff)
            │   success → return + record in EpisodicMemory
            │   all retries fail → FallbackChain
            │
            ▼ FallbackChain: [specialist, general, cached, static]
            │   first success → return (with degraded=True flag)
            │
            ▼ DLQ.enqueue() + HumanEscalationHook.escalate()
```

### Implementation

```python
from src.failure.handlers import FailureOrchestrator, RetryConfig, CircuitBreakerConfig, FallbackOption
from src.observability import ObservabilityEngine, DecisionType

obs = ObservabilityEngine()

class SelfHealingAgent:
    def __init__(self, agent_id: str, primary_fn, fallback_fns: list):
        self._fo = FailureOrchestrator(
            agent_id=agent_id,
            retry_config=RetryConfig(max_retries=3, base_delay_seconds=0.5),
            circuit_config=CircuitBreakerConfig(failure_threshold=5),
            fallback_options=[
                FallbackOption(name=name, fn=fn, description=desc)
                for name, fn, desc in fallback_fns
            ],
        )
        self._memory = MemoryManager()
        self._obs = obs

    async def run(self, query: str) -> dict:
        cache_key = f"result:{hash(query)}"
        
        cached = self._memory.read_working(cache_key, self._agent_id)
        if cached:
            self._obs.record_decision(
                agent_id=self._agent_id,
                decision="return_cached_result",
                decision_type=DecisionType.LOCAL_EXECUTION,
                confidence=cached.get("confidence", 0.9),
                reasoning="Cache hit in working memory",
            )
            return cached
        
        result = await self._fo.execute(
            self._primary_fn, query,
            operation="primary_analysis",
            message={"query": query},
        )
        
        self._memory.write_working(
            key=cache_key,
            content=result,
            agent_id=self._agent_id,
            readable_by=["planner-agent"],
            ttl_seconds=3600,
        )
        
        return result
```

---

## Pattern 7: Pattern Selection Guide

### Decision Framework

```
START: What is my use case?
         │
         ├─ Single domain, many tools, no delegation
         │    → Tool-Augmented Agent (Pattern 4)
         │
         ├─ Simple 1:1 domain handoff
         │    → Direct A2A delegation (no pattern needed)
         │
         ├─ Need to classify and route requests
         │    → Router Agent (Pattern 1)
         │
         ├─ Complex multi-step task, sequential dependencies
         │    → Planner + Executor (Pattern 2)
         │
         ├─ Need high confidence via consensus
         │    → Agent Swarm (Pattern 3)
         │
         ├─ Need resilience and self-healing
         │    → Self-Healing Agent (Pattern 6)
         │
         └─ Full enterprise system with all of the above
              → Hybrid Enterprise System (Pattern 5)
```

### Pattern Comparison Matrix

| Pattern | Agents | Parallelism | Complexity | Best For |
|---------|--------|-------------|-----------|---------|
| Tool-Augmented | 1 | Tools only | Low | Single expert domain |
| Router Agent | 1 + N specialists | Via A2A | Low-Medium | Classification/triage |
| Planner + Executor | 1 + N executors | asyncio.gather | Medium | Multi-step research |
| Agent Swarm | N identical | Full parallel | Medium | Consensus decisions |
| Self-Healing | 1 + fallbacks | Sequential fallback | Medium | Critical ops |
| Hybrid Enterprise | All | All types | High | Production systems |

### When NOT to Use Each Pattern

```
Tool-Augmented:   Don't use if you need multiple specialized domains
Router Agent:     Don't use if routing logic needs to learn/adapt
Planner+Executor: Don't use for simple 1-2 step tasks (overkill)
Agent Swarm:      Don't use if tasks have data dependencies
Self-Healing:     Don't use if you can tolerate failure (overengineered)
Hybrid:           Don't use for prototypes or PoCs (too complex)
```

---

## Pattern 8: Pattern Anti-Patterns

### Anti-Pattern 1: God Router (Putting Logic in the Router)

```python
# WRONG — router does domain work
class BadRouter:
    async def route(self, query: str) -> dict:
        if "invoice" in query:
            # Calculating invoice directly in router!
            return self._calculate_invoice(query)   # ← domain logic in router
        
# RIGHT — router only routes, never executes domain logic
class GoodRouter:
    async def route(self, query: str) -> dict:
        rule = self._classify(query)
        return await self._a2a_client.delegate(
            capability=rule.target_capability,
            input_data={"query": query},
        )
```

### Anti-Pattern 2: Monolithic Planner (All Logic in Decomposition)

```python
# WRONG — planner hardcodes all domain knowledge
class BadPlanner:
    def decompose(self, query):
        if "ACME" in query and "Q3" in query and "revenue" in query:
            return [step_1, step_2, step_3]   # ← every domain case in planner
        elif "contract" in query:
            return [legal_step_1, legal_step_2]
        # ... hundreds of cases

# RIGHT — planner uses LLM or general rules, not domain hardcoding
class GoodPlanner:
    async def decompose(self, query):
        return await self._llm.decompose(
            query=query,
            available_capabilities=self._registry.list_capabilities(),
        )
```

### Anti-Pattern 3: Swarm Without Aggregation Strategy

```python
# WRONG — swarm with no agreement strategy
class BadSwarm:
    async def run(self, query):
        results = await asyncio.gather(*[agent.run(query) for agent in self._agents])
        return results[0]    # ← just take the first — ignores other agents' insights

# RIGHT — choose the strategy based on the use case
class GoodSwarm:
    async def run(self, query):
        results = await asyncio.gather(*[agent.run(query) for agent in self._agents])
        if self.strategy == AggregationStrategy.MAJORITY:
            return self._majority_vote(results)
        elif self.strategy == AggregationStrategy.BEST_CONFIDENCE:
            return max(results, key=lambda r: r.get("confidence", 0))
        elif self.strategy == AggregationStrategy.MERGE:
            return self._merge_all(results)
```

### Anti-Pattern 4: Deep Nesting (Patterns Within Patterns Within Patterns)

```
WRONG:
RouterAgent → PlannerAgent → SwarmCoordinator → PlannerAgent → RouterAgent
(too many layers, hard to debug, high latency)

RIGHT:
RouterAgent → PlannerAgent → [SpecialistA, SpecialistB, SpecialistC] → ReportAgent
(flat, predictable, easy to trace)
```

**Rule**: Maximum 3 layers of agent hierarchy. More than 3 layers means you need to redesign the system, not add more layers.

---

## Pattern 9: Interview Questions: Design Patterns

**Q: "When would you use a Router Agent vs a Planner Agent?"**
> Router Agent: when the classification is simple (keyword/rule-based), tasks are independent, and each routed-to agent handles its own complete workflow. Planner Agent: when the task requires decomposition into ordered sub-tasks with dependencies, requires parallel execution, and results need to be aggregated. Router is a traffic cop; Planner is a project manager.

**Q: "What are the risks of an Agent Swarm? When does it become counter-productive?"**
> Risks: N× resource consumption, aggregation complexity, consistency issues if agents disagree. Counter-productive when: tasks have data dependencies (result of A needed for B), a single authoritative source exists (swarm adds noise), or N × latency > acceptable response time. Use swarms only for consensus-critical or embarrassingly parallel workloads.

**Q: "How does the Planner+Executor pattern handle partial failures?"**
> The planner runs `asyncio.gather(return_exceptions=True)` so failures don't cancel successful parallel tasks. For each completed step, results are stored in context. For failed steps: retry with a different executor, continue with available results (mark step as degraded), or abort if the failed step is a dependency of remaining steps.

**Q: "How does the Hybrid Enterprise pattern prevent the system from becoming a monolith?"**
> Each layer is independent: Router classifies (no domain logic), Planner decomposes (no domain execution), Executors handle domain logic (no orchestration), Swarm provides redundancy (no state). Separation of concerns means you can swap out any component — replace the router's keyword rules with ML classification without touching any other layer.

**Q: "What's the difference between ACP workflow orchestration and the Planner+Executor pattern?"**
> They solve the same problem at different levels. Planner+Executor is a design pattern — an architectural choice for how agents decompose and dispatch work. ACP workflow is an implementation mechanism — a message-based protocol for async fan-out with TTL, retry policies, and correlation IDs. In production, the Planner+Executor pattern uses ACP as its communication backbone.
