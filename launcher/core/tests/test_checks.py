import json

import httpx
import pytest

from gothic_launcher import checks
from gothic_launcher.settings import Config, Secrets, Settings

NO_CREDITS = "You have no credits remaining. Please add credits to continue using the service."


class Inworld:
    """Answers as the real one does: the key first, then the credits, then the voice. Records requests."""

    def __init__(self) -> None:
        self.key = "good-key"
        self.credits = True
        self.stream: tuple[int, dict] | None = None  # an answer the check does not know
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization") != f"Basic {self.key}":
            return httpx.Response(401, json={"code": 16, "message": "Invalid authorization credentials"})
        if request.url.path == "/tts/v1/voices":
            return httpx.Response(200, json={"voices": [{"voiceId": "Dmitry"}]})
        assert request.url.path == "/tts/v1/voice:stream"
        if self.stream is not None:
            return httpx.Response(self.stream[0], json=self.stream[1])
        if not self.credits:
            return httpx.Response(402, json={"error": {"code": 7, "message": NO_CREDITS, "details": []}})
        voice = json.loads(request.content)["voiceId"]
        return httpx.Response(404, json={"code": 5, "message": f"Unknown voice: {voice}"})


@pytest.fixture
def api() -> Inworld:
    return Inworld()


def config(tmp_path, key: str | None = "good-key") -> Config:
    settings = Settings()
    settings.tts.provider, settings.tts.fallback = "inworld", "none"
    return Config(settings, Secrets(inworld_api_key=key), tmp_path)


async def speech(api: Inworld, cfg: Config) -> dict:
    found = await checks.providers(cfg, transport=httpx.MockTransport(api))
    [inworld] = found["speech"]["providers"]
    return inworld


def inworld_requests(api: Inworld) -> list[httpx.Request]:
    return [r for r in api.requests if r.url.host == "api.inworld.ai"]


async def test_inworld_with_credits_is_available(api, tmp_path):
    assert await speech(api, config(tmp_path)) == {
        "id": "inworld",
        "ok": True,
        "title": "Inworld",
        "detail": "Доступен",
    }
    [request] = inworld_requests(api)  # one line for a voice that does not exist, nothing else
    assert request.method == "POST"
    assert json.loads(request.content)["voiceId"] == checks.INWORLD_CHECK["voiceId"]


async def test_inworld_without_credits_says_so(api, tmp_path):
    """The free voice list still answers 200 on an empty account; the check line gets 402."""
    api.credits = False
    found = await speech(api, config(tmp_path))
    assert not found["ok"]
    assert found["detail"].startswith("Нет кредитов")
    assert [r.method for r in inworld_requests(api)] == ["POST"]  # not saved by the voice list


async def test_inworld_wrong_key(api, tmp_path):
    found = await speech(api, config(tmp_path, key="old-key"))
    assert (found["ok"], found["detail"]) == (False, "Ключ не подходит")
    assert [r.method for r in inworld_requests(api)] == ["POST"]


async def test_inworld_unknown_answer_falls_back_to_the_voice_list(api, tmp_path):
    api.stream = (400, {"code": 3, "message": "something new"})
    found = await speech(api, config(tmp_path))
    assert (found["ok"], found["detail"]) == (True, "Доступен")
    assert [(r.method, r.url.path) for r in inworld_requests(api)] == [
        ("POST", "/tts/v1/voice:stream"),
        ("GET", "/tts/v1/voices"),
    ]


async def test_inworld_down(api, tmp_path):
    api.stream = (503, {"message": "unavailable"})
    found = await speech(api, config(tmp_path))
    assert (found["ok"], found["detail"]) == (False, "Ответил 503")
    assert len(inworld_requests(api)) == 1


async def test_inworld_without_key_asks_nothing(api, tmp_path):
    found = await speech(api, config(tmp_path, key=None))
    assert (found["ok"], found["detail"]) == (False, "Ключ не задан")
    assert inworld_requests(api) == []
