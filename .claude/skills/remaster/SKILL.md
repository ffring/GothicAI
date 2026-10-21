---
name: remaster
description: Remaster pipeline for Gothic content — rebuild a building, a set of props or a district so it is "the same Gothic, better made" — intent sheet of the original, concept, image_gen concept and texture edits, processing, in-game pairs, a separate reviewer and the user's gates, with every artifact stored and backed up. Use whenever work starts or continues on replacing a Gothic building, its textures, a prop set or a district look, when a concept or texture must be judged, or when the user gives a verdict on such work — also in Russian (ремастер, переделать дом, текстуры дома, концепт здания, замысел оригинала, лист замысла, рецензент, ревью картинки, квартал, хижина, фасад, окно, как в оригинале).
---

# Конвейер ремастера

Процесс и его причины — [docs/remaster-pipeline.md](../../../docs/remaster-pipeline.md). Здесь — что делать по шагам.

**Цель — та же Готика, только лучше сделана.** Форма, пропорции, главная деталь и настроение — у оригинала. Исполнение — твоё, лучше по ремеслу. Если видишь, как сделать форму «лучше», запиши это одной фразой в «Предлагаемые отступления» и делай как в оригинале. Отступление от формы оригинала — это изменение объёма работы, без согласия пользователя его нет. Не сужай, не расширяй и не перерабатывай задачу молча.

Всю работу делаешь сам. Codex только запускает `image_gen` с твоим промптом в свежем треде.

Перед работой прочитай [список отклонённых дефолтов](../../../docs/remaster-defaults.md): это твои прежние промахи. Перед каждым показом пользователю проверь по нему результат.

Инструменты:
- `R` — это `uv run --no-project --with numpy --with pillow --with scipy python scripts/content/remaster.py`;
- `B` — это `uv run --no-project --with numpy --with pillow --with scipy python scripts/content/building_set.py`.

## 0. Ассет и оригинал

1. `R new <ассет> --title "…"` создаёт `reports/content/remaster/<ассет>/` с `asset.json`, `intent.md`, `concept.md`, `decisions.md`.
2. Обмер и кадры оригинала:
   - геометрия — `world-textures.cpp --triangles` (`scripts/content-textures.sh`), перепись `building_survey.py`, обмер по образцу `harbour_hut.py`;
   - кадры — камеры в `cameras.json` ассета (глаз и цель в см мира), `R bench <ассет>` пишет `scripts/content/<ассет>.bench`. Снимает `capture-quality-pilot.sh` на Ryzen с `QUALITY_PILOT_TASK=gothic-ai/remaster-<ассет>`: `--prepare` один раз на сборку движка, `--scenario` после правки камер, `--capture original <каталог>`. Полдень, обычный FOV, NPC скрыты, издалека и вблизи главной детали.
3. Сам смотри кадры оригинала. Пиксели оригинала никогда не идут в `image_gen`.
4. Разведи текстуру и модели:
   - открой превью оригинальных текстур постройки — `~/.local/share/gothic-ai/city-materials-review/2026-10-19/thumbs/<TEXTURE>.TGA.png`, только для себя;
   - найди модели рядом по переписи мешей (`reports/content/2026-10-world-meshes/`).

   Что на кадре нарисовано в текстуре, а что стоит отдельной моделью — по кадру не угадывай. Большое окно хижины порта оказалось моделью `NW_CITY_WINDOW_WOOD_OUT_01`, а текстура несла маленький тёмный проём.
5. Проверь, чьи это текстуры, по [очереди переделки](../../../docs/remaster-pipeline.md#очередь-переделки). Текстура принадлежит одному ассету. Общий материал (кровля, балка) — свой ассет: его лист замысла перечисляет все места, где он стоит (перепись блоков `reports/content/2026-10-harbour-house/census.csv`), и снимает хотя бы по одному месту каждого вида построек.

## 1. Лист замысла (`intent.md`)

Заполни по кадрам и обмеру. Обязательно:
- главная деталь с замером — доля стены, высота, количество;
- черты оригинала в классах «замысел / ограничение / то и другое»;
- напряжения, которые нельзя «чинить»;
- правила квартала с явной областью действия — по номерам из [арт-библии Хориниса](../../../docs/khorinis-art-bible.md#правила-кварталов): правила квартала постройки и правило общих материалов О1. Правило с источником «Claude» до подтверждения пользователя — гипотеза, проверь его по кадрам ассета;
- атомарный **список сохранения**, симметричными вопросами для слепой пары;
- «Отступления: нет».

Числа бери из обмера и кадра, не на глаз.

`R check <ассет> --stage intent`. **В1:** у первой постройки нового типа или квартала покажи пользователю список сохранения. Его ответ запиши: `R decide <ассет> --gate preserve --verdict approved --quote "…"`.

## 2. Концепция (`concept.md`)

Конструкция, материалы и как они стареют, что объёмное и что нарисованное, раскладка текстур по обмеру, ссылки на лист замысла и [библию города](../../../docs/khorinis-art-bible.md). Средние цвета — цели оригинала, это ограничители.

## 3. Концепт

1. Бриф — по [templates/brief-concept.txt](templates/brief-concept.txt). Задача и все ограничения — в первом сообщении. Блок `[FORM — keep exactly]` берётся из листа замысла, с числами. `[AVOID]` — дефолты из списка, касающиеся этой постройки. 2–3 варианта одного брифа.
2. Генерация — scout `codex-worker` в пустом каталоге после `git init`: `scout <slug> --file brief.md --repo <пусто>`, затем `wait`.
3. Каждый PNG заноси: `R import <ассет> <png> --role concept --prompt-file … --start … --end … --thread …`. Если `import` предупредил, что модели картинок смешались, остановись и реши с пользователем.
4. Сам отбери лучший, потом **Р1**:
   - `R sheet` — лист кадров оригинала;
   - `R pair <ассет> --id r1-… --stage concept --mode reference --a <лист оригинала> --b <концепт> --subject "…" [--measurements m.json]`;
   - у общего материала эталон — превью оригинальных текстур в раскладке концепта (собери лист сам, вне git) с `--a-label "превью оригинальных текстур" --private-a`: так считаются ряды и детали одного квадрата, а превью не попадает в git;
   - рецензент на `order-1` (раздел «Рецензент» ниже);
   - `R merge <ассет> --id r1-…`.
5. `REJECT` — правь бриф: пункт, который нарушили все варианты, формулируй числом и вноси в дефолты. Затем новый вызов.
6. `PASS` — покажи пользователю концепт с таблицей рецензента и полем «Отступления».
7. **В2:** `R decide <ассет> --gate concept --verdict approved|changes|rejected --quote "<слова пользователя>"`.
8. `R select <ассет> --key concept --file <ns>/<png> --reason "…"`.

## 4. Текстуры

1. `R check <ассет> --stage textures`.
2. Правки — в том же треде, по [templates/brief-edit.txt](templates/brief-edit.txt). В каждой правке повтори блок `[KEEP FROM THE CONCEPT]`. Раскладка (U/V в см, позиции главной детали в долях квадрата) — из обмера.
3. При первом дрейфе следующую правку делай от принятого концепта, а не от последней правки.
4. Каждый PNG — `R import … --role texture --texture NW_….TGA --parent <ns>/<концепт>.png`.
5. Отбери сам. Затем собери макет стены по обмеру: поля нужной ширины, видимая часть UV, линия пола. Сравни с оригиналом в паре через `R pair --stage textures` и запусти рецензента (**Р2**).
6. Выбор — `R select <ассет> --key NW_….TGA --file … --reason …`.

## 5. Обработка

`build.json` в папке ассета:

```json
{"set": "hut-4", "base": "original", "registry": "data/content/remaster/<ассет>.jsonl",
 "textures": {"NW_….TGA": {"axes": "uv", "colour": {"mode": "target"}},
              "NW_….TGA": {"axes": "u", "colour": {"mode": "match", "to": "NW_….TGA", "exclude_uv": [u0, v0, u1, v1]}}},
 "guard": {"luminance_ratio": [0.75, 1.25], "clipped_max": 0.01}}
```

Порядок:
- `B prepare <ассет>` — посмотри отчёт;
- `B build <ассет>` — карты, набор `content-staging/<set>`, реестр, лист альбедо;
- `python3 scripts/content/registry.py check data/content/remaster/<ассет>.jsonl --content-root ~/.local/share/gothic-ai/content-staging/<ассет>-sources --source-root ~/.local/share/gothic-ai/content-sources`.

Выход за ограничитель записывай в отчёт и не подгоняй. Если число требует одного, а вид другого, вынеси это пользователю фразой «число требует X, вид требует Y».

База — `original`: в наборе ассета только его текстуры. Для съёмки соедини его с наборами общих материалов, которые стоят на постройке: `B compose <набор> <часть>…`, например `B compose hut-4-city roofs-1 timber-1 hut-4`.

## 6. В игре

1. Сними `original` и набор одним сценарием через `gq`: `QUALITY_PILOT_TASK=gothic-ai/remaster-<ассет> scripts/content/capture-quality-pilot.sh --capture original|<набор> <каталог>`, сценарий — `scripts/content/<ассет>.bench`.
2. На каждый ракурс — `R pair <ассет> --id r3-<ракурс> --stage ingame --mode blind --a <оригинал> --b <наш> --subject "…"`.
3. Рецензент на `order-1` и `order-2` по отдельности, затем `R merge`.
4. `DISPUTED` и `REJECT` разбери сам. Повторно пускай рецензента только с новым пакетом, не спорь с ним.

## 7. Вердикт

1. Пользователю — пары оригинал | наше с таблицей рецензента: что прошло, что спорно, что не проверено.
2. **В3:** `R decide <ассет> --gate ingame …`. Каждое замечание пользователя — строкой в `docs/remaster-defaults.md`.
3. `R backup <ассет>` — rsync на dev-ubuntu и сверка SHA.
4. `R check <ассет> --stage done`.
5. Отчёт ассета — `reports/content/remaster/<ассет>/README.md`.
6. Включение в рабочий контент — только по слову пользователя (`--gate enable`).

## Рецензент

- Агент `remaster-reviewer` из `.claude/agents/`. Если тип агента не виден в сессии, запусти `general-purpose` с промптом: «Прочитай `.claude/agents/remaster-reviewer.md` и выполни его для пакета `<путь>`».
- Передавай только путь к пакету: ни переписки, ни того, какой кадр наш, ни ожиданий.
- Усилие `high`: на нём рецензент лучше кадрирует и меряет.
- Слепой паре — оба порядка, каждый в отдельном запуске.
- После смены модели Claude, модели картинок или промпта рецензента перепрогони калибровку: `data/content/remaster/reviewer-calibration.json`, отчёт в `reports/content/remaster/calibration/`.

## Что убрать из своих промптов

«Подумай внимательно», «перепроверь», «будь беспощаден», «не делай шаблонно», «только серьёзные замечания», заглавные и «ОБЯЗАТЕЛЬНО». Вместо них — конкретные числа и названные дефолты.
