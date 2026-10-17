"""Bounded JSONL policy service. A service instance freezes one checkpoint."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import TextIO

from .runtime import LatestMailbox, MAX_REQUEST_BYTES, PolicyRuntime


def configure_serve_parser(subparsers) -> None:
    parser = subparsers.add_parser("serve", help="Linux JSONL policy runtime with frozen checkpoint")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--inference-cpu-threads", type=int, choices=(1, 2), default=2)
    parser.add_argument("--max-actors", type=int, default=16)
    parser.add_argument("--max-age-game-ms", type=int, default=100)
    parser.add_argument("--max-dt-game-ms", type=int, default=100)
    parser.add_argument("--journal", type=Path, help="Full actual request/response inference journal")
    parser.set_defaults(func=serve_main)


def serve_stream(runtime: PolicyRuntime, input_stream: TextIO, output_stream: TextIO,
                 *, journal: TextIO | None = None) -> int:
    mailbox = LatestMailbox(runtime, max_actors=runtime.max_actors)
    try:
        while True:
            line = input_stream.readline(MAX_REQUEST_BYTES+1)
            if not line:
                return 0
            response, request, request_id = {}, None, None
            try:
                if len(line.encode()) > MAX_REQUEST_BYTES or not line.endswith("\n"):
                    raise ValueError("JSONL request exceeds bounded reader or lacks newline")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("object request required")
                request_id = request.get("id")
                command = request.get("cmd", "observe")
                if command == "observe":
                    response = runtime.process(request)
                elif command == "submit":
                    response = mailbox.submit(request)
                elif command == "poll":
                    response = {"result": mailbox.poll(request["entity_id"])}
                elif command == "reset":
                    values = [request[key] for key in ("session_epoch", "world_epoch", "initial_game_ms")]
                    if any(type(value) is not int or value < 0 for value in values):
                        raise ValueError("invalid reset identity or game-time boundary")
                    # Stop and replace the mailbox at the episode barrier, so
                    # a response from the previous episode cannot be published.
                    mailbox.close()
                    runtime.begin_episode(request["session_epoch"], request["world_epoch"],
                                          initial_game_ms=request["initial_game_ms"])
                    mailbox = LatestMailbox(runtime, max_actors=runtime.max_actors)
                    response = {"reason": "episode_reset", "hidden_reset": True}
                elif command == "status":
                    response = {**runtime.status(), "mailbox_counters": dict(mailbox.counters)}
                elif command == "end_episode":
                    response = runtime.end_episode(request["session_epoch"], request["world_epoch"])
                    if response["accepted"]:
                        mailbox.close()
                        mailbox = LatestMailbox(runtime, max_actors=runtime.max_actors)
                elif command == "close":
                    response = {"reason": "closed"}
                else:
                    raise ValueError("unknown service command")
                response = {"ok": True, **response}
            except (ValueError, KeyError, TypeError, RuntimeError) as error:
                response = {"ok": False, "status": "fallback", "reason": "invalid_request", "action_id": None,
                            "model_processed": False, "error": str(error)[:512]}
            response.update(id=request_id, schema_version=1)
            if journal is not None:
                journal.write(json.dumps({"request": request, "response": response}, allow_nan=False, separators=(",", ":"))+"\n")
                journal.flush()
            output_stream.write(json.dumps(response, allow_nan=False, separators=(",", ":"))+"\n")
            output_stream.flush()
            if request and request.get("cmd") == "close":
                return 0
    finally:
        mailbox.close()


def serve_main(args: argparse.Namespace) -> int:
    if sys.platform != "linux":
        raise RuntimeError("models run only on dev-ubuntu, never on the Mac")
    runtime = PolicyRuntime(args.checkpoint, device=args.device, max_actors=args.max_actors,
                            max_age_game_ms=args.max_age_game_ms, max_dt_game_ms=args.max_dt_game_ms,
                            inference_cpu_threads=getattr(args, "inference_cpu_threads", 2))
    if args.journal:
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        with args.journal.open("x", encoding="utf-8") as journal:
            return serve_stream(runtime, sys.stdin, sys.stdout, journal=journal)
    return serve_stream(runtime, sys.stdin, sys.stdout)
