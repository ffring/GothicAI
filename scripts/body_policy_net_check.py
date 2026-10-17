#!/usr/bin/env python3
"""Replay a collector's periodic primary observations through the body service.

The net command uses only the Python standard library and can run on the Mac.
The runtime command loads the same frozen checkpoint in-process on dev-ubuntu
to write an exact JSONL reference for net --expect.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import math
from pathlib import Path
import socket
import sys
import time

# Keep the Mac client independent of gothic_body (and of any model packages).
MAX_REQUEST_BYTES = 256 * 1024


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as error:
                raise ValueError(f"{path}:{number}: invalid JSON: {error}") from error
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{number}: JSON object required")
            yield value


def episode_initial(observations: Path) -> dict:
    """Use the native reset boundary, never guess the primary from row order."""
    arena_path = observations.parent / "arena-events.jsonl"
    initial = None
    if arena_path.exists():
        initial = next((row.get("arena") for row in read_jsonl(arena_path)
                        if row.get("kind") == "reset_ready"), None)
    if initial is None:
        for name in ("episode.json", "trajectory-manifest.json"):
            path = observations.parent / name
            if path.exists():
                initial = json.loads(path.read_text(encoding="utf-8")).get("initial")
                if initial is not None:
                    break
    if not isinstance(initial, dict):
        raise ValueError("episode reset metadata is missing; keep arena-events.jsonl beside observations.jsonl")
    for key in ("primary_entity_id", "session_epoch", "world_epoch", "game_ms"):
        if type(initial.get(key)) is not int or initial[key] < 0:
            raise ValueError(f"episode reset metadata has invalid {key}")
    return initial


def replay_requests(observations: Path):
    initial = episode_initial(observations)
    epochs = {key: initial[key] for key in ("session_epoch", "world_epoch")}
    request_id = 1
    yield {"id": request_id, "cmd": "reset", **epochs, "initial_game_ms": initial["game_ms"]}
    for row in read_jsonl(observations):
        if row.get("entity_id") != initial["primary_entity_id"] or row.get("periodic") is not True:
            continue
        now = row.get("t_game_ms")
        if type(now) is not int or now < 0:
            raise ValueError("periodic primary observation has invalid t_game_ms")
        request_id += 1
        # Recorded replay emulates delivery on the observation's own game tick,
        # as the per-tick collector does. Wall clocks never enter the protocol.
        yield {"id": request_id, "cmd": "step", "observation": row, "now_game_ms": now}
    yield {"id": request_id + 1, "cmd": "end_episode", **epochs}
    yield {"id": request_id + 2, "cmd": "close"}


class NetworkClient:
    def __init__(self, host: str, port: int, timeout: float):
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.input = self.socket.makefile("rb")

    def call(self, request: dict) -> dict:
        encoded = (json.dumps(request, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError("request exceeds MAX_REQUEST_BYTES")
        self.socket.sendall(encoded)
        line = self.input.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            raise ValueError("service disconnected before replying")
        if len(line) > MAX_REQUEST_BYTES or not line.endswith(b"\n"):
            raise ValueError("service response exceeds bounded JSONL reader or lacks newline")
        response = json.loads(line)
        if not isinstance(response, dict):
            raise ValueError("service response must be an object")
        if response.get("id") != request["id"]:
            raise ValueError(f"response id {response.get('id')!r} differs from request {request['id']!r}")
        return response

    def close(self):
        try:
            self.input.close()
        finally:
            self.socket.close()


class RuntimeReference:
    """Independent in-process driver for valid replay requests.

    Keep this separate from the TCP adapter: comparing a service against its
    own adapter would hide mistakes in stream bookkeeping or arbitration.
    """
    def __init__(self, runtime):
        self.runtime = runtime
        self.streams = {}

    def handle(self, request: dict) -> dict:
        command = request["cmd"]
        if command == "reset":
            self.runtime.begin_episode(request["session_epoch"], request["world_epoch"],
                                       initial_game_ms=request["initial_game_ms"])
            self.streams.clear()
            result = {"reason": "episode_reset", "hidden_reset": True}
        elif command == "step":
            row = request["observation"]
            identity = tuple(row[key] for key in ("session_epoch", "world_epoch", "entity_id", "control_generation"))
            entity = row["entity_id"]
            previous = self.streams.get(entity)
            sequence = previous[1] + 1 if previous is not None and previous[0] == identity else 1
            eligible = row.get("can_decide") is True and row.get("action_mask_valid") is True
            processed = self.runtime.process({
                "stream_seq": sequence, "observation": row, "source": "periodic_observer",
                "now_game_ms": request["now_game_ms"], "checkpoint_sha256": self.runtime.checkpoint_sha256,
                "command_enabled": eligible, "memory_only": not eligible,
            })
            # A stale generation or epoch must not rewind the active actor's
            # sequence. Only the identity accepted by the runtime owns it.
            if self.runtime.active.get(entity) == identity:
                self.streams[entity] = (identity, sequence)
            arbitration = None
            if processed["status"] == "fallback":
                status, action, reason = "fallback", None, processed["reason"]
            elif eligible:
                arbitration = self.runtime.arbitrate(row, stream_seq=sequence)
                status, action, reason = (arbitration[key] for key in ("status", "action_id", "reason"))
            else:
                status, action, reason = "locked", None, processed["reason"]
            result = {"process": {key: value for key, value in processed.items()
                                   if key not in ("inference_ms", "backend_timing")},
                      "arbitration": arbitration, "status": status, "action_id": action, "reason": reason}
        elif command == "status":
            result = self.runtime.status()
        elif command == "end_episode":
            result = self.runtime.end_episode(request["session_epoch"], request["world_epoch"])
            if result["accepted"]:
                self.streams.clear()
        elif command == "close":
            result = {"reason": "closed"}
        else:
            raise ValueError("unknown reference command")
        return {"ok": True, **result, "id": request["id"], "schema_version": 1}


def runtime_session(args):
    if sys.platform != "linux":
        raise RuntimeError("runtime mode runs only on dev-ubuntu; use net on the Mac")
    package = Path(__file__).resolve().parents[1] / "body-service" / "src"
    sys.path.insert(0, str(package))
    from gothic_body.runtime import PolicyRuntime

    runtime = PolicyRuntime(args.checkpoint, device="cpu", inference_cpu_threads=args.inference_cpu_threads)
    return RuntimeReference(runtime)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def difference(expected, actual, path="$") -> str | None:
    """Report the first exact field difference without rewriting any response."""
    if type(expected) is not type(actual):
        return f"{path}: expected {expected!r}, got {actual!r}"
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            return f"{path}: expected keys {sorted(expected)}, got {sorted(actual)}"
        for key in expected:
            result = difference(expected[key], actual[key], f"{path}.{key}")
            if result:
                return result
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: expected {len(expected)} items, got {len(actual)}"
        for index, (left, right) in enumerate(zip(expected, actual)):
            result = difference(left, right, f"{path}[{index}]")
            if result:
                return result
    elif expected != actual:
        return f"{path}: expected {expected!r}, got {actual!r}"
    return None


def check(args) -> int:
    expected = list(read_jsonl(args.expect)) if args.expect else None
    steps = decisions = fallbacks = response_count = mismatches = 0
    latencies = []
    first_mismatch = None
    with ExitStack() as stack:
        output = stack.enter_context(args.output.open("w", encoding="utf-8")) if args.output else None
        if args.mode == "net":
            client = NetworkClient(args.host, args.port, args.timeout)
            stack.callback(client.close)
            call = client.call
        else:
            call = runtime_session(args).handle
        for request in replay_requests(args.observations):
            started = time.perf_counter_ns()
            response = call(request)
            elapsed_ms = (time.perf_counter_ns() - started) / 1e6
            if request["cmd"] == "step":
                latencies.append(elapsed_ms)
                steps += 1
                decisions += int(response.get("status") == "decision" and response.get("action_id") is not None)
                fallbacks += int(response.get("status") == "fallback")
            if output:
                output.write(json.dumps(response, separators=(",", ":"), allow_nan=False) + "\n")
            if expected is not None:
                mismatch = (difference(expected[response_count], response) if response_count < len(expected)
                            else "unexpected extra response")
                if mismatch:
                    mismatches += 1
                    first_mismatch = first_mismatch or f"response {response_count + 1}: {mismatch}"
            if response.get("ok") is not True:
                raise ValueError(f"request {request['id']} rejected: {response.get('reason')}: {response.get('error', '')}")
            response_count += 1
    if steps == 0:
        raise ValueError("journal contains no periodic primary observations")
    if expected is not None and len(expected) > response_count:
        mismatches += len(expected) - response_count
        first_mismatch = first_mismatch or f"missing {len(expected) - response_count} expected responses"
    summary = {"mode": args.mode, "steps": steps, "decisions": decisions, "fallback": fallbacks,
               "responses": response_count,
               "rtt_ms" if args.mode == "net" else "runtime_ms": {
                   "p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95), "max": max(latencies)},
               "comparison": None if expected is None else {"matched": mismatches == 0, "mismatches": mismatches}}
    print(json.dumps(summary, allow_nan=False))
    if first_mismatch:
        print(first_mismatch[:1024], file=sys.stderr)
    return int(mismatches != 0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for name in ("net", "runtime"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("observations", type=Path, help="collector episode's observations.jsonl")
        subparser.add_argument("--output", type=Path, help="write every response, including episode boundaries, as JSONL")
        subparser.add_argument("--expect", type=Path, help="compare every response field with a reference JSONL file")
        if name == "net":
            subparser.add_argument("--host", required=True)
            subparser.add_argument("--port", type=int, default=47835)
            subparser.add_argument("--timeout", type=float, default=10, help="socket timeout in seconds")
        else:
            subparser.add_argument("--checkpoint", type=Path, required=True)
            subparser.add_argument("--inference-cpu-threads", type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    if args.output and args.expect and args.output.resolve() == args.expect.resolve():
        parser.error("--output and --expect must be different files")
    if args.output and args.output.resolve() == args.observations.resolve():
        parser.error("--output must differ from the observation journal")
    try:
        return check(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
