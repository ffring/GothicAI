# Gate v5: latency failure в known reaction

Known audit — **FAIL**. Единственный issue:
`block-reaction-223-0-gru: model_step_ms missing/exceeds budget`.
Серии 3 shadow, 24 обычных realtime и 6 reaction эпизодов завершены.
Load и fresh screening не запускались; общего боевого PASS нет.

GRU выполнил настоящий block9 на обеих сторонах: HP300, neural control 1,
без runtime/native fallback. Первый управляющий шаг side0 занял 1,243341 мс,
backend inference — 0,781786 мс; collector → send — 6,157565 мс.
Успешная своевременная защита не отменяет отдельный latency gate.

| Периодический снимок | Режим | Receive → process begin, мс | Process wall, мс | Backend inference, мс |
| --- | --- | ---: | ---: | ---: |
| 5, game 16361 | Решение block9, dt0 | 4,743063 | 1,243341 | 0,781786 |
| 7, game 16427 | Locked, memory-only | 0,166033 | **18,962995** | **18,459441** |
| 9, game 16461 | Locked, memory-only | 0,134932 | 0,792226 | 0,385123 |
| 11, game 16527 | Locked, memory-only | 0,272421 | 1,248120 | 0,775954 |

Process wall пересчитан по process BEGIN/END коллекторного runtime.
Поле `model_step_ms` в исходном latency journal здесь равно backend inference;
в сводке эти значения сохранены раздельно. Всплеск второго шага превышает
неизменный порог **5 мс**, остаётся в аудитируемой последовательности и
не исключается как неуправляющий шаг.

Перед серией два GRU warmup forward заняли 0,949641 и 0,206949 мс.
Они используют synthetic zero input, сбрасывают returned hidden и не получают
боевого зачёта. Поштучных CPU/thread measurements для четырёх реальных шагов
нет: OS stall как причина всплеска **не доказан**.

[Компактный JSON](known-failure.json) сохраняет все четыре шага и их clocks,
warmup, реальные command/execution события блока, оба аудированных GRU
reaction результата, исходные issues и 24 source SHA bindings плюс suite SHA.
Выбранные веса и normalizer прежние — [defence-v2](../defence-v2/selection.json).

Следующий gate-v6 планируется с private CPU backend opt-in: один thread,
при прежнем default 2. Веса, normalizer и quality thresholds сохраняются;
результат нового прогона пока не измерен. [Native14](../motor-v14/README.md)
и [queue15](../motor-v15-queue/README.md) остаются отдельными успешными proof.

Оригиналы неизменны:
`/home/kaifat/gothic-ai/body/data/stage8-final-gate-v5/` на dev-ubuntu.
Сводка получена read-only; при её сохранении модели, тесты и симуляции
не запускались, raw журналы, веса и бинарники не копировались целиком.
