# Этап 3: частые числовые наблюдения NPC

Статус: **подтверждён**.

[Схема/runtime](../../../docs/npc-body-observations.md), [план](../../../docs/npc-body-control-plan.md), [конфигурация](../../../evals/npc-body/body-observe-v1.json), [локальные metadata, ignored](../../../data/npc-body/observe/stage3-v1). Полный raw evidence хранится на сервере: `/home/kaifat/gothic-ai/body-headless/runs/stage3-v1`.

| Критерий | Результат | Проверенная граница |
|---|---|---|
| suite | пройден | Completed full server suite; smoke/partial cannot confirm stage 3 |
| headless | пройден | Explicit device/window/audio attestation |
| runtime:performance-off-0 | пройден | Completed native simulation with actual /proc device checks |
| runtime:performance-on-0 | пройден | Completed native simulation with actual /proc device checks |
| bounded_state:performance-on-0 | пройден | Actual native statistics: no invalid rows, bounded queue and runtime identity registry |
| numeric:performance-on-0 | пройден | Raw schema, identity, units/privacy, availability and bounded delivery |
| lossless:performance-on-0 | пройден | Normal polling has no silent or declared row/event loss |
| actors:performance-on-0 | пройден | Four distinct runtime actors |
| timing:performance-on-0 | пройден | Measured total observer simulation-thread work per step |
| runtime:performance-on-1 | пройден | Completed native simulation with actual /proc device checks |
| bounded_state:performance-on-1 | пройден | Actual native statistics: no invalid rows, bounded queue and runtime identity registry |
| numeric:performance-on-1 | пройден | Raw schema, identity, units/privacy, availability and bounded delivery |
| lossless:performance-on-1 | пройден | Normal polling has no silent or declared row/event loss |
| actors:performance-on-1 | пройден | Four distinct runtime actors |
| timing:performance-on-1 | пройден | Measured total observer simulation-thread work per step |
| runtime:performance-off-1 | пройден | Completed native simulation with actual /proc device checks |
| runtime:performance-off-2 | пройден | Completed native simulation with actual /proc device checks |
| runtime:performance-on-2 | пройден | Completed native simulation with actual /proc device checks |
| bounded_state:performance-on-2 | пройден | Actual native statistics: no invalid rows, bounded queue and runtime identity registry |
| numeric:performance-on-2 | пройден | Raw schema, identity, units/privacy, availability and bounded delivery |
| lossless:performance-on-2 | пройден | Normal polling has no silent or declared row/event loss |
| actors:performance-on-2 | пройден | Four distinct runtime actors |
| timing:performance-on-2 | пройден | Measured total observer simulation-thread work per step |
| runtime:bounded-queue | пройден | Completed native simulation with actual /proc device checks |
| bounded_state:bounded-queue | пройден | Actual native statistics: no invalid rows, bounded queue and runtime identity registry |
| numeric:bounded-queue | пройден | Raw schema, identity, units/privacy, availability and bounded delivery |
| timing:bounded-queue | пройден | Measured total observer simulation-thread work per step |
| runtime:privacy-edges | пройден | Completed native simulation with actual /proc device checks |
| bounded_state:privacy-edges | пройден | Actual native statistics: no invalid rows, bounded queue and runtime identity registry |
| numeric:privacy-edges | пройден | Raw schema, identity, units/privacy, availability and bounded delivery |
| lossless:privacy-edges | пройден | Normal polling has no silent or declared row/event loss |
| privacy_oracle:privacy-edges | пройден | Visible/occluded/hidden movement/reappearance compared with separate native truth |
| timing:privacy-edges | пройден | Measured total observer simulation-thread work per step |
| runtime:native-off | пройден | Completed native simulation with actual /proc device checks |
| runtime:native-on | пройден | Completed native simulation with actual /proc device checks |
| bounded_state:native-on | пройден | Actual native statistics: no invalid rows, bounded queue and runtime identity registry |
| numeric:native-on | пройден | Raw schema, identity, units/privacy, availability and bounded delivery |
| lossless:native-on | пройден | Normal polling has no silent or declared row/event loss |
| timing:native-on | пройден | Measured total observer simulation-thread work per step |
| runtime:passive-realtime | пройден | Completed native simulation with actual /proc device checks |
| bounded_state:passive-realtime | пройден | Actual native statistics: no invalid rows, bounded queue and runtime identity registry |
| numeric:passive-realtime | пройден | Raw schema, identity, units/privacy, availability and bounded delivery |
| lossless:passive-realtime | пройден | Normal polling has no silent or declared row/event loss |
| native_activity:passive-realtime | пройден | Every actor has native choices, accepted attempts and hit attempts in each of ten 60 s buckets, plus actual HP loss |
| actors:passive-realtime | пройден | Four distinct runtime actors |
| timing:passive-realtime | пройден | Measured total observer simulation-thread work per step |
| wall_clock:passive-realtime | пройден | Same-process steady_clock joins every periodic actor row to its actual emission step |
| passive_10min | пройден | At least 600000 game ms in realtime with raw endpoint evidence |
| realtime_cadence | пройден | Periodic interval p95 <= 100 game ms at configured 20 Hz |
| realtime_wall_cadence | пройден | Actual emission periodic interval p95 <= 100 wall ms in realtime |
| native_events | пройден | Actual source event IDs reach samples by the next simulation step |
| privacy_coverage | пройден | Actual visible-to-hidden target with aged last-known position and no current privileged state |
| bounded_queue | пройден | Withheld drain provokes real bounded overflow with exact drop/sequence accounting |
| observer_cost | пройден | Total observer simulation-thread p95 <= 2000 wall us for four actors |
| throughput | пройден | Same-scenario >=3 paired repeats: median processing throughput drop <= 5% |
| native_parity | пройден | Ordered native choice/consume/animation/physics/hit/damage parity with watch disabled/enabled |
| provenance | пройден | Source/build/config/save/assets hashes; no model and passive ownership |

## Частота, события и нагрузка

Пассивный realtime: 600633 игровых мс, 52812 снимков; периодические 48008, событийные 4804, пропуски 0. Game и wall интервалы измеряются отдельно.

Период 50 мс при fixed 30 Hz квантован игровыми шагами 33/33/34 мс. p95 периодических интервалов 66 мс; максимум 66 мс. Событийные дополнительные снимки не используются для искусственного улучшения periodic cadence.

Фактический wall интервал периодических снимков: p95 **67.760 мс**, максимум **91.302 мс**. Это emission timestamps одного engine process, а не время получения пакета Python клиентом.

В поток доставлено 8244 событий; максимальная задержка от native source — 0 sim step. Это измерение доставки в числовой поток; latency GRU и боевых команд ещё не измерялась.

Добавленная работа наблюдений в simulation thread: p95 273.00 wall µs. Максимум всех серий **63325.00 µs** отмечен в `performance-on-0`. Cost складывает sample и transport одного sim step для четырёх NPC: native hooks, признаки/видимость/16 лучей, сериализацию журнала, watch list и кодирование/отправку JSONL. Запись timing journal исключена из instrumentation; paired throughput учитывает её.

В десятиминутном realtime p95 **271.00 µs**, максимум **3321.00 µs** на sim step. Пакетный transport отдельно: 302 отправки, p95 **2328.00 µs**, максимум **3075.00 µs** на bulk пакет нескольких samples. Стоимость целого пакета не равна стоимости одного actor sample; transport включён в общую статистику шагов.

| Парный прогон | Off, substeps/s | On, substeps/s | Падение |
|---|---:|---:|---:|
| 0 | 70.80 | 69.53 | 1.79% |
| 1 | 68.43 | 70.19 | -2.57% |
| 2 | 69.41 | 71.39 | -2.86% |

Медиана off/on: 69.41 / 70.19 substeps/s; падение -1.13%. Парные серии используют одинаковые сценарии и sim steps; processing wall включает запрос advance и получение наблюдений. Отрицательное падение отражает шум парных измерений и не доказывает ускорение от подписки.

## Ресурсы и realtime pacing

Ресурсы измерены через `/proc` между первым и последним sample каждой фазы после reset/watch. VmHWM — максимум всего общего engine process, включая предыдущие фазы; его нельзя трактовать как отдельный peak текущего эпизода.

| Прогон | RSS min–max, MiB | VmHWM max, MiB | CPU user+sys, с | Среднее занятых CPU cores | Threads / FD min–max |
|---|---:|---:|---:|---:|---|
| performance-off-0 | 551.64–562.20 | 562.20 | 76.80 | 2.00 | 1–17 / 7–8 |
| performance-on-0 | 614.23–619.24 | 707.90 | 77.95 | 1.98 | 17–17 / 7–8 |
| performance-on-1 | 630.16–630.17 | 719.47 | 77.25 | 1.98 | 17–17 / 7–8 |
| performance-off-1 | 633.83–633.88 | 723.17 | 79.46 | 2.00 | 17–17 / 7–8 |
| performance-off-2 | 633.89–633.93 | 733.94 | 78.20 | 1.99 | 17–17 / 7–8 |
| performance-on-2 | 633.93–633.96 | 734.02 | 75.81 | 1.98 | 17–17 / 7–8 |
| bounded-queue | 633.91–633.92 | 734.19 | 6.08 | 2.00 | 17–17 / 7–8 |
| privacy-edges | 634.05–634.05 | 734.19 | 4.55 | 2.01 | 17–17 / 7–8 |
| native-off | 634.18–634.63 | 737.30 | 47.87 | 2.00 | 17–17 / 7–9 |
| native-on | 634.19–634.20 | 737.70 | 46.91 | 1.99 | 17–17 / 7–9 |
| passive-realtime | 634.33–634.58 | 737.70 | 564.17 | 0.94 | 17–17 / 7–8 |

Десятиминутный realtime: RSS **634.33 → 634.58 MiB**, дельта **0.25 MiB**. Sim 600633 / wall 600612 мс после reset, ratio 1.00003; максимальное отставание pacing **41 мс**. Намеренное realtime ожидание не включается в processing throughput парных lockstep серий.

## Корректность и границы

Снимки имеют общую фазу `post_simulation_post_animation`, runtime ID экземпляра, generation и единую последовательность periodic/event rows. Переключение watch не захватывает тело. `can_decide=false`, `control_allowed=false`, `action_mask_valid=false`: 12 значений `availability_mask` — консервативная доступность native примитивов; общий resolver и действительные action masks относятся к этапу 4.

Текущие признаки невидимой цели отсутствуют. Последние известные положение и скорость маркируются своим возрастом; отдельный debug trace хранит истину для проверки, без включения в policy observations. Проверка hidden_move меняет настоящее положение/HP за преградой и проверяет замороженное last-known состояние.

Нативная механика сравнивается при выключенной и включённой подписке: ordered choice/consume, animation, root motion/collisions, попадания/урон и исходы seeds 17/29/43. Такая parity подтверждает пассивность наблюдений, а не качество обученного контроллера.

ASR/TTS и GPU проверялись только по состояниям units, shared GPU snapshot и устройствам процесса. Их latency не измерялась. Нейросеть, обучение, RPC захвата тела и внешний исполнитель ещё не реализованы этим этапом.

| Provenance | Значение |
|---|---|
| source_sha256 | `9317071045396cd86d1920b4ac1a3f83ef62db7687bcfb0893f7c45a08cfbf59` |
| binary_sha256 | `f6e8a1e22125390f3064cf9121575aac0762a80ab2b5215506f63c5218a8ab44` |
| engine_library_sha256 | `4d23e507992073ae2f5999ea0b703c7b21124acbfbf491104e471f3cd86ade99` |
| save_sha256 | `c2852f4c009f23225e1c9c70f564ca36b4c12e8220736d1c72a1dbfce8d3372f` |
| scenario_sha256 | `4219e1301612e8c290b995a1086d92aca8027a295c02b71355ef0c3f169c9187` |
| config_sha256 | `2c22ee9de056314316a570b252d2767eae69d3fa4e3701b662972ce9c0984bed` |
| settings_sha256 | `c6e42f176f65e7e2302351a4e87ee478b0e846e453968604902922cf6f752867` |
| host | `dev-ubuntu` |
| sim_fps | `30` |
| time_multiplier | `1` |
| recorded_at_utc | `2026-10-13T15:34:39.358577+00:00` |
| completed_at_utc | `2026-10-13T15:49:38.872025+00:00` |
| remote_run_dir | `/home/kaifat/gothic-ai/body-headless/runs/stage3-v1` |
