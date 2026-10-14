# Этап 6: recorder и боевой датасет

**Готов, 14 октября 2026 года.** На `dev-ubuntu` выполнена полная серия `stage6-v1`: запись, проверка причинных связей, групповые splits, train normalization и загрузка последовательностей. Независимый reporter имеет `status=complete`, ошибок нет. **Покрытие для первого диагностического BC пока недостаточно**: `diagnostic_bc_coverage_ready=false`.

Код продолжает текущий worktree `codex/npc-body-api`. Серверная сборка, симуляции и сырые данные находятся в `~/gothic-ai/body/`; на Mac возвращены только manifests и отчёты. Дата конфигурации и UTC начала сбора — 13 октября, дата этого отчёта в Europe/Moscow — 14 октября.

## Что реализовано

[Native recorder](../../../OpenGothic/common/game/bodytrajectory.h) ведёт ограниченную очередь на simulation thread без файлового I/O. [Python recorder](../../../scripts/body_trajectory_recorder.py) вычитывает её и сохраняет JSONL через ограниченную очередь writer. [Collector](../../../scripts/body_trajectory_suite.py) запускает серию эпизодов; [validator и loader](../../../scripts/body_trajectory_dataset.py) строят датасет; [независимый reporter](../../../scripts/body_trajectory_report.py) проверяет исходные доказательства. [Launcher](../../../scripts/body-trajectories-remote.sh) выполняет сбор в отдельном user unit с общим simulation lock.

Periodic observations дополняются точными числовыми снимками в фазе `pre_native_primitive`. Один `choice_id` native AI может содержать несколько примитивов: каждый получает собственный decision ID и execution ID. Записаны исходное намерение, нормализованный примитив, shared mask, принятие, настоящее начало и завершение/отмена. Retry анимации не создаёт повторных labels. Все события имеют общий `record_seq`; join проверяет actor, epoch, generation, simulation step и игровое время.

`accepted` отделён от `started`. Переход хранит следующее наблюдение и исход эпизода до reset. Незавершённые исполнения имеют явную отметку; окончание записи не выдаётся за физическую отмену. Scripted микроэпизоды проверяют API и имеют собственный non-expert provenance.

## Реальный корпус

Серия содержит 18 native дуэлей: 9 seeds × 2 физически зеркальные стороны на расстоянии 400 см; 3 успешных scripted микроэпизода дистанции, атаки и блока; 2 заранее выделенных OOD дуэли на расстояниях 800 и 1000 см. Все 20 native дуэлей дошли до terminal исхода. Прогрев, OFF comparator и три исторических baseline дуэли исключены из датасета.

| Записи | Количество |
|---|---:|
| Эпизоды датасета | 23 |
| Наблюдения | 16 421 |
| Решения / переходы | 1 236 / 1 236 |
| События исполнения | 4 897 |
| Принято / начато | 1 236 / 1 225 |
| Завершено / отменено / незавершено | 1 027 / 205 / 4 |
| Настоящие допустимые native expert labels | 733 |
| Пригодные завершённые BC labels | 681 |

Из 205 отмен 200 физические, 5 — явное завершение native операции без физического старта. Четыре незавершённых исполнения сохранены отдельно. Отменённые 52 expert labels не входят в BC loss. Native decision snapshots — 1 229; остальные 7 решений относятся к scripted проверкам. Пропуски, переполнения observer/control/trajectory очередей, ошибки capture/writer и смешение эпох отсутствуют; очереди полностью вычитаны, writer закрыт.

Независимый аудит повторно проверил 236 SHA сырых доказательств, 207 привязок episode source files, 3 115 файлов engine source archive и все 9 физических mirror pairs. Проверены observation → decision → execution → next observation, конечные masks, accepted/start/lifecycle и timestamps. Сырые журналы остались в `~/gothic-ai/body/data/stage6-v1/`.

## Train / eval / test / OOD

| Split | Seed groups | Эпизоды | Наблюдения | BC labels |
|---|---|---:|---:|---:|
| Train | 17, 61, 73, 89, 101, 127 | 15 | 10 985 | 455 |
| Eval | 29, 113 | 4 | 2 171 | 96 |
| Test | 43 | 2 | 1 500 | 58 |
| OOD | 151, 163 | 2 | 1 765 | 72 |

Split назначается целой группе `(setup_family, seed)`: оба участника, стороны, providers и связанные повторы не пересекают границы. Пропорции 70/15/15 округляются по девяти ID группам; OOD выделен отдельно. [Dataset index](dataset-index.json) сохраняет пути и SHA каждого эпизода.

[Normalizer](normalization.json) и class weights рассчитаны только по train. Whitelist содержит 184 числовых признака; teacher queue, выбор/IDs и будущие исходы исключены. Owner-dependent `can_decide` и `action_mask_valid` используются для проверки; в признаки входит физическая `native_decision_eligible`. Для 82 постоянных train features scale равен 1. Наличие дополнительных decision snapshots учитывается через `sequence_dt_game_ms`; исходный snapshot остаётся неизменным.

Ordered loader проверен на всех splits: 359 train, 72 eval, 49 test и 57 OOD chunks. Размер — 32 наблюдения, prefix/burn-in — 8. Память сбрасывается на границе chunk и прогревается prefix; prefix и locked ticks не входят в loss. Все 455 train labels появляются ровно один раз. `end_of_stream`, смерть, timeout и infrastructure status имеют разные поля.

## Покрытие и границы готовности

[Coverage report](coverage.md) использует настоящие завершённые expert decisions. Порог — минимум 20 train labels каждого включённого класса.

| Недостаточный класс | Сейчас | Нужно дополнительно |
|---|---:|---:|
| `turn_to_target` | 14 | 6 |
| `jump_back` | 0 | 20 |
| `wait_300` | 0 | 20 |

Остальные включённые классы проходят этот диагностический порог. Train содержит 740 наблюдений потери видимости и 4 520 плохой дистанции; условие стены отсутствует. Произвольное попадание луча не засчитывается как подтверждённый wall scenario. Исход дуэли также не выдаётся за успешность каждого отдельного действия.

Перед первым BC checkpoint нужно добрать указанные классы и сценарий стены с настоящим экспертным источником. Scripted demonstrations требуют отдельной проверки teacher и сохранения его provenance. OOD проверяет новые расстояния в той же арене с тем же противником; новая геометрия и противники ещё не покрыты. Согласование sampling BC и runtime остаётся задачей этапа 7. GRU, MLP, optimizer и GPU обучение в этом этапе не запускались.

## Baseline и влияние записи

Строгая пара recorder OFF/ON совпала: **3 049 ordered events и 1 478 physics steps**, включая резервные RNG поля и конечные native queue/RNG/pose/waits. Разрешена только нормализация runtime IDs. Choice, timing, повреждения, анимации, столкновения и позиции совпали.

Первый холодный reset отличается в десяти `fallback_variant` полях presentation RNG. Отдельный [четырёхэпизодный probe](rng-probe-result.json) показал: первый ON, прогретый OFF и прогретый ON совпадают полностью; холодный OFF отличается от всех трёх одинаково. Поэтому конфигурация содержит одну сохранённую OFF дуэль прогрева перед измеряемой парой. RNG поля не исключались из comparator, RNG не пересевался и движок под результат не исправлялся.

Исторический native-off baseline seeds 17/29/43 тоже совпал: **1 077 ordered mechanics и 3 198 physics steps**, ноль различий, дрейф позиции/угла — ноль. Guard `def_par_window` в этих трёх reference эпизодах не был задействован; это ограничение сохраняется.

## Ресурсы и проверки

Полный collector с дочерним runner занял **315,71 с wall**, **578,38 с CPU**, 183% CPU при квоте 200%; `/usr/bin/time` exit 0, swaps 0. Максимальный RSS — **740,81 MiB**; максимальный текущий RSS в 56 samples — **637,27 MiB**. Runner PID 3342156, 17 threads, 8–10 FD. Собственных GPU/audio/display descriptors нет. У transient unit были MemoryMax 12G, TasksMax 128 и timeout 2400 с. Эти числа относятся к сбору, без последующего reporter; скорость model inference и optimizer не измерялась.

Прошли **90 targeted Python cases**: recorder 18, dataset 42, reporter 30; native resolver на сервере — **2 419 assertions**. Негативные проверки обнаруживают missing/duplicate/out-of-order events, несовпадение masks и labels, будущие timestamps, source tampering, leakage между splits, ошибки normalizer и незавершённый lifecycle. Полная pytest/GUI регрессия не запускалась: PR в этом шаге не создавался.

## Сервисный baseline и повторная оценка

Во время сбора `gothic-voxtral` был active/running с PID 3366349; `gothic-voxcpm` уже был inactive/dead с PID 0 до запуска и остался таким после. Состояния и PID не изменились. Доступность TTS этим прогоном не подтверждается. В более позднем read-only preflight он уже был active/running с PID 3359086; наш launcher сервисы не запускал и не перезапускал. Docker и production containers не менялись.

Изначальный frozen reporter наследовал требование «все сервисы active» и отказал только по этому пункту. Исправленный stage-6 comparator проверяет неизменность фактического baseline, включая заранее неактивный сервис. Отсутствующие/некорректные поля, active с PID 0 и любое изменение состояния/PID по-прежнему отклоняются.

Выполнена read-only повторная оценка тех же неизменённых файлов вне каталога run. Исходный report и source archive сохранены; причина и оба reporter SHA явно записаны в [финальном JSON](body-trajectories.json) и [компактной сводке](validation-summary.json). Ни критерии механики/датасета, ни raw данные не менялись. Исходный reporter SHA `45bb794958027efe2562730cee38f5543e1e1017a02cfae3487b89eea6fe7158`; финальный `dcde0f738f1ba89b8571824f92ec923b335514aee56d5cf0311f33d943d64bc8`.

Для воспроизведения проверки на сервере сохранены финальный reporter и его зависимости в `~/gothic-ai/body/reports/stage6-v1-final/code/`. `--output-dir` должен быть новым каталогом вне immutable run:

```sh
python3 ~/gothic-ai/body/reports/stage6-v1-final/code/scripts/body_trajectory_report.py \
  ~/gothic-ai/body/data/stage6-v1 \
  --config ~/gothic-ai/body/reports/stage6-v1-final/code/evals/npc-body/body-trajectories-v1.json \
  --native-reference ~/gothic-ai/body-headless/runs/stage3-v1/native-off \
  --allow-reporter-regrade \
  --output-dir ~/gothic-ai/body/reports/stage6-v1-recheck
```

Полные hashes build/source/config/recorder/validator/normalizer находятся в [manifest](manifest.json), [финальном report](body-trajectories.json) и [validation summary](validation-summary.json). Контракт и обычный запуск новых серий описаны в [документации recorder](../../../docs/npc-body-trajectories.md).
