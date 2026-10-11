# Gothic AI

Управление NPC в Gothic 2 NotR с помощью LLM, а дальше новые механики и генерация сюжетных линий.

Движок: форк [OpenGothic](https://github.com/kaifaty/OpenGothic) (upstream [Try/OpenGothic](https://github.com/Try/OpenGothic)). Он лежит в `OpenGothic/` как отдельный репозиторий. ИИ работает в отдельных сервисах, а в игре остаётся только тонкий мост.

## Структура

| Путь | Что |
|---|---|
| `docs/MVP.md` | Спецификация MVP «один живой NPC»: решения, объём работ, вехи |
| `server/` | ИИ-сервер (Python): соединение с игрой, диалоги, персоны, память, голосовой ввод, адаптер LLM, сценарии |
| `asr-service/` | Сервис распознавания речи со сменными провайдерами: Voxtral Realtime на GPU-сервере, подписка ChatGPT, OpenAI-совместимые API. `deploy/voxtral/` — развёртывание Voxtral на dev-ubuntu |
| `tts-service/` | Сервис синтеза речи для голосов NPC со сменными провайдерами: VoxCPM2 на GPU-сервере, Inworld TTS-2 и OpenAI-совместимые API по ключу. `deploy/voxcpm/` — развёртывание на dev-ubuntu |
| `libs/chatgpt-auth/` | Вход через ChatGPT (OAuth PKCE) и общее хранилище токенов для сервера и ASR-сервиса |
| `docs/protocol.md` | Протокол игра ↔ сервер |
| `scripts/e2e.sh` | Сквозная проверка: сервер + новая игра. `SCENARIO=m0` — управление Ксардасом, `m1` — диалог, `m2` — действия, `m3` — память и голос |
| `scripts/make-dev-app.sh` | Обёртка отладочной сборки в `OpenGothic Dev.app` |
| `reports/` | Итоговые исследования |
| `research_notes/` | Заметки исследований с источниками |
| `OpenGothic/` | Форк движка, отдельный git-репозиторий (здесь игнорируется) |

## Сборка и запуск (macOS)

Игра (ветка `ai-bridge` форка):

```bash
git clone --recurse-submodules -b ai-bridge https://github.com/kaifaty/OpenGothic.git
cmake -S OpenGothic -B OpenGothic/build -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build OpenGothic/build --target Gothic2Notr
```

Вход в ChatGPT. Один раз: откроется браузер, токены сохранятся в `~/.config/gothic-ai/chatgpt.json`.

```bash
cd server && uv run gothic-ai login chatgpt
```

Проверить модель и задержку:

```bash
cd server && uv run gothic-ai llm-test "Кто ты?"
```

ASR-сервис (для голоса) сам моделей не запускает. По умолчанию он распознаёт потоком через Voxtral Mini 4B Realtime на dev-ubuntu, а если тот недоступен — через подписку ChatGPT (Realtime API, `gpt-realtime-whisper`). Провайдеры меняются флагами `--provider` и `--fallback` (`voxtral[:url=…]`, `chatgpt`, `openai:base_url=…,model=…,api_key_env=…`). Развёртывание Voxtral описано в `asr-service/deploy/voxtral/install.sh`.

```bash
cd asr-service && uv run gothic-asr serve
```

Сравнить провайдеры на своих записях (16-битный WAV). `--realtime` подаёт звук в темпе речи, как при push-to-talk:

```bash
cd asr-service && uv run gothic-asr transcribe tests/audio/*.wav --provider voxtral --fallback none --realtime
```

TTS-сервис (голоса NPC) сам моделей не запускает. По умолчанию он берёт VoxCPM2 на dev-ubuntu. Голос каждого NPC создаётся один раз по текстовому описанию из персоны, это не клон актёра. Если сервис недоступен, NPC говорят только субтитрами.

```bash
cd tts-service && uv run gothic-tts serve
```

Провайдер меняется флагами `--provider` и `--fallback` или переменными `GOTHIC_TTS_PROVIDER` и `GOTHIC_TTS_FALLBACK`. Ключи API сервис берёт из ближайшего `.env` выше текущей папки, то есть из `.env` в корне репозитория (он в `.gitignore`).

- `inworld` — Inworld Realtime TTS-2 по ключу `INWORLD_API_KEY`. Голос NPC один раз создаётся по описанию из персоны (Voice Design), публикуется в библиотеку голосов Inworld с тегом `gothic-ai` и запоминается в `~/.local/share/gothic-ai/inworld-voices.json`. Готовый голос из библиотеки задаётся опцией `voice` (например, `inworld:voice=Dmitry`); описание из персоны тогда управляет подачей. Более быстрая модель без управления подачей — `inworld:model=inworld-tts-2-flash`.

```bash
cd tts-service && uv run gothic-tts serve --provider inworld
```

Послушать и замерить (`--out` сохраняет последнюю реплику в WAV):

```bash
cd tts-service && uv run gothic-tts say "Драконы уже здесь." --voice-id xardas --voice "An old man with a deep calm voice" --out /tmp/x.wav
```

ИИ-сервер. Ждёт игру на `ws://127.0.0.1:47817` и принимает команды из консоли: `attach`, `goto`, `come`, `ani`, `talk <текст>`, `memory`, `routine`, … Бэкенд LLM выбирается флагом `--llm chatgpt|openai|local|fake`, модель — `--model`. Память NPC хранится в `~/.local/share/gothic-ai/memory.sqlite3` по веткам сохранений; голос пишется с микрофона Mac (`--no-voice` выключает). NPC говорят вслух через TTS-сервис (`--no-tts` оставляет только субтитры).

```bash
cd server && uv run gothic-ai serve
```

Проверить микрофон и распознавание (macOS спросит доступ к микрофону для терминала):

```bash
cd server && uv run gothic-ai voice-test
```

Игра в режиме разработчика. Чат с NPC открывается клавишей **T**, когда игрок на него смотрит; Enter отправляет, Esc закрывает. **Удерживайте T, чтобы сказать реплику голосом** (в закрытом чате удержание сразу открывает его и пишет речь). Консоль Marvin открывается по F2, там доступны `ai status` и `ai attach <npc>`:

```bash
OpenGothic/build/opengothic/Gothic2Notr -g "/Volumes/shared/projects/Gothic II" -devmode
```

Тесты (в каждом проекте свои):

```bash
cd server && uv run pytest
```

```bash
cd asr-service && uv run pytest
```

```bash
cd tts-service && uv run pytest
```

```bash
cd libs/chatgpt-auth && uv run pytest
```

Сквозная проверка. Запускает свои сервер и игру на отдельных портах; сохранения и память остаются во временной папке:

```bash
SCENARIO=m3 ASR=voxtral scripts/e2e.sh
```
