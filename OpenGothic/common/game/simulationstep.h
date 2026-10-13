#pragma once

#include <cstdint>
#include <functional>

class PlayerControl;

// Shared CPU phases for the rendered game and the headless native simulation. UI work may be
// inserted at its existing phase, without duplicating world, movement or animation advancement.
namespace SimulationStep {
  void worldPlayerStep(uint64_t dt, PlayerControl& player, const std::function<void()>& afterFocus = {});
  bool finishStep(uint64_t dt, PlayerControl& player, const std::function<void()>& updateCamera = {});
  // Normal player-follow camera without UI; preserves listener/perception range in a quiet arena.
  void observerCamera(uint64_t dt);
  }
