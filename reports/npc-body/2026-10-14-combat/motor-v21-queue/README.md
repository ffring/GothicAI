# Queue21: strict serial queue probe — PASS

[Compact JSON](summary.json) сохраняет исходный 4/4 audit, независимо
сверенные native READ/BEGIN/END, IPC и action receipts на binary
`a83efa…777b7`, как в [Motor20](../motor-v20/README.md).
Все7 исходных audit raw SHA совпали с actual files, связаны23 закрытых
source/raw/service/resource файла. Шесть pure reader tests — PASS.

Собственные фактические результаты Queue21:

- **20 ready admissions — все body_heartbeat**. READ предшествует своему
  BEGIN/END, фактический cap —1 на deadline. Это не20 боевых команд.
- **7 fresh query chains**: READ → BEGIN → END → reply; response game_ms
  и sim_step равны фактическому post-tick состоянию. Запросы в
  started/unpaused realtime.
- **4 wait11 accepted и настоящих started**, rejected0. Проверены
  periodic anchors, masks и execution. Данные Queue19 с10 query/3 wait
  к этой пробе не приписаны.
- Clock journal:61 BEGIN,61 END,2106 READ, всего2228 непрерывных records.
  Arena завершилась обычным time_limit1500 мс, оба HP300 и scripted owner
  сохранены; probe exit0.

Каждый из **2107 реальных response packets Queue21** содержит точный
`worker_execution`: serial=true/caller_serial, worker_pool_created=false,
native_task_parallelism_cap=1. Manifest source flag native_serial_workers=true.
Это аттестация native task path, без утверждения, что весь процесс однопоточный.
Отдельные606 ответов CPUv2 не входят в этот счётчик и не дают gameplay credit.

Прежний [Queue17 FAIL](../motor-v17-queue/README.md) сохранён неизменным.
Новые queue/native доказательства не закрывают neural gate: Gate11 known
выполняется; load/fresh ещё не запускались. Веса, normalizer, masks,
freshness и quality thresholds сохраняются, stage8 остаётся IN_PROGRESS.

Четыре сервиса сохранили active/running/PID внутри before/after probe/test
интервалов; protected latency здесь не оценивалась. Полные журналы остаются
в `/home/kaifat/gothic-ai/body/data/stage8-motor-v21/pending-input-probe/`.
Extractor только читал закрытые данные, не запускал модели/тесты/симуляции/
сервисы и завершил source/raw hashing до load-фазы.
