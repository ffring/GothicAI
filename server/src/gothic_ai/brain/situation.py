"""What the decision model is asked: the npc's state as English text, and typed questions.

The state is English (the model loses ~10 pp on Russian); the player's words stay Russian inside it.
Options are generic actions every npc can take; `apply.py` turns the chosen one into game actions.
Npc-specific menus (trader, guard, smith) and voiced SVM lines are the next step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .stimuli import Stimulus

GUILD_ROLES = {
    0: "a commoner without a guild",
    1: "a paladin of the king",
    2: "a militiaman of Khorinis",
    3: "a citizen of Khorinis",
    4: "a Fire Mage",
    5: "a novice of the monastery",
    6: "a dragon hunter",
    7: "a mercenary at Onar's farm",
    8: "a farmer",
    9: "a bandit",
    10: "a prisoner",
    11: "a Seeker",
    12: "a settler from the outskirts",
    13: "a pirate",
    14: "a Water Mage",
}

REACT = "What do you do right now?"
REACT_OPTIONS = {
    "continue": "keep doing what you were doing",
    "look_at": "turn and look at the hero",
    "greet": "nod or greet the hero briefly",
    "approach_talk": "walk up to the hero and start a conversation",
    "comment": "say a short remark about what just happened",
    "step_away": "step away from the hero",
}
# extra options that only make sense after a given event
EVENT_OPTIONS = {
    "item_given": {"thank": "thank the hero for the gift", "give_back": "give the item back to the hero"},
    "enter_room": {"tell_to_leave": "tell the hero to get out of your house"},
    "item_dropped": {"pick_up": "pick up the item"},
    "monster_near": {"call_help": "shout for help"},
    "monster_attack": {"call_help": "shout for help"},
    "npc_attack": {"call_help": "shout for help"},
    "fight_nearby": {"call_guards": "shout for the guards"},
}

ATTITUDE = "How does what just happened change your attitude toward the hero?"
ATTITUDE_LEVELS = [
    "much worse (a serious offense, a threat, a crime against you)",
    "slightly worse (rude, suspicious or annoying)",
    "unchanged",
    "slightly better (polite, a small favor, good business)",
    "much better (saved you, a big gift, a great service)",
]

# Whether the moment deserves the large model: the npc speaking in its own words and thinking ahead. The
# planner decides by rules whether to call it (the hero sees it, budget, cooldown); this only ranks moments.
ESCALATE = (
    "How much does this moment call for you to speak in your own words and think about what to do next, "
    "beyond stock phrases and simple actions?"
)
ESCALATE_LEVELS = [
    "routine: doing nothing or a simple action is enough",
    "a stock phrase or a gesture covers it",
    "worth a short remark of your own, nothing more",
    "calls for real speech in your own words: shouting, pleading, arguing, explaining, or personal business "
    "with the hero (news, a debt, a promise, a grudge, gratitude)",
    "a turning point for you: danger to your life, a big loss or gain, something about your goals; "
    "you need to speak and to plan what to do next",
]

PLAN = "What do you do during the next hour?"
PLAN_OPTIONS = {
    "keep_routine": "keep to your usual daily routine",
    "rest": "take a break and rest",
    "socialize": "go and chat with people nearby",
}


@dataclass(frozen=True)
class NpcContext:
    id: str
    name: str
    guild: int = 0
    distance_cm: float = 0.0
    in_view: bool = False
    traits: str | None = None  # one line of character, e.g. "Stern, loyal to the king."
    agenda: str | None = None  # what it wants from the player
    facts: tuple[str, ...] = field(default_factory=tuple)  # things it remembers about the hero


@dataclass(frozen=True)
class Situation:
    state: str
    questions: dict[str, dict]


class SituationBuilder:
    def __init__(self, traits: dict[str, str] | None = None) -> None:
        self.traits = traits or {}

    def who(self, npc: NpcContext) -> str:
        role = GUILD_ROLES.get(npc.guild, "someone")
        traits = npc.traits or self.traits.get(npc.id)
        return f"You are {npc.name}, {role}." + (f" {traits}" if traits else "")

    def react(self, npc: NpcContext, stimuli: list[Stimulus], now: float, clock: str | None) -> Situation:
        parts = [self.who(npc)]
        if clock:
            parts.append(f"It is {clock}.")
        seen = ", looking at you" if npc.in_view else ""
        parts.append(f"The hero is {npc.distance_cm / 100:.0f} m away{seen}.")
        parts += [s.describe(now) for s in stimuli]
        if npc.agenda:
            parts.append(f"What you want: {npc.agenda}")
        if npc.facts:
            parts.append("What you remember: " + " ".join(npc.facts))
        options = dict(REACT_OPTIONS)
        for s in stimuli:
            options.update(EVENT_OPTIONS.get(s.kind, {}))
        return Situation(
            " ".join(parts), {"react": {"type": "choice", "instructions": REACT, "criteria": options}}
        )

    def attitude(self, react: Situation) -> Situation:
        q = {"attitude": {"type": "score", "instructions": ATTITUDE, "criteria": ATTITUDE_LEVELS}}
        return Situation(react.state, q)

    def escalate(self, react: Situation) -> Situation:
        q = {"escalate": {"type": "score", "instructions": ESCALATE, "criteria": ESCALATE_LEVELS}}
        return Situation(react.state, q)

    def plan(self, npc: NpcContext, clock: str | None) -> Situation:
        parts = [self.who(npc)]
        if clock:
            parts.append(f"It is {clock}.")
        if npc.facts:
            parts.append("What you remember: " + " ".join(npc.facts))
        q = {"plan": {"type": "choice", "instructions": PLAN, "criteria": dict(PLAN_OPTIONS)}}
        return Situation(" ".join(parts), q)


def clock_text(hour: int, minute: int) -> str:
    part = (
        "night"
        if hour < 5 or hour >= 22
        else "morning"
        if hour < 12
        else "afternoon"
        if hour < 18
        else "evening"
    )
    return f"{hour:02d}:{minute:02d}, {part}"
