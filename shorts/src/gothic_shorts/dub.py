"""The English audio track of a short (its dub), for YouTube to offer next to the Russian sound: exactly as
long as the Russian short and as loud as the cut (-14 LUFS). YouTube's API has no call for audio tracks: the
file is added in Studio (Subtitles, the video, the Audio column), which needs the channel's advanced features.

    gothic-shorts dub <shot> [take]  -> <take>/dub.en.wav, and dub.en.mp4 (the Russian short with it)
    gothic-shorts dub <explainer>    -> <workspace>/render/dub.en.wav, dub.en.mp4

A take of a shot:
- the game's sound without the npc's Russian voices: Demucs on the GPU server splits main.wav into the voices
  and the rest (shorts/deploy/dub, installed by `scripts/deploy-gpu.sh dub`); the rest stands in for the sound
  while an npc speaks, from a beat before its line to a beat after, the recording's own sound elsewhere;
- every line in English: the take's subs.en.json (what the caption track says), said by Inworld in a clone of
  the npc's Russian voice (the npc's from the AI server, `gothic-ai voices`; the hero's the shot's
  hero_voice), or in a voice designed from the same description (english_voices), starting where the
  Russian line starts, as loud as it;
- cut as the short is (timeline.keep): the pauses while the AI thinks go out with the same parts.

An explainer: its English script (script.en.txt, the caption track's text) in the user's own voice, cloned
from their recorded lines (voice/clean/) by Inworld, or by VoxCPM2 on the GPU server, and cleaned as those
were, each line from where its Russian line's first word is said, as loud as the Russian voice, over the
plan's music bed (plan.json) ducked under the English lines as the render ducks it under the Russian ones.

A line longer than its slot (until the next line starts) is sped up, by MAX_TEMPO at most; one still too long
is reported, to be shortened in the translation. The lip sync stays the Russian one.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial, reduce
from pathlib import Path

from gothic_launcher.commands import child_env, tools
from gothic_launcher.settings import Config

from . import srt
from .cut import CutError, load_subs
from .film import shorts_dir
from .gpu import GpuError, job
from .shot import Shot
from .timeline import Timeline, build, read_events
from .voice import LEAD_IN_S, VOICE_ID, VoiceError, recording, say
from .words import LEAD, clean_chain

TARGET_LUFS = -14.0  # as the cut
PEAK_DB = -1.5  # sample peaks of the track, after the limiter
LOCALES = {"en": "en-US"}  # a dub's language -> the TTS's
MAX_TEMPO = 1.15  # a line may be sped up this much to fit its slot
GAP_S = 0.15  # an English line ends this long before the next one starts
TAIL_S = 0.3  # and the last one this long before the short ends
STING_S = 2.2  # the channel's end sting; its mastered sound is kept from the rendered Russian short
LEAD_S = 0.05  # silence kept before a line's first sound (the line is placed this much early)
MARGIN_S = 0.3  # the voices are taken out from this long before an npc's line to this long after it
RAMP_S = 0.1  # fades between the recording's sound and the separated one
MAX_GAIN_DB = 20.0
# of the user's recorded lines, for the cloned voice: at least, at most (VoxCPM2's encoder takes up to 15 s;
# Inworld clones best from 10-30 s)
REFERENCE_S = (8.0, 14.0)
REFERENCE_INWORLD_S = (20.0, 29.0)
REFERENCE_GAP_S = 0.3  # between two lines of it
# the voices the dubs speak in, kept for every short and every checkout: the user's recorded one
# (user.<provider>.wav), an npc's Russian sample Inworld clones (*.ru.wav), one VoxCPM designed (*.ref.wav)
VOICES = Path.home() / ".local/share/gothic-ai/voices"
USER_VOICE = "USER"  # the Inworld clone of user.inworld.wav, in Inworld's library and the TTS's voice cache
# what VoxCPM designs a voice reading (the voxcpm bridge's VOICE_SAMPLE, in the dub's language)
SAMPLES = {"en": "You're back. Good. We don't have much time, so listen carefully: I won't say it twice."}
# what an npc's Russian voice reads for Inworld to clone it (10-30 s is best)
RUSSIAN_SAMPLE = (
    "Слушай внимательно, повторять я не стану. Дорога к городу идёт мимо фермы и дальше через лес, где "
    "водятся волки. Если встретишь стражу, не беги и не хватайся за оружие: скажи, что идёшь к паладинам. "
    "И возвращайся, когда узнаешь что-нибудь стоящее."
)
DUCK_RAMP_S = 0.35  # the explainer's music ducks under a line this fast (Explainer.tsx)
FPS = 30  # the explainer's frame rate (Root.tsx)
SEPARATOR = "htdemucs_ft"  # shorts/deploy/dub/separate.py

# the silence at both ends of a TTS line trimmed to LEAD_S before and a breath after
TRIM = (
    f"silenceremove=start_periods=1:start_threshold=-50dB:start_silence={LEAD_S},areverse,"
    "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse"
)


class DubError(RuntimeError):
    pass


@dataclass(frozen=True)
class Slot:
    """A line of the dub: where its English starts and by when it has to be over (seconds of the short)."""

    who: str  # "hero", an npc's symbol, or "voice" (an explainer's)
    ru: str
    en: str
    start: float
    end: float

    @property
    def room(self) -> float:
        return max(self.end - self.start, 0.01)


@dataclass(frozen=True)
class Fit:
    tempo: float  # 1 as voiced, above 1 sped up
    seconds: float  # how long the line is at that tempo
    fits: bool


@dataclass
class Dub:
    wav: Path
    preview: Path | None
    duration: float
    ru_duration: float | None  # of the Russian short, when it is there
    lufs: float | None
    ru_lufs: float | None
    rows: list[str] = field(default_factory=list)  # a line each: where, how long, tempo, gain, text
    too_long: list[str] = field(default_factory=list)  # lines to shorten: the report


def slots(lines: list[tuple[str, str, str, float, float]], duration: float) -> list[Slot]:
    """(who, Russian, English, where its speech starts, where the Russian ends) per line, in the short's time
    and order -> each line's slot: up to the next line's start. Where the Russian lines overlap (one speaks
    over another), the English may too, as long as the Russian one went on."""
    out = []
    for i, (who, ru, en, start, ru_end) in enumerate(lines):
        after = [line[3] for line in lines[i + 1 :] if line[3] > start]
        end = duration - TAIL_S if not after else max(min(after) - GAP_S, ru_end)
        out.append(Slot(who, ru, en, start, min(end, duration - TAIL_S)))
    return out


def fit(seconds: float, slot: Slot, max_tempo: float = MAX_TEMPO) -> Fit:
    """The tempo that fits a line of `seconds` of speech into its slot, if a slight one does."""
    if seconds <= slot.room:
        return Fit(1.0, seconds, True)
    tempo = seconds / slot.room
    if tempo <= max_tempo:
        return Fit(round(tempo, 3), slot.room, True)
    return Fit(max_tempo, seconds / max_tempo, False)


def take_slots(tl: Timeline, subs: dict[str, str]) -> list[Slot]:
    """The take's lines in the short's time: the hero's speech starts after its recording's lead-in."""
    rows = [
        (
            line.who,
            line.text,
            subs[line.text],
            tl.to_cut(line.start) + (LEAD_IN_S if line.hero else 0.0),
            tl.to_cut(line.end),
        )
        for line in tl.lines
    ]
    return slots(rows, tl.cut_duration)


def merged(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def mask(spans: list[tuple[float, float]], ramp: float) -> str:
    """An ffmpeg expression of t: 1 inside the spans, 0 away from them, a linear ramp of `ramp` s at each
    edge (outside it)."""
    parts = [
        f"clip(min((t-{a - ramp:.3f})/{ramp:g},({b + ramp:.3f}-t)/{ramp:g}),0,1)" for a, b in merged(spans)
    ]
    return reduce(lambda x, y: f"max({x},{y})", parts) if parts else "0"


def music_volume(music: dict, end: float, spans: list[tuple[float, float]]) -> str:
    """The explainer's bed as Explainer.tsx's Music plays it: `volume` faded in and out, `duck` times that
    under each line (DUCK_RAMP_S ramps either side of it)."""
    fade_in, fade_out = float(music.get("fadeIn", 1.0)), float(music.get("fadeOut", 1.5))
    duck = float(music.get("duck", 0.5))
    fade = f"min(clip(t/{fade_in:g},0,1),clip(({end:.3f}-t)/{fade_out:g},0,1))"
    return f"{float(music['volume']):g}*{fade}*(1-{1 - duck:g}*{mask(spans, DUCK_RAMP_S)})"


def english_provider(spec: str, locale: str) -> str:
    """The launcher's TTS provider spec for voices designed in `locale`: Inworld designs a voice per language
    (VoxCPM's are designed reading Russian), so another provider gives way to it."""
    name, _, rest = spec.partition(":")
    kept = rest.split(",") if name.strip() == "inworld" else []
    opts = [o for o in kept if o and not o.startswith("language=")]
    return "inworld:" + ",".join([*opts, f"language={locale}"])


def reference_count(durations: list[float], want: tuple[float, float] = REFERENCE_S) -> int:
    """How many of the recorded lines, from the first, make a reference of at least want[0] s and at most
    want[1] (with REFERENCE_GAP_S after each); at least one (the reference is cut to want[1] then)."""
    least, most = want
    total, n = 0.0, 0
    for d in durations:
        if n and total + d + REFERENCE_GAP_S > most:
            break
        total += d + REFERENCE_GAP_S
        n += 1
        if total >= least:
            break
    return max(n, 1)


def english_script(ws: Path, lang: str) -> list[str]:
    path = ws / f"script.{lang}.txt"
    if not path.is_file():
        raise DubError(f"no {path}: the translated script, a line per voiced line")
    rows = [row for row in path.read_text().splitlines() if row.strip()]
    return [" ".join(c.strip() for c in row.split(srt.CHUNK) if c.strip()) for row in rows]


# --- sound --------------------------------------------------------------------------------------------------


def _ffmpeg(args: list[str], what: str) -> str:
    done = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-y", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise DubError(f"{what}: ffmpeg failed:\n{done.stderr.strip()[-2500:]}")
    return done.stderr


def _seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def _probe(path: Path, stream: str = "a:0") -> float | None:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", "stream=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )  # fmt: skip
    try:
        return float(done.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def loudness(path: Path, start: float | None = None, length: float | None = None) -> float | None:
    """Integrated loudness (LUFS) of the file, or of a part of it, as it sounds in a stereo mix (a mono line
    on both sides); None for silence."""
    cut = ([] if start is None else ["-ss", f"{max(start, 0.0):.3f}"]) + (
        [] if length is None else ["-t", f"{length:.3f}"]
    )
    err = _ffmpeg([*cut, "-i", str(path), "-af", "aformat=channel_layouts=stereo,loudnorm=print_format=json",
                   "-f", "null", "-"], f"loudness of {path.name}")  # fmt: skip
    found = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err)
    if not found:
        return None
    value = float(json.loads(found.group(0))["input_i"])
    return value if value > -70 else None


def _gain(target: float | None, level: float | None) -> float:
    if target is None or level is None:
        return 0.0
    return max(-MAX_GAIN_DB, min(MAX_GAIN_DB, target - level))


def trim(src: Path, dst: Path, chain: str = TRIM) -> float:
    """The line as 48 kHz mono with its silence trimmed (`chain`); returns its length."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(["-i", str(src), "-af", chain, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le", str(dst)],
            f"trim {src.name}")  # fmt: skip
    return _seconds(dst)


def _line_filters(placed: list[tuple[Path, float, Fit, float]], first: int) -> tuple[list[str], list[str]]:
    """Each line (file, where its speech starts, its fit, gain dB) as a stream on the short's time."""
    graph, labels = [], []
    for i, (_, start, f, gain) in enumerate(placed):
        ms = max(0, round((start - LEAD_S) * 1000))
        tempo = f",atempo={f.tempo:.4f}" if f.tempo != 1.0 else ""
        graph.append(
            f"[{first + i}:a]aformat=sample_rates=48000:channel_layouts=stereo{tempo},"
            f"volume={gain:.2f}dB,adelay={ms}|{ms}[l{i}]"
        )
        labels.append(f"[l{i}]")
    return graph, labels


def finish(mix: Path, out: Path, duration: float) -> float | None:
    """The track at TARGET_LUFS: one gain for the whole of it, a limiter for the peaks it raises, exactly
    `duration` long (48 kHz stereo, 24-bit); returns its loudness."""
    level = loudness(mix)
    gain = 0.0 if level is None else TARGET_LUFS - level
    samples = round(duration * 48000)
    out.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(["-i", str(mix), "-af",
             f"volume={gain:.2f}dB,alimiter=limit={10 ** (PEAK_DB / 20):.4f}:attack=5:release=50:level=0,"
             f"aresample=48000,apad,atrim=end_sample={samples}",
             "-ac", "2", "-c:a", "pcm_s24le", str(out)], "the track's loudness")  # fmt: skip
    return loudness(out)


def preview(short: Path, track: Path, out: Path) -> Path:
    """The Russian short with the dub as its first (default) sound and the Russian as its second, to check
    it as a viewer gets it."""
    _ffmpeg(["-i", str(short), "-i", str(track), "-map", "0:v", "-map", "1:a", "-map", "0:a",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=rus",
             "-disposition:a:0", "default", "-disposition:a:1", "0", "-movflags", "+faststart", str(out)],
            "the preview")  # fmt: skip
    return out


def _key(*parts: object) -> str:
    return hashlib.sha1("\n".join(map(str, parts)).encode()).hexdigest()[:16]


# --- a take -------------------------------------------------------------------------------------------------


def separate(take: Path, work: Path) -> tuple[Path, Path]:
    """main.wav as its voices and the rest (Demucs on the GPU server), kept while main.wav stays the same."""
    main = take / "main.wav"
    voices, rest, stamp = work / "voices.wav", work / "rest.wav", work / "separated.key"
    key = _key(main.stat().st_size, main.stat().st_mtime_ns, SEPARATOR)
    if voices.is_file() and rest.is_file() and stamp.is_file() and stamp.read_text() == key:
        return voices, rest
    out = job(
        [main],
        "if [ ! -x ~/gothic-ai/dub/separate.sh ]; then echo 'no ~/gothic-ai/dub/separate.sh: run "
        "scripts/deploy-gpu.sh dub' >&2; exit 1; fi; ~/gothic-ai/dub/separate.sh {dir}/main.wav {dir}/out",
        {"out/voices.wav": voices, "out/rest.wav": rest},
        "the voices' separation",
    )
    print(out.strip())
    stamp.write_text(key)
    return voices, rest


def npc_voices(config: Config, npcs: list[str]) -> dict[str, dict[str, str]]:
    """{npc: {"voice_id", "voice"}}: the voices the game speaks them in (`gothic-ai voices`)."""
    if not npcs:
        return {}
    repo, uv = tools(config)
    argv = [str(uv), "run", "--project", str(repo / "server"), "gothic-ai", "voices", *npcs]
    if game := config.settings.game.dir:
        argv += ["--game-dir", str(game)]
    done = subprocess.run(argv, capture_output=True, text=True, env=child_env({}))
    if done.returncode != 0:
        raise DubError(f"gothic-ai voices failed:\n{done.stderr.strip()[-2000:]}")
    return json.loads(done.stdout)


def dub_take(config: Config, shot: Shot, take: Path, lang: str = "en", provider: str | None = None) -> Dub:
    if lang not in LOCALES:
        raise DubError(f"no voices for {lang}: the dub speaks {', '.join(LOCALES)}")
    events = take / "events.jsonl"
    for p in (events, take / "main.wav"):
        if not p.is_file():
            raise DubError(f"the take has no {p.name}")
    tl = build(read_events(events))
    try:  # the lines only: the hook and the captions stay burnt in, in Russian
        subs = load_subs(take, lang, list(dict.fromkeys(line.text for line in tl.lines)))
    except CutError as err:
        raise DubError(str(err)) from err
    repo, _ = tools(config)
    work = take / f"dub-{lang}"
    work.mkdir(exist_ok=True)
    voices, rest = separate(take, work)

    # the English voices: the npc's designed from its Russian voice's description, the hero's from the shot's
    npcs = sorted({line.who for line in tl.lines if not line.hero})
    described = {"hero": (VOICE_ID, shot.hero_voice)}
    for npc, v in npc_voices(config, npcs).items():
        described[npc] = (v["voice_id"], v["voice"])
    said: dict[str, list[str]] = {}
    for line in tl.lines:
        said.setdefault(line.who, []).append(subs[line.text])
    recorded = english_voices(config, described, said, shorts_dir(repo) / "voice" / lang, lang, provider)

    dub = Dub(take / f"dub.{lang}.wav", None, tl.cut_duration, None, None, None)
    placed: list[tuple[Path, float, Fit, float]] = []
    for i, (line, slot) in enumerate(zip(tl.lines, take_slots(tl, subs), strict=True)):
        trimmed = work / f"line-{i:02d}.wav"
        seconds = trim(recorded[(line.who, slot.en)], trimmed) - LEAD_S
        f = fit(seconds, slot)
        target = _russian_level(shot, line, voices, repo)
        gain = _gain(target, loudness(trimmed))
        placed.append((trimmed, slot.start, f, gain))
        _report(dub, slot, f, gain, line.name or "hero")

    ru = take / "short-ru.mp4"
    if ru.is_file():
        dub.ru_duration = _probe(ru)
        dub.ru_lufs = loudness(ru)
    dub.duration = dub.ru_duration or tl.cut_duration
    mix = work / "mix.wav"
    _mix_take(tl, take / "main.wav", rest, placed, mix)
    # The rendered short is authoritative: the shot's brand flag may have changed since it was cut.
    has_sting = dub.ru_duration is not None and dub.ru_duration - tl.cut_duration >= STING_S - 1 / 30
    if has_sting:
        body_duration = dub.duration - STING_S
        body = work / "body.wav"
        finish(mix, body, body_duration)
        _take_sting(body, ru, dub.wav, body_duration, dub.duration)
        dub.lufs = loudness(dub.wav)
    else:
        dub.lufs = finish(mix, dub.wav, dub.duration)
    if ru.is_file():
        dub.preview = preview(ru, dub.wav, take / f"dub.{lang}.mp4")
    return dub


def _take_sting(body: Path, ru: Path, out: Path, start: float, duration: float) -> None:
    """Join the mastered English body to the Russian sting, preserving its gain and existing fades."""
    first, last = round(start * 48000), round(duration * 48000)
    graph = (
        f"[0:a]afade=t=out:st={max(0, start - 0.02):.6f}:d=0.02[body];"
        f"[1:a]aresample=48000,atrim=start_sample={first}:end_sample={last},"
        "asetpts=PTS-STARTPTS[sting];[body][sting]concat=n=2:v=0:a=1[track]"
    )
    _ffmpeg(["-i", str(body), "-i", str(ru), "-filter_complex", graph, "-map", "[track]",
             "-ac", "2", "-ar", "48000", "-c:a", "pcm_s24le", str(out)], "the dub's end sting")  # fmt: skip


def english_voices(
    config: Config,
    voices: dict[str, tuple[str, str]],
    lines: dict[str, list[str]],
    cache: Path,
    lang: str,
    tts: str | None = None,
) -> dict[tuple[str, str], Path]:
    """Each speaker's lines ({who: [text]}) in an English voice of its own ({who: (voice id, description)}),
    {(who, text): recording}, by the launcher's TTS provider, then its fallback (as the game falls back), or
    `tts`. Inworld clones the voice the game speaks Russian in (a sample of it voiced by the TTS service), so
    the English keeps its timbre, and designs an en-US one from the description when that fails; VoxCPM on
    the GPU server designs one reading an English sample (as the voxcpm bridge does with its Russian one).
    The lines are kept in `cache` by provider, voice and text; the voices themselves (the Russian samples
    Inworld clones, the ones VoxCPM designs) in VOICES, so a voice is cloned or designed once, whichever the
    checkout."""
    out: dict[tuple[str, str], Path] = {}
    for who, (voice_id, description) in voices.items():
        texts = list(dict.fromkeys(lines.get(who, [])))
        if not texts:
            continue
        errors = []
        for way, make in _ways(config, voice_id, description, texts, cache, lang, tts):
            try:
                wanted = make()
            except (VoiceError, GpuError, DubError) as err:
                errors.append(f"{way}: {str(err).strip().splitlines()[-1]}")
                print(f"{who}: {errors[-1]}; the next way")
                continue
            print(f"{who}: {len(texts)} line(s) in {lang}, {way}")
            out.update({(who, t): p for t, p in wanted.items()})
            break
        else:
            raise DubError(f"no {lang} voice for {who}:\n  " + "\n  ".join(errors))
    return out


def _providers(config: Config, tts: str | None) -> list[str]:
    specs = [tts] if tts else [config.settings.tts.provider, config.settings.tts.fallback]
    return [p for p in dict.fromkeys(specs) if p and p != "none"]


def _ways(config: Config, voice_id: str, description: str, texts: list[str], cache: Path, lang: str,
          tts: str | None) -> list[tuple[str, Callable[[], dict[str, Path]]]]:  # fmt: skip
    """The ways to voice a speaker's lines, in order: each makes the missing recordings and returns them."""

    def missing(wanted: dict[str, Path]) -> dict[str, Path]:
        return {t: p for t, p in wanted.items() if not p.is_file()}

    def inworld_clone(spec: str) -> dict[str, Path]:
        russian = VOICES / f"{voice_id}-{_key(spec, description, RUSSIAN_SAMPLE)}.ru.wav"
        if not russian.is_file():  # the game's own voice: the same id and description, so the same voice
            say(config, voice_id, description, {RUSSIAN_SAMPLE: russian}, provider=spec, fallback="none")
        en = english_provider(spec, LOCALES[lang])
        sample = _key(russian.read_bytes())
        wanted = {t: cache / f"{_key(en, 'clone', sample, t)}.wav" for t in texts}
        say(config, f"{voice_id}@{lang}~ru", description, missing(wanted), provider=en, fallback="none",
            clone=russian)  # fmt: skip
        return wanted

    def inworld_design(spec: str) -> dict[str, Path]:
        en = english_provider(spec, LOCALES[lang])
        wanted = {t: cache / f"{_key(en, voice_id, description, t)}.wav" for t in texts}
        say(config, f"{voice_id}@{lang}", description, missing(wanted), provider=en, fallback="none")
        return wanted

    def voxcpm_design() -> dict[str, Path]:
        ref = VOICES / f"{voice_id}-{_key(description, SAMPLES[lang])}.ref.wav"
        wanted = {t: cache / f"{_key('voxcpm', ref.name, t)}.wav" for t in texts}
        design = {"description": description, "sample": SAMPLES[lang], "seed": _seed(description)}
        voxcpm(ref, missing(wanted), cache, design)
        return wanted

    def unknown(name: str) -> dict[str, Path]:
        raise DubError(f"{name} makes no voices in {lang} (inworld or voxcpm do)")

    ways: list[tuple[str, Callable[[], dict[str, Path]]]] = []
    for spec in _providers(config, tts):
        name = _name(spec)
        if name == "inworld":
            ways += [
                ("its Russian voice cloned by Inworld", partial(inworld_clone, _russian(spec))),
                ("a voice designed by Inworld", partial(inworld_design, spec)),
            ]
        elif name == "voxcpm":
            ways.append(("a voice designed by VoxCPM", voxcpm_design))
        else:
            ways.append((name, partial(unknown, name)))
    return ways


def _name(spec: str) -> str:
    return spec.partition(":")[0].strip()


def _russian(spec: str) -> str:
    """Inworld's spec as the game has it: its own options, the language left at Russian."""
    opts = [o for o in spec.partition(":")[2].split(",") if o and not o.startswith("language=")]
    return ":".join(["inworld", ",".join(opts)]) if opts else "inworld"


def _seed(description: str) -> int:
    """The voice's seed, as the voxcpm bridge has it: a description designs the same voice every time."""
    return int(hashlib.sha1(description.encode()).hexdigest()[:10], 16) % 2**31


def voxcpm(ref: Path, lines: dict[str, Path], work: Path, design: dict | None = None) -> None:
    """Voices each text into its file in the voice of `ref`, in one job on the GPU server (VoxCPM2 of
    gothic-voxcpm, shorts/deploy/dub/clone.py); a missing `ref` is designed there first from `design`
    ({"description", "sample", "seed"}) and kept."""
    if not lines:
        return
    if not ref.is_file() and design is None:
        raise DubError(f"no {ref}")
    work.mkdir(parents=True, exist_ok=True)
    ids = {f"{i:02d}": (text, path) for i, (text, path) in enumerate(lines.items(), start=1)}
    spec = work / "lines.json"
    spec.write_text(json.dumps([{"id": k, "text": t} for k, (t, _) in ids.items()], ensure_ascii=False))
    send, fetch, extra = [spec], {f"out/{k}.wav": p for k, (_, p) in ids.items()}, ""
    if ref.is_file():
        send.append(ref)
    else:
        how = work / "design.json"
        how.write_text(json.dumps(design, ensure_ascii=False))
        send.append(how)
        fetch[ref.name] = ref
        extra = " --design {dir}/design.json"
    log = job(
        send,
        "if [ ! -x ~/gothic-ai/dub/clone.sh ]; then echo 'no ~/gothic-ai/dub/clone.sh: run "
        "scripts/deploy-gpu.sh dub' >&2; exit 1; fi; "
        f"~/gothic-ai/dub/clone.sh {{dir}}/{ref.name} {{dir}}/lines.json {{dir}}/out{extra}",
        fetch,
        "VoxCPM2 on the GPU server",
    )
    print(log.strip())


def _russian_level(shot: Shot, line, voices: Path, repo: Path) -> float | None:
    """How loud the Russian line is in the short: an npc's in the separated voices over its line, the hero's
    recording (by the take's path, or voiced again for this text: the same file)."""
    if not line.hero:
        return loudness(voices, line.start, max(line.end - line.start, 0.5))
    for wav in (Path(line.wav) if line.wav else None, recording(shorts_dir(repo) / "voice", shot.hero_voice,
                                                                 line.text)):  # fmt: skip
        if wav is not None and wav.is_file():
            return loudness(wav)
    return None


def _mix_take(tl: Timeline, main: Path, rest: Path, placed: list, out: Path) -> None:
    spans = [(line.start - MARGIN_S, line.end + MARGIN_S) for line in tl.lines if not line.hero]
    m = mask(spans, RAMP_S)
    keep = tl.keep()
    fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,asetnsamples=n=240"
    f = [
        f"[0:a]{fmt},volume='1-({m})':eval=frame[o]",
        f"[1:a]{fmt},volume='{m}':eval=frame[r]",
        "[o][r]amix=inputs=2:normalize=0:duration=first[bg]",
        f"[bg]asplit={len(keep)}" + "".join(f"[s{i}]" for i in range(len(keep))),
    ]
    # the parts of the recording the cut keeps, as the cut joins them
    for i, (a, b) in enumerate(keep):
        fade = min(0.02, (b - a) / 4)
        f.append(
            f"[s{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={fade:.3f},afade=t=out:st={max(0.0, b - a - fade):.3f}:d={fade:.3f}[k{i}]"
        )
    f.append("".join(f"[k{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=0:a=1[cut]")
    lines, labels = _line_filters(placed, 2)
    f += lines
    f.append(f"[cut]{''.join(labels)}amix=inputs={1 + len(labels)}:normalize=0:duration=first[mix]")
    inputs = ["-i", str(main), "-i", str(rest)]
    for wav, *_ in placed:
        inputs += ["-i", str(wav)]
    (out.parent / "ffmpeg.txt").write_text(";\n".join(f))
    _ffmpeg([*inputs, "-filter_complex", ";".join(f), "-map", "[mix]", "-c:a", "pcm_f32le", str(out)],
            "the dub's mix")  # fmt: skip


def _report(dub: Dub, slot: Slot, f: Fit, gain: float, who: str) -> None:
    tempo = "" if f.tempo == 1.0 else f" x{f.tempo:.2f}"
    dub.rows.append(f"{slot.start:6.2f} {who[:14]:14} {f.seconds:4.1f}/{slot.room:4.1f} s{tempo:6} "
                    f"{gain:+5.1f} dB  {slot.en}")  # fmt: skip
    if not f.fits:
        dub.too_long.append(
            f"{slot.start:.1f} s, {who}: {f.seconds:.1f} s at x{f.tempo:.2f} for {slot.room:.1f} s: {slot.en}"
        )


# --- an explainer -------------------------------------------------------------------------------------------


def clone(
    config: Config, ws: Path, texts_en: list[str], lang: str, tts: str | None = None, reclone: bool = False
) -> list[Path]:
    """The lines in the user's own voice: by Inworld through the TTS service (the clone USER of the recording
    user.inworld.wav), else by VoxCPM2 on the GPU server (user.voxcpm.wav), in the launcher's order or by
    `tts`. The recordings are kept in VOICES for every short (user_voice); `reclone` makes them anew from this
    explainer's. A line is voiced again only when its text, the recording or the provider change."""
    work = ws / "voice" / lang
    raw = work / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    if reclone:
        for provider, want in (("inworld", REFERENCE_INWORLD_S), ("voxcpm", REFERENCE_S)):
            user_voice(ws, provider, want, anew=True)
    out = [raw / f"{i:02d}.wav" for i in range(1, len(texts_en) + 1)]
    errors = []
    for spec in _providers(config, tts):
        name = _name(spec)
        try:
            if name == "inworld":
                ref = user_voice(ws, name, REFERENCE_INWORLD_S)
                keys = [_key(name, _digest(ref), text) for text in texts_en]
                todo = [i for i, (wav, key) in enumerate(zip(out, keys, strict=True)) if not _fresh(wav, key)]
                say(config, USER_VOICE, "the author's own voice", {texts_en[i]: out[i] for i in todo},
                    provider=english_provider(spec, LOCALES[lang]), fallback="none", clone=ref)  # fmt: skip
            elif name == "voxcpm":
                ref = user_voice(ws, name, REFERENCE_S)
                keys = [_key(name, _digest(ref), text) for text in texts_en]
                todo = [i for i, (wav, key) in enumerate(zip(out, keys, strict=True)) if not _fresh(wav, key)]
                voxcpm(ref, {texts_en[i]: out[i] for i in todo}, work)
            else:
                raise DubError(f"{name} clones no voices (inworld or voxcpm do)")
        except (VoiceError, GpuError, DubError) as err:
            errors.append(f"{name}: {str(err).strip().splitlines()[-1]}")
            print(f"the user's voice: {errors[-1]}; the next provider")
            continue
        for i in todo:
            out[i].with_suffix(".key").write_text(keys[i])
        print(f"the user's voice cloned by {name}: {len(todo)} line(s) voiced, {len(out) - len(todo)} kept")
        return out
    raise DubError("no clone of the user's voice:\n  " + "\n  ".join(errors))


def user_voice(ws: Path, provider: str, want: tuple[float, float], anew: bool = False) -> Path:
    """The user's recorded voice as `provider` clones it, VOICES/user.<provider>.wav, kept for every short so
    that it is cloned once: made from this explainer's first recorded lines (voice/clean/, `want` seconds of
    them) only when it is not there, or `anew` (gothic-shorts dub --reclone)."""
    ref = VOICES / f"user.{provider}.wav"
    if ref.is_file() and not anew:
        return ref
    clean = sorted((ws / "voice/clean").glob("[0-9][0-9].wav"))
    if not clean:
        raise DubError(f"no recorded lines in {ws / 'voice/clean'} for {ref}: gothic-shorts words {ws.name}")
    takes = clean[: reference_count([_seconds(c) for c in clean], want)]
    pads = "".join(f"[{i}:a]apad=pad_dur={REFERENCE_GAP_S}[p{i}];" for i in range(len(takes)))
    joined = pads + "".join(f"[p{i}]" for i in range(len(takes)))
    joined += f"concat=n={len(takes)}:v=0:a=1,atrim=end={want[1]}"
    ref.parent.mkdir(parents=True, exist_ok=True)
    part = ref.with_name(ref.stem + ".part.wav")
    _ffmpeg([*_inputs(takes), "-filter_complex", joined, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
             str(part)], "the voice's reference")  # fmt: skip
    part.replace(ref)
    print(f"the user's voice for {provider}: {ref}, {_seconds(ref):.1f} s of {ws.name}'s lines "
          f"{takes[0].stem}-{takes[-1].stem}")  # fmt: skip
    return ref


def _digest(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16]


def dub_explainer(
    config: Config,
    ws: Path,
    lang: str = "en",
    plan_file: str = "plan.json",
    tts: str | None = None,
    reclone: bool = False,
) -> Dub:
    words_path, plan_path = ws / "words.ru.json", ws / plan_file
    for p in (words_path, plan_path):
        if not p.is_file():
            raise DubError(f"no {p}")
    said = json.loads(words_path.read_text())["lines"]
    plan = json.loads(plan_path.read_text())
    texts_en = english_script(ws, lang)
    if len(texts_en) != len(said):
        raise DubError(f"script.{lang}.txt has {len(texts_en)} lines for {len(said)} said ones")
    sounding = [b for b in plan["beats"] if b.get("src") and b.get("kind") in ("footage", "split")]
    if float(plan.get("gameVolume", 1)) > 0 and any(float(b.get("game", 1)) > 0 for b in sounding):
        raise DubError(
            f"{plan_file} plays the footage's own sound (gameVolume > 0): the dub mixes the music and the "
            "voice only"
        )
    end = round(sum(float(b["dur"]) for b in plan["beats"]) * FPS) / FPS
    ru = ws / "render/short-ru.mp4"
    dub = Dub(ws / f"render/dub.{lang}.wav", None, end, None, None, None)
    if ru.is_file():
        dub.ru_duration = _probe(ru)
        dub.ru_lufs = loudness(ru)
    dub.duration = dub.ru_duration or end

    voiced = clone(config, ws, texts_en, lang, tts, reclone)
    rows = [
        ("voice", " ".join(w["text"] for w in r["words"]), en, r["words"][0]["start"], r["words"][-1]["end"])
        for r, en in zip(said, texts_en, strict=True)
    ]
    work = ws / "voice" / lang
    # cleaned as the Russian lines were (no declick: TTS has no clicks), LEAD kept before the voice
    cleaned = [trim(v, work / v.name, clean_chain(v, declick=False)) - LEAD for v in voiced]
    # the English voice as loud as the Russian one, one gain for all of it (the lines keep their differences)
    ru_level = _level_of([ws / line["file"] for line in said], work / "ru.wav")
    en_level = _level_of([work / v.name for v in voiced], work / "en.wav")
    gain = _gain(ru_level, en_level)
    placed: list[tuple[Path, float, Fit, float]] = []
    spans = []
    for v, seconds, slot in zip(voiced, cleaned, slots(rows, dub.duration), strict=True):
        f = fit(seconds, slot)
        # the cleaning keeps LEAD before the voice, not LEAD_S: placed so that the voice starts on the first word
        placed.append((work / v.name, slot.start - (LEAD - LEAD_S), f, gain))
        spans.append((slot.start, slot.start + f.seconds))
        _report(dub, slot, f, gain, "voice")

    mix = work / "mix.wav"
    inputs: list[str] = []
    music = plan.get("music")
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(ws / music["file"])]
        bed = (f"[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
               f"atrim=end={dub.duration:.3f},asetnsamples=n=240,"
               f"volume='{music_volume(music, end, spans)}':eval=frame[bed]")  # fmt: skip
    else:
        inputs += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={dub.duration:.3f}"]
        bed = "[0:a]anull[bed]"
    for wav, *_ in placed:
        inputs += ["-i", str(wav)]
    graph, labels = _line_filters(placed, 1)
    f = [bed, *graph, f"[bed]{''.join(labels)}amix=inputs={1 + len(labels)}:normalize=0:duration=first[mix]"]
    (work / "ffmpeg.txt").write_text(";\n".join(f))
    _ffmpeg([*inputs, "-filter_complex", ";".join(f), "-map", "[mix]", "-c:a", "pcm_f32le", str(mix)],
            "the dub's mix")  # fmt: skip
    dub.lufs = finish(mix, dub.wav, dub.duration)
    if ru.is_file():
        dub.preview = preview(ru, dub.wav, ws / f"render/dub.{lang}.mp4")
    return dub


def _level_of(wavs: list[Path], joined: Path) -> float | None:
    """The loudness of the lines said one after another."""
    one = "".join(f"[{i}:a]" for i in range(len(wavs))) + f"concat=n={len(wavs)}:v=0:a=1"
    _ffmpeg([*_inputs(wavs), "-filter_complex", one, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le",
             str(joined)], "the voice's loudness")  # fmt: skip
    return loudness(joined)


def _inputs(files: list[Path]) -> list[str]:
    return [arg for f in files for arg in ("-i", str(f))]


def _fresh(wav: Path, key: str) -> bool:
    stamp = wav.with_suffix(".key")
    return wav.is_file() and stamp.is_file() and stamp.read_text() == key
