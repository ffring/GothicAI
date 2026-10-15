"""Lines in one voice: clone.py <reference wav> <lines.json> <out dir> [--design design.json] writes
<out dir>/<id>.wav (16-bit mono, 48 kHz) for each {"id", "text"} of lines.json. Runs on the GPU server, through
the VoxCPM2 engine of gothic-voxcpm (audio.cpp's HTTP server, 127.0.0.1:VOXCPM_ENGINE_PORT), so it needs no
GPU memory of its own; the engine serves one request at a time, the game's lines included.

The reference gives the timbre only (VoxCPM2's reference cloning, no transcript): the lines may be in another
language than the recording, as the user's English lines from their Russian takes. With --design
({"description", "sample", "seed"}) a reference that is not there yet is designed first, as the voxcpm bridge
designs an npc's voice ("(description)sample"), with the sample in the lines' language. A take whose length
is far from what its text reads in is redone with another seed (VoxCPM now and then trails off or rushes).
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

RATE = 48000  # VoxCPM2's AudioVAE decodes to 48 kHz
CHARS_PER_S = 15.0  # about how fast a line is read
TAKES = 4


def speak(engine: str, text: str, seed: int, steps: int, ref: Path | None = None) -> bytes:
    body: dict = {
        "model": "voxcpm2",
        "input": text,
        # the engine serves the model streaming: audio.cpp's own retry of bad takes must stay off
        "options": {"seed": seed, "num_inference_steps": steps, "retry_badcase": False},
    }
    if ref is not None:
        body["voice_ref"] = {"type": "path", "path": str(ref)}
    req = urllib.request.Request(
        f"{engine}/v1/audio/speech", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        raise SystemExit(f"the engine refused a line: {err.code} {err.read()[:300]!r}") from err
    except urllib.error.URLError as err:
        raise SystemExit(f"no VoxCPM2 engine at {engine} (is gothic-voxcpm running?): {err}") from err


def seconds(wav: bytes) -> float:
    return (len(wav) - 44) / 2 / RATE  # a 16-bit mono WAV at RATE


def design(engine: str, spec: dict, ref: Path, steps: int) -> None:
    began = time.monotonic()
    for take in range(TAKES):
        wav = speak(engine, f"({spec['description']}){spec['sample']}", int(spec.get("seed", 1)) + take, steps)
        if 3.0 <= seconds(wav) <= 15.0:
            break
        print(f"  the voice's sample take {take + 1} is {seconds(wav):.1f} s long, again")
    ref.write_bytes(wav)
    print(f"designed the voice in {time.monotonic() - began:.1f} s: {spec['description']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("reference")
    p.add_argument("lines")
    p.add_argument("out")
    p.add_argument("--design", help="design the reference first, if it is not there: a JSON file")
    p.add_argument("--engine", default=f"http://127.0.0.1:{os.environ.get('VOXCPM_ENGINE_PORT', '47836')}")
    p.add_argument("--steps", type=int, default=16, help="flow sampling steps per patch (the game uses 10)")
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()
    ref = Path(args.reference).resolve()  # audio.cpp opens it by path
    if args.design and not ref.is_file():
        design(args.engine, json.loads(Path(args.design).read_text()), ref, args.steps)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for line in json.loads(Path(args.lines).read_text()):
        text = str(line["text"]).strip()
        want = len(text) / CHARS_PER_S
        began = time.monotonic()
        for take in range(TAKES):
            wav = speak(args.engine, text, args.seed + take, args.steps, ref)
            if 0.5 * want <= seconds(wav) <= 1.8 * want + 0.5:
                break
            print(f"  {line['id']}: take {take + 1} is {seconds(wav):.1f} s for ~{want:.1f} s of text, again")
        (out / f"{line['id']}.wav").write_bytes(wav)
        print(f"{line['id']}: {seconds(wav):.2f} s in {time.monotonic() - began:.1f} s: {text}")


if __name__ == "__main__":
    main()
