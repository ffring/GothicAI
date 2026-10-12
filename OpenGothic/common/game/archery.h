#pragma once

#include "graphics/mesh/hitzones.h"

class Npc;
class Item;

// Archery: how well one handles a bow (a crossbow) in the manual aim. The skill makes the aim steady,
// strength over what the weapon needs holds it drawn, dexterity draws it fast; the weapon makes the arrow
// fast. The physics decides the hit, no dice: these numbers are what to balance (the test bench)
namespace Archery {
  struct Stats final {
    bool  crossbow  = false;
    float skill     = 0; // 0..1: the talent (the hit chance of the scripts)
    float strength  = 1; // strength over what the weapon needs: below 1 too weak for it
    float sway      = 0; // radians, RMS of the sway at full draw: breathing, the arms
    float settle    = 0; // seconds: a turn, a shot shake the aim that long
    float breath    = 0; // seconds the breath can be held
    float drawTime  = 0; // seconds to the full draw, 0: a crossbow, cocked
    float holdLimit = 0; // seconds at full draw before the arms tremble
    float speed     = 0; // cm/ms: the arrow at full draw
    float cone      = 0; // radians: the release throws the arrow off by up to that
    float reload    = 1; // the reload plays that many times as fast (dexterity; a crossbow: 1 yet)
    };

  // the animation of the reload: the next arrow, the next bolt
  static constexpr const char* BowReload  = "T_BOWRELOAD";
  static constexpr const char* CBowReload = "T_CBOWRELOAD";

  static constexpr float MinDraw = 0.3f; // drawn less: the arrow is not shot, the string let down
  static constexpr float GiveUp  = 4.f;  // seconds trembling past the hold limit: the arms let it down

  // Manual aim on (GAME/manualAim in Gothic.ini, on by default; Marvin: toggle manualaim): the hero aims a
  // bow by hand
  bool  isEnabled();
  void  setEnabled(bool e);

  Stats stats(Npc& npc, const Item* weapon);
  // the arrow's speed and damage at a part of the full draw
  float speedAt(const Stats& s, float draw);
  float damageAt(float draw);
  float zoneDamage(HitZones::Zone z);
  }
