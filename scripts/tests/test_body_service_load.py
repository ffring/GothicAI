"""Service receipt proofs use protocol frames only, never deployed model calls."""
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import body_service_load as module
from body_service_load import (NetworkClients, ServiceBenchmarkError, bind_window, fixed_requests,
                               measure, p95, baseline_burst, cadence_receipts, validate_request_cadence)


class FrameWebsocket:
    def __init__(self, frames):
        self.frames, self.sent = list(frames), []

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        return self.frames.pop(0)

    async def close(self):
        pass


class ReceiptWriter:
    def __init__(self):
        self.parts, self.closed = [], False

    def write(self, data):
        self.parts.append(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def sse_reader(events):
    body = b"".join(b"data: "+(b"[DONE]" if event is None else json.dumps(event).encode())+b"\n\n" for event in events)
    reader = asyncio.StreamReader()
    reader.feed_data(f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode()+body)
    reader.feed_eof()
    return reader


@pytest.mark.parametrize("model,expected,accepted", [
    ("gothic-g9e", module.DECIDER_GREETING, True),
    ("strands-decider-2B-qwen3.5-v1-2610", module.DECIDER_GREETING, False),
    ("arbitrary-running-model", module.DECIDER_GREETING, False),
    ("arbitrary-running-model", {**module.DECIDER_GREETING, "model": "arbitrary-running-model"}, False),
])
def test_decider_connection_binds_exact_preflighted_tuned_greeting_before_any_decision(monkeypatch, model, expected, accepted):
    hello = {**module.DECIDER_GREETING, "model": model, "protocol_version": 1}
    tts, decider = FrameWebsocket([]), FrameWebsocket([json.dumps(hello)])
    connections = []
    async def connect(url, **options):
        connections.append(url)
        return tts if url == module.ENDPOINTS["tts"] else decider
    fake_client = ModuleType("websockets.asyncio.client")
    fake_client.connect = connect
    monkeypatch.setitem(sys.modules, "websockets", ModuleType("websockets"))
    monkeypatch.setitem(sys.modules, "websockets.asyncio", ModuleType("websockets.asyncio"))
    monkeypatch.setitem(sys.modules, "websockets.asyncio.client", fake_client)
    async def run():
        client = NetworkClients({"decider": {"expected_greeting": expected}}, b"", b"")
        if accepted:
            await client.open()
            assert client.handshakes["decider"] == hello
        else:
            with pytest.raises(ServiceBenchmarkError, match="tuned model greeting"):
                await client.open()
            assert client.handshakes == {}
        assert not decider.sent and not tts.sent
        assert connections == [module.ENDPOINTS["tts"], module.ENDPOINTS["decider"]]
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("changed", [None, "checkpoint", "source"])
def test_decider_provenance_requires_current_unit_invocation_checkpoint_and_exact_source(monkeypatch, tmp_path, changed):
    root, proc = tmp_path / "decider", tmp_path / "proc/42"
    root.mkdir()
    proc.mkdir(parents=True)
    checkpoint = root / "models/gothic-g9e"
    monkeypatch.setattr(module, "DECIDER_ROOT", root)
    monkeypatch.setattr(module, "DECIDER_CHECKPOINT", checkpoint)
    args = [str(root / ".venv/bin/python"), str(root / ".venv/bin/gothic-decider"), "serve",
            "--host", "dev-ubuntu", "--port", "47832"]
    (proc / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in args)+b"\0")
    (proc / "cwd").symlink_to(root, target_is_directory=True)
    source = root / "service.py"
    source.write_bytes(b"preflighted source")
    expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(module, "DECIDER_PROVENANCE", {"service": (source, expected_sha)})
    if changed == "source":
        source.write_bytes(b"different deployment")
    def fixture_path(*parts):
        path = Path(*parts)
        return tmp_path / str(path).lstrip("/") if str(path) == "/proc" or str(path).startswith("/proc/") else path
    monkeypatch.setattr(module, "Path", fixture_path)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "systemctl":
            return SimpleNamespace(stdout="FragmentPath=/home/kaifat/.config/systemd/user/gothic-decider.service\n"
                +f"ExecStart={{ path={root}/deploy/run.sh ; argv[]={root}/deploy/run.sh ; }}\n")
        loaded = root / "models/wrong-model" if changed == "checkpoint" else checkpoint
        return SimpleNamespace(stdout=f"INFO gothic_decider.engines.strands: loaded {loaded} in 16.9 s (LoRA merged)\n")
    monkeypatch.setattr(module.subprocess, "run", run)
    identity = {"MainPID": "42", "InvocationID": "current-invocation"}
    if changed:
        with pytest.raises(ServiceBenchmarkError, match="checkpoint proof" if changed == "checkpoint" else "source changed"):
            module.deployed_decider_binding(identity)
    else:
        proof = module.deployed_decider_binding(identity)
        assert proof["unit_identity"] == identity and proof["checkpoint_path"] == str(checkpoint)
        assert proof["sources"]["service"]["sha256"] == expected_sha
    assert "_SYSTEMD_INVOCATION_ID=current-invocation" in calls[-1]
    assert all(not any("Environment" in arg for arg in argv) for argv in calls)


def test_tts_latency_uses_first_actual_pcm_and_still_waits_for_corresponding_done():
    async def run():
        request = fixed_requests()["tts"]
        ws = FrameWebsocket([b"\x00\x01", json.dumps({"type": "done", "id": request["id"], "sample_rate": 48000})])
        client = NetworkClients({"tts": {"request": request}}, b"", b"")
        client.websockets["tts"] = ws
        record = {"response_events": []}
        await client.tts(record)
        assert record["first_result_wall_monotonic_ns"] == record["response_events"][0]["wall_monotonic_ns"]
        assert record["result"]["pcm_sha256"] == hashlib.sha256(b"\x00\x01").hexdigest()
        assert record["response_events"][-1]["value"]["type"] == "done" and ws.sent == [request]
    asyncio.run(run())


def test_tts_done_header_without_streamed_pcm_is_not_a_latency_sample():
    async def run():
        request = fixed_requests()["tts"]
        client = NetworkClients({"tts": {"request": request}}, b"", b"")
        client.websockets["tts"] = FrameWebsocket([json.dumps({"type": "done", "id": request["id"], "sample_rate": 48000})])
        with pytest.raises(ServiceBenchmarkError, match="actual streamed PCM"):
            await client.tts({"response_events": []})
    asyncio.run(run())


def test_gemma_ignores_role_only_delta_and_records_real_content_and_done_marker():
    async def run():
        request = fixed_requests()["gemma"]
        events = [{"model": request["model"], "choices": [{"delta": {"role": "assistant"}}]},
                  {"model": request["model"], "choices": [{"delta": {"content": "Готов."}}]},
                  {"model": request["model"], "choices": [{"delta": {}, "finish_reason": "stop"}]}, None]
        client = NetworkClients({"gemma": {"request": request}}, b"", b"")
        reader, writer = sse_reader(events), ReceiptWriter()
        async def http(*args):
            return reader, writer
        client.http = http
        record = {"response_events": []}
        await client.gemma(record)
        assert record["first_result_wall_monotonic_ns"] == record["response_events"][1]["wall_monotonic_ns"]
        assert record["response_events"][-1]["type"] == "sse_done" and writer.closed
        assert request["max_tokens"] == 16 and record["result"]["text"] == "Готов."
    asyncio.run(run())


def test_asr_finishes_on_real_final_transcript_and_preserves_exact_pcm_chunk_bytes():
    async def run():
        pcm, tail = b"\x01\x02", b"\x00\x00"
        request = {"path": "/v1/audio/transcriptions/live", "query": {"model": "voxtral-realtime", "sample_rate": 48000}}
        client = NetworkClients({"asr": {"request": request}}, pcm, tail)
        reader = sse_reader([{"type": "transcript.text.delta", "delta": "Стой"},
                             {"type": "transcript.text.done", "text": "Стой."}, None])
        writer = ReceiptWriter()
        async def http(*args):
            return reader, writer
        client.http = http
        record = {"response_events": []}
        await client.asr(record)
        assert writer.parts == [b"2\r\n"+pcm+b"\r\n", b"2\r\n"+tail+b"\r\n", b"0\r\n\r\n"]
        assert record["result"]["transcript"] == "Стой." and record["response_events"][-1]["type"] == "sse_done"
        assert record["metric_origin_wall_monotonic_ns"] == record["speech_submitted_wall_monotonic_ns"]
    asyncio.run(run())


def test_fast_first_token_does_not_admit_a_request_that_completed_after_native_terminal():
    row = {"request_begin_wall_monotonic_ns": 10, "first_result_wall_monotonic_ns": 15, "completed_wall_monotonic_ns": 21}
    assert not bind_window([row], 5, 20, True)["all_requests_inside_window"]
    assert bind_window([{**row, "completed_wall_monotonic_ns": 20}], 5, 20, True)["all_requests_inside_window"]
    assert not bind_window([{**row, "completed_wall_monotonic_ns": 20}], 5, 20, False)["all_requests_inside_window"]


def test_p95_uses_all_twenty_samples_and_rejects_missing_or_nonfinite_data():
    assert p95(list(range(1, 21))) == 19
    with pytest.raises(ServiceBenchmarkError):
        p95([])
    with pytest.raises(ServiceBenchmarkError):
        p95([float("nan")])


class ReceiptClients:
    active, maximum = Counter(), Counter()

    def __init__(self, *args):
        self.handshakes = {}

    async def open(self):
        pass

    async def close(self):
        pass

    async def request(self, service, record):
        self.active[service] += 1
        self.maximum[service] = max(self.maximum[service], self.active[service])
        try:
            record["request_submitted_wall_monotonic_ns"] = module.time.monotonic_ns()
            await asyncio.sleep(0)
            record["first_result_wall_monotonic_ns"] = module.time.monotonic_ns()
            record["metric_origin_wall_monotonic_ns"] = record["request_submitted_wall_monotonic_ns"]
            record["result"] = {"receipt_only": True}
        finally:
            self.active[service] -= 1


def patch_receipts(monkeypatch, tmp_path, cls=ReceiptClients):
    voice = tmp_path / "existing-voice.wav"
    voice.write_bytes(b"cached fixture bytes")
    monkeypatch.setattr(module, "VOICE_WAV", voice)
    monkeypatch.setattr(module, "VOICE_SHA256", hashlib.sha256(voice.read_bytes()).hexdigest())
    monkeypatch.setattr(module, "service_identities", lambda: {service: {"MainPID": "1"} for service in module.SERVICES})
    monkeypatch.setattr(module, "NetworkClients", cls)
    return {"fixture_binding_sha256": "fixed", "request_sha256": {service: "same-"+service for service in module.SERVICES},
            "decider": {"deployed_binding": {"unit_identity": {"MainPID": "1"}}}}


def test_fixed_baseline_has_two_warm_and_twenty_measured_real_receipts_per_service_with_one_inflight(monkeypatch, tmp_path):
    ReceiptClients.active.clear()
    ReceiptClients.maximum.clear()
    fixtures = patch_receipts(monkeypatch, tmp_path)
    result = asyncio.run(measure({"phase": "before", "measured_requests": 20, "warmup_requests": 2},
                                 fixtures, b"", b"", tmp_path))
    assert result["status"] == "collected" and len(result["requests"]) == 88
    for service in module.SERVICES:
        rows = [row for row in result["requests"] if row["service"] == service]
        assert sum(row["warmup"] for row in rows) == 2 and sum(not row["warmup"] for row in rows) == 20
        assert ReceiptClients.maximum[service] == 1
    assert len((tmp_path / "before/requests.jsonl").read_text().splitlines()) == 88


def test_terminal_cancellation_preserves_all_inflight_receipts_before_journal_close(monkeypatch, tmp_path):
    class BlockedReceipts(ReceiptClients):
        async def request(self, service, record):
            await asyncio.Event().wait()
    fixtures = patch_receipts(monkeypatch, tmp_path, BlockedReceipts)
    async def run():
        task = asyncio.create_task(measure({"phase": "before", "measured_requests": 20, "warmup_requests": 2},
                                           fixtures, b"", b"", tmp_path))
        for _ in range(20):
            await asyncio.sleep(.001)
            if (tmp_path / "before/requests.jsonl").exists():
                await asyncio.sleep(.005)
                break
        task.cancel()
        result = await task
        assert result["status"] == "cancelled" and len(result["requests"]) == 4
        rows = [json.loads(line) for line in (tmp_path / "before/requests.jsonl").read_text().splitlines()]
        assert len(rows) == 4 and all(row["status"] == "cancelled" for row in rows)
    asyncio.run(run())


def test_declared_burst_plan_has_four_groups_of_five_and_only_two_initial_warmups():
    groups = Counter(baseline_burst(index, 2) for index in range(22))
    assert groups == {0: 7, 1: 5, 2: 5, 3: 5}
    assert [sum(baseline_burst(index, 2) == burst for index in range(2, 22)) for burst in range(4)] == [5]*4
    assert module.FOUR_BURST_CADENCE["baseline_offsets_ms"] == [0, 20000, 40000, 60000]


@pytest.mark.parametrize("index,warmup", [(-1, 2), (22, 2), (True, 2), (0, 3), (0, 0)])
def test_burst_plan_rejects_extra_measurements_or_warmups(index, warmup):
    with pytest.raises(ServiceBenchmarkError):
        baseline_burst(index, warmup)


@pytest.mark.parametrize("field,value", [("bursts", 3), ("measured_per_burst", 6),
    ("warmup_first_burst", 3), ("minimum_actual_start_interval_ms", 0),
    ("maximum_actual_start_interval_ms", 30000), ("baseline_offsets_ms", [0, 1000, 2000, 3000])])
def test_cadence_repair_cannot_silently_change_counts_warmups_or_timing(field, value):
    cadence = {**module.FOUR_BURST_CADENCE, field: value}
    with pytest.raises(ServiceBenchmarkError):
        validate_request_cadence(cadence)


def burst_receipts(interval_ms=20000):
    result = []
    for service in module.SERVICES:
        for index in range(22):
            burst = baseline_burst(index, 2)
            result.append({"service": service, "burst_index": burst, "warmup": index < 2,
                "request_begin_wall_monotonic_ns": 1_000_000_000+burst*interval_ms*1_000_000+index*10_000})
    return result


@pytest.mark.parametrize("interval_ms,expected", [(20000, True), (21200, True), (24999, True),
                                                       (19999, False), (25001, False)])
def test_cadence_proof_uses_actual_monotonic_request_starts_with_declared_envelope(interval_ms, expected):
    report = cadence_receipts(burst_receipts(interval_ms), module.FOUR_BURST_CADENCE)
    assert report["valid"] is expected
    assert all(len(service["starts"]) == 4 and len(service["intervals_ms"]) == 3 for service in report["services"].values())


@pytest.mark.parametrize("change", ["extra_warmup", "missing_burst", "missing_clock", "duplicate_measurement"])
def test_actual_cadence_proof_rejects_missing_clock_and_nonmatching_real_request_counts(change):
    rows = burst_receipts()
    if change == "extra_warmup":
        rows[8]["warmup"] = True
    elif change == "missing_burst":
        rows = [row for row in rows if row["burst_index"] != 3]
    elif change == "missing_clock":
        rows[8].pop("request_begin_wall_monotonic_ns")
    else:
        rows.append(dict(rows[-1]))
    assert cadence_receipts(rows, module.FOUR_BURST_CADENCE)["valid"] is False


def test_opt_in_cadence_cancellation_during_idle_does_not_invent_later_bursts(monkeypatch, tmp_path):
    fixtures = patch_receipts(monkeypatch, tmp_path)
    fixtures["request_cadence"] = dict(module.FOUR_BURST_CADENCE)
    async def run():
        task = asyncio.create_task(measure({"phase": "before", "measured_requests": 20, "warmup_requests": 2},
                                           fixtures, b"", b"", tmp_path))
        journal = tmp_path / "before/requests.jsonl"
        for _ in range(1000):
            if journal.exists() and len(journal.read_text().splitlines()) == 28:
                break
            await asyncio.sleep(.001)
        assert journal.exists() and len(journal.read_text().splitlines()) == 28
        task.cancel()
        result = await task
        assert result["status"] == "cancelled"
        for service in module.SERVICES:
            rows = [row for row in result["requests"] if row["service"] == service]
            assert len(rows) == 7 and all(row["burst_index"] == 0 and row["status"] == "completed" for row in rows)
            assert sum(row["warmup"] for row in rows) == 2
    asyncio.run(run())
