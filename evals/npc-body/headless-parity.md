# Проверка механики headless runner

Критерии заданы до измерения в [headless-parity-v1.json](headless-parity-v1.json). Это проверка
упорядоченного поведения каждого участника каждого seed. Одинаковый исход, гистограмма примитивов
или средний урон не заменяют совпадение native instruction queues, consume, animation hooks,
guard transitions, попаданий, HP и terminal события.

Допуски: один фиксированный substep (34 мс) для относительного времени, 2 см для позиции и
векторов перемещения, 0,5° для yaw, 1e-6 для численных animation metadata. Порядок, очереди из
шести элементов, принятие/отказ анимации, collision flags/counts, урон и HP проверяются точно.
После неудачного прогона допуски не расширяются: отчёт сохраняет первые расхождения, их seq и
полные counts. Сначала воспроизводится причина на сохранённом бинарнике/reference или
rendered сборке того же source. Seed не объявляется обещанием отсутствия native nondeterminism.

`scripts/body_headless_parity.py` использует строгий reader и completeness validator stage 1.
Отсутствующий terminal, неправильные настройки, редкие/неполные snapshots, пропущенные per-tick
physics events и неполные поля дают `incomplete`, а не timeout или совпадение. `diverged` означает
полное измерение, которое вышло за объявленный контракт. `matched` относится только к указанному
в отчёте профилю и виду сравнения.

Есть два профиля:

- `archived_reference`: сравнение с неизменяемым `melee-reference-stage1-v4`. В старом trace есть
  конечные позиции, yaw и CPU animation metadata, но нет root-motion deltas и collision internals.
  Этот профиль проверяет наблюдаемую часть stage 1 и не закрывает весь этап 2.
- `full`: добавляет `physics_step` на каждом fixed tick для обоих участников. Для доказательства
  сохранения физики после удаления графики reference должен быть rendered сборкой того же source
  с новой телеметрией. Сравнение двух headless прогонов через этот профиль проверяет независимость
  механики от wall-clock pacing; оно помечается `headless_schedule_invariance` и не выдаёт
  `full_stage2_evidence=true`.

`physics_step` записывается в `npc_post_movement`: pure CPU pose root displacement в мировых
координатах до navigation/gravity, первая запрошенная native MoveAlgo displacement, конечное
перемещение, native movement state и результаты вызовов `MoveAlgo::tryMove`. `movealgo_tick=true` обозначает реальный вызов, его `collision_scope`
равен `movealgo_trymove`: прямые ground corrections и mobsi moves не выдаются за эти probes.
Native tick без вызова движения записывается с `movealgo_tick=false`,
`skip_reason=npc_tick_without_movealgo`, `collision_scope=none`, нулевым applied root/requested
displacement и collision counters. Фактическая позиция/yaw по-прежнему измеряются;
пропущенный tick без такого явного свидетельства остаётся ошибкой.
`grounded` означает `movement_state==Run`, а не новый физический тест. Точные имена и обязательные
поля зафиксированы в JSON. Все ветки, включая early return, должны сохранить событие текущего tick.

Пример после сбора трёхseed серии с сервера:

```sh
python3 scripts/body_headless_parity.py \
  data/npc-body/reference/melee-reference-stage1-v4 \
  data/npc-body/headless/headless-lockstep \
  --profile archived_reference \
  --output-dir reports/npc-body/headless-archived-parity

python3 scripts/body_headless_parity.py \
  data/npc-body/reference/rendered-physics-reference \
  data/npc-body/headless/headless-lockstep \
  --profile full \
  --output-dir reports/npc-body/headless-rendered-parity
```

Оба каталога должны содержать `events.jsonl` и измеренный `manifest.json`. В candidate обязательны
`headless=true`, `graphics_device_created=false`, `window_created=false`,
`audio_device_created=false`, `display=null`, `renderer="none"`. Доказательство создания устройств
приходит из runner/startup instrumentation, а не из отсутствия `DISPLAY` в окружении. Asset,
scenario/config, save/settings hashes, частота симуляции и версия игры должны совпадать.

Reporter пишет `headless-parity.md` и `headless-parity.json`, exit 0 только для `matched`, exit 2
для `diverged`/`incomplete`. Оба результата сохраняются. Raw traces и private assets остаются в
ignored каталогах. Сами игровые прогоны выполняются на dev-ubuntu; этот Python reporter не
запускает игру или модели и пригоден для лёгкой локальной проверки уже собранных данных.

Guard окна остаются предикатами движка. Left/right censoring сравнивается явно; terminal-clipped
интервал не превращается в полное окно. Если guard не активировался ни разу, отчёт обозначает его
как `unexercised`, даже когда все остальные проверки совпали. Например, stage 1 не упражнял
`def_par_window`; совпадение этих прогонов не доказывает обработку parry окна.

Focused parser suite (один запуск координирующим агентом, без игры):

```sh
cd server
uv run pytest ../scripts/tests/test_body_headless_parity.py
```

## Контроль случайной ветки animation clip

Обычный режим оставляет исходный `std::rand()%3` для Gothic II 1H/2H `AttackBlock` без изменений.
Варианты `T_*HPARADE_0`, `_A2`, `_A3` имеют одинаковые защитные tag metadata, но различаются root
motion. Shared `std::rand` также используется звуком: отличие варианта способно изменить дистанцию
и следующий hit, даже если native intent первоначально одинаков. Для benchmark доступны два явно
включаемых режима:

- `OPENGOTHIC_BODY_ANIMATION_RNG=isolated`: отдельный xorshift32 stream каждого участника,
  начальное состояние `seed ^ (0x9e3779b9 * (side + 1))`, ноль заменяется `0x6d2b79f5`. Каждый
  eligible solve делает один шаг stream. Повторные fresh bodyduel/reset начинают его заново.
- `OPENGOTHIC_BODY_ANIMATION_RNG=replay` вместе с `OPENGOTHIC_BODY_ANIMATION_FIXTURE=...`:
  точный вариант принятого блока из исторического trace по `(seed, role, relative_time_ms)`.
  Два continuation/start solves в одном tick получают один вариант. Это воспроизведение записанной
  случайной ветки для изоляции изменений исполнителя, а не новая оценка качества native AI.

Во всех режимах исходный shared `std::rand` всё равно вызывается ровно один раз в прежнем месте
solver. Только выбранный clip заменяется при opt-in, только у двух участников активного bodyduel.
Native AI intent, queue/mask, guard predicates, collision, hit и HP не задаются из fixture. Default,
NPC без context и работа вне benchmark возвращают исходный результат. Actor context передаётся
явно из `MdlVisual`; references и PRNG state очищаются на terminal, destruction и runner reset.

Fixture JSON: `schema_version=1`, `kind="body_animation_branch_fixture"`, `source_events_sha256`,
`entries=[{seed, role, relative_time_ms, animation, variant, source_seq}]`. Роли — `actor`/`target`,
variants — 0/1/2; названия 1H/2H clip должны соответствовать variant. Duplicate JSON keys,
duplicate `(seed, role, time)` или `source_seq`, неправильные поля и hash syntax отклоняются.
Никакого поиска ближайшего timestamp или расширения временного допуска при выборе fixture нет.

Entry считается consumed только после native `accepted=true, pose_changed=true` attempt
`AttackBlock`, если реальный resulting layer, variant и точный timestamp совпали. Draw сам по себе
не является consumption. Неожиданный/дублированный accepted block, неиспользованные entries на
terminal или в конце сценария создают `body_duel_error`, который отвергает базовый validator.
В trace есть `animation_branch_draw`, `animation_branch_accept`, per-seed
`body_animation_control_summary` и общие `body_animation_control_totals`; launcher проверяет их
связь с исходными accepted attempts. Replay требует полный набор fixture seeds; для отдельного
seed нужен отдельно извлечённый subset fixture. Общий footer не скрывает entries других seeds.

Ограничения остаются строгими: replay не гарантирует восстановления других источников случайности
или состояния мира; несовпадение любого другого механического события остаётся `diverged`.
Engine проверяет schema и usage fixture; происхождение/hash его содержимого относительно
неизменяемого raw reference подтверждается extractor/launcher manifest. Historical replay и
same-source isolated rendered/headless parity сохраняются как разные виды доказательств.
