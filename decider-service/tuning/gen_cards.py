"""Scene cards for fine-tuning the decider: gpt-6.1-sol writes Gothic situations around npc's.

A card is one moment in the life of one npc: who it is, where, what the hero just did (events of the
planner's own kinds) and a menu of what this npc could do, with voiced SVM lines from the game. Cards
become training questions in build_rows.py; their answers come from label.py, not from here.

Npc's of the eval set (evals/npc-decisions) never appear, so the eval stays held out.

    cd decider-service/tuning
    uv run --project ../../server python gen_cards.py --calls 40 [--per-call 8] [--jobs 3]
    uv run --project ../../server python gen_cards.py --calls 19 --emergency   # danger around the npc
    uv run --project ../../server python gen_cards.py --calls 10 --personal    # business with the hero
    uv run --project ../../server python gen_cards.py --calls 10 --danger      # a creature goes for the npc
    uv run --project ../../server python gen_cards.py --calls 15 --rescue      # ... and is killed

Appends to data/cards.jsonl; rerunning continues with new seeds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from pathlib import Path

from gothic_ai.gamedata.ou import find_ou_file, parse_ou_bin
from gothic_ai.llm import Done, LLMError, Message, make_backend

HERE = Path(__file__).parent
DATA = HERE / "data"
EVAL = HERE.parent.parent / "evals" / "npc-decisions" / "situations.yaml"
GAME_DIR = Path(os.environ.get("GOTHIC_DIR", "/Volumes/shared/projects/Gothic II"))

EVENT_KINDS = [
    "player_near", "player_speech", "enter_room", "item_given", "item_dropped", "player_sits",
    "player_sleeps", "weapon_drawn", "theft", "attack", "magic", "murder",
    "monster_near", "attacked_by", "fight_nearby", "cry_for_help", "monster_killed",
]  # fmt: skip

SETTINGS = [
    "Khorinis, the lower town: narrow streets, the smithy, craftsmen's houses (citizens, militia, beggars)",
    "Khorinis, the harbour district: fish stalls, the harbour tavern, the brothel, sailors' shacks",
    "Khorinis, the market square: traders' stalls, the hotel, militia on patrol",
    "Khorinis, the upper quarter: paladins, rich citizens' houses, the town hall",
    "Khorinis, the temple square: Vatras preaching, a few listeners, beggars",
    "Khorinis, a city gate: militiamen checking who comes in, farmers with carts",
    "Khorinis at night: closed shops, drunks, thieves, a militia night patrol",
    "Onar's farm: mercenaries lounging around, farmhands at work, Onar's house",
    "a small farm outside Khorinis: farmers, sheep, fields, a barn",
    "the monastery of the Fire Mages: novices at work, mages, the library, the wine cellar",
    "the Valley of Mines: the besieged castle, paladins, knights, miners, orcs nearby",
    "a road through the forest near Khorinis: bandits, hunters, travellers",
    "the hunters' camp and the outskirts: settlers, hunters, the tavern 'Dead Harpy'",
    "Jharkendar: the pirates' camp on the beach",
    "Jharkendar: the Water Mages' excavation site and its guards",
    "the bandits' camp in the swamp of Jharkendar",
    "a tavern: drinking, gambling, someone wants a fight",
]
THEMES = [
    "the hero only passes by, stands around or stares (often nothing needs to be done)",
    "the hero says something: a greeting, a question about the way, work or a person",
    "the hero says something rude, strange, provocative or threatening",
    "the hero wants to trade, sell, buy, bargain or asks for a favour",
    "the hero gives an item (valuable, cheap, odd, suspicious) or drops one",
    "the hero walks into a house, a room or a place where he should not be, by day or at night",
    "the hero draws a weapon, casts a spell or starts a fight",
    "the hero steals something or is caught near someone else's things",
    "the hero attacks or kills someone or something (a person, an animal, a monster)",
    "the npc wants something from the hero (an agenda) or remembers something about him",
    "the npc is busy (working, eating, sleeping, praying, guarding) when the hero does something",
    "an unusual situation that fits none of the usual patterns",
]

# danger and commotion that are not the hero's doing; the hero is somewhere around
EMERGENCY = [
    "a monster (wolf, scavenger, giant rat, goblin, bloodfly, lurker, warg...) runs at or attacks the npc",
    "bandits, orcs or a hostile person attack the npc or what it guards",
    "a fight between other people breaks out near the npc",
    "an accident or emergency: a fire, a fall, a collapsed drunk, a runaway sheep, a broken cart",
    "someone nearby shouts for help, screams or raises the alarm",
    "a minor scare that turns out harmless (a meatbug, a noise, a cat, a sleepwalker)",
]
EMERGENCY_NOTE = (
    " These moments are not caused by the hero: he is only somewhere around (1-25 m), sometimes watching, "
    "sometimes busy elsewhere, sometimes able to help. Use the events monster_near, attacked_by, "
    "fight_nearby and cry_for_help (a hero event may be added as a second one). Vary how dangerous it is: "
    "from harmless to deadly, and from a npc used to fighting to a frightened commoner."
)

# creatures as the Russian release names them (the bridge sends these names): English = Russian
MONSTERS_RU = {
    "wolf": "Волк", "young wolf": "Молодой волк", "black wolf": "Черный волк", "warg": "Варг",
    "scavenger": "Падальщик", "meadow scavenger": "Луговой падальщик", "giant rat": "Гигантская крыса",
    "young giant rat": "Молодая гигантская крыса", "swamp rat": "Болотная крыса", "molerat": "Крысокрот",
    "bloodfly": "Кровавая муха", "meatbug": "Мясной жук", "lurker": "Луркер", "shadowbeast": "Мракорис",
    "snapper": "Снеппер", "field raider": "Полевой хищник", "goblin": "Гоблин",
    "black goblin": "Черный гоблин", "skeleton": "Скелет", "zombie": "Зомби", "orc": "Орк",
    "orc warrior": "Орк-воин", "orc scout": "Орк-разведчик",
    "swampshark": "Болотная акула", "harpy": "Гарпия", "troll": "Тролль", "cave troll": "Пещерный тролль",
}  # fmt: skip

# a creature goes for the npc while the hero is close: how the game reports it (monster_near, attacked_by)
DANGER = [
    "a dangerous creature (wolf, warg, shadowbeast, orc, lurker, snapper...) runs at the npc",
    "a creature attacks the npc and bites or hits it",
    "a weak or harmless creature (meatbug, young giant rat, molerat, bloodfly...) comes at the npc",
    "a creature goes for the npc while it works, eats, sleeps or guards something",
]
# ... and is killed: by the hero (mostly), by someone else or by the npc itself
RESCUE = [
    "a creature ran at or attacked the npc, and the hero has just killed it",
    "a creature attacked the npc, and the hero killed it at the last moment",
    "a creature went for the npc, and a militiaman, hunter or mercenary nearby killed it",
    "a creature went for the npc, and the npc killed it itself",
    "the hero killed a creature nearby that was no danger to this npc (it attacked someone else or just "
    "prowled around)",
]
MONSTER_NOTE = (
    " The hero is close (1-15 m), mostly watching. Vary the npc (from a frightened commoner or old woman "
    "to a seasoned fighter), how dangerous the creature is and how close the npc came to harm. Name the "
    "creature with one of these, English = Russian release: {monsters}. Every monster event also gets "
    '"detail_ru": the creature\'s Russian name from this list, and every card "npc_ru": the npc\'s name '
    "as the Russian release spells it (Cyrillic)."
)
DANGER_NOTE = (
    " Use the events monster_near (it is coming at the npc) or attacked_by (it attacks the npc), with "
    "detail the creature; a hero event (player_near, player_speech) may be added as a second one."
    + MONSTER_NOTE
)
RESCUE_NOTE = (
    " Events in order: first the threat (monster_near or attacked_by, detail the creature), then "
    'monster_killed (detail the same creature, "by": "hero", "self" or who killed it, e.g. '
    '"a militiaman"); the scene is the moment right after the kill.' + MONSTER_NOTE
)

# the npc has its own reason to talk to the hero when he shows up
PERSONAL = [
    "the hero owes the npc money, a favour or an item, or broke a promise",
    "the npc owes the hero thanks, a reward or an apology for something he did earlier",
    "the npc has news, a rumour or a warning that concerns the hero",
    "the npc holds a grudge against the hero or is afraid of him because of something that happened",
    "the npc needs the hero's help with its own trouble (its goal, its family, its work)",
    "an old friend or acquaintance of the hero meets him again after a long time",
]
PERSONAL_NOTE = (
    " In every card the npc has personal business with the hero: put it into agenda and/or facts. The "
    "hero only shows up, passes by or says something small (player_near, player_speech, enter_room); "
    "mix in a few cards where the business is too minor or the moment is wrong to bring it up."
)

QUIET = (
    "Quiet moments only: the hero comes close, passes by, stands or waits nearby, sits or lies down near the "
    "npc, drops something, or talks to someone else within earshot. Use only the events player_near, "
    "player_sits, player_sleeps, item_dropped and player_speech (small talk, not addressed to this npc). "
    "Most npc's in such moments simply keep doing what they do or at most glance at the hero; some greet "
    "him or say something because of who they are or what they want."
)

INSTRUCTIONS = """\
You write training situations for a small model that decides how npc's of the game Gothic 2 (Night of
the Raven, the Russian release) react to the hero (the player) and to what happens around them. Each
situation is one moment in the life of one npc. Be faithful to Gothic 2's world, guilds and tone, and
make situations varied: some where the obvious answer is to do nothing, some where it is clearly not,
some genuinely ambiguous.
Reply with JSON only: {"cards": [ ... ]}."""

CARD_SPEC = """\
Each card:
{{
  "npc": "name (a Gothic 2 character or a fitting invented one; never one of: {excluded})",
  "guild": <int: {guilds}>,
  "traits": "one English line of character",
  "agenda": "what the npc wants from the hero, English, or null (most cards: null)",
  "facts": ["0-2 English sentences the npc remembers about the hero"],
  "hour": <0-23>, "minute": <0-59>,
  "distance_m": <1-25: how far the hero is now>, "in_view": <bool: the hero is looking at the npc>,
  "activity": "what the npc is doing, English",
  "events": [1-2 of {{"kind": one of {kinds}, "detail": ...}}],
  "scene": "2-5 English sentences in the second person, the npc's full view of the moment: who you are,
            where, what you are doing, what the hero just did or said. Consistent with all fields above.",
  "menu": {{"<key>": "<what the npc would do>", ...}}
}}
Events: player_speech detail = what the hero said, in Russian (keep it in Russian in the scene too);
item_given / item_dropped / theft detail = the item in English ("a bottle of wine"); attack / murder
detail = the victim ("Edda the cook", "a sheep"); monster_near / attacked_by detail = the attacker ("a wolf",
"two bandits"); fight_nearby detail = who fights ("two drunk sailors"); cry_for_help detail = who shouts
("a farmer by the field"); monster_killed detail = the creature, plus "by" = "hero", "self" (the npc killed
it) or who killed it; other kinds: detail null.
Menu: 4-7 options for this npc in this moment. Always include "continue" (keep doing what you were
doing). Mix sensible and wrong options, each a short plain action (under 12 words) in the same neutral
tone: never hint in the wording whether an option is right ("solely", "immediately", "for no reason").
Include 1-3 voiced stock lines from the catalogue below as
"svm_<name in lower case>": "say: \\"<the Russian line>\\"" (only lines that could plausibly fit).
Other keys: short snake_case verbs ("call_guards", "attack", "haggle", "walk_away", ...).

Voiced stock lines (name: Russian text):
{catalogue}"""

GUILDS = (
    "0 commoner, 1 paladin, 2 militia, 3 citizen, 4 fire mage, 5 novice, 6 dragon hunter, 7 mercenary, "
    "8 farmer, 9 bandit, 10 prisoner, 11 seeker, 12 settler, 13 pirate, 14 water mage"
)
SVM_SKIP = re.compile(
    r"^(GOLD_|SMALLTALK|AARGH|DEAD|NOLEARN|YOULEARNED|CHEERFRIEND|OOH0|ABS_|ADDON_WRONGARMOR|ADDON_NOARMOR|"
    r"AWAKE|OHMYHEAD|ENEMYKILLED|STUPIDBEASTKILLED|MONSTERKILLED|SHITNOGOLD|ITOOKYOURGOLD|ITAKEYOURWEAPON|"
    r"TOUGHGUY_|RUMFUMMLERDOWN|SHEEPATTACKERDOWN|THIEFDOWN|GOODVICTORY)"
)


def eval_npcs() -> list[str]:
    text = EVAL.read_text()
    names = set(re.findall(r"You are ([A-Z][a-z]+)", text)) | set(re.findall(r"Lord ([A-Z][a-z]+)", text))
    names.discard("Lord")
    return sorted(names)


def svm_catalogue(voice: int = 6) -> dict[str, str]:
    ou = find_ou_file(GAME_DIR)
    if ou is None:
        sys.exit(f"no OU.BIN under {GAME_DIR} (set GOTHIC_DIR)")
    lines = parse_ou_bin(ou.read_bytes())
    prefix = f"SVM_{voice}_"
    out = {}
    for key, text in sorted(lines.items()):
        if key.upper().startswith(prefix):
            name = key[len(prefix) :].upper()
            if not SVM_SKIP.match(name):
                out[name] = " ".join(text.split())
    return out


def prompt(
    rnd: random.Random,
    n: int,
    excluded: list[str],
    catalogue: dict[str, str],
    quiet: float,
    focus: str | None,
) -> tuple[str, dict]:
    setting = rnd.choice(SETTINGS)
    note = ""
    if focus == "emergency":
        themes, note = rnd.sample(EMERGENCY, 2), EMERGENCY_NOTE
    elif focus == "personal":
        themes, note = rnd.sample(PERSONAL, 2), PERSONAL_NOTE
    elif focus in ("danger", "rescue"):
        pool, note = (DANGER, DANGER_NOTE) if focus == "danger" else (RESCUE, RESCUE_NOTE)
        themes = rnd.sample(pool, 2)
        note = note.format(monsters=", ".join(f"{k} = {v}" for k, v in MONSTERS_RU.items()))
    elif rnd.random() < quiet:
        themes = [QUIET, rnd.choice(THEMES[9:11])]
    else:
        themes = rnd.sample(THEMES, 2)
    seed = {"setting": setting, "themes": themes}
    spec = CARD_SPEC.format(
        excluded=", ".join(excluded),
        guilds=GUILDS,
        kinds=", ".join(EVENT_KINDS),
        catalogue="\n".join(f"{k}: {v}" for k, v in catalogue.items()),
    )
    text = (
        f"Write {n} cards. Setting: {setting}. Mostly about: {themes[0]}; some about: {themes[1]}. "
        f"Different npc's, times of day and moods.{note}\n\n{spec}"
    )
    return text, seed


def valid(card: dict, excluded: set[str], catalogue: dict[str, str]) -> str | None:
    """Why the card is unusable, or None."""
    try:
        if any(w in excluded for w in re.findall(r"[A-Z][a-z]+", card["npc"])):
            return "eval npc"
        if not 0 <= int(card["guild"]) <= 14:
            return "guild"
        if not card["events"] or any(e["kind"] not in EVENT_KINDS for e in card["events"]):
            return "events"
        if any(e["kind"] == "monster_killed" and not e.get("by") for e in card["events"]):
            return "monster_killed without by"
        if any(e.get("detail_ru") and e["detail_ru"] not in MONSTERS_RU.values() for e in card["events"]):
            return "unknown creature"
        menu = card["menu"]
        if "continue" not in menu or not 3 <= len(menu) <= 8:
            return "menu"
        if any(k.startswith("svm_") and k[4:].upper() not in catalogue for k in menu):
            return "unknown svm"
        if not card["scene"].strip():
            return "scene"
        int(card["hour"]), int(card["minute"]), float(card["distance_m"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return "fields"
    return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=40)
    ap.add_argument("--per-call", type=int, default=8)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--quiet", type=float, default=0.4, help="share of calls about quiet moments")
    ap.add_argument(
        "--emergency",
        action="store_const",
        dest="focus",
        const="emergency",
        help="only danger and commotion around the npc",
    )
    ap.add_argument(
        "--personal",
        action="store_const",
        dest="focus",
        const="personal",
        help="only npc's with personal business with the hero",
    )
    ap.add_argument(
        "--danger",
        action="store_const",
        dest="focus",
        const="danger",
        help="only creatures going for the npc",
    )
    ap.add_argument(
        "--rescue",
        action="store_const",
        dest="focus",
        const="rescue",
        help="only creatures that went for the npc and were killed",
    )
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    out_path = DATA / "cards.jsonl"
    done = sum(1 for _ in out_path.open()) if out_path.exists() else 0
    rnd = random.Random(1000 + done)
    excluded = eval_npcs()
    excluded_set = set(excluded)
    catalogue = svm_catalogue()
    print(f"{len(catalogue)} SVM lines, {len(excluded)} eval npc's excluded, {done} cards so far", flush=True)

    backend = make_backend("chatgpt", reasoning_effort=args.effort)
    sem = asyncio.Semaphore(args.jobs)
    stats: dict = {"cards": 0, "rejected": {}}
    lock = asyncio.Lock()

    async def one(i: int, seed_rnd: random.Random) -> None:
        text, seed = prompt(seed_rnd, args.per_call, excluded, catalogue, args.quiet, args.focus)
        cards: list[dict] = []
        async with sem:
            for attempt in range(3):
                try:
                    reply = ""
                    messages = [Message("user", text)]
                    async for ev in backend.stream(instructions=INSTRUCTIONS, messages=messages):
                        if isinstance(ev, Done):
                            reply = ev.text
                    m = re.search(r"\{.*\}", reply, re.DOTALL)
                    cards = json.loads(m.group(0))["cards"] if m else []
                    break
                except (LLMError, json.JSONDecodeError, KeyError) as e:
                    if isinstance(e, LLMError) and not e.retryable:
                        raise
                    print(f"call {i}: {e!r}, retry {attempt + 1}", flush=True)
                    await asyncio.sleep(5 * (attempt + 1))
        async with lock:
            with out_path.open("a") as out:
                for card in cards:
                    why = valid(card, excluded_set, catalogue)
                    if why:
                        stats["rejected"][why] = stats["rejected"].get(why, 0) + 1
                        continue
                    card["seed"] = seed
                    out.write(json.dumps(card, ensure_ascii=False) + "\n")
                    stats["cards"] += 1
            print(f"call {i}: {len(cards)} cards, total kept {stats['cards']}", flush=True)

    try:
        await asyncio.gather(*(one(i, random.Random(rnd.random())) for i in range(args.calls)))
    finally:
        await backend.aclose()
    print(json.dumps(stats))


if __name__ == "__main__":
    asyncio.run(main())
