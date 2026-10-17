# Native + model CPU diagnostic v1

Закрытый дополнительный diagnostic на старом бинарнике `681a1704364e07836f839c3e4382b16ae43b28ae3bf75fb46f7884e2b10a1359` и неизменённых frozen Gate10 driver/runtime/checkpoints. Root выполнил полный ordinary realtime phase из 24 сценариев; wrapper добавил только native `--scheduling-diagnostics`. Sampler работал вокруг одного заранее заданного `realtime-native_duel-223-1-gru`, включая reset. Фактические лимиты: `cpu.max=200000 100000`, `memory.max=6442450944`.

| Scope | Ticks | p95 wall, ms | Max wall, ms | >33 ms |
| --- | ---: | ---: | ---: | ---: |
| Active target arena | 394 | 7.698 | 11.589 | 0 |
| Reset + active target | 410 | 7.958 | 74.033 | 3 |

В target получены 263 model forwards, 25 accepted commands, 0 rejected commands и 0 runtime fallbacks; настоящий outcome — `death_or_down`. Это диагностические наблюдения, не качество модели и не повторное прохождение gate. 680 внешних samples наблюдали 17–18 threads.

Все три ticks >33 мс относятся к reset bootstrap, до `arena_start`:

| Bootstrap tick | Wall, ms | Cgroup throttle increment в bracket | Main runqueue в bracket, ms | CPU PSI full increment, ms |
| --- | ---: | ---: | ---: | ---: |
| 1 | 74.033 | 0 / 0 μs | 0.011 | 0.026 |
| 9 | 73.593 | 1 / 222 372 μs | 54.871 | 53.806 |
| 15 | 69.344 | 1 / 350 976 μs | 55.761 | 58.499 |

В этих brackets отсутствует увеличение major faults, disk `read_bytes` и IO PSI. Для двух bootstrap stalls наблюдается совпадение с throttling, main-thread runqueue и CPU pressure. Counter brackets шире tick и включают соседнюю работу; cgroup включает collector/sampler, а thread samples имеют отдельный cadence. Это временная ассоциация, не доказательство причинности и не точный per-tick CPU. Перекрывающиеся brackets нельзя складывать как независимые измерения.

[summary.json](summary.json) сохраняет исходные SHA bindings и три slow-tick joins с thread counters. [profile.json](profile.json) и [external-metadata.json](external-metadata.json) — точные компактные копии wrapper provenance и sampler metadata. Проверены все frozen imported-source hashes и wrapper raw-artifact hashes. Старые source/library привязаны к сохранённым manifest и immutable Gate10 archive/environment, без повторного хеширования изменяемого remote checkout после новой сборки.

Активная задержка и fallback Gate10 здесь не воспроизведены; причина Gate10 остаётся не установленной этим diagnostic. Quality, selection, model-gate и fresh-eval credit отсутствуют. Raw journals, checkpoints и бинарники остаются на dev-ubuntu; original failed Gate10 сохранён.
