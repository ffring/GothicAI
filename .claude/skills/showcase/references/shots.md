# Shots: scenes, cameras, what goes wrong

## The set

The bench save (`save_slot_1.sav` of the main checkout) starts in Xardas' tower; Xardas is
`NONE_100_XARDAS`. The whole island is loaded, so other places are a teleport away: find waypoints with
`findwp <part of a name>` (printed to the take's `bench.log`), go with `goto <waypoint>`. An npc can be
added next to the hero with the Marvin console: `insert VLK_408_JORA`. There is no save or load from the
bench, so "remembers after loading" cannot be filmed yet.

Other useful lines: `set time 12 0` (daylight), `cheat god` (the hero survives a fight),
`tap weapon` (draws or puts away the weapon), `walkmode walk` + `approach <npc>` + `waitarrive` (walks up
instead of teleporting).

## Scenes per feature

Each idea names what the short proves. Film it, look at the takes, keep the honest best.

| Feature | Scene | The hook |
|---|---|---|
| deception | the hero claims someone sent him for an item (`shots/xardas-lie.toml`) | "I tried to fool Xardas" |
| witnesses | `insert` a second npc beside the first, lie to the first, then ask the second what you just said | "The witness heard everything" |
| reacting to deeds | mid-conversation `tap weapon`, wait, then speak again; the npc answers the drawn sword | "Drew a sword mid-sentence" |
| actions | ask the npc to follow you or to hand something over, then walk off (`walkmode walk`, `hold forward 3000`) and keep the npc in `cam two` | "He actually came with me" |
| interrupting | a long question, `wait 2500` while the npc starts answering, then a short `hero Хватит.` | "You can interrupt them" |
| gaze | two npc's and the hero, `cam face <npc>` on the npc you do not address: its eyes follow the talk | "They look at whoever speaks" |
| feet (no AI) | not gothic-shorts: `scripts/bench.sh ik-ab`, then `scripts/bench_report.py compare` off/on, stacked for 9:16 | "Before and after on stairs" |

Lines that work: the npc's name first, one request per line, the way a player would say it aloud.
Lines that fail: long sentences (recognition drops words), names the model does not know, questions the
npc can only answer with lore dumps (the answer runs past the short).

## Camera recipes

| Moment | Command | Why |
|---|---|---|
| the hero speaks | `cam shoulder {npc} 130 15 1` | the listener's face over the hero's shoulder |
| the npc answers | `cam face {npc} 150 0 25` | the face from where it looks, turned towards the hero |
| a reaction to a deed, walking together | `cam two {npc}` | both in frame |
| a hard cut between shots | `cam cut` before the next `cam` | otherwise the camera glides (`cam smooth 0.12`) |

`cam face` takes an npc's name here; with a number first it is the bench's close-up of the watched one's
mouth (lip sync), not a shot of a short.

Change the camera after a hero line and before `waitreply`, so the answer opens on the npc's face.
Interiors are tight: if a frame of the sheet shows a wall, pull `near <npc> <dist>` closer, change the
angle of `cam face`, or `view clean 80` (clips what stands between the camera and the subject).

## What goes wrong on set

| Symptom | Cause | Fix |
|---|---|---|
| `timed out` in the summary, no answer | the language model was overloaded or failed (`llm_error` in `journal.jsonl`) | film another take |
| the npc walks off, answers on the move | it was busy with its routine | `ai attach {npc}` before `rec` |
| answers overlap or start too early | the game ran slower than real time (`rec stop ... speed=` in `bench.log` under ~95%) | free the machine (other builds, VMs), keep `size` at 486x864 |
| the npc answers something you did not say | speech recognition misheard (`heard:` in the summary) | simpler words, the name first; the take shows it, so do not hide it |
| the hero's voice changed between shorts | the TTS fell back to another provider | check `.shorts/voice/` lines of the shot, voice again (`gothic-shorts voice`) after deleting them |
| a black or frozen video | the game did not start the scenario | `logs/game.log` and `bench.log` of the take |
