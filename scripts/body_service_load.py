#!/usr/bin/env python3
"""Fixed protected-service workload, measured only on dev-ubuntu.

This is a network client, not a model process. It uses existing cached speech
and voice fixtures; no voice design, installation, restart or external LLM is
performed. One request per service is in flight. Four services run in parallel.
The parent supplies real arena windows; independent audit verifies overlap.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time
from urllib.parse import urlencode, urlsplit
import wave

SERVER_ROOT = Path("/home/kaifat/gothic-ai")
ASR_WAV = SERVER_ROOT / "voxcpm/line_6_0.wav"
ASR_SHA256 = "18e3cbf9ebdafe601d2a40cd05c0834a6db9a19bb29b8ae9dcdb29d9262bb53e"
VOICE_DESCRIPTION = "A middle-aged man with an ordinary, slightly rough voice, speaking calmly"
VOICE_WAV = SERVER_ROOT / "voxcpm/voices/default-052a7ffed9.wav"
VOICE_SHA256 = "c8ded914859872453b1a273045ee771f24c0770d665cf574466ed0d40ff1d4b6"
ENDPOINTS = {"asr": "http://dev-ubuntu:47830", "tts": "ws://dev-ubuntu:47831",
             "decider": "ws://dev-ubuntu:47832", "gemma": "http://dev-ubuntu:47834"}
SERVICES = {"asr": "gothic-voxtral", "tts": "gothic-voxcpm", "decider": "gothic-decider", "gemma": "gothic-gemma"}
METRICS = {"asr": "asr_completion_ms", "tts": "tts_first_pcm_ms",
           "decider": "decider_first_decision_ms", "gemma": "gemma_first_token_ms"}
PROTOCOL_SOURCES = {"asr": SERVER_ROOT / "voxtral/audio.cpp/app/server/parallel_runtime.cpp",
                    "tts": SERVER_ROOT / "voxcpm/server.py", "decider": SERVER_ROOT / "decider/src/gothic_decider/service.py",
                    "gemma": SERVER_ROOT / "gemma/run.sh"}
DECIDER_ROOT = SERVER_ROOT / "decider"
DECIDER_MODEL = "gothic-g9e"
DECIDER_CHECKPOINT = DECIDER_ROOT / "models" / DECIDER_MODEL
DECIDER_GREETING = {"type": "hello", "service": "gothic-decider", "model": DECIDER_MODEL}
# Read-only preflight of the deployed tuned service. These are small source and
# metadata files, not weights; a changed deployment requires a fresh fixture.
DECIDER_PROVENANCE = {
    "service": (PROTOCOL_SOURCES["decider"], "60e6fdc65009c112a44847b89714d7392c6d74c5c0d140dff49fc19f54bbdcd3"),
    "engine": (DECIDER_ROOT / "src/gothic_decider/engines/strands.py", "0e4e9e6cf688353b289fd183bce65ba25ba69f4bfe111e1c119a34cbd07e289c"),
    "cli": (DECIDER_ROOT / "src/gothic_decider/cli.py", "00838c49e664bd86b1e29364471e981f8792ce1e01beb7b7f879eb352a594359"),
    "launcher": (DECIDER_ROOT / "deploy/run.sh", "4fa73be40742f62d59ea9063739512536444847a33ab1306769aa2c5a7cc8e8a"),
    "entrypoint": (DECIDER_ROOT / ".venv/bin/gothic-decider", "fbe186287523550c1496c70a764aa28d3169f40052197944b6e7e97aef32b685"),
    "checkpoint_metadata": (DECIDER_CHECKPOINT / "strands_decider_config.json", "08623922b389d37228e76116ce1495abf8bf25128b24327b14db5f8e89655102"),
}
MAX_RESPONSE_BYTES = 2*1024*1024
MAX_PCM_BYTES = 1024*1024
FOUR_BURST_CADENCE = {
    "schema_version": 1,
    "kind": "four_bursts_monotonic_v1",
    "bursts": 4,
    "measured_per_burst": 5,
    "warmup_first_burst": 2,
    "nominal_start_interval_ms": 20000,
    "minimum_actual_start_interval_ms": 20000,
    "maximum_actual_start_interval_ms": 25000,
    "baseline_offsets_ms": [0, 20000, 40000, 60000],
    "during_timing": "actual_arena_start_plus_reset_floor_before_reset",
}


class ServiceBenchmarkError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_request_cadence(value):
    """The opt-in repair is preregistered; old frozen workloads stay unchanged."""
    if value is None:
        return None
    if value != FOUR_BURST_CADENCE or digest(value) != digest(FOUR_BURST_CADENCE):
        raise ServiceBenchmarkError("service request cadence differs from the declared four bursts")
    return json.loads(json.dumps(value))


def baseline_burst(index, warmup):
    if type(index) is not int or index < 0 or index >= warmup+20 or warmup != 2:
        raise ServiceBenchmarkError("baseline burst requires two warmups and twenty measurements")
    return 0 if index < warmup+5 else 1+(index-warmup-5)//5


def cadence_receipts(records, cadence):
    """Check every actual service start; never infer a start from fast results."""
    cadence = validate_request_cadence(cadence)
    if cadence is None:
        return None
    result = {"configuration": cadence, "services": {}, "valid": True}
    for service in SERVICES:
        groups = {}
        for row in records:
            if row.get("service") == service:
                groups.setdefault(row.get("burst_index"), []).append(row)
        starts, issues = [], []
        if set(groups) != set(range(4)) or any(type(burst) is not int for burst in groups):
            issues.append("requires four actual bursts")
        for burst in sorted(groups, key=lambda value: -1 if value is None else value):
            rows = groups[burst]
            warm = sum(row.get("warmup") is True for row in rows)
            measured = sum(row.get("warmup") is False for row in rows)
            if warm != (2 if burst == 0 else 0) or measured != 5:
                issues.append("burst warmup/measurement counts differ")
            times = [row.get("request_begin_wall_monotonic_ns") for row in rows]
            if any(type(value) is not int or value <= 0 for value in times):
                issues.append("burst lacks actual request clocks")
                continue
            starts.append({"burst_index": burst, "wall_monotonic_ns": min(times)})
        intervals = [(b["wall_monotonic_ns"]-a["wall_monotonic_ns"])/1e6 for a, b in zip(starts, starts[1:])]
        if any(not cadence["minimum_actual_start_interval_ms"] <= value <= cadence["maximum_actual_start_interval_ms"] for value in intervals):
            issues.append("actual burst cadence outside declared timing envelope")
        result["services"][service] = {"starts": starts, "intervals_ms": intervals, "issues": issues}
        result["valid"] = result["valid"] and not issues
    return result


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n")


def fixed_requests():
    return {"tts": {"type": "speak", "id": "body-stage8-service", "text": "Стой.",
                    "voice": {"id": "default", "description": VOICE_DESCRIPTION}},
            "decider": {"type": "decide", "id": "body-stage8-service", "lane": "urgent", "priority": 0,
                        "deadline_ms": 1000,
                        "state": "You are a guard at the city gate. The hero walks past and nods at you.",
                        "questions": {"react": {"type": "choice", "instructions": "What do you do right now?",
                                                "criteria": {"continue": "keep standing guard", "greet": "nod back",
                                                             "question": "stop him"}}}},
            "gemma": {"model": "gemma-4-12b", "messages": [
                {"role": "system", "content": "Ответь одной короткой репликой по-русски."},
                {"role": "user", "content": "Скажи: готов."}], "max_tokens": 16, "stream": True,
                      "temperature": 0, "seed": 17}}


def service_identities():
    result = {}
    for key, name in SERVICES.items():
        completed = subprocess.run(["systemctl", "--user", "show", name,
                                    "--property=ActiveState,SubState,MainPID,InvocationID,ExecMainStartTimestampMonotonic"],
                                   capture_output=True, text=True, timeout=5, check=True)
        values = dict(line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
        if values.get("ActiveState") != "active" or values.get("SubState") != "running" or int(values.get("MainPID", "0")) <= 0:
            raise ServiceBenchmarkError("protected service is not running: "+name)
        result[key] = values
    return result


def deployed_voice_binding(identity):
    """Inspect only safe process paths/voice option, never secret Environment."""
    pending, seen = [int(identity["MainPID"])], set()
    while pending and len(seen) < 16:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        proc = Path("/proc") / str(pid)
        args = [part.decode() for part in (proc / "cmdline").read_bytes().split(b"\0") if part]
        cwd = Path(os.readlink(proc / "cwd"))
        scripts = [Path(arg) if Path(arg).is_absolute() else cwd / arg for arg in args if arg.endswith("server.py")]
        if PROTOCOL_SOURCES["tts"].resolve() in [path.resolve() for path in scripts]:
            value = args[args.index("--voices")+1] if "--voices" in args else str(PROTOCOL_SOURCES["tts"].parent / "voices")
            directory = Path(value) if Path(value).is_absolute() else cwd / value
            actual = (directory / VOICE_WAV.name).resolve()
            if actual != VOICE_WAV.resolve():
                raise ServiceBenchmarkError("deployed TTS cache directory differs; new voice design forbidden")
            return {"pid": pid, "source_path": str(PROTOCOL_SOURCES["tts"]), "voice_path": str(actual)}
        children = proc / "task" / str(pid) / "children"
        if children.is_file():
            pending.extend(map(int, children.read_text().split()))
    raise ServiceBenchmarkError("cannot bind cached voice to the deployed TTS process")


def deployed_decider_binding(identity):
    """Bind safe process/unit paths and current-invocation startup, never Environment."""
    proc = Path("/proc") / str(int(identity["MainPID"]))
    args = [part.decode() for part in (proc / "cmdline").read_bytes().split(b"\0") if part]
    expected_prefix = [str(DECIDER_ROOT / ".venv/bin/python"), str(DECIDER_ROOT / ".venv/bin/gothic-decider"), "serve"]
    if args[:3] != expected_prefix or (proc / "cwd").resolve() != DECIDER_ROOT.resolve():
        raise ServiceBenchmarkError("protected Decider process is not the preflighted service")
    options = {}
    for option in ("--host", "--port", "--checkpoint", "--engine"):
        if args.count(option) > 1 or (option in args and args.index(option)+1 == len(args)):
            raise ServiceBenchmarkError("ambiguous protected Decider launch option")
        if option in args:
            options[option] = args[args.index(option)+1]
    if (options.get("--host") != "dev-ubuntu" or options.get("--port") != "47832" or
            options.get("--engine", "strands") != "strands" or
            ("--checkpoint" in options and Path(options["--checkpoint"]).resolve() != DECIDER_CHECKPOINT.resolve())):
        raise ServiceBenchmarkError("protected Decider launch differs from the preflighted endpoint/model")
    unit = subprocess.run(["systemctl", "--user", "show", SERVICES["decider"], "--property=FragmentPath,ExecStart"],
                          capture_output=True, text=True, timeout=5, check=True)
    values = dict(line.split("=", 1) for line in unit.stdout.splitlines() if "=" in line)
    launch = re.search(r"(?:^|[ {;])path=([^ ;}]+)", values.get("ExecStart", ""))
    unit_path = Path("/home/kaifat/.config/systemd/user/gothic-decider.service")
    if values.get("FragmentPath") != str(unit_path) or launch is None or launch[1] != str(DECIDER_ROOT / "deploy/run.sh"):
        raise ServiceBenchmarkError("protected Decider unit differs from the preflighted launcher")
    startup = subprocess.run(["journalctl", "--user", "--unit", SERVICES["decider"],
                              "_SYSTEMD_INVOCATION_ID="+identity["InvocationID"], "--no-pager", "-n", "2",
                              "--grep", "gothic_decider.engines.strands: loaded ", "-o", "cat"],
                             capture_output=True, text=True, timeout=5, check=True)
    lines = startup.stdout.splitlines()
    match = re.fullmatch(r"[^\n]*gothic_decider\.engines\.strands: loaded (\S+) in [0-9.]+ s(?: \(LoRA merged\))?", lines[0]) if len(lines) == 1 else None
    if match is None or Path(match[1]).resolve() != DECIDER_CHECKPOINT.resolve():
        raise ServiceBenchmarkError("current Decider invocation lacks the preflighted tuned checkpoint proof")
    sources = {}
    for key, (path, expected) in DECIDER_PROVENANCE.items():
        actual = file_sha(path)
        if actual != expected:
            raise ServiceBenchmarkError("protected Decider preflight source changed: "+key)
        sources[key] = {"path": str(path), "sha256": actual}
    return {"unit": SERVICES["decider"], "unit_path": str(unit_path), "unit_identity": dict(identity),
            "exec_start_path": launch[1], "pid": int(identity["MainPID"]), "cwd": str(DECIDER_ROOT),
            "entrypoint_path": expected_prefix[1], "selected_launch_options": options,
            "checkpoint_path": str(DECIDER_CHECKPOINT), "startup_record": lines[0], "sources": sources}


def validate_decider_greeting(hello, expected):
    if (expected != DECIDER_GREETING or not isinstance(hello, dict) or
            any(hello.get(key) != value for key, value in DECIDER_GREETING.items())):
        raise ServiceBenchmarkError("unexpected protected Decider tuned model greeting")


def prepare_fixtures(output, *, cadence=None):
    identities = service_identities()
    voice_binding = deployed_voice_binding(identities["tts"])
    decider_binding = deployed_decider_binding(identities["decider"])
    if file_sha(ASR_WAV) != ASR_SHA256 or file_sha(VOICE_WAV) != VOICE_SHA256:
        raise ServiceBenchmarkError("existing cached fixture bytes differ; no replacement or voice design allowed")
    with wave.open(str(ASR_WAV), "rb") as wav:
        rate, channels, width, frames = wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()
        if channels != 1 or width != 2 or wav.getcomptype() != "NONE" or not 0 < frames/rate <= 3:
            raise ServiceBenchmarkError("ASR fixture must be a real short mono PCM16 WAV")
        pcm = wav.readframes(frames)
    if len(pcm) != frames*2 or hashlib.sha1(VOICE_DESCRIPTION.encode()).hexdigest()[:10] != "052a7ffed9":
        raise ServiceBenchmarkError("cached voice/PCM contract differs")
    folder = output / "fixtures"
    folder.mkdir()
    (folder / "asr.wav").write_bytes(ASR_WAV.read_bytes())
    tail = bytes(2*round(rate*.96))
    (folder / "asr-pcm-with-tail.bin").write_bytes(pcm+tail)
    requests = fixed_requests()
    request = {"method": "POST", "endpoint": ENDPOINTS["asr"], "path": "/v1/audio/transcriptions/live",
               "query": {"model": "voxtral-realtime", "sample_rate": rate, "channels": 1, "sample_format": "s16le"},
               "speech_pcm_sha256": hashlib.sha256(pcm).hexdigest(), "payload_sha256": hashlib.sha256(pcm+tail).hexdigest(),
               "tail_s": .96, "transfer_encoding": "chunked", "send_profile": "cached speech then fixed silence, unpaced"}
    fixtures = {"schema_version": 1, "kind": "body_stage8_fixed_service_fixtures", "endpoints": ENDPOINTS,
                "asr": {"source_path": str(ASR_WAV), "source_sha256": ASR_SHA256, "sample_rate": rate,
                        "channels": channels, "sample_width": width, "frames": frames, "speech_seconds": frames/rate,
                        "input_file": "fixtures/asr.wav", "payload_file": "fixtures/asr-pcm-with-tail.bin", "request": request},
                "tts": {"voice_path": str(VOICE_WAV), "voice_sha256": VOICE_SHA256, "request": requests["tts"],
                        "deployed_voice_binding": voice_binding,
                        "voice_design_allowed": False, "server_generation_seed": "existing server chooses random seed; request fixed"},
                "decider": {"request": requests["decider"], "external_llm_or_tools": False,
                            "expected_greeting": dict(DECIDER_GREETING), "deployed_binding": decider_binding},
                "gemma": {"request": requests["gemma"], "maximum_output_tokens": 16},
                "protocol_sources": {key: {"path": str(path), "sha256": file_sha(path)} for key, path in PROTOCOL_SOURCES.items()},
                "request_sha256": {key: digest(value) for key, value in {"asr": request, **requests}.items()},
                "driver_sha256": file_sha(Path(__file__)), "clock_domain": "linux-clock-monotonic"}
    if cadence is not None:
        fixtures["request_cadence"] = validate_request_cadence(cadence)
    fixtures["fixture_binding_sha256"] = digest(fixtures)
    write_json(output / "fixtures.json", fixtures)
    return fixtures, pcm, tail


async def read_head(reader):
    line = await reader.readline()
    pieces = line.decode("latin1").strip().split()
    if len(pieces) < 2 or not pieces[0].startswith("HTTP/"):
        raise ServiceBenchmarkError("invalid HTTP response status")
    headers = {}
    size = len(line)
    while True:
        line = await reader.readline()
        size += len(line)
        if size > 32768 or not line:
            raise ServiceBenchmarkError("invalid/bounded HTTP headers")
        if line in (b"\r\n", b"\n"):
            return int(pieces[1]), headers
        name, _, value = line.decode("latin1").partition(":")
        headers[name.lower()] = value.strip()


async def body_parts(reader, headers):
    total = 0
    if "chunked" in headers.get("transfer-encoding", "").lower():
        while True:
            line = await reader.readline()
            length = int(line.split(b";", 1)[0], 16)
            if length == 0:
                return
            total += length
            if length < 0 or total > MAX_RESPONSE_BYTES:
                raise ServiceBenchmarkError("HTTP body exceeds bounded response")
            chunk = await reader.readexactly(length)
            if await reader.readexactly(2) != b"\r\n":
                raise ServiceBenchmarkError("malformed HTTP chunk")
            yield chunk
    else:
        left = int(headers["content-length"]) if "content-length" in headers else None
        while left is None or left > 0:
            chunk = await reader.read(min(65536, left) if left is not None else 65536)
            if not chunk:
                if left:
                    raise ServiceBenchmarkError("truncated HTTP body")
                return
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ServiceBenchmarkError("HTTP body exceeds bounded response")
            if left is not None:
                left -= len(chunk)
            yield chunk


async def sse_events(reader, headers):
    pending = b""
    async for chunk in body_parts(reader, headers):
        pending += chunk
        if len(pending) > 256*1024:
            raise ServiceBenchmarkError("SSE line exceeds bounded parser")
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            line = line.strip()
            if line.startswith(b"data:"):
                data = line[5:].strip()
                yield None if data == b"[DONE]" else json.loads(data)


class NetworkClients:
    def __init__(self, fixtures, pcm, tail):
        self.fixtures, self.pcm, self.tail = fixtures, pcm, tail
        self.websockets = {}
        self.handshakes = {}

    async def open(self):
        # Present in the existing Decider venv. The parent starts this client
        # there; the body model venv needs no new package or model imports.
        from websockets.asyncio.client import connect
        for service in ("tts", "decider"):
            ws = await connect(ENDPOINTS[service], open_timeout=3, max_size=MAX_RESPONSE_BYTES, ping_interval=None)
            self.websockets[service] = ws
            if service == "decider":
                hello = json.loads(await asyncio.wait_for(ws.recv(), 3))
                validate_decider_greeting(hello, self.fixtures["decider"]["expected_greeting"])
                self.handshakes[service] = hello

    async def close(self):
        await asyncio.gather(*(ws.close() for ws in self.websockets.values()), return_exceptions=True)
        self.websockets.clear()

    async def request(self, service, record):
        return await asyncio.wait_for(getattr(self, service)(record), 8)

    async def http(self, service, path, body=None):
        endpoint = urlsplit(ENDPOINTS[service])
        reader, writer = await asyncio.wait_for(asyncio.open_connection(endpoint.hostname, endpoint.port, limit=256*1024), 3)
        header = f"POST {path} HTTP/1.1\r\nHost: {endpoint.hostname}:{endpoint.port}\r\nConnection: close\r\nAccept: text/event-stream\r\n"
        if body is None:
            header += "Content-Type: application/octet-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
            writer.write(header.encode())
        else:
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            writer.write((header+f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n").encode()+payload)
        await writer.drain()
        return reader, writer

    async def asr(self, record):
        template = self.fixtures["asr"]["request"]
        reader, writer = await self.http("asr", template["path"]+"?"+urlencode(template["query"]))
        try:
            for payload in (self.pcm, self.tail):
                writer.write(f"{len(payload):x}\r\n".encode()+payload+b"\r\n")
                await writer.drain()
                if payload is self.pcm:
                    record["speech_submitted_wall_monotonic_ns"] = time.monotonic_ns()
            writer.write(b"0\r\n\r\n")
            await writer.drain()
            record["request_submitted_wall_monotonic_ns"] = time.monotonic_ns()
            status, headers = await read_head(reader)
            if status != 200:
                raise ServiceBenchmarkError("ASR HTTP status "+str(status))
            transcript = None
            async for event in sse_events(reader, headers):
                if event is None:
                    record["response_events"].append({"wall_monotonic_ns": time.monotonic_ns(), "type": "sse_done"})
                    break
                record["response_events"].append({"wall_monotonic_ns": time.monotonic_ns(), "value": event})
                if event.get("type") == "error":
                    raise ServiceBenchmarkError("ASR error event")
                if event.get("type") == "transcript.text.done":
                    transcript = event.get("text")
                    record["first_result_wall_monotonic_ns"] = time.monotonic_ns()
            if not isinstance(transcript, str) or not transcript.strip():
                raise ServiceBenchmarkError("ASR lacks a real nonempty final transcript")
            record["result"] = {"transcript": transcript}
            record["metric_origin_wall_monotonic_ns"] = record["speech_submitted_wall_monotonic_ns"]
        finally:
            writer.close()
            await writer.wait_closed()

    async def tts(self, record):
        ws = self.websockets["tts"]
        request = self.fixtures["tts"]["request"]
        record["request_submitted_wall_monotonic_ns"] = time.monotonic_ns()
        await ws.send(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
        pcm, size, first = hashlib.sha256(), 0, None
        while True:
            raw = await ws.recv()
            now = time.monotonic_ns()
            if isinstance(raw, bytes):
                if not raw or len(raw)%2:
                    raise ServiceBenchmarkError("TTS returned invalid PCM16")
                first = now if first is None else first
                pcm.update(raw)
                size += len(raw)
                if size > MAX_PCM_BYTES:
                    raise ServiceBenchmarkError("TTS short fixture exceeded bounded audio")
                record["response_events"].append({"wall_monotonic_ns": now, "type": "pcm", "bytes": len(raw),
                                                   "sha256": hashlib.sha256(raw).hexdigest()})
                continue
            event = json.loads(raw)
            record["response_events"].append({"wall_monotonic_ns": now, "value": event})
            if event.get("id") != request["id"]:
                raise ServiceBenchmarkError("TTS reply does not match fixed request")
            if event.get("type") == "error":
                raise ServiceBenchmarkError("TTS error event")
            if event.get("type") == "done":
                if first is None or size == 0 or event.get("sample_rate") != 48000:
                    raise ServiceBenchmarkError("TTS done without actual streamed PCM")
                record["first_result_wall_monotonic_ns"] = first
                record["metric_origin_wall_monotonic_ns"] = record["request_submitted_wall_monotonic_ns"]
                record["result"] = {"pcm_bytes": size, "pcm_sha256": pcm.hexdigest(), "sample_rate": 48000, "done": event}
                return

    async def decider(self, record):
        ws = self.websockets["decider"]
        request = self.fixtures["decider"]["request"]
        record["request_submitted_wall_monotonic_ns"] = time.monotonic_ns()
        await ws.send(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
        event = json.loads(await ws.recv())
        now = time.monotonic_ns()
        record["response_events"].append({"wall_monotonic_ns": now, "value": event})
        answer = event.get("answers", {}).get("react", {})
        if event.get("type") != "answers" or event.get("id") != request["id"] or answer.get("choice") not in request["questions"]["react"]["criteria"]:
            raise ServiceBenchmarkError("Decider lacks a real isolated typed decision")
        record["first_result_wall_monotonic_ns"] = now
        record["metric_origin_wall_monotonic_ns"] = record["request_submitted_wall_monotonic_ns"]
        record["result"] = event

    async def gemma(self, record):
        request = self.fixtures["gemma"]["request"]
        record["request_submitted_wall_monotonic_ns"] = time.monotonic_ns()
        reader, writer = await self.http("gemma", "/v1/chat/completions", request)
        try:
            status, headers = await read_head(reader)
            if status != 200:
                raise ServiceBenchmarkError("Gemma HTTP status "+str(status))
            first, text, finished, done, model = None, "", False, False, None
            async for event in sse_events(reader, headers):
                now = time.monotonic_ns()
                if event is None:
                    record["response_events"].append({"wall_monotonic_ns": now, "type": "sse_done"})
                    done = True
                    break
                record["response_events"].append({"wall_monotonic_ns": now, "value": event})
                if "error" in event:
                    raise ServiceBenchmarkError("Gemma error event")
                if event.get("model") is not None:
                    model = event["model"]
                    if model != request["model"]:
                        raise ServiceBenchmarkError("Gemma returned another model alias")
                for choice in event.get("choices", []):
                    content = choice.get("delta", {}).get("content")
                    if content:
                        first = now if first is None else first
                        text += content
                    finished = finished or choice.get("finish_reason") is not None
            if first is None or not text.strip() or not finished or not done or model is None:
                raise ServiceBenchmarkError("Gemma lacks real streamed text and terminal markers")
            record["first_result_wall_monotonic_ns"] = first
            record["metric_origin_wall_monotonic_ns"] = record["request_submitted_wall_monotonic_ns"]
            record["result"] = {"text": text, "model": model, "finish_received": finished, "done_received": done}
        finally:
            writer.close()
            await writer.wait_closed()


def p95(values):
    if not values or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in values):
        raise ServiceBenchmarkError("invalid latency samples")
    return sorted(values)[max(0, math.ceil(.95*len(values))-1)]


def bind_window(records, start_ns, end_ns, healthy):
    return {"window_start_wall_monotonic_ns": start_ns, "window_end_wall_monotonic_ns": end_ns,
            "healthy_physical_ownership": healthy,
            "all_requests_inside_window": bool(records) and healthy and all(
                type(row.get("request_begin_wall_monotonic_ns")) is int and type(row.get("completed_wall_monotonic_ns")) is int and
                start_ns <= row["request_begin_wall_monotonic_ns"] <= row["completed_wall_monotonic_ns"] <= end_ns
                for row in records)}


def compare_cohorts(before, cohorts):
    """Observed counts/deltas only; independent audit applies the 10% gate."""
    result = {"quality_gate_evaluated": False, "fixture_binding_sha256": before["fixture_binding_sha256"],
              "providers": {}}
    for provider, phases in cohorts.items():
        issues, metrics = [], {}
        if len(phases) != 4:
            issues.append("requires four declared cohorts")
        for phase in phases:
            if (phase.get("status") != "collected" or phase.get("fixture_binding_sha256") != before["fixture_binding_sha256"] or
                    not phase.get("window_binding", {}).get("all_requests_inside_window")):
                issues.append("incomplete or unhealthy actual four-actor window")
            if phase.get("services_before") != before.get("services_before") or phase.get("services_after") != before.get("services_after"):
                issues.append("protected service identity changed")
        cadence = before.get("request_cadence")
        if cadence is not None:
            before_cadence = cadence_receipts(before["requests"], cadence)
            during_cadence = cadence_receipts([row for phase in phases for row in phase.get("requests", [])], cadence)
            if (not before_cadence["valid"] or not during_cadence["valid"] or
                    any(phase.get("request_cadence") != cadence for phase in phases)):
                issues.append("baseline/during actual four-burst cadence differs")
        for service, metric in METRICS.items():
            baseline = [row for row in before["requests"] if row["service"] == service and not row["warmup"] and row.get("status") == "completed"]
            during = [row for phase in phases for row in phase.get("requests", []) if row["service"] == service and not row["warmup"] and row.get("status") == "completed"]
            if (len(baseline) != 20 or len(during) != 20 or
                    {row["request_sha256"] for row in baseline+during} != {before["requests"][next(i for i, row in enumerate(before["requests"]) if row["service"] == service)]["request_sha256"]}):
                issues.append(service+": fixed request count/binding differs")
                metrics[metric] = {"before_n": len(baseline), "during_n": len(during), "before_p95_ms": None,
                                   "during_p95_ms": None, "degradation_fraction": None}
                continue
            b, d = p95([row["latency_ms"] for row in baseline]), p95([row["latency_ms"] for row in during])
            metrics[metric] = {"before_n": 20, "during_n": 20, "before_p95_ms": b, "during_p95_ms": d,
                               "degradation_fraction": d/b-1 if b > 0 else None}
        result["providers"][provider] = {"measurement_issues": sorted(set(issues)), "metrics": metrics}
        if cadence is not None:
            result["providers"][provider]["cadence"] = {"before": before_cadence, "during": during_cadence}
    return result


async def measure(command, fixtures, pcm, tail, output):
    phase = command["phase"]
    count, warmup = command["measured_requests"], command["warmup_requests"]
    if count not in (5, 20) or warmup not in (0, 2) or (phase == "before" and (count, warmup) != (20, 2)):
        raise ServiceBenchmarkError("service workload differs from declared counts")
    if phase != "before":
        match = re.fullmatch(r"during-(native|gru|mlp)-([0-3])", phase)
        window = command.get("window")
        if (match is None or count != 5 or warmup != (2 if int(match[2]) == 0 else 0) or
                not isinstance(window, dict) or type(window.get("start_wall_monotonic_ns")) is not int or
                window["start_wall_monotonic_ns"] <= 0 or len(set(window.get("physical_actor_ids", []))) != 4):
            raise ServiceBenchmarkError("during workload lacks a declared real four-actor cohort window")
    cadence = validate_request_cadence(fixtures.get("request_cadence"))
    folder = output / phase
    folder.mkdir()
    result = {"schema_version": 1, "phase": phase, "status": "collecting", "request_count_per_service": count,
              "warmup_per_service": warmup, "fixture_binding_sha256": fixtures["fixture_binding_sha256"],
              "concurrency_per_service": 1, "parallel_services": list(SERVICES), "metrics": {}, "requests": [],
              "window": command.get("window"), "quality_gate_evaluated": False}
    if cadence is not None:
        result["request_cadence"] = cadence
    clients = NetworkClients(fixtures, pcm, tail)
    tasks = []
    try:
        result["services_before"] = await asyncio.to_thread(service_identities)
        if result["services_before"]["decider"] != fixtures["decider"]["deployed_binding"]["unit_identity"]:
            raise ServiceBenchmarkError("protected Decider invocation changed after fixed fixture binding")
        if file_sha(VOICE_WAV) != VOICE_SHA256:
            raise ServiceBenchmarkError("cached voice changed before workload")
        await clients.open()
        result["connection_handshakes"] = clients.handshakes
        burst_origin_ns = time.monotonic_ns()
        with (folder / "requests.jsonl").open("x") as journal:
            async def run_service(service):
                previous_burst, previous_burst_start_ns, previous_burst_target_ns = None, None, None
                for index in range(warmup+count):
                    burst = baseline_burst(index, warmup) if phase == "before" else int(match[2])
                    target_ns = burst_origin_ns
                    if cadence is not None and phase == "before":
                        target_ns += cadence["baseline_offsets_ms"][burst]*1_000_000
                        if previous_burst == burst:
                            target_ns = previous_burst_target_ns
                        elif previous_burst_start_ns is not None:
                            target_ns = max(target_ns, previous_burst_start_ns+cadence["minimum_actual_start_interval_ms"]*1_000_000)
                        remaining = (target_ns-time.monotonic_ns())/1e9
                        if remaining > 0:
                            await asyncio.sleep(remaining)
                    record = {"service": service, "phase": phase, "iteration": index, "warmup": index < warmup,
                              "metric": METRICS[service], "request_sha256": fixtures["request_sha256"][service],
                              "fixture_binding_sha256": fixtures["fixture_binding_sha256"], "response_events": [],
                              "request_begin_wall_monotonic_ns": time.monotonic_ns()}
                    if cadence is not None:
                        record.update(burst_index=burst, nominal_burst_start_wall_monotonic_ns=target_ns,
                                      request_cadence_kind=cadence["kind"])
                        if previous_burst != burst:
                            previous_burst, previous_burst_start_ns = burst, record["request_begin_wall_monotonic_ns"]
                            previous_burst_target_ns = target_ns
                    try:
                        await clients.request(service, record)
                        record["completed_wall_monotonic_ns"] = time.monotonic_ns()
                        record["latency_ms"] = (record["first_result_wall_monotonic_ns"]-record["metric_origin_wall_monotonic_ns"])/1e6
                        record["status"] = "completed"
                    except BaseException as error:
                        record["completed_wall_monotonic_ns"] = time.monotonic_ns()
                        record["status"] = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
                        record["error"] = str(error)[:200] or type(error).__name__
                        raise
                    finally:
                        result["requests"].append(record)
                        journal.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)+"\n")
                        journal.flush()
            tasks = [asyncio.create_task(run_service(service)) for service in SERVICES]
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), 120 if phase == "before" else 18)
            finally:
                # Preserve cancelled receipts before the journal closes. No
                # next request can outlive the native cohort's cancellation.
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        result["services_after"] = await asyncio.to_thread(service_identities)
        if result["services_before"] != result["services_after"] or file_sha(VOICE_WAV) != VOICE_SHA256:
            raise ServiceBenchmarkError("protected service identity or voice cache changed during workload")
        for service in SERVICES:
            records = [row for row in result["requests"] if row["service"] == service and not row["warmup"]]
            if len(records) != count or any(row["status"] != "completed" for row in records):
                raise ServiceBenchmarkError("fixed workload sample count is incomplete")
            result["metrics"][METRICS[service]] = {"n": count, "p95_ms": p95([row["latency_ms"] for row in records])}
        result["status"] = "collected"
        if cadence is not None and phase == "before":
            result["cadence_receipts"] = cadence_receipts(result["requests"], cadence)
            if result["cadence_receipts"]["valid"] is not True:
                raise ServiceBenchmarkError("baseline actual four-burst timing did not match declaration")
    except BaseException as error:
        result["status"] = "cancelled" if isinstance(error, asyncio.CancelledError) else "infrastructure_failure"
        result["error"] = str(error)[:300] or type(error).__name__
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await clients.close()
        result["completed_wall_monotonic_ns"] = time.monotonic_ns()
        path = folder / "requests.jsonl"
        result["requests_sha256"] = file_sha(path) if path.exists() else None
        write_json(folder / "result.json", result)
    return result


async def stdio_worker(args, config):
    fixtures, pcm, tail = prepare_fixtures(args.output, cadence=validate_request_cadence(config["services"].get("request_cadence")))
    if (config["services"]["warmup_requests"], config["services"]["measured_requests"], config["services"]["concurrency_per_service"]) != (2, 20, 1):
        raise ServiceBenchmarkError("unsupported service counts in authoritative config")
    if (config["services"]["during_cohorts"], config["services"]["measured_requests_per_cohort"],
            config["services"]["warmup_cohort"]) != (4, 5, 0):
        raise ServiceBenchmarkError("unsupported declared service cohort layout")
    print(json.dumps({"kind": "ready", "pid": os.getpid(), "fixture_binding_sha256": fixtures["fixture_binding_sha256"],
                      "fixtures_sha256": file_sha(args.output / "fixtures.json")}), flush=True)
    reader = asyncio.StreamReader(limit=256*1024)
    await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    task = None
    async def run(command):
        try:
            result = await measure(command, fixtures, pcm, tail, args.output)
        except (OSError, ValueError, RuntimeError) as error:
            result = {"schema_version": 1, "phase": command.get("phase"), "status": "infrastructure_failure",
                      "fixture_binding_sha256": fixtures["fixture_binding_sha256"], "requests": [],
                      "error": str(error)[:300]}
        print(json.dumps({"kind": "phase_completed", "id": command["id"], "result": result}, allow_nan=False), flush=True)
    while raw := await reader.readline():
        command = json.loads(raw)
        if command["cmd"] == "measure":
            if task is not None and not task.done():
                raise ServiceBenchmarkError("only one fixed service cohort can be active")
            task = asyncio.create_task(run(command))
            print(json.dumps({"kind": "measure_started", "id": command["id"]}), flush=True)
        elif command["cmd"] == "cancel":
            if task is not None and not task.done():
                task.cancel()
                await task
            print(json.dumps({"kind": "cancelled", "id": command["id"]}), flush=True)
        elif command["cmd"] == "close":
            if task is not None and not task.done():
                task.cancel()
                await task
            print(json.dumps({"kind": "closed", "id": command["id"]}), flush=True)
            return
        else:
            raise ServiceBenchmarkError("unknown service workload command")
    if task is not None and not task.done():
        task.cancel()
        await task


class ServiceLoadWorker:
    """Lightweight parent adapter; the existing Decider venv supplies websockets."""
    def __init__(self, config, output, *, python=SERVER_ROOT / "decider/.venv/bin/python"):
        if sys.platform != "linux":
            raise ServiceBenchmarkError("protected model requests run only on dev-ubuntu")
        self.output = Path(output)
        self.output.mkdir()
        self.stderr = (self.output / "worker-stderr.log").open("wb")
        self.process = subprocess.Popen([str(python), str(Path(__file__).resolve()), "--config", str(config),
                                         "--output", str(self.output), "--stdio"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.stderr)
        self.buffer, self.messages, self.sequence = b"", [], 0
        try:
            self.ready = self.wait("ready", timeout=10)
        except BaseException:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.stderr.close()
            raise

    def send(self, command):
        self.sequence += 1
        command = {"id": self.sequence, **command}
        self.process.stdin.write((json.dumps(command, allow_nan=False)+"\n").encode())
        self.process.stdin.flush()
        return self.sequence

    def poll(self):
        while select.select([self.process.stdout], [], [], 0)[0]:
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                break
            self.buffer += chunk
            if len(self.buffer) > 4*1024*1024:
                raise ServiceBenchmarkError("worker exceeded bounded receipt reader")
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                self.messages.append(json.loads(line))
        return list(self.messages)

    def wait(self, kind, *, request_id=None, timeout=30):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            self.poll()
            for index, message in enumerate(self.messages):
                if message.get("kind") == kind and (request_id is None or message.get("id") == request_id):
                    return self.messages.pop(index)
            if self.process.poll() is not None:
                raise ServiceBenchmarkError("service client worker exited before receipt")
            select.select([self.process.stdout], [], [], min(.05, max(0, deadline-time.monotonic())))
        raise ServiceBenchmarkError("bounded service client receipt watchdog expired")

    def begin(self, phase, *, count, warmup, window=None):
        request_id = self.send({"cmd": "measure", "phase": phase, "measured_requests": count,
                                "warmup_requests": warmup, "window": window})
        self.wait("measure_started", request_id=request_id, timeout=3)
        return request_id

    def finish(self, request_id, *, timeout=30):
        return self.wait("phase_completed", request_id=request_id, timeout=timeout)["result"]

    def take_completed(self, request_id):
        self.poll()
        for index, message in enumerate(self.messages):
            if message.get("kind") == "phase_completed" and message.get("id") == request_id:
                return self.messages.pop(index)["result"]
        return None

    def cancel(self):
        request_id = self.send({"cmd": "cancel"})
        self.wait("cancelled", request_id=request_id, timeout=5)

    def close(self):
        try:
            if self.process.poll() is None:
                request_id = self.send({"cmd": "close"})
                self.wait("closed", request_id=request_id, timeout=5)
                self.process.wait(timeout=5)
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=5)
            self.stderr.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stdio", action="store_true", required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("service model requests are forbidden on the development Mac")
    args.output.mkdir(exist_ok=True)
    try:
        asyncio.run(stdio_worker(args, json.loads(args.config.read_text())))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
