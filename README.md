# Gothic AI

Управление NPC в Gothic 2 NotR с помощью LLM, а дальше новые механики и генерация сюжетных линий.

Движок: форк [OpenGothic](https://github.com/kaifaty/OpenGothic) (upstream [Try/OpenGothic](https://github.com/Try/OpenGothic)). Он лежит в `OpenGothic/` как отдельный репозиторий. ИИ работает в отдельных сервисах, а в игре остаётся только тонкий мост.

## Структура

| Путь | Что |
|---|---|
| `docs/MVP.md` | Спецификация MVP «один живой NPC»: решения, объём работ, вехи |
| `server/` | ИИ-сервер (Python): сессии, память, оркестратор, адаптер LLM *(появится в M0)* |
| `asr-service/` | Сервис распознавания речи со сменными провайдерами *(появится в M3)* |
| `reports/` | Итоговые исследования |
| `research_notes/` | Заметки исследований с источниками |
| `OpenGothic/` | Форк движка, отдельный git-репозиторий (здесь игнорируется) |

## Сборка и запуск игры (macOS)

```bash
git clone --recurse-submodules https://github.com/kaifaty/OpenGothic.git
cmake -S OpenGothic -B OpenGothic/build -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build OpenGothic/build
OpenGothic/build/opengothic/Gothic2Notr -g "/Volumes/shared/projects/Gothic II"
```
