# Результаты

`react ✓` — выбран эталон или приемлемый вариант; `react =` — ровно эталон; `≥0.9` — ответы с уверенностью от 0.9: сколько их и сколько из них приемлемы; `think` — точность «звать ли большую модель» (в скобках: найдено случаев «да» / ложных «да»); `attitude ✓` — уровень в приемлемых, `±1` — не дальше одного уровня; `extra` — выбор параметра.

| модель | условие | react ✓ | react = | ≥0.9: доля / верно | think | attitude ✓ | ±1 | extra |
|---|---|---|---|---|---|---|---|---|
| gpt-6.1-sol-low | en | 99% | 92% | 100% / 99% | 87% (20/24, 6/52) | 95% | 100% | 6/6 |
| laya | en | 58% | 53% | 13% / 70% | 68% (1/24, 1/52) | 50% | 82% | 3/6 |
| laya | ru | 45% | 42% | 33% / 56% | 67% (0/24, 1/52) | 32% | 92% | 4/6 |
| laya | en_rev | 54% | 49% | 14% / 64% | 68% (1/24, 1/52) | 50% | 82% | 2/6 |
| openjev-2b | en | 80% | 72% | 7% / 100% | 51% (23/24, 36/52) | 75% | 87% | 3/6 |
| openjev-2b | ru | 72% | 67% | 8% / 100% | 50% (23/24, 37/52) | 66% | 84% | 5/6 |
| openjev-2b | en_rev | 80% | 72% | 7% / 100% | 51% (23/24, 36/52) | 75% | 87% | 3/6 |
| openjev-4b | en | 83% | 74% | 11% / 100% | 61% (19/24, 25/52) | 71% | 95% | 5/6 |
| openjev-4b | ru | 79% | 68% | 12% / 100% | 57% (23/24, 32/52) | 68% | 93% | 5/6 |
| openjev-4b | en_rev | 83% | 74% | 11% / 100% | 61% (19/24, 25/52) | 71% | 95% | 5/6 |
| service-g5 | en | 97% | 87% | 49% / 100% | 57% (24/24, 33/52) | 87% | 96% | 5/6 |
| service-g5 | ru | 95% | 84% | 47% / 100% | 57% (24/24, 33/52) | 87% | 96% | 5/6 |
| service-g5 | en_rev | 97% | 88% | 43% / 100% | 57% (24/24, 33/52) | 87% | 96% | 5/6 |
| service-g7 | en | 95% | 84% | 55% / 100% | 54% (24/24, 35/52) | 83% | 95% | 6/6 |
| service-g7 | ru | 96% | 84% | 43% / 100% | 57% (23/24, 32/52) | 84% | 95% | 6/6 |
| service-g7 | en_rev | 96% | 84% | 54% / 100% | 54% (24/24, 35/52) | 83% | 95% | 6/6 |
| service-g8b | en | 99% | 87% | 54% / 98% | 57% (24/24, 33/52) | 80% | 95% | 5/6 |
| service-g8b | ru | 97% | 88% | 53% / 98% | 54% (24/24, 35/52) | 84% | 97% | 5/6 |
| service-g8b | en_rev | 97% | 86% | 50% / 100% | 57% (24/24, 33/52) | 80% | 95% | 5/6 |
| service-v1-2610 | en | 86% | 76% | 21% / 100% | 53% (24/24, 36/52) | 80% | 91% | 4/6 |
| service-v1-2610 | ru | 80% | 68% | 20% / 100% | 45% (24/24, 42/52) | 80% | 89% | 4/6 |
| service-v1-2610 | en_rev | 89% | 79% | 21% / 100% | 53% (24/24, 36/52) | 80% | 91% | 4/6 |
| strands | en | 86% | 78% | 12% / 100% | 53% (23/24, 35/52) | 82% | 92% | 5/6 |
| strands | ru | 71% | 64% | 12% / 100% | 42% (24/24, 44/52) | 80% | 95% | 5/6 |
| strands | en_rev | 83% | 74% | 8% / 100% | 53% (23/24, 35/52) | 82% | 92% | 5/6 |
| strands-fast | en | 86% | 78% | 12% / 100% | 53% (23/24, 35/52) | 83% | 93% | 5/6 |
| strands-fast | ru | 70% | 63% | 13% / 90% | 42% (24/24, 44/52) | 80% | 95% | 5/6 |
| strands-fast | en_rev | 83% | 74% | 8% / 100% | 53% (23/24, 35/52) | 83% | 93% | 5/6 |
| strands-v1-2610 | en | 86% | 76% | 21% / 100% | 53% (24/24, 36/52) | 80% | 91% | 4/6 |
| strands-v1-2610 | ru | 80% | 68% | 20% / 100% | 45% (24/24, 42/52) | 80% | 89% | 4/6 |
| strands-v1-2610 | en_rev | 89% | 79% | 21% / 100% | 53% (24/24, 36/52) | 80% | 91% | 4/6 |
| strands-v21 | en | 83% | 75% | 5% / 100% | 58% (21/24, 29/52) | 78% | 93% | 5/6 |
| strands-v21 | ru | 70% | 63% | 7% / 100% | 46% (24/24, 41/52) | 75% | 91% | 4/6 |
| strands-v21 | en_rev | 82% | 72% | 3% / 100% | 58% (21/24, 29/52) | 78% | 93% | 5/6 |
| tuned-g1 | en | 97% | 86% | 20% / 100% | 64% (18/24, 21/52) | 82% | 96% | 5/6 |
| tuned-g1 | ru | 96% | 84% | 21% / 100% | 62% (20/24, 25/52) | 84% | 95% | 4/6 |
| tuned-g1 | en_rev | 99% | 88% | 21% / 100% | 64% (18/24, 21/52) | 82% | 96% | 5/6 |
| tuned-g2 | en | 95% | 84% | 45% / 100% | 64% (21/24, 24/52) | 82% | 95% | 5/6 |
| tuned-g2 | ru | 93% | 84% | 34% / 100% | 57% (23/24, 32/52) | 82% | 95% | 4/6 |
| tuned-g2 | en_rev | 96% | 87% | 42% / 100% | 64% (21/24, 24/52) | 82% | 95% | 5/6 |
| tuned-g3 | en | 93% | 84% | 55% / 100% | 58% (17/24, 25/52) | 86% | 96% | 6/6 |
| tuned-g3 | ru | 93% | 84% | 49% / 100% | 58% (20/24, 28/52) | 89% | 96% | 5/6 |
| tuned-g3 | en_rev | 92% | 82% | 50% / 100% | 58% (17/24, 25/52) | 86% | 96% | 5/6 |
| tuned-g4 | en | 95% | 83% | 34% / 100% | 54% (22/24, 33/52) | 84% | 96% | 5/6 |
| tuned-g4 | ru | 96% | 86% | 26% / 100% | 54% (22/24, 33/52) | 86% | 97% | 5/6 |
| tuned-g4 | en_rev | 92% | 83% | 29% / 100% | 54% (22/24, 33/52) | 84% | 96% | 5/6 |
| tuned-g5 | en | 97% | 87% | 49% / 100% | 57% (24/24, 33/52) | 87% | 96% | 5/6 |
| tuned-g5 | ru | 95% | 84% | 47% / 100% | 57% (24/24, 33/52) | 87% | 96% | 5/6 |
| tuned-g5 | en_rev | 97% | 88% | 43% / 100% | 57% (24/24, 33/52) | 87% | 96% | 5/6 |
| tuned-g6 | en | 93% | 84% | 34% / 100% | 58% (23/24, 31/52) | 86% | 97% | 5/6 |
| tuned-g6 | ru | 95% | 84% | 30% / 100% | 54% (22/24, 33/52) | 91% | 97% | 6/6 |
| tuned-g6 | en_rev | 93% | 84% | 30% / 100% | 58% (23/24, 31/52) | 86% | 97% | 6/6 |
| tuned-g6c | en | 96% | 84% | 58% / 98% | 55% (23/24, 33/52) | 87% | 97% | 5/6 |
| tuned-g6c | ru | 96% | 87% | 47% / 100% | 53% (22/24, 34/52) | 88% | 99% | 5/6 |
| tuned-g6c | en_rev | 95% | 84% | 54% / 100% | 55% (23/24, 33/52) | 87% | 97% | 5/6 |
| tuned-g7 | en | 96% | 86% | 55% / 100% | 55% (24/24, 34/52) | 83% | 95% | 6/6 |
| tuned-g7 | ru | 96% | 84% | 43% / 100% | 58% (24/24, 32/52) | 84% | 95% | 6/6 |
| tuned-g7 | en_rev | 96% | 86% | 54% / 100% | 55% (24/24, 34/52) | 83% | 95% | 6/6 |
| tuned-g8 | en | 97% | 86% | 57% / 100% | 58% (22/24, 30/52) | 86% | 96% | 6/6 |
| tuned-g8 | ru | 93% | 82% | 47% / 100% | 54% (22/24, 33/52) | 89% | 95% | 5/6 |
| tuned-g8 | en_rev | 96% | 86% | 54% / 100% | 58% (22/24, 30/52) | 86% | 96% | 6/6 |
| tuned-g8b | en | 99% | 87% | 55% / 98% | 57% (24/24, 33/52) | 80% | 95% | 5/6 |
| tuned-g8b | ru | 97% | 88% | 53% / 98% | 54% (24/24, 35/52) | 84% | 97% | 5/6 |
| tuned-g8b | en_rev | 97% | 86% | 51% / 100% | 57% (24/24, 33/52) | 80% | 95% | 5/6 |
| tuned-g8c | en | 99% | 87% | 55% / 100% | 53% (23/24, 35/52) | 86% | 95% | 5/6 |
| tuned-g8c | ru | 97% | 87% | 51% / 97% | 53% (24/24, 36/52) | 87% | 97% | 5/6 |
| tuned-g8c | en_rev | 97% | 86% | 53% / 100% | 53% (23/24, 35/52) | 86% | 95% | 5/6 |
| tuned-g8d | en | 97% | 86% | 47% / 100% | 55% (23/24, 33/52) | 86% | 96% | 6/6 |
| tuned-g8d | ru | 96% | 83% | 43% / 100% | 55% (23/24, 33/52) | 86% | 97% | 5/6 |
| tuned-g8d | en_rev | 97% | 86% | 42% / 100% | 55% (23/24, 33/52) | 86% | 96% | 6/6 |

## Порог уверенности для react (условие en)

Ниже порога сервер не вмешивается: NPC продолжает распорядок, то есть ответ считается `continue`. `охват` — доля ситуаций, где модель уверена; `верно среди уверенных`; `итог` — доля приемлемых действий с учётом отката на `continue`.

| модель | ≥0.3: охват / верно / итог | ≥0.5: охват / верно / итог | ≥0.7: охват / верно / итог | ≥0.9: охват / верно / итог |
|---|---|---|---|---|
| laya | 75% / 65% / 55% | 41% / 71% / 43% | 25% / 63% / 33% | 13% / 70% / 28% |
| openjev-2b | 75% / 88% / 70% | 42% / 91% / 50% | 17% / 100% / 33% | 7% / 100% / 25% |
| openjev-4b | 86% / 86% / 74% | 61% / 93% / 66% | 32% / 100% / 45% | 11% / 100% / 28% |
| service-g5 | 100% / 97% / 97% | 88% / 99% / 88% | 75% / 98% / 75% | 49% / 100% / 62% |
| service-g7 | 100% / 95% / 95% | 88% / 97% / 86% | 80% / 98% / 80% | 55% / 100% / 64% |
| service-g8b | 99% / 99% / 97% | 97% / 99% / 96% | 78% / 98% / 82% | 54% / 98% / 63% |
| service-v1-2610 | 92% / 89% / 84% | 67% / 96% / 74% | 42% / 97% / 55% | 21% / 100% / 39% |
| strands | 87% / 86% / 78% | 61% / 91% / 64% | 42% / 97% / 55% | 12% / 100% / 30% |
| strands-fast | 87% / 86% / 78% | 61% / 91% / 64% | 41% / 97% / 54% | 12% / 100% / 30% |
| strands-v1-2610 | 92% / 89% / 84% | 68% / 96% / 75% | 42% / 97% / 55% | 21% / 100% / 39% |
| strands-v21 | 83% / 86% / 76% | 55% / 90% / 59% | 29% / 95% / 42% | 5% / 100% / 24% |
| tuned-g1 | 95% / 97% / 93% | 72% / 100% / 76% | 51% / 100% / 64% | 20% / 100% / 38% |
| tuned-g2 | 96% / 96% / 93% | 88% / 99% / 88% | 76% / 100% / 80% | 45% / 100% / 59% |
| tuned-g3 | 99% / 93% / 93% | 88% / 96% / 86% | 82% / 97% / 82% | 55% / 100% / 63% |
| tuned-g4 | 97% / 96% / 93% | 80% / 98% / 80% | 68% / 100% / 72% | 34% / 100% / 50% |
| tuned-g5 | 100% / 97% / 97% | 89% / 99% / 89% | 75% / 98% / 75% | 49% / 100% / 62% |
| tuned-g6 | 97% / 93% / 91% | 86% / 97% / 84% | 63% / 100% / 67% | 34% / 100% / 50% |
| tuned-g6c | 99% / 97% / 96% | 92% / 99% / 93% | 80% / 98% / 83% | 58% / 98% / 68% |
| tuned-g7 | 100% / 96% / 96% | 88% / 97% / 86% | 80% / 98% / 80% | 55% / 100% / 64% |
| tuned-g8 | 100% / 97% / 97% | 92% / 99% / 93% | 79% / 98% / 84% | 57% / 100% / 67% |
| tuned-g8b | 99% / 99% / 97% | 97% / 99% / 96% | 75% / 98% / 79% | 55% / 98% / 64% |
| tuned-g8c | 99% / 99% / 97% | 96% / 99% / 95% | 76% / 98% / 79% | 55% / 100% / 66% |
| tuned-g8d | 99% / 97% / 96% | 89% / 99% / 89% | 78% / 98% / 80% | 47% / 100% / 59% |

## think: можно ли подобрать порог (условие en)

AUROC — насколько вероятность «да» отделяет нужные случаи от ненужных (0,5 — случайно, 1 — идеально); `лучший порог` подобран на этом же наборе, поэтому оценка оптимистичная.

| модель | AUROC | лучший порог | точность при нём |
|---|---|---|---|
| laya | 0.56 | 0.440 | 70% |
| openjev-2b | 0.73 | 0.714 | 72% |
| openjev-4b | 0.77 | 0.763 | 76% |
| service-g5 | 0.78 | 0.937 | 76% |
| service-g7 | 0.78 | 0.921 | 75% |
| service-g8b | 0.73 | 0.978 | 75% |
| service-v1-2610 | 0.82 | 0.733 | 76% |
| strands | 0.77 | 0.690 | 74% |
| strands-fast | 0.76 | 0.848 | 74% |
| strands-v1-2610 | 0.82 | 0.711 | 76% |
| strands-v21 | 0.74 | 0.799 | 74% |
| tuned-g1 | 0.76 | 0.701 | 75% |
| tuned-g2 | 0.78 | 0.717 | 76% |
| tuned-g3 | 0.71 | 0.900 | 76% |
| tuned-g4 | 0.70 | 0.957 | 75% |
| tuned-g5 | 0.78 | 0.958 | 78% |
| tuned-g6 | 0.75 | 0.957 | 76% |
| tuned-g6c | 0.76 | 0.961 | 75% |
| tuned-g7 | 0.78 | 0.921 | 76% |
| tuned-g8 | 0.78 | 0.948 | 79% |
| tuned-g8b | 0.73 | 0.979 | 75% |
| tuned-g8c | 0.73 | 0.958 | 75% |
| tuned-g8d | 0.80 | 0.935 | 80% |

## Порядок вариантов

Доля ситуаций, где модель выбрала тот же `react` при обратном порядке вариантов (en против en_rev).

| модель | совпадение |
|---|---|
| laya | 72% |
| openjev-2b | 100% |
| openjev-4b | 100% |
| service-g5 | 96% |
| service-g7 | 95% |
| service-g8b | 97% |
| service-v1-2610 | 92% |
| strands | 88% |
| strands-fast | 88% |
| strands-v1-2610 | 92% |
| strands-v21 | 89% |
| tuned-g1 | 97% |
| tuned-g2 | 97% |
| tuned-g3 | 97% |
| tuned-g4 | 95% |
| tuned-g5 | 96% |
| tuned-g6 | 97% |
| tuned-g6c | 97% |
| tuned-g7 | 97% |
| tuned-g8 | 99% |
| tuned-g8b | 97% |
| tuned-g8c | 97% |
| tuned-g8d | 100% |

## react ✓ по типам ситуаций (условие en)

| модель | combat (4) | farm (2) | initiative (13) | memory (12) | noop (11) | param (9) | speech (30) | svm (3) | threat (11) | trade (8) | unusual (7) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-6.1-sol-low | 75% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| laya | 100% | 50% | 31% | 50% | 100% | 56% | 53% | 33% | 27% | 50% | 57% |
| openjev-2b | 100% | 50% | 92% | 75% | 73% | 100% | 87% | 100% | 36% | 75% | 100% |
| openjev-4b | 75% | 50% | 100% | 92% | 91% | 100% | 80% | 100% | 45% | 100% | 100% |
| service-g5 | 100% | 100% | 100% | 100% | 100% | 100% | 97% | 100% | 91% | 100% | 100% |
| service-g7 | 100% | 100% | 100% | 100% | 100% | 100% | 93% | 100% | 82% | 88% | 100% |
| service-g8b | 100% | 100% | 100% | 100% | 100% | 100% | 97% | 100% | 100% | 100% | 100% |
| service-v1-2610 | 100% | 50% | 100% | 92% | 73% | 100% | 90% | 100% | 55% | 88% | 100% |
| strands | 100% | 50% | 100% | 92% | 91% | 100% | 87% | 100% | 45% | 88% | 100% |
| strands-fast | 100% | 50% | 100% | 92% | 91% | 100% | 87% | 100% | 45% | 88% | 100% |
| strands-v1-2610 | 100% | 50% | 100% | 92% | 73% | 100% | 90% | 100% | 55% | 88% | 100% |
| strands-v21 | 75% | 50% | 92% | 83% | 82% | 100% | 90% | 100% | 45% | 75% | 100% |
| tuned-g1 | 100% | 100% | 100% | 100% | 100% | 100% | 93% | 100% | 100% | 100% | 100% |
| tuned-g2 | 100% | 100% | 100% | 100% | 100% | 100% | 93% | 100% | 82% | 100% | 100% |
| tuned-g3 | 100% | 100% | 100% | 100% | 100% | 100% | 93% | 100% | 82% | 100% | 86% |
| tuned-g4 | 100% | 100% | 100% | 100% | 100% | 100% | 93% | 100% | 82% | 100% | 100% |
| tuned-g5 | 100% | 100% | 100% | 100% | 100% | 100% | 97% | 100% | 91% | 100% | 100% |
| tuned-g6 | 100% | 100% | 100% | 100% | 100% | 100% | 90% | 100% | 73% | 88% | 100% |
| tuned-g6c | 100% | 100% | 92% | 100% | 91% | 100% | 97% | 100% | 91% | 100% | 100% |
| tuned-g7 | 100% | 100% | 100% | 100% | 100% | 100% | 93% | 100% | 91% | 88% | 100% |
| tuned-g8 | 100% | 100% | 100% | 100% | 100% | 100% | 97% | 100% | 91% | 100% | 100% |
| tuned-g8b | 100% | 100% | 100% | 100% | 100% | 100% | 97% | 100% | 100% | 100% | 100% |
| tuned-g8c | 100% | 100% | 100% | 100% | 100% | 100% | 97% | 100% | 100% | 100% | 100% |
| tuned-g8d | 100% | 100% | 100% | 100% | 100% | 100% | 93% | 100% | 100% | 88% | 100% |

## Задержка и память

| модель | запрос p50 | p95 | пик памяти | загрузка |
|---|---|---|---|---|
| gpt-6.1-sol-low | 7181.4 мс на вызов из 4 ситуаций | — | — | — |
| laya | 11.3 мс | 20.0 мс | 4.4 ГБ | 8.1 с |
| openjev-2b | 120.1 мс | 174.0 мс | 4.35 ГБ | 6.6 с |
| openjev-4b | 271.2 мс | 421.8 мс | 9.06 ГБ | 6.1 с |
| strands | 98.1 мс | 115.3 мс | 3.78 ГБ | 184.9 с |
| strands-fast | 84.1 мс | 96.0 мс | 3.73 ГБ | 5.0 с |
| strands-v1-2610 | 85.9 мс | 97.8 мс | 3.88 ГБ | 16.4 с |
| strands-v21 | 84.2 мс | 96.3 мс | 3.73 ГБ | 10.4 с |
| tuned-g1 | 87.5 мс | 100.8 мс | 3.88 ГБ | 8.9 с |
| tuned-g2 | 87.7 мс | 99.3 мс | 3.88 ГБ | 9.1 с |
| tuned-g3 | 87.7 мс | 99.9 мс | 3.88 ГБ | 8.6 с |
| tuned-g4 | 88.4 мс | 99.8 мс | 3.88 ГБ | 8.8 с |
| tuned-g5 | 85.0 мс | 97.2 мс | 3.88 ГБ | 8.5 с |
| tuned-g6 | 98.1 мс | 113.9 мс | 3.93 ГБ | 4.4 с |
| tuned-g6c | 98.5 мс | 114.4 мс | 3.93 ГБ | 4.5 с |
| tuned-g7 | 100.8 мс | 117.7 мс | 3.93 ГБ | 4.7 с |
| tuned-g8 | 99.8 мс | 115.8 мс | 3.93 ГБ | 4.3 с |
| tuned-g8b | 101.6 мс | 117.6 мс | 3.93 ГБ | 4.4 с |
| tuned-g8c | 153.0 мс | 176.3 мс | 3.93 ГБ | 10.2 с |
| tuned-g8d | 126.5 мс | 146.7 мс | 3.93 ГБ | 16.3 с |

## Ошибки react (условие en)

| ситуация | эталон | gpt-6.1-sol-low | laya | openjev-2b | openjev-4b | service-g5 | service-g7 | service-g8b | service-v1-2610 | strands | strands-fast | strands-v1-2610 | strands-v21 | tuned-g1 | tuned-g2 | tuned-g3 | tuned-g4 | tuned-g5 | tuned-g6 | tuned-g6c | tuned-g7 | tuned-g8 | tuned-g8b | tuned-g8c | tuned-g8d |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| lothar_weapon_drawn | svm_weapon_down | svm_weapon_down | **continue** | **continue** | svm_weapon_down | **continue** | **continue** | svm_weapon_down | **continue** | **continue** | **continue** | **continue** | **continue** | svm_weapon_down | **continue** | **continue** | **continue** | **continue** | **continue** | svm_weapon_down | **continue** | svm_weapon_down | svm_weapon_down | svm_weapon_down | svm_weapon_down |
| lothar_weapon_ignored_warnings | attack | attack | **warn_again** | **warn_again** | **warn_again** | attack | **warn_again** | attack | **warn_again** | **warn_again** | **warn_again** | **warn_again** | **warn_again** | attack | **warn_again** | **warn_again** | **warn_again** | attack | **warn_again** | attack | attack | **warn_again** | attack | attack | attack |
| coragon_robbery | call_guards/flee | call_guards | **attack** | **give_gold** | **give_gold** | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards |
| harad_theft_witnessed | attack/call_guards | attack | **continue** | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack |
| lobart_sheep_beaten | svm_sheepkiller/call_farmhands | svm_sheepkiller | **continue** | call_farmhands | **continue** | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | call_farmhands | svm_sheepkiller | svm_sheepkiller | call_farmhands | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller | svm_sheepkiller |
| lobart_sheep_killed | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | **svm_sheepkiller** | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | **svm_sheepkiller** | **svm_sheepkiller** | **svm_sheepkiller** | **svm_sheepkiller** | **svm_sheepkiller** | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime | svm_sheepkiller_crime |
| valentino_bedroom_night | call_guards/svm_get_out | call_guards | svm_get_out | **attack** | **attack** | svm_get_out | svm_get_out | svm_get_out | **attack** | **attack** | **attack** | **attack** | **attack** | svm_get_out | svm_get_out | svm_get_out | svm_get_out | svm_get_out | svm_get_out | svm_get_out | svm_get_out | svm_get_out | svm_get_out | svm_get_out | svm_get_out |
| vatras_magic_in_crowd | svm_stop_magic | svm_stop_magic | **continue** | **continue** | **continue** | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic | **continue** | **continue** | svm_stop_magic | **continue** | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic | svm_stop_magic |
| edda_market_fight | svm_its_a_fight/flee | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | **continue** | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | **continue** | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | flee | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight | svm_its_a_fight |
| sylvio_militia_on_farm | threaten/svm_mock | svm_mock | **continue** | svm_mock | svm_mock | threaten | svm_mock | svm_mock | svm_mock | svm_mock | svm_mock | svm_mock | svm_mock | svm_mock | threaten | threaten | svm_mock | threaten | threaten | **attack** | svm_mock | svm_mock | svm_mock | svm_mock | svm_mock |
| bandit_wanted_man | attack | **question** | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack | attack |
| gate_guard_ragged_stranger | block_and_question | block_and_question | **attack** | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question | block_and_question |
| zuris_passerby_customer | call_out_offer | call_out_offer | **greet** | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer | call_out_offer |
| citizen_way_to_market | svm_galgen_2_markt | svm_galgen_2_markt | **continue** | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt | svm_galgen_2_markt |
| citizen_way_to_barracks | svm_galgen_2_kaserne | svm_galgen_2_kaserne | **continue** | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne | svm_galgen_2_kaserne |
| pepe_wolves | ask_help | ask_help | **greet** | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help | ask_help |
| milten_friend_returns | greet_and_talk | greet_and_talk | **continue** | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk | greet_and_talk |
| lehmar_debt_overdue | demand_money/send_thugs | demand_money | **greet** | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money | demand_money |
| halvor_fence | buy_stolen | buy_stolen | **continue** | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen | buy_stolen |
| xardas_hero_back_from_valley | ask_report | ask_report | **svm_not_now** | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report | ask_report |
| farmhand_sleeping | continue | continue | continue | **wake_and_check** | **wake_and_check** | continue | continue | continue | **wake_and_check** | continue | continue | **wake_and_check** | continue | continue | continue | continue | continue | continue | continue | **wake_and_check** | continue | continue | continue | continue | continue |
| patrol_hero_sleeping | continue | continue | continue | **wake_hero** | continue | continue | continue | continue | **wake_hero** | continue | continue | **wake_hero** | **wake_hero** | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue |
| matteo_after_trade | continue | continue | continue | **approach_talk** | continue | continue | continue | continue | continue | continue | continue | continue | **approach_talk** | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue |
| kardif_drunk_hero | continue | continue | continue | continue | continue | continue | continue | continue | **throw_out** | **throw_out** | **throw_out** | **throw_out** | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue | continue |
| citizen_polite_greeting | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | **continue** | **continue** | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back | greet_back |
| bosper_thief_returns | svm_you_again/refuse_service | svm_you_again | svm_you_again | **greet** | **greet** | refuse_service | refuse_service | refuse_service | **greet** | refuse_service | refuse_service | **greet** | refuse_service | refuse_service | refuse_service | refuse_service | refuse_service | refuse_service | refuse_service | refuse_service | refuse_service | refuse_service | refuse_service | refuse_service | refuse_service |
| akil_rescuer_arrives | thank_warmly | thank_warmly | **greet** | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | **greet** | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly | thank_warmly |
| sekob_beaten_yesterday | svm_not_now/flee | flee | **attack** | **attack** | flee | flee | flee | flee | svm_not_now | **attack** | **attack** | svm_not_now | **attack** | flee | flee | flee | flee | flee | flee | flee | flee | flee | flee | flee | flee |
| diego_promised_reward | approach_pay | approach_pay | **svm_not_now** | **continue** | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay | approach_pay |
| guard_insulted | svm_want_more/attack | svm_want_more | attack | attack | **continue** | attack | svm_want_more | attack | attack | attack | attack | attack | attack | attack | attack | svm_want_more | attack | attack | svm_want_more | attack | svm_want_more | attack | attack | attack | attack |
| thekla_compliment | thank_smile/give_more | thank_smile | **continue** | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile | thank_smile |
| jora_threatened | call_guards | call_guards | **give_gold** | **give_gold** | **give_gold** | call_guards | call_guards | call_guards | **give_gold** | **give_gold** | **give_gold** | **give_gold** | **give_gold** | call_guards | call_guards | call_guards | call_guards | call_guards | **give_gold** | call_guards | call_guards | call_guards | call_guards | call_guards | call_guards |
| bosper_apprentice_request | discuss_apprenticeship | discuss_apprenticeship | **svm_not_now** | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship | discuss_apprenticeship |
| edda_gift | accept_thank | accept_thank | accept_thank | accept_thank | **continue** | **refuse** | **refuse** | **refuse** | accept_thank | accept_thank | accept_thank | accept_thank | accept_thank | **refuse** | **refuse** | **refuse** | **refuse** | **refuse** | **refuse** | **refuse** | **refuse** | **refuse** | **refuse** | **refuse** | **refuse** |
| gate_guard_false_envoy | question_doubt/laugh_off | question_doubt | **attack** | **let_pass** | **let_pass** | question_doubt | question_doubt | question_doubt | question_doubt | **let_pass** | **let_pass** | question_doubt | **let_pass** | question_doubt | question_doubt | question_doubt | question_doubt | question_doubt | question_doubt | question_doubt | question_doubt | question_doubt | question_doubt | question_doubt | question_doubt |
| zuris_lowball | refuse_haggle | refuse_haggle | **sell_cheap** | **sell_cheap** | refuse_haggle | refuse_haggle | **sell_cheap** | refuse_haggle | **sell_cheap** | **sell_cheap** | **sell_cheap** | **sell_cheap** | **sell_cheap** | refuse_haggle | refuse_haggle | refuse_haggle | refuse_haggle | refuse_haggle | **sell_cheap** | refuse_haggle | **sell_cheap** | refuse_haggle | refuse_haggle | refuse_haggle | **sell_cheap** |
| hagen_dragon_report | question_report | question_report | **svm_not_now** | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report | question_report |
| wulfgar_overheard_insult | confront/svm_want_more | confront | **attack** | confront | **continue** | confront | confront | confront | **continue** | confront | confront | **continue** | confront | **continue** | **continue** | **continue** | **continue** | confront | confront | confront | confront | confront | confront | confront | confront |
| lothar_fake_paladin | question | question | **svm_pal_greet** | question | question | question | question | question | question | question | question | question | question | question | question | **svm_pal_greet** | question | question | question | question | question | question | question | question | question |
| bengar_sheep_dead_news | shock_ask | shock_ask | **attack** | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask | shock_ask |
| rengaru_dropped_purse | take_purse | take_purse | **call_out** | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse | take_purse |
| gorn_join_request | agree_follow/discuss_first | agree_follow | **refuse** | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow | agree_follow |
| edda_sees_pickpocket | call_for_help | call_for_help | **flee** | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help | call_for_help |
| coragon_strong_drink | serve_drink | serve_drink | **svm_not_now** | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink | serve_drink |
