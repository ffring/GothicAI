#pragma once

#include <Tempest/Vec>

#include <cstdint>

#include "game/archery.h"

// Archer: a bow drawn by hand (the manual aim). The string is drawn while the button is held and the bow
// holds still, held at full draw until the arms tire, let go with the button. The aim sways meanwhile:
// less with skill, more when too weak for the bow, tired, out of breath, right after a turn or a shot.
// The hero's input drives it (PlayerControl), npc archers later
class Archer final {
  public:
    enum Stage : uint8_t {
      Lowered,   // the bow is not raised
      Raising,   // being raised, an arrow nocked (the animation): no draw yet
      Nocked,    // ready, the string not drawn
      Drawing,
      Held,      // at full draw
      Reloading, // shot: the next arrow
      };

    struct Input final {
      bool     raised = false; // the bow is up (the stance)
      bool     ready  = false; // the bow holds still in the aim: an arrow on the string
      bool     draw   = false; // the button is held
      bool     breath = false; // the breath is held
      float    turn   = 0;     // radians the aim turned since the last tick
      uint64_t dt     = 0;
      };

    // State for the test bench
    struct Probe final {
      bool  valid    = false;
      Stage stage    = Lowered;
      float draw     = 0;
      float hold     = 0;  // seconds at full draw
      float fatigue  = 0;
      float breath   = 0;  // seconds of breath left
      bool  holding  = false;
      bool  short_   = false; // out of breath: recovering
      float disturb  = 0;
      float amp      = 0;  // degrees: the sway's scale now
      float yaw      = 0;  // degrees: the sway
      float pitch    = 0;
      float strength = 1;
      float skill    = 0;
      };

    void          reset(uint32_t seed);
    // Moves on by in.dt; true: let the arrow go now, draw() far drawn
    bool          tick(const Input& in, const Archery::Stats& st);
    // After a release: the arrow left (reload) or could not (the string let down)
    void          onShot(bool shot);

    Stage         stage() const { return stg; }
    float         draw()  const { return drawAmt; }
    bool          isRaised() const { return stg!=Lowered; }
    // Where the aim sways off to: yaw (about up), pitch (+ up), radians
    Tempest::Vec2 sway()  const { return sw; }

    void          probe(Probe& p) const;
    static Probe  playerProbe;

  private:
    Stage         stg       = Lowered;
    float         drawAmt   = 0;
    float         hold      = 0;
    float         fatigue   = 0;
    float         breath    = -1;  // seconds left, -1: full (not known yet)
    float         recover   = 0;   // seconds out of breath yet
    bool          holding   = false;
    float         calm      = 1;   // the sway's scale by the breath, smoothed
    float         disturb   = 0;
    float         amp       = 0;
    bool          repress   = false; // the arms let the string down: the button has to be pressed again
    bool          unready   = false; // reloading: the bow moved (the next arrow is being nocked)
    float         stageTime = 0;
    double        clock     = 0;
    uint32_t      seed      = 0;
    float         strength  = 1;
    float         skill     = 0;
    Tempest::Vec2 sw;

    float         wander(float t, uint32_t axis) const;
    float         tremor(float t, uint32_t axis) const;
    void          setStage(Stage s);
  };
