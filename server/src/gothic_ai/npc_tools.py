"""What an npc can do while talking: tools offered to the LLM and their execution in the game.

Tool calls are fire-and-forget for the model: the game queues the action after the npc's current
speech and the outcome is written into the conversation as a note the model sees next turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .game import GameError, GameLink
from .llm import Tool, ToolCall
from .protocol import ActionResult, Snapshot

log = logging.getLogger(__name__)

# gesture id offered to the model -> human animation
GESTURES = {
    "nod": "T_YES",
    "shake_head": "T_NO",
    "come_here": "T_COMEOVERHERE",
    "shrug": "T_DONTKNOW",
    "greet": "T_GREETNOV",
    "dismiss": "T_FORGETIT",
}

END_CONVERSATION = "end_conversation"


def _object(properties: dict | None = None) -> dict:
    properties = properties or {}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TOOLS = [
    Tool(
        "give_item",
        "Отдать герою предмет из СВОЕГО инвентаря (список — в обстановке). "
        "Если герой далеко, ты сначала подойдёшь. Не раздавай ценное без причины.",
        _object(
            {
                "item": {
                    "type": "string",
                    "description": "идентификатор предмета из инвентаря, например ITPO_HEALTH_01",
                },
                "count": {"type": "integer", "description": "сколько штук, не меньше 1"},
            }
        ),
    ),
    Tool("follow_player", "Пойти за героем и следовать за ним, пока тебя не остановят.", _object()),
    Tool("stop", "Остановиться: перестать следовать за героем или прервать движение.", _object()),
    Tool("come_closer", "Подойти к герою вплотную.", _object()),
    Tool(
        "gesture",
        "Сделать жест во время разговора.",
        _object({"name": {"type": "string", "enum": list(GESTURES), "description": "какой жест"}}),
    ),
    Tool(
        END_CONVERSATION,
        "Закончить разговор и вернуться к своим делам. Используй, когда сказать больше нечего "
        "или герой ведёт себя грубо. Перед этим попрощайся.",
        _object(),
    ),
]


@dataclass(frozen=True)
class Outcome:
    note: str  # for the conversation history, in the npc's perspective
    ok: bool


async def execute(link: GameLink, npc: str, call: ToolCall, snapshot: Snapshot | None) -> Outcome:
    """Run one tool call in the game. `end_conversation` is handled by the dialogue itself."""
    args = call.arguments
    try:
        match call.name:
            case "give_item":
                item = str(args.get("item", "")).upper()
                count = max(1, int(args.get("count") or 1))
                name = _item_name(snapshot, item)
                res = await link.act(npc, "give_item", item=item, count=count)
                return _outcome(res, f"Ты отдал герою: {name} ×{count}", f"Не вышло отдать {name}")
            case "follow_player":
                res = await link.act(npc, "follow", npc="PLAYER")
                return _outcome(res, "Ты идёшь за героем", "Не вышло пойти за героем")
            case "stop":
                res = await link.act(npc, "stop")
                return _outcome(res, "Ты остановился", "Не вышло остановиться")
            case "come_closer":
                res = await link.act(npc, "go_to_npc", npc="PLAYER")
                return _outcome(res, "Ты подошёл к герою", "Не вышло подойти к герою")
            case "gesture":
                anim = GESTURES.get(str(args.get("name", "")))
                if anim is None:
                    return Outcome(f"Неизвестный жест {args.get('name')!r}", ok=False)
                res = await link.act(npc, "play_ani", name=anim)
                return _outcome(res, "", "Жест не получился")
    except (GameError, TimeoutError, ValueError) as err:
        return Outcome(f"Действие {call.name} не выполнено: {err}", ok=False)
    return Outcome(f"Неизвестное действие {call.name}", ok=False)


def _outcome(res: ActionResult, done: str, failed: str) -> Outcome:
    if res.status == "done":
        return Outcome(done, ok=True)
    log.info("action %s %s: %s", res.id, res.status, res.detail)
    return Outcome(f"{failed} ({res.detail or res.status})", ok=False)


def _item_name(snapshot: Snapshot | None, item: str) -> str:
    if snapshot is not None:
        for it in snapshot.npc.inventory:
            if it.item == item:
                return it.name
    return item


# --- situation ------------------------------------------------------------------------

GUILDS = {
    0: "без гильдии",
    1: "паладин",
    2: "ополченец",
    3: "горожанин",
    4: "маг Огня",
    5: "послушник",
    6: "охотник на драконов",
    7: "наёмник",
    8: "крестьянин",
    9: "бандит",
    10: "каторжник",
    11: "ищущий",
    12: "житель окрестностей",
    13: "пират",
    14: "маг Воды",
}


def guild_name(guild: int) -> str:
    return GUILDS.get(guild, "существо")


def _metres(cm: float) -> str:
    return f"{max(1, round(cm / 100))} м"


def render_situation(snap: Snapshot | None) -> str:
    """What the npc perceives right now; sent with every turn, not kept in history."""
    if snap is None:
        return "[Обстановка неизвестна]"
    w, npc, pl = snap.world, snap.npc, snap.player
    lines = [
        "[Обстановка — видна только тебе, герою её не пересказывай]",
        f"Время: день {w.day + 1}, {w.hour:02d}:{w.minute:02d}. "
        f"Ты у точки {npc.waypoint or npc.nearest_waypoint or 'неизвестно где'}.",
    ]
    if pl is not None:
        lines.append(
            f"Герой в {_metres(pl.distance)} от тебя. Гильдия героя: {guild_name(pl.guild)}. "
            f"Доспех: {pl.armor or 'нет'}. Оружие: {pl.melee or 'нет'}"
            f"{f', {pl.ranged}' if pl.ranged else ''}."
            + (f" Здоровье героя: {pl.hp}/{pl.hp_max}." if pl.hp is not None and pl.hp_max else "")
        )
    items = [f"{it.name} ({it.item}) ×{it.count}" for it in npc.inventory if not it.equipped]
    lines.append("Твой инвентарь: " + ("; ".join(items) if items else "пусто") + ".")
    near = [f"{o.name} ({_metres(o.distance)}{', мёртв' if o.dead else ''})" for o in snap.nearby_npcs]
    lines.append("Рядом: " + (", ".join(near) if near else "никого") + ".")
    lines.append("Сейчас ты идёшь за героем." if npc.following else "Сейчас ты стоишь на месте.")
    return "\n".join(lines)
