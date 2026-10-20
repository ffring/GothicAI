# Проверка генерации 3D: бочка, сид 1

## Результат первого окна

2026-10-20, dev-ubuntu, RTX 3090 24 ГБ, 46 ГиБ RAM без swap. TRELLIS.2 прошёл сэмплинг формы и материала `1024_cascade`, но при декодировании ядро убило процесс из-за нехватки **оперативной памяти**. Это первая ошибка генератора; повторов пока нет. TRELLIS v1 не запускался: launcher прекратил работу после первой модели. `raw.npz`, `meta.json` и превью не созданы.

Вход — настоящий концепт `~/.local/share/gothic-ai/content-sources/mesh3d-pilot/barrel-v1.png` из [PR #312](https://github.com/kaifaty/GothicAI/pull/312), RGBA 1297×1212, альфа 0…255. SHA-256: `fd2e697f9c5627fdcdb5e9c828812e5708c2a3cb1761f1ea616b69fe7d4ee84f`. Сид **1**, модель CLI — `both`. GLB, запекание и запрещённые растеризаторы/удаление фона не использовались.

```sh
pgrep -lx Gothic2Notr || echo no game
scripts/content/mesh3d/run-mesh3d.sh \
  --image ~/.local/share/gothic-ai/content-sources/mesh3d-pilot/barrel-v1.png \
  --seed 1 --model both
```

Перед окном: `no game`. Launcher ограничен 30 минутами: 1200 с минус прошлый простой на генерацию, 600 с на восстановление; суммарный простой ≤1800 с.

| Показатель | TRELLIS.2 `1024_cascade`, low_vram | TRELLIS.2 512 | TRELLIS v1 |
|---|---:|---:|---:|
| Попыток | 1 | 0 | 0 |
| Результат | SIGKILL, exit 137, host OOM | не запускался | не запускался |
| Время процесса по событиям launcher | 349 с | — | — |
| Пик torch allocated/reserved | не записан: процесс убит до `finally` | — | — |
| Максимум GPU, наблюдавшийся в выборочных `nvidia-smi` | ≥19 373 MiB, включая ASR/TTS | — | — |
| Анонимный RSS процесса при OOM-kill | 29 801 656 KiB = 29 103,18 MiB (28,42 ГиБ) | — | — |

**Выборочный максимум `nvidia-smi` — нижняя граница, а не измеренный пик генератора.** Пик torch восстановить после SIGKILL нельзя. При остановленных Gemma/Decider было свободно 17 180 MiB GPU. Ядро записало `global_oom` и убило именно Python генератора; CUDA OOM не возник и обработчик повтора на 512 не получил управление.

## Окно и восстановление

Run ID: `20261020T121151Z-seed1-11485`; данные на сервере — `~/gothic-ai/mesh3d/runs/<run-id>/`, события — `events.tsv`, простой — `downtime.tsv` в папке mesh3d.

| Событие | UTC |
|---|---|
| Окно открыто | 12:11:52 |
| Gemma stop-request / stop | 12:11:53 |
| Decider stop-request / stop | 12:11:54 |
| TRELLIS.2 start | 12:11:54 |
| TRELLIS.2 exit 137; обоим сервисам отправлен start | 12:17:43 |
| Gemma отвечает | 12:17:50 |
| Decider отвечает; окно закрыто | 12:18:28 |

Простой считаем консервативно от `stop-request` до успешной проверки ответа: **Gemma 357 с**, **Decider 394 с**. Общий интервал простоя — **395 с (6 мин 35 с)**, полное окно — 396 с. Voxtral/VoxCPM не останавливались. После восстановления GPU занят на **18 981–18 989 MiB** (до окна — 18 989 MiB). Проверка ответов: Gemma `/health` HTTP 200, Voxtral `/health` HTTP 200, Decider и VoxCPM HTTP 426 (живой WebSocket-сервер отклоняет запрос без Upgrade); backend VoxCPM `/health` HTTP 200. Все четыре systemd-юнита active.

## Подготовленная правка и следующий шаг

Загрузчик теперь пропускает неиспользуемые flow-веса: для `1024_cascade` — texture flow 512; для 512 — оба flow 1024. В ветке 512 это убирает около 5,2 ГБ ненужных весов из RAM; выбранные веса и сид не меняются. Правка проверена по обращениям upstream-конвейера, но повторная генерация ещё не проверена.

Поскольку разрешённое одно окно уже завершилось, дополнительное окно для 512 и v1 ожидает решения пользователя. До него на Mac в `~/.local/share/gothic-ai/content-staging/mesh3d-pilot/smoke/` копировать нечего, два CPU-превью отсутствуют. CPU-рендерер `scripts/content/mesh3d/preview_raw.py` подготовлен и проверен на синтетическом меше.

Проверки: `sh scripts/content/mesh3d/check-generate.sh` — **8 passed за 1,17 с**, без GPU и сети; синтаксис Python/Bash и `git diff --check` прошли.
