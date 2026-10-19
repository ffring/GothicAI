# Сравнение плиточных PBR-материалов

Готовы **30 материалов, 10 листов и 6 снимков из игры**. Ещё 20 вариантов FLUX пропущены: Hugging Face требует соглашение, оно не принималось. Все 50 записей — `draft`; генератор выбирает пользователь.

[Реестр](../../../data/content/registry.jsonl) содержит промпты, negative, сиды, параметры, полные ревизии HF, лицензии, время и пути карт. Генераторы получали только текст. Marigold и фильтр обрабатывали только созданные плитки; пиксели Gothic им не передавались.

## Листы материалов

Пять колонок: FLUX+Marigold, FLUX+фильтр, SDXL+Marigold, SDXL+фильтр, StableMaterials. Четыре строки: плитка цвета 2×2 для просмотра швов, нормаль, шероховатость и плоскость под косым светом. [CPU-превью](../../../scripts/content/material_preview.py) считает Ламберт + GGX в numpy с одинаковыми камерой, светом и экспозицией.

| Материал | Лист |
|---|---|
| Булыжная мостовая | [Открыть](cobblestone.jpg) |
| Каменная кладка городской стены | [Открыть](stone-wall.jpg) |
| Штукатурка фахверка | [Открыть](plaster.jpg) |
| Доски пола | [Открыть](floorboards.jpg) |
| Брёвна и балки | [Открыть](timber.jpg) |
| Соломенная крыша | [Открыть](thatch.jpg) |
| Черепица | [Открыть](roof-tiles.jpg) |
| Утоптанная земля и грязь | [Открыть](mud.jpg) |
| Лесная подстилка с травой | [Открыть](forest-floor.jpg) |
| Скала | [Открыть](rock.jpg) |

## Снимки из игры

Шесть вариантов сняты на Ryzen через `gq` (заявка `c35d79720ea8`): **6 игр, 6 завершений с кодом 0**, нужная точка и загрузка материала подтверждены журналами. JPG уменьшены с 1920×1080 до 1280×720. Мостовая видна перед героем, подмена стены — на левом фасаде дома.

| Конвейер | Мостовая | Стена |
|---|---|---|
| SDXL+Marigold | [Снимок](game-cobblestone-sdxl-marigold.jpg) | [Снимок](game-stone-wall-sdxl-marigold.jpg) |
| SDXL+фильтр | [Снимок](game-cobblestone-sdxl-filter.jpg) | [Снимок](game-stone-wall-sdxl-filter.jpg) |
| StableMaterials | [Снимок](game-cobblestone-stablematerials.jpg) | [Снимок](game-stone-wall-stablematerials.jpg) |

Четыре позиции FLUX пропущены вместе с моделью из-за gate.

Используется дневной префикс исходного [rf.bench](../../render-features/2026-10-18/tools/rf.bench): `NW_CITY_MERCHANT_SHOP01_FRONT_03`, face 0, игровая камера, полдень и 26 секунд ожидания. Подмена через `OPENGOTHIC_CONTENT`: только `NW_MISC_OLDSTONEPATH_01.TGA` либо только `NW_CITY_WALL_03.TGA`, scale=1. Цвет и ORM — DXT1, нормаль — DXT5nm, все с mip-уровнями. В текущей сборке игры освещение учитывает нормаль и AO; GGX показан в отдельном CPU-превью.

По решению пользователя упаковщик для Z<0 ставит Z=1e-6 и нормализует вектор. **Прижаты 2 из 1 048 576 пикселей нормали стены SDXL+Marigold; у остальных пяти вариантов — 0.** Знак Y меняется под касательный базис движка; X хранится в альфе DDS, Y — в зелёном. Исходные PNG не менялись: SHA-256 всех шести нормалей совпали до и после упаковки.

## Скорость и VRAM

Замер 19 октября 2026, dev-ubuntu, RTX 3090. Время включает получение карт и сохранение PNG, без скачивания и отдельной загрузки модели. Исходная SDXL-плитка одна на пару способов получения карт; её время учтено в обеих строках сравнения.

| Конвейер | Размер | Медиана, с | Сумма на 10, с | Пик allocated / reserved, MiB |
|---|---:|---:|---:|---:|
| FLUX+Marigold | — | — | — | gated |
| FLUX+фильтр | — | — | — | gated |
| SDXL+Marigold | 1024 | 17,07 | 174,85 | 5439,6 / 9714,0 |
| SDXL+фильтр | 1024 | 11,79 | 118,68 | 5439,6 / 9714,0 |
| StableMaterials | 512 | 3,34 | 33,78 | 3831,1 / 5416,0 |

Это пики CUDA allocator процесса, без памяти остальных сервисов и полного контекста драйвера. Фактически четыре стадии заняли 208,628 с плюс 16,324 с загрузки. Перед загрузками и всеми 40 инференциями выполнялся `nvidia-smi`; измерения сохранены в реестре.

SDXL: 30 шагов, guidance 7, FP16 и CPU offload. Круговой padding включён в UNet/VAE, включая асимметричный Downsample2D. Marigold: по 4 шага, ensemble 1, 1024 px; нормаль — normals, цвет/шероховатость/металличность — appearance. Высота и AO получены общим фильтром. Фильтр: периодический bandpass яркости σ=2/32 px, нормаль из высоты, AO из впадин, шероховатость по виду материала с вариацией яркости, металличность 0. StableMaterials: родные карты 512 px, 50 DDIM-шагов, guidance 10, родной `tileable=True`; AO добавлен из высоты. Сиды 31000–31009, Marigold 41000–41009. Точные рецепты и константы — в реестре и [скриптах](../../../scripts/content/bakeoff.py).

## Лицензии

| Модель | Лицензия |
|---|---|
| [FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell) | Apache-2.0; пропущена из-за gate |
| [SDXL base 1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0) | CreativeML OpenRAIL++-M |
| [Marigold normals v1-1](https://huggingface.co/prs-eth/marigold-normals-v1-1) | CreativeML OpenRAIL++-M |
| [Marigold appearance v1-1](https://huggingface.co/prs-eth/marigold-iid-appearance-v1-1) | CreativeML OpenRAIL++-M |
| [StableMaterials](https://huggingface.co/gvecchio/StableMaterials) | `openrail` из HF metadata |

[Лицензия SDXL](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/462165984030d82259a11f4367a4eed129e94a7b/LICENSE.md) допускает использование результата с её ограничениями; правообладатель не заявляет прав на результат. Обе карточки Marigold ссылаются на OpenRAIL++-M. StableMaterials прямо называет разработку игр своим применением, но публикует только `openrail`, без полного текста и точного подварианта; именно это записано в реестре.

## Остановки сервисов

При генерации останавливался только Gemma. «Готов» ниже означает `is-active=active` и HTTP 200 от `/v1/models`. Время — UTC, 19 октября 2026; длительность включает ожидание готовности после старта.

| Стадия | Запрос stop | Stop завершён | Start | Готов | Секунд |
|---|---|---|---|---|---:|
| StableMaterials | 04:28:00 | 04:28:01 | 04:28:42 | 04:28:48 | 48 |
| SDXL | 04:29:20 | 04:29:20 | 04:31:27 | 04:31:33 | 133 |
| Marigold normals | 04:31:41 | 04:31:41 | 04:32:13 | 04:32:17 | 36 |
| Marigold appearance | 04:32:20 | 04:32:20 | 04:32:55 | 04:32:59 | 39 |

**Все остановленные юниты восстановлены; суммарное окно 256 с из разрешённых 3600 с.** Decider не останавливался, его порт после генерации ответил WebSocket HTTP 101. Для продолжения со снимками новых остановок не требовалось.

## Файлы и проверки

Карты: `~/gothic-ai/materials/out/bakeoff/` на dev-ubuntu; копия — `~/.local/share/gothic-ai/content/bakeoff/` на Mac. Пути реестра считаются от каталога `content/`. Получены 180 карт PNG и 10 исходных SDXL-плиток. Весь каталог задачи на dev-ubuntu после генерации занимал 22,31 ГБ из лимита 80 ГБ. В git находятся рецепты и 16 уменьшенных JPG; веса, PNG и DDS — вне git. Исходные кадры: `~/.local/share/gothic-ai/content/bakeoff/captures/20261019T044335Z-19309/captures/<id>/city.png` на Mac и `~/gothic-ai/material-bakeoff/runs/20261019T044335Z-19309/<id>/city.png` на Ryzen.

Реестр: 55 тестов прошли; `registry.py check --content-root ~/.local/share/gothic-ai/content` проверил 50 записей и внешние файлы. На dev-ubuntu завершены 40 инференций, получены 30 полных комплектов; созданы 10 листов 1482×1210. Проверка проекции DDS подтвердила прижим 2/256 синтетических пикселей, нормализацию и сохранность пяти входных PNG. На Ryzen одна заявка `gq` успешно сняла все шесть доступных вариантов; кадры просмотрены, камера и подменяемые поверхности совпадают с заданием.

Повторный запуск снимков: `RYZEN_LOCK_WAIT=7200 scripts/content/capture-materials.sh --content-root ~/.local/share/gothic-ai/content --output ~/.local/share/gothic-ai/content/bakeoff/captures`. Генерация и установка собственного venv описаны в [bakeoff.py](../../../scripts/content/bakeoff.py), [run-materials.sh](../../../scripts/content/run-materials.sh) и [requirements.txt](../../../scripts/content/requirements.txt).
