#pragma once

#include <cstdint>
#include <string>
#include <string_view>

// Bench-only: a recorded lockstep arena episode replayed in the rendered game, to film a policy's
// duel. The headless runner records the primary's accepted commands (body-control-events.jsonl),
// in a self-play duel (arena opponent_owned) the opponent's too. The replay configures the arena
// from the same reset, lets the bench set it up at the same line, acquires the primary (and the
// owned opponent) as the lockstep driver did and issues each command after the same number of
// steps, through the ordinary body authority. It writes the runner's journals next to the bench
// output, so the replay is checked against the recording (scripts/body_schedule_equivalence.py)
// rather than trusted. File format: scripts/body_replay_film.py.
class BodyReplay final {
  public:
    // `bodyreplay <file>`: configures the arena and opens the journals in `outDir`; the bench then
    // runs the arena setup itself, as `bodyarena`. `tailMs` of game time are kept after the outcome.
    static void load(const std::string& path, const std::string& outDir, uint64_t tailMs);
    // After each step, where the runner serves its driver: the start, heartbeats, the commands.
    static void postStep();
    static bool active();
    static void stop(std::string_view reason);
  };
