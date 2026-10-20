# Статичные меши vob четырёх миров Gothic II + Ночь Ворона

Офлайн-перепись для этапа 2 [плана своего контента](../../../docs/own-content.md), 2026-10-20. В репозитории только имена ресурсов и агрегированные числа; вершины, UV, пиксели и меши не экспортируются.

Повторить на Mac из корня репозитория:

```sh
scripts/content-meshes.sh --self-test
scripts/content-meshes.sh
scripts/content-meshes.sh reports/content/2026-10-world-meshes
```

`GOTHIC_DIR` задаёт установленную игру (по умолчанию `/Volumes/DevSSD/Dev/games/Gothic II`); необязательный аргумент — каталог отчёта. Скрипт использует готовую библиотеку ZenKit или собирает только цель `zenkit`; бинарник — `OpenGothic/build/meshes/world-meshes`. Игра, GPU и модели ИИ не запускаются.

## Состав и метод

- Смонтированы `Meshes.vdf`, `Meshes_Addon.vdf`, `Worlds.vdf`, `Worlds_Addon.vdf` именно в этом порядке с `VfsOverwriteBehavior::ALL`: аддон перекрывает базу независимо от временных меток. Loose-файлы и моды не подключаются.
- Обход и геометрия повторяют `world-textures.cpp`: все видимые vob с `.3DS` → `.MRM`, `.MRM` или `.MSH`, полная детализация. Флаг `vob_static` не фильтрует объекты: входят и лежащие предметы `oCItem`. Дочерние vob обходятся независимо от родителя. Ключ визуала — basename в верхнем регистре без расширения, как в `meshes.json`.
- Меш мира не входит. Исключены NPC, предметы в руках и инвентарях NPC, скрытые визуалы, декали, частицы, `.MDS`/`.MDL` и другие анимированные/морф-модели; сундуки и двери на `.MDS` сюда не попадают. Отсутствующий меш, коллизия ключей разных форматов или ошибка ZenKit прерывает прогон.
- Площадь — сумма площадей треугольников (см² / 10 000), для каждого экземпляра со своим мировым трансформом. Вложенные vob уже имеют object-to-world трансформ; родитель повторно не применяется. Это площадь всех поверхностей, не земли и не видимых пикселей; прозрачность не вычитается. `.MSH` триангулируется веером с тем же исключением portal/ghost occluder/outdoor, что в переписи текстур.
- Локальный bbox вычислен по позициям вершин исходного меша; размеры переведены из см в м. Локальная площадь и число треугольников относятся к одному экземпляру без трансформа. Текстуры — непустые имена материалов, basename в верхнем регистре, без повторов, по алфавиту.
- `movable` и `destructible` — совпадение ключа визуала с глобом первого столбца `OpenGothic/data/movable-vobs.csv` и `destructible-vobs.csv` (без регистра, `*` — любая строка, `?` — один символ). Это признаки таблиц движка, не вывод из имени семейства или типа vob.

## Семейства и районы

Семейство: имя без расширения разбивается по `_`; с хвоста снимаются числовые и вариантные токены (`01`, `3`, `01B`, `V1`, `V2`, одиночная буква), у последнего оставшегося токена отрезаются хвостовые цифры (`BARREL2` → `BARREL`), затем снимаются хвостовые модификаторы. Семейство — последний оставшийся токен; если токенов не осталось, берётся полное имя. Это эвристическая группировка имён, а не ручная классификация.

Полный список модификаторов: `BIG`, `BROKEN`, `CLOSED`, `DEFECT`, `HALF`, `HIGH`, `L`, `LARGE`, `LEFT`, `LONG`, `LOW`, `NEW`, `NORMAL`, `OLD`, `OPEN`, `POOR`, `R`, `RICH`, `RIGHT`, `SHORT`, `SMALL`.

Каждому учтённому vob назначается ближайший waypoint своего `world.way_net->points`, исключая `free_point`, в радиусе **30 м** по 3D-расстоянию от позиции vob. На равном расстоянии выбирается первое имя по алфавиту. При отсутствии точки — `<NONE>`. У имени снимаются хвостовые числовые токены, после чего остаются **первые три токена** по `_`: гавань `NW_CITY_HABOUR_*` (написание оригинала), рынок `NW_CITY_MERCHANT_*`, верхний квартал `NW_CITY_UPTOWN_*` и казармы `NW_CITY_KASERN_*` остаются разными районами. `NW_CITY_TO` объединяет переходы от города. Это приближение по навигационным именам, не географические границы: большие объекты приписываются по началу координат, не по покрываемой территории.

## Файлы и столбцы

[Все миры](all-worlds.csv), [Хоринис](newworld.csv), [Долина рудников](oldworld.csv), [Яркендар](addonworld.csv), [Остров драконов](dragonisland.csv): строка на визуал, сортировка по экземплярам в области файла, затем по площади (оба по убыванию), затем по имени.

- `world`: имя мира или `ALL`; `visual`, `format`: ключ и `MRM`/`MSH` (`.3DS` считается `MRM`).
- `instances`, `item_instances`, `area_total_m2`: экземпляры, из них `oCItem`, сумма мировых площадей в области файла. `instances_newworld`, `instances_oldworld`, `instances_addonworld`, `instances_dragonisland`, `instances_total` в каждом файле дают глобальные числа этого визуала по четырём мирам.
- `triangles`, `bbox_x_m`, `bbox_y_m`, `bbox_z_m`, `area_per_instance_m2`: полная локальная геометрия одного экземпляра. `textures`: имена через `;`; `movable`, `destructible`: 0/1; `family`: семейство.
- `cum_instances_fraction`, `cum_area_fraction`: накопленные доли 0–1 в порядке строк, с итогами области файла в знаменателе.

[families.csv](families.csv): семейство, число разных визуалов, экземпляры по мирам и всего, суммарная площадь, число подвижных визуалов, до пяти самых частых примеров; сортировка по экземплярам, площади и имени.

[areas.csv](areas.csv): `world`, `area`, `instances`, `area_m2`, число разных `visuals` и `families`, `visuals_for_80pct_instances` (минимум визуалов для ≥80 % экземпляров района при сортировке по экземплярам), `movable_instances`, `top_visuals` (до пяти `ИМЯ:число` через `;`). Сортировка по имени мира, затем экземплярам, площади и имени района.

## Сверка сумм

Независимая сумма по всем треугольникам считается до разнесения по визуалам. Прогон падает, если сумма экземпляров не равна числу учтённых vob или площадь расходится больше чем на 0,1 %. Районные суммы проверяются отдельно. Для сравнения взяты опубликованные числа [переписи текстур](../2026-10-world-textures/README.md), округлённые до 0,001 м².

| Мир | Учтено vob | Визуалов | Семейств | Сумма CSV, м² | По треугольникам, м² | Ошибка, % | Vob / площадь отчёта текстур | Совпало |
|---|---:|---:|---:|---:|---:|---:|---|---|
| NEWWORLD.ZEN | 11546 | 312 | 183 | 2533385.016 | 2533385.016 | 0.000000000 | 11546 / 2533385.016 | да |
| OLDWORLD.ZEN | 4438 | 203 | 120 | 1781506.359 | 1781506.359 | 0.000000000 | 4438 / 1781506.359 | да |
| ADDONWORLD.ZEN | 4718 | 248 | 144 | 1316101.720 | 1316101.720 | 0.000000000 | 4718 / 1316101.720 | да |
| DRAGONISLAND.ZEN | 499 | 91 | 61 | 13138.775 | 13138.775 | 0.000000000 | 499 / 13138.775 | да |
| Все миры | 21201 | 533 | 278 | 5644131.870 | 5644131.870 | 0.000000000 | — | — |

Число vob и площадь совпали во всех четырёх мирах в точности опубликованного округления.

В этих vob встречаются 533 визуалов MRM и 0 MSH. Это использованные визуалы четырёх миров, а не все 1 355 MRM в архивах из плана замены: неиспользованные ресурсы и другие классы визуалов не перечисляются. Учтённых `oCItem`: 0; `item_instances` считает только явно заданные подходящие визуалы vob из ZEN, без восстановления визуалов предметов по скриптам.

| Мир | Скрытых статичных визуалов | Прочих исключённых непустых визуалов |
|---|---:|---:|
| NEWWORLD.ZEN | 18 | 2986 |
| OLDWORLD.ZEN | 4 | 734 |
| ADDONWORLD.ZEN | 23 | 1177 |
| DRAGONISLAND.ZEN | 7 | 363 |

## Топ-50 визуалов всех миров по экземплярам

NW / OW / AW / DI — экземпляры в Хоринисе, Долине рудников, Яркендаре и на Острове драконов. M / D — movable / destructible; bbox и площадь одного экземпляра локальные.

| Визуал | Семейство | NW | OW | AW | DI | Всего | Треугольников | Bbox X × Y × Z, м | Площадь одного, м² | Всего, м² | M / D |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| `NW_NATURE_GRASSGROUP_01` | GRASSGROUP | 688 | 63 | 174 | 0 | 925 | 40 | 7.290 × 2.200 × 6.879 | 189.788 | 175553.993 | 0 / 0 |
| `NW_NATURE_BUSH_120P` | BUSH | 690 | 0 | 116 | 0 | 806 | 120 | 3.707 × 4.229 × 7.431 | 213.520 | 172096.932 | 0 / 0 |
| `NW_NATURE_FARNTEPPICH_306P` | FARNTEPPICH | 565 | 0 | 211 | 0 | 776 | 306 | 11.888 × 3.028 × 15.177 | 447.698 | 347413.639 | 0 / 0 |
| `NW_CAVEWEBS_V201` | CAVEWEBS | 455 | 84 | 209 | 9 | 757 | 20 | 7.146 × 5.392 × 7.316 | 263.424 | 199411.978 | 0 / 0 |
| `OW_NATURE_SMALLTREE_01` | SMALLTREE | 286 | 192 | 55 | 2 | 535 | 191 | 1.786 × 5.332 × 2.901 | 25.311 | 13541.248 | 0 / 0 |
| `NW_NATURE_TANNE_75P` | TANNE | 389 | 143 | 0 | 0 | 532 | 75 | 11.064 × 11.981 × 11.272 | 695.488 | 369999.795 | 0 / 0 |
| `NW_NATURE_FARN_102P` | FARN | 101 | 114 | 303 | 0 | 518 | 102 | 4.358 × 1.644 × 4.237 | 59.965 | 31061.960 | 0 / 0 |
| `MIN_LOB_COUPLESTONES` | COUPLESTONES | 236 | 152 | 52 | 1 | 441 | 261 | 10.656 × 1.291 × 8.381 | 33.493 | 14770.594 | 0 / 0 |
| `OW_MISC_WALL_TORCH_01` | TORCH | 145 | 21 | 170 | 88 | 424 | 134 | 0.162 × 0.759 × 0.455 | 0.362 | 153.648 | 0 / 0 |
| `OW_NATURE_BUSH_02` | BUSH | 104 | 229 | 51 | 0 | 384 | 400 | 6.063 × 1.430 × 4.879 | 151.446 | 58155.125 | 0 / 0 |
| `OW_MUSHROOM_V2` | MUSHROOM | 133 | 52 | 122 | 19 | 326 | 660 | 1.978 × 2.165 × 3.717 | 5.109 | 1665.614 | 0 / 0 |
| `MIN_LOB_STONE_V2_30` | STONE | 2 | 298 | 0 | 0 | 300 | 52 | 6.831 × 4.461 × 5.292 | 96.978 | 29093.280 | 0 / 0 |
| `NW_CITY_OILLAMP_01` | OILLAMP | 223 | 0 | 0 | 19 | 242 | 138 | 0.291 × 0.485 × 0.410 | 0.560 | 135.539 | 0 / 0 |
| `NW_NATURE_FARN_SMALL_102P` | FARN | 121 | 0 | 120 | 0 | 241 | 102 | 4.358 × 1.492 × 4.237 | 53.471 | 12886.600 | 0 / 0 |
| `OC_BARREL_V1_DESTROYED` | DESTROYED | 94 | 46 | 88 | 2 | 230 | 84 | 0.738 × 1.108 × 0.738 | 4.520 | 1039.620 | 0 / 0 |
| `OW_NATURE_BUSH_01` | BUSH | 92 | 131 | 0 | 0 | 223 | 20 | 2.007 × 2.561 × 2.521 | 32.143 | 7167.838 | 0 / 0 |
| `NW_NATURE_LONG_BUSH_360P` | BUSH | 156 | 0 | 63 | 0 | 219 | 360 | 6.571 × 4.229 × 16.233 | 640.559 | 140282.492 | 0 / 0 |
| `NW_MISC_SPIDERWEBS_01` | SPIDERWEBS | 140 | 4 | 74 | 0 | 218 | 256 | 5.192 × 2.828 × 5.189 | 124.036 | 27039.867 | 0 / 0 |
| `NW_NATURE_HANGPLANTS_02` | HANGPLANTS | 44 | 0 | 173 | 0 | 217 | 128 | 5.446 × 5.692 × 5.231 | 100.635 | 21837.789 | 0 / 0 |
| `NW_NATURE_FARN_BIG_01` | FARN | 91 | 0 | 119 | 0 | 210 | 216 | 4.857 × 2.330 × 5.040 | 87.459 | 18366.484 | 0 / 0 |
| `NW_NATURE_TANNE_100P` | TANNE | 209 | 0 | 0 | 0 | 209 | 99 | 11.064 × 11.981 × 11.272 | 866.061 | 181006.683 | 0 / 0 |
| `NW_NATURE_SIDEPLANT_01` | SIDEPLANT | 202 | 0 | 1 | 0 | 203 | 10 | 2.102 × 0.980 × 0.745 | 6.205 | 1259.680 | 0 / 0 |
| `NW_NATURE_FENCE_01` | FENCE | 167 | 1 | 20 | 0 | 188 | 60 | 3.202 × 1.808 × 0.487 | 5.302 | 996.749 | 1 / 1 |
| `NW_CITY_CANDLE_01` | CANDLE | 157 | 0 | 18 | 7 | 182 | 124 | 0.400 × 0.322 × 0.490 | 0.445 | 81.004 | 1 / 0 |
| `NW_HARBOUR_BARREL_01` | BARREL | 150 | 0 | 27 | 3 | 180 | 60 | 0.798 × 1.108 × 0.798 | 3.187 | 573.718 | 1 / 1 |
| `NW_NATURE_BUSH_25P` | BUSH | 168 | 5 | 4 | 0 | 177 | 26 | 4.844 × 5.380 × 3.551 | 98.714 | 17472.303 | 0 / 0 |
| `OW_LOB_BUSH_V5` | BUSH | 39 | 132 | 0 | 0 | 171 | 48 | 1.348 × 1.857 × 1.348 | 13.329 | 2279.289 | 0 / 0 |
| `NW_NATURE_BUSH_56P` | BUSH | 164 | 0 | 4 | 0 | 168 | 56 | 2.432 × 1.992 × 3.356 | 38.966 | 6546.219 | 0 / 0 |
| `OW_NATURE_BIG_TREE_02_380P` | TREE | 11 | 153 | 0 | 0 | 164 | 375 | 26.686 × 31.024 × 26.687 | 7476.365 | 1226123.577 | 0 / 0 |
| `NW_HARBOUR_CRATEPILE_01` | CRATEPILE | 132 | 0 | 8 | 24 | 164 | 135 | 3.068 × 2.102 × 2.870 | 37.518 | 6152.969 | 0 / 0 |
| `OW_LOB_WOODPLANKS_V2` | WOODPLANKS | 28 | 125 | 10 | 0 | 163 | 12 | 0.250 × 6.500 × 0.550 | 10.506 | 1712.426 | 0 / 0 |
| `NW_NATURE_SIDEPLANT_BIG_01` | SIDEPLANT | 97 | 0 | 56 | 0 | 153 | 10 | 3.738 × 1.743 × 1.324 | 19.617 | 3001.368 | 0 / 0 |
| `OC_BARREL_V1` | BARREL | 8 | 87 | 54 | 0 | 149 | 60 | 0.798 × 1.108 × 0.798 | 3.187 | 474.910 | 1 / 1 |
| `ORC_SKELETON_V3` | SKELETON | 98 | 25 | 12 | 6 | 141 | 816 | 0.787 × 0.238 × 2.362 | 1.608 | 226.707 | 0 / 0 |
| `NW_NATURE_HOHETANNE_59P` | HOHETANNE | 88 | 47 | 0 | 0 | 135 | 59 | 5.354 × 14.066 × 5.097 | 224.265 | 30275.709 | 0 / 0 |
| `NW_NATURE_SMALLTREE_79P` | SMALLTREE | 130 | 0 | 1 | 0 | 131 | 79 | 6.322 × 12.005 × 3.937 | 247.425 | 32412.606 | 0 / 0 |
| `NW_NATURE_BUSH_300P` | BUSH | 115 | 12 | 3 | 0 | 130 | 320 | 3.149 × 1.472 × 2.767 | 40.625 | 5281.291 | 0 / 0 |
| `NW_NATURE_HANGPLANTS_01` | HANGPLANTS | 95 | 0 | 34 | 0 | 129 | 192 | 5.028 × 3.639 × 3.514 | 71.165 | 9180.247 | 0 / 0 |
| `OW_LOB_TREE_V11` | TREE | 31 | 97 | 0 | 0 | 128 | 159 | 19.208 × 20.021 × 19.078 | 1517.248 | 194202.107 | 0 / 0 |
| `AW_CRATE_01` | CRATE | 1 | 104 | 22 | 0 | 127 | 12 | 1.200 × 1.000 × 1.000 | 6.800 | 863.600 | 1 / 1 |
| `NW_HARBOUR_BARRELGROUP_02` | BARRELGROUP | 97 | 6 | 13 | 6 | 122 | 420 | 4.338 × 1.359 × 1.790 | 22.311 | 2721.969 | 0 / 0 |
| `OC_SAECKE_01` | SAECKE | 98 | 11 | 12 | 0 | 121 | 252 | 1.956 × 0.908 × 1.589 | 8.176 | 989.281 | 0 / 0 |
| `NW_HARBOUR_CRATEBROKEN_01` | CRATEBROKEN | 92 | 1 | 27 | 0 | 120 | 152 | 1.200 × 1.000 × 1.000 | 6.704 | 804.431 | 0 / 0 |
| `NW_HARBOUR_CRATE_01` | CRATE | 105 | 0 | 5 | 9 | 119 | 12 | 1.200 × 1.000 × 1.000 | 6.800 | 809.200 | 1 / 1 |
| `ORC_SKULLSONFLOOR` | SKULLSONFLOOR | 107 | 3 | 6 | 1 | 117 | 232 | 0.854 × 0.316 × 0.811 | 1.216 | 142.227 | 0 / 0 |
| `OW_LOB_BUSH_V7` | BUSH | 0 | 112 | 0 | 0 | 112 | 98 | 3.670 × 1.751 × 3.848 | 17.644 | 1976.155 | 0 / 0 |
| `NW_NATURE_BAUMSTAMM_01_30P` | BAUMSTAMM | 104 | 0 | 7 | 0 | 111 | 30 | 5.926 × 0.885 × 1.014 | 13.342 | 1480.980 | 0 / 0 |
| `OW_LOB_FENCE_V1` | FENCE | 0 | 106 | 0 | 0 | 106 | 60 | 3.202 × 1.808 × 0.487 | 5.302 | 561.997 | 1 / 1 |
| `NW_NATURE_STONE_BIGHIGH_01` | BIGHIGH | 85 | 1 | 19 | 0 | 105 | 40 | 6.509 × 2.941 × 6.408 | 53.288 | 5595.273 | 0 / 0 |
| `ADDON_DUNGEON_BROKENSTONE_03` | BROKENSTONE | 29 | 0 | 74 | 0 | 103 | 70 | 4.094 × 0.168 × 3.352 | 4.941 | 508.964 | 0 / 0 |

## Топ-20 семейств по экземплярам

| Семейство | Визуалов | Экземпляров | Площадь, м² | Подвижных визуалов | Примеры |
|---|---:|---:|---:|---:|---|
| BUSH | 16 | 2692 | 419951.112 | 0 | NW_NATURE_BUSH_120P; OW_NATURE_BUSH_02; OW_NATURE_BUSH_01; NW_NATURE_LONG_BUSH_360P; NW_NATURE_BUSH_25P |
| FARN | 3 | 969 | 62315.044 | 0 | NW_NATURE_FARN_102P; NW_NATURE_FARN_SMALL_102P; NW_NATURE_FARN_BIG_01 |
| GRASSGROUP | 1 | 925 | 175553.993 | 0 | NW_NATURE_GRASSGROUP_01 |
| FARNTEPPICH | 2 | 866 | 399144.461 | 0 | NW_NATURE_FARNTEPPICH_306P; NW_NATURE_FARNTEPPICH_612P |
| CAVEWEBS | 3 | 779 | 205960.786 | 0 | NW_CAVEWEBS_V201; OW_CAVEWEBS_V1; OW_CAVEWEBS_V2 |
| TANNE | 2 | 741 | 551006.477 | 0 | NW_NATURE_TANNE_75P; NW_NATURE_TANNE_100P |
| SMALLTREE | 3 | 711 | 59475.300 | 0 | OW_NATURE_SMALLTREE_01; NW_NATURE_SMALLTREE_79P; NW_NATURE_SMALLTREE_103P |
| STONE | 11 | 643 | 46012.512 | 0 | MIN_LOB_STONE_V2_30; MIN_MOB_STONE_V2_20; MIN_LOB_STONE_V3_30; ADDON_STONES_STONE_01_84P; MIN_LOB_STONE_V1_30 |
| COUPLESTONES | 2 | 501 | 15981.786 | 0 | MIN_LOB_COUPLESTONES; ADDON_STONES_COUPLESTONES_01_230P |
| TREE | 16 | 499 | 1904942.422 | 0 | OW_NATURE_BIG_TREE_02_380P; OW_LOB_TREE_V11; ADDON_PLANTS_TREE_05_239P; ADDON_PLANTS_TREE_01_353P; ADDON_VALLEYPLANT_TREE_01_L_357P |
| SKELETON | 10 | 445 | 713.494 | 0 | ORC_SKELETON_V3; ORC_SKELETON_V4; ORC_SKELETON_V2; ORC_SKELETON_V1; NW_MISC_SKELETON_02 |
| TORCH | 2 | 429 | 154.622 | 0 | OW_MISC_WALL_TORCH_01; DT_TORCH_V1 |
| HANGPLANTS | 4 | 403 | 82353.177 | 0 | NW_NATURE_HANGPLANTS_02; NW_NATURE_HANGPLANTS_01; ADDON_PLANTS_HANGPLANTS_02_494P; ADDON_PLANTS_HANGPLANTS_01_374P |
| FENCE | 4 | 385 | 1790.251 | 4 | NW_NATURE_FENCE_01; OW_LOB_FENCE_V1; OW_LOB_FENCE_V3; OW_LOB_FENCE_V2 |
| BARREL | 4 | 374 | 1233.504 | 4 | NW_HARBOUR_BARREL_01; OC_BARREL_V1; NW_CITY_WEAPON_BARREL_01; OC_BARREL_V2 |
| SIDEPLANT | 2 | 356 | 4261.048 | 0 | NW_NATURE_SIDEPLANT_01; NW_NATURE_SIDEPLANT_BIG_01 |
| MUSHROOM | 2 | 355 | 1837.445 | 0 | OW_MUSHROOM_V2; OW_MUSHROOM_V1 |
| WOODPLANKS | 5 | 284 | 4084.833 | 0 | OW_LOB_WOODPLANKS_V2; OW_LOB_WOODPLANKS_V1; OW_LOB_WOODPLANKS_V3; OW_LOB_WOODPLANKS_V4; OW_LOB_WOODPLANKS_V5 |
| DESTROYED | 4 | 266 | 2845.585 | 0 | OC_BARREL_V1_DESTROYED; OW_LOB_TREE_DESTROYED_V2; OC_BARREL_V2_DESTROYED; OC_TABLE_V2_DESTROYED |
| CUPBOARD | 4 | 258 | 3578.282 | 2 | NW_CITY_CUPBOARD_POOR_02; NW_CITY_CUPBOARD_RICH_02; NW_CITY_CUPBOARD_POOR_01; NW_CITY_CUPBOARD_RICH_01 |

## Топ-20 семейств по площади

| Семейство | Визуалов | Экземпляров | Площадь, м² | Подвижных визуалов | Примеры |
|---|---:|---:|---:|---:|---|
| TREE | 16 | 499 | 1904942.422 | 0 | OW_NATURE_BIG_TREE_02_380P; OW_LOB_TREE_V11; ADDON_PLANTS_TREE_05_239P; ADDON_PLANTS_TREE_01_353P; ADDON_VALLEYPLANT_TREE_01_L_357P |
| BIGTREE | 1 | 100 | 614841.704 | 0 | NW_NATURE_BIGTREE_356P |
| TANNE | 2 | 741 | 551006.477 | 0 | NW_NATURE_TANNE_75P; NW_NATURE_TANNE_100P |
| BUSH | 16 | 2692 | 419951.112 | 0 | NW_NATURE_BUSH_120P; OW_NATURE_BUSH_02; OW_NATURE_BUSH_01; NW_NATURE_LONG_BUSH_360P; NW_NATURE_BUSH_25P |
| FARNTEPPICH | 2 | 866 | 399144.461 | 0 | NW_NATURE_FARNTEPPICH_306P; NW_NATURE_FARNTEPPICH_612P |
| CAVEWEBS | 3 | 779 | 205960.786 | 0 | NW_CAVEWEBS_V201; OW_CAVEWEBS_V1; OW_CAVEWEBS_V2 |
| WATERGRASS | 2 | 144 | 181557.386 | 0 | NW_NATURE_WATERGRASS_56P; NW_NATURE_WATERGRASS_02_100P |
| GRASSGROUP | 1 | 925 | 175553.993 | 0 | NW_NATURE_GRASSGROUP_01 |
| HOHETANNEN | 2 | 67 | 157978.534 | 0 | NW_NATURE_HOHETANNEN_01_531P; NW_NATURE_HOHETANNEN_02_531P |
| OLDTREE | 1 | 59 | 152970.184 | 0 | OW_NATURE_OLDTREE_02 |
| XL | 4 | 83 | 104145.377 | 0 | ADDON_VALLEYPLANT_TREE_02_XL_191P; ADDON_VALLEYPLANT_TREE_01_XL_357P; ADDON_VALLEYPLANT_TREE_04_XL_304P; ADDON_VALLEYPLANT_TREE_03_XL_344P |
| HANGPLANTS | 4 | 403 | 82353.177 | 0 | NW_NATURE_HANGPLANTS_02; NW_NATURE_HANGPLANTS_01; ADDON_PLANTS_HANGPLANTS_02_494P; ADDON_PLANTS_HANGPLANTS_01_374P |
| FARN | 3 | 969 | 62315.044 | 0 | NW_NATURE_FARN_102P; NW_NATURE_FARN_SMALL_102P; NW_NATURE_FARN_BIG_01 |
| HOHETANNE | 2 | 217 | 61278.397 | 0 | NW_NATURE_HOHETANNE_59P; NW_NATURE_HOHETANNE_91P |
| SMALLTREE | 3 | 711 | 59475.300 | 0 | OW_NATURE_SMALLTREE_01; NW_NATURE_SMALLTREE_79P; NW_NATURE_SMALLTREE_103P |
| STONE | 11 | 643 | 46012.512 | 0 | MIN_LOB_STONE_V2_30; MIN_MOB_STONE_V2_20; MIN_LOB_STONE_V3_30; ADDON_STONES_STONE_01_84P; MIN_LOB_STONE_V1_30 |
| BUSHES | 4 | 84 | 31296.758 | 0 | ADDON_CANYONPLANT_BUSHES_02_240P; ADDON_CANYONPLANT_BUSHES_05_168P; ADDON_CANYONPLANT_BUSHES_04_240P; ADDON_CANYONPLANT_BUSHES_01_120P |
| VERYHIGH | 1 | 28 | 27376.597 | 0 | NW_NATURE_HOHETANNE_VERYHIGH_59P |
| SPIDERWEBS | 1 | 218 | 27039.867 | 0 | NW_MISC_SPIDERWEBS_01 |
| JUNGLETREE | 3 | 118 | 25142.876 | 0 | ADDON_PLANTS_JUNGLETREE_02_163P; ADDON_PLANTS_JUNGLETREE_03_163P; ADDON_PLANTS_JUNGLETREE_04_163P |

## Покрытие

Число визуалов и семейств до первого достижения ≥50/80/90 % выбранной меры. Для каждой меры своя сортировка по убыванию; семейства ранжируются по сумме всех своих визуалов.

| Область | Мера | 50 %: визуалов | 50 %: семейств | 80 %: визуалов | 80 %: семейств | 90 %: визуалов | 90 %: семейств |
|---|---|---:|---:|---:|---:|---:|---:|
| Все миры | Экземпляры | 32 | 13 | 117 | 49 | 190 | 84 |
| Все миры | Площадь | 5 | 3 | 17 | 9 | 35 | 15 |
| NEWWORLD.ZEN | Экземпляры | 23 | 11 | 72 | 37 | 111 | 59 |
| NEWWORLD.ZEN | Площадь | 4 | 3 | 11 | 7 | 19 | 10 |

## Топ-15 районов NEWWORLD.ZEN по экземплярам

| Район | Экземпляров | Площадь, м² | Визуалов | Семейств | Визуалов для 80 % | Подвижных экземпляров | Топ визуалов |
|---|---:|---:|---:|---:|---:|---:|---|
| `NW_FOREST_PATH` | 934 | 230842.437 | 46 | 27 | 14 | 0 | NW_NATURE_FARNTEPPICH_306P:176;NW_NATURE_BUSH_25P:77;NW_NATURE_BUSH_120P:73;NW_NATURE_BAUMSTAMM_01_30P:63;NW_NATURE_FARN_SMALL_102P:58 |
| `NW_CITY_HABOUR` | 582 | 10210.857 | 98 | 74 | 31 | 85 | NW_NATURE_SIDEPLANT_01:98;NW_NATURE_BUSH_60P:36;NW_CITY_OILLAMP_01:29;NW_HARBOUR_CRATEPILE_01:27;NW_CITY_BEAMS_01:25 |
| `NW_TROLLAREA_PATH` | 515 | 177776.494 | 40 | 30 | 15 | 13 | NW_NATURE_BUSH_120P:78;NW_NATURE_GRASSGROUP_01:72;NW_NATURE_TANNE_75P:55;NW_NATURE_FARNTEPPICH_306P:32;NW_NATURE_FARN_102P:30 |
| `<NONE>` | 498 | 163763.417 | 65 | 52 | 25 | 25 | OW_NATURE_SMALLTREE_01:57;NW_CAVEWEBS_V201:35;NW_NATURE_LONG_BUSH_360P:32;NW_NATURE_HOHETANNE_59P:25;NW_HARBOUR_CRATEBROKEN_01:23 |
| `NW_XARDAS_TOWER` | 490 | 73763.008 | 75 | 58 | 30 | 29 | NW_NATURE_BUSH_300P:77;NW_CAVEWEBS_V201:40;NW_NATURE_GRASSGROUP_01:36;NW_NATURE_BUSH_120P:25;OW_MISC_WALL_TORCH_01:21 |
| `NW_CITY_TO` | 364 | 72987.436 | 49 | 34 | 18 | 8 | NW_NATURE_GRASSGROUP_01:57;NW_NATURE_BUSH_120P:41;NW_NATURE_BUSH_56P:32;NW_NATURE_FARN_SMALL_102P:23;NW_NATURE_FARNTEPPICH_306P:17 |
| `NW_FARM1_PATH` | 325 | 74947.083 | 40 | 30 | 13 | 24 | NW_NATURE_GRASSGROUP_01:53;NW_NATURE_BUSH_120P:47;NW_NATURE_BUSH_56P:29;NW_NATURE_FENCE_01:24;NW_NATURE_TANNE_100P:20 |
| `NW_TROLLAREA_RUINS` | 277 | 61940.035 | 55 | 44 | 21 | 7 | NW_CAVEWEBS_V201:51;NW_NATURE_STONE_CULT_STAND_01:33;NW_NATURE_BUSH_120P:27;NW_NATURE_STONE_CULT_TOP_01:18;NW_NATURE_SMALLTREE_79P:13 |
| `NW_FARM4_WOOD` | 244 | 182449.482 | 26 | 22 | 9 | 3 | NW_NATURE_BUSH_120P:48;NW_NATURE_TANNE_75P:34;NW_NATURE_FARNTEPPICH_306P:32;NW_NATURE_FARNTEPPICH_612P:17;NW_NATURE_BIGTREE_356P:16 |
| `NW_CITY_KANAL` | 239 | 5214.700 | 45 | 31 | 18 | 33 | OC_BARREL_V1_DESTROYED:38;OW_LOB_WOODPLANKS_V3:32;OC_SAECKE_01:21;NW_HARBOUR_CRATE_01:21;OC_LOB_GATE_SMALL3:12 |
| `NW_CITY_UPTOWN` | 194 | 5927.754 | 45 | 33 | 22 | 29 | NW_CITY_WINDOW_STONE_OUT_01:23;NW_CITY_WINDOW_WOOD_IN_02:21;NW_NATURE_BUSH_120P:11;NW_CITY_CUPBOARD_RICH_02:10;NW_HARBOUR_BARREL_01:10 |
| `NW_CITY_MERCHANT` | 185 | 5867.381 | 53 | 46 | 25 | 71 | NW_HARBOUR_BARREL_01:28;NW_CITY_WEAPON_BARREL_01:15;NW_HARBOUR_CRATE_01:13;NW_MISC_SIGN_01:11;NW_CITY_OILLAMP_01:9 |
| `NW_FARM1_CITYWALL` | 155 | 94185.941 | 23 | 17 | 10 | 4 | NW_NATURE_TANNE_75P:29;NW_NATURE_FARNTEPPICH_306P:29;NW_NATURE_BUSH_120P:12;NW_NATURE_GRASSGROUP_01:12;NW_NATURE_TANNE_100P:9 |
| `NW_TROLLAREA_RITUALFOREST` | 155 | 75255.907 | 28 | 23 | 12 | 2 | NW_NATURE_TANNE_75P:20;NW_NATURE_GRASSGROUP_01:19;NW_NATURE_HOHETANNE_59P:18;NW_NATURE_FARNTEPPICH_306P:13;NW_NATURE_HANGPLANTS_01:12 |
| `NW_PASS` | 133 | 5150.493 | 11 | 8 | 4 | 0 | OW_NATURE_SMALLTREE_01:42;OW_NATURE_BUSH_01:37;ORC_SKELETON_V4:27;ORC_SKELETON_V3:16;MIN_LOB_COUPLESTONES:5 |

## С чего начать

По числу экземпляров первым идёт семейство **BUSH**: экземпляров — 2692 (12.698 %), 16 разных визуалов, 419951.112 м². Это первый кандидат при приоритете количества.

Среди городских районов `NW_CITY_*` в топ-15 выше (кроме переходов `NW_CITY_TO`) наибольшее число экземпляров на визуал в наборе для 80 % даёт **NW_CITY_HABOUR**: разных визуалов — 98, экземпляров — 582. Топ-31 закрывает 468 экземпляров (80.412 %), 15.097 экземпляров на визуал. Полный набор района закрывает 98 визуалов; сокращённый набор для 80 % — 31. Это вывод из таблиц частот, без решений о геймплее.
