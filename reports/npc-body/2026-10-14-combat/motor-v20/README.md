# Motor20: serial native proof — PASS

Runner SHA:
`a83efa28236ff9917b7e7d60a4a508de6feedd31d8d9ff4bc69adeb20ab777b7`.
Full native parity17/29/43 — matched, issues[], differences0 у каждого seed.
Native события, физика, pose и timing совпали с архивом. Body API —37/37,
complete, full_stage4_evidence=true, issues[]. Resolver —12 наборов,
2534 утверждения, passed=true.

- Realtime: четыре NPC/animation streams; arena29,951185 steps/s и
  generic31,740301 в объявленных границах27–33. Pre-start/terminal/watchdog
  и12 wall-clearance случаев прошли.
- Visibility boundary: 8/8 checks, replay64 команд и физический prefix1801
  строк до7567 мс. Настоящие hit70/stumble360 мс сохранены; policy decisions
  при потере видимости запрещены, раннего release нет, четыре owner сохранены.
- Compact reply: 10/10 checks, полный held/armed RNG, неизменность numeric
  masks/pose/HP/clocks и полные terminal данные.

Worker profile — фактическое native исполнение на caller:
`serial=true`, `mode=caller_serial`, `worker_pool_created=false`,
`native_task_parallelism_cap=1`. Source opt-in `native_serial_workers=true`
связан с manifest каждой из пяти native-фаз. Это не утверждение
о whole-process Thread1: остальные потоки процесса здесь не запрещены и
не подсчитаны как часть этой аттестации.

[Compact JSON](native-summary.json) связывает39 закрытых source/report/manifest,
resource/log/service файлов и113 raw SHA исходного API отчёта. Он также
сохраняет5 отдельных CPU diagnostic bindings: независимо прочитаны **606
ответов NativeCPUv2**, каждый имеет точный тот же worker profile и binary.
Эти606 — только число ответов отдельной пробы, а не число Motor20 сценариев.

[NativeCPUv2 comparison](../native-cpu-profile-v2/README.md) измерила whole-service
CPU−23,31% при прежнем бюджете200%/6GiB, но native ticks стали медленнее:
max31,107499 мс против15,849250 мс. Whole-service включает setup/collection.
Это наблюдение не доказывает причину или исправление прежнего Gate10 отказа,
не получает neural/model/fresh credit.

[Queue21](../motor-v21-queue/README.md) на этом binary прошла строгую очередь.
Четыре защищённых сервиса сохранили active/running/PID внутри before/after
пяти native-фаз. Нового protected latency сравнения здесь нет.

Gate11 заморожен: preregistration PASS26,768 с, targeted runtime101/final94
PASS; known3/24/6 выполняется, load/fresh не начались. Все прежние FAIL,
веса, normalizer и quality thresholds сохранены. Этап8 — IN_PROGRESS,
общего боевого PASS нет.

Полные данные остаются в `stage8-motor-v20` и `stage8-native-cpu-profile-v2`
на dev-ubuntu. Извлечение только read-only; модели, тесты, симуляции и сервисы
не запускались, raw/код не менялись, большие журналы/веса/binary не скачивались.
Все source/raw hashes вычислены до load-фазы.
