# Этап 5: headless арена и runner эпизодов

**Готов, 13 октября 2026 года.** Полная серия `stage5-v2` выполнена на `dev-ubuntu` в одном CPU runner. В текущем worktree реализованы reset из контрольного save, запуск и продвижение эпизода в lockstep, scripted provider, боевые микроэпизоды, парное сравнение с native AI и измерение скорости. [План](../../../docs/npc-body-control-plan.md) обновлён; [интерфейс и запуск](../../../docs/npc-body-arena.md) описаны отдельно.

Проверка основана на фактических IPC, arena/control/mechanics journals и числовых наблюдениях. Независимый аудит подтвердил SHA-256 всех 650 исходных журналов, 3113 файлов движка в архиве и связь отчёта с исполняемым бинарником. [Машинный отчёт](body-arena.json), [краткие метрики](validation-summary.json), [manifest](manifest.json) и [автоматический отчёт](body-arena.md) сохранены рядом.

## Reset и сценарии

Серия содержит 130 записей арены: 100 циклов изменения состояния и reset, один завершающий контрольный reset, пять микроэпизодов, 12 парных дуэлей и 12 эпизодов throughput. Все использовали один runner PID `3236185`.

Перед каждым stress reset настоящий удар уменьшал HP противника на 70, jump back менял позицию, а NPC оставался под scripted управлением с активным wait. Все 101 начальных состояния совпали по контрольным HP, экипировке, позициям, направлениям, целям, очередям, waits, позе и состоянию управления. Runtime IDs/epochs менялись; адаптер очищал кеш наблюдений, command IDs, leases и recurrent token. Стресс-эпизоды намеренно прерывались reset и имеют `infrastructure/reset_requested`; это проверка прерывания, их не считают обычными боевыми исходами.

Воспроизводимость относится к начальному семантическому состоянию при одинаковых seed/setup. Побитовая идентичность всего мира и полной дальнейшей дуэли не заявляется. До `arena_start` участники стоят в подготовленном состоянии; после старта события получаются из настоящей симуляции без перенастройки бойцов.

| Проверка | Фактический результат |
|---|---|
| Дистанция | 207,334 см, условие ≤220 см удержано 200 игровых мс |
| Атака | Native melee damage 70; HP противника 300 → 230 |
| Защита | Один настоящий заблокированный удар; HP обоих остались 300 |
| Native terminal | Противник down с HP 0; primary HP 230 |
| Лимит времени | `time_limit` на 33 игровых мс, без победителя |
| Legacy baseline | Seeds 17/29/43: 0 различий, 1077 ordered mechanics events и 3198 physics steps; drift позиции/yaw 0 |

Baseline сравнивается с сохранённым native-off архивом этапа 3, отдельно от новой арены. Этот reference не упражняет `def_par_window`; совпадение его trace не расширяет покрытие такой механики.

У парных дуэлей совпадают seed и состояние до acquire; стороны физически меняются местами. На seeds 17/29/43 и двух сторонах native primary выиграл 4 дуэли и проиграл 2. Scripted primary: 0 побед, 2 поражения, 1 time limit, 3 `control_lost` из-за потери цели из вида. Эти результаты проверяют среду и показывают ограничение текущего scripted provider; они не оценивают будущую GRU.

`control_lost` фиксируется по настоящему ownership event, пока оба бойца живы, после simulation step. Три таких фрагмента исключены из будущих actor updates; продолжение native AI не засчитывается candidate. Финальное наблюдение снимается до cleanup/reset. Смерть, успешная задача, time limit, потеря управления и infrastructure failure различаются.

## Throughput

Измерены 12 scripted дуэлей с начальной дистанцией 150 см и лимитом 10 игровых секунд. Все закончились `time_limit`; `control_lost` в этой серии не было. Из 178 принятых команд 176 действительно начали native segment: 138 завершились и 38 были отменены с связанным outcome. Две принятые, но не начавшиеся команды исключены. Locked ticks, `continue`, heartbeat и status не считаются policy decisions.

| Метрика | Измерение |
|---|---:|
| Реальные transitions, диагностические / пригодные для actor update | 176 / 176 |
| Исключённые benchmark эпизоды / transitions | 0 / 0 |
| Wall time с reset, IPC и local provider | 71,228 с |
| Transitions/s с reset | **2,471** |
| Transitions/s только rollout | 3,038 |
| Episodes/s с reset | 0,168 |
| Substeps/s с reset | 50,542 |
| Reset p50 / p95 в throughput серии | 1,094 / 1,163 с |
| Reset p50 / p95 в 100 stress циклах | 1,107 / **1,168 с** |

Начальный бюджет среды — 32768 решений не дольше 6 часов, минимум 1,517 transitions/s. По измеренному end-to-end throughput этот критерий пройден.

| Число решений | Прогноз времени сбора при измеренной скорости |
|---:|---:|
| 32768 | 3 ч 41 мин |
| 100000 | 11 ч 14 мин |
| 1000000 | 4 дня 16 ч 25 мин |

Это линейный прогноз для данного scripted workload на одном runner. Он включает reset и IPC, исключает будущие GRU inference, optimizer и запись датасета этапа 6. Scripted траектории сами по себе не являются on-policy PPO датасетом. Нейросеть и обучение в этом этапе не запускались. Для будущих серий скорость пригодных transitions считается с учётом всего benchmark wall time, включая время на исключённые `control_lost` фрагменты.

## Нагрузка и проверка

Полная серверная серия заняла 449,68 с; CPU user/system — 737,07 / 34,17 с, peak process RSS — 736,20 MiB, swap — 0. В 266 `/proc` снимках runner имел 17 потоков и 8–10 файловых дескрипторов; GPU, display и audio descriptors отсутствовали. Systemd показал peak cgroup memory 2,9G; эта величина включает память группы и не равна RSS процесса.

Runner запущен отдельным пользовательским transient unit с CPUQuota 200%, MemoryMax 12G, Nice 10 и общим simulation lock. ASR/TTS оставались `active/running` с теми же MainPID до и после; продовые контейнеры и Docker не менялись. Это проверка состояния сервисов, измерение их p95 latency под совместной нагрузкой относится к будущей интеграции модели.

Целевые проверки прошли: 2419 native resolver assertions, 11 Python adapter tests и 53 reporter tests. Python и симуляция выполнялись последовательно. Независимый аудит также сверил все 260 actor damage/block summaries с сырыми HP/events и проверил шесть физических зеркальных setup. Полные тесты проекта и GUI e2e в этом этапе не запускались.

## Исходники и повторная проверка

Архив полной серии: `~/gothic-ai/body-arena/runs/stage5-v2/` на `dev-ubuntu`. Там сохранены source archive, собственный бинарник, конфигурация, manifest и исходные журналы. Base Git HEAD — `979d343354ea7bf711a53fce1331c83b31080a27`; изменения текущего worktree входят в архив, этот HEAD не обозначает отдельный commit реализации.

| Артефакт | SHA-256 |
|---|---|
| Runner binary | `d2895c9bbb3b994b2c21b27b24af8a0e620c5b3b56fb60f317fffa79c8b18ce1` |
| Engine source digest | `9d75c6d4658ffe5d71d57c87750ae9106ea388816baab656f7f465de6468fc64` |
| Source archive | `d605a42f13ce4d6e5661ce319ee187c8726720d30d571ac3f62f00ca575219ba` |
| Config | `e331a23ad53cdf7ae525486a43f4a5e0a88f11b738c5d5008f3ae3bce5bc8e97` |
| Final reporter | `3bf81cad01d42b29611a52f665b9f8ef1c91296c4e52891804d0cfebac0504a7` |

После серии reporter уточнён: диагностические transitions и transitions из пригодных для actor update эпизодов показываются отдельно, а feasibility и прогноз используют вторые. Из-за отсутствия `control_lost` в throughput серии численные результаты не изменились. Исходный архив, журналы и первоначальный reporter (`957254e2c206b2433ab1e640f22347bfe0558de4e37ddc34ed41662ca7198a05`) сохранены. Финальный отчёт создан отдельно в `~/gothic-ai/body-arena/reports/stage5-v2-final/`; его код, helpers и configs зафиксированы в подпапке `code/`.

Повторная проверка на сервере читает существующие журналы и не запускает симуляцию:

```sh
python3 ~/gothic-ai/body-arena/reports/stage5-v2-final/code/scripts/body_arena_report.py \
  ~/gothic-ai/body-arena/runs/stage5-v2 \
  --native-reference ~/gothic-ai/body-headless/runs/stage3-v1/native-off \
  --output-dir ~/gothic-ai/body-arena/reports/stage5-v2-recheck
```

Следующий этап плана — recorder, валидируемые траектории и датасет на сервере. Бюджет большого сбора следует пересчитать после добавления recorder и inference.
