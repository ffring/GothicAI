"""Synthetic WAVs only; game speech is never committed as a test fixture."""

import json
import struct
from types import SimpleNamespace

import numpy as np

from gothic_tts import originals


def wav(fmt, data, fact=None):
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if fact is not None:
        chunks += b"fact" + struct.pack("<II", 4, fact)
    chunks += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks


def test_ima_wave_matches_openal_reference():
    # Synthetic vectors evaluated with the vendored OpenAL Soft FmtIMA4 reference in
    # OpenGothic/lib/Tempest/Engine/thirdparty/openal-soft/core/voice.cpp. Its integer
    # product rule, signed saturation, index updates and low-nibble-first order are
    # the reference; no audio decoder executable or game recording is needed here.
    encoded = bytes.fromhex("1032547698badcfeff77")
    expected = np.array(
        [
            25000,
            28079,
            32767,
            32767,
            32767,
            32767,
            32767,
            32767,
            32767,
            28672,
            17500,
            572,
            -20981,
            -32768,
            -32768,
            -32768,
            -32768,
            -32768,
            -32768,
            28670,
            32767,
            -12000,
            -11998,
            -11993,
            -11985,
            -11975,
            -11963,
            -11946,
            -11916,
            -11853,
            -11862,
            -11886,
            -11923,
            -11971,
            -12027,
            -12109,
            -12252,
            -12546,
            -13177,
            -14534,
            -11624,
            -5386,
        ],
        dtype=np.int16,
    )
    block = 4 + len(encoded)
    fmt = struct.pack("<HHIIHHHH", 0x11, 1, originals.RATE, 0, block, 4, 2, 21)
    data = struct.pack("<hBB", 25000, 85, 0) + encoded + struct.pack("<hBB", -12000, 8, 0) + encoded
    # The advisory fact length does not truncate encoded blocks: this keeps game
    # snapshot durations and candidate order identical to the previous conversion.
    decoded = np.rint(originals.decode_wav(wav(fmt, data, fact=39)) * 32768).astype(np.int32)
    assert np.max(np.abs(decoded - expected.astype(np.int32))) <= 1


def test_pcm16_wave_is_lossless():
    expected = np.array([-32768, -12345, -1, 0, 1, 12345, 32767], dtype="<i2")
    fmt = struct.pack("<HHIIHH", 1, 1, originals.RATE, originals.RATE * 2, 2, 16)
    decoded = np.rint(originals.decode_wav(wav(fmt, expected.tobytes())) * 32768).astype(np.int32)
    np.testing.assert_array_equal(decoded, expected)


def test_prepare_reuses_matching_source_and_rebuilds_changes(tmp_path, monkeypatch):
    game = tmp_path / "game"
    (game / "Data").mkdir(parents=True)
    speech = game / "Data" / "Speech.vdf"
    speech.write_bytes(b"synthetic archive")
    monkeypatch.setenv("STORYGRAPH_TOOL", __file__)
    messages = [["DIA_XARDAS_TEST_14_00.WAV", "Synthetic line."]]
    exports = []

    def export(argv, **kwargs):
        exports.append(argv)
        return SimpleNamespace(stdout=json.dumps({"messages": messages}).encode())

    monkeypatch.setattr(originals.subprocess, "run", export)
    monkeypatch.setattr(originals, "speech_index", lambda game: {})
    built = []
    cid = "NONE_100_XARDAS"

    def build(selected, index, texts, out):
        built.append(selected)
        path = out / f"{selected}.wav"
        path.write_bytes(b"synthetic sample")
        return {"id": selected, "file": str(path)}

    monkeypatch.setattr(originals, "build", build)
    out = tmp_path / "voices"
    progress = []
    path = originals.prepare(game, out, ids=[cid], progress=lambda n, total: progress.append((n, total)))
    first = path.read_bytes()
    originals.prepare(game, out, ids=[cid])
    assert built == [cid]
    assert path.read_bytes() == first
    assert progress == [(1, 1)]
    assert all(argv[-1] == "--ou-messages" for argv in exports)
    manifest = json.loads(first)
    assert manifest["source"]["game_dir"] == str(game)
    assert manifest["source"]["speech"][0] == {
        "name": "Data/Speech.vdf",
        "size": speech.stat().st_size,
        "mtime_ns": speech.stat().st_mtime_ns,
    }
    speech.write_bytes(b"changed synthetic archive")
    originals.prepare(game, out, ids=[cid])
    assert built == [cid] * 2
    messages[0][1] = "Changed synthetic text."
    originals.prepare(game, out, ids=[cid])
    assert built == [cid] * 3
    monkeypatch.setattr(originals, "ALGORITHM_VERSION", originals.ALGORITHM_VERSION + 1)
    originals.prepare(game, out, ids=[cid])
    assert built == [cid] * 4
