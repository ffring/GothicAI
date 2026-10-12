# Shipped games with systemic / procedural narrative: directors, storytellers, radiant quests, storylets, and lessons for a hybrid authored + dynamic RPG

Scope: mostly pre-LLM commercial games and their developer write-ups (GDC talks, postmortems, official/modding wikis), read with a Gothic 2 NotR / OpenGothic + external LLM server in mind. Research date: 2026-10-11.

Source-access caveats, stated once here: UESP (ck.uesp.net, skyrimck.uesp.net), the Valve Developer Community wiki, TaleWorlds dev blogs, GeekWire, ScreenHub and the CK3 dev-diary forum page returned HTTP 403/500 to the fetch tool, and the Internet Archive was not reachable. Where a finding below says "(search snippet)", it comes from the search engine's excerpt of that page and was not read in full. The Left 4 Dead slides were read in full (text pulled out of the PDF). The Caves of Qud paper PDF could not be decoded, so it is cited from its abstract and snippet only.

---

## Q1. How does each system work architecturally: data model, selection logic, pacing, state tracking, and what was praised or criticised?

### Takeaway
Shipped systemic-narrative games fall into four families: (1) **pacing directors** that change *when* threats or events arrive based on a player-state metric (L4D, RimWorld); (2) **event/quest templates with role and location slots** that are filled from world state when a trigger fires (Skyrim Radiant, Bannerlord issues, Watch Dogs Legion, Wildermyth, Shadows of Doubt, CK/Paradox); (3) **rule/storylet databases** where a query over world facts picks the most specific or highest-priority piece of content (Valve's dynamic dialog, Hades, Fallen London / Sunless Sea); (4) **persistent simulated or generated actors and histories** that remember the player and create rivalries or lore (Nemesis, Dwarf Fortress, Caves of Qud, WD Legion's Census). The systems players praised all keep their selection logic simple and their state legible. The ones they criticised produced templated fetch tasks (Skyrim radiant, Bannerlord issues) or world changes that reset (GW2).

### Cited Findings

#### Left 4 Dead AI Director (Valve, Michael Booth, AIIDE 2009)
- The design had four stated AI goals: "Deliver Robust Behavior Performances", "Provide Competent Human Player Proxies", "Promote Replayability", "Generate Dramatic Game Pacing". — [Booth, The AI Systems of Left 4 Dead (slides)](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- **Structured Unpredictability** means population is "Not purely random, nor deterministically uniform": several population functions whose space/time vary by a designer-set amount of randomness, laid on top of each other. Example: mobs spawn at a random interval of 90–180 s on Normal. A Boomer's vomit forces a mob spawn and resets the timer. Mob size grows from a minimum just after a spawn to a maximum over time, so frequent mobs stay balanced. — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Population layers, by frequency: wanderers (high), mobs of 20–30 (medium), special infected (medium), bosses (low), weapon caches (low), scavenge items (medium). — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Spatial tools: the navigation mesh; **Flow Distance** (travel distance from the start safe room to every nav area, used "as a metric for populating enemies and loot"); potential visibility; and the **Active Area Set** (nav areas around the team, where the Director creates and destroys population, letting "hundreds of enemies" be made from "a small set of reused entities"). — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Wanderers are stored as a count N per area, randomised at map (re)start from escape-route length and desired density. N is zeroed when an area becomes visible to any survivor or when the Director is in Relax mode. 75% of mobs come from behind, because wanderers, specials and bosses are usually met ahead of the team. — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Bosses are placed every N units along the flow path. "Three Boss events are shuffled and dealt out: Tank, Witch, and Nothing. Successive repeats are not allowed." — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Weapon caches and item groups are **designer-placed candidates, and the Director picks which ones exist**. Reasons given: "Prediction of possible locations beneficial", "Allows visual storytelling/intention", and it avoids placement artefacts. — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- **Survivor Intensity** rises when a survivor is injured (in proportion to damage), incapacitated, or pulled/pushed off a ledge, and when a nearby infected dies (inversely proportional to distance). It decays toward zero, but does NOT decay while infected are actively engaging. The Director tracks the max intensity across all 4 survivors. — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Phase machine:
  - **Build Up**: full threat population until intensity crosses the peak threshold.
  - **Sustain Peak**: 3–5 s more.
  - **Peak Fade**: minimal threats until intensity decays out of the peak range, so the current fight can play out.
  - **Relax**: minimal threats for 30–45 s, "or until Survivors have traveled far enough toward the next safe room", then back to Build Up.
  - In Relax there are no mobs and no new specials. "Boss Encounters NOT affected by adaptive pacing."
  — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Key design statement: "Algorithm adjusts pacing, not difficulty. Amplitude (difficulty) is not changed, frequency (pacing) is"; "Survivor Intensity estimation is crude, yet the resulting pacing works." The pacing was inspired by Counter-Strike: "Constant, unchanging combat is fatiguing", "Long periods of inactivity are boring". — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Survivor bots made it possible to tune for a 4-player team and to run "Automated stress testing with 4 SurvivorBots and accelerated game time". A bot that gets far out of place is teleported back "when no human is looking at it". — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)

#### RimWorld AI storytellers (Ludeon, Tynan Sylvester)
- Cassandra Classic sends events on an "increasing curve of challenge and tension", alternating hard events with breathing room. Phoebe Chillax gives "lots of time between disasters". Randy Random sends "random events" that "don't care if they make a story of triumph or utter hopelessness". — [RimWorld Wiki: AI Storytellers](https://rimworldwiki.com/wiki/AI_Storytellers)
- Inputs checked before a storyteller event: colony wealth, building wealth, colonist count, animal count, recent colonist death or severe wound, and time since the last major event. — [RimWorld Wiki: AI Storytellers](https://rimworldwiki.com/wiki/AI_Storytellers)
- **Adaptation**: the score grows over time and is "knocked down when you take damage". Two settings: growth rate and impact. At "Strive to survive" both are 100%; "Losing is fun" cuts impact to 40%. — [RimWorld Wiki: AI Storytellers](https://rimworldwiki.com/wiki/AI_Storytellers)
- Cassandra's XML: `minDaysPassed 11.0`, `onDays 4.6`, `offDays 6.0`, `minSpacingDays 1.9` (minimum gap between major threats), `numIncidentsRange 1~2`, misc events `mtbDays 4.8`. The cycle restarts every 10.6 days. She opens with a scripted mad animal and then a single raider (day 5). She averages about 8.5 major threats per year, and players find her "more difficult than Randy, since her raids are consistent". — [RimWorld Wiki: Cassandra](https://rimworldwiki.com/wiki/Cassandra)
- Phoebe uses `onDays 8 / offDays 8` (search snippet). — [RimWorld Wiki: Phoebe Chillax](https://rimworldwiki.com/wiki/Phoebe_Chillax)
- Threat budget formula: Raid Points = (Wealth Points + Pawn Points) × Difficulty × Starting Factor × Adaptation Factor.
  - "Storyteller Wealth" = items + people/animals + 0.5 × buildings.
  - Wealth points scale from 0 at ≤14,000 wealth to 2,400 at 400,000.
  - Caps: 1,000,000 wealth and 10,000 points.
  - The adaptation factor is shared across settlements.
  — [RimWorld Wiki: Raid points](https://rimworldwiki.com/wiki/Raid_points)
- Randy keeps a minimum colonist-adding incident chance of 0.08%, against 0.02% for the others at high population: a "population intent" curve. — [RimWorld Wiki: AI Storytellers](https://rimworldwiki.com/wiki/AI_Storytellers)
- Incident defs carry per-incident gating such as base chance, minimum refire days and minimum threat points (e.g. "Base chance 0.75", "Min. refire 6 days"). These are seen in mod discussions, not official docs (search snippet; low-authority source). — [Steam Workshop discussion](https://steamcommunity.com/sharedfiles/filedetails/comments/2887952810)
- RimWorld bills itself as "A story generator by Tynan Sylvester". The storyteller is described as modelled on L4D's AI Director (secondary blog summary of the GDC 2017 talk). — [Habrador blog](https://blog.habrador.com/2019/06/why-rimworld-sold-more-than-1-million.html)

#### Skyrim Radiant Story / Story Manager (Bethesda)
- The Story Manager "is a nested node tree that holds all of the radiant story quests and is used to determine which quest or quests are started as a result of receiving a Story Manager Event from the game" (search snippet). — [CK wiki: Radiant Story](https://ck.uesp.net/wiki/Radiant_Story)
- Event nodes have a "Do all before repeating" flag: the Story Manager tries every quest in the node before re-starting one that already ran (search snippet). — [CK wiki: SM Event Node](https://skyrimck.uesp.net/wiki/SM_Event_Node)
- **Aliases**: "An Alias is a Reference or a Location used by a Quest". It is "filled" by picking from the world. The engine filters out every candidate that fails the conditions, then picks at random from the rest. A location can act as a **seed alias**, picked first for its features (creature types, hold, front door, "boss" actor), and the other aliases are then filled relative to it (search snippet). — [CK wiki: Bethesda Tutorial Radiant Quests](https://ck.uesp.net/wiki/Bethesda_Tutorial_Radiant_Quests)
- Player-facing behaviour:
  - Most radiant quests have "about a dozen" possible locations, chosen by occupants or hold (e.g. "any Hagraven nest or any Bandit camp in Falkreath Hold"); some have 140+.
  - The system "aims to send you to a location that you have not yet discovered".
  - Radiant quests are used by the Companions, Thieves Guild and Dark Brotherhood, by bounties and by repeatable jobs.
  - Radiant also drives world interactions such as inheritance couriers when a befriended NPC dies and hired thugs after theft or violence.
  — [UESP: Skyrim:Radiant](https://app.uesp.net/wiki/Skyrim:Radiant)
- Lead designer Bruce Nesmith:
  - The team defined **30 player actions** and matching world reactions.
  - They cut NPCs claiming bought items were stolen ("it didn't feel good; you ended up always killing the guy").
  - They limited job quests to fitting NPCs and added custom lines per voice type.
  - They tightened conditions because "every time I drop something on the ground, half the town comes over, it is starting to get really silly".
  - On a radiant main quest: "Sounds great, would play horribly"; "really easy going too far with that kind of thing"; "If we pass the buck to a system, it's not the same."
  — [GamesBeat: Nesmith on Radiant Story](https://gamesbeat.com/bethesdas-nesmith-reflects-on-the-difficult-birth-of-skyrims-radiant-story-system/)
- Criticism: reviews called radiant content a technical achievement that yields "terribly repetitive" "go here, get this, come back" quests (search snippet). — [CheatCC Skyrim review](https://cheatcc.com/articles/the-elder-scrolls-v-skyrim-review-for-playstation-3-ps3-ps3/)

#### Middle-earth: Shadow of Mordor / Shadow of War — Nemesis System (Monolith)
- **Data model**: each captain gets a random role (warrior, archer, hunter, savage, defender, olog), a clan (8 options) and "a random set of traits from skills, weaknesses, and angers", plus a random name, armour and personality. Levels run 1–60 and rise by killing the player or completing map encounters. The hierarchy runs captains → veteran captains → warchiefs (with bodyguards) → overlord of a fortress. Dead captains are replaced. Dominated captains can be sent to assassinate others and climb the ranks, and some betray the player. — [Game Developer: Core System Analysis of Shadow of War](https://www.gamedeveloper.com/design/core-system-analysis-of-middle-earth-shadow-of-war)
- When the player dies to a leader, the current mission is cancelled and that leader gains power. Uruks remember encounters: one thrown into fire wants revenge for being disfigured. Branding lets the player trigger infighting. Critics called it "gimmicky": it added "different appearances and weaknesses to villains without adding personality", and felt "unappealing until the game's second half". — [Wikipedia: Shadow of Mordor](https://en.wikipedia.org/wiki/Middle-earth:_Shadow_of_Mordor)
- In his GDC 2018 talk, Chris Hoge framed the design goal as "maximizing players' positive and negative emotions", around "making Orcs more memorable", "designing exceptional moments" and "keeping the player-Orc relationships going". — [GDC news: Shadow of War Nemesis talk](https://gdconf.com/news/attend-gdc-2018-behind-scenes-look-shadow-wars-nemesis-system)
- **Patent**: US 10,926,179, "Nemesis characters, nemesis forts, social vendettas and followers in computer games". Filed March 2015, rejected and revised six times, granted 23 Feb 2021, maintainable until 2035. — [PatentArcade](https://www.patentarcade.com/video-game-patent/US10926179/nemesis-characters-nemesis-forts-social-vendettas-and-followers-in-computer-games)
  - One outlet says the protection runs until 2036, a conflict with the 2035 date. It also says the patent outlives the studio's closure. — [Mezha](https://mezha.ua/en/news/shadow-of-mordor-s-nemesis-system-is-protected-by-patent-until-2036-even-after-the-studio-closes-300106/)
  - Fans criticised WB for holding the patent without using it. — [Push Square, 2025](https://www.pushsquare.com/news/2025/02/fans-are-fuming-warner-bros-patented-the-nemesis-system-but-isnt-using-it)

#### Crusader Kings / Paradox event systems
- CK2 event = **trigger** + **mean_time_to_happen** (MTTH) + **options**.
  - "An MTTH of 90 days... means that after 90 days, the probability of the event having been fired is exactly 50%", and modifiers scale MTTH multiplicatively.
  - Pre-triggers such as `only_playable` cut evaluation from about 20,000 characters to about 1,000.
  - `is_triggered_only` events fire only from decisions, other events or **on_actions** (hard-coded hooks such as marriage, death, battle), which use `weight_multiplier` instead of MTTH.
  - Chains schedule follow-ups with delays (`character_event = { id = 38001 days = 1825 }`) that keep their scopes.
  - Very low MTTH causes performance trouble.
  — [CK2 Wiki: Event modding](https://ck2.paradoxwikis.com/Event_modding)
- In CK3, events fire entirely through on_actions: action hooks or regular pulses (e.g. yearly or every five years) with weighted `random_events` lists. MTTH was dropped because pure probability made it hard to balance many events against each other, "caused statistical anomalies, and tanked performance" (search snippet summarising the CK3 dev diary; full page returned 500). — [CK3 Dev Diary #30: Event Scripting](https://forum.paradoxplaza.com/forum/developer-diary/crusader-kings-3-dev-diary-30-event-scripting.1397140)
- CK3 **story cycles**: "essentially an event manager that fires events periodically and stores any related values for them".
  - Hooks: `on_setup`, `on_end`, `on_owner_death`.
  - `effect_group`s pulse at day/month/year intervals or ranges, with a `chance` and triggers.
  - Ownership can pass to an heir.
  - Used, for example, for pets.
  — [CK3 Wiki: Story cycles modding](https://ck3.paradoxwikis.com/Story_cycles_modding)

#### Wildermyth (Worldwalker Games)
- Nate Austin: the game "alternates layers of handcrafted and procedural content". A central narrative with a defined beginning, middle and end sits alongside procedural events that arise from combat and hero personalities. — [Wikipedia: Wildermyth](https://en.wikipedia.org/wiki/Wildermyth)
- **Casting** metaphor: "A play has roles that it needs to fill, so you put out a casting call. One play might require a leader, a hothead, and a goofball." Inline markup varies lines "based on personality, relationship status, or really anything in the game". The focus is breadth across themes, monsters and biomes, with follow-up stories as later depth. — [TurnBasedLovers interview](https://turnbasedlovers.com/10-turns-interview/with-wildermyth-developer)
- Event inputs:
  - Typed targets with filters. "Almost always, it's a good idea to call out heroes by some particular personality trait, relationship, hook, other aspect, or primary stat."
  - `party` = heroes present; HERO targets can reach absent heroes, with `notAlreadyMatchedAs`.
  - `overlandTile` / `site` context.
  - Company and legacy state.
- Event outputs: gear, limb replacement, history lines, stats, aspects, map changes, relationships, recruit or sacrifice.
- Guidance: "Choices that give you something you want at a high cost are great"; "Permanent, visible character change is sort of the gold standard."
— [Wildermyth Wiki: Story Inputs and Outputs](https://wildermyth.com/wiki/Story_Inputs_and_Outputs)
- **Aspects** are named markers on entities that reflect narrative state. Later events can filter on them, which is how chains work. Events trigger on things like arriving at a hostile site, winning a mission or a plot event (search snippet). — [Wildermyth Wiki: Modding Guide](https://wildermyth.com/wiki/Modding_Guide)
- The GDC talk covers emotional investment in procedural heroes and its pitfalls: "breach of trust, acting out of character, or rage quits". — [GDC Vault: Austin, Wildermyth](https://www.gdcvault.com/play/1027614/Independent-Games-Summit-Session-Getting)

#### Dwarf Fortress and Caves of Qud (procedural history)
- Tarn Adams set out to make a "fantasy world generator". He noted many games make maps, but could not think of one before 2006 "that tries to make a history". Legends mode records civilisations, figures and the player's deeds, and the world persists across games (search snippet). — [Wikipedia: Dwarf Fortress](https://en.wikipedia.org/wiki/Dwarf_Fortress)
- Caves of Qud generates five procedural sultans and keeps a fixed sixth, Resheph. Their "names, events from their lives, bits of lore that recount those events, and recurring sultan themes" are procedural. Later sultans get procedural historic sites with cults drawn from favoured factions. "a few of the main quest locations are pre-determined or pre-built, such as Grit Gate". — [Caves of Qud Wiki: World generation](https://wiki.cavesofqud.com/wiki/World_generation)
- The Qud method: a state machine plus a replacement grammar that "first generates historical events and rationalizes them ex post facto". It treats history as rhetoric, with bias and conflicting accounts (paper abstract, via search snippet). — [Grinblat & Bucklew 2017, "Subverting Historical Cause & Effect"](https://pcgworkshop.com/archive/grinblat2017subverting.pdf)
- The GDC talk sets this against full simulation (Dwarf Fortress, Epitaph). Goal: "rich, convincing histories on a tight scope budget" using replacement grammars. — [GDC Vault: Procedurally Generating History in Caves of Qud](https://gdcvault.com/play/1024990/Procedurally-Generating-History-in-Caves)

#### Valve dynamic dialog (Elan Ruskin, GDC 2012)
- The system uses "hundreds of facts about the world in a fuzzy pattern match against a database of thousands of possible lines". Characters "remember history, cascade from special to general cases". Writers can add "special cases, running gags, and track additional facts without forcing programmers to change thousands of lines of code". It supports "self-branching conversation where each followup line is a new query" (search summary of the abstract). — [GDC Vault: AI-driven Dynamic Dialog through Fuzzy Pattern Matching](https://www.gdcvault.com/play/1015528/AI-driven-Dynamic-Dialog-through)
- The most specific matching rule wins, falling back to less specific ones, e.g. a special quip when attacked in the circus. The result is "a strong illusion of situational awareness", with lines that can be interrupted when combat breaks out. Ruskin stressed "making tools that writers find comfortable to use". — [Emily Short on Ruskin's talk](https://emshort.blog/2012/03/16/gdc-2012-talk-on-dynamic-dialogue/)
- Used in Left 4 Dead 2 and Dota 2 (search snippet). — [GDC 2012 news](https://gdconf.com/news/gdc_2012_adds_valve_double_fin)
- An open-source re-implementation (Subtale's **Mimir**, Rust): facts form a Query; Rules hold Evaluators; Rulesets return the rule with the most satisfied requirements, with ties broken at random. — [GitHub: subtalegames/mimir](https://github.com/subtalegames/mimir)
- Robert Yang's reading of the same structure as narrative logic: a "self-branching structure" that picks "the most specific plot event that fits". — [Radiator blog](https://www.blog.radiator.debacle.us/2012/07/rule-databases-for-contextual-narrative.html)

#### Failbetter: quality-based narrative and storylets (Fallen London, Sunless Sea)
- In QBN, "Qualities are numerical variables that can go up or down during play, and represent absolutely everything from inventory... to skills... to story progress". Storylets are unlocked by qualities, and "it allows a lot of short stories to slot together".
  - Weaknesses: manual bookkeeping; content feels thin until the pool is large; long storylet lists overwhelm players.
  - Salience-based narrative picks "which content element is judged to be most applicable" (L4D, Firewatch). Its risks are a weak overall arc and score tuning by trial and error.
  — [Emily Short: Beyond Branching](https://emshort.blog/2016/04/12/beyond-branching-quality-based-and-salience-based-narrative-structures/)
- A storylet = content + prerequisites + effects on world state. It supports patterns such as gauntlet, branch-and-bottleneck, sorting hat and loop-and-grow. "Time caves" (branches that never rejoin) cancel out storylets' reusability. — [Emily Short: Storylets: You Want Them](https://emshort.blog/2019/11/29/storylets-you-want-them/)
- Fallen London's **Opportunity Deck**:
  - Hand size runs from 2 to 5 cards depending on lodgings.
  - Decks are shared by area, with location-specific cards.
  - "Autofire" cards must be played; most others can be discarded.
  - Some cards vanish when their requirements stop being met.
  — [Fallen London Wiki: Cards](https://fallenlondon.wiki/wiki/Cards)
  - The deck refills about one card per 10 minutes, and Menace qualities at 5+ insert penalty cards (search snippet). — [Fallen London Wiki: Cards](https://fallenlondon.wiki/wiki/Cards)
- Alexis Kennedy later rejected the "QBN" label. Treating all qualities alike ("PC characteristics are the same as currency amounts are the same as story tracking variables") erased useful distinctions, which then had to be "hacked in". He prefers "resource narrative": resources that align "with the grain of the story", where events "emerge in a natural-seeming way from the combination of resource states". Resources should be "scarce, reproducible and fungible". — [Weather Factory: resource narrative](https://weatherfactory.biz/?p=6991)

#### Watch Dogs: Legion (Ubisoft Toronto) — Census and missions for any protagonist
- Census "can basically fill in all of the blanks in a plausible way". It cascades occupation → workplace, income → housing, hobbies, relationships and schedules. Characters are "uprezzed" into full simulation when observed (24 h schedules, off-screen travel), and low-priority NPCs are deleted off-screen.
- Recruitment missions are **templates** of gameplay beats + location *types* + required *roles*, filled at runtime.
- Every line was "written and recorded 20 times" for personas, and formant modulation multiplied voices.
- Sentiment spreads through the social graph (friends and family, not by geography), and enough negative sentiment triggers **revenge missions**.
- A "player attention system" makes systems ask permission before interrupting narrative missions, based on priority and player idle time.
— [Game Developer: How WD Legion's play-as-anyone simulation works](https://www.gamedeveloper.com/design/how-watch-dogs-legion-s-play-as-anyone-simulation-works)
- Narrative designer Brandon Hennessy: "We can't know a character's age, gender, sexuality, occupation, etc." The fix was fixed-identity **Anchors**, mostly antagonists: "Introducing a single authored character into a big sea of procedural characters makes them cohere a bit." The cost: weaker protagonist characterisation, and permadeath makes heroes temporary. — [Game Developer: how WD Legion made Ubisoft rethink storytelling](https://www.gamedeveloper.com/marketing/how-i-watch-dogs-legion-i-made-ubisoft-rethink-its-storytelling)
- GDC 2021 talks on Census and on missions "played as anyone" (Christopher Dragert). — [GDC Vault: Census](https://gdcvault.com/play/1027018/Census-The-Systemic-Backbone-Behind); [GDC Vault: Missions Played As Anyone](https://www.gdcvault.com/play/1027145/AI-Summit-Missions-Played-As)

#### Shadows of Doubt (ColePowered)
- The city, its citizens and routines, and every murder case are generated per save. Murders can happen anywhere, involve any citizen and use different methods. Evidence includes fingerprints, CCTV and witnesses. — [Wikipedia: Shadows of Doubt](https://en.wikipedia.org/wiki/Shadows_of_Doubt)
- The introductory case is the only hand-crafted one. Different murder types point to different suspect pools: a business-card message points to a coworker or corporate rival, hearts and "X and Y forever" point to a stalker. — [HackerNoon guide](https://sia.hackernoon.com/shadows-of-doubt-a-guide-to-investigating-crime-scenes-and-solving-murder-cases)
- The developer blogged on "Scripted Missions in a Procedural World" (as a video) and on generating citizens. — [Devblog #18](https://colepowered.itch.io/shadows/devlog/111292/shadows-of-doubt-devblog-18-scripted-missions-in-a-procedural-world); [Devblog #23](https://colepowered.itch.io/shadows/devlog/189689/shadows-of-doubt-devblog-23-generating-citizens-part-1)

#### Other examples
- **Mount & Blade II: Bannerlord** — **Issues** link quests to settlement management: an issue lowers morale, food or prosperity until a quest solves it. Quests are generated from live situations, e.g. bandits blocking trade lead a notable to ask for the hideout to be cleared (search snippet of TaleWorlds dev blog). — [TaleWorlds Dev Blog](https://www.taleworlds.com/en/Games/Bannerlord/Blog/24)
  - TaleWorlds planned to improve "the issue creation algorithm" so players see a broader range of issue quests (search snippet). — [GameBanshee: Bannerlord plans](https://www.gamebanshee.com/383c4)
  - Players complain that bound-village issues come back immediately and drain prosperity and loyalty. Mods such as "Governors Handle Issues" exist (player forum). — [Steam discussion](https://steamcommunity.com/app/261550/discussions/0/3008927444647027216)
- **Guild Wars 2 dynamic events** — Colin Johanson:
  - Chains "can reset… they move along paths". Kill the centaur commander, and "eventually the centaurs will choose a new commander" and the chain restarts.
  - "If we wanted every event we designed to run only once, we'd need to hire approximately 100,000 people."
  - ArenaNet avoided the word "quest" (Eric Flannum).
  — [GameBanshee: GW2 Dynamic Events Q&A](https://www.gamebanshee.com/9xd)
  - Events run without players, and "the enemy will take over" (Martin Kerstein). — [Engadget](https://www.engadget.com/2011-05-18-living-breathing-world-martin-kerstein-expounds-on-guild-war.html)
  - Escalation pattern: ignored soldiers → camp → village captured → liberation event. — [TenTonHammer](https://www.tentonhammer.com/articles/dynamic-events-in-guild-wars-2-living-in-a-living-world.amp)
  - GDC talk. — [GDC Vault: Designing GW2 Dynamic Events](https://gdcvault.com/play/1013691/Designing-Guild-Wars-2-Dynamic)
- **Hades (Supergiant)** — dialogue snippets are "tagged with priority levels" and have unlock conditions, and the game "selects the most interesting interaction". After first beating Meg, Achilles skips chit-chat for the higher-urgency Meg conversation. Criticism: the content often arrives in jarring orders, "watching a stage actor forget which scene they're in" (critic Antony de Fault). — [Film Stories](https://filmstories.co.uk/?p=83138)
  - More than 22,000 voiced lines from a studio of fewer than 20 people (search snippet). — [Game Developer: Hades dialogue at GDC 2021](https://gamedeveloper.com/audio/dive-into-the-dialogue-of-i-hades-i-at-gdc-2021)
  - Players report a pool of unique, requirement-gated lines plus recycled filler when nothing unique is eligible; unlocked lines may wait several encounters (player forum). — [Steam discussion](https://steamcommunity.com/app/1145360/discussions/0/2987539784417139179)
- **Elite Dangerous** — players report that missions often do not visibly move the Background Simulation and that BGS rules are learned "through experimentation" (player forums; low authority). — [Frontier forum](https://forums.frontier.co.uk/threads/missions-not-impacting-bgs-especially-during-elections-in-pp-systems.633066); [ElitePVE](https://elitepve.com/viewtopic.php?p=45734)
- **Ubisoft Ghostwriter** (La Forge, GDC 2023) — a writer defines a character and an interaction type, and the tool proposes variations for barks that the writer picks and edits. It is meant to support writers, not replace them. — [Ubisoft News: Ghostwriter](https://news.ubisoft.com/en-us/article/7Cm07zbBGy4Xml6WgYi25d/the-convergence-of-ai-and-creativity-introducing-ghostwriter)

### Inferences
- Every successful director here is a **small state machine over one or two cheap scalar metrics**: L4D's survivor intensity, RimWorld's on/off cycle × threat points × adaptation. Neither does story reasoning. Story texture comes from authored or templated content placed into the slots the director opens. An LLM director should likewise decide only *what kind of beat* and *when*, not invent the whole beat unconstrained.
- "Designer-placed candidates, director picks which exist" (L4D items, Skyrim's dozen locations per quest) is the main bridge between authored geography and dynamic choice. It maps directly onto Gothic's hand-placed waypoints, freepoints and spawn spots.
- Radiant, Bannerlord and GW2 show the same failure: a template with few slots, repeated at high frequency and with no lasting world change, is felt as a chore no matter how varied the slot fillers are.

### Gaps
- No official description of Skyrim's per-node "hours until reset" or "num quests to run" fields could be read (UESP 403).
- No primary Supergiant description of Hades' priority tiers was found. The mechanism is known only from critics and players.
- No primary description of Shadows of Doubt's case-generation algorithm (murderer selection, clue planting, solvability guarantees) was found; it is only in a devlog video.
- Stellaris events were not researched separately. They use the Clausewitz event scripting family, as CK does, but this was not verified.
- No Ubisoft narrative tool named "Mimir" was found. The only "Mimir" located is Subtale's open-source Ruskin-style rule engine.
- Kenshi, Darkest Dungeon, Ultima Online and EverQuest dynamic events were not researched, beyond Tynan Sylvester's remark on UO's failed ecology (Q2).

---

## Q2. How do these games keep coherence with an authored main plot, and how do they avoid procedural fatigue and fetch-quest repetition?

### Takeaway
The shipped answer is **separation plus anchoring**. The main plot stays authored and is kept out of the procedural system (Nesmith: a radiant main quest "would play horribly"; Qud's pre-built main-quest sites; Wildermyth's authored campaign; WD Legion's authored Anchors). Procedural content is anchored to persistent, recognisable entities and to visible, permanent consequences. Fatigue comes from low *perceptual uniqueness* (Compton's oatmeal), from templates that ignore context, and from cycles that reset the world. Teams fight it with tight casting conditions, no-repeat decks, priority/salience for context-specific content, and restraint in frequency.

### Cited Findings
- Bethesda kept Radiant out of the main quest, "Sounds great, would play horribly", and limited it to faction side-quests, jobs and world reactions. They tightened conditions after systemic reactions became "really silly". — [GamesBeat: Nesmith](https://gamesbeat.com/bethesdas-nesmith-reflects-on-the-difficult-birth-of-skyrims-radiant-story-system/)
- Radiant deliberately prefers undiscovered locations, which ties generated quests to exploration rather than repetition. — [UESP: Skyrim:Radiant](https://app.uesp.net/wiki/Skyrim:Radiant)
- Caves of Qud keeps main-quest locations "pre-determined or pre-built" while generating sultan history and other sites around them. — [Caves of Qud Wiki: World generation](https://wiki.cavesofqud.com/wiki/World_generation)
- Wildermyth "alternates layers of handcrafted and procedural content", with an authored beginning/middle/end. — [Wikipedia: Wildermyth](https://en.wikipedia.org/wiki/Wildermyth)
- WD Legion added authored Anchors because "a single authored character into a big sea of procedural characters makes them cohere a bit". — [Game Developer: WD Legion storytelling](https://www.gamedeveloper.com/marketing/how-i-watch-dogs-legion-i-made-ubisoft-rethink-its-storytelling)
- WD Legion's "player attention system" stops systemic interruptions from breaking narrative missions. — [Game Developer: WD Legion simulation](https://www.gamedeveloper.com/design/how-watch-dogs-legion-s-play-as-anyone-simulation-works)
- L4D keeps authored bosses outside adaptive pacing, because "Overall pacing affected too much if they are missing", and blocks successive repeats (Tank, then Tank) with a shuffled deal. — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Kate Compton: "I can easily generate 10,000 bowls of plain oatmeal… mathematically speaking they will all be completely unique. But the user will likely just see a lot of oatmeal."
  - Perceptual *differentiation* (not identical to the last one) is far easier than perceptual *uniqueness* (memorable character).
  - "the most reliable generators are the ones where you can concretely describe constraints".
  - Humans like "evidence of process and forces".
  — [Kate Compton: So you want to build a generator](https://galaxykate0.tumblr.com/post/139774965871/so-you-want-to-build-a-generator)
- Tynan Sylvester:
  - "The whole value of a game is in the mental model of itself it projects into the player's mind."
  - Dense simulations become "hairballs" whose events players never perceive.
  - Design for "story-richness"; use "minimum viable representation" plus cosmetic "hair complexity".
  - Rely on **apophenia**, with "condensed systems of simple, understandable hints".
  - Ultima Online's ecology failed because players destroyed it faster than they could interact with it meaningfully.
  — [Tynan Sylvester: The Simulation Dream](https://tynansylvester.com/2013/06/the-simulation-dream/)
- Ruskin-style salience: broad defaults first, then more salient special cases for specific situations ("easy to build a rudimentary set of content with sensible, broad defaults, and then gradually add new, more salient content"). The risk is a weak overall arc, so salience fits reactive and emotional layers better than plot sequencing. — [Emily Short: Beyond Branching](https://emshort.blog/2016/04/12/beyond-branching-quality-based-and-salience-based-narrative-structures/)
- Coherence through structure rather than plot continuity: in 80 Days, "The level of drama [is] proportional to longitude" (Jon Ingold). Sunless Sea's loop is "You leave port; you cross the darkness; you find the lights" (Kennedy). — [Game Developer: modular storytelling](https://www.gamedeveloper.com/design/-i-sunless-sea-i-i-80-days-i-and-the-rise-of-modular-storytelling)
- Sunless Sea's roguelike permadeath fought its CRPG stories: early stories had to be replayed, and "the early-game experience stayed rawer and grindier than it should have been". Long-time early-access players dominated feedback and never saw the early game. — [Game Developer: Sunless Sea postmortem](https://www.gamedeveloper.com/audio/postmortem-failbetter-games-i-sunless-sea-i-)
- Kennedy: resources must align "with the grain of the story". Treating story-progress variables like currency erased distinctions. — [Weather Factory](https://weatherfactory.biz/?p=6991)
- GW2's cycling chains are a deliberate trade-off against content cost (the "100,000 people" remark), so world changes are temporary. — [GameBanshee: GW2 Q&A](https://www.gamebanshee.com/9xd)
- Wildermyth's guidance for avoiding hollow events: "Permanent, visible character change is sort of the gold standard". Choices should give "something you want at a high cost". — [Wildermyth Wiki](https://wildermyth.com/wiki/Story_Inputs_and_Outputs)
  - The talk names "acting out of character" and "breach of trust" as the main ways procedural characters lose players. — [GDC Vault: Wildermyth](https://www.gdcvault.com/play/1027614/Independent-Games-Summit-Session-Getting)
- Nemesis criticism: randomised looks and weaknesses without personality felt "gimmicky". — [Wikipedia: Shadow of Mordor](https://en.wikipedia.org/wiki/Middle-earth:_Shadow_of_Mordor)
- Hades' priority picking can surface lines in an incoherent order. — [Film Stories](https://filmstories.co.uk/?p=83138)
- Bannerlord issue spam shows high-frequency templated quests turning into chores. — [Steam discussion](https://steamcommunity.com/app/261550/discussions/0/3008927444647027216)

### Inferences
- For Gothic, the authored chapter plot (Kapitel 1–6, MIS_* states, key NPC deaths and guild choices) should be **read-only input** to the dynamic layer: the dynamic system can react to it and decorate it, but never advance or contradict it. This is the "radiant never touches the main quest" rule.
- An LLM is very good at the Qud move, "generate events, then rationalize them after the fact" in fitting prose. That is the safest place to use it. Letting it decide plot-critical state transitions is the riskiest.
- The fixes for fatigue are: fewer but more context-specific dynamic quests; each one cast from *named* NPCs with a history the player has seen; consequences that persist (an NPC remembers, a camp stays cleared, a relative appears); and no visible reset loops.

### Gaps
- No quantitative player-retention data was found on radiant or issue repetition.
- No developer postmortem on Skyrim radiant quest frequency tuning was found.

---

## Q3. How do they track state (qualities, flags, world facts) and decide what to offer next (salience/priority, prerequisites, cooldowns, pacing curves)?

### Takeaway
State is almost always a **flat key-value fact store**: qualities, aspects, flags, story variables, plus per-entity records (orc traits/rank, Census profiles, sentiment). Selection follows one of four logics: (a) **trigger → filter by conditions → random among eligible** (Skyrim aliases, CK on_actions with weights); (b) **most-specific-match** (Ruskin/Mimir); (c) **priority tiers with a fallback pool** (Hades); (d) **stochastic timers under pacing constraints**: MTTH/MTB, on/off cycles, minimum spacing, refire cooldowns, no-repeat shuffles, and intensity-gated phases.

### Cited Findings
- **Fact stores**:
  - Ruskin's system matches a query of "hundreds of facts" against rules, and the remembered facts are written back. — [GDC Vault: Ruskin](https://www.gdcvault.com/play/1015528/AI-driven-Dynamic-Dialog-through)
  - Mimir models facts as a `Query` map, `Rule`s with `Evaluator` predicates, and `Ruleset` selection by most satisfied requirements, with random tie-breaks. — [GitHub: mimir](https://github.com/subtalegames/mimir)
- **Qualities** are numeric variables for everything (inventory, skills, story progress). — [Emily Short: Beyond Branching](https://emshort.blog/2016/04/12/beyond-branching-quality-based-and-salience-based-narrative-structures/)
  - Kennedy argues for separate types: character stats, currency and story-tracking variables. — [Weather Factory](https://weatherfactory.biz/?p=6991)
- **Aspects** (Wildermyth) are named narrative markers on entities that later events filter on. — [Wildermyth Wiki: Modding Guide](https://wildermyth.com/wiki/Modding_Guide); [Story Inputs and Outputs](https://wildermyth.com/wiki/Story_Inputs_and_Outputs)
- **Persistent narrative objects**: CK3 story cycles keep variables, pulse effect groups at intervals with a `chance`, and pass to heirs. — [CK3 Wiki: Story cycles](https://ck3.paradoxwikis.com/Story_cycles_modding)
- **Per-actor memory**: Nemesis captains carry traits, rank and level, and remember encounters. — [Game Developer: Shadow of War](https://www.gamedeveloper.com/design/core-system-analysis-of-middle-earth-shadow-of-war)
  - WD Legion characters hold permanent opinions that spread through the social graph and cross thresholds that trigger revenge missions. — [Game Developer: WD Legion](https://www.gamedeveloper.com/design/how-watch-dogs-legion-s-play-as-anyone-simulation-works)
- **Trigger → node tree → conditions → cast aliases**: Story Manager events walk a node tree. Quests in a node can cycle with "Do all before repeating". Aliases filter candidates by conditions and pick at random, with a seed location first (search snippets). — [CK wiki: SM Event Node](https://skyrimck.uesp.net/wiki/SM_Event_Node); [CK wiki: Radiant Quests tutorial](https://ck.uesp.net/wiki/Bethesda_Tutorial_Radiant_Quests)
- **Probabilistic timers**:
  - CK2 MTTH (90 days = 50% by day 90, multiplicative modifiers). — [CK2 Wiki](https://ck2.paradoxwikis.com/Event_modding)
  - CK3 replaced it with on_action pulses plus weighted random lists, for balance and performance (search snippet). — [CK3 Dev Diary #30](https://forum.paradoxplaza.com/forum/developer-diary/crusader-kings-3-dev-diary-30-event-scripting.1397140)
  - RimWorld misc events use `mtbDays 4.8`, and major threats use on/off windows with `minSpacingDays 1.9`. — [RimWorld Wiki: Cassandra](https://rimworldwiki.com/wiki/Cassandra)
- **Budgeted intensity**: RimWorld sizes threats with a points budget from wealth, pawns, difficulty and adaptation. — [RimWorld Wiki: Raid points](https://rimworldwiki.com/wiki/Raid_points)
  - L4D gates spawn types by Director phase, driven by max survivor intensity. — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- **No-repeat and deck mechanics**:
  - L4D deals Tank/Witch/Nothing shuffled, with no successive repeats. — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
  - Fallen London has a limited hand, area decks, autofire must-play cards, and cards that vanish when requirements fail. — [Fallen London Wiki: Cards](https://fallenlondon.wiki/wiki/Cards)
  - Skyrim has "Do all before repeating". — [CK wiki: SM Event Node](https://skyrimck.uesp.net/wiki/SM_Event_Node)
- **Priority with a fallback pool**: Hades serves high-priority, condition-unlocked conversations before chit-chat. — [Film Stories](https://filmstories.co.uk/?p=83138)
  - Players report recycled lines when nothing unique is eligible. — [Steam discussion](https://steamcommunity.com/app/1145360/discussions/0/2987539784417139179)
- **Interrupt arbitration**: WD Legion systems request permission to interrupt, by priority and player idle time. — [Game Developer: WD Legion](https://www.gamedeveloper.com/design/how-watch-dogs-legion-s-play-as-anyone-simulation-works)
- **Adaptation to player state**:
  - RimWorld's adaptation drops after damage and colonist losses. — [RimWorld Wiki: AI Storytellers](https://rimworldwiki.com/wiki/AI_Storytellers)
  - L4D's Relax follows a peak and ends after 30–45 s or after enough forward travel. — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- **Performance-aware filtering**: CK2 pre-triggers cut candidates from about 20k to about 1k characters. — [CK2 Wiki](https://ck2.paradoxwikis.com/Event_modding)
  - WD Legion uprezzes only the NPCs the player observes. — [Game Developer: WD Legion](https://www.gamedeveloper.com/design/how-watch-dogs-legion-s-play-as-anyone-simulation-works)
- **Location preference**: Radiant aims at undiscovered locations. — [UESP: Skyrim:Radiant](https://app.uesp.net/wiki/Skyrim:Radiant)

### Inferences
- A practical selection pipeline for an LLM-assisted director:
  1. Event trigger (Daedalus/engine hook or timer pulse).
  2. Hard filter by prerequisites (chapter, guild, location, NPC alive, cooldowns, not during authored sequences).
  3. Score: specificity (Ruskin) + priority tier (Hades) + pacing fit (L4D/RimWorld phase) − recency/repetition penalty.
  4. Weighted-random pick among the top-k, with no-repeat (deck/"do all before repeating").
  5. Cast roles from the fact store (Skyrim aliases / Wildermyth roles).
  6. Only then ask the LLM to write the texture and dialogue inside a validated schema.
- Keep the authoritative fact store **outside the LLM**: in the Python server, mirrored from Daedalus variables. The LLM receives a curated slice, the Ruskin "query".
- Typed state (Kennedy) maps naturally onto Gothic: character stats and guild (engine), resources (gold, ore, items), story progress (MIS_* / chapter), relationships and memories (new server-side store), and dynamic quest instances (story-cycle-like objects with their own timers and end conditions).

### Gaps
- Exact Hades priority tiers and requirement schema were not found from a primary source.
- Official RimWorld incident fields (`minRefireDays`, `earliestDay`) were seen only in mod discussions.
- The Story Manager's numeric fields (reset hours, max concurrent) could not be read.

---

## Q4. What are their authoring costs and tooling?

### Takeaway
Systemic narrative cuts *plot-branching* cost, but moves cost into **content volume, variants, conditions and testing**. GW2 put one-shot events at "100,000 people". WD Legion recorded every line 20 times. Hades has 22k+ voiced lines. Successful teams invested in **writer-facing tools** (Valve's rule DB, Wildermyth's comic and event editor, StoryNexus/Failbetter data, Paradox script) and in **automated playtesting** (L4D survivor bots on accelerated time).

### Cited Findings
- GW2: running every event only once would need "approximately 100,000 people", so they built cycling chains. — [GameBanshee: GW2 Q&A](https://www.gamebanshee.com/9xd)
- WD Legion: every line "written and recorded 20 times" for personas, formant modulation to multiply voices, and localisation problems with gender agreement and names across 14+ languages. — [Game Developer: WD Legion simulation](https://www.gamedeveloper.com/design/how-watch-dogs-legion-s-play-as-anyone-simulation-works)
  - Workflow: base script → persona adaptation → mission-lead revision. Every writer worked on every character. — [Game Developer: WD Legion storytelling](https://www.gamedeveloper.com/marketing/how-i-watch-dogs-legion-i-made-ubisoft-rethink-its-storytelling)
- Hades: more than 22,000 voiced lines from a team of under 20 (search snippet). — [Game Developer](https://gamedeveloper.com/audio/dive-into-the-dialogue-of-i-hades-i-at-gdc-2021)
- Skyrim: 30 defined player actions with world reactions, plus custom lines per voice type to reduce repetition. — [GamesBeat: Nesmith](https://gamesbeat.com/bethesdas-nesmith-reflects-on-the-difficult-birth-of-skyrims-radiant-story-system/)
- Valve: the rule DB let writers add special cases and running gags without programmer changes. Ruskin stressed comfortable writer tools. — [GDC Vault: Ruskin](https://www.gdcvault.com/play/1015528/AI-driven-Dynamic-Dialog-through); [Emily Short](https://emshort.blog/2012/03/16/gdc-2012-talk-on-dynamic-dialogue/)
- Sunless Sea: modular islands allowed distributed authorship, with about 30% of islands by freelancers. "We didn't have to commit to one form from day one." — [Game Developer: Sunless Sea postmortem](https://www.gamedeveloper.com/audio/postmortem-failbetter-games-i-sunless-sea-i-)
- QBN content "feels thin until sufficient storylets accumulate"; salience tuning needs "extensive trial-and-error". Modern computing makes randomised playthrough testing possible. — [Emily Short: Beyond Branching](https://emshort.blog/2016/04/12/beyond-branching-quality-based-and-salience-based-narrative-structures/)
- Wildermyth: data-driven events and a comic editor, plus tools so "anyone can make their own maps and their own comics". — [TurnBasedLovers](https://turnbasedlovers.com/10-turns-interview/with-wildermyth-developer); [Wildermyth Wiki: Comic Editor Reference](https://wildermyth.com/wiki/Comic_Editor_Reference)
- Paradox: text-script events (triggers, MTTH/weights, options, on_actions, story cycles) that modders and designers share. Performance discipline (pre-triggers) is part of authoring. — [CK2 Wiki](https://ck2.paradoxwikis.com/Event_modding); [CK3 Wiki: Story cycles](https://ck3.paradoxwikis.com/Story_cycles_modding)
- L4D: survivor bots enabled "Automated stress testing… and accelerated game time". — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- Caves of Qud aimed for "rich, convincing histories on a tight scope budget" with replacement grammars instead of full simulation. — [GDC Vault: Qud](https://gdcvault.com/play/1024990/Procedurally-Generating-History-in-Caves)
- Elite Dangerous shows the opposite problem: opaque systemic rules that players reverse-engineer (player reports). — [Frontier forum](https://forums.frontier.co.uk/threads/missions-not-impacting-bgs-especially-during-elections-in-pp-systems.633066)
- Ubisoft Ghostwriter applies generation to the cheapest, most repetitive layer (barks), with human curation of the variants. — [Ubisoft News: Ghostwriter](https://news.ubisoft.com/en-us/article/7Cm07zbBGy4Xml6WgYi25d/the-convergence-of-ai-and-creativity-introducing-ghostwriter)

### Inferences
- LLMs collapse the per-variant writing cost that WD Legion and Hades paid by hand. Voiced output is the remaining cost: TTS latency, voice consistency, Russian quality. So the costs that remain for us are **conditions, validation and testing**, not prose.
- The L4D bot lesson carries over directly: build an offline harness that replays thousands of simulated director runs against a mocked Gothic fact store, to catch repetition, rate spikes and plot contradictions before players do.
- Tooling matters even with an LLM. Authors need a readable table of event templates (preconditions, roles, cooldowns, priority, allowed effects), as in Valve's rule DB or Paradox scripts, rather than prompts alone.

### Gaps
- No headcount or time figures were found for Skyrim Radiant, RimWorld storytellers or the Nemesis system.
- No figures were found on how many storylets Fallen London or Sunless Sea contain.

---

## Q5. What transferable design patterns fit a hybrid authored + dynamic RPG (Gothic 2 NotR on OpenGothic, with an external LLM server)?

### Takeaway
Use a **three-layer architecture**:
- **Authored spine**: Daedalus main and guild quests, untouched by the dynamic system.
- **Symbolic director**: a fact store, a template/storylet DB with prerequisites, priority and cooldowns, and a pacing state machine. It decides *what* and *when*.
- **LLM texture layer**: dialogue, motives and after-the-fact rationalisation, writing inside validated slots.

Bias the system toward few, context-specific, consequential beats cast from named NPCs the player already knows, and away from many generic fetch tasks.

### Cited Findings (pattern → precedent)
- **Keep the main plot authored; the system only reacts and decorates.** — [Nesmith](https://gamesbeat.com/bethesdas-nesmith-reflects-on-the-difficult-birth-of-skyrims-radiant-story-system/); [Qud main-quest sites](https://wiki.cavesofqud.com/wiki/World_generation); [Wildermyth layers](https://en.wikipedia.org/wiki/Wildermyth)
- **Anchor procedural content to authored characters.** — [WD Legion Anchors](https://www.gamedeveloper.com/marketing/how-i-watch-dogs-legion-i-made-ubisoft-rethink-its-storytelling)
- **Pace by frequency, not amplitude; one crude metric is enough; build-up/peak/relax.** — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)
- **On/off windows plus minimum spacing between major beats; adapt to recent losses.** — [RimWorld Cassandra](https://rimworldwiki.com/wiki/Cassandra); [AI Storytellers](https://rimworldwiki.com/wiki/AI_Storytellers)
- **Threat or reward budget scaled by player power** (Gothic analogue: level, gear value, chapter). — [RimWorld Raid points](https://rimworldwiki.com/wiki/Raid_points)
- **Designer-placed candidate spots; the director chooses.** — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf); [UESP Radiant](https://app.uesp.net/wiki/Skyrim:Radiant)
- **Trigger → condition filter → role casting (aliases / casting call) → seed location first.** — [CK wiki tutorial (snippet)](https://ck.uesp.net/wiki/Bethesda_Tutorial_Radiant_Quests); [Wildermyth casting](https://turnbasedlovers.com/10-turns-interview/with-wildermyth-developer)
- **Templates = beats + location types + roles**, filled at runtime. — [WD Legion](https://www.gamedeveloper.com/design/how-watch-dogs-legion-s-play-as-anyone-simulation-works)
- **Most-specific-rule-wins over a fact query, with broad defaults plus special cases.** — [Ruskin / GDC Vault](https://www.gdcvault.com/play/1015528/AI-driven-Dynamic-Dialog-through); [Mimir](https://github.com/subtalegames/mimir)
- **Priority tiers with filler fallback; urgent reactive content first.** — [Hades via Film Stories](https://filmstories.co.uk/?p=83138)
- **No-repeat decks and "do all before repeating"; must-play cards for crises.** — [Booth slides](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf); [SM Event Node](https://skyrimck.uesp.net/wiki/SM_Event_Node); [Fallen London Cards](https://fallenlondon.wiki/wiki/Cards)
- **Persistent story objects with their own timers and end conditions** (dynamic quest instances). — [CK3 story cycles](https://ck3.paradoxwikis.com/Story_cycles_modding)
- **Typed state: separate resources, character traits and story progress.** — [Kennedy](https://weatherfactory.biz/?p=6991)
- **Narrative markers (aspects) as the glue for chains and callbacks.** — [Wildermyth](https://wildermyth.com/wiki/Story_Inputs_and_Outputs)
- **Social-graph memory → threshold → revenge or gratitude events.** — [WD Legion](https://www.gamedeveloper.com/design/how-watch-dogs-legion-s-play-as-anyone-simulation-works); [Nemesis](https://www.gamedeveloper.com/design/core-system-analysis-of-middle-earth-shadow-of-war); [Skyrim world interactions](https://app.uesp.net/wiki/Skyrim:Radiant)
- **Escalation if the player ignores a problem** (GW2: soldiers → camp → village captured). — [TenTonHammer](https://www.tentonhammer.com/articles/dynamic-events-in-guild-wars-2-living-in-a-living-world.amp)
- **Issues that hurt a settlement until resolved**, with a frequency cap so they do not spam. — [TaleWorlds (snippet)](https://www.taleworlds.com/en/Games/Bannerlord/Blog/24); [player complaints](https://steamcommunity.com/app/261550/discussions/0/3008927444647027216)
- **Generate events first, rationalise after** (ideal LLM job). — [Grinblat & Bucklew](https://pcgworkshop.com/archive/grinblat2017subverting.pdf)
- **Legibility: simulate only what players can perceive; cue apophenia.** — [Sylvester](https://tynansylvester.com/2013/06/the-simulation-dream/)
- **Constraints first; aim for perceptual uniqueness, not mere differentiation.** — [Compton](https://galaxykate0.tumblr.com/post/139774965871/so-you-want-to-build-a-generator)
- **Restraint: cut reactions that "don't feel good"; avoid "half the town comes over".** — [Nesmith](https://gamesbeat.com/bethesdas-nesmith-reflects-on-the-difficult-birth-of-skyrims-radiant-story-system/)
- **Interrupt arbitration around authored scenes.** — [WD Legion attention system](https://www.gamedeveloper.com/design/how-watch-dogs-legion-s-play-as-anyone-simulation-works)
- **Legal caution**: a Nemesis-style hierarchy of generated rivals with memory and promotions is close to the claims of US 10,926,179 (until about 2035/2036). — [PatentArcade](https://www.patentarcade.com/video-game-patent/US10926179/nemesis-characters-nemesis-forts-social-vendettas-and-followers-in-computer-games)

### Inferences
- **Concrete shape for Gothic** (inference, not a sourced fact):
  1. The server keeps a world-fact store mirrored from Daedalus: chapter, MIS_* states, guild, NPC alive/dead, attitudes, player location and time of day.
  2. Each dynamic beat is a template record: trigger, preconditions, roles with casting filters (guild, location, `Npc_IsDead`, relationship), seed waypoint list, priority tier, cooldown, global spacing class (major/minor), allowed effects (spawn from a whitelist, give or take items, set a server-side flag, add a quest-log topic), and an expiry.
  3. The director tracks a crude tension metric (recent damage, kills, deaths, time since last major beat) and enforces on/off and relax windows, plus "never during authored cutscenes or key dialogues".
  4. The LLM writes the NPC's lines, motive and log entries for the chosen, already-validated beat, and must return a structured JSON effect list that the server checks against the template's allowed effects before anything reaches Daedalus.
- **Favour reactive and consequential beats over generic fetch quests**: an NPC remembers being robbed or helped; the relatives of a killed NPC react; a bandit camp the player ignored grows (GW2 escalation); a guild rival takes revenge. These draw on the player's own history, which is what lifts perceptual uniqueness.
- **Keep the dynamic layer's effects reversible or additive at first** (new NPC lines, new optional log topics, spawns at designer-approved points). Allow writes to authored quest variables only through an explicit allowlist, so the main plot cannot break.
- **Budget variety honestly**: a small, well-conditioned template set plus LLM texture is likely to beat a big generic one. Skyrim, Bannerlord and GW2 show that volume without context reads as repetition.
- **Plan an offline simulation harness early**, per L4D's survivor bots and Emily Short's note on randomised playthrough testing.

### Gaps
- No shipped pre-LLM game was found that lets a generative model change authored quest state. Claims about how players perceive LLM-written quests in a classic RPG cannot be backed by this research.
- Whether a Gothic-specific implementation infringes the Nemesis patent needs legal review. This note only flags how close the feature set is.
