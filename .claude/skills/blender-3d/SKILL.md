---
name: blender-3d
description: 3D modelling in Blender for the Gothic AI project — building or editing models (props, furniture, parts), Blender Python scripts, processing generated meshes (cleanup, decimation, zones, UV, glTF export), and renders that check a model. Claude does this work itself; it is never handed to Codex or the codex-worker skill. Use whenever a task touches Blender or 3D models for the game, also in Russian (3D-модель, моделирование, блендер, Blender, меш, сетка, развёртка, UV, ретопология, low-poly, бочка, ящик, сундук, стол, лавка, GLB, glTF).
---

# 3D-моделирование в Blender

Модели, скрипты Blender и обработку мешей делаешь сам. Codex и `codex-worker` для этого не используются: это решение пользователя от 2026-10-20. Генераторы на GPU (TRELLIS на `dev-ubuntu`), загрузчик в движке и прочий код вне Blender — по обычным правилам.

## Где что лежит

- План и решения — `docs/own-content.md`, этап 2. Пилот — `reports/content/2026-10-3d-pilot/`.
- Цели предметов — `data/content/mesh3d-pilot.json`: bbox в метрах glTF, точка привязки, бюджет треугольников, зоны `{key, group, role}`.
- Готовый конвейер — `scripts/content/mesh3d/blender/` (README, `postprocess.py`, `parametric.py`, `sheet.py`). Сначала переиспользуй его, потом пиши новое.
- Blender — одна версия везде, **5.2 LTS**. На Mac (`/Applications/Blender.app/Contents/MacOS/Blender`) — короткие скрипты и быстрые рендеры. На Ryzen (`~/gothic-ai/mesh3d/blender`, 5.2.2, ставит `scripts/content/mesh3d/blender/install.sh`) — пакеты через `run-ryzen.sh` и `gq batch`. Новую версию ставим сразу на обе машины.

## Правила проекта

- **Вход.** Только текст, свои концепты `image_gen`, выход генераторов и числа оригинала: bbox, точка привязки, число треугольников. Вершины, текстуры и снимки оригинала не трогаем.
- **Выход — glTF:**
  - метры, +Y вверх; загрузчик сам переводит в сантиметры Gothic и отражает X;
  - имя материала = ключ `materials.json`;
  - группа звука и физики — `mat["group"]` (`WOOD`, `METAL`, …) с `export_extras=True`.
- **Текстуры** — плиточные материалы этапа 1 по зонам, с UV в метрах. Запечённую текстуру генератора в игру не берём, если пользователь не решил иначе.
- **Реестр и хранение.** Скрипты — в git. Результаты — вне git: `~/.local/share/gothic-ai/content-staging/…`. В реестр — SHA-256 и параметры.

## Как работать

1. **Сначала задумка.** Что это, из каких частей (клёпки, обручи, доски, ножки), размеры в метрах из цели, что видно игроку, бюджет треугольников. Части называй по роли, каждой части — свой слот материала по ключу зоны.
2. **Строй данными, а не операторами.** Используй `bmesh`, `bpy.data`, numpy через `foreach_get`/`foreach_set`. Операторы `bpy.ops` зависят от контекста и в режиме `-b` ломаются. Если без оператора никак — `with bpy.context.temp_override(...)`. После `bmesh` — `to_mesh`, `mesh.validate()`, `mesh.update()`.
3. **Настоящий масштаб с самого начала.** Применяй трансформации до модификаторов, зависящих от размера (Bevel, Solidify, Weld), и до экспорта. Начало координат — как в цели.
4. **Смотри на результат после каждого заметного шага.** Рендер 512–768 px: 3/4, спереди, сверху, плюс каркас (копия с модификатором Wireframe). Workbench — быстрый и без GPU. Открой PNG через Read и сравни с концептом и целью. Числам без картинки не верь.
5. **Проверки перед экспортом:**
   - треугольники в бюджете, bbox равен цели;
   - 0 открытых и non-manifold рёбер там, где их быть не должно;
   - нормали наружу;
   - UV-шахматка 10 см лежит ровно, а волокно идёт вдоль длинной стороны детали.
6. **Нормали.** `mesh.shade_smooth()` и `mesh.set_sharp_from_angle(angle=math.radians(30))`; `use_auto_smooth` больше нет (с 4.1). glTF сам расщепляет острые рёбра.
7. **Экспорт.** `bpy.ops.export_scene.gltf(export_format="GLB", use_selection=True, export_apply=True, export_yup=True, export_extras=True)`. Затем glTF Validator и, для игры, стенд `own-mesh-*.bench` на Ryzen с проверкой переписи.

## Чего избегать

- Не подгоняй критерий под результат и не крути одну ошибку бесконечно. Если одна проблема повторилась дважды — остановись, покажи картинку и числа, предложи решение.
- Простую утварь (бочка, ящик, стол, лавка) часто быстрее и чище построить параметрически, чем чистить выход генератора. Выбирай то, что лучше выглядит на рендере.
- Тяжёлые пакеты на Mac не гоняй: только `gq batch` на Ryzen.
