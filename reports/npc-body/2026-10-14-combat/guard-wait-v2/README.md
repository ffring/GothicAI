# Guard wait v2: paced load-loop

**75 passed, 0 failures, 0 errors, 0 skipped** на dev-ubuntu. Проверены те же три файла: `test_body_runtime_delivery.py`, `test_body_policy_diagnostics.py`, `test_body_policy_load.py`.

При pending native guard load-loop теперь использует обычный `env.step()`: paced heartbeat, native deadline, events и числовой drain. Immediate pull сохранён для здоровых stale/masked refresh. Новый loop test проверяет два таких шага, память по locked periodic row, фактический handoff/terminal, сохранённый rejected denominator и отсутствие новых команд/accepted credit.

Новый immutable snapshot: `~/gothic-ai/body/data/stage8-guard-wait-v2`. До запуска сохранён manifest 32 файлов; все 15 фактически импортированных project modules совпали с SHA. Лимиты и общий lock: CPU 200%, Memory 6 GiB, Tasks 128, Nice 10, GPU visibility empty, `body-simulation.lock`. Pytest — 0,18 с; полный процесс — 0,34 с, max RSS 56 144 KiB. Состояния/PID четырёх сервисов и GPU memory 19 011 MiB не изменились.

Это protocol receipt tests: симуляции и реальные model forwards не выполнялись, quality/selection credit отсутствует. Native grace, watchdog, action masks, наблюдения и model-control thresholds не изменены. Настоящая арена остаётся отдельной проверкой. [v1: 74 PASS](../guard-wait-v1/README.md) сохранён как самостоятельный snapshot.

[bindings.json](bindings.json) содержит manifest, actual module/resource/service receipts и SHA JUnit/логов. Полные receipts остаются на dev-ubuntu; raw journals и веса сюда не копировались.

- Snapshot manifest SHA256: `7e5d18d8e546a71303d984b44fa2095a0c939cfe5d4003c3fa1af7fcf92d8aca`.
- JUnit SHA256: `58ab303bfd4d842f959cea015eba50d3b612f883810433b9bc50dbad6d1d9de6`.
- Compact bindings SHA256: `458937638a3b02786c0e279ec92237d43873370dc13ad85027040a352403cb02`.
