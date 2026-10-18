import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image, ImageChops

from gothic_shorts import cli, cover

needs_font = pytest.mark.skipif(not Path(cover.FONT).is_file(), reason="macOS Arial Black is unavailable")


@needs_font
@pytest.mark.parametrize("sub", ["БЫЛО → СТАЛО", "BEFORE → AFTER", None])
def test_render_keeps_long_title_and_optional_line_in_centre_square(tmp_path, sub):
    title = "ПРЕДМЕТЫ И МЕБЕЛЬ"
    image = cover.render(Image.new("RGB", (1920, 1080), "blue"), title.lower(), sub)
    assert image.size == (1080, 1920)
    assert image.mode == "RGB"

    lettering = cover._lettering(title, sub)
    opaque = lettering.getchannel("A").point(lambda value: 255 if value == 255 else 0)
    bounds = opaque.getbbox()
    assert bounds is not None
    left, top, right, bottom = bounds
    assert 50 <= left < right <= 1030
    assert 420 <= top < bottom <= 1500
    assert image.getpixel((0, 0)) == (0, 0, 255)
    path = cover.save(image, tmp_path / "cover.jpg")
    assert 0 < path.stat().st_size <= 2_000_000
    with Image.open(path) as jpeg:
        assert jpeg.format == "JPEG"
        assert jpeg.size == (1080, 1920)


@needs_font
def test_render_uppercases_title_and_omits_missing_second_line():
    frame = Image.new("RGB", (1080, 1920), "blue")
    lower = cover.render(frame, "магия", None)
    upper = cover.render(frame, "МАГИЯ", None)
    comparison = cover.render(frame, "МАГИЯ", "БЫЛО → СТАЛО")
    assert ImageChops.difference(lower, upper).getbbox() is None
    assert ImageChops.difference(upper, comparison).getbbox() is not None
    assert frame.getpixel((0, 960)) == (0, 0, 255)


def test_save_compresses_noisy_frame_below_thumbnail_limit(tmp_path):
    # Real game frames can be far less compressible than a flat test background.
    noise = Image.frombytes("RGB", (1080, 1920), Image.effect_noise((1080 * 3, 1920), 100).tobytes())
    path = cover.save(noise, tmp_path / "nested/cover.jpg")
    assert path.stat().st_size <= 2_000_000
    with Image.open(path) as image:
        assert image.size == (1080, 1920)
        assert image.format == "JPEG"


def test_frame_reads_image_and_crops_the_centre(tmp_path, monkeypatch):
    source = tmp_path / "frame.png"
    image = Image.new("RGB", (1920, 1920), "blue")
    image.paste("red", (420, 0, 1500, 1920))
    image.save(source)
    run = Mock()
    monkeypatch.setattr(cover.subprocess, "run", run)

    result = cover.frame(source, 3)

    assert result.size == (1080, 1920)
    assert result.getpixel((540, 960)) == (255, 0, 0)
    run.assert_not_called()


def test_frame_extracts_requested_video_time_with_ffmpeg(tmp_path, monkeypatch):
    source = tmp_path / "short.mp4"
    source.write_bytes(b"video")
    encoded = BytesIO()
    Image.new("RGB", (1080, 1920), "red").save(encoded, "PNG")
    run = Mock(return_value=SimpleNamespace(stdout=encoded.getvalue()))
    monkeypatch.setattr(cover.subprocess, "run", run)

    result = cover.frame(source, 1.8)

    assert result.size == (1080, 1920)
    command = run.call_args.args[0]
    assert command[command.index("-ss") + 1] == "1.8"
    assert command[command.index("-i") + 1] == str(source)
    assert command[command.index("-frames:v") + 1] == "1"
    assert "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920" in command[
        command.index("-vf") + 1
    ]


@pytest.mark.parametrize("at", [-1, float("nan"), float("inf")])
def test_frame_rejects_invalid_time(tmp_path, at):
    with pytest.raises(cover.CoverError, match="time"):
        cover.frame(tmp_path / "short.mp4", at)


def test_frame_reports_empty_video_frame(tmp_path, monkeypatch):
    source = tmp_path / "short.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(cover.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=b""))
    with pytest.raises(cover.CoverError, match="cannot extract a frame"):
        cover.frame(source, 999)


@pytest.mark.parametrize("custom", [False, True], ids=["image-defaults", "video-custom"])
def test_cli_cover_passes_source_time_and_text_to_renderer(tmp_path, monkeypatch, capsys, custom):
    source = tmp_path / ("short.mp4" if custom else "frame.png")
    output = tmp_path / ("export/topic.jpg" if custom else "cover.jpg")
    background, rendered = Image.new("RGB", (1, 1)), Image.new("RGB", (1, 1))
    frame = Mock(return_value=background)
    render = Mock(return_value=rendered)
    save = Mock(return_value=output)
    monkeypatch.setattr(cover, "frame", frame)
    monkeypatch.setattr(cover, "render", render)
    monkeypatch.setattr(cover, "save", save)
    arguments = ["gothic-shorts", "cover", str(source), "--title", "МАГИЯ"]
    if custom:
        arguments += ["--at", "1.8", "--sub", "БЫЛО → СТАЛО", "--out", str(output)]
    monkeypatch.setattr(sys, "argv", arguments)

    with pytest.raises(SystemExit) as result:
        cli.main()

    assert result.value.code == 0
    frame.assert_called_once_with(source, 1.8 if custom else 0)
    render.assert_called_once_with(background, "МАГИЯ", "БЫЛО → СТАЛО" if custom else None)
    save.assert_called_once_with(rendered, output)
    assert str(output) in capsys.readouterr().out
