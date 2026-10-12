#pragma once

#include <Tempest/Matrix4x4>
#include <Tempest/Vec>

#include <cstdint>
#include <vector>

class Skeleton;

// Aim: an archer (a crossbowman, a mage) turns the upper body so the shot leaves toward the target,
// above or below too; the animations aim straight ahead. Runs as a post-pass over the world-space
// bone matrices that Pose builds (after FootIk, before Gaze): the waist and the chest turn and bend.
class Aim final {
  public:
    struct Input final {
      bool          active  = false;
      bool          arrow   = false;   // a bow, a crossbow: the animation aims where the arrow in the right hand
                                       // lies (an aim up, down is another pose); a spell: straight ahead
      Tempest::Vec3 dir     = {};      // where the shot has to leave, world
      Tempest::Vec3 forward = {1,0,0}; // horizontal facing of the body
      uint64_t      dt      = 0;
      };

    // State for the test bench, degrees
    struct Probe final {
      bool  valid     = false;
      bool  active    = false;
      bool  steady    = false; // the bow holds still (not raised, not reloading): the turn is measured
      float weight    = 0;
      float yaw       = 0;  // where the shot has to go, from the facing
      float pitch     = 0;
      float animYaw   = 0;  // where the animation aims
      float animPitch = 0;
      float turn      = 0;  // the turn and the bend of the upper body
      float bend      = 0;
      float error     = -1; // between where it aims after the turn and where the shot has to go
      };

    static bool    isEnabled();
    static void    setEnabled(bool e);

    void           setSkeleton(const Skeleton* sk);
    bool           isValid() const { return valid; }

    // Moves the smoothed state toward the input; true if the pose has to be rebuilt
    bool           update(const Input& in);
    void           apply(Tempest::Matrix4x4* tr, size_t numBones);
    void           probe(Probe& p) const;

    static Probe       playerProbe;   // filled every frame for the hero
    static Probe       subjectProbe;  // and for the npc the test bench watches
    static const void* probeNpc;

  private:
    Tempest::Vec3       arrowOf(const Tempest::Matrix4x4* tr) const;

    size_t              waist = size_t(-1), chest = size_t(-1), slot = size_t(-1);
    std::vector<size_t> waistTree, chestTree;
    bool                valid  = false;

    bool                arrow   = false;
    bool                ready   = false; // the turn was measured once since the bow went up
    bool                steady  = false;
    float               weight  = 0;
    float               yaw     = 0;  // radians, smoothed, from the facing
    float               pitch   = 0;
    uint8_t             steadyFrames = 0;
    Tempest::Vec3       rest    = {}; // where the animation aims while the bow holds still, body's frame
    Tempest::Vec3       goal    = {}; // turn of the upper body from the arrow to the target: axis*angle,
    Tempest::Vec3       rot     = {}; // in the body's frame (forward, up, side); and the one smoothed
    Tempest::Vec3       forward = {1,0,0};

    // from the last pose
    bool                measured  = false;
    Tempest::Vec3       animArrow = {}; // as animated
    Tempest::Vec3       prevArrow = {}; // the frame before
    float               error = -1;
  };
