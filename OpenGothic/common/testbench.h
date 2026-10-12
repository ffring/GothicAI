#pragma once

#include <cstdint>

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
  // The captured frame: draws the overlay and writes it to the video
  void     capture(Tempest::Pixmap& frame);
  // An event the game reports (an arrow hit): into the bench log, if a scenario runs
  void     note(const char* text);
  }
