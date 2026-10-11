# Russian TTS providers for NPC voicing (Gothic 2 RU mod) and access restrictions — state as of 2026-10-11

Scope: Yandex SpeechKit (now documented under Yandex AI Studio), Sber SaluteSpeech, T-Bank VoiceKit, MTS AI Audiogram, VK Cloud Voice, plus other Russian offerings (Silero, GigaChat audio), and the official access policies of foreign TTS/LLM providers toward Russia.

Conventions used in these notes:
- Exchange rate for normalisation: **83.5 RUB = 1 USD** (Bank of Russia official rate 83.25–83.56 RUB/USD on 1–4 Oct 2026, as reported by [ppt.ru](https://ppt.ru/currencies/usd/03-10-2026)). All Russian prices below include VAT unless stated.
- "Per hour of audio" figures assume ~50,000 characters of Russian text per hour of speech (~14 chars/s). This is my own working assumption, not a sourced figure.
- **[VERIFIED]** = read on the provider's own page during this session. **[SECONDARY]** = from a search-engine summary, aggregator or third-party article, not confirmed on a primary page. **[POSSIBLY OUTDATED]** = source dated before mid-2026 or undated.
- Observation about fetching: `yandex.cloud/ru/docs/speechkit/*` now 301-redirects to `aistudio.yandex.ru/docs/ru/speechkit/*` (SpeechKit docs moved into Yandex AI Studio). T-Bank's `developer.tbank.ru` returned "self signed certificate in certificate chain" and then HTTP 502 from my (non-Russian) fetch location.

---

## 1. Which TTS models and voices does each provider offer now, and are there deep/old male voices suitable for characters?

### Takeaway
Only Yandex SpeechKit is a live, self-serve Russian TTS API with a current voice catalogue (18 "general" ru-RU voices + 6 newer "livetts" voices); none of its male voices is described as old, deep or villainous, and the "evil" role exists only for two female voices. SaluteSpeech has 6 Russian voices but closed sales to new customers on 15 July 2026; T-Bank, MTS AI and VK are B2B/legacy offerings with small voice sets.

### Cited Findings

**Yandex SpeechKit (Yandex AI Studio)** [VERIFIED, page undated]
- Two synthesis models exist: `general` and `livetts`; they "differ in available voices, roles and supported settings" — [Yandex SpeechKit TTS overview](https://aistudio.yandex.ru/docs/ru/speechkit/tts/)
- `general` ru-RU voices and roles (API v1/v3/Realtime availability as listed): male — filipp (no roles; v1, v3, Realtime), ermil (neutral, good), zahar (neutral, good), madi_ru (no roles), alexander (neutral, good; v3+Realtime only), kirill (neutral, **strict**, good; v3+Realtime), anton (neutral, good; v3+Realtime); female — jane (neutral, good, **evil**), omazh (neutral, **evil**), marina (neutral, whisper, friendly), dasha, julia (strict), lera, masha, saule_ru (strict, whisper), zamira_ru, zhanar_ru, yulduz_ru (incl. whisper) — [Yandex voices list](https://aistudio.yandex.ru/docs/ru/speechkit/tts/voices)
- `livetts` voices (API v3 model `livetts` and Realtime models with LiveTTS): Sofia (F: casual default, support), Vera (F: casual default, support), **Vasily (M: default, support, sales)**, **Sergey (M: default, support, sales)**, **Denis (M: casual, formal default, neutral, sales)**, Irina (F: formal, support, narrator, sales default, neutral) — [Yandex voices list, LiveTTS section](https://aistudio.yandex.ru/docs/ru/speechkit/tts/voices)
- The release date of `livetts` / the Realtime "Speech TTS Live" API is not stated on the docs pages fetched — [Yandex TTS overview](https://aistudio.yandex.ru/docs/ru/speechkit/tts/)
- The most recent dated Yandex voice-catalogue announcement found was Dec 2023 (eight new contact-centre voices with different roles) — [Yandex Cloud blog, 2023-12](https://yandex.cloud/ru/blog/posts/2023/12/new-speechkit-voices) [POSSIBLY OUTDATED]
- Conflicting secondary claim: an aggregator lists voices "oksana, kseniya, alena, uliana (premium), children's zorro/ermolaev" — these do not appear in the current official list and look like legacy v1-era data — [toolfox / plaan aggregator summary via search](https://plaan.ai/yandex-speechkit/) [SECONDARY, contradicted by official list]

**Sber SaluteSpeech** [VERIFIED, page undated]
- Russian voices: Наталья `Nec` (F), Борис `Bys` (M), Марфа `May` (F), Тарас `Tur` (M), Александра `Ost` (F), Сергей `Pon` (M); plus English Kira `Kin`; each in 24 kHz and 8 kHz variants — [SaluteSpeech voices](https://developers.sber.ru/docs/ru/salutespeech/guides/synthesis/voices)
- "Через API не предоставляются голоса ассистента Салют" (the Salute assistant voices are not available via API) — [SaluteSpeech voices](https://developers.sber.ru/docs/ru/salutespeech/guides/synthesis/voices)
- A search summary of the changelog says a female "Асем (ru)" voice was added in Oct 2024, but it was not on the voices page as fetched — [SaluteSpeech changelog via search](https://developers.sber.ru/docs/ru/salutespeech/changelog) [SECONDARY, conflicts with voices page]
- A third-party site headline claims "Премиальные голоса SaluteSpeech будут отключены" (premium voices will be switched off) — [tospeech.ru](https://tospeech.ru/posts/salute-speech-api) [SECONDARY, not opened/verified]
- Changelog contains no 2026 entries; the last listed entries are Jun–Aug 2025 (SSML-by-language, synthesis languages, sync synthesis) — [SaluteSpeech changelog](https://developers.sber.ru/docs/ru/salutespeech/changelog)

**T-Bank (ex-Tinkoff) VoiceKit**
- Marketing page: speech synthesis is offered; "Стоимость синтеза речи рассчитываем отдельно" (synthesis priced separately) with an application form requiring company name and INN; recognition prices listed (0.60 ₽/min streaming etc., excl. VAT) — [T-Bank VoiceKit page](https://www.tbank.ru/software/voicekit/) [VERIFIED]
- Docs (per search summary): streaming synthesis in real time, choice of reading style or emotion, stress control and automatic stress placement, automatic text normalisation; gRPC endpoint `api.tinkoff.ai:443`; methods `StreamingSynthesize` and `Synthesize`; synthesis available to legal entities — [T-Bank VoiceKit synthesis docs via search](https://developer.tbank.ru/voicekit/api/synthesis/) [SECONDARY — docs site returned 502 / self-signed cert to my fetch]
- Voice names/count: not obtained.

**MTS AI Audiogram**
- Audiogram is MTS AI's ASR+TTS platform (also emotion recognition), delivered on-premise or as SaaS in MTS AI's cloud; synchronous synthesis takes text + voice + sample rate + encoding — [MTS AI Audiogram PDF, 2023-04](https://mts.ai/wp-content/uploads/20230417_Audiogram_-dlya-saita.pdf); [mts-ai/audiogram manual on GitHub](https://github.com/mts-ai/audiogram/blob/main/AG_manual_ru.md) [SECONDARY via search; POSSIBLY OUTDATED]
- No product called "Cosmos TTS" was found; MTS Exolve is a telephony/voice-robot platform, not a standalone TTS API — [search summary](https://media.mts.ru/business/209933-kak-rabotaet-raspoznavanie-rechi) [SECONDARY]

**VK Cloud Voice (ex-Mail.ru Cloud Voice, Marusya technology)**
- TTS voices/models: Katherine (default; also `katherine-hifigan`), Maria (`maria-serious`), Pavel (`pavel-hifigan`); tempo 0.75–1.75; formats PCM/MP3/Opus; up to 2,000 UTF-8 symbols per request; endpoint `https://voice.mcs.mail.ru/tts`; no SSML, pitch or stress parameters documented — [VK Cloud TTS docs](https://cloud.vk.ru/docs/en/ml/cloud-voice/instructions/text-to-speech) [VERIFIED, page undated]
- Product launched on Mail.ru Cloud Solutions with Marusya technology in 2021 — [Habr VK news](https://habr.com/ru/companies/vk/news/581342/) [POSSIBLY OUTDATED]

**Other Russian offerings**
- GigaChat Audio (Sber, July 2026) is an audio-*understanding* LLM (processes voice/audio input, detects sentiment, up to 3 h recordings), not a TTS — [TAdviser GigaChat Audio](https://www.tadviser.ru/index.php/%D0%9F%D1%80%D0%BE%D0%B4%D1%83%D0%BA%D1%82:GigaChat_Audio); [vc.ru](https://vc.ru/provod/3029150-gigachat-audio-raspoznaet-emotsii-i-nakhodit-momenty) [SECONDARY]
- Silero: search found only the open-source TTS packages / self-hosted API servers on PyPI, no commercial hosted TTS API or tariff — [PyPI silero-tts](https://pypi.org/project/silero-tts); [PyPI silero-api-server](https://pypi.org/project/silero-api-server) (self-hosting is out of scope per "no local models on Mac" rule)

### Inferences
- For Xardas-like (old, deep, commanding) voices, Yandex's best fits are probably `kirill` with role `strict`, or `filipp`/`ermil`/`zahar`, possibly lowered with the v3 `pitch_shift` hint; this is a workaround, not a designed "old necromancer" timbre. Orcs/monsters would need post-processing (pitch/formant shift) on the client side.
- The `*_ru` voices (madi_ru, saule_ru, zamira_ru, zhanar_ru, yulduz_ru) are probably Central-Asian-named voices speaking Russian (naming pattern); timbre/accent not verified.
- With ~10 usable Russian male voices (Yandex) vs. effectively unlimited prompt-designed voices in Gemini Voice Design, Russian providers cannot give every NPC a distinct voice without heavy reuse.

### Gaps
- No official release date for Yandex `livetts` / Speech TTS Live, and no descriptions of age/timbre for any Yandex voice.
- T-Bank voice list and synthesis docs could not be retrieved (502 / certificate errors).
- MTS AI Audiogram: no 2025–2026 voice list or public self-serve access found.

---

## 2. What evidence exists on Russian quality (stress, intonation, ё, numbers) vs ElevenLabs / Google / OpenAI?

### Takeaway
No rigorous 2026 benchmark of Russian TTS was found; the available evidence is reseller/aggregator articles. They agree ElevenLabs leads on expressiveness and Yandex SpeechKit is the strongest "native" Russian option, and that homographs, ё and numbers remain failure points for all models unless marked up.

### Cited Findings
- Habr (BotHub, 2026-09-12): models pick homograph readings "по статистике, а не по вашему контексту"; without ё, «все»/«всё» are resolved by frequency; amounts like «1 250 000 ₽» can be read correctly or "рассыпаться на отдельные цифры" depending on spacing — [Habr: Как озвучить текст с помощью ИИ в 2026 году](https://habr.com/ru/companies/bothub/articles/1081544/) [SECONDARY; author is an API reseller]
- Same article: ElevenLabs v3 "понимает аудиотеги, держит паузы, меняет темп внутри фразы"; for OpenAI TTS "Управлять эмоцией нельзя никак" — [Habr BotHub 2026-09-12](https://habr.com/ru/companies/bothub/articles/1081544/) [SECONDARY]
- Aggregator ranking: ElevenLabs first for voice quality and cloning; Yandex SpeechKit second as "best TTS for Russian"; SaluteSpeech claimed better on long monologues/banking terms, Yandex slightly better on short conversational requests — [toolfox.ru](https://toolfox.ru/blog/nejroseti-dlya-ozvuchki-teksta); [Habr BotHub top-5 2026](https://habr.com/ru/companies/bothub/articles/993576/) [SECONDARY, SEO-style, no methodology]
- SaluteSpeech docs: words with «ё» are pronounced correctly "regardless of how they are written" (contextual by default) — [SaluteSpeech SSML pronunciation](https://developers.sber.ru/docs/ru/salutespeech/guides/synthesis/ssml/pronunciation) [VERIFIED, vendor claim]
- T-Bank claims automatic stress placement and text normalisation — [T-Bank VoiceKit docs via search](https://developer.tbank.ru/voicekit/api/synthesis/) [SECONDARY]

### Inferences
- For short (1–3 sentence) NPC lines, the Russian-native engines' main advantage is predictable stress/number normalisation plus explicit stress markup (see Q4); their main weakness vs. Gemini/ElevenLabs is limited acting range (call-centre-oriented roles).
- A practical evaluation would need an in-house listening test on Gothic-style lines (archaic vocabulary, names like Ксардас, Ли, Диего), since no published benchmark covers fantasy/game diction.

### Gaps
- No independent MOS/ABX comparison of Russian TTS (Yandex vs Sber vs Gemini vs ElevenLabs) dated 2026 was found.
- No quality evidence found for `livetts` specifically, or for VK/MTS/T-Bank voices in 2026.

---

## 3. Custom voices: cloning / Brand Voice, minimum data, price, who can order; any voice design?

### Takeaway
Yandex Brand Voice Lite is the only priced self-serve custom voice: ≥30 min of labelled recordings, 9,150 ₽ setup plus ~101,666 ₽/month hosting for the first voice — far beyond hobby budgets. No Russian provider offers prompt-based "voice design" comparable to Gemini Voice Design.

### Cited Findings
- Brand Voice tiers: **Premium** (full-text synthesis, extensive studio recordings with Yandex specialists), **Lite** ("загрузив минимум размеченных аудиозаписей (от 30 минут)", customer prepares data), **Call Center** (template-based phrases for phone calls, max phrase 24 s / 250 chars) — [Yandex Brand Voice](https://aistudio.yandex.ru/docs/ru/speechkit/tts/brand-voice/) [VERIFIED]
- Brand Voice Lite pricing: one-time creation 9,150 ₽ (≈ $110); first 7 days hosting free; monthly hosting tiered from 101,666 ₽ for the 1st voice (≈ $1,218/month) down to 50,833 ₽ for the 6th+ voice (≈ $609/month); requests 0.1626 ₽ each. Premium hosting "по запросу" — [Yandex SpeechKit pricing](https://aistudio.yandex.ru/docs/ru/speechkit/pricing) [VERIFIED]
- Yandex ethics principles: "We do not synthesize speech of people without their permission" — i.e., consent required for voice cloning — [Yandex Cloud blog, 2024-11-14](https://yandex.cloud/ru/blog/posts/2024/11/speechkit-ethics) [POSSIBLY OUTDATED]
- SaluteSpeech: custom branded voice via the "YourVoice" service (no details on data/price) — [SaluteSpeech voices](https://developers.sber.ru/docs/ru/salutespeech/guides/synthesis/voices) [VERIFIED mention only]

### Inferences
- Even one custom Xardas voice on Yandex would cost ≈ $1,330 in the first month; with SaluteSpeech closed to new customers, there is no realistic Russian custom-voice path for a hobby mod.
- Character variety must therefore come from voice + role + speed + pitch combinations, or from a foreign provider with voice design/cloning.

### Gaps
- Whether Brand Voice Lite can be ordered by an individual (vs. only legal entities) and turnaround time — not stated on the page.
- YourVoice (Sber), T-Bank, MTS custom-voice terms and prices — not found.

---

## 4. Expressiveness: emotions/roles, SSML, stress markup, speed/pitch

### Takeaway
Yandex offers the richest documented control for Russian (stress `+`, pauses, emphasis, phonemes, roles, speed, pitch shift), but SSML only works in API v1. SaluteSpeech supports SSML with apostrophe stress marks and (per secondary sources) emotion tags; VK documents only tempo.

### Cited Findings
- Yandex TTS markup (API v1 and v3): stress `+` before the stressed vowel, e.g. "Дверь в комнату заперта на зам+ок."; explicit pause `sil<[300]>` (max 7,000 ms); context pauses `<[tiny|small|medium|large|huge]>`; emphasis `**word**` or `<[accented]>`; phonemes `[[v a sʲ ʌ]]`; full markup support applies to Russian — [Yandex TTS markup](https://aistudio.yandex.ru/docs/ru/speechkit/tts/markup/tts-markup) [VERIFIED]
- "SSML поддерживается только в запросах API v1"; speed changeable in v1 and v3; pitch in v3 via `hints: pitch_shift` in [-1000;1000] Hz; loudness normalisation `MAX_PEAK` / `LUFS` — [Yandex TTS overview](https://aistudio.yandex.ru/docs/ru/speechkit/tts/) [VERIFIED]
- Yandex roles: neutral/good/strict/friendly/whisper/evil for `general` voices; casual/formal/neutral/support/sales/narrator for `livetts` voices — [Yandex voices](https://aistudio.yandex.ru/docs/ru/speechkit/tts/voices) [VERIFIED]
- SaluteSpeech stress: apostrophe after the stressed vowel, e.g. `<speak>бере'т</speak>`; works independently of SSML; ё handled contextually — [SaluteSpeech SSML pronunciation](https://developers.sber.ru/docs/ru/salutespeech/guides/synthesis/ssml/pronunciation) [VERIFIED]
- Sber SSML (assistant docs): `break`, `sub`, `audio`, `extra.background-audio`, `say-as` (abbreviations, dates, numerals), `voice` (mood, whisper), `paint` (tone, intonation, speed, loudness), `extra.fulldate` — [Sber SSML overview](https://developers.sber.ru/docs/ru/va/chat/voice-interface/speech-synthesis/ssml/overview) [VERIFIED; unclear whether all apply to SaluteSpeech API voices]
- Per search summary of the SaluteSpeech changelog: version 3.3.0 added SSML emotion tags (irritation, joy, sadness, whisper) and "emotion management in the synthesis API" was documented in March 2025 — [SaluteSpeech changelog via search](https://developers.sber.ru/docs/ru/salutespeech/changelog) [SECONDARY; not visible in the fetched changelog excerpt]
- VK Cloud Voice: only tempo 0.75–1.75 and voice choice documented — [VK Cloud TTS docs](https://cloud.vk.ru/docs/en/ml/cloud-voice/instructions/text-to-speech) [VERIFIED]

### Inferences
- The LLM can be prompted to emit Yandex `+` stress marks and `sil<[..]>` pauses for names and archaic words; a small per-mod stress dictionary (e.g. "Кс+ардас") applied before synthesis would fix recurring names.
- Neither Yandex nor Sber offers "anger/menace/old age" acting as a role; dramatic NPC delivery (threats, orc growls) will be weaker than prompt-steerable models.

### Gaps
- Whether `livetts` voices accept TTS markup, speed and `pitch_shift` — not stated on pages fetched.
- T-Bank stress-markup syntax and emotion list — docs inaccessible.

---

## 5. Streaming API, latency, Python SDK / async

### Takeaway
Yandex has gRPC + REST API v3 with streaming synthesis and a Realtime (Speech TTS Live) API; a `yandex-speechkit` PyPI SDK exists. T-Bank offers gRPC streaming synthesis. No published latency figures were found for any Russian provider.

### Cited Findings
- Yandex API v1 is REST only; API v3 supports gRPC and REST; endpoint `tts.api.cloud.yandex.net:443`; "На один запрос SpeechKit API v3 может прийти больше одного ответа"; streaming synthesis lets you send texts sequentially within one session; a "Realtime API" exists for LiveTTS — [Yandex TTS overview](https://aistudio.yandex.ru/docs/ru/speechkit/tts/) [VERIFIED]
- Python SDK: `pip install yandex-speechkit` (alias `speechkit`), modules for auth, streaming/sync recognition and synthesis; possible `grpcio-tools` version conflicts — [Yandex SDK install docs via search](https://yandex.cloud/en/docs/speechkit/sdk/python/install); [PyPI speechkit](https://pypi.org/project/speechkit) [SECONDARY]
- T-Bank VoiceKit: `StreamingSynthesize` (streaming) and `Synthesize` gRPC methods at `api.tinkoff.ai:443`, API_KEY + SECRET_KEY auth, REST for async scenarios — [T-Bank docs via search](https://developer.tbank.ru/voicekit/api/synthesis/grpc/synthesize) [SECONDARY]
- VK Cloud Voice: plain HTTP GET/POST `https://voice.mcs.mail.ru/tts`; streaming not mentioned — [VK Cloud TTS docs](https://cloud.vk.ru/docs/en/ml/cloud-voice/instructions/text-to-speech) [VERIFIED]

### Inferences
- For an asyncio server, the Yandex v3 gRPC stubs can likely be driven via `grpc.aio` directly, bypassing the SDK; this needs a prototype to confirm.
- Short NPC lines (<250 chars) fit in a single v3 request, so streaming mainly matters for time-to-first-audio.

### Gaps
- No time-to-first-byte or real-time-factor measurements found for Yandex, Sber, T-Bank or VK.
- Whether the official Yandex SDK exposes asyncio APIs, and the transport of the Realtime API (WebSocket?) — the SDK doc page returned 404.

---

## 6. Pricing normalised to USD per 1M characters / per hour, and free grants

### Takeaway
Yandex v1 costs ≈ $16 per 1M chars; v3 is billed per request (≤250 chars) at 0.1626 ₽ (`general`) or 0.25 ₽ (`livetts`), i.e. ≈ $0.002–0.003 per short NPC line. SaluteSpeech was the cheapest (≈ $2.2/1M for companies) but is closed to new customers since 15 July 2026.

### Cited Findings
- **Yandex** (VAT incl.): API v1 1,342 ₽ per 1M characters (≈ **$16.07/1M**); API v3 `general` 0.1626 ₽ per request, `livetts` 0.25 ₽ per request; billing unit = 250-character block (<250 chars = 1 unit, 250–500 = 2, etc.; empty requests = 1 unit; server errors free); Realtime "Speech TTS Live" 0.25 ₽ per 250-char block per `response.create`, only input text billed — [Yandex SpeechKit pricing](https://aistudio.yandex.ru/docs/ru/speechkit/pricing) [VERIFIED, page undated]
- **SaluteSpeech, companies**: 0.000186 ₽ per character (= 186 ₽/1M ≈ **$2.23/1M**); package 55,000,000 chars for 10,230 ₽/month; postpaid minimum 15,000 ₽/month (≈ $180); "As of July 15, 2026, new customers cannot purchase packages or use pay-as-you-go" — [SaluteSpeech legal tariffs](https://developers.sber.ru/docs/ru/salutespeech/tariffs/legal-tariffs) [VERIFIED]
- **SaluteSpeech, individuals**: 1M-char synthesis package 1,000 ₽/month (≈ $11.98/1M); Freemium 200,000 chars/month; "С 15 июля 2026 года прекращена продажа новых пакетов и продление Freemium тариф[а]"; already purchased packages remain valid to expiry — [SaluteSpeech individual tariffs](https://developers.sber.ru/docs/ru/salutespeech/tariffs/individual-tariffs) [VERIFIED]
- Secondary claim that SaluteSpeech also closed registration for new legal entities/IP from 15 July 2026 — [did-official.ru](https://did-official.ru/tools/salute-speech/) / search summary [SECONDARY; consistent with the legal-tariffs note]
- **T-Bank**: synthesis "рассчитываем отдельно" (quote on request) — [T-Bank VoiceKit](https://www.tbank.ru/software/voicekit/) [VERIFIED]
- **VK Cloud**: pay per synthesised character; new users get up to 12,000 ₽ credit for 60 days after full verification; rates not shown — [VK Cloud Voice](https://cloud.vk.ru/cloud-voice/) [VERIFIED]
- **Yandex Cloud starter grant**: ≥4,000 ₽ for Russian residents and Belarus tax residents, ≥20,000 tenge for Kazakhstan, ≥$30 for others; 60 days; once per billing account — [Yandex partner FAQ via search](https://cloud.yandex.ru/docs/partner/qa/start-grant) [SECONDARY — page now 404]; the legal-entity trial period is "временно недоступен" — [Yandex usage-grant page](https://yandex.cloud/ru/docs/getting-started/usage-grant) [VERIFIED]
- Reseller prices quoted on Habr (BotHub, 2026-09-12): ElevenLabs v3 26.60 ₽/1,000 chars; OpenAI tts-1 1,767.86 ₽/1M tokens; Yandex v1 1,342 ₽/1M chars — [Habr BotHub](https://habr.com/ru/companies/bothub/articles/1081544/) [SECONDARY, reseller markup]

Normalised table (83.5 ₽/$; my calculations):

| Option | ₽ per unit | ≈ $ per 1M chars (packed) | ≈ $ per 1M chars if lines avg 100 chars | ≈ $ per NPC line (≤250 chars) | ≈ $ per hour (50k chars/h assumption) |
|---|---|---|---|---|---|
| Yandex v1 | 1,342 ₽ / 1M chars | 16.07 | 16.07 | ~0.0016 (100 chars) | 0.80 |
| Yandex v3 `general` | 0.1626 ₽ / 250-char unit | 7.79 | 19.47 | 0.0019 | 0.39 (packed) – 0.97 (100-char lines) |
| Yandex v3 `livetts` / Realtime | 0.25 ₽ / 250-char unit | 11.98 | 29.94 | 0.0030 | 0.60 – 1.50 |
| SaluteSpeech companies (closed to new) | 0.000186 ₽ / char | 2.23 (+ $180/mo minimum postpaid) | 2.23 | ~0.0002 | 0.11 |
| SaluteSpeech individuals (sales stopped) | 1,000 ₽ / 1M chars | 11.98 | 11.98 | ~0.0012 | 0.60 |
| Yandex Brand Voice Lite | 9,150 ₽ + 101,666 ₽/mo | — | — | + hosting ≈ $1,218/mo | — |

### Inferences
- At ~10,000 NPC lines per month, Yandex costs ≈ 1,626 ₽ (~$19.5) with `general` or ≈ 2,500 ₽ (~$30) with `livetts` — small in absolute terms; cost is not the deciding factor vs. Gemini, voice variety is.
- Because v3 rounds every request up to 250 chars, sending NPC lines individually wastes most of each billing unit; v1 (per-character) is cheaper for very short lines but loses v3-only voices and pitch control.

### Gaps
- VK Cloud Voice and MTS Audiogram per-character prices not found.
- Yandex starter-grant terms not confirmed on a live official page; unclear if AI Studio has a separate free tier for SpeechKit.

---

## 7. Access: can foreigners use Russian providers, and do foreign providers block Russia?

### Takeaway
Yandex Cloud accepts non-residents of Russia (personal accounts paid by bank card or SBP; whether foreign cards work is unstated). On the foreign side, OpenAI and Gemini API official country lists exclude Russia (and Belarus), ElevenLabs' terms bar users located in Russia, and Microsoft suspended Azure for Russian organisations in 2024; Fish Audio has a generic US-embargo clause; nothing was found for MiniMax.

### Cited Findings

**Russian providers for non-residents**
- Yandex Cloud billing accounts are available to "Резидентов Российской Федерации и нерезидентов Российской Федерации"; personal accounts pay by "банковская карта или система быстрых платежей (СБП)"; business accounts by bank transfer or card — [Yandex Cloud billing account](https://yandex.cloud/ru/docs/billing/concepts/billing-account) [VERIFIED; foreign-card acceptance not specified]
- SaluteSpeech: no longer sells to new individual or corporate customers since 15 July 2026 — [SaluteSpeech legal tariffs](https://developers.sber.ru/docs/ru/salutespeech/tariffs/legal-tariffs); [individual tariffs](https://developers.sber.ru/docs/ru/salutespeech/tariffs/individual-tariffs) [VERIFIED]
- T-Bank VoiceKit synthesis requires an application with company name and INN — [T-Bank VoiceKit](https://www.tbank.ru/software/voicekit/) [VERIFIED]

**Foreign providers vs Russia (official policies)**
- **OpenAI API**: Russia is not on the supported countries list (Belarus also absent; Kazakhstan present); "Accessing or offering access to our services outside of the countries and territories listed below may result in your account being blocked or suspended." — [OpenAI supported countries](https://developers.openai.com/api/docs/supported-countries) [VERIFIED]
- **Google Gemini API / AI Studio**: Russia not in available regions (Belarus also absent; Ukraine and Kazakhstan present); page says "If you're not in one of these countries or territories, try the Gemini API in Gemini Enterprise Agent Platform" — [Gemini API available regions](https://ai.google.dev/gemini-api/docs/available-regions) [VERIFIED]
- **ElevenLabs**: non-EEA Terms of Service require users to represent they are not located in a country subject to US sanctions/embargoes "including Belarus, Cuba, Iran, North Korea, Russia, Syria, and the annexed regions of Ukraine"; terms reported as last updated 2026-10-09 — [ElevenLabs Terms (non-EEA) via search index](https://elevenlabs.io/terms-of-use) [SECONDARY — page is JS-rendered; direct fetch redirected to a help article titled "Do you restrict access to the service and platform for any specific countries" which returned 403]
- **Microsoft Azure**: under the EU 12th sanctions package (Dec 2023, ban on supplying enterprise/cloud software to Russian-registered companies), Microsoft suspended 50+ cloud products incl. Azure for Russian organisations from spring 2024, disconnecting customers in batches from May 2024 — [BleepingComputer](https://bleepingcomputer.com/news/microsoft/microsoft-to-shut-down-50-cloud-services-for-russian-businesses); [DatacenterDynamics](https://www.datacenterdynamics.com/en/news/microsoft-suspends-access-to-cloud-services-for-russian-companies/) [SECONDARY news; POSSIBLY OUTDATED (2024)]. The Azure Speech regions page (updated 2026-10-08) lists no Russian region and no Russia statement — [Azure Speech regions](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions) [VERIFIED]
- **Fish Audio**: Terms (effective 2024-08-18) prohibit export to "any U.S. embargoed countries or any country that has been designated by the U.S. Government as a 'terrorist supporting' country" and require users to warrant they are not located there; Russia is not named — [Fish Audio Terms](https://fish.audio/terms) [VERIFIED; POSSIBLY OUTDATED]
- **MiniMax**: runs separate international (`api.minimax.io`) and mainland-China (`api.minimaxi.com`) backends — [MiniMax API docs via search](https://platform.minimax.io/docs/api-reference/api-overview) [SECONDARY]; no statement on Russia found (ToS page is JS-rendered and could not be read).
- EU continued expanding Russia sanctions (20th package adopted May 2026) — [Skadden](https://www.skadden.com/insights/publications/2026/05/eu-20th-sanctions-package) [SECONDARY; not specific to AI SaaS]

### Inferences
- For a player physically in Russia, the project's Gemini baseline is officially unsupported for the Gemini API; the same is true for OpenAI and ElevenLabs. A provider adapter for Yandex SpeechKit is the only officially available, currently-selling Russian TTS option among those surveyed. (No circumvention advice is given here.)
- For a developer/player outside Russia, Yandex is officially usable as a non-resident; practical card acceptance needs testing.

### Gaps
- Whether Yandex Cloud accepts non-Russian-issued cards for personal accounts.
- MiniMax and Fish Audio payment/IP restrictions for Russia — no explicit official statement found.
- Whether Azure Speech is available to Russian *individuals* (news covers organisations).
- Whether "Gemini Enterprise Agent Platform" (Google Cloud) is available in Russia — not checked.

---

## 8. Terms for using generated audio in a game mod

### Takeaway
Little game-specific language was found. Yandex prohibits voicing illegal, insulting or discriminatory content; Fish Audio's free plan is personal non-commercial only. No provider page found explicitly addresses game mods.

### Cited Findings
- Yandex SpeechKit ethics: prohibits "Using Yandex SpeechKit to voice illegal materials, as well as texts containing insults or discrimination"; violations may lead to access restrictions; Yandex "retain[s] the right to limit scenarios for our technology use"; no synthesis of people's voices without permission — [Yandex Cloud blog, 2024-11-14](https://yandex.cloud/ru/blog/posts/2024/11/speechkit-ethics) [VERIFIED; POSSIBLY OUTDATED]
- Business must obtain consent to use a voice, and use scenarios are limited by service rules (Brand Voice context) — [search summary of Yandex pages](https://yandex.cloud/ru-kz/docs/speechkit/tts/brand-voice/) [SECONDARY]
- Fish Audio: free plan limited to "your own internal, personal, non-commercial use, and not on behalf of or for the benefit of any third party"; paid plans licensed for commercial use — [Fish Audio Terms](https://fish.audio/terms) [VERIFIED]

### Inferences
- Gothic dialogue includes insults and threats between characters; under Yandex's "no insults" rule, LLM-generated in-character abuse could in theory breach the rules. Content filtering on the LLM side is advisable if Yandex is used.
- A free, non-commercial hobby mod where each player uses their own API key is the lowest-risk set-up across providers; redistributing pre-generated audio is riskier.

### Gaps
- Full Yandex Cloud / AI Studio terms of use on ownership of synthesised audio — not retrieved.
- SaluteSpeech, T-Bank, VK, MTS terms on output rights — not retrieved.
