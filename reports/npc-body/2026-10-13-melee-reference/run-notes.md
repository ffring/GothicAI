# Этап 1: native melee reference на dev-ubuntu

13 октября 2026 года. **Проверка завершена: `body baseline complete`.** Это reference настоящего оконного OpenGothic до выделения headless runner. Нейросеть, LLM и внешний body API в боях не участвовали.

Численные данные: [отчёт](body-baseline.md) и [JSON с manifest](body-baseline.json). Настройка: [melee-v1](../../../evals/npc-body/melee-v1.json); механика и бюджеты: [боевой контракт](../../../docs/npc-body-combat-contract.md).

| Seed | Время боя в игре | Bandit / Paladin: terminal HP | Исход |
|---|---:|---:|---|
| 17 | 14 133 мс | 0 / 300 | Bandit погиб |
| 29 | 17 234 мс | 300 / 0 | Paladin погиб |
| 43 | 9 800 мс | 300 / 0 | Paladin погиб |

Все шесть участников имеют разные lifetime IDs, проверенные исходные характеристики, native instruction queues и consume, animation hooks и полные снимки шести guard полей. Все terminal события присутствуют; timeout, посторонних участников и non-melee damage нет. Получено 24 измеренных damage события: net damage суммарно 1040, фактическая потеря HP — 900; разница включает overkill. Два incoming удара отражены защитой.

Снимки записаны в `npc_pre_tick_animation_tags` при фиксированных substeps 30 Гц. p95 и максимум интервала снимков для каждого участника — 66 мс, в дополнение записаны переходы состояния. Полные observed интервалы `prehit` — 66–1134 мс, `defence` — 466–467 мс, `def_window` — 33–767 мс. Censored интервалы исключены из quantiles и видны в JSON. `def_par_window` не активировался. Metadata `T_1HPARADE_0*` даёт 25 fps, 600 мс total и `[0,480]` защитное окно; `S_1HATTACK` содержит несколько hit/combo windows внутри 4560 мс sequence.

Это engine predicates, а не универсальные windup/hit/recovery labels. В частности `isJumpBack` также true при блоке; отдельного consume `MV_JUMPBACK` в серии нет. `native_attempt` считает все `set_anim`/`do_attack` hooks, включая draw/idle/hit reaction, и может нести последний choice ID или primitive 0. Такие интервалы не являются задержкой policy. Нужные для BC observation/action boundaries появятся после общего resolver и API.

## Сборка и нагрузка

- Хост: `dev-ubuntu`, пользовательский каталог `~/gothic-ai/body-baseline/`; GCC/G++ 15.2, CMake 4.2.3, Ninja 1.13.2, `RelWithDebInfo` (`-O2 -g -DNDEBUG`). Toolchain Vulkan Headers/glslang закреплён на `vulkan-sdk-1.4.341.0`; commits и хеши приведены в manifest.
- Renderer: lavapipe / llvmpipe LLVM 21.1.8 через Xvfb 640×480. Выбран только CPU Vulkan ICD; renderer не использует RTX 3090. Оконный renderer всё ещё присутствует, поэтому stage 2 этим запуском не закрыт.
- Собственный transient user unit: CPUQuota 200%, Nice 10, MemoryMax 12 GiB, TasksMax 128, RuntimeMax 900 секунд, два lavapipe worker threads. Сборка также использовала два jobs с Nice 10.
- Измерение `/usr/bin/time -v`: wall **508,12 с**; user CPU 677,83 с + system CPU 163,10 с; CPU 165%; maximum RSS **2 106 764 KiB**; swaps 0; exit 0. Systemd отдельно показал peak cgroup memory 1,6 GiB. Это разные измерители памяти, их значения не объединяются.
- В каждом record bench сообщил **8% simulated / wall speed**; суммарное время трёх боёв — 41,167 игровых секунд. Пacing realtime был включён, но renderer отстал на 451 381 мс. Это не real-time latency test, не accelerated throughput и не замер GRU.
- На старте shared GPU имел 23 608 MiB used / 516 MiB free, в конце 22 159 / 1966 MiB. Это общая нагрузка чужих/текущих сервисов, не расход renderer. `gothic-voxtral` и `gothic-voxcpm` были `active/running` до и после. Latency ASR/TTS/Strands под совместной нагрузкой не измеряли; production containers, Docker и системные пакеты не изменяли.

Для Linux понадобились три небольшие compatibility правки: прямой include `<algorithm>`, удаление недопустимого assignment у immutable RapidJSON `GenericStringRef` для GCC 15 и корректный Vulkan sampled-image usage у attachments/storage images. Последняя отделяет фактическую поддержку sampling от требования blit для автоматических mipmaps: R11G11B10UF на lavapipe поддерживает sampling, но не BLIT_DST. Pixmap/mipmap path не изменён. Поддержка разных image usages задаётся [format feature flags Vulkan](https://docs.vulkan.org/refpages/latest/refpages/source/VkFormatFeatureFlagBits.html).

Неудачные startup/diagnostic запуски сохранены отдельно и в baseline не включены. Финальный reference снят обычной сборкой после исправления загрузки версии (`-g2`, NotR) и Vulkan usage; ASAN был отдельной диагностикой. Перед headless refactor сохранены именно этот instrumented source и бинарник, без изменения native queue/RNG/боевого исполнителя.

## Где сохранён reference

На сервере: `~/gothic-ai/body-baseline/runs/melee-reference-stage1-v4/` — `events.jsonl`, `manifest.json`, `bench.log`, `game.log`, `launcher.log`, `resources.txt`, исходный `source.patch`, архив `source.tar.gz` и `reference/Gothic2Notr`. Хеши бинарника и всех исходников сверены перед архивированием; manifest содержит также хеши всех 308 asset files, scenario/config, save и settings. Private game assets/save и бинарники не входят в Git.

Локальная ignored копия данных: `data/npc-body/reference/melee-reference-stage1-v4/`; компактный отчёт сохранён в этом каталоге `reports/`. Бинарник остаётся на сервере. Повторный запуск:

```sh
scripts/body-baseline-remote.sh all melee-reference-new
```

Верификация: Linux engine build успешен; targeted reporter suite — **15 passed**; реальная трёхseed серия завершилась и reporter принял её с exit 0. Запуск игры/моделей на Mac не выполнялся. Три seeds дают instrumented reference, не статистическую оценку качества контроллера или доказательство bitwise determinism.

## Следующий gate

Этап 2 должен убрать graphics device/render/audio playback и сохранить CPU анимации, animation tags, root motion, урон, perceptions и native queue semantics относительно этого reference. Короткий `def_window` 33 мс требует сохранять transitions/events: периодические 20 Гц сами по себе не дают покрытия всех окон. Recovery/interruptibility остаются описанными по коду и metadata; отдельный scripted прогон всех примитивов, mask parity, realtime command latency, четыре NPC и обучение выполняются на следующих этапах.
