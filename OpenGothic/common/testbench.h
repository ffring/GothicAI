#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <vector>

namespace Tempest {
class Pixmap;
}
class PlayerControl;
class Npc;

// Test bench for developing game mechanics: runs a scenario file inside the game with a fixed time step,
// drives the player and the camera, records video with a debug overlay and per-frame metrics.
// Inert unless OPENGOTHIC_BENCH is set. Usage: scripts/bench.sh, see scripts/bench/README.md.
namespace TestBench {
  bool     isActive();
  // Reinitialize the harness for a fresh runner generation. The caller owns world teardown/load.
  void     configure(std::string scenario, std::string output, int64_t seedOverride = -1);
  // Body runner --fork-prefix: the template stops its scenario before the first line starting with
  // `command` (held), flushes its output and forks. The fork writes to its own output and resumes
  // that very line within the same step, as if the scenario had never stopped.
  void     holdBefore(std::string command);
  bool     held();
  void     flushOutput();
  void     retarget(std::string output);
  void     resumeHeld();
  bool     done();
  uint64_t stepCount();
  uint64_t nativeChoices();
  // Explicit scenario participants for a passive body_watch, including duplicate script symbols.
  std::vector<Npc*> bodyObservationActors();
  // Fixed time step while a scenario runs: the video and the metrics do not depend on the frame rate
  uint64_t frameDt(uint64_t dt);
  // Runs the scenario after the world update; true if this frame has to be captured
  bool     tick(uint64_t dt, PlayerControl& player);
  // The captured frame: draws the overlay and writes it to the video; uiScale: the interface's (the
  // crosshair is drawn that big)
  void     capture(Tempest::Pixmap& frame, float uiScale);
  // An event the game reports (an arrow hit): into the bench log, if a scenario runs; also a `note` of
  // events.jsonl (with the time and the frame)
  void     note(const char* text);
  // Native melee baseline telemetry. Opt in with OPENGOTHIC_BODY_TRACE=1; no events outside an active bench.
  bool     bodyTracing();
  void     bodyEvent(const Npc& actor, const Npc* target, std::string_view phase, uint64_t gameTime,
                     const std::string& fields = {}, std::string_view animation = {});
  // Trace IDs describe an NPC object's lifetime; a new object never inherits a destroyed one's ID.
  void     bodyForget(const Npc* npc);
  std::string bodyJson(std::string_view text);
  // Benchmark-only 1H/2H block clip random branch. The solver has already drawn fallback once.
  // Default/inactive benches return it unchanged; replay accounting observes native_attempt.
  int      bodyBlockVariant(const Npc& npc, int fallback);
  // who it is, for the notes: its script symbol (the names are in the game's code page), "none"
  std::string_view who(const Npc* npc);
  // An npc says a line of the game or an SVM (AI_Output, AI_OutputSvm): who, to whom, how long (`ms`, 0 if
  // the engine did not look it up: far away)
  void     npcOutput(const Npc& npc, std::string_view name, const Npc* target, uint64_t ms);
  // Filming a short (OPENGOTHIC_BENCH_REALTIME): in step with the wall clock, the window is not drawn
  bool     filming();
  // `ui on`: the captured frame has the interface on top, as the player sees it (subtitles, the deal window)
  bool     withUi();

  // What the AI bridge does, for scenarios that talk (`ptt`, `waitreply`; shorts/): texts in UTF-8.
  // An npc line starts (`ms`: its length as known now; `to`: the npc it is said to, empty for the hero;
  // `filler`: a sound said at once while the model thinks, "Хм..."), an npc voice has played out, the hero's
  // phrase was recognized (`to`: whom the server says it was said to)
  void     aiLine(std::string_view npc, std::string_view name, std::string_view text, uint64_t ms,
                  std::string_view to, bool filler = false);
  void     aiVoiceEnd(std::string_view npc, uint64_t ms);
  void     aiHeard(std::string_view text, std::string_view to);
  // set by the bridge: an npc of the AI speaks now or has a line waiting for its turn
  extern std::function<bool()> aiBusy;
  // set by the bridge: whether a thing of it has got that far (`waitai necro risen`: the necromancer's answer)
  extern std::function<bool(std::string_view key, std::string_view stage)> aiReached;
  }
