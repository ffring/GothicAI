# Gate v6: load FAIL

Known audit остаётся [PASS](README.md), но следующий load **не пройден**.
Завершены **10 из 12** cohorts: native4, GRU4, MLP2. Третий MLP cohort
прерван на инфраструктуре; четвёртый не начинался. Fresh screening
**не запускался**, общего боевого зачёта нет.

В `four-actor-mlp-2` stream4 первый актёр получил receive → process pause
**169,008339 мс**; остальные — 170,094827 / 170,909434 / 171,622080 мс.
Это шаги памяти. Следующий stream5 вернул `observation_too_old` всем
четырём актёрам без model forward; коллектор отправил четыре `body_release`.
Native остановил арену с `unexpected_control_loss:released` на elapsed
367 игровых мс. Это инфраструктурное прекращение, без зачёта native continuation.

Все четыре HP — **3000**, received damage0. В 70 настоящих visibility
diagnostics видимость сохранена, несовпадений query/diagnostic нет.
Прекращение не связано с зарегистрированным LOS loss или физическим поражением.
При отсутствии GC callbacks в старом запуске причина паузы 169 мс
**не доказана**.

Завершённые GRU cohorts сохранили neural control
0,995145631 / 1 / 1 / 0,987096774, runtime fallback0. Эти результаты
не закрывают незавершённый load и не являются новым боевым quality gate.

Защищённая Gemma отдельно превысила неизменный бюджет p95 деградации 10%:

| Нагрузка | Baseline p95, мс | During p95, мс | Деградация | Measured requests |
| --- | ---: | ---: | ---: | ---: |
| Native | 108,032685 | 147,864559 | **+36,87%** | 20 / 20 |
| GRU | 108,032685 | 149,453219 | **+38,34%** | 20 / 20 |

Обе полные серии имеют корректную одинаковую четырёхбурстовую cadence.
MLP service-серия содержит только 10 из 20 during requests и два burst;
её p95 comparison невалидна и не получает PASS. ASR/TTS/decider в полных
native/GRU сериях уложились в бюджет. Четыре сервиса оставались active/running
с одинаковыми PID в записанных load before/after; это утверждение касается
только данной фазы.

[Компактный JSON](load-failure.json) содержит 31 source SHA binding,
включая все raw-файлы частичного cohort, manifest, suite, resources и
service snapshots, четыре шага задержки и последующие fallback/release.
Оригинальный suite SHA:
`bfdd866e42c1a96b8e97197d2bbb61f7de1112503128b9dbc57c8d9ae9d44830`.
Все оригиналы сохранены на dev-ubuntu без изменений.

[Две отдельные GC диагностики](../collector-gc/README.md) подтверждают
возможные длительные обходы heap. Они не устанавливают причину старых
169 мс и не меняют load FAIL. Gate-v7 с профилем GC заморожен и выполняет
known-серию после preregistration PASS21,768 с и targeted runtime62/final67
PASS. Веса, freshness и quality thresholds сохраняются; fresh не запускался.
