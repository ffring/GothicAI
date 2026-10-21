# Перепись оболочек Хориниса: портовый дом

2026-10-21. [Перепись CSV](census.csv): **69 связных кластеров ≥30 м²**. Выбор отдельного портового дома остановлен: заданная связность объединяет соседние здания, поэтому проверка дома у рынка **не прошла**. Игровая съёмка не запускалась: **0/6 кадров**, лист JPEG ещё не создан.

## Как считалась перепись

[`world-textures.cpp`](../../../scripts/content/world-textures.cpp), режим `--triangles`, читает только `Meshes.vdf`, `Meshes_Addon.vdf`, `Worlds.vdf`, `Worlds_Addon.vdf`, аддон перекрывает базу. Меш `NEWWORLD.ZEN` — BSP leaf-треугольники; статичные видимые vob `.3DS` → `.MRM`, `.MRM`, `.MSH` — с мировыми трансформами. NPC, декали, частицы и анимированные визуалы не экспортируются. Записи содержат только имя текстуры, visual/source, площадь, центр, ориентированную нормаль, три точки и три UV. Архивы текстур, оригинальные превью и листы не открывались; image_gen не использовался.

Прямоугольник города: X от −180 до 230 м, Z от −240 до 180 м, как область города в `intent_cameras.py`. Включаются целые треугольники, чьи рамки XZ пересекают прямоугольник; геометрия на границе не обрезается. Сырые JSON, геометрия земли и метаданные остаются вне git в `~/.local/share/gothic-ai/harbour-house/`.

Отобрана 31 строка [city-materials.csv](../../../data/content/city-materials.csv); 30 текстур встречаются внутри прямоугольника, `NW_CITY_STONEWALL_02.TGA` отсутствует. Вошли классы: `bound_timber_tile`, `framed_masonry_tile`, `masonry_dressed`, `masonry_panel`, `masonry_rubble`, `planks_wall`, `quay_wall_tile`, `roof_shingle`, `roof_tiles`, `stone_strip`, `timber_frame_tile`, `timber_strip`, `window_wall_tile`.

Это стены, оконные поля, балки, деревянные обвязки, дощатая обшивка и фронтоны, кровли, кладка, крепостные и подпорные стены с их каменными обвязками. Исключены все классы полов, земли, мостовых, пола причала, ступеней, внутренней конструкции крыши, `INSIDEWALL_*`, флагов/плюща и прочей альфы, сетей, верёвок и предметов. Каменный бордюр причала `HAFENRANDSTEIN_01` исключён отдельно. Для смешанных материалов vob сохраняются только реальные строительные семейства этой выгрузки: `NW_CITY_BEAM*`, `NW_MISC_POOR_HUT_*`; пушки, сети, полки, вывески, корабль и отдельные доски исключены, даже если используют текстуру балки.

Выгружены 61 448 треугольников выбранных текстур; после фильтра предметных visual остаются **44 610**. [`building_survey.py census`](../../../scripts/content/building_survey.py) объединяет общие вершины с округлением до 1 см и грани с истинным минимальным расстоянием ≤50 см (вершина–грань, ребро–ребро и пересечения). Получены **244 компоненты**, из них 69 площадью ≥30 м². Площадь — сумма 3D-площадей, а не площадь застройки. Центр взвешен площадью; размеры — мировая рамка XYZ в метрах; шесть текстур — по убыванию площади; ориентир — ближайший waypoint/freepoint по 3D-расстоянию до центра. ID упорядочены по центру X, Z, Y и стабильны при неизменных входах.

## Проверка дома у рынка: требуется решение

Рядом с `NW_CITY_MERCHANT_HUT_02_ENTRY` получен **B052**: оболочка **1514,855 м²**, рамка **33,246 × 11,331 × 37,347 м**. В него входят все **271/271** треугольников шести текстур эталона и ещё **594** треугольника соседней геометрии (всего 865). Все 865 относятся к мешу мира, а не предметным vob.

| Текстура (NW_CITY_) | Эталон, м² | B052, м² | Отклонение |
|---|---:|---:|---:|
| WALL_03 | 143.265 | 318.122 | +122.05% |
| WINDOW_03 | 22.599 | 52.570 | +132.62% |
| FACHWERKBASEMENT_WINDOW_01 | 35.974 | 67.325 | +87.15% |
| FACHWERKBASEMENT_EMPTY_01 | 109.587 | 185.329 | +69.12% |
| FACHWERKBASEMENT_CHAIN_01 | 28.397 | 51.681 | +81.99% |
| WOODBEAM_02 | 123.795 | 313.779 | +153.47% |

Допуск ±5% не выполнен по всем шести текстурам; максимум **153,47%**. Одна приёмочная попытка завершилась exit 1. Затем проверено происхождение связности: при расстоянии между гранями **0 см** (только общие вершины и касания) получаются 314 компонент вместо 244, но B052 по-прежнему содержит те же 865 треугольников и те же площади. Уменьшение допуска 50 см проблему этого дома не решает.

Для продолжения нужно выбрать: сохранять перепись связных блоков и изменить критерий сверки с эталоном, либо разрешить дополнительный способ разделения связных оболочек на отдельные здания. Отдельный алгоритм разделения брифом не задан и здесь не добавлен.

## Шесть кандидатов по заданному правилу

Фильтр: `HAFEN_HOLZWAND_02` ≥10 м². Сортировка — по сумме площадей `HAFEN_HOLZWAND_01`, `HAFEN_HOLZWAND_02`, `HAFEN_GIEBEL_01`, затем ID. Ниже именно кластеры связности; они пока не подтверждены как отдельные дома.

| ID | Оболочка, м² | HOLZWAND_01, м² | HOLZWAND_02, м² | GIEBEL_01, м² | Рамка X×Y×Z, м | Ориентир |
|---|---:|---:|---:|---:|---|---|
| B035 | 2730.885 | 973.886 | 274.586 | 42.479 | 27.90 × 9.03 × 40.38 | `NW_CITY_PATH_HABOUR_14` |
| B050 | 21428.029 | 356.272 | 159.103 | 17.094 | 191.13 × 31.16 × 177.48 | `NW_CITY_MERCHANT_TAVERN01_02` |
| B037 | 1012.778 | 272.034 | 157.497 | 17.094 | 23.62 × 6.03 × 25.02 | `NW_CITY_HABOUR_POOR_AREA_HUT_05_BED_02` |
| B036 | 1012.458 | 272.099 | 152.842 | 21.125 | 23.44 × 5.67 × 25.12 | `NW_CITY_HABOUR_HUT_05_IN` |
| B031 | 1012.251 | 271.973 | 141.878 | 32.060 | 30.85 × 5.65 × 19.57 | `NW_CITY_HABOUR_POOR_AREA_HUT_01_BED_02` |
| B032 | 1782.555 | 301.143 | 113.988 | 0.000 | 23.28 × 9.28 × 21.11 | `NW_CITY_HABOUR_PUFF_IN_06` |

B050 охватывает **191,13 × 177,48 м** по земле: это городской блок. Снимать его как «дом» до решения о разделении было бы неверно.

## Камеры и подготовка съёмки

[`harbour-house.bench`](../../../scripts/content/harbour-house.bench) порождён из переписи. Главная сторона выбирается по крупнейшему связному копланарному полю `HOLZWAND_02`, наружу — по нормали архива; диагонали триангуляции не делят стену. Камера смотрит на середину высоты оболочки, расстояние подбирается по всем её вершинам: вертикальный FOV движка 67,5°, 1280×720, запас 10% от края. Глаз — 170 см над верхней направленной вверх гранью земли ниже середины выбранного поля. Это геометрическая подготовка; видимость и положение в игре ещё не проверены.

Сценарий задаёт полдень, скрывает NPC, morph и эффекты, отключает автоматический подъём камеры, удерживает кадр после высокой точки телепорта. `capture-quality-pilot.sh/.py` поддерживает отдельную папку `QUALITY_PILOT_TASK=gothic-ai/harbour-house`, допускает только `original`, проверяет exit 0, шесть разных снимков 1280×720, пустой индекс и открытие трёх оригинальных материалов. `building_survey.py sheet` собирает шесть полных панелей с площадями в JPEG ≤1 000 000 байт.

Сборка неизменённого движка на Ryzen через `gq quick` **прошла, exit 0** (job `5cb7b7cebc5d`). Съёмка и JPEG ожидают решения выше. Рабочий контент, staging, концепт и текстуры дома у рынка не менялись.

## Воспроизведение

```sh
work="$HOME/.local/share/gothic-ai/harbour-house"
mkdir -p "$work"
uv run --no-project --with numpy --with scipy python - <<'PYTEXTURES'
import csv, sys
from pathlib import Path
sys.path.insert(0, "scripts/content")
from building_survey import shell_rows
rows = shell_rows(csv.DictReader(open("data/content/city-materials.csv")))
(Path.home()/".local/share/gothic-ai/harbour-house/shell-textures.txt").write_text("\n".join(rows)+"\n")
PYTEXTURES
scripts/content-textures.sh --triangles "$work/triangles.json" \
  --textures "$work/shell-textures.txt" --bounds -18000 -24000 23000 18000
remote_task="$(ssh ryzen 'printf %s "$HOME/gothic-ai/harbour-house"')"
ssh ryzen 'mkdir -p "$HOME/gothic-ai/harbour-house"'
rsync -a scripts/content/building_survey.py data/content/city-materials.csv \
  "$work/triangles.json" "ryzen:$remote_task/"
GQ_OWNER=harbour-house scripts/gq --host ryzen run --class quick \
  --threads 2 --mem 5G --max 12m --wait-max 900 --name 'harbour census' -- \
  env OPENBLAS_NUM_THREADS=1 "${remote_task%/gothic-ai/harbour-house}/.local/bin/uv" run --no-project --with numpy --with scipy \
  python "$remote_task/building_survey.py" census --input "$remote_task/triangles.json" \
  --mapping "$remote_task/city-materials.csv" --output "$remote_task/census.csv" \
  --metadata "$remote_task/survey.json" --scenario "$remote_task/harbour-house.bench"
rsync -a "ryzen:$remote_task/census.csv" reports/content/2026-10-harbour-house/
rsync -a "ryzen:$remote_task/survey.json" "$work/survey.json"
rsync -a "ryzen:$remote_task/harbour-house.bench" scripts/content/
# After copying market-house-measurement.json to the remote task, adding
# --market-measurement "$remote_task/market-house-measurement.json"
# deliberately fails the current acceptance gate.
```

После решения и регенерации камер для отдельных домов:

```sh
QUALITY_PILOT_TASK=gothic-ai/harbour-house scripts/content/capture-quality-pilot.sh \
  --capture original "$work/captures"
uv run --no-project --with numpy --with scipy --with pillow python scripts/content/building_survey.py sheet \
  --metadata "$work/survey.json" --captures "$work/captures"
```

## Проверки

- `scripts/content-textures.sh --self-test`: 27/27, exit 0.
- Выгрузка: exit 0, 61 448 записей с конечными координатами/UV, 36 616 опорных треугольников, 2959 waypoint и 2289 freepoint.
- Синтетика нового скрипта: 17/17; общая стена объединяет два дома, порог 50/50,01 см, близость середин рёбер, пересечение граней, дом и земля, отбор классов и предметных visual, крупнейшая разбитая на треугольники стена и кадрирование.
- `python3 scripts/content/map_textures.py check`: exit 0, 78 строк.
- Полный запрошенный набор `uv run --no-project --with pytest --with numpy --with pillow --with scipy python -m pytest -q scripts/content/`: **843 passed, 1 failed**, 234,79 с. Вне этой задачи упал `mesh3d/test_run_mesh3d.py::test_restore_records_each_service_readiness_independently[30-1]`: timeout дочернего процесса 20 с. Код mesh3d не менялся; повтор полного набора не запускался.
- Сверка рынка ±5%: exit 1, 0/6 текстур в допуске, одна приёмочная попытка; диагностика связности при 0/50 см не меняет результат.
- Игровая съёмка: не запускалась, 0/6 кадров; JPEG отсутствует.
