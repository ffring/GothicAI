# Этап 2: настоящий headless runner

Статус: **подтверждён**.

[Протокол/runtime](../../../docs/npc-body-headless-runner.md), [план](../../../docs/npc-body-control-plan.md), [контракт](../../../docs/npc-body-combat-contract.md). [Сырые данные, ignored](../../../data/npc-body/headless/stage2-v3), [suite.json](../../../data/npc-body/headless/stage2-v3/suite.json), [manifest.json](../../../data/npc-body/headless/stage2-v3/manifest.json).

| Критерий | Результат | Проверенная граница |
|---|---|---|
| Полная серия | пройден | suite=passed, full_stage2_evidence=True, required_gate_failures=[], manifest=passed |
| Без graphics/audio device | пройден | Проверены явные flags отсутствия device/window/playback и renderer=none |
| rendered vs lockstep | пройден | status=matched, profile=full, comparison=rendered_vs_headless |
| rendered vs lockstep-delayed | пройден | status=matched, profile=full, comparison=rendered_vs_headless |
| rendered vs realtime | пройден | status=matched, profile=full, comparison=rendered_vs_headless |
| lockstep vs lockstep-delayed | пройден | status=matched, profile=full, comparison=headless_schedule_invariance |
| lockstep vs realtime | пройден | status=matched, profile=full, comparison=headless_schedule_invariance |
| historical vs branch replay | пройден | status=matched, profile=archived_reference, comparison=rendered_vs_headless |
| Полнота архивного replay | пройден | Complete trace: seeds 17/29/43, actor setup и terminal события |
| Replay: 17 веток ровно один раз | пройден | entries=17, accepted_blocks=17, consumed=17 |
| Зрение/преграды/звук | пройден | Оба case: настоящие world rays, native sight и завершение звука по metadata duration |
| 100 последовательных resets | пройден | count=100, status=passed, issues=[] |
| JSONL IPC | пройден | Freeze, stop на native decision, partial line в realtime, pipeline и clean close |
| lockstep: runtime evidence | пройден | Completed, device attestation, /proc samples и complete melee trace всех трёх seeds |
| lockstep: native perceptions | пройден | native callback dispatches=30 |
| lockstep-delayed: runtime evidence | пройден | Completed, device attestation, /proc samples и complete melee trace всех трёх seeds |
| lockstep-delayed: native perceptions | пройден | native callback dispatches=30 |
| realtime: runtime evidence | пройден | Completed, device attestation, /proc samples и complete melee trace всех трёх seeds |
| realtime: native perceptions | пройден | native callback dispatches=30 |
| Историческая диагностика: lockstep | пройден | Записан diverged; legacy shared RNG не является isolated RNG |
| Историческая диагностика: lockstep-delayed | пройден | Записан diverged; legacy shared RNG не является isolated RNG |
| Историческая диагностика: realtime | пройден | Записан diverged; legacy shared RNG не является isolated RNG |
| IPC на проверенном build | пройден | IPC фиксирует тот же runner/save/scenario/settings и SHA-256 использованной engine library |
| Provenance | пройден | Хеши исходников/бинарника/save/config/assets и связь fixture с неизменным archive trace |

## Механика и RNG

Основное сравнение использует одну версию настоящего движка: rendered probe под CPU lavapipe/Xvfb и runner без graphics/audio device. На обоих путях выбран `isolated` RNG вариантов block-анимации. Сравнение охватывает native очереди/consume, animation tags, урон/HP, root motion, collision flags и исход дуэлей в допусках зафиксированной конфигурации.

Исторический reference этапа 1 использовал общий `std::rand`; потребление RNG звуком зависело от wall clock. Его обычное сравнение с isolated запуском сохранено как диагностика. Отдельный архивный replay воспроизводит только записанные варианты block-клипов: 17 веток, каждая ровно один раз. Native AI самостоятельно выбирает действия; HP, урон, движение и исходы не подставляются из записи.

| Seed | Isolated: мс / исход / HP actor–target | Архивный branch replay: мс / исход / HP actor–target |
|---|---|---|
| 17 | 14133 / actor_down / 0–300 | 14133 / actor_down / 0–300 |
| 29 | 15600 / target_down / 224–0 | 17234 / target_down / 300–0 |
| 43 | 23567 / actor_down / 0–288 | 9800 / target_down / 300–0 |

`actor` — Bandit, `target` — Paladin. Эти три seeds проверяют механику; статистическое качество будущего контроллера ими не оценено.

| Историческая диагностика | Статус | Число ordered расхождений по seeds |
|---|---|---|
| lockstep | diverged | 17: 2150, 29: 6955, 43: 6959 |
| lockstep-delayed | diverged | 17: 2150, 29: 6955, 43: 6959 |
| realtime | diverged | 17: 2150, 29: 6955, 43: 6959 |

## Скорость и ресурсы

Wall время и sim/wall ratio ниже начинаются после cold reset; намеренная IPC задержка учитывается в elapsed wall. Processing throughput lockstep исключает введённый sleep. Realtime paced ожидание не объявляется processing throughput. CPU/RSS измерены по `/proc` на протяжении процесса, включая загрузку; RSS peak — максимум наблюдаемого VmHWM.

| Режим | Sim / wall после reset, с | Sim/wall | Substeps / processing с | Native choices / processing с | Peak RSS, MiB | CPU user+sys, с | Threads / FD max |
|---|---:|---:|---:|---:|---:|---:|---|
| lockstep | 53.83 / 25.20 | 2.14 | 64.61 | 6.40 | 562.60 | 51.73 | 17 / 7 |
| lockstep-delayed | 53.83 / 29.32 | 1.84 | 68.08 | 6.75 | 562.60 | 51.48 | 17 / 7 |
| realtime | 53.83 / 53.81 | 1.00 | нет данных | нет данных | 562.56 | 55.11 | 17 / 7 |

Realtime: максимальное отставание 46 мс; native choices 2.97/wall с. Native choice — событие родного AI для бойцов, а не policy observation/action или RL transition. Скорость GRU не измерялась.

Rendered диагностический прогон: wall с загрузкой 634.81 с, peak RSS 2010.12 MiB; renderer GPU VRAM 0 MiB (CPU lavapipe).

## Reset и IPC

Проверено 100 resets: latency p95 910.14 мс. Warmed RSS 629.05 → 629.48 MiB, дельта 0.43 MiB; span FD 0, threads 0. Проверены поколения и одинаковое экспонированное начальное actor/animation state. Это не равенство всей памяти или будущего policy hidden state.

IPC проверяет остановку lockstep без команды, `advance(until_decision)`, продолжение realtime при неполной JSONL строке, две команды в одном write и чистое закрытие. Это transport scheduling; внешний боевой API остаётся следующим этапом.

## Общий сервер

| Сервис | До | После |
|---|---|---|
| gothic-voxtral | active/running | active/running |
| gothic-voxcpm | active/running | active/running |

Shared GPU snapshot до: `16105 MiB, 8019 MiB, 96 %`; после: `16249 MiB, 7875 MiB, 95 %`. Эти показатели относятся ко всему хосту. ASR/TTS/Strands latency не измеряли: `service_latency_measurement=null`; состояние units не доказывает отсутствие влияния на задержки.

## Provenance и границы

Хост `dev-ubuntu`, Gothic II Night of the Raven (explicit -g2), fixed 30 Hz. Хешировано 3106 source files и 308 asset files; private assets/save в отчёт не копируются.

Серверный evidence: `/home/kaifat/gothic-ai/body-headless/runs/stage2-v3`; исходный archive: `/home/kaifat/gothic-ai/body-baseline/runs/melee-reference-stage1-v4`. Manifest/raw журналы сохраняют полную конфигурацию; компактный JSON хранит идентифицирующие хеши.

| Объект | SHA-256 |
|---|---|
| source_sha256 | `92c16db854ff2148a8f53ae8bba89a29152fcf028407d247139a87c464b8bded` |
| binary_sha256 | `25fd4ee2ade0401316b0b247ff8db6726d7cf29b22b0b3fce66a00d473fa1beb` |
| engine_library_sha256 | `2743968223c4de9190937836f698b574528e97eff2af3ad521a055e4f056fb9d` |
| reference_trace_sha256 | `9179ca4b2e779913247453f72ead4e30549002e9c414b32190681e2a19060ae4` |
| animation_fixture_sha256 | `d7b10e2ad0e9c4739e31cfcd9865cd43a0643cfe765dfa862d281de0e0210bc7` |

Gate относится к native human 1H melee-v1 и отдельным sight/sound probes. PFX/spells, CPU morph rendering, archery, весь остров и навигационные задачи этим набором не подтверждены. GRU, action ownership/masks, обучающий recorder, BC/PPO и несколько параллельных арен остаются следующими этапами.
