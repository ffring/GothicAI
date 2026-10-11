# Open-weight TTS for Russian NPC voices, self-hosted on one RTX 3090 (24 GB), state as of 2026-10-11

Scope: open-weight TTS that the Gothic 2 AI-NPC mod could run on "dev-ubuntu" (RTX 3090, 24 GB, Ubuntu). The mod is a free hobby project, so non-commercial licenses are acceptable but are flagged. Workload: short Russian replies of 1–3 sentences, a distinct and stable voice per NPC (for example Xardas: old, deep, commanding), with low time to first audio.

Method: GitHub metadata from `gh api` (stars and last push, fetched 2026-10-11), READMEs and HF model cards (read, not downloaded), GitHub issues, the Artificial Analysis open-weights arena, the Russian-only ESpeech/Shmyrev leaderboard (its raw `data.json` was read), and habr reviews. Nothing was downloaded or run locally.

Evidence labels used below: **[V]** means verified in a primary source (repo, model card, license, or the vendor's own report). **[S]** means a secondary claim (community issue, blog, habr review, or a competitor's benchmark table).

---

## Overview: which open-weight models are realistic for Russian NPC voices on one 3090?

### Takeaway
Five 2026-era models are the realistic shortlist:
- **Qwen3-TTS 1.7B** (Apache-2.0): has voice design, 3-second cloning and Russian; its stock inference is slow, so it needs vLLM-Omni or an optimized fork.
- **VoxCPM2 2B** (Apache-2.0): has voice design, about 8 GB VRAM and RTF about 0.13–0.3 on a 4090; Russian stress errors are known.
- **Fish Audio S2 Pro 4B**: best measured Russian WER, but it is non-commercial (hobby use allowed) and only borderline real-time on a 3090 within 24 GB.
- **F5-TTS Russian fine-tunes (ESpeech / Misha24-10)**: Russian-native, with stress control through `+` marks and RUAccent. Cloning only, no voice design.
- **Silero v5_ru**: tiny and 300× real-time on a 3090, with automatic stress. Only 5 fixed voices and no cloning, so it fits as a fallback or for background NPCs.

Many of the highest-ranked open models in 2026 don't support Russian at all: Breeze TTS 2, Voxtral TTS, Kokoro, IndexTTS-2.5, VibeVoice, Orpheus, Zonos, Dia, CSM, MegaTTS3, Spark-TTS and Kyutai.

### Cited Findings

**Master comparison** (details and sources in the sections below):

| Model (latest open version, date) | Size | License (hobby OK?) | Russian | Clone / Design / Emotion | 3090-relevant speed / VRAM | Fit |
|---|---|---|---|---|---|---|
| Fish Audio S2 Pro (2026-03-09) | 4B + 0.4B | Fish Audio Research License: non-commercial, explicitly allows "personal use (i.e., hobbyist)"; commercial use needs a separate license | Tier-2 language | Clone (10–30 s ref); inline free-form emotion tags; no timbre-from-text design documented | Vendor: H200 RTF 0.195, TTFA ~100 ms. 3090: ~6 it/s (default) to ~25 it/s (Linux + compile); w4a16 quant still >21 GB | Top quality, tight on a 3090 |
| Qwen3-TTS 12Hz 0.6B/1.7B (2026-01-22) | 0.6B / 1.7B | Apache-2.0 | Native (one of 10 languages) | 3-s clone (Base); VoiceDesign 1.7B; CustomVoice (9 voices + instruct) | Claimed 97 ms streaming latency; stock HF code reported 5–10× slower than real time; fork reaches RTF <0.25 on a 5090; vLLM-Omni OpenAI API | Best feature match; needs a serving stack |
| VoxCPM2 (2026-04) | 2B | Apache-2.0 | Native (one of 30 languages) | Clone + "controllable clone" + text voice design | RTF ~0.30 on 4090 (PyTorch), ~0.13 (Nano-vLLM); ~8 GB VRAM | Strong; stress errors |
| Fun-CosyVoice3-0.5B-2512 (2025-12) | 0.5B | Apache-2.0 | One of 9 languages | Clone + instruct (emotion, speed, volume) | Bi-streaming, latency as low as 150 ms (vendor) | Russian bugs (falls back to Chinese speech, digits read in Chinese) |
| F5-TTS RU fine-tunes: Misha24-10 F5-TTS_RUSSIAN (v2, HF updated 2026-01), ESpeech-TTS-1 RL-V2 (2025-08) | ~0.3B (F5 v1 Base) | Misha: CC-BY-NC-4.0; ESpeech card: Apache-2.0 | Russian-only fine-tunes | Clone only; stress through `+` and RUAccent | xRT 0.19 on RTX 4090 (leaderboard) | Best Russian-native cloning |
| Chatterbox Multilingual V3 (2026-06-10) | 0.5B | MIT | One of 23+ languages (no Russian single-language pack) | Clone; exaggeration / CFG knobs | Not measured | Russian accent and stress complaints |
| Higgs TTS 3 (2026-06-04) | ~4B | Boson non-commercial + "Creator Use Grant" | In the "WER/CER under 5" tier | Clone; inline emotion and style tags | Vendor numbers are on H100 | Promising, unverified on a 3090 |
| OmniVoice (2026-03-30) | n/a | Weights CC-BY-NC, code Apache-2.0 | One of 600+ languages | Clone; attribute voice design (trained on zh/en only) | RTF as low as 0.025 (vendor) | Untested for Russian |
| XTTS-v2 (2023-12) | ~0.5B | CPML (non-commercial); vendor is defunct | One of 17 languages | Clone | Leaderboard xRT 0.35 | Legacy |
| Silero v5_5_ru (v5: 2025-10-31; v5_5 in 2026) | ~140 MB | CC BY-NC-SA 4.0 | Russian-native, automatic stress, homographs, ё | 5 fixed speakers, SSML | 300–350× real time on an RTX 3090 | Fallback, background NPCs |
| Piper (ru_RU: irina, denis, dmitri, ruslan) | small | piper1-gpl GPL-3.0 | Native voices | Fixed voices | CPU xRT 0.045 | Fallback |
| Vosk-TTS 0.9 (2025-06) | small | Apache-2.0 | Russian-native multi-speaker | Multi-speaker (SIM 0.875) | GPU xRT 0.07 | Niche fallback |

- [V] On the Artificial Analysis open-weights arena the top 10 are: Breeze TTS 2 (Elo 1222, Aug 2026), Fish Audio S2 Pro (1116, Mar 2026), Step Audio EditX (1096), Voxtral TTS (1086, Mar 2026), Kokoro 82M v1.0 (1064), NVIDIA Magpie-Multilingual 357M (1062), Maya1 (1046), OpenAudio S1 Mini (1044), Higgs Audio V3 TTS (1036, Jun 2026) and Chatterbox (1026). The page shows no language methodology, so the ranking is probably English-centric. — [Artificial Analysis open-weights leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard/provider-voice/open-weights)
- [V] Of that top 10, these have **no Russian**:
  - Breeze TTS 2: en, zh. — [HF card](https://huggingface.co/BreezeBlue/Breeze-TTS-2)
  - Voxtral TTS: en, fr, es, pt, it, nl, de, ar, hi. — [HF card](https://huggingface.co/mistralai/Voxtral-4B-TTS-2603)
  - Kokoro: en, es, fr, hi, it, ja, pt-br, zh. — [hexgrad/kokoro README](https://github.com/hexgrad/kokoro)
  - Magpie: 12 languages without ru. — [HF card](https://huggingface.co/nvidia/magpie_tts_multilingual_357m)
  - Step-Audio-EditX: Russian is still an unchecked TODO. — [README](https://github.com/stepfun-ai/Step-Audio-EditX)
- [V] The only Russian-specific open leaderboard is "Open TTS Leaderboard Ru" by Nikolay Shmyrev and Denis Petrov. It uses CER, UTMOS, speaker SIM, FAD and xRT. The Space was last modified 2026-06-03, but its newest entries date from Aug 2025. It does **not** include Qwen3-TTS, Fish S2, VoxCPM2, CosyVoice3 or Chatterbox. — [ESpeech/open_tts_leaderboard_ru](https://huggingface.co/spaces/ESpeech/open_tts_leaderboard_ru)

### Inferences
- The model that meets all of the NPC needs (Russian, a timbre built from a text description like "old deep commanding necromancer", stable reuse across lines, Apache license, fits easily in 24 GB) is **Qwen3-TTS VoiceDesign, then Base clone**. **VoxCPM2** is the closest alternative. Fish S2 Pro is the quality ceiling but costs most of the 3090 and has the worst latency risk.
- A practical hybrid: use Qwen3-TTS VoiceDesign, VoxCPM2 or the Gemini cloud baseline to *design* each NPC's reference clip once offline. Then serve a fast cloning model (Qwen3-TTS Base, VoxCPM2, or a Russian F5 fine-tune for best stress control) at runtime.

### Gaps
- No independent 2026 Russian benchmark covers the new models (Qwen3-TTS, Fish S2, VoxCPM2, Higgs TTS 3, OmniVoice) on the same test set. The best cross-model Russian numbers come from VoxCPM2's own README, which is a competitor-run evaluation.
- No source gives measured time to first audio on an RTX 3090 for any of the 2026 LLM-style models.

---

## Q1. Release date, version and license (free hobby mod context)

### Takeaway
Apache-2.0 or MIT, commercial-safe: Qwen3-TTS, VoxCPM2, CosyVoice3, Chatterbox, ESpeech (per its card) and Vosk-TTS.

Non-commercial, still fine for a free hobby mod but with attribution and redistribution duties:
- Fish S2 Pro and S1-mini
- Misha24-10 F5 and the official F5-TTS weights (CC-BY-NC)
- Silero (CC BY-NC-SA)
- XTTS-v2 (CPML; its licensor no longer exists)
- Higgs TTS 3, OmniVoice weights, Voxtral, Breeze

### Cited Findings
- [V] **Fish Audio S2 Pro**: HF repo created 2026-03-09 ([HF](https://huggingface.co/fishaudio/s2-pro)); GitHub release `v2.0.0-beta` "Fish Audio S2 Beta" on 2026-03-10 ([releases](https://github.com/fishaudio/fish-speech/releases)). Code and weights use the **Fish Audio Research License** (last updated 2026-03-07). It grants free "Research & Non-Commercial" use and defines Non-Commercial to include "personal use (i.e., hobbyist)". Any commercial use (product, hosted service, API) needs a separate written license. Redistribution requires a copy of the license plus a "Notice" attribution file. Outputs may not be used to train other foundation models. — [LICENSE](https://github.com/fishaudio/fish-speech/blob/main/LICENSE)
- [V] **OpenAudio S1-mini** (2025-05-31): CC-BY-NC-SA-4.0, gated with auto-approval. — [HF s1-mini](https://huggingface.co/fishaudio/s1-mini)
- [V] **Qwen3-TTS**: released 2026-01-22 as 0.6B and 1.7B models on the 12Hz tokenizer. Variants: 1.7B-VoiceDesign, 1.7B/0.6B-CustomVoice, 1.7B/0.6B-Base. Apache-2.0. The README says other models from the tech report (for example the 25Hz ones) "will be released in the near future". — [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), [HF VoiceDesign](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)
- [V] **VoxCPM2**: 2B, released 2026.04 (HF created 2026-04-03), trained on more than 2M hours, 48 kHz output. Weights and code are Apache-2.0, "free for commercial use". Tech report: arXiv 2606.06928. Predecessors: VoxCPM-0.5B (2025-09) and VoxCPM1.5 (2025-12), both zh/en only. — [OpenBMB/VoxCPM](https://github.com/OpenBMB/VoxCPM), [HF](https://huggingface.co/openbmb/VoxCPM2)
- [V] **Fun-CosyVoice 3.0**: `Fun-CosyVoice3-0.5B-2512` (base and RL checkpoints) released 2025-12, Apache-2.0. The repo now lives at QwenAudio/CosyVoice (formerly FunAudioLLM). A CosyVoice3-1.5B appears in evals, but only the 0.5B is downloadable. — [QwenAudio/CosyVoice](https://github.com/QwenAudio/CosyVoice), [HF](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)
- [V] **F5-TTS**: v1 base model 2025-03-12. Code is MIT, but "the pre-trained models are licensed under the CC-BY-NC license due to the training data Emilia". — [SWivid/F5-TTS](https://github.com/SWivid/F5-TTS)
- [V] **Misha24-10/F5-TTS_RUSSIAN**: CC-BY-NC-4.0, base model SWivid/F5-TTS; HF last modified 2026-01-13. — [HF](https://huggingface.co/Misha24-10/F5-TTS_RUSSIAN)
- [V] **ESpeech-TTS-1_RL-V2**: created 2025-08-25; its card says Apache-2.0; uses the F5-TTS architecture (`f5_tts` DiT config). — [HF](https://huggingface.co/ESpeech/ESpeech-TTS-1_RL-V2)
- [V] **Chatterbox**: MIT. "v3 multilingual and single language pack release" committed 2026-06-10; Turbo (350M, English) 2025-12-15. Output carries an imperceptible PerTh watermark. — [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox), [commits](https://github.com/resemble-ai/chatterbox/commits/master)
- [V] **XTTS-v2**: `coqui-public-model-license` (CPML); HF weights last modified 2023-12-11 ([HF](https://huggingface.co/coqui/XTTS-v2)). The idiap fork calls the original repo "unmaintained" ([idiap/coqui-ai-TTS](https://github.com/idiap/coqui-ai-TTS)). [S] Coqui the company shut down around Dec 2023 – Jan 2024, so no commercial XTTS license can be bought; CPML permits non-commercial and hobby use. — [coqui-ai/TTS discussion #4145](https://github.com/coqui-ai/TTS/discussions/4145)
- [V] **Silero models**: the repo LICENSE is CC BY-NC-SA 4.0 (the README calls it "CC-NC-BY"); only the `v5_cis_base` models are MIT. — [LICENSE](https://github.com/snakers4/silero-models/blob/master/LICENSE), [README](https://github.com/snakers4/silero-models). [S] The habr/Raft review summarizes Silero as "free only for educational purposes". — [habr Raft](https://habr.com/ru/companies/raft/articles/991844/)
- [V] **Silero v5_ru** release article dated 2025-10-31. — [habr 961930](https://habr.com/ru/articles/961930/). Latest is `v5_5_ru` (adds speaker `eugene` and questions). — [README](https://github.com/snakers4/silero-models)
- [V] **Piper**: rhasspy/piper is archived (MIT); development continues in OHF-Voice/piper1-gpl under **GPL-3.0** (pushed 2026-10-11). Russian voices on HF: `denis`, `dmitri`, `irina`, `ruslan`. — [rhasspy/piper](https://github.com/rhasspy/piper), [piper1-gpl](https://github.com/OHF-Voice/piper1-gpl), [piper-voices ru_RU](https://huggingface.co/rhasspy/piper-voices/tree/main/ru/ru_RU)
- [V] **Vosk-TTS** (alphacep/vosk-tts): Apache-2.0, 274 stars, pushed 2026-06-06. — [GitHub](https://github.com/alphacep/vosk-tts)
- [V] **Higgs TTS 3 4B** (HF created 2026-06-04) uses the "Boson Higgs TTS 3 Research and Non-Commercial License". Production, hosted APIs or embedding in a product need a commercial license. A "Creator Use Grant" allows monetized podcasts, videos and posts if credited. — [HF](https://huggingface.co/bosonai/higgs-tts-3-4b). The Higgs Audio v2 GitHub code is Apache-2.0. — [boson-ai/higgs-audio](https://github.com/boson-ai/higgs-audio)
- [V] **OmniVoice** (k2-fsa, HF 2026-03-30): code Apache-2.0, "pre-trained model is licensed under the CC-BY-NC due to … Emilia". — [HF](https://huggingface.co/k2-fsa/OmniVoice)
- [V] **Supertonic 3** (HF 2026-05-06): model OpenRAIL-M, sample code MIT; the original GitHub repo is archived as `supertone-oss-archive/supertonic`. — [HF](https://huggingface.co/Supertone/supertonic-3), [GitHub](https://github.com/supertone-oss-archive/supertonic)
- [V] Other licenses:
  - Voxtral-4B-TTS-2603: CC-BY-NC-4.0. — [HF](https://huggingface.co/mistralai/Voxtral-4B-TTS-2603)
  - Breeze TTS 2: research and non-commercial. — [HF](https://huggingface.co/BreezeBlue/Breeze-TTS-2)
  - IndexTTS-2.5 (2026-08-10): `bilibili-model-license`. — [HF](https://huggingface.co/IndexTeam/IndexTTS-2.5)
  - ChatTTS: AGPL-3.0. — [GitHub](https://github.com/2noise/ChatTTS)
  - Kokoro: Apache-2.0. — [HF](https://huggingface.co/hexgrad/Kokoro-82M)
  - MOSS-TTSD v1.0 (2026-02-10): Apache-2.0. — [GitHub](https://github.com/OpenMOSS/MOSS-TTSD)
  - Tencent AuK (2026-08-18): MIT. — [HF](https://huggingface.co/tencent/AuK)

### Inferences
- For a free, non-monetized mod, every license above is usable except where it would be redistributed inside a paid product. Watch three points:
  - Fish's license is "revocable" and requires shipping a Notice file if the mod redistributes model files. Running the model on the user's own server and shipping only audio avoids redistribution.
  - Silero's NC-**SA** share-alike applies to derivatives of the model, not to generated audio, but this has not been legally verified.
  - XTTS's CPML has no living licensor, which is a long-term risk.
- If the mod might ever be monetized (Patreon, donations tied to features), the Apache/MIT set (Qwen3-TTS, VoxCPM2, CosyVoice3, Chatterbox) avoids future relicensing work.

### Gaps
- Whether ESpeech-TTS-1 was trained from scratch or fine-tuned from the CC-BY-NC F5 base is not stated on its card, so its Apache-2.0 label may be shaky.
- No MiniMax open-weight TTS was found (the GitHub repo `MiniMax-AI/MiniMax-Speech` returns 404). MiniMax appears in vLLM-Omni only as "MiniMax H3 joint video/audio generation" ([vllm-omni](https://github.com/vllm-project/vllm-omni)). Treat it as "no open TTS weights found".

---

## Q2. Russian support: native vs fine-tune, quality evidence, stress (ударения)

### Takeaway
- **Best measured Russian intelligibility among multilingual models:** Fish S2 Pro, then Qwen3-TTS 1.7B, then VoxCPM2, all ahead of ElevenLabs and MiniMax on one shared test set.
- **Most reliable stress control:** the Russian-native stacks (F5 fine-tunes with `+` marks and RUAccent, Silero with built-in automatic stress).
- **Known weak spots:** stress errors in VoxCPM2 and Qwen3-TTS; non-native accent in Chatterbox and Qwen3 CustomVoice; Russian bugs in CosyVoice3.

### Cited Findings
**Cross-model Russian numbers (multilingual models):**
- [S, competitor-run] MiniMax-Multilingual test set, Russian WER (lower is better): MiniMax 4.281, ElevenLabs 3.878, Qwen3-TTS 3.212, Fish Audio S2 **2.400**, VoxCPM2 3.634. Russian speaker SIM: 76.1 / 67.6 / 79.2 / 79.0 / **81.1** (VoxCPM2 best). — [VoxCPM README](https://github.com/OpenBMB/VoxCPM)
- [S] CV3-eval Russian WER: CosyVoice3-1.5B 6.64, Fish Audio S2 2.78, VoxCPM2 5.21. — [VoxCPM README](https://github.com/OpenBMB/VoxCPM)
- [S] VoxCPM's internal 30-language ASR benchmark (500 samples per language, transcribed with Gemini 3.1 Flash Lite): Russian WER VoxCPM2 0.90% vs Fish S2-Pro 0.86%. — [VoxCPM README](https://github.com/OpenBMB/VoxCPM)
- [V] Qwen3-TTS's own multilingual table, Russian WER: 25Hz-0.6B 5.957, 25Hz-1.7B 4.535, 12Hz-0.6B 4.458, **12Hz-1.7B 3.212**, MiniMax 4.281, ElevenLabs 3.878. This matches the number VoxCPM cites. — [Qwen3-TTS README](https://github.com/QwenLM/Qwen3-TTS)
- [V] Fish S2 lists Russian as a "Tier 2" language (Tier 1 is ja/en/zh) and says it needs no phonemes or language-specific preprocessing. — [fish-speech README](https://github.com/fishaudio/fish-speech)

**Russian-only leaderboard** (Shmyrev/Petrov; CER %, UTMOS higher is better, SIM, xRT): — [Open TTS Leaderboard Ru data](https://huggingface.co/spaces/ESpeech/open_tts_leaderboard_ru)
- [V] Voice-cloning class:
  - ESpeech-TTS-1 [RL] V2: CER 1.4, UTMOS 3.265, SIM 0.833, xRT 0.19 on an RTX 4090.
  - ESpeech PODCASTER [SFT]: CER 2.5, UTMOS 3.294.
  - FishSpeech 1.5: CER 1.5, UTMOS 3.183, SIM 0.757, xRT **1.7**, i.e. slower than real time.
  - F5-TTS Misha V2: CER 3.0, UTMOS 3.151, SIM 0.821, xRT 0.19.
  - XTTS2: CER 2.7, UTMOS 3.035, SIM 0.762 (flagged "outdated data").
  - Cloud references: ElevenLabs Multilingual V2 CER 1.2, UTMOS 3.309; MWS AI CER 0.8, UTMOS 3.313.
- [V] Multi-speaker: Vosk-TTS 0.9 CER 0.6, UTMOS 3.29, SIM 0.875, GPU xRT 0.07.
- [V] Single-speaker:
  - Piper Irina: CER 1.4, UTMOS 3.672, CPU xRT 0.045.
  - Piper Ruslan: CER 3.0, UTMOS 2.975.
  - Silero v3.1 and v4 (old versions): UTMOS 1.76–2.98. **v5 is not evaluated.**

**habr reviews (Russian practitioners):**
- [S] habr/Raft review "Обзор Open Source моделей для задачи TTS" by D. Muzafarov, tested on an RTX 5070 and Ryzen 7500F. The page date reads 3 Feb; the fetch tool showed 2025, but the article reviews Qwen3-TTS, released 2026-01-22, so it is early 2026. — [habr 991844](https://habr.com/ru/companies/raft/articles/991844/)
  - ESpeech: "excellent", handles stress marks; 4.5/5 naturalness, 4/5 expressiveness.
  - F5-TTS fine-tune (v1_Base_v4_winter): 4.5/5 and 5/5.
  - Qwen3-TTS: CustomVoice has a noticeable accent; VoiceDesign is "decent" but prompts must be in English; Base cloning is "excellent"; 4.5/5 and 5/5.
  - Silero v5_ru: 4/5 and 4/5, built-in stress.
  - Chatterbox: 3/5, stress and intonation issues.
  - XTTS-v2: 3/5.
  - Higgs Audio (v2): 1/5, "strong accent".
- [S] habr Qwen3-TTS review with Russian examples. — [habr 988670](https://habr.com/ru/articles/988670/)
  - "ударения иногда расставляются неправильно" (stress is sometimes placed wrongly).
  - Trouble with long texts.
  - VoiceDesign instructions work better in English.
  - Needs an NVIDIA GPU with 8+ GB.

**Per-model stress and accent evidence:**
- [V] **Misha24-10 F5-TTS_RUSSIAN**: trained on 5,000+ hours (4,000 h custom Russian, Common Voice RU/EN, Sova, LibriHeavy). Stress is controlled by `+` before the stressed vowel (`молок+о`); it recommends **RUAccent** for automatic stress. The `v1_Base_accent_tune` and `v2` checkpoints are trained with full stress markup. — [HF](https://huggingface.co/Misha24-10/F5-TTS_RUSSIAN), [RUAccent](https://github.com/Den4ikAI/ruaccent)
- [V] **ESpeech**'s reference pipeline installs and calls `ruaccent`. — [HF card](https://huggingface.co/ESpeech/ESpeech-TTS-1_RL-V2)
- [V] **Silero v5_5_ru**: automatic stress, homograph disambiguation and questions are all supported; v5 also restores ё automatically. — [README](https://github.com/snakers4/silero-models), [habr 961930](https://habr.com/ru/articles/961930/)
- [S] **VoxCPM2** issue #312 (open, 2026-05): Russian words with wrong stress (баловать, балую, алгебра, сосредоточение). The maintainer says manual stress marks are not supported and Russian training data "may be relatively limited". A commenter says fine-tuning helps and "one of the latest models doing well with stress marks for Russian is Higgs TTS v3". — [VoxCPM #312](https://github.com/OpenBMB/VoxCPM/issues/312)
- [S] **Chatterbox** (V2 era):
  - "Russian TTS quality degrades after multiple generations": an English accent appears and stress becomes wrong. Dec 2025 comment: "Russian audio quality is unacceptable… crazy non-russian pronunciation". — [#360](https://github.com/resemble-ai/chatterbox/issues/360)
  - Numbers are mispronounced. — [#284](https://github.com/resemble-ai/chatterbox/issues/284), [#358](https://github.com/resemble-ai/chatterbox/issues/358)
  - Й and Ё are cut off. — [#302](https://github.com/resemble-ai/chatterbox/issues/302)
  - [V] The `russian-text-stresser` dependency was made optional and then removed in Dec 2025. — [commits](https://github.com/resemble-ai/chatterbox/commits/master)
  - [V] V3 claims reduced hallucination and better accent preservation, but there is **no Russian single-language pack** (packs exist for zh, es-MX, pt-BR, es-ES, pt-PT, hi). — [README](https://github.com/resemble-ai/chatterbox)
- [S] **Fun-CosyVoice3-0.5B-2512**:
  - Outputs **Chinese speech instead of Russian** in all inference modes. The suggested `<|endofprompt|>` fix did not help one reporter, and the issue went stale. — [#1790](https://github.com/QwenAudio/CosyVoice/issues/1790)
  - Russian digits are converted to Chinese. — [#1779](https://github.com/QwenAudio/CosyVoice/issues/1779)
- [S] **Fish**:
  - S1-mini Russian is "very poor"; "official S1 in web version is normal. S1-Mini local is unusable" (2025). — [#1032](https://github.com/fishaudio/fish-speech/issues/1032)
  - A community tip for stress is to write accented vowel characters (á, ó, и́…) directly. — [#791](https://github.com/fishaudio/fish-speech/issues/791)
- [V] **Higgs TTS 3** lists Russian in its "WER/CER under 5 — polished, production-quality (85)" tier. — [HF](https://huggingface.co/bosonai/higgs-tts-3-4b)
- [V] **OmniVoice** lists 600+ languages including Russian. Its voice design "is trained on Chinese and English data only… may produce unstable results" for other languages. — [OmniVoice README](https://github.com/k2-fsa/OmniVoice)
- [V] Russian is listed for:
  - Supertonic 3 (one of 31 languages). — [HF](https://huggingface.co/Supertone/supertonic-3)
  - MOSS-TTSD (one of 20 languages, dialogue-oriented). — [GitHub](https://github.com/OpenMOSS/MOSS-TTSD)
  - XTTS-v2 (one of 17 languages). — [idiap](https://github.com/idiap/coqui-ai-TTS)
- [V] **No Russian**:
  - Kokoro. — [README](https://github.com/hexgrad/kokoro)
  - IndexTTS-2.5 (zh/en/ja/es/ar). — [README](https://github.com/index-tts/index-tts)
  - VibeVoice-Realtime-0.5B (English plus experimental de/fr/it/ja/ko/nl/pl/pt/es voices). — [README](https://github.com/microsoft/VibeVoice)
  - Orpheus multilingual (zh, hi, ko, es/it, fr, de). — [HF collection](https://huggingface.co/collections/canopylabs/orpheus-multilingual-research-release-67f5894cd16794db163786ba)
  - Zonos v0.1 (en/ja/zh/fr/de). — [README](https://github.com/Zyphra/Zonos)
  - Dia ("only supports English"). — [README](https://github.com/nari-labs/dia)
  - Sesame CSM (non-English "likely won't do well"). — [README](https://github.com/SesameAILabs/csm)
  - MegaTTS3 and Spark-TTS (zh/en bilingual). — [MegaTTS3](https://github.com/bytedance/MegaTTS3), [Spark-TTS](https://github.com/SparkAudio/Spark-TTS)
  - Kyutai Pocket TTS (en/fr/de/pt/it/es/nl). — [README](https://github.com/kyutai-labs/pocket-tts)
  - Step-Audio-EditX (Russian is an unchecked TODO). — [README](https://github.com/stepfun-ai/Step-Audio-EditX)

### Inferences
- For Gothic-style dialogue (archaic words, names like Ксардас and Миненталь), stress errors are the dominant risk with the multilingual LLM-TTS models. A text pre-pass helps where the model accepts marks: run RUAccent (or a small per-NPC stress dictionary for game terms), then emit `+` marks (F5 fine-tunes) or accented vowel characters (Fish, community tip). VoxCPM2 and Qwen3-TTS document **no** manual stress input, so they need fine-tuning or respelling.
- WER/CER does not catch wrong stress: a misplaced stress is still transcribed correctly by ASR. So the low Russian WERs above do not settle the ударения question; native listening tests are needed.

### Gaps
- No independent Russian stress-accuracy metric exists for any 2026 model.
- Higgs TTS 3 and OmniVoice Russian quality has no Russian community review yet; only the Higgs tier list and one GitHub comment are available.
- Russian quality of Chatterbox Multilingual V3 (2026-06) after its "reduced hallucination" update has no review yet.

---

## Q3. Voice cloning, voice design by text prompt, emotion/style control

### Takeaway
Text-prompt **voice design** with Russian output is available open-weight in Qwen3-TTS 1.7B-VoiceDesign and VoxCPM2 (best documented), plus OmniVoice (attribute-based; zh/en-trained, unstable elsewhere). Breeze TTS 2 also has it but no Russian. Fish S2 Pro and Higgs TTS 3 offer the richest inline emotion tags but rely on reference-audio cloning for timbre. Qwen3-TTS documents exactly the "design once, then clone for consistency" workflow an NPC system needs.

### Cited Findings
- [V] **Qwen3-TTS**:
  - Base clones from a 3-second reference (audio plus transcript).
  - VoiceDesign creates a voice from a natural-language `instruct`.
  - CustomVoice has 9 premium timbres with instruction-based style control.
  - Tone, rate and emotion adapt to instructions and text semantics.
  - README workflow for "a consistent character voice across many lines": (1) generate a short reference with VoiceDesign, (2) build a reusable prompt with `create_voice_clone_prompt`, (3) call `generate_voice_clone`.
  - [Qwen3-TTS README](https://github.com/QwenLM/Qwen3-TTS)
- [V] **VoxCPM2**:
  - Voice Design from a description (gender, age, tone, emotion, pace), written as `"(description)text"`.
  - Controllable Cloning: a short reference plus style guidance.
  - "Ultimate Cloning": reference plus transcript continuation.
  - The README admits "Voice Design and Controllable Voice Cloning results can vary between runs — you may try to generate 1~3 times".
  - [VoxCPM README](https://github.com/OpenBMB/VoxCPM)
- [V] **Fish S2 Pro**:
  - Cloning from "short reference samples (typically 10-30 seconds)".
  - "15,000+ unique tags", free-form inline tags such as `[whisper]`, `[angry]`, `[low voice]`, `[shouting]`, `[pitch up]`.
  - Multi-speaker and multi-turn generation.
  - [fish-speech README](https://github.com/fishaudio/fish-speech)
- [V] **CosyVoice3**: zero-shot multilingual and cross-lingual cloning; instruct support for language, dialect, emotion, speed, volume. — [README](https://github.com/QwenAudio/CosyVoice)
- [V] **Chatterbox**:
  - Zero-shot cloning.
  - `exaggeration` and `cfg_weight` knobs; for dramatic speech the README suggests `cfg_weight≈0.3`, `exaggeration≈0.7`.
  - The reference clip should match the language tag, otherwise the output inherits the reference's accent.
  - Paralinguistic tags (`[laugh]`, `[cough]`) only in the English Turbo model.
  - [README](https://github.com/resemble-ai/chatterbox)
- [V] **Higgs TTS 3**: zero-shot cloning; inline emotion tags (anger, determination, pride, contempt-like `bitterness`, …), style, prosody, pauses, sound effects. — [HF](https://huggingface.co/bosonai/higgs-tts-3-4b)
- [V] **OmniVoice**: cloning (same-language reference recommended, otherwise an accent carries over); voice design through attributes (`"male, low pitch, british accent"`). — [README](https://github.com/k2-fsa/OmniVoice)
- [V] **F5 Russian fine-tunes**: cloning only (reference audio plus its text), stress through `+`; no design or emotion tags. — [Misha24-10 HF](https://huggingface.co/Misha24-10/F5-TTS_RUSSIAN), [ESpeech HF](https://huggingface.co/ESpeech/ESpeech-TTS-1_RL-V2)
- [V] **Silero v5**: 5 fixed Russian speakers (aidar, baya, kseniya, xenia, eugene); SSML prosody; no cloning. — [README](https://github.com/snakers4/silero-models), [habr 961930](https://habr.com/ru/articles/961930/)
- [V] **Breeze TTS 2**: Voice Clone, Voice Design, and "Voice Direction" (clone plus tone and emotion steering), but en/zh only. — [HF](https://huggingface.co/BreezeBlue/Breeze-TTS-2)
- [S] habr/Raft: Qwen3 VoiceDesign prompts must be in English. Qwen3 Base cloning of Russian is "excellent"; CustomVoice preset voices speak Russian with an accent. — [habr 991844](https://habr.com/ru/companies/raft/articles/991844/)

### Inferences
- For Xardas-type personas, use English VoiceDesign prompts ("elderly male, very deep, slow, commanding, slightly raspy, ominous") to generate a Russian reference line once. Store it as the NPC's reference and clone from it at runtime. This avoids VoxCPM2/Qwen3 run-to-run design variance and fixes timbre across a play session.
- A cloud-designed reference (Gemini Voice Design, or any licensed recording) can feed any of the cloning models, which decouples "design" from the self-hosted "speak" step. Licensing of the reference voice is the user's responsibility.

### Gaps
- No source compares how well these models render "old or deep" male timbres specifically in Russian.
- Fish S2 Pro has no documented text-to-timbre voice design; only style tags were found.

---

## Q4. Speed on RTX 3090: RTF, time to first audio, streaming, VRAM, serving

### Takeaway
On one 3090:
- **Comfortable:** Silero (300× real time), Piper, Vosk, the F5 Russian fine-tunes (xRT about 0.19 on a 4090, a few GB), VoxCPM2 (about 8 GB, RTF 0.13–0.3 on a 4090) and Qwen3-TTS (only if served through vLLM-Omni or an optimized fork).
- **Borderline:** Fish S2 Pro (needs about 21–24 GB; roughly real time at best on Linux with `--compile`).

**vLLM-Omni** has become the common OpenAI-compatible `/v1/audio/speech` server for Qwen3-TTS, VoxCPM2, Fish S2 Pro, Higgs TTS 3, OmniVoice, IndexTTS, CosyVoice3 and Voxtral.

### Cited Findings
**Fish S2 Pro**
- [V] Vendor: single H200 with SGLang, RTF 0.195, TTFA about 100 ms, 3,000+ acoustic tokens/s; Dual-AR runs at about 21 Hz with 10 codebooks; SGLang-Omni and vLLM-Omni recipes. — [fish-speech README](https://github.com/fishaudio/fish-speech)
- [V] The vLLM-Omni recipe is tested on an A800 80GB (loads at about 48.3 GiB, peaks at about 48.9 GiB) and on 2×H100. — [vLLM-Omni Fish S2 Pro recipe](https://github.com/vllm-project/vllm-omni/blob/main/recipes/fishaudio/Fish-Speech-S2-Pro.md)
- [S] RTX 3090 community reports:
  - "slow on my 3090, even when using the `--compile` flag… between 4 and 5 it/s" (Windows); moving to Linux/WSL2 with Triton/Inductor gave "around 25 it/s".
  - The community w4a16 quant "still uses more than 21 GB VRAM"; "Why can't a 4B model run on a 4090?".
  - [#1168](https://github.com/fishaudio/fish-speech/issues/1168)
- [S] Ubuntu 24.04, RTX 3090 24 GB, torch 2.10: "multi-speaker ~6 it/s". — [#1212](https://github.com/fishaudio/fish-speech/issues/1212)
- [S] A third-party skill page states ≥24 GB VRAM is required and recommends A800/H200. — [skills.sh fish-speech](https://www.skills.sh/openlark/skills/fish-speech)

**Qwen3-TTS**
- [V] Claims "end-to-end synthesis latency as low as 97ms" with Dual-Track streaming (first packet after one character). vLLM-Omni has day-0 support. — [README](https://github.com/QwenLM/Qwen3-TTS)
- [V] vLLM-Omni recipe:
  - OpenAI-compatible `/v1/audio/speech`, with raw PCM streaming (`stream=true`, `stream_format="audio"`).
  - On an RTX 4090 with 0.6B CustomVoice, about 13.5 GiB is used at idle with the default config (`gpu_memory_utilization: 0.3` per stage; tunable).
  - Non-streaming serving discards incomplete audio and retries.
  - [vLLM-Omni Qwen3-TTS recipe](https://github.com/vllm-project/vllm-omni/blob/main/recipes/Qwen/Qwen3-TTS.md)
- [S] Stock `qwen-tts` performance:
  - Issue "Very slow inference on 5090": "10 times slower than realtime".
  - RTX 4070 Ti Super: 5 s per 1 s of audio. RTX 3060: 5 s per 1 s, with 0.6B the same as 1.7B.
  - A fork with torch.compile and CUDA graphs reports "ttfb ~0.08s on a 5090, RTF < 0.25" but has chunk-merge clicking artifacts.
  - An early vLLM-Omni user reported RTF about 1.6 on a 4090.
  - [#89](https://github.com/QwenLM/Qwen3-TTS/issues/89), [dffdeeq/Qwen3-TTS-streaming](https://github.com/dffdeeq/Qwen3-TTS-streaming), [#171](https://github.com/QwenLM/Qwen3-TTS/issues/171)
- [S] habr/Raft on an RTX 5070: Qwen VoiceDesign took 15.0 s on GPU for the test phrase; Silero took 0.071 s; F5 fine-tune 2.32 s; ESpeech 3.76 s; XTTS-v2 18.54 s; Higgs v2 50.36 s. — [habr 991844](https://habr.com/ru/companies/raft/articles/991844/)

**VoxCPM2**
- [V] RTF about 0.30 on an RTX 4090 (PyTorch) and about 0.13 with Nano-vLLM-VoxCPM (batched, async, FastAPI); VRAM about 8 GB; streaming API (`generate_streaming`); GGUF via llama.cpp-omni. — [VoxCPM README](https://github.com/OpenBMB/VoxCPM), [nanovllm-voxcpm](https://github.com/a710128/nanovllm-voxcpm)
- [V] vLLM-Omni recipe: a single RTX 4090 24 GB is the "minimum recommended"; about 4.9 GiB weights, about 15.2 GiB KV cache (configurable), about 2 GiB buffers. — [vLLM-Omni VoxCPM2 recipe](https://github.com/vllm-project/vllm-omni/blob/main/recipes/OpenBMB/VoxCPM2.md)

**Other models**
- [V] **CosyVoice3**: bi-streaming (text in, audio out), "latency as low as 150ms"; vLLM support for CosyVoice2; CosyVoice3 is listed among vLLM-Omni TTS models. — [CosyVoice README](https://github.com/QwenAudio/CosyVoice), [vllm-omni](https://github.com/vllm-project/vllm-omni)
- [V] **Silero v5**: measured on an **RTX 3090** with an i9-10940X: GPU 300–350× real time (250–300× with stress and homographs); CPU single-thread 37–42×; model about 140 MB. The article uses inverted RTF, i.e. times faster than real time. — [habr 961930](https://habr.com/ru/articles/961930/)
- [V] **Russian leaderboard xRT** (lower is better):
  - ESpeech and F5 Misha 0.19 on an RTX 4090
  - Vosk-TTS 0.07 GPU
  - XTTS2 0.35
  - FishSpeech 1.5: 1.7
  - Piper 0.045 CPU
  - [leaderboard](https://huggingface.co/spaces/ESpeech/open_tts_leaderboard_ru)
- [V] **XTTS-v2** "can stream with <200ms latency". — [idiap README](https://github.com/idiap/coqui-ai-TTS)
- [V] **Higgs TTS 3**:
  - Serving via SGLang-Omni (continuous batching) or vLLM-Omni (OpenAI-compatible).
  - Benchmarks are on 1×H100 only.
  - In vLLM-Omni the talker (about 4B) uses about 60% of GPU memory and the codec about 25% (fractions of an 80 GB H100).
  - [HF](https://huggingface.co/bosonai/higgs-tts-3-4b), [vLLM-Omni Higgs recipe](https://github.com/vllm-project/vllm-omni/blob/main/recipes/BosonAI/Higgs-Audio-V3-TTS.md)
- [S] Higgs Audio v2 needed ≥24 GB VRAM. — [habr 991844](https://habr.com/ru/companies/raft/articles/991844/)
- [V] **OmniVoice**: "RTF as low as 0.025"; vLLM-Omni recipe (MI300X run: RTF 0.19 on the first request). — [README](https://github.com/k2-fsa/OmniVoice), [recipe](https://github.com/vllm-project/vllm-omni/blob/main/recipes/k2-fsa/OmniVoice.md)
- [V] **Voxtral TTS** (no Russian), for reference: on a single H200, 70 ms latency and RTF 0.103 at concurrency 1; runs on ≥16 GB. — [HF](https://huggingface.co/mistralai/Voxtral-4B-TTS-2603)
- [V] **Breeze TTS 2** (no Russian): about 7.7 GiB eager, TTFA under 40 ms on an H100. — [HF](https://huggingface.co/BreezeBlue/Breeze-TTS-2)
- [V] **vLLM-Omni** (7,119 stars, pushed 2026-10-11) has recipe folders for fishaudio, OpenBMB, BosonAI, k2-fsa, IndexTeam, Qwen, Tencent, mistralai, BreezeBlue and others. Its stable releases track even vLLM minors (0.26.0 in 2026-08). — [vllm-omni](https://github.com/vllm-project/vllm-omni)
- [V] **OpenAI-compatible wrappers** for older models: Chatterbox-TTS-Server (devnen, 1,468 stars, CUDA/ROCm/CPU) and Kokoro-FastAPI (5,527 stars). — [Chatterbox-TTS-Server](https://github.com/devnen/Chatterbox-TTS-Server), [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI)

### Inferences
- **Fish S2 Pro on a 3090:** at about 21 frames/s of audio, real time needs about 21 AR steps/s. The reported 3090 rates (~6 it/s default, ~25 it/s on Linux with compile) mean RTF of about 3.5 down to about 0.85 at best. Combined with more than 21 GB VRAM, nothing else (ASR, a second model) could share the card. This is an estimate from community it/s numbers, not a measured RTF.
- **Qwen3-TTS and VoxCPM2:** RTX 3090 (Ampere) supports bf16 and FlashAttention 2 but lacks FP8 and has lower throughput than the 4090. Expect the 4090 RTFs (VoxCPM2 0.13–0.3) to be somewhat worse but still under 1 for 1–3-sentence replies.
- **Latency budget:** for 1–3-sentence NPC lines (about 3–10 s of audio), time to first audio matters more than RTF. Streaming-capable stacks (Qwen3-TTS via vLLM-Omni, VoxCPM2 `generate_streaming` or Nano-vLLM, CosyVoice3 bi-streaming, XTTS) can start playback within a few hundred ms; non-streaming F5 must finish the whole line first (about 0.19 × duration ≈ 1–2 s).
- **VRAM headroom:** the 24 GB lets the user co-host two models, for example VoxCPM2 (~8 GB) plus Silero (~0.14 GB), or Qwen3-TTS 1.7B plus an F5 Russian fine-tune, but not Fish S2 Pro plus anything substantial. vLLM-Omni pre-allocates KV cache by `gpu_memory_utilization`, so it must be tuned down when co-hosting.

### Gaps
- No measured RTX 3090 RTF or TTFA was found for Qwen3-TTS, VoxCPM2, CosyVoice3, Chatterbox V3, Higgs TTS 3 or OmniVoice.
- Whether vLLM-Omni's Qwen3-TTS streaming fixed the early RTF ~1.6 on the 4090 report is not documented in the sources read.
- The minimum VRAM footprint of vLLM-Omni deployments when tuned down (rather than the default pre-allocation) is not measured in the sources.

---

## Q5. Maturity, maintenance activity, stars, known issues

### Takeaway
- **Actively maintained as of Oct 2026:** fish-speech, F5-TTS, VoxCPM, VibeVoice (now ASR-focused), Chatterbox, OmniVoice, piper1-gpl, the idiap XTTS fork, vLLM-Omni and IndexTTS.
- **Slowed or dormant:** Qwen3-TTS (no commits since 2026-03-17), CosyVoice (last push 2026-05), Kokoro, Orpheus, CSM, Spark-TTS, Zonos, Dia, original Coqui TTS, original Piper, Supertonic GitHub (archived).
- **Known failure modes:** clicks and partial audio in streaming (Qwen3 forks), end-of-word cutoffs (VoxCPM2), quality drift across generations (Chatterbox), wrong output language (CosyVoice3), extreme VRAM use (Fish S2 Pro).

### Cited Findings
- [V] GitHub stars and last push (via `gh api`, 2026-10-11):

| Repo | Stars | Last push | Status |
|---|---|---|---|
| microsoft/VibeVoice | 54,745 | 2026-10-08 | TTS-1.5B "Disabled" after misuse (2025-09-05) |
| coqui-ai/TTS | 46,105 | 2024-08-16 | Unmaintained |
| 2noise/ChatTTS | 39,899 | 2026-04-10 | |
| OpenBMB/VoxCPM | 38,650 | 2026-10-08 | |
| myshell-ai/OpenVoice | 37,833 | 2025-04-19 | |
| fishaudio/fish-speech | 32,994 | 2026-10-10 | |
| resemble-ai/chatterbox | 26,838 | 2026-07-21 | |
| index-tts/index-tts | 24,413 | 2026-09-29 | |
| QwenAudio/CosyVoice | 23,917 | 2026-05-25 | |
| nari-labs/dia | 19,403 | 2025-11-19 | |
| SWivid/F5-TTS | 15,370 | 2026-10-11 | |
| SesameAILabs/csm | 14,728 | 2025-05-27 | |
| k2-fsa/OmniVoice | 14,435 | 2026-10-06 | |
| supertone-oss-archive/supertonic | 13,773 | 2026-09-09 | Archived |
| QwenLM/Qwen3-TTS | 13,728 | 2026-03-17 | |
| rhasspy/piper | 11,297 | 2025-08-26 | Archived |
| SparkAudio/Spark-TTS | 10,990 | 2025-04-09 | |
| kyutai-labs/pocket-tts | 9,862 | 2026-10-10 | |
| hexgrad/kokoro | 9,246 | 2025-08-06 | |
| boson-ai/higgs-audio | 8,365 | 2026-06-05 | |
| Zyphra/Zonos | 7,232 | 2025-03-05 | |
| vllm-project/vllm-omni | 7,119 | 2026-10-11 | |
| canopyai/Orpheus-TTS | 6,349 | 2025-12-05 | |
| snakers4/silero-models | 6,123 | 2026-07-31 | |
| bytedance/MegaTTS3 | 6,097 | 2026-06-15 | |
| OHF-Voice/piper1-gpl | 5,823 | 2026-10-11 | |
| idiap/coqui-ai-TTS | 2,336 | 2026-10-02 | |
| OpenMOSS/MOSS-TTSD | 1,406 | 2026-09-06 | |
| alphacep/vosk-tts | 274 | 2026-06-06 | |

Sources: [VibeVoice](https://github.com/microsoft/VibeVoice), [VoxCPM](https://github.com/OpenBMB/VoxCPM), [fish-speech](https://github.com/fishaudio/fish-speech), [chatterbox](https://github.com/resemble-ai/chatterbox), [CosyVoice](https://github.com/QwenAudio/CosyVoice), [F5-TTS](https://github.com/SWivid/F5-TTS), [OmniVoice](https://github.com/k2-fsa/OmniVoice), [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), [silero-models](https://github.com/snakers4/silero-models), [vllm-omni](https://github.com/vllm-project/vllm-omni)

- [V] HF popularity: Qwen3-TTS-12Hz-1.7B-CustomVoice 2,095 likes; VoxCPM2 1,670 likes and 609k downloads; Fish s2-pro 1,404 likes; OmniVoice 1,503; Chatterbox 1,829 likes and 1.64M downloads; Misha24-10 F5-TTS_RUSSIAN 242 likes and about 114k downloads; ESpeech RL-V2 44 likes. — [HF API listing](https://huggingface.co/models?pipeline_tag=text-to-speech&sort=likes), [Misha24-10](https://huggingface.co/Misha24-10/F5-TTS_RUSSIAN), [ESpeech](https://huggingface.co/ESpeech/ESpeech-TTS-1_RL-V2)
- [S] **Known issues**:
  - Qwen3-TTS: fine-tuning Base yields "progressively faster speech with every epoch" (37 comments) ([#179](https://github.com/QwenLM/Qwen3-TTS/issues/179)); Japanese and Korean audio incomplete ([#55](https://github.com/QwenLM/Qwen3-TTS/issues/55)); NaN logits with the flash-attention backend ([#333](https://github.com/QwenLM/Qwen3-TTS/issues/333)); distorted audio "有破音" ([#2](https://github.com/QwenLM/Qwen3-TTS/issues/2)).
  - VoxCPM2: "Voice cutoff at the end of words" (Polish, 52 comments) ([#213](https://github.com/OpenBMB/VoxCPM/issues/213)); full fine-tune OOM even on 4×3090 ([#335](https://github.com/OpenBMB/VoxCPM/issues/335)).
  - Fish S2 Pro: VRAM and speed complaints on 3090 and 4090 ([#1168](https://github.com/fishaudio/fish-speech/issues/1168)).
  - Chatterbox: Russian drift, numbers, Й/Ё ([#360](https://github.com/resemble-ai/chatterbox/issues/360), [#302](https://github.com/resemble-ai/chatterbox/issues/302)).
  - CosyVoice3: Chinese output for Russian input ([#1790](https://github.com/QwenAudio/CosyVoice/issues/1790)).
- [V] VibeVoice's TTS model page reads "VibeVoice-TTS-1.5B … Disabled" after Microsoft "discovered instances where the tool was used in ways inconsistent with the stated intent" (2025-09-05). — [VibeVoice README](https://github.com/microsoft/VibeVoice)
- [V] Chatterbox V3 is "optimized to reduce unwanted continuation, repetition, and off-prompt speech". — [README](https://github.com/resemble-ai/chatterbox)
- [V] Supertonic 3 "reduces repeat/skip failures" vs v2. — [HF](https://huggingface.co/Supertone/supertonic-3)

### Inferences
- Qwen3-TTS's slowing activity matters less because vLLM-Omni carries the serving side. Still, bug fixes for Russian stress are unlikely from upstream; fine-tuning (scripts provided) is the path.
- Outdated or abandoned, not to be built on: original Coqui TTS / XTTS-v2 (use the idiap fork if at all), rhasspy/piper (use piper1-gpl), VibeVoice TTS (disabled), Spark-TTS, Zonos, CSM, Orpheus (no Russian anyway), and Kokoro (no Russian, last commit 2025-08).
- For short NPC phrases, LLM-based AR models (Qwen3, Fish, VoxCPM, Higgs, Chatterbox) carry hallucination, cutoff and repetition risk. A guard is cheap and worth it: ASR back-check with the existing Realtime/Whisper ASR, plus a duration sanity check and regenerate on mismatch.

### Gaps
- Short-phrase-specific artifact rates (1–3 sentences) are not reported by any source for any model.
- Kyutai's DSM TTS languages were not confirmed from the README excerpt read; only its STT models (en/fr) were. It is presumed not Russian.
- Tencent AuK's (MIT, 2026-08) language list was not found in the card excerpt.
