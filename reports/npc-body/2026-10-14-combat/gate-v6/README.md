# Gate v6: закрытый known audit

**Known audit — PASS**, issues пусты. Завершены 3 shadow, 24 обычных realtime
и 6 reaction эпизодов. Audit process wall — 42,099 с; внутренний reader wall —
42,019438 с. Этап 8 остаётся **IN_PROGRESS**: следующий [load завершился
FAIL](load-failure.md), fresh screening не запускался. Общего боевого зачёта нет.

Выбранные веса, normalizer, seeds и quality thresholds сохранены.
Private CPU profile использует PyTorch intra-op **1** при default 2;
interop остаётся **12**. Closed reader проверил профиль каждого из
**2205 настоящих forward calls**: shadow 802, ordinary 1387, reaction 16.
Warmup в эти реальные observation calls не входит. Frozen focused tests:
runtime CPU profile 48 PASS, final evaluation 63 PASS.

| Архитектура | Максимальный model p95, мс | Максимальный observation → arbitration p95, мс | Максимальный collector → send p95, мс | Минимальный healthy neural control |
| --- | ---: | ---: | ---: | ---: |
| GRU | 1,440461 | 9,690933 | 5,542793 | 1,0 |
| MLP | 1,364769 | 9,381393 | 5,833228 | 1,0 |

Это **максимумы отдельных case p95**, не общий pooled percentile.
Model maximum GRU относится к distance side0 (29 samples). Arbitration/send
maximum может иметь один sample; counts и соответствующие case сохранены
в JSON. Модельные шаги памяти входят в прежний latency budget.

Оба GRU reaction блока фактические: action9 начал исполнение через
66 игровых мс после наблюдаемого замаха при входящем окне 166 мс.
На каждой стороне final HP300, received damage0, blocked hits1,
neural control1, fallback0. Reaction model p95 — 1,202355/1,320276 мс
по четырём шагам; collector-send — 5,542793/5,542474 мс.
Обычные neutral block результаты также сохранены: GRU 0/2, time_limit;
они не переписаны как успех реактивного benchmark.

[Компактный JSON](known-summary.json) содержит метрики всех 33 case,
агрегаты фактических execution profiles, HP/effect proof для двух блоков,
33 выбранные raw SHA bindings и SHA оригинального reader report.
Все **295** исходных raw bindings остаются в связанном оригинале;
во время активного load большие журналы повторно не хэшировались.
Hash отчёта и двух небольших episode metadata вычислены при извлечении.

Оригинал:
`/home/kaifat/gothic-ai/body/data/stage8-final-gate-v6/validation/known-audit-v1/report.json`,
SHA-256 `7b76bbfe1ab517874db59696dcdd00e2619e03a00f3d82764b8ddb1617a742ed`.

[Отказ gate-v5](../gate-v5/README.md), включая memory-only spike 18,962995 мс,
сохранён. Новый результат CPU1 не устанавливает причину прежнего всплеска.
[Native14](../motor-v14/README.md) и [queue15](../motor-v15-queue/README.md)
остаются отдельными неизменными proof.

Извлечение — read-only; модели, тесты, runner и сервисные действия при
сохранении этого отчёта не выполнялись. Данные активного load не читались.
