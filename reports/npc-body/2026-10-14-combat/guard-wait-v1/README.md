# Guard wait: целевые проверки на dev-ubuntu

**74 passed, 0 failures, 0 errors, 0 skipped.** Проверены три файла:

- `scripts/tests/test_body_runtime_delivery.py`;
- `scripts/tests/test_body_policy_diagnostics.py`;
- `scripts/tests/test_body_policy_load.py`.

Проверки покрывают ожидание фактического native guard после подтверждённого `target_invalid`, сохранение отказа и числовой истории, восстановление только по новому periodic-наблюдению, native handoff без success credit, ограничение ожидания и отказ при несовпадении контекста. Симуляции и реальные model forwards не выполнялись; результаты не дают quality или selection credit. Проверка handler в настоящей арене остаётся отдельным этапом.

Прогон выполнен из нового snapshot `~/gothic-ai/body/data/stage8-guard-wait-v1`, под общим `body-simulation.lock`. До запуска сохранён SHA manifest 32 файлов; все 15 фактически импортированных project modules совпали с snapshot. Frozen Gate11 не изменён.

Фактические лимиты: CPU 200%, Memory 6 GiB, Tasks 128, Nice 10, `CUDA_VISIBLE_DEVICES=''`. Pytest занял 0,18 с; полный процесс — 0,33 с, max RSS 55 920 KiB. Состояния и PID четырёх защищённых сервисов одинаковы до и после; GPU memory — 19 011 MiB в обоих снимках.

[bindings.json](bindings.json) содержит исходный manifest, actual module bindings, cgroup/resource/service receipts и SHA JUnit/логов. Полные исходные receipts сохранены на dev-ubuntu по указанным в JSON путям; raw game journals и веса сюда не копировались.

- Snapshot manifest SHA256: `cd19e5a89b2feda186163ca62d722b1b064cef61b959ecc6652a7283e080f1f5`.
- JUnit SHA256: `2fdaf2dc1dee1f7eae90d1ade1d4ace1d1613b0bdd548b754fce2d1e06defdfe`.
- Compact bindings SHA256: `8dfa87cd4efd5eb6829e1ba415d82ffa2f1b6ad9f9a4317f91e0ccc92811e76c`.

После отдельного read-only аудита найдено, что load-loop при guard hold обходил paced `env.step()` и мог многократно делать пустой drain. Исправление и дополнительный loop test проверены в отдельном [guard-wait-v2](../guard-wait-v2/README.md). Исходный v1 snapshot и его результат **74 PASS** сохранены.
