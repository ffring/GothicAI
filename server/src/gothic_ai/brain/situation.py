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

# The wording is short on purpose: every question is read by the model in full, with the state, so its cost
# grows with its length (the old, longer wording took 110-190 tokens a question, these 70-110). Shortened
# in g7 (decider-service/tuning); the meaning, and so the labels, stayed the same.
REACT = "What do you do now?"
REACT_OPTIONS = {
    "continue": "keep doing what you do",
    "look_at": "look at the hero",
    "greet": "greet the hero",
    "approach_talk": "go talk to the hero",
    "comment": "remark on what happened",
    "step_away": "step away from the hero",
}
# extra options that only make sense after a given event
EVENT_OPTIONS = {
    "item_given": {"thank": "thank the hero", "give_back": "give it back"},
    "enter_room": {"tell_to_leave": "tell the hero to get out"},
    "item_dropped": {"pick_up": "pick it up"},
    # threats from others: the body fights or flees with the game's own combat (npc_act fight / flee)
    "monster_near": {"call_help": "shout for help", "flee": "run away", "fight": "fight it"},
    "npc_attack": {"call_help": "shout for help", "flee": "run away", "fight": "fight back"},
    "fight_nearby": {"call_guards": "call the guards", "fight": "join the fight", "flee": "get away"},
}

# and those that only make sense when the hero did it
BY_HERO_OPTIONS = {
    "monster_killed": {"thank": "thank the hero"},
}

ATTITUDE = "How does this change your attitude to the hero?"
ATTITUDE_LEVELS = [
    "much worse: offense, threat, crime",
    "slightly worse: rude, suspicious",
    "unchanged",
    "slightly better: polite, small favor",
    "much better: saved you, big gift",
]

# Whether the moment deserves the large model: the npc speaking in its own words and thinking ahead. The
# planner decides by rules whether to call it (the hero sees it, budget, cooldown); this only ranks moments.
ESCALATE = "How much does this call for your own words and a plan?"
ESCALATE_LEVELS = [
    "nothing or a simple action",
    "a stock phrase or gesture",
    "a short remark of your own",
    "real speech: shout, plead, argue, or personal business with the hero (news, debt, grudge, thanks)",
    "a turning point: danger to life, big loss or gain; speak and plan",
]

# Two acquaintances near the hero (gossip.py): does the one who knows of his lie tell the other now. Asked
# in shadow: the rumor's lot decides, the answer is only traced.
TALK_TO = "Do you tell {name} this news now?"

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
    view: tuple[str, ...] = field(default_factory=tuple)  # what it perceives, the most telling first


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
        parts += npc.view
        parts += [s.describe(now) for s in stimuli]
        if npc.agenda:
            parts.append(f"What you want: {npc.agenda}")
        if npc.facts:
            parts.append("What you remember: " + " ".join(npc.facts))
        options = dict(REACT_OPTIONS)
        for s in stimuli:
            options.update(EVENT_OPTIONS.get(s.kind, {}))
            if s.other == "PLAYER":
                options.update(BY_HERO_OPTIONS.get(s.kind, {}))
        return Situation(
            " ".join(parts), {"react": {"type": "choice", "instructions": REACT, "criteria": options}}
        )

    def attitude(self, react: Situation) -> Situation:
        q = {"attitude": {"type": "score", "instructions": ATTITUDE, "criteria": ATTITUDE_LEVELS}}
        return Situation(react.state, q)

    def escalate(self, react: Situation) -> Situation:
        q = {"escalate": {"type": "score", "instructions": ESCALATE, "criteria": ESCALATE_LEVELS}}
        return Situation(react.state, q)

    def talk_to(self, npc: NpcContext, clock: str | None, other: str, other_m: float, news: str) -> Situation:
        """Whether to pass `news` (news_text) on to `other`, who stands `other_m` metres away, now."""
        parts = [self.who(npc)] + ([f"It is {clock}."] if clock else [])
        parts.append(f"{other} stands {other_m:.0f} m from you.")
        seen = ", looking at you" if npc.in_view else ""
        parts.append(f"The hero is {npc.distance_cm / 100:.0f} m away{seen}.")
        parts += npc.view
        parts.append(f"News you know and {other} does not: {news}")
        criteria = {"tell": f"tell {other} now", "keep": "keep it to yourself for now"}
        q = {"talk_to": {"type": "choice", "instructions": TALK_TO.format(name=other), "criteria": criteria}}
        return Situation(" ".join(parts), q)

    def plan(self, npc: NpcContext, clock: str | None, options: dict[str, str] | None = None) -> Situation:
        """What to do in the next hour: the npc's own activities and habits (brain/day.py), or generic
        options when its routine is not known."""
        parts = [self.who(npc)]
        if clock:
            parts.append(f"It is {clock}.")
        if npc.agenda:
            parts.append(f"What you want: {npc.agenda}")
        if npc.facts:
            parts.append("What you remember: " + " ".join(npc.facts))
        criteria = dict(options) if options else dict(PLAN_OPTIONS)
        q = {"plan": {"type": "choice", "instructions": PLAN, "criteria": criteria}}
        return Situation(" ".join(parts), q)


def news_text(kind: str, about_name: str, title: str, *, about_you: bool = False) -> str:
    """A rumor of the hero (social.Rumor: kind, about_name, title) in English, for talk_to. The title stays
    as the game has it. `about_you`: the one asked is the one he lied to."""
    whom = "you" if about_you else about_name
    if kind == "broken":
        return f"the hero did not pay back {whom} on time: {title}"
    if kind == "found_out":
        return f"the hero lied to {whom}: he took a reward for '{title}' without doing it"
    return f"the hero lied to {whom}: he claimed '{title}', and it was false"


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
