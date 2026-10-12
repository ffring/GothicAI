#pragma once

#include <cstdint>
#include <functional>
#include <string_view>

namespace Tempest {
class Pixmap;
}
class PlayerControl;

// Test bench for developing game mechanics: runs a scenario file inside the game with a fixed time step,
// drives the player and the camera, records video with a debug overlay and per-frame metrics.
// Inert unless OPENGOTHIC_BENCH is set. Usage: scripts/bench.sh, see scripts/bench/README.md.
namespace TestBench {
  bool     isActive();
  // Fixed time step while a scenario runs: the video and the metrics do not depend on the frame rate
  uint64_t frameDt(uint64_t dt);
  // Runs the scenario after the world update; true if this frame has to be captured
  bool     tick(uint64_t dt, PlayerControl& player);
  // The captured frame: draws the overlay and writes it to the video; uiScale: the interface's (the
  // crosshair is drawn that big)
  void     capture(Tempest::Pixmap& frame, float uiScale);
  // An event the game reports (an arrow hit): into the bench log, if a scenario runs
  void     note(const char* text);
  // Filming a short (OPENGOTHIC_BENCH_REALTIME): in step with the wall clock, the window is not drawn
  bool     filming();
  // `ui on`: the captured frame has the interface on top, as the player sees it (subtitles, the deal window)
  bool     withUi();

  // What the AI bridge does, for scenarios that talk (`ptt`, `waitreply`; shorts/): texts in UTF-8.
  // An npc line starts (`ms`: its length as known now), an npc voice has played out, the hero's phrase was
  // recognized (`to`: whom the server says it was said to)
  void     aiLine(std::string_view npc, std::string_view name, std::string_view text, uint64_t ms);
  void     aiVoiceEnd(std::string_view npc, uint64_t ms);
  void     aiHeard(std::string_view text, std::string_view to);
  // set by the bridge: an npc of the AI speaks now or has a line waiting for its turn
  extern std::function<bool()> aiBusy;
  }
