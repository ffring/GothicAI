# Отдельный сервисный отчёт: сохранённый Gate11

Сервисный бюджет **FAIL**: при четырёх native NPC p95 Decider вырос на **10.59663%**, Gemma на **31.21631%**. Порог остаётся **10%**; baseline и native comparison содержат по **20 фактических измерений** каждого сервиса. ASR −1.24908%, TTS +8.36735%. Эти результаты описывают нагрузку native на общем сервере; причинное влияние GRU не установлено.

| Сервис | Before p95, мс | Native p95, мс | Изменение | 10% бюджет |
|---|---:|---:|---:|---|
| ASR | 817.536568 | 807.324908 | −1.24908% | PASS |
| TTS | 437.162087 | 473.740987 | +8.36735% | PASS |
| Decider | 68.927712 | 76.231724 | +10.59663% | FAIL |
| Gemma | 109.876405 | 144.175764 | +31.21631% | FAIL |

Физический допуск также **FAIL** независимо от сервисного бюджета. GRU cohort0/2/3 потеряли видимость и ownership на 2067/3700/7400 мс; только cohort1 прошёл полные20с. Сокращённые unhealthy service windows не дают валидного сравнения GRU. MLP cohort0 завершился infrastructure `unexpected_control_loss:released` после настоящего отказа `target_invalid`; серия неполная. Старые frozen Gate11 данные и результаты не переоценивались и не изменялись.

По явному запросу пользователя новый preregistered protocol вводит `service_gate_role=standalone_report`, version1. Только сервисные p95-budget failures идут в отдельный отчёт; все source/PID/count/cadence/healthy-window проверки и боевые p95≤5/20/100мс, neural control≥95%, zero invariants/softlocks и no-fallback остаются обязательными. Конфиги без роли сохраняют прежний coupled режим. CLI final/load readers не изменился; они дополнительно сохраняют `body-stage8-services.json`. Комбинированная готовность указывается отдельно; service FAIL не превращается в PASS.

Одна разрешённая SafePeer calibration попытка завершилась FAIL (6 эпизодов, approved2=0/5=2/11=0 при требовании2 каждого). Попытка остановлена: full collection, обучение, child279, новый боевой gate и fresh screening не открывались.

Числа и original file SHA взяты из независимо проверенного [сохранённого prior audit](../guard-reader-v1/prior-gate11.json); [report.json](report.json) связывает этот источник, frozen environment/declaration и оригинальные service receipts. Журналы и веса сюда не копировались. Изменение readers проверено [175 целевыми dev-only тестами](validation/README.md); они не запускали модели, игру или сервисный workload.
