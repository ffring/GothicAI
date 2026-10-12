+++
title = "Three days in: characters that talk back"
summary = "What came together in the first three days: voice in, voices out, actions, memory, two minds for every character, and feet that stand on the ground."
date = 2026-10-12T12:00:00+03:00
cover = "cover.jpg"
+++
Gothic AI started on October 10 with a question: what if the people of Gothic II could actually hear you?
Three days and about eighty commits later, they can.

The game is the original Gothic II: Night of the Raven, running on the open-source
[OpenGothic](https://github.com/Try/OpenGothic) engine. Inside the engine sits a thin bridge; everything
clever runs in a separate AI server next to the game.

## What works

**You talk with your voice.** There is no chat window: you hold T, walk up to someone and speak. Speech
recognition (Voxtral) turns it into text while you are still talking, and the server works out whom you
addressed, from where you look and what you say.

**They answer with voices of their own.** Each character gets a voice designed from a description of it,
not cloned from the original actors. The voice starts while the answer is still being generated, so the
first words come quickly.

**They act, not only talk.** Characters follow you, give you items, draw weapons, fight or run away. A
conversation can end in a deal, or in a fight.

**They remember.** Conversations are remembered per save game: load an older save and the characters forget
what happened after it. Bystanders overhear what you say to someone else, which matters, because lying is
meant to be part of the game.

**Two minds per character.** A small model, fine-tuned on scenes from Gothic, runs the body: the daily
routine, reactions to a drawn sword, fleeing or fighting, in a fraction of a second. The large language
model does the talking and sets intentions. The body reacts first, the words follow.

**Feet on the ground.** Not AI, but you notice it at once: with foot IK people stand on slopes and stairs
with both feet, and wolves and molerats with all four paws, instead of hovering above the ground.

## The tooling around it

A launcher starts the game and all the services with one button. A test bench plays scenarios inside the
game on its own and records video, metrics and a report of suspicious frames, which is how the foot IK was
tuned without anyone watching the screen.

## Next

The story layer. We are lifting the dialogues of the original scripts into a graph that an AI director can
edit, to build quests on top of the original story rather than instead of it, and to make lies to the
characters work and have consequences.

The project is not playable yet: it runs from source on macOS and needs your own copy of the game.
