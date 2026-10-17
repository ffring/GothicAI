# Gate v16: боевой допуск этапа 8 — PASS

15 октября 2026 года, Ryzen 9 3950X (без виртуализации, Ubuntu 26.04). Независимые
known- и load-аудиты и финальный отчёт прошли без замечаний:
`implementation_ready`, `checkpoint_ready`, `combat_gate_ready` — true. Сжатая
сводка с хэшами полных отчётов: [summary.json](summary.json). Полные данные
остаются на Ryzen в `~/gothic-ai/body/data/stage8-final-gate-v16`.

Не менялись: веса GRU/MLP (`e9a605…93b3eb` / `d17752…073c9`), normalizer, 20 свежих
seed, пороги качества (task success не хуже native на 15 п.п., neural control ≥ 0,95,
model p95 ≤ 5 мс, collector → send ≤ 20 мс, observation → arbitration ≤ 100 мс),
strict masks/freshness и 100-мс age guard.

## Свежий screening: 20 новых seed, lockstep

| Задача | Native | GRU | MLP |
|---|---:|---:|---:|
| distance | 20/20 | 20/20 | 20/20 |
| attack | 20/20 | 20/20 | 20/20 |
| block | 0/20 | 20/20 | 20/20 |
| native_duel | 9/20 | 20/20 | 20/20 |

## Realtime

| Прогон | Neural control (min) | Model p95, мс | Collector → send p95, мс | Observation → arbitration p95, мс |
|---|---:|---:|---:|---:|
| Обычные сценарии (16 нейронных эпизодов) | 1,0 | 1,24 | 2,01 | 3,38 |
| Защитная реакция (4) | 1,0 | 1,30 | 7,27 | 8,83 |
| Нагрузка: 4 NPC в одном мире, 8 нейронных когорт | 0,989 | 1,14 | 3,72 | 5,98 |

Блок начинается через 66 игровых мс после замаха при окне удара 166 мс; runtime fallback — 0.

## Что изменилось по сравнению с остановкой Codex

См. [отчёт о продолжении](../2026-10-15-stage8-resume/README.md): FAI-доворот исполнителя
при выходе цели из конуса зрения, пропущенное решение вместо release, порядок шагов
коллектора, разнесённые heartbeat. Proof нового runner на Ryzen: `stage8-motor-v26`
(полная native parity с эталоном, API, realtime, visibility, compact, faults) и
`stage8-motor-v27` (очередь). Прежние отказы v1–v14 привязаны в регистрации; v13 и v15
заморожены, но не регистрировались.

## Ограничения

- **Сервисы не измерялись.** Роль `remote_host_not_measured`: на Ryzen нет ASR, TTS,
  Decider и Gemma, поэтому общего CPU с ними нет. `service_gate_ready=false`. Сервисный
  отчёт остаётся отдельным на dev-ubuntu (последний — Gate11 native: Decider и Gemma выше
  10%). Метка `stage8_readiness_scope` в v16 ошибочно говорит
  `combat_runtime_and_candidate_services`, правильно — `combat_runtime`. Исправлено для
  следующих запусков.
- Screening идёт в lockstep: политика решает без задержки обработки. Задержки проверены
  отдельно в realtime.
- Это допуск этапа 8. Релизный gate на 100 парных seed с доверительными интервалами
  (план, этапы 9–10) ещё впереди; `release_ready=false`.
