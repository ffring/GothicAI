"""Stand-in for `codex app-server` (stdio JSON-RPC), websocket transport: speaks every appendSpeech
as 20 ms audio deltas per character, then a final assistant transcript."""

import base64
import json
import sys


def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


log = next((open(a, "a") for a in sys.argv[1:] if a.endswith(".log")), None)
for line in sys.stdin:
    msg = json.loads(line)
    if log:
        log.write(line)
        log.flush()
    method, req, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if req is None:
        continue  # notifications
    if method == "initialize":
        send({"id": req, "result": {"userAgent": "fake"}})
        send({"method": "remoteControl/status/changed", "params": {}})
    elif method == "thread/start":
        send({"id": req, "result": {"thread": {"id": "thread-1"}}})
    elif method == "thread/realtime/start":
        send({"id": req, "result": {}})
        send({"method": "thread/realtime/started", "params": {"threadId": "thread-1"}})
    elif method == "thread/realtime/appendSpeech":
        send({"id": req, "result": {}})
        chunk = base64.b64encode(b"\x10\x27" * 480).decode()  # 20 ms at 24 kHz, loud
        for _ in params["text"]:
            send(
                {
                    "method": "thread/realtime/outputAudio/delta",
                    "params": {
                        "threadId": "thread-1",
                        "audio": {"data": chunk, "sampleRate": 24000, "numChannels": 1},
                    },
                }
            )
        send(
            {
                "method": "thread/realtime/transcript/done",
                "params": {"threadId": "thread-1", "role": "assistant", "text": params["text"]},
            }
        )
    else:
        send({"id": req, "result": {}})
