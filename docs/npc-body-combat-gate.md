# Этап 8: боевой gate GRU

Статус: **исторический журнал этапа 8 до 14 октября 2026 года.** Остановка на preflight снята: 15 октября боевой gate пройден (gate v16, [отчёт](../reports/npc-body/2026-10-15-gate-v16/README.md)), итог — в [плане, этап 8](npc-body-control-plan.md#этап-8-управление-в-headless-арене-и-разбор-ошибок). Рабочая модель боя теперь P30 (этап 9), а не веса отсюда.

Статус на 14 октября: **STOPPED_AT_PREFLIGHT**. Единственная дополнительная SafePeer
калибровка не прошла критерии физически подтверждённого покрытия; работа
остановлена по ограничению пользователя. [Итог остановки](npc-body-stage8-stop-report.md).
В child `stage8-defence-bc-v2` собраны
72/72 настоящих завершённых блока, завершены все шесть BC кандидатов,
зафиксированы веса GRU/MLP и пройден независимый числовой аудит.
Gate-v3 known audit R2 — PASS: GRU выполнил два настоящих блока с HP300,
control 1 и fallback 0. Последующий [gate-v4](../reports/npc-body/2026-10-14-combat/gate-v4/README.md)
завершил known-серии, но не прошёл порог управления в одном сценарии
дистанции: 2/3 принятых решений, один strict stale rejection, без fallback.
Native14 и отдельная queue15 проба завершены. [Gate-v5](../reports/npc-body/2026-10-14-combat/gate-v5/README.md)
собрал known 3/24/6, но не прошёл latency gate: второй, memory-only шаг GRU
занял 18,962995 мс при пороге 5 мс. Оба настоящих блока сохранили HP300,
control 1 и fallback 0. [Gate-v6 known audit](../reports/npc-body/2026-10-14-combat/gate-v6/README.md)
прошёл с private CPU intra-op1: known 3/24/6 закрыта, issues пусты.
Оба настоящих GRU блока сохранили HP300, control1 и fallback0.
[Load gate-v6](../reports/npc-body/2026-10-14-combat/gate-v6/load-failure.md)
завершился FAIL: 10/12 cohorts, остановка MLP после задержки обработки и
отдельное превышение защищённого Gemma p95. Fresh screening не запускался;
[Gate-v7](../reports/npc-body/2026-10-14-combat/gate-v7/README.md) остановился
до quality cases: shadow0, preexisting GC ownership. Новый Gate-v8 заморожен,
preregistration PASS за 50,177 с; targeted runtime76/final70 PASS.
После освобождения simulation lock [known audit gate-v8](../reports/npc-body/2026-10-14-combat/gate-v8/README.md)
прошёл: 3/24/6 закрыты, issues пусты, оба GRU блока task_success/HP300.
[Load gate-v8](../reports/npc-body/2026-10-14-combat/gate-v8/load-failure.md)
закрыл 5/12 cohorts и завершился FAIL: следующий GRU1 получил strict masked
отказ после изменения текущего focus во время strafe цели. Native20/20
protected p95: TTS +10,02641%, decider +24,24811%, Gemma +51,08487% при
бюджете10%; ASR −0,97253% прошёл. GRU5/20 не оценивается.
[Motor16](../reports/npc-body/2026-10-14-combat/motor-v16/README.md) прошёл full
native parity 17/29/43 без различий, realtime4, visibility8, compact10 и все
37 Body API сценариев с пустыми issues. Отдельная
[Queue17](../reports/npc-body/2026-10-14-combat/motor-v17-queue/README.md)
завершилась FAIL: три ответа `body_control_status` отправлены до уже
просроченного следующего тика. Gate9 прошёл targeted runtime101/final78/
combat-reader82 tests, но не запускал quality cases или model forwards.
[Motor18](../reports/npc-body/2026-10-14-combat/motor-v18/README.md) и
[Queue19](../reports/npc-body/2026-10-14-combat/motor-v19-queue/README.md)
прошли: full native parity 17/29/43 без различий, API37/realtime4/visibility8/
compact10 и четыре неизменённые строгие проверки очереди.
[Gate10 known collection](../reports/npc-body/2026-10-14-combat/gate-v10/README.md)
завершилась FAIL до load/reaction/fresh: shadow3 и ordinary22 завершены,
следующий GRU native_duel получил control 12/13 и age fallback с release.
Причина длинных native-операций ещё не доказана.
[Motor20](../reports/npc-body/2026-10-14-combat/motor-v20/README.md) и
[Queue21](../reports/npc-body/2026-10-14-combat/motor-v21-queue/README.md)
на serial-native бинарнике a83efa… прошли native/queue proof. Gate11 заморожен:
preregistration PASS за 26,768 с, targeted runtime101/final94 PASS. Независимый
known audit закрыл 3/24/6: PASS за 39,393 с, issues пусты, фактический serial
worker profile проверен. Последующий
[load Gate11](../reports/npc-body/2026-10-14-combat/gate-v11/README.md)
закрыт с FAIL: после native4/GRU4 partial MLP0 завершился с
`unexpected_control_loss:released`. [Causal report](../reports/npc-body/2026-10-14-combat/gate-v11/load-failure.md)
подтвердил GRU handoff после completed RightStrafe/Idle без runtime fallback
и native protected Decider/Gemma +10,59663%/+31,21631% при бюджете10%.
Последняя SafePeer калибровка закрыла шесть эпизодов без infrastructure failure
и с сохранением HP, но дала approved action2/5/11 = 0/2/0 при требовании
2/2/2. Child279 не создан, новые веса не обучены; дополнительный боевой gate
этой попытки не запускался. Fresh не запускался. Выбранные веса207,
normalizer и боевые thresholds прежние. Защищённые сервисы рассматриваются
отдельным отчётом с прежним бюджетом10%; их FAIL сохранён.
Общего боевого gate PASS нет, fresh screening не запускался.

Проверенный backend `stage8-motor-v7`:
`44eff82c4980fe1e54e1b69f5ed2ecc4d6d94c726bc94f23d45fde868620f84c`.
Native parity на 17/29/43 совпала; настоящий realtime probe прошёл четыре
раздельных NPC/RNG stream, удержание мира до старта, свободные тики и immutable
terminal. Native resolver: 11 наборов / 2498 утверждений. Профильный Python-набор:
215 проверок прошли в `stage8-combat-v6`.

Backend `stage8-motor-v10`,
`04d45057b9e3393bb3e641c580008aec19283bff7c377eb8d6a0363f23d5c5d4`,
прошёл все 37 [проверок Body API](../reports/npc-body/2026-10-14-combat/current-api-v1/body-api.md)
и сохранённую native parity; focused resolver — 2534 утверждения.
Это подтверждает механику и контракт, а не качество GRU.

Motor-v12 (`d44ef8a769c49226ea4ee08a024b43dd64bcddaa942183edfb9343eb03b3caaa`)
прошёл 129 Python checks, полную native parity на 17/29/43, realtime с
четырьмя NPC и все 37 Body API сценариев. Физический replay (4,2 МБ, в репозитории не хранится: dev-ubuntu `~/gothic-ai/body/data/stage8-motor-v12/visibility-boundary-probe/probe.json`)
сохранил точный prefix из 64 scripted команд до 7567 мс, настоящий урон 70,
`T_STUMBLEB` 360 мс и четырёх владельцев без преждевременного release.
Последующий Motor-v13 завершён на бинарнике
`cd8abf1364b736a2d961f3969316e73b99a04b39881f644bbc711550506a2a8c`:
full native parity 17/29/43, API37, realtime4, visibility boundary8 и compact10
прошли; focused tests — 10+49. [Motor-v13 proof](../reports/npc-body/2026-10-14-combat/motor-v13/README.md)
связывает результаты с исходными отчётами. Модельное качество эти пробы
не оценивают.

Предыдущий [Motor-v14 proof](../reports/npc-body/2026-10-14-combat/motor-v14/README.md),
runner `ac2dfe85bc7dd255f08e3eff5502594f854241ee04985f698569f527d2652263`,
прошёл full native parity 17/29/43, API37, realtime4, visibility8 и compact10.
Его первая проба очереди сохранила FAIL: реальных priority admissions — 0.
[Queue15 на том же бинарнике](../reports/npc-body/2026-10-14-combat/motor-v15-queue/README.md)
подтвердила 13 приоритетных heartbeat, один post-tick запрос чтения и две
команды wait11 с accepted/started без отказов; шесть reader tests прошли.
Heartbeat admissions не являются 13 приоритетными боевыми действиями.

Рабочий контур — `codex/npc-body-api`, симуляции и модели — только `dev-ubuntu`.
Исходные данные, веса и результаты этапа 7 остаются неизменными в
`~/gothic-ai/body/data/stage7-bc-v9/`. Новые эксперименты получают отдельные
каталоги, снимки кода, binary/source hashes и журнал исходных событий.

## Причина потери видимости

Диагностические прогоны `stage8-visibility-v1/v2` сравнивают обе стороны дуэли
на seed 29 с прежними весами GRU/MLP. Дополнительные диагностические события
показывают действительный `canSeeNpc`, направление BIP01, yaw тела и оба луча
видимости. Эти поля служат аудиту и не входят в 184 числовых признака модели.

При отказе GRU геометрические лучи свободны, расстояние допустимо, но BIP01
выходит из конуса зрения. Native AI во время `faiWaitTime` продолжает вызывать
поворот к цели и `FaiMove`. Исполнитель принятого neural-примитива после
`consumed` вызывал только `WaitMove`, поэтому yaw тела оставался прежним во
время бокового движения. Исправление использует существующий wait-участок
`Npc::implAttack(..., MV_NULL)` при том же условии native FAI/script wait либо
HIT. В этом участке нет выбора следующего примитива или расходования RNG.

Проверка исправления включает повтор того же probe с прежними весами,
сопоставление yaw/видимости до возврата native и полную сохранённую native
parity. Настоящий retarget к герою после `native_restored` сохраняется в raw
журнале и учитывается отдельно; он не становится успехом neural policy.

## Данные и выбор модели

Конфигурации: [gate](../evals/npc-body/body-stage8-v1.json) и
[демонстрации](../evals/npc-body/body-combat-demonstrations-v1.json).
Новые train seed — 181, 191, 193, 197, 199, 211. Для выбора весов используются
только полные последовательности новых eval seed 223/227; старые 29/113
остаются диагностической регрессией. Test 229/233 и OOD 239/241 не выбирают
веса. Normalizer вычисляется только по train.

Наблюдающий scripted teacher не вызывает второй раз stateful `FightAlgo`.
Полные боевые демонстрации выполняются против native противника. Отдельные
train микроэпизоды у стены имеют явно удерживаемого scripted противника:
направление манёвра выбирается по наблюдаемой геометрии, а класс ожидания —
по наблюдаемому расстоянию. Такие эпизоды не доказывают качество дуэлей.
В датасет попадают только команды с причинной связью снимок → решение →
приём → фактическое исполнение и пройденным физическим proof.

Калибровка 16 состояний дала 15 безопасных arc и 16 корректных ожиданий.
В первом production использованы восемь комбинаций yaw/дистанции, по четыре
для каждого направления. После исправления normal placement старые Right
варианты не проходят наблюдаемый безопасный путь: teacher не выдаёт команды.
Все 84 эпизода `stage8-combat-v5` сохранены, корпус не допущен: новые метки
3/4/10/11 = 24/0/15/13 при минимуме 20. Отдельная калибровка
правых манёвров выполнена на исправленной геометрии. Калибровка v2 (28 эпизодов)
подтвердила: обмен physical side не зеркалит стену, поскольку поиск выбирает
знак tangent. Новый reset-only `wall_tangent_sign: +1 | -1` разрешён только
для `near_wall` вместе с `wall_clearance_cm`; он фильтрует кандидатов до
начала эпизода без fallback на другой знак. Координаты пары независимо
подтверждают requested/actual sign. Поведение без параметра сохраняется.

Совместная калибровка v5 подтвердила все восемь манёвров и восемь ожиданий.
Левые варианты: side0, sign+1, yaw20/35 × distance350/450; правые: side0,
sign−1, yaw−35/−38 × distance350/450. Preflight v6 на 48 микроэпизодах
дал после periodic alignment по 24 пригодные новые метки для каждой из
команд 3/4/10/11. Максимальный подтверждённый pose-cone gap — 66 игровых мс;
потерь при approval и periodic projection нет. Привязки исходных данных и
физическое разнообразие сохранены в [отчёте preflight](../reports/npc-body/2026-10-14-combat/preflight-v6/README.md).

Микроэпизоды дополнительно используют реальные
расстояния до стены 120/130/140/150/160/170 см. Полные 36 боевых демонстраций
сохраняют стандартную геометрию. Все 84 эпизода corpus v6 записаны и проверены:
36 полных боёв и 48 микроэпизодов. Calibration
и geometry preflight сохраняются отдельно и автоматически в обучение не
попадают. Порог новых подтверждённых train labels — минимум 20 для каждой
из команд 3/4/10/11 после periodic alignment.

Проверка точного clearance обнаружила ошибку размещения: tangent вычислялся
от поискового луча, а не от нормали стены. В успешном legacy-сценарии
Right(−35°, 450 см), side1 первичный NPC имел wall ray 150.831 см при
default offset 120 см. Поэтому отказ explicit 120±20 см сам по себе не
доказывает небезопасность манёвра. Новый optional параметр использует
геометрию по настоящей нормали; стандартная ветка сохраняется. Независимо
проверяются normal projection и настоящий ray distance (оба 60–180 см,
ошибка запроса ≤20 см), нормали, tangent и исходные collision/LOS guards. Неуспешные
прогоны и raw журналы сохранены. Границы физических проверок и допустимый
transient pose-cone gap ≤100 игровых мс не ослаблены.

В предыдущем v6 GRU-256 и MLP обучены BC с тремя training seed, chunk 64 и burn-in 32.
Оценка для выбора checkpoint проходит непрерывно по целому эпизоду, включая
locked наблюдения; искусственный сброс памяти на границе chunk запрещён.

Selection dataset v6 содержит 112 train, 8 новых eval, 7 сохранённых test и
8 OOD эпизодов; нормализация использует только train. После причинного
выравнивания train содержит 1068 меток, fresh eval — 157. Выбран GRU seed 29,
epoch 26; checkpoint `44727b7c99211c6a5e79b4f9aead852c33e277f6cee5dc4bbed95bd86af15c7c`.
Fresh eval поддерживает четыре из 11 классов и не проверяет защиту.
Состав данных и выбор всех шести кандидатов сверены в
[аудите](../reports/npc-body/2026-10-14-combat/dataset-v6/audit.md) и
[отчёте кандидатов](../reports/npc-body/2026-10-14-combat/dataset-v6/candidates.md).

## Текущий результат и защитная реакция

Выбранный GRU v6 выиграл обе realtime дуэли seed 223: HP primary 230,
HP opponent 0. В исходных нейтральных block-сценариях результат GRU — 0/2:
он убил атакующего, но `blocked_hits=0`. Native прошёл обе проверки.
Победа в дуэли не засчитывается как блок; исходные отказы v6 сохранены.

Первое наблюдение attack и block побайтно совпадает по всем 184 исходным и
нормализованным признакам для обеих сторон и обеих моделей. Противник ещё
не показывает замах, goal — `native_combat`. Успешные защитные примеры
старого curriculum, напротив, начинаются при настоящем видимом замахе.
Боевой corpus v6 не добавил подтверждённых train block/jump labels: завершённая
анимация без предотвращённого удара экспертной меткой не становится.
[Диагностика входов и тайминга](../reports/npc-body/2026-10-14-combat/defense-diagnosis-v6/README.md)
связывает эти выводы с исходными журналами.

Отдельный диагностический `block_reaction` объявлен для шести известных
случаев seed 223: Native/GRU/MLP × две стороны. Все получают одинаковую
физическую подготовку атакующего; primary удерживается без команд и native
chooser до свежего видимого замаха в range. На явной границе активации
создаётся начальное состояние модели: hidden сброшен, первый фактический
periodic снимок имеет sequence dt 0; подготовка не обновляет hidden.
Критерий сохраняется: настоящий `blocked_hit`, HP300 и отсутствие damage.

Reaction-v3 завершён и независимо проверен. GRU первым выбрал отскок (5)
на обеих сторонах: физическая реакция 33 игровых мс при настоящем входящем
ударе через 166 мс после наблюдения. Native `jumped_back=true`, HP300,
урон и runtime fallback — ноль, healthy fraction — 1,0. Строгий блок GRU —
0/2; Native и MLP также не блокировали, получили по 70 урона и закончили
с HP230. Все шесть случаев завершились `time_limit`. Это lockstep-диагностика
известных случаев, без зачёта realtime/fresh-screening gate.
[Результат reaction-v3](../reports/npc-body/2026-10-14-combat/reaction-v3/README.md)
содержит ссылку на независимый JSON-аудит. Веса в этом прогоне не менялись.

Новый [reactive-block curriculum](../evals/npc-body/body-defence-demonstrations-v1.json)
проверен: все 72/72 эпизода дали настоящий завершённый блок (9),
36 train и по 12 eval/test/OOD. Primary выполняет одну команду блока по настоящему
видимому замаху; метка требует принятого, начатого и завершённого блока с
физически предотвращённым ударом, HP300 и отсутствием урона. Opt-in
`reaction_hold` для `reset_stress` даёт блоку завершиться после удара и
сохраняет исходный block benchmark. Подготовка остаётся в raw; обучающая
последовательность начинается с подтверждённого prepared anchor, dt0.

Child `stage8-defence-bc-v2` содержит 207 эпизодов: 148 train, 20 eval,
19 test и 20 OOD. Распределение известных групп унаследовано; нормализация
пересчитана только по объединённому train. Train содержит 1104 метки,
включая 36 новых блоков; eval — 169 меток и пять поддержанных классов из
11. Эти данные не заменяют независимую проверку поведения.

[Профиль child BC](../evals/npc-body/body-defence-bc-v1.json) фиксирует
шесть CUDA jobs: GRU/MLP × seed 11/29/43, до 120 эпох, patience25,
chunk64 и burn-in32; выбор использует только непрерывный eval. CPU
verification и tiny overfit обеих моделей прошли; overfit accuracy — 1,0.
Все шесть jobs завершены, GPU unit — 23 мин 7,662 с; CPU evaluate — 1 мин 15 с.
PyTorch allocator ограничен 1024 MiB. GPU monitor сохранил 681 sample,
измерил peak наших процессов 472 MiB и минимум свободной VRAM 4646 MiB.
Все четыре защищённых сервиса остались active/running с прежними PID;
модели на Mac не запускались.

Выбран GRU seed 11 / epoch 25,
`e9a605b4ae15e30ad7e2dd632c10c882c56f98b19f40cace3d8b2343a693b3eb`;
MLP seed 11 / epoch 14,
`d17752fd0fc765f2ab841eec107f0a8ccbf77fd5db83ceabbee866e33a1073c9`.
Веса зафиксированы 14 октября 2026, 11:15:23.540769 UTC. GRU предсказал
164/169 eval labels, macro F1/11 — 0,445295, NLL — 0,141480;
test — 74/92 (0,804348), OOD — 117/156 (0,75). Block recall:
eval 12/12, test 17/18, OOD 15/18; false positives — ноль.
Warm CPU model step p95: GRU 0,132028 мс, MLP 0,117020 мс.

[Независимый аудит child v2](../reports/npc-body/2026-10-14-combat/defence-v2/README.md)
прошёл 3867 файловых привязок. Эти метрики описывают сохранённые labels и
model step; собственное боевое исполнение проверяется отдельно. Gate использует
[заранее объявленный protocol v2](../evals/npc-body/body-stage8-evaluation-v2.json):
20 fresh seeds, четыре задачи, три провайдера — 240 эпизодов.
Веса GRU `e9a605…b3eb`, MLP `d17752…73c9`, normalizer `c6ee3a…38fe`
не менялись. Результаты поведения не возвращаются в выбор checkpoint.

## Отказы gate и текущая проверка транспорта

В gate-v1 reaction на side1 команда блока (9) была отклонена как stale;
последовал fallback, HP230 и neural control 0. Проверка не пройдена.
[Известные reaction случаи](../reports/npc-body/2026-10-14-combat/gate-v1/known-reaction-audit/report.json)
и исходные отказы сохраняются.

В нагрузке GRU 0/1 терял видимость на страфе; настоящий удар на 70 HP
прерывал движение и вызывал `T_STUMBLEB` 360 мс. Другой прогон, GRU 2,
терял управление после stale команды. Gate сервисов также не пройден:
старая методика cadence дала деградацию выше 10%. Между baseline и нагрузкой
были разные интервалы запросов. [Диагностика load-v1](../reports/npc-body/2026-10-14-combat/load-v1-diagnosis/README.md)
фиксирует причинные времена и ограничения этих измерений.

Gate-v2 собрал shadow и 24 обычных эпизода с прежними весами, затем
подготовка reaction завершилась инфраструктурной ошибкой `rng_state`:
компактный transport применялся к held-сцене и не передавал нужное поле
purity proof. Reaction cases — 0; нагрузка и screening этого запуска не
начинались. Новая проверка сохраняет полный статус при `reactionHeld`,
включая armed фазу; Motor-v13 подтвердил это настоящим compact probe.
[Gate-v2 preparation failure](../reports/npc-body/2026-10-14-combat/gate-v2/README.md)
сохраняет фазовые статусы и SHA исходных журналов.

Gate-v3 зарегистрирован до измерения и собрал 3 shadow, 24 ordinary и
6 reaction случаев с прежними весами. Первый known-audit-v1 остановился на
ложном предположении reader: он требовал `shadow=true` и для native comparator.
У этого comparator raw control journal пуст и ownership не приобреталось.
Отказ reader сохраняется; отдельно замороженный external reader исправляет
только этот predicate для native и связывает изменение с исходным SHA и
первым failed report. [Known audit R2](../reports/npc-body/2026-10-14-combat/gate-v3/README.md)
прошёл: GRU выполнил настоящий блок (9) на обеих сторонах, HP300, урон 0,
neural control 1 и fallback 0. Физический старт через 66 игровых мс после
наблюдения при настоящем окне 166 мс. Collector→send — 5,691093/6,285137 мс,
model p95 — 1,255344/1,459747 мс, observation→arbitration — 6,895532/7,511438 мс.
Это подтверждённые known случаи, без общего stage8 quality credit.

Доставка теперь использует paced heartbeat и ограниченный повтор stale
решения по свежему снимку. Старые отказы сохраняются в журнале без зачёта
как принятых действий. Compact transport включается явно; CPU backend 2
поддерживает `discard_hidden`. Маски, физические окна и обученные веса
сохраняются.

Service baseline и нагрузка используют одинаковые четыре серии по пять
измеряемых запросов, два warmup перед первой серией и nominal cadence 20 с
с записью actual 20–25 с. Число measured samples остаётся 20, бюджет p95
деградации — 10%. Это изменение методики предстоит проверить в новом gate.
Выявленные до load compact/startup/dispatch hooks подключены в новом
frozen драйвере, включая warmup восьми CPU runtimes до reset. Load ещё не
проверен. Gate-v4 сохранил настоящий known failure управления 2/3 при
физическом успехе дистанции. В старом журнале нет tick BEGIN/native READ:
отправка до следующего observation EMIT не доказывает прибытие до начала
тика. Строгая проверка queued command freshness и отказ сохранены.

Gate-v5 declaration — PASS, 17,487 с; привязаны Motor14, queue15, failed
gate-v1/v2/v4 и closed known-v3. Исходные focused tests: 58 PASS и два
отказа тестовых fixtures; отдельное исправление только fixtures прошло 2/2,
producer не менялся. Known 3/24/6 завершена; аудит не прошёл только
`block-reaction-223-0-gru` model-step budget. Первый управляющий шаг —
1,243341 мс, backend 0,781786 мс, collector-send 6,157565 мс; второй
locked memory-only шаг — 18,962995 мс, backend 18,459441 мс. Все четыре шага
и warmup 0,949641/0,206949 мс сохранены в [отчёте gate-v5](../reports/npc-body/2026-10-14-combat/gate-v5/README.md).
Пошаговых CPU/thread samples нет, OS stall не установлен. Всплеск не исключён
из gate. Frozen gate-v6 использует opt-in CPU intra-op1 при default2;
interop12 сохранён. Focused CPU profile tests — 48 PASS, final evaluation
tests — 63 PASS. Known audit — PASS за 42,099 с, issues пусты. Профиль1/1
подтверждён у всех 2205 real forward calls: 802 shadow, 1387 ordinary,
16 reaction. Максимумы отдельных GRU case p95: model1,440461 мс,
observation→arbitration9,690933 мс, collector→send5,542793 мс; минимальный
healthy neural control1. Оба reaction блока начались через 66 игровых мс
при окне166 и сохранили HP300 без damage/fallback. Веса, normalizer и пороги
прежние; новый результат не доказывает причину старого spike.

Load gate-v6 — FAIL: завершены native4/GRU4/MLP2 из 12 cohorts. В третьем
MLP cohort первый receive→process gap — 169,008339 мс, максимум четырёх
актёров — 171,622080 мс. Следующий stream вернул observation_too_old всем
четырём, затем Python отправил body_release. Native остановился на elapsed
367 мс с unexpected_control_loss:released, при HP3000/damage0 у каждого;
70 visibility diagnostics оставались visible. GRU control в закрытых cohorts:
0,995145631 / 1 / 1 / 0,987096774, fallback0.

Отдельно Gemma p95 превысила бюджет 10%: baseline108,032685 мс,
native147,864559 (+36,87%), GRU149,453219 (+38,34%) при полных 20/20 запросах
и допустимой cadence. MLP service-серия неполная, 10/20, сравнение невалидно.
[GC диагностики](../reports/npc-body/2026-10-14-combat/collector-gc/README.md)
показывают отдельные длительные heap обходы, но не устанавливают причину
старой паузы169 мс. Profile1: 29,95–42,73 мс до static freeze против
0,000241–0,003376 мс после, GC включён. Profile2: natural generation2
73,338749/57,597238/49,018579 мс; последний входит в pull54,128573 мс.
Эти запуски не получают quality credit и не открывают fresh seeds.

Gate-v7 завершился startup infrastructure failure за 5,089 с: shadow0,
`static GC scope requires enabled dynamic GC and no preexisting freeze ownership`.
[Ownership proof](../reports/npc-body/2026-10-14-combat/collector-gc/ownership-bootstrap.json)
подтвердил private UV Python3.12.13 frozen375 до imports и после всех8 warm2,
без GC mutator calls; isolated -I -S также375, system3.14.4 —0. Opt-in
bootstrap ограничен точным private version/path/executable SHA021044…acec7;
unknown nonzero ownership не снимается. Actual helper:375→0, freeze8891,
close0, GC enabled/thresholds700/10/10 сохранены; pure tests28 PASS.
Это operational proof, без модельного quality credit и без объяснения старых169 мс.

На момент gate-v8 этап 8 был IN_PROGRESS. Gate-v8 bootstrap profile заморожен,
preregistration PASS50,177 с, targeted runtime76/final70 PASS. Known3/24/6
прошёл audit за 62,755 с, issues пусты. GRU max case model p95 — 2,209114 мс,
observation→arbitration18,053185 мс, collector→send8,422820 мс; repaired
population control минимум1. Старый neutral block diagnostic8/9 сохранён
отдельно. Оба reaction блока HP300/damage0/block1/fallback0. CPU1 подтверждён
у 2518 реальных forward; GC bootstrap375→0, static freeze183578/183641,
close0 с GC enabled и thresholds700/10/10. Последующий load — FAIL: завершены
native0–3 и GRU0; в partial GRU1 action8 был разрешён source mask, но правильно
отклонён current mask после движения цели. Принятого/начавшегося удара нет,
owner сохранился, collector остановился. Native защищённые TTS/decider/Gemma
превысили10% p95, неполный GRU5/20 не оценён. Этот load использовал native ac2.
[Motor16](../reports/npc-body/2026-10-14-combat/motor-v16/README.md) на следующем
бинарнике a1695d…bc0c2 прошёл full native parity 17/29/43 с нулём различий,
realtime4, visibility8, compact10 и API37; full-stage4 issues пусты.
[Queue17](../reports/npc-body/2026-10-14-combat/motor-v17-queue/README.md)
сохранила FAIL порядка native post-tick: запросы `body_control_status`
8/81/153 получили ответы до следующего уже просроченного тика в запущенном,
не приостановленном realtime. Для запроса8 Tick17 END был
1112734195979706 нс; deadline следующего тика — 1112734176246554 нс,
READ — 1112734196027605 нс, reply — 1112734196764587 нс со step17/game16327.
Только затем Tick18 с тем же deadline начался в 1112734197511767 нс и
завершился в 1112734214846055 нс со step18/game16361. Все значения — фактический
Linux CLOCK_MONOTONIC; модель этот probe не запускала.

Gate9 targeted runtime101/final78/combat-reader82 tests прошли; quality
cases и model forwards — 0. Узкая native правка проверена в
[Motor18](../reports/npc-body/2026-10-14-combat/motor-v18/README.md) и отдельной
[Queue19](../reports/npc-body/2026-10-14-combat/motor-v19-queue/README.md), runner
`681a1704364e07836f839c3e4382b16ae43b28ae3bf75fb46f7884e2b10a1359`.
Motor18 прошёл full native parity 17/29/43 с нулём различий, API37, realtime4,
visibility8 и compact10. Queue19 прошла все четыре неизменённые строгие
проверки: 18 реальных WRITE admissions были heartbeat, 10 цепочек fresh query,
три wait11 приняты и фактически начаты, отказов 0; шесть reader tests прошли.
Эти admissions не являются 18 боевыми командами. Failed Queue17 сохранена.
[Gate10 known collection](../reports/npc-body/2026-10-14-combat/gate-v10/README.md)
завершилась FAIL до load, reaction и fresh. Завершены shadow3 и ordinary22;
partial `realtime-native_duel-223-1-gru` сохранил 146 model inputs:
145 реальных forwards и один age fallback. Process wall p95 — 2,591128 мс,
максимум — 9,772362 мс. Из 13 eligible opportunities приняты 12; один stale
отказ дал control 0,9230769 ниже порога. Source observation335/game23527 имело
receive→process 10,674 мс, receive→send 12,8856 мс и model step 0,87635 мс.
Позднее получены observations337/339/341/343 до game23727; oldest337/game23561
с age166 вызвал `observation_too_old`, hidden reset и release6046. Native
восстановлен на game23761, затем arena завершена с
`unexpected_control_loss:released`. Masked retry budget не исчерпан.

Native RPC6041 observations занял 187,74668 мс, RPC6042 action — 78,658525 мс,
RPC6044 events — 81,912808 мс, RPC6045 observations — 19,113965 мс. Native
steps231/232/233/234/236 длились 70,005/103,477/81,268/89,944/71,352 мс при
обычных 9–10 мс. Snapshot packet p95 — 0,420 мс, максимум — 1,96 мс;
соседний GC 0,349 мс не объясняет эти задержки. Причина не доказана.
Отдельная native CPUv2 диагностика сохранила 606 response attestations и CPU −23,31%, но
тики стали медленнее; результат относится только к этому diagnostic run и
не доказывает причину Gate10 FAIL.

[Motor20](../reports/npc-body/2026-10-14-combat/motor-v20/README.md) и
[Queue21](../reports/npc-body/2026-10-14-combat/motor-v21-queue/README.md)
завершены на serial-native runner `a83efa28236ff9917b7e7d60a4a508de6feedd31d8d9ff4bc69adeb20ab777b7`. Motor20 прошёл full native
parity 17/29/43 без различий, API37, realtime4, visibility8 и compact10.
Queue21 прошла четыре неизменённые строгие проверки и шесть reader tests.
Все 20 ready admissions — `body_heartbeat`; fresh query chains — 7,
четыре wait11 accepted и фактически started, rejected — 0.
Прежние Queue19 counts 10/3 к этой пробе не относятся.
Typed header `worker_execution` содержит `serial=true`, `mode=caller_serial`,
`worker_pool_created=false`, `native_task_parallelism_cap=1`; manifest содержит
`native_serial_workers=true`. Профиль совпал во всех 2107 реальных response
packets Queue21. Это профиль native worker pool, а не утверждение одного
thread во всём процессе; числа attestations не являются числом proof cases.

Gate11 заморожен, preregistration PASS за 26,768 с, targeted runtime101/final94
PASS. Независимый known audit закрыл 3/24/6: PASS за 39,393 с, issues пусты;
reader508cf… проверил фактический serial worker profile. Load фактически
начался в 17:47 UTC после завершения чужого physics-disabled regression
linux-v8 и освобождения simulation lock, unit
`gothic-body-bc-stage8-final-gate-v11-load`. Actual log фиксирует baseline до
любого engine lease/tick: сначала 80 с baseline, затем 12 cohorts
(native4/GRU4/MLP4) с одинаковыми protected20 before/during и прежним p95
budget10%. [Закрытый load Gate11](../reports/npc-body/2026-10-14-combat/gate-v11/README.md)
завершился FAIL, exit2: unit wall — 3:52,841, CPU — 1:38,928, memory — 5,6G.
Native0–3 завершились с `time_limit`; GRU0/2/3 — `control_lost`, GRU1 —
`time_limit`. Partial MLP0 после explicit release завершился infrastructure
`unexpected_control_loss:released`. [Causal audit](../reports/npc-body/2026-10-14-combat/gate-v11/load-failure.md)
подтвердил GRU0/2/3 native target_visibility_lost после завершённого RightStrafe4,
Idle и remaining_lock0: control19/24,32/35,75/78; healthy GRU1 —153/158.
Runtime fallback0 не даёт credit после handoff. Partial MLP отказал action1
target_invalid при сохранённой lease, control_allowed=false/all masks=false;
receive→send4,848299 мс, native snapshot→send6,709377 мс, RTT19,703637 мс.
Явный release7907 завершил арену infrastructure. Native20/20 ASR/TTS
−1,24908%/+8,36735% PASS, Decider/Gemma +10,59663%/+31,21631% FAIL.
Все GRU service populations16/15/20/20 invalid из-за трёх сокращённых окон;
actual before/after4PID совпали, реальный restart не заявляется.
Частичный прогон не получает общего gate credit. Первая попытка дала только
lock refusal без launcher-load/service baseline/output/requests; это
исторический отказ запуска, без quality failure. Fresh не запускался. Веса, normalizer,
physical masks и quality thresholds сохраняются. Fresh screening из 240
эпизодов не запускался, общего боевого PASS нет; прежние load и Queue17 FAIL
сохранены.
В opt-in transport header поле
`next_tick_wall_monotonic_ns` сохраняет фактический Linux CLOCK_MONOTONIC
deadline следующего tick, включая уже прошедший deadline; оно присутствует
только в active/unpaused realtime. Polling запрашивает slices не более5 мс,
сохраняет actual overshoot и все periodic/event observations и latency counts.
Поле служит транспортному ожиданию, схема184 model features прежняя.
Работа остановлена на SafePeer preflight. Сбор72, child279, пересчёт normalizer,
новое обучение и новый боевой gate не выполнялись. Private placement diagnostic
отменён, повторной калибровки и следующих запусков не запланировано.
Потеря native visibility не объявляется исправленной; perception/100 мс и
боевые thresholds прежние. Motor20/Queue21 и known audit PASS сохранены в
своём scope, прежние отказы остаются в raw и отчётах. Возобновление требует
нового указания пользователя. [Финальный статус](npc-body-stage8-stop-report.md).
Команды и reader snapshots описаны в [evals README](../evals/npc-body/README.md).
Исходные block failures и reaction-v3 сохранены.

## Допуск

После фиксации checkpoint выполняются shadow, realtime, отдельная нагрузка
четырёх настоящих NPC в одном мире и fresh screening из 20 новых paired seed
на каждую из четырёх задач. Данные screening не изменяют выбранные веса.
Порог первичного боевого допуска — task success GRU не ниже native более
чем на 15 процентных пунктов, neural control ≥95%, без invariant violations,
softlock и приписывания успеха native fallback.

Измеряются model step p95 ≤5 мс, снимок в игре → arbitration p95 ≤100 мс и
collector → отправка команды p95 ≤20 мс. Защитная реакция проверяется по
фактическому началу блока/отскока в реальном боевом окне. Дополнительно
проверяются duplicate/stale/gap/reset/end и четыре независимых recurrent
состояния и terminal barrier.

ASR/TTS/Strands/Gemma остаются в работе. Их p95 сравнивается по одному
фиксированному workload до и во время healthy нагрузки этого же запуска;
бюджет деградации — 10%. Продовые контейнеры, Docker и чужие среды не меняются.

Этот допуск относится к этапу 8. Для выпуска в обычный мир требуется отдельный
release gate с интервальной оценкой на 100 paired seed, описанный в
[плане](npc-body-control-plan.md).
