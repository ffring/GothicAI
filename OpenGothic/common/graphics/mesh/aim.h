#pragma once

#include <Tempest/Matrix4x4>
#include <Tempest/Vec>

#include <cstdint>
#include <vector>

class Skeleton;
class Npc;

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
      float         side    = 0;       // radians: lean out sideways from the hips (+ right), round a corner
      float         follow  = 1;       // the upper body follows the aim that much as fast (crouched: slower)
      Tempest::Vec2 sway    = {};      // radians, the manual aim (Archer): yaw, pitch (+ up) off where the
                                       // shot has to go, as it is: the chest
      Tempest::Vec2 tremor  = {};      // and the arms trembling (not smoothed: fast): the arms, not the head
      bool          manual  = false;   // aimed by hand: the upper body follows the mouse faster
      bool          reload  = false;   // the reload plays: the arrow is not on the string, not measured (the
                                       // hand holds still at the quiver for a while)
      size_t        slot    = size_t(-1); // the bone the arrow lies along, if not the right hand's slot (a bolt
                                          // in the crossbow: the left hand's); under the chest
      uint64_t      dt      = 0;
      };

    // The angle rule remains the fallback for manual aim and the inexpensive rule for NPCs.
    // The hero normally uses leanNeeded: the smallest hip bend that clears the nearby edge.
    static constexpr float LeanFrom  = 55.f; // degrees down
    static constexpr float LeanFull  = 80.f;
    static constexpr float LeanMax   = 70.f;
    static constexpr float NpcLeanMax= 55.f;
    static constexpr float LeanStart = 30.f;
    static constexpr float PelvisBack= 12.f; // cm, with the feet held by FootIk
    // Leaning out round a corner: the trunk tilts sideways from the hips up to SideMax, the head goes
    // SideReach to the side then
    static constexpr float SideMax   = 22.f; // degrees
    static constexpr float SideReach = 30.f; // cm
    static float           leanFor(float down); // the lean for the angle down, radians
    static float           leanForNpc(float down);

    struct LeanResult final {
      float angle   = 0; // radians, the smallest safe hip bend
      bool  visible = false;
      bool  room    = true;
      };
    LeanResult     leanNeeded(const Npc& npc, const Tempest::Vec3& target, float down, bool manual) const;
    // Predicts the head bone from the last unmodified animated joints, using the same hip,
    // waist and chest rotations as apply. origin is at the feet; angle is in radians.
    Tempest::Vec3  headAt(const Tempest::Vec3& origin, const Tempest::Vec3& forward,
                         const Tempest::Vec3& dir, float angle) const;
    Tempest::Vec3  pelvisOffset() const;

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
      float side      = 0;  // out sideways (+ right)
      float error     = -1; // between where it aims after the turn and where the shot has to go
      };

    static bool    isEnabled();
    static void    setEnabled(bool e);

    void           setSkeleton(const Skeleton* sk);
    bool           isValid() const { return valid; }
    // The bow holds still in the aim (an arrow on the string), the upper body turned to the target
    bool           isSteady() const { return steady && ready && weight>=1.f; }
    // Degrees between where the arrow points (before the sway) and where the shot has to go, -1 not aiming
    float          trackError() const { return weight>=1.f ? error : -1.f; }

    // Moves the smoothed state toward the input; true if the pose has to be rebuilt
    bool           update(const Input& in);
    void           apply(Tempest::Matrix4x4* tr, size_t numBones, const Tempest::Vec3& origin,
                         const Tempest::Vec3& facing);
    void           probe(Probe& p) const;

    static Probe       playerProbe;   // filled every frame for the hero
    static Probe       subjectProbe;  // and for the npc the test bench watches
    static const void* probeNpc;

  private:
    Tempest::Vec3       arrowOf(const Tempest::Matrix4x4* tr) const;

    size_t              spine = size_t(-1), waist = size_t(-1), chest = size_t(-1), slot = size_t(-1), head = size_t(-1);
    size_t              arrowSlot = size_t(-1); // where the arrow lies now: slot, or the input's
    std::vector<size_t> spineTree, waistTree, chestTree;
    std::vector<size_t> armsTree; // both arms from the clavicles: they tremble
    bool                valid  = false;

    bool                arrow   = false;
    bool                ready   = false; // the turn was measured once since the bow went up
    bool                steady  = false;
    float               weight  = 0;
    float               leanAmt = 0;  // radians, smoothed
    float               sideAmt = 0;  // radians, leaning out sideways, smoothed
    Tempest::Vec2       sway    = {};
    Tempest::Vec2       tremor  = {};
    Tempest::Vec3       wantB   = {}; // where the shot has to go, body's frame, smoothed
    uint8_t             steadyFrames = 0;
    Tempest::Vec3       rest    = {}; // where the animation aims while the bow holds still, body's frame
    Tempest::Vec3       goal    = {}; // turn of the upper body from the arrow to the target: axis*angle,
    Tempest::Vec3       rot     = {}; // in the body's frame (forward, up, side); and the one smoothed
    Tempest::Vec3       forward = {1,0,0};

    // Joint positions before Aim, relative to the feet, in the body's frame. Capturing before
    // the rotations prevents the next frame's visibility test from chasing its own leaned head.
    Tempest::Vec3       baseSpine = {}, baseWaist = {}, baseChest = {}, baseHead = {};

    // from the last pose
    bool                measured  = false;
    Tempest::Vec3       animArrow = {}; // as animated
    Tempest::Vec3       prevArrow = {}; // the frame before
    float               error = -1;
  };
