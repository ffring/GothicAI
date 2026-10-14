Первое решение в старых `attack` и `block` имеет одинаковые 184 исходных и нормализованных признака: проверены обе стороны для GRU и MLP замороженным stdlib packing. Время наблюдения — 16327 мс, начало эпизода — 16294 мс, delta — 33 мс. Противник ещё не показывает `attack_anim`/`prehit`, goal — `native_combat`; задача «блокировать» в этом входе не выражена. Для MLP/attack/side0 нормализованный вектор пересчитан из сохранённого запроса: runtime остановился до forward. Остальные семь SHA подтверждаются журналом.

В старом успешном defensive curriculum противник уже физически атаковал перед решением NPC. Пример `demo-train-17-0`:

| Действие | Решение | Физический старт | Incoming hit | Завершение |
| --- | --- | --- | --- | --- |
| Block (9) | 20861 | 20894 | 21027 | 21527 |
| Jump back (5) | 22061 | 22094 | 22227 | 22661 |

В обоих случаях hit происходит через 133 мс после старта защиты. Перед решением наблюдаются `attack_anim=true`, `prehit=true`, range/focus допустимы. Реальные `hit_attempt` связаны с последующими native `defence_result` того же game timestamp и обратных actor IDs; HP primary сохраняется.

В новом train corpus 43 завершённых блока и два завершённых jump back не содержат ни одного incoming `hit_attempt` внутри своего исполнения. Например, Block в `combat-train-191-open-0` закончился в 17994 мс, а удар пришёл в 18127 мс — на 133 мс позже. После блока наблюдение в 18027 мс показывало `attack_anim=false`/`prehit=false`; teacher начал Attack (6), который был отменён ударом в 18127 мс. Отказ в экспертной разметке соответствует физическим событиям.

Следующий диагностический протокол — отдельно объявленный `block_reaction`: одинаковая реальная подготовка Native/GRU/MLP, primary без команд и native chooser до видимого in-range windup атакующего, затем явная activation boundary и новый начальный model clock/context. Критерий остаётся actual blocked hit при HP300 и нулевом damage. Старые v6 block failures сохраняются; измерение этого нового fixture выполняется отдельно.

[JSON evidence и source SHA](/Users/kaifat/.codex/worktrees/npc-body-api/gothic/reports/npc-body/2026-10-14-combat/defense-diagnosis-v6/evidence.json) связывают восемь исходных запросов, normalizer, frozen packing code, старые native defense events и новые train manifests. Этот анализ не запускал нейросетевые forward, игры или обучение и не менял исходные артефакты.
