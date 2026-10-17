# Gate11 load: причины и защищённые сервисы — FAIL

Закрыты8/12 cohorts: native4 и GRU4. Полное здоровое окно есть у всех
native и только GRU1. MLP0 остался partial и завершил suite infrastructure
`unexpected_control_loss:released`; MLP1–3 и fresh screening не запускались.
Known audit остаётся PASS; общего боевого gate credit нет.

[Compact JSON](load-failure.json) связывает46 закрытых source/raw/resource/
service файлов. Suite SHA:
`c03ac494184e7544747535faaa7816c673772b5437dd9c5fa6a700e211bde1ba`.
Исходные raw и выбранные веса остаются неизменными в `stage8-final-gate-v11`.

| GRU cohort | Конец | Game elapsed, мс | Accepted / refused | Control fraction | Model process p95, мс |
| --- | --- | ---: | ---: | ---: | ---: |
| 0 | control_lost | 2067 | 19 /5 | 0,791667 | 2,070822 |
| 1 | time_limit | 20000 | 153 /5 | 0,968354 | 1,251006 |
| 2 | control_lost | 3700 | 32 /3 | 0,914286 | 1,609428 |
| 3 | control_lost | 7400 | 75 /3 | 0,961538 | 1,406908 |

Runtime fallback0 у всех четырёх GRU не отменяет native handoff. В failed
cohorts0/2/3 нативный `target_visibility_lost` пришёл после completed
RightStrafe4, уже в Idle `S_1HRUN`, remaining_lock0. Restore actors —3/4/4;
completed→restore game clocks —18227→18361,19927→19994,23627→23694.
Эти состояния не являются protected active segment или runtime/OID/age
fallback. Ни p95≤5 мс, ни control0,961538 в cohort3 не дают credit после handoff.

У MLP actor2 command5 LeftStrafe3 завершился в19261 мс. Source observation274
имел can_decide=true и mask Approach1=true; следующий реальный ответ в19294 мс
отказал `target_invalid`. Lease/epoch/generation сохранены, owner scripted,
current control_allowed=false и все masks false. Команда не принята.
Collector receive→send —**4,848299 мс**; от native snapshot до send —
**6,709377 мс**, включая1,861078 мс wire transit. Body action RTT —19,703637 мс.
Затем явный release7907 привёл к native_restored и infrastructure terminal.
Это отказ классификации в collector после реальной потери допустимости;
исправление классификации не означает исправления физической видимости.

Native protected populations — cadence-valid20/20, бюджет p95 **10%**:

| Сервис | Baseline p95, мс | Native p95, мс | Изменение | Бюджет |
| --- | ---: | ---: | ---: | --- |
| ASR | 817,536568 | 807,324908 | −1,24908% | PASS |
| TTS | 437,162087 | 473,740987 | +8,36735% | PASS |
| Decider | 68,927712 | 76,231724 | +10,59663% | FAIL |
| Gemma | 109,876405 | 144,175764 | +31,21631% | FAIL |

Все GRU service comparisons не оцениваются: окна примерно2,0/20,0/3,6/7,3 с,
лишь одно полное healthy. Фактические populations ASR16/TTS15/Decider20/Gemma20
сохранены; count20 у последних двух не делает окно допустимым. Source helper
measurement issues, включая identity/cadence, оставлены. Четыре фактических
PID совпали между load before/after и сервисы active/running; реальный restart
не заявляется. MLP measured population0/20 также не оценивается.

Unit: wall3:52,841, CPU1:38,928, memory peak5,6G, exit2. Веса GRU
`e9a605…93b3eb`/MLP `d17752…073c9`, normalizer `c6ee3a…438fe` и пороги
p95/control/age/visibility100 мс прежние. Причины не сводятся к OID, age
или GC. Root оценивает guard классификации и TRAIN-only dual-peer SafeDemos/
нового кандидата; данные и обучение ещё не объявлены готовыми. Новое
ослабление native perception не допускается, private placement diagnostic
остаётся IN_PROGRESS и не становится обучающей выборкой автоматически.

Extractor только прочитал закрытые данные и вычислил исходные SHA;
source/runtime/config не менялись, модели/тесты/симуляции/сервисы не запускались.
