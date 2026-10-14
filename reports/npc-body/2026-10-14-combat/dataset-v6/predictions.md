# Аудит сохранённых prediction rows

Все 6 итоговых eval/test/OOD evaluation artifacts прошли проверку после фиксации весов 2026-10-14T09:02:35.266523+00:00. Selection SHA `b8ae94660743319462ce6e24216f83e64fbe104e15885e08a1f8b0605b9022c0`. Полные bindings, SHA prediction/metrics files, per-action metrics и 23 исходных episode manifest/records bindings: [predictions.json](predictions.json).

| Модель | Split | Labels | Accuracy | Macro F1 / 11 | NLL | Ложный wait | Ложный strafe |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gru | eval | 157 | 0.968153 | 0.354386 | 0.182613 | 0 | 0 |
| gru | test | 80 | 0.737500 | 0.718259 | 1.466553 | 2 | 10 |
| gru | ood | 144 | 0.750000 | 0.588359 | 1.293259 | 11 | 15 |
| mlp | eval | 157 | 0.949045 | 0.332400 | 0.275238 | 1 | 1 |
| mlp | test | 80 | 0.725000 | 0.692774 | 0.743762 | 3 | 15 |
| mlp | ood | 144 | 0.756944 | 0.642814 | 1.131800 | 7 | 14 |

Для каждого prediction сверены exact episode/actor generation/observation identity, причинно выровненная экспертная метка и decision ID, t_game_ms, original observation sequence, raw availability и общая policy mask. Метки восстановлены из SHA-bound canonical records через frozen periodic_model_view; повторов, пропущенных либо придуманных loss-rows нет. Masked entries имеют null log-probability, разрешённые — конечные значения с суммой exp≈1. Выбранный primitive — разрешённый masked argmax; class weight совпадает с train normalizer.

Confusion, accuracy, precision/recall/F1 и fixed macro F1 пересчитаны из JSONL. NLL и weighted NLL пересчитаны из log-probabilities, допуск к агрегатам 1e-5 учитывает float32 суммирование. Все identity, mask и argmax проверки прошли строго. Артефакты созданы после weights_fixed_at; checkpoint SHA совпадает с selection. Eval содержит только fresh seeds 223/227.

На размеченном fresh eval выбранный GRU не даёт ложных wait/strafe. На test он имеет 2 ложных wait и 10 strafe; на OOD — 11 wait и 15 strafe, включая 5 front attack → wait200. Эти ошибки ограничивают выводы о боевой устойчивости. MLP имеет более высокий OOD macro F1 при фиксированном выборе GRU по fresh eval. Macro F1 разных splits напрямую не сопоставим без учёта отсутствующих классов.

Этот аудит использует уже проверенные canonical records и их SHA; полная повторная проверка исходных engine/teacher доказательств остаётся задачей independent reporter. Ни inference, ни обучение, ни игры здесь не запускались. Test/OOD прочитаны только после фиксации весов; screening gameplay и service-load результаты не открывались.
