# Аудит всех кандидатов и фиксированной selection

Завершены все 6 кандидатов. Веса зафиксированы 2026-10-14T09:02:35.266523+00:00; selection SHA `b8ae94660743319462ce6e24216f83e64fbe104e15885e08a1f8b0605b9022c0`. Полные SHA, проверки и confusion matrices: [candidates.json](candidates.json). Сохранённые prediction rows отдельно сверены в [predictions.json](predictions.json).

| Модель | Seed | Эпоха | Accuracy | Macro F1 / 11 | NLL | Ложный wait | Ложный strafe |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gru | 17 | 32 | 0.968153 | 0.354386 | 0.237208 | 0 | 0 |
| gru | 29 | 26 | 0.968153 | 0.354386 | 0.182613 | 0 | 0 |
| gru | 43 | 6 | 0.968153 | 0.345533 | 0.199111 | 5 | 0 |
| mlp | 17 | 11 | 0.936306 | 0.327330 | 0.424773 | 0 | 2 |
| mlp | 29 | 8 | 0.929936 | 0.321964 | 0.287877 | 1 | 2 |
| mlp | 43 | 7 | 0.949045 | 0.332400 | 0.275238 | 1 | 1 |

Выбран GRU seed 29 / epoch 26, checkpoint `44727b7c99211c6a5e79b4f9aead852c33e277f6cee5dc4bbed95bd86af15c7c`; MLP seed 43 / epoch 7, checkpoint `21c81594817959490e2c4d8032d7a75296e878830ae34a89af6e56f0b084d5bf`. Это лучшие кандидаты по максимальному fixed macro F1, затем минимальному unweighted NLL, только fresh eval 223/227. Config/profile/dataset/normalizer SHA, точные eval IDs/groups, все три seeds, per-arch selection и побайтные копии опубликованных артефактов сверены.

У выбранного GRU: approach 67/67, turn 23/23, front attack 55/59, wait200 7/8. Precision всех четырёх представленных классов — 1. Ошибки: 4 front attack → side attack (1 left, 3 right), 1 wait200 → right attack. Ложных ожиданий и strafe на этих размеченных fresh-eval состояниях нет.

У выбранного MLP: approach 65/67 (P=1), turn 23/23 (P=1), front attack 56/59 (P≈0,966), wait200 5/8 (P≈0,833). Есть 1 ложный wait200 вместо front attack и 1 strafe_left вместо approach.

Все 19 проверок каждого завершённого кандидата прошли: checkpoint/sidecar, dataset/normalizer/helper SHA, canonical embedded normalizer digest, порядок 184 признаков/12 действий, train episode IDs, config, лучшая эпоха по F1 затем NLL, continuous eval без artificial resets/duplicate burn-in labels, 12 frozen model-source hashes, supports и метрики из confusion.

Fresh eval содержит только четыре поддержанных класса; верхняя граница fixed F1 — 4/11. Состояния без loss-label и защитная реакция требуют отдельной проверки. Результаты test/OOD в [predictions.md](predictions.md) прочитаны после фиксации весов и не участвовали в выборе. Игры, модели и тесты этим агентом не запускались; fresh-screen gameplay не открывался.
