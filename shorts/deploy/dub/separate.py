"""The voices and the rest of a recording, by Demucs (htdemucs_ft): separate.py <wav> <out dir> writes
voices.wav and rest.wav (32-bit float, the input's rate and channels, as long as it). Runs on the GPU server
(install.sh); --fetch only downloads the model.

The model is made for music: its "vocals" are the voices, drums, bass and "other" are the game's music and
sounds, so the rest is those three summed (what `demucs --two-stems vocals` writes as no_vocals).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import soundfile as sf
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model
from julius import resample_frac


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("wav", nargs="?")
    p.add_argument("out", nargs="?")
    p.add_argument("--model", default="htdemucs_ft")
    p.add_argument("--shifts", type=int, default=2, help="random time shifts averaged (quality vs time)")
    p.add_argument("--fetch", action="store_true", help="download the model and stop")
    args = p.parse_args()
    model = get_model(args.model)
    if args.fetch:
        return
    if not (args.wav and args.out):
        p.error("give the wav and the output folder")
    began = time.monotonic()
    data, rate = sf.read(args.wav, dtype="float32", always_2d=True)  # frames x channels
    frames, channels = data.shape
    x = torch.from_numpy(data.T.copy())
    if channels == 1:
        x = x.repeat(2, 1)
    elif channels > 2:
        x = x[:2]
    if rate != model.samplerate:
        x = resample_frac(x, rate, model.samplerate)
    # as demucs' own separate.py: the model wants the mix around 0 with unit spread
    ref = x.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    device = "cuda" if torch.cuda.is_available() else "cpu"
    with torch.no_grad():
        out = apply_model(
            model, ((x - mean) / std)[None], device=device, shifts=args.shifts, split=True, overlap=0.25,
            progress=False,
        )[0]
    out = out * std + mean
    voices = out[model.sources.index("vocals")]
    rest = sum(out[i] for i, name in enumerate(model.sources) if name != "vocals")
    folder = Path(args.out)
    folder.mkdir(parents=True, exist_ok=True)
    for name, y in (("voices", voices), ("rest", rest)):
        if rate != model.samplerate:
            y = resample_frac(y, model.samplerate, rate)
        y = y[:, :frames]
        if y.shape[1] < frames:
            y = torch.nn.functional.pad(y, (0, frames - y.shape[1]))
        if channels == 1:
            y = y.mean(0, keepdim=True)
        sf.write(folder / f"{name}.wav", y.T.cpu().numpy(), rate, subtype="FLOAT")
    print(f"separated {frames / rate:.1f} s with {args.model} on {device} in {time.monotonic() - began:.1f} s")


if __name__ == "__main__":
    main()
