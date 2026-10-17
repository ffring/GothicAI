# Gate v7: startup failure до quality cases

Shadow завершился **infrastructure failure** за 5,089 с (process wall,
сообщённый launcher): **0 cases**. Точная причина:
`static GC scope requires enabled dynamic GC and no preexisting freeze ownership`.
Это отказ подготовки, без оценки поведения policy. Load и fresh screening
не начинались; общего боевого PASS нет.

[Компактный JSON](startup-failure.json) содержит GC/startup evidence,
manifest/source/checkpoint hashes, resources/log fragments и 21 source binding.
Suite SHA:
`033e3e330f1c51f67f4ac10faf20ed667b176e26808c8619c782a1c83f278a9e`.
Оригинальный failed run `stage8-final-gate-v7` остаётся неизменным.

[Ownership и actual activation](../collector-gc/ownership-bootstrap.json)
показывают freeze_count375 в private UV Python3.12.13 до imports, после
imports и после warm2 всех восьми runtimes, при GC enabled/thresholds700/10/10.
Зарегистрированных GC mutator calls нет. Отдельный повтор `-I -S` подтвердил
private375 против system Python3.14.4 count0. У system другой default
thresholds2000/10/0; ни один interpreter в сравнении не изменял GC state.

Private executable:
`/home/kaifat/gothic-ai/body/.venv/bin/python`, resolved UV:
`/home/kaifat/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/bin/python3.12`,
SHA-256 `021044895e95be79dc2f110367607e684119afbc8ce75f6f0eec94844e0acec7`.

Новый opt-in bootstrap снимает только распознанное стартовое владение
при точном version/path/executable SHA; неизвестное nonzero ownership
не нормализуется. Actual private helper activation:375 →0, static freeze8891,
close0; GC enabled и thresholds700/10/10 сохранены. Pure tests28/28 PASS.
Общий unit wall61 мс сообщён parent из закрытого stdout (отдельного saved
launcher log для activation нет); внутренние helper clocks отдельно:
normalize0,021610 мс / freeze1,161068 мс / close0,027191 мс.
Это проверка helper, без модельного или игрового зачёта.

Gate-v8 заморожен, preregistration PASS50,177 с. После освобождения общего
simulation lock targeted runtime76/final70 прошли; known3/24/6 выполняется.
Веса, thresholds качества и native ac2 source прежние. Fresh screening
не запускался, этап8 остаётся IN_PROGRESS.

Повтор трёх read-only команд для interpreter comparison явно разрешён
parent; добавлен только отдельный immutable
`stage8-gc-ownership-profile-v1/isolated-interpreters.txt`.
Failed run7, runtime, конфигурации и reader sources не переписывались.
