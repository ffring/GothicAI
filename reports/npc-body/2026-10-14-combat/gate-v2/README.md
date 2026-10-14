# Gate-v2: инфраструктурный отказ подготовки reaction

Источник: `/home/kaifat/gothic-ai/body/data/stage8-final-gate-v2`.
Сводка прочитана по SSH без запуска моделей, тестов или симуляции.
Документы gate и исходные серверные артефакты не изменялись.

| Фаза | Сохранённый статус | Завершённых case records | Wall time | Exit status |
| --- | --- | ---: | ---: | ---: |
| Shadow | collected | 3 | 1:09,29 | 0 |
| Обычный realtime | collected | 24 | 2:23,04 | 0 |
| Realtime reaction | infrastructure_failure | 0 | 0:05,76 | 2 |

Первая reaction-подготовка остановилась с ошибкой `'rng_state'` на
`block-reaction-223-0-native`. В исходных body status ответах на запросы
3/5/8 поле `purity_state.rng_state` есть. В ответе 21 оно отсутствует у обоих
акторов; значения control generation сохраняются. В manifest включён
`compact_replies=true`. Это отказ формы transport payload до сохранения
preparation/activation proof; он не показывает изменение RNG или результат
защитной реакции. Завершённого episode record, `preparation.json` и
`activation.json` для этого случая нет.

Launcher сохранил `evaluation v2 failed: 'rng_state'`, unit exit 2 и runtime
5,770 с. Каталоги и launcher logs load/screening/screen/fresh-screening
отсутствуют: нагрузка и fresh screening этого запуска не начинались.

Checkpoint SHA в трёх фазовых manifest и сохранённых case records совпадают
с прежней selection: GRU `e9a605b4ae15e30ad7e2dd632c10c882c56f98b19f40cace3d8b2343a693b3eb`,
MLP `d17752fd0fc765f2ab841eec107f0a8ccbf77fd5db83ceabbee866e33a1073c9`,
normalizer в reaction manifest и сохранённых shadow/realtime cases —
`c6ee3acdea6a065c04f6de2e88eb4fe7af189d50aee3c699f925ffadbd7438fe`.
Привязка сверена с [фиксированной selection](../defence-v2/selection.json).
Веса и бинарники не скачивались, их bytes эта сводка повторно не хешировала.

[JSON evidence](preparation-failure.json) содержит 53 SHA привязки настоящих
серверных файлов: фазовые suite/manifest, episode metadata, resources/logs
и raw журналы незавершённой reaction-подготовки. Сами большие raw журналы
не скачивались; переданы только SHA и ограниченные фрагменты формы ответа.
Статус `collected` не означает quality PASS: во всех suite
`quality_gate_evaluated=false`. Этап 8 остаётся **IN_PROGRESS**, зачёта
fresh screening или боевого gate нет.
