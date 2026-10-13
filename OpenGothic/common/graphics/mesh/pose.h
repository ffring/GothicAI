#pragma once

#include <Tempest/Matrix4x4>
#include <vector>

#include "game/constants.h"
#include "animation.h"
#include "animblend.h"
#include "animationsolver.h"
#include "footik.h"
#include "gaze.h"
#include "lean.h"
#include "aim.h"
#include "aimwalk.h"
#include "bowdraw.h"
#include "flinch.h"
#include "resources.h"

class Skeleton;
class Serialize;
class AnimationSolver;
class Npc;

class Pose final {
  public:
    Pose();

    enum Flags {
      NoFlags       = 0,
      NoTranslation = 1, // useful for mobsi
      };

    enum StartHint : uint8_t {
      NoHint     = 0x0,
      Force      = 0x1,
      };

    static uint8_t     calcAniComb(const Tempest::Vec3& dpos, float rotation);
    static uint8_t     calcAniCombVert(const Tempest::Vec3& dpos);

    void               save(Serialize& fout);
    void               load(Serialize& fin, const AnimationSolver &solver);

    void               setFlags(Flags f);
    BodyState          bodyState() const;
    bool               hasState(BodyState s) const;
    bool               hasStateFlag(BodyState f) const;
    void               setSkeleton(const Skeleton *sk);
    bool               startAnim(const AnimationSolver &solver, const Animation::Sequence* sq,
                                 uint8_t comb, BodyState bs,
                                 StartHint hint, uint64_t tickCount);
    bool               stopAnim(std::string_view name);
    bool               stopWalkAnim();
    void               interrupt();
    void               stopAllAnim();

    void               setObjectMatrix(const Tempest::Matrix4x4& obj, bool sync);
    bool               update(uint64_t tickCount, bool force);

    void               processLayers(AnimationSolver &solver, uint64_t tickCount);
    bool               processEvents(uint64_t& barrier, uint64_t now, Animation::EvCount &ev) const;

    Tempest::Vec3      animMoveSpeed(uint64_t tickCount, uint64_t dt) const;
    void               processSfx(Npc &npc, uint64_t tickCount);
    void               processSfx(Interactive& mob, uint64_t tickCount);
    void               processPfx(MdlVisual& visual, World& world, uint64_t tickCount);
    bool               isDefParWindow(uint64_t tickCount) const;
    bool               isDefWindow(uint64_t tickCount) const;
    bool               isDefence(uint64_t tickCount) const;
    bool               isJumpBack(uint64_t tickCount) const;
    bool               isJumpAnim() const;
    bool               isFlyAnim() const;
    bool               isStanding() const;
    bool               isPrehit(uint64_t now) const;
    bool               isAttackAnim() const;
    bool               isIdle() const;
    bool               isInAnim(std::string_view           sq) const;
    bool               isInAnim(const Animation::Sequence* sq) const;
    std::string        animNames() const; // the playing layers, for debugging
    bool               hasAnim() const;
    uint64_t           animationTotalTime() const;
    uint64_t           atkTotalTime() const;

    auto               continueCombo(const AnimationSolver &solver, const Animation::Sequence *sq, BodyState bs, uint64_t tickCount) -> const Animation::Sequence*;
    uint16_t           comboLength() const;

    float              translateY() const { return trY; }
    auto               rootNode() const -> const Tempest::Matrix4x4;
    auto               rootBone() const -> const Tempest::Matrix4x4;
    auto               bone(size_t id) const -> const Tempest::Matrix4x4&;
    size_t             boneCount() const;
    size_t             findNode(std::string_view b) const;

    void               setHeadRotation(float dx, float dz);
    Tempest::Vec2      headRotation() const;
    FootIk&            footIk() { return ik; }
    const FootIk&      footIk() const { return ik; }
    bool               updateFootIk(const FootIk::Input& in);
    Gaze&              gazeIk() { return gaze; }
    const Gaze&        gazeIk() const { return gaze; }
    bool               updateGaze(const Gaze::Input& in);
    Lean&              leanIk() { return lean; }
    Aim&               aimIk() { return aim; }
    const Aim&         aimIk() const { return aim; }
    bool               updateAim(const Aim::Input& in);
    // How far the animation of that name has played, 0..1; -1: it does not play
    float              animProgress(std::string_view name, uint64_t tickCount) const;
    // Seconds the animation of that name takes at its speed; 0: it does not play
    float              animDuration(std::string_view name) const;
    // The animation of that name as it is at `at` (0..1 of it): bone `to` relative to its ancestor `from`;
    // false: it does not play
    bool               animRelative(std::string_view name, float at, size_t from, size_t to, Tempest::Matrix4x4& out) const;
    // The animation of that name plays k times as fast, from its next start on (1: as it is)
    void               setAnimSpeed(std::string_view name, float k);
    BowDraw&           bowDrawIk() { return bowDraw; }
    AimWalk&           aimWalk() { return walk; }
    bool               updateAimWalk(const AimWalk::Input& in);
    bool               updateBowDraw(const BowDraw::Input& in);
    Flinch&            flinchIk() { return flinch; }
    bool               updateFlinch(const Flinch::Input& in);
    bool               updateLean(const Lean::Input& in);
    void               setAnimRotate(const AnimationSolver &solver, Npc &npc, WeaponState fightMode, AnimationSolver::TurnType turn, int dir);
    auto               setAnimItem(const AnimationSolver &solver, Npc &npc, std::string_view scheme, int state) -> const Animation::Sequence*;
    bool               stopItemStateAnim(const AnimationSolver &solver, uint64_t tickCount);

    const Tempest::Matrix4x4* transform() const;

    // The animated pose for the test bench: joints in the model space, before the post-passes
    // (lean, foot IK, gaze), and what plays
    struct Probe final {
      bool            valid    = false;
      const Skeleton* skeleton = nullptr;
      size_t          numBones = 0;
      Tempest::Vec3   joint[Resources::MAX_NUM_SKELETAL_NODES] = {};
      std::string     layers;  // the playing animations, 'name:blendIn/blendOut' (ms), '+' between layers
      AnimBlend::Probe blend;
      };
    void               probe(Probe& p);
    void               setBlendLog(bool l) { blend.setLog(l); }
    static Probe       playerProbe;   // filled every frame for the hero, on the test bench
    static Probe       subjectProbe;  // and for the npc the test bench watches
    static const void* probeNpc;

  private:
    // S_Old is only in old saves: AnimBlend does the blending now
    enum SampleStatus : uint8_t {
      S_None  = 0,
      S_Old   = 1,
      S_Valid = 2,
      };

    struct Layer final {
      const Animation::Sequence* seq      = nullptr;
      uint64_t                   sAnim    = 0;
      float                      speed    = 1;  // plays that many times as fast, from its start (setAnimSpeed)
      uint8_t                    comb     = 0;
      BodyState                  bs       = BS_NONE;
      };

    // the layer's own clock at the time t: its animation plays `speed` times as fast from its start
    static uint64_t layerTime(const Layer& l, uint64_t t);
    float           speedOf(const Animation::Sequence* seq) const;

    struct ComboState {
      uint16_t bits = 0;
      uint16_t len()     const { return bits & 0x7FFF; }
      void     incLen()        { bits = (bits+1)&0x7FFF; };
      bool     isBreak() const { return bits & 0x8000; }
      void     setBreak()      { bits |=0x8000; }
      };

    auto mkBaseTranslation() -> Tempest::Vec3;
    void mkSkeleton(const Tempest::Matrix4x4 &mt);
    void implMkSkeleton(const Tempest::Matrix4x4 &mt);
    void implMkSkeleton(const Tempest::Matrix4x4 &mt, size_t parent);

    bool updateFrame(const Animation::Sequence &s, BodyState bs, uint64_t sTime, uint64_t now);

    const Animation::Sequence* solveNext(const AnimationSolver& solver, const Layer& lay);

    void addLayer(const Animation::Sequence* seq, BodyState bs, uint8_t comb, uint64_t tickCount);
    void onAddLayer   (Layer& l);
    void onRemoveLayer(const Layer& l);

    static bool hasLayerEvents(const Layer& l);

    template<class T,class F>
    void removeIf(T& t,F f);

    const Skeleton*                 skeleton=nullptr;
    std::vector<Layer>              lay;
    const Animation::Sequence*      rotation=nullptr;
    int32_t                         itemUseSt     = 0;
    int32_t                         itemUseDestSt = 0;
    float                           trY=0;
    Flags                           flag=NoFlags;
    uint64_t                        lastUpdate=0;
    ComboState                      combo;
    bool                            needToUpdate = true;
    uint8_t                         hasEvents = 0;
    uint8_t                         isFlyCombined = 0;
    uint8_t                         hasTransitions = 0;
    std::vector<std::pair<std::string,float>> speeds; // setAnimSpeed

    float                           headRotX = 0, headRotY = 0;
    FootIk                          ik;
    Gaze                            gaze;
    Lean                            lean;
    AnimBlend                       blend;
    Aim                             aim;
    AimWalk                         walk;
    BowDraw                         bowDraw;
    Flinch                          flinch;

    size_t                          numBones = 0;
    SampleStatus                    hasSamples[Resources::MAX_NUM_SKELETAL_NODES] = {};
    zenkit::AnimationSample         base      [Resources::MAX_NUM_SKELETAL_NODES] = {};  // shown: raw blended by AnimBlend
    zenkit::AnimationSample         raw       [Resources::MAX_NUM_SKELETAL_NODES] = {};  // what the animations give
    Tempest::Matrix4x4              tr        [Resources::MAX_NUM_SKELETAL_NODES] = {};
    Tempest::Matrix4x4              pos;
  };
