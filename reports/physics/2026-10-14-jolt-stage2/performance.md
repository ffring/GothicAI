# Этап 2: производительность 20 тел на M1 Pro

**Финальный критерий «без замедления FPS» — FAIL.** Первый полностью resource-clean matched опыт v12 подтверждает все 20 моделей/480 частей во всех 1020 кадрах. Active повышает p50 полного цикла на 9,96%, p95 на 36,53%; sleeping — на 1,60% и 69,94%. Ни одного валидного отрицательного результата не повторяли для улучшения чисел. Функция остаётся экспериментальной с `[GAME] ragdoll=0`; строгая готовность этапа не объявляется. Функциональные death/KO/save/recovery проверки v12 проходят; итоговые измерения и происхождение приведены ниже.

## Первые невалидные замеры v6: сохранённая история

В первой серии v6 критерий производительности оставался OPEN: объявленные измерения сохранены целиком, но не проходили контроль покрытия. Игровая площадка создала 12 NPC вместо 20: восемь точек сетки не имеют земли. Отдельный CPU-стенд прекратился при нарушении контроля активности. Причинные исправления стенда и все последующие невалидные попытки отдельно перечислены в [поправке к протоколу](performance-amendment.md), до новых измерений.

Это результат проверки кандидата v6, а не доказательство замедления либо отсутствия замедления сцены с 20 телами. Функциональные игровые проверки, полный сейв и подъём находятся в [общем отчёте](validation-notes.md).

## Объявленные условия и происхождение

План записан до первого измерения в [declaration.json](../../../data/physics/stage2/performance-v6/declaration.json), `2026-10-14 10:34:05 UTC`, и в [протоколе](validation-protocol.md). Порядок: native CPU20, затем три свежих игровых процесса `disabled`, `active`, `sleeping`. Каждый игровой вариант сохраняет первые 120 кадров прогрева и все следующие 900 измеренных кадров. Отбор удачных кадров или повторов отсутствует.

- MacBookPro18,1, Apple M1 Pro, 10 логических ядер, 32 GiB, питание от сети, батарея 100%.
- Кандидат: manifest SHA-256 `89d3a5e42d0b212e1b797bd3cc75ab140323c40701724505b1b657acf526f447`; Gothic2Notr SHA-256 `e390a077fd6645f9511217ab0f50073ac5d386221c290619454b9508b8b8ffaf`. Запускался обычный путь `OpenGothic/build/opengothic/Gothic2Notr`.
- Одинаковые ассеты `/Volumes/DevSSD/Dev/games/Gothic II`; сейв `/Volumes/DevSSD/Dev/projects/gothic/save_slot_1.sav`, SHA-256 `c2852f4c009f23225e1c9c70f564ca36b4c12e8220736d1c72a1dbfce8d3372f`.
- Площадка `NW_XARDAS_TOWER_VALLEY_091`, seed 73013; заявлены 20 одинаковых `BDT_1013_BANDIT_L`, сетка 5 × 4 с шагом 250 см, `HUMANS.MDS`, 24 физические части каждого человека. Требуемое суммарное число частей — 480.
- Одинаковая камера `cam world 2300 1800 74`, `cam look 80`, настройка разрешения `INTERNAL vidResIndex=2`, `overlay off`, `view nopfx`. Размер экспортированного PNG 800 × 600 сам по себе не доказывает разрешение игрового viewport.
- `NO_BUILD=1`, `TIMEOUT=300`, `OPENGOTHIC_RAGDOLL_TRACE=0`, `OPENGOTHIC_RAGDOLL_PERF=1`, `GOTHIC_AI=0`, `OPENGOTHIC_TEXTURES=''`, `OPENGOTHIC_BODY_ANIMATION_RNG=isolated`. `OPENGOTHIC_BENCH_KEEP`, `OPENGOTHIC_BENCH_REALTIME` и диагностический дамп создания убраны из окружения.

Во время измерений на Mac не было других игр, компиляции, ffmpeg, монтажа и тяжёлого чтения исходников или сырых результатов. Linux-проверки шли на отдельном компьютере; передача их результатов на Mac ожидала `TIMING DONE`.

После завершения всех запусков повторно проверены 49 SHA-256: 30 исходных файлов, 13 вспомогательных файлов, пять бинарников и исходный сейв. Все совпали с замороженным manifest; [post-run-integrity.json](../../../data/physics/stage2/candidate-v6/post-run-integrity.json) сохранён. Игровые ассеты не изменялись.

Время полного цикла измеряется монотонными часами между post-world вызовами стенда и включает игровой/render-цикл. Фиксированный игровой `dt` не используется для расчёта FPS. Счётчики и таймеры буферизуются в памяти, CSV пишется после измерения. p50 — элемент 449, p95 — элемент 854 отсортированных 900 значений.

Основной критерий: p50 **и** p95 полного цикла активного и спящего вариантов не выше соответствующих величин контроля с 20 анимированными трупами, без разрешённого замедления. p95 нативного шага ≤ 2 мс — отдельный инженерный ориентир, который не заменяет этот критерий.

## Контроль покрытия: FAIL

Все три игровых процесса завершились с кодом 0 и записали ровно 1020 кадров `0..1019`. В каждом журнале до записи присутствуют восемь ошибок `twenty-NPC fixture has no ground`, а событие `ragdoll_down_bulk` подтверждает только 12 созданных и умерших NPC. Отсутствие земли выявлено обычным raycast при подготовке сетки; NPC на этих точках не вставлялись.

| Первый игровой вариант | NPC / мёртвых / центров в frustum, все 1020 кадров | Частей, все 1020 кадров | Сон после импульса, 900 кадров | Сон до импульса, 900 кадров | Результат |
|---|---:|---:|---|---|---|
| disabled | 12 / 12 / 12 | 0 | 0 | 0 | FAIL: требуется 20 NPC |
| active | 12 / 12 / 12 | 288 | 0 | 0: 881 кадров; 1: 12; 10: 6; 11: 1 | FAIL: требуется 20 тел / 480 частей; ещё 19 кадров не полностью активны до импульса |
| sleeping | 12 / 12 / 12 | 288 | 12 | 12 | FAIL: требуется 20 тел / 480 частей |

У обоих физических вариантов каждый реально созданный NPC имеет полные 24 уникальные части одинаковой схемы; отсутствуют восемь целых NPC. Повторный импульс после шага будит тела, но не доказывает активность уже завершившегося шага. Поэтому 19 кадров со спящими телами в active не переименованы в активные по счётчику после импульса.

Тогдашняя сохранённая [версия checker v6](../../../data/physics/stage2/candidate-v6/source-byte-copies/scripts/ragdoll_performance.py) возвращает код 1. [comparison.json](../../../data/physics/stage2/performance-v6/comparison.json) сохраняет первый отказ; [diagnostic-summary.json](../../../data/physics/stage2/performance-v6/diagnostic-summary.json) содержит отдельный отказ каждого варианта, все нарушающие номера кадров, полные гистограммы и диагностические квантили. Сырые CSV и журналы находятся в [disabled](../../../data/physics/stage2/performance-v6/render-disabled/ragdoll-performance.csv), [active](../../../data/physics/stage2/performance-v6/render-active/ragdoll-performance.csv), [sleeping](../../../data/physics/stage2/performance-v6/render-sleeping/ragdoll-performance.csv).

Сохранённые после измерения [disabled](../../../data/physics/stage2/performance-v6/render-disabled/twenty_disabled_coverage.png), [active](../../../data/physics/stage2/performance-v6/render-active/twenty_active_coverage.png) и [sleeping](../../../data/physics/stage2/performance-v6/render-sleeping/twenty_sleeping_coverage.png) PNG просмотрены: камера показывает внешнюю/обратную сторону ландшафта, видимых 20 тел в кадре нет. Попадание 12 центров в frustum не подтверждает отсутствие окклюзии. Визуальный критерий также не выполнен.

## Все первые 900 значений: диагностические числа

Числа ниже относятся к фактическим **12** NPC и не закрывают критерий документа для 20 тел. Они сохранены вместе с отрицательным результатом покрытия; это не выбранный успешный повтор.

| Первый вариант | Полный цикл p50 / p95, мс | Шаг физики p50 / p95, мс | Средний FPS из монотонных интервалов | Кадров ≤ 16,667 мс из 900 |
|---|---:|---:|---:|---:|
| disabled, 12 анимированных трупов | 8,365041 / 14,445000 | 0,105042 / 0,121000 | 113,15 | 875 |
| active, 12 тел, 19 кадров с неполной активностью до импульса | 8,246750 / 16,571625 | 1,713834 / 2,799791 | 110,72 | 856 |
| sleeping, 12 спящих тел | 8,301417 / 13,372750 | 0,109292 / 0,131125 | 115,92 | 879 |

У active диагностическое отношение p50 к неполному disabled-контролю — 0,985859, p95 — 1,147222; p95 выше на 14,72%. У sleeping — 0,992394 / 0,925770. Эти отношения не считаются результатом для 20 тел. Диагностический p95 нативной физики active превышает 2 мс уже на этой неполной площадке.

## Отдельный native CPU20: INVALID, без оценки времени

Первый `gothic-ragdoll-contract --benchmark20` использует 20 синтетических схем без игровых ассетов и рендера. Это отдельная область проверки, не FPS игры. Процесс завершился с кодом 1: `active benchmark must not turn into a sleeping-body measurement`.

Стенд пишет общий JSON после всех вариантов; отказ произошёл раньше, поэтому JSON и квантили времени отсутствуют. [Первый журнал](../../../data/physics/stage2/performance-v6/native-cpu20-first.log) и [статус](../../../data/physics/stage2/performance-v6/native-cpu20-status.json) сохранены. Отсутствующие времена не восстановлены предположениями и не заменены игровыми измерениями.

На этом историческом шаге производительность оставалась OPEN. Следующие данные сохраняют первые невалидные результаты и исходные критерии.

## Первый полностью чистый matched опыт v12: coverage PASS, FPS FAIL

Измерения выполнены **2026-10-14 13:13:06–13:14:29 UTC**, после фиксированных 120 последовательных 1 Hz quiet-проверок. [Предварительная декларация resource3](../../../data/physics/stage2/performance-v12-resource3/resource-only-preregistration.json), [quiet precheck](../../../data/physics/stage2/performance-v12-resource3/resource-quiet-precheck.json), [декларация](../../../data/physics/stage2/performance-v12-resource3/first-qualified-declaration.json), [итог integrity/resources](../../../data/physics/stage2/performance-v12-resource3/first-qualified-post.json) сохранены. Предыдущие два опыта на тех же v12 байтах не квалифицируются: чужой Cargo/Rust/AWS-LC запускался после тихого precheck. Все их кадры и process audits сохранены; первый полностью resource-clean набор выбран по наличию внешних процессов, без выбора более быстрого baseline.

- Тот же MacBookPro18,1 /Apple M1 Pro /10 логических ядер /32 GiB; питание AC, 100% до и после опыта. Native CPU, disabled, active, sleeping идут последовательно; других Gothic/компиляторов/тестовых Cargo-процессов/ffmpeg не наблюдалось в 1 Hz monitor всех четырёх случаев. Это наблюдение с указанной частотой, а не доказательство отсутствия любой активности macOS между опросами. Linux all11/E2E выполнялись на отдельном компьютере, тяжёлые локальные чтения и передача результатов отложены.
- [Manifest v12](../../../data/physics/stage2/candidate-v12/manifest.json) SHA `38613b7e4ece26d0f2ecdffdf9ca9634765b0236e728841e01095208d3f9eaec`; Gothic2Notr `e9032feea82bcd80da4b6d20c05e0bd155fa31d8933696ebf214a8b8b1a98acb`. Все 31 source, 18 helpers, пять бинарников и исходный сейв проверены до/после и совпали. Запуск обычного build-пути, `NO_BUILD=1`; полные traces, per-model JSON и запись видео выключены.
- Исправленная геометрически выбранная площадка `NW_PATH_TO_MONASTERY_08`: центр `(39174,5156;4381,84033;12351,749)` см, сетка 5×4 /250 см. Одинаковая фиксированная камера всех случаев: origin `(39205,4609;6006,14355;13007,3633)`, target `(39205,4609;4387,97363;12337,0957)`, FOVy67,5°, aspect4:3, прежний `vidResIndex=2`, overlay off /view nopfx. Site/camera не выбирались по временам.
- Все 20 — `BDT_1013_BANDIT_L`, `HUMANS.MDS`, уникальные actor IDs и одинаковая полная 24-part topology. Во всех **1020** warmup+measured кадрах: 20 акторов/трупов/центров/loaded-body proxies; disabled имеет 0 частей, active/sleeping — ровно 480. Active до и после импульса имеет 20 физических /0 спящих; sleeping —20/20. Отрицательные warmup-данные не пропускаются; квантили используют только заранее объявленные последние 900 кадров.
- ACTIVE — консервативная нагрузка с `allowSleeping=false` только у этих benchmark-тел, а не оценка естественного сна. Импульс 50/5/25 N·s, фиксированные пятёрочные sign blocks/чётность акторов неизменны. Sleeping и обычная игра сохраняют native sleeping policy. Эта политика не сохраняется в сейве.

Полное [untimed coverage](../../../data/physics/stage2/performance-v12/coverage-comparison.json) заранее проходит все 3×1020×20 индивидуальных posed-оболочек, real body Item/slice/material/texture и landscape/object sight-линии, проверяет .90 projection margin и содержание полной оболочки в лёгкой сфере. Независимо просмотрены [disabled PNG](../../../data/physics/stage2/performance-v12/preflight-disabled/twenty_disabled_coverage.png), [active PNG](../../../data/physics/stage2/performance-v12/preflight-active/twenty_active_coverage.png), [sleeping PNG](../../../data/physics/stage2/performance-v12/preflight-sleeping/twenty_sleeping_coverage.png). Disabled/sleeping показывают 20 различимых моделей; active перекрывается и foliage местами мешает независимо сосчитать 20 силуэтов. Основной proof20 там — индивидуальные actor/schema/drawable/envelope записи. Timed `visible_mesh_proxies` — actual body presence + center/frustum/sphere nonrejection, не GPU draw submission, pixel visibility или occlusion-тест. В timed нет костных циклов/снимков и дополнительных 80 sight-ray запросов каждого кадра.

[Полный сравнивающий результат](../../../data/physics/stage2/performance-v12-resource3/frame-comparison.json) содержит все квантили первого чистого набора. Сырые CSV: [disabled](../../../data/physics/stage2/performance-v12-resource3/disabled/ragdoll-performance.csv), [active](../../../data/physics/stage2/performance-v12-resource3/active/ragdoll-performance.csv), [sleeping](../../../data/physics/stage2/performance-v12-resource3/sleeping/ragdoll-performance.csv). Полный цикл — действительные монотонные интервалы между post-world вызовами, включая game/render loop; forced dt не используется как FPS.

| Вариант, по 900 кадров | Полный цикл p50 /p95, мс | Изменение p50 /p95 к disabled | Native backend p50 /p95, мс | Средний FPS из циклов | Кадров ≤16,667 мс |
|---|---:|---:|---:|---:|---:|
| 20 анимированных трупов disabled | 8,359500 /14,546667 | контроль | 0,113500 /0,138000 | 110,20 | 865/900 |
| 20 active /480 частей | 9,192250 /19,860960 | **+9,96% /+36,53%** | 3,238334 /6,936750 | 95,22 | 822/900 |
| 20 sleeping /480 частей | 8,493292 /24,720291 | **+1,60% /+69,94%** | 0,121666 /0,162791 | 89,66 | 805/900 |

Основной заранее заданный критерий p50 **и** p95 ≤disabled не проходит для обоих физических вариантов. У sleeping native шаг мал, но полный цикл всё равно не проходит; эти измерения сами по себе не локализуют задержку в конкретной визуальной/игровой системе. Гипотезы не заменяют отрицательный результат, оптимизации и повторов после него нет.

## Native CPU20 того же чистого набора

[Полный native JSON](../../../data/physics/stage2/performance-v12-resource3/native-cpu20.json) содержит все 1020 count-записей каждой фазы и все 900 времён каждого случая. Execution/coverage/timing-valid — PASS; все 20 синтетических rigs сохраняются, active содержит 380 частей /все бодрствуют, sleeping —все спят. Это 19-part asset-free схема и native CPU без рендера, отдельно от игрового 24-part/480 workload.

| Native случай | p50 /p95, мс |
|---|---:|
| disabled | 0,000542 /0,000625 |
| active 20×19 | 0,892041 /2,977958 |
| sleeping 20×19 | 0,001458 /0,001792 |

Supplementary engineering budget 2 мс для active p95 также **FAIL**, но он не является заменой основного FPS-критерия. Первый native v12 result p95=14,421334 мс и все предыдущие ресурсно невалидные/неполные серии сохранены в [истории поправки](performance-amendment.md); они не выбираются вместо этого первого полностью clean matched набора.

Итог: coverage и функциональные проверки проходят, строгая производительность не проходит. Default `ragdoll=0` сохраняется; включение по умолчанию не квалифицировано.
