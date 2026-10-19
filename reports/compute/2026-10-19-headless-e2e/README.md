# Сколько стоит рендер в e2e на Ryzen

Дата: 19 октября 2026. Исходники: `bf0a2e6d743f1537836848f03ec7b251228974c7`. Движок, сервер, сценарии и очередь не менялись. Результат — замеры и перепись кода.

## 1. Замер

**Замер остановлен по правилу задачи: оба одиночных `fight_reactions_bandits` завершились с кодом 1.** В каждом не было ответного удара одного из бандитов за 3 с: сначала `BDT_1015_BANDIT_L`, затем `BDT_1014_BANDIT_L`. Подготовка — штатная новая игра, `LLM=fake`, одна игра, `quiet`, эксклюзивное окно. Сценарии и движок не исправлялись. Все семь измерительных заявок (шесть e2e и runner) имеют `oom_kill=0` и **0 пересечений с другими заявками gq** по журналу. После второй ошибки отменены две ещё не допущенные подготовки повторных `stages`; лимит FPS не запускался. Активных и ожидающих своих заявок после остановки — **0**.

Данные частичные: у двух первых `stages` получены CPU, потоки, RSS, общий GPU и FPS, но не получены SM/VRAM процесса из-за ошибки разбора `pmon` в наблюдателе. До `crimes` исправлены число полей и буферизация вывода; повторные `stages` были поставлены в очередь и сняты правилом остановки. Пропуски ниже оставлены пустыми. Их нельзя заменять VRAM другого сценария.

### Условия и способ подсчёта

Ryzen 3950X, RTX 3080 10 ГиБ, NVIDIA 615.71.09, Xwayland, клиент **640×360**, обычная `RelWithDebInfo`; у оконной игры включён `OPENGOTHIC_ENABLE_APP_CADENCE=ON`. Рендер из `game.log`: ray query=1, meshlets=1, GI=1, AA=1. Окно второго `crimes` проверено через `xwininfo/xprop`: 640×360 в (100,137), Normal, активно на текущем рабочем столе — [снимок состояния](window-checks.json). Кэши не очищались. Полные сведения — [environment.json](environment.json).

Запуски: `BENCH_CLASS=quiet RYZEN_DISPLAY_EXCLUSIVE=1 OPENGOTHIC_FPS_LOG=1 SCENARIO=<имя> LLM=fake TIMEOUT=600 scripts/ryzen.sh e2e`. Для `stages` и `crimes` передан канонический `save_slot_1.sav` (SHA-256 в environment); бой использует новую игру. Все три — мир `NEWWORLD.ZEN`. Очередь общая; отдельной копии gq и обхода допуска не было. Два отменённых повтора имели отдельные временные каталоги снимков **той же ветки**, без новых Git worktree; после сбора они удалены.

[Наблюдатель sample.py](sample.py) читает `/proc/<pid>/stat`, `task/*/stat` и `comm` примерно раз в секунду. CPU — прирост `utime+stime` / фактический интервал: **1,00 = одно занятое логическое ядро**; это не накопленный `%CPU` из ps. RSS — resident pages × 4096. Средние CPU/GPU ниже считаются **после первой загрузки мира**, включая следующие сохранения/загрузки; RSS и VRAM — наблюдавшийся пик за жизнь процесса, с разрешением около 1 с. Сервер — отдельный главный `gothic-ai` процесс; вспомогательный fake TTS/launcher в его CPU не входят.

GPU карты — `nvidia-smi --query-gpu=utilization.gpu,memory.used --loop=1`; процесс — `pmon -s um -d 1`. **SM** — отдельная метрика процесса, средняя только по доступным числам; `-` сохранено как отсутствие отсчёта, а не ноль. Покрытие показано явно. Общий GPU содержит рабочий стол; у baseline он в среднем 11,2%, хотя runner не имеет GPU-контекста. FPS — сумма кадров / сумма времени полных **10-секундных** интервалов `OPENGOTHIC_FPS_LOG`, включая загрузки; свежего FPS каждую секунду этот флаг не выдаёт.

### CPU, GPU и память

В ячейках CPU и RSS порядок **игра / главный сервер**. SM: среднее известных отсчётов и их число / число отсчётов процесса. Для `stages` SM/VRAM не сняты. Исходные интервалы и все имена потоков — в [process.csv](process.csv), [threads.csv](threads.csv); сводные числа — в [summary.json](summary.json).

| Режим / прогон | CPU, ядра | GPU карты, среднее % | SM игры, % (покрытие) | VRAM игры, пик МиБ | RSS, пик МиБ | Интервалы игры после loaded: число / секунды |
|---|---:|---:|---:|---:|---:|---:|
| stages-1 | 2,32 / 0,16 | 19,0 | — | — | 1752 / 253 | 8 / 8,2 |
| stages-2 | 2,31 / 0,15 | 37,3 | — | — | 1746 / 238 | 9 / 9,2 |
| crimes-1 | 0,58 / 0,04 | 16,7 | 16,1 (35/103) | 1223 | 3382 / 245 | 103 / 105,4 |
| crimes-2 | 1,74 / 0,09 | 19,5 | 16,4 (33/42) | 1223 | 3297 / 242 | 42 / 43,1 |
| fight_reactions_bandits-1 | 2,86 / 0,08 | 28,1 | 17,6 (21/21) | 845 | 1412 / 239 | 21 / 21,6 |
| fight_reactions_bandits-2 | 2,88 / 0,13 | 26,1 | 16,7 (19/21) | 845 | 1423 / 238 | 21 / 21,6 |
| runner, realtime idle 60 с | 1,00 / — | 11,2 (фон) | 0, контекста нет | 0 | 549 / — | 57 / 58,2 |
| stages, лимит 30 FPS | — | — | — | — | — | 0: не запускался |

У `crimes-1` шесть полных интервалов, всего **61,5 с**, дали 0,95–0,99 FPS. Причина падения частоты не установлена. Весь прогон прошёл 28 проверок, но его низкое среднее CPU не описывает непрерывный рендер. На интервалах с зафиксированным средним FPS ≥30 CPU игры составил **1,73 ядра** в первом и **1,80** во втором `crimes` (правило отбора и секунды есть в summary). Среднее за весь прогон из таблицы не скрыто.

### Загрузка, соединение и результат

Рождение процесса восстановлено по `/proc` starttime. Строки журналов наблюдались примерно каждые 50 мс; Logger сбрасывает вывод по строкам. `Мир` — первая `[ai] session ... (load/new_game)` из `onWorldLoaded` ([aibridge.cpp:634](../../../OpenGothic/ai_bridge/aibridge.cpp#L634), [696](../../../OpenGothic/ai_bridge/aibridge.cpp#L696)); это готовность мира, а не измерение первого нарисованного кадра. `TCP` — `game connected from` в server.log; `bridge ready` — `[ai] connected to` при обработке Open в игровом tick ([546](../../../OpenGothic/ai_bridge/aibridge.cpp#L546)). Сокет открывается раньше первой обработки мостом, поэтому приведены обе метки. Сырые строки с временем наблюдения — [markers.jsonl](markers.jsonl).

`Заявка` включает запуск сервера и завершение своих процессов, но не очередь, upload и сборку. Записи допуска/конца и отмен двух подготовок — [queue-runs.json](queue-runs.json).

| Прогон | Заявка, с | Мир от старта игры, с | TCP, с | bridge ready, с | FPS полных окон | Проверки / результат |
|---|---:|---:|---:|---:|---:|---|
| stages-1 | 17,9 | 4,86 | 2,75 | 4,91 | 71,59 | 7/7, код 0 |
| stages-2 | 17,8 | 4,79 | 0,44 | 4,84 | 71,46 | 7/7, код 0 |
| crimes-1 | 114,4 | 4,78 | 0,43 | 4,83 | 21,73 | 28/28, код 0 |
| crimes-2 | 51,2 | 4,76 | 0,47 | 4,86 | 68,44 | 28/28, код 0 |
| fight_reactions_bandits-1 | 32,2 | 5,93 | 0,50 | 5,98 | 100,62 | 5/6, код 1 |
| fight_reactions_bandits-2 | 31,3 | 5,88 | 0,48 | 5,93 | 102,49 | 5/6, код 1 |
| runner idle | 65,1 | 4,47 | — | — | нет рендера; шаг 33 мс | код 0 |
| stages, 30 FPS | — | — | — | — | — | не запускался |

Runner собран в своей папке через gq; [run_baseline.py](run_baseline.py) запускает его с `--mode realtime --stdio`, сразу посылает reset и измеряет первый ответ `loaded:true`. Сейв тот же, [idle.bench](idle.bench) содержит только `wait 60000` и `quit`; управляющего ввода герою нет. `OPENGOTHIC_BODY_TRACE=0`, без `--serial-workers`. **60,033 с симуляции за 60,020 с реального времени**, максимальное отставание 234 мс; `graphics_device_created/window_created/audio_device_created=false` — [baseline-1-result.json](baseline-1-result.json). `reset_wall_ms=4324` исключает первоначальную инициализацию, поэтому полное время старта — 4,47 с, а не 4,32 с.

Runner — **нижняя граница затрат сопоставимого активного мира**: нет AiBridge, сервера, диалогов, сценарных действий и повторных загрузок. В нём физика по умолчанию на вызывающем потоке (`OPENGOTHIC_PHYSICS_THREADS=0`), шаг 33 мс, CPU viewport 640×480 ([bodyrunner.cpp:336](../../../OpenGothic/game/bodyrunner.cpp#L336), [1401](../../../OpenGothic/game/bodyrunner.cpp#L1401)); в обычной игре физика создаёт hardware_concurrency−2 = 30 рабочих потоков ([collisionworld.cpp:282](../../../OpenGothic/common/physics/collisionworld.cpp#L282)). Это не полный A/B одного исполнения сценария.

### Какие потоки расходуют CPU

Ниже ядра CPU после первой загрузки. `Workers [0..15]` — пул CPU-работ движка; `Worker 1..30` — Jolt ([JobSystemThreadPool.cpp:300](../../../OpenGothic/lib/JoltPhysics/Jolt/Core/JobSystemThreadPool.cpp#L300)). Главный поток определён по TID=PID: другие потоки тоже могут наследовать имя `Main thread`. Полная сводка по именам и числу TID — [threads-summary.csv](threads-summary.csv).

| Прогон | Главный поток | Workers [0..15], сумма | Worker 1..30, сумма | Loading thread, сумма | Прочие и неразнесённый остаток |
|---|---:|---:|---:|---:|---:|
| stages-1 | 0,75 | 1,12 | 0,00 | 0,23 | 0,21 |
| stages-2 | 0,75 | 1,13 | 0,02 | 0,23 | 0,18 |
| crimes-1 | 0,19 | 0,13 | 0,00 | 0,13 | 0,13 |
| crimes-2 | 0,63 | 0,50 | 0,00 | 0,35 | 0,27 |
| fight_reactions_bandits-1 | 0,93 | 1,69 | 0,17 | 0,03 | 0,03 |
| fight_reactions_bandits-2 | 0,94 | 1,69 | 0,18 | 0,03 | 0,04 |
| baseline-1 | 0,21 | 0,79 | 0,00 | 0,00 | 0,00 |

При 1 Гц короткоживущий поток может закончиться между отсчётами. CPU процесса учитывает его время, а разложение по TID — не всегда; остаток показан явно. По `/proc` нельзя разделить функции рендера, VM, анимации и ожидания внутри главного потока. У runner примерно **0,21 ядра главный поток + 0,79 пул Workers**: CPU-анимация и мир остаются существенными и без GPU.

### Что можно заключить и сколько игр помещается

На участках с обычным FPS оконная игра потребляла примерно **1,7–2,9 ядра**, idle runner — **1,0**. Разность **0,7–1,9 ядра (около 40–65%)** — оптимистический ориентир экономии совокупности удаляемых/изменяемых затрат при таком сравнении, **не измеренная чистая доля CPU рендера**: меняются действия, мост, загрузки, частота шага и число потоков физики. GPU-контекст runner отсутствует, тогда как игра занимала **845–1223 МиБ VRAM** в измеренных `crimes`/боях. На том же сейве старт сократился лишь примерно с 4,8 до 4,47 с — около **0,3 с / 7%**. Главный ожидаемый выигрыш — ресурс GPU и параллельность, а не загрузка мира.

Существующий лимит FPS есть: локальный `Gothic.ini`, `[ENGINE] zMaxFps=30`, при `SystemPack.ini [PARAMETERS] FPS_Limit=0` ([mainwindow.cpp:409](../../../OpenGothic/common/mainwindow.cpp#L409), [1389](../../../OpenGothic/common/mainwindow.cpp#L1389)). Он использует целочисленные 33 мс, то есть потолок около 30,3 FPS. **Измерения этого режима нет:** после двух ошибок бойцов новые игровые проверки остановлены. По этим данным нельзя утверждать его экономию или стабильность боёв.

| Расчёт для пула 28 логических потоков / 20 ГиБ | Одновременных игр | Граница применимости |
|---|---:|---|
| Текущая заявка 4 потока / 5 ГиБ, только убрать окно | до 4 по RAM | параметры заявок в этом PR не меняются; сейчас оконных слотов 3 |
| Idle runner + максимальное измеренное среднее главного сервера: 0,999+0,160 ядра, RSS 549+253 МиБ | CPU ≤24; RSS ≤25; вместе **≤24** | оптимистическая арифметика без накладных моста/меню/сценария; 28 SMT-потоков не равны 28 физическим ядрам |
| Плановый бюджет headless: 2 потока и 2–2,5 ГиБ на заявку | **8–10** | ориентир с запасом для действий, повторных загрузок и пиков; требует замера настоящего headless e2e после реализации |

Это оценка пула целиком под headless. Реальная ёмкость ещё не проверена: в idle нет боёв и lifecycle e2e. Сам по себе переход на headless не доказывает стабильность бойцов: **оба одиночных боя здесь не прошли** при средних FPS 100,62 и 102,49. Причины сохранены в [failures.json](failures.json); исправление механики вне этого PR.

Не получено по правилу остановки: SM/VRAM процесса для двух `stages`, два их полных повтора и один `stages` с лимитом FPS. Тесты кода не запускались: изменений кода проекта нет.


## 2. Перепись сценариев

В [карте](../../../docs/README.md#тесты-и-прогоны) 90 имён с развёрнутым `m0`–`m6`; [шапка e2e.sh](../../../scripts/e2e.sh#L3) добавляет четыре `fight_reactions_*`. Всего **94 актуальных сценария**. Сейчас неизменённый runner запускает **0/94 e2e**: в нём нет AiBridge. Ниже — прогноз после общего подключения моста, lifecycle save/load, таймеров, realtime, консоли и отделения UI-состояния от рисования.

Категории соответствуют брифу: **1** — DialogMenu; **2** — аудио; **3** — кадры/видео; **4** — камера/рендер для логики; **5** — окно/ввод. `—` означает, что проверяемому условию зависимость не нужна, а не отсутствие такого кода во всей игре. `N` — native/SVM или текстовая речь с длительностью: тихий путь runner уже есть. `PCM` — нужен тихий потребитель потокового звука; `real` — проверка настоящего аудиовывода. `CPU` — нужна камера/геометрия/фокус на CPU, GPU для этого не нужен. `inject`, PTT/debug, WAV и bench означают программную команду или файл: **OS-клавиатура, мышь и микрофон игры не нужны**. `opt` — запись нужна только при дополнительном режиме демонстрации/записи.

Группы: **Б** — достаточно общих адаптеров; **Д** — дополнительно DialogMenu без рисования; **З** — дополнительно тихий PCM; **А** — отдельная проверка настоящего звука; **В** — сохранить видеозапись/рендер. `programs` требует и меню, и PCM, учитывается один раз в Д. `?` — **не уверен**, причина указана в строке.

| Сценарий | 1 меню | 2 звук | 3 кадры | 4 камера/рендер | 5 ввод | Группа | Основание в коде |
|---|---|---|---|---|---|---|---|
| m0 | — | — | — | — | — | Б | [basics.py:69,91](../../../server/src/gothic_ai/scenarios/basics.py#L69) |
| m1 | — | N | — | — | inject | Б | [basics.py:106,118](../../../server/src/gothic_ai/scenarios/basics.py#L106) |
| m2 | — | N | — | — | inject | Б | [basics.py:324,357](../../../server/src/gothic_ai/scenarios/basics.py#L324) |
| m3 | — | N | — | — | WAV | Б | [basics.py:421,483](../../../server/src/gothic_ai/scenarios/basics.py#L421); WAV на сервере |
| m4 | — | N | — | CPU | — | Б | [basics.py:499,513](../../../server/src/gothic_ai/scenarios/basics.py#L499) |
| m5 | — | N | — | — | inject | Б | [basics.py:566,632,667](../../../server/src/gothic_ai/scenarios/basics.py#L566) |
| m5b | — | N | — | — | inject | Б | [basics.py:700,823,871](../../../server/src/gothic_ai/scenarios/basics.py#L700) |
| m5c | — | N | — | — | inject | Б | [basics.py:911,971](../../../server/src/gothic_ai/scenarios/basics.py#L911) |
| m5e | — | N | — | CPU focus | PTT/debug | Б | [basics.py:1048,1063,1089](../../../server/src/gothic_ai/scenarios/basics.py#L1048) |
| m6 | — | N | — | — | inject | Б | [basics.py:1190,1195](../../../server/src/gothic_ai/scenarios/basics.py#L1190); heard_in_menu проверяется direct choose |
| tts | — | real | opt | — | inject | А | [basics.py:398,405,412](../../../server/src/gothic_ai/scenarios/basics.py#L398); По смыслу проверяет настоящее проигрывание |
| websocket | — | N | — | — | inject | Б | [basics.py:153,157,210](../../../server/src/gothic_ai/scenarios/basics.py#L153) |
| orders | — | N | — | CPU focus | inject | Б | [companions.py:1191,1202,1298](../../../server/src/gothic_ai/scenarios/companions.py#L1191) |
| companion | — | N | — | CPU focus | inject | Б | [companions.py:1833,1909,1917](../../../server/src/gothic_ai/scenarios/companions.py#L1833) |
| calm | — | N | — | CPU | inject | Б | [companions.py:1960,2028,2035](../../../server/src/gothic_ai/scenarios/companions.py#L1960) |
| wolf | — | N | — | CPU | inject | Б | [companions.py:1621,1638](../../../server/src/gothic_ai/scenarios/companions.py#L1621); beasts.listen |
| rescue | — | N | — | CPU | inject | Б | [companions.py:828,887,934,980](../../../server/src/gothic_ai/scenarios/companions.py#L828) |
| rescue_self | — | N | — | CPU | inject | Б | [companions.py:828,934](../../../server/src/gothic_ai/scenarios/companions.py#L828); cli.py:1387, killer=npc |
| neural | — | N | — | — | inject | Б | [companions.py:54,591,637](../../../server/src/gothic_ai/scenarios/companions.py#L54) |
| neural-companion | — | N | — | — | inject | Б | [companions.py:59,591,734](../../../server/src/gothic_ai/scenarios/companions.py#L59) |
| neural-group | — | N | — | — | inject | Б | [companions.py:327,430,524](../../../server/src/gothic_ai/scenarios/companions.py#L327) |
| neural-talk | — | N | — | — | inject | Б | [companions.py:198,257](../../../server/src/gothic_ai/scenarios/companions.py#L198); neural_knockout.py:289 |
| neural-load | — | N | — | — | inject | Б | [neural_load.py:47,231,313](../../../server/src/gothic_ai/scenarios/neural_load.py#L47) |
| see | — | N | — | CPU | inject | Б | [perception.py:544,651,668](../../../server/src/gothic_ai/scenarios/perception.py#L544) |
| seen | — | N | — | CPU | inject | Б | [perception.py:702,824,859](../../../server/src/gothic_ai/scenarios/perception.py#L702) |
| hearing | — | N | — | CPU | inject | Б | [perception.py:335,385](../../../server/src/gothic_ai/scenarios/perception.py#L335); talk.hear, без микрофона |
| cave | — | N | — | CPU | inject | Б | [perception.py:139,201,243](../../../server/src/gothic_ai/scenarios/perception.py#L139) |
| ground | — | N | — | CPU | inject | Б | [perception.py:46,108,113,121](../../../server/src/gothic_ai/scenarios/perception.py#L46) |
| places | — | — | — | — | — | Б | [cli.py:1704](../../../server/src/gothic_ai/cli.py#L1704); places.py:89, JSON waypoints/people |
| knows | — | N | — | — | inject | Б | [perception.py:271,320](../../../server/src/gothic_ai/scenarios/perception.py#L271) |
| remembers | — | N | — | CPU | inject | Б | [perception.py:417,491,506](../../../server/src/gothic_ai/scenarios/perception.py#L417) |
| trace | — | N | — | CPU aim | bench | Б | [perception.py:907](../../../server/src/gothic_ai/scenarios/perception.py#L907); trace-e2e.bench:27,29,34,37 — CPU camera ray |
| broken | — | N | + | CPU | bench | В | [broken.py:69](../../../server/src/gothic_ai/scenarios/broken.py#L69); broken-e2e.bench:17,29 — два rec |
| day | — | PCM | — | CPU | inject | З | [npc_life.py:1225,1488,1498](../../../server/src/gothic_ai/scenarios/npc_life.py#L1225) |
| noise | — | N | — | CPU | inject | Б | [npc_life.py:1006,1153,1173](../../../server/src/gothic_ai/scenarios/npc_life.py#L1006); Физический noise, не аудиоустройство |
| initiative | — | N | — | CPU | inject/deal | Б | [npc_life.py:664,665,745,772](../../../server/src/gothic_ai/scenarios/npc_life.py#L664); Проверяет guard важного диалога и помечает told |
| lead | — | N | — | CPU | inject/deal | Б | [npc_life.py:137,145,180](../../../server/src/gothic_ai/scenarios/npc_life.py#L137) |
| meet | — | N | — | — | inject/deal | Б | [npc_life.py:281,304,353](../../../server/src/gothic_ai/scenarios/npc_life.py#L281) |
| meet_missed | — | N | — | — | inject/deal | Б | [npc_life.py:217,330](../../../server/src/gothic_ai/scenarios/npc_life.py#L217); cli.py:1576,1585 |
| errand | — | PCM | — | CPU | inject | З | [npc_life.py:1632,1674,1689,1716](../../../server/src/gothic_ai/scenarios/npc_life.py#L1632) |
| errand_fork | — | PCM | — | CPU | inject | З | [npc_life.py:1754](../../../server/src/gothic_ai/scenarios/npc_life.py#L1754); cli.py:1590 |
| errand_far | — | PCM | — | CPU | inject | З | [npc_life.py:1718](../../../server/src/gothic_ai/scenarios/npc_life.py#L1718); Ранние реплики voiced |
| errand_give | — | PCM | — | CPU | inject | З | [npc_life.py:1779,1823,1843](../../../server/src/gothic_ai/scenarios/npc_life.py#L1779); TTS включён e2e.sh:350 |
| chests | — | N | — | CPU | inject | Б | [npc_life.py:371,442,455,484](../../../server/src/gothic_ai/scenarios/npc_life.py#L371) |
| chests_lobart | — | N | — | CPU | inject | Б | [npc_life.py:371,442](../../../server/src/gothic_ai/scenarios/npc_life.py#L371); cli.py:1547,1551 |
| group | — | PCM | — | CPU | inject | З | [conversation.py:161,177,188,262](../../../server/src/gothic_ai/scenarios/conversation.py#L161) |
| interject | — | PCM | — | CPU | inject | З | [conversation.py:279,334,341,360](../../../server/src/gothic_ai/scenarios/conversation.py#L279) |
| gossip | — | PCM | — | CPU | PTT/debug | З | [conversation.py:593,595,639,810](../../../server/src/gothic_ai/scenarios/conversation.py#L593); Ждёт voice starts/played |
| gossip_demo | — | N | opt | CPU | inject | Б | [conversation.py:896,904,951](../../../server/src/gothic_ai/scenarios/conversation.py#L896); Внешний gossip.bench требует рендера |
| cards | — | — | — | — | — | Б | [conversation.py:848,862](../../../server/src/gothic_ai/scenarios/conversation.py#L848) |
| deal | — | N | — | — | debug deal | Б | [deals.py:89,97,102,122](../../../server/src/gothic_ai/scenarios/deals.py#L89); Нужна модель состояния ChatOverlay/deal |
| deal_demo | — | N | opt | CPU | debug deal | Б | [deals.py:475,486,516](../../../server/src/gothic_ai/scenarios/deals.py#L475); Внешний deal.bench требует рендера; модель deal |
| debt | — | N | — | — | debug deal | Б | [deals.py:327,334,372,376](../../../server/src/gothic_ai/scenarios/deals.py#L327); Модель deal |
| contract | — | N | — | — | debug deal | Б | [deals.py:216,223,242](../../../server/src/gothic_ai/scenarios/deals.py#L216); Модель deal |
| haggle | — | N | — | — | debug deal | Б | [deals.py:438,463](../../../server/src/gothic_ai/scenarios/deals.py#L438); Модель deal |
| lie | — | N | — | CPU | inject | Б | [lies.py:98,105,130](../../../server/src/gothic_ai/scenarios/lies.py#L98) |
| vlie | — | N | — | — | inject | Б | [lies.py:281,336,337,369,379](../../../server/src/gothic_ai/scenarios/lies.py#L281); Direct choose |
| vgive | — | N | — | — | inject | Б | [lies.py:462,482,489](../../../server/src/gothic_ai/scenarios/lies.py#L462); Scripted effects ending |
| vcheck | — | N | — | CPU | inject | Б | [lies.py:529,586,599,608](../../../server/src/gothic_ai/scenarios/lies.py#L529) |
| rumor | — | N | — | — | inject | Б | [lies.py:672,680,715](../../../server/src/gothic_ai/scenarios/lies.py#L672) |
| witness | — | N | — | CPU | inject | Б | [lies.py:1047,1051,1108](../../../server/src/gothic_ai/scenarios/lies.py#L1047) |
| amends | ? | N | — | — | inject | Д? | [lies.py:861,864,867](../../../server/src/gothic_ai/scenarios/lies.py#L861); Не уверен: auto important TALK, fallback choose |
| amends_deal | ? | N | — | — | debug deal | Д? | [lies.py:861,892,898](../../../server/src/gothic_ai/scenarios/lies.py#L861); Не уверен: auto important TALK, fallback choose |
| crimes | + | N | — | CPU | debug menu | Д | [crimes.py:427,431,437](../../../server/src/gothic_ai/scenarios/crimes.py#L427); Настоящий выбор карманной кражи |
| crime_react | — | N | — | CPU | inject | Б | [crime_react.py:441,453](../../../server/src/gothic_ai/scenarios/crime_react.py#L441); Native SVM queued/done |
| crime_meet | + | N | — | CPU | inject | Д | [crime_meet.py:578,598,621](../../../server/src/gothic_ai/scenarios/crime_meet.py#L578); Обязательный automatic TALK |
| crime_talk | — | N | — | CPU | debug deal | Б | [crime_talk.py:261,324,329](../../../server/src/gothic_ai/scenarios/crime_talk.py#L261); Модель deal |
| crime_law | + | N | — | — | debug menu/deal | Д | [crime_law.py:222,224,233](../../../server/src/gothic_ai/scenarios/crime_law.py#L222) |
| crime_director | ? | N | — | CPU | inject/deal | Д? | [crime_director.py:626,648,650](../../../server/src/gothic_ai/scenarios/crime_director.py#L626); Не уверен: auto important Amends, fallback choose |
| wrath | — | N | — | CPU | inject | Б | [lies.py:174,178,180,213](../../../server/src/gothic_ai/scenarios/lies.py#L174); Закрывает возможный intro, проверяет spells/raised |
| trace_crime | — | N | — | CPU | bench/debug | Б | [trace_crime.py:104,381](../../../server/src/gothic_ai/scenarios/trace_crime.py#L104); trace_crime-e2e.bench:12,16 — без rec |
| graph | — | N | — | — | inject | Б | [story.py:131,139,142](../../../server/src/gothic_ai/scenarios/story.py#L131); Choose и original SayCmd |
| programs | + | PCM | — | — | debug menu | Д | [story.py:238,247,344,350,360](../../../server/src/gothic_ai/scenarios/story.py#L238); Также нужен тихий PCM |
| repair | —? | N | — | — | inject | Б? | [story.py:545,547,550,554,558](../../../server/src/gothic_ai/scenarios/story.py#L545); Не уверен: необязательный native greeting |
| authored | —? | N | — | — | inject | Б? | [story.py:935,937,956,988](../../../server/src/gothic_ai/scenarios/story.py#L935); Не уверен: необязательный native greeting |
| own | — | — | — | — | inject | Б | [story.py:2134,2165,2216](../../../server/src/gothic_ai/scenarios/story.py#L2134) |
| own_repair | —? | N | — | — | inject | Б? | [story.py:1056,1102,1200,1306](../../../server/src/gothic_ai/scenarios/story.py#L1056); Не уверен: необязательный native greeting |
| trigger | — | N | — | — | inject | Б | [story.py:2254,2423,2438,2463](../../../server/src/gothic_ai/scenarios/story.py#L2254) |
| stages | —? | N | — | — | inject | Б? | [story.py:1102,1184,1200,1306](../../../server/src/gothic_ai/scenarios/story.py#L1102); Не уверен: native greeting; свои choose не требуют меню |
| requires | —? | N | — | — | inject | Б? | [story.py:1355,1357,1483,1525,1536](../../../server/src/gothic_ai/scenarios/story.py#L1355); Не уверен: необязательный native greeting |
| chain | — | N | — | — | inject | Б | [story.py:1688,1709,1749](../../../server/src/gothic_ai/scenarios/story.py#L1688) |
| cast | — | — | — | — | inject | Б | [story.py:1896,1948,1984,1993](../../../server/src/gothic_ai/scenarios/story.py#L1896) |
| person | —? | N | — | — | inject | Б? | [story.py:2081,2105,2109,2119](../../../server/src/gothic_ai/scenarios/story.py#L2081); Не уверен: необязательный native greeting |
| character | —? | N | — | — | inject | Б? | [story.py:854,901](../../../server/src/gothic_ai/scenarios/story.py#L854); Не уверен: вызывает authored_quest |
| occasions | — | N | — | — | inject | Б | [occasions.py:20,84,148,171](../../../server/src/gothic_ai/scenarios/occasions.py#L20) |
| questlog | + | N | — | — | debug menu | Д | [questlog.py:72,79,86,91](../../../server/src/gothic_ai/scenarios/questlog.py#L72) |
| showrunner | — | N | — | — | inject | Б | [showrunner.py:429](../../../server/src/gothic_ai/scenarios/showrunner.py#L429); Вызывает story.py:1749 |
| showrunner_live | — | N | — | — | inject | Б | [showrunner.py:621](../../../server/src/gothic_ai/scenarios/showrunner.py#L621); Вызывает story.py:1749 |
| vanilla_line | + | N | — | — | debug menu | Д | [vanilla_line.py:138,142,146,150,217](../../../server/src/gothic_ai/scenarios/vanilla_line.py#L138) |
| vanilla_line_live | + | N | — | — | debug menu | Д | [vanilla_line.py:138,150,282,295](../../../server/src/gothic_ai/scenarios/vanilla_line.py#L138) |
| fight_reactions_wolf | — | PCM | — | CPU | inject | З | [scenario_fight_reactions.py:196,399,674,705](../../../server/src/gothic_ai/scenario_fight_reactions.py#L196); TTS включён e2e.sh:350 |
| fight_reactions_sight | — | PCM | — | CPU | inject | З | [scenario_fight_reactions.py:498,515,569](../../../server/src/gothic_ai/scenario_fight_reactions.py#L498); Native sight rays, без render visibility |
| fight_reactions_bandits | — | PCM+N | — | CPU | inject | З | [scenario_fight_reactions.py:188,865,879](../../../server/src/gothic_ai/scenario_fight_reactions.py#L188); Native duration, общий TTS setup |
| fight_reactions_flee | — | PCM+N | — | CPU | inject | З | [scenario_fight_reactions.py:1049,1059,1062,1071](../../../server/src/gothic_ai/scenario_fight_reactions.py#L1049); Voiced и native shout |

`Б?`: семь сюжетных сценариев допускают побочный native greeting. Собственные квестовые выборы идут напрямую через `choose_dialog`; без проверки состояния сейва нельзя исключить автоматический `AI_ProcessInfo`. `Д?`: `amends`, `amends_deal`, `crime_director` сначала ждут важный native TALK, но имеют fallback choose. Без меню такой fallback может дать зелёный результат, потеряв проверку задуманного начала встречи. Эти три строки консервативно оставлены в Д.

`tts` формально проверяет только `LineStarted.voiced` ([basics.py:398–414](../../../server/src/gothic_ai/scenarios/basics.py#L398)); тихий sink сможет дать зелёный результат. По смыслу сценарий проверяет путь проигрывания, поэтому консервативно он оставлен в А. На Ryzen уже используется OpenAL null-driver: физические динамики этим тестом не проверяются. SoundDevice/микшер и отсутствие окна — разные зависимости. `GOTHIC_TTS_RECORD=1` добавляет loopback и видео ([e2e.sh:450](../../../scripts/e2e.sh#L450)). Тихий sink не заменяет проверку реального микса и губ по записанному звуку. `deal_demo/gossip_demo` в штатном e2e проверяют данные; внешние `scripts/bench/deal.bench` и `gossip.bench` — демонстрации с рендером. `broken` действительно делает два `rec` ([broken-e2e.bench:17](../../../scripts/bench/broken-e2e.bench#L17), [29](../../../scripts/bench/broken-e2e.bench#L29)). Само наличие `OPENGOTHIC_BENCH` ещё не означает запись: `trace_crime` использует только барьеры и физику, а `trace` — луч CPU-камеры ([trace-e2e.bench:27](../../../scripts/bench/trace-e2e.bench#L27), [trace_crime-e2e.bench:12](../../../scripts/bench/trace_crime-e2e.bench#L12)).

### Общие обработчики, на которых основана классификация

| Путь | Почему не требует окна или что нужно сохранить |
|---|---|
| Настоящее меню | `talk/menu/menu_pick` идут в callback ([aibridge.cpp:1869](../../../OpenGothic/ai_bridge/aibridge.cpp#L1869), [1883](../../../OpenGothic/ai_bridge/aibridge.cpp#L1883), [1909](../../../OpenGothic/ai_bridge/aibridge.cpp#L1909)); сейчас их привязывает [MainWindow:161](../../../OpenGothic/common/mainwindow.cpp#L161). |
| Прямой choose и `AI_Output` | [dialoggraph.cpp:614](../../../OpenGothic/ai_bridge/dialoggraph.cpp#L614) вызывает `Gothic::dialogExec` напрямую. Начальный pipe NPC — GlobalOutput ([npc.cpp:253](../../../OpenGothic/common/world/objects/npc.cpp#L253), [gamescript.cpp:55](../../../OpenGothic/common/game/gamescript.cpp#L55), [1362](../../../OpenGothic/common/game/gamescript.cpp#L1362), [1691](../../../OpenGothic/common/game/gamescript.cpp#L1691)). Меню-pipe устанавливает `AI_ProcessInfo` ([npc.cpp:4267](../../../OpenGothic/common/world/objects/npc.cpp#L4267)). Поэтому не всякий выбор реплики требует DialogMenu. |
| Native/SVM звук | [WorldSound:175](../../../OpenGothic/common/world/worldsound.cpp#L175), [206](../../../OpenGothic/common/world/worldsound.cpp#L206) уже держит длительность без устройства; native observer сохраняет queued/done ([npcobserver.cpp:623](../../../OpenGothic/ai_bridge/npcobserver.cpp#L623), [730](../../../OpenGothic/ai_bridge/npcobserver.cpp#L730)). |
| Потоковый PCM | [GameSession:304](../../../OpenGothic/common/game/gamesession.cpp#L304) без устройства возвращает пустой Sound; PCM/lips сейчас продвигает audio callback ([voicebuffer.cpp:44](../../../OpenGothic/ai_bridge/voicebuffer.cpp#L44), [54](../../../OpenGothic/ai_bridge/voicebuffer.cpp#L54)). Без CPU drain `tickVoices` не завершит непустой поток ([aibridge.cpp:2242](../../../OpenGothic/ai_bridge/aibridge.cpp#L2242), [2280](../../../OpenGothic/ai_bridge/aibridge.cpp#L2280)). |
| Ввод и deal | PTT/type синтезируются мостом ([aibridge.cpp:1549](../../../OpenGothic/ai_bridge/aibridge.cpp#L1549), [1561](../../../OpenGothic/ai_bridge/aibridge.cpp#L1561), [2049](../../../OpenGothic/ai_bridge/aibridge.cpp#L2049)). Deal выбирается программно ([1448](../../../OpenGothic/ai_bridge/aibridge.cpp#L1448), [chatoverlay.cpp:193](../../../OpenGothic/ai_bridge/chatoverlay.cpp#L193)); нужно сохранить его модель состояния без создания overlay ([aibridge.cpp:2392](../../../OpenGothic/ai_bridge/aibridge.cpp#L2392)). |
| Камера и видимость | Фокус: [PlayerControl:344](../../../OpenGothic/common/game/playercontrol.cpp#L344) → [World:748](../../../OpenGothic/common/world/world.cpp#L748) → [WorldObjects:1259](../../../OpenGothic/common/world/worldobjects.cpp#L1259): дистанция, поворот и физические лучи. Camera нужна для left/right ([npcview.cpp:437](../../../OpenGothic/ai_bridge/npcview.cpp#L437)), listener/range ([worldsound.cpp:225](../../../OpenGothic/common/world/worldsound.cpp#L225)) и manual aim ([playercontrol.cpp:1077](../../../OpenGothic/common/game/playercontrol.cpp#L1077)). Это CPU-логика. |
| Отсечение и заморозка | Качество AI зависит от расстояния до героя ([worldobjects.cpp:310](../../../OpenGothic/common/world/worldobjects.cpp#L310)); отдельно есть free-camera freeze ([251](../../../OpenGothic/common/world/worldobjects.cpp#L251), [npc.cpp:7225](../../../OpenGothic/common/world/objects/npc.cpp#L7225)). В проверенных e2e-путях нет зависимости условий AI от GPU visibility. CPU Camera, PlayerControl и TestBench уже сохраняет runner ([bodyrunner.cpp:327](../../../OpenGothic/game/bodyrunner.cpp#L327), [SimulationStep:42](../../../OpenGothic/common/game/simulationstep.cpp#L42)). |

### Сколько сценариев и сколько прогонов

Считались **начатые** заявки `event=started`, имя начинается ровно с `e2e `, время старта **18.10.2026 00:00–24:00 Europe/Moscow (UTC+3)**. Сборки, отмены до допуска и ожидание не входят; неудачные и повторные начатые прогоны входят. Это число прогонов, не доля CPU-времени и не число уникальных сценариев. Источник — `~/gothic-ai/queue/journal.jsonl`; публичная выжимка — [journal-starts-2026-10-18.csv](journal-starts-2026-10-18.csv). В календарных сутках UTC получается 252 заявки; их не смешивали с московскими сутками.

| Прогнозная группа | Сценариев из 94 | Прогонов из 183 | Доля прогонов |
|---|---:|---:|---:|
| Б: общие headless-адаптеры | 70 (из них 7 не уверен) | 94 | 51,37% |
| Д: DialogMenu без рисования | 10 (из них 3 не уверен; programs также PCM) | 32 | 17,49% |
| З: тихий streaming PCM | 12 | 31 | 16,94% |
| А: настоящая проверка аудиовывода, tts | 1 | 4 | 2,19% |
| В: запись видео, broken | 1 | 0 | 0,00% |
| Исторические имена, зависимости не установлены | — | 22 | 12,02% |
| **Всего** | **94** | **183** | **100%** |

Исторические `calm_companion` (10) и `quest_law` (12) отсутствуют в текущих CLI/сценариях; строки с ними в [test_ryzen.py:140](../../../scripts/tests/test_ryzen.py#L140), [143](../../../scripts/tests/test_ryzen.py#L143) — фиктивные аргументы теста оболочки. **Не уверен**, какой код исполняли эти заявки; автоматически приравнивать их к `calm/companion/crime_law` нельзя.

После всех адаптеров без рендера/настоящего аудиоустройства потенциально покрываются **92/94 сценария (97,9%)** и **157/183 запусков (85,8%)**. Это прогноз по текущему коду. Семь Б? и три Д? требуют проверки поведения greeting/fallback; на них приходится 5 запусков (3 stages и 2 crime_director). Два неизвестных исторических имени не включены. Если считать только формальные условия `tts` и разрешить ему тихий PCM, получится **93/94 сценария (98,9%) и 161/183 запуск (88,0%)**, но проверка прежнего пути микшера исчезнет.

Потоковый тихий PCM нужен 13 сценариям: 12 в З плюс `programs` в Д; они дали 39/183 запусков (21,3%). Обязательного OS-ввода среди 94 не найдено; `tts` нужен audio backend, а не само окно. Для текущей видеозаписи `broken` нужен рендер и окно.

## 3. Что нужно сделать в движке

Оценка по чтению кода, без реализации. День — рабочий день одного разработчика с целевыми проверками; интервалы частично перекрываются.

| Работа | Уже есть в runner | Что потребуется | Размер |
|---|---|---|---|
| AiBridge без Device | Конструктор моста не принимает Device ([aibridge.cpp:348](../../../OpenGothic/ai_bridge/aibridge.cpp#L348)); runner уже линкует библиотеку ([CMakeLists:280](../../../OpenGothic/CMakeLists.txt#L280)). | Выбрать headless до API/Device ([main.cpp:137](../../../OpenGothic/game/main.cpp#L137)), создать `Resources()`; отделить ChatOverlay/deal-state: `ensureChat` сейчас безусловен в tick ([aibridge.cpp:530](../../../OpenGothic/ai_bridge/aibridge.cpp#L530), [2389](../../../OpenGothic/ai_bridge/aibridge.cpp#L2389)). | 4–8 ч |
| Lifecycle, save/load, таймеры | GameSession может создать мир/загрузить сейв без аудиоустройства ([gamesession.cpp:71](../../../OpenGothic/common/game/gamesession.cpp#L71), [125](../../../OpenGothic/common/game/gamesession.cpp#L125)); `finishLoading` отделён от окна ([gothic.cpp:512](../../../OpenGothic/common/gothic.cpp#L512)). | Перенести обработчики из MainWindow ([69](../../../OpenGothic/common/mainwindow.cpp#L69), [1091](../../../OpenGothic/common/mainwindow.cpp#L1091), [1108](../../../OpenGothic/common/mainwindow.cpp#L1108), [1142](../../../OpenGothic/common/mainwindow.cpp#L1142)); дать CPU preview-заглушку сейва и session/world_loaded hooks. `Gothic::save/load` лишь испускают сигналы ([634](../../../OpenGothic/common/gothic.cpp#L634)). Обслужить deferred save/load и body watchdog на Timer ([aibridge.cpp:398](../../../OpenGothic/ai_bridge/aibridge.cpp#L398), [1159](../../../OpenGothic/ai_bridge/aibridge.cpp#L1159)). | 1–2 дня |
| DialogMenu без рисования | Есть машина состояния, выбор и output pipe ([dialogmenu.cpp:66](../../../OpenGothic/common/ui/dialogmenu.cpp#L66), [200](../../../OpenGothic/common/ui/dialogmenu.cpp#L200), [275](../../../OpenGothic/common/ui/dialogmenu.cpp#L275), [549](../../../OpenGothic/common/ui/dialogmenu.cpp#L549)); menu callbacks не требуют клавиш. | Выделить состояние из Widget/InventoryMenu. Просто пропустить paint нельзя: SoundDevice хранится по значению ([dialogmenu.h:129](../../../OpenGothic/common/ui/dialogmenu.h#L129)); print/aiPrintScr вызывают font вне paint ([250](../../../OpenGothic/common/ui/dialogmenu.cpp#L250), [337](../../../OpenGothic/common/ui/dialogmenu.cpp#L337)), а headless font запрещён ([resources.cpp:1041](../../../OpenGothic/common/resources.cpp#L1041)). | 1–2 дня; торговля расширяет объём |
| Тихий звук | Native WorldSound уже хранит длительность и сохраняет RNG выбора варианта ([worldsound.cpp:175](../../../OpenGothic/common/world/worldsound.cpp#L175), [206](../../../OpenGothic/common/world/worldsound.cpp#L206); [gamesession.cpp:316](../../../OpenGothic/common/game/gamesession.cpp#L316)). | Подключить native timing к UI-репликам; для say_audio сделать CPU drain PCM с длительностью, EOF, прерыванием, барьерами и lip-track. Иначе voiced/played не завершатся. Реальный аудиомикс проверять отдельно. | 4–8 ч native/UI + 0,5–1 день streaming/lips |
| Шаг в реальном времени | Общий CPU SimulationStep ([14](../../../OpenGothic/common/game/simulationstep.cpp#L14), [26](../../../OpenGothic/common/game/simulationstep.cpp#L26)), deadline/realtime loop ([bodyrunner.cpp:415](../../../OpenGothic/game/bodyrunner.cpp#L415), [460](../../../OpenGothic/game/bodyrunner.cpp#L460), [1416](../../../OpenGothic/game/bodyrunner.cpp#L1416)); CPU Camera. | Подключить к headless Gothic2Notr, добавить tick диалога, паузу, lifecycle, transport/timer dispatch. Сохранить PlayerControl и viewport; GPU-рендер не нужен. | 4–8 ч после lifecycle |
| Консоль ai | Обработчик ([aibridge.cpp:1987](../../../OpenGothic/ai_bridge/aibridge.cpp#L1987)), callback ([370](../../../OpenGothic/ai_bridge/aibridge.cpp#L370)), прямой вызов bench ([testbench.cpp:1411](../../../OpenGothic/common/testbench.cpp#L1411), [13570](../../../OpenGothic/common/testbench.cpp#L13570)), PTT с файлом ([aibridge.cpp:2049](../../../OpenGothic/ai_bridge/aibridge.cpp#L2049)). | Дать вход через bench/stdin в главном потоке; OS-клавиатура не нужна. | 2–4 ч |

Порядок работ: общий запуск и lifecycle → bridge с UI-state без виджетов → базовые e2e → DialogMenu и тихий PCM → оставшиеся группы. Грубый общий объём **5–8 рабочих дней**, с риском роста на торговом меню и сохранении точной семантики речи. Мир, VM, AI, физику, анимацию и realtime заново строить не требуется. Запись видео и проверку настоящего аудио стоит оставить отдельными прогонами.
