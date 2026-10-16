"""Cuts a before/after short from the two takes of shorts/compare/film.sh: each beat (a recorded segment)
the take with the animation work off on top, the one with it on below, 1080x1920; a hook over the first
seconds, a title per beat, the game's sound of the "on" take, -14 LUFS. Optional in the plan: a beat's
"slow" {"from", "to", "<lang>"} (seconds of its segment) marks a slowed stretch in the corner; "music"
{"file", "volume"} (the file under the takes' folder, a theme of the game) lies under the whole short.

    uv run --project shorts python shorts/compare/cut.py bodies ru
    uv run --project shorts python shorts/compare/cut.py bodies en
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
W, H, HALF = 1080, 1920, 960
FPS = 30
LABEL_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
HOOK_FONT = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
GRADE = "eq=gamma=1.18:saturation=1.08"  # the game's noon is dark on a phone; the same for both takes


def text_image(path: Path, items: list[tuple[str, str, int, int, tuple, str]]) -> None:
    """items: (text, font, size, y, color, anchor x: 'l' at 40 px, 'r' at W - 40 or 'c' centered); a dark band under
    each"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for text, font_file, size, y, color, ax in items:
        font = ImageFont.truetype(font_file, size)
        x = {"l": 40, "r": W - 40}.get(ax, W // 2)
        anchor = {"l": "la", "r": "ra"}.get(ax, "ma")
        box = draw.textbbox((x, y), text, font=font, anchor=anchor, stroke_width=0)
        pad = size // 4
        draw.rounded_rectangle((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad), radius=pad,
                               fill=(0, 0, 0, 150))
        draw.text((x, y), text, font=font, fill=color, anchor=anchor, stroke_width=2, stroke_fill=(0, 0, 0, 255))
    img.save(path)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def master(inputs: list[str], mix: str, out: Path) -> None:
    """The sound of the joined video brought to -14 LUFS by one gain: the peaks limited, then loudnorm in two passes,
    linear (one pass is dynamic: it lifts the quiet stretches, a slowed silence under the music). mix: a filter graph
    from the inputs to the sound, ending where the mastering goes on"""
    loud = "loudnorm=I=-14:TP=-1.5:LRA=11"
    pre = f"{mix},alimiter=limit=0.75:level=false"
    probe = subprocess.run(["ffmpeg", "-hide_banner", *inputs, "-filter_complex", f"{pre},{loud}:print_format=json",
                            "-f", "null", "-"], check=True, capture_output=True, text=True).stderr
    key = probe.rindex('"input_i"')  # the json loudnorm prints, among ffmpeg's other lines
    m = json.loads(probe[probe.rindex("{", 0, key):probe.index("}", key) + 1])
    loud += (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
             f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", f"{pre},{loud}[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)])


def main() -> None:
    name, lang = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "ru"
    plan = json.loads((ROOT / "shorts/compare" / f"{name}.json").read_text())
    takes = ROOT / ".shorts/compare" / name
    out = takes / f"short-{lang}.mp4"
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        parts = []
        for i, beat in enumerate(plan["beats"]):
            seg, start, end = beat["segment"], beat["start"], beat["end"]
            items = [
                (plan["labels"][lang][0], LABEL_FONT, 40, 40, (230, 230, 230, 255), "l"),
                (plan["labels"][lang][1], LABEL_FONT, 40, HALF + 40, (255, 214, 120, 255), "l"),
                (beat["title"][lang], LABEL_FONT, 64, HALF - 150, (255, 255, 255, 255), "c"),
            ]
            text_image(tmp / f"t{i}.png", items)
            layers = [f"[2]loop=-1:1,trim=0:{end - start}[t]"]
            overlay = "[s][t]overlay=0:0:shortest=1"
            inputs = ["-i", str(takes / "off" / f"{seg}.mp4"), "-i", str(takes / "on" / f"{seg}.mp4"),
                      "-i", str(tmp / f"t{i}.png")]
            if i == 0:
                hook = plan["hook"][lang]  # a line break in it: two lines, raised by the second
                text_image(tmp / "hook.png", [(hook, HOOK_FONT, 86, HALF - 330 - 100 * hook.count("\n"),
                                               (255, 255, 255, 255), "c")])
                inputs += ["-i", str(tmp / "hook.png")]
                layers.append(f"[3]loop=-1:1,trim=0:{plan['hook_s']}[h]")
                overlay += "[o];[o][h]overlay=0:0:eof_action=pass"
            if "slow" in beat:  # both takes are slowed: the mark in the corner of each half
                slow = beat["slow"]
                mark = (slow[lang], LABEL_FONT, 40, 40, (255, 255, 255, 255), "r")
                text_image(tmp / f"s{i}.png", [mark, (*mark[:3], HALF + 40, *mark[4:])])
                inputs += ["-i", str(tmp / f"s{i}.png")]
                n = len(inputs) // 2 - 1
                layers.append(f"[{n}]loop=-1:1,trim=0:{end - start}[m]")
                overlay += (f"[q];[q][m]overlay=0:0:shortest=1:"
                            f"enable='between(t,{max(slow['from'] - start, 0)},{slow['to'] - start})'")
            graph = ";".join([
                f"[0]trim={start}:{end},setpts=PTS-STARTPTS,scale={W}:{HALF},{GRADE}[a]",
                f"[1]trim={start}:{end},setpts=PTS-STARTPTS,scale={W}:{HALF},{GRADE}[b]",
                "[a][b]vstack[s]", *layers, overlay + ",format=yuv420p[v]",
            ])
            fade = 0.06
            dur = end - start
            audio = (f"[{len(inputs) // 2}]atrim={start}:{end},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={fade},afade=t=out:st={dur - fade}:d={fade}[aud]")
            inputs += ["-i", str(takes / "on" / f"{seg}.wav")]
            part = tmp / f"p{i}.mp4"
            run(["ffmpeg", "-y", *inputs, "-filter_complex", graph + ";" + audio, "-map", "[v]", "-map", "[aud]",
                 "-r", str(FPS), "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
                 "-ar", "48000", "-ac", "2", str(part)])
            parts.append(part)
        (tmp / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
        joined = tmp / "joined.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-c", "copy", str(joined)])
        if "music" in plan:  # the theme under the game's sound, faded in and out over the whole short
            music, total = plan["music"], sum(b["end"] - b["start"] for b in plan["beats"])
            master(["-i", str(joined), "-i", str(takes / music["file"])],
                   f"[1:a]atrim=0:{total},asetpts=PTS-STARTPTS,volume={music['volume']},afade=t=in:d=1,"
                   f"afade=t=out:st={total - 1.5}:d=1.5[m];[0:a][m]amix=inputs=2:duration=first:normalize=0", out)
        else:
            master(["-i", str(joined)], "[0:a]anull", out)
    # frames over the whole short, for a look
    run(["ffmpeg", "-y", "-i", str(out), "-vf", "fps=1,scale=270:-2,tile=6x3", "-frames:v", "1",
         str(out.with_suffix(".png"))])
    print(out)


if __name__ == "__main__":
    main()
