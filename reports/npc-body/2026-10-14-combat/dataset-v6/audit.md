# Аудит датасета и selection view — stage8-combat-v6

Дата: 2026-10-14T08:45:05.920067+00:00. Источник: `/home/kaifat/gothic-ai/body/data/stage8-combat-v6`.
Это диагностический аудит данных до завершения обучения; он не меняет preregistered gates. Полные SHA, исходные episode IDs, веса классов и контексты находятся в [audit.json](audit.json).

Parent содержит 144 эпизода. Selection view содержит 135: 112 train, 8 fresh eval, 7 сохранённых test и 8 OOD. Из eval удалены все 9 ранее открытых legacy-эпизодов. Выбор весов использует только seed 223/227, оба физических side и open/near_wall, с непрерывным состоянием на реальных границах actor/epoch/generation. Test/OOD не участвуют в выборе.

Все проверки привязок прошли. Нормализация рассчитана только по train, содержит ровно 184 признака и 52 892 train-наблюдения. Список её train episode IDs совпадает с датасетом; parent и child используют побайтно одинаковую нормализацию. Новые genuine teacher labels после причинного выравнивания: approach 183, turn 67, front attack 158 и по 24 для strafe left/right и wait 200/300.

| Действие | Train labels | Fresh eval labels |
| --- | ---: | ---: |
| approach | 374 | 67 |
| turn_to_target | 93 | 23 |
| strafe_left | 58 | 0 |
| strafe_right | 61 | 0 |
| jump_back | 25 | 0 |
| attack_front | 205 | 59 |
| attack_left | 36 | 0 |
| attack_right | 55 | 0 |
| block | 37 | 0 |
| wait_200 | 75 | 8 |
| wait_300 | 49 | 0 |

Всего train — 1 068 меток, fresh eval — 157. Семь классов отсутствуют в fresh eval; fixed macro F1 по всем 11 имеет верхнюю границу 4/11 ≈ 0,363636 даже при идеальной классификации представленных классов. Fresh eval не измеряет блок, отскок, боковые атаки или манёвры. Train block=37 и jump_back=25 не выросли относительно v9: новые полноформатные бои не дали ни одной подтверждённой primary defense label. Балансировка оставляет jump_back вес 3,884, block 2,624, front attack 0,474; наличие больших весов не заменяет новые защитные примеры.

В 24 новых primary train-боях есть 806 физически допустимых periodic ticks: 408 размеченных и 398 без loss-label. Из последних 260 не имеют команды, 138 имеют команду без подтверждённой метки. Среди непромеченных состояний 236 — incoming attack вне w-range, 46 — incoming attack внутри w-range. В 8 primary eval-боях соответственно 180/142/38; все 3 состояния incoming attack внутри w-range не размечены. Ещё 15 eval-меток принадлежат native opponent.

Подсчёт использует только immutable approved records, exact observation_seq для scripted primary и usable_for_bc transition; SHA manifest/records сверены с индексом. Физическая eligibility не означает, что каждый tick был последним доступным кадром при команде: initial/cadence rows тоже могут остаться без команды. Однако правило teacher намеренно не выбирает действие при incoming attack вне w-range с focus5; эти пропуски нельзя превращать в wait labels. Runtime выбирает разрешённый primitive в своих eligible command boundaries, поэтому поведение на таких состояниях требует отдельной проверки после фиксации весов.

Forward verification обоих моделей прошла на dev-ubuntu: GRU p95 0.140443 мс, MLP p95 0.119564 мс. Обучение и боевое качество этим аудитом не подтверждаются. Модели, игры и тесты агент аудита не запускал; test/OOD predictions и fresh-screen gameplay не открывал.

| Привязка | SHA-256 |
| --- | --- |
| Parent dataset | `e0dcadc86555f3f110504ee84d0e2ef0e9ddc48d89308063f02e86bef0ab0313` |
| Selection dataset | `f75fdcb02ec9389bab77fac1ef2da661d5f48f61add36727a1a57c6ed3fbcfe6` |
| Selection profile | `e9c5ca6db72f21e81fd874826edb7714fa88be17c9efcaf0635425c56b7dc6d4` |
| Train normalization | `76fc89990193b087085787d0aada1cb4683db934a81be17cbf0bc51d96f459b6` |
| Engine compatibility | `d32850fa13f18bb877676e60e98c17872b1bddccf544e86c6e570a39a6fd36e0` |
| Frozen stage8 config | `506cae01813644e53acaf5d9124f887d4ebdab5e198a89e8ce07840b6778b9ac` |
