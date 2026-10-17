# Serial workers: CPU diagnostic v2

Закрытый no-model probe на бинарнике `a83efa28236ff9917b7e7d60a4a508de6feedd31d8d9ff4bc69adeb20ab777b7`, с startup `--serial-workers`. Frozen saved audit независимо пересчитан и полностью совпал с сохранённым audit. Все 606 native replies подтверждают `serial=true`, `mode=caller_serial`, `worker_pool_created=false`, `native_task_parallelism_cap=1`.

Оба профиля используют realtime native duel seed 223 / side 1 / 400 см / timeout 10 000 мс, одинаковые save/settings/scenarios, polling 20 мс и sampler 20/50 мс. Фактические лимиты обоих запусков: `cpu.max=200000 100000`, `memory.max=6442450944`. Lease, policy actions и модели отсутствуют; outcome — настоящий `time_limit`.

| Измерение | Default v1 | Serial v2 |
| --- | ---: | ---: |
| Active ticks | 300 | 300 |
| Active tick median, ms | 5.489 | 18.259 |
| Active tick p95, ms | 7.487 | 24.127 |
| Active tick max, ms | 15.849 | 31.107 |
| Active ticks >33 ms | 0 | 0 |
| CPU всей systemd service, s | 16.383 | 12.564 |
| Wall всей service, s | 16.208 | 16.501 |
| Cgroup CPU в sampled window, μs | 12 675 860 | 8 795 425 |
| Throttles / throttled μs в sampled window | 3 / 228 311 | 0 / 0 |

В этом единичном сравнении serial уменьшил CPU всей service на 23.31%, а wall вырос на 1.81%. Отдельный tick выполнялся медленнее. Service costs включают setup, сбор и сохранение данных; sampled window имеет другую область измерения. Это наблюдение о расходе CPU и parallelism, не доказательство улучшения tick latency.

601 external samples наблюдали 1–2 threads; потоков с именем `Workers [n]` нет. Дополнительный loading thread относится к процессу, а native task cap=1 описывает dispatch Workers, не глобальный предел всех threads процесса.

[comparison.json](comparison.json) хранит фактический argv, worker mode, SHA raw journals, source/library manifest и frozen Gate11 archive/environment, вместе со сравнением [v1](../native-cpu-profile-v1/README.md). [external-metadata.json](external-metadata.json), launcher logs, resource report и JUnit — компактные точные копии. Mutable remote source/library не использованы как доказательство закрытых предыдущих запусков. Raw journals, бинарники и веса не скопированы в репозиторий.

Gate10 in-fight failure здесь не воспроизведён. Этот diagnostic не доказывает его причину или исправление, не содержит NN-проверки и не даёт quality, selection, model-gate либо fresh-eval credit. Native mechanical parity оценивается отдельно в root-owned Motor20.
