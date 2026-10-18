# Этап 8: остановка на preflight

Статус: **исторический журнал этапа 8 до 14 октября 2026 года.** Остановка на preflight снята: 15 октября боевой gate пройден (gate v16, [отчёт](../reports/npc-body/2026-10-15-gate-v16/README.md)), итог — в [плане, этап 8](npc-body-control-plan.md#этап-8-управление-в-headless-арене-и-разбор-ошибок). Рабочая модель боя теперь P30 (этап 9), а не веса отсюда.

Статус на 14 октября: **STOPPED_AT_PREFLIGHT**, 14 октября 2026 года. После одного
дополнительного SafePeer preflight работа остановлена по ограничению
пользователя. Боевой допуск GRU не получен. Повторных исправлений, калибровок,
сбора данных, обучения и gate-запусков не назначено.

Единственная попытка — `stage8-visibility-attempt-v1`. Все шесть эпизодов
закрыты с `time_limit` на 4000 игровых мс без infrastructure failure, HP300
сохранены. Во всех захваченных numeric рядах primary цель visible/current valid и
melee line clear; все 1002 mapped native visibility diagnostics primary также true. Физически
одобренное новым verifier покрытие не прошло:

| Действие | Approved | Требование preflight |
| --- | ---: | ---: |
| TurnToTarget (2) | 0 | 2 |
| JumpBack (5) | 2 | 2 |
| Wait300 (11) | 0 | 2 |

114 целевых тестов прошли. Завершение эпизода и сохранение HP не заменяют
causal command → accepted/started/completed → физическое доказательство
результата. Неодобренные команды остаются диагностикой; они не подставлены в
обучающие метки. Сбор72, объединённый child279, пересчёт normalizer и новое
обучение не запускались. Для этой попытки число новых GRU боевых gate — **0**;
fresh screening из240 эпизодов не запускался ни разу.

Разбор исходных журналов установил ограничения нового proof-контракта:

- Turn2 реально завершился шесть раз на side0 и пять на side1. Семь решений
  отклонены потому, что yaw physics пишет `movealgo_tick=false`: эта ветка
  поворачивает NPC без `MoveAlgo::tick`. Ещё четыре не имеют numeric снимка
  ровно в момент завершения; следующий настоящий periodic снимок приходит
  через33 игровых мс. Потери HP, видимости или gap здесь не обнаружено.
- Wait11 реально завершился дважды на каждой стороне: 333 игровых мс,
  неподвижность, collision0. Новый verifier требует
  `controller_segment_completed`, который ветка завершения Wait не пишет.
  Все четыре команды отклонены по отсутствующему маркеру.
- Первый Jump5 на каждой стороне одобрен: causal anchor в WR, реальный
  отскок около127 см, прирост расстояния около66,5 см и настоящий mapped
  `out_of_range` hit attempt. Второй отскок из outWR не имел нового hit
  attempt и обоснованно не получил экспертную метку.

Это отказ предрегистрационного доказательства, а не установленная потеря
видимости в калибровке или поломка Turn/Wait. Proof, teacher и runtime после
отказа не исправляются и не перезапускаются в рамках этой попытки.

[SHA-bound causal audit](../reports/npc-body/2026-10-14-combat/visibility-attempt-v1/calibration-causal-audit.json)
содержит каждую команду шести сцен, четыре frozen source hash, suite/registry
и manifest/records/events/IPC каждой сцены. Все привязанные source-файлы
перепроверены; native visibility — 1002 строки, отказов0. Audit SHA:
`e7036a233bf6762fe33c1aa9b1334f417e67a0232bc4caf09d18d83a618a8ce9`.
Исходный suite SHA:
`e8a282533a37215c17d7dbd7d8b72e4ca2683cae5c0925241f0833d5a16be106`.

Сохраняется выбранный child `stage8-defence-bc-v2`: 207 эпизодов
(148 train /20 eval /19 test /20 OOD), train1104 метки. Шесть BC кандидатов
обучены ранее, независимый [числовой аудит](../reports/npc-body/2026-10-14-combat/defence-v2/README.md)
прошёл 3867 привязок. Веса зафиксированы 2026-10-14 11:15:23.540769 UTC:

- GRU: seed11, epoch25,
  `e9a605b4ae15e30ad7e2dd632c10c882c56f98b19f40cace3d8b2343a693b3eb`.
- MLP: seed11, epoch14,
  `d17752fd0fc765f2ab841eec107f0a8ccbf77fd5db83ceabbee866e33a1073c9`.
- Датасет:
  `e61890483f66166e9717d700602c9469a30e6f3bd2531462f94fef6dd17542a8`.
- Normalizer:
  `c6ee3acdea6a065c04f6de2e88eb4fe7af189d50aee3c699f925ffadbd7438fe`.

[Motor20](../reports/npc-body/2026-10-14-combat/motor-v20/README.md),
[Queue21](../reports/npc-body/2026-10-14-combat/motor-v21-queue/README.md) и
[known Gate11](../reports/npc-body/2026-10-14-combat/gate-v11/README.md)
прошли свои проверки. Known закрыл3/24/6, оба GRU reaction блока настоящие:
HP300, control1, fallback0. Эти результаты не отменяют
[load Gate11 FAIL](../reports/npc-body/2026-10-14-combat/gate-v11/load-failure.md):
GRU0/2/3 потеряли target visibility после завершённого RightStrafe4/Idle,
без активного lock; control19/24,32/35,75/78. GRU1 завершил20с с153/158.
Partial MLP0 получил `target_invalid` при той же lease, затем explicit release
завершил арену infrastructure. Fallback0 не означает непрерывное владение
после native handoff. Все прежние неуспешные прогоны и raw сохранены.

Protected service оценка отделена от боевого допуска. В здоровом native20/20
окне Gate11 ASR/TTS прошли бюджет10%; Decider/Gemma дали
+10,59663%/+31,21631% и не прошли. GRU populations16/15/20/20 невалидны из-за
сокращённых окон и не получают зачёт. Четыре actual PID before/after совпали;
restart не заявляется. Бюджет10% и результаты сервисов не изменены.
Отдельные [service report](../reports/npc-body/2026-10-14-combat/service-separation/README.md)
и [175 целевых тестов](../reports/npc-body/2026-10-14-combat/service-separation/validation/README.md)
сохранены; quality/runtime словари обоих конфигов полностью совпадают с WIP f17e5d6.

Strict masks, freshness, visibility100 и боевые thresholds сохранены;
потеря видимости не объявлена исправленной. Все модели и симуляции выполнялись
на `dev-ubuntu`; Mac использовался для разработки и документации.

WIP `f17e5d625fea643ed463b9f2aab10b3498c2b4f6` сохранил изменения этапов4–8.
SafePeer teacher/collector/config/test сохранены как WIP; suite/report/config
нового BC pipeline279 не реализованы. Итоговая документация и разделение service gate сохраняются отдельным
завершающим WIP-коммитом. Продолжение требует нового указания пользователя.
