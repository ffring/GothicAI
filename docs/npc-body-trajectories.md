# Боевые траектории и датасет

Этап 6 [плана GRU](npc-body-control-plan.md) использует [headless арену](npc-body-arena.md) на `dev-ubuntu`. Сбор, валидация, split и загрузка последовательностей работают без модели. Исходные траектории находятся в `~/gothic-ai/body/data/`; в репозиторий возвращаются конфигурация, manifests и отчёт.

**Проверен 14 октября 2026 года:** [полная серия stage6-v1 и ограничения](../reports/npc-body/2026-10-14-trajectories/validation-notes.md), [dataset index](../reports/npc-body/2026-10-14-trajectories/dataset-index.json), [coverage](../reports/npc-body/2026-10-14-trajectories/coverage.md). 23 эпизода дали 681 пригодную BC label, из них 455 в train. Pipeline готов; coverage для первого BC остаётся недостаточным по трём классам и условию стены.

## Наблюдение, решение и исполнение

Recorder читает отдельную ограниченную очередь native событий. Она объединяет обычные числовые снимки, точные снимки перед выбором примитива, решения и исполнение с единым порядковым номером. Новая очередь не пишет файлы в simulation thread; внешний Python recorder сохраняет JSONL через ограниченную очередь writer. Переполнение, ошибка записи и пропущенный номер означают явную ошибку сбора.

Обычные снимки на 20 Hz сохраняются для recurrent memory. Native teacher дополнительно фиксирует числовое состояние непосредственно перед исполнением нормализованного примитива: shared resolver задаёт action и mask. Этот снимок имеет отдельную фазу и источник. У native очереди один `choice_id` может содержать несколько примитивов, поэтому каждому решению присваивается собственный ID; исходные intent/choice остаются диагностикой. Превращение атаки в поворот также сохраняется явно. Внутреннее завершение strafe и неподходящие/no-op решения не создают BC label.

`accepted` не означает физическое начало. Lifecycle хранит реальные `started`, `completed`, `rejected` и `cancelled`, включая незавершённые действия на границе эпизода. Переход связывает исходный снимок, решение, исполнение, следующее наблюдение, длительность и конечный исход. Terminal состояние берётся до reset. Мир, actor и control generation не пересекаются при join.

Файлы эпизода содержат `trajectory-manifest.json`, `raw-trajectory.jsonl`, канонический `records.jsonl`, счётчики drain в `capture-packets.jsonl` и native ответы в `source-integrity.json`; исходные IPC и механика остаются рядом как независимое доказательство. Их SHA фиксируются после закрытия движка, поскольку следующий reset дописывает cleanup предыдущего эпизода. Ordered loader использует канонические записи. Исходный native snapshot сохраняется целиком для проверки, а признаки модели выбираются фиксированным whitelist. `queued_primitive`, `choice_id`, command/execution IDs и исходы не входят в признаки: native очередь содержит ответ teacher, а terminal outcome появляется после действия. Зависимые от владельца `can_decide` и `action_mask_valid` также остаются проверочными полями; в признаки входит физическая `native_decision_eligible`.

Loss применяется только к настоящим допустимым teacher decision opportunities. Locked ticks сохраняются с `decision_valid=false`; они не становятся примерами класса `continue`. Scripted микроэпизоды имеют собственный источник и не выдаются за native экспертные демонстрации. Модель в этом этапе не обрабатывает наблюдения; соответствующий признак recorder хранит явно.

Точные teacher snapshots добавляются к periodic потоку, поэтому последовательность не имеет строго равномерного шага 50 мс. В ней сохраняются источник, фаза, исходный `dt_game_ms` движка и `sequence_dt_game_ms` между соседними каноническими наблюдениями актёра. Normalizer и loader используют второй интервал; исходный снимок остаётся неизменным. Этап обучения должен использовать эту семантику явно и согласовать её с runtime sampling, не заменяя features ближайшим post-action кадром.

## Проверка и разделение

Validator проверяет весь журнал, а не только итоговый флаг driver: последовательность записей, эпохи, времена и фазы, однозначную связь observation → decision → execution → next observation, recorded mask, lifecycle и явные незавершённые действия. Признаки событий не могут содержать события из будущего. Replay здесь проверяет журнал; он не воспроизводит физику без движка.

Конфигурация [body-trajectories-v1.json](../evals/npc-body/body-trajectories-v1.json) задаёт 18 native дуэлей, защитные и боевые микроэпизоды, проверку записи on/off и отдельный OOD набор с новой начальной дистанцией. Перед строгим on/off сравнением сохраняется полная native дуэль с выключенным recorder для прогрева resource cache. Первый холодный reset отличается в presentation RNG; повторные OFF/ON должны совпасть даже по резервному RNG полю. Native механика также сравнивается с историческим baseline. Покрытие сообщает количество настоящих labels каждого action, успешные/неуспешные примеры и недостаточно представленные условия, включая стену, потерю видимости и плохую дистанцию.

Splits назначаются целым группам по setup family и seed. Обе роли, зеркальные стороны, providers и связанные повторы группы остаются вместе. Начальная пропорция — 70/15/15 для train/eval/test с неизбежным округлением по небольшому числу групп. OOD назначается до сбора и хранится отдельно; test/OOD не используются для выбора checkpoint.

Normalizer и class weights вычисляются только по train. Coverage gate первого диагностического BC — минимум 20 настоящих teacher labels каждого включённого action; недостаток примеров указывается явно. Дублирование labels не исправляет покрытие. Успешная проверка корпуса и готовность покрытия для BC отражаются отдельно.

Loader выдаёт ordered chunks с prefix/burn-in (начальные параметры: 32 наблюдения и 8 prefix observations), loss mask и reset границами по episode/entity/generation. Каждый chunk начинает память с нуля и прогревает её prefix; burn-in не входит в supervised loss. `end_of_stream` отделён от настоящих `terminated`, `truncated` и `infrastructure`, поэтому лимит времени не превращается в смерть. Ни chunk, ни bootstrap не переходят через reset. Constant train features имеют scale 1, чтобы новый binary признак в OOD не раздувался из-за деления на практически нулевую variance.

Python API [body_trajectory_dataset.py](../scripts/body_trajectory_dataset.py): `validate_episode(path)`, `build_dataset(paths, output)`, `OrderedSequenceLoader(dataset, split="train", chunk_length=32, burn_in=8)`. Independent [reporter](../scripts/body_trajectory_report.py) повторно проверяет сырые файлы, split, train population/statistics, все loader splits и parity.

## Запуск на dev-ubuntu

```sh
scripts/body-trajectories-remote.sh preflight
scripts/body-trajectories-remote.sh sync
scripts/body-trajectories-remote.sh build
scripts/body-trajectories-remote.sh run stage6-validation
scripts/body-trajectories-remote.sh collect stage6-validation
```

Launcher переиспользует private incremental build текущего worktree в `~/gothic-ai/body-api/`. Каждый run сохраняет собственные source archive и бинарник. Отдельный transient user unit ограничивает CPU до 200%, память до 12G и время до 2400 с; общий simulation lock допускает один серверный движок. Mac выполняет SSH orchestration и лёгкие parser/fake tests. Сырые JSONL, assets/saves, бинарники и будущие checkpoints на Mac не зеркалируются.

Preflight сохраняет фактические состояния/PID ASR и TTS. CPU сбор может работать с заранее неактивным сервисом; после серии reporter требует неизменности его baseline и отдельно сообщает доступность. Ошибочный или изменившийся service identity не проходит проверку.

`smoke` проверяет интерфейс и writer; он не заменяет полную серию и dataset gate. На этапе 6 не запускаются GRU, optimizer или GPU обучение. Ресурсный gate этапа 7 остаётся отдельной проверкой.
