# План реализации GRU слоя управления телом NPC

Статус: **этапы 1–7 готовы; этап 8 — боевой gate пройден (gate v16, 15 октября 2026 года, Ryzen 9 3950X)**: известные сценарии, нагрузка из 4 NPC и свежий screening на 20 seed прошли все боевые пороги; сервисный бюджет на этом хосте не измерялся и остаётся отдельным отчётом. [Итог gate v16](../reports/npc-body/2026-10-15-gate-v16/README.md), [продолжение после остановки](../reports/npc-body/2026-10-15-stage8-resume/README.md), [прежняя остановка](npc-body-stage8-stop-report.md). Релизный gate на 100 seed не начат.

Добраны 37 физических демонстраций, включая поворот, отскок, wait300 и стену; GRU/MLP обучены на общем periodic dataset с 11 классами. Первый checkpoint прошёл 12/12 микро-задач, но 0/6 дуэлей из-за native perception loss и возврата к native AI. Последующее обучение на корпусе207 и known-проверки описаны ниже; итоговый допуск также не пройден. Новые запуски не назначены.

**Первый результат — один NPC с оружием ближнего боя, который выбирает следующий боевой примитив через GRU и сражается в воспроизводимой headless арене на `dev-ubuntu`.** Движок сохраняет движение, столкновения, урон, анимации и ограничения прерывания. Большая LLM и Strands задают намерение и режим; GRU получает числовую задачу и состояние боя.

Сначала фиксируем контракт и выделяем headless runner из настоящего движка. Затем создаём наблюдения, общий исполнитель и корректный датасет. Сбор траекторий, серии дуэлей, reset, BC/PPO и массовая оценка выполняются на сервере. Развёртывание в мире идёт после проверки управления, задержки и качества. Локальная навигация — следующая волна на том же интерфейсе.

## Объём первой версии

- Дуэль двух людей, одна выбранная группа оружия, фиксированные характеристики и явно заданная цель `fight`.
- Наблюдения с начальной частотой 20 Гц для выбранных участников; события боя дополнительно. Частоту уточняем по реальным окнам защиты.
- Действия: подход, поворот, боковой шаг, отскок назад, допустимые варианты удара, блок, ожидание и продолжение текущего примитива.
- Общие веса actor, отдельный hidden state каждого NPC; один слой GRU-256 и небольшие MLP. Размер рассчитываем после фиксации признаков, ориентир — 0,5–2M параметров.
- Headless runner на `dev-ubuntu` обязателен до сбора обучающего датасета. Он считает мир на CPU без окна, графического контекста и renderer VRAM; actor и обучение работают на CPU этого сервера или RTX 3090 по бюджету.
- Все настоящие модели, включая MLP для сравнения, работают только на `dev-ubuntu`. Там же находятся recorder и training/evaluation orchestration. На Mac — разработка, обычная игра и её gateway; только короткие проверки интеграции, без серий обучающих симуляций.

Полное управление суставами, новый физический движок, нейронный глобальный поиск пути, монстры, дальний бой и массовое обучение всего острова не входят в первую версию. Эти задачи не нужны для проверки основного слоя.

## Этапы и зависимости

Этапы 1–7 выполнены, боевой gate этапа 8 пройден (gate v16). Следующий — этап 9 по решению пользователя от 15 октября 2026 года: сначала скорость симуляции (9.1), затем отбор лучших прогонов и PPO. Этапы 10–11 описаны, но не назначены.

| Этап | Результат | Зависимость |
|---|---|---|
| 1, готов | Контракт действия, baseline и бюджет задержки | Начало |
| 2, готов | Headless runner настоящего движка на dev-ubuntu | 1 |
| 3, готов | Частые числовые наблюдения в общей симуляции | 1–2 |
| 4, готов | Боевой API и один владелец тела | 2–3 |
| 5, готов | Headless арена, reset и измерение скорости среды | 2–4 |
| 6, готов | Проверенный recorder и датасет на сервере | 4–5 |
| 7, реализация готова | GRU service, MLP baseline и BC; checkpoint quality gate не пройден | 6 и ресурсный бюджет |
| 8, боевой gate пройден | Gate v16: known, нагрузка 4 NPC и свежий screening; сервисы — отдельный отчёт | 7 |
| 9, следующий | Сильнее учителя: скорость симуляции, отбор лучших прогонов, PPO, новые действия и ситуации, сравнение MLP/GRU/Mamba | 8; начинается с 9.1 |
| 10, не начат | Интеграция с намерениями и ограниченное включение в мире | 8 и общий release gate; только после возобновления |
| 11, не начат | Локальная навигация, препятствия и несколько целей | После готовности первой версии и возобновления |

Численные бюджеты ниже — **начальные критерии проекта**, а не обещанные результаты. На этапе 1 проверяем их реалистичность и фиксируем до обучения. Если бюджет меняется, отчёт объясняет причину; критерии не подгоняются под выбранный checkpoint.

## Общие правила интерфейса

### Источник решения и исполнитель

`native`, `scripted` и `neural` выбирают следующий примитив в одной точке и используют одинаковый набор действий, mask и исполнитель. Программный `scripted` provider нужен для тестирования API; он не является нейросетью.

Сейчас родной `FightAlgo` выбирает инструкции, а `Npc::implAttack` может сначала повернуть NPC вместо удара, отложить начало или превратить ожидание в отскок. Перед обучением разделяем **намерение**, **нормализованный примитив**, **принятие** и **фактическое начало**. Преобразования оформляем в общем resolver, а не оставляем только внутри пути native AI. При записи фиксируем все эти стадии. Refactor сохраняет порядок native очереди/комбо, `nextFromQueue`/`consumeAction`, задержки и число RNG calls. Нормализованный примитив не проходит второе смысловое преобразование только в neural пути. [FightAlgo](../OpenGothic/common/game/fightalgo.cpp), [боевое исполнение](../OpenGothic/common/world/objects/npc.cpp).

Запрос частого примитива не идёт через нынешний `NpcController::action`: этот путь предназначен для длинных действий и может очищать очередь. GRU подменяет источник следующего боевого решения, сохраняя исполнение текущей анимации. [NpcController](../OpenGothic/ai_bridge/npccontroller.cpp).

### Идентификаторы и время

| Поле | Правило |
|---|---|
| `session_epoch`, `world_epoch` | Меняются при соответствующем reconnect/load/world change; сообщения прежней эпохи недействительны |
| `entity_id` | Уникальный runtime ID в мире; script symbol хранится отдельно. Два NPC одного вида не делят ID |
| `control_generation` | Меняется при смене владельца, reset и восстановлении после неопределённого состояния сервиса |
| `observation_seq` | Возрастает в рамках generation; повторный запрос не обновляет hidden state дважды |
| `goal_version` | Версия структурированной задачи; действие старой задачи не применяется |
| `t_game_ms`, `dt_game_ms` | Время и интервал игры; единицы и фаза снимка зафиксированы |
| `valid_for_ms` | Максимальный возраст наблюдения для команды, проверяется движком по сохранённому локальному времени его создания |
| `command_id`, `decision_id` | Связывают выбор, подтверждение и исполнение; дубликат не запускает действие повторно |

`tickCount` игры сохраняется и загружается, может идти назад и зависит от масштаба игрового времени. Он не является общим monotonic clock для сервера и Mac. TTL по игровому времени, watchdog по локальному `steady_clock` движка и monotonic измерения каждого сервиса задаются раздельно. Нельзя сравнивать абсолютные monotonic timestamps разных машин. [GameSession](../OpenGothic/common/game/gamesession.cpp).

В обычной игре и realtime evaluation движок проверяет актуальность в игровой шкале и по локальному возрасту снимка. Пауза не создаёт новые решения; при возобновлении запрашивается свежий снимок и инвалидируются ожидавшие команды. Load всегда начинает новое поколение управления.

Для headless training с самого начала вводится lockstep: runner выдаёт наблюдение и ждёт action, пока simulated time не продвигается. CPU/GPU ожидание не расходует окно защиты и не становится игровым штрафом; отдельный wall watchdog прерывает зависший job как infrastructure failure. Если нужно моделировать задержку реакции, она задаётся явно в игровых substeps и записывается в manifest. Нельзя ускорять игру увеличением `timeMul` или просто растягивать TTL. Realtime evaluation проверяет тот же actor с реальными deadlines; качество ускоренного обучения не заменяет её.

### Предлагаемые сообщения

Названия и поля уточняются на этапе 1; номер новой версии протокола выбирается при реализации с учётом остальных изменений проекта.

| Сообщение | Назначение |
|---|---|
| `body_watch` | Подписка на компактные наблюдения с частотой и лимитом акторов; сама по себе не захватывает тело |
| `body_observations` | Пакет наблюдений, masks, `can_decide`, текущий примитив и последний фактический результат |
| `body_control` | Явная смена владельца и lease; ответ содержит generation |
| `body_action` | Один примитив для конкретного актуального наблюдения и задачи |
| `body_action_result` | `accepted`, `rejected`, `started`, `completed` или `cancelled`; reason и effective primitive |
| `body_event` | Замах, попадание, завершение lock, потеря цели, смерть; ограниченная по частоте доставка |

`accepted` означает, что арбитр принял команду; это не синоним `started` или нанесённого урона. Перед `started` повторно проверяются возраст, goal/generation, owner и допустимость. Устаревшая ожидающая команда получает `cancelled` с причиной, а не запускается после lock. У каждого отказа есть причина: stale, expired, invalid mask, owner changed, target gone или несовместимое состояние. Очередь следующего решения ограничена одним элементом; нескончаемый backlog недопустим.

## Этап 1 Контракт и исходные измерения

Артефакты реализации: [боевой контракт](npc-body-combat-contract.md), [конфигурация melee-v1](../evals/npc-body/melee-v1.json), [сценарий](../scripts/bench/body-melee.bench), [инструкция запуска](../evals/npc-body/README.md) и [серверный launcher](../scripts/body-baseline-remote.sh). Trace включается только сочетанием bench и `OPENGOTHIC_BODY_TRACE=1`; stage 1 не добавляет внешнее управление NPC.

**Выполнено:** seeds 17/29/43, длительности 14 133 / 17 234 / 9 800 игровых мс, terminal без timeout; все шесть участников имеют native choice/consume, animation hooks и полные guard snapshots. p95 и максимум интервала снимков — 66 мс, дополнительные edges присутствуют. Отчёт `complete`, исходные trace/manifest сохранены на сервере и в ignored `data/`. Renderer lavapipe под Xvfb дал около 8% realtime при CPUQuota 200%; это исходная механика до headless refactor, а не throughput будущего runner или latency модели.

**Сделать:**

1. Выбрать одну melee дуэль в изолированном месте с фиксированными оружием, HP, защитой и навыком. Существующий `npc-fight.bench` в основном проверяет лучников и неподвижного героя; новый melee сценарий нужен отдельно.
2. Измерить окна замаха, удара, блока, восстановления и прерывания; определить, когда native AI действительно выбирает новый примитив.
3. Зафиксировать action vocabulary, masks и преобразования native намерений. `RUN_BACK` пока TODO; короткий `MoveBack` не считать полноценным бегством.
4. Выбрать единую фазу снимка. Сейчас bridge обрабатывается до игрового update; нельзя смешать состояние до действия с label после него без явного timestamp/decision boundary.
5. Зафиксировать способ получения reference trace штатной симуляции и baseline на сервере: исходы дуэлей, урон, длительность, интервалы решений и порядок фаз. Сохранить reference до refactor вместе с build/asset hashes: совпадения двух режимов после одинаковой ошибочной правки недостаточно. На этапе 2 он используется для сравнения. Обследовать путь команды и определить рабочую нагрузку — сначала один управляемый NPC, затем четыре активных.

**Готовность:** существует таблица полей и единиц, список примитивов с условиями запуска/завершения/прерывания, схема фаз тика и отчёт baseline. Для каждого поля известно, доступно ли оно NPC или является privileged telemetry только для анализа. Приняты budgets и сценарии оценки; в action space нет примитива без исполнителя.

**Проверка:** один короткий reference melee сценарий без модели и чтение фактических фаз в engine trace; автоматические серии на Mac не запускаются. Способ получения reference на `dev-ubuntu` фиксируется без изменения продовых сервисов. Результат — первый отчёт в `reports/npc-body/`, конфигурация сценария в Git.

## Этап 2 Headless runner на dev-ubuntu

**Выполнено:** Linux target `gothic-body-runner`, общий simulation step обычной игры и runner, CPU resources/world/audio adapters, JSONL `reset/advance/status/close`, lockstep и realtime. На `dev-ubuntu` собраны оба executable и завершена серия `stage2-v3`; [runtime и запуск](npc-body-headless-runner.md), [конфигурация parity](../evals/npc-body/headless-parity-v1.json), [результаты](../reports/npc-body/2026-10-13-headless/headless-report.md).

Full rendered/headless trace совпал для seeds 17/29/43 в lockstep, lockstep с задержкой клиента и realtime; совпали также два сравнения режимов runner. Сохранённый baseline до refactor прошёл строгий replay 17 записанных случайных вариантов block-клипов: AI, движение, урон и исходы вычислял движок. Варианты блока исходно делили `std::rand` со звуками, поэтому прямое сравнение исторического RNG и нового benchmark `isolated` закономерно расходится; диагностика сохранена. [Метод и границы проверки](npc-body-headless-runner.md#воспроизводимость-вариантов-анимации) не заменяют historical trace совпадением двух новых режимов.

В lockstep измерены 64,61 substeps/s и 2,14 игровых секунды на wall секунду, peak RSS 562,60 MiB. 100 resets: p95 910,14 мс, warmed RSS +0,43 MiB, FD/thread span 0. Sight/occlusion, sound lifecycle, native perceptions и IPC probes прошли. Graphics/window/audio device не создаются; ASR/TTS units остались active/running. Их latency не измерялась. Policy transitions/s пока неприменимы: боевой provider и recorder ещё не реализованы; измерены native choices/s. Готовность ограничена human 1H melee-v1 и отдельными sensory probes.

**Реализованный объём:** отдельный Linux executable `gothic-body-runner`, который использует настоящий engine simulation и игровые данные, но не создаёт `Tempest::Device`, окно, swapchain, renderer или звуковое устройство. Скрытое окно, минимизированная игра и `xvfb` не закрывают этот этап.

Runner использует общую симуляцию настоящего движка. До refactor графические ресурсы создавались при старте, `World` обязательно создавал view, а simulation/animation updates были связаны с `MainWindow::render`. CPU ресурсы и функции мира теперь отделены от GPU allocation и presentation. [Старт игры](../OpenGothic/game/main.cpp), [Resources](../OpenGothic/common/resources.cpp), [World](../OpenGothic/common/world/world.cpp), [MainWindow](../OpenGothic/common/mainwindow.cpp).

**Выполненные работы:**

1. Выделить общий simulation step для обычной игры и runner с прежним порядком script/world tick, animation update, root motion, animation tags, hit/block/damage events и восприятий. Один вызов `GameSession::tick` недостаточен: обновление анимаций отдельно, а animation tags участвуют в нанесении урона.
2. Разделить CPU загрузку скелетов, анимаций, meshes для collision/hit geometry и weapon attachments от текстур, shaders и GPU buffers. Выключение картинки не должно выключить позу, root motion или хитбоксы.
3. Добавить headless world/view/audio adapters. Убрать playback и визуальные эффекты, сохранив игровые события звука, perceptions, callbacks и необходимые длительности. Нельзя менять условия боя отсутствием аудиоустройства.
4. Задать arena focus и активных участников с той же семантикой AI LOD, что в reference арене. Отсутствие героя/камеры не должно переводить бойцов в `AiFar` и отключать восприятия.
5. Сделать минимальный env API: `reset(scenario, seed)`, `advance(substeps/until_decision)`, `status`, `close`, и место подключения будущего `step(actions, substeps)` на этапе 4. Мелкие simulation substeps отделены от policy period; первое значение и порядок фаз берутся из reference. До готовности body API работает native AI в scripted сценариях.
6. Реализовать lockstep и realtime режимы на одной симуляции. Lockstep не ждёт wall clock между substeps и останавливает продвижение мира на ожидание policy; realtime воспроизводит нормальную скорость и таймауты. В обоих режимах сохраняется одинаковая игровая механика.
7. Подготовить user-local Linux build/toolchain, пути к игровым assets и save/setup, собственные каталоги runtime и CPU/RAM/threads/disk budgets. Игровые данные пользователя не входят в Git. Зависимости и сервисы размещаются в `~/gothic-ai/`; системные пакеты и конфигурация Docker не меняются.

**Связь с обучением:** runner, recorder, collector и `body-service` работают на одной машине. Начать с localhost/Unix socket IPC с той же feature/action schema и resolver, без ChatGPT, ASR/TTS и Mac gateway в training loop. Сервис этих моделей продолжает работать независимо; shared host resources всё равно учитываются.

**Готовность:**

- Бинарник на `dev-ubuntu` запускается без DISPLAY/Wayland и графического backend, не создаёт graphics device и не занимает renderer VRAM. Нет зависимости от запущенной игры или процесса на Mac.
- Native reference и headless используют одинаковые assets, настройки, seeds и общий simulation step. Trace сравнивается также с записью до refactor: примитивы/consume, animation hit/block timings, HP/урон, root motion, collisions и terminal outcome. При фиксированном setup существенных расхождений нет; допуски чисел установлены до оценки. Reference получают выбранным на этапе 1 способом на сервере. Parity masks добавляется на этапе 4, когда общий resolver/API готов.
- Тест отдельно подтверждает, что CPU animation update и события урона продолжают работать; бой не превращается в вызовы AI без исполняемой механики.
- Участники арены сохраняют ожидаемые восприятия и AI LOD независимо от наличия камеры. Невидимость и audio perceptions проверяются по событиям, а не по картинке.
- Lockstep даёт одинаковый игровой trace при разной wall задержке ответа policy, если simulated delay одинаков. Realtime задержки фиксируются отдельно; операционный watchdog не выдаётся за terminal поражение.
- Reset не оставляет старых targets, queues, effects, leases и event/reward state; 100 коротких последовательных эпизодов не показывают необъяснимого роста RSS, файлов, threads или зависаний.
- Отчёт содержит CPU/RAM, actual substeps/s и transitions/s, reset latency и влияние на текущие сервисы. Ускорение сверх realtime измерено либо явно указано, что runner пока не обеспечивает его; конкретный множитель не обещается.

**Первый запуск — один runner и одна симуляция за раз.** Пул процессов/несколько арен не входят в этот этап и не запускаются автоматически; это отдельное последующее решение с учётом правил проекта и измеренного запаса сервера.

**Код:** новый CMake target/entry point, общий simulation driver и CPU/headless adapters в `OpenGothic`; scripts сборки/запуска на сервере. Проверка — одна серия `body_headless_parity` со scripted/native эпизодами на `dev-ubuntu`. Перед следующими этапами headless и обычный build должны собираться, а существующие игровое исполнение и save/load сохраняться.

## Этап 3 Частые наблюдения

**Выполнено:** общий CPU builder [BodyObservations](../OpenGothic/common/game/bodyobservations.h), пассивные `body_watch/body_observations` в runner и обычном bridge, типы/ограниченный cache Python транспорта, numeric journal, validator и серверная серия `stage3-v1`. [Схема и запуск](npc-body-observations.md), [конфигурация](../evals/npc-body/body-observe-v1.json), [измеренный отчёт](../reports/npc-body/2026-10-13-observations/body-observe.md), [проверки и ограничения](../reports/npc-body/2026-10-13-observations/validation-notes.md).

За 600 633 игровых мс realtime получены 52 812 снимков четырёх экземпляров: 48 008 периодических и 4 804 дополнительных. Период 20 Гц квантуется 30 Гц substeps: p95 66 игровых мс и 67,76 wall мс, максимум wall интервала 91,30 мс. Нет dropped rows/events, sequence gaps, пропущенных period buckets и невалидных чисел. Каждый NPC выбирал native действия и атаковал во всех десяти минутных интервалах; реальная потеря HP подтверждена trace. События доставлены в снимок того же substep.

Добавленная работа четырёх NPC: p95 0,273 wall мс при лимите 2 мс. Три пары одинаковых сценариев дали медианы 69,41 / 70,19 substeps/s off/on; снижение скорости не выявлено, отрицательная измеренная разница не означает ускорения от подписки. Native ordered melee trace seeds 17/29/43 совпал off/on. Реальный overflow имеет точное соответствие dropped count и sequence gaps; четыре privacy cases подтверждают фильтрацию скрытых признаков по независимой истине той же фазы. 64 целевых теста прошли.

**Реализованный объём:** отдельный compact numeric поток без inventory и длинного текста существующего `snapshot`. Начальная частота 20 Гц для выбранных участников, наблюдения сохраняются и при animation lock.

Первая схема содержит относительную позицию/скорость цели, расстояние и угол, HP, оружие и навык, native flags собственного действия и наблюдаемой атаки, interruptibility, предыдущий результат, native goal и `dt`. Дальность оружия и фазовые методы берутся из исполнителя. `availability_mask[12]` имеет информационную семантику; `can_decide/control_allowed/action_mask_valid=false` до lease и общего resolver этапа 4. `native_decision_eligible` отдельно отражает read-only gates. Полноценные LLM goals и execution IDs также относятся к этапу 4.

Добавлены 16 Landscape/Object лучей, лимиты четыре NPC, 256 строк очереди, 32 pending events на actor и 4096 live runtime IDs. Текущие сведения о невидимой цели равны `null`; сохраняются последнее известное положение и возраст. Debug oracle хранится отдельно. Явные fixture teleports и первая видимость после пропуска инвалидируют derivative velocity.

Периодические и событийные данные объединены одной последовательностью. Снимки имеют фазу `post_simulation_post_animation` после обновления всего мира и poses, перед benchmark mutations. Очередь ограничена, потери маркируются. Network memory будущей policy может обновляться при `can_decide=false`; внешние команды в этом этапе не исполняются.

**Готовность:**

- 10 минут пассивного наблюдения без захвата тела: корректные ID, последовательности, единицы, masks; нет NaN и неограниченных буферов.
- В normal realtime сценарии p95 интервала снимков не больше 100 мс при заданной частоте; пропуски и frame quantization отражены в отчёте.
- Событие замаха/попадания попадает в поток не позднее следующего доступного sample tick; предел измерен в игровых кадрах.
- Для четырёх активных NPC добавленная работа simulation thread — целевой p95 до 2 мс, падение headless substeps/s до 5%; влияние на realtime шаг измерено относительно baseline. Эти начальные budgets зафиксированы в контракте этапа 1, измеряются после готовности headless; клиентский frame time дополнительно проверяется при интеграции. Если лучи дороже, уменьшаем их бюджет и повторяем этот же сценарий.
- Подписка выключается без изменения поведения native AI; невидимые цели не дают актуальных privileged features actor.

**Код:** общий builder наблюдений для headless и `OpenGothic/ai_bridge/`; типы в [protocol.py](../server/src/gothic_ai/protocol.py), транспорт обычной игры в [game.py](../server/src/gothic_ai/game.py), local env API для runner. Проверка: schema/unit тесты и один серверный сценарий `body_observe`.

## Этап 4 Боевой API и арбитр тела

**Выполнен 13 октября 2026 года.** [API и runtime](npc-body-api.md), [отчёт](../reports/npc-body/2026-10-13-body-api/validation-notes.md), [37 независимых проверок](../reports/npc-body/2026-10-13-body-api/body-api.md). Общий арбитр расположен в `OpenGothic/common/game/bodycontroller`, headless и bridge используют один parser/resolver/executor. Native-off совпал с архивом этапа 3 на seeds 17/29/43. Первая версия управляет melee относительно текущей native цели; стратегические goals и выбор другой цели ещё запланированы.

**Сделать:** общий resolver/mask и source selector рядом с `FightAlgo`/`Npc::implAttack`; отдельный body controller в bridge. Сначала команды задаёт scripted provider.

Арбитр допускает одного владельца. Native AI, soft `npc_act`, управление в разговоре и новая policy не должны одновременно выбирать движение или чистить очереди друг друга. Владение разрешается по `body_control`, ограничениям сценария и цели. Сюжетный/native takeover завершает neural generation.

Mask описывает реально допустимые примитивы. Исполнитель повторно валидирует их на текущем состоянии. Команды во время неразрешённого lock не прерывают удар. В обычной игре и realtime evaluation при timeout или разрыве связи следующий выбор возвращается native AI на безопасной границе; текущая допустимая анимация заканчивается штатно. В lockstep wall watchdog завершает/помечает эпизод как infrastructure failure с финальным наблюдением при возможности; коллектор не продолжает его как обычную дуэль после сбоя policy.

**Готовность:**

- Scripted provider запускает каждый примитив первой версии; trace связывает выбор, принятие, начало и завершение.
- Native и scripted используют тот же resolver и executor. Seeded primitive/queue traces до и после refactor подтверждают порядок, consume и RNG calls; с recording и API, выключенными для управления, baseline сохраняется в пределах исходной вариативности.
- Проверены duplicate, out-of-order, stale goal/generation, просрочка, неизвестная цель, смерть, load, disconnect и pause/resume. Ни один такой запрос не запускает действие повторно и не создаёт второго владельца.
- После потери provider нет вечного freeze: в realtime watchdog возвращает native на следующей допустимой границе, максимум — установленный timeout плюс оставшийся непрерываемый primitive; в lockstep завершает аварийный эпизод. Значения и режим видны в trace. Команда, принятая до lock, но утратившая актуальность к запуску, не получает `started`.
- Подключение body mode не обходит weapon/animation/collision checks и не заменяет `npc_act` частыми длинными командами.

**Проверка:** pure protocol/arbiter тесты с fake; один `body_api` e2e на серверном runner со scripted последовательностью и fault injection. Resolver и masks общие для env API и bridge. Изменения существующих [bodies.py](../server/src/gothic_ai/bodies.py), [npccontroller.cpp](../OpenGothic/ai_bridge/npccontroller.cpp) и [npcobserver.cpp](../OpenGothic/ai_bridge/npcobserver.cpp) проверяются на конфликт владельцев.

## Этап 5 Headless арена и runner эпизодов

**Выполнен 13 октября 2026 года.** [Арена и API эпизода](npc-body-arena.md), [результаты и ограничения](../reports/npc-body/2026-10-13-arena/validation-notes.md), [независимый отчёт](../reports/npc-body/2026-10-13-arena/body-arena.md). Полная серия `stage5-v2`: 100 восстановлений после настоящего урона/движения/active wait, 130 arena snapshots/эпизодов, микроэпизоды, 12 paired и 12 throughput эпизодов. Измерено 2,471 реальных policy transitions/s с reset и local provider; p95 reset 1,168 с. Бюджет сбора 32768 таких решений за 6 часов пройден по simulator-only прогнозу; model inference и optimizer ещё не измерены. `control_lost` завершает неуспешный candidate эпизод, исключается из actor updates и не засчитывается как победа.

**Сделать:** episode orchestration поверх headless runner на `dev-ubuntu`, reset из контрольного save/setup с повторной установкой участников, характеристик, позиции, цели и seed. Добавить серии коротких задач: дистанция, блок scripted атаки, собственный удар и native дуэль. На Mac эпизоды не собираются.

Существующий стенд с fixed шагом 30 fps и `seed` служит источником сценариев и reference, но сам по себе остаётся графической игрой. Использовать совместимые команды/общие сценарные данные и новый headless API; проверить фактический throughput вместе с local provider и без видео. [TestBench](../OpenGothic/common/testbench.cpp), [bench tools](../scripts/bench/README.md).

`terminated` означает смерть/выведение из строя или достигнутую terminal цель; `truncated` — лимит времени либо `control_lost` при потере цели scripted участником. Load, crash, disconnect и неудачный setup имеют отдельный infrastructure status. Timeout и потеря управления не маркируются победой.

**Готовность:**

- 100 последовательных коротких resets в одном runner возвращают контрольные HP, экипировку, позиции, цели, очередь и начальное состояние управления; нет следов прошлой дуэли.
- Один seed/setup повторяется с проверкой initial state; оставшаяся недетерминированность описана. Не требуется обещать битовую идентичность всей игры.
- Terminal state снимается до reset; generation меняется, recurrent state очищается.
- Есть paired evaluation: одинаковые setup/seed для native и кандидата, со сменой сторон/позиций.
- Отчёт содержит episodes/s, реальных policy transitions/s, reset latency и прогноз `число шагов / фактический throughput` для обучения.
- Решено, достаточно ли фактической скорости headless runner для небольшого PPO запуска. Если нет, оптимизация серверного runner становится задачей до большого обучения; перенос серий на Mac не является обходом этого gate.

**Код:** общий melee scenario/setup, headless episode runner и адаптер reference для [testbench.cpp](../OpenGothic/common/testbench.cpp); переиспользовать [scenarios.py](../server/src/gothic_ai/scenarios.py) и [bench_report.py](../scripts/bench_report.py), где они подходят без GUI. Проверка — одна серверная серия `body_arena`, pytest и симуляция последовательно.

## Этап 6 Траектории и датасет

**Выполнен 14 октября 2026 года.** [Контракт recorder и loader](npc-body-trajectories.md), [результаты и ограничения](../reports/npc-body/2026-10-14-trajectories/validation-notes.md), [независимый отчёт](../reports/npc-body/2026-10-14-trajectories/body-trajectories.md). Полная серия `stage6-v1`: 23 эпизода, 16 421 наблюдение, 1 236 решений/переходов и 681 пригодная BC label. Сырые causal joins, masks, lifecycle, групповые train/eval/test/OOD splits, train normalization и ordered loader с reset/burn-in проверены; OFF/ON и исторический native baseline совпали. Train содержит 455 labels. Coverage gate первого BC **ещё не пройден**: `turn_to_target` 14/20, `jump_back` 0/20, `wait_300` 0/20; отсутствует сценарий стены. Модели и optimizer на этом этапе не запускались.

**Сделать:** отдельный body recorder на `dev-ubuntu`, подписанный на headless observations/decision/execution events. Семантический trace разговоров не использовать как основной частый журнал. Писать компактные файлы с последующей упаковкой для обучения; JSONL подходит для первых диагностических эпизодов.

| Уровень записи | Содержимое |
|---|---|
| Manifest | Schema, build/commit, hashes assets/save/setup, scenario, seeds, action/feature versions, teacher/model, simulation mode/dt/delay profile, условия и split group |
| Наблюдение | Epoch/generation/entity/seq, фаза снимка, `t`, `dt`, признаки, mask, `can_decide`, goal/style |
| Решение | Источник, decision/command ID, native intent при наличии, normalized chosen action, принятая команда |
| Исполнение | `started/completed/rejected/cancelled`, effective primitive, причина, игровые timestamps |
| Переход | Следующее наблюдение, события урона/цели, длительность, terminal/truncation/infrastructure flags |

Native teacher логируется в общей точке выбора. Текущая анимация не считается новым действием на каждом кадре. Lock ticks сохраняются для памяти, но имеют `decision_valid=false` и не заполняют весь BC loss классом `continue`. Если выбранный intent был преобразован resolver, expert label относится к нормализованному действию; исходный intent остаётся для диагностики.

Начать с 10–20 полных дуэлей и защитных микроэпизодов. Далее объём определяет покрытие: по каждому действию и существенному условию есть успешные и неуспешные примеры, включая стену, потерю цели и плохую дистанцию. Целевой минимальный набор для первого диагностического BC — не менее 20 настоящих decision labels каждого включённого класса; это проверка покрытия, а не гарантия достаточного обучения.

Splits — целые группы эпизодов, например 70/15/15. Обе стороны одной дуэли и связанные повторы одного setup остаются в одном split. Отдельный OOD набор — новые начальные положения/геометрия/противник. Normalization и class weights вычисляются только по train; test не участвует в выборе checkpoint.

**Готовность:** validator проходит все файлы; нет silent gaps, дублирования решений, смешения эпизодов, labels вне recorded mask или события будущего в признаках. Число решений, подтверждений и завершений сверяется, незавершённые помечены. Есть coverage report, manifest splits, список недостаточно представленных состояний и загрузчик ordered sequences с reset/burn-in.

Replay здесь проверяет журнал и связи, а не обещает точно воспроизвести мир без симуляции. Запись не блокирует game thread и не меняет выбор native AI.

**Хранение:** configs, manifests и сводки — в Git; сырые trajectories и checkpoints — `~/gothic-ai/body/data/` на сервере. На Mac возвращаются небольшие manifests/отчёты и при необходимости выбранные записи для анализа, без зеркала всего датасета. В Git не коммитить веса, игровые assets/saves и большие сырые журналы.

## Этап 7 Сервис и behavioral cloning

Реализация: [workflow](npc-body-bc.md), [body-service](../body-service/README.md), [remote launcher](../scripts/body-bc-remote.sh). Результаты: [validation notes](../reports/npc-body/2026-10-14-bc/validation-notes.md), [независимый audit](../reports/npc-body/2026-10-14-bc/body-bc.json). `implementation_ready=true`, `checkpoint_ready=false`: численные и runtime инварианты пройдены; дуэли требуют разбора. Сохранены шесть training runs и eval-selected веса GRU/MLP на сервере.

**Сделать:** отдельный пакет `body-service/` с `serve`, сборкой dataset, BC training и evaluation; отдельный `uv` environment в `~/gothic-ai/body/`. Сервис не загружает языковую модель.

Actor: self/goal/geometry features → MLP → GRU-256 → masked categorical primitive head. Для первой дуэли target фиксирован. При нескольких целях сохранять per-entity embeddings и использовать scorer по каждой сущности; одного pooling для выбора ID недостаточно. Critic понадобится на этапе PPO.

Сравнение: GRU и MLP со стеком 4–8 наблюдений, одинаковые features/actions/BC dataset. Первые sequence chunks — 32–64 наблюдения с prefix/burn-in; terminal/reset очищает память. Supervised loss применяется к expert decision opportunities, а locked наблюдения участвуют в обновлении памяти.

Сервис хранит состояние по epoch/entity/generation, обрабатывает наблюдение один раз и кеширует ответ для retry. На первого NPC — один последовательный запрос в работе. Gateway имеет bounded latest-observation mailbox, сообщает gap/`dt`; после timeout с неизвестным исходом начинает новую generation. Старый ответ не меняет новое состояние. Checkpoint фиксирован на весь эпизод.

Recorder дополнительно отмечает, какие наблюдения действительно обработала модель. BC и runtime используют одну sampling/coalescing семантику: `gap/dt` не восстанавливает потерянную историю. Для первой версии unexpected gap вызывает явный reset поколения/fallback; healthy нагрузка должна обрабатываться без таких пропусков. Позже устойчивость к drops обучается на таком же режиме пропусков с отдельной оценкой.

**Готовность реализации:** step и sequence forward согласуются, resets работают, недопустимые logits замаскированы, normalizer фиксирован и версия schema проверяется. Маленький overfit на выбранных траекториях доказывает работоспособность pipeline. Все model forward проверки выполняются на `dev-ubuntu`.

**Готовность первого BC checkpoint:** есть held-out per-action precision/recall, confusion matrix, whole-episode результаты в арене и сравнение с native/MLP. Замкнутый цикл проходит все задачи без softlock. Для допуска к дальнейшему обучению первичная task success не ниже native более чем на 15 процентных пунктов в диагностическом наборе; это предварительный gate, без заявления о статистической неуступчивости. При провале сначала проверить labels/features, затем расширить demonstrations.

**Ресурсный gate:** свежий `nvidia-smi`, measured CPU/GPU runtime и достаточный запас под training batch/optimizer/activations. Выбрать CPU или GPU **сервера** по замеру. Сервисы ASR/TTS/Strands продолжают выполнять свои задачи; Docker и продовые контейнеры не изменяются. Если обучение не помещается, уменьшить batch/длину, использовать CPU сервера для малого эксперимента либо отложить GPU training до доступного бюджета; дефицит ресурсов фиксируется до запуска.

## Этап 8 Управление в headless арене и разбор ошибок

**Итог (15 октября 2026 года): gate v16 — PASS** на Ryzen 9 3950X; свежий screening GRU 20/20 во всех четырёх задачах (native: блок 0/20, дуэль 9/20); нагрузка 4 NPC — neural control ≥ 0,989; p95 модели ≤ 1,3 мс. [Отчёт](../reports/npc-body/2026-10-15-gate-v16/README.md). Ниже — история до этого допуска.

**Статус: STOPPED_AT_PREFLIGHT.** Единственная дополнительная SafePeer калибровка закрыта: шесть эпизодов с сохранением HP и без infrastructure failure; approved action2/5/11 =0/2/0 при требовании2/2/2. Поэтому сбор72, child279, новое обучение и новый боевой gate не запускались. Fresh screening не запускался. [Итог остановки](npc-body-stage8-stop-report.md). В сохранённом child `stage8-defence-bc-v2` подтверждены 72/72 настоящих завершённых блока (9): 36 train и по 12 eval/test/OOD. Объединённый датасет — 207 эпизодов (148 train, 20 eval, 19 test, 20 OOD); train содержит 1104 метки, включая 36 новых блоков, eval — 169 меток и пять классов из 11. CPU verification и tiny overfit GRU/MLP прошли; overfit accuracy — 1,0.

Все шесть CUDA jobs завершены по фиксированному профилю 120 эпох / patience25; GPU unit — 23 мин 7,662 с. Monitor сохранил 681 sample: peak наших процессов 472 MiB, минимум свободной VRAM 4646 MiB; четыре сервиса сохранили active/running и PID. Выбраны GRU seed 11 / epoch 25 и MLP seed 11 / epoch 14, веса зафиксированы 2026-10-14 11:15:23.540769 UTC. Точность GRU на сохранённых метках: eval 164/169, test 74/92, OOD 117/156; block recall 12/12, 17/18, 15/18 без false positives. Warm CPU p95 — GRU 0,132028 мс, MLP 0,117020 мс. Независимый [числовой аудит](../reports/npc-body/2026-10-14-combat/defence-v2/README.md) прошёл 3867 файловых привязок.

Gate-v1 не пройден; gate-v2 остановился на infrastructure `rng_state`. Gate-v3 собрал зарегистрированную known matrix: 3 shadow, 24 ordinary, 6 reaction. [Known audit R2](../reports/npc-body/2026-10-14-combat/gate-v3/README.md) — PASS: оба настоящих блока GRU сохранили HP300, neural control 1 и fallback 0; физический старт 66 игровых мс при окне 166 мс. Первый reader failure сохранён; external correction меняет только predicate `shadow` для native comparator с пустыми controls. Числовой аудит, выбранные веса и normalizer прежние; общего боевого PASS нет.

Compact/startup/dispatch hooks подключены, восемь CPU runtimes прогреваются до reset. Последующий [gate-v4](../reports/npc-body/2026-10-14-combat/gate-v4/README.md) завершил known 3/24/6, но не прошёл порог управления в одном distance случае: физический успех, 2/3 принятых решений, strict stale rejection и один fresh retry, без fallback. В старом запуске нет tick BEGIN/native READ, поэтому будущий observation EMIT не устанавливает начало тика. Отказ API не получает исключения или зачёта.

[Motor-v14](../reports/npc-body/2026-10-14-combat/motor-v14/README.md) (`ac2dfe…5263`) прошёл full native parity 17/29/43, API37, realtime4, visibility8 и compact10. Его queue probe с нулём реальных priority admissions сохранён как FAIL. [Queue15](../reports/npc-body/2026-10-14-combat/motor-v15-queue/README.md) на том же бинарнике подтвердила 13 heartbeat admissions, один post-tick запрос чтения и два accepted/started wait11 без отказов; шесть tests прошли. Это не 13 приоритетных боевых действий. Доставка использует paced heartbeat, bounded fresh retries и явный compact transport с `discard_hidden`; rejected commands не получают зачёт.

Gate-v5 declaration прошла за 17,487 с с привязками Motor14/queue15, failed gate-v1/v2/v4 и known-v3. Исходные focused tests — 58 PASS и два fixture failure; внешняя правка только fixtures прошла 2/2 при неизменном producer. Known 3/24/6 завершена, но [аудит gate-v5](../reports/npc-body/2026-10-14-combat/gate-v5/README.md) не прошёл latency: второй memory-only GRU шаг 18,962995 мс при пороге 5 мс. Первый управляющий шаг 1,243341 мс; оба блока настоящие, HP300, control 1 и fallback 0. CPU/thread samples по шагам нет, причина всплеска не доказана.

[Gate-v6 known audit](../reports/npc-body/2026-10-14-combat/gate-v6/README.md) — PASS за 42,099 с: 3/24/6 закрыты, issues пусты. Frozen CPU profile tests48 и final evaluation tests63 прошли. Private PyTorch intra-op1 подтверждён для всех 2205 real forwards (802/1387/16); interop12, веса, normalizer и quality thresholds прежние. GRU максимальный case model p95 — 1,440461 мс, observation→arbitration9,690933 мс, collector→send5,542793 мс; healthy neural control минимум1. Оба настоящих reaction блока: start66 игровых мс при окне166, HP300, damage0, fallback0.

Последующий [load gate-v6](../reports/npc-body/2026-10-14-combat/gate-v6/load-failure.md) — FAIL: 10/12 cohorts (native4/GRU4/MLP2). После receive→process gap169,008339 мс у первого актёра и максимума171,622080 мс следующий stream MLP дал observation_too_old всем4; body_release завершил арену на367 мс с unexpected_control_loss:released. HP всех четырёх3000, damage0, visibility сохранена. Gemma защищённый p95 вырос с108,032685 до147,864559/149,453219 мс при native/GRU (+36,87%/+38,34%), выше бюджета10%. GRU control0,99515/1/1/0,98710 без fallback не отменяет эти отказы. Отдельные [GC профили](../reports/npc-body/2026-10-14-combat/collector-gc/README.md) измерили реальные паузы, но причина старых169 мс не доказана.

[Gate-v7](../reports/npc-body/2026-10-14-combat/gate-v7/README.md) дал startup infrastructure failure: shadow0, preexisting frozen ownership375 private UV Python3.12.13. Оно существует до imports и после8warm, mutator calls нет. Exact version/path/executable SHA bootstrap opt-in прошёл actual375→0/freeze8891/close0 и pure tests28, сохранив GC enabled/thresholds700/10/10; неизвестное nonzero ownership не снимается. Gate-v8 заморожен: preregistration PASS50,177 с, targeted runtime76/final70 PASS.

[Known audit gate-v8](../reports/npc-body/2026-10-14-combat/gate-v8/README.md) — PASS за 62,755 с: 3/24/6 закрыты, issues пусты, оба GRU reaction блока HP300/damage0/block1/fallback0. Максимальный case model p95 GRU2,209114 мс; repaired population control минимум1. Neutral block diagnostic8/9 сохранён отдельно. Все2518 forward CPU intra-op1; dynamic GC enabled, bootstrap375→0/static freeze183578/183641/close0 проверены.

[Load gate-v8](../reports/npc-body/2026-10-14-combat/gate-v8/load-failure.md) — FAIL: завершены5/12 cohorts (native0–3 и GRU0), partial GRU1 остановлен после current masked отказа action8 при движущейся цели. Source mask была true, текущая физическая false; удар не принят/не начат, owner сохранён. Native20/20 protected p95: ASR −0,97253% PASS, TTS +10,02641%, decider +24,24811%, Gemma +51,08487% FAIL при бюджете10%. GRU5/20 не оценивается.

[Motor16](../reports/npc-body/2026-10-14-combat/motor-v16/README.md) на бинарнике a1695d…bc0c2 прошёл full native parity 17/29/43 с нулём различий, realtime4, visibility8, compact10 и API37; full-stage4 issues пусты. [Queue17](../reports/npc-body/2026-10-14-combat/motor-v17-queue/README.md) завершилась FAIL: `body_control_status` запросы8/81/153 получили ответы до уже просроченного следующего тика при запущенном и не приостановленном realtime. Для запроса8 reply остался на step17/game16327, а Tick18 со step18/game16361 начался только после него; точные native timestamps сохранены в отчёте. Gate9 targeted runtime101/final78/combat-reader82 tests прошли, quality cases/model forwards — 0. [Motor18](../reports/npc-body/2026-10-14-combat/motor-v18/README.md) и [Queue19](../reports/npc-body/2026-10-14-combat/motor-v19-queue/README.md) на runner `681a1704364e07836f839c3e4382b16ae43b28ae3bf75fb46f7884e2b10a1359` прошли: full native parity 17/29/43 без различий, API37/realtime4/visibility8/compact10 и четыре неизменённые строгие проверки очереди. Queue19 сохранила 18 WRITE admissions (все heartbeat), 10 fresh query chains и три wait11 accepted/actual started без отказов; шесть reader tests прошли. Это не 18 боевых команд; failed Queue17 неизменна. Gate10 known collection завершилась FAIL до load/reaction/fresh. Guarded masked refresh и opt-in actual native-deadline polling сохраняют physical masks и freshness. Веса/normalizer и thresholds прежние, fresh screening не запускался, этап8 не завершён.

[Gate10](../reports/npc-body/2026-10-14-combat/gate-v10/README.md) завершил shadow3 и ordinary22; partial `realtime-native_duel-223-1-gru` сохранил 145 actual forwards и один age fallback из 146 model inputs. Control — 12/13 = 0,9230769: один stale отказ, затем oldest observation337/game23561 с age166 вызвал hidden reset/release6046, `native_restored` на game23761 и `unexpected_control_loss:released`. Process wall p95/max — 2,591128/9,772362 мс. RPC observations187,74668 мс/action78,658525 мс/events81,912808 мс совпали с native steps70–103 мс при обычных9–10; packet p95/max0,420/1,96 мс и соседний GC0,349 мс не объясняют задержки. Masked budget не исчерпан, причина не доказана. Отдельная native CPUv2 диагностика сохранила 606 response attestations: CPU −23,31%, но тики медленнее; этот diagnostic run не доказывает причину Gate10 FAIL. Веса, normalizer, masks и quality thresholds прежние. Ни load, ни reaction, ни fresh в Gate10 не начались; на тот момент этап8 был IN_PROGRESS.

[Motor20](../reports/npc-body/2026-10-14-combat/motor-v20/README.md) и [Queue21](../reports/npc-body/2026-10-14-combat/motor-v21-queue/README.md) на serial-native runner `a83efa28236ff9917b7e7d60a4a508de6feedd31d8d9ff4bc69adeb20ab777b7` завершены: full native parity 17/29/43 без различий, API37/realtime4/visibility8/compact10 и четыре строгие проверки очереди PASS. Queue21 ready admissions — 20, все `body_heartbeat`; fresh query chains — 7, четыре wait11 accepted и actual started, rejected — 0; шесть reader tests прошли. Counts Queue19 не переносятся. Header `worker_execution`: `serial=true`, `mode=caller_serial`, `worker_pool_created=false`, `native_task_parallelism_cap=1`; manifest `native_serial_workers=true`. Во всех 2107 реальных response packets Queue21 профиль совпал; это attestations, а не число proof cases. Это native worker pool, а не весь процесс с одним thread. Gate11 заморожен: preregistration PASS за 26,768 с, targeted runtime101/final94 PASS. Независимый known audit закрыл 3/24/6: PASS за 39,393 с, issues пусты, reader508cf… проверил actual serial worker profile. Load фактически начался в 17:47 UTC после завершения чужого physics-disabled regression linux-v8 и освобождения lock, unit `gothic-body-bc-stage8-final-gate-v11-load`. Baseline записывается до любого engine lease/tick: 80 с baseline, затем 12 cohorts (native4/GRU4/MLP4), одинаковые protected20 before/during, бюджет10% неизменён. [Закрытый load Gate11](../reports/npc-body/2026-10-14-combat/gate-v11/README.md) — FAIL, exit2: unit wall3:52,841/CPU1:38,928/memory5,6G. Native0–3 завершились с `time_limit`; GRU0/2/3 — `control_lost`, GRU1 — `time_limit`; partial MLP0 после explicit release завершился infrastructure `unexpected_control_loss:released`. [Causal report](../reports/npc-body/2026-10-14-combat/gate-v11/load-failure.md) связал GRU0/2/3 target_visibility_lost с completed RightStrafe4/Idle/no lock; control19/24,32/35,75/78, healthy GRU1 —153/158. Fallback0 не даёт handoff credit. Partial MLP target_invalid/all masks false при той же lease, receive→send4,848299 мс, snapshot→send6,709377 мс/RTT19,703637; explicit release завершил infrastructure. Native20/20 ASR/TTS −1,24908%/+8,36735% PASS, Decider/Gemma +10,59663%/+31,21631% FAIL; все GRU16/15/20/20 populations invalid. Actual4PID before/after совпали, реальный restart не заявляется; частичный прогон не получает gate credit. Прежняя попытка дала только lock refusal без launcher-load/service baseline/output/requests; это историческое ожидание, а не quality failure. Fresh не запускался. Боевого допуска нет; текущий статус этапа8 — STOPPED_AT_PREFLIGHT, все прежние отказы сохранены.

Опциональный realtime transport header `next_tick_wall_monotonic_ns` хранит фактический Linux CLOCK_MONOTONIC deadline, будущий либо уже прошедший, только в active/unpaused realtime. `native_deadline_polling` запрашивает slices≤5 мс, сохраняет actual overshoot и полный periodic/event stream с latency counts. Схема184 признаков модели прежняя.

Для сервисов baseline/load cadence приведена к одинаковым 4×5 measured запросам +2 warmup, nominal 20 с / actual 20–25 с; count 20 и бюджет 10% сохранены. Модели и симуляции выполняются только на dev-ubuntu.

**Сделать:** сначала shadow inference при native исполнении на серверном runner — проверка схемы, времени и памяти. Затем включить GRU как владельца в headless арене, сохраняя fault injection и native fallback. Проверки скорости реакции выполняются в realtime режиме, серии поведения — также в lockstep с явным simulated delay. Shadow не показывает качество поведения после собственных ошибок learner.

Начальная нагрузка — один NPC; после прохождения — четыре активных. Считать `neural_control_fraction` только среди decision opportunities. Успех, достигнутый в основном native fallback, не приписывается GRU.

**Готовность:**

- В healthy run не менее 95% eligible decisions действительно управляются policy; invariant violations — ноль в проверенных сценариях.
- Собственный model step: целевой warm p95 до 5 мс. В realtime режиме local collector от готового снимка до отправки команды runner: целевой p95 до 20 мс, включая local IPC. Сетевой Mac gateway проверяется отдельно на этапе 10.
- От создания наблюдения в игре до arbitration result: целевой p95 до 100 мс в normal realtime стенде. Начало/завершение анимации измеряется отдельно: animation lock нельзя скрыть в latency модели.
- На defense микроэпизодах измеряется наблюдаемый замах → фактическое начало допустимого блока/отскока, включая sampling, RPC и игровой тик. Там, где защита разрешена, этот путь должен помещаться в измеренное на этапе 1 боевое окно. Быстрая arbitration с опоздавшим блоком gate не проходит.
- При дубликатах, задержке, пропуске и reconnect нет повторного hidden update, второго владельца или применения команды старой generation.
- Нет зависания, бесконечного отхода, удара через стену и циклического переключения примитивов; failures записаны отдельными эпизодами.
- При совместной фиксированной нагрузке деградация p95 выбранных метрик ASR/TTS/Strands не больше начального бюджета 10%; измеряется относительно baseline этого же запуска, не исторических чисел.

**Исправление поведения:** добавить targeted demonstrations на посещённых learner состояниях. DAgger использовать только при наличии teacher oracle без побочных эффектов. `FightAlgo` stateful и использует RNG: простой второй вызов teacher может изменить настоящий бой. Нужен clone состояния/RNG или независимый наблюдающий scripted oracle. Gate oracle — запрос разметки не изменяет game trace. Если его нет, DAgger откладывается, PPO остаётся следующим возможным этапом.

## Этап 9 Сильнее учителя: обучение с подкреплением

Подражание поднимает сеть максимум до уровня родного ИИ вместе с его ошибками: например, он достаёт оружие, когда его уже бьют. Чтобы NPC стал умнее, сеть дообучается на награде в той же headless арене настоящего движка. Что выучено в арене, работает в игре без переноса между симулятором и движком. Отправная точка — BC-веса этапа 8 (gate v16). Симуляции идут на Ryzen 9 3950X (16 ядер, без соседей; dev-ubuntu делит CPU с CI и сервисами). Обновление весов маленькой сети — на CPU Ryzen или на 3090 dev-ubuntu, по замеру. Каждый подэтап заканчивается отчётом и решением пользователя о следующем.

### 9.1 Скорость симуляции

**Выполнено 15 октября 2026 года:** около 90 решений/с при 16 параллельных runner на Ryzen; тик одного runner 2,0 мс вместо 21 с флагами режима обучения `OPENGOTHIC_FOOTIK_WATCHED_ONLY` и `OPENGOTHIC_ANIM_SKIP_FAR`, native parity арены полная. Узкие места теперь — reset (~1,9 с) и коллектор. [Отчёт](../reports/npc-body/2026-10-15-sim-throughput/README.md).

RL нужны сотни тысяч или миллионы решений. На этапе 5 арена давала около 2,5 решения/с вместе с reset, тик runner стоит около 20 мс: почти всё время уходит на остальной мир Хориниса. Без ускорения эксперимент на один навык занимает недели.

**Сделать:** замерить на Ryzen lockstep-throughput одного runner (решения/с, эпизоды/ч, reset, память) и разложить тик: мир, боевые NPC, наблюдения, диагностика, IPC. Затем по очереди и с замером после каждого шага:

- N параллельных lockstep runner по числу ядер и памяти, каждый со своим миром и seed;
- отключаемая в режиме обучения тяжёлая диагностика (visibility trace, body trace, журналы IPC); в evaluation она остаётся;
- более дешёвый мир арены (заморозка или удаление дальних NPC и объектов, меньшая зона) только при неизменной native parity арены;
- быстрый reset без полной перезагрузки save, если он воспроизводит то же начальное состояние.

**Готовность:** суммарно не меньше 50 решений/с на Ryzen при сохранённой native parity и прежних результатах проверок этапов 4–5 на ускоренном runner; отчёт с прогнозом времени для 1 и 10 млн решений. Если цель недостижима, отчёт называет, что ограничивает (CPU тика, память на runner, reset), и решение принимает пользователь.

### 9.2 Награда и отбор лучших прогонов

**Сделать:** счёт эпизода: исход задачи, урон нанесённый и полученный, время; штрафы за застревание и выход за границу. Собрать K эпизодов текущей политики с контролируемым разбросом действий (температура), оставить лучшие по счёту, добавить к BC-датасету и дообучить (self-imitation, filtered BC). Повторить несколько раундов.

**Готовность:** улучшение счёта относительно BC на свежих seed без потери успеха задач; проверки эксплойтов (бесконечный отход, спам блока, удар через стену, выход за арену) пройдены; бои просмотрены на видео стенда. Этот шаг проверяет награду и конвейер до более капризного PPO.

### 9.3 Recurrent PPO

**Сделать:** actor/critic, masked distribution, synchronous rollout collector и recurrent updater поверх параллельных runner из 9.1. Старт с весов 9.2 или BC; KL-штраф к BC-политике удерживает поведение «готическим». Противники: сначала родной ИИ, затем пул собственных прошлых версий (self-play) и смешанные противники, чтобы сеть не училась только на слабостях родного ИИ. Оценка всегда против отложенных противников и на свежих seed.

**Правила сбора** (из исходного плана): один frozen checkpoint собирает batch в lockstep, обновление происходит после сбора; новые веса включаются между эпизодами. Перед новым on-policy batch завершить текущие эпизоды старой версии и сбросить управление/hidden state; лишние эпизоды входят в прежний batch. Не смешивать незавершённый rollout старых весов с новым behavior policy. Начать с коротких задач дистанции и защиты, затем native дуэли. Увеличивать rollout/сложность после стабильного малого эксперимента, без автоматического запуска нескольких игр.

В rollout сохранять sampled action, его log-probability, value, mask, behavior-policy version, начальный hidden state фрагмента, rewards и terminal semantics. **Фактическое native fallback действие не подставляется вместо sampled action в PPO ratio.** Rejection/delay либо моделируется как часть среды с исходным action, либо affected fragment исключается из actor update по явно заданному правилу.

GAE не пересекает resets. Смерть/terminal success не bootstrap; при time-limit truncation value берётся из финального наблюдения до reset. Смену владельца, crash и потерю связи не выдавать за обычное terminal поражение; для первой версии такие rollout fragments отдельно маркировать и не использовать в actor update. Переменный `dt` учитывать в rewards/discounting либо собирать на фиксированной сетке с отдельным признаком lock, не смешивая два режима молча.

Награды: исход задачи, нормализованный HP урон, полезная защита при надёжном событии, timeout/stuck cost. Не награждать попытку атаки или каждый прожитый тик так, чтобы выгодно было бесконечно стоять/убегать. Отдельные проверки — block spam, infinite kite, wall attack, animation exploits, нарушение цели и границы арены.

**Готовность механики:** no-update ratio около 1, masks совпадают при sampling и recomputation, sequence/reset/value targets проходят содержательные тесты; небольшой цикл rollout → update → новый эпизод заканчивается без утечки состояния. Все numerical/model тесты — на сервере.

**Готовность качества:** сравнение native, BC GRU, PPO GRU и MLP на общей серверной evaluation конфигурации; итоговый release набор проверяется в realtime headless режиме с зафиксированным delay profile. Для debug достаточно 5–10 парных seeds на сценарий; screening checkpoint — 20–30. Для release начать со 100 парных seeds на каждый основной сценарий и показать 95% интервалы; при недостаточной точности расширить по заранее выбранной цели. Отдельно показывать lockstep throughput и realtime качество, не смешивать их.

**Общий release gate для BC или PPO:** нижняя граница интервала разницы task success с native выше −10 процентных пунктов на основных задачах; пройдены stability/latency/coverage/OOD критерии и проверки exploits. Этот gate применяется и до PPO.

**Дополнительный gate продвижения PPO checkpoint:** хотя бы одна заранее выбранная основная метрика улучшена относительно BC без ухудшения остальных сверх бюджета; эффект и интервалы показаны в отчёте. Если PPO не улучшает BC, сохраняем лучший BC и разбираем среду/награды — завершение PPO job не доказывает готовность PPO policy.

Для содержательного архитектурного/алгоритмического сравнения — три training seeds при доступном бюджете. Каждая on-policy модель собирает собственные rollouts; фиксируются сценарии и interaction budget, а не общий PPO dataset. Стиль и читаемость поведения оцениваются отдельно от win rate.

### 9.4 Новые действия и ситуации

Сейчас сеть включается, когда меч уже в руке и бой идёт. Тактика «достать оружие заранее» недоступна, пока нет таких действий и эпизодов, начинающихся до боя.

**Сделать по очереди, каждый пункт с тестами и gate как на этапе 8:**

1. Достать, убрать и сменить оружие; эпизод начинается с подхода противника.
2. Лук и арбалет: выстрел и смена на ближнее оружие при сближении. Учитель есть: родной ИИ лучников и ArcherAi проекта.
3. Задачи на память в ближнем бою: цель ушла из поля зрения и вернулась, два противника, бой у стены или обрыва, волк с другим ритмом атак.
4. Зелья и предметы в бою. Если родной ИИ так не делает, учителя нет: только награда (9.2–9.3). Отдельно решить баланс: кто пьёт, сколько зелий, ограничения характера.

Препятствия и лазанье остаются на этапе 11.

### 9.5 Сравнение архитектур

MLP, GRU и Mamba (state-space) с одинаковым бюджетом параметров и тремя training seed на задачах 9.4, где нужна память. На задачах этапа 8 все три дают 20/20, поэтому сравнение там ничего не различает. Победитель определяется по качеству, задержке шага и стабильности обучения.

### Сила и характер

Слишком сильный NPC не всегда хорош для игры. Сила политики регулируется: температура, KL к BC, черты характера (трус, берсерк) как часть задачи, уровни сложности. Стиль и читаемость поведения оцениваются отдельно от процента побед, по видео.

## Этап 10 Интеграция в мир

**Сделать:** явный feature flag, allowlist NPC/типов боя и лимит активных акторов. Strands и `set_intent` задают typed goal; новая policy не получает свободный текст и не вызывает LLM в каждом тике. Начать с одного NPC, затем расширить до четырёх активных в зоне героя.

Арбитр поддерживает переходы разговор → угроза → бой → возврат, смену цели, сюжетный takeover, смерть и load. При выключении флага возвращается native источник следующих примитивов, hidden state инвалидируется. Checkpoint rollback — смена версии между эпизодами, без смешения весов и старого hidden state.

**Готовность первой версии:**

- Пройдены gates 2–8 и общий release gate для выбранного checkpoint. Для PPO дополнительно пройден этап 9; для выпуска BC завершение PPO не требуется. В отчёте явно указан тип обучения.
- Один интеграционный сценарий с fake семантическими моделями проходит переходы разговора/угрозы/боя/возврата и загрузку. Живая LLM нужна только для отдельной проверки её намерений, не для основного acceptance.
- 30 минут headless realtime сценария полного мира на сервере при зафиксированной нагрузке: нет новых softlocks, invariant violations, утечки памяти и конфликтов владельца; соблюдены latency/resource budgets.
- Короткая проверка обычной игры на Mac подтверждает визуальную и сетевую интеграцию. Mac gateway от получения снимка до отправки action: целевой p95 до 20 мс с Tailscale; observation-to-arbitration и event-to-defense проверяются с реальным игровым тиком. Продолжительные серии оценки выполняются на сервере; влияние частого bridge на клиентский frame time укладывается в бюджет этапа 1.
- Есть release report с model/dataset/schema versions, coverage, held-out/OOD результатами, latency, fallback долей и известными ограничениями.
- Возврат на native и предыдущий checkpoint проверен в том же сценарии; feature flag по умолчанию включается только в прошедшей оценку области.

## Этап 11 Навигация и несколько целей

Этот этап расширяет готовый melee слой, не блокирует первую версию.

Добавить waypoint candidates и локальную геометрию; goal `move/follow/protect/retreat`, несколько видимых сущностей и target scorer с сохранёнными embeddings. Штатный графовый поиск остаётся в движке. Policy выбирает локальную позицию/обход/восстановление после застревания; длительное retreat получает полноценный исполнитель вместо одного jumpback.

Сначала отдельные навыки и сценарии: открытый маршрут, угол, узкий проход, движущийся NPC, потеря/недостижимость цели. Затем сочетать с боем, проверяя, что navigation training не разрушает уже обученный duel skill. Если общая GRU ухудшает навыки, использовать task heads или отдельные небольшие policy.

**Готовность:** на held-out маршрутах success не хуже native сверх заранее установленного допуска; stuck/collision rate и длина/время пути имеют отдельные метрики. В тупике NPC прекращает бесконечную команду и сообщает наверх `goal_unreachable`. Изменение цели и target IDs не повреждает hidden state, сопровождение соблюдает дистанцию, OOD сцены покрыты отчётом.

## Структура реализации и артефактов

Новые пути ниже — предлагаемые места, окончательные имена выбираются перед кодом соответствующего этапа.

| Область | Место |
|---|---|
| Headless engine | Новый `gothic-body-runner` CMake target/entry point, общий simulation driver, CPU resource/view/audio adapters |
| Headless env и collector | Новые env/runner modules в `body-service/`, localhost/Unix socket IPC, общие scenario/setup configs |
| Body observation/controller | Новые `OpenGothic/ai_bridge/bodyobservation.{h,cpp}`, `bodycontroller.{h,cpp}` |
| Общий primitive resolver | Рядом с `common/game/fightalgo` и `Npc::implAttack`; регистрация в сборке движка |
| Gateway/body loop | Новый `server/src/gothic_ai/body/`; транспорт через существующий `GameLink`, координация с `Bodies`/talk/planner |
| Remote inference/training | Новый пакет `body-service/`; `schema`, `dataset`, `model`, `bc`, `ppo`, `serve`, deploy scripts |
| Dataset/evaluation configs | Новый `evals/npc-body/` с README, manifests, seeds, scenario matrix и отчётами покрытия |
| Сценарии симуляции | Общие setups в `evals/npc-body/`; headless scenario entries и reference `scripts/bench/body-*.bench` |
| Краткие результаты этапов | `reports/npc-body/` — Markdown/JSON сводки без сырых больших logs |
| Runtime на сервере | `~/gothic-ai/body/`, отдельный `uv`, engine binary/build, paths к assets/save/setup, user units/jobs; отдельные runner, inference и training команды |

Checkpoint bundle включает веса, feature/action schema, train normalizer, model config и версии dataset/кода. Env manifest дополнительно фиксирует engine/asset hashes, phase order, simulation substep и delay profile. Gateway не импортирует PyTorch и не загружает веса на Mac. Веса и shared training buffers не должны занимать место в рабочем репозитории игры.

## Порядок проверок и расход ресурсов

Во время реализации после правки — pytest только изменённого модуля и **один** соответствующий e2e/bench сценарий, с вариантами внутри него. Body/headless симуляции и model проверки — на `dev-ubuntu`; локальные тесты используют fake и небольшие данные. Перед PR — полный pytest и регрессия общих e2e один раз: слой затрагивает движок, protocol/game и управление разговором. Обычная игра на Mac нужна для короткой интеграционной проверки, не для training/evaluation серий.

Перед обычной игрой проверить `pgrep -fl Gothic2Notr`; перед headless запуском на сервере проверить также наличие `gothic-body-runner` и runner job/lease. Если симуляция занята другой сессией, дождаться её. Одна игра/runner за раз, pytest и e2e последовательно. Большие evaluation серии выполняются для выбранного checkpoint, не после каждого изменения файла.

Перед runner/training/inference сверить CPU/RAM/threads/disk и GPU бюджеты. Исторический снимок с примерно 2 GiB свободной VRAM не гарантирует доступность сейчас. Headless освобождает renderer VRAM, но всё равно конкурирует за CPU/RAM; выставить измеренные лимиты и не занимать все ядра сервера. ASR/TTS/Strands и продовые контейнеры не останавливать ради прохождения gate; GPU training не запускается фоном без известного бюджета. Для модели используются отдельные venv и user units; конфигурацию Docker и системные пакеты не менять.

## Остановка работы

Работа остановлена по ограничению пользователя после одного SafePeer preflight FAIL. Повторной попытки, private CPU/placement diagnostic, сбора72, child279, нового обучения, боевого gate и fresh screening не назначено. Pipeline279 не реализован и не выполнялся. WIP `f17e5d625fea643ed463b9f2aab10b3498c2b4f6` сохраняет этапы4–8; итоговая документация входит в последующий WIP. Выбранные веса207, normalizer, strict masks/freshness/visibility100 и боевые thresholds неизменны. Motor20/Queue21 и known3/24/6 PASS остаются доказательствами своего scope; Gate11 visibility/handoff FAIL и прежние отказы сохранены. Protected service FAIL рассматривается отдельно с прежним бюджетом10% и не подменяет оценку боя. [Итоговый отчёт](npc-body-stage8-stop-report.md). Возобновление возможно только по новому указанию пользователя.
