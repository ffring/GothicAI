# Регрессия при `[GAME] ragdoll=0`

14 октября 2026 года. **Окончательный проверенный binary v12: все 11 числовых сценариев PASS, E2E обеих версий — 23/25 и FAIL; общий объявленный критерий не выполнен.** Все 15 899 строк CSV побайтово совпали с Jolt этапа 1. Состояния, HP, hits и 138 981 упорядоченная запись боя совпали без допусков. Предыдущие самостоятельные серии v4/v5/v6 и их первые исходы полностью сохранены. Результат v12 получен отдельной фактической серией после production-исправления birth velocity cache и benchmark corrections. Прежние расхождения Jolt с Bullet этим сравнением не закрываются.

## Зафиксированный эталон

- Личный стенд `dev-ubuntu-ts`: Linux `dev-ubuntu`, x86_64. Доступ и существующая инструментальная цепочка проверены только чтением; установка пакетов не требуется.
- Неизменяемый CPU runner этапа 1: `/home/kaifat/gothic-ai/physics-stage1/jolt-final-v13/reference/opengothic/gothic-body-runner`, SHA-256 `818360cdea0d96fd4d9c6be51cac631e879824f5f6e86cb1e9e54d6dae9a3165`.
- Неизменяемая игра этапа 1: тот же каталог, `Gothic2Notr`, SHA-256 `bc7ba04395a635400a38de361521d1efe30abbf7937fdfde42a5cac52766ce6f`.
- Сохранение: `/home/kaifat/gothic-ai/body-baseline/reference/save_slot_1.sav`, SHA-256 `c2852f4c009f23225e1c9c70f564ca36b4c12e8220736d1c72a1dbfce8d3372f`.
- Ассеты: `/home/kaifat/gothic-ai/body-baseline/assets`; исходный manifest всех 308 обычных файлов — `runs/melee-reference-stage1-v4/manifest.json`, SHA-256 `27d3fb7ae7caa8f2f8993617ffa709799811f4b2825259c3e253a9a7613e0660`. Перед новыми образцами проверяются фактические файлы, размеры и хеши.
- Числовой эталон: [полная исправленная серия Jolt v13](../../../data/physics/stage1/jolt-numeric-v13-corrected-resource/manifest.json), 11 сценариев, 15 899 строк, 323 исходных столбца. Она выполнялась с фактически проверенными CPUQuota=200%, MemoryMax=12G, TasksMax=128.
- E2E: замороженный изолированный server source из 71 файла, SHA-256 `a2367b97b8315de1e21eff30f81956b7ae754c777e13ce6b2a49c21c45d37fc9`; `uv.lock` SHA-256 `1710ab1bc23c892d680a836e8d6e230abb819b0edfce86d19b8dd2daff78c7a3`. Существующий результат 25/25 получен при CPUQuota=400%; он не подменяет новую пару при 200%.

## Порядок выполнения и критерии

После явного разрешения владельца интеграции на замораживание создаётся отдельный снимок исходников этапа 2 и отдельная Linux-сборка. До сборки сохраняются точные байты исходников, хеши, команды, версия компилятора и параметры CMake. Изменение исходников или последующий выпуск нового кандидата требует нового номера версии и сохранения всех предыдущих результатов.

Сборка и все игры выполняются последовательно под общим `body-simulation.lock`. Каждый SYSTEM unit использует CPUQuota=200%, MemoryMax=12G, TasksMax=128; фактические свойства cgroup проверяются до игровых образцов. Не допускаются параллельные Gothic/runner, компиляция или другие задачи этого стенда. Production-службы не изменяются.

1. Собрать новый `gothic-body-runner`, `Gothic2Notr`, выполнить `check-physics` и `check-ragdoll` существующим компилятором и Vulkan toolchain. Сохранить неизменяемые копии обоих бинарников.
2. Один раз выполнить все 11 сценариев: `smoke`, `ik-moves`, `ik-stairs`, `ik-sweep`, `lean`, `aim`, `manual-aim`, `crossbow`, `quad-wolf`, `quad-zoo`, `flinch`. Используются исходные сценарии этапа 1, seed 73013, lockstep 30 FPS, без окна, аудиоустройства и рендера; в каждом рабочем `Gothic.ini` явно записывается `[GAME] ragdoll=0`. Трассировки ragdoll и замеры M1 отключены. Для каждого сценария требуется успешное завершение, полное покрытие записей и исходная схема CSV. Строгий критерий — побайтовое равенство каждого полного CSV эталону. Дополнительно проверяются состояния, HP и упорядоченные боевые события без допуска числовых расхождений.
3. Один раз выполнить свежую E2E-пару **этап 1 → этап 2**, в каждом кандидате `see → seen → lie`. Оба используют один замороженный server fixture, одно сохранение, Vulkan lavapipe `llvmpipe`, Xvfb 320×240, четыре потока рендерера, CPUQuota=200%, MemoryMax=12G, TasksMax=128, LLM/ASR/TTS `fake`, brain и внешние decision-сервисы выключены. Проверки и ограниченные ожидания native push остаются исходными. Требуется ровно 25 уникальных исходных checks: `see` 8, `seen` 10, `lie` 7; сохраняются также exit codes, native ticks и фактический графический backend.

Все первые результаты сохраняются независимо от успеха. Повтор для выбора лучшего результата, увеличение CPU или времени ожидания после неудачи не допускаются. Совпадающие неудачи обоих E2E-кандидатов описываются как ограничение данного стенда, а не как проход 25/25. Это функциональное сравнение: исторические и новые настенные времена не используются как критерий быстродействия этапа 2 или замена проверки M1 Pro.

Сырые данные, сборка, команды и неизменяемые снимки будут сохранены в игнорируемом `data/physics/stage2/disabled-regression/`; хеши и итоговые вердикты будут добавлены сюда после выполнения.

## Prelaunch до первой игры

Снимок `candidate-v3` сохранён 08:16:26 UTC: 2945 файлов OpenGothic, 40 202 240 байт, SHA-256 архива `86f422c7ef13f8fef743be6edb63e3ea2be84b890f08cad1235eabb5a098eb16`. Все 28 объявленных исходных файлов проверены перед архивированием и после него. Исходный текст протокола с SHA-256 `8c4e0834b1c9d17be65efd47530ab9c0e001a4d516e44c6a79e602cf9d8627f2` сохранён рядом с архивом.

Первый SYSTEM unit завершился за 113 мс при попытке получить общий lock: его уже держала независимая задача `gothic-body-bc-stage8-combat-v6-combat-demos.service`, запущенная 08:26:53 UTC. **Распаковка исходников, сборка и игровые образцы не начались.** [Запись отказа](../../../data/physics/stage2/disabled-regression/linux-v1/prelaunch1.json) и [полный launcher log](../../../data/physics/stage2/disabled-regression/linux-v1/prelaunch1-launcher.log) сохранены. Чужой unit и процессы не изменялись; сообщений другой задаче не отправляли.

Чтение стенда выявило ограничение `/proc/*/exe` между соседними процессами. До первой фактической игры launcher дополнен чтением настоящего `/proc/*/comm`, включая сокращённое Linux-имя `gothic-body-run`. Проверка не ищет строки в argv и не изменяет сценарии или условия игры.

Во время ожидания M1-проверка обнаружила конкретную ошибку точности сохранения физической позы кандидата v3. Поэтому до первого Linux-образца выбран окончательный исправленный кандидат: v3 остаётся историей prelaunch без сборки и игры. Порядок, ограничения и строгие функциональные критерии выше сохраняются; новый хеш кандидата будет объявлен до первой фактической сборки.

## Первая фактическая серия: v4

В 09:17:42 UTC до сборки сохранены все 2945 файлов OpenGothic v4: 40 263 680 байт, SHA-256 архива `5bd99744faedf3542dc2d9a756f60b778c5e31321b3f73d0b4941a1894386fc3`. Все объявленные исходники проверены до и после относительно [manifest M1 v4](../../../data/physics/stage2/candidate-v4/manifest.json), SHA-256 `f48593eff3f4f1f81018db4b74a67d1eefeeabc436c6778ca5824e0aca8e0b4e`. Точные команды, скрипты и их хеши объявлены в 09:19:01 UTC: [run protocol](../../../data/physics/stage2/disabled-regression/linux-v2/run-protocol.json), SHA-256 `72c391e3d1b424328d4a8e234ad85b67e1862d7eaa4ae275d6ce8ade0501d1bd`.

Общий stand был свободен перед запуском. SYSTEM unit `gothic-stage2-disabled-regression-v2-actual.service` стартовал 09:20:18 UTC и получил общий lock. До распаковки, сборки и игровых образцов проверены реальные свойства: `User=kaifat`, `CPUQuotaPerSecUSec=2s`, `MemoryMax=12884901888`, `TasksMax=128`. В 09:20:21 UTC все 308 обычных файлов ассетов совпали по размеру и SHA-256 с исходным manifest. Никаких пакетов не устанавливали; исходники и сборку этапа 1 не меняли.

Сборка выполнена отдельно в `/home/kaifat/gothic-ai/physics-stage2/disabled-regression/linux-v2/build`; исходный архив и копии инструментов неизменяемы. Все требуемые Linux-бинарники собраны 09:27:04 UTC, затем отдельно прошли `check-physics` и `check-ragdoll`, включающий настоящий Pose/AnimationSolver и ZIP-кодек. До первой игры компиляция и другие Gothic/runner отсутствовали; все этапы использовали один общий lock.

Неизменяемый CPU runner v4: SHA-256 `35ce975db6ecfb0aae35230a973c98e708962ee7446b36748239e6da4cb39f59`. Игра v4: SHA-256 `bacf07c86617f473bb143b06231541f7bc231fb669eccf9cfb2a0883227c99f8`. Бинарники сохраняются отдельно в remote `linux-v2/reference/`; главный [pipeline](../../../data/physics/stage2/disabled-regression/linux-v2/pipeline.json) связывает их с точными исходниками и всеми командами, SHA-256 `2dd699a5b1a88557a42dab38fbc27142780b0b9417ef2d70d2515411ee72a1f6`.

### Строгое числовое сравнение v4

Все сценарии завершены один раз, без ошибок команд или пропущенных сегментов/кадров. Схема во всех — прежние 323 уникальных столбца; никакие поля не исключались. Точное равенство полных CSV включает непрерывные числа и прежние нечисловые значения эталона. Состояния, HP, количество ударов, все упорядоченные combat records и отдельные body observations совпали без допуска числовых расхождений.

| Сценарий | Строки CSV | Упорядоченные combat records | Полный CSV / состояния / события |
|---|---:|---:|---|
| smoke | 120 | 5 | PASS |
| ik-moves | 1350 | 13 218 | PASS |
| ik-stairs | 1680 | 49 | PASS |
| ik-sweep | 2700 | 64 542 | PASS |
| lean | 804 | 27 | PASS |
| aim | 1716 | 29 903 | PASS |
| manual-aim | 3510 | 5643 | PASS |
| crossbow | 718 | 2133 | PASS |
| quad-wolf | 1260 | 10 998 | PASS |
| quad-zoo | 1080 | 9431 | PASS |
| flinch | 961 | 3032 | PASS |
| **Всего** | **15 899** | **138 981** | **11/11 PASS** |

[Полное сравнение](../../../data/physics/stage2/disabled-regression/linux-v2/numeric-comparison.json), SHA-256 `cc1012a2b36e7bc1ee9a1910c14ff145e42bfa9a91ebac781bac015e991d3592`, сохраняет хеш каждого полного CSV, реальную полноту сегментов/кадров, длины упорядоченных списков и нулевые различия. Это совпадение с сохранённым Jolt v13; прежние отличия этого эталона от Bullet остаются открытыми.

### Первая E2E-пара v4 при CPUQuota=200%

Исходный Jolt выполнен 09:31:55–09:34:02 UTC, v4 — 09:34:02–09:36:21 UTC, в заранее объявленном порядке. В каждом ровно 25 исходных checks; **оба дали одинаковые 23 PASS и 2 FAIL**. У каждого `see` — 7/8 (`pushed` FAIL), `seen` — 9/10 (`story_state_sight` FAIL), `lie` — 7/7. Кроме того, в `seen` у обеих версий не выполнено ограниченное native readiness `urban_humans`; `fresh_push_waits_valid=false`. Поэтому совпадение исходов не означает успешную проверку E2E.

Во всех шести играх подтверждены `llvmpipe`, исходный server fixture `a2367b97…37fc9`, сохранение и ассеты, а до запуска каждой игры записаны фактические CPUQuota=200%, MemoryMax=12G, TasksMax=128 и `[GAME] ragdoll=0`. Пара использует один и тот же ограниченный стенд; совпадающие неудачи оставлены как его открытые проверки. Причина общего отказа не установлена этим сравнением. Исторический результат 25/25 при 400% не заменяет эти новые неудачи; CPU, ожидания и assertions после них не менялись, повторов для выбора успеха нет.

Сводки [исходного Jolt](../../../data/physics/stage2/disabled-regression/linux-v2/e2e/stage1-reference-v2/e2e-summary.json) и [v4](../../../data/physics/stage2/disabled-regression/linux-v2/e2e/stage2-candidate-v2/e2e-summary.json) сохраняют все checks, native ticks, ожидания и фактический backend. [Общая функциональная сводка](../../../data/physics/stage2/disabled-regression/linux-v2/functional-summary.json) сохраняет отдельные вердикты; общий `all_declared_functional_checks_pass=false`, SYSTEM unit завершился с кодом 1.

Серия завершена 09:36:25 UTC. Все 308 файлов ассетов повторно совпали по размеру и SHA-256 после обеих версий, исходники не изменились. Сырые CSV, JSONL, bench/launcher/server/game logs, фактические resource checks и команды сохранены в локальном `data/physics/stage2/disabled-regression/linux-v2/`; private saves и memory databases в локальную коллекцию не переносились. Эти результаты относятся к v4. Для нового v5 будут отдельные замороженные исходники, сборка и проверки; результат последней причинно исправленной версии будет окончательным независимо от успеха.

## Заранее объявленное обновление до v5

M1-проверки v4 обнаружили конкретные ошибки точности нефизических слотов `ZS_` и передачи позиции при подъёме; v5 содержит их исправления и проверки повреждённых конечных значений AnimationBlend. Переход к v5 обусловлен изменением исходников после этих ошибок, а не выбором удачного повторного результата. До его Linux-образцов будут сохранены новый полный source archive, manifest и точные хеши скриптов.

Для сокращения повторной компиляции используется исключительно наша изолированная рабочая копия `linux-v2/source` и её CMake build tree. Перед заменой вся старая source tree должна совпасть с v4 manifest. Затем из нового полного архива заменяются только изменившиеся файлы и повторно выполняется исходная CMake-конфигурация; точный перечень замен и хеши прежних build metadata сохраняются. Архив v4, reference binaries, протоколы, первая серия и логи неизменяемы. Новые бинарники, протокол и вся новая серия сохраняются отдельно в `linux-v3/`.

Объявленные порядок и критерии сохраняются: contracts, все 11 числовых сценариев, затем одна новая свежая E2E-пара этап 1 → v5 при фактически проверенных 200%/12G/128. Сохранение, 308 файлов ассетов, 71 файл E2E fixture, сценарии, часы, ограниченные ожидания и assertions остаются прежними. Все первые результаты v5 будут сохранены независимо от успеха.

## Первая серия v5

Полный снимок v5 сохранён 10:01:00 UTC: те же 2945 файлов, 40 284 160 байт; архив SHA-256 `fb12a19cb219e564893dc4812b9e8048b2331b118cf6962cd80fa4b9322d8910`. Все 30 объявленных source files, 13 helpers и полная source tree совпали с [manifest M1 v5](../../../data/physics/stage2/candidate-v5/manifest.json), SHA-256 `fc4304b773402613ba514d25d36ce79e95a24b1aea9b90f60f33a2c40f23e218`, до и после копирования. [Новый протокол](../../../data/physics/stage2/disabled-regression/linux-v3/run-protocol.json) зарегистрирован до unit, SHA-256 `c139301ff284a1ef309d5e7aae84ac37bf57b006c8043b728180aedb07050cac`.

Unit `gothic-stage2-disabled-regression-v3-actual.service` получил общий lock 10:02:27 UTC. Перед overlay подтвердились все байты старой source tree v4, архив, reference binaries, протокол и скрипты. Из нового полного архива изменены ровно восемь файлов; source paths и прежние build metadata hashes записаны в [pipeline](../../../data/physics/stage2/disabled-regression/linux-v3/pipeline.json), SHA-256 `9d4cd4f8f1b7a63d4854bb44809b19b02b1144ffb697657cdffd4ef6a3135429`. Повторная конфигурация и incremental build PASS; оба contracts PASS. Реальные 200%/12G/128 проверены до работы и до игр; исходные 308 файлов ассетов и E2E fixture совпали.

Неизменяемый Linux runner v5: SHA-256 `95d6d8c971811451e78193b078912816af501afc260c77bda2f06a1078d90a1c`. Игра v5: SHA-256 `fbb3f4e4549562c823d73fc74b22f0d6f6b20cfb94ec3091ac7f7b4641a4976e`. Их отдельные копии сохранены в remote `linux-v3/reference/`.

**Все 11 числовых сценариев v5 PASS**: 15 899 строк, 323 прежних поля, полные CSV побайтово равны Jolt v13. Состояния, HP, hits, все 138 981 упорядоченных combat records и body observations совпали. По каждому сценарию число строк и событий равно таблице v4 выше; [полное сравнение v5](../../../data/physics/stage2/disabled-regression/linux-v3/numeric-comparison.json), SHA-256 `92156de81fbbc82b3224621fd340ca111e79c6fd1210be085b320e208eb3b539`, сохраняет самостоятельные проверки полноты и нулевые различия.

Одна новая свежая E2E-пара выполнена в исходном порядке 10:09:13–10:11:21 UTC (Jolt v13), 10:11:21–10:13:30 UTC (v5). **Обе версии дали ровно 23/25, FAIL**: `see` 7/8 с `pushed`, `seen` 9/10 с `story_state_sight`, `lie` 7/7. В обоих `seen` также FAIL ограниченного native readiness `urban_humans`, `fresh_push_waits_valid=false`. Все шесть игр подтвердили `llvmpipe`, фактические 200%/12G/128 и `ragdoll=0`. Сводки [Jolt v13](../../../data/physics/stage2/disabled-regression/linux-v3/e2e/stage1-reference-v3/e2e-summary.json), [v5](../../../data/physics/stage2/disabled-regression/linux-v3/e2e/stage2-candidate-v3/e2e-summary.json) и [общий verdict](../../../data/physics/stage2/disabled-regression/linux-v3/functional-summary.json) сохраняют каждую первую неудачу. Общий критерий FAIL, unit rc=1; исходы checks одинаковы, причина общего отказа этим сравнением не установлена.

Серия закончена 10:13:35 UTC. После неё все 308 файлов ассетов и source bytes снова совпали; прежние v4 reference binaries и pipeline не изменились. Сырые данные собраны в `linux-v3/` без private saves, SQLite, binaries или build tree; сбор происходил до M1 timed measurements. Повторов, изменения CPU, waits или assertions не было.

## Следующая версия после остаточной ошибки M1

Отдельная M1-проверка v5 обнаружила остаточную ошибку нефизических attachment slots при full session/long OFF при точных native State3 и mapped bones. Владелец интеграции разрешил причинное исправление Pose v6. До его первой игры объявлено: новый полный замороженный source archive, самостоятельные binaries и вся первоначальная последовательность checks, числовых сценариев и новой E2E200 пары. Перед overlay требуется точное совпадение рабочей source tree с последним manifest v5; все immutable archives/references/first results v4 и v5 сохраняются.

## Первая серия v6

В 10:23:23 UTC сохранены все 2945 файлов v6, 40 284 160 байт: архив SHA-256 `d6aa1eaec8ec09fd82788b25b874ec593316d6515c855cb5e530f64edd751df9`. Все 30 source files, 13 helpers и полная source tree проверены до и после копирования против [manifest M1 v6](../../../data/physics/stage2/candidate-v6/manifest.json), SHA-256 `89d3a5e42d0b212e1b797bd3cc75ab140323c40701724505b1b657acf526f447`. [Linux-протокол v6](../../../data/physics/stage2/disabled-regression/linux-v4/run-protocol.json), SHA-256 `1c1912a4100628a55c32c722b0fc0c0dbd106bdfb8de9f3a2cd1b10abcf6b62c`, зарегистрирован до unit; сравнивающий скрипт побайтово совпадает со всеми предыдущими сериями.

SYSTEM unit `gothic-stage2-disabled-regression-v4-actual.service` стартовал 10:25:50 UTC, invocation `98b00906f18e40e2b79c34450d379b77`. Под общим lock проверены все старые source bytes v5, immutable archives/references/protocols v4/v5, baseline binaries, сохранение, 308 ассетов и 71 файл E2E fixture. Из нового архива заменены ровно `pose.cpp`, `testbench.cpp`, `physics_ragdoll_contract.cpp`; удалённых/добавленных source paths нет. CMake configure, incremental build, `check-physics` и `check-ragdoll` PASS. До сборки и до игровых образцов фактически подтверждены `User=kaifat`, 200%/12G/128; компиляция с играми не пересекалась.

Новый immutable Linux runner: SHA-256 `52ed53b40aa37a194931dfa5778a548c384cf6b03b78b43d49573ea3a5edfaf0`. Игра: SHA-256 `f44473bac56f9b28da7630cb1873a19dbcc8723618df2c29d61a8c6a8f0e697c`. Копии обоих и contract binaries сохранены в remote `linux-v4/reference/`; [pipeline](../../../data/physics/stage2/disabled-regression/linux-v4/pipeline.json), SHA-256 `c622242081d0fa7a4ca55460a5c9fe3c8ea2598a9c97e80d401f7981249c5c8b`, сохраняет точные команды, compiler/platform, source/archive/bin hashes и предыдущие build metadata.

**v6: все 11 числовых сценариев PASS**, каждый выполнен один раз в исходном порядке. Полные CSV всех 15 899 строк и 323 уникальных исходных столбцов побайтово совпали с frozen Jolt v13; states/HP/hits, 138 981 ordered combat records и body observations точны. Ноль различий, полный foreach/recstop/event coverage. [Самостоятельное полное сравнение v6](../../../data/physics/stage2/disabled-regression/linux-v4/numeric-comparison.json), SHA-256 `a2ffbd07e754b37190619f26802c56c0be8fea95d606bfa2b88c01f08a4afe3a`, содержит все результаты по 11 сценариям; числа строк/событий равны таблице v4 выше. Эта проверка относится к фактически собранному v6, результаты прошлых версий не использованы вместо нового запуска.

Первая свежая E2E200 пара: Jolt v13 — 10:31:09–10:33:16 UTC, v6 — 10:33:16–10:35:17 UTC. **У каждой версии 23/25, FAIL**: `see` 7/8 (`pushed`), `seen` 9/10 (`story_state_sight`), `lie` 7/7; statuses всех 25 уникальных checks совпали. В обеих версиях `seen/urban_humans` readiness FAIL, `fresh_push_waits_valid=false`. Все шесть игр подтвердили `llvmpipe`, действительные 200%/12G/128, UID 1000 и `ragdoll=0`. Полные [baseline E2E](../../../data/physics/stage2/disabled-regression/linux-v4/e2e/stage1-reference-v4/e2e-summary.json), [v6 E2E](../../../data/physics/stage2/disabled-regression/linux-v4/e2e/stage2-candidate-v4/e2e-summary.json), resource-before-game records и [общая сводка](../../../data/physics/stage2/disabled-regression/linux-v4/functional-summary.json) сохраняют первые исходы. **Общий объявленный критерий FAIL**, оба E2E rc=1, unit rc=1. Совпадение неудач не закрывает E2E; причина общего отказа этим сравнением не установлена.

Серия завершена 10:35:24 UTC; все source bytes, 308 ассетов, frozen fixture и старые immutable v4/v5 archives/reference binaries/protocols повторно проверены; у v4 дополнительно повторно совпали hashes numeric comparison и обеих E2E summaries. Ни CPU/ожидания/assertions, ни seed/clocks/columns не изменялись; повторов для выбора результата нет.

Полные CSV, JSONL, сценарии, INI, bench/launcher/server/game logs, все resource records, pipeline и verdict сохранены локально в `data/physics/stage2/disabled-regression/linux-v4/` и на stand в одноимённом каталоге. Локальная коллекция выполнена только после явного M1 **TIMING DONE**, без private saves/SQLite/binaries/build tree. После переноса снова совпали hashes всех трёх immutable source archives и исходных metadata v4/v5/v6. [Проверка уже собранных raw данных](../../../data/physics/stage2/disabled-regression/linux-v4/collected-numeric-verification.json) тем же скриптом дала те же полные результаты всех 11 сценариев, CSV/coverage/states/HP/hits/ordered combat/body observations; новых игровых образцов при ней не было. Финальная [functional summary](../../../data/physics/stage2/disabled-regression/linux-v4/functional-summary.json) SHA-256 `f6a42210de03b915ae972005ebe9e7515f36182f5b3ee68db1458240f8eb9110`.

## Продолжение после исправления performance fixture

Владелец интеграции признал первые M1 performance samples v6 недействительными: в fixture было 12 тел, а не 20, и камера не обеспечивала вид всех 20. Исправленный v7 проверен без записи FPS; выявлена возможность кратковременного засыпания активных тел. Эти ошибки benchmark fixture не отменяют самостоятельные числовые и E2E результаты v6 выше.

Перед корректной performance серией объявлено получение нового freeze исходников и helpers после промежуточных untimed v7–v11. Добавлены управление разрешением сна в ограниченном benchmark workload и проверка присутствия полной body model; дорогая visibility проверка вынесена из timed path. Обычная игровая политика сна сохраняется. Независимый untimed audit дополнительно выявил production-ошибку birth velocity cache: перед READY требуются исправление NPC и focused actual falling death/KO oracle. Поэтому следующий freeze включает конкретные production и fixture corrections; промежуточный архив v10 или новые Linux jobs тогда не запускались.

После READY сохраняется полный новый архив до M1 timing window. Для фактически собранного нового binary будет отдельная серия в `linux-v5/`: contracts → все 11 числовых сценариев → одна свежая E2E200 пара, с прежними 200%/12G/128, baseline, assets, fixture, seed, clocks, columns, waits и assertions. Перед overlay требуется точное совпадение рабочей source tree с v6 manifest; все предыдущие immutable archives/references/first results остаются сохранёнными. Архивирование и запуск до READY не выполняются.

## Окончательная самостоятельная серия v12

После READY владельца проверки в 12:42:01 UTC сохранён полный v12: 2945 файлов, 40 325 120 байт, source archive SHA-256 `ffa659c6c6e1f3c578fb55ac72c2cf9a034352c7ce52a6320fcc0b32f176d806`. Все 31 source file, 18 helpers и вся source tree проверены до и после против [manifest M1 v12](../../../data/physics/stage2/candidate-v12/manifest.json), SHA-256 `38613b7e4ece26d0f2ecdffdf9ca9634765b0236e728841e01095208d3f9eaec`. Владелец перед freeze подтвердил focused birth oracle, main/fullSession/queue, native/exact creation replay и три untimed 20-model preflights. Этот отчёт не делает выводов о FPS из этих preflights или прежних invalid resource samples.

Первая source-copy попытка остановлена занятым stand lock: remote mkdir и receiver `flock` отказали до записи source tree, компиляции или игр. [Отказ сохранён](../../../data/physics/stage2/disabled-regression/linux-v5/source-copy-prelaunch1.json). Чужой `gothic-body-bc-stage8-final-gate-v3-shadow.service` и его процессы не изменялись, сообщений другой задаче не отправляли. Локальный immutable archive уже был готов, поэтому M1 начал свою проверку с ним; 40MB upload отложен до TIMING DONE. Когда stand освободился, upload успешно выполнен под receiver lock в 12:55 UTC. Это не повтор игрового образца; до него новых Linux samples не было.

Точный [протокол v12](../../../data/physics/stage2/disabled-regression/linux-v5/run-protocol.json) зарегистрирован 12:50:54 UTC, до первой сборки: SHA-256 `6aa25e8db0305c7753120907be0b68f84654bc83e52526a8355e6b91d10c6a5c`. Compare script побайтово совпадает со всеми предыдущими сериями; E2E helper меняет только каталог результатов. SYSTEM unit `gothic-stage2-disabled-regression-v5-actual.service`, invocation `a6e8e669a5c243bc8a37b2531a557837`, получил stand lock 12:55:55 UTC. Перед overlay проверены вся source tree v6, immutable archives/references/protocols v4/v5/v6, сохранение, baseline binaries, все 308 ассетов и 71 файл frozen E2E source.

Из нового снимка заменены ровно девять source paths: `mdlvisual.h`, `meshobjects.h`, `ragdoll.cpp/h`, `testbench.cpp/h`, `npc.cpp/h`, `physics_ragdoll_contract.cpp`. Added/removed paths нет. Повторная CMake-конфигурация, incremental build (завершён 12:58:52 UTC), `check-physics` и `check-ragdoll` PASS. До работы и до samples фактически проверены `User=kaifat`, CPUQuota=200%, MemoryMax=12G, TasksMax=128; source/assets/fixtures и история после серии снова прошли проверки, compiler/game overlap отсутствовал.

Неизменяемый Linux runner v12 SHA-256 `f7cc9f874dca5e5a4c7b40d9abda6ef0e1b0b179912b1028d00096ff4ccaa942`; игра SHA-256 `7d499016d6f4a0a0047c2b71f3dc1a7d26f44500089960f50a1185adb63cd32e`. Отдельные copies обоих и contract binaries — remote `linux-v5/reference/`. [Pipeline](../../../data/physics/stage2/disabled-regression/linux-v5/pipeline.json), SHA-256 `7d28f68351d0c1cea6566bdfb486d779cc006de19da1b0f396080337ab68a403`, связывает их с полным source archive, точными командами, actual resource gates, compiler/platform и old build metadata.

**Все 11 сценариев фактического v12 PASS**: 15 899 строк × 323 уникальных прежних столбца, полные CSV побайтово равны frozen Jolt v13. States/HP/hits, все 138 981 ordered combat records и body observations совпали. Нулевые различия, полные foreach/recstop/event/segment/frame coverage; каждый сценарий выполнен один раз в первоначальном порядке. [Самостоятельное numeric comparison](../../../data/physics/stage2/disabled-regression/linux-v5/numeric-comparison.json), SHA-256 `fa848aa4c85b3ef5dd76fc749af97ca04e77ed784b7bb91898f04e8498b71d07`, сохраняет каждый результат; числа по сценариям совпадают с таблицей v4 выше. Старые PASS не использовались вместо новых samples.

Первая свежая E2E200 пара выполнена 13:03:43–13:05:48 UTC (frozen Jolt v13), 13:05:48–13:07:55 UTC (v12). **Оба 23/25, FAIL**: `see` 7/8 (`pushed`), `seen` 9/10 (`story_state_sight`), `lie` 7/7. Statuses всех 25 исходных уникальных checks совпали. В обоих `seen/urban_humans` readiness FAIL, `fresh_push_waits_valid=false`. Все шесть игр подтвердили CPUQuota=200%, MemoryMax=12G, TasksMax=128, UID 1000, `ragdoll=0`, `llvmpipe`, immutable save/assets и тот же fixture. [Baseline E2E](../../../data/physics/stage2/disabled-regression/linux-v5/e2e/stage1-reference-v5/e2e-summary.json) и [v12 E2E](../../../data/physics/stage2/disabled-regression/linux-v5/e2e/stage2-candidate-v5/e2e-summary.json) сохраняют первые checks, wait/tick/backend/resource evidence. Общий объявленный критерий **FAIL**: E2E rc=[1,1], SYSTEM unit rc=1. Эти совпадающие отказы остаются открытым ограничением данного сравнения; их причина здесь не установлена, historical400%/25PASS не подменяет новый результат.

Серия завершена 13:08:03 UTC. Сырые CSV/JSONL, сценарии/INI, bench/launcher/server/game logs, actual resource records и все manifests/verdicts собраны локально в `linux-v5/` только после resource2 M1 TIMING DONE; private saves/SQLite/binaries/build tree не переносились. После переноса снова совпали hashes всех четырёх archives и исходных metadata v4/v5/v6/v12. [Offline verification собранных raw данных](../../../data/physics/stage2/disabled-regression/linux-v5/collected-numeric-verification.json) тем же compare script воспроизвела все результаты 11 сценариев и structured events; новых игровых образцов не было. [Итоговая сводка](../../../data/physics/stage2/disabled-regression/linux-v5/functional-summary.json), SHA-256 `e0e39a224814b63d46cd4244179cf6fe94d1f7711c547b5dc755f92cff52a15f`, сохраняет отдельные вердикты и общий `overall_declared_pass=false`.

Настенные времена, invalid M1 timings и будущая matched resource series не используются как oracle этой функциональной проверки. Ни seed/clocks/columns, ни CPU/waits/assertions не менялись; все первые результаты сохранены. Итог этого файла относится к exact v12 binary при `ragdoll=0` и не является заявлением о закрытии performance или прежних Bullet/Jolt различий.
