# Этап 7: GRU, MLP baseline и BC

Проверки выполнены на `dev-ubuntu`, run `stage7-bc-v9`, исходные демонстрации `stage7-bc-v4`. [Независимый audit](body-bc.json) подтвердил **implementation_ready=true, checkpoint_ready=false**. [План](../../../docs/npc-body-control-plan.md) сохраняет quality gate непройденным; следующий разбор — этап 8.

## Данные и физическая механика

Добавлены 37 эпизодов: 25 train, 5 eval, 5 test, 2 OOD. Scripted teacher демонстрирует подход, поворот, фронтальный/левый/правый удар, блок, отскок и ожидание 300 мс. Три эпизода используют существующую стену мира, по одному в train/eval/test. Боковые шаги и ожидание 200 мс сохраняются из настоящих native labels.

Каждый expert label проверен через исходное periodic observation, команду, acceptance, фактические started/completed и физический результат. Для отскока проверены реальное смещение, рост дистанции, unchanged HP и связанные `defence_result.jumped_back`. Для wait300 — не менее 300 мс игрового времени, неподвижность и безопасная дистанция. Для стены — native capsule/ground/LOS proof существующей геометрии. Успех не выводится из одного `accepted`.

Исходный collector завершил запись, закрыл engine и утвердил все 37 эпизодов; затем общий unit достиг лимита времени во время повторной inline сборки датасета. Исходные данные и статус сохранены. Offline recovery отдельно проверяет source/config/binary SHA, исходные full snapshots, compact recorder final, физические сертификаты и объявленные splits. Нулевой exit исходного engine — вывод из проверенного frozen collector control flow и закрытого IPC, а не новый измеренный exit. Для исходного timeout process RSS/exit недоступны; новые recovery/dataset фазы измерены отдельно.

Объединённый датасет содержит 60 эпизодов: train40/eval9/test7/OOD4, usable labels480/94/80/60. Нормализация рассчитана по 33 210 train observations, class weights — только по train labels; assignments `(setup_family, seed)` наследуются из этапа 6. Обе модели используют одну causal periodic view, общий whitelist из 184 признаков и action masks.

| Действие | Usable train labels |
|---|---:|
| approach | 160 |
| turn_to_target | 26 |
| strafe_left | 31 |
| strafe_right | 30 |
| jump_back | 25 |
| attack_front | 41 |
| attack_left | 35 |
| attack_right | 37 |
| block | 37 |
| wait_200 | 33 |
| wait_300 | 25 |
| **Всего** | **480** |

После alignment минимальное покрытие — 25 при gate 20. В train есть 16 wall observations и 696 наблюдений потери видимости; независимая проверка нашла потерю видимости до terminal при живых участниках. Test `wait_200` имеет один пример, поэтому его оценка ограничена.

## Архитектуры и численные проверки

GRU: MLP-256 → GRU-256 → MLP-128 → 12 masked logits. MLP: стек восьми наблюдений → 256/256/128 → тот же head. Float32, CPU сервера, два потока; CUDA отключена.

| Модель | Параметров | Warm step p95, мс |
|---|---:|---:|
| GRU | 476 556 | 0,1363 |
| MLP | 477 324 | 0,1235 |

Замер: 100 прогревочных и 500 измеряемых вызовов. Обе модели прошли step/sequence equality, reset, actor isolation и mask invariants. Tiny overfit на 18 train labels со всеми 11 классами достиг 100%: GRU за 30 steps, MLP за 20. Это отдельная проверка pipeline, её веса не являются кандидатами. Время model step измерено без IPC и симуляции. Целевые Python/model проверки: **200 passed** на сервере. Native resolver и setup/parity проверены в `stage7-bc-v3`; SHA runner совпадает с v9: `2b1252bd6943e59886c8a9f7f6617088143fce59dc2d180881ebd6a9fbab812e`.

## Обучение и сравнение

Конфигурация зафиксирована до измерений в `evals/npc-body/body-bc-v1.json`: BC, AdamW, train-only class-weighted CE, chunks32/prefix8, gradient clipping, до 120 epochs и три seeds каждой архитектуры. Epoch и seed выбираются только по eval macro F1, затем NLL; test/OOD не участвуют в выборе. Tiny overfit имеет отдельные веса и не допускается к управлению NPC.

Все шесть training jobs завершились. Eval выбрал GRU seed29/epoch11 и MLP seed43/epoch6. Общая training фаза заняла 540,907 с, CPU time763,859 с, cgroup memory peak1,6G. После выбора весов выполнена отдельная оценка test/OOD.

| Модель | Eval macro F1 | Test macro F1 | OOD macro F1 | Test accuracy |
|---|---:|---:|---:|---:|
| GRU | 0,7653 | 0,7436 | 0,5478 | 0,7375 |
| MLP | 0,7331 | 0,7304 | 0,6199 | 0,7250 |

Macro F1 считает все 11 классов; per-action precision/recall и confusion matrix сохранены отдельно. Это решения на зафиксированных траекториях; качество управления после собственных ошибок проверяется в 54 парных native/GRU/MLP эпизодах. Признаки допуска implementation/checkpoint оцениваются раздельно.

## Замкнутая арена

Все 54 объявленных эпизода завершены, как и предварительный smoke. Native primary выполняет настоящий AI, микрозадачи используют одинакового scripted противника; в дуэлях противник native. Возврат к native и `control_lost` не дают learner credit.

| Задача | Native success | GRU success | MLP success |
|---|---:|---:|---:|
| Дистанция | 4/4 | 4/4 | 4/4 |
| Атака | 4/4 | 4/4 | 4/4 |
| Защита | 4/4 | 4/4 | 0/4 |
| Дуэль, включая стену | 6/6 | 0/6 | 1/6 |

GRU завершила все шесть дуэлей с `control_lost/target_visibility_lost`; MLP — пять с таким исходом и одну с настоящей победой. Диагностический gate GRU (success delta к native не ниже −0,15 по каждой задаче) **не пройден** в дуэлях. Checkpoint остаётся экспериментальным; следующая работа — диагностика и demonstrations этапа 8.

Начальный разбор двух GRU дуэлей seed29/side0–1: последний `strafe_right` был принят на свежем periodic observation при дистанции около 221 см. Затем NPC сместился примерно на 282 см вбок, видимость пропала во время защищённого сегмента. Runtime продолжил обновлять память без запрещённых команд; после фактического completed native guard восстановил владельца. Runtime fallback events отсутствуют, fraction команд на доступных periodic opportunities равна 1,0. Это повод расширить demonstrations безопасного маневрирования на посещаемых learner состояниях и проверить геометрию шага на этапе 8. Веса и masks не менялись ради улучшения этой оценки.

У side1 последние 27 physics rows оставались grounded, collision failure не было. Native perception использует направление взгляда BIP01 и ray checks головы/центра, а observation содержит body yaw; поэтому body bearing сам по себе не объясняет потерю видимости. Точный BIP/LOS reason не инструментирован. Перед изменением recovery grace или новым добором стоит добавить этот diagnostic и сопоставить native/neural исполнение полного бокового шага. По двум проверенным эпизодам serialization/sampling/TTL/hidden-state ошибки не обнаружены; обобщать это на все возможные состояния нельзя.

## Независимый audit

Первый reporter остановился на строгом сравнении native journal и model input: в native journal числа записаны через `%.17g`, RPC разбирает и повторно сериализует их через RapidJSON. Во всех 36 neural episodes 3 208 model/arbitration rows точно совпали с фактическими IPC inputs. Из 6 689 native/wire rows обнаружено 10 478 float leaf расхождений: 10 449 в один ULP и 29 в два ULP. IDs, masks, структура и прочие значения совпали точно.

Новый read-only reporter зафиксирован отдельно в `validation/audit-code` и связывает model input с точным wire row; raw journal сравнивается с ним по строгой структуре и finite-float пределу 2 ULP. Исходные source/config/helper/archive/binary/failed-report SHA остаются обязательными. **42 auditor tests passed**. Прежний [failed report](initial-body-bc.json) и frozen code сохранены; [audit manifest](audit-source-manifest.json) связывает новый код с исходным экспериментом. Дополнительный stdlib replay упаковки всех 2 930 model inputs подтвердил logged feature SHA и **ноль изменений нормализованных float32 входов**. Симуляции, обучение и выбор весов после этой правки не повторялись. Повторный audit завершён с exit0 за 167,279 с; implementation issues отсутствуют.

## Ресурсы и воспроизведение

Исходники и запуск: [workflow](../../../docs/npc-body-bc.md), [launcher](../../../scripts/body-bc-remote.sh), [body-service](../../../body-service/README.md). Raw trajectories, predictions и checkpoint weights остаются в `~/gothic-ai/body/data/stage7-bc-v9/` на сервере.

User units ограничены CPUQuota200%, MemoryMax6G, TasksMax128. Игровые фазы и pytest используют общую simulation очередь; offline фазы — отдельную training очередь. ASR/TTS/Decider/Gemma identities проверяются до/после каждой фазы. Docker, production containers и service environments не менялись.
