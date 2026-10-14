# Этап 4: боевой API и арбитр тела

**Готов.** Полный прогон `stage4-v5` на dev-ubuntu прошёл 13 октября 2026 года. [Независимый отчёт](body-api.md) подтвердил 37/37 сценариев, без пропусков или ошибок; [JSON](body-api.json) содержит результаты и 113 SHA-256 исходных журналов. Код находится в отдельном worktree, ветка `codex/npc-body-api`.

## Что реализовано

Общий `BodyController` владеет телом NPC через lease. Headless JSONL и обычный bridge вызывают один parser, pure `FightAlgo` resolver и существующий `Npc::implAttack`. Есть 12 примитивов, authoritative masks, версии цели/владения, freshness/TTL, bounded pending command, receipts для повторов и журнал фактического исполнения. [Контракт API](../../../docs/npc-body-api.md) описывает wire и границы версии.

Native routines, soft actions, attach и разговор не выбирают движение одновременно с provider. Сюжетный takeover, timeout и disconnect запрещают следующий внешний выбор; защищённый сегмент заканчивается, затем управление возвращается native AI. Для lockstep потеря provider завершает эпизод как infrastructure failure. Краткая потеря видимости внутри strafe сохраняет начатый сегмент, но скрывает признаки цели и блокирует новые решения.

## Измеренные проверки

| Проверка | Результат |
|---|---|
| Все 12 примитивов | Принятие/реальный start/completion; `continue` сохраняет execution |
| Masks и freshness | Pure resolver, repeat purity/RNG/queue invariance, weapon/range/реальная преграда |
| Команды | Duplicate, out-of-order, stale session/world/generation/disconnect/goal/observation, TTL, invalid target |
| Жизненный цикл | Смерть, потеря цели, reset, pause/resume, истечение accepted команды до старта |
| Конфликт владельцев | Реальные soft/attach/dialog guards; второй provider отклонён |
| Сюжетный takeover | Lock 1121 игровых мс сохранён; native restored на 17661 мс, native choice того же actor на 17761 мс |
| Цепочка одной lease | `attack_front → wait_300 → attack_right`; HP цели 1000000 → 999930 → 999860 |
| Realtime watchdog | Обнаружен через 1017 wall мс при timeout 1000 мс, в установленном допуске 100 мс |
| Realtime disconnect | Native return через 1124 wall мс при оставшемся lock 1121 игровых мс |
| Lockstep watchdog | Через 1009 wall мс: infrastructure failure, `advance` остановлен; native дуэль не продолжена |
| Native-off parity | Seeds 17/29/43 совпали: 1077 ordered mechanics и 3198 physics steps; drift позиции/угла — 0 |

Parity сравнивает **архив headless этапа 3 до refactor** с текущим headless runner. Новый rendered прогон в этап 4 не входит. `def_par_window` в этих baseline seeds не exercised; отчёт сохраняет это ограничение. Удары scripted provider подтверждены связанными native hit events и настоящим уменьшением HP.

На dev-ubuntu собраны `gothic-body-runner`, `Gothic2Notr` и CPU test executable. Native тест выполнил [2419 assertions](resolver-tests.json). Прошли 26 тестов parser отчёта и 26 тестов Python body adapter. Последние используют четыре точных native encoder payload из `stage4-v5/wait_300`, включая strict decoding и ordinary bridge cache; fixture содержит source/binary/IPC hashes.

## Ресурсы и сохранённые данные

Полная suite заняла 95,85 с wall и 143,85 с CPU. `/usr/bin/time` записал peak RSS 753764 KiB (736,1 MiB); 397 `/proc` samples одного runner PID показали максимум sampled RSS 648340 KiB (633,1 MiB), 17 threads и 10 FD. Отдельная cgroup-метрика systemd — peak 647M. [Сводка](validation-summary.json), [time output](resources.txt), [manifest](manifest.json).

Manifest фиксирует отсутствие graphics/window/audio device и модели. В samples отсутствуют GPU device/display socket descriptors. Voxtral и VoxCPM остались active/running с теми же PID 3366349 и 630559. Один серверный runner использовал общий simulation lock и transient user unit: CPUQuota 200%, MemoryMax 12G, TasksMax 128. Docker и системные пакеты не изменялись. Runner завершился после проверки.

Полный неизменённый run, журналы, source archive и бинарники: `dev-ubuntu:~/gothic-ai/body-api/runs/stage4-v5/`. Base Git HEAD — `979d343354ea7bf711a53fce1331c83b31080a27`; архив включает добавленные исходники, suite и configs. SHA source archive: `beea806afa913716259c3650a0de77cd0054028ca6f17fbf899425cefdfed05f`. Binary SHA: `875b7a970bd29f88025f3b1cfbeb58487f7a7ab70bfad7528cd6a0c1cc7a59a7`; source tree SHA: `63d4776a2de1d368c1a29b933b8739af8ba56c26213aa229482a0cf5301c9e7b`.

После успешной suite финальный report пересчитан из тех же raw данных в `~/gothic-ai/body-api/reports/stage4-v5-final/`: уточнены только labels scope/comparison kind архивного parity. Comparator и readiness gates не менялись; `reporter_sha256` в JSON фиксирует точный текущий reporter. Исходный run и его original report сохранены.

## Воспроизведение и границы

Из корня репозитория:

```sh
scripts/body-api-remote.sh preflight
scripts/body-api-remote.sh sync
scripts/body-api-remote.sh build
scripts/body-api-remote.sh run stage4-repeat
scripts/body-api-remote.sh collect stage4-repeat
```

Выбирать новое имя для каждого запуска. Большие IPC/observation/mechanics journals остаются на сервере. Smoke проверяет инфраструктуру и не устанавливает готовность.

Проверены один controlled melee NPC и один target. Fixture устанавливает реальные начальные состояния и вводит сбои; анимации, движение, столкновения и урон выполняет движок. API v1 работает с текущей native целью; стратегические goals, выбор другой цели, neural policy, inference latency и обучение ещё не реализованы. Результаты не измеряют качество побед в дуэли, training throughput или нагрузку нескольких controlled NPC. Следующий этап — арена и runner эпизодов.
