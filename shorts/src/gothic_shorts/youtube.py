"""Uploads a short to YouTube with the Data API v3: the sign-in of an installed app (the browser opens once,
the token stays next to the client's file, outside the repository), a resumable upload, private by default.

An API project that has not passed YouTube's audit can only upload private videos: the user makes them
public in YouTube Studio. The client's file is the OAuth client of the Google Cloud project (a Desktop
app), downloaded from the console.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import httpx

HOME = Path(os.environ.get("GOTHIC_YOUTUBE_DIR", Path.home() / ".local/share/gothic-ai"))
CLIENT = HOME / "youtube-client.json"
TOKEN = HOME / "youtube-token.json"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",  # whose channel it is, before uploading
)
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK = 8 * 1024 * 1024
GAMING = "20"


class YouTubeError(Exception):
    pass


@dataclass
class Meta:
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    language: str = "ru"
    synthetic: bool = False  # altered or synthetic content (AI voices that sound real)
    privacy: str = "private"

    def body(self) -> dict:
        if len(self.title) > 100:
            raise YouTubeError(f"the title is {len(self.title)} characters, YouTube takes 100")
        return {
            "snippet": {
                "title": self.title,
                "description": self.description,
                "tags": self.tags,
                "categoryId": GAMING,
                "defaultLanguage": self.language,
                "defaultAudioLanguage": self.language,
            },
            "status": {
                "privacyStatus": self.privacy,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": self.synthetic,
            },
        }


def load_meta(path: Path, lang: str) -> Meta:
    """The `youtube.<lang>` section of a short's JSON (shorts/compare/<name>.json)"""
    data = json.loads(path.read_text())
    try:
        yt = data["youtube"][lang]
    except KeyError as err:
        raise YouTubeError(f"{path} has no youtube.{lang}") from err
    return Meta(title=yt["title"], description=yt["description"], tags=yt.get("tags", []), language=lang,
                synthetic=bool(yt.get("synthetic", False)))


def _client() -> dict:
    if not CLIENT.is_file():
        raise YouTubeError(f"no OAuth client at {CLIENT} (Google Cloud: a Desktop app client, Download JSON)")
    data = json.loads(CLIENT.read_text())
    return data.get("installed") or data.get("web") or data


def _save(token: dict) -> None:
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(json.dumps(token))
    TOKEN.chmod(0o600)


def auth_url(client: dict, redirect: str, challenge: str, state: str) -> str:
    q = {
        "client_id": client["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    base = client.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")
    return base + "?" + urllib.parse.urlencode(q)


def _sign_in(client: dict) -> dict:
    """The browser opens Google's consent page; the answer comes back to a port on this machine"""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if "code" in q or "error" in q:
                got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done, the window can be closed.")

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = auth_url(client, redirect, challenge, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"opening Google's consent page; if no browser opens, go to:\n{url}")
    webbrowser.open(url)
    try:
        deadline = time.monotonic() + 300
        while not got and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        server.shutdown()
    if not got:
        raise YouTubeError("no answer from the consent page in 5 minutes")
    if "error" in got:
        raise YouTubeError(f"consent refused: {got['error']}")
    if got.get("state") != state:
        raise YouTubeError("the consent page answered with another state")
    r = httpx.post(client["token_uri"], data={
        "code": got["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier,
    }, timeout=30)
    if r.status_code != 200:
        raise YouTubeError(f"token exchange failed: {r.status_code} {r.text[:200]}")
    token = r.json()
    token["expires_at"] = time.time() + token.get("expires_in", 3600) - 60
    _save(token)
    return token


def access_token(*, sign_in: bool = True) -> str:
    client = _client()
    token = json.loads(TOKEN.read_text()) if TOKEN.is_file() else None
    if token and token.get("expires_at", 0) > time.time():
        return token["access_token"]
    if token and token.get("refresh_token"):
        r = httpx.post(client["token_uri"], data={
            "client_id": client["client_id"], "client_secret": client["client_secret"],
            "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
        }, timeout=30)
        if r.status_code == 200:
            fresh = r.json()
            token.update(fresh)
            token["expires_at"] = time.time() + fresh.get("expires_in", 3600) - 60
            _save(token)
            return token["access_token"]
        # a test app's refresh token expires after 7 days: sign in again
    if not sign_in:
        raise YouTubeError("not signed in: gothic-shorts youtube login")
    return _sign_in(client)["access_token"]


def channel(token: str) -> tuple[str, str]:
    r = httpx.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                  headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.status_code != 200:
        raise YouTubeError(f"channels.list: {r.status_code} {r.text[:300]}")
    items = r.json().get("items", [])
    if not items:
        raise YouTubeError("this Google account has no YouTube channel")
    return items[0]["id"], items[0]["snippet"]["title"]


def upload(video: Path, meta: Meta, token: str) -> str:
    """A resumable upload in chunks; the id of the new video"""
    size = video.stat().st_size
    r = httpx.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                   headers={"Authorization": f"Bearer {token}",
                            "Content-Type": "application/json; charset=UTF-8",
                            "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                   content=json.dumps(meta.body()).encode(), timeout=60)
    if r.status_code != 200 or "location" not in r.headers:
        raise YouTubeError(f"videos.insert: {r.status_code} {r.text[:300]}")
    where = r.headers["location"]
    sent = 0
    with video.open("rb") as f:
        while sent < size:
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            r = httpx.put(where, content=chunk, timeout=300,
                          headers={"Authorization": f"Bearer {token}", "Content-Length": str(len(chunk)),
                                   "Content-Range": f"bytes {sent}-{end}/{size}"})
            if r.status_code in (200, 201):
                return r.json()["id"]
            if r.status_code != 308:
                raise YouTubeError(f"upload: {r.status_code} {r.text[:300]}")
            got = r.headers.get("range")  # bytes=0-N: what the server has
            sent = int(got.split("-")[1]) + 1 if got else 0
            f.seek(sent)
            print(f"  {sent * 100 // size}%")
    raise YouTubeError("the upload ended without a video id")
