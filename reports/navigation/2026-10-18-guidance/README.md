# Направление дороги и повороты учителя — 2026-10-18

Маршрут начинается с ближайшего известного узла ([knownroads.cpp:195](../../../OpenGothic/common/game/knownroads.cpp#L195)); `readPath` выбирает первый его узел дальше 1 м от NPC, иначе последний (пустой путь — цель), в том числе позади NPC ([knownroadsbench.cpp:397](../../../OpenGothic/common/game/knownroadsbench.cpp#L397)). `navdemos` записывает нормированное направление в системе тела и прямое 3D-расстояние ([navdemos.cpp:248](../../../OpenGothic/common/navdemos.cpp#L248)).

Ryzen, `nav_demos.iter_chunks`, исходный split `nav-bc/cache/index.json`: все удачные эпизоды обучающих пар; DAgger — все поездки, включая неудачные (30 train / 31 validation / 1 контроль). Угол — между `roads.next_direction` и `teacher_hint.next_corner_cm − position_cm`, переведённым через `body_yaw_deg` в систему тела.

| Метрика | Учитель, train | DAgger 1, все поездки |
|---|---:|---:|
| Эпизоды / решения | 125 / 80 372 | 62 / 132 929 |
| Медиана 3D-угла | 79,12° | 105,03° |
| Угол >45° / >90° | 65,86% / 45,62% | 79,65% / 57,67% |
| Скачки 3D >90° / соседние решения | 1 111 / 80 247 (1,384%) | 265 / 132 867 (0,199%) |
| Из них в последних 5 м | 96 (8,64%) | 10 (3,77%) |
| Доля соседних решений в последних 5 м | 11,44% | 5,54% |
| Совпадение turn: дорога / угол сетки | 28,31% / 50,93% | 40,24% / 87,28% |
| Средняя полнота трёх классов: дорога / сетка | 38,02% / 52,92% | 32,28% / 76,83% |
| Совпадение на метках ±1: дорога / сетка | 44,24% / 54,21% | 41,57% / 89,01% |

Правило: `α=atan2(x,z)`, turn=−1 при α>10°, +1 при α<−10°, иначе 0; эталон — знак `keys.turn`. Нулевых векторов нет. Соседи — внутри эпизода, шаг 100 мс; последние 5 м — `roads.goal_distance_cm ≤ 500` в позднем решении. Компенсация yaw почти не меняет горизонтальные скачки: 1 137→1 134 / 273→271.

Оговорки: deadband учителя 4,5°/9°, climbing запрещает поворот ([navkeyteacher.cpp:45](../../../OpenGothic/common/navkeyteacher.cpp#L45)); при `waynet_fallback` записывается hint другого mesh-запроса, keys остаются от резервного пути ([navdemos.cpp:253](../../../OpenGothic/common/navdemos.cpp#L253)). Записанная позиция физическая, учитель использует `n.position()` ([navdemos.cpp:221](../../../OpenGothic/common/navdemos.cpp#L221)).

Повтор: [скрипт](../../../scripts/nav_guidance.py), замок пачками, чтение по 32 решения; RSS **77,3 МиБ**, [тесты](../../../scripts/tests/test_nav_guidance.py) **13 passed**. Числа/матрицы: `/home/kaifaty/gothic-ai/nav-guidance/metrics.json`.

```sh
P=/home/kaifaty/gothic-ai
"$P/nav-bc/.venv/bin/python" -I "$P/nav-guidance/source/scripts/nav_guidance.py" \
  --teacher-source "$P/nav-demos" --dagger-source "$P/nav-dagger/round-1/dagger" \
  --split-index "$P/nav-bc/cache/index.json" --output "$P/nav-guidance/metrics.json" --batch-size 32
```

Несогласованность направлений подтверждена: в DAgger угол >90° на 57,67% шагов, правило сетки существенно точнее. Скачки не сосредоточены у цели: их доля в последних 5 м ниже доли решений в этой зоне. Причину кружения и невозможность обучения по остальным входам и истории GRU эти измерения не доказывают.
