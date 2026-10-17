# Gate v8: закрытый load — FAIL

Завершены **5/12 cohorts**: native0–3 и GRU0, каждый по 20 игровых секунд.
GRU0 имел control1, 167 принятых команд, 0 отказов и fallback0.
Следующий GRU1 остановлен с `unexpected realtime decision refusal: masked`;
GRU2–3 и все четыре MLP cohorts не запускались. Known audit остаётся PASS,
load — FAIL, fresh screening не запускался; этап8 — **IN_PROGRESS**.

[Компактный JSON](load-failure.json) сохраняет 32 привязки исходных файлов,
точные request/response и control event, маски, clocks, закрытые service
сравнения и resource/log fragments. Suite SHA:
`a606f09a2048ab1d62050962ec805dd6bb8f560dac8ba2c7679180564a3f0cce`.
Raw остаются неизменными в `stage8-final-gate-v8` на dev-ubuntu.

У actor4 команда7 выбрала action8 по periodic observation197 в 18461 игровых
мс. Source mask разрешала удар, `focus_5=true`, yaw error3,477065°.
Пока цель исполняла StrafeRight, текущее состояние изменилось: в 18494 мс
live mask запретила удары6/7/8 и разрешила TurnToTarget2. Native правильно
отказал action8 по **текущей физической маске**. TTL100 мс не истёк:
source→send10,049882 мс, receive→send5,233868 мс, RTT13,536693 мс;
model process0,816753 мс, backend0,379841 мс, CPU intra-op1.

Отказанная команда не получила accepted/started. В ответе owner остаётся
scripted, release_pending=false, can_decide=true, инфраструктурного отказа
арены нет. Collector после отказа завершился с exit2: partial cohort не имеет
terminal, episode.json или final-clear proof и не считается завершённым.
Последние два actor4 visibility diagnostics — visible. Все 414 диагностических
снимков, включая 21 `outside_pose_cone`, сохранены в raw; прежние временные
cone gaps не выданы за причину masked отказа или за успех эпизода.

Native protected workload имеет полный, cadence-valid набор20/20 для каждого
сервиса. Бюджет деградации p95 остаётся **10%**, warmup2 не входит в count20.

| Сервис | Baseline p95, мс | Native load p95, мс | Изменение | Бюджет |
| --- | ---: | ---: | ---: | --- |
| ASR completion | 821,685182 | 813,694022 | −0,97253% | PASS |
| TTS first PCM | 438,122535 | 482,050478 | +10,02641% | FAIL |
| Decider first decision | 56,864743 | 70,653368 | +24,24811% | FAIL |
| Gemma first token | 112,013644 | 169,235666 | +51,08487% | FAIL |

GRU workload записал лишь5/20 запросов; его service comparison не оценивается.
MLP workload отсутствует. Suite final service quality gate не вычислен из-за
неполного load, но три измеренных native budget failures остаются настоящими
отказами и не зависят от остановки collector.

Процесс load занял 2:59,67, RSS peak743612 KiB, exit2. Четыре сервиса остались
active/running с прежними PID именно между load before/after; это не утверждение
о PID за всю рабочую сессию. Веса GRU `e9a605…93b3eb`, MLP `d17752…073c9`
и normalizer `c6ee3a…438fe` неизменны. Этот load использовал native binary
`ac2dfe…5263` и прежние GC bootstrap/static opt-ins.

Motor16/Queue17/Gate9 заморожены и ожидают общий simulation lock; новые
quality cases не начались. Guarded masked refresh и opt-in actual
native-deadline polling требуют нового native/queue proof. Заявленный binary
`a1695dfb0edafdbc793075c84c3aae80b3ec3de61d9a7446dfe68ccff92bc0c2`
не является проверенным бинарником этого отчёта. Маски, TTL и quality thresholds
не ослаблены; все прежние отказы сохраняются. Extractor не запускал
модели, тесты, симуляции или сервисы и не скачивал веса/бинарники/большие IPC.
