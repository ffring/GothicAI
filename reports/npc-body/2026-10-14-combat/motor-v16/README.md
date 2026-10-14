# Motor16: native-механика — PASS

Runner SHA:
`a1695dfb0edafdbc793075c84c3aae80b3ec3de61d9a7446dfe68ccff92bc0c2`.
Все перечисленные native-проверки завершены на dev-ubuntu. Они не дают
GRU/MLP quality credit и не закрывают этап8: [Queue17](../motor-v17-queue/README.md)
на том же binary обнаружила настоящий дефект порядка post-tick query.

[Компактный JSON](native-summary.json) связывает 39 исходных report/manifest,
code/archive, resource/log и service файлов с SHA. Сохранены 113 raw SHA
привязок исходного независимого Body API отчёта и journal hashes двух probes;
большие журналы, веса и binary остаются на сервере.

- Full native parity17/29/43 — matched, issues[], differences0 у всех seeds.
  Упорядоченные события, физика, pose и timing совпали с архивом. Длительности
  reference/candidate — 14133/15600/23567 игровых мс.
- Realtime — четыре NPC и четыре раздельных animation streams. Arena rate
  29,972532 steps/s, generic30,982050 при объявленном интервале27–33.
  Выполнены pre-start/terminal/watchdog проверки и 12 размещений у стены.
- Visibility boundary — 8/8 checks: replay64 команд, физический prefix1801
  строк до7567 мс, настоящий урон70 и stumble360 мс сохранены; раннего release
  нет, policy decisions при потере видимости запрещены, четыре owner сохранены.
- Compact reply — 10/10 checks, включая полный RNG held/armed preparation,
  неизменность numeric masks/pose/HP/clocks и полные terminal данные.
- Body API — 37/37, status=complete, full_stage4_evidence=true, issues[].
- Resolver — 12 наборов, 2534 утверждения, passed=true.

Четыре защищённых сервиса сохранили active/running и те же PID между before/after
каждой из пяти закрытых native-фаз; это проверка этих интервалов. Сравнение
latency защищённых сервисов относится к отдельному load gate.
Extractor только прочитал закрытые JSON/журналы и metadata: моделей, тестов,
симуляций и сервисов не запускал, исходные файлы не переписывал.

Полные исходные данные:
`/home/kaifat/gothic-ai/body/data/stage8-motor-v16/`.
