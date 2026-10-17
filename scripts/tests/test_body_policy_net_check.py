"""The Mac replay client reads collector journals and compares complete replies."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import socketserver
import sys
import tempfile
import threading
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


if __name__ == "__main__":
    unittest.main()
