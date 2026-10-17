# Проверка reader: Guard v1

На dev-ubuntu прошли **164 теста** трёх изменённых модулей: final evaluation, combat report и final runtime report. Прогон выполнен из отдельного immutable snapshot с общим simulation lock, CPUQuota=200%, MemoryMax=6G, Nice=10, TasksMax=128 и пустым CUDA_VISIBLE_DEVICES.

Время процесса — 0.40 с; CPU user/system — 0.37/0.02 с; maximum RSS — 44,480 KiB. Все четыре защищённых сервиса сохранили active/running и те же PID. Snapshot manifest связывает 121 исходный файл и конфигурации; исполнявшиеся изменённые reader/test-файлы сохранены в `code/`.

Отдельная read-only проверка тем же замороженным producer приняла реальные неизменённые данные Gate11 как `known_pass_eight_cohort_visibility_load_failure`. Результат в [prior-gate11.json](prior-gate11.json) сохраняет Native4 + GRU4, три настоящие потери управления, невалидные сервисные окна и partial MLP0. Этот результат подтверждает корректное сохранение провала и не даёт зачёт load или этапу 8.

Модели, игровые симуляции и новые service workloads не запускались. Сырые engine journals и веса остались на dev-ubuntu; здесь только результаты, хеши, исходники проверок, JUnit и ресурсные/service receipts.
