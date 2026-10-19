# RSS OpenGothic на Ryzen: свободные блоки glibc

Накопление создаёт **перезагрузка мира (`load` → новый `GameSession`/`World`)**: освобождённая игрой память остаётся резидентной в аренах glibc. Видимая ступень подготовки волны находится на **`save`: +256,004 МиБ**. При загрузке одновременно растёт анонимная куча и падает RSS отображений NVIDIA, поэтому общий `VmRSS` скрывает большую часть накопления до следующего сейва.

Ryzen 3950X, RTX 3080, Linux 7.0.0-38, glibc 2.43, NVIDIA 615.71.09. Движок `RelWithDebInfo`, окно 640×360; SHA-256 исходников `0b5309fb2cbe44a64c732847995343af5dc084fa28395cb9d544990846173e16`. `LLM=fake`, новая игра, без body URL и P30. Исходники движка и сервис P30 не менялись.

## Фазы и шаги

Каждая фаза — отдельная игра, восемь циклов. Ниже медианы циклов 3–8, МиБ. `VmRSS` и `RssAnon` — из `status`, остальные столбцы — изменение RSS группы `smaps` между концами циклов. Медианы столбцов считаются независимо.

| Фаза | ΔVmRSS | ΔRssAnon | `[heap]` | Прочая анонимная | `/dev/nvidia*` |
|---|---:|---:|---:|---:|---:|
| `save` | +0,004 | +0,004 | 0 | +0,002 | 0 |
| `load` | +231,293 | +231,295 | 0 | +231,297 | 0 |
| `spawn` | +0,619 | +0,525 | +0,408 | +0,117 | 0 |
| `teleport` | +0,018 | +0,018 | 0 | +0,014 | 0 |
| `wave` | +265,562 | +263,746 | +0,002 | +260,320 | −1,000 |

`/dev/dri/*` и memfd/shm не накапливают RSS. Swap во всех измерениях равен нулю. Небольшой рост `spawn` согласуется с `WorldObjects::npcRemoved`: удалённые NPC сохраняются до уничтожения мира; это другой масштаб.

Все **42 шага `wave` × 8 циклов** — в [wave-steps.csv](wave-steps.csv). Краткая таблица медиан:

| Шаг | ΔVmRSS, МиБ |
|---|---:|
| `player_goto_refuge`, `heal`, `player_equip`, `player_draw_weapon` — каждый | 0 |
| `save` | **+256,004** |
| `insert_bandits` | +0,012 |
| `bandit_attributes` | 0 |
| `bandit_{1..4}_{skip_important,npc_goto_arena,start_activity,check,draw_weapon,npc_goto_spread}` — каждый из 24 | 0 |
| `bandit_{1..4}_position` — каждый | 0 |
| `teleport_to_bandit` | +0,004 |
| `B_ATTACK` | 0 |
| `combat_10s` | +1,418 |
| `escape_refuge`, `remove_bandits` — каждый | 0 |
| `load_wait_world` | +19,682 |
| `load_settle_3s` | −19,990 |

У `save` состав ступени: **RssAnon +0,004, RssFile +128, RssShmem +128 МиБ**. Код проходит через `MainWindow::saveGame` → `Renderer::screenshoot` → `Device::readPixels`. У следующего `load_wait_world` RssAnon растёт на **257,582 МиБ**, а RssFile/RssShmem падают примерно на 117/118 МиБ. Этим объясняется маскировка роста загрузкой в soak. Это медианы отдельных составляющих, их сумма не обязана совпадать с медианой `VmRSS`.

Дополнительные снимки `smaps` под jemalloc подтвердили временное отображение NVIDIA: цикл 2, `save` 178,36 МиБ → `load_wait_world` 60,36 МиБ; цикл 3 — 176,36 → 58,36 МиБ. В конце циклов эта группа не растёт. Удержанная память находится в **прочих анонимных отображениях**, а не в накоплении страниц устройства.

## Кто удерживает память

Сначала выполнен полный `wave` под jemalloc без профиля, затем `load` с jemalloc 5.3.0 (`prof:true,prof_accum:true,lg_prof_sample:19`), дампы после циклов 2 и 8. Ещё один `load` под glibc снял `malloc_info`/`mallinfo2` и сделал единственный `malloc_trim(0)` после измерения восьмого цикла.

| Проверка, 8 циклов | Результат |
|---|---|
| `wave`, glibc | медиана +265,562 МиБ/цикл; RSS цикла 2 → 8: 1777,95 → 3052,22 МиБ |
| `wave`, только `LD_PRELOAD=libjemalloc.so.2` | медиана −0,703 МиБ/цикл; 1177,43 → 1177,67 МиБ |
| `load`, jemalloc + профиль | медиана −1,943 МиБ/цикл; `stats.allocated` 761,770 → 762,204 МиБ (**+0,434**) |
| `load`, glibc, цикл 2 → 8 | размер арен 1338,930 → 2543,367 МиБ; свободно 747,801 → **1919,940 МиБ** |
| То же, живое `uordblks + hblkhd` | 692,270 → 692,565 МиБ (**+0,294**) |
| После `malloc_trim(0)` | RSS **2859,020 → 1316,344 МиБ**, возврат **1542,676 МиБ**, 0,185 с; RssFile/RssShmem не изменились |

Число арен — 55 → 56: растёт вместимость и резидентность свободных блоков существующих арен. После trim адресные арены остаются, но свободные страницы больше не резидентны. Это удержание аллокатором, а не живой объект мира размером 258 МиБ.

`jeprof --base` даёт выборочную оценку разницы живой кучи +13,804 МиБ; точный `mallctl stats.allocated` выше показывает +0,434 МиБ. Суммарные выделения между дампами — около 9,48 ГБ: мир каждый раз заново строит геометрию, физику и объекты. Примеры **стеков выделения из профиля**, объёмы за шесть загрузок:

```text
World::World::{lambda#2}
  → PackedMesh::PackedMesh → packMeshletsLnd → Meshlet::flush
  → std::vector<Resources::Vertex>::resize          360 МиБ

DynamicWorld::DynamicWorld → CollisionWorld::CollisionWorld
  → CollisionWorld::Impl::Impl → JPH::TempAllocatorImpl  192 МиБ
```

Первый вызов resize — [packedmesh.cpp:117](../../../OpenGothic/common/graphics/mesh/submesh/packedmesh.cpp#L117), резерв Jolt 32 МиБ на мир — [collisionworld.cpp:346](../../../OpenGothic/common/physics/collisionworld.cpp#L346). Это источники крупных повторных выделений; освобождённые блоки удерживает glibc. [Выдержки jeprof](heap-profile.txt), [десять крупнейших стеков](allocation-stacks.txt), [точные счётчики аллокаторов](allocator-stats.csv).

## Повтор и предложение починки

```bash
for phase in wave save load spawn teleport; do
  SCENARIO=memprobe LLM=fake MEMPROBE_PHASES="$phase" MEMPROBE_CYCLES=8 \
    TIMEOUT=1200 RYZEN_LOCK_WAIT=14400 RYZEN_DISPLAY_EXCLUSIVE=1 \
    LOG_DIR="$PWD/.ryzen/memprobe-$phase" scripts/ryzen.sh e2e || break
done
```

Статус снимается после подтверждения команды и 0,5 с для кадров; дополнительные паузы соответствуют фазам. `GAME_EXTRA_ENV` принимает отдельную `NAME=value` на строку и действует только на игру. Для простого контроля: `GAME_EXTRA_ENV='LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2'`. Для дампов использован временный preload-поток с `mallctl("prof.dump")`, для glibc — с `malloc_info`/`malloc_trim`; `MEMPROBE_CYCLE_PAUSE=5` оставляет время на снимок. Помощники не меняют движок. При символизации PIE путь бинарника должен совпадать с `MAPPED_LIBRARIES` дампа — здесь это бинарник общего кэша Ryzen.

Предложение: проверить Linux-вызов `malloc_trim(0)` после завершения загрузки мира, когда временные буферы уже освобождены; в замере он вернул 1,51 ГиБ за 185 мс. Альтернатива для Linux — jemalloc в лаунчере. Починка и повтор 30-минутного soak в эту работу не входят.

Проверки: **8 игр, 64 цикла, 8 результатов `done`, 0 ошибок сценария**; обязательные import и `bash -n` прошли. Полный pytest и регрессия e2e не запускались. Исходные JSONL и дампы остаются локально в `.ryzen/memprobe-*`; переносимые данные всех циклов — [cycles.csv](cycles.csv), всех групп — [smaps.csv](smaps.csv), медианы — [summary.csv](summary.csv).
