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
      float         lean    = 0;       // radians: bend forward from the hips over an edge (leanFor), 0 if
                                       // there is no room in front of the head
      uint64_t      dt      = 0;
      };

    // At an edge one leans over it to shoot who is below: steeper down than LeanFrom the trunk bends
    // forward from the hips, up to LeanMax at LeanFull; the head goes LeanReach forward and LeanDrop down
    // then. The focus looks from there too (Npc::canSeeNpcOverEdge)
    static constexpr float LeanFrom  = 55.f; // degrees down
    static constexpr float LeanFull  = 80.f;
    static constexpr float LeanMax   = 55.f;
    static constexpr float LeanReach = 58.f; // cm: the head 70 cm above the hips, bent 55 degrees
    static constexpr float LeanDrop  = 28.f;
    static float           leanFor(float down); // the lean for the angle down, radians

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
      float lean      = 0;  // over an edge
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

    size_t              spine = size_t(-1), waist = size_t(-1), chest = size_t(-1), slot = size_t(-1);
    std::vector<size_t> spineTree, waistTree, chestTree;
    bool                valid  = false;

    bool                arrow   = false;
    bool                ready   = false; // the turn was measured once since the bow went up
    bool                steady  = false;
    float               weight  = 0;
    float               leanAmt = 0;  // radians, smoothed
    Tempest::Vec3       wantB   = {}; // where the shot has to go, body's frame, smoothed
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
