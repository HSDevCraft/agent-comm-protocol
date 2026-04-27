"""
Design Patterns for multi-agent systems.
Each pattern addresses a distinct architectural challenge.
"""
from src.patterns.router_agent import RouterAgent
from src.patterns.planner_executor import PlannerAgent, ExecutorAgent, PlannerExecutorSystem
from src.patterns.swarm import SwarmCoordinator, SwarmAgent

__all__ = [
    "RouterAgent",
    "PlannerAgent",
    "ExecutorAgent",
    "PlannerExecutorSystem",
    "SwarmCoordinator",
    "SwarmAgent",
]
