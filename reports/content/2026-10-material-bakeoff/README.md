# Сравнение плиточных PBR-материалов — октябрь 2026

**Сравнение не завершено: игровых снимков нет.** Готовы 30 материалов и 10 листов. Ещё 20 рецептов FLUX пропущены из-за HF gate; соглашение не принималось. Подготовка снимков остановилась после двух ошибок, до запуска игры на Ryzen. Выбор генератора остаётся за пользователем; все 50 записей имеют статус `draft`, по умолчанию ничего не подключается.

Реестр: [`data/content/registry.jsonl`](../../../data/content/registry.jsonl). В нём английские промпты, negative, сиды, параметры, полные ревизии HF, лицензии, результаты замеров и относительные пути шести карт. Пиксели Gothic не передавались ни одной модели: исходная плитка создаётся по тексту; Marigold и фильтр получают только эту сгенерированную плитку.

## Листы

В каждом листе пять колонок: FLUX+Marigold, FLUX+фильтр, SDXL+Marigold, SDXL+фильтр, StableMaterials. Строки: цвет с повторением 2×2, нормаль, шероховатость, плоскость под косым светом. Повтор 2×2 показывает границы без нарисованных поверх изображения направляющих. В колонках FLUX указана причина пропуска.

| Материал | Лист |
|---|---|
| Булыжная мостовая | [cobblestone.jpg](cobblestone.jpg) |
| Каменная кладка городской стены | [stone-wall.jpg](stone-wall.jpg) |
| Штукатурка фахверка | [plaster.jpg](plaster.jpg) |
| Доски пола | [floorboards.jpg](floorboards.jpg) |
| Брёвна и балки | [timber.jpg](timber.jpg) |
| Соломенная крыша | [thatch.jpg](thatch.jpg) |
| Черепица | [roof-tiles.jpg](roof-tiles.jpg) |
| Утоптанная земля и грязь | [mud.jpg](mud.jpg) |
| Лесная подстилка с травой | [forest-floor.jpg](forest-floor.jpg) |
| Скала | [rock.jpg](rock.jpg) |

CPU-превью [`material_preview.py`](../../../scripts/content/material_preview.py) использует numpy: sRGB → linear, нормализованную касательную нормаль, Ламберт и GGX со Smith masking и Schlick Fresnel, затем одинаковые экспозицию и преобразование цвета. Камера, свет и два повтора одинаковы для всех колонок. Это отдельный простой рендер: движок сейчас применяет нормаль и AO, но ещё не использует шероховатость и металличность для GGX.

## Генерация и параметры

[`bakeoff.py`](../../../scripts/content/bakeoff.py), [`stable_materials_adapter.py`](../../../scripts/content/stable_materials_adapter.py), зависимости — [`requirements.txt`](../../../scripts/content/requirements.txt). Запуск моделей был только на dev-ubuntu, RTX 3090. На Mac выполнялись проверки JSON, упаковка и CPU-превью.

- **SDXL:** 1024×1024, 30 шагов, EulerDiscreteScheduler из закреплённого snapshot, guidance 7, FP16, CPU offload. Круговой padding всех Conv2d UNet/VAE и явного асимметричного padding Downsample2D; ручной ретуши швов нет.
- **Marigold:** normals-v1-1 и iid-appearance-v1-1, по 4 шага, ensemble 1, processing resolution 1024, результат 1024×1024. Тот же круговой padding, включая Downsample2D. Цвет/шероховатость/металличность — appearance; нормаль — normals. Высота и AO — общий ненейросетевой помощник из исходной плитки, поскольку эти две модели их не выдают.
- **Фильтр:** яркость RGB, Gaussian bandpass σ=2 и 32 px с wrap; относительная высота, центральные периодические производные для нормали со strength 0,025. AO — локальные впадины высоты; шероховатость — константа вида материала плюс вариация яркости 0,14; металличность 0. Точные константы по материалам записаны в реестре. В паре SDXL+Marigold и SDXL+фильтр используется одна исходная плитка и один сид.
- **StableMaterials:** родные 512×512, стандартный UNet, 50 DDIM-шагов, guidance 10, `tileable=True` с родным feature rolling и padded decode. UNet/text encoder FP16, VAE FP32; используются только текстовые компоненты, vision encoder не загружался. Пять карт родные, AO вычислен из родной высоты. Код pipeline проверяется по SHA-256 закреплённой ревизии.
- **FLUX:** запланированы 1024×1024, 4 шага, guidance 0 и int8-квантование transformer/T5; запуск и реализация адаптера не выполнялись из-за gate. Веса FLUX не скачивались; показателей скорости и VRAM для него нет.

Сиды генерации — 31000–31009, Marigold — 41000–41009. Все десять положительных промптов занимают 50–53 токена CLIP, без обрезания. Общий стиль: средневековье, износ и грязь, приглушённые цвета, рассеянный свет без бликов и теней, строго сверху, бесшовная плитка, без текста и логотипов. Выполнение промпта моделью оценивается по самим листам.

Нормали PNG имеют OpenGL-конвенцию (+X вправо, +Y к верху изображения, +Z к наблюдателю). [MatSynth](https://huggingface.co/datasets/gvecchio/MatSynth) также описывает OpenGL-конвенцию. Для производного касательного базиса движка упаковщик меняет знак Y; X пишется в альфу DXT5nm, Y — в зелёный.

## Время и видеопамять

Измерено 19 октября 2026. Время включает получение результата, CPU-помощник и сохранение PNG, исключает скачивание весов и отдельную загрузку pipeline. Показаны десять материалов каждого конвейера. Время исходной SDXL-плитки учтено в обеих сравниваемых колонках; фактически она генерировалась один раз.

| Конвейер | Карты, px | Медиана, с/материал | Сумма на 10, с | Пик allocated / reserved, MiB |
|---|---:|---:|---:|---:|
| FLUX+Marigold | — | — | — | пропущен: gated |
| FLUX+фильтр | — | — | — | пропущен: gated |
| SDXL+Marigold | 1024 | 17,07 | 174,85 | 5439,6 / 9714,0 |
| SDXL+фильтр | 1024 | 11,79 | 118,68 | 5439,6 / 9714,0 |
| StableMaterials | 512 | 3,34 | 33,78 | 3831,1 / 5416,0 |

Пики — счётчики CUDA allocator процесса PyTorch, не общая память GPU: они не включают остальные сервисы и весь контекст драйвера. Загрузка pipeline выполнялась один раз на стадию: SDXL 5,442 с; normals 3,417 с; appearance 2,587 с; StableMaterials 4,878 с. Стадии Marigold дополнительно заняли 26,650 и 29,527 с; их пики allocated/reserved — 3123,5/4544 и 3129,6/5318 MiB. Фактическая сумма четырёх стадий — 208,628 с плюс 16,324 с загрузки. Перед каждой загрузкой и каждой из 40 инференций выполнен `nvidia-smi`; выборки и времена сохранены в реестре и внешнем `gpu.jsonl`.

## Модели и лицензии

| Модель | Ревизия HF | Лицензия |
|---|---|---|
| [FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell) | `741f7c3ce8b383c54771c7003378a50191e9efe9` | Apache-2.0; gated, не запускалась |
| [SDXL base 1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0) | `462165984030d82259a11f4367a4eed129e94a7b` | CreativeML OpenRAIL++-M |
| [Marigold normals v1-1](https://huggingface.co/prs-eth/marigold-normals-v1-1) | `09cfdd258cb281fa006cf1afcd2284376d16687d` | CreativeML OpenRAIL++-M |
| [Marigold IID appearance v1-1](https://huggingface.co/prs-eth/marigold-iid-appearance-v1-1) | `e7280a0a0fc5a0df0b36050882b3d8b77da22fd9` | CreativeML OpenRAIL++-M |
| [StableMaterials](https://huggingface.co/gvecchio/StableMaterials) | `4d452731535bd5c74261d6645623a573326f6f36` | `openrail` из HF metadata |

[Лицензия SDXL](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/462165984030d82259a11f4367a4eed129e94a7b/LICENSE.md) допускает использование модели и её результата с оговорёнными ограничениями использования; правообладатель не заявляет прав на результат. Карточки обеих моделей Marigold ссылаются на CreativeML OpenRAIL++-M. Карточка StableMaterials прямо включает разработку игр в intended use, но публикует только обозначение `openrail`, без полного текста и точного подварианта; в реестр не добавлен выдуманный вариант лицензии. Ограничений «только исследования» в этих карточках не заявлено. Выбора модели и принятия пользователем материалов этот отчёт не делает.

## GPU-окна и восстановление сервисов

[`run-materials.sh`](../../../scripts/content/run-materials.sh) проверяет игру на Mac перед остановкой, сериализует собственные GPU-запуски, ограничивает их оставшимся бюджетом и резервирует 600 секунд на восстановление. В этом прогоне останавливался **только `gothic-gemma`**. Остальные юниты, контейнеры, Docker, системные пакеты и настройки не менялись. `gothic-decider` оставался активным; после работы его порт 47832 ответил `HTTP/1.1 101 Switching Protocols` через адрес прослушивания.

Время ниже — UTC. «Готов» означает `is-active=active` и успешный ответ порта Gemma (`/v1/models`, HTTP 200). Длительность включает запрос остановки и ожидание готовности после старта, поэтому больше времени непосредственно неактивного юнита.

| Стадия | Запрос stop | Stop завершён | Start | Готов | Окно, с |
|---|---|---|---|---|---:|
| StableMaterials | 04:28:00 | 04:28:01 | 04:28:42 | 04:28:48 | 48 |
| SDXL | 04:29:20 | 04:29:20 | 04:31:27 | 04:31:33 | 133 |
| Marigold normals | 04:31:41 | 04:31:41 | 04:32:13 | 04:32:17 | 36 |
| Marigold appearance | 04:32:20 | 04:32:20 | 04:32:55 | 04:32:59 | 39 |

**Все остановленные юниты восстановлены. Сумма окон — 256 с (4 мин 16 с), лимит 3600 с соблюдён.**

В первом launcher проверка `pgrep -f` встретила ошибку локали; немедленная проверка с `LC_ALL=C` подтвердила отсутствие игры. Исправлены локаль, обработка ошибки и поиск точного имени процесса: аргументы других агентов тоже могут содержать `Gothic2Notr`. Первый launcher после успешной генерации и восстановления вернул 127, поскольку файл исправлялся во время его исполнения; следующие три запуска завершились кодом 0. GPU-стадии не повторялись. Ветка launcher с остановкой Decider не использовалась; её проверку порта через localhost нужно заменить на адрес фактического прослушивания перед таким запуском.

## Почему нет снимков из игры

Подготовлены [`pack_materials.py`](../../../scripts/content/pack_materials.py) и [`capture-materials.sh`](../../../scripts/content/capture-materials.sh). Они создают отдельный `OPENGOTHIC_CONTENT` для каждого варианта: цвет/ORM DXT1 с mip-цепочкой, нормаль DXT5nm. Компрессор общий с `test-material.py`; форматы и mip-цепочка соответствуют `scripts/hd/texpack.cpp`. Подменяется только `NW_MISC_OLDSTONEPATH_01.TGA` или только `NW_CITY_WALL_03.TGA`, scale=1. Прочие поверхности сохраняют исходный материал.

Сценарий — дневной префикс исходного [`rf.bench`](../../render-features/2026-10-18/tools/rf.bench), до `shot city`: `NW_CITY_MERCHANT_SHOP01_FRONT_03`, face 0, игровая камера, полдень, те же 26 секунд ожидания. Исходный `run-ryzen.sh` получает только другой корневой каталог задачи. Найден кеш движка с точным совпадением дерева исходников `897ab877aaf757e951a29540d887cededabe993de6352b7ae0eb86b5f64d5de0`; сборка и изменения движка не понадобились. Планируемая заявка — `gq quick`, 4 потока, 5G RAM, 4G VRAM, эксклюзивное окно, потолок 20 минут; записи только в `~/gothic-ai/material-bakeoff/`.

Две попытки подготовки команды снимков завершились кодом 1:

1. Bash 3.2 на Mac: обращение к пустому `PACK_ARGS[@]` при `set -u`. Исправлено; вторая попытка прошла этот участок.
2. Упаковка `bakeoff-stone-wall-sdxl-marigold`: **2 из 1 048 576 пикселей нормали имеют Z<0**, минимум −0,113725. DXT5nm восстанавливает только положительный Z; упаковщик отказался молча менять карту. Три комплекта DDS мостовой успели записаться вне git, до создания заявки Ryzen. Исходные PNG не изменялись.

По правилу двух попыток дальнейшие проверки остановлены. **Заявок стенда на Ryzen — 0; запущенных игр — 0; игровых снимков — 0 из 6 доступных после пропуска FLUX.** Четыре позиции снимков FLUX пропущены по той же причине gate. Нужно согласовать обработку двух отрицательных Z для DDS и разрешить продолжение после лимита. Варианты: стандартная проекция в положительную полусферу только при упаковке с сохранением исходного PNG либо пропуск этого снимка. Решение не принято.

## Файлы и воспроизведение

- dev-ubuntu: `~/gothic-ai/materials/out/bakeoff/<id>/{albedo,normal,roughness,metallic,height,ao}.png`; исходные SDXL-плитки — `out/bakeoff/_sources/sdxl/`. Веса, собственный venv, Python, uv/HF-кеши и логи остаются в `~/gothic-ai/materials/`. После генерации каталог занимал **22 313 124 974 байта**, меньше лимита 80 000 000 000.
- Mac: `~/.local/share/gothic-ai/content/bakeoff/`, 180 карт PNG и 10 исходных SDXL-плиток, плюс журнал окон. Пути `files` в реестре отсчитываются от `~/.local/share/gothic-ai/content/`.
- Частичные DDS: `~/.local/share/gothic-ai/content/bakeoff/captures/20261019T043344Z-67237/content/`. Это результаты подготовки, не снимки.
- В git — код, текстовые рецепты, этот отчёт и 10 уменьшенных JPG-листов 1482×1210. Веса, PNG и DDS в git не добавлены.

Подготовка нового venv на dev-ubuntu (все кеши внутри каталога задачи):

```sh
cd ~/gothic-ai/materials
export UV_CACHE_DIR="$PWD/uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/python"
export HF_HOME="$PWD/hf" HF_HUB_DISABLE_XET=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1
~/.local/bin/uv venv --python 3.11 .venv
~/.local/bin/uv pip install --python .venv/bin/python torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python bakeoff.py download --registry registry.jsonl --root "$PWD"
```

Перед этим код и реестр копируются из данного worktree в `~/gothic-ai/materials/`; `requirements.txt` лежит в `scripts/content/`. Команда `bakeoff.py prepare` заново создаёт рецепты по открытым HF metadata и пропускает gate без авторизации; для воспроизведения этого опыта нужно использовать уже сохранённые ревизии реестра. GPU-стадии запускаются с Mac через `scripts/content/run-materials.sh <stage>`; стадиями являются `stablematerials`, `sdxl`, `normals`, `appearance`. Готовые стадии пропускаются, поэтому рецепт для повторной генерации требует явного сброса своих измерений/состояния. Следующий прогон снимков пока заблокирован описанным выше лимитом и вопросом о формате.

Выполненные проверки:

```sh
uv run --no-project --with pytest python3 -m pytest -q scripts/content/test_registry.py
# 55 passed
python3 scripts/content/registry.py check --content-root ~/.local/share/gothic-ai/content
# 50 material recipes, external files verified
uv run --no-project --with numpy --with pillow python3 scripts/content/material_preview.py \
  --registry data/content/registry.jsonl --content-root ~/.local/share/gothic-ai/content \
  --output reports/content/2026-10-material-bakeoff
# 10 reduced JPG sheets
```

На dev-ubuntu завершены 40 инференций (10 SDXL, 10 StableMaterials, 10 normals, 10 appearance), получены 30 полных комплектов карт. Целевые проверки упаковки на синтетических данных прошли: 2 материала/6 DDS с mip-цепочками; после добавления преобразования Y — 1 материал/3 DDS. Живые данные выявили описанные два отрицательных Z. Полная регрессия и изменения `OpenGothic/` или `server/` не выполнялись.
