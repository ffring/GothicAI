import json
import urllib.parse

import httpx
import pytest

from gothic_shorts import youtube


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
