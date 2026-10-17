# Gate10: known collection — FAIL

Закрыты shadow3 и ordinary realtime22. Следующий
`realtime-native_duel-223-1-gru` завершился инфраструктурным отказом
`unexpected_control_loss:released`. Reaction, load и fresh screening
не запускались. Known matrix и независимый quality audit не завершены;
этап8 остаётся **IN_PROGRESS**, общего боевого PASS нет.

[Compact JSON](known-failure.json) содержит31 SHA-привязку закрытых
source/raw/manifest/resource/service файлов, точные model/control/native RPC
clocks и исходные байты незавершённой строки. Realtime suite SHA:
`547f55fec7572f2ea077b8bb144bae59c817c4e4395f352a24e02853b059fc9b`.
Все failed raw остаются неизменными в `stage8-final-gate-v10` на dev-ubuntu.

Partial GRU имеет146 model inputs: **145 actual forwards и1 age fallback**.
Process wall p95 —2,591128 мс, максимум9,772362 мс, включая locked memory
updates. Все145 forward использовали CPU intra-op1, interop12. Порог control
не пройден: **12 accepted /13 opportunities =0,923077** при требовании0,95;
один strict stale refusal сохранён. Masked budget exhaustion нет.

Последняя команда13/action1 использовала observation335/game23527.
Receive→process —10,674372 мс; receive→send —12,885638 мс.
Backend inference —0,876353 мс, полное process wall —1,809592 мс.
Предыдущий locked forward на observation331 занял9,772362 мс wall /
5,921622 мс backend; этот шаг не исключён из статистики.

| Native RPC | Команда | RTT, мс |
| --- | --- | ---: |
| 6041 | body_observations | 187,746680 |
| 6042 | body_action, stale_observation | 78,658525 |
| 6044 | body_events | 81,912808 |
| 6045 | body_observations | 19,113965 |
| 6046 | body_release | 68,906715 |

Пакет6045 содержит primary sequences337/339/341/343, до game23727.
Первый обработанный337 имеет game23561: возраст166 игровых мс, ответ
`observation_too_old`, model_processed=false, hidden_reset=true. Затем
collector послал release6046. Реальное `native_restored` произошло в23761 мс;
арена остановилась с infrastructure=true на elapsed7467 мс. Этот возврат
к native не получает neural success credit.

Нативные timestamp brackets внутри steps231/232/233/234/236 выросли до
70,005363 /103,476594 /81,268128 /89,943992 /71,351879 мс; соседние recorded
brackets — около9–11 мс. Это первые→последние visibility diagnostics в step,
а не полные BEGIN/END timers. Step232 начинается
`owned_consumed_wait_move_before`; отсутствующий step235 не реконструирован.
Причина длинных интервалов из этих clocks не следует.

Валидный packet timing prefix213 строк заканчивается на step230:
p95 0,420 мс по floor((N−1)×0,95), max1,960 мс; nearest-rank p95 отдельно
сохранён как0,423 мс. Весь timing-файл имеет427 valid records и **114 последних
байт незавершённого JSON**: строка428 начинается с step231/game23461 и
обрывается на `"actors":`. Raw bytes/base64, их SHA и SHA всего файла
сохранены. Достраивать строку или приписывать более поздние timing rows нельзя.

Ближайший collector GC generation0 span —0,349285 мс; он не объясняет
предшествующие долгие native RPC. GC enabled, dropped0. Root готовит отдельную
пробу native CPU/cgroup без моделей для оценки16 workers в бюджете200%.
Ни CPU quota, ни OS scheduling, ни конкретный worker пока не установлены
как причина этого отказа. Веса `e9a605…93b3eb`/`d17752…073c9`, normalizer
`c6ee3a…438fe`, masks, freshness и quality thresholds сохраняются.

[Motor18](../motor-v18/README.md) и [Queue19](../motor-v19-queue/README.md)
остаются native/transport PASS; прежние Queue17 и gate load failures сохранены.
Четыре сервиса остались active/running с одинаковыми PID между before/after
закрытых shadow/realtime фаз; protected latency здесь не измеряется.
Extractor не запускал модели, тесты, симуляции или сервисы и не скачивал
веса, бинарники или большие журналы. Нового игрового зачёта нет.
