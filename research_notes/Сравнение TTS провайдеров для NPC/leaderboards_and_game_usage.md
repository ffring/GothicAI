# Лидерборды TTS, независимые замеры задержки и практика озвучки игровых NPC (срез на 2026-10-11)

Контекст: хобби-мод для русской Gothic 2 (OpenGothic), Python asyncio сервер, короткие реплики NPC (1–3 предложения, русский), свой голос на каждого NPC. Базовый кандидат: Google Gemini 3.8 Flash TTS + Voice Design. Здесь только сравнительная картина по провайдерам и практика в играх; глубокие разборы конкретных провайдеров делают коллеги.

Пометки: **[verified]** прочитано в первоисточнике (страница лидерборда, репозиторий через GitHub API, официальный пресс-текст); **[secondary]** блог/агрегатор/новость, пересказывающие первоисточник; **[vendor]** публикация заинтересованного вендора; **[snippet]** видно только в поисковой выдаче, страница не открылась; **[inference]** мой вывод. Все числа Elo разных лидербордов между собой несравнимы (разные шкалы, наборы моделей и промпты).

## 1. Текущие рейтинги: Artificial Analysis, TTS Arena V2, Design Arena, другие лидерборды 2026 года

### Takeaway
На главном арена-лидерборде (Artificial Analysis Speech Arena, снято 2026-10-11) лидирует ElevenLabs Eleven v4 Turbo (1327), Gemini 3.8 Flash TTS на 5-м месте (1272) и при этом самая дешёвая модель в первой пятёрке ($16.5 за 1M символов против $40–80 у ElevenLabs). Доверительные интервалы около ±16–19 Elo, поэтому первые 4–6 мест статистически почти ничья. На втором независимом лидерборде (Voice Arena, US English) Gemini 3.8 Flash-Lite на 1-м месте, Gemini 3.8 Flash на 4-м. Все human-preference рейтинги по сути англоязычные.

### Cited Findings

**Artificial Analysis (AA) Text-to-Speech / Speech Arena: главный рейтинг, снято 2026-10-11** [verified] — [AA TTS Leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard)

| # | Модель | Провайдер | Elo | $ / 1M симв. |
|---|---|---|---|---|
| 1 | Eleven v4 Turbo | ElevenLabs | 1327 | 40.0 |
| 2 | Eleven v4 | ElevenLabs | 1323 | 80.0 |
| 3 | Qwen-Audio-3.1-TTS-Plus | Alibaba | 1298 | 19.3 |
| 4 | Sonic 3.6 | Cartesia | 1284 | 49.0 |
| 5 | Gemini 3.8 Flash TTS | Google | 1272 | 16.5 |
| 6 | Qwen-Audio-3.0-TTS-Plus | Alibaba | 1264 | 19.3 |
| 7 | Realtime TTS-2 | Inworld | 1257 | 20.8 |
| 8 | Gemini 3.8 Flash-Lite TTS | Google | 1244 | 11.0 |
| 9 | Simba 3.2 | SpeechifyAI | 1243 | 6.6 |
| 10 | Breeze TTS 2 (open weights) | BreezeBlue | 1222 | 34.0 |
| 11 | Luna TTS | VUI Labs | 1218 | 15.0 |
| 12 | Realtime TTS-2 Flash | Inworld | 1217 | 10.4 |
| 13 | Gemini 3.1 Flash TTS | Google | 1212 | 18.3 |
| 14 | StepAudio 2.5 TTS | StepFun | 1209 | 85.0 |
| 15 | v3 Conversational | ElevenLabs | 1207 | 50.0 |
| 16 | Sonic 3.5 | Cartesia | 1191 | 49.0 |
| 17 | Soniox TTS Real-Time v2 | Soniox | 1179 | 14.2 |
| 18 | Eleven v3 | ElevenLabs | 1176 | 100.0 |
| 19 | Speech 2.8 HD | MiniMax | 1173 | 100.0 |
| 20 | Lightning V3.1 Pro | Smallest.ai | 1172 | 19.5 |

- Метаданные страницы на 2026-10-11: в рейтинге 96 моделей, из них 16 с открытыми весами; примерно 1 500–7 300 голосов на модель; фильтры по акценту (All/US/UK) и категории промптов (Knowledge Sharing, Assistants, Entertainment, Customer Service); даты релиза моделей от 2016-09 до 2026-09. Самая дешёвая модель: Kokoro 82M v1.0, $0.7 за 1M симв., Elo 1064. На самом лидерборде **нет колонок latency/TTFA**, есть только Elo и цена — [AA TTS Leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard) [verified]
- Доверительные интервалы: в выгрузке страницы они отрисовались искажённо («±189», «±179»), но снимок от 2026-09-25 даёт ±14–19 (Sonic 3.6 1279±17, Gemini 3.8 Flash TTS 1265±17, Qwen-Audio-3.0-TTS-Plus 1259±16, Simba 3.2 1239±14, Gemini 3.8 Flash-Lite 1239±16, Breeze TTS 2 1205±16), а 2026-09-28 Eleven v4 вошёл с 1319±19 — [Digital Applied, 2026-09-24/обновл.](https://www.digitalapplied.com/blog/best-text-to-speech-models-september-2026-ranked-priced) [secondary]; выдача поиска даёт Eleven v4 1321±18 — [AA leaderboard (snippet)](https://artificialanalysis.ai/text-to-speech/leaderboard) [snippet]
- На сводной странице AA есть новая метрика «Pronunciation Robustness»: доля выделенных фрагментов, которые люди-оценщики признали произнесёнными правильно, в 4 категориях (отдельные термины, уместность в контексте, точность последовательностей, нормализация текста). Промпты подаются без нормализации; оценщиков, проваливших attention-check, исключают; до 3 оценщиков на клип. Конкретных чисел по моделям в выгрузке нет. Самая высокая пропускная способность: Amazon Polly Standard, 1306.5 симв/с. AA прямо указывает, что тестирование **только на английском** — [AA Text-to-Speech](https://artificialanalysis.ai/text-to-speech) [verified]
- Динамика лидера AA в 2026: 2026-06-03 №1 Alibaba Fun-Realtime-TTS 1219 (962 появления), далее Gemini 3.1 Flash TTS 1214, Inworld Realtime TTS-2 Research Preview 1209, Cartesia Sonic 3.5 1203, «24 Elo между первой пятёркой» — [AA article, 2026-06-03](https://artificialanalysis.ai/articles/fun-realtime-tts-new-text-to-speech-model-topping-artificial-analysis-leaderboard) [snippet]; 2026-07-28 лидировал Speechify Simba 3.2 (1229), за ним Qwen-Audio-3.0-TTS-Plus 1227, Gemini 3.1 Flash TTS 1213 — [поисковая сводка, источник точно не установлен](https://offlinetts.com/blog/tts-model-ranking-2026/) [snippet, unverified]
- Лучшая open-weights модель на AA сейчас Breeze TTS 2 (Elo 1222), но лицензия research/non-commercial — [AA](https://artificialanalysis.ai/text-to-speech/leaderboard) [verified]; [Digital Applied](https://www.digitalapplied.com/blog/best-text-to-speech-models-september-2026-ranked-priced) [secondary]. Раньше выдача поиска называла лучшей открытой моделью Fish Audio S2 Pro (1123), без даты — [AA open-weights (snippet)](https://artificialanalysis.ai/text-to-speech/leaderboard/provider-voice/open-weights) [snippet, outdated]
- AA Controlled Voice Arena: все модели сравниваются на одном наборе из 8 клонированных голосов, чтобы отделить качество модели от удачности конкретного голоса. Анонс на X от 2026-07-08 (дата получена декодированием ID поста) — [AA on X](https://x.com/ArtificialAnlys/status/2074886571166462405) [snippet]

**Voice Arena (voicearena.com), US English, снято 2026-10-11** [verified] — [Voice Arena](https://voicearena.com)

| # | Модель | Elo ± CI | TTFA P50 | Голосов |
|---|---|---|---|---|
| 1 | Gemini 3.8 Flash-Lite TTS | 1083 ± 14 | нет данных | 734 |
| 2 | Eleven v4 | 1066 ± 12 | нет данных | 917 |
| 3 | Cartesia Sonic-3.6 Real-time | 1060 ± 13 | 340.5 ms | 1 123 |
| 4 | Gemini 3.8 Flash TTS | 1059 ± 12 | нет данных | 1 261 |
| 5 | Gemini 3.1 Flash TTS | 1054 ± 6 | 890 ms | 6 223 |

- Методология: Bradley-Terry Elo с 95% CI по вертикалям; по 60 предложений на поддомен, взятых из расшифровок реальных продакшн-звонков (банки, страхование, home services); голосование слепое, на задержку Elo не влияет; perceived TTFA = время до первого байта плюс тишина в начале клипа — [Voice Arena](https://voicearena.com) [snippet: методика взята из поисковой сводки по запросу «Voice Arena», первоисточник методики не открывался; сама таблица verified]
- Снимок 2026-09-25: Gemini 3.8 Flash-Lite 1087±15, Sonic-3.6 1068±13 (341 ms), Inworld Realtime TTS 2 1058±15 (169 ms, 4-е место), Speechify Simba 3.2 1032±6 (123 ms, 7-е) — [Digital Applied](https://www.digitalapplied.com/blog/best-text-to-speech-models-september-2026-ranked-priced) [secondary]

**Design Arena: TTS / «Audio arena», снимок 2026-09-02 (через зеркало benchmarklist.com)** [secondary] — [benchmarklist: Design Arena TTS](https://benchmarklist.com/arenas/design_arena_tts/)
- 1 Gemini 3.1 Flash TTS 1459; 2 Gemini 2.5 Pro TTS 1414; 3 Gemini 2.5 Flash TTS 1371; 4 Eleven v3 1290; 5 xAI Grok TTS 1254; 6 OpenAI GPT-4o Mini TTS 1225; 7 Microsoft MAI-Voice-2 1190; 8 Hume Octave 2 1162; 9 Cartesia Sonic 3.5 1149; 10 Inworld TTS-1.5 Max 1149. Всего 25 систем, число голосов не указано. В снимке нет Gemini 3.8 и Eleven v4, так что он **устарел** относительно AA. В другой выдаче поиска была версия с 1429/1423/1381 для тех же трёх Gemini — [поиск](https://benchmarklist.com/arenas/design_arena_tts/) [snippet]

**TTS Arena V2 (Hugging Face, TTS-AGI)**: таблицу получить не удалось. Space рендерится динамически, в отданной разметке только интерфейс голосования (счётчик «0/1000») — [HF Space](https://huggingface.co/spaces/TTS-AGI/TTS-Arena-V2), [hf.space](https://tts-agi-tts-arena-v2.hf.space/leaderboard). Блог offlinetts.com прямо пишет, что убрал свои ранее опубликованные таблицы TTS Arena, потому что не может их воспроизвести — [offlinetts](https://offlinetts.com/blog/tts-arena-leaderboard-2026/) [secondary]

**Другие лидерборды 2026 года**
- Open TTS Leaderboard (HF blog, 2026-09-30): объективные метрики (WER/CER через Qwen3-ASR, RTFx, TTFA, сходство спикера по WavLM), **только open-source модели**, коммерческих API нет. Топ English: Kokoro-82M, Supertonic-3, Fish S2 Pro; топ Multilingual: k2-fsa OmniVoice, Fish S2 Pro, Fun-CosyVoice3-0.5B — [HF blog: Open TTS Leaderboard](https://huggingface.co/blog/open-tts-leaderboard) [verified]
- Deccan AI VoxClash: слепые попарные сравнения плюс теги ошибок (mispronunciation, prosody, code-switch и т.д.); языки English (India), Hindi, Telugu; 9 моделей; публичная v1 «скоро», чисел нет — [Deccan VoxClash](https://deccan.ai/research/introducing-voxclash-a-tts-leaderboard-that-reveals-where-exactly-a-model-breaks-down) [verified]
- Coval TTS benchmark: объективные WER и TTFA (не предпочтения), см. раздел 3 — [Coval](https://benchmarks.coval.ai/tts)

### Inferences
- [inference] «Топ-10 сейчас» по AA (2026-10-11): Eleven v4 Turbo, Eleven v4, Qwen-Audio-3.1-TTS-Plus, Cartesia Sonic 3.6, Gemini 3.8 Flash TTS, Qwen-Audio-3.0-TTS-Plus, Inworld Realtime TTS-2, Gemini 3.8 Flash-Lite TTS, Speechify Simba 3.2, Breeze TTS 2. Модели, которые входят в топ-5 на обоих живых лидербордах (AA и Voice Arena): Eleven v4, Cartesia Sonic 3.6, Gemini 3.8 Flash TTS.
- [inference] Gemini 3.8 Flash TTS как базовый кандидат защищаем по связке «качество/цена»: топ-5 на AA и топ-4 на Voice Arena при цене примерно в 2.4 раза ниже Eleven v4 Turbo и в 4.8 раза ниже Eleven v4. Gemini 3.8 Flash-Lite ($11) на Voice Arena даже выше, на AA на 28 Elo ниже Flash. Эту пару стоит слепо сравнить на русском.
- [inference] Elo на AA «инфлирует»: 1219 был №1 в июне 2026, а сейчас это примерно 11-е место. Числа из разных месяцев сравнивать нельзя, только места внутри одного снимка.
- [inference] Все рейтинги построены на английских промптах «ассистент / колл-центр / нарратив». Короткие эмоциональные реплики персонажей фэнтези-RPG на русском они не измеряют.

### Gaps
- Актуальная таблица TTS Arena V2 (HF) не получена: динамический рендер. Насколько этот лидерборд вообще жив в 2026, не установлено.
- Первичная страница Design Arena не открывалась, есть только зеркало от 2026-09-02, где нет моделей Gemini 3.8 и Eleven v4.
- Точные CI AA на 2026-10-11 не извлечены (артефакт выгрузки); подставлены CI из снимка 2026-09-25.

## 2. Многоязычные и русскоязычные оценки: какие коммерческие модели лучше звучат по-русски

### Takeaway
На 2026-10-11 **ни один публичный human-preference лидерборд не включает русский**: у AA 9 неанглийских языков, у Voice Arena 10 дорожек, у VoxClash 3 языка, русского нет нигде. По русскому есть только объективные WER-бенчмарки, где ElevenLabs v3, MiniMax Speech 2.8 HD и Qwen-Audio-3.0 близки (WER ≈ 4.7–5.4%), а данных по Gemini для русского нет. Русскоязычные статьи на habr и vc.ru 2025–2026 в основном SEO-подборки без слепых тестов. Повторяющийся (не доказанный) консенсус такой: ElevenLabs v3 самый выразительный, Yandex SpeechKit лучше всех ставит ударения.

### Cited Findings
- AA Multilingual TTS Arena (анонс 2026-09-22, дата из ID поста): японский, китайский (мандарин), хинди, испанский, немецкий, французский, португальский, вьетнамский, арабский; **русского нет**. Голоса стандартизированы через клонирование, промпты написаны носителями, голосуют носители, Elo считается отдельно по языку; >100 000 голосов, 15–24 модели на язык. Результаты: Cartesia Sonic 3.6 №1 в 7 языках, Sonic 3.5 лидирует в португальском, Inworld Realtime TTS-2 №1 в мандарине (1185, против Sonic 3.6 1146 и StepAudio 2.5 1130); семейство Eleven v3 в топ-3 в 7 из 9 языков — [AA on X](https://x.com/ArtificialAnlys/status/2102490340678856997) [snippet: пост вернул HTTP 402, содержание взято из поисковой сводки]
- Voice Arena ведёт отдельные лидерборды для US English, японского, бразильского португальского, вьетнамского, арабского MSA, хинди, мексиканского испанского, мандарина, немецкого и французского; **русского нет** — [Voice Arena](https://voicearena.com) [verified]
- В VoxClash русского нет — [Deccan](https://deccan.ai/research/introducing-voxclash-a-tts-leaderboard-that-reveals-where-exactly-a-model-breaks-down) [verified]
- В HF Open TTS Leaderboard русский не упоминается, коммерческих API нет — [HF blog](https://huggingface.co/blog/open-tts-leaderboard) [verified]
- **CV3-Eval, русская таблица WER** (клонирование голоса, 16 языков), разбор на Хабре от 2026-09-15: Qwen-Audio-3.0-TTS **4.68**, ElevenLabs-v3 **5.38**, MiniMax-Speech-2.8-HD **5.39**, VoxCPM2 5.97, CosyVoice3-1.5B 6.64, CosyVoice3-0.5B 6.77, Qwen3-TTS-12Hz-1.7B-Base 7.40, Dots.TTS-2B 14.0. Вывод автора: метрика меряет «акустическую гладкость», а не качество (люди на китайском бенчмарке дают CER 1.26, модель 0.84), то есть WER уже мешает развитию TTS — [Хабр, Яндекс Практикум](https://habr.com/ru/companies/yandex_praktikum/articles/1080414/) [secondary, разбор статьи о Qwen-Audio-3.0]
- **Технический отчёт Qwen3-TTS, русский** (вендорские замеры, сравнение с коммерческими API): WER Qwen3-TTS-12Hz-1.7B 3.212, ElevenLabs 3.878, MiniMax 4.281; сходство спикера 0.792 / 0.676 / 0.761 соответственно — [Qwen3-TTS benchmarks (docs mirror)](https://www.mintlify.com/QwenLM/Qwen3-TTS/resources/benchmarks) [vendor/snippet]
- Хабр (bothub, 2026-09-12): прогон одного стресс-абзаца на 289 символов с 7 ловушками (склонение дат, суммы с НДС, «ё», омографы, римские цифры, номера ГОСТ, латиница внутри русского текста) на OpenAI tts-1/tts-1-hd, ElevenLabs v3 / Multilingual v2 / Flash v2.5 / Turbo v2.5, Google Gemini 3.1 Flash TTS / Chirp 3 HD, Yandex SpeechKit, Silero, Piper, FireRedTTS3. **Слепого прослушивания нет, баллов нет.** Вердикт: «ElevenLabs v3 даёт самый выразительный результат с паузами и контролем интонации», но с лимитом 5 000 символов на запрос; OpenAI берёт чёткостью; Yandex подходит для русскоязычных коммерческих проектов, где нужны закрывающие документы. Цены за 1 000 симв.: tts-1 ≈ 2.66 ₽, tts-1-hd ≈ 6.43 ₽, Eleven v3 ≈ 26.60 ₽ — [Хабр: Как озвучить текст с помощью ИИ в 2026 году](https://habr.com/ru/companies/bothub/articles/1081544/) [secondary]
- Повторяющиеся утверждения в русских подборках: «Yandex SpeechKit лучше всех для русского: ударения, сложные слова»; «SaluteSpeech и SpeechKit сопоставимы»; «ElevenLabs правильно ставит вопросительную, восклицательную и саркастическую интонацию» — [genvoice.ru](https://genvoice.ru/blog/chem-zamenit-elevenlabs), [toolfox.ru](https://toolfox.ru/blog/speechkit-salutespeech-whisper), [dtf.ru](https://dtf.ru/craft/5188057-luchshie-ii-servisy-dlya-ozvuchki-teksta) [secondary, SEO-подборки, низкая достоверность]
- В выдаче vc.ru «топ-рейтингов» встречается «слепой тест 2025 года на 2 000 человек, 68% не отличили сервис "Студия 24" от диктора» и «награда AI Russia 2025». Это маркетинговый контент, первоисточник не найден — [vc.ru top_rating](https://vc.ru/top_rating/3007030-sintez-i-klonirovanie-golosa) [snippet, unverified]
- Заявленное вендорами число языков: Gemini 3.8 Flash TTS 130, Gemini 3.8 Flash-Lite 101, ElevenLabs 70+, Fish Audio 83; русскоязычных бенчмарков в сентябрьском обзоре нет — [Digital Applied](https://www.digitalapplied.com/blog/best-text-to-speech-models-september-2026-ranked-priced) [secondary]
- Русскоязычное моддинг-сообщество о Skyrim/Mantella: XTTS даёт «разнообразные и хорошие» русские голоса, но для приемлемой задержки локально нужна RTX 3070 или лучше; у Piper нашлось всего 4 русских голоса, 2 из них «роботизированные» голоса Microsoft — [pikabu / ixbt (поисковая сводка)](https://pikabu.ru/story/skyrim__llm_mod_mantella_14214310/author) [snippet, unverified]

### Inferences
- [inference] Для русского нет внешнего арбитра, поэтому выбор провайдера по качеству придётся делать собственным слепым A/B на репликах Gothic: короткие грубые реплики, имена собственные (Хоринис, Ксардас, Миненталь), омографы, «ё». Это прямо следует из отсутствия русского во всех аренах.
- [inference] Русский WER у топовых коммерческих моделей укладывается в 3–5.5%. Разборчивость не будет главным отличием; отличать модели будут ударения в редких словах и именах, а также естественность и стабильность тембра.
- [inference] Доминирование Cartesia в 7 из 9 неанглийских языков AA и стабильное место Eleven v3 в топ-3 говорят о сильной мультиязычности этих семейств. Переносить это на русский без проверки нельзя.
- [inference] Gemini (3.1 / 3.8) не фигурирует ни в одном русском количественном сравнении. Это пробел для нашего базового кандидата.

### Gaps
- Не найдено ни одного слепого прослушивания русского TTS с числовыми результатами за 2025–2026 на habr или vc.ru (одна потенциально релевантная статья habr.com/ru/articles/1020424 вернула 403).
- Нет русских данных (WER, MOS, Elo) для Gemini 3.8 Flash TTS, Inworld, Cartesia Sonic 3.6, Eleven v4.
- Полная таблица CV3-Eval с русскими WER для Gemini и Cartesia не найдена.

## 3. Независимые замеры задержки (time-to-first-audio) потоковых API в 2026

### Takeaway
Основной независимый непрерывный замер TTFA делает Coval: open-source (Apache-2.0), только английский, с 2026-06-03 меряет «perceived TTFA» с учётом тишины в начале. Самые быстрые модели укладываются в 50–200 ms (Inworld Flash 2 75 ms, ElevenLabs Flash v2.5 185 ms), Cartesia Sonic 3.6 в 340–440 ms. Gemini 3.1 Flash TTS на Voice Arena показал **890 ms P50**, для Gemini 3.8 независимых замеров TTFA нет. Наш базовый кандидат, скорее всего, самый медленный в верхнем эшелоне качества.

### Cited Findings
- **Coval, 2026-09-08** (perceived TTFA P50, через публикации участников): Fluxions vui 51 ms (IQR 27–66, P95 96, WER 5.3%); Inworld Flash 2 75 ms (P95 111, WER 5.3%); ElevenLabs Flash v2.5 185 ms (WER 6.5%); Gradium TTS 214 ms (P95 346, WER 4.9%); Soniox TTS Rt v2 255 ms (P95 311, WER 4.0%); Cartesia Sonic 3.5 269 ms (WER 5.8%); ElevenLabs Eleven v3 Conversational 320 ms (WER 4.3%); Cartesia Sonic 3.6 440 ms (WER 5.3%) — [Gradium: TTS Latency Benchmark 2026](https://gradium.ai/content/tts-latency-benchmark-2026) [vendor: Gradium сам участвует в бенчмарке]; [поисковая сводка Coval](https://gradium.ai/blog/coval-perceived-ttfa-benchmark) [snippet]
- Методика Coval: 480 сэмплов на модель, окно 1 день; perceived TTFA включает тишину в начале клипа, которая «составляет 28%» общей задержки; TLS/WebSocket-рукопожатие (50–200 ms) и клонирование голоса исключены; только английский; WER считается через обратное STT с нормализацией jiwer. **Все цифры Coval до 2026-06-03 сняты по round-trip** и с новыми несопоставимы — [Gradium](https://gradium.ai/content/tts-latency-benchmark-2026) [vendor]
- Coval, 2026-05-04 (round-trip, старая методика): Gradium 155 ms, Cartesia Sonic-3 188 ms, ElevenLabs Turbo v2.5 264 ms, ElevenLabs Flash v2.5 288 ms, Deepgram Aura-2 313 ms, **OpenAI TTS-1-HD 2 295 ms** — [Gradium: Best Low-Latency TTS APIs 2026](https://gradium.ai/content/best-low-latency-tts-apis-2026) [vendor/snippet]
- Nari Labs: «Qwen3-TTS Fast» №2 по TTFA на Coval, P50 63 ms, WER 3.8% (2026-09-14) — [Nari Labs blog](https://narilabs.com/blog/nari-labs-leads-coval-voice-ai-benchmarks/) [vendor/snippet]
- Speechify: TTFA одной и той же модели Simba 3.2 на Coval снизился с 379 ms до 106 ms, то есть задержку определяет инфраструктура, а не модель — [Speechify blog (заголовок)](https://speechify.ai/blog/simba-3-2-first-audio-latency-coval) [vendor/snippet]
- **Voice Arena, TTFA P50** (снято 2026-10-11): Cartesia Sonic-3.6 340.5 ms; **Gemini 3.1 Flash TTS 890 ms**; для Gemini 3.8 Flash, Gemini 3.8 Flash-Lite и Eleven v4 стоит «NA» — [Voice Arena](https://voicearena.com) [verified]. Снимок 2026-09-25: Simba 3.2 123 ms, Inworld Realtime TTS 2 169 ms, Gradium 236 ms, Gemini 3.1 Flash TTS 890 ms (самый медленный из измеренных) — [Digital Applied](https://www.digitalapplied.com/blog/best-text-to-speech-models-september-2026-ranked-priced) [secondary]
- Вендорские заявления без проверки: Cartesia «sub-90 ms», ElevenLabs «~75 ms» для v4 Turbo — [Digital Applied](https://www.digitalapplied.com/blog/best-text-to-speech-models-september-2026-ranked-priced) [secondary/vendor]; Inworld TTS-1.5 (январь 2026): P90 130 ms у Mini и 250 ms у Max — [поисковая сводка](https://aiwiki.ai/wiki/inworld_ai/raw) [vendor/snippet]
- На лидерборде AA нет TTFA, есть только пропускная способность (чемпион Polly Standard, 1306.5 симв/с) — [AA](https://artificialanalysis.ai/text-to-speech) [verified]
- Полное время ответа в продакшне (не TTFA): у Latitude/Voyage Inworld TTS 0.9–1.4 s на вызов против ~4.3 s у «ближайшей альтернативы» — [Inworld case study: Latitude](https://inworld.ai/customers/latitude) [vendor]

### Inferences
- [inference] Если Gemini 3.8 Flash TTS ведёт себя как 3.1 (~0.9 s до первого звука в США), то вместе с TTFT LLM и сетью из России или Европы реплика NPC начнёт звучать примерно через 1.5–2.5 s после конца реплики игрока. Нужны конвейер по предложениям (первое предложение сразу в TTS) и собственный замер из нашей сети.
- [inference] Числа Coval и Voice Arena сняты в США/Европе на английском; рукопожатие исключено. Для нашего сервера держите постоянное соединение (keep-alive/WebSocket), иначе добавится 50–200 ms.
- [inference] Разброс «одна модель: 379 → 106 ms» показывает, что TTFA конкретного провайдера надо мерить у себя, на своём регионе и тексте; лидерборды дают только порядок величин.

### Gaps
- Первичная страница benchmarks.coval.ai рендерится JS-ом и не открылась; цифры Coval взяты у участников бенчмарка (Gradium, Speechify, Nari Labs), что создаёт конфликт интересов.
- Нет независимого TTFA для Gemini 3.8 Flash/Flash-Lite TTS, Eleven v4/v4 Turbo, Qwen-Audio-3.x, а также для неанглийского (русского) текста и из российских сетей.

## 4. Использование TTS для NPC в выпущенных играх и крупных модах (Inworld, ElevenLabs, ACE, Mantella, CHIM, Bannerlord, STALKER, Gothic)

### Takeaway
В коммерческих играх динамическую озвучку NPC выпустили немногие. Fortnite Darth Vader (ElevenLabs Flash v2.5 + Gemini 2.0 Flash, май 2025), PUBG Ally на NVIDIA ACE (бета с голосом), Latitude Voyage на Inworld TTS; Where Winds Meet и inZOI используют LLM без динамического голоса. В модах стандарт задают Skyrim-проекты: Mantella (Piper по умолчанию, xVASynth, XTTS, OpenAI-compatible) и CHIM (23 TTS-коннектора, от ElevenLabs и Inworld до Kokoro и XTTS). Для Gothic 2 уже есть мод с живой озвучкой через ElevenLabs (около 19 «голосовых слотов» и дисковый кэш) и англоязычная AI-озвучка Archolos с 70+ голосами из Qwen3-TTS VoiceDesign. Gothic-мода с динамическими LLM-репликами и русской озвучкой не найдено.

### Cited Findings
**Коммерческие игры**
- Fortnite, Darth Vader (с 2025-05-16): диалог генерирует Google Gemini 2.0 Flash, голос ElevenLabs Flash v2.5, воссоздан голос Джеймса Эрла Джонса. Джонс при жизни передал права на AI-воспроизведение голоса, наследники одобрили использование — [eWeek](https://www.eweek.com/news/fortnite-ai-darth-vader-sag-aftra-complaint/), [Droid Life, 2025-05-16](https://www.droid-life.com/2025/05/16/darth-vader-becomes-gemini-powered-conversational-ai-in-fortnite/) [secondary]. Игроки сразу заставили его материться — [Nintendo Life, 2025-05](https://www.nintendolife.com/news/2025/05/fortnite-turns-to-the-dark-side-with-ai-darth-vader-voice-and-of-course-its-been-swearing) [secondary]. SAG-AFTRA подала жалобу о нечестной трудовой практике (ULP) в NLRB против Llama Productions (дочка Epic) — [eWeek](https://www.eweek.com/news/fortnite-ai-darth-vader-sag-aftra-complaint/) [secondary]
- PUBG Ally (Krafton + NVIDIA ACE): AI-напарник с голосовым ответом на английском, корейском и китайском. Стек ACE включает UE5-плагины ASR, SLM и TTS. Бета в режиме Duos шла до 2026-06-30 — [Windows Central](https://windowscentral.com/hardware/nvidia/pubg-adds-ai-squadmates-that-listen-loot-and-fight-like-real-players-powered-by-nvidias-ace-tech), [Wccftech](https://wccftech.com/nvidia-ace-pubg-ally-ai-teammate-beta//amp/) [secondary/snippet]
- inZOI (Krafton, ранний доступ с 2025-03-28): «Smart Zoi» работают на on-device SLM на базе NVIDIA ACE, локально, без облака. Where Winds Meet (с конца 2025): свободный LLM-чат **текстом** для части второстепенных NPC, на выходе только текст и фиксированный набор игровых флагов. Инди-игра «Suck Up!» построена на уговорах GPT-жителей. По состоянию на середину 2026 ни одна AAA-игра не выпущена с основным квестовым диалогом, написанным AI — [Cinevva: AI NPCs and Dialogue in Games (2026-07)](https://app.cinevva.com/guides/ai-npcs-dialogue) [secondary]
- Latitude Voyage (AI-native RPG, open beta): весь нарратор и голоса NPC на Inworld; «15M+ TTS-вызовов за 6 недель»; 97% TTS-объёма на Inworld; TTS ≈ 6% всех AI-расходов, примерно четверть цены «действующего премиум-вендора» (имя не названо); 0.9–1.4 s на вызов против ~4.3 s у ближайшей альтернативы. Цитата: «inconsistency between dynamically generated voices was the deal-breaker» — [Inworld: Latitude](https://inworld.ai/customers/latitude) [vendor]
- Inworld в 2026 сместился от «NPC-студии» к B2B-инфраструктуре; в январе 2026 вышел TTS-1.5 — [поисковая сводка](https://www.eesel.ai/pt/blog/inworld-ai) [snippet]
- На GDC есть сессия Tencent «Real-time speech AI for games: squad comms AI teammates and esports commentary» (presented by AWS); содержание не получено — [GDC schedule](https://schedule.gdconf.com/session/real-time-speech-ai-for-games-with-tencent-squad-comms-ai-teammates-and-esports-commentary-presented-by-amazon-web-services/917546) [snippet]

**Skyrim / Fallout: Mantella**
- Репозиторий art-from-the-machine/Mantella: 416 звёзд, последний push 2026-07-16, релиз v0.14 вышел 2026-04-21. TTS-модули в `src/tts/`: `piper.py`, `xtts.py`, `xvasynth.py`, `openai_compatible.py` (шлёт запросы на `{URL}/v1/audio/speech`). ElevenLabs среди встроенных нет — [GitHub API: Mantella](https://github.com/art-from-the-machine/Mantella) [verified]
- Документация: Piper по умолчанию, «fast and local», «Piper only supports English»; xVASynth даёт «greatest emotional range»; XTTS даёт «highest quality», локально требует NVIDIA GPU (серия 5000 не поддерживается), внешне через RunPod от $0.14/ч — [Mantella docs: Installation](https://art-from-the-machine.github.io/Mantella/pages/installation.html) [verified]
- Приёмы против задержки в конфиге (`tts_definitions.py`): `number_words_tts` (по умолчанию 3, минимальное число слов в предложении перед отправкой в TTS); `fast_response_mode` (первую реплику играть прямо из exe, не через игру); `streamed_fast_response` (стримить первую реплику с TTS-сервера); `lip_generation` (Enabled/Lazy/Disabled, отключение «improve response times»); `allow_per_character_tts_overrides` (свой TTS на персонажа); `xtts_accent` (передаёт в XTTS язык из колонки `lang_override`, чтобы NPC с одной базовой моделью голоса звучали по-разному) — [Mantella tts_definitions.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/config/definitions/tts_definitions.py) [verified]
- Mantella распознаёт речь более чем на 20 языках, включая русский (крупное обновление, статья 2025-03-01) — [Respawn Media](https://respawn.media/gaming/3h61p-npc-v-skyrim-stali-ponimat-russkuiu-rech-funktsii-ii-moda-shokiruiut) [secondary]

**Skyrim: CHIM (бывший Herika)**
- Активный репозиторий Dwemer-Dynamics/HerikaServer (ветка `aiagent`, push 2026-10-09). В папке `tts/` **23 коннектора**: ElevenLabs (`tts-11labs`), Azure, Cartesia, Chatterbox, Convai, Coqui-AI, Deepgram, Google Cloud (`tts-gcp`), Higgs, Inworld, KoboldCpp, Kokoro, MeloTTS, Mimic3, OmniVoice, OpenAI, Piper, PocketTTS, StyleTTS2, XTTS, XTTS-FastAPI, xVASynth, Zonos (gradio). Плюс модули `tts_fallback` (резервный провайдер), `tts_pronunciation` (словарь произношений) и `player_tts`. Старый репозиторий abeiro/HerikaServer помечен как legacy — [GitHub API: Dwemer-Dynamics/HerikaServer](https://github.com/Dwemer-Dynamics/HerikaServer), [abeiro/HerikaServer](https://github.com/abeiro/HerikaServer) [verified]
- Старая рекомендация сообщества CHIM/Herika: ElevenLabs «produces the best results at the moment», но выше 10 000 символов нужна подписка; XTTS занимает около 2–3 GB VRAM, на CPU работает медленнее — [Nexus (поисковая сводка)](https://nexusmods.com/skyrimspecialedition/mods/113445) [snippet, дата не установлена, вероятно 2024]

**Mount & Blade II: Bannerlord**
- «Inworld Calradia» (моддер Bloc, 2023): GPT плюс TTS от Inworld AI — [eXputer](https://exputer.com/news/games/mount-and-blade-2-bannerlord-mod/) [secondary, устаревшее]
- Мод «ChatAi»: STT, а NPC озвучиваются через Player2 или Azure — [smods catalogue](https://catalogue.smods.ru/archives/408100) [snippet]

**STALKER Anomaly / GAMMA**
- TALKER (danclave): LLM-генератор реплик, срабатывающий на игровые события, у каждого NPC свой профиль личности; OpenAI-кредиты больше не обязательны, модель можно сменить. 57 звёзд, push 2026-08-20. Отдельный аддон TALKER-TTS (ForgottenHistory, push 2025-09-07); какой там TTS, не установлено (README 404) — [GitHub search via gh](https://github.com/danclave/TALKER) [verified метаданные], [описание (зеркало)](https://verifico.duckdns.org/danclave/TALKER) [secondary]

**Gothic 2**
- Мод автора oscarrc91 на Nexus (gothic2/mods/155): живая озвучка диалогов Gothic II **через собственный аккаунт ElevenLabs** «на языке, на котором вы играете». Каждый субтитр отправляется в момент реплики, аудио проигрывается вместо оригинала и **сохраняется на диск**. Повторные реплики (уличная болтовня, стандартные фразы героя, боевые крики) не генерируются заново. Массовой предгенерации нет. Озвучиваются **около 19 «голосовых слотов»**, как в оригинале, где сотни NPC делят небольшой набор голосов, а не каждый персонаж отдельно — [Nexus Mods: gothic2/mods/155](https://www.nexusmods.com/gothic2/mods/155) [snippet: страница вернула 403, описание из поисковой выдачи]
- «The Chronicles of Myrtana: Archolos — Full English AI Voiceover» (gothic2/mods/138): 70+ кастомных голосов, спроектированных через **Qwen3-TTS VoiceDesign** (предгенерированная озвучка, не динамическая) — [Nexus Mods: gothic2/mods/138](https://www.nexusmods.com/gothic2/mods/138) [snippet, 403], [ModDB: Archolos AI VO pack](https://www.moddb.com/mods/the-chronicles-of-myrtana-archolos-english-voice-over-pack-ai-generated)
- Другие англоязычные AI-озвучки модов Gothic 2 (статичные, не LLM): «Gothic 2 New Balance English Dubbing» (перевод через GPT-4, озвучка ElevenLabs), Awakening, Way of the Sleeper, Valendor, Velaya — [Nexus: New Balance dubbing](https://www.nexusmods.com/gothic2/mods/78), [Way of the Sleeper](https://www.nexusmods.com/gothic2/mods/112), [Valendor](https://www.nexusmods.com/gothic2/mods/114) [snippet]
- MelodyCheetah/gothic-2-ai-npc-edition: «every NPC in Khorinis now responds to open-ended questions»; LLM через Ollama или OpenAI-compatible API; упомянут «AI-generated voice in Gothic-like tone» без указания провайдера; 1 звезда, 5 коммитов — [GitHub](https://github.com/MelodyCheetah/gothic-2-ai-npc-edition) [verified]

**Платформы для NPC**
- Convai: оплата за взаимодействия, есть бесплатный тариф, инди-план $29/мес на 3 000 взаимодействий. Inworld: TTS около $5–25 за 1M символов (оценка гайда) — [Cinevva (2026-07)](https://app.cinevva.com/guides/ai-npcs-dialogue) [secondary]

### Inferences
- [inference] Паттерн «голосовые слоты + дисковый кэш» из Gothic-мода с ElevenLabs прямо переносится к нам: в оригинале около 19 актёрских голосов на сотни NPC. Это совпадает с идеей Voice Design (спроектировать N тембров и назначать их NPC) и ограничивает число голосов, за стабильностью которых нужно следить.
- [inference] Моддинг-сообщество почти не использует Gemini TTS: в CHIM коннектор только к Google Cloud (`tts-gcp`, вероятно Cloud TTS / Chirp), у Mantella его нет. Выбирая Gemini 3.8, мы отходим от проторенного пути, но не от лидербордов.
- [inference] Архитектура «провайдер-агностичный TTS-адаптер + fallback + словарь произношений + per-NPC override» (как в CHIM и Mantella) стала де-факто стандартом зрелых AI-NPC модов. Её стоит заложить с первого дня.

### Gaps
- Не найдено ни одного русскоязычного мода (Gothic, STALKER, Skyrim) с динамическими LLM-репликами и облачной русской озвучкой, по которому есть отчёт о качестве или задержке.
- Какой TTS используют TALKER-TTS и gothic-2-ai-npc-edition, не установлено.
- Страницы Nexus (403) и пресс-релиз Krafton (401) напрямую не открылись; модель TTS в PUBG Ally (NVIDIA Riva/Magpie или другая) не подтверждена.

## 5. Практические проблемы NPC-озвучки: бюджет задержки, кэш, стоимость часа игры, стабильность голоса, лицензии и согласие актёров

### Takeaway
Ориентир по задержке: голосовой NPC должен начать отвечать меньше чем через ~0.8–1 s, а облачный round-trip в 1–2 s игроки воспринимают как «зависание». Отсюда конвейер по предложениям и кэш повторяемых реплик. Стоимость TTS для нашего сценария копеечная: при Gemini 3.8 Flash около $0.2 за час игры (оценка), основной риск не в деньгах, а в стабильности тембра между генерациями. Юридически профсоюзные правила (SAG-AFTRA 2025) требуют явного письменного согласия на цифровые реплики голосов актёров. Клонировать голоса русского дубляжа Gothic без согласия даже в хобби-моде рискованно.

### Cited Findings
**Задержка**
- «Ответы быстрее ~800 ms ощущаются разговорными»; облачные round-trip добавляют 1–2 s, что воспринимается как «stall» — [Cinevva (2026-07)](https://app.cinevva.com/guides/ai-npcs-dialogue) [secondary]
- Голосовые NPC: round-trip меньше 1 s. Разбивка бюджета: сеть 30–50 ms, первая партиальная гипотеза STT 100–150 ms, TTFT LLM 200–300 ms (с префикс-кэшем), первый звук TTS 80–150 ms у стриминговых провайдеров, оркестрация и guardrail 50–100 ms. Хорошо оптимизированный локальный конвейер даёт 250–540 ms end-to-end, если стримить токены LLM в TTS по границам предложений — [FutureAGI: sub-500ms voice AI guide 2026](https://futureagi.com/blog/sub-500ms-voice-ai-guide-2026/) [secondary/snippet]
- Mantella режет ответ на предложения (минимум 3 слова), может стримить первую реплику и отключать генерацию lip-файлов ради скорости — [Mantella tts_definitions.py](https://github.com/art-from-the-machine/Mantella/blob/main/src/config/definitions/tts_definitions.py) [verified]

**Кэш**
- Gothic-мод с ElevenLabs сохраняет каждую сгенерированную реплику на диск и не перегенерирует повторяемые (уличные фразы, стандартные фразы героя, боевые крики). Генерация только по требованию — [Nexus: gothic2/mods/155](https://www.nexusmods.com/gothic2/mods/155) [snippet]
- В CHIM есть отдельные модули fallback-провайдера и словаря произношений — [HerikaServer](https://github.com/Dwemer-Dynamics/HerikaServer) [verified]

**Стоимость**
- Цены AA за 1M символов (2026-10-11): Gemini 3.8 Flash TTS $16.5; Gemini 3.8 Flash-Lite $11.0; Eleven v4 $80; Eleven v4 Turbo $40; Cartesia Sonic 3.6 $49; Inworld Realtime TTS-2 $20.8 / TTS-2 Flash $10.4; Qwen-Audio-3.1-TTS-Plus $19.3; Simba 3.2 $6.6; MiniMax Speech 2.8 HD $100; Eleven v3 $100 — [AA](https://artificialanalysis.ai/text-to-speech/leaderboard) [verified]
- Gemini 3.8 Flash TTS: $16.49/1M симв. ≈ $0.0135 за минуту аудио; Flash-Lite $11.03 ≈ $0.009/мин. Утверждается, что это **промо-цена до 2026-12-31, с 2027-01-01 она удвоится** — [Digital Applied](https://www.digitalapplied.com/blog/best-text-to-speech-models-september-2026-ranked-priced) [secondary, проверить у Google]
- Генерируемый диалог стоит денег «every single time it's produced, per NPC, per player, per conversation» — [Cinevva](https://app.cinevva.com/guides/ai-npcs-dialogue) [secondary]. У Latitude Voyage TTS составляет около 6% всех AI-расходов — [Inworld: Latitude](https://inworld.ai/customers/latitude) [vendor]

**Стабильность голоса**
- Latitude: непостоянство голоса между генерациями было «deal-breaker», потому что «a cast that shifts character between generations breaks the illusion» — [Inworld: Latitude](https://inworld.ai/customers/latitude) [vendor]
- AA специально ввёл Controlled Voice Arena (8 одинаковых клонированных голосов), чтобы отделить качество модели от качества голоса (2026-07-08) — [AA on X](https://x.com/ArtificialAnlys/status/2074886571166462405) [snippet]
- Mantella различает NPC с одной базовой моделью через подмену «акцента» XTTS (`xtts_accent`) — [Mantella](https://github.com/art-from-the-machine/Mantella/blob/main/src/config/definitions/tts_definitions.py) [verified]

**Лицензии и согласие**
- SAG-AFTRA Interactive Media Agreement 2025 ратифицирован **2025-07-09**, «за» проголосовали 95.04%. Это закончило почти годовую забастовку актёров видеоигр. Условия: отдельное письменное, «clear and conspicuous, reasonably specific» согласие на создание и использование цифровой реплики голоса; описание предполагаемого использования; компенсация за сгенерированные репликой выступления; отчёты об использовании реплик; право приостановить генерацию нового материала во время забастовки. Рост ставок +15.17% сразу и затем по 3% в год — [Frankfurt Kurnit (fkks)](https://ipandmedialaw.fkks.com/post/102ksuq/new-sag-aftra-2025-interactive-media-agreement-approved-by-members), [Decrypt](https://decrypt.co/329479/video-game-performers-ai-consent-rules?amp=1) [secondary]. Расхождение: fkks пишет «15.7% compounded», Decrypt пишет 15.17%.
- Даже при согласии наследников (Вейдер / Джеймс Эрл Джонс) профсоюз подал ULP-жалобу, потому что работу передали ИИ без переговоров — [eWeek](https://www.eweek.com/news/fortnite-ai-darth-vader-sag-aftra-complaint/) [secondary]
- Лицензии открытых моделей, популярных в модах: XTTS v2 под Coqui Public Model License (некоммерческая), F5-TTS под CC-BY-NC-4.0, Piper под MIT — [PromptQuorum](https://www.promptquorum.com/power-local-llm/local-tts-voice-cloning-piper-coqui-xtts) [secondary]; Kokoro 82M под Apache 2.0, Voxtral TTS под CC BY-NC 4.0, Breeze TTS 2 только для исследований и некоммерческого использования — [Digital Applied](https://www.digitalapplied.com/blog/best-text-to-speech-models-september-2026-ranked-priced) [secondary]

### Inferences
- [inference] **Оценка стоимости часа игры** (мой расчёт): реплика NPC из 1–3 предложений ≈ 150–250 символов; при ~60 репликах NPC в час это ≈ 12 000 символов/ч. Получается: Gemini 3.8 Flash ≈ $0.20/ч (≈ $0.40/ч после возможного удвоения цены в 2027), Flash-Lite ≈ $0.13/ч, Inworld TTS-2 ≈ $0.25/ч, Eleven v4 Turbo ≈ $0.48/ч, Eleven v4 ≈ $0.96/ч, Sonic 3.6 ≈ $0.59/ч, Simba 3.2 ≈ $0.08/ч. Даже непрерывная речь (≈ 50–55 тыс. симв./ч для русского, ≈ $0.0135/мин) обходится Gemini 3.8 Flash примерно в $0.8 за час звучания. Для одного игрока стоимость TTS не выбирает провайдера; важнее подписочные лимиты и RPM.
- [inference] Бюджет для нашего конвейера: LLM TTFT (сотни ms) + TTS TTFA (Gemini 3.1 ≈ 0.9 s, 3.8 неизвестно) + сеть. Цель «первый звук меньше ~1–1.5 s после реплики игрока» реальна только при стриминге LLM, отправке первого предложения в TTS сразу и постоянном соединении. Короткие ремарки («Хм…», «Чего тебе?») можно кэшировать заранее и проигрывать как филлер, маскируя задержку. Филлер я вывел сам; в источниках его нет, кроме кэша стоковых реплик в Gothic-моде.
- [inference] Стабильность тембра между генерациями нужно закрывать на уровне продукта: фиксированный «голосовой слот» на архетип NPC (как ~19 слотов оригинала), сохранённые параметры Voice Design или референс, регресс-проверка на эталонных фразах. Latitude прямо называет это главным критерием.
- [inference] Для хобби-мода профсоюзные правила SAG-AFTRA формально не применимы (русские актёры дубляжа, некоммерческий проект), но этический и правовой принцип ясен: клонировать узнаваемые голоса русской локализации Gothic без согласия актёров рискованно. Безопаснее синтетические голоса из Voice Design, которые лишь «в духе» оригинала.

### Gaps
- Не найдено ни одного GDC 2025–2026 доклада или постмортема с конкретными числами «стоимость TTS на час игры» или «доля кэш-хитов» для динамических NPC; есть только вендорский кейс Latitude.
- Российский правовой статус клонирования голоса (охрана голоса гражданина в ГК РФ, обсуждавшийся законопроект) в рамках этой задачи не исследован.
- Нет данных о том, как часто пользователи модов (Mantella, CHIM) реально выбирают облачные TTS против локальных; есть только рекомендации в документации.
