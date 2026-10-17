# Боевой API и арбитр тела NPC

Этап 4 [плана GRU](npc-body-control-plan.md). Общий CPU `BodyController` используется headless runner и обычным AI bridge. Scripted provider выбирает примитивы, а настоящие NPC, физика, анимационные окна и расчёт урона исполняют их. Нейросеть и обучение относятся к следующим этапам.

Этап прошёл полный серверный прогон `stage4-v5`: [результаты и границы проверки](../reports/npc-body/2026-10-13-body-api/validation-notes.md), [37 сценариев](../reports/npc-body/2026-10-13-body-api/body-api.md), [машинный отчёт](../reports/npc-body/2026-10-13-body-api/body-api.json).

## Владение и границы первой версии

Для NPC существует один body lease. Без lease источник — native AI. Acquire требует `body_control=true`, наблюдаемого участника сценария, живую видимую текущую цель и безопасную границу. Боевые действия первой версии требуют drawn 1H/2H sword. Bridge дополнительно запрещает acquire у attached NPC, во время разговора и soft action.

Эта версия управляет melee-примитивами относительно **существующей native цели**. `body_goal` повышает версию этой цели; выбор другого NPC и стратегические задачи пока не входят в API. Native смена цели завершает lease через безопасную передачу управления.

Под lease native routines/perceptions и обычные body queues не выбирают следующий примитив. `npc_act`, attach/action и разговорные команды, меняющие тело, отклоняются. Сюжетные/native изменения идут через отложенный takeover: они инвалидируют команду, ждут завершения защищённого сегмента и применяются после возврата native. Текущая атака не теряет animation/wait gates из-за очистки очереди.

Повторная настройка `body_watch` запрещена при действующем lease. World load/reset завершает эпизод, очищает управление и меняет IDs. Reconnect сначала завершает старое владение на безопасной границе и только затем меняет observation epoch.

## Общий wire API

Headless использует JSONL поле `cmd`; bridge — такое же значение в `type` и свой transport request ID. Оба адаптера вызывают один parser. Runner сохраняет точный общий encoder в `body_reply`, а вокруг добавляет свой status envelope.

| Операция | Назначение |
|---|---|
| `body_acquire` | Получить lease: actor, provider, текущая цель, `body_control`, mode, timeout |
| `body_goal` | Повысить `goal_version` для текущей native melee цели |
| `body_action` | Передать выбранный примитив с полным контекстом наблюдения |
| `body_heartbeat` | Обновить wall watchdog без изменения действия |
| `body_release` | Запросить возврат native на безопасной границе |
| `body_disconnect` | Потеря конкретного lease/provider; требуется полная identity |
| `body_control_status` | Прочитать owner, pending execution, masks и допустимость решения |
| `body_events` | Забрать ограниченную очередь событий с явным счётчиком потерь |

Lease identity: `session_epoch`, `world_epoch`, `entity_id`, `control_generation`, `provider`. Команда дополнительно несёт `goal_version`, `target_entity_id`, `observation_seq`, `issued_game_ms`, `command_id`, `action_id` и `ttl_ms`. Runtime entity ID обозначает экземпляр NPC, а не script symbol.

`command_id` положителен и возрастает внутри lease. Последние 64 receipts кэшируются: точный повтор возвращает прежний ответ с `duplicate=true`, изменённый повтор отклоняется, слишком старый ID не исполняется. Pending command slot один; во время исполнения новое действие отклоняется, а `continue` сохраняет тот же execution ID.

Snapshot должен быть последним для этого NPC, с точным game timestamp. После acquire и смены goal нужен новый sample; прежнее наблюдение нельзя присвоить новому управлению. TTL 1–1000 мс проверяется при принятии и снова перед фактическим началом. В realtime учитываются game age и wall age исходного engine snapshot. В lockstep TTL определяется game time, а wall потеря provider отдельно завершает аварийный эпизод.

Режим lease привязан к runner mode; в обычной игре разрешён только realtime. Timeout provider — 100–5000 wall мс, измеряется локальным `steady_clock`. Часы Mac и сервера не сравниваются.

## Примитивы, masks и события

| ID | Действие |
|---|---|
| 0 | `continue` |
| 1 | `approach` |
| 2 | `turn_to_target` |
| 3, 4 | `strafe_left`, `strafe_right` |
| 5 | `jump_back` |
| 6, 7, 8 | `attack_front`, `attack_left`, `attack_right` |
| 9 | `block` |
| 10, 11 | `wait_200`, `wait_300` |

Pure `FightAlgo` resolver получает числовое/boolean состояние и не вызывает RNG, не двигает очередь и не запускает анимацию. Native normalization и policy masks используют общие правила; исполнитель — один `Npc::implAttack`. Masks учитывают sword/target, gates, доступность анимаций, угол, дальность и collision ray. Полный native consume/skip порядок сохраняется при выключенном внешнем управлении.

`wait_200` сохраняет native преобразование в jump back при тесной дистанции и focus. Поэтому intent, effective primitive и физическое исполнение записываются отдельно. Mask не обещает попадание: range/focus/collision проверяются снова, включая native damage commit.

В observations schema 1 добавлены authoritative `action_mask`, `action_mask_valid`, `control_generation`, `goal.version`, `control_allowed` и `can_decide`. Пассивная availability mask сохраняется отдельно. Без lease или при lock новое решение недоступно; observations продолжают поступать для будущей recurrent memory.

События связывают command и execution: `accepted`, `started`, `continued`, `completed`, `rejected`, `cancelled`. Start определяется реальной сменой pose/segment, движения, поворота или native wait deadline; consume сам по себе не является start. Completion следует engine условию движения/ожидания или завершению конкретного animation segment. Ранняя замена сегмента — cancellation.

Видимость обязательна для нового решения и старта команды. Краткая потеря видимости из-за направления взгляда в strafe-анимации не прерывает уже начатый защищённый сегмент. После его завершения NPC возвращается в Idle; если цель остаётся скрытой после обновления позы, управление возвращается native AI. Скрытые признаки цели и новые действия при этом недоступны. Смерть, удаление или смена самой цели инвалидируют команду независимо от видимости.

События имеют process/world/control epochs, goal/actor/target/command/execution IDs, возрастающий `event_seq`, game/wall timestamps, effective primitive и причину. Очередь ограничена 512 событиями; pending native mutations — ограниченным буфером. Старые epoch события сохраняются в durable journal, но не входят в новый IPC packet после reset.

## Потеря provider

В realtime watchdog или disconnect отменяет административную команду и запрещает следующий внешний выбор. Непрерываемая физическая часть заканчивается штатно; на следующей безопасной границе возвращается native. Журнал фиксирует обнаружение потери, оставшийся lock и фактическую передачу ownership.

В lockstep timeout/disconnect ставит `infrastructure_failure`; `advance` заканчивается с соответствующим `stopped_on`. Этот эпизод не продолжается как обычная native дуэль. Watchdog работает также при неподвижном game time и pause. `advance` во время pause отклоняется; resume не ускоряет симуляцию для компенсации wall паузы.

## Запуск на dev-ubuntu

Отдельный mutable runtime: `~/gothic-ai/body-api/source`, `build`, `runs`. Assets/save и baseline этапов 1–3 используются из архивов. Mac выполняет только SSH orchestration.

```sh
scripts/body-api-remote.sh preflight
scripts/body-api-remote.sh sync
scripts/body-api-remote.sh build
scripts/body-api-remote.sh run stage4-validation
scripts/body-api-remote.sh collect stage4-validation
```

Сборка включает CPU resolver/arbiter test executable. Серверный сценарий исполняется transient user unit с CPUQuota 200%, MemoryMax 12G, TasksMax 128 и wall watchdog 2400 с. Общий simulation lock допускает один engine процесс. Продовые контейнеры, Docker и ASR/TTS не изменяются.

`body_fixture` и `body_self_test` — test-only headless endpoints. Fixture устанавливает реальные позиции/weapon/pose/HP/locks и проверяет legacy/native пути; движения, атаки и урон остаются в engine. Геометрический сценарий ищет настоящую преграду через world rays, а не подставляет boolean столкновения.

Полная проверка и независимый report используют [body-api-v1.json](../evals/npc-body/body-api-v1.json): все 12 примитивов, fault injection, masks/purity, ownership, realtime native fallback, lockstep infrastructure failure и strict native-off traces для seeds 17/29/43. Smoke не считается готовностью этапа. Большие журналы и бинарники остаются на сервере; в репозитории сохраняются компактные результаты и код повторного запуска.
