# The day menu, g9

day_probe.py: 249 rows, 169 answered.

## Verdict

| rule | pass | value |
|---|---|---|
| 1. menus sane | yes | 100% |
| 2. do question p95 ≤ 700 chars | yes | 440 |
| 3. two questions ≤ one + 15 ms (infer p50) | yes | +3.8 ms |
| 4. mean P(chat): news − control ≥ 0.15 | yes | +0.48 |
| 5. do ≥ 75% net | yes | 84% |
| 5. no concern kind < 60% | yes | worst 71% |
| 5. do ≥ plan − 2 pp, same rows | no | -17.6 pp |
| 6. same choice reversed ≥ 85% | yes | 94% |

Not all pass: the menus stay in shadow.

## 1. Menus

All rules hold.

## 2. Length

| what | p50 / p95 / max chars | p50 / p95 / max tokens |
|---|---|---|
| state | 206 / 248 / 253 | 60 / 67 / 67 |
| state, loss rows (the agenda) | 288 / 308 / 310 | 77 / 81 / 81 |
| `plan` question | 177 / 291 / 291 | 86 / 143 / 143 |
| `do` question | 246 / 440 / 565 | 99 / 166 / 201 |

do menu sizes: {2: 115, 3: 38, 4: 12, 5: 4}

## 3. Latency

infer_ms p50: two questions 27 / 36 / 44, one 24 / 27 / 27 (p50 / p95 / max).

## 4. Concern sensitivity

| condition | trip | mean P(trip) | chosen |
|---|---|---|---|
| control | chat | 0.28 | 3 of 35 |
| news | chat | 0.76 | 24 of 35 |
| death | pray | 0.63 | 15 of 25 |
| loss | tavern | 0.25 | 5 of 23 |

## 5. Accuracy

A/B agreement (the ceiling): 90% of 169 rows; gold rows: 152.
Net at the planner's threshold 0.5: do 84%; on the rows with both golds do 81%, plan 99% (85 rows).

| condition | do net |
|---|---|
| none | 95% |
| control | 77% |
| news | 71% |
| death | 87% |
| loss | 92% |

| do options | rows | ≥ 0.3: coverage / correct / net | ≥ 0.5: coverage / correct / net | ≥ 0.7: coverage / correct / net |
|---|---|---|---|---|
| 2 | 103 | 89% / 82% / 81% | 79% / 88% / 85% | 57% / 97% / 83% |
| 3 | 36 | 97% / 83% / 83% | 94% / 82% / 81% | 86% / 87% / 81% |
| 4 | 11 | 100% / 82% / 82% | 100% / 82% / 82% | 64% / 100% / 100% |
| 5 | 2 | 100% / 100% / 100% | 100% / 100% / 100% | 100% / 100% / 100% |

## 6. Stability

do: the same choice with the menu reversed in 94% of 169 rows.

## The npc's

- BAU_911_ELENA Елена: chat
- BAU_931_TILL Тилл: chat
- BAU_950_LOBART Лобарт: always
- BAU_951_HILDA Хильда: always
- BAU_952_VINO Вино: always
- BAU_953_BAUER Крестьянин: pray
- BAU_954_MALETH Малет: always
- KDF_507_TALAMON Таламон: duty
- KDF_508_GORAX Горакс: pray
- MIL_330_MILIZ Городской стражник: chat
- MIL_331_MILIZ Городской стражник: tavern
- MIL_333_STADTWACHE Городской стражник: duty
- MIL_337_MIKA Мика: tavern
- NONE_100_XARDAS Ксардас: always
- NOV_604_DYRIAN Дуриан: pray
- PAL_241_RITTER Рыцарь: tavern
- SLD_826_SOELDNER Наемник: chat
- VLK_4000_BUERGER Гражданин: tavern
- VLK_402_RICHTER Судья: pray
- VLK_413_BOSPER Боспер: tavern
- VLK_414_HANNA Ханна: pray
- VLK_415_MELDOR Мелдор: chat
- VLK_429_BUERGERIN Гражданка: pray
- VLK_457_BRIAN Брайан: pray
- VLK_462_THORBEN Торбен: tavern
- VLK_469_HALVOR Халвор: tavern
- VLK_470_SARAH Сара: chat
- VLK_479_LUCY Люси: chat
- VLK_487_BUERGERIN Гражданка: chat
- VLK_492_RENGARU Ренгару: tavern
- VLK_498_IGNAZ Игнац: pray
