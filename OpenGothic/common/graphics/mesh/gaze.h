#pragma once

#include <Tempest/Matrix4x4>
#include <Tempest/Vec>

#include <cstdint>
#include <vector>

class Skeleton;

// Gaze: a character looks at whom it talks to. Runs as a post-pass over the world-space bone
// matrices that Pose builds (after FootIk): the head turns first, the chest and the waist join for
// larger angles; the turn is smooth and limited, a partner behind the back is let go.
class Gaze final {
  public:
    struct Input final {
      bool          active  = false;
      Tempest::Vec3 target  = {};      // where to look: the eyes of the other one
      Tempest::Vec3 forward = {1,0,0}; // horizontal facing of the body
      bool          headOnly = false;  // the hero (the body is the player's), one walking
      uint64_t      dt      = 0;
      };

    // State for the test bench, degrees
    struct Probe final {
      bool          valid    = false;
      bool          active   = false;
      float         weight   = 0;
      float         yaw      = 0;  // smoothed turn
      float         pitch    = 0;
      float         yawGoal  = 0;  // where the target is, from the body facing
      float         pitchGoal= 0;
      float         error    = -1; // angle between the facing of the head and the target, -1 if unknown
      float         headYaw  = 0;  // parts of the turn: head, chest, waist
      float         chestYaw = 0;
      float         waistYaw = 0;
      Tempest::Vec3 head     = {};
      Tempest::Vec3 facing   = {};
      Tempest::Vec3 target   = {};
      };

    static bool    isEnabled();
    static void    setEnabled(bool e);

    void           setSkeleton(const Skeleton* sk);
    bool           isValid() const { return valid; }
    bool           isActive() const { return weight>0.f; }

    // Moves the smoothed state toward the input; true if the pose has to be rebuilt
    bool           update(const Input& in);
    void           apply(Tempest::Matrix4x4* tr, size_t numBones);
    void           probe(Probe& p) const;

    static Probe       playerProbe;   // filled every frame for the hero
    static Probe       subjectProbe;  // and for the npc the test bench watches
    static const void* probeNpc;

  private:
    size_t              waist = size_t(-1), chest = size_t(-1), head = size_t(-1);
    std::vector<size_t> waistTree, chestTree, headTree;
    bool                valid    = false;
    bool                headOnly = false;

    float               weight  = 0;
    float               yaw     = 0;  // radians, smoothed
    float               pitch   = 0;
    float               yawGoal = 0;
    float               pitchGoal = 0;
    Tempest::Vec3       target  = {};
    Tempest::Vec3       forward = {1,0,0};

    // from the last pose
    Tempest::Vec3       headPos = {};
    int                 faceAxis = -1;  // local axis of the head bone that points where the face does
    float               faceSign = 1;
    float               parts[3] = {};  // debug: head, chest, waist turn, radians
    float               error    = -1;
    Tempest::Vec3       facing   = {};
  };
