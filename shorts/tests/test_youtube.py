import json
import urllib.parse

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
    youtube.check_channel("gothic ai", "Gothic AI")
    with pytest.raises(youtube.YouTubeError):
        youtube.check_channel("Kaifat", "Gothic AI")
