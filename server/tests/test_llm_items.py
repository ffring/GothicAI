from types import SimpleNamespace

from gothic_ai.llm import (
    Done,
    FakeBackend,
    FakeStep,
    Message,
    Reasoning,
    TextDelta,
    Tool,
    ToolCall,
    ToolResult,
    Usage,
)
from gothic_ai.llm.openai_compat import chat_messages
from gothic_ai.llm.openai_responses import OpenAIResponsesBackend, input_items

GIVE = Tool("give_item", "give")


def test_responses_input_items_carry_a_turn_back():
    items = input_items(
        [
            Message("user", "Дай зелье"),
            Reasoning({"type": "reasoning", "summary": [], "encrypted_content": "gAAA"}),
            Message("assistant", "Держи."),
            ToolCall("give_item", {"item": "ITPO_HEALTH_01"}, "call_1", '{"item":"ITPO_HEALTH_01"}'),
            ToolResult("call_1", "rejected"),
        ]
    )
    assert items == [
        {"role": "user", "content": "Дай зелье"},
        {"type": "reasoning", "summary": [], "encrypted_content": "gAAA"},
        {"role": "assistant", "content": "Держи."},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "give_item",
            "arguments": '{"item":"ITPO_HEALTH_01"}',  # the model's own text, not re-serialized
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "rejected"},
    ]


class _Item(SimpleNamespace):
    def model_dump(self, exclude_none: bool = False) -> dict:
        return {k: v for k, v in vars(self).items() if not (exclude_none and v is None)}


class _Stream:
    def __init__(self, events: list) -> None:
        self.events = events

    async def __aenter__(self) -> "_Stream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass

    def __aiter__(self):
        async def gen():
            for e in self.events:
                yield e

        return gen()


async def test_responses_stream_returns_the_response_as_items_and_usage():
    events = [
        SimpleNamespace(
            type="response.output_item.done",
            item=_Item(type="reasoning", id="rs_1", status=None, summary=[], encrypted_content="gAAA"),
        ),
        SimpleNamespace(type="response.output_text.delta", delta="Нет у меня "),
        SimpleNamespace(type="response.output_text.delta", delta="свитка."),
        SimpleNamespace(
            type="response.output_item.done",
            item=_Item(type="message", id="msg_1", content=[SimpleNamespace(text="Нет у меня свитка.")]),
        ),
        SimpleNamespace(
            type="response.output_item.done",
            item=_Item(type="function_call", id="fc_1", name="give_item", call_id="call_9", arguments="{}"),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=1200,
                    input_tokens_details=SimpleNamespace(cached_tokens=1024),
                    output_tokens=40,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=12),
                )
            ),
        ),
    ]
    sent = {}

    async def create(**kwargs):
        sent.update(kwargs)
        return _Stream(events)

    async def token() -> str:
        return "t"

    backend = OpenAIResponsesBackend("m", token)
    backend._client.responses.create = create  # type: ignore[method-assign]
    out = [e async for e in backend.stream(instructions="i", messages=[], tools=[GIVE], tool_choice="none")]
    await backend.aclose()

    assert sent["include"] == ["reasoning.encrypted_content"]
    assert sent["tool_choice"] == "none" and sent["store"] is False
    assert [type(e) for e in out] == [TextDelta, TextDelta, ToolCall, Done]
    done = out[-1]
    assert done.text == "Нет у меня свитка."
    assert done.items == (
        Reasoning({"type": "reasoning", "summary": [], "encrypted_content": "gAAA"}),  # no id: not stored
        Message("assistant", "Нет у меня свитка."),
        ToolCall("give_item", {}, "call_9", "{}"),
    )
    assert done.usage == Usage(input_tokens=1200, cached_tokens=1024, output_tokens=40, reasoning_tokens=12)


def test_chat_messages_attach_tool_calls_to_the_assistant_message():
    chat = chat_messages(
        [
            Message("developer", "[обстановка]"),
            Message("user", "Дай зелье"),
            Reasoning({"type": "reasoning"}),
            Message("assistant", "Держи."),
            ToolCall("give_item", {"item": "X"}, "c1", '{"item":"X"}'),
            ToolResult("c1", "ok"),
        ]
    )
    assert chat == [
        {"role": "system", "content": "[обстановка]"},
        {"role": "user", "content": "Дай зелье"},
        {
            "role": "assistant",
            "content": "Держи.",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "give_item", "arguments": '{"item":"X"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]


async def test_fake_backend_follows_its_script_one_step_per_call():
    llm = FakeBackend(
        script=[
            FakeStep("Держи.", [ToolCall("give_item", {"item": "X", "count": 1})]),
            FakeStep("Хотя нет, такого у меня нет."),
        ],
        delay=0,
    )
    first = [e async for e in llm.stream(instructions="", messages=[], tools=[GIVE])]
    done = first[-1]
    assert isinstance(done, Done)
    call = done.items[-1]
    assert isinstance(call, ToolCall) and call.call_id and call.raw == '{"item": "X", "count": 1}'

    second = [e async for e in llm.stream(instructions="", messages=[], tools=[GIVE])]
    assert second[-1].text == "Хотя нет, такого у меня нет."
    third = [e async for e in llm.stream(instructions="", messages=[], tools=[GIVE])]
    assert third[-1].text == "Хотя нет, такого у меня нет."  # the last step repeats


async def test_fake_backend_makes_no_calls_with_tool_choice_none():
    llm = FakeBackend("Держи.", tool_calls=[ToolCall("give_item", {})], delay=0)
    events = [e async for e in llm.stream(instructions="", messages=[], tools=[GIVE], tool_choice="none")]
    assert not any(isinstance(e, ToolCall) for e in events)
    assert llm.tool_choices == ["none"]
