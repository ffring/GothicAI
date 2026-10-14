# Motor18: закрытый native proof — PASS

Runner SHA:
`681a1704364e07836f839c3e4382b16ae43b28ae3bf75fb46f7884e2b10a1359`.
Full native parity17/29/43 — matched, issues[], differences0 у каждого seed.
Упорядоченные события, физика, pose и timing совпали с сохранённым архивом.
Reference/candidate длительности — 14133/15600/23567 игровых мс.

- Realtime: четыре NPC и четыре отдельных animation streams; arena29,963489
  steps/s и generic31,698135 в объявленных границах27–33. Pre-start,
  watchdog/terminal и 12 wall-clearance случаев прошли.
- Visibility boundary: 8/8 checks, replay64 команд и физический prefix1801
  строк до7567 мс. Настоящие hit70 и stumble360 мс сохранены; при потере
  видимости решения запрещены, раннего release нет, четыре owner сохранены.
- Compact reply: 10/10 checks, включая полный held/armed RNG, неизменность
  numeric masks/pose/HP/clocks и полные terminal данные.
- Body API: 37/37, complete, full_stage4_evidence=true, issues[].
- Resolver: 12 наборов, 2534 утверждения, passed=true.

[Compact JSON](native-summary.json) содержит 39 SHA-привязок закрытых
source/report/manifest, resource/log и service файлов. В нём сохранены 113 raw
hashes исходного независимого API отчёта и journal hashes probes. Полные данные
остаются в `/home/kaifat/gothic-ai/body/data/stage8-motor-v18/`.
Большие журналы, веса и binary не копировались.

Четыре защищённых сервиса active/running, PID совпадают между before/after
каждой из пяти закрытых фаз. Новое protected latency сравнение здесь
не выполнялось. Отдельная [Queue19](../motor-v19-queue/README.md) на том же
бинарнике прошла строгий post-tick probe; прежняя
[Queue17 FAIL](../motor-v17-queue/README.md) сохраняется без изменений.

Это native-механика и транспорт. GRU/MLP боевой quality credit не получен,
этап8 остаётся IN_PROGRESS. Gate10 ожидает preregistration и known;
fresh screening не запускался. Веса, normalizer и quality thresholds прежние.
Extractor запускал только read-only расчёт компактных исходных привязок,
без моделей, тестов, симуляций или сервисов. Хэширование закончено до load.
