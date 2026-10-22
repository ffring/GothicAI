import pytest

from gothic_ai.dialogue import DialogueManager
from gothic_ai.llm import FakeBackend, FakeStep, Message, ToolCall, ToolResult
from gothic_ai.persona import PersonaProvider
from gothic_ai.replay import replay
from gothic_ai.trace import TraceWriter, find_turn, item_json
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import XARDAS, _until

CALL = ToolCall("take_item", {"item": "ITPL_HEALTH_HERB_01"}, "recorded-call")
REJECTION = "Отклонено: на земле нет ITPL_HEALTH_HERB_01; ты видишь там: ничего."


def _record(text="[angry] Полегче, приятель.", *, call=CALL, output=REJECTION, status="rejected"):
    return {
        "id": "recorded-turn",
        "npc": "BDT_1013_BANDIT_L",
        "trigger": {"kind": "player", "text": "Привет, чмо!"},
        "tools": [call.name],
        "steps": [
            {
                "added": [item_json(Message("user", "Привет, чмо!"))],
                "text": text,
                "calls": [
                    {
                        "name": call.name,
                        "arguments": call.arguments,
                        "call_id": call.call_id,
                        "result": output,
                        "outcome": status,
                    }
                ],
            },
            {
                "added": [
                    item_json(Message("assistant", text)),
                    item_json(call),
                    item_json(ToolResult(call.call_id, output)),
                ],
                "text": "",
            },
        ],
    }


@pytest.mark.parametrize("text", ["[angry] Полегче, приятель.", ""])
async def test_replay_adds_spoken_words_to_a_recorded_rejection(text):
    llm = FakeBackend(script=[FakeStep(text, [CALL]), FakeStep("")], delay=0)

    result = await replay(llm, _record(text), "NPC instructions")

    assert len(llm.calls) == 2
    (output,) = [i.output for i in llm.calls[1][1] if isinstance(i, ToolResult)]
    expected = REJECTION
    if text:
        expected += (
            " Ты уже сказал вслух: «Полегче, приятель.». Если отказ этого не меняет — ничего не говори. "
            "Если меняет — поправься одной короткой фразой, не повторяя сказанного."
        )
    assert output == expected
    assert result["replay"][0]["calls"][0]["output"] == expected


@pytest.mark.parametrize("after_step", [0, 1])
async def test_first_words_do_not_finish_a_wordless_accepted_action(after_step):
    call = ToolCall("give_item", {"item": "ITSC_FIREBALL", "count": 1}, "give-call")
    record = _record("", call=call, output="Принято: передашь свиток.", status="accepted")
    record["events"] = [{"kind": "first_words", "text": "Хм..."}]
    script = [FakeStep("", [call]), FakeStep("Держи, это поможет.")]
    llm = FakeBackend(script=script[after_step:], delay=0)

    result = await replay(llm, record, "NPC instructions", after_step=after_step)

    assert len(llm.calls) == 2 - after_step
    assert result["replay"][-1]["text"] == "Держи, это поможет."
    assert result["said_before"] == "Хм..."


@pytest.mark.parametrize("after_step", [0, 1])
@pytest.mark.parametrize(
    "events",
    [
        [{"kind": "first_words", "text": "Ишь ты!"}],
        [
            *[
                {"kind": "first_words", "outcome": outcome, "text": f"Unsaid {outcome}."}
                for outcome in ("too_long", "same", "sound", "nothing", "late", "error")
            ],
            {
                "kind": "first_words",
                "outcome": "said",
                "text": "[angry] Ишь ты! И это всё?",
                "words": "Ишь ты!",
            },
        ],
    ],
)
async def test_replay_quotes_only_first_words_that_were_said_once(events, after_step):
    record = _record("Ишь ты! Держи свиток.")
    record["events"] = events
    script = [FakeStep("Ишь ты! Держи свиток.", [CALL]), FakeStep("")]
    llm = FakeBackend(script=script[after_step:], delay=0)

    result = await replay(llm, record, "NPC instructions", after_step=after_step)

    (output,) = [i.output for i in llm.calls[-1][1] if isinstance(i, ToolResult)]
    assert output == (
        REJECTION + " Ты уже сказал вслух: «Ишь ты! Держи свиток.». "
        "Если отказ этого не меняет — ничего не говори. "
        "Если меняет — поправься одной короткой фразой, не повторяя сказанного."
    )
    assert result["said_before"] == ("Ишь ты! Держи свиток." if after_step else "Ишь ты!")


@pytest.mark.parametrize("after_step", [0, 2])
@pytest.mark.parametrize(
    "first_reply",
    ["Ишь ты!", "functions.give_quest({...})"],
    ids=["repeated-first-words", "discarded-tool-text"],
)
async def test_replay_quotes_the_dialogue_lines_instead_of_raw_step_text(
    tmp_path, first_reply, after_step
):
    recall = ToolCall("recall", {"query": "scroll"}, "recall-call")
    give = ToolCall("give_item", {"item": "ITSC_FIREBALL", "count": 1}, "give-call")
    script = [
        FakeStep(first_reply, [recall]),
        FakeStep("Ишь ты! Держи свиток.", [give]),
        FakeStep(""),
    ]
    fake = FakeGame()
    llm = FakeBackend(script=script, chunk=3, delay=0)
    writer = TraceWriter(tmp_path)
    async with connected(fake) as link:
        dialogue = DialogueManager(
            link,
            llm,
            PersonaProvider(None),
            quick=FakeBackend("Ишь ты!", delay=0),
            trace=writer,
        )
        await link.attach(XARDAS)
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Дай мне свиток.")
        await _until(lambda: fake.statuses[-1:] == ["idle"])

    assert fake.said == ["Ишь ты!", "Держи свиток."]
    assert len(llm.calls) == 3
    original = next(
        i for i in llm.calls[2][1] if isinstance(i, ToolResult) and i.call_id == give.call_id
    )
    assert original.output.startswith("Отклонено: У тебя нет предмета ITSC_FIREBALL")
    assert original.output.endswith(
        " Ты уже сказал вслух: «Ишь ты! Держи свиток.». "
        "Если отказ этого не меняет — ничего не говори. "
        "Если меняет — поправься одной короткой фразой, не повторяя сказанного."
    )
    assert original.output.count("Ишь ты!") == 1
    record, instructions = find_turn(writer.path(), None)
    assert instructions
    assert record["steps"][0]["text"] == first_reply
    assert record["steps"][1]["text"] == "Ишь ты! Держи свиток."
    assert record["steps"][1]["calls"][0]["outcome"] == "rejected"
    other = FakeBackend(script=script[after_step:], chunk=3, delay=0)

    result = await replay(other, record, instructions, after_step=after_step)

    assert len(other.calls) == 3 - after_step
    replayed = next(
        i for i in other.calls[-1][1] if isinstance(i, ToolResult) and i.call_id == give.call_id
    )
    assert replayed.output == original.output
    assert result["said_before"] == (" ".join(fake.said) if after_step else "Ишь ты!")
    assert not result["diverged"]


async def test_replay_can_continue_the_recorded_rejection_without_saying_the_first_step_again():
    record = _record()
    # New traces already contain the suffix in added; the call's result remains the raw rejection.
    record["steps"][1]["added"][-1]["output"] += " old suffix must be replaced"
    llm = FakeBackend(script=[FakeStep("")], delay=0)

    result = await replay(llm, record, "NPC instructions", after_step=1)

    assert len(llm.calls) == 1
    assert result["after_step"] == 1
    assert result["said_before"] == "Полегче, приятель."
    (output,) = [i.output for i in llm.calls[0][1] if isinstance(i, ToolResult)]
    assert output.startswith(REJECTION + " Ты уже сказал вслух: «Полегче, приятель.».")
    assert output.count("Ты уже сказал вслух") == 1
    assert "old suffix" not in output
    assert result["initial_tool_results"] == [output]


async def test_continuing_a_recorded_step_keeps_the_turn_step_cap():
    llm = FakeBackend(script=[FakeStep("", [CALL]), FakeStep("", [CALL])], delay=0)

    result = await replay(llm, _record(), "NPC instructions", after_step=1)

    assert len(result["replay"]) == 2
    assert llm.tool_choices == [None, "none"]
    assert result["replay"][-1]["calls"] == []


async def test_replay_offers_check_claim_after_the_recorded_quest_rejection():
    accept = ToolCall("accept_quest", {"quest": "MIS_ADDON_CAVALORN_KILLBRAGO"}, "accept-call")
    check = ToolCall("check_claim", accept.arguments, "check-call")
    record = _record(
        "[neutral] Получай обещанную награду.",
        call=accept,
        output="Отклонено: он уже лгал тебе. Можешь сходить и посмотреть сам (check_claim).",
    )
    record["tools"].append(check.name)
    record["steps"][1]["calls"] = [
        {
            "name": check.name,
            "arguments": check.arguments,
            "call_id": check.call_id,
            "result": "Принято: после реплики пойдёшь смотреть сам (Браго).",
            "outcome": "accepted",
        }
    ]
    llm = FakeBackend(
        script=[FakeStep("Только сперва вернусь в пещеру и проверю.", [check])], delay=0
    )

    result = await replay(llm, record, "NPC instructions", after_step=1)

    assert len(llm.calls) == 1
    assert {t.name for t in llm.calls[0][2]} == {accept.name, check.name}
    assert result["replay"][0]["calls"][0]["outcome"] == "accepted"
    assert not result["diverged"]


async def test_replay_does_not_add_the_rejection_rule_to_recall():
    call = ToolCall("recall", {"query": "bandits"}, "recall-call")
    llm = FakeBackend(script=[FakeStep("Погоди.", [call]), FakeStep("Вспомнил.")], delay=0)

    result = await replay(
        llm, _record(call=call, output="Ты помнишь бандитов.", status="read"), "NPC instructions"
    )

    assert len(llm.calls) == 2
    assert result["replay"][0]["calls"][0]["output"] == "Ты помнишь бандитов."


async def test_continuing_a_silent_step_keeps_gemmas_words_and_does_not_offer_a_correction():
    record = _record("These words were not said.")
    record["steps"][0]["silent"] = True
    record["steps"][1]["silent"] = True
    record["steps"].insert(
        0,
        {
            "added": [item_json(Message("developer", "Quick situation"))],
            "text": "[angry] Ни шагу!",
            "llm": "gemma",
        },
    )
    record["events"] = [{"kind": "first_words", "text": "Эй!"}]
    llm = FakeBackend(script=[FakeStep("These words stay silent too.")], delay=0)

    result = await replay(llm, record, "NPC instructions", after_step=2)

    assert result["said_before"] == "Эй! Ни шагу!"
    assert result["initial_tool_results"] == [REJECTION]
    assert result["replay"][0]["silent"]
    assert not any(getattr(i, "content", None) == "Quick situation" for i in llm.calls[0][1])
    assert llm.tool_choices == [None]


@pytest.mark.parametrize("after_step", [-1, 2])
async def test_replay_rejects_a_missing_continuation_step(after_step):
    llm = FakeBackend(delay=0)

    with pytest.raises(ValueError, match="after_step must be between 0 and 1"):
        await replay(llm, _record(), "NPC instructions", after_step=after_step)

    assert not llm.calls
