# Живое аудио OpenAI (Realtime, GPT-Live) для голосов NPC: подходит ли нам и можно ли кастомные голоса

Дата: 2026-10-11. Вопрос: годится ли «живой» голосовой сервис OpenAI для Gothic AI и можно ли поставить свои голоса.

Пометки:
- **[замер]** — проверено нашим токеном «Sign in with ChatGPT» 2026-10-11 (скрипты-зонды, вне репозитория);
- **[док]** — официальная документация OpenAI или Microsoft Foundry, страница прочитана;
- **[вторичный]** — новости, блоги, форум;
- **[вывод]** — мои рассуждения.

## Коротко

1. **Через подписку ChatGPT голос OpenAI получить нельзя.** Наш токен пускает только к распознаванию (`gpt-realtime-whisper`, уже используем). Realtime открывает сессию, но любой ответ падает с `server_error` и нулевым расходом токенов. GPT-Live, TTS и эндпоинты кастомных голосов отвечают 401. [замер]
2. **Остаётся платный API-ключ.** Ориентир: `gpt-realtime-2.1` около $0,10 за минуту разговора, `-mini` около $0,03, GPT-Live $0,05 за минуту сессии плюс бэкенд. [вывод из цен]
3. **Кастомные голоса у OpenAI есть, но нам недоступны.**
   - Они только для «eligible customers» через отдел продаж, до 20 голосов на организацию. [док]
   - Нужна запись согласия: сам владелец голоса читает фразу согласия. Поэтому голоса актёров русского дубляжа Gothic так не получить, даже если бы доступ был. [док] Это совпадает с нашим решением в MVP не клонировать голоса актёров.
4. **Встроенные голоса** — 10 в Realtime и 13 в TTS. Это голоса «ассистентов», на русском они звучат с нейтральным, неродным акцентом. Стиль, темп и эмоцию можно задать инструкцией, тембр нельзя. [док, вторичный]
5. **Вывод: нам не подходит, и замена не нужна.** В `main` уже есть голоса NPC (коммит `b0284f6`): `tts-service` с VoxCPM2 на dev-ubuntu. Голос создаётся по текстовому описанию из персоны, первый звук через 0,07 с — то есть свои русские голоса у нас уже есть, причём без клонирования людей. OpenAI live-аудио по сравнению с этим:
   - заменило бы мозг NPC (`gpt-6.1-sol` с персонами, памятью и инструментами) голосовой моделью;
   - требует платного ключа;
   - даёт только стоковые «ассистентские» голоса.
   
   Как запасной провайдер по ключу в `tts-service` уже есть `openai` (`/audio/speech`). [вывод]

## Что есть у OpenAI на октябрь 2026

| Модель | Что делает | Цена (API) | Источник |
|---|---|---|---|
| `gpt-realtime-2.1` | Речь → речь по очереди, рассуждения «класса GPT-5», инструменты, контекст 128K. Вышла 2026-07-06, p95 задержки на 25 % ниже, чем у `gpt-realtime-2` (май 2026) | $4 текст / $32 аудио на вход, $64 аудио на выход за 1M токенов | [вторичный: DataNorth, форум OpenAI] |
| `gpt-realtime-2.1-mini` | То же, быстрее и дешевле, слабее | $0,60 / $10 / $20 | [вторичный: DataNorth] |
| `gpt-live-1` | Полный дуплекс: слушает и говорит одновременно, сам решает, когда отвечать. Рассуждения и инструменты **делегирует** бэкенду: Responses-модели или нашему приложению. В API с 2026-09-10 | $0,05 за минуту сессии (тишина тоже считается) + бэкенд отдельно | [док: модель, Foundry] |
| `gpt-realtime-whisper` | Потоковое распознавание (мы уже используем как запасной ASR) | $0,017/мин | [вторичный: форум] |
| `gpt-realtime-translate` | Синхронный перевод | $0,034/мин | [вторичный: форум] |
| `gpt-4o-mini-tts` (снимок 2025-12-15) | Обычный TTS с полем `instructions` (акцент, эмоция, темп, шёпот) | по токенам, порядка $0,015/мин | [док]; цена по памяти, не перепроверял |

Оценка цены минуты разговора для Realtime [вывод]: аудиотокены идут примерно по 10 в секунду на вход и 20 в секунду на выход. Для `2.1` это около $0,02 за минуту речи игрока и $0,08 за минуту речи NPC, плюс текст персоны и истории на каждом ходу (с кэшем дёшево). Для `mini` примерно втрое дешевле.

Задержка: OpenAI приводит смену реплик за 0,80 с у GPT-Live против 1,41 с у `gpt-realtime-2.1` [вторичный: форум, со слов OpenAI]. У нас сейчас первые слова текста появляются через 2,6–2,8 с (`gpt-6.1-sol`).

## Доступ через нашу подписку [замер]

Токен из `~/.config/gothic-ai/chatgpt.json`, клиент `dynamic_agent_client`.

| Что | Результат |
|---|---|
| `GET /v1/models` | 200, но список пустой; `GET /v1/models/<id>` → 403 `Missing scopes: api.model.read` |
| Realtime `wss://…/v1/realtime?model=gpt-realtime-2.1` (а также `-2.1-mini`, `gpt-realtime-2`, `gpt-realtime`) | `session.created` приходит за 0,6–1,7 с, `session.update` принимается. Но `response.create` сразу даёт `response.done` со статусом `failed`, `server_error`, usage = 0. Так при любом входе: аудио PTT или текст, и при любом выходе: аудио или только текст |
| То же через эфемерный ключ (`POST /v1/realtime/client_secrets` → 200, `ek_…`) | Тот же `server_error` |
| `wss://…/v1/live/sessions` (GPT-Live) | 401 `rejected_by_access_enforcement / no_matching_rule` |
| `POST /v1/audio/speech` (`gpt-4o-mini-tts`) | 401 `rejected_by_access_enforcement` |
| `GET /v1/audio/voices`, `/audio/consent_phrases`, `/audio/voice_consents` | 401 `rejected_by_access_enforcement` |

На status.openai.com в это время инцидентов по Realtime и Audio не было, поэтому `server_error` — это, скорее всего, ограничение маршрута подписки, а не сбой. Похоже на `missing_compute_residency_info` для `gpt-4o-*-transcribe` (см. память проекта). На форуме писали, что OAuth-маршрут «потом получил поддержку» новых версий, но у нас 2026-10-11 это не подтвердилось. Сессии для обращения в поддержку: `sess_EXilvl3Qc9dhH3EtHAsce`, `sess_EXimynGvYuSCPD5vZ6Wd8`.

## Голоса

**Встроенные:**
- Realtime: `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, `marin`, `cedar`. Лучшее качество у `marin` и `cedar`. [док]
- TTS: те же плюс `fable`, `nova`, `onyx`, всего 13. [док]
- GPT-Live-1: свой набор из 12 — Quartz, Ripple, Vesper, Willow, Stone, Gleam, Meridian, Bossa, Tempo, Beacon, Delta, Cinder. [док]

**Ограничения:**
- В Realtime голос нельзя сменить после первого звука в сессии. Значит, одна сессия на одного NPC. [док]
- В GPT-Live голос и инструкции задаются при старте и не меняются. [док: Foundry]
- Языки: «голоса оптимизированы под английский». Русский поддерживается, но акцент «нейтрализован», отдельного русского произношения нет. [док TTS, вторичный]
- В языковой справке GPT-Live перечислены варианты английского, бразильский португальский и филиппинский; русского нет. Microsoft прямо пишет, что качество по языкам не гарантировано. [док]
- Правила OpenAI требуют явно сообщать слушателю, что голос сгенерирован ИИ. [док TTS]

**Кастомные голоса** ([guide](https://developers.openai.com/api/docs/guides/custom-voices)) [док]:
- Доступ: «limited to eligible customers», включается для организации через продажи. После включения в консоли появляется вкладка Voices.
- Создание:
  1. Запись согласия: владелец голоса читает одну из готовых фраз. Фразы есть на 16 языках, **включая ru**. Любое отклонение от текста — отказ.
  2. Образец голоса до 30 с: wav, mp3, ogg, flac и др.; рекомендуют студийный микрофон и тихую комнату.
  - Модель копирует всё: тембр, темп, энергию, паузы, манеру.
- API: `POST /v1/audio/voice_consents`, `POST /v1/audio/voices`, `GET /v1/audio/consent_phrases`. Голос подключается как `"voice": {"id": "voice_…"}` в TTS, Realtime, Chat Completions с аудио и GPT-Live.
- Лимит: **не больше 20 голосов на организацию**.
- В GPT-Live у кастомного голоса поддерживаются только английские акценты; образец — минимум 5 с речи.

Что это значит для нас [вывод]:
- Подписка не даёт доступа, а «eligible customers» — это корпоративные клиенты через продажи. Для личного проекта шансов мало.
- Клонировать голоса актёров дубляжа без их согласия нельзя ни по правилам OpenAI, ни по нашему решению в MVP. Свои голоса возможны только с живыми людьми, которые запишут согласие.
- Лимит в 20 голосов, кстати, хорошо лёг бы на игру. В Gothic 2 у NPC есть номер голоса (`DIA_<Npc>_<Info>_<voice>_<n>`, у Ксардаса 14, у героя 15), и голосов в игре около двух десятков.

## Подходит ли нам [вывод]

| Вариант | Плюсы | Минусы для Gothic AI |
|---|---|---|
| **Realtime S2S** (`gpt-realtime-2.1`) вместо цепочки ASR → LLM → текст | Одна модель, ответ голосом примерно через 1,4 с, инструменты работают, русский понимает | Нужен платный ключ. Мозг NPC — голосовая модель, а не `gpt-6.1-sol`: персоны, память и сводки придётся перенести в инструкции сессии. Голоса «ассистентские», с неродным акцентом. Нужно воспроизводить звук в игре (сейчас реплика — субтитры через `AI_Output` с анимацией губ). Нет контроля над текстом до того, как он прозвучал |
| **GPT-Live-1** + наш сервер как бэкенд (client delegation) | Архитектурно ближе всего к нам: голосовой слой отдельно, мозг наш. Естественные перебивания | Нужен платный ключ. Русского нет в списке языков. Наш текст модель **пересказывает**, а не читает дословно (`session.commentary.append`), то есть NPC скажет не то, что написал мозг. Полный дуплекс с VAD против нашего push-to-talk (есть только `input_audio.mute`). Платим за каждую минуту открытой сессии, включая тишину |
| **Цепочка + отдельный TTS-провайдер** (текст `gpt-6.1-sol` → голос) | Ничего не ломаем: персоны, память, инструменты, субтитры остаются. TTS — ещё один провайдер со сменными реализациями, как ASR и LLM. Можно выбрать сервис с хорошим русским и своими голосами | Добавляется задержка синтеза (у потоковых TTS 0,1–0,3 с до первого звука, по предложениям). Голос по-прежнему нужно проигрывать в игре |

**Рекомендация:** OpenAI live-аудио не брать. Третий вариант уже реализован в `main` (`tts-service`, VoxCPM2 с голосами по описанию). Там же выяснилось, что голос Codex через `codex app-server` на подписке работает только по WebRTC и пересказывает текст, поэтому как TTS не годится — это совпадает с поведением GPT-Live (`commentary` пересказывается).

Если VoxCPM2 не устроит по качеству русского, есть другие варианты своих голосов без клонирования людей:
- **Qwen3-TTS** (Alibaba, Apache 2.0, январь 2026). Есть VoiceDesign — голос по текстовому описанию — и клонирование по 3 с образца. Русский поддерживается, потоковый синтез от 97 мс, 1,7B параметров. [док HF]
- **ElevenLabs**: Voice Design, v3, Flash около 75 мс, 70+ языков. Облако и платно; есть страновые ограничения сервиса. [вторичный]
- **OpenAI `gpt-4o-mini-tts`** (провайдер `openai` в `tts-service`): дёшево, управляется инструкцией, но только 13 стоковых голосов и нужен API-ключ. [док]

## Что можно сделать дальше

- Если хочется услышать русский у `marin`/`cedar`/`ash` или замерить S2S на наших фразах, нужен API-ключ OpenAI с балансом. Зонд готов и требует копейки: 3 ответа по 5 с.
- Ключ кладём вне репозитория, в `~/.config/gothic-ai/openai.env` (`OPENAI_API_KEY=…`, права 600), рядом с токенами подписки. Сервер, `asr-service` и `tts-service` читают именно `OPENAI_API_KEY`.

## Источники

- [OpenAI: Custom voices (guide)](https://developers.openai.com/api/docs/guides/custom-voices)
- [OpenAI: Text to speech (guide)](https://developers.openai.com/api/docs/guides/text-to-speech)
- [OpenAI: Realtime conversations (guide)](https://developers.openai.com/api/docs/guides/realtime-conversations)
- [OpenAI: GPT-Live 1 model](https://developers.openai.com/api/docs/models/gpt-live-1), [Live conversations](https://developers.openai.com/api/docs/guides/live-conversations), [Live delegation](https://developers.openai.com/api/docs/guides/live-delegation)
- [Microsoft Foundry: What is GPT-Live?](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/gpt-live), [Use GPT-Live for real-time voice](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/gpt-live)
- [OpenAI Community: New Realtime Voice Models in the API](https://community.openai.com/t/new-realtime-voice-models-in-the-api/1380471), [Introducing GPT-Live-1 in the API](https://community.openai.com/t/introducing-gpt-live-1-in-the-api/1396471)
- [OpenAI: Updates for developers building with voice](https://developers.openai.com/blog/updates-audio-models)
- [DataNorth: gpt-realtime-2.1](https://datanorth.ai/news/openai-releases-gpt-realtime-2-1-voice-models)
- [TechCrunch 2026-05-07](https://techcrunch.com/2026/05/07/openai-launches-new-voice-intelligence-features-in-its-api/)
- [Rime: GPT-Live API capabilities and limits](https://www.rime.ai/resources/openai-gpt-live-api-capabilities-limits-and-alternatives) (конкурент OpenAI)
- [Hugging Face: Qwen3-TTS-12Hz-1.7B-VoiceDesign](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)
- [Fora Soft: ElevenLabs alternatives 2026](https://www.forasoft.com/blog/article/elevenlabs-alternatives)
- [OpenAI Status](https://status.openai.com/)
