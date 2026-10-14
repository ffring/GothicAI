# Gate11: known PASS, load FAIL

Независимый known audit — **PASS**, issues пусты: закрыты3 shadow,
24 ordinary realtime и6 prepared reaction cases. Audit process wall —39,393 с;
внутренний reader wall —39,314284 с. Общего боевого gate PASS нет,
этап8 остаётся **IN_PROGRESS**, fresh screening не запускался.

В момент извлечения known отчёта load ещё не стартовал: прежний запуск получил
только точный отказ общего simulation lock, без launcher-load/service-before/
workload output или запросов. После завершения чужой physics-disabled regression
linux-v8 root подтвердил настоящий старт load в **17:47 UTC**: baseline защищённых
сервисов до engine lease/tick, затем12 cohorts native4/GRU4/MLP4 с прежними
fair20 before/during и бюджетом10%. Load теперь закрыт с **FAIL**, exit2:
native0–3 завершились по time_limit; GRU0/2/3 — control_lost, GRU1 — time_limit.
Следующий MLP0 остался partial: explicit release привёл к
`unexpected_control_loss:released`/infrastructure. Fresh не запускался;
partial не получает gate credit. Root unit wall —3:52,841, CPU —1:38,928,
memory peak —5,6G. [Закрытый causal/service отчёт](load-failure.md) сохранил
реальные handoff после completed RightStrafe и partial MLP target_invalid/
release. Native Decider/Gemma превысили10%; все GRU service windows invalid,
неполный прогон не получает gate credit.
Прежнее ожидание не является load-результатом или модельным отказом;
known JSON сохраняет статус именно на момент извлечения.

[Компактный JSON](known-summary.json) извлечён только из закрытого independent
report, без source/raw/weight/proof scans. Original report —1 368 003 байта,
SHA `8db1117197e669210090cb6f150182990b4a64474d7d78113fd2d45c4c9f1a6f`.
Сохранены33 episode summaries и94 исходные raw SHA bindings; все295 находятся
в связанном оригинале. Источник:
`stage8-final-gate-v11/validation/known-audit-v1/report.json` на dev-ubuntu.

Оба GRU reaction случая — настоящий task_success: по1 linked blocked hit,
received damage0, hp_preserved=true в подготовленной HP300 сцене,
neural control1 и runtime fallback0. Нет возврата к native в final.
Action9 начал исполнение через66 игровых мс после наблюдаемого windup
при настоящем incoming-hit window166 мс.

| Reaction side | Model process p95, мс (4 шага) | Collector → send, мс | Observation → arbitration, мс |
| --- | ---: | ---: | ---: |
| 0 | 1,168049 | 5,434115 | 6,862872 |
| 1 | 1,115430 | 5,925685 | 7,266407 |

| Максимальный case p95 | GRU, мс | MLP, мс |
| --- | ---: | ---: |
| Model process | 1,435901 | 1,408570 |
| Observation → arbitration | 7,266407 | 7,498032 |
| Collector → send | 5,925685 | 5,713087 |

Это максимумы отдельных case p95, а не pooled percentiles; case names и counts
сохранены. В зарегистрированном repaired наборе8 cases/model control minimum
—1,0 у GRU и MLP. GRU known distance/attack/prepared-block/duel — по2/2;
Native comparator duel1/2 и prepared block0/2. Эти закрытые известные seeds
не дают fresh/generalization credit. Старые neutral block diagnostics
сохранены отдельными cases и не переписаны в prepared block успех.

**1992 actual observation forwards**: shadow802, ordinary1174, reaction16.
Все CPU intra-op1. Known collector подтвердил native serial profile по
11484 response attestations (3625/7020/839); whole-process thread1 не заявлен.
Отдельные606 attestations прежнего CPUv2 diagnostic в эти counts не подмешаны.
Opt-in native deadline polling сохранил все periodic inputs и latency samples;
actual overshoot сохранён, просыпание за≤5 мс не обещается.

Source checkpoint GRU `e9a605…93b3eb`, MLP `d17752…073c9` и normalizer
`c6ee3a…438fe` неизменны. Четыре сервиса active/running/PID совпали внутри
before/after каждой закрытой known фазы; protected latency пока не измерялся.
[Motor20](../motor-v20/README.md)/[Queue21](../motor-v21-queue/README.md) —
native/transport PASS, все предыдущие Queue17/Gate10/load failures сохраняются.

При извлечении читался и хэшировался только закрытый report. Source, weights,
бинарники и raw-протокол не читались; модели, тесты, engine и сервисы не запускались.
Known PASS и native proofs сохранены; load FAIL не меняет веса и пороги p95,
control, age или visibility. Все следующие действия принадлежат root orchestration.
