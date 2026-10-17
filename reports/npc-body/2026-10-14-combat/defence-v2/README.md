# Child BC v2: выбранные веса и числовой аудит

Независимый numerical audit v3 — **PASS**, 3867 привязанных файлов.
Источник: `/home/kaifat/gothic-ai/body/data/stage8-defence-bc-v2`.
Проверены исходные approvals, непрерывные последовательности, train-only
нормализация, все шесть кандидатов и сохранённые predictions. Отчёт описывает
обучение и предсказания меток; боевой gate этапа 8 ещё выполняется.

В корпусе 207 эпизодов: 148 train, 20 eval, 19 test, 20 OOD. Все 72 новых
reactive-block эпизода имеют завершённую команду 9 и настоящий предотвращённый
удар. Train содержит 1104 метки, eval — 169; eval поддерживает пять из 11
классов. Подготовка остаётся в raw, model sequence начинается с реального
prepared anchor при dt0.

Веса зафиксированы **2026-10-14 11:15:23.540769 UTC**. Выбор — только по
непрерывному eval, macro F1, затем unweighted NLL; test/OOD не выбирают веса.

| Модель | Seed | Эпоха | Warm CPU step p95, мс | Tiny overfit accuracy |
| --- | ---: | ---: | ---: | ---: |
| GRU | 11 | 25 | 0,132028 | 1,0 |
| MLP | 11 | 14 | 0,117020 | 1,0 |

GRU checkpoint: `e9a605b4ae15e30ad7e2dd632c10c882c56f98b19f40cace3d8b2343a693b3eb`.
MLP checkpoint: `d17752fd0fc765f2ab841eec107f0a8ccbf77fd5db83ceabbee866e33a1073c9`.

| Сохранённый split, GRU | Верных labels | Accuracy | Правильных block labels | Ложных block labels |
| --- | ---: | ---: | ---: | ---: |
| Eval | 164/169 | 0,970414 | 12/12 | 0 |
| Test | 74/92 | 0,804348 | 17/18 | 0 |
| OOD | 117/156 | 0,750000 | 15/18 | 0 |

GRU eval macro F1 по фиксированным 11 классам — 0,445295, NLL — 0,141480.
Метрики относятся к сохранённым teacher labels. Собственная защитная реакция
и task success моделей проверяются отдельно после фиксации checkpoint.

Шесть CUDA jobs завершены: общий GPU user unit — 23 мин 7,662 с; CPU evaluate —
1 мин 15 с. Профиль фиксировал 120 эпох, patience25 и allocator cap 1024 MiB.
Независимый GPU monitor сохранил 681 sample: peak памяти наших процессов
472 MiB, минимум свободной VRAM 4646 MiB. Все четыре защищённых сервиса
остались active/running с прежними PID. Модели на Mac не запускались.

[Selection](selection.json), [аудит](audit/report.md) и
полный evidence JSON (1,7 МБ, в репозитории не хранится: dev-ubuntu `~/gothic-ai/body/data/stage8-defence-bc-v2/validation/defence-audit-v3/report.json`) содержат точные bindings.
Датасет SHA: `e61890483f66166e9717d700602c9469a30e6f3bd2531462f94fef6dd17542a8`;
normalizer SHA: `c6ee3acdea6a065c04f6de2e88eb4fe7af189d50aee3c699f925ffadbd7438fe`.
Проверенный reporter v3 SHA:
`d2cffbe93415f1afe2ff2eab30c479201707e86a1c193aa7ee9a318ef95dcd10`.
Предыдущие отказы reader сохранены в `failed-audit/` и `failed-audit-v2/`.

Отдельный gate-v1 дал реальные отказы reaction/load; gate-v2 остановился
на infrastructure подготовки reaction. Fresh screening из 240 эпизодов
ни разу не запускался. Gate-v3 ждёт compact/Motor-v13 proof с прежними
весами; числовой аудит не даёт closed-loop quality credit.
Этап 8 — **IN_PROGRESS**. [Текущий статус gate](../../../../docs/npc-body-combat-gate.md).
