# Queue17: настоящий post-tick ordering — FAIL

На том же runner `a1695d…bc0c2`, что в [Motor16](../motor-v16/README.md),
три `body_control_status` query вернули прежнее состояние **до уже due physical
tick**. Probe завершился exit2 с
`queued numeric/status query replied before its due physical tick`.
Исходный status=collected означает сохранённый сбор, а не audit PASS.
Все failed raw остаются неизменными; retry credit нет.

[Compact JSON](failed-summary.json) сохраняет точные три цепочки, 21 SHA
привязку source/raw/service/resource файлов и отдельно три closed Gate9
test XML bindings. Clock integers сохранены без округления. READ/reply/BEGIN/END
измерены в одном Linux CLOCK_MONOTONIC domain; inference из будущего EMIT
для этого вывода не используется.

| Request | Due ns | READ ns | Reply ns | Следующий BEGIN ns | END ns |
| --- | ---: | ---: | ---: | ---: | ---: |
| 8 | 1112734176246554 | 1112734196027605 | 1112734196764587 | 1112734197511767 | 1112734214846055 |
| 81 | 1112734410246554 | 1112734411508480 | 1112734412115468 | 1112734412197221 | 1112734430155248 |
| 153 | 1112734810246554 | 1112734820777391 | 1112734821922026 | 1112734822991171 | 1112734846548334 |

Все три query прочитаны в started/unpaused realtime. При request8 предыдущий
Tick17 END —1112734195979706 ns. Следующий deadline уже просрочен на19,781051 мс
в момент READ, но reply содержит step17/game16327. Только затем тот же deadline
исполняется Tick18: BEGIN/END дают step18/game16361. Для request81 и153 reply
также отстаёт ровно на один physical tick: step24→25 и36→37. Это реальный
порядок исполнения, а не недостаток покрытия тестового сценария.

Журнал содержит61 BEGIN,61 END и301 READ, всего423 diagnostic records с
непрерывными seq. Зафиксированы30 priority `body_heartbeat` admissions,
не более одного на deadline; это heartbeat, а не30 body_action.
Arena сама завершилась обычным time_limit1500 мс, оба HP300 и scripted owner
сохранены. Эти факты не отменяют query ordering FAIL. Шесть pure reader tests
прошли, но также не заменяют физическую пробу.

Gate9 targeted tests: runtime101, final78, combat-reader82 — PASS.
Quality cases и real model forwards — **0**, quality phase directories отсутствуют;
load/fresh screening не запускались. Общего боевого gate PASS нет.
Нативное исправление и последующие Motor18/Queue19/Gate10 ожидаются;
веса, normalizer, маски и quality thresholds прежние.

Resources/log и четыре service before/after привязки сохранены в compact.
Сервисы active/running с теми же PID внутри probe/test интервалов; новое
protected latency измерение не заявляется. Extractor не запускал модели,
тесты, симуляции или сервисы. Полные журналы остаются в
`/home/kaifat/gothic-ai/body/data/stage8-motor-v17/pending-input-probe/`.
