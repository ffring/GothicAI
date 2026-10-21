# Перепись связных блоков Хориниса: кандидаты портового дома

2026-10-21. [CSV](census.csv): **69 связных блоков ≥30 м²**. Выбраны шесть блоков с портовой дощатой обшивкой; сняты **12/12 кадров original**, игра завершилась с **exit 0**. Пользователь выбирает дом по кадрам, затем его геометрию обмеряем ручной рамкой, как у дома у рынка. Автоматического разделения блоков на отдельные здания нет.

![Шесть блоков: front слева, three-quarter справа](candidates.jpg)

Лист — **1280×2544**, шесть строк по две панели **640×360**, **662 104 байта** (JPEG quality 90). Каждый полный игровой кадр 1280×720 уменьшен вдвое без обрезки. Подписи: id блока, ближайший waypoint/freepoint и площади трёх портовых текстур по всему блоку.

## Как считалась перепись

[`world-textures.cpp`](../../../scripts/content/world-textures.cpp), режим `--triangles`, читает только `Meshes.vdf`, `Meshes_Addon.vdf`, `Worlds.vdf`, `Worlds_Addon.vdf`; аддон перекрывает базу. Меш `NEWWORLD.ZEN` — BSP leaf-треугольники; статичные видимые vob `.3DS` → `.MRM`, `.MRM`, `.MSH` — с мировыми трансформами. NPC, декали, частицы и анимированные визуалы не входят. Записи содержат имя текстуры, visual/source, площадь, центр, ориентированную нормаль, три точки и три UV. Дополнительно выходят геометрия заслонений, направленные вверх грани с именами их материалов и waypoint/freepoint. Пиксели текстур, их превью и листы не открывались; image_gen не использовался.

Прямоугольник города: X от −180 до 230 м, Z от −240 до 180 м, как область города в `intent_cameras.py`. Включаются целые треугольники, чьи рамки XZ пересекают прямоугольник; геометрия на границе не обрезается. Сырые JSON и метаданные остаются вне git в `~/.local/share/gothic-ai/harbour-house/`.

Отобрана 31 строка [city-materials.csv](../../../data/content/city-materials.csv); 30 текстур встречаются внутри прямоугольника, `NW_CITY_STONEWALL_02.TGA` отсутствует. Вошли классы: `bound_timber_tile`, `framed_masonry_tile`, `masonry_dressed`, `masonry_panel`, `masonry_rubble`, `planks_wall`, `quay_wall_tile`, `roof_shingle`, `roof_tiles`, `stone_strip`, `timber_frame_tile`, `timber_strip`, `window_wall_tile`.

Это стены, оконные поля, балки, деревянные обвязки, дощатая обшивка и фронтоны, кровли, кладка, крепостные и подпорные стены с каменными обвязками. Исключены полы, земля, мостовые, пол причала, ступени, внутренняя конструкция крыши, `INSIDEWALL_*`, флаги/плющ и прочая альфа, сети, верёвки и предметы. Каменный бордюр причала `HAFENRANDSTEIN_01` исключён отдельно. У статичных vob сохраняются строительные семейства этой выгрузки `NW_CITY_BEAM*`, `NW_MISC_POOR_HUT_*`; пушки, сети, полки, вывески, корабль и отдельные доски исключены, даже если используют материал балки.

Выгружены **61 448** треугольников выбранных текстур. После фильтра предметных visual остаются **44 610**. [`building_survey.py census`](../../../scripts/content/building_survey.py) объединяет общие вершины с округлением до 1 см и грани с истинным минимальным расстоянием ≤50 см: вершина–грань, ребро–ребро и пересечения. Получены **244 компоненты**, из них **69 блоков** площадью ≥30 м². Общая стена объединяет соседние постройки в один блок.

Площадь — сумма 3D-площадей оболочки, а не площадь застройки и не площадь отдельного дома. Центр взвешен площадью; размеры — мировая рамка XYZ в метрах; шесть главных текстур — по убыванию площади; ориентир — ближайший waypoint/freepoint по 3D-расстоянию до центра. ID упорядочены по центру X, Z, Y и стабильны при неизменных входах.

## Сверка дома у рынка

**Пройдена: B052 содержит 271/271 эталонный треугольник шести текстур, пропусков 0.** Проверяется мультимножество имени текстуры, visual и трёх мировых вершин с точностью 0,001 см; порядок вершин не влияет, повторяющиеся треугольники учитываются по количеству. Дополнительная геометрия блока допустима. Источник — `~/.local/share/gothic-ai/texture-intent/market-house-measurement.json`.

Блок B052 рядом с `NW_CITY_MERCHANT_HUT_02_ENTRY`: **865 треугольников**, **1514,855 м²**, рамка **33,246 × 11,331 × 37,347 м**. Он содержит дом у рынка и соседнюю оболочку. Это проверка сохранности геометрии, а не попытка автоматически выделить отдельный дом.

## Шесть кандидатов

Фильтр: `HAFEN_HOLZWAND_02` ≥10 м² и рамка не больше **40 м по X и Z**. Сортировка — по сумме площадей `HAFEN_HOLZWAND_01`, `HAFEN_HOLZWAND_02`, `HAFEN_GIEBEL_01`, затем ID. B050 исключён из-за рамки **191,127 × 177,477 м** по земле; B035 также исключён: Z = **40,375 м**.

Площади ниже относятся ко всему блоку; после выбора дома его границы и площади снимем отдельно вручную.

| Блок | Оболочка, м² | HOLZWAND_01, м² | HOLZWAND_02, м² | GIEBEL_01, м² | Рамка X×Y×Z, м | Ориентир |
|---|---:|---:|---:|---:|---|---|
| B037 | 1012.778 | 272.034 | 157.497 | 17.094 | 23.62 × 6.03 × 25.02 | `NW_CITY_HABOUR_POOR_AREA_HUT_05_BED_02` |
| B036 | 1012.458 | 272.099 | 152.842 | 21.125 | 23.44 × 5.67 × 25.12 | `NW_CITY_HABOUR_HUT_05_IN` |
| B031 | 1012.251 | 271.973 | 141.878 | 32.060 | 30.85 × 5.65 × 19.57 | `NW_CITY_HABOUR_POOR_AREA_HUT_01_BED_02` |
| B032 | 1782.555 | 301.143 | 113.988 | 0.000 | 23.28 × 9.28 × 21.11 | `NW_CITY_HABOUR_PUFF_IN_06` |
| B034 | 1510.885 | 203.139 | 157.459 | 14.966 | 26.58 × 9.08 × 16.79 | `NW_CITY_HABOUR_TAVERN01_STAND_01` |
| B023 | 521.970 | 113.986 | 92.019 | 28.233 | 19.91 × 19.63 × 16.74 | `NW_LIGHTHOUSE_IN_07` |

## Камеры и съёмка

[`harbour-house.bench`](../../../scripts/content/harbour-house.bench) порождён из переписи. Основа фасада — крупнейшее связное копланарное поле `HOLZWAND_02`: нормали в пределах 5°, плоскости в пределах 0,5 см, общие вершины ≈1 см. Затем берутся все треугольники оболочки блока, у которых все три вершины лежат в полосе **±50 см** от этой плоскости. Наружная сторона направлена от центра блока; это разворачивает внутренние исходные нормали B031 и B023.

Кадр охватывает фасад по высоте от основания до деревянного конька, по горизонтали — с полями **3 м** с каждой стороны. Верх берётся по кровлям `ROOF_01/04` и `HAFEN_GIEBEL_01`, поэтому у B023 учитывается конёк **29,956 м**, а не верх каменной башни **43,724 м**. `front` смотрит прямо на плоскость; `three-quarter` — с той же наружной стороны под **45°**. Позиции могут смещаться вдоль фасада, сохраняя эти углы.

Глаз во всех 12 позициях находится **на 170 см над опорной землёй**. Опора выбирается по материалам улиц, мостовой, почвы и скал; крыши, внутренние полы, деревья, вода и альфа исключены. Видимость выбранного поля проверяется лучами по **904 971** треугольнику мира и видимых статичных vob, включая деревья и предметы. Естественные заслонения в плотной застройке и растительность на кадрах сохраняются.

В узких переулках использован вертикальный **FOV 125°**. `capture-quality-pilot.sh` создаёт в собственной папке съёмки ссылки на исходную установку и отдельный `SystemPack.ini` с этим FOV; общий конфиг игры не меняется. Проекция при расчёте камеры использует тот же угол, формат 1280×720 и запас 4% от края. Это широкоугольные обзорные кадры для выбора постройки.

Полдень, эффекты и morph скрыты. Штатная команда `clear 1000000` убрала **1052 NPC** только из временного мира этого запуска; это исключает оставшиеся отдельные головы после скрытия Animated. Сохранение берётся копией. Рабочий контент, staging, концепт и текстуры дома у рынка не менялись.

Съёмка — только `original`, через `gq quick` с GPU и эксклюзивным окном на Ryzen, собственная папка `QUALITY_PILOT_TASK=gothic-ai/harbour-house`. Использован ранее собранный в этой папке неизменённый движок. Финальная игра: **exit 0, 12/12 PNG 1280×720, [bench] done, 0 подмен ресурсов**; открыты все три портовых оригинала. Валидатор требует шесть пар `Bxxx-front` / `Bxxx-three-quarter`, пустой индекс и отсутствие ошибок игры. Полные PNG, game.log, exit.txt и census.json лежат в `~/.local/share/gothic-ai/harbour-house/captures/original/`.

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
rsync -a scripts/content/building_survey.py scripts/content/intent_cameras.py data/content/city-materials.csv \
  "$work/triangles.json" "$HOME/.local/share/gothic-ai/texture-intent/market-house-measurement.json" \
  "ryzen:$remote_task/"
GQ_OWNER=harbour-house scripts/gq --host ryzen run --class quick \
  --threads 2 --mem 5G --max 12m --wait-max 900 --name 'harbour census' -- \
  env OPENBLAS_NUM_THREADS=1 "${remote_task%/gothic-ai/harbour-house}/.local/bin/uv" run --no-project --with numpy --with scipy \
  python "$remote_task/building_survey.py" census --input "$remote_task/triangles.json" \
  --mapping "$remote_task/city-materials.csv" --output "$remote_task/census.csv" \
  --metadata "$remote_task/survey.json" --scenario "$remote_task/harbour-house.bench" \
  --market-measurement "$remote_task/market-house-measurement.json"
rsync -a "ryzen:$remote_task/census.csv" reports/content/2026-10-harbour-house/
rsync -a "ryzen:$remote_task/survey.json" "$work/survey.json"
rsync -a "ryzen:$remote_task/harbour-house.bench" scripts/content/
```

Съёмка и лист (используется собранный в этой папке неизменённый движок):

```sh
QUALITY_PILOT_TASK=gothic-ai/harbour-house scripts/content/capture-quality-pilot.sh \
  --capture original "$work/captures"
uv run --no-project --with numpy --with scipy --with pillow python scripts/content/building_survey.py sheet \
  --metadata "$work/survey.json" --captures "$work/captures"
```

## Проверки

- `scripts/content-textures.sh --self-test`: **27/27**, exit 0. Выгрузка: **61 448** выбранных треугольников, **36 616** направленных вверх граней с материалами, **904 971** грань заслонений; 2959 waypoint и 2289 freepoint.
- Синтетика `test_building_survey.py`: **31/31**. Общая стена, порог 50/50,01 см, расстояния и пересечения, отбор классов и предметов, включение эталона с учётом повторов, граница 40 м, фасадная полоса/поля, наружная сторона, высота кровли, земля, смещение камеры, 12 кадров и лист.
- Контентные тесты: **860 passed, 2 deselected за 58,79 с**, exit 0. Команда ниже исключает оба варианта указанного пользователем `mesh3d/test_run_mesh3d.py::test_restore_records_each_service_readiness_independently`: ранее чужой тест с таймаутом 20 с падал под нагрузкой; его код не менялся.
- `python3 scripts/content/map_textures.py check`: **78 строк**, exit 0.
- Финальная съёмка: **12/12**, exit 0, **0** подмен. Лист: **12 панелей, 662 104 байта**.

```sh
OPENBLAS_NUM_THREADS=1 uv run --no-project --with pytest --with numpy --with pillow --with scipy \
  python -m pytest -q scripts/content/ \
  --deselect scripts/content/mesh3d/test_run_mesh3d.py::test_restore_records_each_service_readiness_independently
python3 scripts/content/map_textures.py check
```
