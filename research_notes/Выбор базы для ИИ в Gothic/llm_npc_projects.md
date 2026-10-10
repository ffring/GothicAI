# LLM/AI-driven NPCs, dialogue and quest generation in Gothic and comparable RPGs: existing projects and the engine requirements they imply

Research date: 2026-10-10. Scope: 2023–2026. Notation: **[verified]** means checked in a primary source (repo source code, README, official docs) during this research. **[snippet]** means it comes only from a search-engine summary because the page itself was blocked (worldofplayers.de, worldofgothic.de and Nexus returned Anubis or 403). **[inference]** means my own reasoning.

---

## Q1. Gothic-specific: do any mods or plugins (2023–2026) connect Gothic 1/2 to ChatGPT, LLMs or local models? What base did they use, and how did they handle voice, STT, UI and actions?

### Takeaway
I found no verifiable, working, open-source LLM-NPC conversation mod for Gothic 1/2 on Union, Ikarus/LeGo, OpenGothic or G2O. The one repo that claims to be such a mod ("Gothic 2 AI NPC Edition") has every sign of a malware/spam landing page and should be treated as unsafe. The real Gothic-adjacent work is narrow:
- an LLM quest-generator microservice for a Gothic 2 multiplayer server;
- offline AI voice generation (GameTTS) for classic mods;
- legal pushback from an original German voice actor against AI voice cloning (Sept 2025).

A Gothic LLM-NPC project would therefore be greenfield. All architecture lessons have to come from other games (Q2).

### Cited Findings
**Search coverage and the fake mod**
- A GitHub repository search for "gothic llm" (Oct 2026) returned 6 repos. Only one is a real Gothic-game project: `LucyJohnson-lua4life/LLM-Quest-Generator`. The others are unrelated "gothic"-aesthetic projects. — [GitHub search](https://github.com/search?q=gothic+llm&type=repositories)
- GitHub API searches for "gothic ai npc", "gothic chatgpt", "gothic ollama", "gothic union openai", "opengothic ai", "gothic npc llm" and "khorinis ai" found no Gothic LLM-NPC code. The only hit was the suspicious MelodyCheetah repo (created 2026-08-29). "gothic chatgpt" returned only Gothic-Workshop tools *written with* ChatGPT (zenFix batch tool, zDialogueToExcel), not AI-in-game mods. — [GitHub: Gothic-Workshop/zDialogueToExcel](https://github.com/Gothic-Workshop/zDialogueToExcel)
- **"Gothic 2 AI NPC Edition" (MelodyCheetah/gothic-2-ai-npc-edition)**:
  - It claims "AI dialogue for 120+ named NPCs", Ollama/OpenAI support, an AI chat overlay hotkey and AI voice.
  - The repo contains only a README, with no source code and 5 commits. Its "Download for Windows/macOS" buttons point to an external `*.github.io` domain rather than GitHub releases.
  - It claims "25K+ downloads" and to be one of the most downloaded mods on Nexus/ModDB, but the repo has 1 star and 0 forks.
  - Search engines repeat its marketing text uncritically.
  - Treat it as a likely malware/SEO lure, not as evidence that a Gothic LLM mod exists. — [GitHub repo](https://github.com/MelodyCheetah/gothic-2-ai-npc-edition)

**The real Gothic-adjacent projects**
- **LLM-Quest-Generator** (LucyJohnson-lua4life, last updated July 2025) is a "LLM Quest Generator Micro-Service for the Gothic 2 Multiplayer" (README mentions the "GMA server").
  - Language and packaging: Common Lisp, Docker, MIT licence.
  - Design: quest *structure* is generated procedurally, and the LLM only "comments on the structure" (writes flavour text). The README's rationale: LLMs "have no sophisticated representation of understanding".
  - Quest types: "Kill X", "Kill X until item Y drops", "Interact with X" (shrines/objects).
  - Delivery: quests are handed out by ambient "Adventurer" NPCs; three tasks make one daily quest.
  - The game/server protocol is undocumented ("more detailed documentation coming soon"). — [GitHub](https://github.com/LucyJohnson-lua4life/LLM-Quest-Generator) **[verified README]**
- The same author's `gmp_ai` is a rule-based (non-LLM) Lua AI framework for GMP (Gothic Multiplayer 1.5.3). It runs a 50 ms server-side loop with AI state, action and behaviour modules and waypoint navigation. This shows that multiplayer servers give script-level control of NPCs from outside the original scripts. — [GitHub](https://github.com/LucyJohnson-lua4life/gmp_ai) **[verified README]**
- G2O (Gothic 2 Online) exposes engine functionality to Squirrel scripts. This is the only Gothic platform with an official scripting API that runs outside Daedalus. — [G2O docs](https://gothicmultiplayerteam.gitlab.io/docs/0.3.3/) **[snippet]**
- The only OpenGothic fork that surfaced (Lu2cz/OpenGothic) is a macOS/Archolos compatibility fork. It has no AI/LLM, TTS or plugin features. — [GitHub](https://github.com/Lu2cz/OpenGothic) **[verified]**

**Community discussion**
- A German worldofplayers.de thread, "Gothic Mod mit KI gesteuerten NPCs? Und was würdet ihr tun?", discusses feeding per-NPC lore to an AI. According to the search snippet, it notes that LLMs "don't really understand dynamic events" and that truly intelligent NPCs have not been achieved this way. The page itself was behind Anubis bot protection, so I could not read it. — [WoP thread](https://forum.worldofplayers.de/forum/threads/1619315-Gothic-Mod-mit-KI-gesteuerten-NPCs-Und-was-w%C3%BCrdet-ihr-tun) **[snippet]**
- Russian-language searches (worldofplayers.ru, rpgrussia) found no LLM-NPC Gothic project. They did surface a Union co-op plugin thread ("НБ — Плагин для игры в кооперативе"). That suggests Union plugins already do networking, but I only saw the title. — [worldofplayers.ru thread](https://worldofplayers.ru/threads/43172/) **[title only]**

**Voice: AI voices for Gothic**
- **GameTTS** (lexkoro) is a VITS-based TTS GUI with a ~155 MB model, released for non-commercial use under CC BY-NC 4.0. The repo was created in 2021 and last pushed in October 2023. — [GitHub](https://github.com/lexkoro/GameTTS) **[verified README]**
- The Nexus mod "Gothic Die Welt der Verurteilten TTS" uses GameTTS. According to the snippet:
  - all characters are voiced "with their original G1 & G2 AI voices";
  - the files are AI-generated, with usage rights limited to private use inside that mod;
  - the author thanks Bodo Henkel for authorising use of his AI voice;
  - a "GameTTS Dialog Exporter" extracts dialogue lines from Gothic project files for batch (offline) TTS. — [Nexus Gothic mod 28](https://www.nexusmods.com/gothic/mods/28) **[snippet; page 403]**
- On 2025-09-28, Bodo Henkel (German voice of Xardas) said he would take legal action against unauthorised AI imitation of his voice. He offered licensing talks with modders; existing content may stay if agreed, but no new AI-voice videos are allowed. His stance followed a German court win by voice actor Manfred Lehmann against AI voice imitation. — [PCGH, 2025-09-28](https://www.pcgameshardware.de/Gothic-Remake-Spiel-73829/News/Xardas-Sprecher-wehrt-sich-gegen-KI-Imitation-seiner-Stimme-1483074/); [ronaldkah.de](https://ronaldkah.de/ki-generierte-stimmen-sprecher-bodo-henkel-droht-mit-klagen/)
- The Gothic 1 Remake developers brought back original voice actors (Christian Wewerka as the hero, Bodo Henkel as Xardas). They are reported to be against AI voice acting. — [PCGames](https://www.pcgames.de/Gothic-Remake-Spiel-73829/News/synchronsprecher-deutsch-vertonung-bodo-henkel-christian-wewerka-1485920/) **[snippet]**

**Engine facts that matter for a Gothic LLM mod** (Daedalus API, Gothic Modding Community docs)
- **Dialogue lines are compile-time assets.** `AI_Output(slf, oth, "outputname")` plays "the output and the associated .wav file". The subtitle text is "defined by the comment after the function call", so it is baked in when scripts compile. Vanilla scripts therefore cannot speak an arbitrary runtime string with a runtime WAV. — [GMC AI externals](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/ai/) **[verified]**
- **Daily routines are script functions.** `TA_Min(npc, start_h, start_m, stop_h, stop_m, state, waypoint)` builds a routine, and `Npc_ExchangeRoutine(npc, "routine")` swaps it. `AI_StartState(npc, ZS_func, behaviour, wpname)` pushes an AI state. — [GMC TA](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/ta/); [GMC NPC](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/npc/); [GMC AI](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/ai/) **[verified]**
- **The quest log is string-keyed.** `Log_CreateTopic(topicName, section)`, `Log_AddEntry(topicName, entry)` and `Log_SetTopicStatus(...)` take runtime strings. A runtime-generated quest can be shown in the journal without pre-compiled quest records (unlike Skyrim). — [GMC Log](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/log/) **[verified]**
- **Union SDK** lets a C++ plugin register custom Daedalus externals through the global `zCParser` and install hooks. **zParserExtender** (Union) supports Daedalus injection/merge at runtime. **zMultilogue** (Union) gives multi-NPC dialogues "without breaking the AI queue". — [GMC Union SDK externals](https://gothic-modding-community.github.io/gmc/zengin/union/sdk/externals/); [GMC zParserExtender](https://gothic-modding-community.github.io/gmc/zengin/scripts/extenders/zparserextender/daedalus_injection/other/); [GMC zMultilogue](https://gothic-modding-community.github.io/gmc/zengin/union/plugins/zmultilogue/) **[verified]**

### Inferences
- No Gothic project has solved the hard parts: runtime subtitles plus voice through `AI_Output`, a dialogue UI, action injection, and save persistence. Any reference implementation will come from Skyrim, Kenshi or Morrowind.
- Because `AI_Output` binds text and WAV at compile time, a Gothic LLM mod needs native code either way:
  - with Union: hook or replace the output-unit/subtitle path and the sound lookup;
  - with OpenGothic: patch the C++ dialogue/sound code.
  This is the Gothic counterpart of Mantella's "overwrite the voice file of placeholder topics" trick (Q2).
- Voice cloning of the original German (and likely Russian/Polish) voice actors carries real legal risk in 2025–2026. Generic TTS voices, licensed clones, or clones with explicit actor consent (as in the GameTTS mod with Henkel's authorisation) are the safe paths.
- G2O and GMP are a credible third option for *server-authoritative* AI (quests and NPC control from a server-side script). They are multiplayer frameworks with their own constraints and are not covered further here.

### Gaps
- YouTube devlogs: search did not surface any Gothic + ChatGPT/LLM demo videos. Such videos may exist but were not discoverable via web search, and YouTube itself was not searched directly.
- I could not read the worldofplayers.de thread or the worldofgothic.de news item (news_3442, probably about GameTTS) because of bot protection.
- ModDB and r/worldofgothic were not reachable in a meaningful way. No LLM-mod results appeared in search.
- Which multiplayer the LLM-Quest-Generator targets (GMP vs G2O; "GMA server") and its wire protocol are unknown.

---

## Q2. LLM-NPC mods in other games: architecture, state export, action execution, latency, memory, and conflicts with NPC routines

### Takeaway
The successful mods fall into three architectures:
1. **In-process native plugin + external AI server** (Mantella, CHIM/Herika, Sentient Sands, Dwemer Dynamics' Stobe/Dialectic/SYNTH).
2. **Everything in-process** in a native plugin (SkyrimNet, 2025).
3. **Patched engine fork with a narrow native bridge**, where the open-source engine has no plugin or network API (LORKHAN for OpenMW, 2026). This is the closest analog to OpenGothic.

All of them needed the same capabilities from the game side:
- read actor and world state;
- asynchronous HTTP that never blocks the game thread;
- play runtime-generated audio through the engine's own voice path so lip/face animation works;
- typed, allowlisted actions executed through engine AI packages, with results reported back;
- per-save persistence;
- a custom in-game UI.

The recurring pain points are latency, NPC AI packages and routines breaking or fighting AI-issued tasks, and stale replies after save/load.

### Cited Findings

#### Mantella (Skyrim SE/VR, Fallout 4; 2023 → v0.14, 2026-04-21)
- **Pipeline.** STT (Whisper/Moonshine) → LLM → TTS (Piper/xVASynth/XTTS); the server is in Python. Licence: AGPL-3.0. — [GitHub](https://github.com/art-from-the-machine/Mantella)
- **Releases** (GitHub API): v0.13 (2025-03-29), v0.13.1 (2025-06-12), v0.14_preview_1 (2025-12-12), v0.14 (2026-04-21). — [GitHub releases](https://github.com/art-from-the-machine/Mantella/releases) **[verified]**
- **Transport.** The Python side is a FastAPI/uvicorn server with one `POST /mantella` route.
  - Request types: `initialize`, `start_conversation`, `continue_conversation`, `player_input`, `end_conversation`.
  - Reply types: `npc_talk`, `npc_action`, `player_talk`, `end_conversation`, `interrupted`. — [http_server.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/http/http_server.py); [mantella_route.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/http/routes/mantella_route.py) **[verified source]**
- **Exported game state** (JSON keys):
  - per actor: base/ref IDs, name, gender, race, is_player, relationship rank, voice type, in-combat, is-enemy, equipment, custom values;
  - context: location, weather, time, game days, nearby actors, in-game events (a text log of things that happened), config flags, vision hints. — [communication_constants.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/http/communication_constants.py) **[verified source]**
- **Game-side HTTP.** Papyrus has no HTTP, so Mantella wrote **SKSE_HTTP**, a C++ SKSE plugin (libcurl/cpr/nlohmann statically linked).
  - Papyrus builds typed dictionaries via handles and calls `sendLocalhostHttpRequest(handle, port, route)`.
  - The reply arrives asynchronously as the ModEvent `SKSE_HTTP_OnHttpReplyReceived`.
  - It also exposes `StopVoiceLine(actor, fadeMs)` for interrupting speech. — [SKSE_HTTP README](https://github.com/art-from-the-machine/SKSE_HTTP) **[verified]**
- **Legacy fallback: file IPC.** A compatibility thread monitors `_mantella_communication.txt` for JSON and forwards it to the HTTP server, so file-based IPC works when the game script can only write files. — [file_communication_compatibility.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/http/file_communication_compatibility.py) **[verified source]**
- **Voice playback and lip sync.** The ESP defines two placeholder dialogue Topics, `MantellaDialogueLine1/2`.
  - The server writes the generated `.wav` and a `.lip` file into the Skyrim voice folder under the filename those topics expect.
  - Papyrus calls `actor.Say(topic)`, then `Utility.Wait(lineDuration)`.
  - Topics are alternated because "Playing the same topic twice in a row can cause the engine to replay the previous (cached) voiceline instead of the newly written audio file". — [MantellaConversation.psc](https://github.com/art-from-the-machine/Mantella-Spell/blob/main/Scripts/Source/MantellaConversation.psc); [games/skyrim.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/games/skyrim.py) **[verified source]**
- **Participants.** Conversation participants are tagged by adding them to a `MantellaConversationParticipantsFaction`. The faction rank disambiguates non-unique names. — [MantellaConversation.psc](https://github.com/art-from-the-machine/Mantella-Spell/blob/main/Scripts/Source/MantellaConversation.psc) **[verified]**
- **Vanilla dialogue capture.** Vanilla dialogue is also logged into Mantella's memory, with blacklists for menu-like lines. — [MantellaDialogue.ini](https://github.com/art-from-the-machine/Mantella-Spell/blob/main/SKSE/Plugins/MantellaDialogue.ini) **[verified]**
- **Action schema.** Actions are JSON files.
  - Example fields: `identifier`, `name`, `description`, JSON-schema `parameters`, `required`, `prompt`, `allowed_games`, `requires_response`, `is-interrupting`, `one-on-one`, `multi-npc`, `radiant`.
  - **Basic mode:** the LLM prefixes its reply with `Key:` (e.g. "Follow: Lead the way").
  - **Advanced mode:** tool calling, optionally through a *separate, cheaper tool-calling model* (default example `google/gemma-4-26b-a4b-it:free`).
  - Function actions add `llm_feedback` (an "about to move…" rationalisation) and `veto_warning` (the NPC may refuse in character).
  - Actions include follow/unfollow, attack, brawl, barter, inventory, loot, move_to, travel_to, teleport, lead_to, wait, flee, cast_spell, report/absolve crime, emote. Many are disabled by default. — [action_definitions.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/config/definitions/action_definitions.py); [follow.json](https://github.com/art-from-the-machine/Mantella/blob/main/data/actions/follow.json); [move_npc.json](https://github.com/art-from-the-machine/Mantella/blob/main/data/actions/functions/move_npc.json) **[verified source]**
- **Routine conflict, stated explicitly.** The Follow action carries `"note": "EXPERIMENTAL: May cause NPC AI packages to break."` — [follow.json](https://github.com/art-from-the-machine/Mantella/blob/main/data/actions/follow.json) **[verified]**
- **Memory.**
  - At conversation end, an LLM writes a summary per NPC into a text file, keyed by NPC name, ref ID and `world_id` (the playthrough).
  - NPCs get summaries only of messages they actually heard.
  - When the summaries exceed 30% of the model's token limit (`summary_limit_pct = 0.3`), they are re-summarised. — [summaries.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/remember/summaries.py) **[verified source]**
- **Streaming.** The source tree has `sentence_accumulator.py`, `sentence_queue.py` and `sentence_end_parser.py`, i.e. output is split into sentences and queued for TTS while generation continues. — [Mantella src tree](https://github.com/art-from-the-machine/Mantella/tree/main/src/llm) **[verified file names; behaviour inferred]**

#### CHIM / Herika (Skyrim; Herika 2023, CHIM released 2024-09-02; active Oct 2026)
- **History.** Herika started in 2023 as "The ChatGPT Companion", a single AI follower. — [Nexus Herika](https://www.nexusmods.com/skyrimspecialedition/mods/89931)
- **Architecture.**
  - Client: an SKSE plugin `AIAgent.dll` (C++, CommonLibSSE-NG), plus Papyrus, an ESP and an in-game web UI via **PrismaUI**.
  - Server: **HerikaServer**, PHP on Apache with **PostgreSQL**, running in a WSL2 Debian VM ("DwemerDistro").
  - The client discovers the server via an INI, then a launcher at `127.0.0.1:7135`, then a fallback `127.0.0.1:8081/HerikaServer/comm.php`. — [CHIM agent guide](https://github.com/Dwemer-Dynamics/CHIM/blob/main/AIAgent/docs/CHIM/agent-guide.md); [CHIM README](https://github.com/Dwemer-Dynamics/CHIM); [dwemerdynamics.com/chim](https://dwemerdynamics.com/chim/) **[verified; WSL/PSQL detail partly snippet]**
- **Turn flow.**
  1. Native code and Papyrus observe the world and take input.
  2. The client sends events and context over HTTP; voice is uploaded to the STT route.
  3. The server records events, selects profiles and context, calls the LLM, and produces dialogue, actions and TTS audio.
  4. The client resolves the actor, plays speech and applies permitted actions.
  The guide warns that load/halt/conversation changes "can invalidate work. A successful HTTP response is not proof that the correct actor spoke or completed an action." — [CHIM agent guide](https://github.com/Dwemer-Dynamics/CHIM/blob/main/AIAgent/docs/CHIM/agent-guide.md) **[verified]**
- **Features.** Function-calling actions ("trade with me, move here, attack that monster"), long-term memory, and "Any NPC can be an AI, including group conversations". — [HerikaServer (legacy)](https://github.com/abeiro/HerikaServer) **[verified README]**
- **The same team ships a reusable server-plus-adapter family.** As of Oct 2026, Dwemer Dynamics maintains:
  - **Stobe** (Kenshi), **Dialectic** (Fallout NV/TTW), **SYNTH** (Fallout 4), **LORKHAN** (Morrowind/OpenMW);
  - an embeddings service (`minime-t5`) and an embedding visualiser (CHIM-MindMap);
  - STT/TTS services (faster-whisper, Parakeet, Piper, XTTS, Chatterbox, Higgs…);
  - a `CHIM-MCP` server. — [Dwemer-Dynamics org](https://github.com/orgs/Dwemer-Dynamics/repositories) **[verified via API]**
- **Porting template (ExampleMod / ExampleServer, Oct 2026).** A C++17 reference client in which a `GameAdapter` interface is "the only interface that touches the game". It keeps the game thread and worker threads apart and implements stale-reply checks, cancellation, a client-side action allowlist, a `session_id` per save game, server checkpoints, and a 40 s timeout per HTTP phase with no retry of turns. These are described as "the patterns a real engine port must keep". — [ExampleMod README](https://github.com/Dwemer-Dynamics/ExampleMod) **[verified]**

#### SkyrimNet (Skyrim; repo created 2025-05-05; 340★; active Oct 2026)
- **Single in-process SKSE DLL**: "No WSL. No Python launcher." The config UI is a web dashboard at `localhost:8080`. — [README](https://github.com/MinLL/SkyrimNet-GamePlugin) **[verified]**
- **Rationale and latency techniques:**
  - game state is "read straight from memory (no syncing, no serialization round-trips)";
  - "TTS starts speaking the first sentence while the model is still writing the second";
  - action eligibility is "pre-warmed while your voice is being transcribed";
  - "The game thread is never blocked on a network request, a database query, or a TTS render". — [README](https://github.com/MinLL/SkyrimNet-GamePlugin) **[verified]**
- **Memory:**
  - per-NPC first-person memories with vector recall (local GPU embeddings), importance and decay, and diaries;
  - **World Knowledge** facts scoped by conditions such as `get_quest_stage("MQ104") >= 13` or location/faction, either always injected or retrieved semantically;
  - shareable "Knowledge Packs" (`.sknpack`) for shipping lore with quest mods. — [README](https://github.com/MinLL/SkyrimNet-GamePlugin) **[verified]**
- **Actions:**
  - defined in YAML (calling Papyrus functions on quest scripts), Papyrus-registered at runtime, or native C++ from other plugins;
  - grouped into categories, so the model first picks an intent and then a cheaper drill-down prompt picks the specific action;
  - eligibility is expressed via "decorators" (e.g. `is_in_faction`). — [README](https://github.com/MinLL/SkyrimNet-GamePlugin); [WORKFLOW_ACTIONS.md](https://github.com/MinLL/SkyrimNet-GamePlugin/blob/main/docs/modding/WORKFLOW_ACTIONS.md) **[verified]**
- **Modder API and tooling:**
  - other mods can "Mark actors as busy with a stated reason, so SkyrimNet defers to your mod's logic". This is an explicit mechanism for AI-vs-scripted-behaviour conflicts;
  - prompts are Inja templates over 100+ live-state "decorators";
  - YAML triggers ("when X happens, do Y");
  - an MCP server (port 8889, 44+ tools) that can query game data and run console commands on the game thread;
  - vision (screenshots → VLM descriptions);
  - 8 TTS engines; local Whisper STT;
  - PrismaUI chat overlay. — [README](https://github.com/MinLL/SkyrimNet-GamePlugin) **[verified]**

#### IntelEngine (SkyrimNet extension; created 2026-02-03): AI-scheduled NPC tasks and an LLM "Dungeon Master"
- **Scheduled real-world tasks.** NPCs accept meetings, fetches and deliveries at future times ("at sunset"). They compute travel time, depart early, and path across cells using native AI pathfinding. A C++ NPC index locates actors "including unloaded cells". — [README](https://github.com/galanx/IntelEngine-GamePlugin) **[verified]**
- **Routine handling.**
  - AI-issued tasks are custom AI packages (travel/sandbox/escort at three speeds) in a "slot-based task state machine": 5 active and 10 scheduled task slots.
  - Stuck/departure detection uses native position polling. Recovery is a "soft recovery" (re-evaluate AI packages) plus a "safety timeout" that force-completes tasks "to prevent permanent soft-locks in unloaded cells".
  - After a fetch, the NPC lingers; "Walk away, and they return to their routine."
  - Tasks persist via "dual persistence (runtime arrays + StorageUtil)", and "AI packages are reconstructed on game load". — [README](https://github.com/galanx/IntelEngine-GamePlugin) **[verified]**
- **Runtime quests.** The LLM Dungeon Master offers *template-based* runtime quests:
  - **Combat**: clear a location; guide or map marker.
  - **Rescue**: "victim is pre-placed deep inside the dungeon".
  - **Find Item**: "A quest chest is placed at the boss room with a guardian enemy".
  - Plus gossip chains (up to 10 NPCs) and faction wars with spawned soldiers (5 waves × 22 per side). — [README](https://github.com/galanx/IntelEngine-GamePlugin) **[verified]**
- **Multi-mod conflict.** When several mods register overlapping actions (travel/cancel), the AI "may pick IntelEngine's travel but the other mod's cancel, which won't work". Duplicates must be disabled. — [README](https://github.com/galanx/IntelEngine-GamePlugin) **[verified]**

#### LORKHAN (Morrowind on OpenMW; Dwemer Dynamics; created 2026-09-22; v0.1.0 prototype). This is the closest analog to an OpenGothic route.
- **It ships its own engine build.** "LORKHAN is a side-by-side OpenMW build plus a Lua mod… The Lua-only package still requires the matching LORKHAN engine and native bridge". OpenMW is pinned to 0.51.0, commit `f4bec414…` (Lua API 129). — [LORKHAN README](https://github.com/Dwemer-Dynamics/LORKHAN); [agent-guide.md](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/lorkhan/files/docs/LORKHAN/agent-guide.md) **[verified]**
- **Why a native patch was required.** "OpenMW Lua 0.51 runs each script in an OS-isolated sandbox. Its exposed packages include world, types, UI, input, sound, storage and a read-only VFS, but not HTTP or sockets." — [ENGINE-INTEGRATION-PLAN.md](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md) **[verified]**
- **Patch surface.**
  - New C++ in `apps/openmw/mwlorkhan/` and `components/lorkhan/`, plus a Lua binding package `openmw.lorkhan`.
  - Upstream edits are limited to CMake, Lua package registration, lifecycle and VFS/media seams, and are tracked in a `patch-manifest.json` with upstream blob SHAs.
  - The Lua API is typed: `submitTurn`, `submitActionResult`, `submitStt`, `pollResults(maxItems)`, `cancel`, `cancelGeneration`, `halt`, `prepareMedia`, `playSpeech(mediaId, actor, subtitle)`, `stopSpeech`.
  - It deliberately has no generic `request(url)`.
  - Transport is Boost.Asio/Beast HTTP/1.1 to loopback only, with size caps, deadlines and no retry of turns. — [ENGINE-INTEGRATION-PLAN.md](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md) **[verified]**
- **Threading.** "All public Lua functions validate/copy synchronously and enqueue; none waits on I/O. Worker uses no `sol`, `MWWorld::Ptr`, VFS object, UI object or OpenMW service. Main thread maps validated results back into Lua events". — [ENGINE-INTEGRATION-PLAN.md](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md) **[verified]**
- **Runtime audio.**
  - Stock `Sound.say` resolves files through the read-only VFS, so files downloaded after startup are "not safely assumed visible".
  - The bridge therefore streams audio into a private cache (SHA-256 verified, codec allowlist, 512 MiB LRU quota) and plays it through "the existing voice playback/loudness path so lip animation and spatial voice work". — [ENGINE-INTEGRATION-PLAN.md](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md) **[verified]**
- **Additional engine patches** needed for parity with CHIM:
  - the dialogue window emits an event with the clicked topic before the vanilla response (for player TTS);
  - an `autoFocus` option on the Lua TextEdit for the chat overlay;
  - a loaded-save "handshake fence" (`cancelGeneration(generation, loadedSave)`, `finishLoadedSave(calendar)`);
  - a narrow exception allowing creation and update of a fixed "physical NPC diary book" record;
  - read-books-aloud;
  - combat-bark gating. — [ENGINE-INTEGRATION-PLAN.md](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md) **[verified]**
- **Maturity.** As of 2026-09-16, the Windows build and 97 Lua tests passed, but "No game was launched; audible behavior remains unverified in-game." — [ENGINE-INTEGRATION-PLAN.md](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md) **[verified]**
- **Lua structure.** Player Lua handles input and UI; GLOBAL Lua reads world context and orchestrates turns; per-actor CUSTOM scripts perform self-only actor mutations. Actions use a typed registry with terminal results, because "accepted HTTP does not prove an actor performed an action". — [agent-guide.md](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/lorkhan/files/docs/LORKHAN/agent-guide.md) **[verified]**
- **Other Morrowind AI work** (older, classic engine):
  - "Voices of Vvardenfell", an MWSE mod aiming to AI-voice the whole game (Feb 2023), **[snippet]**;
  - "Quest Voices", with 1,800+ ElevenLabs lines for quest givers;
  - a mod adding ChatGPT-written dialogue for Balmora NPCs, generated **offline and compiled as a normal content mod**;
  - a Morrowind MCP server (MWSE-based) bridging the game to external LLMs. — [r/tes3mods via redlib](https://redlib.neco.lol/r/tes3mods/comments/10udx7y/voices_of_vvardenfell_ai_voiced_morrowind_mwse); [Morrowind MCP](https://lobehub.com/mcp/longod-morrowind-mcp) **[snippet]**

#### Kenshi: Sentient Sands
- A "C++ plugin that hooks into the game and communicates with a background Python server", built on **RE_Kenshi** (plugin loader) and KenshiLib. The server auto-starts and stops with the game. — [itch.io](https://harvicus.itch.io/sentientsands) **[verified page]**
- **State read:** faction, health, hunger, equipped gear and proximity of nearby characters.
- **Actions:** "recruit, attack, trade, change political relations, or even imprison your character".
- **Persistence:** the server generates a unique name, backstory and personality per NPC on first meeting, "saved permanently to your campaign folder".
- **Backends:** Player2 by default, or any OpenAI-compatible API.
- **UI:** an F8 hub. — [itch.io](https://harvicus.itch.io/sentientsands) **[verified]**

#### Mount & Blade II: Bannerlord
- **Inworld Calradia** (2023, modder "Bloc") used Inworld AI for dialogue and TTS. It does not replace vanilla interaction. — [eXputer](https://exputer.com/news/games/mount-and-blade-2-bannerlord-mod/) **[snippet]**
- **AI Influence:** NPC memory of player words, reactions to world events, secrets, conflicts escalating into brawls, and "world responds… creating events, crises, and consequences". It supports OpenRouter, Player2, Ollama and KoboldCpp, has 70+ MCM settings, and uses `world_secrets.json` for custom lore and quests. — [smods catalogue](https://catalogue.smods.ru/archives/402946) **[snippet]**
- Bannerlord mods are C#/.NET with full in-process access to the game API, so they can call HTTP directly. **[inference; not verified for these mods]**

#### Stardew Valley: ValleyTalk
- Generates context-aware dialogue for villagers "anywhere repetition is a problem, leaving classic lines in place for events and festivals". It adds a "Something Else" free-text option to the native response menu and lets other mods inject biographies and prompt changes. Requires SMAPI. — [Nexus (search snippet)](https://nexusmods.com/stardewvalley/mods/30319) **[snippet; exact page ID unverified]**

#### Minecraft agents
- **Voyager** (2023): a GPT-4 agent with:
  - an automatic curriculum;
  - a **skill library of executable code**;
  - iterative prompting using "environment feedback, execution errors, and self-verification".
  Results: 3.3× more unique items, 2.3× longer travel distances, and tech-tree milestones unlocked 15.3× faster than prior state of the art. — [arXiv 2305.16291](https://arxiv.org/abs/2305.16291) **[verified abstract]**
- **Mindcraft** (repo created 2023-08-16; ~5.9k★; active Oct 2026):
  - built on Mineflayer;
  - 41 `!command` actions (`!goToPlayer`, `!followPlayer`, `!collectBlocks`, `!craftRecipe`, `!attack`, `!goToBed`, `!stay`, `!givePlayer`, `!putInChest`…);
  - reflexive "modes" that run without the LLM and can interrupt actions: `self_preservation` and `cowardice` "Interrupts all actions"; `unstuck` "Interrupts some actions"; `self_defense`, `hunting`, `item_collecting` and `idle_staring` run when idle.
  - LLM code generation is disabled by default (`allow_insecure_coding`) because of injection risk. — [Mindcraft README](https://github.com/mindcraft-bots/mindcraft); [modes.js / actions.js](https://github.com/mindcraft-bots/mindcraft/tree/main/src/agent) **[verified source]**

#### Generative Agents (Stanford; Park et al., 2023)
- A **memory stream** of all experiences in natural language, **retrieval** by recency, importance and relevance, **reflection** into higher-level memories, and **planning** and reacting. 25 agents in a Sims-like town. An ablation shows that observation, planning and reflection "each contribute critically" to believability. — [arXiv 2304.03442](https://arxiv.org/abs/2304.03442) **[verified abstract]**

#### Hosted character engines
- Inworld moved from "Inworld Studio" to "Runtime", which it describes as an orchestration toolkit offering "knowledge retrieval, safety checks, long-term memory, and expressive voice synthesis". — [Inworld docs](https://docs.inworld.ai/guides/runtime-character) **[verified]**

#### Latency evidence
- CUI '25 (Maslych et al.) measured a fully local ASR+LLM+TTS pipeline at mean 1.47 s (σ 0.23): ASR 0.11 s, LLM 0.49 s, TTS 0.88 s. The paper cites thresholds that responses beyond ~2 s feel unnatural and silences over ~4 s disrupt conversation. Natural conversational fillers significantly improved perceived response time at 4.0 s and 6.5 s delays; artificial wait indicators did not. — [arXiv 2507.22352](https://arxiv.org/html/2507.22352v1) **[verified]**

### Inferences
- **Mapping to the Gothic decision:**
  - **Union** is the counterpart of SKSE: an in-process C++ DLL, custom Daedalus externals, hooks. It supports either the Mantella/CHIM pattern (plugin + external server) or the SkyrimNet pattern (all in-process), and these patterns are proven at scale.
  - **OpenGothic** is the counterpart of OpenMW: open source, but with no plugin/networking surface. LORKHAN shows what that costs: a maintained engine fork pinned to an upstream commit, a patch manifest, a native transport and media cache, and several small UI/dialogue/save patches. LORKHAN is only a v0.1 prototype that, as of mid-September 2026, had not been proven in-game.
- **Proven voice trick.** Every mature mod plays generated speech through the engine's *native voice path*, so that lip/face animation, 3D audio and subtitles behave like vanilla:
  - Mantella overwrites the voice files of placeholder topics;
  - LORKHAN routes cached media into the engine's voice playback.
  In Gothic this means injecting into `AI_Output` / the output-unit lookup (Union) or the C++ equivalent (OpenGothic).
- **Action design pattern** (Mantella, SkyrimNet, CHIM, ExampleMod all converge on it):
  - a small, typed, allowlisted action set with JSON/YAML schemas;
  - eligibility pre-filtering;
  - a separate cheap model or drill-down prompt for action selection;
  - an "NPC may refuse" veto;
  - the client reporting terminal success or failure back.
  Free-form code generation (Voyager) is a research technique, not a shipped-mod technique (Mindcraft disables it by default).
- **Routines vs AI control.** No project lets the LLM drive NPCs continuously. They use a temporary override layer: custom AI packages, a faction tag, or a "busy" flag. They also add stuck detection, timeouts and a return to the original routine. Gothic has exactly these primitives (`Npc_ExchangeRoutine`, `AI_StartState`, ZS_* states, `TA_Min`), but perception-driven ZS interrupts (combat, theft reactions) will compete with AI-issued tasks in the same way Mindcraft's modes do.
- **Latency budget.** About 1.5 s is achievable fully locally with small models. With a cloud LLM and voice cloning, 2–5 s is typical, so sentence-level streaming to TTS, eligibility prewarming and in-character fillers are required.

### Gaps
- No primary latency benchmarks were found for Mantella, CHIM or SkyrimNet themselves; only vendor claims and the CUI '25 study.
- Nexus pages (Mantella, CHIM, AI Influence, ValleyTalk) were blocked (403), so download counts and exact release dates for some mods are unverified.
- Herika/CHIM's exact action schema and memory algorithm (embedding model, retrieval scoring) were not read in source.
- Convai/Inworld game-mod integrations beyond Bannerlord and GTA V were not investigated in depth.
- Sonorus (Hogwarts Legacy) and the Garry's Mod/Arma 3 ChatGPT mods were seen only in search results; their architectures were not examined.

---

## Q3. Derived requirements: what an engine or mod base must provide for LLM-driven NPCs in Gothic

### Takeaway
From the projects above, a Gothic AI-NPC base needs about fourteen capabilities. They fall into three groups:
- **Network and threading:** asynchronous networking with game-thread marshalling.
- **Engine surface:** full state read, action injection with routine override, runtime-text speech through the native voice/lip path, dynamic dialogue choices and chat UI, runtime spawning, and string-based quest log entries.
- **Persistence:** save-scoped persistence plus session/generation fencing.

Union can provide all of these through C++ plugins plus Daedalus externals and hooks. OpenGothic can provide them only through engine source patches, i.e. maintaining a fork, as LORKHAN does for OpenMW.

### Cited Findings (requirement → evidence)
1. **Non-blocking networking with results marshalled back to the game thread.**
   - SKSE_HTTP delivers replies as async ModEvents. — [SKSE_HTTP](https://github.com/art-from-the-machine/SKSE_HTTP)
   - SkyrimNet: "The game thread is never blocked". — [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin)
   - LORKHAN: enqueue-only Lua API, a worker that touches no engine objects, and results polled on the main thread. — [LORKHAN plan](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md)
2. **A fallback IPC when only file I/O is available.** Mantella's file bridge monitors a text file. — [Mantella file bridge](https://github.com/art-from-the-machine/Mantella/blob/main/src/http/file_communication_compatibility.py)
3. **Read world and actor state** (identity, stats, faction/guild, relationship/attitude, combat state, equipment, location, time, weather, nearby actors, event log). — [Mantella communication_constants](https://github.com/art-from-the-machine/Mantella/blob/main/src/http/communication_constants.py); [Sentient Sands](https://harvicus.itch.io/sentientsands)
   - SkyrimNet goes further: 100+ state "decorators", quest stages, and an NPC index across unloaded cells. — [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin); [IntelEngine](https://github.com/galanx/IntelEngine-GamePlugin)
4. **An event feed** (hits, kills, item transfers, location changes, vanilla dialogue lines) for memory and reactions.
   - Mantella's `ingame_events` and vanilla-dialogue capture. — [Mantella constants](https://github.com/art-from-the-machine/Mantella/blob/main/src/http/communication_constants.py); [MantellaDialogue.ini](https://github.com/art-from-the-machine/Mantella-Spell/blob/main/SKSE/Plugins/MantellaDialogue.ini)
   - SkyrimNet YAML triggers. — [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin)
   - LORKHAN dialogue-click event and quest-journal delta. — [LORKHAN plan](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md)
5. **Execute typed actions through the NPC's AI queue**, with an allowlist and terminal results (follow, go to, attack, trade/give, flee, wait, use item, cast). — [Mantella actions](https://github.com/art-from-the-machine/Mantella/tree/main/data/actions); [ExampleMod](https://github.com/Dwemer-Dynamics/ExampleMod); [LORKHAN agent guide](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/lorkhan/files/docs/LORKHAN/agent-guide.md)
   - Gothic primitives: `AI_StartState`, `AI_*` queue functions. — [GMC AI](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/ai/)
6. **Temporarily override and then restore daily routines**, with stuck detection, timeouts and reconstruction on load. — [IntelEngine](https://github.com/galanx/IntelEngine-GamePlugin); [Mantella follow.json note](https://github.com/art-from-the-machine/Mantella/blob/main/data/actions/follow.json)
   - Plus a "busy" flag so scripted quests win over AI. — [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin)
   - Gothic primitives: `TA_Min`, `Npc_ExchangeRoutine`. — [GMC TA](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/ta/); [GMC NPC](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/npc/)
7. **Speak runtime text with runtime audio through the engine's voice path** (subtitles, 3D positioning, lip/face animation, interruption/stop).
   - Mantella: placeholder topics plus file overwrite, `.lip` files, `StopVoiceLine`. — [MantellaConversation.psc](https://github.com/art-from-the-machine/Mantella-Spell/blob/main/Scripts/Source/MantellaConversation.psc); [SKSE_HTTP](https://github.com/art-from-the-machine/SKSE_HTTP)
   - LORKHAN: media cache into native voice playback. — [LORKHAN plan](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md)
   - In Gothic, `AI_Output` binds the WAV name and compile-time subtitle text, so an engine-level hook is mandatory. — [GMC AI_Output](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/ai/)
8. **Streaming TTS** (first sentence plays while the LLM is still generating) and speech queue ordering. — [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin); [Mantella llm/](https://github.com/art-from-the-machine/Mantella/tree/main/src/llm); [LORKHAN response_queue](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/lorkhan/files/docs/LORKHAN/agent-guide.md)
9. **Microphone capture and STT, or text input**: push-to-talk, a hotkey, a text field with focus handling. — [Mantella constants (mic/text/ptt)](https://github.com/art-from-the-machine/Mantella/blob/main/src/http/communication_constants.py); [LORKHAN autoFocus patch](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md)
10. **Custom UI.**
    - A chat overlay (PrismaUI web views in CHIM and SkyrimNet). — [CHIM](https://github.com/Dwemer-Dynamics/CHIM); [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin)
    - Or a "Something Else" option in the native dialogue menu. — [ValleyTalk snippet](https://nexusmods.com/stardewvalley/mods/30319)
    - Plus multi-NPC conversation support. Gothic already has zMultilogue / LeGo Trialoge. — [GMC zMultilogue](https://gothic-modding-community.github.io/gmc/zengin/union/plugins/zmultilogue/)
11. **Save-scoped persistence and save/load fencing.**
    - Mantella keys memories by `world_id`. — [summaries.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/remember/summaries.py)
    - ExampleMod uses `session_id` per save plus server checkpoints. — [ExampleMod](https://github.com/Dwemer-Dynamics/ExampleMod)
    - IntelEngine persists tasks via StorageUtil co-save and reconstructs on load. — [IntelEngine](https://github.com/galanx/IntelEngine-GamePlugin)
    - LORKHAN has a loaded-save fence. — [LORKHAN plan](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md)
    - Sentient Sands keeps per-campaign NPC profiles. — [Sentient Sands](https://harvicus.itch.io/sentientsands)
12. **Runtime content creation:**
    - spawn and position NPCs and items, place containers and map markers (IntelEngine rescue/find-item quests, faction battle spawns). — [IntelEngine](https://github.com/galanx/IntelEngine-GamePlugin)
    - create a book/record at runtime (LORKHAN's narrow "diary book" exception). — [LORKHAN plan](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md)
    - quest journal entries from runtime strings (Gothic `Log_*` supports this natively). — [GMC Log](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/log/)
13. **An extension API for other mods** (register actions, hooks, events, "busy" flags) to avoid action duplication and conflicts. — [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin); [IntelEngine conflict note](https://github.com/galanx/IntelEngine-GamePlugin)
14. **Security boundaries:** loopback-only endpoints, no API keys in game scripts, no generic URL/file API exposed to scripts, and LLM code execution off by default. — [LORKHAN plan](https://github.com/Dwemer-Dynamics/LORKHAN/blob/lorkhan/docs/ENGINE-INTEGRATION-PLAN.md); [Mindcraft](https://github.com/mindcraft-bots/mindcraft)

### Inferences
- **Union fit.**
  - Requirements 1, 3, 4, 5, 6, 10, 12 and 13 can be implemented in a Union C++ plugin plus Daedalus externals and hooks, the same way SKSE plugins do it. Union SDK documents custom externals and hooks.
  - Requirement 7 needs reverse-engineered hooks into the output-unit/subtitle and sound code. This is the riskiest Union item and needs verification by the Union-ecosystem research.
  - Requirement 11 needs a co-save or a save-slot-ID mechanism. Whether Union offers a standard one is unknown here.
- **OpenGothic fit.**
  - Everything is implementable because the source is open: dialogue, sound, UI and save code can be changed directly.
  - But, as with LORKHAN, it means shipping and maintaining a forked engine. Also, OpenGothic is a reimplementation, so classic Union-dependent mods and plugins (e.g. zMultilogue) would not be available.
- **Where to put the AI.** An external AI server (Python, PHP or a Dwemer-style distro) keeps the engine-side surface small and portable. An in-process design (SkyrimNet) minimises latency and setup friction, but puts LLM, TTS and STT dependencies into the game process. For a 32-bit Gothic executable under Union, in-process heavy ML runtimes are probably impractical, which pushes Union toward an external server. **[inference; the 32-bit address space of the original Gothic exe should be checked by the platform research]**

### Gaps
- I did not verify whether OpenGothic currently exposes any scripting/extension surface beyond its Daedalus VM, or whether its save format can carry custom data. This is presumably covered by the parallel platform research.
- I did not find a concrete Union technique for runtime subtitles/voice with arbitrary text, or for adding C_INFO dialogues at runtime (zParserExtender's injection is compile/merge-time). This needs confirmation.

---

## Q4. Storyline and quest generation: approaches, consistency constraints, validation, and runtime vs offline generation

### Takeaway
Shipped game mods do **not** let the LLM invent quest logic freely. They either:
- **(a)** instantiate a small set of engine-executable quest templates (kill / fetch / rescue / interact) and let the LLM choose parameters and write flavour text (LLM-Quest-Generator for Gothic 2 MP; IntelEngine for Skyrim); or
- **(b)** generate content *offline* and compile it into a normal mod (ChatGPT-written Morrowind dialogue; GameTTS-voiced Gothic mods).

Research supports both stances:
- raw LLM quest text is acceptable only about 1 in 5 times (Värtinen et al.);
- staged pipelines with structured JSON intermediates are much more coherent (Borawski et al. 2026).

Lore consistency is enforced by condition-scoped knowledge injection (SkyrimNet World Knowledge gated by quest stage, location or faction), not by trusting the model.

### Cited Findings
- **LLM-Quest-Generator** (Gothic 2 MP, 2025): build quest logic procedurally and have the LLM "simply comment on the structure"; three quest types; daily quests via ambient "Adventurer" NPCs. — [GitHub](https://github.com/LucyJohnson-lua4life/LLM-Quest-Generator)
- **IntelEngine** (Skyrim, 2026): an LLM Dungeon Master picks *when* and *which* template quest to start (combat/rescue/find-item/faction battle). The engine then places victims, chests, guardians and spawned soldiers and uses map markers/compass. Gossip chains and faction politics feed later generation. — [GitHub](https://github.com/galanx/IntelEngine-GamePlugin)
- **SkyrimNet World Knowledge:**
  - facts gated by template conditions like `get_quest_stage("MQ104") >= 13` or location/faction, either always injected or retrieved semantically;
  - Knowledge Packs let quest mods ship curated lore alongside their content. — [GitHub](https://github.com/MinLL/SkyrimNet-GamePlugin)
- **AI Influence** (Bannerlord): NPCs pursue goals and keep secrets; dialogue can create events and crises; `world_secrets.json` lets users author custom lore and quests that constrain generation. — [smods catalogue](https://catalogue.smods.ru/archives/402946) **[snippet]**
- **Värtinen, Hämäläinen, Guckelsberger**, "Generating Role-Playing Game Quests With GPT Language Models", IEEE Transactions on Games 16(1), 2024 (DOI 10.1109/TG.2022.3228480): 978 quests from six RPGs, GPT-2 fine-tuned plus GPT-3, an online study with 349 players. Result: "one in five quest descriptions would be deemed acceptable by a human critic", with large variance. — [Aalto research portal](https://research.aalto.fi/en/publications/generating-role-playing-game-quests-with-gpt-language-models/) **[snippet of abstract]**
- **Borawski, Szulc, Chudy, Giedrowicz, Mironowicz** (arXiv 2604.25482, 2026-04-28): a 5-stage, dependency-aware prompt pipeline (world → NPCs → player character → campaign quest plan → quest expansion). Each stage consumes "structured JSON outputs from previous stages". Human-centred evaluation found consistently "logically sound and structurally valid RPG content, without quality degradation as complexity increases". — [arXiv](https://arxiv.org/abs/2604.25482) **[verified abstract]**
- **CHI 2023**, "Personalized Quest and Dialogue Generation in Role-Playing Games: A Knowledge Graph- and Language Model-based Approach" (dragn.ai): it grounds quest and dialogue generation in a knowledge graph. — [PDF](https://dragn.ai/wp-content/uploads/2023/03/Personalized_Quest_and_Dialogue_Generation_in_Role_Playing_Games.pdf) **[title/venue only; PDF text not extractable here]**
- **Generative Agents** planning: agents plan their day and re-plan from retrieved memories and reflections, which yields emergent social events (the Valentine's party propagation). This is a model for "living world" storylines rather than authored quests. — [arXiv 2304.03442](https://arxiv.org/abs/2304.03442)
- **Voyager** shows the agentic alternative: the LLM writes and verifies executable code (a skill library) against a game API, with self-verification and error feedback. It is powerful but unsafe and unpredictable for shipped mods (Mindcraft disables code generation by default). — [arXiv 2305.16291](https://arxiv.org/abs/2305.16291); [Mindcraft](https://github.com/mindcraft-bots/mindcraft)
- **Offline generation compiled into a mod:**
  - Morrowind: ChatGPT-written Balmora NPC dialogues released as a normal mod;
  - Gothic: GameTTS Dialog Exporter extracts dialogue from project files for batch voice generation. — [Morrowind MCP / mods snippet](https://lobehub.com/mcp/longod-morrowind-mcp); [Nexus Gothic mod 28](https://www.nexusmods.com/gothic/mods/28) **[snippet]**
- **Gothic runtime constraints relevant to quests:**
  - quest log functions take runtime strings (`Log_CreateTopic`, `Log_AddEntry`, `Log_SetTopicStatus`);
  - but dialogue lines (`AI_Output`) and C_INFO dialogue entries are compiled script assets. — [GMC Log](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/log/); [GMC AI](https://gothic-modding-community.github.io/gmc/zengin/scripts/externals/ai/)

### Inferences
- **A hybrid fits Gothic best:**
  1. **Offline LLM pipeline** (staged JSON, lore knowledge base built from the original scripts, validators checking that every referenced NPC, item, waypoint and world exists, plus human review) that generates Daedalus quests, C_INFO dialogues and items. These are compiled into a regular mod or into zParserExtender injections, with batch TTS.
  2. **Runtime layer** limited to free-form conversation, memory, a small set of parameterised quest templates (kill/fetch/deliver/escort/interact) using existing waypoints and spawnable instances, and journal entries via `Log_AddEntry`.
- **Validation layers needed at runtime:**
  - JSON schema for actions and quests;
  - existence checks against world data (instances, waypoints, items);
  - state preconditions (NPC alive, not in a scripted quest state, guild/attitude);
  - an NPC-level veto (Mantella `veto_warning`);
  - terminal success/failure reporting so memory reflects what actually happened.
- **Lore consistency:**
  - index the original Gothic scripts (dialogue comments, `Log_*` texts, NPC instances, guilds) as a knowledge base;
  - gate facts by chapter (`Kapitel`) and quest-state variables, analogous to SkyrimNet's quest-stage conditions;
  - avoid letting the LLM change main-quest state.

### Gaps
- No Gothic-specific study or tool for validating generated Daedalus content was found.
- No published evaluation of IntelEngine's or SkyrimNet's runtime quest quality was found; the claims come from READMEs only.
- The content of the CHI 2023 knowledge-graph paper could not be extracted, so its method details are unverified.
