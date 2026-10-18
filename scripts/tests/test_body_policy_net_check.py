"""The Mac replay client reads collector journals and compares complete replies."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import random
import socket
import socketserver
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import body_policy_net_check as check


def write_jsonl(path, values):
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def write_episode(path):
    initial = {"primary_entity_id": 7, "session_epoch": 2, "world_epoch": 3, "game_ms": 100}
    write_jsonl(path / "arena-events.jsonl", [{"kind": "reset_ready", "arena": initial}])
    rows = [
        {"entity_id": 8, "periodic": True, "t_game_ms": 100},
        {"entity_id": 7, "periodic": False, "t_game_ms": 110},
        {"entity_id": 7, "periodic": True, "t_game_ms": 100, "can_decide": False},
        {"entity_id": 7, "periodic": True, "t_game_ms": 150, "can_decide": True},
        {"entity_id": 7, "periodic": True, "t_game_ms": 200, "can_decide": False},
    ]
    write_jsonl(path / "observations.jsonl", rows)
    return path / "observations.jsonl"


def response_for(request):
    response = {"id": request["id"], "schema_version": 1, "ok": True, "reason": request["cmd"]}
    if request["cmd"] == "step":
        decide = request["observation"]["can_decide"]
        response.update(status="decision" if decide else "locked", action_id=6 if decide else None,
                        process={"effective_action_mask": [False, True], "scores": [0.123, 0.456]})
    return response


@contextmanager
def service(*, wrong_id=False):
    requests = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                request = json.loads(line)
                requests.append(request)
                response = response_for(request)
                if wrong_id:
                    response["id"] += 1
                self.wfile.write((json.dumps(response) + "\n").encode())
                self.wfile.flush()
                if request["cmd"] == "close":
                    break

    with socketserver.ThreadingTCPServer(("localhost", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], requests
        finally:
            server.shutdown()
            thread.join()


def write_load_episode(path, *, times_ms, epoch=2):
    path.mkdir()
    initial = {"primary_entity_id": 7, "session_epoch": epoch,
               "world_epoch": 3, "game_ms": times_ms[0]}
    write_jsonl(path / "arena-events.jsonl", [{"kind": "reset_ready", "arena": initial}])
    rows = [{"entity_id": 8, "periodic": True, "t_game_ms": times_ms[0]},
            {"entity_id": 7, "periodic": False, "t_game_ms": times_ms[0]}]
    rows.extend({"entity_id": 7, "periodic": True, "t_game_ms": tick,
                 "can_decide": index % 2 == 0} for index, tick in enumerate(times_ms))
    write_jsonl(path / "observations.jsonl", rows)
    return path


@contextmanager
def load_service(respond=None, *, trickle_interval=None):
    """Record separate sessions and optionally delay, alter or drop a reply."""
    requests = []
    connections = []
    lock = threading.Lock()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            with lock:
                connection = len(connections)
                connections.append(connection)
            for line in self.rfile:
                request = json.loads(line)
                entry = {"connection": connection, "request": request,
                         "received_at": time.monotonic(), "thread": threading.get_ident()}
                with lock:
                    requests.append(entry)
                delay, response = (respond(entry, response_for(request)) if respond
                                   else (0, response_for(request)))
                if delay:
                    time.sleep(delay)
                if response is None:
                    return
                try:
                    encoded = (json.dumps(response) + "\n").encode()
                    if trickle_interval is None:
                        self.wfile.write(encoded)
                        self.wfile.flush()
                    else:
                        for byte in encoded:
                            self.wfile.write(bytes((byte,)))
                            self.wfile.flush()
                            time.sleep(trickle_interval)
                except (BrokenPipeError, ConnectionResetError):
                    return
                if request["cmd"] == "close":
                    return

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("localhost", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], requests, connections
        finally:
            server.shutdown()
            thread.join()


class ReplayCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.observations = write_episode(self.folder)

    def test_replay_preserves_all_periodic_primary_rows_and_native_reset(self):
        requests = list(check.replay_requests(self.observations))
        self.assertEqual(requests[0], {"id": 1, "cmd": "reset", "session_epoch": 2,
                                       "world_epoch": 3, "initial_game_ms": 100})
        self.assertEqual([request["cmd"] for request in requests],
                         ["reset", "step", "step", "step", "end_episode", "close"])
        self.assertEqual([request["id"] for request in requests], list(range(1, 7)))
        self.assertEqual([request["now_game_ms"] for request in requests if request["cmd"] == "step"], [100, 150, 200])

    def test_exact_network_reference_comparison_and_step_latency_count(self):
        expected = self.folder / "expected.jsonl"
        output = self.folder / "network.jsonl"
        write_jsonl(expected, [response_for(request) for request in check.replay_requests(self.observations)])
        with service() as (port, requests), redirect_stdout(io.StringIO()) as stdout:
            result = check.main(["net", str(self.observations), "--host", "localhost", "--port", str(port),
                                 "--expect", str(expected), "--output", str(output)])
        self.assertEqual(result, 0)
        self.assertEqual(list(check.read_jsonl(output)), list(check.read_jsonl(expected)))
        self.assertEqual(requests, list(check.replay_requests(self.observations)))
        summary = json.loads(stdout.getvalue())
        self.assertEqual((summary["steps"], summary["decisions"], summary["fallback"], summary["responses"]), (3, 1, 0, 6))
        self.assertEqual(summary["comparison"], {"matched": True, "mismatches": 0})
        self.assertEqual(set(summary["rtt_ms"]), {"p50", "p95", "max"})

    def test_a_nested_non_action_field_difference_fails_comparison(self):
        expected = self.folder / "expected.jsonl"
        replies = [response_for(request) for request in check.replay_requests(self.observations)]
        replies[2]["process"]["scores"][0] = 0.124
        write_jsonl(expected, replies)
        with service() as (port, _), redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            result = check.main(["net", str(self.observations), "--host", "localhost", "--port", str(port),
                                 "--expect", str(expected)])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(stdout.getvalue())["comparison"], {"matched": False, "mismatches": 1})
        self.assertIn("response 3: $.process.scores[0]", stderr.getvalue())

    def test_misordered_response_id_is_rejected(self):
        with service(wrong_id=True) as (port, _):
            client = check.NetworkClient("localhost", port, timeout=2)
            try:
                with self.assertRaisesRegex(ValueError, "differs from request"):
                    client.call({"cmd": "status", "id": 1})
            finally:
                client.close()

    def test_primary_is_never_inferred_from_first_observation(self):
        (self.folder / "arena-events.jsonl").unlink()
        with self.assertRaisesRegex(ValueError, "reset metadata is missing"):
            list(check.replay_requests(self.observations))


class LoadCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def run_load(self, port, episodes, *options):
        with redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
            result = check.main(["load", *(str(episode) for episode in episodes),
                                 "--host", "localhost", "--port", str(port), *options])
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        return json.loads(stdout.getvalue())

    def test_absolute_schedule_four_sessions_and_summary_samples_format(self):
        episodes = [write_load_episode(self.folder / f"episode-{index}",
                                      times_ms=list(range(100, 4100, 200)), epoch=100 + index)
                    for index in range(4)]
        output = self.folder / "summary.json"
        sample_path = self.folder / "samples.jsonl"

        def delayed(entry, response):
            return (0.03 if entry["request"]["cmd"] == "step" else 0), response

        with load_service(delayed) as (port, requests, connections):
            summary = self.run_load(port, episodes, "--streams", "4", "--duration-s", "2",
                                    "--seed", "0", "--label", "fake-host", "--output", str(output),
                                    "--samples", str(sample_path))
        samples = list(check.read_jsonl(sample_path))
        self.assertEqual(summary, json.loads(output.read_text(encoding="utf-8")))
        self.assertEqual(len(connections), 4)
        self.assertEqual(len({entry["thread"] for entry in requests}), 4)
        self.assertEqual((summary["mode"], summary["label"], summary["host"], summary["port"]),
                         ("load", "fake-host", "localhost", port))
        self.assertEqual((summary["streams"], summary["duration_s"], summary["seed"]), (4, 2, 0))
        self.assertEqual((summary["valid_for_ms"], summary["call_timeout_ms"]), (100, 1000))
        self.assertGreaterEqual(summary["elapsed_s"], 1.95)
        self.assertLess(summary["elapsed_s"], 2.4)
        start, end = (datetime.fromisoformat(summary[key].replace("Z", "+00:00"))
                      for key in ("started_at", "ended_at"))
        self.assertEqual(start.tzinfo, timezone.utc)
        self.assertEqual(end.tzinfo, timezone.utc)
        self.assertLess(start, end)
        self.assertEqual((summary["steps"], len(samples)), (30, 30))
        self.assertEqual(summary["responses"], 30)
        self.assertEqual((summary["timeouts"], summary["errors"], summary["reconnects"],
                          summary["fallback"], summary["stale"]), (0, 0, 0, 0, 0))
        self.assertEqual(summary["stale_fraction"], 0)
        self.assertEqual(set(summary["rtt_ms"]), {"p50", "p95", "p99", "p99.9", "max"})
        self.assertEqual(set(summary["age_ms"]), {"p99", "max"})
        self.assertEqual(set(summary["lag_ms"]), {"p99", "max"})
        self.assertGreater(summary["rtt_ms"]["p50"], 20)
        self.assertLess(summary["lag_ms"]["p99"], 80)
        self.assertEqual(len(summary["rtt_by_minute"]), 1)
        minute = summary["rtt_by_minute"][0]
        self.assertEqual((minute["minute"], minute["steps"]), (0, 30))
        self.assertEqual(minute["p99"], summary["rtt_ms"]["p99"])

        expected_fields = {"stream", "episode", "episode_index", "request_id", "t_game_ms",
                           "scheduled_s", "sent_s", "received_s", "rtt_ms", "lag_ms",
                           "age_ms", "stale", "status", "error"}
        phases = random.Random(0)
        for stream, expected_steps in enumerate((6, 7, 8, 9)):
            rows = [sample for sample in samples if sample["stream"] == stream]
            counts = next(row for row in summary["per_stream"] if row["stream"] == stream)
            self.assertEqual((counts["steps"], counts["stale"]), (expected_steps, 0))
            self.assertAlmostEqual(counts["phase_s"], phases.uniform(0, 1))
            self.assertEqual(len(rows), expected_steps)
            for index, row in enumerate(rows):
                self.assertTrue(expected_fields.issubset(row))
                self.assertEqual((row["episode"], row["episode_index"]), (str(episodes[stream]), stream))
                self.assertEqual((row["request_id"], row["t_game_ms"]), (index + 2, 100 + 200 * index))
                self.assertAlmostEqual(row["scheduled_s"] - rows[0]["scheduled_s"], 0.2 * index)
                self.assertLess(row["scheduled_s"], 2)
                self.assertAlmostEqual(row["rtt_ms"], (row["received_s"] - row["sent_s"]) * 1000)
                self.assertAlmostEqual(row["age_ms"], (row["received_s"] - row["scheduled_s"]) * 1000)
                self.assertAlmostEqual(row["lag_ms"], max(0, (row["sent_s"] - row["scheduled_s"]) * 1000))
                self.assertIn(row["status"], ("decision", "locked"))
                self.assertIsNone(row["error"])
                self.assertFalse(row["stale"])
        self.assertEqual(summary["decisions"], sum(row["status"] == "decision" for row in samples))
        resets = [entry["request"]["session_epoch"] for entry in requests
                  if entry["request"]["cmd"] == "reset"]
        self.assertEqual(sorted(resets), [100, 101, 102, 103])
        for connection in connections:
            commands = [entry["request"]["cmd"] for entry in requests
                        if entry["connection"] == connection]
            self.assertEqual((commands[0], commands[-1]), ("reset", "close"))

    def test_slow_step_replies_are_stale_including_locked_and_fallback(self):
        episode = write_load_episode(self.folder / "episode", times_ms=[100, 180, 260, 340])
        sample_path = self.folder / "samples.jsonl"

        def delayed(entry, response):
            request = entry["request"]
            if request["cmd"] == "step":
                if request["now_game_ms"] == 260:
                    response.update(status="fallback", action_id=None, reason="test_fallback")
                return 0.035, response
            return 0, response

        with load_service(delayed) as (port, _, _):
            summary = self.run_load(port, [episode], "--streams", "1", "--duration-s", "0.65",
                                    "--seed", "1", "--valid-for-ms", "15", "--samples", str(sample_path))
        samples = list(check.read_jsonl(sample_path))
        self.assertEqual((summary["steps"], summary["decisions"], summary["fallback"]), (4, 1, 1))
        self.assertEqual(summary["responses"], 4)
        self.assertEqual((summary["stale"], summary["stale_fraction"]), (4, 1))
        self.assertEqual((summary["timeouts"], summary["errors"]), (0, 0))
        self.assertEqual(summary["per_stream"][0]["stale"], 4)
        self.assertEqual([row["status"] for row in samples], ["decision", "locked", "fallback", "locked"])
        self.assertTrue(all(row["stale"] and row["age_ms"] > 15 for row in samples))
        self.assertGreater(summary["age_ms"]["p99"], 25)

    def test_timeout_reconnects_and_continues_at_next_episode(self):
        episodes = [write_load_episode(self.folder / f"episode-{index}",
                                      times_ms=[100, 200, 300], epoch=20 + index)
                    for index in range(2)]
        sample_path = self.folder / "samples.jsonl"

        def timeout_first_session(entry, response):
            delay = 0.2 if entry["connection"] == 0 and entry["request"]["cmd"] == "step" else 0
            return delay, response

        with load_service(timeout_first_session) as (port, requests, connections):
            summary = self.run_load(port, episodes, "--streams", "1", "--duration-s", "1.65",
                                    "--seed", "1", "--call-timeout-ms", "50",
                                    "--samples", str(sample_path))
        samples = list(check.read_jsonl(sample_path))
        self.assertEqual((summary["steps"], summary["timeouts"], summary["errors"], summary["reconnects"]),
                         (4, 1, 0, 1))
        self.assertEqual(summary["responses"], 3)
        self.assertEqual(summary["rtt_by_minute"][0]["steps"], 3)
        self.assertEqual(len(connections), 2)
        self.assertEqual([sample["episode_index"] for sample in samples], [0, 1, 1, 1])
        self.assertEqual(samples[0]["status"], "timeout")
        self.assertIsNotNone(samples[0]["error"])
        self.assertIsNone(samples[0]["received_s"])
        self.assertIsNone(samples[0]["rtt_ms"])
        self.assertIsNone(samples[0]["age_ms"])
        self.assertFalse(samples[0]["stale"])
        self.assertTrue(all(sample["status"] in ("decision", "locked") for sample in samples[1:]))
        first = [entry["request"]["cmd"] for entry in requests if entry["connection"] == 0]
        second = [entry["request"] for entry in requests if entry["connection"] == 1]
        self.assertEqual(first, ["reset", "step"])
        self.assertEqual([request["cmd"] for request in second],
                         ["reset", "step", "step", "step", "end_episode", "close"])
        self.assertEqual(second[0]["session_epoch"], 21)
        self.assertGreaterEqual(samples[1]["sent_s"] - samples[0]["sent_s"], 1)

    def test_disconnect_is_an_error_and_does_not_abort_other_episodes(self):
        episodes = [write_load_episode(self.folder / f"episode-{index}",
                                      times_ms=[100, 200, 300], epoch=30 + index)
                    for index in range(2)]
        sample_path = self.folder / "samples.jsonl"

        def disconnect_first_session(entry, response):
            if entry["connection"] == 0 and entry["request"]["cmd"] == "step":
                return 0, None
            return 0, response

        with load_service(disconnect_first_session) as (port, requests, connections):
            summary = self.run_load(port, episodes, "--streams", "1", "--duration-s", "1.65",
                                    "--seed", "1", "--samples", str(sample_path))
        samples = list(check.read_jsonl(sample_path))
        self.assertEqual((summary["steps"], summary["timeouts"], summary["errors"], summary["reconnects"]),
                         (4, 0, 1, 1))
        self.assertEqual(summary["responses"], 3)
        self.assertEqual(len(connections), 2)
        self.assertEqual(samples[0]["status"], "error")
        self.assertIsNotNone(samples[0]["error"])
        self.assertIsNone(samples[0]["rtt_ms"])
        self.assertIsNone(samples[0]["age_ms"])
        self.assertEqual([sample["episode_index"] for sample in samples], [0, 1, 1, 1])
        self.assertEqual([entry["request"]["session_epoch"] for entry in requests
                          if entry["request"]["cmd"] == "reset"], [30, 31])

    def test_completed_episodes_repeat_in_order_with_a_one_second_pause(self):
        episodes = [write_load_episode(self.folder / f"episode-{index}",
                                      times_ms=[100], epoch=40 + index)
                    for index in range(2)]
        with load_service() as (port, requests, connections):
            summary = self.run_load(port, episodes, "--streams", "1", "--duration-s", "2.45",
                                    "--seed", "1")
        self.assertEqual(summary["label"], "localhost")
        self.assertEqual((summary["steps"], summary["responses"], summary["timeouts"],
                          summary["errors"], summary["reconnects"]), (3, 3, 0, 0, 0))
        self.assertEqual(len(connections), 1)
        self.assertEqual([entry["request"]["cmd"] for entry in requests],
                         ["reset", "step", "end_episode"] * 3 + ["close"])
        resets = [entry for entry in requests if entry["request"]["cmd"] == "reset"]
        ends = [entry for entry in requests if entry["request"]["cmd"] == "end_episode"]
        self.assertEqual([entry["request"]["session_epoch"] for entry in resets], [40, 41, 40])
        for previous_end, next_reset in zip(ends, resets[1:]):
            self.assertGreaterEqual(next_reset["received_at"] - previous_end["received_at"], 0.98)

    def test_partial_reply_does_not_extend_the_total_call_timeout(self):
        with load_service(trickle_interval=0.01) as (port, _, _):
            client = check.NetworkClient("localhost", port, timeout=0.5)
            request = {"cmd": "status", "id": 1}
            encoded = check.NetworkClient.encode(request)
            try:
                started = time.perf_counter()
                with self.assertRaises(socket.timeout):
                    client.call_until(request, encoded, started + 0.05)
                elapsed = time.perf_counter() - started
            finally:
                client.close()
        self.assertGreaterEqual(elapsed, 0.035)
        self.assertLess(elapsed, 0.3)


class FakeRuntime:
    """Record direct runtime calls without importing model or transport code."""
    checkpoint_sha256 = "frozen"

    def __init__(self):
        self.processed = []
        self.arbitrated = []
        self.epochs = None
        self.active = {}

    def begin_episode(self, session_epoch, world_epoch, *, initial_game_ms):
        self.epochs = (session_epoch, world_epoch)
        self.initial_game_ms = initial_game_ms
        self.active.clear()

    def process(self, request):
        self.processed.append(request)
        row = request["observation"]
        identity = tuple(row[key] for key in ("session_epoch", "world_epoch", "entity_id", "control_generation"))
        previous = self.active.get(row["entity_id"])
        fallback_reason = None
        if identity[:2] != self.epochs:
            fallback_reason = "stale_epoch"
        elif previous is not None and identity[3] < previous[3]:
            fallback_reason = "stale_generation"
        else:
            self.active[row["entity_id"]] = identity
            if row.get("force_fallback") is True:
                fallback_reason = "observation_too_old"
        fallback = fallback_reason is not None
        return {"status": "fallback" if fallback else "processed",
                "reason": fallback_reason if fallback else "locked" if request["memory_only"] else "decision",
                "action_id": None, "model_processed": not fallback, "stream_seq": request["stream_seq"],
                "logits": [0.123, 0.456], "inference_ms": 0.25, "backend_timing": {"wall_ms": 0.2}}

    def arbitrate(self, row, *, stream_seq):
        self.arbitrated.append((row, stream_seq))
        return {"status": "decision", "reason": "decision", "action_id": 6, "stream_seq": stream_seq}

    def status(self):
        return {"reason": "status", "expected_epochs": list(self.epochs), "initial_game_ms": self.initial_game_ms}

    def end_episode(self, session_epoch, world_epoch):
        accepted = self.epochs == (session_epoch, world_epoch)
        if accepted:
            self.active.clear()
        return {"accepted": accepted, "reason": "episode_ended" if accepted else "stale_episode_end"}


class ReferenceDriverTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.reference = check.RuntimeReference(self.runtime)
        self.reset = {"cmd": "reset", "id": "reset", "session_epoch": 2, "world_epoch": 3, "initial_game_ms": 100}
        self.reference.handle(self.reset)

    def step(self, generation=1, **overrides):
        row = {"session_epoch": 2, "world_epoch": 3, "entity_id": 7, "control_generation": generation,
               "can_decide": True, "action_mask_valid": True, "t_game_ms": 150, **overrides}
        return self.reference.handle({"cmd": "step", "id": "step", "observation": row, "now_game_ms": 150})

    def test_sequences_belong_to_full_actor_identity_and_stale_generations_do_not_reset_newer_stream(self):
        responses = [self.step(), self.step(2), self.step(1), self.step(2),
                     self.step(2, world_epoch=2), self.step(2), self.step(2, entity_id=8)]
        self.assertEqual([response["process"]["stream_seq"] for response in responses], [1, 1, 1, 2, 1, 3, 1])
        self.assertEqual([request["stream_seq"] for request in self.runtime.processed], [1, 1, 1, 2, 1, 3, 1])
        self.assertEqual(responses[2]["reason"], "stale_generation")
        self.assertEqual(responses[4]["reason"], "stale_epoch")

    def test_direct_reference_preserves_fields_and_only_arbitrates_eligible_success(self):
        decision = self.step()
        locked = self.step(action_mask_valid=False)
        fallback = self.step(force_fallback=True)
        self.assertEqual(decision, {"ok": True, "id": "step", "schema_version": 1,
            "process": {"status": "processed", "reason": "decision", "action_id": None,
                        "model_processed": True, "stream_seq": 1, "logits": [0.123, 0.456]},
            "arbitration": {"status": "decision", "reason": "decision", "action_id": 6, "stream_seq": 1},
            "status": "decision", "action_id": 6, "reason": "decision"})
        self.assertEqual([request["memory_only"] for request in self.runtime.processed], [False, True, False])
        self.assertEqual([request["command_enabled"] for request in self.runtime.processed], [True, False, True])
        self.assertEqual(len(self.runtime.arbitrated), 1)
        self.assertEqual((locked["status"], locked["action_id"], locked["arbitration"]), ("locked", None, None))
        self.assertEqual((fallback["status"], fallback["reason"], fallback["arbitration"]),
                         ("fallback", "observation_too_old", None))
        self.assertEqual(self.runtime.processed[0]["source"], "periodic_observer")
        self.assertEqual(self.runtime.processed[0]["checkpoint_sha256"], "frozen")
        self.assertEqual(self.runtime.processed[0]["now_game_ms"], 150)

    def test_reset_and_accepted_end_clear_reference_sequences(self):
        self.step()
        stale_end = self.reference.handle({"cmd": "end_episode", "id": "stale", "session_epoch": 2, "world_epoch": 4})
        self.assertFalse(stale_end["accepted"])
        self.assertEqual(self.step()["process"]["stream_seq"], 2)
        accepted_end = self.reference.handle({"cmd": "end_episode", "id": "end", "session_epoch": 2, "world_epoch": 3})
        self.assertTrue(accepted_end["accepted"])
        self.assertEqual(self.reference.streams, {})
        self.reference.handle(self.reset)
        self.assertEqual(self.step()["process"]["stream_seq"], 1)
        self.reference.handle(self.reset)
        self.assertEqual(self.step()["process"]["stream_seq"], 1)
        status = self.reference.handle({"cmd": "status", "id": "status"})
        self.assertEqual(status, {"ok": True, "id": "status", "schema_version": 1, "reason": "status",
                                  "expected_epochs": [2, 3], "initial_game_ms": 100})
        self.assertEqual(self.reference.handle({"cmd": "close", "id": "close"}),
                         {"ok": True, "id": "close", "schema_version": 1, "reason": "closed"})


if __name__ == "__main__":
    unittest.main()
