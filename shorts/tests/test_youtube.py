import json
import sys
import urllib.parse

import httpx
import pytest

from gothic_shorts import cli, youtube


def test_meta_from_json(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"youtube": {"ru": {"title": "Т", "description": "D", "tags": ["a"]}}}))
    m = youtube.load_meta(p, "ru")
    body = m.body()
    assert body["snippet"]["categoryId"] == youtube.GAMING
    assert body["snippet"]["defaultLanguage"] == "ru"
    assert body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                              "containsSyntheticMedia": False}
    with pytest.raises(youtube.YouTubeError):
        youtube.load_meta(p, "en")


def test_meta_by_language(tmp_path):
    p = tmp_path / "youtube.json"
    p.write_text(json.dumps({"en": {"title": "T", "description": "D", "synthetic": True}}))
    m = youtube.load_meta(p, "en")
    assert m.title == "T" and m.language == "en" and m.synthetic


def test_title_limit():
    with pytest.raises(youtube.YouTubeError):
        youtube.Meta(title="x" * 101, description="").body()


def test_auth_url_asks_offline_with_pkce():
    client = {"client_id": "id", "auth_uri": "https://a/auth"}
    url = youtube.auth_url(client, "http://127.0.0.1:5", "ch", "st")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert q["access_type"] == "offline" and q["code_challenge_method"] == "S256" and q["state"] == "st"
    assert set(q["scope"].split()) == set(youtube.SCOPES)


def test_upload_only_to_the_projects_channel():
    youtube.check_channel("UC1", "GothicAI", "UC1")
    with pytest.raises(youtube.YouTubeError):
        youtube.check_channel("UC2", "Kaifat", "UC1")


def test_privacy_read_back(monkeypatch):
    answers = {"v1": {"items": [{"status": {"privacyStatus": "public"}}]}, "v2": {"items": []}}

    def get(url, params, headers, timeout):
        return httpx.Response(200, json=answers[params["id"]])

    monkeypatch.setattr(youtube.httpx, "get", get)
    assert youtube.privacy("t", "v1") == "public"
    with pytest.raises(youtube.YouTubeError):
        youtube.privacy("t", "v2")


def test_one_video_in_every_language(tmp_path):
    p = tmp_path / "youtube.json"
    p.write_text(json.dumps({"ru": {"title": "Т", "description": "Д"},
                             "en": {"title": "T", "description": "D", "tags": ["x"]}}))  # fmt: skip
    body = youtube.load_meta(p, "ru").body()
    assert body["snippet"]["defaultLanguage"] == "ru"
    assert body["localizations"] == {"en": {"title": "T", "description": "D"}}
    # the sound is Russian whichever section leads
    en = youtube.load_meta(p, "en").body()
    assert en["snippet"]["defaultAudioLanguage"] == "ru" and en["localizations"] == {
        "ru": {"title": "Т", "description": "Д"}}
    long = youtube.Meta(title="t", description="",
                        localizations={"en": {"title": "x" * 101, "description": ""}})
    with pytest.raises(youtube.YouTubeError, match="en title"):
        long.body()


def test_translations_go_in_with_the_upload(monkeypatch, tmp_path):
    sent = {}

    def post(url, params, headers, content, timeout):
        sent.update(params=params, body=json.loads(content))
        return httpx.Response(400, text="stop here")

    monkeypatch.setattr(youtube.httpx, "post", post)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0")
    meta = youtube.Meta(title="Т", description="Д", localizations={"en": {"title": "T", "description": "D"}})
    with pytest.raises(youtube.YouTubeError):
        youtube.upload(video, meta, "t")
    assert sent["params"]["part"] == "snippet,status,localizations"
    assert sent["body"]["localizations"]["en"]["title"] == "T"
    with pytest.raises(youtube.YouTubeError):
        youtube.upload(video, youtube.Meta(title="Т", description="Д"), "t")
    assert sent["params"]["part"] == "snippet,status"


def test_captions_need_the_wider_sign_in(monkeypatch, tmp_path):
    token = tmp_path / "token.json"
    monkeypatch.setattr(youtube, "TOKEN", token)
    assert youtube.granted() == set()
    token.write_text(json.dumps({"scope": " ".join(youtube.SCOPES[:2])}))
    with pytest.raises(youtube.YouTubeError, match="youtube login"):
        youtube.check_captions_scope(youtube.granted())
    token.write_text(json.dumps({"scope": " ".join(youtube.SCOPES)}))
    youtube.check_captions_scope(youtube.granted())


def test_a_caption_track_is_one_multipart_request(monkeypatch, tmp_path):
    subs = tmp_path / "subs.en.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    got = {}

    def post(url, params, headers, content, timeout):
        got.update(url=url, params=params, type=headers["Content-Type"], content=content)
        return httpx.Response(200, json={"id": "c1"})

    monkeypatch.setattr(youtube.httpx, "post", post)
    assert youtube.add_captions("t", "v1", "en", subs) == "c1"
    assert got["url"] == youtube.UPLOAD_CAPTIONS and got["params"]["uploadType"] == "multipart"
    boundary = got["type"].split("boundary=")[1]
    meta, file = got["content"].split(f"--{boundary}".encode())[1:3]
    assert json.loads(meta.split(b"\r\n\r\n", 1)[1]) == {
        "snippet": {"videoId": "v1", "language": "en", "name": "", "isDraft": False}}
    assert file.split(b"\r\n\r\n", 1)[1] == subs.read_bytes() + b"\r\n"


def test_translations_read_back(monkeypatch):
    def get(url, params, headers, timeout):
        assert params["part"] == "localizations"
        return httpx.Response(200, json={"items": [{"localizations": {"en": {"title": "T"}}}]})

    monkeypatch.setattr(youtube.httpx, "get", get)
    assert youtube.translated("t", "v1") == {"en"}


def test_thumbnail_is_one_media_request(monkeypatch, tmp_path):
    file = tmp_path / "cover-ru.jpg"
    file.write_bytes(b"jpeg cover")
    got = {}

    def post(url, params, headers, content, timeout):
        got.update(url=url, params=params, headers=headers, content=content, timeout=timeout)
        return httpx.Response(200, json={"items": [{"default": {"url": "https://example.test/cover.jpg"}}]})

    monkeypatch.setattr(youtube.httpx, "post", post)
    youtube.set_thumbnail("t", "v1", file)
    assert got == {
        "url": "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
        "params": {"videoId": "v1", "uploadType": "media"},
        "headers": {"Authorization": "Bearer t", "Content-Type": "image/jpeg"},
        "content": file.read_bytes(), "timeout": 120,
    }


def test_thumbnail_over_limit_never_sends_a_request(monkeypatch, tmp_path):
    file = tmp_path / "large.jpg"
    file.write_bytes(b"0" * (youtube.THUMBNAIL_LIMIT + 1))

    def post(*args, **kwargs):
        pytest.fail("an oversized thumbnail must not be sent")

    monkeypatch.setattr(youtube.httpx, "post", post)
    with pytest.raises(youtube.YouTubeError, match="2 MB"):
        youtube.set_thumbnail("t", "v1", file)


def _mock_thumbnail_api(monkeypatch, thumbnail_status=200):
    """Exercise the CLI and the real request-building helpers without a Google connection."""
    calls = []
    monkeypatch.setattr(youtube, "access_token", lambda: "t")

    def get(url, params, headers, timeout):
        assert headers["Authorization"] == "Bearer t"
        if url == f"{youtube.API}/channels":
            calls.append("channel")
            assert params == {"part": "snippet", "mine": "true"}
            return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "GothicAI"}}]})
        assert url == f"{youtube.API}/videos" and params == {"part": "status", "id": "v1"}
        calls.append("privacy")
        return httpx.Response(200, json={"items": [{"status": {"privacyStatus": "private"}}]})

    def post(url, params, headers, content, timeout):
        assert headers["Authorization"] == "Bearer t"
        if url == youtube.UPLOAD:
            calls.append("upload")
            assert params["uploadType"] == "resumable"
            return httpx.Response(200, headers={"location": "https://example.test/session"})
        assert url == youtube.UPLOAD_THUMBNAIL
        calls.append("thumbnail")
        assert params == {"videoId": "v1", "uploadType": "media"}
        assert headers["Content-Type"] == "image/jpeg" and content == b"jpeg cover"
        return httpx.Response(thumbnail_status, json={"error": {"message": "thumbnail forbidden"}})

    def put(url, content, timeout, headers):
        calls.append("video bytes")
        assert url == "https://example.test/session" and content == b"video"
        return httpx.Response(200, json={"id": "v1"})

    monkeypatch.setattr(youtube.httpx, "get", get)
    monkeypatch.setattr(youtube.httpx, "post", post)
    monkeypatch.setattr(youtube.httpx, "put", put)
    return calls


@pytest.mark.parametrize("thumbnail_status", [200, 403])
def test_upload_cli_sets_thumbnail_and_survives_403(monkeypatch, tmp_path, capsys, thumbnail_status):
    calls = _mock_thumbnail_api(monkeypatch, thumbnail_status)
    video = tmp_path / "short-ru.mp4"
    video.write_bytes(b"video")
    file = tmp_path / "cover-ru.jpg"
    file.write_bytes(b"jpeg cover")
    meta = tmp_path / "youtube.json"
    meta.write_text(json.dumps({"ru": {"title": "Т", "description": "Д"}}))
    monkeypatch.setattr(sys, "argv", ["gothic-shorts", "upload", str(video), "--meta", str(meta),
                                      "--thumbnail", str(file), "--channel", "UC1", "--yes"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 0
    assert calls == ["channel", "upload", "video bytes", "thumbnail", "privacy"]
    output = capsys.readouterr()
    assert "uploaded: https://youtu.be/v1" in output.out
    if thumbnail_status == 200:
        assert f"thumbnail: {file}" in output.out
        assert output.err == ""
    else:
        assert "thumbnails.set: 403" in output.err
        assert "choose a cover frame in the YouTube mobile app" in output.err


@pytest.mark.parametrize("confirmed", [True, False])
def test_thumbnail_cli_checks_channel_and_asks(monkeypatch, tmp_path, capsys, confirmed):
    calls = _mock_thumbnail_api(monkeypatch)
    file = tmp_path / "cover-ru.jpg"
    file.write_bytes(b"jpeg cover")
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return "y" if confirmed else "n"

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(sys, "argv", ["gothic-shorts", "thumbnail", "v1", str(file), "--channel", "UC1"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == (0 if confirmed else 1)
    assert prompts == ["set the thumbnail? [y/N] "]
    assert calls == (["channel", "thumbnail"] if confirmed else ["channel"])
    output = capsys.readouterr().out
    assert "channel: GothicAI (UC1)" in output
    assert ("thumbnail set:" if confirmed else "not set") in output


def test_thumbnail_cli_yes_does_not_prompt(monkeypatch, tmp_path):
    calls = _mock_thumbnail_api(monkeypatch)
    file = tmp_path / "cover-ru.jpg"
    file.write_bytes(b"jpeg cover")

    def answer(prompt):
        pytest.fail("--yes must not prompt")

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(sys, "argv", ["gothic-shorts", "thumbnail", "v1", str(file),
                                      "--channel", "UC1", "--yes"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 0 and calls == ["channel", "thumbnail"]


def test_thumbnail_cli_refuses_another_channel(monkeypatch, tmp_path, capsys):
    calls = _mock_thumbnail_api(monkeypatch)
    file = tmp_path / "cover-ru.jpg"
    file.write_bytes(b"jpeg cover")
    monkeypatch.setattr(sys, "argv", ["gothic-shorts", "thumbnail", "v1", str(file),
                                      "--channel", "UC2", "--yes"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 1 and calls == ["channel"]
    assert "not UC2" in capsys.readouterr().err
