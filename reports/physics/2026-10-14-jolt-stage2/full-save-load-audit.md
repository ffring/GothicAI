# Независимая проверка полного save/load: этап 2

Дата: 2026-10-14. Первоначальный аудит проверял порядок загрузки по исходному коду; результаты ниже добавлены после отдельных запусков валидатора. **На финальной обычной сборке v12 прошли полное сохранение/загрузка спящего тела в двух свежих процессах и независимая передача скорости при смерти/оглушении во время падения.** Проверка скорости выполнена в момент создания тела до первого нативного шага; полный save/load — для уже спящего тела. Pending/mid-return подтверждены контрактами и NPC helper, отдельно от полного `GameSession` пути.

## Подтверждённый полный save/load v12

Валидатор использовал одинаковую обычную сборку в двух новых процессах, настоящий `GameSession::save` и штатный конструктор загрузки; оба процесса завершились с кодом 0. Initial-снимок получен после полного конструктора при точном сохранённом tick, **до первого положительного шага**, без тестового обновления позы. Затем проверена одна секунда обычного продолжения спящего тела. В сохранении actor/archive index **292**, **24 физических части** и **34 world bones**; native `State3`, точные native COM/quaternion/linear velocity, дискретное состояние и inventory совпали точно.

| Проверка | Initial, `dt=0` | После 1 секунды | Заранее закреплённый предел |
|---|---:|---:|---:|
| Root position, см | 0 | 0 | ≤0,001 |
| Basis | 0 | 0 | ≤0,00001 |
| Linear velocity, см/с | 0 | 0 | ≤0,001 |
| Angular velocity, рад/с | 0 | 0 | ≤0,00001 |
| Максимальное отклонение 34 world bones, см | 0,00006100000007336348 | 0,00006100000007336348 | ≤0,001 |

Пределы не повышались после просмотра результата. Нативные идентификаторы тел между процессами не сравнивались: backend заново создаёт тела по проверенному скелету и восстанавливает состояние. Результат и точные числовые значения сохранены в [`full-session-v12/comparison.json`](../../../data/physics/stage2/full-session-v12/comparison.json).

```text
normal-build SHA-256: e9032feea82bcd80da4b6d20c05e0bd155fa31d8933696ebf214a8b8b1a98acb
full-save SHA-256:    a4deda5b47527ce106fceb63346af1733803d20028673f7f24ef782ed4cd8a22
```

Новые 31 отрицательных контроля повреждённого recovery также прошли интегрированные проверки: среди них конечные `x0=1e38`/`dir.x=100`, дающие переполнение сразу, и полином с единичным направлением, конечными коэффициентами и нулевыми значениями на концах, переполняющийся внутри интервала. Дополнительный контроль цепочки локально конечных SPD-преобразований проверяет возврат **всех** ordinary world matrices при переполнении композиции и прекращение recovery до передачи позы рендеру. Корректные pending/mid-return и последующие обычные переходы продолжают проходить контракты.

Полный v12 проверяет спящее тело. Он сам по себе не доказывает полный recovery-only load, удаление optional-записей из полного старого сейва или смену мира. Игровой focus/loot проверен отдельно в основном сценарии, а не этим save/load.

## Независимая скорость в момент создания тела

На той же обычной сборке v12 отдельный свежий сценарий содержит `stationary`, `falling_dead` и `falling_ko`. Для каждого случая записаны **6 обычных живых наблюдений** NPC и **4 совпадающих последовательных записи истории**; последняя относится к самому birth tick. Reader [`ragdoll_birth_compare.py`](../../../scripts/ragdoll_birth_compare.py) самостоятельно вычисляет XYZ displacement / game tick delta из живых координат: восстанавливает исходные binary32 значения, выполняет float subtraction/multiplier и неизменный предел 3000 см/с. Заявленный helper-ом expected vector не участвует в вычислении.

| Случай | Самостоятельно вычисленная Y velocity, см/с | Максимальная ошибка всех 24 частей, см/с | Первый положительный нативный шаг | Падение таза на этом шаге, см |
|---|---:|---:|---:|---:|
| Stationary | 0 | 0 | 33 мс | 0,197754; продолжение падения для этого контроля не требовалось |
| Falling dead | −196,0005340576172 | 0,000000057617199900050764 | 34 мс | 7,1961670000000595 |
| Falling KO | −196,0005340576172 | 0,000000057617199900050764 | 34 мс | 7,1961670000000595 |

Последний живой интервал равен 33 мс; оба falling случая имеют три последовательных `in_air` интервала с отрицательным Y displacement и скорость ниже −50 см/с. Все 24 native velocities совпали с независимым измерением в прежнем пределе **0,001 см/с**, без срабатывания предела скорости. Это доказывает скорость **до первого native tick**, поэтому её нельзя объяснить последующим добавлением гравитации Jolt.

Чистый результат: [`birth-velocity-v12-clean/comparison.json`](../../../data/physics/stage2/birth-velocity-v12-clean/comparison.json), SHA-256 исходного `events.jsonl` — `cc82aac689932b187379b728987a40d47873e3f9898d44487956ebddf1ca0661`; валидатор не обнаружил command errors/`ragdoll_error`. Первый [`birth-velocity-v12/comparison.json`](../../../data/physics/stage2/birth-velocity-v12/comparison.json) численно прошёл, но имел **три ошибки fixture cleanup**: использовалось `remove watched` вместо `ragdoll remove watched`. Этот первый исход сохранён; он не называется чистым прохождением сценария. Для повторного запуска исправлен только сценарный cleanup.

Отдельно прошли положительный synthetic fixture и **29 отрицательных контролей reader-а**: [`birth-reader-controls.json`](../../../data/physics/stage2/birth-reader-controls.json). Проверяются duplicate/nonfinite JSON, типы/длины, отсутствующие части/история, неверные ticks/actor/mode, подмена скорости и отсутствие дальнейшего падения. Synthetic controls подтверждают reader, а не реальный движок. Данное живое доказательство относится к обычному падению; scripted climb/ladder и authoritative `setPosition` намеренно инвалидируют накопленную историю движения.

## NPC helper и игровой lifecycle v12

Основной свежий сценарий завершился самостоятельно за 298 с при лимите 300 с: **12 случаев, 3640 CSV/trace frames, strict PASS**, [`gameplay-v12/strict-comparison.json`](../../../data/physics/stage2/gameplay-v12/strict-comparison.json). NPC helper подтвердил sleeping/awake body и pending/mid-return архивы; в одинаковом feature mode измеренные world bones/part frames/velocities совпали точно. Recovery helper восстанавливает один существующий NPC и обновляет его визуал: это не отдельный полный свежий `GameSession` load в середине recovery.

В этом основном сценарии также прошли dormant off/on сохранение, legacy-NPC архив без optional-записей, удаление тела и настоящий focus/open обыска corpse после перемещения **50,58938499209338 см**. Focus выбрал ожидаемый actor 292, а обычный inventory flow открыл `Ransack`. Эти проверки не расширяют sleeping full-session proof на все перечисленные варианты полного сейва. Отдельная очередь AI_Wait/standup v12 тоже прошла: 31 физический кадр сохраняет барьер ожидания, единственная передача управления происходит после deadline; подробности и исходный результат приведены в [общем отчёте](validation-notes.md#функциональные-проверки-v12).

## Сохранённые первые исходы full-session v4–v6

Все сравнения оставлены без перезаписи; предел world bones равен 0,001 см во всех версиях.

| Первый исход | Максимальное отклонение world bones, см | Результат |
|---|---:|---|
| [v4](../../../data/physics/stage2/full-session-v4/comparison.json) | 0,004642208526199297 | FAIL; root/basis/velocities были 0, но visual bones превысили предел |
| [v5](../../../data/physics/stage2/full-session-v5/comparison.json) | 0,004384619025562241 | FAIL по тому же неизменному пределу |
| [v6](../../../data/physics/stage2/full-session-v6/comparison.json) | 0,00006100000007336348 initial и после 1 с | PASS; 24 части/34 кости, native state/inventory exact |

```text
v4 save SHA-256: 7c6a6802421f577fc5ed66e7ad585c60209cf4cf1cbc5a61b8d7eea96e19d154
v5 save SHA-256: fc39e7ae72cca1bd4a307e9c8b3df7683b72f0089c2cfe5d9f8ad997778fe2c9
v6 normal-build SHA-256: e390a077fd6645f9511217ab0f50073ac5d386221c290619454b9508b8b8ffaf
v6 full-save SHA-256:    b579b58f5f41428a3f2d0cae58d6ca5e575e295ac47bcc4df401898ed7feb5b9
```

## Порядок полного восстановления

| Шаг | Исходный код | Значение для тела |
|---|---|---|
| Читаются header и сохранённые ticks/time/world | [`GameSession`](../../../OpenGothic/common/game/gamesession.cpp#L124), строки 130–147 | Время анимаций и физического восстановления уже соответствует сейву. |
| Создаются VM и статический мир | [`GameSession`](../../../OpenGothic/common/game/gamesession.cpp#L149), [`World`](../../../OpenGothic/common/world/world.cpp#L84) | Ландшафт, исходные vob и физический мир существуют до восстановления NPC. |
| Устанавливается версия мира, заранее создаются все NPC | [`WorldObjects::load`](../../../OpenGothic/common/world/worldobjects.cpp#L68), строки 78–83 | Индексы архивных NPC доступны для восстановления межобъектных ссылок. Это не сохранение нативных `BodyID`. |
| NPC читает script/AI, root, движение и визуал | [`Npc::load`](../../../OpenGothic/common/world/objects/npc.cpp#L311), строки 319–377 | `isDead` и `isUnconscious` зависят от загруженного script-state, а не только от HP. Скелет и старая анимационная поза доступны. |
| Восстанавливаются inventory/armor и выполняется `updateTransform` | [`Npc::load`](../../../OpenGothic/common/world/objects/npc.cpp#L379) | Одежда/прикрепления и объектная матрица готовы перед optional body state. |
| Читаются `/ragdoll` и `/ragdoll-recovery`, при включённой настройке тело создаётся/восстанавливается | [`Npc::load`](../../../OpenGothic/common/world/objects/npc.cpp#L389) | Отсутствие записи сохраняет старую down-позу; нативные тела создаются заново, геометрия и mapping проверяются backend. |
| Загружаются `npc_invalid`, сохранённые предметы/vob/routines; затем `postValidate` | [`WorldObjects::load`](../../../OpenGothic/common/world/worldobjects.cpp#L86) | Тело может быть создано до этих шагов, но в этом пути загрузки нет физического `Update`. Нельзя считать его первое столкновение проверенным одним NPC helper. Для invalid NPC вызывается `suspendRagdoll`. |
| Восстанавливаются VM variables, HERO и камера; вызывается session hook | [`GameSession`](../../../OpenGothic/common/game/gamesession.cpp#L162) | Проверка сразу после полного конструктора покрывает состояние после всех этих шагов. |

При обычной загрузке полного сейва конструктор `GameSession(Serialize&)` не вызывает `initScripts`/`resetPositionToTA`. При переходе между мирами путь другой: [`implChangeWorld`](../../../OpenGothic/common/game/gamesession.cpp#L426) после `World::load` вызывает `initScripts`, который вызывает `resetPositionToTA`. Результат обычного save/load нельзя распространять на смену мира без отдельного контроля.

## Найденный случай: recovery-only, первый кадр

**P2, найдено по исходному коду и исправлено владельцем NPC-интеграции.** До исправления, если NPC сохранён во время возврата из физической позы и active/dormant body отсутствует, optional recovery state устанавливалось после последнего пересчёта визуала в `Npc::load`:

1. `updateTransform()` выполняется в строке 383.
2. `loadRagdollRecovery()` вызывается позже, в строке 399.
3. [`Pose::loadRagdollRecovery`](../../../OpenGothic/common/graphics/mesh/pose.cpp#L541) устанавливает `physicalReturning=true` и `needToUpdate=true`, но не пересчитывает `tr`/прикрепления.
4. `prepareRagdoll()` вызывается здесь только при наличии dormant body; recovery-only запись его не имеет.

До следующего обновления анимации визуал мог показывать уже пересчитанную анимационную позу без сохранённой ненулевой поправки возврата. Это именно случай с ненулевой поправкой `AnimBlend`; равная ей анимационная поза дефект не проявит.

Исходный NPC helper **маскировал этот путь**: после `npc->load` он явно вызывал `npc->prepareRagdoll(); npc->updateAnimation(0,true)` ([`ragdollNpcArchive`](../../../OpenGothic/common/testbench.cpp#L1602), строки 1603–1605 исходного снимка). Полный bodyrunner вызывает `SimulationStep::finishStep(0)` ([`reset`](../../../OpenGothic/game/bodyrunner.cpp#L119), строки 129–130), но [`WorldObjects::updateAnimation`](../../../OpenGothic/common/world/worldobjects.cpp#L443) пропускает `dt==0`. Поэтому успешный NPC helper не доказывал правильный первый кадр полного load.

Применённое исправление: после загрузки optional-информации `Npc::load` обновляет визуал и прикрепления через `updateAnimation(0,true)`. Контракты Pose и NPC helper проверяют ненулевые pending/mid-return поправки и продолжение; полный sleeping v12 подтверждает производственный initial-load порядок для спящего тела. Для отдельного полного recovery-only доказательства по-прежнему нужен снимок после конструктора **до первого положительного dt**, без дополнительного тестового refresh. Этот отчёт различает эти два сценария.

## Тело, позиция и обыск

- [`syncRagdoll`](../../../OpenGothic/common/world/objects/npc.cpp#L891) переносит root translation тела в `Npc::x/y/z`, обновляет ghost position и физический pose overlay. [`Ragdoll::rootTransform`](../../../OpenGothic/common/physics/ragdoll.cpp#L458) сохраняет исходную объектную ориентацию и добавляет перемещение первого физического узла. Точные кости идут отдельно через `getPose`.
- Focus по-прежнему выбирает NPC по position/range/visibility ([`findNpcNear`](../../../OpenGothic/common/world/worldobjects.cpp#L783), [`testObj`](../../../OpenGothic/common/world/worldobjects.cpp#L1115)), а обыск вызывается через `other.isDown()` ([`PlayerControl::interact`](../../../OpenGothic/common/game/playercontrol.cpp#L419)). Backend body не заменяет inventory или NPC identity.
- `centerPosition()` использует root position плюс bind-height `Pose::translateY`, `displayPosition()` — обычную высоту подписи. Физическая голова используется дополнительным visibility-ray. Это сохранение прежней схемы фокуса; реальный контроль переместившегося corpse прошёл в gameplay v12, как описано выше. Он не доказывает точный фокус на любой повернувшейся лежащей позе.
- `npcNear` пересчитывается в `WorldObjects::tick` до нативного physics tick, `syncRagdolls` выполняется после него. Положение тела для фокуса обновлено, список near может отставать на один шаг при пересечении его границы. Контроль следует делать после следующего штатного шага, отдельно от точного initial-load снимка.
- Команда `time` вызывает [`World::setDayTime`](../../../OpenGothic/common/world/world.cpp#L418) → `resetPositionToTA`. Обычные corpses без mission items переносятся в `npc_invalid`/deadPoint, тела suspend ([`WorldObjects`](../../../OpenGothic/common/world/worldobjects.cpp#L1003)). Это отдельная очистка мира; такую команду нельзя вставлять перед сравнением сохранённого места тела.

## Протокол полного probe и отдельные сценарии

1. Использовать одну зафиксированную сборку, одинаковые assets/settings и два свежих процесса. До создания `GameSession` явно включить `GAME/ragdoll` в изолированной тестовой конфигурации. Если второй процесс сначала загружает режим off, проверять dormant→on отдельно: это другой тест.
2. В первом процессе создать/убить конкретного NPC обычным игровым lifecycle. Допуск к сохранению: нативное тело действительно существует, part count положительный, **каждая часть sleeping**, все transforms/velocities конечные. Не продолжать тест полного тела при неудачной фабрике человека.
3. На границе после обычного world/animation шага сохранить полный `GameSession` через настоящий `GameSession::save(Serialize&,name,Pixmap)`. Использовать небольшой валидный CPU `Pixmap` для preview, дождаться разрушения `Serialize` и финализации ZIP. Сохранить SHA сейва и manifest с archive NPC index, instance symbol, `hnpc.id`, world, ticks/time, HP, script state, root, полной world bone pose, mapping, всеми body transforms/velocities/sleep, inventory и настройкой feature.
4. Во втором процессе прочитать этот файл штатным `-save`/`GameSession(Serialize&)`. После завершения конструктора и установки game получить снимок **до первого worldPlayerStep с dt>0**. Нативные BodyID между процессами не сравнивать. Проверить число NPC/частей, identity, HP/dead state, inventory, root/bones/parts и sleep. Для чисел предварительно закрепить тот же узкий предел, что у NPC helper: position/bone/linear delta ≤0,001 см (linear в см/с), basis/angular ≤0,00001 (angular в рад/с), все дискретные поля строго одинаковые. Ничего не исключать после просмотра результата.
5. Затем выполнить штатные фиксированные шаги без reset/time/teleport/push. Спящее тело должно оставаться на месте и в той же позе; дополнительно проверить обыск/инвентарь после подведения живого героя и обычного focus refresh.
6. Отдельный полный old-save контроль: убрать только optional body/recovery записи из полученного полного ZIP, сохранить остальные entries неизменными, загрузить свежим процессом. Down NPC должен остаться animated/legacy, новый body не должен возникнуть на первом или следующих шагах до нового down event.
7. Отдельный mid-return контроль: сохранить в момент ненулевого `physicalReturning`, загрузить свежим процессом и сравнить initial frame с сохранёнными final world bones без тестового `updateAnimation` перед снимком. Затем проверить продолжение возврата, а не только начальную позу.

Идентифицировать загруженный corpse следует архивным индексом вместе с symbol/`hnpc.id`: [`findNpc`](../../../OpenGothic/common/testbench.cpp#L1095) предпочитает живого NPC при повторяющемся instance symbol; `watched` в свежем процессе без subject означает героя. Кроме того, сценарные benchmark-команды запускаются только после первого положительного шага ([`TestBench::tick`](../../../OpenGothic/common/testbench.cpp#L5199), строки 5206/5255). Одного сценарного readout недостаточно для initial-frame gate; нужен независимый hook непосредственно после полного load.

## Снимок проверенных исходников

SHA-256 до исправления recovery-only порядка:

```text
f5ddaf6378bef0b1020eab23dc8757f6116af36ab676c816a954e02ead959be2  common/game/gamesession.cpp
eb1880fcb97b96d0c54c6e3c078575fa0da521e7704139ecd45e78c65af9f7e4  common/world/world.cpp
70b5c0eef878fd8e5add1f1ec71e97b482ceaab94d95838a05e1f55bb774cb6b  common/world/worldobjects.cpp
1df2da86c751000adcb552fd21be24ab7f8d6adf17103a239f4d164c8fcc8a63  common/world/objects/npc.cpp
c55bdbbb6eca0b4c11ee417454b9ff9c5a316b72d0b1c9fd9e202f109bfde464  common/graphics/mesh/pose.cpp
e36bfc7dd26050c976a8371fd1af7bebc3f6e9d7dd9bf68c8377c974327d36a9  common/testbench.cpp
87ff0c970afc7136f06c2999b7ba243eee3f3455c1ead48d721e5c59e55b4829  game/bodyrunner.cpp
f0e5e07c162f4631f197b14647a12ea00b74694ea6e69f27d5a95924cbfb492b  common/physics/ragdoll.cpp
```
