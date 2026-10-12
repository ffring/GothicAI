#pragma once

#include <Tempest/Matrix4x4>
#include <Tempest/Vec>

#include <cstdint>
#include <vector>

class Skeleton;

// BowDraw: the string drawn by hand (the manual aim, game/archery). The aim animations of G2 hold the bow
// drawn fully, the arrow's nock at the face; here the right hand goes forward along the arrow, to the
// string as it rests on the bow, as far as the string is not drawn: the arrow with it (it sits in the
// hand). The hand keeps its turn, the arrow its line. Runs as a post-pass after Aim
class BowDraw final {
  public:
    struct Input final {
      bool     active = false; // aiming a bow by hand
      bool     ready  = false; // the bow holds still in the aim (not being raised, the next arrow nocked)
      float    draw   = 1;     // how far the string is drawn, 0..1
      float    span   = 0;     // cm the string goes from rest to drawn as the animation holds it (the bow's
                               // morph); 0: from the pose, the grip ahead of the nock
      float    nock   = -1;    // the next arrow brought to the string (the end of the reload), 0..1; -1 not
      uint64_t dt     = 0;
      };

    // State for the test bench
    struct Probe final {
      bool  valid  = false;
      float weight = 0;
      float draw   = 0;
      float span   = 0;  // cm: from the nock at full draw to the string at rest
      float travel = 0;  // cm the hand goes forward from the animation now (not further than the arm reaches)
      float miss   = 0;  // cm between the hand and where it has to go (the arm does not reach)
      float follow = 0;  // cm back past the face after the shot
      };

    static bool    isEnabled();
    static void    setEnabled(bool e);

    void           setSkeleton(const Skeleton* sk);
    bool           isValid() const { return valid; }
    // How far the bow's string is drawn as the hand holds it now, 0..1 of the animation's draw; -1: the
    // hand is not drawing it (the animation does: after the shot too)
    float          string() const { return weight<=0.f || follow>0.f ? -1.f : (span>0.f ? 1.f-travel/span : 1.f-(1.f-draw)*weight); }

    // Moves the smoothed state toward the input; true if the pose has to be rebuilt
    bool           update(const Input& in);
    void           apply(Tempest::Matrix4x4* tr, size_t numBones);
    void           probe(Probe& p) const;

    static Probe   playerProbe;   // filled every frame for the hero

  private:
    size_t              upper = size_t(-1), fore = size_t(-1), hand = size_t(-1), slot = size_t(-1), bowHand = size_t(-1);
    std::vector<size_t> upperTree, foreTree, handTree;
    bool                valid  = false;

    float               weight = 0;
    float               draw   = 1;
    float               given  = 0;  // the span from the input
    bool                wasReady = false;
    float               followT  = 1;  // seconds since the shot (the follow through)
    float               follow   = 0;  // cm the hand is back past the face now

    // from the last pose
    float               span   = 0;
    float               travel = 0;
    float               miss   = 0;
  };
