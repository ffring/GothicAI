"""The npc "brain" outside conversations: which npc thinks when, and what it decides.

A decision model (gothic-decider service, Strands Decider on the GPU) picks reactions; the planner
(`NpcPlanner`) decides who to ask and when, by tier, events and deadlines. Design: docs/npc-scheduler.md.
"""

from .apply import Applier, Decision, GameApplier, LogApplier
from .decider import Decider, DeciderClient, Outcome
from .planner import NpcPlanner, PlannerConfig
from .tiers import Tier

__all__ = [
    "Applier",
    "Decider",
    "DeciderClient",
    "Decision",
    "GameApplier",
    "LogApplier",
    "NpcPlanner",
    "Outcome",
    "PlannerConfig",
    "Tier",
]
