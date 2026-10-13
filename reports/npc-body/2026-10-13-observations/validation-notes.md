# Проверка реализации этапа 3

13 октября 2026 года. [Итоговый отчёт](body-observe.md), [числовая схема](../../../docs/npc-body-observations.md), [критерии до измерений](../../../evals/npc-body/body-observe-v1.json).

## Реализация и проверки

Общий CPU builder исполняется после завершения world/player/animation/camera update и до команд benchmark. У наблюдаемых NPC остаются native AI, физика, урон и анимации. На сервере собраны обычный `Gothic2Notr` и `gothic-body-runner`; новые сообщения обычного bridge имеют версию протокола 16.

Целевые проверки схемы, транспорта и парсера выполнены до запуска полной серверной серии:

```sh
server/.venv/bin/pytest scripts/tests/test_body_observe_report.py \
  server/tests/test_body_observations.py server/tests/test_protocol.py
```

**64 passed.** Проверены строгие типы, finite числа, размеры, epochs/sequence, скрытые target features, скорость при разрыве движения, bounded cache/queue, загрузка/reconnect и гонка первого пакета. Bash syntax, Python compilation и `git diff --check` прошли. Pytest и симуляции не выполнялись одновременно; модели и серийная игра на Mac не запускались.

Перед включением в main дополнительная проверка нашла несовместимость обычного bridge: Python ограничивал `session_epoch` значением 1, хотя shared native encoder передаёт opaque ID процесса. Схема принимает положительный строгий integer, проверяет session каждого ряда; cache отвергает чужую session до нового connection/hello. Добавлена небольшая fixture из двух реальных строк `stage3-v1`, без response envelope runner. 53 целевых теста Python bridge/protocol прошли, включая приём настоящего пакета, отклонение смешанных sessions и смену процесса. Эта правка Python не меняет проверенные симуляцию и измерения headless.

## Серверная серия

Полная серия использует один процесс и последовательные fresh-world resets: три чередующиеся пары observer off/on по 90 игровых секунд, реальный overflow, четыре privacy cases, три seeded mortal melee дуэли off/on и 600 секунд realtime для четырёх native бойцов. Высокие HP этого отдельного нагрузочного сценария сохраняют настоящий урон без invulnerability или healing. Runtime guard проверяет активный AI LOD, native ownership, дистанцию до observer и отсутствие смерти; независимый validator требует бой каждого NPC в каждом минутном интервале и реальную потерю HP.

**Финальный `stage3-v1` прошёл:** suite exit 0, validator `complete`, failed gates 0. Вся серия заняла 15 мин 13 с; ASR/TTS остались active/running с прежними PID. Сам ten-minute phase дал 52 812 снимков и 600 067 мс между первым и последним sample, без row/event drops и пропущенных period buckets. Четыре NPC атаковали во всех десяти минутных интервалах; подтверждённые потери HP: 7 788 / 5 446 / 5 702 / 9 362 points.

Realtime p95 интервала — 66 игровых / 67,76 wall мс, максимум wall 91,30 мс. Добавленная работа p95 — 0,273 мс по объединённым четырёхакторным шагам; long phase p95 0,271 мс, max 3,321 мс. У всей серии max 63,325 мс: p95 budget не является hard maximum. Bulk IPC packets (до 189 строк за запрос) в long phase имеют p95 2,328 мс; эти затраты включены в соответствующий simulation step, а не выданы за стоимость одного actor forward.

Медианы throughput off/on — 69,41 / 70,19 substeps/s; небольшая положительная разница относится к вариации замера, ускорение от подписки не заявляется. RSS в long phase 634,33 → 634,58 MiB, FD 7–8 и threads 17; process HWM 737,70 MiB включает предыдущие фазы того же процесса. CPU long phase user+sys около 564,17 с при 600,61 wall с. Устройства graphics/audio отсутствуют; renderer/GPU процесса не используется. Состояние юнитов не является замером задержек ASR/TTS.

Нагрузка складывает native hooks, признаки/visibility, 16 Landscape/Object rays, кодирование строк, engine journal, watch list и обработку/отправку IPC одного simulation step. Timing journal — инструмент измерения; его собственная запись исключена из cost timer, но включена в paired throughput. P95 считается по четырём NPC, без разбавления setup ticks и privacy fixture из двух акторов. Periodic game/wall cadence проверяется отдельно от дополнительных событийных строк.

Сырой engine journal и фактически доставленные пакеты сохраняются на `dev-ubuntu`. В репозитории остаются компактный отчёт, provenance, scripts/config и небольшой privacy oracle; большие журналы и assets сюда не зеркалируются.

Серверный evidence — `/home/kaifat/gothic-ai/body-headless/runs/stage3-v1/`, компактная локальная копия — ignored `data/npc-body/observe/stage3-v1/`. Повторный запуск использует новое имя, чтобы сохранить provenance прежней серии.

## Исправления, обнаруженные проверкой

Read-only аудит выявил, что `hasAnim(AttackBlock)` расходует C RNG. Availability probe проверяет конкретные block clips и сохраняет native RNG. Финальное сравнение native off/on проверяет пассивность по ordered боевому trace.

NPC могут оставаться в `npcRemoved` после удаления из активного мира. Снятие наблюдения выполняется при remove, а не откладывается до деструктора. Каждая подписка/reset меняет observation epoch; каждый процесс имеет отдельный session ID.

Телепорты privacy fixture явно инвалидируют производную скорости. После повторного появления цели первый velocity также не считается достоверным; скрытые движения не обновляют last-known state.

Первый короткий прогон обнаружил ошибку oracle: координаты команды setup сравнивались со снимком следующего тика, в котором настоящий root motion уже сдвинул цель. Отдельный privileged `body_observe_probe_sample` теперь снимается в той же фазе и substep, что actor input; допуски не увеличены. Повторный короткий прогон подтвердил все четыре cases, корректные данные и точный учёт overflow.

Availability mask остаётся информационной. Lease, окончательные masks, executor results и управление телом относятся к этапу 4; GRU и обучение отсутствуют.
