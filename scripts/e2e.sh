#!/usr/bin/env bash
# End-to-end check: starts the AI server with a scenario, then a new game in OpenGothic.
#   m0: takes Xardas over, walks him around and returns him to his routine
#   m1: Xardas answers a player question line by line (LLM backend: LLM=fake|chatgpt|local)
#   m2: npc actions (give_item, follow/stop, gestures)
#   m3: memory survives save/load and follows the save; push-to-talk from a wav file (streamed at
#       speaking pace) through the ASR service (ASR=fake|voxtral|chatgpt)
#   m4: what the npc planner needs from the bridge: npc roster, soft actions (npc_act) and their end
#       (npc_idle); with DECIDER_URL the planner runs against that decision service too
#   m5: the turn loop (a rejected commitment corrected in the same turn, a real give after the line),
#       intents moving the body, and an incident: the body reacts before the words (behavioral model:
#       DECIDER_URL, or a fixed stand-in)
#   rescue: the hero next to the shepherd Maleth, a wolf attacks him: the body reacts (a call for help
#       runs from the wolf), words follow on the move; the hero kills the wolf (monster_killed): the shepherd
#       reacts and speaks again (the live models with DECIDER_URL and LLM, or a fixed stand-in and scripted
#       words). rescue_self: the shepherd kills the wolf himself and says a word about it
#   m5e: talking without a window: a typed line and the talk key (a wav through the ASR service) with the
#       hero's own focus, the addressee, the npc's body, the hero's doings, an interruption, a witness, the end
#       of a conversation when the hero walks away, a walking npc answering on the move
#   m6: npc's know the world from the scripts' dialogs as far as the story has got: Xardas's talk of chapter 1
#       but not chapter 3, a dialog played from the menu becomes one the hero has heard, chapter 3 opens its
#       talk; Lobart knows his field work and what farmers say; the knowledge is in the model's request and
#       recall gives a topic in full (model scripted with LLM=fake)
#   see: what npc's perceive (npc_view, v11): Xardas sees the hero in front of him and not through the tower's
#       walls; Lobart names whom he knows and tells the hero by his look, in militia armor a militiaman; the
#       view is in the model's request, `look` answers, the views of the nearest npc's are pushed each second;
#       places by name at Xardas's tower, Lobart's and Onar's farms and in the city
#   places: not a check: dumps the world's waypoints and its people's routines into $LOG_DIR/world_points.json
#       for the book of places (gothic-ai places <dump>, server/src/gothic_ai/places.py)
#   tts: npc lines reach the game with audio from the TTS service (TTS=fake|voxcpm|inworld) and play aloud
#   lie: the hero lies to a quest giver (Xardas: two sheep to kill) and is paid; a sheep he did not kill
#        turns up, Xardas finds out, attacks him through the scripts, and Jora, standing by, hears it as a
#        rumor and has it in mind when the hero speaks to her (story graph, stage 4)
#   repair: Lobart gives the hero the turnip quest and dies; the director finds the quest without an ending,
#       its model proposes a repair (scripted with LLM=fake: first the dead Lobart, rejected by the validator,
#       then Hilda), and the hero ends the quest with Hilda for Lobart's reward (story graph, stage 5)
#   vlie: a lie about a quest of the scripts: the hero tells Lobart the turnips are in the barn, Lobart's
#       model (scripted with LLM=fake) believes him and his own ending plays; the next morning he misses the
#       turnips, turns against the hero, and the people around hear it (story graph, stage 5)
#   vcheck: lies as a mechanic: the hero tells Lobart he killed the raiders on his field; Lobart goes to see
#       for himself, sees them alive and catches the lie; then he takes no lie on the hero's word; once the
#       raiders are really dead he goes to see again and pays (model scripted with LLM=fake; stage 5)
#   rumor: Lobart catches the hero lying; the clock goes to the next morning and the rumor goes round the
#       people that know each other (routines, shared quests) as far as the city; one there has it in mind
#       when the hero speaks to it (stage 5)
#   amends: the director's storylet: Lobart catches the hero lying, the rumor reaches Andre, who asks for a
#       fine and closes joining the militia until then (model scripted with LLM=fake); the hero hears him
#       out and pays, and the militia is open again (stage 5)
#   amends_deal: as amends, but the hero makes them in conversation: he asks to be forgiven, the npc offers a
#       deal that forgives the lie for the fine, and he accepts it (docs/npc-tools.md, wave 1 step 2)
#   debt: Lobart lends the hero 20 gold for 25 back by the end of the next day; two days on the debt is a
#       broken word (trust) and goes round as a rumor; then the hero pays it back, late (wave 1 step 2)
#   deal: the hero asks Lobart to sell him something; Lobart offers it for 50 gold (model scripted with
#       LLM=fake): Y waits without the gold; the hero declines (N), Lobart offers it for 40, then 30 after
#       haggling: the deal window shows the new version, Y waits right after the change, then the deal is
#       done and Lobart answers (docs/npc-tools.md, wave 1)
#   graph: the story graph decides which dialogs an npc offers (docs/story-graph.md, stage 3): switching a
#       dialog off, adding a condition and a dialog of the director's own, keeping that across save/load;
#       the game checks every script condition against its lifted form ("[story] check" in game.log)
# A new game first plays the intro video (~80 s), so a run takes ~2 min. SAVE=<file.sav> loads that save
# instead (copied into LOG_DIR as slot 1): no intro, no opening dialogs. The game runs in LOG_DIR,
# so its saves and settings stay there; memories go to LOG_DIR/memory.sqlite3.
#
#   SCENARIO=m3 LLM=fake ASR=voxtral GOTHIC_DIR=/path/to/Gothic\ II scripts/e2e.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GOTHIC_DIR="${GOTHIC_DIR:-/Volumes/shared/projects/Gothic II}"
GAME_BIN="${GAME_BIN:-$ROOT/OpenGothic/build/opengothic/Gothic2Notr}"
LOG_DIR="${LOG_DIR:-$(mktemp -d -t gothic-e2e)}"
TIMEOUT="${TIMEOUT:-240}"
SCENARIO="${SCENARIO:-m0}"
LLM="${LLM:-fake}"
ASR="${ASR:-fake}"
# own ports, so a server / ASR service running for a play session is left alone
PORT="${PORT:-47827}"
ASR_PORT="${ASR_PORT:-47829}"
TTS_PORT="${TTS_PORT:-47840}"
TTS="${TTS:-fake}"
VOICE_FILE="${VOICE_FILE:-$ROOT/asr-service/tests/audio/ru_1.wav}"
# the npc planner is off unless a decision service is given (DECIDER_URL=ws://<gpu-host>:47832)
BRAIN_ARGS=(--no-brain)
if [ -n "${DECIDER_URL:-}" ]; then
  BRAIN_ARGS=(--decider-url "$DECIDER_URL")
fi

ASR_PID=""
TTS_PID=""
VOICE_ARGS=(--no-voice)
TTS_ARGS=(--no-tts)
if [ "$SCENARIO" = "tts" ]; then
  (cd "$ROOT/tts-service" && exec uv run gothic-tts serve --port "$TTS_PORT" --provider "$TTS" --fallback none) >"$LOG_DIR/tts.log" 2>&1 &
  TTS_PID=$!
  TTS_ARGS=(--tts-url "ws://127.0.0.1:$TTS_PORT")
fi
if [ "$SCENARIO" = "m3" ] || [ "$SCENARIO" = "m5e" ]; then
  (cd "$ROOT/asr-service" && exec uv run gothic-asr serve --port "$ASR_PORT" --provider "$ASR" --fallback none) >"$LOG_DIR/asr.log" 2>&1 &
  ASR_PID=$!
  VOICE_ARGS=(--asr-url "ws://127.0.0.1:$ASR_PORT" --voice-file "$VOICE_FILE")
fi

(cd "$ROOT/server" && exec uv run gothic-ai serve --port "$PORT" --scenario "$SCENARIO" --llm "$LLM" --exit-after --no-console \
  --memory-db "$LOG_DIR/memory.sqlite3" --trace-dir "$LOG_DIR/traces" "${VOICE_ARGS[@]}" "${TTS_ARGS[@]}" "${BRAIN_ARGS[@]}") >"$LOG_DIR/server.log" 2>&1 &
SRV=$!
GAME=""
# `|| true`: with set -e a failing kill of an already finished process would change the exit code
trap 'if [ -n "$GAME" ]; then kill "$GAME" 2>/dev/null || true; fi; kill "$SRV" 2>/dev/null || true; if [ -n "$ASR_PID" ]; then kill "$ASR_PID" 2>/dev/null || true; fi; if [ -n "$TTS_PID" ]; then kill "$TTS_PID" 2>/dev/null || true; fi' EXIT

sleep 2
if ! kill -0 "$SRV" 2>/dev/null; then
  cat "$LOG_DIR/server.log"
  exit 1
fi

START_ARGS=(-nomenu)
if [ -n "${SAVE:-}" ]; then
  cp "$SAVE" "$LOG_DIR/save_slot_1.sav"
  START_ARGS=(-save 1)
fi
(cd "$LOG_DIR" && GOTHIC_AI_URL="ws://127.0.0.1:$PORT" exec "$GAME_BIN" -g "$GOTHIC_DIR" "${START_ARGS[@]}" -devmode) >"$LOG_DIR/game.log" 2>&1 &
GAME=$!

for _ in $(seq 1 "$TIMEOUT"); do
  kill -0 "$SRV" 2>/dev/null || break
  if ! kill -0 "$GAME" 2>/dev/null; then
    echo "game exited unexpectedly, see $LOG_DIR/game.log"
    exit 1
  fi
  sleep 1
done

if kill -0 "$SRV" 2>/dev/null; then
  echo "timeout after ${TIMEOUT}s, logs in $LOG_DIR"
  exit 1
fi
wait "$SRV" && rc=0 || rc=$?

grep -E "scenario|attached|detached|replied|note|calls|memory|remembers|saved|voice|timing|roster|turn_to|say_svm|play_ani|look_at|unknown_npc|planner|decision|incident|reflex|rejected_give|give|intent_|story|director|ERROR" "$LOG_DIR/server.log" || true
grep -E "^\[(ai|story)\]" "$LOG_DIR/game.log" || true
echo "logs: $LOG_DIR"
exit "$rc"
