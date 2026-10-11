# Кастомные голоса в Gemini 3.8 TTS и их доступность через OpenRouter

Дата исследования: 2026-10-11. Контекст: в MVP озвучки NPC нет (см. `docs/MVP.md`), заметка на будущее, когда дойдём до TTS.

Пометки:
- **[verified]**: проверено по первоисточнику (официальная документация Google или OpenRouter, публичный API OpenRouter).
- **[measured]**: я сам запросил публичный API OpenRouter с этого Mac.
- **[community]**: ветка на форуме разработчиков, официального подтверждения нет.
- **[secondary]**: новости или блоги.
- **[inference]**: мой вывод, не проверен на практике.
- **[unverified]**: утверждение встречалось, но подтвердить не удалось.

---

## TL;DR

1. **В Gemini 3.8 есть кастомные голоса.** Модели `gemini-3.8-flash-tts` и `gemini-3.8-flash-lite-tts` вышли 2026-09-23, обе GA. Поддерживают русский язык. Своих голосов два вида:
   - **Voice Design**: голос по текстовому описанию, согласие не нужно;
   - **Voice Replication**: клон по записи 10–30 с плюс запись согласия владельца голоса.
2. **Через OpenRouter кастомного голоса Gemini нет.**
   - Обе модели там есть, но доступны только 30 готовых голосов.
   - У всех эндпоинтов Gemini 3.8 (AI Studio и Vertex) стоит `supports_voice_cloning: false`.
   - Эндпоинта для создания голосов (аналога `/v1beta/voices`) у OpenRouter нет.
3. **Клонирование на OpenRouter есть только у других моделей**: `fish-audio/s2.1-pro` (включая `:free`) и `bytedance-seed/seed-audio-1-0`. Это stateless-клон: образец отправляется вместе с каждым запросом.
4. **Для NPC Gothic лучше всего подходит Voice Design напрямую через Gemini API.** Клонировать голоса актёров русской озвучки нельзя без их личного записанного согласия.

---

## 1. Gemini 3.8 TTS: что есть

### Модели [verified]

| Модель | ID | Статус | Языки | Цена за 1M токенов (вход / выход аудио) |
|---|---|---|---|---|
| Gemini 3.8 Flash TTS | `gemini-3.8-flash-tts` | GA | 130+, включая русский | $0.50 / $9 до 2026-12-31, затем $1 / $18 |
| Gemini 3.8 Flash-Lite TTS | `gemini-3.8-flash-lite-tts` | GA | 100+, включая русский | $0.50 / $6 до 2026-12-31, затем $1 / $12 |

- Free tier: все функции бесплатны.
- Создание голоса отдельно не тарифицируется (в прайсе отдельной строки нет).
- Flash Google позиционирует как вариант с максимальным качеством, Flash-Lite — для массовой генерации.
- Готовые голоса: 30 базовых (Kore, Puck, Charon, Fenrir…) плюс библиотека из 2000+ голосов с региональными акцентами и персонажами.
- Стриминг: `stream: true` отдаёт headerless PCM 16-bit LE. Unary-запрос возвращает WAV.
- Мультиспикер: до 2 говорящих в одном запросе, только с готовыми голосами.
- Все сгенерированные записи помечаются водяным знаком SynthID.
- Google обещает «voice remix»: подстройку тембра, высоты, темпа и акцента у существующего голоса. Срока нет.

### Создание голоса [verified]

Оба вида голосов создаются одним эндпоинтом `POST /v1beta/voices` (`client.voices.create`). Нужен SDK Python 2.25.0+ или JS 2.24.0+.

**Voice Design** (`type: "prompted"`):

```python
created_voice = client.voices.create(
    store=True,
    voice={
        "model": "gemini-3.8-flash-tts",
        "type": "prompted",
        "display_name": "Warm British Astronomer",
        "gender": "male",
        "language_code": "en-GB",
        "prompted": {"input": "A warm, thoughtful astronomer..."},
    },
)
```

- В ответе приходят `voice_...` ID и `sample_audio`: превью в base64 WAV.
- Совет Google: постоянные черты голоса (возраст, пол, тембр, акцент) задавать при создании голоса. Ситуативную эмоцию задавать через `style` в каждом запросе.

**Voice Replication** (`type: "replicated"`):

- `replicated.source_audio`: одна запись 10–30 с, чистая речь. Рекомендуемый формат: WAV 24 кГц, моно, 16 бит.
- `replicated.consent_audio`: владелец голоса дословно читает фразу согласия на одном из 30 поддерживаемых языков. Для `ru-RU`: «Я являюсь владельцем этого голоса и даю согласие Google на использование этого голоса для создания модели синтетического голоса.»
- Регионы, где клонирование недоступно: Иллинойс, Техас, ЕЭЗ, Великобритания, Швейцария, Индия (по блогу Google). Voice Design этими ограничениями не затронут.

### Хранение [verified]

- `store=True`: голос получает `voice_...` ID. Лимит 200 голосов на проект (общий для prompted и replicated), TTL 1 год. Голоса привязаны к проекту.
- `store=False` (только для replication): API возвращает зашифрованный самодостаточный ключ `voicekey_...`, хранить его должен клиент. TTL 7 дней. Можно ли использовать его из другого проекта или с другим API-ключом, в документации не сказано [unverified].

### Использование голоса [verified]

- Interactions API: `speech_config: [{"voice": "<voice_... или voicekey_...>"}]`.
- generateContent (REST): `"speechConfig": {"voiceConfig": {"voice": "voice_..."}}`.

### Live API (realtime-диалог)

- В документации TTS сказано, что TTS отличается от генерации речи в Live API [verified].
- По веткам форума, голоса из `/v1beta/voices` в `gemini-3.8-live` пока не работают. Кастомный голос в Live (`replicated_voice_config`) выдаётся по allowlist [community].
- Вывод: для живого голосового диалога с NPC на своём голосе пока остаётся только связка LLM-текст → TTS.

---

## 2. OpenRouter

### Что есть [verified + measured]

- TTS-эндпоинт: `POST /api/v1/audio/speech` (OpenAI-совместимый). Поля: `model`, `input`, `voice`, `instructions`, `response_format` (`mp3`/`pcm`, по умолчанию `pcm`), `speed`, `input_references`, `provider`. Стриминг есть.
- Модели `google/gemini-3.8-flash-tts` и `google/gemini-3.8-flash-lite-tts` (slug `...-20260922`). Цены такие же, как у Google: $0.50 / $9 и $0.50 / $6 за 1M токенов. Два провайдера: `google-ai-studio` и `google-vertex/global`.
- `supported_voices`: только 30 базовых голосов (Zephyr, Puck, Charon, Kore, Fenrir, Leda, Orus, Aoede…). Библиотеки из 2000+ голосов нет.
- Для Gemini поле `instructions` уходит в `speech_metadata.style`. Остальные поля `speech_metadata` можно передать через `provider.options.google-ai-studio`. Прочие provider options пробрасываются как generation config. Свой `speechConfig` в provider options «forwarded as-is» и заменяет тот, что собирает OpenRouter. В документации это описано в разделе про мультиспикер.

### Что не работает

- **Клонирования нет** [measured]. `GET /api/v1/models/google/gemini-3.8-flash-tts/endpoints` и `.../gemini-3.8-flash-lite-tts/endpoints` для обоих провайдеров возвращают `supports_voice_cloning: false`, `supports_multiple_audio_references: false`, `supports_image_reference: false`. Значит, `input_references` к Gemini не уйдёт.
- **Создать голос через OpenRouter нельзя** [verified]: эндпоинта `/voices` и управления голосами у него нет. Voice Design на OpenRouter недоступен. Можно только задать манеру через `instructions` поверх одного из 30 готовых голосов.

### Обходной путь (не проверен) [inference]

1. Создать голос напрямую в Google (`/v1beta/voices`) и передать его ID через OpenRouter: `provider.options.google-ai-studio.speechConfig.voiceConfig.voice`.
2. Проблема: голос с `store=True` привязан к нашему Google-проекту, а OpenRouter ходит в Google от своего проекта. Скорее всего, такой ID там не найдётся.
3. Варианты, которые теоретически могут сработать:
   - `voicekey_...`: он самодостаточный, но переносимость между проектами не задокументирована;
   - BYOK: OpenRouter умеет BYOK для Vertex (запросы идут с нашими ключами, OpenRouter берёт 5% сверху). Применяется ли BYOK к `/audio/speech` и поддерживается ли BYOK для AI Studio, в документации не сказано [unverified].
4. Вывод: если всё равно нужен свой Google-ключ, проще вызывать Gemini API напрямую.

### Клонирование на OpenRouter у других моделей [measured]

Опрос всех 32 моделей из `GET /api/v1/models?output_modalities=speech`: `supports_voice_cloning: true` только у трёх.

| Модель | Клон | Несколько образцов | Образ по картинке |
|---|---|---|---|
| `fish-audio/s2.1-pro` | да | нет | нет |
| `fish-audio/s2.1-pro-free:free` | да | нет | нет |
| `bytedance-seed/seed-audio-1-0` | да | да | да |

- Это stateless-клон: образец передаётся в `input_references` (base64 `input_audio`) в каждом запросе.
- Поддержку русского у этих моделей я не проверял [unverified].
- У ElevenLabs, MiniMax, Qwen, MAI-Voice, Grok и Gemini на OpenRouter клонирования нет. У ElevenLabs оно есть в их собственном API, но OpenRouter его не пробрасывает.

---

## 3. Что это значит для проекта [inference]

- **Озвучка NPC.** Voice Design в Gemini 3.8 Flash TTS напрямую через Google: голос на каждого ключевого NPC по описанию (например, Ксардас: пожилой, низкий, властный, неторопливый). Потом эмоцию реплики задаём через `style`. Лимит 200 голосов на проект для MVP с запасом.
- **Голоса оригинальной русской озвучки.** Клонировать их нельзя: Google требует записанного согласия самого владельца голоса. Делать стороннее stateless-клонирование (Fish и Seed на OpenRouter) без прав мы тоже не стали бы.
- **OpenRouter в TTS-адаптере.** Его можно добавить как бэкенд с 30 готовыми голосами Gemini и `instructions`. Для кастомных голосов нужен отдельный бэкенд на Gemini API. Это укладывается в схему со сменными провайдерами (как LLM-адаптер и ASR).
- **Задержка.** Для реплик NPC пригодится стриминг PCM. Время до первого аудио я не замерял.

### Что проверить экспериментом

1. Создать голос через Voice Design на русском (`language_code: "ru-RU"`) и оценить качество и время до первого чанка при стриминге.
2. Проверить, работает ли сохранённый `voice_...` или `voicekey_...` через OpenRouter в `speechConfig` (сначала без BYOK, потом с BYOK для Vertex).
3. Проверить, что Gemini API доступен из страны, где будет работать сервер.

---

## Источники

- Google:
  - [Блог: Gemini 3.8 Flash TTS и Flash-Lite TTS](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-8-text-to-speech/) (2026-09-23)
  - [Docs: Voice design](https://ai.google.dev/gemini-api/docs/voice-design)
  - [Docs: Voice design через generateContent](https://ai.google.dev/gemini-api/docs/generate-content/voice-design)
  - [Docs: Voice replication](https://ai.google.dev/gemini-api/docs/voice-replication)
  - [Docs: Speech generation](https://ai.google.dev/gemini-api/docs/speech-generation)
  - [Docs: Pricing](https://ai.google.dev/gemini-api/docs/pricing)
  - [Модель Gemini 3.8 Live](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live)
- OpenRouter:
  - [Docs: Text-to-Speech](https://openrouter.ai/docs/guides/overview/multimodal/tts)
  - [Docs: Audio output](https://openrouter.ai/docs/features/multimodal/audio)
  - [Docs: BYOK](https://openrouter.ai/docs/guides/overview/auth/byok)
  - [Модель google/gemini-3.8-flash-tts](https://openrouter.ai/google/gemini-3.8-flash-tts)
  - Публичный API: `https://openrouter.ai/api/v1/models?output_modalities=speech` и `https://openrouter.ai/api/v1/models/<id>/endpoints` (запрошено 2026-10-11)
- Сообщество:
  - [Форум: Gemini 3.8 Live и кастомные голоса](https://discuss.ai.google.dev/t/gemini-3-8-live-support-for-voice-design-custom-voices/184651)
  - [Форум: allowlist для replicated_voice_config в Live](https://discuss.ai.google.dev/t/allowlist-request-custom-voice-replicated-voice-config-for-gemini-3-8-live-on-vertex-ai-project-my-asist-ai/186167)
- Вторичные: [Android Authority](https://www.androidauthority.com/gemini-3-8-flash-text-to-speech-rolling-out-3714915/), [Phil Schmid](https://www.philschmid.de/gemini-3-8-tts), [Guillaume Laforge (Java SDK)](https://glaforge.dev/posts/2026/09/24/gemini-3-8-flash-tts-and-custom-voices-with-the-interactions-java-sdk/)
