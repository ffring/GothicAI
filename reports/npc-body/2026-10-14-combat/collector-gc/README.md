# Диагностика collector GC

Оба запуска закрыты и привязаны `diagnostic-binding.json` к wrapper,
report, resources и launcher. Они не выбирают веса, не открывают fresh
seeds и не дают quality credit. Старый [load FAIL](../gate-v6/load-failure.md)
остаётся FAIL; его пауза 169 мс не имеет собственных GC callbacks.

[Profile v1](profile-v1.json) — чистая CPU диагностика после подготовки
восьми GRU/MLP runtimes, без запуска движка. Три полных GC обхода заняли
42,733592 / 32,217121 / 29,945750 мс. После static freeze — 0,003376 /
0,001903 / 0,000241 мс; frozen object count184812. GC остаётся включён.
PyTorch intra-op1, interop12; веса прежние.

[Profile v2, компактная сводка](profile-v2-summary.json) — настоящий
диагностический четырёхактёрный runner с native/GRU/MLP, 20 секунд на
provider, без запросов защищённых сервисов и без поведенческого зачёта.
Наблюдались natural generation2 GC spans 73,338749 / 57,597238 /
49,018579 мс. Последний целиком входит в `DiagnosticEpisodeEnv.pull`
длительностью 54,128573 мс. В исходном profile — 686 GC spans и 68381
collector spans; здесь сохранены только верхние spans и их context.

Полный 7,97 MB profile остаётся на сервере; его SHA взят из закрытого
binding, сам большой файл не скачивался. Summary не превращает измерения
отдельного запуска в причинное объяснение gate-v6. Native source не менялся,
новый native proof для этих диагностик не заявляется.

Оригиналы:
`~/gothic-ai/body/data/stage8-collector-gc-profile-v1/` и
`~/gothic-ai/body/data/stage8-collector-gc-profile-v2/` на dev-ubuntu.
При сохранении этой сводки модели, тесты, runner и сервисные действия
не запускались.

Дополнительно [ownership/bootstrap proof](ownership-bootstrap.json)
связывает startup375 private Python3.12.13, изолированное сравнение с
system3.14.4 и actual activation375→0/freeze8891/close0 с исходными SHA.
Private GC enabled/thresholds700/10/10 сохранены; unknown nonzero ownership
не снимается. Pure tests28 прошли; старый [gate-v7 startup FAIL](../gate-v7/README.md)
с zero quality cases сохранён. Эти проверки не устанавливают причину
прежнего gate-v6 gap169 мс и не дают новому gate модельного зачёта.
