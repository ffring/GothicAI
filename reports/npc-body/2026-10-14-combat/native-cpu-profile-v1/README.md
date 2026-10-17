# Native CPU diagnostic v1

Закрытый standalone no-model probe на старом бинарнике `681a1704364e07836f839c3e4382b16ae43b28ae3bf75fb46f7884e2b10a1359`: realtime native duel, seed 223, side 1, дистанция 400 см, timeout 10 000 мс. Запущен root на dev-ubuntu с фактическими `cpu.max=200000 100000` и `memory.max=6442450944`. Lease, policy commands и модели отсутствуют; завершение — настоящий `time_limit`.

| Scope | Ticks | p95 wall, ms | Max wall, ms | >33 ms |
| --- | ---: | ---: | ---: | ---: |
| Active arena | 300 | 7.487 | 15.849 | 0 |
| Reset + active | 316 | 7.820 | 74.794 | 2 |

582 внешних samples; во время запуска число наблюдаемых threads менялось от 1 до 17. За всю запись cgroup counters увеличились на 3 throttles / 228 311 μs. Один active tick counter bracket содержит увеличение throttle counter, но bracket захватывает соседнюю работу reset/startup. Это не точное измерение CPU или throttling отдельного tick.

Saved audit повторно вычислен неизменённым frozen helper `8137e8e9…` и полностью совпал с сохранённым `audit.json`. [summary.json](summary.json) содержит привязки к старому source/library manifest, immutable Gate10 source archive/environment, полным удалённым журналам и интервалы slow bootstrap ticks. [external-metadata.json](external-metadata.json) — точная копия данных о лимитах и scope sampler.

В активной арене задержка Gate10 не воспроизведена. Этот diagnostic не доказывает причину Gate10 и не даёт quality, selection, model-gate или fresh-eval credit. Текущие изменяемые source/library после новой сборки не использованы как доказательство старого бинарника. Raw journals, веса и бинарники остаются на dev-ubuntu.
