#pragma once

#include <Tempest/Matrix4x4>
#include <Tempest/Vec>

#include <cstdint>
#include <vector>

class Skeleton;

// BowDraw: the string drawn by hand (the manual aim, game/archery). The aim animations of G2 hold the bow
// drawn fully, the arrow's nock at the face; here the right hand goes forward to the string as it rests
// on the bow, as far as the string is not drawn: the arrow with it (it sits in the hand), the nock on the
// string's middle (the bow's morph). The hand keeps its turn, the arrow its direction.
// The reload of G2 lowers the bow, nocks at the hip and raises it again, the hand going through the head;
// here the upper body holds the aim (hold, before Aim) and the drawing hand goes round the head on its
// side to the quiver, where the animation takes the arrow, and from there to the string. apply runs after
// Aim
class BowDraw final {
  public:
    static constexpr const char* ReloadAnim = "T_BOWRELOAD";
    static constexpr float       GrabAt     = 0.12f; // of the reload: the animation has the arrow at the quiver
                                                     // over the shoulder (then it goes on down to the hip: not
                                                     // followed)...
    static constexpr float       NockAt     = 0.75f; // ...and the hand has brought it to the string by now
    static constexpr float       MinReach   = 0.25f; // seconds at least from the face to the quiver (round the
                                                     // head; the animation is faster than that, quick hands
                                                     // more so)

    struct Input final {
      bool     active = false; // aiming a bow by hand
      bool     ready  = false; // the bow holds still in the aim (not being raised, the next arrow nocked)
      float    draw   = 1;     // how far the string is drawn, 0..1
      float    span   = 0;     // cm the string goes from rest to drawn as the animation holds it (the bow's
                               // morph); 0: from the pose, the grip ahead of the nock
      float    reload = -1;    // reloading: how far, 0..1 (1: the animation is over, the bow comes to rest);
                               // -1 not
      float    reloadTime = 0; // seconds the reload's animation takes (at its speed)
      bool          arrow      = false; // an arrow in the drawing hand
      bool          hasString  = false; // the bow's string (its morph): its middle at rest, how it moves to
      Tempest::Vec3 stringRest = {};    // the full draw of the animation; in the space of the bow (the bow
      Tempest::Vec3 stringPull = {};    // hand's slot)
      bool               grabKnown = false; // the hand and the elbow as they take the arrow (the reload at
      Tempest::Matrix4x4 grabHand;          // GrabAt), relative to the chest: Pose fills them in
      Tempest::Matrix4x4 grabElbow;
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
      float fit    = -1; // cm from the string drawn by its morph to the arrow's line (drawn by the animation)
      float offset = -1; // cm from the string's middle at rest to that line
      float angle  = -1; // degrees between the way the string goes and the arrow
      float head   = 99; // cm from the head (a capsule) to the drawing hand, the forearm, the arrow: < 0 in it
      float fore   = 99;
      float arrow  = 99;
      bool  holding = false; // the reload: the upper body holds the aim
      float nock    = 0;     // the hand to the quiver (0..1), then to the string (1..2)
      Tempest::Vec3 handAt = {}; // the drawing hand as shown, relative to the body (the frame it is held in)
      };

    static bool    isEnabled();
    static void    setEnabled(bool e);

    void           setSkeleton(const Skeleton* sk);
    bool           isValid() const { return valid; }
    // the bones the grab pose is taken of: the chest, the hand, the elbow (the forearm)
    bool           grabNodes(size_t& chest, size_t& hand, size_t& elbow) const;
    // How far the bow's string is drawn as the hand holds it now, 0..1 of the animation's draw; -1: the
    // hand is not drawing it (the animation does: after the shot too)
    float          string() const { return weight<=0.f || follow>0.f ? -1.f : shown; }

    // Moves the smoothed state toward the input; true if the pose has to be rebuilt
    bool           update(const Input& in);
    // before Aim: in the reload the upper body as it held the aim
    void           hold(Tempest::Matrix4x4* tr, size_t numBones, const Tempest::Matrix4x4& object);
    // after Aim: the hand to the string; in the reload round the head to the quiver and back
    void           apply(Tempest::Matrix4x4* tr, size_t numBones);
    void           probe(Probe& p) const;

    static Probe   playerProbe;   // filled every frame for the hero

  private:
    void                implApply(Tempest::Matrix4x4* tr, size_t numBones);
    void                reload(Tempest::Matrix4x4* tr, size_t numBones, const Tempest::Vec3& nock,
                               const Tempest::Vec3& axis);
    void                measureHead(const Tempest::Matrix4x4* tr, size_t numBones);
    // moves the drawing hand to target, the elbow bent toward `bend` (as it is if none), the hand keeping
    // its turn
    bool                moveHand(Tempest::Matrix4x4* tr, const Tempest::Vec3& target, const Tempest::Vec3* bend = nullptr);

    size_t              upper = size_t(-1), fore = size_t(-1), hand = size_t(-1), slot = size_t(-1), bowHand = size_t(-1);
    size_t              head = size_t(-1), crown = size_t(-1), chest = size_t(-1);
    std::vector<size_t> upperTree, foreTree, handTree;
    bool                valid  = false;

    float               weight = 0;
    float               draw   = 1;
    float               given  = 0;  // the span from the input
    bool                hasString  = false;
    Tempest::Vec3       stringRest = {};
    Tempest::Vec3       stringPull = {};
    float               fit = -1, offset = -1, angle = -1;
    float               gapHand = 99, gapFore = 99, gapArrow = 99;
    bool                active  = false;
    bool                inHand  = false; // an arrow in the drawing hand
    bool                wasReady = false;
    float               followT  = 1;  // seconds since the shot (the follow through)
    float               follow   = 0;  // cm the hand is back past the face now
    float               shotAt   = 0;  // cm the hand was forward of the face as the string went (a short draw)

    // the upper body held in the reload: the bones from the spine up, relative to the body's facing at the
    // pelvis (bodyRoot): the reload turns the hips, the upper body holds the aim
    size_t                          bodyRoot = size_t(-1);
    std::vector<size_t>             bodyTree;
    std::vector<Tempest::Matrix4x4> heldRel;
    bool                            held     = false; // the aim pose was taken
    bool                            capture  = false; // take it now
    bool                            holding  = false;
    float                           toQuiver = 0; // the hand from the face to the quiver, 0..1...
    float                           toString = 0; // ...and from there to the string
    bool                            grabKnown = false;
    Tempest::Matrix4x4              grabHand, grabElbow;
    Tempest::Matrix4x4              frameNow;
    Tempest::Vec3                   handAt;

    // from the last pose
    float               span   = 0;
    float               travel = 0;
    float               miss   = 0;
    float               shown  = 1;  // the string drawn as shown, 0..1
  };
