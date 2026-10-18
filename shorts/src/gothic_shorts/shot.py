"""A shot file: one short, as a scene for the test bench plus what the cut needs.

    title = "Соврал Ксардасу"            # the short's name
    hook = "Я соврал NPC в Готике"       # big text over the first seconds of the cut
    npc = "NONE_100_XARDAS"              # {npc} in the script
    music = 0.0                          # game music stays off during filming
    sound = 1.0                          # the game's effects, 0..1
    script = '''
    overlay off
    cam shoulder {npc}
    rec main
    hero Ксардас, меня прислал Ли.       # the hero says it: a TTS recording through the talk key
    waitreply
    stop
    '''

The script is the bench's own language (scripts/bench/README.md) plus `hero <text>`, which becomes
`ptt <wav> <text>` once the line is voiced (voice.py).
Game music is not recorded: the cut adds one music bed for the whole short. `film` always keeps the
game's music off, including for older shot files that set `music` above zero.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HERO_VOICE = (
    "A man of about thirty with a calm, confident, slightly husky mid-low voice; laconic, a little "
    "ironic, speaks plainly like a seasoned adventurer"
)


class ShotError(ValueError):
    pass


@dataclass(frozen=True)
class Shot:
    name: str
    title: str
    script: str
    hook: str = ""
    brand: bool = True
    npc: str = ""
    save: Path | None = None
    music: float = 0.0
    sound: float = 1.0
    hero_voice: str = DEFAULT_HERO_VOICE
    # the window in points: 972x1728 pixels on a Retina screen, the cut scales it to 1080x1920 (the full
    # 540x960 records at ~94% of real time on an M1 Pro, this at ~99%)
    size: str = "486x864"
    timeout: int = 300
    description: str = ""
    # more arguments for the AI server (`gothic-ai serve`), e.g. ["--wrath"] for the necromancer's answer
    server_args: tuple[str, ...] = ()
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def hero_lines(self) -> list[str]:
        return [text for kind, text in self.commands() if kind == "hero"]

    def commands(self) -> list[tuple[str, str]]:
        """(command, rest of the line) per script line, comments and empty lines dropped."""
        out = []
        for raw in self.script.format(npc=self.npc).splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                cmd, _, rest = line.partition(" ")
                out.append((cmd, rest.strip()))
        return out

    def scenario(self, voiced: dict[str, Path]) -> str:
        """The bench scenario; `voiced` maps each hero line to its recording."""
        lines = []
        for cmd, rest in self.commands():
            if cmd == "hero":
                if rest not in voiced:
                    raise ShotError(f"{self.name}: the line is not voiced yet: {rest}")
                lines.append(f"ptt {voiced[rest].resolve()} {rest}")
            else:
                lines.append(f"{cmd} {rest}".strip())
        return "\n".join(lines) + "\n"


KNOWN = {
    *("title", "hook", "npc", "save", "music", "sound", "hero_voice", "size", "timeout", "description"),
    *("script", "server_args", "brand"),
}


def load(path: Path) -> Shot:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise ShotError(f"{path}: {err}") from err
    if "script" not in data:
        raise ShotError(f"{path}: no script")
    save = data.get("save")
    shot = Shot(
        name=path.stem,
        title=str(data.get("title", path.stem)),
        script=str(data["script"]),
        hook=str(data.get("hook", "")),
        brand=bool(data.get("brand", True)),
        npc=str(data.get("npc", "")),
        save=(path.parent / save).resolve() if save else None,
        music=float(data.get("music", Shot.music)),
        sound=float(data.get("sound", 1.0)),
        hero_voice=str(data.get("hero_voice", DEFAULT_HERO_VOICE)),
        size=str(data.get("size", Shot.size)),
        timeout=int(data.get("timeout", 300)),
        description=str(data.get("description", "")),
        server_args=tuple(str(a) for a in data.get("server_args", [])),
        extra={k: v for k, v in data.items() if k not in KNOWN},
    )
    try:
        shot.commands()
    except (KeyError, IndexError, ValueError) as err:
        raise ShotError(f"{path}: the script has a {{...}} that is not npc: {err}") from err
    if not any(cmd == "rec" for cmd, _ in shot.commands()):
        raise ShotError(f"{path}: the script records nothing (no `rec`)")
    return shot
