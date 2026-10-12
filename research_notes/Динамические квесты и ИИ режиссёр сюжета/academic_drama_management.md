# Classical drama management, experience management, narrative planning, storylets, story sifting and procedural quest generation: foundations for a Gothic 2 "story director"

Scope note: this covers mostly pre-LLM academic work (1992 to about 2021). LLM-era work appears only where a classical method was later combined with an LLM. Most numbers below were read from the primary PDFs (text extracted locally). Where only a secondary source or search snippet was available, the finding says so. The Gothic/OpenGothic mappings in the "Inferences" sections are my own synthesis, not findings from the literature.

---

## 1. Drama managers and experience managers (Bates/Weyhrauch SBDM, Façade, DODM, TTD-MDP, PaSSAGE, Automated Story Director, narrative mediation, the Riedl & Bulitko survey)

### Takeaway
Classical drama managers fall into three families. (a) **Selection from authored content**: Façade's beat sequencer and PaSSAGE's encounter picker. These are cheap, robust and proven with players. (b) **Optimization over an abstract plot-point space**: Bates/Weyhrauch search, DODM, and TTD-MDP or RL policies. These are elegant but do not scale with online search. They work only when the policy is computed offline. (c) **Generative (planner-based)**: Mimesis and the Automated Story Director. These re-plan when the player breaks causal links, but plan repair has to be precomputed offline as contingency trees because planning time is unpredictable. For a real-time RPG director, (a) and offline-trained (b) are the deployable options. (c) works only on small, abstracted planning domains.

### Cited Findings

**Framing: the experience management problem (Riedl & Bulitko)**
- Riedl & Bulitko frame experience management as computing trajectories through the world's state space. The experience manager "must generally look ahead into possible futures" and pick interventions that bring about a coherent experience. The target trajectory is the *best* narrative reachable given user agency, and "often should not be" the most likely one. Interventions usually work by directing NPCs, and the user should not notice the manager. — [Riedl & Bulitko, "Interactive Narrative: An Intelligent Systems Approach", AI Magazine 34(1), 2013, pp. 67–77, DOI 10.1609/aimag.v34i1.2449](https://ojs.aaai.org/index.php/aimagazine/article/view/2449) (PDF: [cs.uky.edu mirror](https://cs.uky.edu/~sgware/reading/papers/riedl2013interactive.pdf)). Note: the AAAI OJS page shows a December 2012 date, while the issue is cited as 2013.
- The survey organizes the field along three dimensions: **authorial intent**, **virtual character autonomy** (from strong story to strong autonomy) and **player modeling**. — [Riedl & Bulitko 2013](https://ojs.aaai.org/index.php/aimagazine/article/view/2449)
- Hand-authored branching story graphs grow exponentially with the number of choice points. The intermediate approach is a **plot graph**: events as nodes, precedence constraints as arcs, plus experience-manager actions (hint, cause, temporarily deny) that "increase the probability that certain trajectories occur". A search process explores trajectories and scores them with an author heuristic. The surveyed search processes are adversarial search (Weyhrauch 1997), reinforcement learning (Roberts et al. 2006) and case-based planning (Sharma et al. 2010). At the far end, **generative** experience managers embed a full story generator (Young et al. 2004; Riedl et al. 2008; Porteous, Cavazza & Charles 2010). — [Riedl & Bulitko 2013](https://cs.uky.edu/~sgware/reading/papers/riedl2013interactive.pdf)
- When a deviation is detected, the manager can respond with **intervention** or **accommodation**. In intervention, it prevents the user from crossing the narrative boundary, either by redirecting NPCs (Magerko 2005, IDA) or by changing the world, e.g. "causing the user's gun to jam" (Young et al. 2004). In accommodation, it adapts the narrative to incorporate the change. — [Riedl & Bulitko 2013](https://cs.uky.edu/~sgware/reading/papers/riedl2013interactive.pdf)

**Search-based drama management (Bates 1992; Weyhrauch 1997, the Oz project "Moe")**
- Bates first proposed search-based drama management (SBDM) and Weyhrauch developed it. The story is modelled as abstract **plot points** whose ordering constraints form a DAG (AND-OR graph), and the legal stories are its topological orderings. The DM has actions such as hints, causers and deniers. An author-specified evaluation function scores a complete plot-point sequence. The DM runs game-tree search in which the player is the "opponent" (sampled), not an adversary. — [Nelson, Mateas, Roberts & Isbell, "Declarative Optimization-Based Drama Management in Interactive Fiction", IEEE Computer Graphics and Applications 26(3), May/June 2006, pp. 32–41](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf) (abstract page: [kmjn.org](https://kmjn.org/publications/DODM_IEEECGA-abstract.html)), citing J. Bates, "Virtual Reality, Art, and Entertainment", *Presence* 1(1), 1992, and P. Weyhrauch, *Guiding Interactive Drama*, PhD dissertation, Carnegie Mellon University, 1997. The Riedl & Bulitko reference list gives the title as "Guiding Interactive Fiction". "Guiding Interactive Drama" is the commonly cited title.
- On Weyhrauch's small *Tea for Three* story, **SAS+ (sampling adversarial search) at depth 1 gave a mean story quality at the 97th percentile** of the unmanaged distribution. Memoized full-depth search reached the 99th percentile. SAS+ lets temporarily denied plot points appear in rollouts, on the assumption that they may be re-enabled later. — [Nelson et al. 2006](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf)

**Declarative optimization-based drama management (DODM), Nelson, Mateas, Roberts & Isbell 2006**
- Nelson et al. applied SBDM/DODM to the interactive fiction *Anchorhead*. The full game has "well over a hundred significant plot points", and they modelled day two with **29 plot points** and **30 DM actions**: 4 permanent deniers, 5 temporary deniers with 5 matching re-enablers, 4 causers, 10 hints and 2 game endings. — [Nelson et al. 2006](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf)
- The evaluation function is a weighted sum of **7 features**:
  - *location flow*: spatial locality of consecutive plot points.
  - *thought flow*: continuity of author-annotated "thoughts".
  - *motivation*: plot points preceded by the plot points that motivate them.
  - *plot mixing*: exploration of several subplots early.
  - *plot homing*: converging on one subplot late.
  - Meta-feature *choices*: player freedom. It stops the DM from linearizing the story.
  - Meta-feature *manipulativity*: an author-assigned "noticeability" cost per DM action.
  — [Nelson et al. 2006](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf)
- **Evaluation methodology.** The DM is judged by how far it shifts the *distribution* of story scores against no DM, measured with simulated players. The unmanaged distribution came from 10,000 random plots, and the RL distributions from 2,000 simulated runs each. — [Nelson et al. 2006](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf)
- **Search scaled badly.** On Anchorhead, SAS+ (time-limited, 2 s per decision in the subplot experiments) moved the mean only to the 64th percentile. With one set of DM actions it was *worse than no drama manager*. The authors attribute this to shallow search being unable to use spaced-out pairs of temporary deniers and re-enablers, "similar to a chess program that searches only three moves into the future". Full memoization would need "hundreds of millions of entries". — [Nelson et al. 2006](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf)
- **Offline RL fixed it.** Temporal-difference learning (in the style of Tesauro's TD-Gammon) precomputes a policy over story states, so "DM responses during gameplay are nearly instant". Training against a pseudo-adversarial player gave better policies than training against a random player, even when evaluated against a random player. The RL policy beat search on mean and median. — [Nelson et al. 2006](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf); [Nelson et al., "Reinforcement learning for declarative optimization-based drama management", AAMAS 2006](https://www.kmjn.org/publications/DODM_AAMAS06-abstract.html)
- Practical lesson from the authors: plot points should be recognizable by "simple and easy-to-code triggers". Plot-point recognition should not become "a problem requiring significant AI of its own". Not every DM action makes sense at any time; for example, a hint delivered by an NPC is useless when the player is nowhere near that NPC. — [Nelson et al. 2006](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf)

**Targeted Trajectory Distribution MDPs (TTD-MDP), Roberts et al. 2006**
- A TTD-MDP changes the agent's goal from finding *one optimal trajectory* to **realizing a specified distribution over trajectories**. This gives variety on replay, which matters for a drama manager. The algorithm builds a trajectory tree and computes locally consistent stochastic policies. A heuristic is used when an exact solution is impossible. It was tested on a grid world and on interactive-drama stories. — [Roberts, Nelson, Isbell, Mateas & Littman, "Targeting Specific Distributions of Trajectories in MDPs", AAAI 2006, pp. 1213–1218](https://www.kmjn.org/publications/TTDMDP_AAAI06-abstract.html) (PDF: [kmjn.org](https://www.kmjn.org/publications/TTDMDP_AAAI06.pdf))
- Follow-ups: a globally optimal algorithm for TTD-MDPs ([Bhat et al., AAMAS 2007](https://www.kmjn.org/publications/TTDMDP_AAMAS07-abstract.html)), and "authorial idioms" for specifying target distributions ([Roberts et al., AAAI 2007](https://mlanthology.org/aaai/2007/roberts2007aaai-authorial)).

**Façade beat-based drama manager (Mateas & Stern 2003, 2005)**
- Façade's narrative unit is the **beat**, a small interactive scene. Each beat contains 10–100 **joint dialog behaviors (jdbs)** written in ABL. A jdb is a coordinated exchange of 1–5 lines between Grace and Trip, lasting a few seconds and taking 40–200 lines of ABL. Each jdb can change story values such as affinity, self-revelation progress and global tension. — [Mateas & Stern, "Structuring Content in the Façade Interactive Drama Architecture", AIIDE 2005](https://cdn.aaai.org/AIIDE/2005/AIIDE05-016.pdf)
- **Beat selection algorithm.** Each beat is annotated with "preconditions, weights, weight tests, priorities, priority tests, and story value effects". The sequencer chooses "the unused beat whose preconditions are satisfied and whose story tension effects most closely match the near-term trajectory of an author-specified story tension arc (in Façade, an Aristotelian tension arc)". Weights and priorities also influence the choice. — [Mateas & Stern 2005](https://cdn.aaai.org/AIIDE/2005/AIIDE05-016.pdf)
- **Content scale and cost.** About 2,500 jdbs, of which roughly 66% sit in **27 beats**. About 15 beats are seen per roughly 20-minute playthrough, and one run uses at most 25% of the content. Global mix-ins (three tiers) handle off-topic player input. Authoring alone took **about 3 person-years**. In hindsight the authors say they traded global agency for local agency, and "only 27 beats were created in the end". — [Mateas & Stern 2005](https://cdn.aaai.org/AIIDE/2005/AIIDE05-016.pdf)
- Player free text is mapped to **discourse acts** (e.g. praise, criticize, provoke), which drive both the local reactions inside a beat and the global story values. — [Mateas & Stern 2005](https://cdn.aaai.org/AIIDE/2005/AIIDE05-016.pdf)

**PaSSAGE (Thue, Bulitko, Spetch & Wasylishen 2007)**
- **Player model.** A vector over Laws' five player types: Fighter, Power Gamer, Tactician, Storyteller and Method Actor. Designers annotate possible player actions with *weight deltas*. For example, asking for a reward raises Power Gamer, so (F=1, M=81, S=1, T=1, P=41) becomes (P=141). — [Thue et al., "Interactive Storytelling: A Player Modelling Approach", AIIDE 2007](https://ojs.aaai.org/index.php/AIIDE/article/view/18780) (PDF: [cs.uky.edu mirror](https://www.cs.uky.edu/~sgware/reading/papers/thue2007interactive.pdf))
- **Encounters.** A library of authored **encounters**, each annotated with the player types it suits and with one or more *branches* (courses of action). PaSSAGE picks the encounter whose branch best fits the model **by inner product**. Encounters are grouped by phases of Campbell's Monomyth to keep story structure. *Triggers* cast suitable actors into roles at runtime, so content is written generically. Dialogue is then refined per type, e.g. reward mentions for Power Gamers. — [Thue et al. 2007](https://www.cs.uky.edu/~sgware/reading/papers/thue2007interactive.pdf)
- **Platform and study.** Built in BioWare's Aurora Neverwinter Toolset on a disguised Little Red Riding Hood: 8 encounters plus 5 endings, 3 decision nodes and 20 possible paths. The user study had **90 university students** across 2 fixed stories and 1 adaptive story. Across all participants the hypotheses were *not* strongly supported (74% confidence for fun, 71% for agency). In subgroups the effect was stronger: females who found the game easy to follow showed 93% confidence for more fun and 86% for more agency. — [Thue et al. 2007](https://www.cs.uky.edu/~sgware/reading/papers/thue2007interactive.pdf)
- Conflicting numbers: a search snippet cites a PaSSAGE study with **101 participants** and 91%/87% confidence for low-experience, easy-to-follow players. That probably comes from Thue's MSc thesis or a later write-up, not the AIIDE 2007 paper. — [AAAI OJS listing](https://ojs.aaai.org/index.php/AIIDE/article/view/18780); [Thue MSc 2007](https://rise.csit.carleton.ca/pubs/Thue_MSc_2007.pdf). Not reconciled.

**Mimesis, narrative mediation and the Automated Story Director (Young, Riedl et al.)**
- **Narrative mediation** (Riedl, Saretto & Young, AAMAS 2003) is a plan-based technique for "human user actions that conflict with the story structure". It generates real-time responses to unanticipated actions while balancing user freedom and coherence. — [Riedl, Saretto & Young 2003, "Managing Interaction Between Users and Agents in a Multi-agent Storytelling Environment", AAMAS 2003](https://narrativestudies.lib.unb.ca/bibcite/reference/30914)
- Mimesis (Young et al. 2004, *Journal of Game Development* 1(1)) integrated plan-based behavior generation with a game engine. It is the source of the "gun jams" intervention example. — cited in [Riedl & Bulitko 2013](https://cs.uky.edu/~sgware/reading/papers/riedl2013interactive.pdf)
- **Automated Story Director (ASD)**: Riedl, Stern, Dini & Alderman, "Dynamic Experience Management in Virtual Worlds for Entertainment, Education, and Training", *International Transactions on Systems Science and Applications*, 2008. — [preprint PDF](https://www.cs.uky.edu/~sgware/reading/papers/riedl2008dynamic.pdf)
  - **Representation.** The narrative is a partial-order causal-link plan (steps, causal links, temporal links). Authorial intent is encoded as **islands**: intermediate world states every valid plan must pass through, implemented as effect-less plan steps. Example: Red Riding Hood must at some point be eaten and later not eaten. — [Riedl et al. 2008](https://www.cs.uky.edu/~sgware/reading/papers/riedl2008dynamic.pdf)
  - **Threat analysis.** The ASD analyzes the plan for every causal link the user could threaten during its interval. For each, it generates a repaired plan using **tiered replanning**: (i) drop only the threatened link and re-plan the open condition; (ii) also drop the dependent step and all causally downstream steps except islands; (iii) also drop islands; (iv) discard everything and switch to the next author-ranked set of goals and islands. If all tiers fail, the system falls back to an unmanaged, emergent world. — [Riedl et al. 2008](https://www.cs.uky.edu/~sgware/reading/papers/riedl2008dynamic.pdf)
  - **Offline contingency tree.** Replanning is done **offline**. The output is a **tree of contingency plans**, depth-capped, with a simple user model to focus on probable inconsistencies, "without being held up by planner computation time, which can be unpredictable". At runtime, **daemons** watch individual world propositions such as "the Wolf is dead". When one fires, the ASD swaps to the matching child plan. — [Riedl et al. 2008](https://www.cs.uky.edu/~sgware/reading/papers/riedl2008dynamic.pdf)
  - **Character control and applications.** Plan steps become abstract directives to semi-autonomous believable agents. The approach was used for an interactive Little Red Riding Hood and for IN-TALE, a military training environment. — [Riedl et al. 2008](https://www.cs.uky.edu/~sgware/reading/papers/riedl2008dynamic.pdf)
- Related generative experience management: Porteous, Cavazza & Charles, "Applying Planning to Interactive Storytelling: Narrative Control Using State Constraints", *ACM TIST* 1(2), 2010 (state-trajectory constraints as plot control). Magerko, "Evaluating Preemptive Story Direction in the Interactive Drama Architecture", *Journal of Game Development* 2(3), 2005. — both cited in [Riedl & Bulitko 2013](https://cs.uky.edu/~sgware/reading/papers/riedl2013interactive.pdf)

**Data-driven and case-based DMs**
- Sharma, Ontañón, Mehta & Ram, "Drama Management and Player Modeling for Interactive Fiction Games", *Computational Intelligence* 26(2):183–211, 2010: case-based player modeling plus planning for the DM. — cited in [Riedl & Bulitko 2013](https://cs.uky.edu/~sgware/reading/papers/riedl2013interactive.pdf)
- Yu & Riedl, **Prefix-Based Collaborative Filtering (PBCF)**: sequential recommendation over a branching story graph. It predicts a player's ratings of successive story prefixes from other players' ratings, without assuming fixed player-type dimensions. Evaluated on choose-your-own-adventure-style stories, it improved players' experience over the designer's intent alone while preserving agency. — [Yu & Riedl, AAMAS 2012](https://ifaamas.csc.liv.ac.uk/Proceedings/aamas2012/papers/2A_3.pdf); [Yu & Riedl, IEEE TCIAIG 2014 (PDF)](https://faculty.cc.gatech.edu/~riedl/pubs/tciaig14-yu.pdf); [Data-Driven Personalized Drama Management, AIIDE 2013](https://ojs.aaai.org/index.php/AIIDE/article/view/12665)

### Inferences
- **Plot-point DMs map almost one-to-one onto Gothic.** The quest-state variables of Daedalus scripts can serve as **plot points**: `MIS_*` variables, log topics created with `Log_CreateTopic`/`Log_SetTopicStatus`, and the chapter variable. Engine externals can serve as **DM actions**:
  - spawn an NPC as a *causer*: `Wld_InsertNpc`;
  - change a daily routine to lure the player or deliver a hint: `Npc_ExchangeRoutine`;
  - change faction relations: `Wld_SetGuildAttitude`;
  - other externals such as `AI_Teleport`.

  All of these are bound in OpenGothic (`OpenGothic/common/game/gamescript.cpp`, lines 118, 127, 163, 277, 305–307). A DODM-style director in the Python server could keep a plot-point DAG for the current chapter and pick DM actions through the AI bridge.
- **Use DODM's evaluation design even without DODM's search.** Its feature list is directly usable for an RPG: location flow, thought flow, motivation, plot mixing/homing, choices, manipulativity. "Manipulativity" in particular is a good per-action cost for heavy-handed LLM-driven interventions.
- **The safe deployable patterns are Façade-style greedy selection or offline-trained policies.** Greedy selection means choosing the next quest/event by preconditions, priority and fit to a target tension arc. Offline-trained policies mean RL or TTD-MDP trained on a simulated player, then queried instantly. Online deep search over plot points should not be expected to work: Anchorhead is far smaller than Gothic 2 and search already failed there.
- **Gothic already uses hard intervention, which ASD-style mediation can make softer.** Plot-critical NPCs in Gothic are protected from being killed (an engine immortal flag; domain knowledge, not verified here). ASD-style accommodation would let a planner re-route a quest when the player kills or robs a quest giver, e.g. tier (ii) replacement of the downstream steps, instead of forbidding the action.
- **Precompute repairs.** The ASD lesson for an LLM-era director is to precompute or asynchronously compute repairs for the threats that are *likely*, so they are ready before the player acts. Generating them only after the inconsistency happens means waiting on planning (or LLM) latency at the worst moment.

### Gaps
- I did not obtain Weyhrauch's 1997 dissertation text: the exact evaluation-function features for Tea for Three, the size of the Moe experiments and the runtime numbers. The figures above come from Nelson et al.'s retelling.
- The Façade paper reports no controlled player study of the DM. I found no quantitative evaluation of the beat sequencer.
- TTD-MDP experimental numbers (trajectory-tree sizes, KL-divergence to the target) were not extracted.

---

## 2. Narrative planning (IPOCL, CPOCL, Glaive, Sabre): intentions, conflict, belief, computational cost, and plan breakage and repair

### Takeaway
Narrative planners look for an action sequence that (1) reaches an *author* goal and (2) contains only actions *explained* by the characters' own goals and (in Sabre) beliefs. Plan-space POCL versions (IPOCL) were extremely slow, taking over 12 hours on a small benchmark. State-space heuristic search (Glaive) solves the same problem in about 64 ms. Adding a deep theory of mind (Sabre) brings back runtimes of minutes to hours on richer domains. They are therefore usable in real time only on small, abstracted, quest-local domains, or offline/asynchronously. They pair well with LLMs: the planner guarantees causal and intentional validity, and the LLM supplies heuristic guidance or text.

### Cited Findings
- **IPOCL** (Intent-based Partial Order Causal Link) planner. Riedl & Young argue that two attributes are universal to narratives: logical causal progression of plot, and character believability (characters perceived as intentional agents). IPOCL builds causally sound plans while identifying character goals that explain each action, plus *motivating* structures for why characters commit to those goals. In an empirical evaluation, IPOCL plans supported **audience comprehension of character intentions** better than plans from conventional POCL planners. — [Riedl & Young, "Narrative Planning: Balancing Plot and Character", JAIR 39:217–268, 2010, DOI 10.1613/jair.2989](https://arxiv.org/pdf/1401.3841)
- **IPOCL cost.** As reported by Ware & Young, Riedl & Young's IPOCL "took over 12 hours to solve Aladdin, visited 673,079 nodes and expanded 1,857,373" even with a domain-specific heuristic. — [Ware & Young, "Glaive: A State-Space Narrative Planner Supporting Intentionality and Conflict", AIIDE 2014](https://www.cs.uky.edu/~sgware/reading/papers/ware2014glaive.pdf)
- **CPOCL** (Ware & Young 2011) extended IPOCL with **conflict**: plans of intentional agents that are thwarted, including steps characters intend but never execute. — described in [Ware & Young 2014](https://www.cs.uky.edu/~sgware/reading/papers/ware2014glaive.pdf)
- **Glaive** is a forward state-space heuristic search planner (A*) for intentional planning with conflict.
  - A step is *explained* if it lies on an **intentional path** for each consenting character, and all other steps on that path are explained.
  - Valid plans may contain failed intentional paths, which is how conflict is represented.
  - The heuristic is h(n) = max(FF(n), cost(n)). FF is a Fast-Forward relaxed plan graph that only admits "potentially motivated" steps. cost(n) sums, over unexplained steps, their layer in per-character **goal graphs**.
  — [Ware & Young 2014](https://www.cs.uky.edu/~sgware/reading/papers/ware2014glaive.pdf)
- **Glaive benchmarks** (8 problems, 6 GB RAM, 3.5 GHz i7, averages of 10 runs): Space 0.00 s; Fantasy 0.03 s (FF-only: 2.76 s); Raiders 0.03 s; **Aladdin 0.06 s with 12 nodes visited** (FF-only: unsolved after 288 s); BLP-Win 0.31 s; Western 28.4 s; BLP-Die and Heist **unsolved** (about 237 s and 351 s before giving up). — [Ware & Young 2014](https://www.cs.uky.edu/~sgware/reading/papers/ware2014glaive.pdf)
- **Sabre** (Ware & Siler 2021) is "a centralized, omniscient decision maker that solves a multi-agent storytelling problem". The author goal must be achieved, but every action must make sense according to the acting agent's intentions and **limited, possibly wrong beliefs**. It supports full ADL syntax, numeric fluents and an arbitrarily deep theory of mind. Agents must commit to specific beliefs (no true uncertainty), a tradeoff made to solve larger problems. Observation and other triggers update beliefs. — [Ware & Siler, "Sabre: A Narrative Planner Supporting Intention and Deep Theory of Mind", AIIDE 2021](https://ojs.aaai.org/index.php/AIIDE/article/view/18896) (PDF: [cs.uky.edu](https://cs.uky.edu/~sgware/reading/papers/ware2021sabre.pdf))
- **Sabre benchmarks** (deterministic A* with the h+ heuristic, 2.1 GHz Xeon, time to first solution):

  | Problem | Time | Nodes visited |
  |---|---|---|
  | Treasure | 1 ms | — |
  | Space | 6 ms | — |
  | Hubris | 47 ms | — |
  | Raiders | 1.4 s | 905 |
  | BearBirdJr (Micro-TaleSpin) | 14.0 min | 290,711 |
  | Lovers (average over 9 of 10 solved instances) | 40.3 min | — |
  | Grandma (4 characters, about 836 ground actions; from a planning-based narrative game) | **6.2 h** | 598,577 |

  — [Ware & Siler 2021](https://cs.uky.edu/~sgware/reading/papers/ware2021sabre.pdf)
- **Ablation.** Planners that model intention without belief (approximating IPOCL/Glaive), or belief without intention (approximating HeadSpace), accept many plans that are invalid under full Sabre. Earlier work found audiences significantly prefer stories with both intention and belief and notice flaws when either is missing (Shirvani, Farrell & Ware 2018). — [Ware & Siler 2021](https://cs.uky.edu/~sgware/reading/papers/ware2021sabre.pdf)
- **Complexity.** Doran & Parberry note that general planning is NP-complete via SAT encodings and PSPACE-hard in general, and call general planners "impractical" for quest generation. They restrict generation to structural patterns with implicit pre- and postconditions instead. — [Doran & Parberry 2011](https://ianparberry.com/pubs/pcg2011.pdf)
- **Plan breakage and repair.** In planner-based interactive narrative, user actions can threaten causal links. The responses are intervention (make the action fail) or accommodation (re-plan); see Section 1 for the ASD's four repair tiers and offline contingency trees. Harris & Young's "Proactive Mediation in Plan-based Narrative Environments" extends mediation to act *before* threats occur. — cited in [Riedl et al. 2008](https://www.cs.uky.edu/~sgware/reading/papers/riedl2008dynamic.pdf)
- **Classical planner + LLM (brief).** Farrell & Ware use a state-space narrative planner over hand-written symbolic domains, with an **LLM acting as a search guide** that estimates "which events are worth exploring first". They give an initial evaluation on benchmark narrative-planning problems. — [Farrell & Ware, "Large Language Models as Narrative Planning Search Guides", IEEE Transactions on Games 17(2):419–428, 2025 (online Oct 2024)](https://www.cs.uky.edu/~sgware/reading/papers/farrell2024large.pdf); [UKY record](https://scholars.uky.edu/en/publications/large-language-models-as-narrative-planning-search-guides/)

### Inferences
- **Realistic sizing.** Gothic 2 has hundreds of NPCs, items and locations. A Sabre-style planner cannot run over the whole world. The workable pattern is to build a **quest-local planning problem**: the 2–6 characters involved, a few locations and items, and the relevant beliefs. Glaive and Sabre benchmarks of that size solve in milliseconds to about a second, while realistic multi-character belief problems took minutes to hours.
- **Explained actions solve a known LLM-NPC weakness.** The "explained action" constraint (every NPC action serves that NPC's goal under its beliefs) is the formal version of "the NPC wouldn't do that". A director could use it as a **validator** for LLM-proposed quest plans, without generating plans itself: check that each proposed step is motivated for its actor.
- **Belief-tracking maps to Gothic mechanics.** Sabre-style belief modeling fits Gothic's crime, witness and rumor mechanics, e.g. a theft only matters if an NPC observed it. LLM NPCs already have memories. A small symbolic belief layer, "who believes what about the player", would let the director plan deception and revenge quests coherently.
- **Pre-planning.** Since the planner's latency is unpredictable, run it asynchronously on the Python server. Precompute repairs for likely threats, e.g. the quest giver dies or the item is sold, in ASD style.

### Gaps
- IPOCL's own runtime table and the CPOCL paper were not fetched directly; IPOCL's cost comes from Glaive's citation of it.
- I did not verify planner-repair efficiency results such as plan-repair vs replan-from-scratch benchmarks in narrative domains.
- Porteous et al. 2010 (state-trajectory constraints with a fast planner) was not read in full; no runtime numbers.

---

## 3. Storylets, quality-based and salience-based narrative, and social-simulation systems (Versu/Praxis, Comme il Faut/Prom Week, Ceptre)

### Takeaway
Storylets are the most industry-proven and cheapest classical structure: precondition-gated content chunks selected by a selection architecture. Gothic's dialogue system is already a storylet system. Social-simulation systems (Versu, Comme il Faut) show how to make storylets character-agnostic: roles are cast at runtime and NPCs choose via utility over "social practices". Ceptre provides a formal multiset-rewriting/linear-logic language for the same rule-based world state that is good for prototyping and analysis. All of these pair naturally with LLMs, with the LLM as content filler or caster and the symbolic layer as the gate.

### Cited Findings
- **Storylet design space** (Kreminski & Wardrip-Fruin, "Sketching a Map of the Storylets Design Space", ICIDS 2018, LNCS). The paper surveys storylet systems including StoryNexus (Failbetter's QBN), The King of Chicago, Reigns, Ice-Bound Compendium and StoryAssembler, along four dimensions:
  1. how storylet preconditions are defined;
  2. whether storylets can repeat;
  3. what content a storylet holds;
  4. the **content selection architecture**, i.e. how eligible storylets are chosen.
  — summarized by [Emily Short, "Kreminski on Storylets" (2019)](https://emshort.blog/2019/01/06/kreminski-on-storylets/); [EIS lab post](https://eis-blog.soe.ucsc.edu/?p=4054). The primary PDF was not retrieved (see Gaps).
- **Quality-based vs salience-based narrative** (Short 2016). In **quality-based narrative** (QBN, e.g. Fallen London), content becomes available when the player's "qualities" (numeric state) meet requirements, and the player chooses among the legal elements. In **salience-based narrative**, the system picks "the most applicable" piece from a large pool. Short also describes waypoint structures. — [Emily Short, "Beyond Branching: Quality-Based, Salience-Based, and Waypoint Narrative Structures" (2016)](https://emshort.blog/2016/04/12/beyond-branching-quality-based-and-salience-based-narrative-structures/)
- **Versu** (Evans & Short) is a text-based "simulationist interactive drama".
  - **Social practices** describe recurring social situations: a meal, a conversation, a game, a family. A practice is a "successor to the Schankian script", implemented as **reactive joint plans** that provide **affordances** to role-holders.
  - Practices "never control the agents directly; they merely provide suggestions". Each agent decides with **utility-based reactive action selection**.
  - Practices are **character-agnostic**: any character can be cast into a role, and roles can be human or AI.
  - Several practices run concurrently (dinner party + political conversation + flirtation + spilled soup), and an agent's options are the union of their affordances.
  - A centralized DM exists but rarely overrides characters. It works "at a higher level—by providing suggestions, or tweaking the desires of the participants".
  - State is stored in **exclusion logic**, tree-structured literals such as `brown.relationship.lucy.evaluation.attractive!40`, where `!` marks a unique value. The authoring language is Praxis.
  — [Evans & Short, "Versu—A Simulationist Storytelling System", IEEE TCIAIG 6(2):113–130, June 2014](https://www.cs.uky.edu/~sgware/reading/papers/evans2014versu.pdf)
- **Comme il Faut (CiF) / Prom Week.** The player shapes the social lives of **18 high-schoolers**. CiF's **social exchanges** are social actions a character takes toward another, intended to change their relationship, plus the response. Each character keeps a ranked list of desired exchanges ("over 40" in Prom Week), and desire is computed from **over 5,000 "social considerations"** (rules over traits, relationships and past events). — [Prom Week project page (EIS, UCSC)](https://eis-blog.soe.ucsc.edu/2011/11/prom-week/); [promweek.soe.ucsc.edu](https://promweek.soe.ucsc.edu/?p=295). The primary paper is McCoy, Treanor, Samuel, Reed, Mateas & Wardrip-Fruin, "Social Story Worlds With Comme il Faut", IEEE TCIAIG 6(2), 2014; its full text was not retrieved.
- **Ceptre** (Martens 2015) is a rule-specification language for rapid prototyping of generative game mechanics and multi-agent simulation. It rests on **linear logic** (a logic of resource consumption): a gameplay run corresponds to proof search. Interactivity can be added selectively, which allows inspection of intermediate states for debugging. Case studies cover interactive narrative and a strategy-like domain. — [Martens, "Ceptre: A Language for Modeling Generative Interactive Systems", AIIDE 2015, 11(1):51–57](https://ojs.aaai.org/index.php/AIIDE/article/view/12784); earlier narrative work in [Martens et al., "Generative story worlds as linear logic programs" (INT 2014)](https://webarchive.di.uminho.pt/haslab.uminho.pt/jff/files/2014-generativestoryworldsllp.pdf)
- **Gothic dialogue is already a storylet engine.** In OpenGothic, every Daedalus `C_INFO` instance is enumerated at load (`OpenGothic/common/game/gamescript.cpp:446`). A dialog option is eligible when it belongs to this NPC, matches the `important` flag (NPC-initiated vs player-chosen), is not already known unless `permanent` is set, and its `condition` function returns true (`gamescript.cpp:2537–2561`). Options are ordered by `nr` (`ch.sort = info.nr`, `gamescript.cpp:920`). — local source: `OpenGothic/common/game/gamescript.cpp`

### Inferences
- **Mapping to Kreminski's dimensions.** In Daedalus `C_INFO`:
  - preconditions = the `condition` function over global script state;
  - repeatability = `permanent`;
  - content = an `information` function that runs dialog and script effects;
  - selection = player choice (QBN-like), with `important` as salience-like push and `nr` as priority.

  A "story director" can therefore inject **storylets in the same shape** (precondition, priority, repeatability, effect) that the LLM layer fills with text. This avoids replacing the Daedalus quest system wholesale.
- **Character-agnostic quest templates.** Versu's role casting is the template for such quests: write "debt-collection practice: creditor, debtor, enforcer", then cast Gothic NPCs from guilds and routines at runtime. PaSSAGE's triggers do the same thing.
- **Social simulation without heavy authoring.** CiF-style volition rules, e.g. "if X was insulted by Y, X wants revenge on Y", give believable social dynamics without 5,000 hand-authored rules. An LLM can propose desires, while a small rule set or score keeps them consistent with state.

### Gaps
- The primary Kreminski & Wardrip-Fruin 2018 PDF (exact DOI and page numbers) was not retrieved. The dimensions come from Short's summary.
- No quantitative evaluation of Versu or CiF selection was extracted (Prom Week player studies exist in the FDG 2013 paper, which was not read).
- The salience-scoring formulas of commercial systems (e.g. Valve's dynamic dialog / "fuzzy pattern matching") were not covered here.

---

## 4. Story sifting and emergent narrative: detecting stories in the event log and building quests from them

### Takeaway
Story sifting is a pattern-based search over a chronicle of simulation events that finds "microstories" such as revenge, betrayal or broken hospitality. Ryan's thesis named and formalized it, Felt made it a declarative Datalog query language, and Winnow made it **incremental**, so partial matches are tracked live. Incremental sifting is exactly what a quest director needs: detect the *beginning* of a storyful sequence in the game's event log, then issue a quest or event that completes or subverts it. It is computationally cheap per event and pairs well with LLMs, which can narrate a sifted match or propose patterns.

### Cited Findings
- **Origin.** Story sifting was first described by Ryan, Mateas & Wardrip-Fruin (2015) as "story recognition". **Ryan's dissertation (2018) introduced the term "story sifting"** and presented **Sheldon**, a sifter whose patterns were procedural Python code finding microstories in a simulated small American town. — [Kreminski, Dickinson & Mateas, "Winnow: A Domain-Specific Language for Incremental Story Sifting", AIIDE 2021](https://ojs.aaai.org/index.php/AIIDE/article/view/18903); [Ryan, *Curating Simulated Storyworlds*, PhD thesis, UC Santa Cruz, 2018](https://escholarship.org/uc/item/1340j5h2)
- **Ryan's curationist argument.** Simulations like Dwarf Fortress sometimes produce fascinating emergent plots and sometimes dull ones, so **curation** is needed. Ryan distinguishes **sifting patterns** (match specific event structures) from **sifting heuristics** (looser criteria). He advocates **causal bookkeeping** (record why events happened) and contrasts "feedforward" architectures (simulation feeds a separate presentation) with "feedback" ones (e.g. NPCs retell events in-world). — [Emily Short's chapter summaries of Ryan 2018, Ch. 4–5](https://emshort.blog/2019/05/23/curating-simulated-storyworlds-james-ryan-2/); [Ch. 1–3](https://emshort.blog/2019/05/21/curating-simulated-storyworlds-james-ryan/) (secondary)
- **Felt** (Kreminski, Dickinson & Wardrip-Fruin, ICIDS 2019, pp. 267–281, Springer) is a declarative DSL for sifting patterns compiled to Datalog queries over a DataScript store of `[entity attribute value]` triples. It was used in the co-creative storytelling game "Why Are We Like This?". — [Winnow paper (background)](https://ojs.aaai.org/index.php/AIIDE/article/view/18903); [Felt on GitHub](https://github.com/mkremins/felt)
- **Synthesifter** (Kreminski, Wardrip-Fruin & Mateas 2020) synthesizes sifting patterns from user-provided example event sequences via inductive logic programming. — [Winnow paper](https://ojs.aaai.org/index.php/AIIDE/article/view/18903)
- **Winnow** (Kreminski, Dickinson & Mateas, AIIDE 2021):
  - Patterns are sequences of event clauses with logic variables, plus `unless-event` negative clauses.
  - Example `breakHospitality`: a ?guest enters town; a ?host who values communalism shows hospitality; the ?host later harms the ?guest; and no `leaveTown` event intervenes.
  - Because patterns are **temporally staged**, each new event only has to check whether it starts a new partial match, advances one, or invalidates one. This "dramatically reduces" per-event work, compared with re-running every partial-pattern variant after every event.
  - Winnow compiles to Felt for retrospective sifting.
  - Partial-match pool growth is controlled by "advance directly" instead of "fork and advance" once the non-event variables are bound, and by domain heuristics such as pruning matches involving dead characters.
  - Patterns are causality-agnostic by default, but can use causal links when the simulation records them.
  — [Winnow, AIIDE 2021](https://ojs.aaai.org/index.php/AIIDE/article/view/18903) (code: [github.com/mkremins/winnow](https://github.com/mkremins/winnow))
- **Prior and industry analogues** (per the Winnow related-work section):
  - **The Sims 2** matches past events against "story trees" and **raises the probability of future events that would advance partially matched trees** (Brown 2006; Nelson 2006), which is effectively prospective sifting combined with drama management.
  - **Caves of Qud** generates historical figures' biographies by random actions followed by sifting for plausible motivations (Grinblat & Bucklew, FDG 2017).
  - **Playspecs**: regular expressions over play traces, without variables (Osborn et al. 2015).
  - Plan recognition used prospectively (Cardona-Rivera & Young 2015).

  — [Winnow, AIIDE 2021](https://ojs.aaai.org/index.php/AIIDE/article/view/18903)
- **Talk of the Town** (Ryan's town simulation, documented in Ryan & Mateas 2019, "Simulating Character Knowledge Phenomena in Talk of the Town", *Game AI Pro 3*) models character knowledge, including misremembering and lying. According to Kreminski et al. it does **not** do explicit causal bookkeeping, leaving causality to human interpreters. — [Winnow, AIIDE 2021](https://ojs.aaai.org/index.php/AIIDE/article/view/18903)
- **LLM-era combinations (brief).** "Narrative Gold Digging" formalizes sifting heuristics ("unexpectedness", "dramatic situations") over a procedural social simulation and compares human-written and LLM-written narrations of sifted stories. — [Lessard et al., project page](https://www.jonathanlessard.net/?p=357) (not read in depth)

### Inferences
- **The AI server is already a chronicle.** The Python server's event stream (dialogues, attacks, thefts, deaths, trades, guild changes, witnessed crimes) is a Felt/Winnow-style chronicle. Store it as `[entity attribute value]` facts in SQLite or Datalog. Hand-write about 20–50 Winnow-style patterns for Gothic-typical microstories:
  - "player stole from X and X is militia" leads to a pursuit or bounty quest;
  - "NPC A insulted B and B is a mercenary" leads to a revenge quest;
  - "player saved X's friend" leads to a gratitude storylet.

  Each *partial* match is a hook for the director, which can issue a quest that advances the match (Sims 2 style) or that resolves it.
- **Sifting output maps onto Doran & Parberry's motivations** (Section 5). For example, a "revenge" match maps to motivation Serenity with strategy "Revenge, Justice", which gives a principled bridge from emergent events to structured quests.
- **Add causal bookkeeping at the event-logging layer**: each event records the event or intention that caused it. Ryan and Kreminski both identify it as the enabler of good curation, and it also lets an LLM narrator explain *why* something happened.
- **LLM roles.** A good fit is LLM as (a) narrator of a sifted match (Ryan's "feedback architecture", NPC gossip); (b) proposer of new sifting patterns, validated against logged examples in the spirit of Synthesifter; or (c) scorer of "interestingness" (sifting heuristics). Matching itself should stay symbolic, for determinism and cost.

### Gaps
- Ryan's thesis PDF did not load (the eScholarship page returned empty content). Details of Sheldon's patterns, Hennepin, Bad News and the thesis evaluation are from secondary summaries only, or not covered.
- No performance benchmark numbers (events per second, match-pool sizes) for Felt or Winnow were found in the paper text I extracted.
- Johnson-Bey's "Centrifuge" (PLIE 2021, [PDF](https://eis.ucsc.edu/papers/Johnson-Bey_Centrifuge_PLIE2021.pdf)), another sifting tool, was not read.

---

## 5. Procedural quest generation (Doran & Parberry, CONAN, Soares de Lima et al., grammar and graph rewriting, quest validation)

### Takeaway
Doran & Parberry's analysis of more than 750 MMORPG quests is the cornerstone. Quests reduce to 9 NPC motivations, each realized by 2–7 strategies, each strategy a sequence of 1–6 actions. The actions expand recursively through a small BNF grammar whose terminals are about 20 atomic actions with pre- and postconditions. Later systems replaced the grammar with planners (CONAN), with GA plus planning guided by story arcs (de Lima et al.), or with graph rewriting (ReGEN). Validation comes almost for free when actions carry pre- and postconditions checked against the live world state. This is directly usable for Gothic: the motivation/strategy/action tree as the director's quest skeleton, and the LLM as dialog and flavor filler.

### Cited Findings
- **Corpus.** More than **750 quests** from 4 MMORPGs: EverQuest 266 (35%), World of Warcraft 205 (27%), Eve Online 145 (19%), Vanguard: Saga of Heroes 137 (18%). Quests showed "significantly less structural variety than one might expect". — [Doran & Parberry, "A Prototype Quest Generator Based on a Structural Analysis of Quests from Four MMORPGs", Proc. 2nd Int. Workshop on Procedural Content Generation in Games (PCGames '11), Bordeaux, 2011, pp. 1–8, ACM](https://ianparberry.com/pubs/pcg2011.pdf); [project page](https://ianparberry.com/research/quests)
- **9 NPC motivations, with observed frequencies:**

  | Motivation | Quests | Share |
  |---|---|---|
  | Conquest | 152 | 20.2% |
  | Equipment | 139 | 18.5% |
  | Knowledge | 138 | 18.3% |
  | Protection | 137 | 18.2% |
  | Serenity | 103 | 13.7% |
  | Reputation | 49 | 6.5% |
  | Wealth | 15 | 2.0% |
  | Comfort | 12 | 1.6% |
  | Ability | 8 | 1.1% |

  The authors argue that motivations are "essential for ensuring that quests appear intentional and appropriate rather than randomly generated". An NPC's motivation should change as quests are completed, and designers can shift the motivation distribution over time to change the game's flavor. — [Doran & Parberry 2011](https://ianparberry.com/pubs/pcg2011.pdf)
- **Strategies.** Each motivation has 2–7 **strategies** (verb–noun pairs), and each strategy is 1–6 actions. Examples:
  - Serenity, "Revenge, Justice" = `<goto> damage`
  - Serenity, "Rescue captured NPC" = `<goto> damage escort <goto> report`
  - Protection, "Guard Entity" = `<goto> defend`
  - Knowledge, "Spy" = `<spy>`
  - Conquest, "Steal stuff" = `<goto> <steal> <goto> give`
  - Comfort, "Obtain luxuries" = `<get> <goto> <give>`

  — [Doran & Parberry 2011](https://ianparberry.com/pubs/pcg2011.pdf)
- **Atomic actions** (about 20), each with an informal pre- and postcondition: capture, damage, defend, escort, exchange, experiment, explore, gather, give, goto, kill, listen, read, repair, report, spy, stealth, take, use, plus ε. Example: `give` requires "Somebody is there, you have something" and yields "They have it, and you don't". — [Doran & Parberry 2011](https://ianparberry.com/pubs/pcg2011.pdf)
- **Recursive BNF rules**, for example:
  - `<subquest> ::= <goto> | <goto> <QUEST> goto`
  - `<goto> ::= ε | explore | <learn> goto`
  - `<learn> ::= ε | <goto> <subquest> listen | <goto> <get> read | <get> <subquest> give listen`
  - `<get> ::= ε | <steal> | <goto> gather | <goto> <get> <goto> <subquest> exchange`
  - `<steal> ::= <goto> stealth take | <goto> <kill> take`
  - `<kill> ::= <goto> kill`

  A quest is a tree whose **pre-order traversal** gives the player's action sequence. — [Doran & Parberry 2011](https://ianparberry.com/pubs/pcg2011.pdf)
- **Generator.** Input is a motivation plus a seed (reproducible). Rule choice is constrained by "what the player is assumed to know and what the state of the game is", so the generator avoids sending the player for something they already have, or assuming knowledge they cannot obtain. Alternatives are chosen at random. It is written in **Prolog**, so backtracking abandons unexpandable subtrees, with a Java applet front end. Output is an action tree plus a narrative with boilerplate NPC dialog. They show it reproduces EverQuest's "Cure for Lempeck Hargrin" structure. — [Doran & Parberry 2011](https://ianparberry.com/pubs/pcg2011.pdf)
- **Related classifications cited by Doran & Parberry.** Ashmore & Nitsche proposed **key-lock** structured quest generation, which "lacks the sense of purpose" of NPC-goal-derived quests. Earlier classifications include Walker (WoW exploration vs combat), Dickey, Sullivan and Aarseth. — [Doran & Parberry 2011](https://ianparberry.com/pubs/pcg2011.pdf)
- **CONAN** (Creation Of Novel Adventure Narrative), Breault, Ouellet & Davies. A **planning-based** quest generator: a world description of characters, locations and items is converted to a planning problem, and quests are generated "according to the state of the world and the preferences of the characters" (NPC motivations). Evaluation classified generated quests by motivation based on their action sequences and compared them with human categorization of commercial-game quests, reproducing established quest structures. — [Breault, Ouellet & Davies, "Let CONAN tell you a story: Procedural quest generation", arXiv:1808.06217 (2018)](https://arxiv.org/abs/1808.06217). I believe a journal version appeared in *Entertainment Computing* (2021), but did not verify it.
- **Soares de Lima, Feijó & Furtado (2019).** Combines **automated planning with a genetic algorithm guided by story arcs**, to generate coherent quests that follow a specified narrative structure. In preliminary results the quests were "nearly at par with those created by game design professionals". It won second place among SBGames 2019 full papers. — [de Lima, Feijó & Furtado, "Procedural Generation of Quests for Games Using Genetic Algorithms and Automated Planning", SBGames 2019](https://www.sbgames.org/sbgames2019/files/papers/ComputacaoFull/196293.pdf). Follow-up: [de Lima et al., "Adaptive Branching Quests Based on Automated Planning and Story Arcs", SBGames 2021](https://sol.sbc.org.br/index.php/sbgames/article/view/19563)
- **ReGEN** (Kybartas, supervised by Verbrugge, McGill). Quest/narrative generation via **graph rewriting**: the game world and narrative are graphs, and initial and secondary rewrite rules transform them. — [Kybartas, McGill thesis (eScholarship)](https://escholarship.mcgill.ca/downloads/v118rh70q)

### Inferences
- **Gothic quests fit the taxonomy closely.** Gothic 2 NotR examples:
  - "kill the bandits on the road" = Protection, "Attack threatening entities";
  - "bring me X" = Equipment, "Deliver supplies";
  - pickpocketing = Conquest or Wealth "steal";
  - militia investigations = Serenity, "Capture criminal" / "Check on NPC";
  - Lares or Vatras errands = Knowledge, "Spy" / "Interview NPC".

  A director can generate a quest **skeleton** with the Doran & Parberry grammar over the *current* world state: real NPCs, items, waypoints and guild attitudes known to the server. The **LLM** then writes dialog, motivation text and log entries. The skeleton's action terminals map onto checkable Gothic conditions: `Npc_HasItems`, `Npc_IsDead`, distance-to-waypoint, `Npc_KnowsInfo`, and so on.
- **Validation needs explicit, checkable conditions.** Doran & Parberry's informal pre- and postconditions should be made explicit predicates over the server's world model. A quest is valid if a forward simulation of the leaf actions from the current state succeeds and every referenced entity exists and is reachable (STRIPS-style checking). Ceptre-style or planner-based validation can catch quests broken by the player beforehand, e.g. the target NPC is already dead.
- **Use motivations to keep quests intentional.** Maintain per-NPC motivations, updated by sifted events and quest outcomes, and pick strategies from them. This avoids the "random fetch-quest" feel and gives the LLM a coherent reason to voice.
- **Planners vs grammars.** CONAN and de Lima show that planners give more world-grounded quests than grammars, at higher cost. A grammar plus world-state filtering is near-instant, so the practical design is the grammar for the skeleton and a planner only to check feasibility or fill sub-goals.

### Gaps
- I did not retrieve a dedicated "Questgen" paper beyond Doran & Parberry's generator, which their site calls Questgen ([questgen.txt](https://ianparberry.com/research/quests/questgen.txt)). If the brief meant a different "Questgen" (e.g. the LLM-based work by Värtinen et al. 2022), that falls into LLM-era scope covered by another researcher.
- CONAN's internals (planner used, number of quest types, quantitative results) and de Lima's GA fitness and evaluation details were not extracted from full texts.
- ReGEN's journal publication details and evaluation were not verified.
- No dedicated classical paper on "quest validation" was found in this pass. The validation notes above are inferences from the generators' precondition mechanics.

---

## 6. Player modeling, pacing (tension curves) and evaluation of interactive narrative

### Takeaway
Two pacing mechanisms are proven. Façade steers toward a target tension arc by greedily picking the next beat whose tension effect best follows the arc. Left 4 Dead's AI Director runs a measured-intensity state machine (Build Up, Sustain Peak, Peak Fade, Relax). Both are cheap and real-time. Player modeling ranges from hand-weighted type vectors (PaSSAGE) to collaborative filtering over many players' ratings (PBCF) and case-based models (Sharma et al.). Evaluation practice is weak but instructive: simulated-player score distributions (DODM), small user studies with subgroup effects (PaSSAGE), and audience-comprehension studies (IPOCL, Sabre).

### Cited Findings
- **Façade tension arc.** Beats carry story-tension effects, and the sequencer picks the eligible beat that best matches the near-term trajectory of an author-specified Aristotelian tension arc. — [Mateas & Stern 2005](https://cdn.aaai.org/AIIDE/2005/AIIDE05-016.pdf)
- **Left 4 Dead "Adaptive Dramatic Pacing"** (Booth, Valve, AIIDE 2009 talk).
  - Each Survivor has an **intensity** value. It rises in proportion to damage taken, on incapacitation, when pushed or pulled off a ledge, and when a nearby Infected dies (inversely proportional to distance). It decays toward zero, except while enemies are actively engaging.
  - The Director tracks the **max** intensity of the 4 Survivors and runs a cycle:
    1. **Build Up**: full threat population until intensity crosses the peak threshold.
    2. **Sustain Peak**: 3–5 s.
    3. **Peak Fade**: until intensity decays out of peak range.
    4. **Relax**: minimal threats for 30–45 s, or until the team has advanced far enough.
  - Boss encounters are exempt. The algorithm "adjusts pacing, not difficulty". "Simple algorithms can generate compelling pacing schedules", and "Survivor Intensity estimation is crude, yet the resulting pacing works."
  — [M. Booth, "The AI Systems of Left 4 Dead", AIIDE 2009 keynote slides (Valve)](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf) (slide text read from a local text copy of the deck; the Valve URL is the commonly used host and was not re-fetched in this pass); development background in [GamesRadar, "The Making Of... Left 4 Dead"](https://gamesradar.com/making-left-4-dead)
- **PaSSAGE player model and study results**: see Section 1. The model uses author-assigned weight deltas, and encounter selection uses an inner product. The effect was significant only for subgroups, among 90 participants. — [Thue et al. 2007](https://www.cs.uky.edu/~sgware/reading/papers/thue2007interactive.pdf)
- **Kinds of player modeling for interactive narrative.** One kind maps observed behavior into abstractions (Thue et al. 2007; Sharma et al. 2010). Another elicits structured feedback across many experiences (Yu & Riedl 2012). Player modeling for interactive narrative is in "early stages", focused on approaches that strongly conform to authorial intent. NPCs can also adapt behavior or dialogue to what they believe about the user. — [Riedl & Bulitko 2013](https://cs.uky.edu/~sgware/reading/papers/riedl2013interactive.pdf)
- **PBCF** treats drama management as sequential recommendation over story prefixes. Evaluated on choose-your-own-adventure stories, it improved the experience over designer intent alone. — [Yu & Riedl, AAMAS 2012](https://ifaamas.csc.liv.ac.uk/Proceedings/aamas2012/papers/2A_3.pdf); [Yu & Riedl, TCIAIG 2014](https://faculty.cc.gatech.edu/~riedl/pubs/tciaig14-yu.pdf)
- **Evaluation methods seen in the classical literature:**
  - (a) Story-quality *distribution* shift under simulated players with an author evaluation function: 10,000 unmanaged samples and 2,000 managed runs ([Nelson et al. 2006](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf)).
  - (b) Between-subjects user studies on fun, agency and replay ([Thue et al. 2007](https://www.cs.uky.edu/~sgware/reading/papers/thue2007interactive.pdf)).
  - (c) Audience comprehension of character intentions ([Riedl & Young 2010](https://arxiv.org/pdf/1401.3841)), and preference studies for intention plus belief ([cited in Ware & Siler 2021](https://cs.uky.edu/~sgware/reading/papers/ware2021sabre.pdf)).
  - (d) Structural comparison of generated quests with commercial quests by motivation ([Breault et al. 2018](https://arxiv.org/abs/1808.06217); [Doran & Parberry 2011](https://ianparberry.com/pubs/pcg2011.pdf)).
  - (e) Expert comparison against professional designers' quests ([de Lima et al. 2019](https://www.sbgames.org/sbgames2019/files/papers/ComputacaoFull/196293.pdf)).

### Inferences
- **Two-level pacing for Gothic.**
  - (1) A **combat/tension intensity** signal in L4D style, computed in the engine or server from damage taken, nearby kills, low HP and fleeing. It gates *when* the director may inject events (no new ambush during Relax, a reward or story beat in Relax).
  - (2) A **narrative tension arc per chapter** in Façade style. Each candidate quest or event carries an author- or LLM-estimated tension delta, and the director picks the one closest to the arc. Gothic 2's chapter structure (Kapitel 1–6) gives natural arc segments.
- **A PaSSAGE-style type vector is cheap to infer from Gothic telemetry.** Signals include combat frequency, pickpocketing and lockpicking, dialog length and choices, guild choice, and exploration. It can bias which quest motivation or strategy the director chooses (Fighter: Conquest/Protection; Power Gamer: Wealth/Equipment; Storyteller: Knowledge/Serenity). The LLM can summarize free-text conversations into the same vector, but the update rule should stay explicit.
- **Evaluation plan.** Adopt DODM's methodology early: a simulated-player harness (scripted or LLM-driven bots in the Python server) that measures the distribution of evaluation-function scores with and without the director. This is far cheaper than user studies and catches directors that make things *worse*, as SAS+ did on Anchorhead.

### Gaps
- I found no classical paper that operationalizes **Freytag's pyramid** as a formal target curve for a drama manager beyond Façade's "Aristotelian arc". The suspense and tension planners (e.g. Cheong & Young's Suspenser; O'Neill & Riedl's Dramatis) were not researched in this pass.
- Yannakakis & Togelius's experience-driven PCG framework and affective player-modeling literature were not covered.
- Quantitative PBCF results (rating improvements, sample sizes) were not extracted.

---

## 7. Which methods are cheap enough for real time, and how they pair with LLMs (content filler vs planner)

### Takeaway
Real-time-safe:
- precondition/priority storylet selection (Façade, PaSSAGE, Gothic's own `C_INFO`);
- incremental story sifting (Winnow);
- grammar-based quest skeletons (Doran & Parberry);
- intensity-driven pacing (Left 4 Dead);
- offline-trained DM policies (DODM RL, TTD-MDP).

Not real-time-safe unless the domain is tiny or the work is asynchronous or offline:
- online plot-graph search (SAS+);
- POCL narrative planning (IPOCL);
- belief-rich planning (Sabre on realistic domains).

The best-established classical-plus-LLM pattern keeps the symbolic layer as the authority on state, legality and coherence. The LLM acts as content filler, heuristic guide or evaluator, not as the sole planner.

### Cited Findings
- **Measured costs from the primary sources:**
  - Glaive solves small intentional/conflict problems in tens of milliseconds, e.g. Aladdin in 64 ms vs over 12 h for IPOCL, but fails on some 8-problem-suite domains (BLP-Die, Heist) within about 4–6 minutes ([Ware & Young 2014](https://www.cs.uky.edu/~sgware/reading/papers/ware2014glaive.pdf)).
  - Sabre ranges from 1 ms to 6.2 h depending on characters, actions and belief depth ([Ware & Siler 2021](https://cs.uky.edu/~sgware/reading/papers/ware2021sabre.pdf)).
  - SAS+ drama management with 2 s per decision failed to beat no-DM on parts of Anchorhead, while an offline TD-learned policy gives "nearly instant" responses ([Nelson et al. 2006](https://www.cs.uky.edu/~sgware/reading/papers/nelson2006declarative.pdf)).
  - The ASD moved replanning offline into contingency trees because planner time "can be unpredictable" ([Riedl et al. 2008](https://www.cs.uky.edu/~sgware/reading/papers/riedl2008dynamic.pdf)).
  - Winnow reduces per-event sifting work to checking stage transitions of tracked partial matches ([Kreminski et al. 2021](https://ojs.aaai.org/index.php/AIIDE/article/view/18903)).
  - L4D's pacing is a few counters and a 4-state machine ([Booth 2009](https://steamcdn-a.akamaihd.net/apps/valve/2009/ai_systems_of_l4d_mike_booth.pdf)).
- **Authoring cost is the classical bottleneck.** Façade needed about 3 person-years of authoring for 27 beats and about 2,500 jdbs ([Mateas & Stern 2005](https://cdn.aaai.org/AIIDE/2005/AIIDE05-016.pdf)). Branching content grows exponentially with choice points ([Riedl & Bulitko 2013](https://cs.uky.edu/~sgware/reading/papers/riedl2013interactive.pdf)). CiF needed over 5,000 social considerations for 18 characters ([Prom Week page](https://eis-blog.soe.ucsc.edu/2011/11/prom-week/)).
- **Documented classical-plus-LLM combination.** A symbolic state-space narrative planner over hand-written domains, with an LLM as search guide ranking which events to expand first ([Farrell & Ware, IEEE ToG 2025](https://www.cs.uky.edu/~sgware/reading/papers/farrell2024large.pdf)). Sifting combined with LLM narration ([Narrative Gold Digging](https://www.jonathanlessard.net/?p=357)). Other LLM-era hybrids are out of scope here.

### Inferences

**Suggested division of labor for a Gothic story director.** Synthesis, not from any single source:

| Layer | Classical method | Where | LLM role |
|---|---|---|---|
| World chronicle | Causal bookkeeping (Ryan) | Python server event log | Summarize free-text dialog into structured events or discourse acts (Façade-style mapping) |
| Opportunity detection | Incremental sifting (Winnow/Felt patterns) | Server, per event | Propose new patterns; score "interestingness" |
| Quest skeleton | Doran & Parberry motivation→strategy→action grammar, grounded in world state; optional small planner (Glaive-sized) for feasibility | Server, async (ms) | Choose motivation and strategy among valid options given NPC personality; write dialog, log entries, flavor |
| Validation | Explicit pre/postconditions; forward simulation; optional "explained action" check (IPOCL/Sabre idea) | Server | None, or explain failures |
| Selection and pacing | Façade-style precondition + priority + tension-arc fit; L4D intensity state machine; PaSSAGE type vector | Server tick (cheap) | Estimate tension deltas and player-type cues |
| Delivery | Storylet injection in `C_INFO` shape (condition / `nr` / `permanent` / `important`); DM actions via `Wld_InsertNpc`, `Npc_ExchangeRoutine`, `Wld_SetGuildAttitude`, `Log_*` | Engine (OpenGothic) | NPC lines in character |
| Repair | ASD tiered replanning, precomputed for likely threats (quest-giver death, item lost) | Server, async | Re-voice changed quest; pick in-world justification (accommodation) |
| Evaluation | DODM-style simulated-player score distributions | Offline harness | Simulated players; judge |

- **Keep the LLM away from hard state decisions.** "LLM as planner" with no symbolic check is what the classical literature warns against implicitly: IPOCL, Glaive and Sabre exist precisely because unconstrained generators produce unmotivated or impossible actions. The LLM should *propose* plans and the symbolic layer should *dispose*: validate, repair, or reject.
- **Replacing Daedalus quests wholesale is risky.** The classical evidence favors *wrapping* the existing system: keep hand-written main-plot quests as islands or "waypoints" (ASD islands; Short's waypoint narrative), and let the director generate side quests and events around them. Hand-written content stays the backbone, and generated content fills the gaps. This matches Façade (authored beats with dynamic sequencing) and PaSSAGE (authored encounters with dynamic selection).

### Gaps
- No classical study directly compares runtime and quality trade-offs across these families on the same domain. The cost comparisons above are assembled from separate papers on different hardware and domains.
- No evidence was found on how any of these methods behave at Gothic scale (hundreds of NPCs, persistent world, 30+ hour play). All published evaluations are on short experiences: Façade about 20 min, PaSSAGE a short NWN module, Anchorhead day two.
