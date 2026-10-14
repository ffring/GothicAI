# Gate v8: known PASS, load FAIL

Known audit — **PASS**, issues пусты: закрыты 3 shadow, 24 ordinary realtime
и 6 reaction cases. Audit process wall — 62,755 с; внутренний reader wall —
62,663423 с. Последующий [закрытый load](load-failure.md) — **FAIL**:
5/12 cohorts завершены, следующий GRU1 остановлен после строгого masked отказа.
Native protected TTS/decider/Gemma p95 превысили бюджет10%; неполный GRU5/20
не оценивается. Fresh screening не запускался.
Этап8 остаётся **IN_PROGRESS**, общего боевого quality credit нет.

[Компактный JSON](known-summary.json) сохраняет метрики всех 33 case,
CPU/GC profiles закрытых фаз, 33 выбранные raw SHA bindings и suite SHA.
Все 295 raw bindings остаются в связанном оригинальном отчёте.
Original report SHA:
`8dd1c45da22c0a450973af5575673c027b3b60bed6709f4d67ee4b100737439b`.

| Метрика | GRU | MLP |
| --- | ---: | ---: |
| Максимальный case model p95, мс | 2,209114 | 2,428685 |
| Максимальный case observation → arbitration p95, мс | 18,053185 | 15,547865 |
| Максимальный case collector → send p95, мс | 8,422820 | 12,471695 |
| Минимальный control в зарегистрированном repaired наборе | 1,0 | 0,956522 |

Максимумы p95 включают все сохранённые cases; это не pooled percentiles.
Counts и case names сохранены. Repaired набор — ordinary distance/attack/duel
и prepared reaction. Старые neutral ordinary block cases оставлены отдельно:
минимальная GRU fraction среди **всех** сохранённых cases — **8/9 =0,888889**
в `realtime-block-223-0-gru`. Этот отказ не скрыт и не превращён в PASS;
known repair оценивает общий настоящий incoming attack до первого решения.
Quiet block diagnostics не переписаны.

Оба GRU reaction случая — настоящий task_success: HP300, received damage0,
blocked hit1, neural control1 и runtime fallback0. Action9 начал исполнение
через 66 игровых мс при окне166 мс. Model p95 — 1,848477/1,753740 мс по
четырём шагам; collector-send — 8,422820/7,286479 мс.

Проверены **2518 actual forward calls**: shadow802, ordinary1700, reaction16.
Каждый использовал requested/actual PyTorch intra-op1; interop12 сохранён.
Веса, normalizer и quality thresholds прежние.

GC profile каждой закрытой фазы доказал opt-in bootstrap private375→0,
static freeze183578/183578/183641 и release0. Dynamic GC включён,
thresholds700/10/10 сохранены, dropped0. Callback events141/341/42;
максимальный generation2 span — 11,654867 мс shadow и 9,031683 мс ordinary,
в короткой reaction-фазе generation2 не зарегистрирован. GC остаётся активным;
старые [startup FAIL gate-v7](../gate-v7/README.md) и
[load FAIL gate-v6](../gate-v6/load-failure.md) не меняются. Новый результат
не устанавливает причину старого gap169 мс.

Извлечение known — лёгкое read-only чтение закрытого report, трёх небольших GC
files и двух episode metadata. Load тогда не читался; его отдельный compact
извлечён после закрытия. Большие completed IPC остаются на сервере,
модели/тесты/runner/сервисы extractor не запускал.
