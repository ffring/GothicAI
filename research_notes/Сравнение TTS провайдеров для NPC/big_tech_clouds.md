# Big-tech / major-lab cloud TTS for Russian NPC voicing (Gothic 2 mod): state as of 2026-10-11

Scope: OpenAI, Google (Gemini 3.8 TTS gaps only + Cloud TTS Chirp 3 HD / Instant Custom Voice), Microsoft (MAI-Voice 2.x, Azure Speech), Amazon (Polly, Nova Sonic), xAI (Grok TTS), Mistral (Voxtral TTS), Deepgram (Aura-2, Flux TTS). Baseline: Gemini 3.8 Flash TTS with Voice Design (see `research_notes/gemini_3_8_tts_custom_voice.md`).

Evidence tags used below:
- **[official]**: vendor docs / pricing page / vendor blog fetched this session.
- **[vendor-claim]**: performance or quality number stated by the vendor, not independently checked.
- **[independent]**: measurement by a third party (developer blog, forum, open-source PR).
- **[secondary]**: news / aggregator / SEO blog; treat with caution.
- **[inference]**: my own calculation or conclusion.
- Items dated before mid-2026 are flagged **(possibly outdated)**.

---

## 1. Current models, IDs and release dates

### Takeaway
Every provider in scope has shipped or refreshed TTS in 2026, but only four have Russian-capable TTS: Google (Gemini 3.8 TTS, Chirp 3 HD), OpenAI (gpt-4o-mini-tts), Microsoft (MAI-Voice-2.1 / 2.1-Flash and older Azure neural voices) and, per secondary sources only, xAI Grok TTS. Mistral Voxtral TTS, Deepgram Aura-2/Flux TTS, Amazon Polly generative and Nova Sonic have no Russian.

### Cited Findings
- **OpenAI.** TTS models: `gpt-4o-mini-tts` (recommended, "newest and most reliable"), `tts-1` (lower latency), `tts-1-hd` (higher quality) [official] — [OpenAI TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech). The current pricing page lists no new 2026 TTS-only model; 2026 audio models are speech-to-speech: `gpt-realtime-2`, `gpt-realtime-mini`, `gpt-audio`, `gpt-audio-mini` [official] — [OpenAI pricing](https://developers.openai.com/api/docs/pricing).
- **OpenAI.** `gpt-realtime-2` launched 2026-05-08 in the Realtime API, alongside `gpt-realtime-translate` and `gpt-realtime-whisper` [secondary] — [buildfastwithai](https://buildfastwithai.com/blogs/openai-gpt-realtime-2-voice-ai-models), [9to5Mac](https://9to5mac.com/2026/05/07/openai-has-new-voice-models-that-reason-translate-and-transcribe-as-you-speak/). The current `gpt-4o-mini-tts` snapshot is reportedly `2025-12-15` [secondary] **(possibly outdated)** — [CometAPI](https://www.cometapi.com/can-chatgpt-do-text-to-speech-the-latest-2026-guide-to-voice-tts-models/).
- **OpenAI.** A `gpt-live-1` model ("GPT-Live", English accents only) is named in the custom-voices guide [official] — [OpenAI custom voices](https://developers.openai.com/api/docs/guides/custom-voices.md). It is priced at $0.05 per voice-session minute [secondary] — [LATENT](https://latent.tokyo/en/articles/2026-09-30-gemini-3-8-flash-tts-gpt-live). It is a conversational speech-to-speech model, not a TTS endpoint.
- **Google.** `gemini-3.8-flash-tts` and `gemini-3.8-flash-lite-tts`: GA since 2026-09-23 (baseline; already documented in the Gemini note). The GetStream integration PR (2026-09-23/24) uses model ID `gemini-3.8-flash-tts` via `streamGenerateContent?alt=sse` [independent] — [GetStream Vision-Agents PR #663](https://github.com/GetStream/Vision-Agents/pull/663).
- **Google Cloud TTS.** Chirp 3: HD voices and Chirp 3: Instant Custom Voice are current Cloud TTS products [official] — [Chirp 3 Instant Custom Voice docs](https://docs.cloud.google.com/text-to-speech/docs/chirp3-instant-custom-voice).
- **Microsoft.** `MAI-Voice-2.1` and `MAI-Voice-2.1-Flash` are served through the Azure Speech API/SDK. They are in **public preview** ("not recommended for production workloads", no SLA). The docs page is dated 2026-09-30 and was updated 2026-10-01 [official] — [MS Learn: MAI-Voice-2.1](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-voices).
- **Microsoft.** MAI-Voice-2 was introduced at Build 2026 with zero-shot cloning across 15 languages [secondary] — [FreeReadText](https://freereadtext.com/en/news/microsoft-mai-voice-2-expressive-multilingual-tts), [pasqualepillitteri.it](https://pasqualepillitteri.it/en/news/6091/mai-voice-2-how-to-try-microsoft). MAI-Voice-2.1 was released 2026-10-01 [secondary] — [DataLearner](https://www.datalearner.com/ai-models/pretrained-models/mai-voice-2-1).
- **Microsoft.** OpenRouter lists `microsoft/mai-voice-2`, `microsoft/mai-voice-2.1` and `microsoft/mai-voice-2.1-flash` [secondary/listing] — [OpenRouter MAI-Voice-2.1-Flash](https://openrouter.ai/microsoft/mai-voice-2.1-flash), [OpenRouter MAI-Voice-2.1](https://openrouter.ai/microsoft/mai-voice-2.1).
- **Amazon.** Polly engines: Standard, Neural, Long-Form, Generative [official] — [Polly pricing](https://aws.amazon.com/polly/pricing/). Nova 2 Sonic is a speech-to-speech model, not plain TTS [official blog, seen as search snippet only] — [AWS blog: Nova 2 Sonic](https://aws.amazon.com/blogs/aws/introducing-amazon-nova-2-sonic-next-generation-speech-to-speech-model-for-conversational-ai/).
- **xAI.** Standalone Grok STT and TTS APIs were announced 2026-04-17 [official] — [x.ai news](https://x.ai/news/grok-stt-and-tts-apis). OpenRouter lists the model as `x-ai/grok-voice-tts-1.0` [listing] — [OpenRouter Grok Voice TTS 1.0](https://openrouter.ai/x-ai/grok-voice-tts-1.0). Custom Voices were announced 2026-04-30 [official] — [x.ai news: Custom Voices](https://x.ai/news/grok-custom-voices).
- **Mistral.** Voxtral TTS was released 2026-03-23 with model ID `voxtral-mini-tts-latest` [official] **(pre-mid-2026; check for updates)** — [Mistral: Speaking of Voxtral](https://mistral.ai/news/voxtral-tts/).
- **Deepgram.** Current TTS models: Flux TTS (recommended, `/v2/speak`), Aura-2 (`/v1/speak`) and Aura (first generation) [official] — [Deepgram TTS models & languages](https://developers.deepgram.com/docs/tts-models-languages-overview).

### Inferences
- For a TTS-only adapter (LLM text in, audio out), the relevant 2026 TTS endpoints are: Gemini 3.8 Flash/Flash-Lite TTS, OpenAI `gpt-4o-mini-tts`, MAI-Voice-2.1(-Flash), Chirp 3 HD and Grok TTS. The OpenAI and Amazon realtime/S2S models (gpt-realtime-2, GPT-Live, Nova Sonic) belong to a different architecture and are much more expensive per token.
- Microsoft's best Russian option (MAI-Voice-2.1) is preview-only. For a hobby mod this is acceptable, but the API can change.

### Gaps
- No official release date found for the current `gpt-4o-mini-tts` snapshot. The 2025-12-15 snapshot claim is secondary.
- The official TTS model ID for Grok in xAI's own docs is not stated; only OpenRouter's `grok-voice-tts-1.0` slug was found.

---

## 2. Russian support, quality evidence, number of Russian voices

### Takeaway
Russian is officially supported by Gemini 3.8 TTS, Chirp 3 HD / Instant Custom Voice, OpenAI `gpt-4o-mini-tts` (Whisper language coverage, English-optimised voices) and Microsoft (4 MAI-Voice ru-RU voices, 2 of them with emotional styles, plus 3 legacy neural voices). Grok TTS lists Russian only in secondary sources. Mistral, Deepgram, Polly generative/neural and Nova Sonic have no Russian. No independent Russian-specific quality benchmark comparing these providers was found.

### Cited Findings
- **OpenAI.** Supported languages "generally follow the Whisper model", which includes Russian, but "voices are optimized for English". There are 13 built-in voices: alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse, marin, cedar. `marin` and `cedar` are recommended for best quality. `tts-1`/`tts-1-hd` support 9 of them [official] — [OpenAI TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech).
- **Microsoft MAI-Voice-2.1 / 2.1-Flash** cover 23 languages including Russian [official] — [MS Learn: MAI-Voice-2.1](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-voices). There are 4 ru-RU managed voices [official, same page]:
  - `ru-RU-Grant` (M): styles `agent`, `customer_call_center`, `neutral`;
  - `ru-RU-Harper` (F): `audiobook`, `educational`, `narrator`, `neutral`;
  - `ru-RU-Lev` (M) and `ru-RU-Masha` (F): `adventurous`, `caringempathy`, `curious`, `encouraging`, `excited`, `friendlycheerful`, `neutral`, `nostalgic`, `reflective`, `saddisappointed`, `serious`.
  - No Russian voice has the full 19-emotion set (`angry`, `fearful`, `shouting`, `whispering`…) that some en-US/de-DE/es-MX voices have.
- **Azure Speech (classic neural).** ru-RU has `ru-RU-DmitryNeural` (M), `ru-RU-SvetlanaNeural` (F) and `ru-RU-DariyaNeural` (F), with no speaking styles and no ru-RU HD/DragonHD voice [official, but extracted by a summariser from a very long table; verify] — [MS Learn language support (TTS)](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts). These voices are long-standing **(pre-2026)**.
- **Google Cloud.** Chirp 3 Instant Custom Voice supports 30+ languages including ru-RU [official] — [Chirp 3 ICV docs](https://docs.cloud.google.com/text-to-speech/docs/chirp3-instant-custom-voice). A search snippet states Chirp 3 HD covers ru-RU [secondary] — [search result pointing to Google pricing/docs](https://cloud.google.com/text-to-speech/pricing?hl=de).
- **Google Gemini 3.8 TTS.** Russian supported (130+ languages Flash, 100+ Flash-Lite), per the baseline note and [GetStream PR](https://github.com/GetStream/Vision-Agents/pull/663) ("130+ languages documented") [independent].
  - A Voice Arena per-language Elo comparison of Gemini 3.8 Flash vs Flash-Lite covered 7 languages (Japanese, Arabic, Hindi, Spanish, English, pt-BR, Vietnamese), **not Russian**. Flash led in ja/ar/hi/es and Lite led in en/pt-BR/vi [secondary] — [LATENT](https://latent.tokyo/en/articles/2026-09-30-gemini-3-8-flash-tts-gpt-live).
- **xAI Grok TTS.** 5 voices: Ara, Eve (default), Leo, Rex, Sal [official for "eve default"; voice list secondary] — [docs.x.ai Voice overview](https://docs.x.ai/developers/model-capabilities/audio/voice), [aipedia](https://www.aipedia.wiki/news/2026-03-16-xai-grok-tts-api-launch/).
  - 20 languages including Russian, with auto-detection [secondary: Replicate/Cloudflare/Segmind listings] — [Replicate grok-text-to-speech](https://replicate.com/xai/grok-text-to-speech), [Cloudflare grok-tts](https://developers.cloudflare.com/ai/models/xai/grok-tts/).
  - xAI's own Custom Voices announcement lists only en/es/fr/de/zh/ja [official] — [x.ai Custom Voices](https://x.ai/news/grok-custom-voices).
  - An "80+ prebuilt voices across 28 languages" voice library launched with Grok 4.3 [secondary] — [buildfastwithai: xAI voice cloning](https://www.buildfastwithai.com/blogs/xai-voice-cloning-api-tutorial-2026).
- **Mistral Voxtral TTS.** 9 languages (en, fr, de, es, nl, pt, it, hi, ar); Russian is **not** included [official] — [Mistral](https://mistral.ai/news/voxtral-tts/).
- **Deepgram.** Flux TTS is English only. Aura-2 covers en, es, de, fr, nl, it, ja. **No Russian in any model** [official] — [Deepgram TTS models & languages](https://developers.deepgram.com/docs/tts-models-languages-overview). (A search-engine summary claimed Aura-2 supports Russian; the official page contradicts it.)
- **Amazon Polly.**
  - Generative engine: 43 voices, none Russian [official] — [Polly generative voices](https://docs.aws.amazon.com/polly/latest/dg/generative-voices.html).
  - Russian has only the Standard (non-neural) voices Tatyana and Maxim [official page, search snippet] — [Polly standard voices](https://docs.aws.amazon.com/polly/latest/dg/standard-voices.html).
  - Nova 2 Sonic languages: en, fr, it, de, es, plus pt and hi; no Russian [secondary summary of AWS blog] — [AWS blog: Nova 2 Sonic](https://aws.amazon.com/blogs/aws/introducing-amazon-nova-2-sonic-next-generation-speech-to-speech-model-for-conversational-ai/).
- **Russian quality, general.** A Habr article (2026-09-12) stress-tested ElevenLabs v3, OpenAI `tts-1` and `tts-1-hd` on a 289-character Russian text with 7 planted problems. Typical failures were stress on homographs (за́мок/замо́к), case agreement of numerals ("к 15 марта"), missing ё, mixed Cyrillic/Latin abbreviations and Roman numerals. It frames Yandex SpeechKit as handling Russian reliably [secondary/native review] — [Habr (bothub)](https://habr.com/ru/companies/bothub/articles/1081544/).
- A Russian consumer roundup calls ElevenLabs v3 the market leader but notes Russian stress errors and a slight "accent" on intonation [secondary] — [Afisha Daily](https://daily.afisha.ru/poleznye-stati/36440-ozvuchka-teksta-neyrosetyu-luchshie-ii-servisy-2026-goda-i-besplatnye-alternativy/).
- A Nov 2025 English test found `gpt-4o-mini-tts` "slightly robotic" with looser text interpretation than `tts-1` [independent] **(possibly outdated)** — [s-anand.net](https://www.s-anand.net/blog/openai-tts-cost/).
- Artificial Analysis lists Chirp 3: HD at Quality Elo ≈1059 (rank 28 of 100), with the top model (Eleven v4 Turbo) at Elo 1327. This is an English-centric arena [independent] — [AA Chirp 3 HD](https://artificialanalysis.ai/text-to-speech/models/chirp3-hd), [AA TTS](https://artificialanalysis.ai/text-to-speech).

### Inferences
- For a deep, commanding old-man voice (Xardas), Microsoft's ru-RU catalogue is thin: 2 male MAI voices (Grant, Lev), and the styles are call-center/narration oriented (`serious`, `reflective`), with no anger, menace or whisper. Gemini Voice Design (free-form description) and OpenAI's `instructions` steering are structurally better suited to many distinct NPC voices. OpenAI's voices are English-optimised, so a Russian accent or prosody risk remains.
- All neural TTS share the Russian stress, homograph and numeral problems. Regardless of provider, the NPC pipeline should pre-normalise text: numbers to words, ё restoration, and possibly stress marks (`+` or combining acute, if the provider honours them). This needs a per-provider test.
- No independent Russian MOS or Elo exists for Gemini 3.8, MAI-Voice-2.1, gpt-4o-mini-tts or Grok. A small blind A/B test of the project's own NPC lines is the only reliable way to rank them.

### Gaps
- No independent Russian-language benchmark (MOS, WER, arena Elo) for Gemini 3.8 TTS, MAI-Voice-2.1, gpt-4o-mini-tts or Grok TTS was found.
- Whether Azure multilingual or HD voices (e.g. `*MultilingualNeural`, DragonHD) can speak Russian as a secondary locale was not verified. The summariser said "none listed", which may be wrong.
- Whether Chirp 3 HD has ru-RU prebuilt voices, and how many, was not confirmed from the official voice list (only a search snippet).
- Grok TTS Russian support is not confirmed by xAI's own docs.

---

## 3. Custom voices: voice design, cloning, voice libraries

### Takeaway
Only Google offers **voice design from a text prompt** without consent gating (Gemini 3.8 Voice Design). Every big-tech **cloning** product is gated:
- OpenAI: sales-approved, max 20 voices per org, consent recording (Russian consent phrase supported);
- Microsoft: Limited Access application, consent audio;
- Google Chirp ICV: allowlist via sales;
- xAI: API cloning is Enterprise-only and US-only (except Illinois).

None of them permits cloning the original Russian Gothic voice actors without the actors' own recorded consent.

### Cited Findings
- **OpenAI custom voices** [official] — [OpenAI custom voices](https://developers.openai.com/api/docs/guides/custom-voices.md):
  - "limited to eligible customers. Contact our sales team".
  - Works with `gpt-4o-mini-tts`, `gpt-realtime-2`, Chat Completions audio and `gpt-live-1` (English accents only).
  - Sample ≤30 s. Consent recording must read an exact phrase. Consent languages include **ru** and uk.
  - "At most 20 voices can be created per organization".
  - Endpoints: `POST /v1/audio/voice_consents`, `POST /v1/audio/voices`.
  - Pricing not disclosed.
- **OpenAI** has no voice-design-from-prompt endpoint. Steering is through `instructions` (accent, emotional range, intonation, impressions, speed, tone, whispering) on top of the 13 built-in voices [official] — [OpenAI TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech).
- **Microsoft MAI-Voice-2.1 instant voice cloning** [official] — [MS Learn: MAI-Voice-2.1](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-voices):
  - 5–60 s reference clip, no training.
  - "Only authorized, licensed voices can be synthesized… No unlicensed voice cloning is possible."
  - Access requires applying through the Custom Neural Voice / Custom Avatar Limited Access Review (aka.ms/customneural), then uploading consent audio plus the prompt.
  - The voice is used via `<mstts:ttsembedding speakerProfileId=...>`.
  - The prebuilt "voice library" consists of licensed curated voices.
- **Azure personal voice** pricing structure: voice creation free, synthesis per 1M chars, profile storage per 1,000 profiles/month; actual numbers are hidden ("$-") on the public page [official] — [Azure Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/).
- **Google Chirp 3 Instant Custom Voice** [official] — [Chirp 3 ICV docs](https://docs.cloud.google.com/text-to-speech/docs/chirp3-instant-custom-voice):
  - "restricted to allow-listed users… contact a member of the sales team".
  - Reference audio ≤10 s mono. A mandatory fixed consent script (≤10 s) must be used.
  - Supports ru-RU and streaming.
  - Regions: global, us, eu, asia-southeast1, asia-northeast1, europe-west2.
- **Google Gemini 3.8**: Voice Design (`type: "prompted"`) needs no consent. Voice Replication needs a 10–30 s source plus a consent recording. Up to 200 stored voices per project. Details in the baseline note [official, previously verified].
- **xAI Custom Voices**:
  - Console: free tier allows up to 30 custom voices per team.
  - **API is gated to Enterprise plan**.
  - "Currently only available in the United States, with the exception of Illinois".
  - Reference audio ≤120 s (90+ s recommended).
  - [official] — [docs.x.ai Custom Voices](https://docs.x.ai/developers/model-capabilities/audio/custom-voices).
  - The announcement describes about 1 minute of speech and a two-stage verification (passphrase check plus speaker-embedding comparison), with no extra charge to use custom voices in TTS [official] — [x.ai Custom Voices](https://x.ai/news/grok-custom-voices).
- **Mistral Voxtral TTS**: zero-shot cloning from as little as 3 s of reference audio, with cross-lingual adaptation [official] — [Mistral](https://mistral.ai/news/voxtral-tts/). This is irrelevant here because Voxtral has no Russian.
- **Deepgram**: voice cloning is on the Flux TTS roadmap, not shipped [secondary summary of Deepgram product page] — [Deepgram Flux TTS](https://deepgram.com/product/text-to-speech/flux).
- **Amazon Polly**: no customer voice cloning or design product was found in the fetched pages [official pages fetched: pricing, generative voices].

### Inferences
- For NPC voices without real-person likeness, the decisive capability is **prompt-based voice design**, and among big-tech clouds only Gemini 3.8 has it.
- The alternatives need different setups:
  - OpenAI: pick one of 13 voices and steer each NPC with a persona `instructions` string. This needs no approval but gives limited timbre variety.
  - Microsoft: pick one of 4 ru-RU voices plus SSML style.
  - Chirp 3 HD: pick a prebuilt voice.
- Cloning with a hired or volunteer voice actor (with consent) is technically possible on OpenAI, Microsoft and Google Chirp ICV, but all three require a sales or limited-access approval that a hobby project is unlikely to get. xAI cloning via console is free but US-only.

### Gaps
- Number of Gemini prebuilt or library voices usable for ru-RU was not checked (the baseline note says 30 base voices plus a 2000+ library).
- Pricing of OpenAI custom voices and Azure personal voice synthesis is not public.

---

## 4. Expressiveness: emotion/style control, multi-speaker

### Takeaway
Natural-language steering is available on Gemini (`style` / prompt) and OpenAI (`instructions`). Microsoft uses SSML `mstts:express-as` with a fixed style list per voice; Russian MAI voices have only 3–11 styles and no anger or whisper. Grok uses inline tags (`[laugh]`, `[sigh]`, `<whisper>`). Classic Azure ru-RU neural voices have no styles.

### Cited Findings
- **OpenAI** `instructions` control "accent, emotional range, intonation, impressions, speed, tone, and whispering" [official] — [OpenAI TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech). Instructing slower speech increases billed output tokens [community] — [OpenAI forum](https://community.openai.com/t/gpt-4o-mini-tts-output-cost-has-exceeded-what-i-calculated/1312195).
- **Microsoft MAI-Voice-2.1**: "fine-grained emotion control via SSML" using `mstts:express-as style=...` (`joy`, `excitement`, `empathy`…). The style set is per voice; the ru-RU sets are listed in section 2 [official] — [MS Learn: MAI-Voice-2.1](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-voices).
- **Azure classic ru-RU neural voices**: no style or role support [official, summariser extraction] — [MS Learn language support](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts).
- **xAI Grok TTS**: inline tags `[laugh]`, `[sigh]`, `[whisper]`, plus emphasis, slow and pause tags [official] — [x.ai news](https://x.ai/news/grok-stt-and-tts-apis). Wrapping tags like `<whisper>…</whisper>` and `[pause]` are also reported [secondary] — [Cloudflare grok-tts](https://developers.cloudflare.com/ai/models/xai/grok-tts/).
- **Gemini 3.8**: per-request `style` (OpenRouter maps `instructions` into `speech_metadata.style`). Multi-speaker supports up to 2 speakers with prebuilt voices only [official, baseline note].
- **Mistral Voxtral**: claims parity with ElevenLabs v3 on emotion steering in human evals [vendor-claim] — [Mistral](https://mistral.ai/news/voxtral-tts/).
- **Amazon Polly generative**: "emotionally engaged", with partial SSML support. No newscaster style. Speech marks unsupported [official] — [Polly generative voices](https://docs.aws.amazon.com/polly/latest/dg/generative-voices.html).

### Inferences
- For per-line NPC emotion (angry guard, frightened peasant), Gemini `style` and OpenAI `instructions` map directly onto LLM-generated stage directions. With Microsoft, the server would have to map emotions onto the ru-RU style list, which lacks `angry`, `fearful` and `whispering`.
- Multi-speaker is not needed: each NPC line is a separate request.

### Gaps
- Whether OpenAI `instructions` work as well in Russian as in English is undocumented.
- Whether Grok tags work in Russian is undocumented.

---

## 5. Streaming and latency (time to first audio)

### Takeaway
- Independent measurements exist mainly for Gemini 3.8 Flash TTS: about **0.9–1.05 s to first audio** with SSE streaming in one integration, about **3.8 s total** for a short non-streamed answer, and OpenRouter P50 of 3.47 s (Flash) and 2.66 s (Flash-Lite).
- Earlier Gemini TTS previews showed "fake" streaming (full generation, then a burst of chunks) and large per-key variance.
- Microsoft (≈150 ms), Mistral (70 ms) and xAI ("sub-second") latency figures are vendor claims only.

### Cited Findings
- **Gemini 3.8 Flash TTS** [independent] — [GetStream PR #663](https://github.com/GetStream/Vision-Agents/pull/663):
  - First audio arrives in **0.9–1.05 s**; a 4-second sentence is fully generated in ~2.5 s.
  - Transport is SSE (`streamGenerateContent?alt=sse`) returning headerless 16-bit PCM at 24 kHz.
  - No text-streaming input: the whole transcript is buffered per request.
  - The PR also notes audio deltas are effectively released when generation completes.
- **Gemini 3.8 Flash and Flash-Lite, non-streaming** [independent] — [mxchat](https://mxchat.ai/gemini-tts-pricing-tokens-per-second/) (2026-09-24): "0.26 to 0.42 seconds of waiting per second of audio: about 3.8 seconds for the short answer, 8 to 9 for the medium one, 14 to 16 for the long one".
- **OpenRouter P50 latency (week-long global aggregate, via AI Studio)**: Gemini 3.8 Flash TTS 3.47 s, Flash-Lite TTS 2.66 s [independent/aggregator] — [LATENT](https://latent.tokyo/en/articles/2026-09-30-gemini-3-8-flash-tts-gpt-live). The article warns these numbers are not comparable across models.
- **Gemini 3.1 Flash TTS preview** (July–Aug 2026) [community] — [Google AI forum](https://discuss.ai.google.dev/t/3-1-flash-tts-preview-streaming-latency/176050):
  - Streaming behaved as "one long silent wait (~15s), then a burst of audio chunks".
  - Latency with a personal API key varied from sub-2 s to 10–20 s; the AI Studio default key was consistently sub-2 s.
  - The Interactions API was "significantly slower" than generateContent streaming.
  - No Google staff reply.
- **OpenAI**: streaming via HTTP chunked transfer encoding. Formats: mp3 (default), opus, aac, flac, wav, pcm. `wav`/`pcm` are recommended for lowest latency [official] — [OpenAI TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech). One non-streamed job of 4,096 chars (268 s of audio) took 44 s total [independent] **(Nov 2025, possibly outdated)** — [s-anand.net](https://www.s-anand.net/blog/openai-tts-cost/).
- **Microsoft MAI-Voice-2.1-Flash**: "ultra-fast low-latency" [official] — [MS Learn](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-voices). A figure of ~150 ms end-to-end latency was reported [secondary/vendor-claim] — [MindStudio](https://www.mindstudio.ai/blog/mai-voice-2-1-pricing-flash). MAI-Voice-2.1 (non-Flash) "prioritizes naturalness and expressivity over latency-critical scenarios" [official].
- **xAI**: WebSocket streaming TTS (no text length limit, audio starts before full input is processed); REST is limited to 15,000 chars per request [official + secondary] — [x.ai news](https://x.ai/news/grok-stt-and-tts-apis), [MarkTechPost](https://www.marktechpost.com/2026/04/18/xai-launches-standalone-grok-speech-to-text-and-text-to-speech-apis-targeting-enterprise-voice-developers/). Docs claim "sub-second latency" [vendor-claim] — [docs.x.ai Voice](https://docs.x.ai/developers/model-capabilities/audio/voice).
- **Mistral Voxtral**: ~70 ms for a 10 s voice sample plus 500 chars; RTF ~9.7x [vendor-claim] — [Mistral](https://mistral.ai/news/voxtral-tts/).
- **Amazon Polly generative** offers a bidirectional streaming API (`StartSpeechSynthesisStream`) in selected regions [official] — [Polly generative voices](https://docs.aws.amazon.com/polly/latest/dg/generative-voices.html). Russian voices are Standard only, so this does not help here.
- **Artificial Analysis**: Polly Standard is the fastest model at 1,306.5 characters/s [independent] — [AA TTS](https://artificialanalysis.ai/text-to-speech).

### Inferences
- For 1–3-sentence NPC replies (~5–15 s of audio), the baseline Gemini Flash TTS gives an expected wait of ~1 s to first audio when streaming works properly. Non-streaming, or when chunks are buffered, the wait is ~2–6 s. That is acceptable for a game dialogue box with a "thinking" animation, but not snappy.
- If latency becomes the bottleneck, the most promising big-tech alternatives are MAI-Voice-2.1-Flash (purpose-built for low latency, Russian supported) and Grok TTS over WebSocket (if Russian holds up). Both need an independent TTFA test.
- The server should measure TTFA per provider in its adapter, since forum evidence shows large per-key and per-route variance for Gemini.

### Gaps
- No independent TTFA measurement found for gpt-4o-mini-tts (2026), MAI-Voice-2.1(-Flash), Chirp 3 HD, or Grok TTS.
- Artificial Analysis per-model latency values were not retrievable (page tables truncated).

---

## 6. Python async SDK availability

### Takeaway
OpenAI and Google have first-party Python SDKs usable from asyncio. Azure's Speech SDK is callback/future-based (`speak_ssml_async(...).get()` blocks), so an asyncio server should call the REST endpoint with httpx/aiohttp or run the SDK in a thread. xAI TTS streams over WebSocket, and a Python SDK for TTS is not documented.

### Cited Findings
- **Azure Speech SDK for Python**: the documented example uses `synthesizer.speak_ssml_async(ssml).get()`, which blocks on a future. A plain REST alternative exists: `POST https://{region}.tts.speech.microsoft.com/cognitiveservices/v1` with an SSML body and `X-Microsoft-OutputFormat` [official] — [MS Learn: MAI-Voice-2.1](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-voices).
- **Gemini**: the Python SDK `google-genai` ≥2.25.0 is required for `client.voices.create` [official, baseline note].
- **Google Cloud Chirp 3 ICV**: Python client library examples cover both standard and streaming synthesis [official] — [Chirp 3 ICV docs](https://docs.cloud.google.com/text-to-speech/docs/chirp3-instant-custom-voice).
- **xAI**: docs describe unary requests and WebSocket streaming. No Python TTS SDK is mentioned [official] — [docs.x.ai Voice](https://docs.x.ai/developers/model-capabilities/audio/voice).
- **OpenAI**: `/v1/audio/speech` supports chunked streaming [official] — [OpenAI TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech).

### Inferences
- The `openai` Python package's `AsyncOpenAI` client and `google-genai`'s `client.aio` async surface are standard, but they were not re-verified in this pass. The OpenAI-compatible `/audio/speech` shape also lets one adapter cover OpenAI and OpenRouter (which proxies Gemini, MAI-Voice and Grok TTS).
- For Azure/MAI, a ~30-line httpx adapter posting SSML is simpler than wrapping the C++-backed Speech SDK in asyncio.

### Gaps
- Async support in xAI's Python SDK for TTS, and `aioboto3` for Polly, were not checked. Both are low priority given the language coverage.

---

## 7. Pricing, normalized to $/1M characters and $/hour, vs the Gemini 3.8 baseline

### Takeaway
At hobby scale all Russian-capable options cost cents to a few dollars per thousand NPC lines.
- **Gemini 3.8 Flash TTS (2026 promo)** ≈ **$1.04/hour of audio ≈ $21 per 1M Russian characters**, using the measured ~32 audio tokens/s rather than the documented 25. It doubles on 2027-01-01 to ≈$2.07/h, ≈$41/1M chars.
- **Flash-Lite** ≈ $0.69/h (2026) → $1.38/h (2027).
- **Cheaper than the 2026 Gemini Flash baseline:**
  - MAI-Voice-2.1-Flash and Azure neural: $15/1M chars ≈ $0.76/h;
  - OpenAI gpt-4o-mini-tts ≈ $0.87–0.90/h;
  - Grok TTS: $15/1M chars officially; a $4.20 launch price is reported only by secondary sources.
- **More expensive than the baseline:** Chirp 3 HD ($30/1M chars ≈ $1.51/h), Instant Custom Voice ($60 ≈ $3.02/h), MAI-Voice-2.1 ($22 ≈ $1.11/h, about equal).
- **Polly Standard** ($4) is the cheapest Russian option but is legacy-quality.

### Cited Findings
- **Gemini audio token rate**:
  - Google's docs state 25 tokens per second of audio [official] — [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).
  - A forum user observed 32 tokens/s billed on Flash 3.8 TTS, with no staff reply (2026-09-24) [community] — [Google AI forum](https://discuss.ai.google.dev/t/how-many-tokens-are-actually-used-per-second-for-flash-3-8-tts/184714).
  - An independent test billed 25.0 tok/s for Gemini 2.5 Flash TTS, 32.0 for 3.1 Flash TTS, and 31.9 for both 3.8 Flash TTS and 3.8 Flash-Lite TTS (12 calls each) [independent] — [mxchat](https://mxchat.ai/gemini-tts-pricing-tokens-per-second/).
  - The GetStream PR notes "about 39 tokens per second of speech" [independent] — [GetStream PR #663](https://github.com/GetStream/Vision-Agents/pull/663).
  - **Conflict:** the official figure is 25 tok/s, independent measurements are 32–39 tok/s.
- **Gemini cost per minute (mxchat)** [independent] — [mxchat](https://mxchat.ai/gemini-tts-pricing-tokens-per-second/):
  - 3.8 Flash TTS: 1.72¢ (to 2026-12-31), 3.44¢ from 2027-01-01.
  - 3.8 Flash-Lite: 1.15¢, then 2.30¢.
  - 2.5 Flash TTS: 1.50¢.
  - 3.1 Flash TTS: 3.84¢.
- **OpenAI** [official] — [OpenAI pricing](https://developers.openai.com/api/docs/pricing):
  - `gpt-4o-mini-tts`: text in $0.60/1M tokens, audio out $12/1M tokens.
  - `tts-1`: $15/1M chars. `tts-1-hd`: $30/1M chars.
  - Audio output: gpt-realtime and gpt-realtime-2 $64/1M tokens; gpt-realtime-mini and gpt-audio-mini $20/1M tokens.
- **OpenAI per-minute cost**: OpenAI's estimate for gpt-4o-mini-tts is ~$0.015/min [community, citing official docs] — [OpenAI forum](https://community.openai.com/t/gpt-4o-mini-tts-output-cost-has-exceeded-what-i-calculated/1312195) **(2025, possibly outdated)**. Measured: 4,096 chars produced 268 s of audio, about 6.15 audio tokens per input token, ≈1.46¢/min, ≈$0.876/h, ≈$15.9 per 1M (English) chars [independent] — [s-anand.net](https://www.s-anand.net/blog/openai-tts-cost/) **(Nov 2025)**. That implies about 20 audio tokens/s [inference from those numbers].
- **Microsoft**:
  - MAI-Voice-2.1: $22/1M chars. MAI-Voice-2.1-Flash: $15/1M chars. Both "introductory through the end of 2026" [secondary] — [MindStudio](https://www.mindstudio.ai/blog/mai-voice-2-1-pricing-flash), [DataLearner](https://www.datalearner.com/ai-models/pretrained-models/mai-voice-2-1).
  - The official Azure pricing page renders prices as "$-" placeholders [official] — [Azure Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/).
  - Azure neural voices ≈$15/1M chars [secondary] — [speechactors](https://speechactors.com/article/?p=3863).
  - Free tier F0: 0.5M neural characters per month [official] — [Azure Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/).
- **Google Cloud**: Chirp 3 HD $30/1M chars, Instant Custom Voice $60/1M chars [secondary search summary of official pricing page; official page could not be rendered] — [Cloud TTS pricing](https://cloud.google.com/text-to-speech/pricing?hl=de).
- **Amazon Polly** [official] — [Polly pricing](https://aws.amazon.com/polly/pricing/):
  - Standard $4, Neural $16, Long-Form $100, Generative $30 per 1M chars.
  - Free tier: Standard 5M chars/month; Neural 1M; Long-Form 0.5M; Generative 0.1M (12 months). New customers since 2025-07-15 get up to $200 credits.
- **xAI** TTS: "$15.00 / 1M chars" [official] — [docs.x.ai models/pricing](https://docs.x.ai/developers/models), [x.ai news](https://x.ai/news/grok-stt-and-tts-apis). Several secondary sources quote **$4.20/1M chars** (described as launch pricing) [secondary] — [buildfastwithai](https://www.buildfastwithai.com/blogs/xai-voice-cloning-api-tutorial-2026), [KuCoin news](https://www.kucoin.com/news/flash/xai-launches-grok-stt-and-tts-apis-with-competitive-pricing-and-multilingual-support). **Conflict**: treat $15 as current. Custom voices carry no extra charge [official] — [x.ai Custom Voices](https://x.ai/news/grok-custom-voices).
- **Mistral Voxtral**: $0.016/1k chars, i.e. $16/1M [official] — [Mistral](https://mistral.ai/news/voxtral-tts/). No Russian.
- **Artificial Analysis**: cheapest TTS is Kokoro 82M at $0.65/1M chars [independent] — [AA TTS](https://artificialanalysis.ai/text-to-speech).

### Inferences
Normalization assumptions [inference]:
- Russian NPC speech ≈ **14 characters/s including spaces** (sensitivity range 12–16), so 1 h ≈ 50,400 chars and 1M chars ≈ 19.8 h (17.4–23.1 h).
- Gemini audio is billed at the measured 32 tok/s, with the official 25 tok/s shown as the low bound. Input-text tokens are negligible: about $0.1–0.2 per 1M Russian chars at $0.50/1M tokens.
- Prices are list prices without free tiers.

| Option (Russian-capable unless noted) | Billing unit | ≈ $/hour of audio | ≈ $/1M Russian chars | vs Gemini 3.8 Flash (2026, 32 tok/s) |
|---|---|---|---|---|
| **Gemini 3.8 Flash TTS, to 2026-12-31** (baseline) | $9/1M audio tok | 1.04 (0.81 at 25 tok/s) | 20.6 (16–24) | 1.00× |
| Gemini 3.8 Flash TTS, from 2027-01-01 | $18/1M audio tok | 2.07 (1.62) | 41 (32–48) | 2.0× |
| Gemini 3.8 Flash-Lite TTS, to 2026-12-31 | $6/1M audio tok | 0.69 (0.54) | 13.7 (11–16) | 0.67× |
| Gemini 3.8 Flash-Lite TTS, from 2027 | $12/1M audio tok | 1.38 (1.08) | 27 (21–32) | 1.33× |
| OpenAI gpt-4o-mini-tts | $12/1M audio tok (~20 tok/s measured) | 0.87–0.90 | ≈17–18 | ≈0.85× |
| OpenAI tts-1 / tts-1-hd | $15 / $30 per 1M chars | 0.76 / 1.51 | 15 / 30 | 0.73× / 1.46× |
| OpenAI gpt-realtime-2 (S2S, not TTS) | $64/1M audio tok | ≈4–5 (if ~20 tok/s) | ≈90 | ≈4–5× |
| MAI-Voice-2.1-Flash (preview, intro price to end-2026) | $15/1M chars | 0.76 | 15 | 0.73× |
| MAI-Voice-2.1 (preview, intro price) | $22/1M chars | 1.11 | 22 | 1.07× |
| Azure neural ru-RU (Dmitry/Svetlana/Dariya) | ≈$15/1M chars (secondary) | 0.76 | 15 | 0.73× |
| Google Cloud Chirp 3 HD | $30/1M chars | 1.51 | 30 | 1.46× |
| Google Chirp 3 Instant Custom Voice (allowlist) | $60/1M chars | 3.02 | 60 | 2.9× |
| Amazon Polly Standard (Tatyana/Maxim) | $4/1M chars | 0.20 | 4 | 0.19× |
| Amazon Polly Generative (**no Russian**) | $30/1M chars | 1.51 | 30 | n/a |
| xAI Grok TTS (Russian per secondary only) | $15/1M chars (official); $4.20 (secondary) | 0.76 (0.21) | 15 (4.2) | 0.73× (0.20×) |
| Mistral Voxtral TTS (**no Russian**) | $16/1M chars | 0.81 | 16 | n/a |
| Deepgram Aura-2 / Flux TTS (**no Russian**) | not checked | — | — | n/a |

- **Hobby-scale estimate:** an NPC reply of ~175 Russian chars is ~12 s of audio. 1,000 replies ≈ 175k chars ≈ **$3.6 on Gemini 3.8 Flash in 2026** (≈$7.2 from 2027), $2.4 on Flash-Lite, ≈$2.6 on MAI-Voice-2.1-Flash or Azure neural, ≈$3 on gpt-4o-mini-tts, ≈$5.3 on Chirp 3 HD. Cost differences between providers are therefore not decisive for a personal mod. Quality of Russian, voice design capability and latency matter more.
- The Gemini price doubling on 2027-01-01 moves Gemini Flash from mid-pack to among the most expensive non-custom options. Flash-Lite after 2027 (~$27/1M) is still cheaper than Chirp 3 HD ($30).
- The MAI-Voice prices are also introductory through end-2026 [secondary]. Post-2026 MAI pricing is unknown.

### Gaps
- Official Azure prices for neural, HD, MAI-Voice and personal voice synthesis could not be read (placeholder rendering). The figures above come from secondary sources.
- The Google Cloud TTS pricing page did not render. The Chirp 3 HD and ICV prices and the free tier come from a search snippet.
- Gemini Free-tier limits for 3.8 TTS (requests/day) were not re-checked.
- Russian characters per second for each specific TTS voice was not measured. The 14 chars/s figure is an assumption; real NPC speech with slow "old wizard" styling will be slower, which raises cost per character for token-billed models (Gemini, OpenAI) but not for per-character billing (Microsoft, Google Cloud, xAI, Polly).

---

## 8. Availability by country (Russia) and terms for game-mod use

### Takeaway
The OpenAI API and the Gemini API do not list Russia as a supported country. Using them from Russia (or offering them to users there) risks account suspension, so the server must run from, and be billed in, a supported country. Microsoft and AWS restrictions for Russia were not verified in this pass. OpenAI requires disclosure that the voice is AI-generated. Microsoft states its MAI library voices are licensed for third-party commercial use. Mistral's open weights are non-commercial, but API use is commercial.

### Cited Findings
- **OpenAI**: the API is supported only in listed countries. Accessing it from unsupported locations "may result in your account being blocked or suspended". Russia is not on the list [official help page, via search summary] — [OpenAI supported countries](https://help.openai.com/en/articles/5347006-openai-api-supported-countries-and-territories).
- **Gemini API / AI Studio**: available only in listed countries, and Russia is not included [official docs, via search summary] — [Gemini available regions snapshot](https://techdevnotes.com/releases/gemini-docs/20260626-140339Z-75a2454c2e49/content/pages/available-regions.txt), [Google AI forum: unsupported country](https://discuss.ai.google.dev/t/unsupported-country-despite-the-clean-ip/73922).
- **OpenAI usage policy for TTS**: "provide a clear disclosure to end users that the TTS voice they are hearing is AI-generated and not a human voice" [official] — [OpenAI TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech).
- **Microsoft MAI-Voice**: "Available for third-party developers. Microsoft holds full licensing rights for commercial use." The models are served globally from 14 regions (EU: francecentral, westeurope, northeurope, swedencentral). Preview features fall under the Supplemental Terms of Use for Azure Previews [official] — [MS Learn: MAI-Voice-2.1](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/mai-voices).
- **Gemini 3.8 TTS**: all output carries a SynthID watermark. Voice Replication (not Voice Design) is unavailable in Illinois, Texas, the EEA, the UK, Switzerland and India [official, baseline note]. Paid Developer API traffic is not used for training [independent summary of Google terms] — [GetStream PR #663](https://github.com/GetStream/Vision-Agents/pull/663).
- **xAI Custom Voices**: US only, excluding Illinois [official] — [docs.x.ai Custom Voices](https://docs.x.ai/developers/model-capabilities/audio/custom-voices).
- **Mistral Voxtral** open weights: CC BY-NC 4.0, so commercial use requires the API or a separate agreement [official + secondary] — [Mistral](https://mistral.ai/news/voxtral-tts/), [anthemcreation](https://anthemcreation.com/en/artificial-intelligence/voxtral-tts-mistral-open-source-text-to-speech/).
- **Amazon Polly generative voices** may drift slightly with model updates, which matters for content synthesized over a long period [official] — [Polly generative voices](https://docs.aws.amazon.com/polly/latest/dg/generative-voices.html).
- **OpenRouter** lists Gemini 3.8 TTS, MAI-Voice-2/2.1/2.1-Flash and Grok Voice TTS 1.0 behind one OpenAI-compatible `/audio/speech` endpoint without voice cloning [listing; baseline note measured `supports_voice_cloning: false` for Gemini] — [OpenRouter MAI-Voice-2.1-Flash](https://openrouter.ai/microsoft/mai-voice-2.1-flash), [OpenRouter Grok Voice TTS](https://openrouter.ai/x-ai/grok-voice-tts-1.0).

### Inferences
- For a personal, non-commercial mod, none of the fetched terms prohibits using generated NPC lines in-game. If the mod is distributed and runs live TTS on players' machines with the user's key, OpenAI's disclosure rule (and good practice generally) suggests a one-line "NPC voices are AI-generated" notice in the mod README or settings.
- For a Russian-language mod whose players may be in Russia, a self-hosted relay or server in a supported country, or OpenRouter, is needed for OpenAI and Gemini. OpenRouter's own country policy was not checked.
- OpenRouter is a practical way to A/B MAI-Voice-2.1-Flash and Grok TTS in Russian without opening Azure or xAI accounts. It cannot do Gemini Voice Design or any cloning.

### Gaps
- Microsoft Azure and AWS sign-up or billing restrictions for Russia-based users were not verified in this pass.
- xAI and OpenRouter country restrictions for API use were not checked.
- Google's and Microsoft's specific terms on redistributing generated audio inside a free game mod were not fetched. Only OpenAI's disclosure rule and Microsoft's "licensed for commercial use" statement were found.
