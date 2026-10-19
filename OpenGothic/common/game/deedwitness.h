#pragma once

// Shared, side-effect-free admission for the bridge's witness snapshot. Native
// perception callbacks are never invoked to discover an NPC deed's witnesses.
struct DeedWitness final {
  bool culprit = false;
  bool victim = false;
  bool player = false;
  bool down = false;
  bool lying = false;
  bool sleeping = false;
  bool simulated = true;
  bool stateStarted = true;
  bool foreignFight = false;
  float distanceSquared = 0;
  float range = 0;

  constexpr bool admitted() const {
    return !culprit && !victim && !player && !down && !lying && !sleeping &&
           simulated && stateStarted && !foreignFight && distanceSquared<=range*range;
    }
  };
