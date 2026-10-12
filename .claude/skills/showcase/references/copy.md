# Copy: posts, Telegram, YouTube

## Voice

Plain and specific, the way a developer tells a friend what works now. Lead with what a player sees or
hears, then how. Numbers only when they are measured (a take's length, the share of real time, commit
counts from git). No hype ("revolutionary", "next-gen", "game-changer", "magic"), no claims about the
future without "next" or "planned". Name tools by what they do for the reader (speech recognition, a
voice designed from a description) and give the product name once, if at all.

## The honesty lines

Put the one that fits into every short's description and into posts that show takes.

- RU: «Ответы NPC не прописаны заранее и не правились: персонаж слышит голос героя через распознавание
  речи и отвечает языковой моделью. Вырезаны только паузы, пока модель думает. Голоса созданы по
  описанию, никто не клонирован.»
- EN: "The NPC's answers are not scripted and not edited: the character hears the hero's voice through
  speech recognition and answers with a language model. Only the pauses while the model thinks are cut.
  The voices are designed from descriptions; nobody's voice is cloned."
- Status, RU: «Поиграть пока нельзя: проект запускается из исходников на macOS и требует своей копии
  Gothic II: Ночь Ворона.» EN: "Not playable yet: it runs from source on macOS and needs your own copy of
  Gothic II: Night of the Raven."

## A devlog post

```
+++
title = "<what the reader will see, not the internal name>"
summary = "<one sentence for the list and the feed>"
date = <now with the zone, e.g. 2026-10-14T19:00:00+03:00>
cover = "cover.jpg"
youtube = "<id, once uploaded>"
+++
<one paragraph: what you can see now and why it matters to a player>

## <what works / how it is made>
<short sections or a numbered list when it is a real sequence>

## What we keep honest      (when the post shows takes)

## Next                     (plans, said as plans)
```

300-700 words. Link the repository paths you mention on GitHub (`https://github.com/kaifaty/GothicAI/tree/main/<path>`).
Credit OpenGothic (`https://github.com/Try/OpenGothic`) when the engine comes up.

## Telegram (Russian)

```
**<Заголовок: что теперь умеет игра>**

<1–2 предложения: что изменилось и почему это интересно>

— <пункт: что видно или слышно в игре>
— <пункт>
— <пункт>

Дальше: <следующий шаг, как план>.

<ссылка на пост на сайте> · <ссылка на шортс, если есть>
```

Under ~900 characters. Emoji: none, or one per item at most; bold only for the title.

## YouTube

- **Title**, under 60 characters, the hook first: RU «Соврал Ксардасу — и он это понял | Gothic II + ИИ»,
  EN "I lied to Xardas and he saw through it | Gothic II + AI". No clickbait the take does not deliver.
- **Description**:
  ```
  <one line: what happens in the short>

  <the honesty line>

  <the status line>
  Devlog: https://kaifaty.github.io/GothicAI/   Code: https://github.com/kaifaty/GothicAI
  Gothic II: Night of the Raven by Piranha Bytes / THQ Nordic, on the OpenGothic engine.

  #Gothic2 #Готика #OpenGothic #AI #NPC
  ```
- Shorts: under 60 s, vertical (the cut is), the Russian and the English cut as two uploads.
