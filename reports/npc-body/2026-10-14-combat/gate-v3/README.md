# Gate-v3: known audit R2

Независимый known audit R2 — **PASS**, `issues=[]`. Проверены только закрытые
known этапы: 3 shadow, 24 ordinary realtime и 6 reaction случаев. Внутренний
`audit_wall_s` — 35,455 с (полный запуск около 35,5 с). Load и fresh screening
не читались и не запускались; overall stage8 quality credit — false.

GRU выполнил два настоящих блока (9) на seed 223, обе стороны. Наблюдение
замаха — 16361 мс, физический старт блока — 16427 мс, входящий удар —
16527 мс. Реакция заняла 66 игровых мс в настоящем окне 166 мс. Каждый блок
связан с mapped native `defence_result`, HP300, received damage 0,
neural control 1 и fallback 0. Память закрыта на терминальной границе.

| GRU сторона | Collector → send, мс | Model step p95, мс | Observation → arbitration, мс |
| --- | ---: | ---: | ---: |
| 0 | 5,691093 | 1,255344 | 6,895532 |
| 1 | 6,285137 | 1,459747 | 7,511438 |

Model p95 использует четыре samples на случай; остальные значения имеют
по одному sample. Эти известные короткие эпизоды не заменяют нагрузку и
проверку на 20 fresh seeds. Выбранные веса/normalizer остаются прежними.

Первый reader-v1 требовал `shadow=true` для native comparator, хотя raw
controls пусты и ownership не приобреталось. Его failed report сохранён.
R2 изменяет только этот predicate в отдельно замороженном external helper;
проверены original SHA, exact declared replacement и первый failed-report SHA.
Движок, gate code, веса и raw события этой коррекцией не менялись.

[Compact report](known-summary.json) сохраняет 295 raw SHA bindings и путь,
размер и SHA полного серверного отчёта. [R2 bindings](r2-bindings.json)
содержат точные full/known reader snapshots, environment/declaration hashes
и привязку исходного reader failure. Большие raw журналы и веса не скачивались;
этот extractor не запускал модели, тесты, engine или сервисные workloads.

До load обнаружены невключённые startup/compact/dispatch hooks в коде его
драйвера. Это находка при подготовке, без model failure: load/fresh никогда
не запускались. Для проверки этих hooks нужен новый frozen gate-v4 с теми же
весами и явной привязкой уже закрытого known результата gate-v3. Этап 8 —
**IN_PROGRESS**; общего боевого PASS пока нет.
