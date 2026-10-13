# Проверка реализации этапа 2

13 октября 2026 года. [Измеренный результат](headless-report.md), [JSON](headless-report.json), [runtime и запуск](../../../docs/npc-body-headless-runner.md).

## Финальная проверка

- Обычный `Gothic2Notr` и `gothic-body-runner` собраны одним Linux toolchain из одной версии engine library.
- Полная серверная серия `stage2-v3` завершилась с exit 0 за 15 мин 35 с. Основное wall время занял отдельный rendered контроль под CPU lavapipe/Xvfb; headless процессы работали без него. Одновременно выполнялась одна симуляция.
- Три full rendered/headless сравнения, две проверки расписания и строгий archived branch replay прошли. Проверялись native queue/consume, animation hooks/guards, hit/block/damage, HP, movement и, для full profile, per-tick root/collision telemetry. [Допуски](../../../evals/npc-body/headless-parity-v1.json) не расширялись.
- Sight/occlusion, sound lifecycle, native perception callbacks, 100 последовательных resets и IPC probes прошли. Manifest содержит hashes assets/save/source/binaries и проверку отсутствия graphics/audio device.
- Лёгкие parser/validator tests: `server/.venv/bin/pytest scripts/tests/test_body_baseline_report.py scripts/tests/test_body_headless_parity.py scripts/tests/test_body_headless_suite.py` — **41 passed**. Bash syntax, Python compilation и `git diff --check` прошли. Pytest выполнялся после завершения симуляции; модели и игра на Mac не запускались.
- После серии game/runner процессов не осталось; `gothic-voxtral` и `gothic-voxcpm` остались active. Их latency не измерялась.

## Исправления, найденные реальными прогонами

Первый полный прогон выявил расхождение вариантов блока: общий C RNG расходовался звуками в зависимости от wall clock. A2/A3 имеют различное root motion, поэтому одинакового script seed недостаточно для одинакового боя. Добавлены opt-in benchmark isolated RNG и строгий replay только записанных clip-веток; native AI, урон и перемещение остаются вычисляемыми. [Метод проверки](../../../docs/npc-body-headless-runner.md#воспроизводимость-вариантов-анимации) явно отделяет историческое сравнение от full rendered/headless parity.

Повторная загрузка выявила неинициализированные оси `PlayerControl`, которые могли сдвинуть наблюдателя и setup арены. Оси имеют нулевую инициализацию; reset пересоздаёт controller и очищает input/focus.

Прогон из 100 resets выявил утечку native NPC physics body/capsule: handle снимал регистрацию объекта, но не освобождал его. Деструктор владельца теперь освобождает body и принадлежащую ему capsule после unregister. Финальная warmed RSS: 629,05 → 629,48 MiB при неизменном допустимом росте 64 MiB; FD/thread span 0.

Отдельные probes выявили два транспортных дефекта: неполная строка stdin останавливала realtime, а несколько строк в одном write могли ждать новую готовность fd после read-ahead. Runner использует неблокирующее чтение и собственный JSONL буфер; обе проверки прошли на настоящем executable. Native no-hit ray sentinel теперь сериализуется как `null`, поэтому sensory trace остаётся валидным JSON.

Неудачные промежуточные runs сохранены на сервере и не считаются успешными evidence. Финальный gate относится к native human 1H melee и проверенным sensory случаям; policy API, GRU и обучение остаются следующими этапами.
