import asyncio

from gothic_ai.brain.reflex import Reflexes
from gothic_ai.dialogue import DialogueManager
from gothic_ai.journal import Journal
from gothic_ai.llm import FakeBackend, ToolCall
from gothic_ai.persona import PersonaProvider
from gothic_ai.protocol import RosterEntry
from gothic_ai.talk import TalkDesk, addressee, default_posture
from tests.fake_game import FakeGame, connected
from tests.fakes import FakeDecider
from tests.test_dialogue import HILDA, LOBART, XARDAS, _until


def person(npc: str, name: str, metres: float, in_view: bool = False, **extra) -> dict:
    return {"id": npc, "name": name, "distance": metres * 100, "in_view": in_view, **extra}


def roster(*people: dict) -> dict[str, RosterEntry]:
    return {p["id"]: RosterEntry(**p) for p in people}


XARDAS_NEAR = person(XARDAS, "Ксардас", 3, True, state="ZS_STAND_ARMSCROSSED")


def test_whom_the_hero_speaks_to():
    people = roster(
        person(XARDAS, "Ксардас", 3, True),
        person(LOBART, "Лобарт", 12),
        person("PC_THIEF", "Диего", 6, True),
    )
    # a name wins, in any case form, even over what he looks at
    assert addressee("Лобарту скажи, что волки близко", XARDAS, people, None).id == LOBART
    assert addressee("Лобарт, за меня!", XARDAS, people, None).why == "name"
    # then his focus, then the partner still close, then the nearest in front of him
    assert addressee("Ты кто?", "PC_THIEF", people, XARDAS).why == "focus"
    assert addressee("А ты?", None, people, XARDAS).why == "partner"
    hit = addressee("Эй!", None, people, None)
    assert (hit.id, hit.why) == (XARDAS, "front")
    # nobody close in front of him: said into the air
    far = roster(person(LOBART, "Лобарт", 20, True))
    assert addressee("Эй!", None, far, None) is None
    assert addressee("Эй!", LOBART, far, None).why == "focus"  # 20 m: still the one he looks at


def test_a_name_as_speech_recognition_heard_it():
    people = roster(
        person(XARDAS, "Ксардас", 3, True),
        person(LOBART, "Лобарт", 12),
        person("PC_THIEF", "Диего", 6, True),
        person("VLK_FESTER", "Фестер", 4),
        person("VLK_LESTER", "Лестер", 9),
        person("MIL_GARAD", "Гарад", 5),
        person("KDF_BASTER", "Бастер", 5),
    )
    # called by a misheard name, even while he looks at someone else
    assert addressee("Сардас, как победить драконов?", "PC_THIEF", people, None).id == XARDAS
    assert addressee("Привет, Ксардоз.", "PC_THIEF", people, None).id == XARDAS
    assert addressee("Лабарт, за меня!", None, people, XARDAS).id == LOBART
    # the name heard right wins over a nearer one only like it
    assert addressee("Лестер!", None, people, None).id == "VLK_LESTER"
    # a misheard word inside the phrase is an ordinary one, and so is an address of its own
    assert addressee("Что мне нужно знать об этом городе?", "PC_THIEF", people, None).why == "focus"
    assert addressee("Да, мастер.", "PC_THIEF", people, None).why == "focus"
    # a word that only begins like a name is not it ("ополчение" is not Ополос, "торт" not Торус)
    guards = roster(person("MIL_OPOLOS", "Ополос", 3), person("VLK_TORUS", "Торус", 3))
    assert addressee("Я хочу вступить в ополчение", None, guards, None) is None
    assert addressee("Купи мне торт", None, guards, None) is None
    assert addressee("Скажи Торусу, что я пришёл", None, guards, None).id == "VLK_TORUS"


def test_the_codes_posture_when_the_behavioral_model_is_unsure():
    stand = RosterEntry(id="A", name="A", distance=300, state="ZS_STAND_ARMSCROSSED")
    assert default_posture(stand) == "listen"
    assert default_posture(stand.model_copy(update={"state": "ZS_SMITH_ANVIL"})) == "keep_working"
    assert default_posture(stand.model_copy(update={"moving": True})) == "keep_working"


async def _desk(fake: FakeGame, llm, decider: FakeDecider | None = None, journal: Journal | None = None):
    cm = connected(fake)
    link = await cm.__aenter__()
    dialogue = DialogueManager(link, llm, PersonaProvider(None), journal=journal)
    reflexes = Reflexes(decider) if decider is not None else None
    talk = TalkDesk(link, dialogue, reflexes, end_silence_s=0.2)
    return cm, link, dialogue, talk


async def test_a_free_npc_standing_about_stops_and_listens():
    fake = FakeGame()
    llm = FakeBackend("Слушаю тебя.", delay=0)
    cm, link, dialogue, talk = await _desk(fake, llm)
    try:
        await fake.roster(XARDAS_NEAR)
        await fake.send(type="player_text", text="Нужен совет", focus=XARDAS)
        await _until(lambda: fake.said == ["Слушаю тебя."])
    finally:
        await cm.__aexit__(None, None, None)

    assert talk.held == {XARDAS: {"listen"}} and XARDAS in fake.attached  # held only while it listens
    acts = [m["action"] for m in fake.received if m["type"] == "action"]
    assert acts[0] == "turn_to"
    say = next(m for m in fake.received if m["type"] == "say")
    assert "overlay" not in say  # standing, with gestures
    situation = llm.calls[0][1][-2].content
    assert "Твоё тело сейчас: ты остановился, повернулся к герою и слушаешь" in situation


async def test_an_npc_can_open_the_conversation():
    fake = FakeGame()
    llm = FakeBackend("Долг, парень.", delay=0)
    cm, link, dialogue, talk = await _desk(fake, llm)
    try:
        # Xardas in front of the hero and nearer: without a partner the hero's words would go to him
        await fake.roster(person(LOBART, "Лобарт", 3), person(XARDAS, "Ксардас", 2, True))
        assert await talk.listen(LOBART)  # it came up: stops and turns to the hero
        assert talk.begin(LOBART, "Лобарт", posture="listen")
        assert talk.partner.npc == LOBART and talk.partner.posture == "listen"
        assert talk.begin(LOBART, "Лобарт")  # the partner already
        assert not talk.begin(XARDAS, "Ксардас")  # the hero talks to another
        await fake.send(type="player_text", text="Чего тебе?")
        await _until(lambda: fake.said == ["Долг, парень."])
    finally:
        await cm.__aexit__(None, None, None)

    to, _ = talk.last
    assert (to.id, to.why) == (LOBART, "partner")
    assert talk.partner.npc == LOBART and talk.held == {LOBART: {"listen"}}
    acts = [(m["npc"], m["action"]) for m in fake.received if m["type"] == "action"]
    assert acts == [(LOBART, "turn_to")]  # once, by listen: the hero's words find it listening
    assert dialogue.conversations[LOBART].history[0].content == "Чего тебе?"


async def test_a_busy_npc_keeps_working_and_answers_on_the_move():
    fake = FakeGame()
    llm = FakeBackend("Некогда мне.", delay=0)
    cm, link, dialogue, talk = await _desk(fake, llm, FakeDecider(choice="keep_working", confidence=0.9))
    try:
        await fake.roster(person(XARDAS, "Ксардас", 3, True, state="ZS_SMITH_ANVIL"))
        await fake.send(type="player_text", text="Как дела?", focus=XARDAS)
        await _until(lambda: fake.said == ["Некогда мне."])
    finally:
        await cm.__aexit__(None, None, None)

    assert talk.held == {} and XARDAS not in fake.attached  # never taken over
    assert (XARDAS, "look_at") in fake.soft
    say = next(m for m in fake.received if m["type"] == "say")
    assert say["overlay"] is True
    state = next(c["state"] for c in talk.reflexes.decider.calls if "posture" in c["questions"])
    assert "Right now you hammer at the anvil." in state and '"Как дела?"' in state


async def test_the_hero_interrupts_the_npc():
    fake = FakeGame()
    llm = FakeBackend(
        "Слушай. " + "Это очень длинная история о драконах, и она всё не кончается. " * 6, delay=0.05
    )
    cm, link, dialogue, talk = await _desk(fake, llm)
    try:
        await fake.roster(XARDAS_NEAR)
        await fake.send(type="player_text", text="Расскажи о драконах", focus=XARDAS)
        await _until(lambda: len(fake.said) >= 1)
        await fake.send(type="ptt_start", focus=XARDAS)  # the hero starts to speak
        await _until(lambda: bool(fake.hushed))
        await asyncio.sleep(0.2)
    finally:
        await cm.__aexit__(None, None, None)

    hush = fake.hushed[0]
    assert hush["npc"] == XARDAS and hush["said"].startswith("Слушай.")
    history = [m.content for m in dialogue.conversations[XARDAS].history]
    assert history[0] == "Расскажи о драконах"
    assert history[1] == hush["said"]  # only what it said
    assert history[2] == "[Герой перебил тебя]"


async def test_witnesses_remember_what_they_heard():
    fake = FakeGame()
    cm, link, dialogue, talk = await _desk(fake, FakeBackend("Хм.", delay=0))
    try:
        await fake.roster(XARDAS_NEAR, person("PC_THIEF", "Диего", 6), person("FAR", "Далёкий", 25))
        await fake.send(type="player_text", text="Я убил Бандита", focus=XARDAS)
        await _until(lambda: fake.said == ["Хм."])
    finally:
        await cm.__aexit__(None, None, None)

    (heard,) = [m.content for m in dialogue.conversations["PC_THIEF"].history]
    assert heard == "[Ты слышал, как герой сказал собеседнику (Ксардас): «Я убил Бандита»]"
    assert "FAR" not in dialogue.conversations


async def test_the_conversation_ends_when_the_hero_walks_away():
    fake = FakeGame()
    cm, link, dialogue, talk = await _desk(fake, FakeBackend("Иди.", delay=0))
    try:
        await fake.roster(XARDAS_NEAR)
        await fake.send(type="player_text", text="Пока", focus=XARDAS)
        await _until(lambda: fake.said == ["Иди."] and XARDAS in fake.attached)
        await asyncio.sleep(1.6)  # the line is said
        await fake.roster({**XARDAS_NEAR, "distance": 2000.0})
        await _until(lambda: talk.partner is None and XARDAS not in fake.attached)
    finally:
        await cm.__aexit__(None, None, None)

    assert talk.held == {}  # back to its routine
    assert dialogue.conversations[XARDAS].history[-1].content == "[Герой ушёл, разговор закончился]"


async def test_the_heros_doings_mid_conversation_are_remembered():
    fake = FakeGame()
    llm = FakeBackend("Убери меч.", delay=0)
    cm, link, dialogue, talk = await _desk(fake, llm, FakeDecider(choice="keep_working"))
    try:
        await fake.roster(XARDAS_NEAR)
        await fake.send(type="player_text", text="Смотри", focus=XARDAS)
        await _until(lambda: fake.said == ["Убери меч."])
        await fake.send(type="npc_event", npc=XARDAS, kind="weapon_drawn", other="PLAYER", distance=300)
        await _until(lambda: dialogue.conversations[XARDAS].history[-1].role == "developer")
    finally:
        await cm.__aexit__(None, None, None)

    assert dialogue.conversations[XARDAS].history[-1].content == "[Герой посреди разговора достал оружие]"


async def test_a_free_npc_is_held_only_to_hand_an_item_over():
    fake = FakeGame()
    llm = FakeBackend(
        "Держи зелье.", delay=0, tool_calls=[ToolCall("give_item", {"item": "ITPO_HEALTH_01", "count": 1})]
    )
    cm, link, dialogue, talk = await _desk(fake, llm, FakeDecider(choice="keep_working"))
    try:
        await fake.roster(person(XARDAS, "Ксардас", 3, True, state="ZS_READ_BOOKSTAND"))
        await fake.send(type="player_text", text="Дай зелье", focus=XARDAS)
        await _until(lambda: any(m["type"] == "detach" for m in fake.received), timeout=5)
    finally:
        await cm.__aexit__(None, None, None)

    kinds = [
        (m["type"], m.get("action")) for m in fake.received if m["type"] in ("attach", "action", "detach")
    ]
    assert kinds == [("attach", None), ("action", "give_item"), ("detach", None)]
    assert any(
        m.content == "[Ты отдал герою: Эликсир лечения ×1]" for m in dialogue.conversations[XARDAS].history
    )


def test_words_after_a_scene_go_to_one_of_its_npcs():
    people = roster(
        person(LOBART, "Лобарт", 9),
        person(HILDA, "Хильда", 11, True),
        person(XARDAS, "Ксардас", 3, True),
        person("PC_THIEF", "Диего", 6),
    )
    scene = [LOBART, HILDA]  # two were talking near him, Lobart last
    # a name, his focus and his partner come first
    assert addressee("Диего, ты слышал?", None, people, None, scene).id == "PC_THIEF"
    assert addressee("Диэго, ты слышал?", None, people, None, scene).id == "PC_THIEF"  # as misheard
    assert addressee("Ну?", "PC_THIEF", people, None, scene).why == "focus"
    assert addressee("Ну?", None, people, XARDAS, scene).why == "partner"
    # then the scene, before whoever stands in front of him: the nearest of it he sees...
    hit = addressee("О чём шепчетесь?", None, people, None, scene)
    assert (hit.id, hit.why) == (HILDA, "scene")
    # ...else the one who spoke last
    unseen = roster(
        person(LOBART, "Лобарт", 9), person(HILDA, "Хильда", 11), person(XARDAS, "Ксардас", 3, True)
    )
    assert addressee("Эй!", None, unseen, None, scene).id == LOBART
    # too far to be called, or down: not his addressee
    far = roster(person(LOBART, "Лобарт", 25, True), person(HILDA, "Хильда", 4, activity="down"), XARDAS_NEAR)
    assert addressee("Эй!", None, far, None, scene).why == "front"
    assert addressee("Эй!", None, people, None).why == "front"  # no scene


async def test_the_heros_voice_stops_a_scene_without_a_partner():
    fake = FakeGame()
    journal: list[dict] = []
    cm, link, dialogue, talk = await _desk(fake, FakeBackend("Да?", delay=0), journal=Journal(journal.append))
    calls: list[str] = []

    def scene() -> bool:
        calls.append("scene")
        return True  # two were talking: they stopped

    def other() -> bool:
        calls.append("other")
        return False  # nothing to stop

    talk.on_hero_voice += [scene, other]
    try:
        assert talk.interrupt() and calls == ["scene", "other"]  # every one asked
        await fake.send(type="ptt_start")  # the talk key
        await _until(lambda: len(calls) == 4)
        await fake.send(type="player_text", text="Эй!")  # typed
        await _until(lambda: len(calls) == 6)
    finally:
        await cm.__aexit__(None, None, None)

    assert talk.partner is None
    assert not any(e["kind"] == "interrupt" for e in journal)  # the scene's own desk tells of its cut


async def test_an_npc_held_for_a_scene_listens_to_the_hero():
    fake = FakeGame()
    llm = FakeBackend("Не твоё дело.", delay=0)
    decider = FakeDecider(choice="keep_working", confidence=0.9)
    cm, link, dialogue, talk = await _desk(fake, llm, decider)
    ended: list[tuple[str, str]] = []
    talk.on_talk_end.append(lambda npc, why: ended.append((npc, why)))
    talk.scene_hint = lambda: [HILDA]
    try:
        await fake.roster(person(HILDA, "Хильда", 6, True, state="ZS_STAND_ARMSCROSSED"))
        assert await talk.hold(HILDA, "scene")  # it stood talking to another
        await fake.send(type="player_text", text="О чём вы шептались?")
        await _until(lambda: fake.said == ["Не твоё дело."])
        assert talk.held == {HILDA: {"scene", "listen"}}

        talk.end()  # the conversation is over; the scene's hold is the scene's to free
        assert talk.partner is None and talk.held == {HILDA: {"scene"}}
        talk.free(HILDA, "scene")
        await _until(lambda: HILDA not in fake.attached)
    finally:
        await cm.__aexit__(None, None, None)

    to, _ = talk.last
    assert (to.id, to.why) == (HILDA, "scene")
    acts = [(m["action"], m["args"]) for m in fake.received if m["type"] == "action"]
    assert acts == [("turn_to", {"npc": "PLAYER"})]  # it turns to him
    say = next(m for m in fake.received if m["type"] == "say")
    assert "overlay" not in say  # it stands and listens, not an answer on the move
    assert not any("posture" in c["questions"] for c in decider.calls)
    situation = llm.calls[0][1][-2].content
    assert "Твоё тело сейчас: ты остановился, повернулся к герою и слушаешь" in situation
    assert ended == [(HILDA, "walked_away")]
    assert dialogue.conversations[HILDA].history[-1].content == "[Герой ушёл, разговор закончился]"


BANDIT = "BDT_1013_BANDIT_L"


def test_a_phrase_to_several():
    # 2026-10-13: «Здарова, пацаны» to two bandits went to Cavalorn, who follows the hero, the nearest
    brago, bandit, cavalorn = "BDT_1014_BANDIT_L", "BDT_1015_BANDIT_L", "BAU_4300_ADDON_CAVALORN"
    people = roster(
        person(cavalorn, "Кавалорн", 2, True),
        person(bandit, "Бандит", 4, True),
        person(brago, "Браго", 6, True),
    )
    lead = {brago: 8, bandit: 6, cavalorn: 8}.get

    def to(text: str, focus: str | None = None, ours=lambda npc: npc == cavalorn):
        a = addressee(text, focus, people, None, ours=ours, lead=lambda npc: lead(npc, 0))
        return (a.id, a.why) if a else None

    assert to("Здарова, пацаны.") == (brago, "group")  # the leader, not the companion, not the nearest
    assert to("Ребята, вы откуда?", focus=bandit) == (bandit, "group")  # the one he looks at
    assert to("Эй, вы двое, стоять!") == (brago, "group")
    assert to("Всем привет") == (brago, "group")
    # called out first, then spoken of (the live run of 2026-10-14: words to Brago went to Cavalorn, nearer)
    assert to("Пацаны, Браго тут главный?", focus=bandit) == (bandit, "group")  # «Пацаны,» calls them all
    assert to("Браго, я ищу того, кто ограбил Кавалорна.") == (brago, "name")
    assert to("Пацаны, верните Кавалорну его вещи.") == (brago, "group")
    assert to("Где Кавалорн?") == (cavalorn, "name")  # only spoken of, but nobody is called: as before
    # a bare «вы» is the polite one to one person
    assert to("Вы не подскажете дорогу?", focus=bandit) == (bandit, "focus")
    # nobody but companions: one of them; nobody but one: not a phrase to several
    assert to("Ребята, привет", ours=lambda npc: True) == (cavalorn, "group")
    # in a fight it is to whoever is at him: words to him, not an order to the companions
    fight = roster(
        person(cavalorn, "Кавалорн", 2, True, state="ZS_ATTACK"),
        person(bandit, "Бандит", 4, True, state="ZS_ATTACK"),
        person(brago, "Браго", 6, True),
    )
    stop = addressee("Стой, пацаны, стой!", None, fight, None, ours=lambda npc: npc == cavalorn)
    assert stop is not None and (stop.id, stop.why) == (bandit, "attacker")
    alone = roster(person(bandit, "Бандит", 4, True))
    assert addressee("Пацаны, здорово", None, alone, None) == addressee("Здорово", None, alone, None)


def test_in_a_fight_he_speaks_to_whoever_is_at_him():
    at_him = person(BANDIT, "Бандит", 4, state="ZS_ATTACK", activity="fight")
    people = roster(
        at_him, person(XARDAS, "Ксардас", 3, True), person("PC_FIGHTER", "Горн", 2, state="ZS_ATTACK")
    )
    gorn_is_ours = lambda npc: npc == "PC_FIGHTER"  # noqa: E731
    hit = addressee("Воу, воу, ты чего?", None, people, XARDAS, ours=gorn_is_ours)
    assert (hit.id, hit.why) == (BANDIT, "attacker")  # not his partner, not his own companion
    assert addressee("Ксардас, помоги!", None, people, None, ours=gorn_is_ours).why == "name"
    assert addressee("Ты кто?", XARDAS, people, None, ours=gorn_is_ours).why == "focus"
    far = roster(person(BANDIT, "Бандит", 14, state="ZS_ATTACK"))
    assert addressee("Эй!", None, far, None) is None  # a fight farther off is not with him


async def test_one_in_a_fight_answers_on_and_fights_on():
    fake = FakeGame()
    llm = FakeBackend("Чего тебе?", delay=0)
    cm, link, dialogue, talk = await _desk(fake, llm, FakeDecider("listen", 0.99))
    try:
        await fake.roster(person(BANDIT, "Бандит", 3, True, state="ZS_ATTACK", activity="fight"))
        await fake.send(type="player_text", text="Ты чего, спокуха!")
        await _until(lambda: fake.said == ["Чего тебе?"])
    finally:
        await cm.__aexit__(None, None, None)

    assert talk.last[0].why == "attacker"
    assert BANDIT not in fake.attached  # not held to listen: its scripts fight on while it answers
