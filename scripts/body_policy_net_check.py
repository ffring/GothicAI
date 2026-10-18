#!/usr/bin/env python3
"""Replay a collector's periodic primary observations through the body service.

The net and load commands use only the standard library and run on the Mac.
Load replays independent NPC sessions at recorded wall-clock intervals.
The runtime command loads the same frozen checkpoint in-process on dev-ubuntu
to write an exact JSONL reference for net --expect.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import socket
import sys
import threading
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
        encoded = self.encode(request)
        self.socket.sendall(encoded)
        line = self.input.readline(MAX_REQUEST_BYTES + 1)
        return self.decode(request, line)

    @staticmethod
    def encode(request: dict) -> bytes:
        encoded = (json.dumps(request, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError("request exceeds MAX_REQUEST_BYTES")
        return encoded

    @staticmethod
    def decode(request: dict, line: bytes) -> dict:
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

    def call_until(self, request: dict, encoded: bytes, deadline: float) -> dict:
        """Load-only exchange with a total deadline, even for a trickled reply.

        Do not mix this raw socket reader with call()'s buffered reader within
        one connection. Load uses this method for every command in its session.
        """
        def remaining():
            seconds = deadline - time.perf_counter()
            if seconds <= 0:
                raise socket.timeout("call deadline exceeded")
            self.socket.settimeout(seconds)

        remaining()
        self.socket.sendall(encoded)
        line = bytearray()
        while b"\n" not in line and len(line) <= MAX_REQUEST_BYTES:
            remaining()
            chunk = self.socket.recv(MAX_REQUEST_BYTES + 1 - len(line))
            if not chunk:
                break
            line.extend(chunk)
        return self.decode(request, bytes(line))

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


def wait_until(target: float, deadline: float) -> bool:
    """Sleep against the absolute clock; never start work after the run ends."""
    delay = min(target, deadline) - time.perf_counter()
    if delay > 0:
        time.sleep(delay)
    return time.perf_counter() < deadline


def load_stream(index, phase, episodes, args, gate, clock, write_sample):
    counts = dict(stream=index, phase_s=phase, steps=0, responses=0, decisions=0,
                  fallback=0, timeouts=0, errors=0, reconnects=0, stale=0)
    timings = {"rtt_ms": [], "age_ms": [], "lag_ms": [], "minutes": {}}
    client = None
    connection_attempted = False
    episode_index = index % len(episodes)
    timeout = args.call_timeout_ms / 1000

    def failure(error):
        counts["timeouts" if isinstance(error, socket.timeout) else "errors"] += 1

    def call(request, encoded):
        reply = client.call_until(request, encoded, time.perf_counter() + timeout)
        if reply.get("ok") is not True:
            raise ValueError(f"request rejected: {reply.get('reason')}: {reply.get('error', '')}")
        return reply

    gate.wait()
    origin, deadline = clock["origin"], clock["deadline"]
    try:
        ready = wait_until(origin + phase, deadline)
        while ready and time.perf_counter() < deadline:
            path, requests = episodes[episode_index]
            try:
                if client is None:
                    reconnecting = connection_attempted
                    connection_attempted = True
                    client = NetworkClient(args.host, args.port, timeout)
                    counts["reconnects"] += int(reconnecting)
                if time.perf_counter() >= deadline:
                    break
                # Reset first: recurrent history always starts at the recording's
                # native boundary, including after a failed previous episode.
                call(*requests[0])
                episode_start = time.perf_counter()
                first_game_ms = requests[1][0]["now_game_ms"]
                for request, encoded in requests[1:]:
                    if request["cmd"] != "step":
                        if time.perf_counter() < deadline:
                            call(request, encoded)
                        break
                    scheduled = episode_start + (request["now_game_ms"] - first_game_ms) / 1000
                    if not wait_until(scheduled, deadline):
                        break
                    sent = time.perf_counter()
                    counts["steps"] += 1
                    lag = (sent - scheduled) * 1000
                    timings["lag_ms"].append(lag)
                    sample = dict(stream=index, episode=str(path), episode_index=episode_index,
                                  request_id=request["id"], t_game_ms=request["now_game_ms"],
                                  scheduled_s=scheduled - origin, sent_s=sent - origin, received_s=None,
                                  rtt_ms=None, lag_ms=lag, age_ms=None, stale=False,
                                  status=None, error=None)
                    try:
                        reply = call(request, encoded)
                        received = time.perf_counter()
                        rtt, age = (received - sent) * 1000, (received - scheduled) * 1000
                        stale = age > args.valid_for_ms
                        counts["responses"] += 1
                        counts["decisions"] += int(reply.get("status") == "decision"
                                                   and reply.get("action_id") is not None)
                        counts["fallback"] += int(reply.get("status") == "fallback")
                        counts["stale"] += int(stale)
                        timings["rtt_ms"].append(rtt)
                        timings["age_ms"].append(age)
                        minute = int((sent - origin) // 60)
                        timings["minutes"].setdefault(minute, []).append(rtt)
                        sample.update(received_s=received - origin, rtt_ms=rtt, age_ms=age,
                                      stale=stale, status=reply.get("status"))
                    except (OSError, ValueError) as error:
                        sample.update(status="timeout" if isinstance(error, socket.timeout) else "error",
                                      error=str(error))
                        raise
                    finally:
                        write_sample(sample)
            except (OSError, ValueError) as error:
                failure(error)
                if client is not None:
                    client.close()
                    client = None
            episode_index = (episode_index + 1) % len(episodes)
            ready = wait_until(time.perf_counter() + 1, deadline)
    finally:
        if client is not None:
            try:
                request = {"id": "load-close", "cmd": "close"}
                call(request, NetworkClient.encode(request))
            except (OSError, ValueError) as error:
                failure(error)
            finally:
                client.close()
    return counts, timings


def load(args) -> int:
    episodes = []
    for path in args.episode_dirs:
        requests = list(replay_requests(path / "observations.jsonl"))[:-1]
        steps = [request for request in requests if request["cmd"] == "step"]
        if not steps:
            raise ValueError(f"{path}: journal contains no periodic primary observations")
        if any(right["now_game_ms"] < left["now_game_ms"] for left, right in zip(steps, steps[1:])):
            raise ValueError(f"{path}: periodic primary observations must be ordered by t_game_ms")
        episodes.append((path, [(request, NetworkClient.encode(request)) for request in requests]))
    rng = random.Random(args.seed)
    phases = [rng.uniform(0, 1) for _ in range(args.streams)]
    gate, clock, sample_lock = threading.Event(), {}, threading.Lock()
    with ExitStack() as stack:
        samples = stack.enter_context(args.samples.open("w", encoding="utf-8")) if args.samples else None

        def write_sample(sample):
            if samples is not None:
                line = json.dumps(sample, allow_nan=False) + "\n"
                try:
                    with sample_lock:
                        samples.write(line)
                except OSError as error:
                    raise RuntimeError(f"cannot write load samples: {error}") from error

        with ThreadPoolExecutor(max_workers=args.streams) as executor:
            futures = [executor.submit(load_stream, index, phase, episodes, args, gate, clock, write_sample)
                       for index, phase in enumerate(phases)]
            started_at = datetime.now(timezone.utc)
            clock["origin"] = time.perf_counter()
            clock["deadline"] = clock["origin"] + args.duration_s
            gate.set()
            results = [future.result() for future in futures]
        ended_at = datetime.now(timezone.utc)
        elapsed = time.perf_counter() - clock["origin"]
    per_stream = [counts for counts, _ in results]
    totals = {key: sum(counts[key] for counts in per_stream)
              for key in ("steps", "responses", "decisions", "fallback", "timeouts", "errors", "reconnects", "stale")}
    summary = dict(mode="load", label=args.label or args.host, host=args.host, port=args.port,
                   streams=args.streams, duration_s=args.duration_s, elapsed_s=elapsed,
                   started_at=started_at.isoformat().replace("+00:00", "Z"),
                   ended_at=ended_at.isoformat().replace("+00:00", "Z"),
                   valid_for_ms=args.valid_for_ms, call_timeout_ms=args.call_timeout_ms, seed=args.seed,
                   **totals, stale_fraction=totals["stale"] / totals["responses"] if totals["responses"] else 0,
                   per_stream=per_stream)
    for name, fractions in (("rtt_ms", (0.50, 0.95, 0.99, 0.999)),
                            ("age_ms", (0.99,)), ("lag_ms", (0.99,))):
        values = [value for _, timings in results for value in timings[name]]
        summary[name] = {f"p{fraction * 100:g}": percentile(values, fraction) for fraction in fractions}
        summary[name]["max"] = max(values, default=None)
    summary["rtt_by_minute"] = []
    for minute in range(math.ceil(args.duration_s / 60)):
        values = [value for _, timings in results for value in timings["minutes"].get(minute, [])]
        summary["rtt_by_minute"].append(dict(minute=minute, steps=len(values), p99=percentile(values, 0.99)))
    encoded = json.dumps(summary, allow_nan=False)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


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
    load_parser = subparsers.add_parser(
        "load", help="replay concurrent NPC sessions in real time",
        description="RTT, age and stale_fraction use successful step replies; lag uses all step attempts. "
                    "Timeout/error counters also include connect, reset, end_episode and close failures. "
                    "The duration bounds step sends; in-flight calls and close may finish afterward.")
    load_parser.add_argument("episode_dirs", type=Path, nargs="+", metavar="EPISODE_DIR")
    load_parser.add_argument("--host", required=True)
    load_parser.add_argument("--port", type=int, default=47835)
    load_parser.add_argument("--streams", type=int, default=4)
    load_parser.add_argument("--duration-s", type=positive_float, default=600)
    load_parser.add_argument("--valid-for-ms", type=positive_float, default=100)
    load_parser.add_argument("--call-timeout-ms", type=positive_float, default=1000)
    load_parser.add_argument("--seed", type=int, default=0,
                             help="seed for each stream's initial delay, uniform in [0, 1) seconds")
    load_parser.add_argument("--label", help="host label in the summary; defaults to --host")
    load_parser.add_argument("--output", type=Path, help="write the JSON summary (also printed to stdout)")
    load_parser.add_argument("--samples", type=Path, help="write one JSONL row per attempted step")
    args = parser.parse_args(argv)
    if args.mode == "load":
        if args.streams <= 0:
            parser.error("--streams must be positive")
        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        outputs = [path.resolve() for path in (args.output, args.samples) if path is not None]
        if len(outputs) != len(set(outputs)):
            parser.error("--output and --samples must be different files")
        sources = {(path / name).resolve() for path in args.episode_dirs
                   for name in ("observations.jsonl", "arena-events.jsonl", "episode.json", "trajectory-manifest.json")}
        if any(path in sources for path in outputs):
            parser.error("load output files must differ from episode input files")
        try:
            return load(args)
        except (OSError, ValueError, RuntimeError) as error:
            print(str(error), file=sys.stderr)
            return 1
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
