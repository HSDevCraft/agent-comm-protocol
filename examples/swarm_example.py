"""
Example: Agent Swarm + Planner-Executor + Router Patterns

Demonstrates:
  1. SwarmCoordinator dispatching to 4 parallel agents (MERGE strategy)
  2. PlannerExecutorSystem decomposing a complex research query
  3. RouterAgent classifying and routing customer queries
  4. ANP decentralized agent discovery across organizations
  5. Full observability output with confidence scores and trace trees

Run:
    python examples/swarm_example.py
"""

from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

from src.agent import AgentConfig
from src.memory import MemoryManager
from src.observability import ObservabilityEngine
from src.patterns.planner_executor import ExecutorAgent, PlannerExecutorSystem
from src.patterns.router_agent import RoutingRule, RouterAgent, build_customer_service_router
from src.patterns.swarm import AggregationStrategy, SwarmAgent, SwarmCoordinator, build_research_swarm
from src.protocols.a2a import AgentCard, AgentRegistry
from src.protocols.anp import ANPAgent, ANPClient, DIDDocument, VerifiableCredential
from src.security import AgentRole, SecurityGateway

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


async def demo_swarm_merge() -> None:
    print("\n" + "="*65)
    print("DEMO 1: Agent Swarm (4 agents, MERGE aggregation)")
    print("="*65)

    swarm = build_research_swarm(n_agents=4, strategy=AggregationStrategy.MERGE)

    result = await swarm.run_swarm(
        query="What are the key trends in enterprise AI adoption for 2024?",
    )

    print(f"\n  Swarm ID:           {result.swarm_id}")
    print(f"  Total agents:       {result.total_agents}")
    print(f"  Successful agents:  {result.successful_agents}")
    print(f"  Failed agents:      {result.failed_agents}")
    print(f"  Aggregated confidence: {result.aggregated_output.get('aggregated_confidence', 0):.2%}")
    print(f"  Duration:           {result.duration_ms:.1f}ms")
    print(f"  Strategy:           {result.strategy.value}")

    print("\n  Individual agent results:")
    for agent_result in result.individual_results:
        out = agent_result.output
        print(f"    [{agent_result.agent_id}] confidence={out.get('confidence', 0):.2f} | {str(out.get('result', ''))[:60]}")

    print(f"\n  Swarm coordinator stats: {swarm.swarm_stats()}")


async def demo_swarm_best_confidence() -> None:
    print("\n" + "="*65)
    print("DEMO 2: Agent Swarm (3 agents, BEST_CONFIDENCE aggregation)")
    print("="*65)

    swarm = build_research_swarm(n_agents=3, strategy=AggregationStrategy.BEST_CONFIDENCE)

    result = await swarm.run_swarm(
        query="Summarize the impact of RAG systems on enterprise LLM deployment",
    )

    print(f"\n  Winner agent: {result.aggregated_output.get('source', 'unknown')}")
    print(f"  Confidence:   {result.aggregated_output.get('confidence', 0):.2%}")
    print(f"  All agents ran in: {result.duration_ms:.1f}ms")


async def demo_planner_executor() -> None:
    print("\n" + "="*65)
    print("DEMO 3: Planner-Executor Pattern (research query decomposition)")
    print("="*65)

    registry = AgentRegistry()
    memory = MemoryManager()
    security = SecurityGateway()
    obs = ObservabilityEngine()

    executor_configs = [
        AgentConfig(
            agent_id="search-executor",
            name="SearchExecutor",
            version="1.0.0",
            role=AgentRole.SPECIALIST,
            capabilities=["web_search", "information_retrieval"],
            description="Executes web search tasks",
        ),
        AgentConfig(
            agent_id="analysis-executor",
            name="AnalysisExecutor",
            version="1.0.0",
            role=AgentRole.SPECIALIST,
            capabilities=["data_analysis", "statistical_analysis"],
            description="Performs data analysis tasks",
        ),
        AgentConfig(
            agent_id="general-executor",
            name="GeneralExecutor",
            version="1.0.0",
            role=AgentRole.SPECIALIST,
            capabilities=["general", "summarization", "text_generation"],
            description="General purpose executor for misc tasks",
        ),
        AgentConfig(
            agent_id="finance-executor",
            name="FinanceExecutor",
            version="2.0.0",
            role=AgentRole.SPECIALIST,
            capabilities=["financial_analysis", "risk_assessment"],
            description="Specialized financial analysis executor",
        ),
    ]

    system = PlannerExecutorSystem.build(
        executor_configs=executor_configs,
        registry=registry,
        memory=memory,
        security=security,
        observability=obs,
    )

    print(f"\n  Built system with {len(system.executors)} executors")
    print(f"  Executor capabilities:")
    for ex in system.executors:
        print(f"    [{ex.agent_id}] → {ex.config.capabilities}")

    queries = [
        "Research and analyze the latest trends in quantum computing for enterprises",
        "Analyze the financial performance and risk profile of the tech sector in Q3 2024",
    ]

    for query in queries:
        print(f"\n  Query: {query[:70]}")
        result = await system.run(query)
        plan_id = result.output.get("plan_id", "unknown")
        sub_tasks = result.output.get("total_sub_tasks", 0)
        failed = result.output.get("failed_sub_tasks", 0)
        print(f"    Plan ID:     {plan_id}")
        print(f"    Sub-tasks:   {sub_tasks} total, {failed} failed")
        print(f"    Success:     {result.success}")
        print(f"    Duration:    {result.duration_ms:.1f}ms")
        sub_results = result.output.get("sub_task_results", {})
        for step_id, step_result in sub_results.items():
            preview = str(step_result)[:80] if step_result else "no result"
            print(f"    [{step_id}]: {preview}")


async def demo_router_agent() -> None:
    print("\n" + "="*65)
    print("DEMO 4: Router Agent (customer service triage)")
    print("="*65)

    registry = AgentRegistry()
    memory = MemoryManager()
    security = SecurityGateway()
    obs = ObservabilityEngine()

    specialist_configs = [
        ("billing-agent", "BillingAgent", ["billing_support", "invoice_handling"]),
        ("tech-agent", "TechAgent", ["technical_support", "bug_triage"]),
        ("legal-agent", "LegalAgent", ["legal_review", "compliance_check"]),
        ("data-agent", "DataAgent", ["data_analysis", "report_generation"]),
        ("general-agent", "GeneralAgent", ["general", "faq"]),
    ]

    for agent_id, name, caps in specialist_configs:
        card = AgentCard(
            agent_id=agent_id,
            name=name,
            version="1.0.0",
            description=f"Specialist: {', '.join(caps)}",
            capabilities=caps,
            endpoint=f"http://localhost/agents/{agent_id}",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        registry.register(card)

    router = build_customer_service_router(registry=registry, memory_manager=memory, security_gateway=security, observability_engine=obs)

    test_queries = [
        "I haven't received my invoice for last month's subscription",
        "The application keeps crashing when I try to export data",
        "I need to review the GDPR compliance terms in our contract",
        "Can you generate a dashboard report for Q3 sales?",
        "How do I reset my password?",
        "There's a bug in the mobile app — it shows a 500 error",
    ]

    print(f"\n  Router has {len(router._rules)} routing rules")

    for query in test_queries:
        matched_rule = router.classify(query)
        target = matched_rule.target_agent_id if matched_rule else "no-match"
        rule_id = matched_rule.rule_id if matched_rule else "none"
        print(f"\n  Query: {query[:60]}")
        print(f"    → Rule: {rule_id:<20} Target: {target}")

    print(f"\n  Running full delegation for: '{test_queries[0]}'")
    result = await router.run(query=test_queries[0])
    print(f"  Result: success={result.success} | protocol={result.protocol_used}")
    print(f"  Output: {str(result.output)[:150]}")
    print(f"  Routing stats: {router.routing_stats()}")


async def demo_anp_decentralized() -> None:
    print("\n" + "="*65)
    print("DEMO 5: ANP — Decentralized Agent Network")
    print("="*65)

    anp = ANPClient()
    org_did = "did:web:identity.acme.com"

    agents_data = [
        ("agents.acme.com", "finance-agent", "ACME Finance Agent", ["financial_analysis", "risk_assessment"]),
        ("agents.acme.com", "legal-agent", "ACME Legal Agent", ["legal_review", "compliance_check"]),
        ("agents.partner.org", "data-agent", "Partner Data Agent", ["data_analysis", "reporting"]),
        ("agents.external.ai", "research-agent", "External Research Agent", ["research", "web_search"]),
    ]

    created_agents = []
    for domain, name, display, caps in agents_data:
        did_doc = DIDDocument.create(domain, name, org_did)
        anp_agent = ANPAgent(
            did_document=did_doc,
            display_name=display,
        )

        vc = VerifiableCredential(
            credential_id=f"vc-{name}",
            issuer_did=org_did,
            subject_did=did_doc.did,
            capabilities=caps,
        )
        anp_agent.credentials.append(vc)
        anp.register_agent(anp_agent)
        created_agents.append(anp_agent)
        print(f"  Registered: {did_doc.did}")

    print(f"\n  Network topology:")
    topology = anp.network_topology()
    print(f"    Total agents: {topology['total_agents']}")
    for agent_info in topology["agents"]:
        print(f"    [{agent_info['did'].split(':')[-1]}] endpoint={agent_info['endpoint']}")

    capabilities_to_discover = ["financial_analysis", "data_analysis", "research"]
    print("\n  Capability discovery (cross-org):")
    for cap in capabilities_to_discover:
        found = anp.discover_agents_by_capability(cap)
        agent_names = [a.display_name for a in found]
        print(f"    '{cap}' → {agent_names}")

    sender = created_agents[0]
    receiver = created_agents[2]
    print(f"\n  Sending ANP message:")
    print(f"    From: {sender.did}")
    print(f"    To:   {receiver.did}")

    message = await anp.send_message(
        from_agent=sender,
        to_did=receiver.did,
        message_type="CAPABILITY_REQUEST",
        payload={
            "capability": "data_analysis",
            "task": "Analyze ACME Corp Q3 data",
            "requester_org": "acme.com",
        },
    )

    print(f"    Message ID: {message.message_id}")
    print(f"    Signature valid: {message.verify_signature()}")
    print(f"    Payload: {message.payload}")

    inbox = anp.get_messages_for(receiver.did)
    print(f"\n  {receiver.display_name} inbox: {len(inbox)} message(s)")

    did_doc = anp.resolve_did(sender.did)
    print(f"\n  DID Resolution for {sender.did}:")
    print(f"    Controller: {did_doc.controller}")
    print(f"    Services: {[s.type for s in did_doc.services]}")
    endpoint = did_doc.resolve_endpoint("AgentEndpoint")
    print(f"    AgentEndpoint: {endpoint}")


async def demo_full_pipeline() -> None:
    print("\n" + "="*65)
    print("DEMO 6: Full Pipeline — User → Planner → Swarm → Memory → Response")
    print("="*65)
    print("""
  Execution flow:
    1. User query arrives at PlannerAgent [ACP dispatch]
    2. PlannerAgent decomposes into 3 sub-tasks
    3. Sub-task 1 (search) → SearchExecutor [A2A]
    4. Sub-task 2 (analysis) → AnalysisExecutor [A2A]
    5. Sub-task 3 (report) → SwarmCoordinator [A2A → 3 swarm agents]
    6. Results stored in SAMEP working memory
    7. PlannerAgent aggregates all results
    8. Final response returned to user
    """)

    registry = AgentRegistry()
    memory = MemoryManager()
    security = SecurityGateway()
    obs = ObservabilityEngine()

    executor_configs = [
        AgentConfig(
            agent_id="search-exec",
            name="SearchExecutor",
            version="1.0.0",
            role=AgentRole.SPECIALIST,
            capabilities=["web_search", "information_retrieval", "research"],
        ),
        AgentConfig(
            agent_id="analysis-exec",
            name="AnalysisExecutor",
            version="1.0.0",
            role=AgentRole.SPECIALIST,
            capabilities=["data_analysis", "financial_analysis", "general"],
        ),
    ]

    system = PlannerExecutorSystem.build(
        executor_configs=executor_configs,
        registry=registry,
        memory=memory,
        security=security,
        observability=obs,
    )

    query = "Research and analyze the financial impact of AI adoption in enterprise software companies in 2024"
    print(f"  Query: {query}\n")

    result = await system.run(query=query, session_id="full-pipeline-demo")

    print(f"  Pipeline complete!")
    print(f"  Success:        {result.success}")
    print(f"  Sub-tasks:      {result.output.get('total_sub_tasks', 0)}")
    print(f"  Failed:         {result.output.get('failed_sub_tasks', 0)}")
    print(f"  Duration:       {result.duration_ms:.1f}ms")
    print(f"  Protocol:       {result.protocol_used}")
    print(f"  Memory stats:   {memory.stats()}")

    print("\n  Decision log summary:")
    log_summary = obs.decision_log.summary()
    for key, value in log_summary.items():
        print(f"    {key}: {value}")


async def main() -> None:
    print("\n" + "🤖 "*22)
    print("  AGENT SWARM + PATTERNS — COMPLETE DEMONSTRATION")
    print("🤖 "*22)

    await demo_swarm_merge()
    await demo_swarm_best_confidence()
    await demo_planner_executor()
    await demo_router_agent()
    await demo_anp_decentralized()
    await demo_full_pipeline()

    print("\n" + "✅ "*22)
    print("  All pattern demonstrations completed!")
    print("✅ "*22 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
