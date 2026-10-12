# LLM-driven quests, plot direction and event injection in shipped products and mods (2023–Oct 2026)

Scope: commercial games, middleware and community mods that use LLMs to generate quests, steer plot or inject events, with the most attention on mods for existing RPGs (closest analogue to an AI layer over Gothic 2 NotR on OpenGothic). Academic papers are excluded unless a product published its architecture. Research date: 2026-10-11. "Marketing-only" or "unverified" marks claims I could not check against code, docs or independent reporting. Several primary pages (Nexus Mods, Steam Workshop, Inworld docs, some press) blocked automated fetches (HTTP 403/429/login), so some facts come from search-engine snippets of those pages; these are marked "(snippet)".

---

## 1. Community mods for existing RPGs: what they actually do for quests, plot and events, and how they are built

### Takeaway
The most advanced practitioner work is in Skyrim mods. **CHIM** ships (a) an AI quest generator that turns LLM output into a constrained XML "storyboard", then into code against an idempotent, resumable quest API, and (b) a separate engine that encodes about 300 *vanilla* quests as beat/trigger graphs, so players can advance hand-written quests by talking freely. That second engine adds an LLM intent classifier on top of the existing quests and does not replace them. **SkyrimNet** and its plugins (**NarrativeEngine**, **IntelEngine**) are "director" layers: they pick from a small menu of event types (letter, visit, ambush, gossip, templated fetch/rescue/combat quests) on a cadence, and they treat vanilla content as untouchable canon. No mod I found replaces a game's authored main quest with generated plot. Everyone layers generated side content and event pressure on top of the authored quests.

### Cited Findings

**CHIM (formerly Herika), Skyrim SE/AE/VR. Server in PHP/PostgreSQL plus SKSE plugin**
- First released 2 Sep 2024. Features: "AI-generated and controlled quests", AI Narrator, long-term memory, "Oghma Infinium" lore RAG with 1900+ topics, and dynamically generated biographies that update the NPC's goals and mood during play — [Nexus Mods CHIM page (snippet)](https://www.nexusmods.com/skyrimspecialedition/mods/126330)
- "Menuless questing (beta)": progress through most vanilla quests by AI dialogue without opening the vanilla dialogue menu; "expanded AI Quest NPC generation"; page last updated 26 Sep 2026 — [Nexus Mods CHIM page (snippet)](https://www.nexusmods.com/skyrimspecialedition/mods/126330); [CHIM Menuless Questing video](https://www.youtube.com/watch?v=EWjjp981ODw)
- Each NPC action can be set to run automatically, require an in-game confirmation (Prisma UI), or be disabled. The LLM runs in "DwemerDistro" (a WSL bundle) or on another machine — [Dwemer Dynamics CHIM site](https://dwemerdynamics.com/chim/)
- **AI quest generator "SNQE" (Skyrim Narrative Quest Engine), added 24 Dec 2025.** Three-agent pipeline:
  - agent0 writes the next quest steps from context: memories, the player's diary, the location list, already spawned NPCs. Its system prompt says: "Scenarios are static so you CAN NOT spawn furniture/new locations", "You cannot instruct player directly, only give hints through NPCs", "Avoid conditionals/branchs", "Use only locations from lists", and to use a "teleport door to closest available location" instead of hidden passages — [agent0.php](https://github.com/abeiro/HerikaServer/blob/aiagent/ui/addons/snqe/cmd/agent0.php)
  - agent1 ("Dynamic Quest Storyboard Assistant") outputs "simple lazy XML" with no root: `<spawn>` (name, race, location, gender, class, background, speechStyle, disposition, goal), `<item>`, `<instruction>` (npc, action, target, location, item, topic important=true|false), `<rumor>`, `<journal>`, and `<next>` (the goals for this step).
    - Actions come from a closed list: TellTopicToPlayer, MoveToPlayer, ToGoAway, AssistPlayer, CombatPlayer, CombatNPC, TravelTo, WaitToItemBeRecovered, WaitAtLocation (blocking), StationAtLocation (non-blocking), WaitForActivation, WaitForPickUpItem.
    - Races, classes, item types and locations are template-injected enumerations: "You MUST NOT invent races, classes, item types, or locations… Any deviation breaks the simulation".
    - A "HARD SPATIAL RULE" forbids TellTopicToPlayer from an NPC who is not at the player's location; such an NPC must start with WaitAtLocation — [agent1.txt prompt](https://github.com/abeiro/HerikaServer/blob/aiagent/ui/addons/snqe/prompts/agent1.txt)
  - agent2 ("questcoder") compiles the XML into PHP calls against a documented API at temperature 0.3, then a second "questcoder_fixer" pass asks the model to "confirm the quest code is correct and complete" and to check that Wait* return values are handled. The code must end with CompleteQuest — [agent2.php](https://github.com/abeiro/HerikaServer/blob/aiagent/ui/addons/snqe/cmd/agent2.php)
  - The SNQE API (CreateNPC, CreateItem, CreateTopic, Spawn*, Wait*, TellTopic…): "All functions are idempotent… State is persisted per quest_id to allow quest scripts to resume execution across multiple runs". Enumerations include NPC classes (beggar, warrior, mage… forsworn), races including creature races, and item types (potion, amulet, ring, book, note, dagger, armor) — [api_doc.php](https://github.com/abeiro/HerikaServer/blob/aiagent/service/processors/snqe/lib/api_doc.php)
  - Quest completion is detected by engine-observable events (item recovered, location reached, activator used, NPC picked up item), not by the LLM's judgement. The LLM only generates the next step after the previous step's Wait* conditions are satisfied — inferred from the action list in [agent1.txt](https://github.com/abeiro/HerikaServer/blob/aiagent/ui/addons/snqe/prompts/agent1.txt) and the API description in [api_doc.php](https://github.com/abeiro/HerikaServer/blob/aiagent/service/processors/snqe/lib/api_doc.php)
  - The UI ("AI Quest Manager") has tabs for item types, NPC templates, outfits, server logs and "Traditional Quests" — [hub.php](https://github.com/abeiro/HerikaServer/blob/aiagent/ui/addons/snqe/hub.php)
- **CHIM quest trigger engine for vanilla and radiant quests (ported 15 Jun 2026; "Track quest gamets for rollback" the same day)** — [commit history](https://github.com/abeiro/HerikaServer/commits/aiagent/lib/chim_quest_engine.php)
  - `skyrim_quest_definitions.json` holds 301 hand-authored definitions. Each quest has beats with:
    - `triggers`: dialogue keywords with `min_matches`, item events and state conditions;
    - `prerequisites`;
    - an `action`: `gate`, `set_stage` with the vanilla quest form ID and stage, and downstream `set_objective_displayed`;
    - `npc_facts` per beat: facts injected into the focus NPC's prompt once that beat has fired.
  - Example: Finn's Lute (BardsCollegeLute) has beats LEARN_ABOUT_LUTE → ACCEPT_QUEST (sets stage 20) → … → RETURN_LUTE → QUEST_COMPLETE — [skyrim_quest_definitions.json](https://github.com/abeiro/HerikaServer/blob/aiagent/data/skyrim_quest_definitions.json)
  - An LLM "strict quest dialogue intent evaluator" sees only *legal* candidate beats, each with summary, intent labels, rules, item-gate status, and positive and negative examples. It returns `{"selected_beat_id": null|id, "confidence": 0..1, "reason"}`. Rules include: "Asking for information is not the same as accepting a quest", "Mentioning an item is not a valid hand-in unless the player is clearly returning, presenting, or reporting it", "Prefer null over a weak guess". The parser maps the returned ID back onto the candidate list and drops unknown IDs — [chim_quest_engine.php](https://github.com/abeiro/HerikaServer/blob/aiagent/lib/chim_quest_engine.php)
  - The same engine has an action outbox (`skyrim_quest_action_outbox`) where state counts only after the game acknowledges an action as "applied", radiant quest templates with alias placeholders, per-turn suppression of NPC actions that would conflict with a quest, and `RollbackRuntimeToGamets`, which rewinds quest runtime state to a game timestamp when the player loads an older save — [chim_quest_engine.php](https://github.com/abeiro/HerikaServer/blob/aiagent/lib/chim_quest_engine.php); [chim_quest_engine.sql](https://github.com/abeiro/HerikaServer/blob/aiagent/data/chim_quest_engine.sql)

**SkyrimNet (MinLL et al.), native SKSE plugin with no external server; repo created May 2025**
- Runs in-process with a web UI at localhost:8080. Different models can be assigned to different jobs: "a fast, cheap model for action selection, a smarter model for dialogue".
- Actions are YAML-defined and call Papyrus functions. They are "grouped into nested categories so the model picks a high-level intent first and drills into specifics second — the drill-down step uses a cheaper, lightweight prompt". Action eligibility is "pre-warmed" while the player's voice is still being transcribed — [SkyrimNet README](https://github.com/MinLL/SkyrimNet-GamePlugin)
- "World Knowledge" facts are gated by template conditions such as `get_quest_stage("MQ104") >= 13`, so "a fact can enter the world the moment a quest milestone fires". Vanilla quest dialogue trees still go through Skyrim's normal system; integrating the two is "on the roadmap" — [SkyrimNet README](https://github.com/MinLL/SkyrimNet-GamePlugin)
- Built-in "GameMaster":
  - picks only high-level actions (StartConversation, ContinueConversation, None/Narrate), and the output must be "exactly one line starting with `ACTION:`" with JSON parameters;
  - recommended action cooldown 60–120 s, continuous-scene cooldown 25–45 s, recent-events context 40–70 events, LLM timeout 45–75 s;
  - "scripted scenes and vanilla dialogue are not interrupted" — [SkyrimNet GameMaster docs (fork docs site)](https://goncalo22.github.io/SkyrimNet-GamePlugin/Features/gamemaster/)
- Dynamic quests ("start and progress (some) quests automatically… automatic quest assignment") are listed only under *Future Plans* — [SkyrimNet overview docs](https://goncalo22.github.io/SkyrimNet-GamePlugin/Features/overview/)
- OpenRouter usage: about 45B tokens in total, top models DeepSeek V3.2 (12.8B) and Gemma 4 31B (12.7B), about 250 models used. The OpenRouter page says "active since July 2025" — [OpenRouter app page](https://openrouter.ai/apps/url/https%3A%2F%2Fgithub.com%2FSkyrimNet-GamePlugin). A search snippet said "since April 2025" — [same page (snippet)](https://openrouter.ai/apps/url/https%3A%2F%2Fgithub.com%2FSkyrimNet-GamePlugin) — a minor conflict.

**NarrativeEngine (pdusen), SkyrimNet plugin, early alpha; repo created Jun 2026, active Oct 2026**
- It is a "Director" that, "at a steady cadence, decides whether right now is a good moment for something to happen". A small evaluation request "score[s] the current dramatic tension" and then either advances a Freytag arc (Exposition → Rising → Climax → Falling → Resolution, then repeat) or dispatches one of a few beats:
  - NPC Letter (a courier brings a letter "about something that actually happened between you");
  - NPC Visit;
  - Ambush (the roster is a plain editable text file: Forsworn in the Reach, Thalmor after the embassy, and so on).
- Two model profiles: `narrative_engine_director` runs "on every tick… a top-tier model here gets expensive fast", and the author recommends `deepseek/deepseek-v4-flash`; `narrative_engine_composer` writes letters in an NPC's voice only when a beat fires.
- The author has not audited costs. The mod is unsafe to remove mid-playthrough because "multiple quests run in the background".
- An in-game F7 dashboard shows "what the Director is thinking" — [NarrativeEngine README](https://github.com/pdusen/skyrimnet-plugin-narrative-engine)
- Design principles:
  - "Alpha Canon / Beta Canon": vanilla events are "authoritative, immutable, never overridden"; the Director "never countermands a vanilla quest, scripted scene, combat encounter".
  - "restraint is a first-class option".
  - "there is no plot to follow… shaping the curve of intensity", because "the player will not stick to a throughline we hand them".
  - "Recent history is ground truth".
  - The Director "both injects and suppresses" (it tells other systems to stand down during calm phases).
  - Cadence is wall-clock, not game time — [DESIGN_GOALS.md](https://github.com/pdusen/skyrimnet-plugin-narrative-engine/blob/main/docs/DESIGN_GOALS.md)
- Engineering detail: every LLM string is sanitized before use, because curly quotes, em-dashes and ellipses render as missing glyphs in game fonts. Non-ASCII letters (Cyrillic and others) pass through untouched — [LLM_RESPONSE_HANDLING.md](https://github.com/pdusen/skyrimnet-plugin-narrative-engine/blob/main/docs/LLM_RESPONSE_HANDLING.md)
- It blacklists cells and locations ("Sovngarde ships on that list") and never acts during menus or vanilla dialogue — [NarrativeEngine README](https://github.com/pdusen/skyrimnet-plugin-narrative-engine)

**IntelEngine (galanx), SkyrimNet plugin; Feb–Apr 2026, described as unmaintained by the NarrativeEngine author**
- An LLM "Dungeon Master" evaluates the world every few in-game hours (default "Story Tick Interval 3 hrs"). It can send NPCs to seek out the player, start gossip chains (up to 10 people), ambushes over real grudges, stalkers, messages, NPC-to-NPC scenes, and **templated quests**:
  - Combat: "clear enemies from a location";
  - Rescue: the victim is "pre-placed deep inside the dungeon before you enter";
  - Find Item: "a quest chest is placed at the boss room with a guardian enemy", and the compass points at it.
- A "Political DM" generates faction events every 6 game hours across 9 factions (trade, espionage, assassinations, wars), with faction quests unlocked at standing 20+.
- "Every event injects facts into NPC bios"; the DM uses "anti-repetition, type balancing, and per-NPC cooldowns".
- Its LLM config (model, endpoint, temperature, token limit) is separate from SkyrimNet's dialogue model.
- Pathfinding failures escalate through recovery steps and, as a last resort, the failure is narrated "so the NPC doesn't silently hang forever" — [IntelEngine README](https://github.com/galanx/IntelEngine-GamePlugin)
- The NarrativeEngine author's critique: "IntelEngine's events fire on a fixed timer cadence, with little regard for what the player is actually doing in the moment" — [NarrativeEngine README](https://github.com/pdusen/skyrimnet-plugin-narrative-engine)

**Mantella (art-from-the-machine), Skyrim and Fallout 4; repo created Jul 2023**
- A separate Mantella.exe server with a browser config at localhost:4999. 350k+ users claimed. Targets responses under 0.5 s with a "Fast Response Mode" — [Arcanum RPGs comparison](https://arcanumrpgs.com/blog/skyrim-ai-npc-mods/)
- No quest generation found. It has 45 JSON action definitions (attack, follow, barter, lead_to, report_crime, absolve_crime, cast_spell…), each with:
  - a prompt rule, e.g. "If an NPC says something highly offensive, begin your response with '{key}:'";
  - parameter scopes (`conversation`, `all_npcs_w_player`);
  - flags for one-on-one, multi-NPC and radiant use;
  - `llm_feedback` that tells the NPC to "find a rationalization for the action";
  - a `veto_warning`: "Begin your response with <veto> if {speakerName} would refuse to do so" — [attack.json](https://github.com/art-from-the-machine/Mantella/blob/main/data/actions/attack.json); [move_npc.json](https://github.com/art-from-the-machine/Mantella/blob/main/data/actions/functions/move_npc.json)
- OpenRouter usage: 9.03B tokens since March 2024, dominated by DeepSeek models (top: DeepSeek V4 Flash at 797M tokens) — [OpenRouter app page](https://openrouter.ai/apps/url/https%3A%2F%2Fart-from-the-machine.github.io%2F)
- **Fallout 4 equivalents:** Mantella is the main Fallout 4 option. CHIM and SkyrimNet are Skyrim-only — [Arcanum RPGs comparison](https://arcanumrpgs.com/blog/skyrim-ai-npc-mods/). I found no LLM quest generator for Fallout 4.

**"Skyrim local LLM NPC" / SkyrimLLMBridge, MSc dissertation released as a mod (Nexus 194492, Oct 2026)**
- One authored quest ("Poisoned Mead", NPC Runa in the Bannered Mare). The local LLM is "an untrusted proposal engine": it emits a JSON proposal that a deterministic Python validator checks against "active quest policies, acquired evidence flags, and character epistemic bounds". Failures fall back to safe dialogue.
- File-based I/O with Papyrus keeps inference off the game thread.
- Fine-tuned 2B with QLoRA: 53.4% routing accuracy, against 19.0% for the base 2B and 29.0% for a zero-shot 26B-A4B MoE. Median latency 0.98 s, p95 1.25 s, peak VRAM 7.1 GB on an RX 6900 XT — [SkyrimLLMBridge README](https://github.com/devPirate01/SkyrimLLMBridge)
- Note: the README names the model "Gemma 4" 2B, while the Nexus snippet says "Gemma 2B" — [Nexus page (snippet)](https://www.nexusmods.com/skyrimspecialedition/mods/194492)

**Mount & Blade II: Bannerlord**
- *AI Influence [AI Diplomacy]* (author MFive): free-form LLM dialogue with memory, plus AI diplomacy (rulers' public statements, war and peace declarations, tribute, territory proposals, war fatigue) and "dynamic events" (political, military, economic, social, mysteries, rumors) that change settlement prosperity, food, security and loyalty. Backends: Player2 (recommended), OpenRouter, DeepSeek, Ollama, KoboldCpp — [Skymods mirror (snippet)](https://catalogue.smods.ru/archives/402946); [Nexus page (snippet)](https://www.nexusmods.com/mountandblade2bannerlord/mods/9711)
- Per the community feature guide:
  - events come from a daily "response-pressure" model ("LLM sets urgency, game rolls each day");
  - a lightweight pre-call "reads the last two messages and selects optional prompt modules" (economy, quests, conflict, romance);
  - AI quests live in a `quest_action` field tied to world events;
  - there is a conversation-RAG memory, and the HTTP timeout was raised to 300 s in v5.0.0 — [AI Influence feature guide (community-made)](https://reggie-ai-influence.github.io/AI-Influence/)
- Ecosystem: submods for OpenAI-compatible endpoints, Battle Shouts, and Realm of Thrones / Banner Kings bridges. About 67.7k downloads (aggregator figure) — [Nexus search results](https://www.nexusmods.com/mountandblade2bannerlord/mods/10690); [DiscoverMods aggregator](https://discovermods.com/bannerlord/ai-influence-ai-diplomacy/)
- *Inworld Calradia* (modder Bloc, 2023): Inworld-powered NPC chat with persistent memory; it did not replace vanilla dialogue — [eXputer](https://exputer.com/news/games/mount-and-blade-2-bannerlord-mod/). Its status after Inworld retired Character Studio is unknown (see Gaps).

**Kenshi: Sentient Sands**
- LLM dialogue for every NPC, NPC-to-NPC chatter, and an event tracker (battles, killings, raids, town conquests) "periodically synthesized into rumors that spread through the world's NPCs".
- Recruitment by persuasion is gated by faction and reputation ("Holy Nation… will refuse unless you have massive reputation…"). A generated name, backstory and personality is persisted per NPC on first contact.
- Ships pre-configured for Player2 — [Sentient Sands itch.io (snippet)](https://harvicus.itch.io/sentientsands); [Nexus Kenshi 1872 (snippet)](https://www.nexusmods.com/kenshi/mods/1872)

**RimWorld**
- RimGPT (event commentary and raid flavor via OpenAI and Azure voices) was removed from the Workshop for violating content guidelines.
- EchoColony (talk to colonists; its storyteller update reacts to deaths, mental breaks and couples).
- RimTalk.
- BetterTeller ("multiple AI systems… selecting and scheduling incidents through a director-style pipeline").
- RimAI (an external-AI storyteller that generates events from wealth, mood, population and recent events) — [Steam "Mods that incorporate AI – RimWorld" collection (snippet)](https://steamcommunity.com/sharedfiles/filedetails/?id=3463214912); [EchoColony Workshop (snippet)](https://steamcommunity.com/sharedfiles/filedetails/?id=3463505750)
- RimMind-Storyteller (GitHub, Apr 2026): tension-curve evaluator, "Needs Climax vs Needs Respite", incident chaining (rescue → retaliatory manhunt), and LLM-written incident letters. Models and costs are not documented — [RimMind-Storyteller](https://github.com/RimWorld-RimMind-Mod/RimWorld-RimMind-Mod-Storyteller)
- Business data point: the EchoColony author said adding Player2 "practically tripled my user base"; players "never run out of the free quota"; developers get monthly revenue-share payouts — [Player2 blog (vendor)](https://blog.player2.game/p/how-one-rimworld-modder-tripled-his)

**Minecraft (thin evidence)**
- LoraCore claims AI-generated villager quests with rewards via the OpenAI API — [ModpackIndex (unverified)](https://www.modpackindex.com/mod/125074/loracore)
- llama.jar is a library mod for local GGUF models (e.g. qwen-2.5-1.5b) that advertises "dynamic quest generation" — [Modrinth (unverified)](https://modrinth.com/mod/llama.jar)
- Player2 AI NPC is a companion mod — [Modrinth](https://modrinth.com/mod/player2npc)

**Gothic**
- GitHub search for gothic + llm/chatgpt/ollama/union/opengothic returned only the user's own kaifaty/GothicAI, plus two unrelated ChatGPT-written tooling repos (zenFix, zDialogueToExcel) — [GitHub search: zDialogueToExcel](https://github.com/Gothic-Workshop/zDialogueToExcel). Russian- and English-language web searches found no Gothic LLM dialogue or quest mod — [overclockers.ru roundup of AI mods (no Gothic)](https://overclockers.ru/blog/Zelikman/show/186422/Moddery-vse-chasche-ispol-zujut-tehnologii-II-chtoby-raznoobrazit-ljubimye-igry)

### Inferences
- The CHIM menuless-questing engine fits Gothic best if the goal is to keep the authored quests. Gothic's Daedalus quests already expose `MIS_*` state variables, `Log_*` topic entries and `B_GiveInvItems`-style hand-ins. Each quest can be encoded as beats with dialogue-intent and item/state triggers, with an LLM classifier over legal beats only. The LLM then drives transitions in an authored state machine and never invents the state machine.
- CHIM SNQE shows a viable pattern for *generated side quests* that fits our existing tool-calling server: closed action vocabulary, enumerated locations/NPC templates/item types, blocking Wait* primitives the engine can verify, journal text written "without spoilers", and generation one step at a time. Its weak points (my reading of the code) are a code-generation hop (LLM → PHP) plus an LLM self-review hop. A JSON schema validated by deterministic code would be safer and cheaper than generating code.
- The NarrativeEngine principles (alpha/beta canon, restraint, suppression, wall-clock cadence, a cheap model for the director tick) transfer directly to a Gothic "story director". The two-model split (cheap scorer every tick, expensive composer only on dispatch) maps onto our local 2B decider plus a cloud dialogue model.
- Save/load consistency is a real, solved-by-practitioners problem: CHIM rolls quest state back by game timestamp. OpenGothic saves need an equivalent, with the server keyed by save ID plus game time.
- Text sanitization matters for us too: OpenGothic fonts (Cyrillic cp1251 in the Russian version) will mangle em-dashes and smart quotes.

### Gaps
- No usage or reception data specific to CHIM's AI quest generator (SNQE): Reddit, Nexus comments and Discord were not reachable or not found. I could not see Nexus download or endorsement counts for CHIM, SkyrimNet or AI Influence (403).
- No published per-hour cost for any Skyrim mod. The NarrativeEngine author explicitly declines to estimate.
- No source on whether Inworld Calradia survived Inworld's 2025 pivot.
- No LLM quest mod for Fallout 4, Kenshi (beyond rumors) or Gothic was found. Minecraft quest mods are only self-described.

---

## 2. Middleware: Inworld, Convai, NVIDIA ACE, Ubisoft NEO NPC / Teammates, Charisma.ai and others

### Takeaway
Middleware converged on "authored goals plus LLM improvisation": section/objective graphs advanced by explicit game triggers (Convai, Charisma, Inworld goals) and, increasingly, on-device small models for low-latency action selection (NVIDIA ACE). The best-known game-AI middleware vendor, Inworld, left the game-character business in 2025, and its Narrative Graph never shipped publicly in a game I could find. None of these vendors offers a production "quest generation API" with documented completion semantics.

### Cited Findings
- **Inworld Narrative Graph** (with Xbox, GDC 2024): upload scripts or documents to "generate a branching graph of the narratives", propose new beats and dialogue. Shown in the text prototype *Mists of Aurora* (three scenes) — [Wccftech GDC 2024](https://wccftech.com/inworld-ai-blows-up-at-gdc-2024-between-ubisofts-neo-npcs-xboxs-narrative-graph-tool-and-more/)
- **Inworld goals/actions:** goals are defined per character in Studio and activated by triggers that the client sends (e.g. "initconvo") — [Inworld Unity "Goal and Actions" demo docs (snippet; now behind login)](https://docs.inworld.ai/docs/tutorial-integrations/Unity/demos/sample-trigger)
- **Inworld Origins:** a detective demo in which you question witnesses by voice — [search snippet, Deku Deals listing](https://www.dekudeals.com/items/inworld-origins)
- **Inworld pivot:**
  - "games" disappeared from its homepage in Mar–Apr 2025; Character Studio was retired by 9 Sep 2025 and its features moved to "Agent Runtime", where developers build characters in their own engine;
  - the stated problem was "keeping voice, timing, and consistency flawless at scale";
  - the only shipped game the timeline mentions is *Death by AI* — [Arcanum RPGs Inworld timeline](https://arcanumrpgs.com/blog/inworld-ai/)
  - Inworld Runtime launched 13 Aug 2025 as a C++ graph engine orchestrating LLM, STT, TTS, memory and tools — [GlobeNewswire](https://www.globenewswire.com/news-release/2025/08/13/3132320/0/en/Inworld-Runtime-The-first-AI-runtime-for-consumer-applications.html)
- **Death by AI case study (vendor):**
  - 700k daily users within 3 days and 20M players in 3 months;
  - 1.2B tokens in month one on GPT-3.5/4;
  - OpenAI + ElevenLabs were "not financially sustainable" within 3 days;
  - AI costs fell 95% after the switch to Inworld — [Inworld blog (vendor case study)](https://inworld.ai/blog/how-inworld-helped-the-ai-game-death-by-ai-with-20-million-players-reach-profitability)
- **Convai Narrative Design:**
  - a graph of Sections (each with an Objective and Decisions), Triggers and edges;
  - some transitions are decided by the LLM from the objective, others by the app via `invokeTrigger`;
  - exact lines can be forced with `<speak>…</speak>`;
  - framed as steering conversation "toward predefined narrative milestones" without dialogue trees — [Convai Narrative Design guide](https://docs.convai.com/api-docs/plugins-and-integrations/web-plugins/narrative-design-guide); [Convai blog](https://convai.com/blog/convai-narrative-design)
- **Charisma.ai:** story graph of nodes (player input, character dialogue, memory writes, effects); the AI selects the most relevant path; subplot nodes with re-entry points; an emotion and memory engine. Customers are mostly training and education rather than games — [Charisma docs: nodes (snippet)](https://docs.charisma.ai/nodes)
- **NVIDIA ACE:**
  - inZOI "Smart Zoi" runs an on-device 0.5B Mistral-NeMo-Minitron SLM that plans, acts and reflects (a nightly "analyze its actions"). Players can read Zois' inner thoughts and steer them with free text. Needs an RTX 3060/4060 with 8 GB — [NVIDIA GeForce news](https://www.nvidia.com/en-us/geforce/news/nvidia-ace-autonomous-ai-companions-pubg-naraka-bladepoint/); [TweakTown](https://www.tweaktown.com/news/104210/the-sims-like-inzoi-lets-you-read-thoughts-of-its-nvdia-ace-ai-characters/index.html)
  - A user bug report shows SmartZoi using about 5 GB of VRAM and disabling itself under Proton — [inZOI forum](https://forum.playinzoi.com/t/topic/11128)
  - PUBG Ally is an on-device SLM teammate (voice/text commands, looting, fighting); testing began in early 2026 and a public beta ran until 30 Jun 2026 — [KRAFTON press release](https://press.krafton.com/KRAFTON-REVEALS-PLAYTEST-PLANS-FOR-PUBG-ALLY-BUILT-WITH-NVIDIA-ACE); [Wccftech](https://wccftech.com/nvidia-ace-pubg-ally-ai-teammate-beta/amp/)
- **Ubisoft NEO NPC** (GDC 2024, built on Inworld with Audio2Face):
  - writers author backstory, personality and conversational style; the data scientist frames the model's task as "I must impersonate this character", tested by asking "Would [she] say this?";
  - the team corrected a female character who drifted into flirtation — [Ubisoft News](https://news.ubisoft.com/en-us/article/5qXdxhshJBXoanFZApdG3L/how-ubisofts-new-generative-ai-prototype-changes-the-nature-of-npcs)
  - demos: relationship "level ups" with Bloom; Iron critiquing the player's mission plan — [HotHardware (snippet)](https://amp.hothardware.com/news/ubisoft-unveils-a-generative-ai-prototype-for-npcs)
  - The claim that NEO NPCs "generate quests in real-time" appears only in launch PR coverage — [Wccftech](https://wccftech.com/inworld-ai-blows-up-at-gdc-2024-between-ubisofts-neo-npcs-xboxs-narrative-graph-tool-and-more/) — **marketing-only / demo**
- **Ubisoft Teammates** (2025, Snowdrop):
  - "We use behavior trees… then we add a layer with our LLM to take decisions based on this" (Google Gemini);
  - assistant Jaspar reminds the player of objectives and suggests next steps; squadmates follow imprecise voice commands;
  - 80-person team (vs about 25 on NEO NPC); playtest limited to "a few hundred players";
  - design rule: "If this is something that could be done better with a controller, let's scrap it" — [Game Developer](https://www.gamedeveloper.com/business/ubisoft-s-first-playable-generative-ai-experience-is-an-r-d-experiment-called-teammates-); [Dot Esports](https://dotesports.com/general/news/teammates-ubisoft-ai-game-experiment)
- **Scenario** (scenario.gg) is an asset/image-generation platform. I found no story or quest generation API from it or similar art vendors (see Gaps).

### Inferences
- Convai's "Section → Objective, advanced by LLM-judged decisions or app triggers" is the most transferable middleware abstraction. In our terms: a quest step equals an objective injected into the NPC prompt; advancement comes either from engine triggers (item handed over, location reached) or from a conservative LLM intent judgement, as in CHIM.
- Teammates' pattern (behavior trees own execution; the LLM only chooses among options) matches our split, with the local 2B decider choosing reactions and the engine executing them.
- Vendor lock-in is a real risk: Inworld's exit stranded its studio-hosted characters. Our self-hosted server avoids this.

### Gaps
- Inworld docs now require login, so I could not verify the current goals/trigger semantics. Narrative Graph availability after 2025 is unknown.
- No public documentation on how NVIDIA ACE titles (Dead Meat, Mecha BREAK) constrain outputs or detect progress.
- No evidence that any "story/quest generation API" vendor (Scenario or others) has production game customers.

---

## 3. Commercial games with dynamic quest, plot or event features, and their lessons

### Takeaway
The products that hold together narratively ship **authored structure** (tropes/cards in Hidden Door, a single tightly scoped scenario in Whispers from the Star, authored suspects and secrets in Dead Meat, world-builder-written quests in Friends & Fables) and use the LLM to perform within it. Products that let LLM conversation decide quest outcomes get exploited at once (Where Winds Meet). One AI-GM product *removed* on-the-fly quest generation because a running plot/plan summary handled quests better (Friends & Fables, Aug 2025).

### Cited Findings
- **Where Winds Meet** (NetEase/Everstone, global launch late 2025): LLM NPCs give small side tasks (bury or retrieve items) that build friendship and pay rewards. Players found two exploits:
  - echoing the NPC's line back ("Solid Snake method") until it "declare[s] the quest complete";
  - typing bracketed fake actions such as "(hands you the missing object)", including gifting "an imaginary cat";
  - reactions ranged from amused to "horrifying" — [GosuGamers](https://www.gosugamers.net/entertainment/news/77655-where-winds-meet-players-outsmart-ai-npcs-to-easily-get-sidequest-rewards); [Outlook Respawn](https://respawn.outlookindia.com/gaming/gaming-news/where-winds-meet-ai-exploit-lets-players-skip-quests)
  - I found no report of a developer patch.
- **Justice Online Mobile / Ni Shui Han** (NetEase, 2023): an in-house model trained on period histories, poems and wuxia novels. Example: warning an NPC about a fire makes them run to check their house. NetEase said it planned to scale AI to "task generation" (quests) — [TechNode](https://technode.com/2023/02/17/netease-announces-use-of-chatgpt-like-tech-in-new-mobile-game/); [WN Hub](https://wnhub.io/news/other/item-1759). Whether quest generation shipped is unverified.
- **Hidden Door** (launched Aug 2025, web):
  - founders describe a "proprietary story engine, not an LLM wrapper", with story elements broken into "tens of thousands of manually curated tropes" for "much stronger guarantees about what is or isn't produced" — [Hilary Mason, Medium (snippet)](https://medium.com/@hmason/the-state-of-ai-native-games-lessons-from-the-frontier-3e696a9e3279)
  - the founder named "narrative ungroundedness" as the core challenge ("the story existed only as far as it had been written") — [same (snippet)](https://medium.com/@hmason/the-state-of-ai-native-games-lessons-from-the-frontier-3e696a9e3279)
  - Ian Bicking's design review: likely a "Llama-level" model for cost. "the treasure chest doesn't have traps. It also doesn't not have traps" until the player chooses. Objectives are "almost immediately completed", e.g. "Deal with Boq" appears after the conflict is already over. Choices like "I have a plan" with no plan behind them. "no sense of pacing". His recommendations: keep a parallel objective world log, and "outline the plot… then generate the actual story text" — [Ian Bicking](https://ianbicking.org/blog/2025/08/hidden-door-design-review-llm-driven-game.html)
  - A 2026 review scores it 2.7/5: Memory 4/5 and Longevity 4/5 (joint highest) but Player Agency 1/5 ("play the cards the engine deals"; an "illusion of emergence… the seams show"). Free tier with unlimited turns plus a subscription; published worlds pay their creators — [Arcanum RPGs review](https://arcanumrpgs.com/blog/hidden-door-review/)
- **Friends & Fables** (AI game master "Franz"):
  - a multi-agent pipeline ("a dozen different requests to various LLMs purpose built for certain tasks"): research agent, narration agent, state-update agents for HP, inventory, location and quests;
  - "medium size models" for speed and cost, no frontier models — [How Franz works](https://release-marketing-site.fables.gg/blog/how-franz-works)
  - **Franz 2.0 (28 Aug 2025):** a "plot and plan" is rewritten every turn and is editable by the player; "the plot & plan are actually quite good for handling quests narratively, so we've decided to remove Franz's on-the-fly quest generation"; quests are now created only by world builders; the update "will increase our costs by 33-100%" — [Franz 2.0 patch notes](https://fables.gg/patch-notes/franz-20-working-context-lore-improved-planning-and-more)
- **AI Dungeon → Heroes → Voyage** (Latitude):
  - Heroes keeps hierarchical summaries, a memory bank, and a structured "Game State" (class, skills, inventory, current quests) fed back into prompts — [Latitude Heroes Dev Log #11 (snippet)](https://latitude.io/news/11)
  - Voyage (expanded beta 21 Apr 2026): "World Engine" built over 5 years, with multiple AI systems for narration, mechanics and state, and character/object/relationship tracking. Free tier plus $15/$30/$50 monthly plans. Proprietary models plus Gemma/Gemini. Beta testers met 160k AI characters and averaged about 3,000 choices per player — [TechCrunch](https://techcrunch.com/2026/04/21/voyage-is-an-ai-rpg-platform-for-creating-custom-gaming-worlds-with-ai-generated-npc-interactions)
- **Whispers from the Star** (Anuttacon, Steam, 14 Aug 2025):
  - text, voice and video chat with one stranded astronaut; no dialogue trees; Stella "can die or fail to progress, locking you out of certain endings" — [Notebookcheck (snippet)](https://www.notebookcheck.net/Steam-launch-New-interactive-fiction-game-debuts-to-Very-Positive-reviews-may-divide-gamers-over-heavy-AI-integration.1087821.0.html); [Grokipedia (snippet; low-quality source)](https://grokipedia.com/page/Whispers_from_the_Star)
  - Reception: "Very Positive" (92% of 202 at launch; 1,663 ratings later) — [Notebookcheck (snippet)](https://www.notebookcheck.net/Steam-launch-New-interactive-fiction-game-debuts-to-Very-Positive-reviews-may-divide-gamers-over-heavy-AI-integration.1087821.0.html)
  - A critic credits it as "a masterclass in constraints": a single scenario with two central characters. Stella occasionally breaks character because of external-LLM safety rules — [Cinehorde review (blog)](https://cinehordereview.wordpress.com/2025/10/03/a-new-dawn-for-interactive-narrative-how-whispers-from-the-star-gets-ai-storytelling-right/)
  - Anuttacon hired "AI Trainers" at $20–30/h to annotate responses — [36Kr](https://eu.36kr.com/en/p/3483227477449859)
- **Dead Meat** (Meaning Machine): an interrogation game in which you extract a confession in your own words; "author-led" AI ("Game Conscious") on NVIDIA ACE small language models — [Adventure Game Hotspot (snippet)](https://adventuregamehotspot.com/announcement/641/dead-meat-being-seasoned-for-pc-release); [NVIDIA ACE (snippet)](https://developer.nvidia.com/ace)
  - Bristol study: 68 participants in 20-minute sessions; 96% rated it 6+/10 for enjoyment, 90% gave 5+ for creative freedom; freedom felt "captivating and somewhat overwhelming". Caveat: small sample, and the studio co-ran the study — [iXBT Games](https://ixbt.games/es/news/2026/03/13/382091-personazi-na-osnove-generativnogo-ii-ocen-nraviatsia-igrokam-pokazalo-issledovanie.amp.html); [Bristol Digital Game Lab](https://bristoldigitalgamelab.blogs.bristol.ac.uk/2025/05/05/recap-dead-meat-player-study/)
  - Players tried to steer the setting into space or play "the Devil" — [Bristol Digital Game Lab](https://bristoldigitalgamelab.blogs.bristol.ac.uk/2025/05/05/recap-dead-meat-player-study/)
- **Vaudeville** (Bumblebee, Inworld-based detective game):
  - Steam reviews "evenly split between enthusiasts and haters"; negative reviews targeted "the limitations of the AI"; voice generation was "too costly"; Inworld's first-month bills were large; "Steam payments are delayed by a month… make sure you have a buffer" — [Vaudeville pre-mortem, Game Developer](https://www.gamedeveloper.com/press-release/vaudeville-a-pre-mortem)
  - Preview headline: "The AI questioning to nowhere" — [KeenGamer](https://keengamer.com/articles/previews/vaudeville-preview-the-ai-questioning-to-nowhere/)
  - Bumblebee later announced an "offline Smart NPCs engine" — [Gamespress (snippet; 401 on fetch)](https://gamespress.com/Bumblebee-presents-its-new-offline-Smart-NPCs-engine)
- **1001 Nights:** the player tells stories as Shahrzad and the LLM King continues them; keywords in the story ("sword", "shield") become usable equipment, which ties free text to mechanics through a fixed lexicon — [Steam](https://store.steampowered.com/app/2542850/1001_Nights/); [IGF entry](https://igf.com/entry/2024/1001-nights)
- **Suck Up!** (Proxima): persuade LLM NPCs to invite a vampire in. It originally required buying "tokens" to cover LLM costs; now $16.99 on Steam; described as arguably the first commercially successful runtime-AI game, with 100M+ YouTube views and zero marketing — [OpenAI community thread](https://community.openai.com/t/vampire-game-where-you-convince-llm-to-let-you-in/604295); [GameMakers podcast (snippet)](https://pocketcasts.com/podcast/gamemakers/daf23c20-1060-0139-41d1-0acc26574db2/the-last-20-is-worth-100-million/b58a4a87-0a8f-43dd-a19c-54cf621fe822); [Steambase price](https://steambase.io/games/suck-up/price)
- **Teach My Little Sister How To Drive** (four-person studio, Oct 2026): LLM sister character on Gemini and ChatGPT. Over $1,000/day in tokens; the studio took a bank loan to keep the free demo running and plans local models plus a paid release with no per-token charges. Peak was 405 CCU in the demo; the headline's "62,000 concurrents" conflicts with the body text — [FRVR](https://frvr.com/blog/news/popular-ai-driving-game-devs-forced-to-take-out-loans-as-62000-concurrents-result-in-massive-ai-token-costs/)

### Inferences
- Lesson for a Gothic story director: never let the dialogue LLM be the authority on quest completion. Where Winds Meet is the canonical failure. CHIM's intent judge is conservative and only *selects* among legal beats whose item/state gates the engine has already verified; Mantella-style bracketed actions from the player must never count as world facts.
- Friends & Fables' reversal is a strong signal against "replace hand-written quests with generated ones". A rolling "plot + plan" summary that the director rewrites each turn is cheaper to keep consistent than a full generated quest graph, and the authored quests stay the backbone.
- Hidden Door's failures (premature or collapsed objectives, no grounded world state) are exactly what Gothic avoids by having a real engine state. The director should read ground truth from the engine (inventory, MIS_ variables, positions) rather than from its own prose.

### Gaps
- No architecture details for Where Winds Meet's quest-completion logic, and no developer response found.
- Retail Mage: searches did not confirm how it uses LLMs (79% positive of 44 Steam reviews per one snippet) — [gg.deals (snippet)](https://gg.deals/gb/game/retail-mage/). Excluded.
- Avatar: Frontiers of Pandora, Infinite Craft caching details, AI2U, and AI People (GoodAI) were not researched in depth or yielded no quest/plot mechanics.
- I could not open the GDC 2026 NVIDIA/Bristol session page ("What good are AI NPCs? Lessons from a large-scale player study") (403). It is known only from a snippet claiming that AI NPCs "embedded within carefully authored experiences" raise engagement — [GDC schedule (snippet)](https://schedule.gdconf.com/session/what-good-are-ai-npcs-lessons-from-a-large-scale-player-study-presented-by-nvidia/917528)

---

## 4. How these systems constrain the LLM and detect quest completion

### Takeaway
Every working system uses the same handful of techniques: (1) a closed action/event vocabulary with enumerated arguments (locations, NPC templates, item types) taken from the live game; (2) structured output (XML/JSON or a fixed `ACTION:` line) parsed and validated by code; (3) authored skeletons (beats, sections, tropes, templates) that the LLM fills in or selects among; (4) completion detected by engine-observable conditions, with LLM judgement used only as a conservative classifier over legal transitions; (5) cadence, cooldown and anti-repetition limits; and (6) "canon" rules that forbid overriding authored content.

### Cited Findings
- Closed vocabularies with enumerations: CHIM SNQE's allowed races/classes/item types/locations and 12 instruction types — [agent1.txt](https://github.com/abeiro/HerikaServer/blob/aiagent/ui/addons/snqe/prompts/agent1.txt); Mantella's 45 JSON action definitions with parameter scopes — [Mantella actions](https://github.com/art-from-the-machine/Mantella/blob/main/data/actions/attack.json); IntelEngine's fixed story types and quest sub-types — [IntelEngine README](https://github.com/galanx/IntelEngine-GamePlugin); NarrativeEngine's three beats and an editable ambush roster — [NarrativeEngine README](https://github.com/pdusen/skyrimnet-plugin-narrative-engine)
- Hierarchical selection to save tokens: SkyrimNet picks the high-level intent first and drills down with a cheaper prompt — [SkyrimNet README](https://github.com/MinLL/SkyrimNet-GamePlugin)
- Output format contracts: SkyrimNet GameMaster requires one `ACTION:` line plus JSON — [GameMaster docs](https://goncalo22.github.io/SkyrimNet-GamePlugin/Features/gamemaster/); CHIM intent JSON `{"selected_beat_id","confidence","reason"}` — [chim_quest_engine.php](https://github.com/abeiro/HerikaServer/blob/aiagent/lib/chim_quest_engine.php); SkyrimLLMBridge's JSON proposal and deterministic validator — [SkyrimLLMBridge](https://github.com/devPirate01/SkyrimLLMBridge)
- Authored skeletons:
  - CHIM's 301 vanilla quest beat graphs with keyword triggers and set_stage actions — [skyrim_quest_definitions.json](https://github.com/abeiro/HerikaServer/blob/aiagent/data/skyrim_quest_definitions.json)
  - Convai sections/objectives/triggers — [Convai guide](https://docs.convai.com/api-docs/plugins-and-integrations/web-plugins/narrative-design-guide)
  - Hidden Door's curated tropes and cards — [Arcanum review](https://arcanumrpgs.com/blog/hidden-door-review/)
  - IntelEngine's pre-placed rescue victim and quest chest in the boss room — [IntelEngine README](https://github.com/galanx/IntelEngine-GamePlugin)
- Completion detection:
  - engine events (WaitToItemBeRecovered, WaitAtLocation, WaitForActivation) in CHIM SNQE — [api_doc.php](https://github.com/abeiro/HerikaServer/blob/aiagent/service/processors/snqe/lib/api_doc.php)
  - item-gate plus LLM intent with "prefer null over a weak guess" in the CHIM engine — [chim_quest_engine.php](https://github.com/abeiro/HerikaServer/blob/aiagent/lib/chim_quest_engine.php)
  - game-acknowledged action outbox (state commits only on an "applied" ack) — [chim_quest_engine.php](https://github.com/abeiro/HerikaServer/blob/aiagent/lib/chim_quest_engine.php)
  - proximity-based meeting completion in IntelEngine ("no timer… just proximity") — [IntelEngine README](https://github.com/galanx/IntelEngine-GamePlugin)
  - counter-example, conversational completion that was exploited: Where Winds Meet — [GosuGamers](https://www.gosugamers.net/entertainment/news/77655-where-winds-meet-players-outsmart-ai-npcs-to-easily-get-sidequest-rewards)
- NPC refusal and rationalization: Mantella's `<veto>` prefix and `llm_feedback` ("find a rationalization for the action") — [move_npc.json](https://github.com/art-from-the-machine/Mantella/blob/main/data/actions/functions/move_npc.json); Sentient Sands' faction/reputation gate on recruitment — [Sentient Sands (snippet)](https://harvicus.itch.io/sentientsands)
- Pacing controls:
  - GameMaster cooldowns of 60–120 s — [GameMaster docs](https://goncalo22.github.io/SkyrimNet-GamePlugin/Features/gamemaster/)
  - Freytag phases with dwell times, plus a "restraint" option — [DESIGN_GOALS.md](https://github.com/pdusen/skyrimnet-plugin-narrative-engine/blob/main/docs/DESIGN_GOALS.md)
  - IntelEngine's anti-repetition, type balancing and per-NPC cooldowns — [IntelEngine README](https://github.com/galanx/IntelEngine-GamePlugin)
  - AI Influence's LLM-set urgency with a dice roll each game day — [AI Influence guide](https://reggie-ai-influence.github.io/AI-Influence/)
- Canon protection:
  - NarrativeEngine never interrupts vanilla quests, combat or dialogue — [README](https://github.com/pdusen/skyrimnet-plugin-narrative-engine)
  - SkyrimNet's GameMaster excludes actors in scripted scenes — [GameMaster docs](https://goncalo22.github.io/SkyrimNet-GamePlugin/Features/gamemaster/)
  - CHIM suppresses NPC actions per turn when they would conflict with a quest — [chim_quest_engine.php](https://github.com/abeiro/HerikaServer/blob/aiagent/lib/chim_quest_engine.php)
- Knowledge gating by quest progress: SkyrimNet World Knowledge conditions such as `get_quest_stage("MQ104") >= 13` — [SkyrimNet README](https://github.com/MinLL/SkyrimNet-GamePlugin); CHIM's per-beat `npc_facts` — [definitions JSON](https://github.com/abeiro/HerikaServer/blob/aiagent/data/skyrim_quest_definitions.json)
- Grounding advice from practice: keep an "objective description of the basic environment" alongside the prose, and plan the outline and choices before writing the text — [Ian Bicking](https://ianbicking.org/blog/2025/08/hidden-door-design-review-llm-driven-game.html)
- Fine-tuned small routers beat large zero-shot models on state routing (53.4% vs 29.0%), though 53% is still low in absolute terms — [SkyrimLLMBridge](https://github.com/devPirate01/SkyrimLLMBridge)

### Inferences
- A practical recipe for Gothic, synthesized from the above:
  - the director emits JSON from a closed event menu (letter via courier/messenger, NPC visit, ambush with an authored roster per region and chapter, rumor, templated fetch/rescue/kill quest);
  - arguments come only from enumerations the server pulls from the live world (waypoints/locations, spawnable instance templates, item types);
  - completion uses engine-checkable predicates (`Npc_HasItems`, distance to waypoint, `Npc_IsDead`, MIS_ variable values);
  - an LLM judge is used only for "did the player accept/report" over legal options, and the item gate must already be true.
- The local 2B decider could serve as the "cheap tick" model (tension scoring, choosing among a few legal options), following the NarrativeEngine director/composer split. SkyrimLLMBridge's numbers suggest it needs fine-tuning on our own state taxonomy, and even then it should only *propose* while code validates.

### Gaps
- No product publishes failure rates for generated quests (soft-locks, unreachable spawns). CHIM's teleport-door workaround hints that spatial validity is a frequent failure mode, but this is unquantified.

---

## 5. What players complain about and what they praise

### Takeaway
Praise centers on reactivity and freedom: NPCs who remember, notice and talk about what you actually did. Complaints center on shallowness (instant or collapsed objectives, generic tropes), exploitability, ungrounded or inconsistent world state, latency or awkward pacing, characters breaking, and a broad cultural backlash against generative AI. That backlash is strongest among narrative-focused players, which is exactly the audience for story RPGs like Gothic.

### Cited Findings
- **Backlash:** 85% of 1,799 gamers surveyed (Oct–Dec 2025) were negative about genAI in games, and 63% chose the most negative option. Negativity rises with interest in "elaborate narratives, interesting NPC backstories". Negative attitudes toward "AI-generated quests/dialogue" rose from 46% (2024) to 77% / 83% (the article's phrasing is ambiguous about which figure applies to which) — [GamesMarket on Quantic Foundry](https://www.gamesmarket.global/quantic-foundry-most-gamers-are-negative-about-the-use-of-gen-ai-in-video-games)
- Contrast, vendor survey (marketing): "99%" of gamers think advanced AI NPCs would improve gameplay — Inworld "Future of NPCs" report — [Game Industry Library (Inworld report)](https://gameindustrylibrary.com/documents/future-of-npcs-report-unknown-time-unknown-time-2025) — **vendor-commissioned; treat as marketing**
- **Adoption:** 7,818 of about 114k Steam games (7%) disclosed genAI as of 13 Jul 2025, and about 1 in 5 new 2025 releases did. The study names runtime-generated content in inZOI, Nyric, Never Ending Dungeon and AI Roguelite — [WN Hub on Totally Human study](https://wnhub.io/news/stores-and-publishing/item-48292)
- **Praise:**
  - Dead Meat study: 96% enjoyment and 90% creative freedom (caveats above) — [iXBT Games](https://ixbt.games/es/news/2026/03/13/382091-personazi-na-osnove-generativnogo-ii-ocen-nraviatsia-igrokam-pokazalo-issledovanie.amp.html)
  - Whispers from the Star "Very Positive" — [Notebookcheck (snippet)](https://www.notebookcheck.net/Steam-launch-New-interactive-fiction-game-debuts-to-Very-Positive-reviews-may-divide-gamers-over-heavy-AI-integration.1087821.0.html)
  - Hidden Door "set-ups pay off" and characters "stay themselves" — [Arcanum review](https://arcanumrpgs.com/blog/hidden-door-review/)
  - EchoColony works because NPCs react to *specific* events (battles, injuries, relationships) rather than generic lines — [Player2 blog](https://blog.player2.game/p/how-one-rimworld-modder-tripled-his)
- **Complaints:**
  - exploitable conversational quests — [GosuGamers](https://www.gosugamers.net/entertainment/news/77655-where-winds-meet-players-outsmart-ai-npcs-to-easily-get-sidequest-rewards)
  - "limitations of the AI" splitting reviews 50/50 — [Vaudeville pre-mortem](https://www.gamedeveloper.com/press-release/vaudeville-a-pre-mortem)
  - ungrounded world state, instant objectives, no pacing, over-indexing on one trait ("mismatched socks… practically a character of their own"), effortful latency — [Ian Bicking](https://ianbicking.org/blog/2025/08/hidden-door-design-review-llm-driven-game.html)
  - limited agency in a curated-trope design — [Arcanum review](https://arcanumrpgs.com/blog/hidden-door-review/)
  - characters breaking because of provider safety filters — [Cinehorde](https://cinehordereview.wordpress.com/2025/10/03/a-new-dawn-for-interactive-narrative-how-whispers-from-the-star-gets-ai-storytelling-right/)
- **Mod-specific friction:** setup problems (antivirus blocking Mantella.exe, Program Files installs, version mismatches as "the #1 reason", API keys out of credit, SKSE breaking after updates) — [Arcanum comparison](https://arcanumrpgs.com/blog/skyrim-ai-npc-mods/)
  - The NarrativeEngine author warns of edge-case bugs and against using it on saves you care about. Its director deliberately fires rarely when natural gameplay already follows the tension curve — [NarrativeEngine README](https://github.com/pdusen/skyrimnet-plugin-narrative-engine)
- **Moderation:** RimGPT was removed from the Steam Workshop for violating guidelines — [Steam collection (snippet)](https://steamcommunity.com/sharedfiles/filedetails/?id=3463214912)

### Inferences
- Gothic's audience overlaps heavily with "story-first, hand-crafted world" players, the segment most hostile to AI-written quests in the Quantic Foundry data. Positioning the director as an *optional overlay* that respects Piranha Bytes' quests (as SkyrimNet plugins do with vanilla) is likely to be received better than "replacing the hand-written quests".
- Reactivity to concrete, verifiable game events (who you robbed, which camp you joined, what chapter it is) is the most consistently praised feature. Feed the director an event log of real engine facts.

### Gaps
- I could not access Nexus comments, Reddit threads or Steam review text for CHIM, SkyrimNet, IntelEngine or AI Influence to quantify mod-player sentiment on generated quests specifically.

---

## 6. Business and cost lessons: per-player inference cost, subscription vs BYO key, local models

### Takeaway
Cloud inference at runtime is the dominant risk for commercial products: Death by AI, Vaudeville and Teach My Little Sister all hit cost walls within days or weeks. They responded by changing vendor, buying credits, going local or charging subscriptions. Mods sidestep this with BYO API keys (mostly OpenRouter with cheap DeepSeek/Gemma models), free sponsored inference (Player2) or local models. Products that survive split work across models: small or cheap for frequent decisions, larger for occasional prose.

### Cited Findings
- Death by AI: OpenAI + ElevenLabs costs were "not financially sustainable" within 3 days; 1.2B tokens in month one; costs down 95% after the switch — [Inworld case study (vendor)](https://inworld.ai/blog/how-inworld-helped-the-ai-game-death-by-ai-with-20-million-players-reach-profitability)
- Teach My Little Sister How To Drive: over $1,000/day in tokens at a peak of 405 CCU in a free demo, a bank loan, then a move to local models and a paid release with no per-token player charges — [FRVR](https://frvr.com/blog/news/popular-ai-driving-game-devs-forced-to-take-out-loans-as-62000-concurrents-result-in-massive-ai-token-costs/)
- Vaudeville: large first-month Inworld bills, voice too expensive, Steam payout lag — [Game Developer pre-mortem](https://www.gamedeveloper.com/press-release/vaudeville-a-pre-mortem)
- Suck Up!: originally sold tokens to pay for LLM calls; now a fixed-price Steam game ($16.99) — [OpenAI community](https://community.openai.com/t/vampire-game-where-you-convince-llm-to-let-you-in/604295); [Steambase](https://steambase.io/games/suck-up/price)
- Subscriptions: Voyage at $15/$30/$50 per month with a free tier — [TechCrunch](https://techcrunch.com/2026/04/21/voyage-is-an-ai-rpg-platform-for-creating-custom-gaming-worlds-with-ai-generated-npc-interactions); Hidden Door's free unlimited-turn tier plus a subscription — [Arcanum review](https://arcanumrpgs.com/blog/hidden-door-review/); Friends & Fables expected costs to rise 33–100% from its better planning pipeline — [Franz 2.0](https://fables.gg/patch-notes/franz-20-working-context-lore-improved-planning-and-more)
- Medium models over frontier models for speed and cost: Franz — [How Franz works](https://release-marketing-site.fables.gg/blog/how-franz-works); Hidden Door's "Llama-level" model, with the reviewer citing Groq Llama 3 at "$0.59 in/$0.79 out per million tokens" and recommending starting with the best model and degrading later — [Ian Bicking](https://ianbicking.org/blog/2025/08/hidden-door-design-review-llm-driven-game.html)
- Mods use BYO keys: OpenRouter (free Gemma tier or paid), OpenAI, or local koboldcpp — [Arcanum comparison](https://arcanumrpgs.com/blog/skyrim-ai-npc-mods/); usage concentrates on cheap models (SkyrimNet: DeepSeek V3.2 and Gemma 4 31B; Mantella: DeepSeek V4 Flash) — [OpenRouter SkyrimNet](https://openrouter.ai/apps/url/https%3A%2F%2Fgithub.com%2FSkyrimNet-GamePlugin); [OpenRouter Mantella](https://openrouter.ai/apps/url/https%3A%2F%2Fart-from-the-machine.github.io%2F)
- The director should run on a cheap model: "a top-tier model here gets expensive fast" (NarrativeEngine) — [README](https://github.com/pdusen/skyrimnet-plugin-narrative-engine). IntelEngine and SkyrimNet also allow separate models per job — [IntelEngine](https://github.com/galanx/IntelEngine-GamePlugin); [SkyrimNet](https://github.com/MinLL/SkyrimNet-GamePlugin)
- Sponsored inference: Player2 offers free usage or daily credits to players, a revenue share with modders, and claims to share $2M with 1,000 developers over 12 months. It is the recommended or default backend for AI Influence and Sentient Sands — [Elefant/Player2 blog (vendor)](https://blog.elefant.gg/p/we-are-sharing-2m-revenue-with-1000); [Player2 RimWorld story](https://blog.player2.game/p/how-one-rimworld-modder-tripled-his)
- Local or on-device:
  - NVIDIA ACE SLMs (0.5B in inZOI on 8 GB RTX) — [NVIDIA](https://www.nvidia.com/en-us/geforce/news/nvidia-ace-autonomous-ai-companions-pubg-naraka-bladepoint/); PUBG Ally runs on-device — [KRAFTON](https://press.krafton.com/KRAFTON-REVEALS-PLAYTEST-PLANS-FOR-PUBG-ALLY-BUILT-WITH-NVIDIA-ACE)
  - SkyrimLLMBridge: a local 2B at 0.98 s median, 7.1 GB VRAM — [SkyrimLLMBridge](https://github.com/devPirate01/SkyrimLLMBridge)
  - Bumblebee is moving to an "offline Smart NPCs engine" — [Gamespress (snippet)](https://gamespress.com/Bumblebee-presents-its-new-offline-Smart-NPCs-engine)
- Inworld's 2025 exit from game characters ("consistency flawless at scale") is a warning about building on hosted character studios — [Arcanum Inworld timeline](https://arcanumrpgs.com/blog/inworld-ai/)

### Inferences
- For a non-commercial Gothic mod, the subscription route (ChatGPT plan) is closest to the BYO-key route used by the Skyrim mods. The lesson from the commercial failures (unbounded per-turn calls at scale) matters less for a single-user mod. The lesson that does apply is architectural: run the director tick on the local 2B or a cheap model, call the cloud model only when a beat actually dispatches (letter text, quest briefing, NPC lines), and cache per-save generated content.
- Generated side quests are the most expensive form of content per player-minute: multi-agent pipelines (CHIM's three agents plus a fixer; Franz's dozen calls) cost far more than dialogue. Frequency caps and phase-based pacing (NarrativeEngine) control both cost and fatigue.

### Gaps
- No product published per-player-hour inference cost. The Death by AI and Teach My Little Sister figures cannot be normalized per hour from the available data. No mod published typical monthly spend.
- No data on whether the ChatGPT-subscription-token route is used by any public game mod (it is unusual; all mods surveyed use API keys, local models or Player2).
