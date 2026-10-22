import pytest

from gothic_ai.llm import FakeBackend, FakeStep, Message, ToolCall, ToolResult
from gothic_ai.replay import replay
from gothic_ai.trace import item_json

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
