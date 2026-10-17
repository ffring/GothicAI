import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from gothic_shorts import cut
from gothic_shorts.shot import Shot


@pytest.fixture
def take(tmp_path):
    take = tmp_path / "take"
    take.mkdir()
    for name in ("main.mp4", "main.wav", "hero.wav"):
        (take / name).touch()
    events = [
        {"kind": "rec_start", "frame": 0},
        {"kind": "hero", "frame": 30, "ms": 1000, "text": "Ксардас, привет.",
         "wav": str(take / "hero.wav")},
        {"kind": "heard", "frame": 65, "text": "Ксардас, привет."},
        {"kind": "caption", "frame": 90, "text": "Он думает"},
        {"kind": "npc_line", "frame": 180, "ms": 2500, "npc": "XARDAS", "name": "Ксардас",
         "text": "Ты снова здесь?"},
        {"kind": "npc_voice_end", "frame": 270, "npc": "XARDAS"},
        {"kind": "caption", "frame": 300, "text": ""},
        {"kind": "rec_stop", "frame": 360, "frames": 360},
    ]
    (take / "events.jsonl").write_text(
        "\n".join(json.dumps({"seg": "main", **event}, ensure_ascii=False) for event in events)
    )
    return take


def _text_height(text, path, font_file, size, color, **kwargs):
    # Stable image heights keep the command snapshot independent of installed fonts and Pillow versions.
    return {84: 200, 64: 150, 62: 180}[size] + (40 if kwargs.get("name") else 0)


def _record_commands(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="2.200000\n", stderr="")
        is_sting = any(str(arg).endswith("sting.mp4") for arg in command)
        measured = {
            "input_i": "-14.7" if is_sting else "-19.1",
            "input_tp": "-3.2", "input_lra": "2.0",
            "input_thresh": "-29.1", "target_offset": "0.1",
        }
        samples = 105_600 if is_sting else 408_000
        audio_info = (
            f"[Parsed_ashowinfo_29 @ 0x1] n:0 pts:0 pts_time:0 fmt:s16 rate:48000 nb_samples:{samples}\n"
        )
        return SimpleNamespace(returncode=0, stdout="", stderr=audio_info + json.dumps(measured))

    monkeypatch.setattr(cut.subprocess, "run", run)
    return commands


def test_unbranded_ffmpeg_commands_match_the_original_cut(tmp_path, take, monkeypatch):
    # Captured from the original cut.py before branding; never regenerate from the implementation here.
    expected = json.loads((Path(__file__).parent / "fixtures/cut-unbranded.json").read_text())
    render_text = Mock(side_effect=_text_height)
    monkeypatch.setattr(cut, "_text_png", render_text)

    class NoBrandAssets:
        def __truediv__(self, name):
            raise AssertionError(f"an unbranded cut accessed a brand asset: {name}")

    monkeypatch.setattr(cut, "BRAND", NoBrandAssets())
    monkeypatch.setattr(cut, "_brand_text_png", Mock(side_effect=AssertionError("branded text")))
    commands = _record_commands(monkeypatch)
    cut.cut(Shot("probe", "Проба", "rec main", hook="Я соврал NPC", brand=False), take)
    normalized = [[arg.replace(str(tmp_path), "<tmp>") for arg in command] for command in commands]
    assert normalized == expected
    assert [(c.args[2], c.args[3], c.args[4]) for c in render_text.call_args_list] == [
        ("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", 84, (255, 255, 255)),
        ("/System/Library/Fonts/Supplemental/Georgia Bold.ttf", 64, (255, 214, 102)),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 62, (255, 214, 102)),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 62, (255, 255, 255)),
    ]


def test_brand_seals_body_then_appends_sting_and_samples_it(tmp_path, take, monkeypatch):
    brand = tmp_path / "brand"
    brand.mkdir()
    for name in ("seal-corner.png", "sting.mp4"):
        (brand / name).touch()
    monkeypatch.setattr(cut, "BRAND", brand)
    monkeypatch.setattr(
        cut, "_brand_text_png",
        lambda text, path, size, color, **kwargs: _text_height(text, path, "PT Serif", size, color, **kwargs),
    )
    commands = _record_commands(monkeypatch)
    cut.cut(Shot("probe", "Проба", "rec main", hook="Я соврал NPC"), take)

    probe, measurement, sting_measurement, render, sheet = commands
    assert probe[0] == "ffprobe"
    assert probe[-1] == str(brand / "sting.mp4")
    measured_graph = measurement[measurement.index("-filter_complex") + 1]
    assert (
        "[ac]alimiter=limit=0.75:level=false,loudnorm=I=-14:TP=-1.5:LRA=11:"
        "print_format=json,aresample=48000,ashowinfo[probe]"
    ) in measured_graph
    assert "sting.mp4" not in " ".join(measurement)
    assert "seal-corner.png" not in " ".join(measurement)
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vc][ac]" in measured_graph
    assert "scale=" not in measured_graph
    assert "overlay=" not in measured_graph
    assert [measurement[i + 1] for i, value in enumerate(measurement) if value == "-map"] == [
        "[vc]", "[probe]",
    ]
    assert [sting_measurement[i + 1] for i, value in enumerate(sting_measurement) if value == "-i"] == [
        str(brand / "sting.mp4"),
    ]
    sting_measured_graph = sting_measurement[sting_measurement.index("-filter_complex") + 1]
    assert "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[ac]" in sting_measured_graph
    assert "alimiter=limit=0.75:level=false,loudnorm=I=-14" in sting_measured_graph

    graph = render[render.index("-filter_complex") + 1]
    assert [render[i + 1] for i, value in enumerate(render) if value == "-i"][-2:] == [
        str(brand / "seal-corner.png"), str(brand / "sting.mp4"),
    ]
    assert "[o3][7:v]overlay=x=940:y=110[sealed]" in graph
    assert "[8:v]fps=30,setsar=1,settb=AVTB,setpts=PTS-STARTPTS[stingv]" in graph
    assert "[bodyv][bodya][stingv][stinga]concat=n=2:v=1:a=1[vout][aout]" in graph
    assert "measured_I=-19.1:measured_TP=-3.2:measured_LRA=2.0:measured_thresh=-29.1" in graph
    assert "offset=0.1:linear=true" in graph
    assert graph.count("loudnorm=") == 2
    # The actual AV concat lasts 8.5 s, beyond the nominal 8.45 s from the event timeline.
    assert (
        "[sealed]fps=30,tpad=stop_mode=clone:stop_duration=8.500000,trim=duration=8.500000,"
        "settb=AVTB,setpts=PTS-STARTPTS[bodyv]"
    ) in graph
    assert "apad,atrim=duration=8.500000," in graph
    body_duration = 8.5
    assert f"afade=t=out:st={max(0, body_duration - 0.02):.6f}:d=0.02[bodya]" in graph
    assert (
        "[8:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=duration=2.200000,"
        "asetpts=PTS-STARTPTS,alimiter=limit=0.75:level=false,loudnorm=I=-14:TP=-1.5:LRA=11:"
        "measured_I=-14.7:measured_TP=-3.2:measured_LRA=2.0:measured_thresh=-29.1:"
        "offset=0.1:linear=true,aresample=48000,afade=t=in:d=0.02,"
        "afade=t=out:st=2.180000:d=0.02[stinga]"
    ) in graph
    assert render[render.index("-map") + 1] == "[vout]"

    sheet_filter = sheet[sheet.index("-vf") + 1]
    sampled = [int(frame) for frame in re.findall(r"eq\(n,(\d+)\)", sheet_filter)]
    assert len(sampled) == 12
    assert body_duration * 30 < sampled[-1] < (body_duration + 2.2) * 30
    assert sampled[-1] == round((body_duration + 1.1) * 30)


@pytest.mark.skipif(not Path(cut.BRAND_FONT).is_file(), reason="macOS PT Serif collection is unavailable")
def test_brand_pngs_use_pt_serif_bold_warm_outline_and_shorts_safe_area(tmp_path, monkeypatch):
    font_calls = []
    real_truetype = cut.ImageFont.truetype

    def truetype(font, size, *args, **kwargs):
        font_calls.append((font, kwargs.get("index", 0)))
        return real_truetype(font, size, *args, **kwargs)

    monkeypatch.setattr(cut.ImageFont, "truetype", truetype)
    shown = [
        cut.Shown("hook", "Я попробовал обмануть Ксардаса", 0, 2.8),
        cut.Shown("caption", "Он реагирует, пока думает", 3, 5),
        cut.Shown("line", "Ксардас, меня прислал Ли. Он очень просил отдать посох.", 0, 3, hero=True),
        cut.Shown("line", "Ты смеешь испытывать моё терпение?" * 4, 3, 8, name="Ксардас"),
    ]
    overlays = cut._overlays(shown, tmp_path, brand=True)
    assert font_calls
    assert set(font_calls) == {("/System/Library/Fonts/Supplemental/PTSerif.ttc", 3)}
    for item, overlay in zip(shown, overlays, strict=True):
        with Image.open(overlay.png) as image:
            left, top, right, bottom = image.getchannel("A").getbbox()
            assert 60 <= left < right <= 930  # to the left of both Shorts UI and the fixed seal
            assert 150 <= overlay.y + top < overlay.y + bottom <= 1540
            colors = {rgba for count, rgba in image.getcolors(image.width * image.height)}
        assert (20, 12, 8, 255) in colors
        assert (0, 0, 0, 255) not in colors
        expected = (255, 214, 102) if item.hero or item.kind == "caption" else (255, 255, 255)
        assert (*expected, 255) in colors
        if item.name:
            assert (230, 190, 120, 255) in colors
