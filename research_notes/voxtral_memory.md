# Voxtral: меньше видеопамяти и быстрее

Исследование и замеры 2026-10-11 на GPU-сервере (RTX 3090 24 ГБ).

## Итог

| Вариант | VRAM | Задержка после конца речи, p50 | WER (16 фраз) |
|---|---|---|---|
| Сейчас: vLLM 0.31, bf16, `--gpu-memory-utilization 0.55`, `--max-model-len 2048` | 13,2 ГБ | 0,33–0,38 с | 8,3% |
| vLLM, онлайн-FP8 (`fp8_per_tensor`), `--max-model-len 768`, KV-кэш 1 ГиБ | 7,3 ГБ | 0,23 с | 8,3% |
| **audio.cpp, Q4_K, сервер, потоковый режим** | **3,4–3,7 ГБ** | **0,14 с** | **8,3%** |
| audio.cpp, Q8_0, CLI, потоковый режим | 5,6 ГБ | не мерили | 8,3% |

- **Качество не изменилось.** Ни одна квантизация не испортила распознавание. Во всех вариантах одни и те же ошибки, слово в слово.
  - Ошибки в основном в именах: «Ли Бейтарг» → «Либейтерк», «Аданоса» → «Адоноса», «стража» → «стражи».
  - В ru_16 ошибка не распознавания, а синтеза: Milena читает кавычки «» как «бек слышь».
- **Задержка.** Время от последнего аудиочанка до готового текста. Аудио шло с Mac в темпе речи, чанками по 100 мс.
  - Для vLLM мерил `gothic-asr transcribe --realtime`.
  - Для audio.cpp — отдельным клиентом по HTTP с тем же темпом.
  - Клиенты разные, поэтому разница в пределах ~0,05 с не показательна.
- **Рекомендация: audio.cpp Q4_K.** Памяти вчетверо меньше, а ответ в 2–2,5 раза быстрее. Освобождается ~9,5 ГБ: хватит на decision-модель для NPC и на запас для TTS.

## audio.cpp

- **Что это.** [github.com/0xShug0/audio.cpp](https://github.com/0xShug0/audio.cpp): движок на C++/ggml для аудио-моделей, ~3,5 тыс. звёзд, активно развивается. Поддерживает Voxtral Realtime в обычном и потоковом режиме, а также VoxCPM2.
- **Веса.** Официальные GGUF от Mistral: [mistral-experimental/AudioCPP-Voxtral-Mini-4B-Realtime-2602-GGUF](https://huggingface.co/mistral-experimental/AudioCPP-Voxtral-Mini-4B-Realtime-2602-GGUF). Q4_K весит 2,9 ГиБ, Q8_0 — 4,75 ГиБ.
- **Сборка на GPU-сервере** (~7 минут):

  ```bash
  scripts/build_linux.sh --backend cuda --cuda-arch 86 --model-set custom --models voxtral_realtime,voxcpm2 --target audiocpp_cli --target audiocpp_server -j 8
  ```

  Сервис ставится скриптом `asr-service/deploy/voxtral/install.sh`.
- **Сервер.** `audiocpp_server --config server.json`, модель с `"mode": "streaming"`. Потоковый ввод идёт через `POST /v1/audio/transcriptions/live?model=…&sample_rate=16000&channels=1&sample_format=s16le`:
  - тело запроса — сырой PCM16 с `Transfer-Encoding: chunked`;
  - ответ — SSE на том же соединении: `transcript.text.delta`, затем `transcript.text.done`, затем `[DONE]`.
  - WebSocket нет, нужен новый провайдер в `asr-service`, протокол vLLM Realtime здесь не подходит.
- **Хвост фразы.** Модель отстаёт от звука на 480 мс, а `finish_stream()` в потоковом режиме не досыпает тишину в конце, в отличие от vLLM. Поэтому клиент после отпускания клавиши сам шлёт 0,96 с тишины одним чанком. GPU обрабатывает её мгновенно, и это уже входит в 0,14 с. С 0,64 с тишины иногда обрезается последнее слово.
- **Память.**
  - CLI Q4_K: пик 3,7 ГБ. Сервер Q4_K: 3,4 ГБ в простое, 3,7 ГБ под нагрузкой.
  - В 3,9 ГБ рядом с vLLM и VoxCPM Q4_K не влез даже с урезанными аренами: `stream_decode_cache_steps`, `*_graph_arena_mb`. Нужно ~4 ГБ свободных.
- **Параметры, которые стоит знать:**
  - `voxtral_realtime.stream_decode_cache_steps` — по умолчанию 1024 шага по 80 мс, это ~82 с контекста;
  - `stream_batch_tokens` — нужен только на слабом железе, 3090 он не нужен.
- **Риски.**
  - Проект молодой: API сервера может меняться, поэтому собранную версию стоит закрепить по коммиту (сейчас `73c5554`).
  - Одна модель обслуживает один запрос за раз. Для одного игрока с push-to-talk этого хватает.

## vLLM: что можно ужать, не меняя клиента

Из лога сервиса текущие 13 ГиБ складываются так:
- веса — 8,43 ГиБ;
- KV-кэш — 3,15 ГиБ;
- пик активаций — 1,02 ГиБ;
- CUDA-графы — 0,17 ГиБ.

Что можно сделать:
- **KV-кэш.** Один токен Voxtral — 80 мс звука и ~1,1 МиБ KV. Сейчас `--max-model-len 2048`, это ~164 с звука, а наши фразы не длиннее 30 с. `--max-model-len 768` (~61 с) и `--kv-cache-memory 1073741824` экономят ~2 ГиБ.
- **FP8.** `--quantization fp8_per_tensor` (старое имя `fp8` устарело). На 3090 нет FP8-вычислений, поэтому vLLM включает Marlin W8A16: квантуются только веса текстового декодера. Веса уменьшаются с 8,43 до 4,66 ГиБ, декодирование ускоряется.
- **Утилизация.** При запуске vLLM проверяет `--gpu-memory-utilization` против свободной памяти. С `--kv-cache-memory` её нужно задавать явно, например `0.4`.
- **Чего нет.** AWQ и GPTQ для Voxtral Realtime в vLLM не собираются: калибровка падает на аудиоэнкодере ([разбор](https://huggingface.co/ltl1605/voxtral-fp8-round2)). FP8 KV-кэш блокируется FlashInfer.

## Другие порты (не проверял)

- [voxtral.cpp](https://github.com/andrijdavid/voxtral.cpp) — ggml, Q2–Q8, без сервера, 36 звёзд.
- [CrispASR GGUF](https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF).
- [voxtral-mini-realtime-rs](https://github.com/TrevorS/voxtral-mini-realtime-rs) — Rust/Burn, Q4 в браузере через WebGPU.

Для сервера на CUDA у audio.cpp лучшая зрелость.

## Сделано (2026-10-11)

- **Провайдер.** `voxtral` в `asr-service` теперь ходит в audio.cpp: chunked POST, разбор SSE, в конце 0,96 с тишины. Адрес задаётся в `GOTHIC_VOXTRAL_URL=http://<GPU-сервер>:47830`.
- **Сервис.** `gothic-voxtral` на GPU-сервере — это теперь `audiocpp_server` с Q4_K.
  - Ставится скриптом `asr-service/deploy/voxtral/install.sh`: он собирает audio.cpp с закреплённого коммита и качает GGUF.
  - Адрес для прослушивания берётся из `~/gothic-ai/gothic-ai.env` (`VOXTRAL_HOST`).
- **vLLM для ASR удалён:** venv, веса bf16 из кэша HF и юнит. Запасной провайдер — по-прежнему `chatgpt`.
- **VoxCPM2 через audio.cpp** пока не проверялся.

## Замечание по эксплуатации

`pkill` по PID-файлу `vllm serve` убивает только API-процесс, а `VLLM::EngineCore` остаётся сиротой и держит память. Из-за этого `gothic-voxtral` не стартовал, пока EngineCore не убили вручную. Останавливать тестовые vLLM нужно по группе процессов или через systemd.
