# Western specialist TTS providers for Russian NPC voices (state as of 2026-10-11)

Scope: ElevenLabs, Inworld, Cartesia, Hume (priority), then Rime, LMNT, Resemble AI, Speechify/PlayHT, Murf, Sesame, plus other non-big-tech specialists near the top of the Artificial Analysis (AA) leaderboard. Baseline for comparison: Google Gemini 3.8 Flash TTS (AA lists it at $16.50/1M chars, Elo 1272).

Source-quality legend used below: **[OFFICIAL]** = vendor docs, pricing page, or changelog (fetched directly or quoted from a search snippet of the official page). **[SECONDARY]** = press, blogs, aggregators, or reviews. **[VENDOR-BIASED]** = a vendor writing about its own or competitors' products.

Important method note: every elevenlabs.io page (docs, blog, pricing) that the research tool tried to fetch returned an HTTP 302 redirect to ElevenLabs' help article "Do you restrict access to the service and platform for any specific countries". ElevenLabs facts below therefore come from search-result snippets of official pages plus secondary sources (TechCrunch, eesel.ai) that cite the official changelog. Re-verify them in a browser before relying on them.

Two headline facts for the report writer:
1. **Hume is shutting down its TTS (Octave) and EVI APIs on 2026-11-13.** It should be excluded as a candidate.
2. **ElevenLabs restricts access from Russia (and Belarus) under its official policy.** This matters for a Russian-speaking user and needs to be checked against where the user actually is.

---

## 1. Current models, IDs, release dates, and positioning (flagship vs low-latency)

### Takeaway
ElevenLabs released Eleven v4 (flagship) and v4 Turbo (real-time) on 2026-09-28, and both lead the AA English Speech Arena. Inworld's Realtime TTS-2 and TTS-2 Flash (May–Aug 2026) and Cartesia's Sonic 3.6 (Aug 2026) are the other current top-tier Western specialists. Hume Octave is being sunset, PlayHT is already dead, and Sesame has no public API.

### Cited Findings
**ElevenLabs**
- Eleven v4 was released 2026-09-28 in two variants: `eleven_v4` for audiobooks and voiceovers and `eleven_v4_turbo` for real-time voice agents [SECONDARY, cites changelog] — [eesel.ai](https://www.eesel.ai/blog/eleven-v4); [TechCrunch, 2026-09-28](https://techcrunch.com/2026/09/28/elevenlabs-new-v4-speech-model-supports-more-expression-control-and-90-languages/)
- v4 uses a new architecture that "reads a script like a voice actor would". Coverage grows from 70 languages (v3) to 90+. The character limit doubles to 10,000 per request, and Professional Voice Clones come back [SECONDARY] — [The Decoder](https://the-decoder.com/elevenlabs-new-v4-speech-model-makes-ai-voices-more-expressive-and-consistent/); [TechCrunch](https://techcrunch.com/2026/09/28/elevenlabs-new-v4-speech-model-supports-more-expression-control-and-90-languages/)
- The official models page snippet describes `eleven_v4_turbo` as "our most expressive, real-time speech synthesis model (~100ms†)" with 90+ languages [OFFICIAL snippet] — [ElevenLabs Models](https://elevenlabs.io/docs/overview/models). Launch coverage instead says Turbo "starts speaking in 150 milliseconds" [SECONDARY] — [DataNorth](https://datanorth.ai/news/elevenlabs-releases-eleven-v4-and-eleven-v4-turbo). The two latency claims conflict (see Q5).
- Older models still listed: Eleven v3 (70+ languages), v3 Conversational, Flash v2.5 (32 languages), and Multilingual v2 (29 languages) [SECONDARY / OFFICIAL snippets] — [eesel.ai](https://www.eesel.ai/blog/eleven-v4-pricing); [ElevenLabs Russian TTS page snippet](https://elevenlabs.io/text-to-speech/russian). eesel reports no deprecation date for v3 [SECONDARY] — [eesel.ai](https://www.eesel.ai/blog/eleven-v4)
- On OpenRouter, the model ID is `elevenlabs/eleven-v4` [OFFICIAL OpenRouter docs] — [OpenRouter](https://openrouter.ai/elevenlabs/eleven-v4)

**Inworld**
- Current models are Realtime TTS-2 (`inworld-tts-2`, the flagship) and Realtime TTS-2 Flash (`inworld-tts-2-flash`, the fastest and cheapest) [OFFICIAL] — [Inworld TTS models](https://docs.inworld.ai/tts/tts-models)
- Release notes give TTS-2 a launch date of 2026-05-05 and TTS-2 Flash a date of 2026-08-09. An OpenAI-compatible `POST /v1/audio/speech` endpoint followed on 2026-09-11 [OFFICIAL] — [Inworld release notes](https://docs.inworld.ai/release-notes/tts). A press release dated 2026-09-02 describes the "launch" of the TTS-2 family, probably as GA [SECONDARY] — [Yahoo Finance/press](https://finance.yahoo.com/technology/ai/articles/inworld-launches-realtime-tts-2-160000791.html). The dates conflict, most likely because the press release is the GA announcement.
- `inworld-tts-1` and `inworld-tts-1-max` were discontinued on 2026-06-15, and requests to them are auto-routed to newer models. TTS-1.5 Max and Mini are still referenced [SECONDARY summary of Inworld docs] — [search summary of docs.inworld.ai](https://docs.inworld.ai/tts/tts-models)

**Cartesia**
- The current model is Sonic 3.6. IDs are `sonic-3.6` (continuously updated), `sonic-3.6-2026-08-27` (dated snapshot), and `sonic-preview` (beta). The stable snapshot is dated 2026-08-27 (beta from 2026-08-17). Sonic 3.5 is still supported, and 3.6 is backwards compatible with it [OFFICIAL] — [Cartesia Sonic 3.6 docs](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest); [MarkTechPost, 2026-08-18](https://www.marktechpost.com/2026/08/18/cartesia-ships-sonic-3-6-a-streaming-tts-model-that-now-leads-both-artificial-analysis-speech-arenas/)
- A lower-latency "Sonic Turbo" variant (~40 ms) is referenced by third parties [SECONDARY] — [Coval, 2026-06-01](https://www.coval.ai/blog/best-text-to-speech-providers-in-2026-how-to-choose-(and-why-vendor-benchmarks-lie)/)

**Hume**
- Octave 1 (~200 ms) and Octave 2 (~100 ms, still marked "preview" in the docs) exist. Octave 2 became available on 2025-10-03 [OFFICIAL] — [Hume TTS overview](https://dev.hume.ai/docs/text-to-speech-tts/overview); [Hume changelog](https://dev.hume.ai/changelog)
- **Sunset, announced 2026-10-02:** "Hume is sunsetting the TTS and EVI APIs. Access ends November 13, 2026 at 12:01 a.m. EST… All account data is permanently deleted after November 13, 2026" [OFFICIAL] — [Hume changelog](https://dev.hume.ai/changelog)

**Others**
- Rime: Arcana v3 (model `arcana`) was released 2026-02-04 [OFFICIAL] — [Rime blog](https://www.rime.ai/resources/arcana-v3)
- Speechify (SpeechifyAI): Simba 3.2 is the current model and Simba 3.0 is the multilingual legacy model [OFFICIAL snippet] — [Speechify docs: models](https://docs.speechify.ai/build/guides/concepts/models)
- Murf: Falcon 2 is a real-time TTS API model [OFFICIAL] — [Murf Falcon 2 docs](https://murf.ai/api/docs/text-to-speech-models/falcon-2)
- PlayHT: acquired by Meta on 2025-07-12, and the API was shut down (permanently terminated on 2025-12-31) with no data export [SECONDARY] — [TextToLab](https://texttolab.com/blog/play-ht-shutdown-alternatives); [AnySpeech](https://anyspeech.io/playht-alternatives)
- Sesame: there is "no API available for Sesame's production voice". Only the open CSM-1B (English, Apache 2.0, Mar 2025) is reachable through third parties [SECONDARY] — [OrcaRouter](https://www.orcarouter.ai/blog/sesame-preview-android-launch); [HF csm-1b](https://huggingface.co/sesame/csm-1b)

**Leaderboard context (AA Text-to-Speech leaderboard, fetched 2026-10-11, English, user-vote Elo)** [INDEPENDENT] — [Artificial Analysis](https://artificialanalysis.ai/text-to-speech/leaderboard)

| # | Model (provider) | Elo | AA price per 1M chars |
|---|---|---|---|
| 1 | Eleven v4 Turbo (ElevenLabs) | 1327 | $40 |
| 2 | Eleven v4 (ElevenLabs) | 1323 | $80 |
| 3 | Qwen-Audio-3.1-TTS-Plus (Alibaba) | 1298 | $19.30 |
| 4 | Sonic 3.6 (Cartesia) | 1284 | $49 |
| 5 | Gemini 3.8 Flash TTS (Google) | 1272 | $16.50 |
| 7 | Realtime TTS-2 (Inworld) | 1257 | $20.80 |
| 9 | Simba 3.2 (Speechify) | 1243 | $6.60 |
| 10 | Breeze TTS 2 (BreezeBlue, open weights) | 1222 | $34 |
| 11 | Luna TTS (VUI Labs) | 1218 | $15 |
| 12 | Realtime TTS-2 Flash (Inworld) | 1217 | $10.40 |
| 15 | v3 Conversational (ElevenLabs) | 1207 | $50 |
| 16 | Sonic 3.5 (Cartesia) | 1191 | $49 |
| 17 | Soniox TTS Real-Time v2 | 1179 | $14.20 |
| 18 | Eleven v3 | 1176 | $100 |
| 20 | Lightning V3.1 Pro (Smallest.ai) | 1172 | $19.50 |
| 21 | Gradium TTS (Sep 2026) | 1168 | $47.80 |
| 24 | Falcon 2 (Murf) | 1151 | $10 |

- Gradium is a Paris-based company from the Kyutai team [SECONDARY / VENDOR-BIASED] — [Gradium](https://gradium.ai/content/best-text-to-speech-apis-2026)

### Inferences
- For a real-time NPC server, the current Western candidates are ElevenLabs v4 Turbo, Inworld TTS-2 or TTS-2 Flash, and Cartesia Sonic 3.6. Hume (sunset), PlayHT (dead), and Sesame (no API) drop out.
- ElevenLabs v4 is only about two weeks old, so independent evidence on its behaviour (especially in Russian) is minimal.

### Gaps
- I could not open ElevenLabs' official models page, v4 blog, or pricing page directly (geo-redirect), so the exact v4 Turbo latency, character limit, and language list are unconfirmed first-hand.
- No official release-date or GA note was found for Cartesia's "Sonic Turbo" in 2026.

---

## 2. Russian language support and evidence of quality

### Takeaway
ElevenLabs (v3/v4/Flash v2.5), Inworld TTS-2 (vendor "Tier 1"), Cartesia Sonic 3.6, LMNT, Chatterbox (Resemble, open source), and Hume Octave 2 officially or reportedly support Russian. Rime Arcana v3, Speechify Simba, Gradium, and Sesame do not. No independent Russian benchmark (WER/MOS/arena) exists for any of them: AA's multilingual arena does not include Russian. The only Russian-specific quality signals are scattered Habr articles.

### Cited Findings
**Official support statements**
- **Inworld**: TTS-2 and TTS-2 Flash accept 623 locale codes covering 470 languages. Russian (`ru`, default `ru-RU`) is in **Tier 1**, a group of 15 languages with "the highest, most consistent quality and the most natural pronunciation". Tier 2 has 79 languages and Tier 3 is "best-effort". The docs also describe cross-lingual voices that try to speak "natively, without carrying over the accent", plus a "voice localization" feature [OFFICIAL, vendor self-assessment] — [Inworld multilingual docs](https://docs.inworld.ai/tts/capabilities/multilingual)
- **Cartesia**: Sonic 3.6 supports 44 languages including Russian (`ru`) and Ukrainian (`uk`), and voice cloning works for all languages [OFFICIAL] — [Cartesia Sonic 3.6 docs](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest). However, the **emotion parameter "is supported only for English"** [OFFICIAL] — [Cartesia volume/speed/emotion docs](https://docs.cartesia.ai/build-with-cartesia/sonic-3/volume-speed-emotion)
- **ElevenLabs**: v4 claims 90+ languages, and launch coverage highlights improvements in Japanese, Brazilian Portuguese, Mandarin, and Cantonese (Russian is not mentioned) [SECONDARY] — [TechCrunch](https://techcrunch.com/2026/09/28/elevenlabs-new-v4-speech-model-supports-more-expression-control-and-90-languages/). Russian is one of the 70+ languages of v3 and of Voice Design (TTV v3), and the platform has a dedicated Russian TTS page offering Russian voices (e.g. Moscow and St Petersburg accents) [OFFICIAL snippets] — [ElevenLabs Russian TTS](https://elevenlabs.io/text-to-speech/russian); [ElevenLabs v3](https://elevenlabs.io/v3)
- ElevenLabs v4 accent behaviour: cloned voices keep the source accent only in their native language. Switching the output language triggers the native accent of the target language, and there is no toggle yet [SECONDARY, cites v4 FAQ] — [eesel.ai](https://www.eesel.ai/blog/eleven-v4). For an English-sourced or OpenRouter premade voice speaking Russian, this should give a native-like Russian accent.
- **Hume**: Octave 2 supports Russian among 11 languages, but the service is being sunset [OFFICIAL] — [Hume TTS overview](https://dev.hume.ai/docs/text-to-speech-tts/overview)
- **LMNT**: supports 31 languages including Russian [SECONDARY] — [Coval](https://www.coval.ai/blog/best-text-to-speech-providers-in-2026-how-to-choose-(and-why-vendor-benchmarks-lie)/); [search summary of LMNT reviews](https://coldiq.com/tools/lmnt)
- **Resemble AI**: open-source Chatterbox Multilingual covers 23 languages including Russian [SECONDARY / GitHub] — [Hugging Face ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox). There is an open GitHub issue titled "Russian TTS quality degrades after multiple generations" — [GitHub issue #360](https://github.com/resemble-ai/chatterbox/issues/360)
- **Murf Falcon 2**: third-party and marketing summaries say 35+ languages including Russian, and Murf has a Russian TTS page [SECONDARY] — [Murf Russian TTS page](https://murf.ai/text-to-speech/russian). The Falcon 2 API doc page does not explicitly list Russian [OFFICIAL], so the two sources conflict — [Murf Falcon 2 docs](https://murf.ai/api/docs/text-to-speech-models/falcon-2)
- **No Russian**:
  - Rime Arcana v3 supports 10 languages (EN, HI, ES, AR, FR, PT, DE, JA, HE, TA) [OFFICIAL] — [Rime](https://www.rime.ai/resources/arcana-v3)
  - Speechify Simba 3.2 is English-only, and Simba 3.0 covers EN/DE/ES/FR/IT/PT-BR [OFFICIAL snippet] — [Speechify language support](https://docs.speechify.ai/build/guides/text-to-speech/language-support?elementor_library=publisher-audiobook)
  - Gradium supports EN/FR/ES/DE/PT [VENDOR] — [Gradium](https://gradium.ai/content/best-voice-cloning-apis-2026)
  - Sesame CSM-1B is English-only [SECONDARY] — [HF csm-1b](https://huggingface.co/sesame/csm-1b)

**Independent or quasi-independent quality evidence for Russian**
- AA's multilingual arena covers 9 languages beyond English (JA, ZH, HI, ES, DE, FR, PT, VI, AR). **Russian is not included** [INDEPENDENT] — [AA on X](https://x.com/ArtificialAnlys/status/2102490340678856997)
- The Hugging Face Open TTS Leaderboard (2026-09-30) evaluates only open-source models and does not document Russian [INDEPENDENT] — [HF blog](https://huggingface.co/blog/open-tts-leaderboard)
- Habr (Bothub, 2026-09-12) calls ElevenLabs v3 "the best there is right now for expressiveness in Russian": it understands audio tags, holds pauses, and changes tempo within a phrase. The article stresses that every model needs text preparation for Russian, including manual stress marks (ударения), homograph disambiguation, numbers, and abbreviations. Its measured cost was ~26.60 ₽ per 1,000 chars [SECONDARY; Bothub is an AI-access reseller, so possible bias] — [Habr/Bothub](https://habr.com/ru/companies/bothub/articles/1081544/)
- Habr (2026-03-28, "tested 30+ voice AI engines"): its table lists ElevenLabs Flash v2.5 (~395 ms TTFB, "excellent"), Cartesia Sonic Turbo (~245 ms, "good"), and Hume Octave 2 (~800 ms, "excellent") as supporting Russian. The author concludes that open source "doesn't reach" cloud quality for Russian and that ElevenLabs costs "4–20× more than competitors" [SECONDARY; individual blogger; the ELO and price-per-hour figures in the table look non-standard, e.g. $0.26/h for Hume, so treat them with caution] — [Habr](https://habr.com/ru/articles/1016156/)
- A Habr Bothub summary (date unclear, likely early 2026) says "Inworld TTS-1.5-Max does not support Russian". This conflicts with Inworld docs, which list Russian for TTS-1.5 and as Tier 1 for TTS-2 [SECONDARY, likely outdated] — [Habr/Bothub news](https://habr.com/ru/companies/bothub/news/1014988/); [Inworld docs](https://docs.inworld.ai/tts/capabilities/multilingual)

### Inferences
- On paper, Inworld TTS-2 has the strongest Russian commitment ("Tier 1" plus voice localization), but that is a self-assessment with no independent Russian test found.
- ElevenLabs has the most anecdotal Russian praise (v3 expressiveness). v4 is too new for any Russian reviews.
- Cartesia Sonic 3.6 is usable for Russian, but its emotion control is English-only. For an expressive NPC (Xardas: commanding, menacing) that is a real limitation compared with ElevenLabs audio tags or Inworld steering.
- Stress errors (ударения) are a cross-vendor Russian problem. Pronunciation override features (ElevenLabs IPA, Inworld custom pronunciation, see Q3/Q4) are the mitigation. The project should plan a stress-marking or lexicon step for Gothic proper nouns (Ксардас, Хоринис, Миненталь…) whichever vendor it picks.

### Gaps
- No WER/CER/MOS numbers for Russian were found for any commercial Western TTS in 2026.
- No native-speaker review of ElevenLabs v4, Inworld TTS-2, or Cartesia Sonic 3.6 in Russian was found. A blind A/B test with project lines is recommended.
- I found no data on how well these models handle Russian stress on fantasy proper nouns.

---

## 3. Custom voices: voice design from text, cloning, libraries, character/fantasy voices

### Takeaway
ElevenLabs (Voice Design v3, 70+ languages, plus IVC from ~10 s and PVC) and Inworld (Voice Design API with an English prompt, IVC from ≥3 s, PVC beta) both offer text-prompt voice design, which is the Gemini-style workflow the project wants. Cartesia offers cloning only (IVC on Pro+, PVC on Startup+); I found no prompt-based design. OpenRouter's ElevenLabs passthrough exposes only 21 premade voices.

### Cited Findings
**ElevenLabs**
- Voice Design creates a voice "from a text prompt by describing the unique characteristics", and the TTV v3 model supports 70+ languages [OFFICIAL snippet] — [ElevenLabs Voice Design](https://elevenlabs.io/voice-design)
- The voice library holds 11,000+ voices (another source says 17,000+) [OFFICIAL snippet / SECONDARY; figures conflict] — [ElevenLabs voices docs](https://elevenlabs.io/docs/overview/capabilities/voices)
- v4 can clone a voice from about 10 seconds of audio, and Professional Voice Clones return with v4 ("rolling out to everyone") [SECONDARY] — [TechCrunch](https://techcrunch.com/2026/09/28/elevenlabs-new-v4-speech-model-supports-more-expression-control-and-90-languages/); [eesel.ai](https://www.eesel.ai/blog/eleven-v4)
- Migration risk: v4 clones "may sound quite different from its Eleven v3 version" [SECONDARY, cites FAQ] — [eesel.ai](https://www.eesel.ai/blog/eleven-v4)
- Cloning rules: you must own the voice or have explicit consent, public figures are prohibited, and accounts have been suspended for violations [SECONDARY] — [search summary of ElevenLabs licensing guides](https://licenseorg.com/blog/ai-voice-licensing-elevenlabs)
- On OpenRouter, `elevenlabs/eleven-v4` lists 21 voices (george, sarah, adam, alice, bella, bill, brian, callum, charlie, chris, daniel, eric, harry, jessica, laura, liam, lily, matilda, river, roger, will) and formats mp3/pcm. Custom or cloned voices are not mentioned [OFFICIAL OpenRouter] — [OpenRouter](https://openrouter.ai/elevenlabs/eleven-v4)

**Inworld**
- Voice Design creates a new voice from a text description plus a script and returns up to 3 previews; the chosen one is published to the voice library with `design-voice` → `publish-voice` [OFFICIAL] — [Inworld Voice Design docs](https://docs.inworld.ai/tts/voice-design); [Design a voice API](https://docs.inworld.ai/api-reference/voiceAPI/voiceservice/design-voice)
- The design prompt "must be in English and up to 1000 characters", and descriptions under 30 characters "rarely produce a useful voice". The docs recommend specifying age, gender, accent, pitch, pace, and tone [OFFICIAL] — [Inworld Voice Design](https://docs.inworld.ai/tts/voice-design)
- TTS-2 "supports text-based voice design" [SECONDARY press] — [01net press release](https://www.01net.it/inworld-launches-realtime-tts-2-voice-model-family-for-controllable-realtime-speech/)
- Instant voice cloning is available on both TTS-2 models, Professional voice cloning is Beta on TTS-2 only, and custom pronunciation is supported [OFFICIAL] — [Inworld TTS models](https://docs.inworld.ai/tts/tts-models)
- The SDK's `clone_voice()` needs at least 3 seconds of audio [OFFICIAL] — [Inworld Python SDK](https://docs.inworld.ai/tts/python-sdk)
- All pricing plans include voice cloning, and Developer tier and above add "Professional voice cloning (add-on)" [OFFICIAL] — [Inworld pricing](https://inworld.ai/pricing)

**Cartesia**
- Instant voice cloning is available on Pro and above. Professional voice cloning is on Startup (2 slots), Scale (4 slots), and Enterprise. The pricing page mentions no prompt-based voice design [OFFICIAL] — [Cartesia pricing](https://cartesia.ai/pricing)
- Cloning is supported across all Sonic 3.6 languages [OFFICIAL] — [Cartesia Sonic 3.6](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest)

**Hume (sunsetting)**
- Voice Design is available in Octave 2 for English only, and cloning needs ~15 s of audio [OFFICIAL] — [Hume TTS overview](https://dev.hume.ai/docs/text-to-speech-tts/overview)

**Others**
- LMNT clones from a 5-second sample [SECONDARY] — [search summary](https://coldiq.com/tools/lmnt)
- Gradium: instant clone from 10 s; Pro cloning starts on the M plan ($340/mo) [VENDOR] — [Gradium cloning](https://gradium.ai/content/how-to-clone-a-voice-create-custom-voice)
- Murf Falcon 2: "150+ voices in 20+ expressive styles" [OFFICIAL] — [Murf Falcon 2](https://murf.ai/api/docs/text-to-speech-models/falcon-2)
- Resemble Chatterbox: voice cloning from short clips, plus exaggeration and cfg controls [SECONDARY] — [fal.ai](https://fal.ai/learn/tools/best-text-to-speech-apis)

### Inferences
- For Xardas-style voices without actor recordings, ElevenLabs Voice Design (multilingual) or Inworld Voice Design (English prompt, voice then localized to Russian) are the closest substitutes for Gemini Voice Design.
- ElevenLabs' docs say v4 gives target-language-native accents. Inworld advertises cross-lingual "native" speech. Both should therefore let an English-designed "old deep necromancer" voice speak Russian without an English accent, but this is unverified.
- Cloning the original Russian Gothic 2 voice actors would raise consent and rights problems under ElevenLabs' and other vendors' rules. Designed voices avoid that.
- Using ElevenLabs through OpenRouter limits the project to 21 premade, mostly English-persona voices. That falls well short of distinct per-NPC voices, so direct ElevenLabs API access would be needed for designed or cloned voices.

### Gaps
- I found no source on the number of Russian-native voices in the ElevenLabs, Inworld, or Cartesia libraries.
- Specific fantasy or character voice presets (wizard, orc) in each library were not verified.
- Whether ElevenLabs Voice Design has a v4 version, or whether v3-designed voices carry over to v4, is unconfirmed.
- No official Cartesia "voice design from prompt" feature was found. It may exist in the playground (unverified).

---

## 4. Expressiveness: emotion and style control, tags, SSML, multi-speaker

### Takeaway
ElevenLabs v4 uses stackable inline audio tags (`[whispers]`, `[sighs]`…), drops the Style/Speed sliders and SSML, and adds a Text-to-Dialogue API. Inworld TTS-2 uses free-form bracketed natural-language steering plus non-verbal tags (not available on Flash). Cartesia offers an emotion enum, speed, and volume, but emotion works only in English. Only `[laughter]` and SSML apply to Russian.

### Cited Findings
**ElevenLabs v4**
- Directions such as `[laughs]`, `[whispers]`, and `[door slams]` go directly in the script, and multiple tags can be stacked with the model following the sequence [SECONDARY] — [The Decoder](https://the-decoder.com/elevenlabs-new-v4-speech-model-makes-ai-voices-more-expressive-and-consistent/); [TechCrunch](https://techcrunch.com/2026/09/28/elevenlabs-new-v4-speech-model-supports-more-expression-control-and-90-languages/)
- The Style and Speed sliders are removed, IPA pronunciation works natively, and SSML is not supported [SECONDARY, cites docs] — [eesel.ai](https://www.eesel.ai/blog/eleven-v4)
- v4 supports multi-speaker dialogue through the Text to Dialogue API. v4 Turbo uses the Text to Dialogue WebSocket (bidirectional) and ElevenAgents [SECONDARY, cites docs] — [eesel.ai](https://www.eesel.ai/blog/eleven-v4)
- For v3, a Habr reviewer reports that audio tags, pauses, and tempo changes work in Russian [SECONDARY] — [Habr/Bothub](https://habr.com/ru/companies/bothub/articles/1081544/)

**Inworld TTS-2**
- Steering is written as `[say excitedly with a high pitch and fast pace]`, and complex mixes such as `[say sadly with deliberate pauses in a low voice and hushed style]` are allowed. Steering stays active until replaced or `[reset]`, and since 2026-08-06 it survives `<break/>` pauses [OFFICIAL] — [Inworld prompting guide](https://docs.inworld.ai/tts/best-practices/prompting-for-tts-2); [release notes](https://docs.inworld.ai/release-notes/tts)
- Non-verbals: `[laugh]`, `[sigh]`, `[clear throat]`, `[breathe]`, `[cough]`, `[yawn]` [OFFICIAL] — [Inworld prompting guide](https://docs.inworld.ai/tts/best-practices/prompting-for-tts-2)
- "Steering is supported only on `inworld-tts-2`. `inworld-tts-2-flash` does not support steering" [OFFICIAL] — [Inworld prompting guide](https://docs.inworld.ai/tts/best-practices/prompting-for-tts-2)
- Other features: pause controls and timestamp alignment with phonemes and visemes, which could drive lip-sync [OFFICIAL] — [Inworld TTS models](https://docs.inworld.ai/tts/tts-models)
- TTS-2 "hears the full audio of the exchange" (conversational context), aimed at voice agents [SECONDARY press] — [01net](https://www.01net.it/inworld-launches-realtime-tts-2-voice-model-family-for-controllable-realtime-speech/)

**Cartesia Sonic 3.x**
- `generation_config` sets speed (0.6–1.5) and volume (0.5–2.0), and an emotion enum covers 40+ emotions (best results for neutral, calm, angry, content, sad, scared). **Emotion tags are supported only for English.** `[laughter]` inserts laughter, and SSML tags are also supported [OFFICIAL] — [Cartesia docs](https://docs.cartesia.ai/build-with-cartesia/sonic-3/volume-speed-emotion)
- Sonic 3.6 adapts pause length to context and turns disfluencies ("uhm") into natural pacing "without SSML tags or explicit instructions" [OFFICIAL] — [Cartesia Sonic 3.6](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest)

**Hume Octave 2 (sunsetting)**
- "Acting instructions" for Octave 2 are marked "Coming soon" [OFFICIAL] — [Hume](https://dev.hume.ai/docs/text-to-speech-tts/overview)

**Others**
- Rime Arcana v3 shows emotional range and multilingual code-switching, but its control mechanism is not documented [OFFICIAL] — [Rime](https://www.rime.ai/resources/arcana-v3)
- Chatterbox has paralinguistic tags and an "exaggeration" control [SECONDARY] — [fal.ai](https://fal.ai/learn/tools/best-text-to-speech-apis)

### Inferences
- The LLM can emit Inworld-style natural-language direction (`[speak slowly in a deep, commanding, menacing tone]`) or ElevenLabs audio tags alongside the reply text, which matches the existing LLM-tool-call architecture. Gemini's style prompts are similar in spirit, so a common "direction" field in the adapter interface could map to each vendor's syntax.
- Inworld docs do not say whether steering instructions must be written in English when the text is Russian. Writing instructions in English and the line in Russian is the safe default.

### Gaps
- No evidence was found on how well ElevenLabs v4 tags or Inworld steering work on Russian text.
- The ElevenLabs v4 tag vocabulary is not fully documented in sources I could access.

---

## 5. Streaming, latency (time to first audio), and output formats

### Takeaway
All three main candidates stream over WebSocket and HTTP and return raw PCM. Vendor-claimed model-side latency is ~20 ms (Inworld TTS-2 Flash, P90), ~100 ms (Inworld TTS-2, ElevenLabs v4 Turbo), and sub-90 ms (Cartesia Sonic 3.6). Independent end-to-end measurements are much higher (e.g. 245–395 ms from a Habr test). For 1–3 sentence NPC replies, any of them is fast enough.

### Cited Findings
**ElevenLabs**
- v4 Turbo: "~100ms†" (official models page snippet) versus "starts speaking in 150 milliseconds" (launch coverage); the figures conflict [OFFICIAL snippet / SECONDARY] — [ElevenLabs models](https://elevenlabs.io/docs/overview/models); [DataNorth](https://datanorth.ai/news/elevenlabs-releases-eleven-v4-and-eleven-v4-turbo)
- v4 (non-Turbo) is "not real-time" [SECONDARY] — [eesel.ai](https://www.eesel.ai/blog/eleven-v4)
- Flash v2.5 is ~75 ms TTFB by vendor claim [SECONDARY] — [Coval](https://www.coval.ai/blog/best-text-to-speech-providers-in-2026-how-to-choose-(and-why-vendor-benchmarks-lie)/)
- A Habr blogger measured ~395 ms TTFB for Flash v2.5 [SECONDARY, independent] — [Habr](https://habr.com/ru/articles/1016156/)
- Output: `pcm_24000` (16-bit PCM 24 kHz) and other formats; WebSocket streaming sends text chunks and receives audio chunks [SECONDARY on SDK] — [PyPI elevenlabs](https://pypi.org/project/elevenlabs/2.60.0/). On OpenRouter, v4 outputs mp3 or pcm (pcm is the default) [OFFICIAL OpenRouter] — [OpenRouter](https://openrouter.ai/elevenlabs/eleven-v4)

**Inworld**
- TTS-2 is ~100 ms TTFB, and TTS-2 Flash is ~20 ms ("20 ms time to first audio (server-side P90 TTFB)", 5× faster than TTS-2) [OFFICIAL] — [Inworld TTS models](https://docs.inworld.ai/tts/tts-models); [release notes](https://docs.inworld.ai/release-notes/tts)
- A secondary source says "under 25 ms TTFB at P99" for Flash [SECONDARY] — [search summary of Inworld blog](https://inworld.ai/blog/realtime-tts-2)
- Formats: MP3, OGG_OPUS, FLAC, LINEAR16, WAV, PCM, ALAW, MULAW. `sample_rate` is configurable with a default of 48,000 Hz [OFFICIAL] — [Inworld Python SDK](https://docs.inworld.ai/tts/python-sdk)
- The OpenAI-compatible endpoint (2026-09-11) supports mp3, opus, flac, wav, pcm, and SSE [OFFICIAL] — [release notes](https://docs.inworld.ai/release-notes/tts)
- Inworld TTS 1.5 Max measured at ~180 ms TTFB [SECONDARY] — [Coval](https://www.coval.ai/blog/best-text-to-speech-providers-in-2026-how-to-choose-(and-why-vendor-benchmarks-lie)/)

**Cartesia**
- Sonic 3.6 advertises "sub-90-millisecond model latency". Third parties note that this is model-side only and excludes network, TLS, and buffering [SECONDARY] — [MarkTechPost](https://www.marktechpost.com/2026/08/18/cartesia-ships-sonic-3-6-a-streaming-tts-model-that-now-leads-both-artificial-analysis-speech-arenas/); [search summary](https://www.therundown.ai/tools/sonic-3-6)
- Sonic-3 is ~90 ms and the Turbo variant ~40 ms [SECONDARY] — [Coval](https://www.coval.ai/blog/best-text-to-speech-providers-in-2026-how-to-choose-(and-why-vendor-benchmarks-lie)/)
- Habr measured Cartesia Sonic Turbo at ~245 ms TTFB [SECONDARY] — [Habr](https://habr.com/ru/articles/1016156/)
- Interfaces are bytes, SSE, and WebSocket, with `pcm_s16le` output at configurable sample rates (e.g. 24,000 or 44,100) [SECONDARY] — [Cartesia quickstart](https://docs.cartesia.ai/get-started/realtime-text-to-speech-quickstart)

**Hume (sunsetting)**
- Octave 2 is ~100 ms and Octave 1 ~200 ms, with HTTP streaming, WebSocket bidirectional streaming, and MP3/WAV/PCM output [OFFICIAL] — [Hume](https://dev.hume.ai/docs/text-to-speech-tts/overview)

**Others**
- Murf Falcon 2: ~100 ms TTFA. Murf's benchmark page claims 61–97 ms across regions, with WebSocket and streaming supported [OFFICIAL / VENDOR-BIASED] — [Murf Falcon 2](https://murf.ai/api/docs/text-to-speech-models/falcon-2); [Murf benchmarks](https://murf.ai/falcon/benchmarks)
- Rime Arcana v3: ~200 ms TTFB via cloud API and 120 ms on-prem [OFFICIAL] — [Rime](https://www.rime.ai/resources/arcana-v3)
- LMNT: ~140 ms TTFB [SECONDARY] — [Coval](https://www.coval.ai/blog/best-text-to-speech-providers-in-2026-how-to-choose-(and-why-vendor-benchmarks-lie)/)
- Coval warns that vendor latency usually means TTFB, not voice-to-voice (the two can differ by 300–500 ms), and that it is rarely reported at P95/P99 [SECONDARY, independent eval vendor] — [Coval](https://www.coval.ai/blog/best-text-to-speech-providers-in-2026-how-to-choose-(and-why-vendor-benchmarks-lie)/)

### Inferences
- All the candidates can output 16-bit PCM at 24 kHz or higher, which fits a Python asyncio server that forwards PCM to OpenGothic.
- From Europe or the CIS, network RTT to US-hosted endpoints will dominate. Expect roughly 200–450 ms to first audio in practice for any of them, which is fine for NPC dialogue.

### Gaps
- No independent (non-vendor) latency measurements of ElevenLabs v4 Turbo, Inworld TTS-2/Flash, or Cartesia Sonic 3.6 were found. AA's leaderboard page shows no latency.
- No region or endpoint list (EU endpoints) was gathered for these vendors.

---

## 6. Python integration (SDK, async)

### Takeaway
All three main candidates have official Python SDKs with asyncio support: `elevenlabs` (AsyncElevenLabs), `inworld-tts` (async streaming, Python ≥3.10), and `cartesia[websockets]`. Inworld also has an OpenAI-compatible `/v1/audio/speech` endpoint, so a generic OpenAI-style adapter could cover Inworld and OpenRouter.

### Cited Findings
- **ElevenLabs**: the `elevenlabs` PyPI package offers synchronous and asynchronous clients (`AsyncElevenLabs`), WebSocket real-time streaming, and the `pcm_24000` output format [SECONDARY summary of SDK docs] — [PyPI elevenlabs 2.60.0](https://pypi.org/project/elevenlabs/2.60.0/); [elevenlabs-python quickstart](https://mintlify.com/elevenlabs/elevenlabs-python/quickstart)
- **Inworld**: `pip install inworld-tts` (Python 3.10+). It provides `stream()` and `stream_with_timestamps()` with `async for` iteration, `clone_voice()`, `design_voice()`, and `publish_voice()`, plus automatic chunking of long texts [OFFICIAL] — [Inworld Python SDK](https://docs.inworld.ai/tts/python-sdk)
- Inworld's OpenAI-compatible `POST /v1/audio/speech` launched 2026-09-11 [OFFICIAL] — [Inworld release notes](https://docs.inworld.ai/release-notes/tts)
- Inworld TTS-2 is also served by DeepInfra and Cloudflare Workers AI [SECONDARY] — [DeepInfra](https://deepinfra.com/inworld-ai/realtime-tts-2/voice); [Cloudflare](https://developers.cloudflare.com/ai/models/inworld/tts-2/)
- **Cartesia**: `python -m pip install "cartesia[websockets]"` gives WebSocket TTS (`client.tts.websocket()`); the SDK requires Python 3.9+ [SECONDARY summary of docs] — [Cartesia quickstart](https://docs.cartesia.ai/get-started/realtime-text-to-speech-quickstart)
- **Hume** has a Python SDK, but the service is sunsetting [OFFICIAL] — [Hume](https://dev.hume.ai/docs/text-to-speech-tts/overview)
- **OpenRouter** exposes ElevenLabs via `/api/v1/audio/speech` with model `elevenlabs/eleven-v4` [OFFICIAL OpenRouter] — [OpenRouter](https://openrouter.ai/elevenlabs/eleven-v4)

### Inferences
- An `OpenAICompatibleSpeechAdapter` could serve OpenRouter (ElevenLabs premade voices) and Inworld through a single code path. Native SDK adapters would be needed for ElevenLabs designed or cloned voices and for Inworld steering or voice design (unless the OpenAI-compatible endpoint accepts custom voice IDs, which is unverified).

### Gaps
- Whether Inworld's OpenAI-compatible endpoint accepts custom (designed or cloned) voice IDs and steering tags is unverified.
- No AsyncCartesia class documentation was confirmed in the sources fetched.

---

## 7. Pricing, normalized to USD per 1M characters and per hour of audio

### Takeaway
At list prices, the cheapest Russian-capable high-quality options are Inworld TTS-2 Flash (~$7–15/1M) and Murf Falcon 2 ($10/1M, Russian support unconfirmed), then Inworld TTS-2 (~$12.5–25/1M). Gemini 3.8 Flash TTS sits at ~$16.50/1M on AA. Cartesia Sonic 3.6 costs ~$37–65/1M and ElevenLabs v4 Turbo $40/1M, while ElevenLabs v4 and v3 cost $80–100/1M. NPC-dialogue volumes for a hobby mod are tiny, so the absolute cost difference is a few dollars a month.

Normalization assumption: ~1,000 characters per minute of audio, i.e. ~60,000 chars per hour. This is Inworld's stated assumption [OFFICIAL] — [Inworld pricing](https://inworld.ai/pricing); Cartesia's figure of "750–800 credits/minute" is similar [OFFICIAL] — [Cartesia pricing](https://cartesia.ai/pricing). Rule of thumb: $/hour ≈ $/1M × 0.06.

| Provider / model | Pricing (source) | ≈ $/1M chars | ≈ $/hour audio | Free tier |
|---|---|---|---|---|
| ElevenLabs v4 | $0.08/1K chars list; 72% launch discount ($0.022) until 2026-10-12 [SECONDARY, cites pricing page] — [eesel](https://www.eesel.ai/blog/eleven-v4-pricing) | $80 (AA: $80) | ~$4.80 | 10K credits/mo, non-commercial |
| ElevenLabs v4 Turbo | $0.04/1K list ($0.011 during launch) — [eesel](https://www.eesel.ai/blog/eleven-v4-pricing) | $40 (AA: $40) | ~$2.40 | same |
| ElevenLabs v3 | $0.08/1K per eesel; AA lists $100/1M (the sources conflict) — [eesel](https://www.eesel.ai/blog/eleven-v4-pricing); [AA](https://artificialanalysis.ai/text-to-speech/leaderboard) | $80–100 | ~$4.8–6 | same |
| ElevenLabs v3 Conversational | $0.04/1K per eesel; AA $50/1M | $40–50 | ~$2.4–3 | same |
| ElevenLabs Flash v2.5 | $0.04/1K [SECONDARY] — [activepieces/search summary](https://www.activepieces.com/blog/elevenlabs-pricing-2026-plans-credits-and-costs) | ~$40 | ~$2.40 | same |
| Inworld Realtime TTS-2 | $25 On-Demand → $20 Creator → $17.50 Builder → $15 Developer → $12.50 Growth → "as low as $5" Enterprise [OFFICIAL] — [Inworld pricing](https://inworld.ai/pricing) | $12.5–25 (AA: $20.80) | ~$0.75–1.50 | On-Demand: "up to 70 minutes TTS included" |
| Inworld TTS-2 Flash | $15 On-Demand → $10 → $9 → $8 → $7 → "sub-$5" [OFFICIAL] — [Inworld pricing](https://inworld.ai/pricing) | $7–15 (AA: $10.40) | ~$0.42–0.90 | same |
| Cartesia Sonic 3.6 | 1 credit = 1 char. Pro $5/100K (= $50/1M), Startup $49/1.25M (= $39.2/1M), Scale $299/8M (= $37.4/1M); overage $65/$45/$38 per 1M credits [OFFICIAL; my arithmetic] — [Cartesia pricing](https://cartesia.ai/pricing) | ~$37–65 (AA: $49) | ~$2.2–3.9 | Free: 20K credits (~27 min), no commercial license |
| Hume Octave 2 (sunsetting) | ~$7.60/1M at scale; plans $3–$500/mo [SECONDARY] — [search summary](https://futureagi.com/blog/best-text-to-speech-providers-2026/) | ~$7.6–150 | — | 10K chars |
| Murf Falcon 2 | "1 cent per 1000 characters" / $0.01/min [OFFICIAL] — [Murf Falcon 2](https://murf.ai/api/docs/text-to-speech-models/falcon-2) | $10 (AA: $10) | ~$0.60 | ? |
| LMNT | $35/1M; plans from $10/mo for 200K chars [SECONDARY] — [search summary](https://coldiq.com/tools/lmnt) | ~$35–50 | ~$2.1–3 | ? |
| Speechify Simba 3.2 (no Russian) | $10/1M entry, $6/1M Scale [SECONDARY] — [Speechify blog](https://speechify.ai/blog/tts-api-pricing-at-volume-2026) | $6–10 | ~$0.4–0.6 | — |
| Gradium (no Russian) | $58/1M list; XS plan $13/mo for 225K credits [VENDOR] — [Gradium](https://gradium.ai/content/best-voice-cloning-apis-2026); AA $47.80 | ~$48–58 | ~$3 | yes (5 clones) |
| Resemble Chatterbox (open source) | Free self-hosted (MIT). Hosted: ~$25/1M on fal, $1/1M on DeepInfra; Chatterbox HD $40/1M [SECONDARY] — [fal.ai](https://fal.ai/learn/tools/best-text-to-speech-apis) | $1–40 | — | — |
| *Baseline: Gemini 3.8 Flash TTS* | AA estimate — [AA](https://artificialanalysis.ai/text-to-speech/leaderboard) | $16.50 | ~$1 | — |

**ElevenLabs subscription plans (2026)** [SECONDARY] — [eesel](https://www.eesel.ai/blog/eleven-v4-pricing)
- Creative-app plans: Free $0 (10K credits), Starter $6 (30K), Creator $22 (121K), Pro $99 (600K), Scale $299 (1.8M), Business $990 (6M).
- API plans at list price after 2026-10-12, in v4 characters: Starter $6 (75K), Creator $22 (275K), Pro $99 (1.238M), Scale $299 (3.738M), Business $990 (12.375M).
- Credit rollover is capped at 3 months, and commercial use requires Starter or higher.

**Inworld subscription plans** [OFFICIAL] — [Inworld pricing](https://inworld.ai/pricing)
- Creator $25/mo, Builder $100, Developer $300, Growth $1,500. Each plan includes credits equal to its price.

**OpenRouter**
- OpenRouter pricing for `elevenlabs/eleven-v4` was not shown on the doc page I fetched [OFFICIAL] — [OpenRouter](https://openrouter.ai/elevenlabs/eleven-v4)

### Inferences
- Example budget: 1,000 NPC lines × ~150 chars ≈ 150K chars/month. That costs roughly $1.5–2.5 on Inworld Flash or TTS-2, ~$6–12 on ElevenLabs v4 Turbo or v4 (fits the $6 Starter or $22 Creator plan), and ~$6 on Cartesia (fits Startup or Pro with overage).
- Price should not drive this decision at hobby scale. Russian quality, voice design, and availability matter more.
- The ElevenLabs launch discount ends 2026-10-12 (tomorrow relative to today), so any cost tests after that should use list prices.

### Gaps
- Official ElevenLabs pricing-page numbers could not be fetched first-hand (geo-redirect). The v3 price conflicts between eesel ($0.08/1K) and AA ($100/1M).
- I found no published OpenRouter per-character price for the ElevenLabs models.
- Murf and LMNT free-tier details were not verified.
- No source says how Russian text length (Cyrillic chars per minute of speech) compares with English for billing. Russian words are longer on average, so per-hour costs might be slightly higher (unverified).

---

## 8. Licensing, commercial use, cloned-voice restrictions, and country availability (Russia)

### Takeaway
Free tiers at ElevenLabs and Cartesia are non-commercial: ElevenLabs needs Starter ($6) or higher, and Cartesia needs Pro ($5) or higher, for a commercial license. Inworld states commercial licensing on all tiers. ElevenLabs officially blocks Russia and Belarus, which is a hard blocker if the user or server is in Russia. I found no explicit Russia policy for Inworld or Cartesia, but as US companies they are presumably bound by US sanctions.

### Cited Findings
**ElevenLabs**
- The free plan has no commercial license. Published free-plan content must be attributed ("elevenlabs.io" or "11.ai" in the title). All paid plans include a commercial license, except for Beta services [SECONDARY summarizing ElevenLabs help center] — [ElevenLabs help: Can I publish…](https://help.elevenlabs.io/hc/en-us/articles/13313564601361); [search summary](https://oakgen.ai/blog/elevenlabs-free-commercial-use)
- **Restricted countries**: "ElevenLabs restricts access from Belarus, Cuba, Iran, North Korea, Russia, Syria, Crimea, and the Donetsk and Luhansk oblasts of Ukraine." Some servers outside these regions may also be blocked if their cloud provider is flagged as associated with them [OFFICIAL, via search snippet of the help article] — [ElevenLabs help center](https://help.elevenlabs.io/hc/en-us/articles/22497891312401-Do-you-restrict-access-to-the-service-and-platform-for-any-specific-countries)
- Users warrant that they are not located in US-sanctioned countries [OFFICIAL snippet] — [ElevenLabs Terms EU](https://elevenlabs.io/terms-of-use-eu)
- Observed during this research: every elevenlabs.io docs, blog, and pricing URL returned a 302 redirect to that country-restriction article for the fetch tool, which shows the geo-gating is actively enforced at the web layer. I don't know where the fetch tool is located [direct observation].
- Third parties advertise workarounds for paying ElevenLabs "even if your bank card doesn't work" [SECONDARY, not endorsed] — [EverTry](https://evertry.co/blog/how-to-pay-for-elevenlabs-subscription-even-if-your-bank-card-doesnt-work/)

**Cartesia**
- The commercial use license is included from Pro ($5/mo) upward, and the Free tier does not list it [OFFICIAL] — [Cartesia pricing](https://cartesia.ai/pricing)

**Inworld**
- "Commercial licensing is available across all tiers" [OFFICIAL] — [Inworld pricing](https://inworld.ai/pricing)
- Zero data retention is available [OFFICIAL] — [Inworld TTS models](https://docs.inworld.ai/tts/tts-models)

**Hume**
- All data is deleted after 2026-11-13, so building on Hume now would be wasted effort [OFFICIAL] — [Hume changelog](https://dev.hume.ai/changelog)

**PlayHT**
- PlayHT deleted user data, including voice clones, at shutdown with no export [SECONDARY] — [TextToLab](https://texttolab.com/blog/play-ht-shutdown-alternatives)

**Voice cloning and consent**
- ElevenLabs requires ownership of or explicit consent for the cloned voice and prohibits cloning public figures. At least 12 US states have voice-cloning laws (e.g. Tennessee's ELVIS Act) [SECONDARY] — [licenseorg](https://licenseorg.com/blog/ai-voice-licensing-elevenlabs)

**General sanctions context**
- US AI vendors (OpenAI, Anthropic, Google) geo-fence Russia under US sanctions and export controls [SECONDARY] — [Voidly, 2026-06-24](https://voidly.ai/atlas/findings/ai-blocked-two-ways-vendors-and-states-2026-06)

### Inferences
- A personal, non-monetized mod is arguably "non-commercial", but free-tier ElevenLabs output must carry attribution and Free Cartesia output has no commercial license. The cheapest paid tiers ($5–6/mo) remove that ambiguity.
- If the user or the AI server is in Russia, ElevenLabs direct access is blocked by policy, and the same likely applies to Inworld and Cartesia under US sanctions (unverified per vendor). Payment with Russian-issued cards would fail regardless. Routing via OpenRouter does not obviously solve this, since OpenRouter is also a US company with its own terms (unverified), and it loses cloned or designed voices anyway.
- Hosting the AI server on a non-Russian VPS or the dev-ubuntu box, if that is outside Russia, would satisfy the technical side. Terms-of-service compliance remains the user's call.

### Gaps
- Inworld's and Cartesia's terms on sanctioned countries (Russia) were not found or fetched.
- OpenRouter's country policy and its pass-through of ElevenLabs ToS were not checked.
- Licensing terms for designed (non-cloned) voices, i.e. who owns a Voice-Design-generated voice at ElevenLabs or Inworld, were not found.
- The ElevenLabs Terms clause on use in video games or mods was not specifically verified. "Apps" are listed among permitted commercial uses in secondary summaries — [search summary](https://oakgen.ai/blog/elevenlabs-free-commercial-use).
