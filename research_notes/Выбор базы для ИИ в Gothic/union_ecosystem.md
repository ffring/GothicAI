# Union plugin ecosystem for original Gothic 1 / Gothic 2 (ZenGin) as a base for LLM-driven NPCs, mechanics and storylines (state as of Oct 2026)

Scope note: research done 2026-10-10. Sources are mostly community docs (Gothic Modding Community, GMC), GitHub/GitLab repos and forum threads. Several primary pages (worldofplayers.ru, Steam Workshop, CodeWeavers) were only partially reachable (403/429), so some facts come from search-result summaries and are marked as such.

## 1. What Union is: authors, versions, SDK vs Framework, packaging, supported Gothic builds

### Takeaway
Union is a closed-binary, community-made (Russian) engine patcher/plugin loader for the original 32-bit ZenGin executables by Gratt (core) and killer-m. The last player-facing release is still **Union 1.0m** (no 1.0n / 2.0 found). Plugins are x86 C++ DLLs. The old Visual Studio "Union SDK" is officially discontinued in favour of the CMake-based **Union Framework** (`union-api` + `gothic-api` on GitLab), which is still marked "in development". It covers four engine builds: G1, G1A (= Gothic Sequel), G2 and G2A (NotR).

### Cited Findings
- GMC definition: "Union is a system to patch and extend Gothic's engine the ZenGin. It allows you to load `.dll` files - ZenGin extensions created using the Gothic/Union SDK and `.patch` files". It also bundles SystemPack, a collection of bug fixes and performance edits. The page was last updated 2025-10-07. [GMC – Union](https://gothic-modding-community.github.io/gmc/zengin/union/)
- Authors: Gratt and killer-m. The original worldofplayers.ru thread shows active development around Jan–June 2018. Stated features at the time: plugin loading, an engine API, a custom scripting language for patch modules, VDF/MOD encryption, and memory management that replaces a separate 4 GB patch. Supported targets were G1, G1A, G2 and G2A. Install routes: Steam via a Workshop BETA branch; GOG with Union preinstalled for G1. [worldofplayers.ru thread 40376](https://worldofplayers.ru/threads/40376/)
- RPGRussia resource page lists **version 1.0m, updated March 24, 2025**. Features listed:
  - a PATCH-file language
  - an extended VDF virtual file system
  - OGG sound
  - DirectX8 mouse
  - a 4 GB (LAA) memory patch
  - Steam overlay
  - DLL/D/SRC plugin autorun
  - engine fixes ported from SystemPack 1.8

  Team: Gratt (core), killer-m (bug fixes), Jr13San (testing), neromont (docs). It was voted best patch of 2021 by RPG Russia users. [RPGRussia – Gothic ½ Union](https://rpgrussia.com/resources/gothic-1-2-union.4209/)
- A search-result summary dates the 1.0m release to 26.06.2022 on worldofplayers.ru. I did not verify this on the primary page. [Lutris installer page (search snippet)](https://lutris.net/games/install/37809/view); offline installers are also on [ModDB – Union 1.0m offline installers](https://www.Moddb.com/games/gothic-ii-the-night-of-the-raven/downloads/union-10m-offline-installers-v10). Players get it through Gratt's "Resource Manager" ([ModDB – Resource Manager for Union v35](https://www.Moddb.com/games/gothic-ii-the-night-of-the-raven/downloads/resource-manager-for-union-v35)).
- Steam Workshop has Union items for Gothic 1 and Gothic 2, for example "Union - patch for Gothic 2". [Steam Workshop item 2787458679](https://steamcommunity.com/workshop/filedetails/?id=2787458679)
- THQ Nordic's official Gothic 1 Steam update on 17 March 2021 integrated **SystemPack 1.8, not Union**. Community members criticised it as outdated next to Union. [PCGH forum thread](https://extreme.pcgameshardware.de/threads/gothic-1-update-patch-auf-steam-zum-geburtstag-fuehrt-mod-offiziell-ein.601831/)
- Union SDK:
  - It is a Visual Studio project template plus a C++ hooking library plus "Gothic API" with method addresses for G1, G2, G2 NotR and the unreleased Gothic Sequel.
  - It needs .NET 4.7.2 and the VC++ 2010 runtime, and it is distributed through the Resource Manager (thread 41415).
  - GMC: "Union SDK has been discontinued in favor of the Union Framework, however, it is still available". [GMC – Union SDK](https://raw.githubusercontent.com/Gothic-Modding-Community/gmc/main/docs/zengin/union/sdk/index.md)
- SDK template "Union Plugin 1.0m":
  - It was built with toolset v100 but supports newer toolsets.
  - Build configurations are G1, G1A, G2, G2A, MP x2 and MP x4, each in Debug, MD Release and MT Release.
  - A mismatch crashes the game: "Gothic 2 Night of the Raven is G2A and doesn't work with G2 or G1A". Steam's "Gothic II Classic" uses the G2A engine.
  - The DLL goes into `System/Autorun/`, or into `System/` with a Gothic.ini entry.
  - Debugging options: the VS debugger, zSpy, and a Union console enabled in SystemPack.ini.

  [GMC – SDK Getting started](https://gothic-modding-community.github.io/gmc/zengin/union/sdk/getting_started/)
- Union Framework is described as the "successor of the Union SDK… simple more modular and improved version". It "is still in development and may not be as easy to set up and use as the Union SDK" (page updated Jan 11, 2025). [GMC – Union Framework](https://gothic-modding-community.github.io/gmc/zengin/union/framework/)
- Framework repos:
  - `gitlab.com/union-framework/union-api` was created May 13, 2023 (99 commits, BSD-3-Clause per the GitLab header). [GitLab union-api](https://gitlab.com/union-framework/union-api)
  - `gitlab.com/union-framework/gothic-api` was created May 14, 2023 (151 commits, BSD-3-Clause per the GitLab header). [GitLab gothic-api](https://gitlab.com/union-framework/gothic-api)
  - Gratt opened a merge request ("zSharedPtr") into gothic-api on 30 March 2025, so he was still active in 2025. [GitLab gothic-api MR !37](https://gitlab.com/union-framework/gothic-api/-/merge_requests/37)
  - **License conflict:** the Silver Ore Team template README says union-api and gothic-api are **GPLv3**, while the GitLab headers show BSD-3. The template itself is MIT. [Silver-Ore-Team/union-plugin-template](https://github.com/Silver-Ore-Team/union-plugin-template)
- Silver Ore template: gothic-api provides "inline files for every ZenGin class". Builds use CMake presets (x86-debug/x86-release) with VS2022 MSVC 14.34+. One DLL covers G1/G1A/G2/G2A through namespaces, with runtime dispatch via `GetGameVersion()` or the `zSwitch` macro. [Silver-Ore-Team/union-plugin-template](https://github.com/Silver-Ore-Team/union-plugin-template)
- Patrix9999's template can be **built on Linux with msvc-wine**, which runs the real MSVC toolset under Wine together with clang-cl, Ninja and Wine. It produces a 32-bit Windows DLL and packs it into a VDF automatically. Loading: the DLL in `System/autorun`, or `UnionPlugin.vdf` in `Data/`. [Patrix9999/union-plugin-template](https://github.com/Patrix9999/union-plugin-template)
- Plugin loading methods per GMC: a SystemPack.ini list with priority control via asterisk notation, `System/Autorun` (or virtual directories inside VDF/MOD volumes), and patch-file functions `LoadLibrary()`/`LoadPlugins()`. [GMC – Plugins](https://gothic-modding-community.github.io/gmc/zengin/union/plugins/)
- Gratt's GitHub hosts UnionProject ("Minimum set for working with Union and Gothic engine"), zGamePad, SteamLauncherOptimizer, zFont, zParserExtender, zTrollStoneThrowing and others (29 repos). [GitHub – Gratt-5r2](https://github.com/Gratt-5r2), [UnionProject](https://github.com/Gratt-5r2/UnionProject)

### Inferences
- "G1A" in Union terminology is the Gothic Sequel engine (1.12f), not a G1 add-on. For G1 the realistic targets are G1 1.08k(_mod); for G2 it is G2A 2.6 (fix). The Ninja and GD3D11 version lists below match this.
- Union itself is closed-source; only the SDK/Framework headers and libraries are open. Long-term risk sits with essentially one core developer (Gratt). The 1.0m line has not moved to a new major version in more than three years.
- For a new project in 2026 the practical choices are (a) the legacy Union SDK 1.0m (documented, stable, VS template) or (b) the union-api/gothic-api CMake framework (modern, multi-engine, Linux-buildable, less documented). Both produce DLLs that Union 1.0m loads.

### Gaps
- I found no official changelog for 1.0m, no confirmed exact release date (26.06.2022 is unverified), and no evidence of a newer Union build. The worldofplayers.ru posts I could read (April–May 2025) mention no new version.
- The union-api and gothic-api README contents (feature list, maintainers, last commit date) could not be read; GitLab rendered only headers. The BSD-3 vs GPLv3 license question is unresolved.
- The ".vdf/.mod packaging" details (encryption, how VDF-embedded DLLs are extracted) are not covered beyond the points above.

## 2. How much of the engine a Union plugin can reach; pain points

### Takeaway
A plugin runs inside the game process with full native access. Through reverse-engineered headers for "every ZenGin class" it can call or hook any engine method (oCNpc, oCGame, zCParser, AI message queues and so on), register new Daedalus externals, call script functions, and get lifecycle events including save and load. The cost: addresses and class layouts come from reverse engineering, hooks are raw x86 detours, and a version or configuration mismatch crashes the game. There is no official engine documentation.

### Cited Findings
- Hooking uses `CInvoke<fn_type> Ivk_X(address, interceptor, flags)` or the equivalent `HOOK Ivk_X AS(address, interceptor, flags)`. You need the target's address from the engine headers or name files. Flags: `IVK_AUTO` (default; "interception tree" so several plugins can hook the same address), `IVK_NORMAL`, `IVK_REDEFINE`, `IVK_PROTECTED`, `IVK_DISABLED`. The original function is called through the CInvoke object. [GMC – SDK Hooks](https://gothic-modding-community.github.io/gmc/zengin/union/sdk/hooks/)
- New Daedalus externals:
  - The handler is a `__cdecl` function with no arguments returning int/bool. It pops arguments with `parser->GetParameter()` in reverse order and must pop all of them.
  - Registration happens in `Game_DefineExternals` through `parser->DefineExternal(name, fn, retType, argTypes…, zPAR_TYPE_VOID)`.
  - Global parser pointers available: `parser`, `parserSoundFX`, `parserParticleFX`, `parserVisualFX`, `parserCamera`, `parserMenu`, `parserMusic`.
  - Types: void, float, int, string, class, func, prototype, instance.

  [GMC – SDK Externals](https://gothic-modding-community.github.io/gmc/zengin/union/sdk/externals/)
- Lifecycle events, useful for an AI controller loop and persistence:
  - startup: `Game_Entry` (before engine init; do not touch engine classes), `Game_DefineExternals`, `Game_Init` (after DATs load, before main menu)
  - level loading: `Game_LoadBegin_/LoadEnd_{NewGame,SaveGame,ChangeLevel,Trigger}`
  - frame and menu: `Game_PreLoop/Loop/PostLoop`, `Game_MenuLoop`
  - saving and pausing: `Game_SaveBegin/SaveEnd`, `Game_Pause/Unpause`, `Game_ApplyOptions`
  - exit: `Game_Exit`

  The docs specify **no threading constraints or persistence mechanism**. [GMC – SDK Events](https://gothic-modding-community.github.io/gmc/zengin/union/sdk/events/)
- zParserExtender, built into Union since 1.0m, adds script-level externals in these categories: AI, CAST, Events, HLP, Log, MDL, MOB, Menu, NPC, PAR, String, VOB, WLD. It also adds a C_Trigger class, engine helper classes, Daedalus hooking/injection and META blocks. [GMC – zParserExtender](https://gothic-modding-community.github.io/gmc/zengin/scripts/extenders/zparserextender/)
- Example NPC externals in zParserExtender: `Npc_SetAsHero`, `Npc_OpenInventory`/`…Steal`/`…Trade`, `Npc_GetLeft/RightHandItem`, `Npc_GetSlotItem`, `Npc_PutInSlot`, `Npc_RemoveFromSlot`. [GMC – zPE NPC externals](https://gothic-modding-community.github.io/gmc/zengin/scripts/extenders/zparserextender/externals/npc/)
- The SDK expects users to know "C++… x86 (32-bit) architecture, dynamically linked libraries, and reverse engineering". [GMC – Union SDK](https://raw.githubusercontent.com/Gothic-Modding-Community/gmc/main/docs/zengin/union/sdk/index.md)
- Hooks written with raw `CInvoke` addresses only work for the engine whose addresses they hard-code, while `HOOK … AS` (multi-platform) works across engines. The SpacerNET README notes exactly this. [postm1/SpacerNET_Union](https://github.com/postm1/SpacerNET_Union)
- Real-world crash and installation friction in 2025 on the main Union thread: offline-install errors that required removing the whole game, and a G2 2.6 install showing version 2.7 with launcher crashes, solved with a specific repacked executable. [worldofplayers.ru 40376, p.125](https://worldofplayers.ru/threads/40376/page-125)

### Inferences
- Plugins can reach NPC AI (state functions, the `AI_*` event-message queue through zCEventManager/oCMsg* classes, perceptions), waynet/pathfinding, oCInfo dialogs and SVM/output units, inventory, combat and world spawning. This rests on the framework claim of headers for every ZenGin class plus the hook mechanism. I did not verify individual class or member coverage, and community experience says some members still need reverse engineering.
- The natural architecture for LLM control: (1) add Daedalus externals (for example `LLM_Ask(npc, prompt)` / `LLM_Poll()`), (2) run game-thread work in `Game_Loop`, (3) inject NPC actions by calling the same engine functions the script externals use (AI_Output, AI_GotoWP, etc.) on the main thread, (4) persist custom state in `Game_SaveBegin`/`Game_LoadEnd_SaveGame`, for example in a side file next to the savegame slot. Because the docs say nothing on persistence, the side-file approach is an inference.
- ZenGin is effectively single-threaded. Every engine and parser call should be marshalled to the main game loop. This is standard practice, not a cited Union rule.

### Gaps
- I found no authoritative list of which ZenGin classes or members are incomplete in gothic-api, and no documented thread-safety guarantees.
- I found no official Union mechanism for storing custom data in savegames. Script-level alternatives (Ikarus/LeGo persistent variables) were not researched in depth.

## 3. Network I/O, threads, HTTP to an LLM, embedded scripting runtimes, existing AI/IPC plugins

### Takeaway
Nothing in Union forbids network I/O or threads. A plugin is an ordinary Win32 DLL in the game process, so it can use WinHTTP/WinINet/libcurl, sockets or named pipes, spawn threads, and link third-party native libraries (BASS, XInput and Discord IPC are already used by plugins). There are working precedents for embedding a scripting VM (Squirrel in Gothic 2 Online) and for a C++ plugin talking to a C# front end (Spacer.NET). I found **no mature, verified LLM-for-Gothic Union plugin**.

### Cited Findings
- Gothic 2 Online (G2O, multiplayer) embeds **Squirrel** scripting. Its GitLab group publishes a "Squirrel Module Template" (updated Oct 02, 2026) and a "Union Module Template – Example template for creating Gothic 2 Online squirrel modules using Union Framework" (updated May 05, 2026). [GitLab topic g2o](https://gitlab.com/explore/projects/topics/g2o); G2O servers are scripted in Squirrel ([ModDB – Gothic Online](https://www.moddb.com/mods/gothic-online)).
- Spacer.NET (world editor): a C++ Union plugin (needs Union 1.0l+, VS2015–2019 v140_xp) communicates with a C# interface through a stack-based export layer (`Stack_PushInt`, Export.cpp). It is MIT-licensed with 770 commits. [postm1/SpacerNET_Union](https://github.com/postm1/SpacerNET_Union)
- zBassMusic (Silver Ore Team) replaces DirectMusic with the third-party **BASS** library, loads files from VDF and is controlled from Daedalus. [GMC – zBassMusic](https://gothic-modding-community.github.io/gmc/zengin/union/plugins/zbassmusic/), [GitHub zBassMusic](https://github.com/Silver-Ore-Team/zBassMusic)
- A Discord Rich Presence plugin ("GothicRichPresence", C++, Franisz fork of Lavalierre) exists, so a plugin can do external IPC. [Search result for Franisz repos](https://gitblind.noratr.app/Franisz)
- The zTTSDialogues Union plugin reads dialogues aloud through Windows Narrator, showing that plugins use OS speech APIs. [Steam Workshop zTTSDialogues (search summary)](https://steamcommunity.com/sharedfiles/filedetails/?id=3198075836)
- zUtilities is a QoL plugin "made for Union 1.0m for Gothic 1 and 2". [Franisz/zUtilities](https://github.com/Franisz/zUtilities)
- UnionMinimap (TieruYT) is a G1/G2 Union plugin that its README says was built with AI assistance (Gemini 3 Pro, Claude 4.5 Opus). This is evidence that LLM-assisted Union C++ development is feasible. [TieruYT/UnionMinimap (search summary)](https://github.com/TieruYT/UnionMinimap)
- Script-level escape hatch without Union: Ikarus `CALL_*` lets Daedalus call any native function by address with `__stdcall`, `__thiscall`, `__cdecl` or `__fastcall` and int/float/ptr/string/struct parameters. Addresses must be found with tools like IDA. [GMC – Ikarus CALL](https://gothic-modding-community.github.io/gmc/zengin/scripts/extenders/ikarus/functions/call/)
- "Gothic 2 AI NPC Edition" (MelodyCheetah) claims Ollama and OpenAI-compatible LLM chat with 120+ NPCs, a hotkey overlay, voice, "25K+" downloads and macOS 12+ / Apple Silicon support. The repository has 1 star, 0 forks, 5 commits and **no technical architecture** (no mention of Union, Ikarus or an external process). [GitHub – MelodyCheetah/gothic-2-ai-npc-edition](https://github.com/MelodyCheetah/gothic-2-ai-npc-edition)
- MultiProcess (Patrix9999), an ASI-loader plugin for multiple game instances, "doesn't work with Union". This is an example of a loader conflict. [Patrix9999/MultiProcess](https://github.com/Patrix9999/MultiProcess)

### Inferences
- An LLM bridge is technically straightforward. Run a worker thread inside the DLL (WinHTTP to a local Ollama or llama.cpp server, or to a cloud API), or better, an **out-of-process sidecar** reached over a localhost socket or named pipe. This keeps heavy TLS/JSON/LLM-client dependencies and crashes out of the 32-bit process and its ~4 GB address space. Results then go into a queue that `Game_Loop` drains on the main thread.
- Embedding Lua or Python in a 32-bit DLL is possible in principle; G2O's Squirrel proves a VM can drive the engine. However, I found **no public Union plugin that embeds Lua, Python or C#/.NET for single-player modding**. You would have to build that bridge yourself, or reuse G2O's Squirrel stack, which is multiplayer-oriented.
- The MelodyCheetah repo fits a common GitHub malware-lure pattern: unverifiable download claims, almost no commit history, no technical description, and claimed macOS support for a Windows-only 32-bit game. Treat it as **unverified/suspicious** and do not run its binaries without inspection. It is not evidence that a working Union LLM integration exists.

### Gaps
- No public example of a Union plugin making HTTP calls or using worker threads was found or verified. The feasibility above is inferred from the normal Win32 DLL model.
- I did not find whether G2O's Union Module Template can be reused outside G2O (license, single-player use).

## 4. Script-level stack: Ikarus, LeGo, AFSP, zParserExtender, Ninja, GMBT

### Takeaway
There are two parallel worlds:
- **Ikarus → LeGo → AFSP**: pure Daedalus memory hacking that runs on the vanilla executables, works with or without Union, and is packaged modularly through **Ninja**.
- **zParserExtender**: Union-built-in. Adds new syntax and externals, plus runtime script injection, and merges new scripts into compiled .DAT files.

A Union C++ plugin can expose externals to either world, so they combine well. The LLM "brain" belongs in a C++ plugin or sidecar; quest and dialog content can stay in Daedalus.

### Cited Findings
- GMC overview: Ikarus is the foundation (memory access, strings, keyboard and other low-level functions). LeGo is "a collection of packages built on top of" Ikarus (AI functions, event handlers, hashtables, timers, UI bars, FX). AFSP "offers even more functionality and is built on top of Ikarus & LeGo". zParserExtender is a parallel, newer Union-based approach. [GMC – Script extenders](https://gothic-modding-community.github.io/gmc/zengin/scripts/extenders/)
- LeGo: "a daedalus library for the game Gothic", maintained by Lehona, with 299 commits on the dev branch. It ships Gothic 1 and Gothic 2 headers and packages for animation, bars, buttons, buffs, talents, hashtables, lists, queues, dialogue systems and memory management. [GitHub – Lehona/LeGo](https://github.com/Lehona/LeGo)
- Current library versions: **Ikarus v1.2.3** and **LeGo v2.9.0**, as supported by Ninja 3.1.16 (18 Jan 2025). [Ninja releases](https://github.com/szapp/Ninja/releases)
- AFSP (AF Script Packet) is a script package for G1 and G2 NotR by Fawkes and Auronen, hosted on GitHub. [search summary](https://www.github.com/Wolf-64); the layering on Ikarus and LeGo is per [GMC](https://gothic-modding-community.github.io/gmc/zengin/scripts/extenders/).
- zParserExtender:
  - author: Gratt
  - GitHub description: "Merge or override new scripts for compiled .DAT files"
  - adds runtime compilation of OU files in game, runtime script injection, namespaces, `while` loops, test-else, extern binding, dialogue constants, event functions, Daedalus hooks and META
  - "fully integrated in Union itself" since 1.0m

  [GMC – zParserExtender](https://gothic-modding-community.github.io/gmc/zengin/scripts/extenders/zparserextender/), [GitHub – Gratt-5r2/zParserExtender](https://github.com/Gratt-5r2/zparserextender)
- Ninja (szapp, MIT): "Extension framework… to allow true modular modifications".
  - Supported builds: G1 1.08k_mod, Gothic Sequel 1.12f, G2 1.30 fix-report and G2 NotR 2.6 fix-report.
  - Latest is **4.1.16 (14 July 2025)**. In 4.0.16 (13 July 2025) the bundled Ikarus and LeGo were split out into a separate "Toolkit" mod, and code signing was added to fight antivirus false positives.
  - 3.0.15 (Sep 2024) completed the Sequel and G2 Classic ports.

  [GitHub – szapp/Ninja](https://github.com/szapp/Ninja), [Ninja releases](https://github.com/szapp/Ninja/releases)
- The community has "Ninja patches", including on Polish community sites. [gothic.org.pl – Ninja patches](https://gothic.org.pl/?p=14292)

### Inferences
- Ninja's AV false-positive problem in 2025 (runtime unpacking) is a warning for a Union LLM plugin too. A DLL that injects into the game and opens network connections is likely to trigger heuristics; code signing helps.
- For an LLM project: put the brain and IO in a C++ Union plugin and expose a small Daedalus API. Then quest, dialog and storyline glue can be authored or generated as Daedalus. zParserExtender's runtime OU compilation and script injection is the strongest existing hook for "generated" content loaded at runtime. That this can be driven by LLM-generated code is an inference, not tested.

### Gaps
- **GMBT (Gothic Mod Build Tool)** was not researched; I have no verified facts on its status.
- Exact Ikarus/LeGo vs zParserExtender coexistence rules (load order, known conflicts) were not found in fetched docs.
- The AFSP repository URL, license and activity were not verified directly.

## 5. Renderers, QoL patches, and compatibility with large modifications

### Takeaway
GD3D11 (kirides fork, GPLv3) is the actively maintained DX11 renderer for G1 1.08k and G2A 2.6, with commits into 2026 and a documented Wine/DXVK path. Large mods do **not** uniformly support Union. Chronicles of Myrtana: Archolos explicitly does not support Union or Ninja.

### Cited Findings
- GD3D11:
  - Replaces the DirectDraw-based pipeline with D3D11 and supports **G1 1.08k (1.30.0.0)** and **G2 NotR 2.6 (2.6.0.0-rev2)**.
  - x86 only, with SSE2/AVX/AVX2 builds, built with VS 2026 and C++23. License GPLv3 (HBAO+ under the GameWorks EULA). 1,428 commits and 303 stars.
  - Supports Wine/Linux **via DXVK (tested on Fedora 42)**.

  [GitHub – kirides/GD3D11](https://github.com/kirides/GD3D11)
- GD3D11 activity: 17.8-dev builds since Dec 2024. dev21 added the ability to change sky textures "from plugins and mods" (an explicit plugin interop point) and fixed ghost NPCs, Bink FPS and similar issues. A search summary reports the last push as May 3, 2026 (not verified on the primary page). [GD3D11 releases](https://github.com/kirides/GD3D11/releases/tag/v17.8-dev1_rl); [gittrend summary](https://gittrend.io/repo/kirides/GD3D11)
- Chronicles of Myrtana: Archolos official FAQ: the mod "was not developed with Union, Ninja or other plugins in mind… use them at your own risk". GD3D11/DX11 is supported in a specific version, and other DX11 versions may cause defects. [Archolos official FAQ (mirror)](https://gameplay.tips/guides/the-chronicles-of-myrtana-archolos-official-faq.html)
- Plugins documented on GMC: zGamePad (XInput controller emulation, by Gratt), zBassMusic, zMultilogue (multi-NPC dialogs similar to LeGo Trialoge), zSplitDialogs. [GMC – Plugins](https://gothic-modding-community.github.io/gmc/zengin/union/plugins/)

### Inferences
- If you build on Union, build on **G2A 2.6 + Union 1.0m (+ optional GD3D11)**, the best-supported combination across Union, Ninja, GD3D11 and OpenGothic data. Expect to own compatibility testing if you target a large total conversion.

### Gaps
- I could not verify Union compatibility for **Returning 2.0 (AB/New Balance), Velaya, L'Hiver Edition or Odyssey**. Russian-language searches returned nothing usable. I know of no citable source on which of these ship with or require Union, or on SystemPack-vs-Union status for them.

## 6. Community health, where development happens, documentation, licenses (2024–2026)

### Takeaway
Activity is spread across Russian forums (worldofplayers.ru, where Union is distributed), GitLab (union-framework, Gothic Multiplayer Team), GitHub (Gratt-5r2, Silver-Ore-Team, szapp, kirides, Lehona, Patrix9999, Franisz) and the English GMC docs. The ecosystem was demonstrably active in 2025–2026, but the core Union binary is maintained by a very small team, and the Framework docs are thin.

### Cited Findings
- GMC Union pages: updated 2025-10-07 (contributors muczc1wek, Auronen), with Framework docs updated 2025-01-11. [GMC – Union](https://gothic-modding-community.github.io/gmc/zengin/union/), [GMC – Framework](https://gothic-modding-community.github.io/gmc/zengin/union/framework/)
- worldofplayers.ru Union thread: user support posts from April 11 to May 11, 2025, with no developer statements about future versions on that page. [worldofplayers.ru 40376 p.125](https://worldofplayers.ru/threads/40376/page-125)
- Gratt's commit to gothic-api on 2025-03-30. [GitLab MR !37](https://gitlab.com/union-framework/gothic-api/-/merge_requests/37)
- Other dated activity:
  - Ninja releases in Jun 2024, Sep 2024, Jan 2025 and Jul 2025 ([Ninja releases](https://github.com/szapp/Ninja/releases))
  - G2O Union module template updated May 2026 and Squirrel module template updated Oct 2026 ([GitLab g2o](https://gitlab.com/explore/projects/topics/g2o))
  - GD3D11 dev builds 2024–2026 ([kirides/GD3D11](https://github.com/kirides/GD3D11))
- Licenses seen:
  - Ninja: MIT
  - GD3D11: GPLv3
  - Silver Ore template: MIT
  - union-api/gothic-api: BSD-3 per GitLab, GPLv3 per Silver Ore README (conflict)
  - Spacer.NET: MIT
  - Union itself: closed binary, no license found

  Sources: [Ninja](https://github.com/szapp/Ninja), [GD3D11](https://github.com/kirides/GD3D11), [Silver Ore template](https://github.com/Silver-Ore-Team/union-plugin-template), [GitLab union-api](https://gitlab.com/union-framework/union-api), [SpacerNET](https://github.com/postm1/SpacerNET_Union)
- Star counts are small: Gratt's UnionProject 25, zParserExtender 8, Silver Ore template 5. [Gratt-5r2](https://github.com/Gratt-5r2), [Silver Ore template](https://github.com/Silver-Ore-Team/union-plugin-template)

### Inferences
- Bus factor is the main strategic risk. Union core is essentially Gratt plus killer-m. If Union stops working on some future Windows, Wine or macOS translation layer, nobody outside that team can fix the loader itself.
- Expect to read Russian forum threads and the C++ headers rather than polished API docs.

### Gaps
- No metrics on the number of active Union plugin developers. I could not get a first-hand read of worldofplayers.ru sub-forums for plugin development (only one page of the main thread).

## 7. Platform limits: Windows-only, 32-bit, DX7, and macOS (Apple Silicon) via CrossOver, Whisky, Wine or GPTK

### Takeaway
Union and its plugins run only inside the original 32-bit Windows executables. On macOS that means a Wine-based layer, in practice CrossOver now that Whisky is discontinued. I found **no specific report of Union 1.0m or its plugins running on macOS/Apple Silicon**: no confirmation either way. OpenGothic runs natively on macOS (Metal) but explicitly cannot run Union.

### Cited Findings
- OpenGothic README: Windows (DX12/Vulkan), Linux (Vulkan) and **macOS (Metal)**. Union is "32 bit and Windows only, not possible". Ikarus/LeGo support is partial ("ongoing efforts"). DX11 renderer, AST SDK and Ninja are unsupported. Plain-script content mods and retextures work. G2 NotR assets are required. MIT license. [GitHub – Try/OpenGothic](https://github.com/Try/OpenGothic)
- Union includes the 4 GB LAA memory patch. [RPGRussia](https://rpgrussia.com/resources/gothic-1-2-union.4209/)
- GD3D11 is x86, needs a D3D11 GPU and the VC++ x86 runtime, and documents a Wine/DXVK path (Fedora 42). [kirides/GD3D11](https://github.com/kirides/GD3D11)
- Whisky (Wine front end for Mac) ended development in April 2025. [AppleInsider](https://appleinsider.com/articles/25/04/16/whisky-development-ends-on-macos-to-help-wine-flourish), [TechSpot](https://www.techspot.com/community/topics/whisky-a-popular-wine-frontend-for-mac-gamers-is-no-more.291735/)
- CodeWeavers compatibility database (via search summary; the page returned 403): Gothic 2 played smoothly for the first hour on macOS Big Sur 11.6 with CrossOver 21 on a MacBook Pro 2017 (Intel). It does not work out of the box and needs manual install steps. [CodeWeavers – Gothic 2](https://codeweavers.com/compatibility/crossover/gothic-2)
- Older Wine bugs for vanilla Gothic: Vdfs32e.exe crash on Gothic 2 Gold (2017; a comment says it does not crash with SystemPack installed), D3D7 crashes, and dsound stutter (2007). [WineHQ Bug 37892](https://www.winehq.org/pipermail/wine-bugs/2017-May/468859.html), [WineHQ Bug 8761](https://list.winehq.org/pipermail/wine-bugs/2007-June/060050.html)
- Linux Proton guide (Apr 2022) for vanilla Gothic II: winetricks directmusic, Windows XP mode, remove dsound override. No mention of Union. [gamepretty guide](https://gamepretty.com/gothic-ii-gold-classic-how-to-play-on-linux/)
- Union plugins can be **built** on non-Windows hosts with msvc-wine and clang-cl through Patrix9999's CMake template. [Patrix9999/union-plugin-template](https://github.com/Patrix9999/union-plugin-template)

### Inferences
- Apple Silicon path: CrossOver (Wine with its 32-on-64 support, Rosetta 2 for x86 translation) is the only maintained option. Expect extra friction for a 32-bit game plus injected DLL hooks plus GD3D11 (DXVK→MoltenVK or D3DMetal). Debugging a hooking plugin under Wine on macOS is markedly harder: no Visual Studio debugger, so you rely on zSpy or Union console logs.
- An LLM sidecar (local model) can run natively on macOS and talk to the Wine-hosted game over localhost TCP. That is an advantage of the out-of-process design.
- Expect to keep a Windows PC or VM for iteration. UTM/Parallels on Apple Silicon run Windows on ARM, which emulates x86-32, so plausibly workable but unverified for Union.

### Gaps
- **No first-hand report found** of Union 1.0m, zParserExtender or GD3D11 running under CrossOver/GPTK on Apple Silicon (M1–M4). This needs hands-on testing.
- No ProtonDB data retrieved for Union-patched installs.

## 8. Engine source access and legal status

### Takeaway
I found **no official ZenGin source release or official engine SDK**. Everything in the Union, Ikarus and GD3D11 world is built on reverse engineering of the shipped executables. An AI-generated search summary claimed a partial G2 NotR source leak in the "late 2000s", but no primary source confirmed it, so treat it as an unverified rumour. The only officially sanctioned modding tooling remains the original Mod Development Kit (scripts and Spacer).

### Cited Findings
- Union SDK and Ikarus require reverse engineering (IDA) to get function addresses and conventions. [GMC – Union SDK](https://raw.githubusercontent.com/Gothic-Modding-Community/gmc/main/docs/zengin/union/sdk/index.md), [GMC – Ikarus CALL](https://gothic-modding-community.github.io/gmc/zengin/scripts/extenders/ikarus/functions/call/)
- The Wikipedia article on Gothic II contains no mention of a source-code leak or release. It mentions THQ Nordic console ports (2023, 2026) and Chronicles of Myrtana released on Steam "with permission from the Gothic II creators". [Wikipedia – Gothic II](https://en.wikipedia.org/wiki/Gothic_II)
- THQ Nordic's 2021 Steam patch shipped the community SystemPack 1.8, which shows tacit acceptance of executable-patching community tools. [PCGH thread](https://extreme.pcgameshardware.de/threads/gothic-1-update-patch-auf-steam-zum-geburtstag-fuehrt-mod-offiziell-ein.601831/)
- The Gothic Multiplayer project suffered a leak of *its own* source (cheats followed). This is unrelated to ZenGin. [GMC/search summary – Gothic 2 Online ModDB](https://www.moddb.com/mods/gothic-2-online)

### Inferences
- Legal posture: Union plugins patch the user's own copy of the game at runtime and distribute no THQ Nordic code. This is the same model the publisher has tolerated (SystemPack on Steam, Chronicles of Myrtana on Steam). The model gives no formal guarantee, and a commercial LLM product on top of it would need clearance.

### Gaps
- Two claims could not be verified from a primary source: "parts of the Gothic II NotR source leaked in the late 2000s" and "THQ Nordic forbids publication of internal demos and source code". Both came only from an AI search summary.
- Whether the gothic-api headers derive partly from leaked debug symbols or PDBs, or purely from reverse engineering, was not established.
