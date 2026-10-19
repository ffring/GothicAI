# P30: свежие записи Ryzen и повторы на Mac

19 октября 2026 года. **Остановлено по стоп-правилу: два последовательных повтора на Mac
не прошли строгую эквивалентность.** Записаны все восемь новых дуэлей; сняты две, открытая
и у стены. До требуемых четырёх дублей (2 + 2) съёмка не продолжалась. Детерминизм движка
не исправлялся, допуски сравнения не менялись.

Оба движка собраны из `main` на начало работы: `9e4dcae00272f724b01785d3383c7e875afc71e6`.
Изменений `OpenGothic/` в этой ветке нет. Ryzen: Jolt `x86-64-v3`,
`CROSS_PLATFORM_DETERMINISTIC=ON`; Mac: arm64, Apple M1 Pro, Metal. В обоих прогонах Jolt
работает с `OPENGOTHIC_PHYSICS_THREADS=0`, изолированы RNG анимаций и скриптов.

## Восемь записей

Ryzen, `~/gothic-ai/replay-mac/recordings/{open,near_wall}/worker-00/`.
Коллектор: T=0, `--keep-journals --start-substeps 1 --command-ttl-ms 100`, один worker.
P30 читался из `~/gothic-ai/body/models/p30/best.pt`; сервис и веса не менялись.
Контрольный save — `~/gothic-ai/body-baseline/runs/melee-reference-stage1-v4/run/save_slot_1.sav`.
Его копия на Mac совпала по SHA-256:
`c2852f4c009f23225e1c9c70f564ca36b4c12e8220736d1c72a1dbfce8d3372f`.
Арена использует `BDT_1003_BANDIT_M` против родного ИИ `PAL_255_RITTER`, стандартный
нормализованный профиль арены с 300 HP и кожаной бронёй.

| Seed | Геометрия | Сторона | Дистанция, см | Финал | HP P30 | Время боя, мс |
|---|---|---:|---:|---|---:|---:|
| 100000 | open | 0 | 250 | opponent_down | 300 | 12733 |
| 100001 | open | 1 | 250 | opponent_down | 300 | 10033 |
| 100002 | open | 0 | 300 | opponent_down | 300 | 10033 |
| 100003 | open | 1 | 300 | opponent_down | 294 | 10167 |
| 100004 | near_wall | 0 | 250 | opponent_down | 300 | 9833 |
| 100005 | near_wall | 1 | 250 | opponent_down | 300 | 12500 |
| 100006 | near_wall | 0 | 300 | opponent_down | 300 | 7233 |
| 100007 | near_wall | 1 | 300 | opponent_down | 300 | 8633 |

Две отдельные заявки `gq batch`: `e06b5d442988` (open) и `071ab475143d` (near_wall),
по 3 потока, 6 ГБ, лимит 10 минут. Обе завершились с кодом 0; собственно сбор занял
9,469 и 7,205 с. Победы 8/8, fallback 0, таймауты 0, расхождения маски 0;
решений 74 + 64. Сборка прошла через штатный `body-api-remote.sh`, скопированный с
`REMOTE=gothic-ai/replay-mac` и `ROOT` этого worktree; сборочная заявка — `4d189a436931`.
Встроенный resolver: 15 случаев, 2828 утверждений, все прошли.

Фактические команды очереди:

```bash
scripts/gq run --class batch --threads 3 --mem 6G --max 10m --name replay-mac-open -- bash /home/kaifaty/gothic-ai/replay-mac/collect.sh open
scripts/gq run --class batch --threads 3 --mem 6G --max 10m --name replay-mac-near-wall -- bash /home/kaifaty/gothic-ai/replay-mac/collect.sh near_wall
```

`collect.sh` выполнял следующий вызов с `geometry=open, seed=100000`, затем в отдельной
заявке с `geometry=near_wall, seed=100004`; все пути абсолютные:

```bash
PYTHONPATH=/home/kaifaty/gothic-ai/replay-mac/source/body-service/src \
OPENGOTHIC_PHYSICS_THREADS=0 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
/home/kaifaty/gothic-ai/replay-mac/.venv/bin/python \
  /home/kaifaty/gothic-ai/replay-mac/source/scripts/body_rl_collect.py \
  --runner /home/kaifaty/gothic-ai/replay-mac/build/opengothic/gothic-body-runner \
  --assets /home/kaifaty/gothic-ai/body-baseline/assets \
  --save /home/kaifaty/gothic-ai/body-baseline/runs/melee-reference-stage1-v4/run/save_slot_1.sav \
  --reference /home/kaifaty/gothic-ai/body-baseline/runs/melee-reference-stage1-v4 \
  --scenario /home/kaifaty/gothic-ai/replay-mac/source/scripts/bench/body-arena.bench \
  --checkpoint /home/kaifaty/gothic-ai/body/models/p30/best.pt \
  --output /home/kaifaty/gothic-ai/replay-mac/recordings/$geometry \
  --workers 1 --episodes 4 --seed-start "$seed" --geometry "$geometry" \
  --temperature 0 --keep-journals --start-substeps 1 --command-ttl-ms 100
```

## Два снятых повтора

Копии `duel.mp4` и `duel.wav` лежат в:

- `~/.local/share/gothic-ai/explain/combat-ai/footage/p30-100000/`;
- `~/.local/share/gothic-ai/explain/combat-ai/footage/p30-100004/`.

Это снятые дубли с выявленным численным расхождением, а не подтверждённые тик-в-тик повторы.
`ffprobe` подтвердил H.264, 1920×1080, 30 кадров/с и отдельный PCM s16le WAV,
48000 Гц, stereo, 16 бит. Музыка и фоновые звуки выключены; звук боя записан.

| Seed | Кадры | MP4, с | Битрейт видео, Мбит/с | WAV, с | Пик PCM | RMS, dBFS |
|---|---:|---:|---:|---:|---:|---:|
| 100000 | 473 | 15.766667 | 24.388465 | 15.766000 | 8655 | −33.635 |
| 100004 | 386 | 12.866667 | 24.481887 | 12.866000 | 27800 | −24.609 |

В обоих дублях игра и `render` завершились с кодом 0. Отклонённых команд и несовпадений
`observation_seq` — 0. Кадры обоих дублей просмотрены: оба участника видны, изображение цветное.

Журналы и сцены на Mac: `.bench/replay-mac/{recordings,film}/`, результаты сравнения —
`.bench/replay-mac/equivalence-{100000,100004}.json`. Сырые записи также сохранены на Ryzen.

Подготовка: `scripts/body_replay_film.py prepare NAME=EPISODE_DIR --output .bench/replay-mac/film`.
Перед каждым запуском выполнялся `LC_ALL=C pgrep -fl Gothic2Notr || echo "no game"`;
дополнительно `pgrep -x Gothic2Notr` отделял игру от строк команд Codex/сборки.
Использованы те же игровые ассеты и HD-текстуры, что у Mac-стенда.

Фактический вызов съёмки из корня worktree, отдельно для `seed=100000` и `seed=100004`:

```bash
SOUND=1 MUSIC=0 WORLD_SOUNDS=0 \
OPENGOTHIC_FOOTIK_WATCHED_ONLY=1 OPENGOTHIC_ANIM_SKIP_FAR=1 OPENGOTHIC_FREEZE_WORLD=1 \
python3 scripts/body_replay_film.py render .bench/replay-mac/film/p30-$seed \
  --game OpenGothic/build/opengothic/Gothic2Notr \
  --assets '/Volumes/DevSSD/Dev/games/Gothic II' \
  --save .bench/replay-mac/save_slot_1.sav --size 1920x1080 \
  --textures /Users/kaifat/.local/share/gothic-ai/hd-textures/textures
```

Начальная настройка `vidResIndex=2` вызвала один подготовительный SIGABRT Metal: снимки
повторно делили текущий viewport до нулевой высоты. Теперь `render` узнаёт масштаб экрана
через AppKit/`osascript`, переводит выходные пиксели в точки окна и задаёт `vidResIndex=0`.
На этом Mac масштаб 2: окно 960×540 points даёт стабильные кадры 1920×1080.
Изменений движка для этого не потребовалось. Неудачный подготовительный запуск сохранён
в `.bench/replay-mac/setup-attempt-1/`; он не считался проверкой эквивалентности.

## Эквивалентность и остановка

Сравнение выполнено неизменённым `scripts/body_schedule_equivalence.py`, как в round3-film.
«Команды» ниже — все события accepted/started/completed/cancelled основного участника;
номера строк начинаются с нуля. Reset в обоих дублях имеет `sim_step=16`.

| Seed | События команд Mac / Ryzen | Принято | Наблюдения Mac / Ryzen | Первый разный индекс | sim_step / шаг после reset | Финальный снимок |
|---|---:|---:|---:|---:|---:|---|
| 100000 | 66 / 66, все равны | 22 / 22 | 585 / 585 | 12 | 23 / 7 | различается |
| 100004 | 51 / 51, все равны | 17 / 17 | 449 / 449 | 165 | 122 / 106 | различается |

Обе команды сравнения вернули **код 2**, `identical=0/1`, `identical_commands=1/1`,
`same_outcome=1/1`, `diverged_without_fault=1`. Пропущенных polls, опоздавших и
отклонённых команд — 0. Это два последовательных расхождения при рабочем захвате.
После второго новые игры, съёмки и поиски причины в движке не запускались.

| Первая разная величина | Mac | Ryzen |
|---|---:|---:|
| 100000, `t_game_ms=16527`, `self.yaw_rad` | −2.9702093601226807 | −2.970219135284424 |
| 100000, `target.fight_distance_cm` | 199.5321044921875 | 199.53176879882812 |
| 100000, `target.position_world_cm[2]` | −12479.1416015625 | −12479.1396484375 |
| 100004, `t_game_ms=19827`, `self.position_world_cm[2]` | −13772.7841796875 | −13772.7861328125 |
| 100004, `self.linear_velocity_cm_s[2]` | −13.523910522460938 | −13.55350399017334 |
| 100004, `target.relative_position_cm[2]` | −173.6787109375 | −173.6767578125 |

В обоих финалах совпали `opponent_down`, HP 300/0 и время боя: 12733 мс (100000),
9833 мс (100004). Не совпали позиции и повороты; у стены также нормаль geometry proof.
Примеры финальных различий:

| Поле финала | Mac | Ryzen |
|---|---:|---:|
| 100000, `actors[0].pos[2]` | −12410.7354 | −12410.7334 |
| 100000, `actors[1].pos[0]` | 65028.1602 | 65028.2109 |
| 100000, `actors[0].yaw_deg` | 179.625595 | 179.624435 |
| 100004, `actors[0].pos[2]` | −13925.2627 | −13925.2637 |
| 100004, `actors[1].pos[0]` | 65234.0781 | 65234.0898 |
| 100004, `setup.geometry_proof.capsule_block_normal[0]` | 0.612816393 | 0.612816453 |

Фактические команды сравнения:

```bash
python3 scripts/body_schedule_equivalence.py \
  --realtime .bench/replay-mac/film/p30-100000 \
  --lockstep .bench/replay-mac/recordings/open/worker-00/ep-native_duel-100000-0-250 \
  --output .bench/replay-mac/equivalence-100000.json
python3 scripts/body_schedule_equivalence.py \
  --realtime .bench/replay-mac/film/p30-100004 \
  --lockstep .bench/replay-mac/recordings/near_wall/worker-00/ep-native_duel-100004-0-250 \
  --output .bench/replay-mac/equivalence-100004.json
```

## Проверка кода

```bash
cd body-service && uv run pytest -q ../scripts/tests/test_body_replay_film.py ../scripts/tests/test_body_schedule_equivalence.py
```

**12 passed in 0.48s**, код 0. Проверены окружение macOS на дисплеях 1×/2×,
звук, обязательный WAV и сохранённый Linux-путь Xvfb/lavapipe.
