# OpenGothic as a base for LLM/AI-driven NPCs, extended mechanics and generated storylines (state as of 2026-10-10)

Method note: the facts below come from the live GitHub repo, GitHub API metadata, issues and discussions, and a shallow clone of `Try/OpenGothic` at commit `d2a94b4` (2026-10-08). File paths refer to that commit. Since PR #973 (merged 2026-08-24), the source tree is split into `common/` (engine and game), `game/` (a 152-line `main.cpp`) and `spacer/` (a new editor). Older articles and forks use the old `game/game/...` paths.

## 1. Architecture: language, renderer, game logic, NPC AI, dialogs, saves, and where to hook an external AI

### Takeaway
OpenGothic is a C++ reimplementation of the ZenGin game layer. It runs on Try's own Tempest graphics engine (Vulkan, DX12 and native Metal) and on ZenKit's Daedalus VM. All NPC behavior goes through one C++ `Npc` class, an `AiQueue` of typed actions, a perception system and a Daedalus-bound `GameScript`. These clean, readable C++ types give an external AI controller clear entry points. Nothing is exposed as a plugin API, though, so any AI integration has to be compiled into a fork.

### Cited Findings
- Language and size: C++. At commit `d2a94b4` (2026-10-08), `common/` holds about 72.7k lines of C++ in 315 files, and `spacer/` (the editor) about 9.4k lines in 89 files. There are 168 shader files. Line counts were measured on the clone. — [Try/OpenGothic](https://github.com/Try/OpenGothic)
- Submodule dependencies: `lib/Tempest` (Try's graphics/app engine), `lib/ZenKit` (GothicKit asset and Daedalus library), `lib/bullet3` (physics), `lib/dmusic` (GothicKit DirectMusic), `lib/TinySoundFont`, `lib/rapidjson`. `miniz` and `edd-dbg` are also vendored. — [.gitmodules](https://github.com/Try/OpenGothic/blob/master/.gitmodules)
- Renderer: Tempest has `vulkan`, `directx12` and `metal` backends under `Engine/gapi`. The Metal backend is native: `metal/mtdevice.mm`, `mtswapchain.mm`, `mtaccelerationstructure.cpp` and others, with `metal-cpp` and `spirv_cross` in thirdparty. It does not go through MoltenVK. — [Try/Tempest Engine/gapi](https://github.com/Try/Tempest/tree/master/Engine/gapi)
- Officially supported systems: Windows (DX12, Vulkan), Linux (Vulkan), macOS (Metal). The README says the engine is "designed to utilize features of modern graphics hardware and APIs, like mesh shaders or ray tracing" and warns not to expect it to run well on low-end GPUs. — [README](https://github.com/Try/OpenGothic/blob/master/README.md)
- Script layer: `common/game/gamescript.cpp` (3,423 lines) binds about 202 Daedalus engine externals with `bindExternal("npc_isdead", &GameScript::npc_isdead)` and similar calls. `bindExternal` is a thin wrapper around `zenkit::DaedalusVm::register_external`. The class also exposes `dialogChoices(...)`, `exec(DlgChoice, player, npc)`, `invokeState(...)`, `invokeCond(...)`, `aiOutput(...)` and `aiOutputSvm(...)`. — [gamescript.h](https://github.com/Try/OpenGothic/blob/master/common/game/gamescript.h), [gamescript.cpp](https://github.com/Try/OpenGothic/blob/master/common/game/gamescript.cpp)
- The VM is created from the compiled `.DAT` only (`Gothic::loadScript(datFile, lang)` and `GameScript::createVm`). I found no in-engine Daedalus source compiler or `-zreparse` equivalent. When Ikarus is detected, the VM is created with `IGNORE_CONST_SPECIFIER`. — [gamescript.cpp](https://github.com/Try/OpenGothic/blob/master/common/game/gamescript.cpp), [gothic.cpp](https://github.com/Try/OpenGothic/blob/master/common/gothic.cpp)
- NPC implementation: `common/world/objects/npc.cpp` has 4,826 lines. The public API includes `aiPush(AiQueue::AiAction&&)`, `startState(ScriptFn, wp[, endTime, noFinalize])`, `startDialog(Npc&)`, `setPerceptionTime`, `setPerceptionEnable(PercType, fn)`, `perceptionProcess(...)`, `excRoutine(callback)`, `setTarget(Npc*)` and `setProcessPolicy(NpcProcessPolicy)`. Internally it runs `implAiTick`, `nextAiAction(AiQueue&, dt)`, `tickRoutine()` and `implGoTo`. — [npc.h](https://github.com/Try/OpenGothic/blob/master/common/world/objects/npc.h)
- `NpcProcessPolicy` has four values: `Player`, `AiNormal`, `AiFar`, `AiFar2`. `WorldObjects::tick` assigns AiFar/AiFar2 to distant NPCs, which handles AI level of detail. — [constants.h](https://github.com/Try/OpenGothic/blob/master/common/game/constants.h), [worldobjects.cpp](https://github.com/Try/OpenGothic/blob/master/common/world/worldobjects.cpp)
- AI queue: `AiQueue::AiAction` holds `{act, target, victim, point, item, func, i0, i1, s0, ...}`. Static factories cover the Daedalus `AI_*` command set: `aiGoToPoint`, `aiGoToNpc`, `aiGoToNextFp`, `aiTurnToNpc`, `aiStartState`, `aiPlayAnim`, `aiWait`, `aiUseMob`, `aiUseItem`, `aiTeleport`, `aiDrawWeapon`, `aiReadySpell`, `aiAttack`, `aiFlee`, `aiDodge`, `aiProcessInfo`, `aiOutput`, `aiOutputSvm` and others. The queue is serializable (`save`/`load`). — [aiqueue.h](https://github.com/Try/OpenGothic/blob/master/common/world/aiqueue.h)
- Dialog output is behind an abstract interface, `AiOuputPipe`, with methods `output(npc,text)`, `outputSvm`, `outputOv`, `printScr`, `isFinished` and `close`. Users include `ui/dialogmenu.cpp` (606 lines), `GameSession`, `World` and `Npc`. `GameSession::dialogExec(DlgChoice, player, npc)` runs a chosen info. — [aiouputpipe.h](https://github.com/Try/OpenGothic/blob/master/common/game/aiouputpipe.h), [gamesession.cpp](https://github.com/Try/OpenGothic/blob/master/common/game/gamesession.cpp)
- Waynet and pathfinding: `WayMatrix` offers `findWayPoint(pos, filter)`, `findWayPoint(name)` and `wayTo(begin..., end) -> WayPath`. In v0.92 (May 2026), Fight-AI "can utilize pathfinding to avoid walls". — [waymatrix.h](https://github.com/Try/OpenGothic/blob/master/common/world/waymatrix.h), [v0.92 notes, Discussion #936](https://github.com/Try/OpenGothic/discussions/936)
- Threading: AI and NPC logic run serially in `WorldObjects::tick`. Only animation updates and some queries such as `isTargeted` use `Workers::parallelTasks/parallelFor`. — [worldobjects.cpp](https://github.com/Try/OpenGothic/blob/master/common/world/worldobjects.cpp)
- Saves use OpenGothic's own zip-based archive (`miniz`, `Serialize::Version`, "First = 36, first zip-based version"). `Npc::save/load`, `saveAiState/loadAiState` and `GameSession::save` handle persistence. — [serialize.h](https://github.com/Try/OpenGothic/blob/master/common/game/serialize.h), [npc.cpp](https://github.com/Try/OpenGothic/blob/master/common/world/objects/npc.cpp)
- A debug console ("Marvin") has a table-driven command list in `common/marvin.cpp`, modeled on vanilla Marvin commands. — [marvin.cpp](https://github.com/Try/OpenGothic/blob/master/common/marvin.cpp)
- Try described the fight-AI path in 2021. `AI_Attack` resolves `FA_<situation>_<fight_tactic>` C_FightAI instances, and OpenGothic keeps an extra small move queue. He floated an `AI_Atack_v2(int explicit_move)` script API. — [Discussion #226](https://github.com/Try/OpenGothic/discussions/226)
- Since Aug–Sep 2026 an in-engine world editor ("spacer") is in development: "Ground work for spacer" #973 merged 2026-08-24, followed by vob tree, vob properties, an UndoStack (2026-09-08) and async world loading (2026-10-06). — [PR #973](https://github.com/Try/OpenGothic/pull/973), [commits](https://github.com/Try/OpenGothic/commits/master)

### Inferences
- Hook points for an external LLM controller, in order of invasiveness:
  1. **ZenKit `override_function`** on Daedalus functions. You can replace script-defined functions in C++, either stub functions you add to the mod scripts or existing ones such as `ZS_*` state loops, `B_Assess*` perception handlers or `DIA_*_Condition/Info`. This needs no new externals in the `.DAT`, and OpenGothic already uses the technique for Ikarus emulation.
  2. **Push `AiQueue::AiAction`s** through `Npc::aiPush` and `startState` from a C++ "director" that reads world state through `World`/`WorldObjects`/`Npc`. This gives you the full vanilla `AI_*` vocabulary: go to waypoint or NPC, use mob or item, attack, flee, play animation, output dialog.
  3. **Implement a custom `AiOuputPipe`** or extend `DialogMenu` to display free-text LLM lines. Vanilla `AI_Output` resolves voice and subtitle data by output-unit name, so generated lines need TTS plus a subtitle path. That is an engineering inference; I did not test it.
  4. **Add a new `NpcProcessPolicy`**, for example "ExternalAI", next to `Player`/`AiNormal`/`AiFar` to switch selected NPCs off script-driven AI.
- Because the AI tick runs on the main thread, any LLM call has to be asynchronous with results marshalled back into the tick. NPCs need a "waiting/thinking" state, for example `aiWait` or an idle animation, so they don't freeze the game.
- Story generation would need generated Daedalus content (C_INFO, quests, items) compiled offline with an external Daedalus compiler, because OpenGothic loads only compiled `.DAT`. The alternative is to synthesize content at runtime through the VM (`init_instance`, symbol manipulation). The second option is unexplored territory.

### Gaps
- I did not measure how easily a new external can be added. ZenKit's `register_external` returns silently if the symbol is not in the compiled `.DAT`, so new externals need a compiler that knows them (see the ZenKit section).
- I could not find documentation of OpenGothic's internal architecture beyond the code. The wiki was last edited in May 2022.

## 2. Compatibility: Gothic 1 / Gothic 2 NotR, big mods, Ikarus/LeGo, Union, zParserExtender

### Takeaway
Vanilla Gothic 2 NotR is fully completable on OpenGothic. Gothic 1 runs but is officially unsupported and has a long open bug list. Content-only mods mostly work: L'Hiver has been played to the end on an M2 Mac. Ikarus/LeGo emulation is real but early: Archolos boots, and community forks are pushing it into early Chapter 1. LeGo's HookEngine is unimplemented, and Union, Ninja, AST SDK and DX11-renderer mods cannot work.

### Cited Findings
- README (current): "The original game has been completely replicated; you can complete both the main quest and the addon." — [README](https://github.com/Try/OpenGothic/blob/master/README.md)
- README: "Development is focused on Gothic 2 and new features are not tested for compatibility with Gothic 1. While Gothic 1 is not officially supported, pull requests that fix Gothic 1 — and general — bugs are welcome." Command-line flags `-g1`, `-g2c` (Classic) and `-g2` exist. — [README](https://github.com/Try/OpenGothic/blob/master/README.md)
- The Gothic 1 bug tracking discussion (opened 2024-05-13) lists more than 20 G1 issues: looting equipped weapons, corpses on objects, G2-style trading, a 2h run-attack shown as a kick, magic damage and others. In Aug 2026 it was merged with "OpenGothic (Gothic 1) big bug report #944". — [Discussion #622](https://github.com/Try/OpenGothic/discussions/622)
- README "What's working?": "Content mods (retexture/reworld/animations) that only rely on regular scripting and do not use memory hacking." "What's not?": Ikarus/LeGo (ongoing work), Union ("32 bit and Windows only, not possible"), DX11 Renderer, AST SDK, Ninja. — [README](https://github.com/Try/OpenGothic/blob/master/README.md)
- Union, Try in 2021: Union plugins are 32-bit x86 Windows DLLs and OpenGothic is 64-bit only "and there is no plans of downgrading". WoW64 is per-process, so a Win32 DLL cannot load into a Win64 process. Loading arbitrary DLLs is a security risk. Union exposes reverse-engineered vanilla classes and direct memory, which makes no sense against OpenGothic's different object layout, and "basically we would have to provide build-in wine". — [Issue #195](https://github.com/Try/OpenGothic/issues/195)
- Ninja: Try (2021-12) said "it's not just a loader, but a proper DMA wrapped with *.dll. Probably won't work." kirides described Ninja as an extension that loads scripts from VDFs, adding that most Ninja patches rely on Ikarus/LeGo engine hooks. — [Issue #195](https://github.com/Try/OpenGothic/issues/195)
- Ikarus/LeGo design: the tracking issue (opened 2021-12-27, still open) plans a fake 32-bit "Win32-like" heap. Address ranges: 0x1000–0x80000000 user space for MEM_Alloc, 0x80000000–0xC0000000 engine-mapped data. Only high-level functionality is provided, "support won't be bullet-proof". — [Issue #231](https://github.com/Try/OpenGothic/issues/231)
- Implementation (2026-10): `common/game/compatibility/` contains `directmemory.cpp` (1,806 lines), `mem32.cpp` (478), `mem32instances.cpp`, `cpu32.cpp` (270; a tiny x86 interpreter for ASMINT stdcall, thiscall and cdecl) and `phoenix.cpp`. The layer activates when the script defines `Ikarus_Version`, `MEM_InitAll`, `MEM_ReadInt`, `MEM_WriteInt`, `_@` and `_^`, and logs "DMA mod detected: LeGo" when `LeGo_Version` is present. — [directmemory.cpp](https://github.com/Try/OpenGothic/blob/master/common/game/compatibility/directmemory.cpp)
- In `directmemory.cpp`, LeGo's `HookEngineI` is overridden with a stub that logs "not implemented call [HookEngineI]". `MEM_GothOptExists("INTERNAL","UnionActivated")` returns true to "Fake Union, so ikarus won't setup crazy workarounds for unpatched G2". `GetUserNameA` is emulated to return "OpenGothic". `_FF_Hook` (LeGo FrameFunctions) is ticked through a "TODO: propper hook-engine". — [directmemory.cpp](https://github.com/Try/OpenGothic/blob/master/common/game/compatibility/directmemory.cpp)
- Ikarus work landed in bursts: "Ikarus: 32bit memory subsystem in progress" (2025-10-30), "basic support for stdcall/thiscall" (2025-11-04), PR "Ikarus dev" #843 (merged 2025-12-01), "consolidate Ikarus/LeGo DMA in a single class" (2025-12-03), more through 2025-12-16. GitHub commit search found 11 Ikarus commits since 2025-01-01. — [PR #843](https://github.com/Try/OpenGothic/pull/843), [commits](https://github.com/Try/OpenGothic/commits/master)
- 2025-11-12: Try found that Ikarus "also assumes `reinterpret_cast` for scriptable-variables", for example casting `HLP_GETNPC` results to a raw `oCNpc` struct to flip bitfields. That case is not yet handled. — [Issue #231](https://github.com/Try/OpenGothic/issues/231)
- 2026-09-12: Try rejected a PR that restored Ikarus bindings after loading a save. Repro: Archolos dialogue and face-animation calls fail after save and reload. His reason: "There is no software design on save/load in DMA environment yet… first priority would be to have DMA work cleanly in use-cases without save/load. Part of it will include: implement `reinterpret_cast`, develop unit-tests, custom-compiler (for test scripts)." — [PR #987](https://github.com/Try/OpenGothic/pull/987)
- Chronicles of Myrtana: Archolos. 2023-08: it "can launch… only early game is playable". 2025-07, Try: the emulation is "enough to start the game and talk with a few characters on the ship, but not much more", and Archolos also uses custom Windows-only DLLs (KmLib.dll exports for achievements, music override, menus). A user got it running on an M2 Mac from the nightly build, but "you can't save at all (except for F5)". — [Issue #231](https://github.com/Try/OpenGothic/issues/231), [Issue #780](https://github.com/Try/OpenGothic/issues/780)
- Community fork `Lu2cz/OpenGothic`, "Archolos on macOS", targets an Apple M2 with Archolos 1.2.11. Furthest player-reported progress (2026-09-25/26): "early Chapter 1, through Riordian's scroll-transcription sidequest". It adds: "A continuous clean playthrough and campaign completion remain unverified." The fork is developed with an `AGENTS.md` (AI-agent workflow). — [Lu2cz/OpenGothic](https://github.com/Lu2cz/OpenGothic)
- L'Hiver: several L'Hiver bugs were fixed in 2025: tomb lid, sewer key, monster HP. PR #953 (July 2026) is still open for "Monster HP sometimes increases during a fight". A user wrote on 2026-02-13: "i just finished Gothic 2 Lhiver 1.6 on Mac Air M2". — [Issue #812](https://github.com/Try/OpenGothic/issues/812), [PR #953](https://github.com/Try/OpenGothic/pull/953), [Issue #231 comment](https://github.com/Try/OpenGothic/issues/231)
- Remaster Mod and NotR Remaster: several issues in 2025 (startup crash with Remaster scripts, a C_FIGHTAI member count mismatch, canyon teleport). One remains open: "Can't get back from Jharkendar to Khorinis" (#852, 2025-12-29). — [Issue #852](https://github.com/Try/OpenGothic/issues/852), [Issue #742](https://github.com/Try/OpenGothic/issues/742)
- The Nightmare mod does not run (opened 2025-04, still open): broken or empty menu bytecode, "can be due to Ikarus/LeGo or broken mod". — [Issue #771](https://github.com/Try/OpenGothic/issues/771)
- Velaya, asked in 2023: there is no way to check compatibility except trying. Try: "For Ikarus/LeGo we have logging… no capacity to test and check every mod." — [Issue #435](https://github.com/Try/OpenGothic/issues/435)
- Union-related assets: ZenKit added "support compressed VDF Union files" (PR #124, 2026-03-15), so mod archives packed in Union's compressed VDF format can be read even though Union code cannot run. — [ZenKit commits](https://github.com/GothicKit/ZenKit/commits/main)
- SystemPack: OpenGothic reads `system/SystemPack.ini` options such as FPS_Limit, VerticalFOV, HideFocus, interface scale and bar visibility, and Try prefers to replicate SystemPack behaviors exactly. — [gothic.cpp](https://github.com/Try/OpenGothic/blob/master/common/gothic.cpp), [Issue #963](https://github.com/Try/OpenGothic/issues/963)
- GothicStarter `[OPTIONS]` section of mod `.ini` files is not implemented (open issue, 2026-08-09). — [Issue #965](https://github.com/Try/OpenGothic/issues/965)

### Inferences
- **Union plugins cannot work on OpenGothic.** The maintainer confirms it and the reasons are architectural: 32-bit x86 DLLs, a 64-bit process, and vanilla memory layout assumptions. Anything currently built as a Union plugin, including AI or LLM plugins, would have to be rewritten as C++ inside an OpenGothic fork.
- **zParserExtender-style features are neither supported nor planned.** Issues, discussions and code contain no mention of zParserExtender; it is a Union plugin. Its main features are injecting and compiling extra Daedalus scripts at load time and adding externals. OpenGothic has no in-engine script compiler, although Try mentioned a "custom-compiler (for test scripts)" as a future DMA testing step in Sep 2026.
- Returning 2.0 / New Balance, Velaya and Odyssey: I found no evidence that they work. Large modern G2 mods tend to depend on Ikarus/LeGo and often Union, so they should be assumed broken or partially working until tested.
- Ikarus/LeGo save/load has no design yet, and HookEngine (used by most LeGo features) is a stub. Script-heavy mods can therefore be expected to be unreliable through 2027.

### Gaps
- I found no published compatibility matrix for big mods. Status for Returning, Velaya, Odyssey and Dark Saga could not be verified from primary sources; GitHub search returned no matching mod-specific issues.
- No exact Ikarus/LeGo API coverage percentage exists. The checklist in #231 covers only part of it, and `Lu2cz/OpenGothic` has a "LeGo/Ikarus coverage audit" document I did not read.

## 3. Extensibility: plugin/scripting/mod API, maintainer stance, cost of maintaining a fork, license

### Takeaway
OpenGothic has no plugin system, no embedded scripting language (no Lua, JS, Python or Squirrel), no networking, and no stable mod API beyond vanilla Daedalus externals. The maintainer is open in principle to a coherent extended script API but prioritizes mod compatibility and faithful vanilla behavior. In practice, AI features mean maintaining a C++ fork of an MIT-licensed codebase of about 80k lines that is actively churned by one main developer.

### Cited Findings
- Issue search on 2026-10-10: zero issues with "plugin", "lua", "mod api", "zparserextender" or "LLM" in the title. One "scripting" issue (#39, "Scripting: api-complete gothic2", 2020, open). Two "multiplayer" issues. — [GitHub issues search](https://github.com/Try/OpenGothic/issues?q=lua)
- Discussions: no Lua discussion. The closest is "Some additional features, mostly for modding" (2021). — [Discussion #226](https://github.com/Try/OpenGothic/discussions/226)
- Code grep of `common/` for Python, Lua, sockets or HTTP found only URLs in comments. There is no embedded scripting runtime or network stack. — [Try/OpenGothic](https://github.com/Try/OpenGothic)
- Try on extending the script API (2021-12-08): "That's is a nice idea to expand script api. Also would be nice to establish some general view: do we want to keep compatibility with vanilla? (Thru Ikarus/Union)… establish overall view of api would be nice — better then soup of different suggestions." He suggested "a proper UI library, with ListView/Canvas/Timer". — [Discussion #226](https://github.com/Try/OpenGothic/discussions/226)
- Try on multiplayer and scripting (2021-12-22): "Basic idea is: provide some api to scripts and let them do the hard work", possibly modeled on Gothic Online or GMP functions. Headless server: "Not really, but we can change it." Others in the thread pushed V8/JavaScript. — [Issue #230](https://github.com/Try/OpenGothic/issues/230)
- Try, 2026-02-18: "This is not something I'm actively working on. At this time there are things such as mod-compatibility and look-and-feel have priority." — [Issue #230](https://github.com/Try/OpenGothic/issues/230)
- Try on new gameplay features (Aug 2026, Union-style quick-loot PR): "Exact implementation is desirable. It helps to avoid fragmentation, when one engine is very different to the other." He would merge the SystemPack-style option first, then consider the Union-style one. — [Issue #963](https://github.com/Try/OpenGothic/issues/963)
- Try in 2022 on a community proposal to hook and patch VM code with C++ "plugins" instead of emulating Ikarus: "replacing all non-conformant in all mods is definitely not the way to go… providing environment (fake VA), so lowest level stuff, like `_@` and `_^` can work is preferable." — [Issue #231](https://github.com/Try/OpenGothic/issues/231)
- A 2022 commenter compared OpenGothic with OpenMW and Daggerfall Unity, which favored scriptability of all mechanics for new mods over legacy mod compatibility. That is community opinion, not a maintainer decision. — [Issue #195](https://github.com/Try/OpenGothic/issues/195)
- The license is MIT, and Tempest and ZenKit are MIT too. — [GitHub repo metadata](https://github.com/Try/OpenGothic/blob/master/LICENSE), [Tempest](https://github.com/Try/Tempest), [ZenKit](https://github.com/GothicKit/ZenKit)
- CONTRIBUTING.md is outdated. It still refers to "MoltenTempest" and "ZenLib", and to wiki pages ("Feature-list", "Daedalus-scripts") last edited in 2020–2021. — [CONTRIBUTING.md](https://github.com/Try/OpenGothic/blob/master/CONTRIBUTING.md), [Wiki](https://github.com/Try/OpenGothic/wiki)
- Churn: 493 commits in 2023, 296 in 2024, 307 in 2025, and 250 in 2026 up to Oct 10 (GitHub commit search, queried 2026-10-10). v0.88→v0.92: 139 commits, 133 files, +4,290/−2,557 lines. v0.92→master (May–Oct 2026): 121 commits touching 300+ files (API cap), driven by the Aug 2026 restructure into `common/` + `spacer/`. — [compare v0.88...v0.92](https://github.com/Try/OpenGothic/compare/v0.88...v0.92), [compare v0.92...master](https://github.com/Try/OpenGothic/compare/v0.92...master)
- Tempest is a moving dependency: "Update Tempest" commits landed on 2026-10-03, 10-05 and 10-07 (twice). — [commits](https://github.com/Try/OpenGothic/commits/master)
- Try does not accept money (2024-10): "I'm not taking money/donates for this project." — [Issue #689](https://github.com/Try/OpenGothic/issues/689)

### Inferences
- An AI or LLM layer would almost certainly have to live in a fork or as a large out-of-tree patch set. An optional, cleanly designed generic scripting or hook API ("let scripts do the hard work") might be upstreamable. LLM-specific gameplay changes that diverge from vanilla are unlikely to be accepted, given Try's "exact implementation" and anti-fragmentation stance.
- Fork maintenance cost: moderate to high. The code base is compact and readable (about 80k lines with clear classes), but upstream churns at about 250–500 commits per year, did a full directory restructure in Aug 2026, and pulls Tempest updates frequently. To reduce conflicts, keep AI code in separate files behind a narrow set of hooks (`Npc` tick/aiPush, `GameScript` overrides, `AiOuputPipe`, `WorldObjects::tick`) and rebase often.
- A practical design is to embed a scripting runtime (Lua or JS) in the fork, or an IPC/HTTP bridge to an out-of-process LLM agent, and expose world queries plus `AiQueue` actions to it. Upstream has nothing like this, so it would be new work.

### Gaps
- I did not find any formal statement on plugin or DLL loading on 64-bit OpenGothic. Try's 2021 security concern about loading arbitrary DLLs suggests reluctance.
- Discord discussions were not accessible, and the maintainer may have said more there.

## 4. Forks and derivative projects: multiplayer, VR, mobile and console ports, scripting or AI forks

### Takeaway
I found no multiplayer, scripting or LLM fork of OpenGothic as of Oct 2026. The active derivatives are platform ports (PS4, iOS, Android, AppImage), an Archolos-on-macOS compatibility fork, and a small C# port. The VR effort (Gothic-UnZENity) is a separate Unity reimplementation that shares ZenKit, not OpenGothic code.

### Cited Findings
- Fork landscape (2026-10-10): 140 forks, all with three stars or fewer. The most active are Solessfir/OpenGothic and tryk016/OpenGothic (pushed 2026-10-09; both upstream contributors), Lu2cz/OpenGothic ("Archolos on macOS", pushed 2026-10-01) and MacSourcePorts/OpenGothic (stale since 2023). — [Forks](https://github.com/Try/OpenGothic/forks)
- PS4: mikolajmikolajczyk started a port in Aug 2026 and ported Mesa to PS4 instead of writing a custom graphics backend. On 2026-09-15 he released `opengothic-ps4-v0.92-r1`, "40-50 fps on highest settings", keyboard and mouse only. Try raised possible Sony NDA concerns. — [Discussion #961](https://github.com/Try/OpenGothic/discussions/961), [orbis-ports release](https://github.com/orbis-ports/OpenGothic/releases/tag/opengothic-ps4-v0.92-r1)
- Android: "Add basic Android APK integration" (#1001) merged upstream on 2026-10-07. It is an ARM64 NativeActivity APK with "touch controls, controller input and game-file importing… not included yet". Ray tracing and mesh shading are disabled. — [android/README.md](https://github.com/Try/OpenGothic/blob/master/android/README.md)
- iOS: upstream CMake has an `IOS` branch (deployment target 15.0, Xcode project generation) and `ui/touchinput.cpp`. Try (2026-01-09) said users must build it themselves, since the App Store "will never accept us". Community repos `tryk016/opengothic-ios` (7★, Jul–Aug 2026) and `Oliver472/opengothic-ios` (Aug 2026; "patches… that take the half-finished iOS port… to a playable state") exist. — [CMakeLists.txt](https://github.com/Try/OpenGothic/blob/master/CMakeLists.txt), [Issue #863](https://github.com/Try/OpenGothic/issues/863), [Oliver472/opengothic-ios](https://github.com/Oliver472/opengothic-ios)
- C# port: `rds1983/OpenGothicSharp`, "C# Port of OpenGothic" (3★, created 2025-12, last push 2026-05). — [OpenGothicSharp](https://github.com/rds1983/OpenGothicSharp)
- Linux packaging: an unofficial AppImage (`pkgforge-dev/OpenGothic-AppImage`), plus Debian packaging and GitHub Actions merged in Jan 2026 (#878) and an AUR package. — [pkgforge-dev/OpenGothic-AppImage](https://github.com/pkgforge-dev/OpenGothic-AppImage), [README](https://github.com/Try/OpenGothic/blob/master/README.md)
- Multiplayer: none exists on OpenGothic. Issue #230 (2021) is open, and Try said in Feb 2026 that he is not working on it. Gothic multiplayer projects target the original engine instead: GMP Classic with Lua, `skejt23/GothicMultiplayer` (21★), and G2O gamemodes. — [Issue #230](https://github.com/Try/OpenGothic/issues/230), [skejt23/GothicMultiplayer](https://github.com/skejt23/GothicMultiplayer)
- LLM precedent on the original engine: `LucyJohnson-lua4life/llm-quest-generator`, "LLM Quest Generator Micro-Service for the Gothic 2 Multiplayer" (last push 2025-07). It is not OpenGothic-based, and I did not inspect it in depth. — [llm-quest-generator](https://github.com/LucyJohnson-lua4life/llm-quest-generator)
- VR: Gothic-UnZENity (formerly GothicVR, now redirecting to `Gothic-Unity-Project/Gothic-Unity`) is "Community rebuild of classical Gothic 1 and 2 in Unity engine. Currently focusing on a native VR implementation". GPL-3.0, 33★, latest release v0.3.0-alpha (2026-06-19). It loads original assets at runtime through ZenKit/ZenKitCS. — [Gothic-Unity](https://github.com/Gothic-Unity-Project/Gothic-Unity), [GothicVR README](https://github.com/GothicVRProject/GothicVR), [mixed-news article](https://mixed-news.com/en/from-cult-classic-to-vr-how-five-developers-are-bringing-gothic-to-vr/)

### Inferences
- An OpenGothic-based LLM/AI fork appears to be unclaimed territory: no prior art to reuse, but no competing effort either.
- Lu2cz's AI-agent-driven Archolos fork is an example of the "LLM-assisted engine development" workflow on this codebase.

### Gaps
- GitHub repository search does not index every fork's README, so a small scripting or AI fork could exist undetected. Discord and worldofplayers threads were not searched.

## 5. Platform support, especially macOS on Apple Silicon

### Takeaway
macOS is a first-class target. The renderer is native Metal through Tempest, not MoltenVK. Universal 2 builds of v0.92 are distributed through Mac Source Ports (built 2026-05-19), and source builds need only Homebrew `glslang` plus CMake. Users report playing full mods on M2 MacBooks. Windows (DX12/Vulkan) and Linux (Vulkan) are primary platforms; Android, iOS and PS4 are early.

### Cited Findings
- README macOS build steps: `brew install glslang`, clone with `--recurse-submodules`, `cmake -H. -Bbuild -DCMAKE_BUILD_TYPE:STRING=RelWithDebInfo`, `cmake --build ./build --target Gothic2Notr`. Game data is auto-detected in `~/Library/Application Support/OpenGothic`. — [README](https://github.com/Try/OpenGothic/blob/master/README.md)
- Mac Source Ports offers OpenGothic "Version 0.92", "Build date: May 19, 2026", "Universal 2 for Apple Silicon and 64-Bit Intel Macs". — [Mac Source Ports – Gothic II](https://www.macsourceports.com/game/gothic2)
- The Metal backend is native code: `mtdevice.mm`, `mtswapchain.mm`, `mtaccelerationstructure.cpp`, plus `metal-cpp` and `spirv_cross` in Tempest thirdparty. — [Tempest gapi/metal](https://github.com/Try/Tempest/tree/master/Engine/gapi/metal)
- macOS-specific fixes and issues: Metal validation crash fixed by gating VSM 3D storage texture allocation (#794, Aug 2025). Open PR "Fix macOS InstanceStorage upload wait hang" (#943, May 2026). An M1 user's launch failure (Jan 2026) came down to the `DYLD_LIBRARY_PATH`/`libTempest.dylib` path and data placed in the wrong Application Support folder; Try recommended `Gothic2Notr.sh`. — [PR #794](https://github.com/Try/OpenGothic/pull/794), [PR #943](https://github.com/Try/OpenGothic/pull/943), [Issue #865](https://github.com/Try/OpenGothic/issues/865)
- A user finished L'Hiver 1.6 on a MacBook Air M2 (2026-02-13). A user ran Archolos on an M2 from the nightly build in Jul 2025, with heavy bugs. — [Issue #231](https://github.com/Try/OpenGothic/issues/231), [Issue #780](https://github.com/Try/OpenGothic/issues/780)
- Windows: DX12 or Vulkan (`-dx12` flag). Linux: Vulkan, with `.deb` packages, AUR and a NixOS flake. — [README](https://github.com/Try/OpenGothic/blob/master/README.md)
- CI: GitHub Actions; AppVeyor was removed on 2026-10-03. — [commits](https://github.com/Try/OpenGothic/commits/master)
- Distribution: GOG ships OpenGothic as the default build of Gothic 2 Gold (the "Vanilla" branch in GOG Galaxy), announced 2025-10-02. — [Discussion #807](https://github.com/Try/OpenGothic/discussions/807)

### Inferences
- An Apple Silicon Mac is a good development host for an OpenGothic AI fork. The build is native, with no Wine or Rosetta, unlike the original engine plus Union, which on macOS requires Wine or CrossOver and 32-bit x86 translation.
- Ray-tracing features on Metal probably depend on Apple GPU support (an `mtaccelerationstructure` exists). I did not verify which RT features are enabled on M-series chips.

### Gaps
- No official signed or notarized `.app` from upstream; Mac users rely on Mac Source Ports (open request #780).
- I found no verified FPS numbers for Apple Silicon.

## 6. Project health: activity, releases, contributors, bus factor, community, documentation

### Takeaway
The project is very active in 2026, with commits nearly daily and two releases in the last 10 months, and it gained legitimacy through GOG distribution. It is still essentially a one-person project: Try wrote about 3,258 of roughly 3,500 attributed commits. Releases are all tagged "pre-release", and the documentation is stale.

### Cited Findings
- Repo stats (2026-10-10): 1,616 stars, 140 forks, 137 open issues+PRs, created 2019-01-11, last push 2026-10-08, MIT, C++. — [Try/OpenGothic](https://github.com/Try/OpenGothic)
- Contributors: 48 including anonymous. Top: Try 3,258 commits, thokkat 73, versable 13, Nindaleth 12, mmind 9, lmichaelis 8, tryk016 7. — [Contributors](https://github.com/Try/OpenGothic/graphs/contributors)
- Recent activity (Oct 2026): commits by Try, thokkat, tryk016 and Solessfir between 2026-10-01 and 10-08 (world mesh memory release, Android APK, editor async loading, `Ai_ReadySpell` fix). — [commits](https://github.com/Try/OpenGothic/commits/master)
- Releases, all flagged prerelease: v0.92 / `opengothic-v1.0.3756` (2026-05-17), `opengothic-v1.0.3549` (2025-12-23), `opengothic-v1.0.3010` (2024-12-23), `opengothic-v1.0.2207` (2023-06-27). Tags v0.85 through v0.92 exist. — [Releases](https://github.com/Try/OpenGothic/releases)
- v0.92 highlights: camera reworked to match the original (#894), Fight-AI rework "more similar to original gothic", fight-AI pathfinding, an asymmetric NPC collision model, sound occlusion, `.ogg` support "for sake of mods", night ambience rework. — [Discussion #936](https://github.com/Try/OpenGothic/discussions/936)
- Roadmap signal (2026-09-21, Try's "Out of box experience" issue): save directory separation, ini hierarchy (Gothic.ini, SystemPack.ini, "DMA from LeGo"), installation discovery, a possible installer and code signing. — [Issue #992](https://github.com/Try/OpenGothic/issues/992)
- Community: the README's Discord badge (invite G9XvcFQnn6) resolves to the "Gothic Modding Community" server, about 1,267 members and about 292 online per the Discord invite API on 2026-10-10. — [Discord invite API](https://discord.com/api/v9/invites/G9XvcFQnn6?with_counts=true)
- Documentation: the wiki has seven pages. The last edit was 2022-05-27, and "Feature-list" dates from 2020-07. — [Wiki](https://github.com/Try/OpenGothic/wiki)

### Inferences
- Bus factor ≈ 1. Try authors more than 90% of commits and makes the design decisions, though a small set of regular contributors appeared in 2025–2026 (thokkat, Solessfir, tryk016, versable). The MIT license means a fork can survive if upstream stalls, but the Tempest engine is also Try-maintained.
- Health is good for an upstream you can fork from. Long-term API stability is not promised, and the project is still at "0.9x".

### Gaps
- No official 1.0 roadmap or date. Discord activity specific to OpenGothic could not be measured, because the server is shared with the general modding community.

## 7. Related library: ZenKit (GothicKit, formerly phoenix) and its Daedalus VM

### Takeaway
ZenKit, maintained mostly by lmichaelis and MIT-licensed, provides the asset parsers and the Daedalus VM that OpenGothic embeds. The VM can register C++ externals for symbols declared in the compiled script, override any script function with a C++ callback, intercept unknown externals, and call script functions from C++. Those are exactly the hooks an AI layer needs. It is also used by the Unity/VR project through C# bindings.

### Cited Findings
- ZenKit: "A re-implementation of file formats used by the early 2000's ZenGin", MIT, 66★, 21 forks, 16 open issues, last push 2026-09-03. Latest tagged release v1.3.0 (2024-09-20), with commits continuing into 2026: Union compressed VDF support (2026-03-15), ModelScript parsing fixes (2026-01-17), MRM v0x805 (2026-05-09), a G2 alpha_test field (2026-09-03, by Try). Top contributors: lmichaelis 1,006 commits, Try 9. — [GothicKit/ZenKit](https://github.com/GothicKit/ZenKit), [Releases](https://github.com/GothicKit/ZenKit/releases)
- `DaedalusVm::register_external(name, std::function<R(P...)>)` type-checks the C++ signature against the script's external declaration. It returns silently if the symbol does not exist and throws "symbol is not external" if the symbol is a normal function. — [DaedalusVm.hh](https://github.com/GothicKit/ZenKit/blob/main/include/zenkit/DaedalusVm.hh)
- `override_function(name, callback)` replaces a script-defined (non-external) function with C++. A "naked" variant receives the raw `DaedalusVm&`. Other APIs: `register_default_external` (catch-all for unregistered externals), `register_access_trap`, `register_exception_handler`, `call_function<R>(name, args...)`, `init_instance` / `allocate_instance`, push/pop stack primitives, and the execution flags `ALLOW_NULL_INSTANCE_ACCESS` and `IGNORE_CONST_SPECIFIER`. — [DaedalusVm.hh](https://github.com/GothicKit/ZenKit/blob/main/include/zenkit/DaedalusVm.hh)
- OpenGothic's Ikarus layer already uses `vm.override_function("HookEngineI", ...)`, `"MEM_GothOptExists"` and others, which shows the override mechanism in production. — [directmemory.cpp](https://github.com/Try/OpenGothic/blob/master/common/game/compatibility/directmemory.cpp)
- In Nov 2025 Try filed a ZenKit issue ("Linking issue for non-trivial string use-case", ZenKit #114) during Ikarus work. ZenKit changes are therefore coupled to OpenGothic's DMA work. — [Issue #231](https://github.com/Try/OpenGothic/issues/231)
- ZenKitCS: C# bindings for ZenKit, MIT, 7★, last push 2026-05-09. Gothic-UnZENity/Gothic-Unity uses ZenKit through these bindings and a C API. — [ZenKitCS](https://github.com/GothicKit/ZenKitCS), [GothicVR README](https://github.com/GothicVRProject/GothicVR)
- A PS4 porter forked ZenKit, through Try's fork, for platform support (Aug 2026). — [Discussion #961](https://github.com/Try/OpenGothic/discussions/961)

### Inferences
- Adding brand-new script-callable functions such as `LLM_Ask(npc, prompt)` is easiest by declaring stub Daedalus functions in mod scripts and using `override_function` in the fork. True externals require a Daedalus compiler that knows the extern declarations; the community compilers are outside ZenKit and their status was not checked.
- Since ZenKit is MIT and engine-agnostic, an alternative strategy is to keep OpenGothic for rendering and simulation while building the AI and story layer on ZenKit's VM, for example generating and validating Daedalus content offline.

### Gaps
- Whether ZenKit can compile Daedalus source was not verified. Its scope is parsing and VM execution of compiled `.DAT`. Try's Sep 2026 mention of a "custom-compiler (for test scripts)" suggests it cannot today.

## 8. Performance, stability and gameplay divergences vs the original engine

### Takeaway
OpenGothic trades low-end compatibility for a modern renderer (mesh shaders, RT GI, SSAO, volumetric fog). It is stable enough to be GOG's default vanilla G2 build, but it still diverges from vanilla in many small behaviors. Current releases are actively reworking fight AI, camera and physics to match the original, and Gothic 1 has dozens of known deviations.

### Cited Findings
- README: designed to "utilize features of modern graphics hardware… mesh shaders or ray tracing… don't expect OpenGothic to run well on low-end or outdated graphics cards". RT is off by default on iGPUs (`-rt 1` to enable); there are also `-gi` (RT global illumination), `-ms` (meshlets) and `-aa` flags. "Rendering distance is not customizable." Vanilla graphics settings are remapped: Cloud Shadows → SSAO, Radial Fog → volumetric fog, Reflections → SSR. — [README](https://github.com/Try/OpenGothic/blob/master/README.md)
- The PS4 port reports 40–50 fps at the highest settings (2026-09). — [Discussion #961](https://github.com/Try/OpenGothic/discussions/961)
- v0.92 (2026-05) includes many fidelity fixes. Camera reworked "to look and feel like in original gothic". Fight-AI reworked "to be more similar to original gothic". "Remove caveat for minimal perception timings". "Reset monsters ai after time-skip". Bug fixes include T_HURT animation and a crash with `-gi 1`. — [Discussion #936](https://github.com/Try/OpenGothic/discussions/936)
- G1 divergences: trading works "the Gothic 2 way", the 2h run-attack shows a kick, equipped weapons cannot be looted, and `ExitGame` is immediate while videos are deferred, so outro videos are skipped. — [Discussion #622](https://github.com/Try/OpenGothic/discussions/622)
- Recent stability issues: crash on quick-load during dialogue (#956, Jul 2026, closed), crash on death (#984, open Sep 2026), and an item ground-collision crash in G1 (#994, closed). Saves taking too long was fixed by downscaling the preview (#916/#917, Apr 2026). — [Issue #956](https://github.com/Try/OpenGothic/issues/956), [Issue #984](https://github.com/Try/OpenGothic/issues/984), [PR #917](https://github.com/Try/OpenGothic/pull/917)
- Vanilla script bugs surface differently on OpenGothic. Try is considering engine-level workarounds for a common `Fight.dat` mod bug (ZenKit #107). — [Issue #780](https://github.com/Try/OpenGothic/issues/780), [Discussion #622](https://github.com/Try/OpenGothic/discussions/622)
- A 2021 user reported 20–40 fps (old builds; superseded). — [Discussion #106](https://github.com/Try/OpenGothic/discussions/106)

### Inferences
- For an AI-NPC project, the main stability risks are engine-side AI and perception divergences: timing of perceptions, routines and fight AI. These are being changed release to release, so any LLM layer that sits on top of `AiQueue` and perceptions should be tested against upstream changes on each rebase.
- On the plus side, a 64-bit modern process gives a lot of memory and CPU headroom for in-process AI glue such as JSON, HTTP clients and local inference runtimes. A 32-bit ZenGin + Union process does not.

### Gaps
- I found no independent, reproducible benchmarks comparing OpenGothic with vanilla, vanilla+DX11 renderer, or Apple Silicon fps in 2025–2026.
- No quantified count of known gameplay divergences in G2 NotR beyond the issue tracker (137 open issues+PRs).
