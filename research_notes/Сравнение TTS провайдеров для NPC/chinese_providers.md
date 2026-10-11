# Chinese/Asian TTS providers for Russian-language NPC voices (state as of 2026-10-11)

Scope: MiniMax, Fish Audio, ByteDance Seed (Seed Audio 1.0 / Seed-TTS 2.0 on BytePlus), Alibaba Qwen (Qwen-Audio-3.0/3.1-TTS, Qwen3-TTS), StepFun, Zhipu GLM-TTS. Use case: short (1–3 sentence) Russian NPC replies, one distinct voice per NPC (e.g. Xardas), Python asyncio server with swappable adapters. Baseline for comparison: Gemini 3.8 Flash TTS ($0.50/1M input tokens, $9/1M audio output tokens).

Evidence labels used below:
- **[P]** = primary source fetched and read (official docs, official blog, arXiv paper, OpenRouter live API JSON pulled 2026-10-11).
- **[S]** = secondary source fetched (news/aggregator/third-party blog).
- **[snip]** = only seen in a search-result summary, page not opened; treat as unverified.
- Caveat: fetched pages were read through an automatic summariser, so exact numbers should be spot-checked before quoting in a final decision.

Normalisation assumptions (mine, not sourced): Russian speech ≈ 1,000 characters/minute ≈ **60,000 chars per hour** (±20%). A typical Xardas line (146 chars) = **263 UTF-8 bytes (1.8 bytes/char)** and ≈ 9–10 s of audio (measured byte count locally on a sample sentence).

---

## 1. Current models, IDs and release dates

### Takeaway
The live Chinese TTS line-up relevant to Russian is: MiniMax Speech 2.8 HD/Turbo (Jan–Feb 2026, still the newest MiniMax speech model), Fish Audio S2.1 Pro (June 2026; S2 Pro open-weight; S1 deprecated), Alibaba Qwen-Audio-3.0-TTS Flash/Plus (20–21 July 2026) and Qwen-Audio-3.1-TTS (23 Sept 2026, Model Studio only), ByteDance Seed Audio 1.0 (20–22 July 2026). StepFun (StepAudio 2.5 / 3) and Zhipu GLM-TTS exist but are Chinese/English-only.

### Cited Findings
**OpenRouter (live API, pulled 2026-10-11)** [P] — [OpenRouter models API](https://openrouter.ai/api/v1/models?output_modalities=speech):
- `minimax/speech-2.8-hd` (canonical `minimax/speech-2.8-hd-20260716`) and `minimax/speech-2.8-turbo` — listed on OpenRouter 2026-07-16.
- `fish-audio/s1`, `fish-audio/s2-pro`, `fish-audio/s2.1-pro`, `fish-audio/s2.1-pro-free:free` (canonical slugs `...-20260729`) — listed 2026-07-29.
- `qwen/qwen-audio-3.0-tts-flash`, `qwen/qwen-audio-3.0-tts-plus` (canonical `...-20260723`) — listed 2026-07-23. Description: "via the DashScope Speech Synthesizer API". No Qwen 3.1 TTS on OpenRouter yet.
- `bytedance-seed/seed-audio-1-0` (canonical `...-20260630`) — listed 2026-09-25; described as "non-streaming audio generation model" producing "speech and other audio from a natural-language text prompt".
- Same list also contains (for context): `google/gemini-3.8-flash-tts` (listed 2026-09-23), `google/gemini-3.8-flash-lite-tts`, ElevenLabs v4/v4-turbo/v3, Microsoft MAI-Voice-2.1, Mistral Voxtral Mini TTS, Deepgram, xAI Grok Voice TTS.

**MiniMax**
- Pay-as-you-go pricing page lists only `speech-2.8-hd` and `speech-2.8-turbo` as current; 2.6 and 02 marked legacy/discontinued; "no newer speech models than 2.8" — [MiniMax pricing](https://platform.minimax.io/docs/guides/pricing-paygo) [P].
- WebSocket docs still accept `speech-2.8-hd/turbo`, `speech-2.6-hd/turbo`, `speech-02-hd/turbo` — [MiniMax T2A WebSocket](https://platform.minimax.io/docs/guides/speech-t2a-websocket) [P].
- Speech 2.8 release date 2026-01-23 — [gradually.ai](https://www.gradually.ai/en/ai-models/minimax-speech-2-8/) [snip]; Artificial Analysis lists it as "Feb 2026" — [AA leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard) [P-ish, aggregator].

**Fish Audio**
- S2.1 Pro launched June 2026; API ID `s2.1-pro`, free ID `s2.1-pro-free`; S2 was "released with open weights earlier this year" — [Fish Audio blog: S2.1 Pro free API](https://fish.audio/blog/s2-1-pro-free-api/) [P]. Exact date 2026-06-23 — [gradually.ai](https://www.gradually.ai/en/ai-models/fish-audio-s2-1-pro/) [snip].
- Price table lists `s2.1-pro`, `s2.1-pro-free`, `s2-pro`, and `s1` marked **deprecated** — [Fish pricing & rate limits](https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits) [P].
- Fish Audio S2 technical report: arXiv 2603.08823 (March 2026), 4B-parameter model — [arXiv 2603.08823](https://arxiv.org/html/2603.08823v2) [P].

**Alibaba Qwen**
- Qwen-Audio-3.0-TTS-Flash and -Plus released 2026-07-21 (Alibaba blog) / 2026-07-20 (IT之家) — [Alibaba Cloud blog](https://www.alibabacloud.com/blog/qwen-audio-3-0-tts-more-multilingual-easier-to-direct_603379) [P]; [IT之家](https://www.ithome.com/0/979/128.htm) [snip]. Hosted-only (no weights) — [the-decoder](https://the-decoder.com/alibabas-qwen-audio-3-0-tts-plus-tops-the-competition-in-the-text-to-speech-rankings/) [S].
- Model Studio model page exists for `qwen-audio-3.0-tts-plus` — [Model Studio doc](https://www.alibabacloud.com/help/en/model-studio/qwen-audio-3-0-tts-plus) [P]; `qwen-audio-3.0-tts-flash` used in examples, `qwen3-tts-flash` still listed as stable in Singapore+Beijing — [Model Studio Qwen-TTS doc](https://www.alibabacloud.com/help/en/model-studio/qwen-tts) [P].
- Qwen-Audio-3.1 family (3.1-TTS, 3.1-TTS-Next, 3.1-ASR, 3.1-ASR-Next, 3.1-Realtime) announced 2026-09-23 at Apsara Conference, ~70% TTS price cut; served as `qwen-audio-3.1-tts-flash` — [ai-tldr](https://ai-tldr.dev/models/qwen-audio-3-1-tts/) [S]; [orcarouter blog](https://www.orcarouter.ai/blog/qwen-audio-3-1-vs-qwen-audio-3-0-tts) [S]; [digg](https://digg.com/ai/hftut7bc) [snip].
- Qwen3-TTS open-weight family (25Hz/12Hz tokenizers, 0.6B/1.7B, plus VoiceDesign variant): tech report arXiv 2601.15621 (Jan 2026) — [arXiv 2601.15621](https://arxiv.org/html/2601.15621) [snip]; [HF VoiceDesign mirror](https://huggingface.co/FenomAI/Qwen3-TTS-12Hz-1.7B-VoiceDesign) [snip].

**ByteDance Seed**
- Seed Audio 1.0 released 2026-07-20; "audio creation model" (speech + SFX + ambience in one pass), available via BytePlus console — [Seed blog](https://seed.bytedance.com/blog/from-speech-to-audio-creation-introducing-the-seed-audio-1-0-audio-creation-model) [P].
- Separate classic TTS product on BytePlus: "Seed Speech" with TTS 2.0 (`seed-tts-2.0`) and "Voice Replication 2.0" — [BytePlus docs](https://docs.byteplus.com/docs/byteplusvoice/texttospeechv2) [P].

**StepFun**
- StepAudio 2.5 TTS (Aug 2026 per AA), StepAudio 3 TTS launched 2026-09-15, hosted-only on StepFun open platform — [AA leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard) [P-ish]; [orcarouter: StepAudio 3 vs Qwen](https://www.orcarouter.ai/blog/stepaudio-3-vs-qwen-audio-3-0-tts) [S].

**Zhipu**
- GLM-TTS: open-source (MIT), Llama-architecture + flow matching, Chinese and English only — [tts.ai GLM-TTS page](https://tts.ai/voices/glm-tts-jiayan-english/) [snip].

**Leaderboard snapshot (Artificial Analysis TTS arena, page fetched 2026-10-11, "Sept 2026" update)** — [AA leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard) [P, aggregator]:
- #1 Eleven v4 Turbo 1327, #2 Eleven v4 1323, **#3 Qwen-Audio-3.1-TTS-Plus 1298**, #4 Cartesia Sonic 3.6 1284, **#5 Gemini 3.8 Flash TTS 1272**, **#6 Qwen-Audio-3.0-TTS-Plus 1264**, #8 Gemini 3.8 Flash-Lite TTS 1244, **#14 StepAudio 2.5 TTS 1209**, **#19 MiniMax Speech 2.8 HD 1173**, **#22 MiniMax Speech 2.8 Turbo 1153**. No Fish Audio, ByteDance/Seed or Zhipu model in top 25.
- Elo drifts: Qwen-Audio-3.0-TTS-Plus was 1236 (#1) at launch per [the-decoder](https://the-decoder.com/alibabas-qwen-audio-3-0-tts-plus-tops-the-competition-in-the-text-to-speech-rankings/) [S], 1259 per [orcarouter](https://www.orcarouter.ai/blog/qwen-audio-3-1-vs-qwen-audio-3-0-tts) [S], 1264 on the current board.
- OpenRouter attaches "design_arena" benchmarks: Gemini 3.8 Flash TTS text-to-speech Elo 1446 (rank 1); Fish S2.1 Pro "audiorealism" Elo 952 (rank 4); Qwen-Audio-3.0-TTS Flash "audiorealism" Elo 861 (rank 5) — [OpenRouter models API](https://openrouter.ai/api/v1/models?output_modalities=speech) [P].

### Inferences
- The arena rankings are essentially English-listening tests; [abit.ee/search summary](https://abit.ee/en/artificial-intelligence/qwen-audio-30-tts-alibaba-text-to-speech-voice-cloning-tts-api-en) [snip] explicitly notes Russian isn't tested on AA. They say little about Russian stress/intonation.
- Release cadence is fast (Qwen 3.0 → 3.1 in two months); adapter design should parametrise model IDs.

### Gaps
- No official MiniMax announcement page for Speech 2.8 was opened; release date is secondary.
- No arena with a Russian-language track was found.

---

## 2. Russian support and quality evidence

### Takeaway
Russian is officially supported by MiniMax (2.8), Fish Audio (S1/S2/S2.1), Qwen-Audio-3.0-TTS and open Qwen3-TTS; it is **not** listed for Seed Audio 1.0, Seed-TTS 2.0, StepAudio 2.5/3 or GLM-TTS. On the only shared objective benchmark with a Russian row (MiniMax Multilingual Testset), Fish Audio S2 has the best Russian WER (2.40%) and speaker similarity (0.790), ahead of Qwen3-TTS-1.7B (3.21%), ElevenLabs Multilingual v2 (3.88%) and MiniMax-Speech (4.28%, 2025-era model). No native-speaker review of Russian quality for these 2026 models was found.

### Cited Findings
**Official language lists**
- MiniMax: WebSocket docs say "40 widely used global languages" including Russian — [MiniMax T2A WebSocket](https://platform.minimax.io/docs/guides/speech-t2a-websocket) [P]. Earlier paper said 32 languages — [arXiv 2505.07916](https://arxiv.org/abs/2505.07916) [P] (count grew; conflict is version-related).
- Fish Audio S2.1 Pro: 83 languages, Russian explicitly listed; "the same model handles all languages — no separate endpoints, no per-language pricing" — [Fish blog](https://fish.audio/blog/s2-1-pro-free-api/) [P]. S2 trained on >10M hours across ~80 languages — [arXiv 2603.08823](https://arxiv.org/html/2603.08823v2) [P].
- Qwen-Audio-3.0-TTS: 16 languages "Arabic, Chinese, English, French, German, Indonesian, Italian, Japanese, Korean, Malay, Portuguese, **Russian**, Spanish, Tagalog, Thai, Vietnamese" + 20 Chinese dialect regions — [Alibaba Cloud blog](https://www.alibabacloud.com/blog/qwen-audio-3-0-tts-more-multilingual-easier-to-direct_603379) [P]. (the-decoder's summary did not mention Russian — [the-decoder](https://the-decoder.com/alibabas-qwen-audio-3-0-tts-plus-tops-the-competition-in-the-text-to-speech-rankings/) [S] — the Alibaba blog is authoritative.)
- Qwen-Audio-3.1-TTS: "16 languages, seven added in this version"; Russian not explicitly listed in the secondary write-up — [ai-tldr](https://ai-tldr.dev/models/qwen-audio-3-1-tts/) [S]; orcarouter says the 3.0 vs 3.1 language counts in Alibaba's material "cannot be reconciled" — [orcarouter](https://www.orcarouter.ai/blog/qwen-audio-3-1-vs-qwen-audio-3-0-tts) [S].
- Qwen3-TTS (open): 10 languages incl. Russian; VoiceDesign variant also supports these 10 — [arXiv 2601.15621](https://arxiv.org/html/2601.15621) [snip]; [DeepInfra Qwen3-TTS-VoiceDesign](https://deepinfra.com/Qwen/Qwen3-TTS-VoiceDesign) [snip].
- Seed Audio 1.0: "20+ languages including Chinese, English, Japanese, Korean, Spanish, Indonesian, German, French, Thai, Vietnamese" — **Russian not listed** — [Seed blog](https://seed.bytedance.com/blog/from-speech-to-audio-creation-introducing-the-seed-audio-1-0-audio-creation-model) [P]. A reseller says "20 languages" without a list — [PiAPI](https://piapi.ai/seed-audio-1-0-api) [snip].
- Seed-TTS 2.0 (BytePlus): built-in voices for English, Chinese, Japanese, Spanish, Indonesian, Portuguese, Korean, Italian, German, French; Russian not listed — [WaveSpeed](https://wavespeed.ai/ru/models/bytedance/seed-speech-tts-2.0) [snip]; BytePlus docs page fetched had no language list — [BytePlus docs](https://docs.byteplus.com/docs/byteplusvoice/texttospeechv2) [P].
- StepAudio 2.5 TTS: Chinese and English — [TypingMind/StepAudio 2.5](https://custom.typingmind.com/tools/estimate-llm-usage-costs/stepfun-ai/stepaudio-2-5-tts) [snip]; StepAudio 3 TTS "Chinese-first", no multilingual claim — [orcarouter](https://www.orcarouter.ai/blog/stepaudio-3-vs-qwen-audio-3-0-tts) [S].
- GLM-TTS: English and Chinese only — [tts.ai](https://tts.ai/voices/glm-tts-jiayan-english/) [snip].

**Objective Russian numbers (MiniMax Multilingual Testset, zero-shot cloning; lower WER better, higher SIM better)**
| System | Russian WER % | Russian SIM | Source |
|---|---|---|---|
| Fish Audio S2 (open 4B; S2.1 Pro is newer) | **2.400** | **0.790** | [arXiv 2603.08823](https://arxiv.org/html/2603.08823v2) [P] |
| Qwen3-TTS-12Hz-1.7B | 3.212 | n/a in snippet | [arXiv 2601.15621](https://arxiv.org/html/2601.15621) [snip] |
| ElevenLabs Multilingual v2 | 3.878 | 0.676 | [arXiv 2505.07916](https://arxiv.org/html/2505.07916) [P] |
| Qwen3-TTS-12Hz-0.6B / 25Hz-1.7B / 25Hz-0.6B | 4.458 / 4.535 / 5.957 | — | [arXiv 2601.15621](https://arxiv.org/html/2601.15621) [snip] |
| MiniMax-Speech (speech-02 era, May 2025) | 4.281 | 0.761 | [arXiv 2505.07916](https://arxiv.org/html/2505.07916) [P] |
| Fish Audio S1 | 5.250 | 0.764 | [arXiv 2603.08823](https://arxiv.org/html/2603.08823v2) [P] |
- For context in the same MiniMax table: MiniMax English WER 2.164 / German 1.906; ElevenLabs German 0.572 — i.e. MiniMax's Russian WER was among its weaker European languages — [arXiv 2505.07916](https://arxiv.org/html/2505.07916) [P].
- Fish S2 also reports Seed-TTS-Eval WER Chinese 0.54 / English 0.99 — [arXiv 2603.08823](https://arxiv.org/pdf/2603.08823) [snip].
- Qwen-Audio-3.0-TTS: average WER across 16 languages 3.87 (Flash) / 3.96 (Plus); average speaker similarity 80.44 (Flash) / 82.75 (Plus); Alibaba claims best WER/CER in 10 of 16 languages, but **no per-language (Russian) breakdown published** — [Alibaba Cloud blog](https://www.alibabacloud.com/blog/qwen-audio-3-0-tts-more-multilingual-easier-to-direct_603379) [P]; ["10 of 16" claim](https://www.buildfastwithai.com/blogs/qwen-audio-3-tts) [snip].
- Seed Audio 1.0: only multilingual MOS "mostly above 4.0" and instruction-following >3.5 (except Vietnamese); no WER/SIM disclosed — [Seed blog](https://seed.bytedance.com/blog/from-speech-to-audio-creation-introducing-the-seed-audio-1-0-audio-creation-model) [P].
- Fish S2.1 Pro: 61% win rate vs S2 Pro in listening tests (language mix not stated) — [Fish blog](https://fish.audio/blog/s2-1-pro-free-api/) [P].

**Qualitative / Russian-language commentary**
- Comparison blog: Fish has broader coverage of less common European languages; MiniMax stronger for Mandarin; both "handle major European languages well" (Russian not specifically assessed) — [bitdoze: Fish vs MiniMax](https://www.bitdoze.com/fish-audio-vs-minimax/) [snip].
- Habr has a Qwen3-TTS article claiming cloning similarity beats ElevenLabs and MiniMax, 9 built-in voices, emotion control via instructions — [habr 1020424](https://habr.com/ru/articles/1020424) [snip; page returned 403].

### Inferences
- For Russian, Fish (S2/S2.1) has the strongest published objective evidence; Qwen is plausible but unproven per-language; MiniMax's published Russian numbers are from the 2025 model and were middling — 2.8 may be better but no evidence found.
- WER measures intelligibility, not stress placement (ударения) or "foreign accent" in designed voices. Stress errors on rare names (Xardas/Ксардас, Innos, Beliar) must be tested by ear; Fish/MiniMax/Qwen all lack documented Russian stress-mark (`+`/U+0301) support in what I read.
- Seed Audio 1.0, Seed-TTS 2.0, StepAudio and GLM-TTS should be treated as **not viable for Russian** until an official Russian listing appears.

### Gaps
- No native-speaker review or blind test of 2026 models in Russian (habr/vc.ru) was accessible; habr article blocked (403).
- No Russian WER for MiniMax 2.8, Fish S2.1 Pro, Qwen-Audio-3.0/3.1.
- Whether Qwen-Audio-3.1-TTS kept Russian is unconfirmed.

---

## 3. Custom voices: voice design, cloning, voice libraries

### Takeaway
All three viable providers support both text-description voice design and zero-shot cloning: MiniMax (design $3/voice, clone $1.5/voice, 10 s–5 min sample), Fish Audio (voice-design-1 $0.01/request, cloning from ~10 s, 2M+ community voice library), Qwen (cloning with built-in speech enhancement; voice design in Qwen3-TTS-VD; Model Studio's English voice-design doc limits descriptions to Chinese/English). Via OpenRouter, cloning works only for Fish and Seed (`input_references`); MiniMax and Qwen on OpenRouter are preset-voice only.

### Cited Findings
**MiniMax**
- Voice Design $3 per voice and Rapid Voice Cloning $1.5 per voice, both charged at first synthesis use; preview synthesis billed at model rate — [MiniMax pricing](https://platform.minimax.io/docs/guides/pricing-paygo) [P].
- Clone source audio: mp3/m4a/wav, **10 s – 5 min**, ≤20 MB; optional prompt audio <8 s to improve quality; flow = File Upload (`purpose: voice_clone`) → Voice Clone API with custom `voice_id` → use in T2A — [MiniMax voice clone guide](https://platform.minimax.io/docs/guides/speech-voice-clone) [P]. Docs page read did not state expiry of unused cloned voices or a consent clause.
- Speaker encoder extracts timbre from reference without transcription — [arXiv 2505.07916](https://arxiv.org/abs/2505.07916) [P].
- On OpenRouter, MiniMax endpoints report `supports_voice_cloning: false`; `supported_voices` lists 45 `English_*` system voices (e.g. `English_ManWithDeepVoice`, `English_WiseScholar`, `English_ImposingManner`); description says it "accepts arbitrary MiniMax voice IDs" — [OpenRouter endpoints API](https://openrouter.ai/api/v1/models/minimax/speech-2.8-hd/endpoints) [P].

**Fish Audio**
- `voice-design-1`: $0.01 per successful request — [Fish pricing](https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits) [P].
- Cloning available on free S2.1 tier; voice used via `reference_id` (saved model) or inline reference audio — [Fish blog](https://fish.audio/blog/s2-1-pro-free-api/) [P]; [Fish SDK docs](https://docs.fish.audio/developer-guide/sdk-guide/python/text-to-speech) [snip].
- ~10 s of audio suffices for a clone; library of "over 2,000,000 voices" — [fish.audio/ru](https://fish.audio/ru/) [snip].
- Plans: Free = 3 public voice slots; Plus = unlimited public + 10 private + 1 "professional" voice slot; Pro = unlimited + 5 professional — [Fish plans](https://fish.audio/plan/) [P].
- OpenRouter: Fish S2.1 Pro and free variant `supports_voice_cloning: true`, `supports_multiple_audio_references: false`; Fish accepts reference transcripts — [OpenRouter endpoints API](https://openrouter.ai/api/v1/models/fish-audio/s2.1-pro/endpoints) [P]; [OpenRouter TTS docs](https://openrouter.ai/docs/features/multimodal/tts) [P].
- Consent: Fish guidance says clone only your own voice or one you have permission for; avoid public figures without permission — [bitdoze clone guide](https://www.bitdoze.com/fish-audio-clone-voice/) [snip]; [licenseorg summary](https://licenseorg.com/guide/ai-content/fish-audio) [snip].
- A search for a Gothic/Xardas community voice on fish.audio returned only unrelated community models — [fish.audio search results](https://fish.audio/m/99ea63a0c3ad4a3d93d587d4cd87643c) [snip].

**Alibaba Qwen**
- Qwen-Audio-3.0-TTS cloning: "speech enhancement is built into the cloning path" for degraded reference audio — [Alibaba Cloud blog](https://www.alibabacloud.com/blog/qwen-audio-3-0-tts-more-multilingual-easier-to-direct_603379) [P]; 3.1 adds cross-language timbre transfer (Mandarin, Cantonese, English, Japanese only) — [orcarouter](https://www.orcarouter.ai/blog/qwen-audio-3-1-vs-qwen-audio-3-0-tts) [S].
- Model Studio: "Supports Voice cloning and Voice Design for creating custom voices" (separate guides) — [Model Studio Qwen-TTS doc](https://www.alibabacloud.com/help/en/model-studio/qwen-tts) [P].
- Model Studio voice-design guide (international doc) covers CosyVoice v3/v3.5: "voice descriptions support Chinese and English only", `voice_prompt` ≤500 chars, voice creation free, ≤1,000 custom voices/account, voices unused for 1 year are deleted; endpoint shown is Beijing-region — [Model Studio voice design](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-voice-design) [P].
- Open Qwen3-TTS-VoiceDesign: descriptions like "a deep male voice with a calm, authoritative presence", 10 languages incl. Russian — [arXiv 2601.15621](https://arxiv.org/html/2601.15621) [snip].
- OpenRouter Qwen endpoints: `supports_voice_cloning: false`; only 2 voices each: Flash `loongjohn`, `longanhuan_v3.6`; Plus `longanlingxin`, `longanlufeng` — [OpenRouter models API](https://openrouter.ai/api/v1/models?output_modalities=speech) [P].

**ByteDance Seed Audio 1.0**
- Accepts text descriptions, authorised reference samples, or both; no per-speaker training — [Seed blog](https://seed.bytedance.com/blog/from-speech-to-audio-creation-introducing-the-seed-audio-1-0-audio-creation-model) [P].
- Up to 3 reference clips (~30 s each) — [Runware docs](https://runware.ai/docs/models/bytedance-seed-audio-1-0) [snip]; OpenRouter: 1–3 audio clips (addressable as `@Audio1..3` in the prompt) OR exactly one image; rejects `voice` + `input_references` together — [OpenRouter TTS docs](https://openrouter.ai/docs/features/multimodal/tts) [P]; endpoint flags `supports_voice_cloning`, `supports_multiple_audio_references`, `supports_image_reference` all true — [OpenRouter endpoints API](https://openrouter.ai/api/v1/models/bytedance-seed/seed-audio-1-0/endpoints) [P].

**StepFun**: StepAudio 2.5 zero-shot cloning — [TypingMind](https://custom.typingmind.com/tools/estimate-llm-usage-costs/stepfun-ai/stepaudio-2-5-tts) [snip]; StepAudio 3: 9.9 CNY per cloned voice — [orcarouter](https://www.orcarouter.ai/blog/stepaudio-3-vs-qwen-audio-3-0-tts) [S].
**GLM-TTS**: cloning from 3–10 s samples — [tts.ai](https://tts.ai/voices/glm-tts-jiayan-english/) [snip].

### Inferences
- For a fixed NPC cast (say 20–50 voices), MiniMax one-off fees are $60–150 (design) — non-trivial but one-time; Fish voice design is effectively free ($0.01/attempt).
- OpenRouter's MiniMax route probably cannot use voice IDs designed/cloned in *your own* MiniMax account (those IDs live in your MiniMax tenant, OpenRouter calls with its own key) — so on OpenRouter MiniMax is limited to system voices. Not verified; test before relying on it.
- Via OpenRouter, Fish cloning sends the reference clip with every request (`input_references`), adding upload size/latency per line; direct Fish API lets you save a voice once and pass `reference_id`.
- Qwen via OpenRouter (2 Chinese-named preset voices, no cloning) is unsuitable for distinct Russian NPC voices; use Model Studio directly if Qwen is chosen.
- Cloning the Russian dub actors of Gothic would fall foul of Fish/MiniMax consent rules; voice design from text (or licensed/own recordings) is the safe path.

### Gaps
- Whether MiniMax Voice Design accepts Russian prompts and yields Russian-native timbre; whether MiniMax has Russian system voices (only `English_*` voices are exposed on OpenRouter).
- Qwen-Audio-3.x voice-design API specifics for international region (language of description, price) not found; only CosyVoice voice-design doc retrieved.
- Fish community library: number/quality of Russian voices not quantified.

---

## 4. Expressiveness: emotion/style control, multi-speaker

### Takeaway
Fish S2/S2.1 (free-form `[bracket]` tags + natural-language control + native multi-speaker) and Qwen-Audio-3.x (86 inline tags + natural-language instructions) are the most directable; MiniMax offers sound tags like `(sighs)`/`(laughs)` plus speed/pitch/volume. Seed Audio treats input as a creative prompt rather than verbatim script — risky for NPC lines.

### Cited Findings
- Fish S2: inline tags at word positions, e.g. `[prolonged laugh]`, `[inhale]`, `[angry]`, `[emphasis]`, `[in a hurry]`; native multi-speaker interleaved dialogue in one pass with `<|speaker:0|>` tags — [arXiv 2603.08823](https://arxiv.org/html/2603.08823v2) [P]. S2 Pro bracket tags like `[whisper]`, `[excited]` — [Fish docs/search](https://docs.fish.audio/resources/best-practices/text-to-speech) [snip]. S1 used parenthetical tags `(angry)`, `(happy)`, `(nervous)` — [Fish developer guide](https://fish.audio/blog/text-to-speech-api-developer-guide/) [snip]; OpenRouter description of S1: "parenthetical controls" — [OpenRouter API](https://openrouter.ai/api/v1/models?output_modalities=speech) [P].
- S2.1 Pro: "natural-language control over emotion and delivery written inline with the text" — [gradually.ai](https://www.gradually.ai/en/ai-models/fish-audio-s2-1-pro/) [snip]; OpenRouter: "open-ended natural-language controls for speaking style" — [OpenRouter API](https://openrouter.ai/api/v1/models?output_modalities=speech) [P].
- Qwen-Audio-3.0-TTS: plain-language prompts steer "emotion, role, scenario, and pace"; inline tags such as `[gasp]`, `[giggles]`, `[angry]` — [Alibaba Cloud blog](https://www.alibabacloud.com/blog/qwen-audio-3-0-tts-more-multilingual-easier-to-direct_603379) [P]; 86 fine-grained tags — [ai-tldr](https://ai-tldr.dev/models/qwen-audio-3-1-tts/) [S]; 3.1 instructions cover "role, emotion, speaking style, rate, timbre and accent" — [ai-tldr](https://ai-tldr.dev/models/qwen-audio-3-1-tts/) [S].
- MiniMax: `(sighs)`, `(laughs)` in example text; `voice_setting` has `speed`, `vol`, `pitch` — [MiniMax T2A WebSocket](https://platform.minimax.io/docs/guides/speech-t2a-websocket) [P]. (An `emotion` parameter exists in older MiniMax APIs but was not shown on the page read — unverified.)
- OpenRouter: `instructions` field is only honoured by OpenAI and Gemini TTS; Gemini wants `instructions` rather than inline directives; multi-speaker turn arrays supported in `input` — [OpenRouter TTS docs](https://openrouter.ai/docs/features/multimodal/tts) [P]. For Fish/Qwen/MiniMax, style must therefore be inline in `input` text.
- Seed Audio 1.0 on OpenRouter: "treats `input` as a creative prompt (not verbatim speech)", 3,000-char input, 0.5–2.0 speed, 120 s output cap — [OpenRouter TTS docs](https://openrouter.ai/docs/features/multimodal/tts) [P]; prompt-level timing control at 100 ms, multi-character dialogue + ambience in one pass — [Seed blog](https://seed.bytedance.com/blog/from-speech-to-audio-creation-introducing-the-seed-audio-1-0-audio-creation-model) [P].
- StepAudio 2.5: plain natural-language instructions for emotion, pacing, pauses — [TypingMind](https://custom.typingmind.com/tools/estimate-llm-usage-costs/stepfun-ai/stepaudio-2-5-tts) [snip]; StepAudio 3 infers prosody from context instead of tags — [orcarouter](https://www.orcarouter.ai/blog/stepaudio-3-vs-qwen-audio-3-0-tts) [S].

### Inferences
- The LLM producing NPC replies can emit provider-specific tags (Fish `[angry]`, Qwen `[gasp]`, MiniMax `(sighs)`); the adapter should translate a neutral tag set to each provider's syntax.
- Seed Audio's "creative prompt" semantics mean words may be paraphrased or padded with SFX — unacceptable when subtitles must match speech.

### Gaps
- Whether Fish/Qwen tags work reliably inside Russian text (tags are English words) — no evidence found.

---

## 5. Streaming, WebSocket, time-to-first-audio

### Takeaway
Fish Audio is fastest (~70–90 ms TTFA claimed, WebSocket + async SDK); MiniMax has WebSocket streaming with sub-300 ms claims; Qwen-Audio Flash ~300 ms first packet with SSE streaming (Plus is slow: ~16 chars/s generation). Seed Audio 1.0 is non-streaming. OpenRouter streams raw audio bytes over HTTP (no WebSocket).

### Cited Findings
- Fish S2.1 Pro TTFA ~70 ms (decode) / ~90 ms (standard API); 8,006 tokens/s at 64 concurrent requests — [Fish blog](https://fish.audio/blog/s2-1-pro-free-api/) [P]. S2 open weights: SGLang inference, RTF 0.195, TTFA <100 ms on one H200 — [arXiv 2603.08823](https://arxiv.org/html/2603.08823v2) [P]. Free tier: "No uptime/TTFA guarantees" — [Fish blog](https://fish.audio/blog/s2-1-pro-free-api/) [P]. Python SDK has `AsyncWebSocketSession` and `stream_websocket()` for incrementally generated text — [Fish SDK WebSocket](https://docs.fish.audio/sdk-reference/python/websocket) [snip]; [Fish realtime streaming](https://docs.fish.audio/features/realtime-streaming) [snip].
- MiniMax WebSocket: `wss://api.minimax.io/ws/v1/t2a_v2` (US-West `wss://api-uw.minimax.io/...`); ≤10,000 chars/request; mp3 32 kHz/128 kbps example; text-normalisation adds ~30–100 ms — [MiniMax T2A WebSocket](https://platform.minimax.io/docs/guides/speech-t2a-websocket) [P]. Sub-300 ms TTFT / sub-250 ms end-to-end streaming claims — [Together AI model page](https://www.together.ai/models/minimax-speech-28) [snip].
- Qwen-Audio-3.0-TTS-Flash: "300ms-level" first-packet latency; Plus aimed at quality — [Alibaba Cloud blog](https://www.alibabacloud.com/blog/qwen-audio-3-0-tts-more-multilingual-easier-to-direct_603379) [P]. Model Studio streaming via SSE header `X-DashScope-SSE: enable`; non-streaming returns a 24-h URL — [Model Studio Qwen-TTS doc](https://www.alibabacloud.com/help/en/model-studio/qwen-tts) [P]. Speed weakness: ~16 chars/s vs Simba 3.2 30.2 and Sonic 3.5 120 (AA measurement, presumably Plus) — [the-decoder](https://the-decoder.com/alibabas-qwen-audio-3-0-tts-plus-tops-the-competition-in-the-text-to-speech-rankings/) [S]. Rate limit 180 RPM for Plus — [Model Studio doc](https://www.alibabacloud.com/help/en/model-studio/qwen-audio-3-0-tts-plus) [P].
- Seed Audio 1.0: "non-streaming" — [OpenRouter API](https://openrouter.ai/api/v1/models?output_modalities=speech) [P].
- OpenRouter: `POST /api/v1/audio/speech`, OpenAI-compatible; response is raw audio bytes (`mp3` or `pcm`, default `pcm`); streaming via OpenAI SDK `.with_streaming_response.create()` — [OpenRouter TTS docs](https://openrouter.ai/docs/features/multimodal/tts) [P]. Live endpoint stats show `latency_last_30m: null` for all Chinese TTS endpoints (no published latency) — [OpenRouter endpoints API](https://openrouter.ai/api/v1/models/fish-audio/s2.1-pro/endpoints) [P].
- OpenRouter uptime (last 1 day, 2026-10-11): MiniMax HD 98.7%, Fish S2.1 Pro 100%, Fish free 99.9%, Seed 100%, Qwen Plus 100% — [OpenRouter endpoints API](https://openrouter.ai/api/v1/models/minimax/speech-2.8-hd/endpoints) [P].

### Inferences
- For 1–3 sentence lines, Fish or Qwen-Flash or MiniMax-Turbo streaming should start audio in well under 0.5 s; network RTT from Russia to US/SG servers adds ~150–300 ms (not measured).
- Qwen Plus at ~16 chars/s would take ~9 s to fully synthesise a 146-char line — only acceptable if streamed or pre-generated.
- Seed Audio is unsuitable for interactive dialogue (non-streaming + up to 120 s generation).

### Gaps
- No independent Russian-region latency measurements; no official MiniMax TTFA number on the docs page read.

---

## 6. Python SDK / async integration

### Takeaway
Easiest async paths: OpenRouter via `openai.AsyncOpenAI(base_url="https://openrouter.ai/api/v1")` for all listed models; Fish has an official Python SDK with async WebSocket; MiniMax and Qwen are reachable via plain HTTP/WebSocket (aiohttp/httpx/websockets), Qwen also via DashScope SDK.

### Cited Findings
- OpenRouter TTS is OpenAI Audio Speech-compatible; `input_references` accepts base64 data URI (≤20 MiB encoded / 15 MiB decoded) or remote URL (≤15 MiB), transcripts ≤10,000 chars; generation ID returned in `X-Generation-Id` header — [OpenRouter TTS docs](https://openrouter.ai/docs/features/multimodal/tts) [P]; [OpenRouter audio API announcement](https://openrouter.ai/blog/announcements/announcing-audio-apis) [snip].
- Fish: Python SDK with `convert()`, `stream()`, `stream_websocket()` and `AsyncWebSocketSession`; REST endpoint `https://api.fish.audio/v1/tts` with Python/JS examples — [Fish SDK docs](https://docs.fish.audio/developer-guide/sdk-guide/python/text-to-speech) [snip]; [Fish blog](https://fish.audio/blog/s2-1-pro-free-api/) [P].
- MiniMax: HTTP T2A and WebSocket T2A (`wss://api.minimax.io/ws/v1/t2a_v2`) — [MiniMax T2A WebSocket](https://platform.minimax.io/docs/guides/speech-t2a-websocket) [P]; official MiniMax MCP servers exist (Python/JS) exposing `voice_clone` etc. — [glama MiniMax-MCP-JS](https://glama.ai/mcp/servers/@MiniMax-AI/MiniMax-MCP-JS/tools/voice_clone) [snip].
- Qwen: DashScope SDK optional; SSE streaming over HTTP — [Model Studio Qwen-TTS doc](https://www.alibabacloud.com/help/en/model-studio/qwen-tts) [P].

### Inferences
- A single OpenRouter adapter covers Fish/MiniMax/Qwen/Seed/Gemini with one code path, but loses provider-native features (saved voice IDs, Qwen voice catalogue, MiniMax custom voices, WebSocket incremental text). A native Fish adapter is the most valuable second adapter.

### Gaps
- Did not verify whether Fish's SDK async session supports HTTP (non-WS) streaming, nor whether MiniMax publishes an official Python SDK.

---

## 7. Pricing (normalised) — native API vs OpenRouter, free tiers

### Takeaway
Cheapest viable Russian options: Fish S2.1 Pro (free until 2026-11-30; then $15/1M UTF-8 bytes ≈ $27/1M Russian chars ≈ $1.6/hour) and Qwen-Audio-3.0-TTS (≈$15–20/1M chars ≈ $0.9–1.2/hour; 3.1 claims ~70% cheaper). MiniMax is 4–7× more expensive ($60–100/1M chars ≈ $3.6–6/hour + per-voice fees). Seed Audio via OpenRouter is the priciest ($0.0025/s = $9/hour). Gemini 3.8 Flash TTS baseline is ≈ $0.8/hour, i.e. cheaper than everything here except Qwen-Flash and Fish-on-OpenRouter.

### Cited Findings
**Native prices**
- MiniMax: speech-2.8-hd **$100/1M chars**, speech-2.8-turbo **$60/1M chars**; Voice Design $3/voice, cloning $1.5/voice; no free TTS tier listed — [MiniMax pricing](https://platform.minimax.io/docs/guides/pricing-paygo) [P].
- Fish: s2.1-pro / s2-pro / s1 **$15 per 1M UTF-8 bytes**, s2.1-pro-free $0; "1M UTF-8 bytes ≈ 180,000 English words, or about 12 hours of speech"; voice-design-1 $0.01/request; concurrency 5 (<$100 prepaid), 15 (≥$100), 50 (≥$1,000) — [Fish pricing](https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits) [P].
- Fish free API: s2.1-pro-free through **2026-11-30**, no hard character cap (fair use), no SLA, requests may be retained for model improvement, >$1M ARR products must contact Fish — [Fish blog](https://fish.audio/blog/s2-1-pro-free-api/) [P].
- Fish consumer plans: Free 8,000 credits/month (~7 min, 500 chars/generation); Plus $11/mo annual ($15 monthly), 250k credits (~200 min); Pro $75/mo annual ($100 monthly), 2M credits (~1,620 min); ~600–625 credits per minute — [Fish plans](https://fish.audio/plan/) [P].
- Qwen-Audio-3.0-TTS-Plus: Singapore **$0.20 per 10,000 chars (= $20/1M)**; Beijing $0.19253/10k — [Model Studio doc](https://www.alibabacloud.com/help/en/model-studio/qwen-audio-3-0-tts-plus) [P]. Conflicting secondary figure $27.60/1M — [the-decoder](https://the-decoder.com/alibabas-qwen-audio-3-0-tts-plus-tops-the-competition-in-the-text-to-speech-rankings/) [S]; AA lists $19.3/1M — [AA leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard) [P-ish]. Flash ≈ $15/1M — [orcarouter](https://www.orcarouter.ai/blog/qwen-audio-3-1-vs-qwen-audio-3-0-tts) [S].
- Qwen-Audio-3.1-TTS-flash after ~70% cut: Singapore $0.23/1M input tokens + $1.87/1M output tokens (token-based, not per character) — [ai-tldr](https://ai-tldr.dev/models/qwen-audio-3-1-tts/) [S]; China: 1.5 CNY input / 12 CNY output per 1M tokens — [orcarouter](https://www.orcarouter.ai/blog/qwen-audio-3-1-vs-qwen-audio-3-0-tts) [S]. AA lists 3.1-TTS-Plus at $19.3/1M chars — [AA leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard) [P-ish] (conflict with "70% cut"; unresolved).
- Seed Audio 1.0 on BytePlus: $0.002 per output second; resellers $0.002–$0.00325/s — [PiAPI](https://piapi.ai/seed-audio-1-0-api) [snip]. Seed-TTS 2.0: $0.03 per 1,000 chars (= $30/1M) — [cloudprice](https://cloudprice.net/models/bytedance-seed-tts-2) [snip].
- StepAudio 2.5 TTS: $0.85/10k chars = $85/1M — [AA model page](https://artificialanalysis.ai/text-to-speech/models/step-audio-2.5-tts) [snip]; StepAudio 3: 2.5 CNY/10k chars ≈ $35/1M — [orcarouter](https://www.orcarouter.ai/blog/stepaudio-3-vs-qwen-audio-3-0-tts) [S].

**OpenRouter prices (live API, 2026-10-11)** — [OpenRouter models API](https://openrouter.ai/api/v1/models?output_modalities=speech) [P]; units per [OpenRouter TTS docs](https://openrouter.ai/docs/features/multimodal/tts) [P] ("most TTS models priced per character of input text"; Seed Audio "per second of generated audio" under `pricing.completion`):
| Model | OpenRouter price field | = per 1M chars | Native equivalent |
|---|---|---|---|
| minimax/speech-2.8-hd | prompt 0.0001 | $100 | $100 (same) |
| minimax/speech-2.8-turbo | prompt 0.00006 | $60 | $60 (same) |
| fish-audio/s1, s2-pro, s2.1-pro | prompt 0.000015 | $15 per char (if per-char) | $15 per 1M **bytes** |
| fish-audio/s2.1-pro-free:free | 0 | free | free to 2026-11-30 |
| qwen/qwen-audio-3.0-tts-flash | prompt 0.000015 | $15 | ≈$15 |
| qwen/qwen-audio-3.0-tts-plus | prompt 0.00002 | $20 | $20 (Singapore) |
| bytedance-seed/seed-audio-1-0 | completion 0.0025 | per second → $9/hour | ≈$0.002/s (BytePlus, snip) |
| google/gemini-3.8-flash-tts (baseline) | prompt 0.0000005, completion 0.000009 | tokens | same as Google |
- OpenRouter discount field = 0 for all these endpoints — [OpenRouter endpoints API](https://openrouter.ai/api/v1/models/qwen/qwen-audio-3.0-tts-plus/endpoints) [P].

**Normalised comparison (my arithmetic; assumes 60k Russian chars/hour, 1.8 UTF-8 bytes/char; 146-char Xardas line ≈ 9.5 s)**
| Option | ≈ $/1M Russian chars | ≈ $/hour audio | ≈ $ per 146-char line |
|---|---|---|---|
| MiniMax 2.8 HD (native or OR) | 100 | 6.00 | 0.0146 (+$3 per designed voice once) |
| MiniMax 2.8 Turbo (native or OR) | 60 | 3.60 | 0.0088 |
| Fish S2.1 Pro native ($15/1M bytes × 1.8) | ~27 | ~1.62 | 0.0039 |
| Fish S2.1 Pro via OpenRouter (if per char) | 15 | 0.90 | 0.0022 |
| Fish S2.1 Pro free (to 2026-11-30) | 0 | 0 | 0 |
| Qwen-Audio-3.0 Plus (native SG or OR) | 20 | 1.20 | 0.0029 |
| Qwen-Audio-3.0 Flash (OR; native ≈ same) | 15 | 0.90 | 0.0022 |
| Seed Audio 1.0 via OR ($0.0025/s) | ~150 | 9.00 | 0.024 |
| StepAudio 2.5 (no Russian) | 85 | 5.10 | — |
| Gemini 3.8 Flash TTS baseline (25 audio tokens/s assumed) | ~14 | ~0.81 | ~0.002 |
- Cross-check for the Gemini row: AA lists Gemini 3.8 Flash TTS at $16.5/1M chars — [AA leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard) [P-ish].

### Inferences
- Fish's per-byte billing roughly doubles its effective price for Cyrillic on the native API; OpenRouter's per-character pricing may make OpenRouter *cheaper* than native for Russian Fish usage — must be verified with one real billed request (check generation cost via `X-Generation-Id`).
- At hobby volumes (e.g. 2,000 NPC lines/month ≈ 300k chars), monthly costs: Fish ~$8, Qwen ~$5–6, MiniMax Turbo ~$18 / HD ~$30, Gemini ~$4–5. All are small; quality/latency should dominate the choice.

### Gaps
- Exact Fish billing unit on OpenRouter (characters vs bytes) not confirmed.
- Official Singapore price for qwen-audio-3.0-tts-flash, Qwen-Audio-3.1 per-char equivalent, and Qwen voice-clone/design fees not found on an official page (Model Studio pricing page fetched had no TTS section).
- No free-tier info for Model Studio TTS (Alibaba usually gives new-user free quota, but not verified for these models).

---

## 8. Availability from Russia, payments, data policy, game-mod / cloned-voice terms

### Takeaway
None of these providers was found to explicitly geo-block Russia in what I read, but Russian-issued cards don't work on foreign billing; OpenRouter accepts crypto (and Russian intermediaries exist), which makes it the most practical single payment route. Fish's free tier retains data for training; Fish's open S2 weights are non-commercial only (fine for a personal hobby mod, not for a paid release). Cloning real people (including the Russian Gothic dub actors) requires consent under Fish/MiniMax rules.

### Cited Findings
- OpenRouter does not accept Russian cards; crypto (stablecoins) officially supported; Russian intermediaries (e.g. Oplatym) top up via rubles/SBP; works without VPN — [vc.ru](https://vc.ru/services/2994375-oplata-openrouter-iz-rossii) [snip]; [dtf.ru](https://dtf.ru/howto/5051854-kak-oplatit-openrouter-iz-rossii) [snip].
- Fish free S2.1 API: "Requests may be retained for model improvement"; no SLA — [Fish blog](https://fish.audio/blog/s2-1-pro-free-api/) [P]. Enterprise plan offers zero data retention — [Fish plans](https://fish.audio/plan/) [P].
- Fish commercial rights: premium subscribers may use "verified voices (that you own)" commercially; free plan "personal, non-commercial projects" (the plan page summary also showed "Commercial use allowed" on Free — internal conflict) — [Fish plans](https://fish.audio/plan/) [P].
- Fish S2 open weights: "Fish Audio Research License" — free for research/non-commercial; commercial use needs a separate licence (business@fish.audio); "open weights", not OSI open source — [Fish blog: what we mean by open source for S2](https://fish.audio/blog/what-we-mean-by-open-source-for-s2/) [snip].
- Fish consent guidance: clone only own/permitted voices, written permission, avoid public figures — [bitdoze](https://www.bitdoze.com/fish-audio-clone-voice/) [snip].
- Seed Audio: reference samples must be "authorised" — [Seed blog](https://seed.bytedance.com/blog/from-speech-to-audio-creation-introducing-the-seed-audio-1-0-audio-creation-model) [P].
- MiniMax clone docs page read did not contain an explicit consent clause — [MiniMax voice clone guide](https://platform.minimax.io/docs/guides/speech-voice-clone) [P].
- Alibaba Model Studio is region-split (Singapore international vs Beijing); international site requires account/real-name rules per region — [Model Studio Plus doc](https://www.alibabacloud.com/help/en/model-studio/qwen-audio-3-0-tts-plus) [P]; [Alibaba real-name FAQ](https://intl.aliyun.com/help/en/account/user-guide/real-name-registration-faqs) [snip]. Seed-TTS 2.0 distribution is "China-first" with Volcengine as primary and RMB pricing — [WaveSpeed](https://wavespeed.ai/ru/models/bytedance/seed-speech-tts-2.0) [snip].
- OpenRouter flags `is_moderated: false` for all these TTS models — [OpenRouter models API](https://openrouter.ai/api/v1/models?output_modalities=speech) [P].

### Inferences
- For a personal, non-commercial mod, Fish free/paid API, MiniMax, Qwen and OpenRouter all look usable; data-policy-sensitive content is low (NPC lines), so Fish's free-tier retention is acceptable.
- Practical payment path from Russia: OpenRouter (crypto) for Fish/MiniMax/Qwen; native Fish/MiniMax/Alibaba require a foreign card (Stripe-style billing assumed, not verified).
- If the mod is ever distributed publicly with pre-generated audio, check each provider's output-ownership terms; cloned voices of real actors should be avoided.

### Gaps
- No explicit statement found on whether MiniMax, Fish, Alibaba Cloud international, or BytePlus block sign-ups from Russia/Russian IPs or accept non-Russian cards issued to Russian residents.
- Full ToS text on output ownership / game use for MiniMax, Fish, Alibaba not read.
- OpenRouter's own policy on forwarding cloned-voice references (consent) not found.
