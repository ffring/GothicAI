# Alternative technical bases for LLM/AI-controlled NPCs in Gothic (excluding plain Union and plain OpenGothic) — state as of 2026-10-10

Notes on method: repository metadata (stars, last push, release dates, licenses) was pulled from the GitHub REST API and GitLab API on 2026-10-10; "last push" means any branch. Items marked "Inference" are my reasoning, not sourced facts.

## What does each alternative platform offer (scripting, NPC control, networking/IPC, platforms, license, activity)?

### Takeaway
The only alternative that already ships server-side NPC control plus a bidirectional HTTP module is Gothic 2 Online (G2O 0.3.x). It is closed source, the client runs only on Windows, and it supports Gothic 2 NotR only. The most open base is Gothic Unity (formerly GothicVR / Gothic-UnZENity): Unity 6 and C#, GPL-3. It is still alpha and VR-first, and it has no macOS binaries. Gothic 1 Remake (UE5, released 5 June 2026) can only be modded unofficially through UE4SS Lua so far, with official AngelScript modding promised but undated. ZenKit is the main reusable building block for asset parsing and a Daedalus VM.

### Cited Findings

#### Gothic 2 Online (G2O)
- G2O is a free multiplayer modification for Gothic II: Night of the Raven 2.6, created in 2016. Scripting is in Squirrel, it supports external modules on server and client, and it is "closed-source for security reasons". It targets only G2 NotR 2.6; I found no Gothic 1 support. — [gothic-online.com](https://gothic-online.com/)
- Current docs version is 0.3.8 (docs.gothic-online.com redirects to /0.3.8/). — [G2O docs](https://docs.gothic-online.com/0.3.8/)
- Server releases on GitLab: 0.3.0.4 (2024-09-15), 0.3.1.0 (2025-03-10), 0.3.3.0 (2025-08-12), 0.3.5.0 (2025-12-13), 0.3.7.0 (2026-05-25), 0.3.8.0 (2026-08-24), 0.3.8.2 (2026-09-01). Release assets are `server-linux-arm64`, `server-linux-x64`, `server-windows-x64` and `server-windows-x86`. There is no macOS server build. — [GothicMultiplayerTeam/server releases (GitLab)](https://gitlab.com/GothicMultiplayerTeam/server/-/releases)
- The GitLab group also contains Docker images and docker-compose templates for running the G2O server, plus a docs repo last active 2026-10-03. — [GothicMultiplayerTeam GitLab group](https://gitlab.com/GothicMultiplayerTeam)
- Server-side NPCs: `int createNpc(string name, string instance = "PC_HERO")` creates a "remote NPC" and is "Available since version: 0.3.0". The NPC is not added to the world until `spawnPlayer` is called. — [createNpc docs](https://docs.gothic-online.com/0.3.8/script-reference/server-functions/npc/createNpc/)
- The server-side NPC API includes:
  - functions: `createNpc`, `destroyNpc`, `pushNpcAction`, `clearNpcActions`, `getNpcActions`, `getNpcHostPlayer`, `setNpcHostPlayer`, `npcAttackMelee`, `npcAttackRanged`, `npcSpellCast`, `npcUseClosestMob`, `isNpcActionFinished`
  - events: `onNpcCreated`, `onNpcActionFinished`, `onNpcActionSent`, `onNpcChangeHostPlayer`
  - server classes: `Daedalus`, `NpcAction`, `Way` (waypoints)
  - — [G2O docs index](https://docs.gothic-online.com/0.3.8/)
- NPC action model: "Instead of simulating the full NPC logic on the server, the server delegates execution of actions (such as animations, attacks etc.) to clients", because "the server cannot fully emulate the Gothic game's internal logic".
  - Each remote NPC has a server action queue. The nearest streaming player becomes its "host player", runs the actions and reports results back.
  - Custom actions are written by extending `NpcAction` on both server and client.
  - Actions queued for an NPC with no host pile up until a player comes within streaming range.
  - — [NPC Action Model](https://docs.gothic-online.com/0.3.8/multiplayer/npc-action-model/)
- Official server modules: Bcrypt, **http** ("send requests to third-party services and handle incoming requests from them"), MySQL, pugiXML, Sentry and SQLite. Shared modules: json and PerformanceCounter. The changelog also mentions a client-side CEF (embedded browser) module. — [Server modules](https://docs.gothic-online.com/0.3.8/resources/server-modules/), [Shared modules](https://docs.gothic-online.com/0.3.8/resources/shared-modules/), [Changelog](https://docs.gothic-online.com/0.3.8/changelog/), [http module docs](https://g2o.gitlab.io/modules/http/)
- http module releases: 2.0.0 (2025-11-11), 2.2.0 (2025-11-16; http_client and http_server merged into one module), 2.3.0 (2026-05-27; httplib 0.43.1, wolfssl). The HTTP server is only enabled when `http_server.xml` is present. — [g2o/modules/http releases](https://gitlab.com/g2o/modules/http/-/releases), [http module docs](https://g2o.gitlab.io/modules/http/)
- Limits: up to 512 players, 65535 virtual worlds, 4094 animations. — [Limits](https://docs.gothic-online.com/0.3.8/multiplayer/limits/)
- 0.3.8.0 raised the maximum packet size from about 65 KB to about 32 MB and fixed the `combo` argument of server-side `npcAttackMelee`. — [Changelog](https://docs.gothic-online.com/0.3.8/changelog/)
- Tooling: VS Code / OpenVSX extensions (Squirrel language, linter, G2O IntelliSense) and a debugger page (sqdbg). — [G2O docs index](https://docs.gothic-online.com/0.3.8/)
- Community AI/IPC work:
  - `LucyJohnson-lua4life/g2o-redis-module`, "Redis Module for G2O", last push 2025-12-08 — [GitHub](https://github.com/LucyJohnson-lua4life/g2o-redis-module)
  - `gmp_ai`, "Simple AI framework for the gothic 2 multiplayer", MIT, 2023 — [GitHub](https://github.com/LucyJohnson-lua4life/gmp_ai)
  - `gmp_gen2_ai`, "Monster AI for the next generation Gothic 2 Multiplayer", 2022 — [GitHub](https://github.com/LucyJohnson-lua4life/gmp_gen2_ai)
  - `G2O-Script-Workshop/DEngine`, a starter framework for G2O servers, 2024-11 — [GitHub](https://github.com/G2O-Script-Workshop/DEngine)

#### GMP (Gothic Multiplayer), predecessor, now open source
- GMP is a fan multiplayer mod for G2 NotR, "originally released in January 2011, … improved … since 2022". The README warns that the current development version "may be unstable or not playable".
  - License: MIT. Build: xmake with C++20 (Visual Studio 2022 on Windows). Requires G2 NotR 2.6.
  - Repo `skejt23/GothicMultiplayer`, also referenced as `Pinemarten/GothicMultiplayer`; 21 stars; last push 2026-01-05.
  - — [GitHub README](https://github.com/skejt23/GothicMultiplayer)
- Commit on 2026-01-04: "Player state manipulation via Server-Side lua, Client-Side Function Binds refactor, lua Cursor class and LUA Documentation". The tree contains `Shared/lua_runtime` and `Shared/lua_compiler`, so the revived GMP scripts in Lua, not Squirrel. — [GitHub commits](https://github.com/skejt23/GothicMultiplayer/commits)
- Older or abandoned MP projects:
  - Sumpfkraut Online (C# server scripts), last push 2019-04-18 — [GitHub](https://github.com/Sumpfkraut-Online/SumpfkrautOnline-Khorinis)
  - OpenGMP, 2018-12 — [GitHub](https://github.com/ErrorFlexXx/OpenGMP)
  - GothicUntoldChapter (C# MP), 2015 — [GitHub](https://github.com/Mainclain/GothicUntoldChapter)
  - "Gothic-Online — Server for Gothic 1", last push 2017-11-27 — [GitHub](https://github.com/Bia10/Gothic-Online)
  - A newer `DamianQualshy/Gothic1Online` (MIT) was pushed 2026-09-02, but I could not read its contents — [GitHub](https://github.com/DamianQualshy/Gothic1Online)
  - Gothic 3 Online server (2021) — [GitHub](https://github.com/Dekryptor/Gothic3-Online)

#### Gothic Unity (formerly GothicVR, then Gothic-UnZENity)
- The repo is now `Gothic-Unity-Project/Gothic-Unity`; the old `Gothic-UnZENity-Project/Gothic-UnZENity` URL redirects there. It describes itself as a "Community rebuild of classical Gothic 1 and 2 in Unity engine. Currently focusing on a native VR implementation".
  - License: GPL-3.0. 33 stars, 8 contributors (main author JaXt0r with about 1,800 commits). Repo created 2024-06-02; last push 2026-10-09.
  - — [GitHub](https://github.com/Gothic-Unity-Project/Gothic-Unity)
- Platforms badge: "PCVR | Pico4 | Quest2+". Release assets for v0.3.0-alpha are `Gothic-Unity-Pico.zip`, `Gothic-Unity-Quest.zip` and `Gothic-Unity-Windows64.zip`; there is no macOS build. — [Releases](https://github.com/Gothic-Unity-Project/Gothic-Unity/releases)
- The README FAQ lists "Flat and Multiplayer support" as possible later ("way too early … Don't quote us on it"). The repo does contain an `Assets/Gothic-Flat` module (FlatPlayerMovement, a Flat Player prefab). — [README](https://github.com/Gothic-Unity-Project/Gothic-Unity), [Gothic-Flat folder](https://github.com/Gothic-Unity-Project/Gothic-Unity/tree/main/Assets/Gothic-Flat)
- Release history:
  - v0.0.1 pre-alpha (2024-09-07): walking and item interaction.
  - v0.1.0-alpha (2025-05-26): NPC dialogs, menus loaded from Daedalus scripts.
  - v0.2.0-alpha (2025-06-09): upgraded to Unity 6 LTS; SpeechToText "after downloading and storing local LLM", so dialog options can be spoken.
  - v0.3.0-alpha (2026-06-19): HUD, lockpicking, a first melee-combat iteration ("Monsters can't hit you (#godmode). Expect huge clunkyness"), swimming, inventory, NPC loot, world load cut to about 10 s.
  - — [Releases](https://github.com/Gothic-Unity-Project/Gothic-Unity/releases)
- Editor version is Unity 6000.3.13f1. `Packages/manifest.json` includes `com.coplaydev.unity-mcp` (an MCP server for the Unity Editor), OpenXR and XR Hands. — [ProjectVersion.txt](https://github.com/Gothic-Unity-Project/Gothic-Unity/blob/main/ProjectSettings/ProjectVersion.txt), [manifest.json](https://github.com/Gothic-Unity-Project/Gothic-Unity/blob/main/Packages/manifest.json)
- SpeechToText uses Unity Sentis model files (`encoder_model.sentis`, `decoder_model.sentis`, …) placed in StreamingAssets; it is optional ("If you do not! want GenAI features, ignore"). — [HOWTO.txt](https://github.com/Gothic-Unity-Project/Gothic-Unity/blob/main/Assets/StreamingAssets/SpeechToText/HOWTO.txt)
- Daedalus scripts run through ZenKit's VM: `VmService` and `VmExternalService` implement engine externals in C#.
  - `VmIkarusLeGoDomain.cs` adds an Ikarus/LeGo compatibility layer with a "virtual heap" and fake addresses.
  - Known gaps: `repeat`/`while`/`MEM_Goto` loops and `_@`/`MEM_GetIntAddress` are stubbed.
  - — [VmIkarusLeGoDomain.cs](https://github.com/Gothic-Unity-Project/Gothic-Unity/blob/main/Assets/Gothic-Core/Scripts/Domain/Vm/VmIkarusLeGoDomain.cs)
- Native dependencies ship only as Windows DLLs (`zenkitcapi.dll`, `dmusic.dll`) and Linux/Android `.so` files (`libzenkitcapi.so`, `libdmusic.so`). There is no `.dylib`. — [binary-dependencies repo](https://github.com/Gothic-Unity-Project/binary-dependencies)
- The predecessor repo `GothicVRProject/GothicVR` (GPL-3.0, created 2023-02-06) was last pushed 2025-03-06. — [GitHub](https://github.com/GothicVRProject/GothicVR)

#### REGoth (old reimplementation)
- `REGoth-project/REGoth` ("OpenSource-Reimplementation of the zEngine"; GPL-3.0; 654 stars) was last pushed 2019-04-18. — [GitHub](https://github.com/REGoth-project/REGoth)
- The restart `REGoth-project/REGoth-bs` (on bs::framework; MIT; 498 stars) was last pushed 2020-05-29 and has no official release. — [GitHub](https://github.com/REGoth-project/REGoth-bs), [REGoth-bs docs](https://regoth-project.github.io/REGoth-bs/general/about.html)

#### Other engine reimplementations (Godot, Unreal, fan remasters)
- A GitHub search for "gothic godot" found only tiny or empty repos (0–1 stars; e.g. `Axword/gothic-godot`, `JucanAndreiDaniel/GothicGodot`). I found no Godot reimplementation with any real progress. — [GitHub search](https://github.com/search?q=gothic+godot&type=repositories)
- **Gothic UE5 Community Remaster (fan, closed, Windows):** Gothic 2 NotR moved to Unreal Engine 5.6 by Dmitri Kaluznyj, who released a Gothic 1 UE remaster in 2024.
  - It is free, published "with the consent of THQ Nordic", and distributed as a Steam beta branch of Gothic II: Gold Classic.
  - It uses original assets and voices; VR mode is mentioned; status is alpha (article dated 2026-05-02).
  - — [Gothic Up (gothic.org.pl)](https://gothic.org.pl/en/news-en/gothic-2-remaster-on-unreal-engine-5-available/)
- Update 0.1.2.3 of that G2 UE remaster was published around 2026-06-23. — [iXBT Games](https://ixbt.games/en/news/2026/06/23/vysel-ulucsennyi-remaster-gothic-ii-na-unreal-engine-predstavlen-apdeit-0123.html)
- Community UE4SS Lua mods already exist for the "Gothic Unreal Community Remaster". Example: AutoLoot, which needs the UE4SS experimental build and states "Beta v0.1.2.3, Steam Playtest". — [RoGSlaqZ/Gothic2-AutoLoot](https://github.com/RoGSlaqZ/Gothic2-AutoLoot)

#### Gothic 1 Remake (Alkimia Interactive / THQ Nordic, UE5)
- Developed by Alkimia Interactive and published by THQ Nordic. The engine switched from UE4 to UE5.4 during development.
  - Release: **5 June 2026** on PS5, Windows and Xbox Series X/S; no macOS.
  - Reception: Metacritic 74 (PC) and 69 (PS5); 500,000 units in the first week.
  - Patches 1.0.1, 1.0.2 and hotfix 1.0.4 followed within three months.
  - Wikipedia: "Modding is possible via AngelScript".
  - — [Wikipedia](https://en.wikipedia.org/wiki/Gothic_1_Remake)
- An RPG Site article dated 7 Aug 2026 says upcoming updates will add "Marvin mode, photo mode, mod support, an alternative progression option". No date or format (Workshop, kit or editor) was given. — [RPG Site](https://www.rpgsite.net/news/21071-gothic-1-remake-new-features-marvin-mode-photo-mod-support-progression)
- A Steam forum thread quotes a developer Discord statement (dated in the thread as 17 Feb 2026): "There will be no mod kit at release. It will be partially possible to mod the game. Some scripting tools (AngelScript) are being proposed." — [Steam discussion](https://steamcommunity.com/app/1297900/discussions/0/563659002336141271/)
- A fan wiki claims game logic, AI behaviours, missions and items are "layered on top of AngelScript", that the same pipeline will be the public modding surface, and that an "Official Mod Kit is planned". This is an aggregator of unclear reliability and is not confirmed by a primary source. — [thegameswiki.com](https://thegameswiki.com/gothic-1-remake/wiki/engine-and-modding)
- Current unofficial modding runs through UE4SS (Lua):
  - Nexus has a UE4SS install guide; mods go in `G1R\Binaries\Win64\ue4ss\Mods`.
  - Community libraries include a "G1R Modding API", "PLuaModLoader" and "GORE" (a modding and save-editing toolsuite with a gore-lua helper).
  - — [Nexus UE4SS guide](https://www.nexusmods.com/gothic1remake/articles/6), [Nexus mod hub](https://www.nexusmods.com/gothic1remake/mods/585), [GORE (search snippet; repo not readable via API)](https://github.com/dh0er/gore)
- Patch 1.0.5 exists, and a hotfix landed around 25 Sep 2026. I could not confirm that official mod support or Workshop shipped by October 2026 (search summaries conflicted, and the patch-notes page did not render). — [Steam news 1.0.5](https://store.steampowered.com/news/app/1297900/view/696522455474241885)
- Gothic 2 Remake: not officially announced. At the THQ Nordic Showcase 2026, studio head Reinhard Pollice said "At some point we'll announce our new game. Probably a year from now on this show", which press read as a G2 Remake hint (speculation). — [RespawnFirst](https://respawnfirst.com/gothic-2-remake-greenlit-alkimia-teases-next-project/), [PC Games (de)](https://www.pcgames.de/Gothic-2-Spiel-4360/Kolumnen/remake-ankuendigung-offiziell-bestaetigung-1550697/)

#### Gothic Classic / Complete Classic (console ports)
- Gothic Classic (G1) launched on Nintendo Switch on 28 Sep 2023, published by THQ Nordic. — [RPG Site](https://www.rpgsite.net/news/14594-gothic-classic-arrives-on-september-28-for-the-nintendo-switch), [THQ Nordic](https://thqnordic.com/news/gothic-classic-is-coming-to-nintendo-switch-in-september-2023)
- Gothic II Complete Classic (with NotR) launched on Switch in late Nov 2023. — [Checkpoint Gaming](https://checkpointgaming.net/news/2023/12/gothic-ii-complete-classic-with-night-of-the-raven-expansion-hits-nintendo-switch), [Nintendo Life](https://retro.nintendolife.com/news/2023/10/gothic-ii-is-already-coming-to-switch-just-weeks-after-the-first-game)
- The Daedalus Language Server README has an "Acknowledgements: Gothic Classic - Nintendo Switch" section with a video. — [DLS README](https://github.com/kirides/DaedalusLanguageServer)

#### ZenKit / GothicKit (building block)
- ZenKit is "A re-implementation of file formats used by the early 2000's ZenGin". It parses MDL/MDS/MRM/TEX/ZEN/VDF/DAT/CSL and more, and includes "a type-safe VM for Daedalus scripts" plus Gothic II class definitions.
  - C++17, compilers: GCC, Clang, MSVC and Apple Clang.
  - License: MIT. 66 stars. Last tagged release v1.3.0 (2024-09-20); last push 2026-09-03.
  - Bindings: C# (NuGet), Java (Maven) and Python (PyPI).
  - — [GitHub](https://github.com/GothicKit/ZenKit)
- Binding repo activity (last push): ZenKitCS 2026-05-09, ZenKitCAPI 2026-05-09, ZenKit4Py 2025-10-19, ZenKit4J 2024-09-21. — [GothicKit org](https://github.com/GothicKit)
- Related GothicKit tools:
  - `dmusic`, a DirectMusic reimplementation (last push 2026-03-02)
  - `mdd`, "A modern decompiler for Daedalus scripts" (last push 2025-12-08)
  - `phoenix-studio`, an inspection and conversion tool (2023)
  - — [GothicKit org](https://github.com/GothicKit)
- OpenGothic and Gothic Unity both use ZenKit as their parser. — [Gothic Unity README](https://github.com/Gothic-Unity-Project/Gothic-Unity)

#### Script-level frameworks and toolchain
- Ikarus and LeGo are Daedalus libraries that exploit the interpreter for direct memory access to the original engine. Latest releases: Ikarus v1.2.3 and LeGo v2.9.0, both 2025-01-18. LeGo was last pushed 2025-08-24. The GitHub API shows no license file for either. — [Ikarus](https://github.com/Lehona/Ikarus), [LeGo](https://github.com/Lehona/LeGo)
- OpenGothic README: Ikarus/LeGo and Ninja are "What's not [working]"; Union is "not possible" because it is 32-bit and Windows only.
  - Tracking issue #231 ("Support for Ikarus/LeGo") is still open. It describes a fake 3 GB Win32-like heap; the maintainer was still working on it in Nov 2025.
  - On 2026-02-13 a user reported finishing the "Gothic 2 Lhiver 1.6" mod on a MacBook Air M2.
  - — [OpenGothic README](https://github.com/Try/OpenGothic), [Issue #231](https://github.com/Try/OpenGothic/issues/231)
- Ninja: "Extension framework … to allow true modular modifications". It supports G1 1.08k_mod, Gothic Sequel, G2 1.30 and G2 NotR 2.6.
  - Build is Windows-only (NASM/GoLink; "no longer possible under *nix").
  - License: MIT. Latest release v4.1.16 (2025-07-14); last push 2026-10-10.
  - — [GitHub](https://github.com/szapp/Ninja)
- Daedalus compilers and language servers:
  - Parsiphae ("A WiP Compiler", MIT, last push 2026-08-02) — [GitHub](https://github.com/Lehona/Parsiphae)
  - Dzieje Khorinis `DaedalusCompiler` (C#, MIT, 74 stars, last push 2025-08-22) — [GitHub](https://github.com/dzieje-khorinis/DaedalusCompiler)
  - Daedalus Language Server (Go, MIT, last push 2026-05-16). It supports VS Code and neovim, plus custom externals "provided from Union plugins such as zParserExtender". — [GitHub](https://github.com/kirides/DaedalusLanguageServer)

### Inferences
- The G2O server is authoritative over NPC *commands* but not NPC *simulation*. An LLM agent could drive NPCs by queueing high-level actions such as animations, attacks, spell casts and mob use, or custom `NpcAction` subclasses. However, pathfinding, perception and collision run on the nearest player's Windows client, so a headless "server plus AI only" world does not work without a connected client to host NPCs.
- GMP's MIT source plus new server-side Lua makes it the only fully open "original-engine multiplayer" base. Its README says the dev branch may not be playable, so it is a research base, not a production one.
- Gothic Unity is the most hackable modern-engine base: full C# source, Unity 6, and an Editor MCP package already in the manifest. The trade-offs are GPL-3 (derivatives must stay GPL), alpha gameplay (combat incomplete, monsters cannot damage the player), VR-first UX, and no macOS native libraries.
- The fan UE5 remasters and Gothic 1 Remake are closed binaries. AI work there means UE4SS Lua (unofficial, Windows-only, breaks on patches) or waiting for official AngelScript tooling, which has no published date as of Oct 2026.
- ZenKit (plus ZenKit4Py or ZenKitCS) is the practical way to extract world, dialogue and script data, build a knowledge base, or embed a Daedalus VM in a custom engine. It does not provide rendering, animation, AI or physics.

### Gaps
- No primary source (Alkimia or THQ) was found that documents Gothic 1 Remake's AngelScript API surface or a release date for official mod tools. Whether Steam Workshop or a mod kit had shipped by 2026-10-10 is unverified.
- Whether the Gothic UE5 Community Remaster uses original Daedalus scripts or reimplemented logic, and its licensing and source status, were not found.
- The G2O client's Windows-only status is inferred: it hooks the original 32-bit Gothic2.exe, and no non-Windows client exists in the docs. I found no official statement on Wine/CrossOver compatibility.
- No source confirmed how complete the G2O server-side `Daedalus` class is, for example whether it can read or run arbitrary script instances.
- The contents and status of `DamianQualshy/Gothic1Online` could not be read.

## Which platform lets an external process (an LLM agent) observe world state and issue NPC commands most easily?

### Takeaway
G2O is the easiest out of the box. Its server runs Squirrel scripts with `createNpc` and NPC action-queue functions, and its official http module can both send and receive HTTP. An external LLM service can therefore receive state pushes and send commands over HTTP or Redis without patching the game. Gothic Unity is the easiest to extend without limits, since any C# networking or LLM library can be dropped in, but you take on an unfinished engine.

### Cited Findings
- G2O NPCs can be created and commanded from server scripts:
  - functions `createNpc`, `pushNpcAction`, `npcAttackMelee`, `npcAttackRanged`, `npcSpellCast`, `npcUseClosestMob`, `setNpcHostPlayer`
  - events such as `onNpcActionFinished`
  - — [G2O docs index](https://docs.gothic-online.com/0.3.8/), [createNpc](https://docs.gothic-online.com/0.3.8/script-reference/server-functions/npc/createNpc/)
- The G2O http module allows "Request sending" and "Request handling … from external applications", with the example use case "exposing local services that interact with the game server". — [http module docs](https://g2o.gitlab.io/modules/http/)
- G2O also has SQLite, MySQL and JSON modules, and a community Redis module. — [Server modules](https://docs.gothic-online.com/0.3.8/resources/server-modules/), [Shared modules](https://docs.gothic-online.com/0.3.8/resources/shared-modules/), [g2o-redis-module](https://github.com/LucyJohnson-lua4life/g2o-redis-module)
- NPC execution is client-hosted: "the server cannot fully emulate the Gothic game's internal logic". — [NPC Action Model](https://docs.gothic-online.com/0.3.8/multiplayer/npc-action-model/)
- Gothic Unity runs Daedalus via ZenKit's VM with C# externals (`VmExternalService`), all under GPL-3 source. It already integrates an optional local speech-to-text model (Sentis) for dialog choice. — [Gothic Unity repo](https://github.com/Gothic-Unity-Project/Gothic-Unity), [v0.2.0-alpha notes](https://github.com/Gothic-Unity-Project/Gothic-Unity/releases/tag/v0.2.0-alpha)
- Revived GMP now exposes "Player state manipulation via Server-Side lua". — [GMP commits](https://github.com/skejt23/GothicMultiplayer/commits)
- Gothic 1 Remake has only unofficial UE4SS Lua access today; community APIs offer "player and actor access, spawning, location detection, game-time access". — [Nexus mod hub](https://www.nexusmods.com/gothic1remake/mods/585)

### Inferences
- Suggested ranking for "external LLM agent ↔ NPCs" with the least engine work:
  1. G2O: HTTP in and out, server NPC API, works today; Windows client needed, G2 NotR only, closed source, NPC sim on client.
  2. Gothic Unity: unlimited via C#, but alpha and VR-first.
  3. GMP: open C++ with Lua, unstable.
  4. Gothic 1 Remake or UE5 fan remaster via UE4SS: fragile, closed.
  5. Ninja/Ikarus/LeGo inside the original engine: Daedalus has no native networking; I/O would have to go through Ikarus DLL calls or Union, which are out of scope here.
- On G2O a sensible architecture is:
  - The G2O server (Linux arm64 Docker on a Mac) pushes compact world-state JSON (positions, nearby NPCs, events) to an LLM service over the http module, or over Redis.
  - The LLM replies with high-level intents, which the Squirrel side turns into NpcAction queues.
  - A Windows (or Wine/CrossOver) client must be connected so NPCs have a host.
- Dialogue and story generation (as opposed to locomotion) is easier on G2O through chat or custom client UI (CEF module) than on Gothic 1 Remake.

### Gaps
- No documented example was found of an LLM driving G2O NPCs end-to-end. A suspicious repo (below) claims LLM NPC dialogue for Gothic 2 but is not credible.
- `MelodyCheetah/gothic-2-ai-npc-edition` (created 2026-08-29) claims LLM dialogue for 120+ NPCs, "25K+" downloads and "one of the most downloaded mods on Nexus Mods". Its download buttons point to external `github.io` pages, including a macOS "Open Terminal" flow. — [GitHub](https://github.com/MelodyCheetah/gothic-2-ai-npc-edition)
  - Inference: this matches common malware-lure patterns (fabricated badges, off-repo binaries). It should not be used or recommended.

## Which of these run natively on macOS (Apple Silicon)?

### Takeaway
Almost none of the alternatives run natively on macOS. G2O and GMP clients, Ninja, Ikarus/LeGo-on-original-engine, Gothic 1 Remake and the UE5 fan remasters are Windows or console only. Gothic Unity ships only Windows, Quest and Pico builds, with no macOS native libraries. The macOS-friendly pieces are ZenKit and its bindings, the Daedalus tooling (DLS, Parsiphae), the G2O *server* (via Linux arm64 Docker), and OpenGothic, which is the main thing excluded from this note.

### Cited Findings
- G2O server binaries: linux-arm64, linux-x64, windows-x64 and windows-x86; Docker images are provided. — [G2O server releases](https://gitlab.com/GothicMultiplayerTeam/server/-/releases), [GitLab group](https://gitlab.com/GothicMultiplayerTeam)
- G2O requires Gothic II NotR 2.6. — [gothic-online.com](https://gothic-online.com/)
- GMP builds with Visual Studio 2022 and installs into a Windows Gothic II directory. — [GMP README](https://github.com/skejt23/GothicMultiplayer)
- Gothic Unity release assets are Pico, Quest and Windows64 only. Native dependencies are `.dll` and `.so` only. — [Releases](https://github.com/Gothic-Unity-Project/Gothic-Unity/releases), [binary-dependencies](https://github.com/Gothic-Unity-Project/binary-dependencies)
- ZenKit lists Apple Clang among supported compilers; its README says it is "currently only tested on Linux". — [ZenKit](https://github.com/GothicKit/ZenKit)
- OpenGothic, which consumes ZenKit, publishes `osx_arm64` and `osx_x64` builds (v0.92, 2026-05-17) and supports macOS via Metal. — [OpenGothic releases](https://github.com/Try/OpenGothic/releases), [README](https://github.com/Try/OpenGothic)
- Gothic 1 Remake platforms: PS5, Windows, Xbox Series X/S. — [Wikipedia](https://en.wikipedia.org/wiki/Gothic_1_Remake)
- Ninja's build requires Windows. — [Ninja](https://github.com/szapp/Ninja)
- Ikarus-based mods partly run on OpenGothic on Apple Silicon: a user finished "Gothic 2 Lhiver 1.6" on a MacBook Air M2 (Feb 2026). — [OpenGothic #231](https://github.com/Try/OpenGothic/issues/231)

### Inferences
- Gothic Unity could likely be built for macOS: Unity 6 Editor runs on macOS, and ZenKit compiles with Apple Clang, as OpenGothic's macOS builds show. This requires compiling `libzenkitcapi` and `libdmusic` as macOS `.dylib` files, and the VR target path is irrelevant on a Mac. Not verified.
- On a Mac, a G2O setup would be: server in Docker (arm64 native) plus the Windows Gothic client under CrossOver, Whisky or a Windows VM. This is unverified, and anti-cheat or hooking may break under Wine.

### Gaps
- No source found on running G2O, GMP or Gothic 1 Remake under CrossOver, Wine or GPTK on Apple Silicon.
- PyPI platform wheels for `zenkit` (ZenKit4Py) on macOS arm64 were not checked.

## Which are alive vs. abandoned (2024–2026)?

### Takeaway
Alive:
- G2O: frequent 0.3.x releases through 2026-09.
- Gothic Unity: v0.3.0-alpha 2026-06, pushes in Oct 2026.
- ZenKit and its bindings: 2026 activity.
- Ninja, DLS, Parsiphae.
- Gothic 1 Remake: patches through Sep 2026.
- The UE5 fan remasters.

Slow but alive: revived GMP (last push Jan 2026); Ikarus/LeGo (stable, 2025 releases).

Abandoned: REGoth and REGoth-bs (2019/2020), Sumpfkraut Online, OpenGMP, old G1 Online servers, any Godot attempts.

### Cited Findings
- G2O server: about 30 tagged releases from 0.3.0.4 (2024-09-15) to 0.3.8.2 (2026-09-01); docs repo active 2026-10-03; http module 2.3.0 on 2026-05-27. — [Server releases](https://gitlab.com/GothicMultiplayerTeam/server/-/releases), [GitLab group](https://gitlab.com/GothicMultiplayerTeam), [http releases](https://gitlab.com/g2o/modules/http/-/releases)
- Gothic Unity: v0.3.0-alpha 2026-06-19; last push 2026-10-09; 8 contributors, but one dominant maintainer. — [GitHub](https://github.com/Gothic-Unity-Project/Gothic-Unity)
- ZenKit: last push 2026-09-03 (last tag v1.3.0, 2024-09-20); ZenKitCS 2026-05-09; dmusic 2026-03-02. — [GothicKit org](https://github.com/GothicKit)
- Ninja: v4.1.16 on 2025-07-14; last push 2026-10-10. — [Ninja](https://github.com/szapp/Ninja)
- DLS last push 2026-05-16; Parsiphae last push 2026-08-02. — [DLS](https://github.com/kirides/DaedalusLanguageServer), [Parsiphae](https://github.com/Lehona/Parsiphae)
- Ikarus v1.2.3 and LeGo v2.9.0 released 2025-01-18. — [Ikarus](https://github.com/Lehona/Ikarus), [LeGo](https://github.com/Lehona/LeGo)
- GMP (open source): last push 2026-01-05; 21 stars. — [GitHub](https://github.com/skejt23/GothicMultiplayer)
- REGoth last push 2019-04-18; REGoth-bs last push 2020-05-29. — [REGoth](https://github.com/REGoth-project/REGoth), [REGoth-bs](https://github.com/REGoth-project/REGoth-bs)
- OpenGothic, for reference: 1,616 stars; last push 2026-10-08. — [GitHub](https://github.com/Try/OpenGothic)
- Gothic 1 Remake: launched 2026-06-05; patches 1.0.1–1.0.5 and a hotfix around 2026-09-25; mod support listed as upcoming in Aug 2026. — [Wikipedia](https://en.wikipedia.org/wiki/Gothic_1_Remake), [RPG Site](https://www.rpgsite.net/news/21071-gothic-1-remake-new-features-marvin-mode-photo-mod-support-progression), [Steam news](https://store.steampowered.com/news/app/1297900/view/696522455474241885)
- UE5 fan remaster of G2: alpha on 2026-05-02 and update 0.1.2.3 around 2026-06-23. — [Gothic Up](https://gothic.org.pl/en/news-en/gothic-2-remaster-on-unreal-engine-5-available/), [iXBT Games](https://ixbt.games/en/news/2026/06/23/vysel-ulucsennyi-remaster-gothic-ii-na-unreal-engine-predstavlen-apdeit-0123.html)

### Inferences
- Community size, using GitHub stars as a rough proxy: OpenGothic (about 1.6k) is far ahead of every alternative. G2O has an established MMO/RP server scene, but because it is closed source its developer base is mostly the Squirrel scripters around its Discord and GitLab modules. Gothic Unity effectively depends on one maintainer, which is a bus-factor risk.
- If Alkimia ships the promised AngelScript modding, Gothic 1 Remake could become the best-looking moddable base. As of Oct 2026 it is promise-only, Windows/console, and limited to Gothic 1 content.

### Gaps
- No reliable player or server counts were found for G2O in 2026.
- No reliable size figures were found for the Gothic 1 Remake modding community beyond Nexus listings. One aggregator claims "400+ mods" ([myrtana.net](https://www.myrtana.net/en/remake-mods)), which is unverified.
