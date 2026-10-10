# Gothic AI

Управление NPC в Gothic 2 NotR с помощью LLM, а дальше новые механики и генерация сюжетных линий.

Движок: форк [OpenGothic](https://github.com/kaifaty/OpenGothic) (upstream [Try/OpenGothic](https://github.com/Try/OpenGothic)). Он лежит в `OpenGothic/` как отдельный репозиторий. ИИ работает в отдельных сервисах, а в игре остаётся только тонкий мост.

## Структура

| Путь | Что |
|---|---|
| `docs/MVP.md` | Спецификация MVP «один живой NPC»: решения, объём работ, вехи |
| `server/` | ИИ-сервер (Python): соединение с игрой, сценарии. Дальше здесь будут память, оркестратор и адаптер LLM |
| `docs/protocol.md` | Протокол игра ↔ сервер |
| `scripts/e2e-m0.sh` | Сквозная проверка: сервер + новая игра, управление Ксардасом |
| `asr-service/` | Сервис распознавания речи со сменными провайдерами *(появится в M3)* |
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

ИИ-сервер. Ждёт игру на `ws://127.0.0.1:47817` и принимает команды из консоли: `attach`, `goto`, `come`, `ani`, `routine`, …

```bash
cd server && uv run gothic-ai serve
```

Игра в режиме разработчика (консоль Marvin по F2, там доступны `ai status` и `ai attach <npc>`):

```bash
OpenGothic/build/opengothic/Gothic2Notr -g "/Volumes/shared/projects/Gothic II" -devmode
```

Тесты сервера и сквозная проверка:

```bash
cd server && uv run pytest
```

```bash
scripts/e2e-m0.sh
```
