#include "graphics/mesh/aim.h"
#include "graphics/mesh/footik.h"
#include "graphics/mesh/skeleton.h"
#include "game/aimstep.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace {

using Tempest::Matrix4x4;
using Tempest::Vec3;

constexpr float Deg = 3.14159265358979323846f/180.f;
constexpr size_t Spine = 2, Waist = 3, Chest = 4, Head = 5, Slot = 9, LeftFoot = 10, RightFoot = 11;

void require(bool condition, const char* message) {
  if(condition)
    return;
  std::fprintf(stderr,"FAIL: %s\n",message);
  std::exit(EXIT_FAILURE);
  }

void near(float actual, float expected, float tolerance, const char* message) {
  if(std::isfinite(actual) && std::abs(actual-expected)<=tolerance)
    return;
  std::fprintf(stderr,"FAIL: %s: %.9g versus %.9g (+/- %.9g)\n",
               message,double(actual),double(expected),double(tolerance));
  std::exit(EXIT_FAILURE);
  }

float length(const Vec3& v) {
  return std::sqrt(v.x*v.x+v.y*v.y+v.z*v.z);
  }

void near(const Vec3& actual, const Vec3& expected, float tolerance, const char* message) {
  near(length(actual-expected),0.f,tolerance,message);
  }

Vec3 position(const Matrix4x4& matrix) {
  return {matrix.at(3,0),matrix.at(3,1),matrix.at(3,2)};
  }

Vec3 rotated(const Vec3& p, float yaw) {
  const float c = std::cos(yaw), s = std::sin(yaw);
  return {c*p.x+s*p.z,p.y,-s*p.x+c*p.z};
  }

Matrix4x4 objectAt(const Vec3& origin, float yaw, const Vec3& scale) {
  auto matrix = Matrix4x4::mkIdentity();
  const float c = std::cos(yaw), s = std::sin(yaw);
  matrix.set(0,0,c*scale.x); matrix.set(0,2,-s*scale.x);
  matrix.set(1,1,scale.y);
  matrix.set(2,0,s*scale.z); matrix.set(2,2,c*scale.z);
  matrix.set(3,0,origin.x); matrix.set(3,1,origin.y); matrix.set(3,2,origin.z);
  return matrix;
  }

zenkit::ModelHierarchy hierarchy() {
  zenkit::ModelHierarchy source;
  struct Bone { const char* name; int16_t parent; Vec3 offset; };
  // Identity joint rotations, realistic segment lengths and slightly asymmetric joint positions.
  // The arrow follows the identity right-hand slot's -X axis.
  const Bone bones[] = {
    {"BIP01",-1,{0,0,0}}, {"BIP01 PELVIS",0,{0,95,0}},
    {"BIP01 SPINE",1,{0,5,0}}, {"BIP01 SPINE1",2,{-1,20,0}},
    {"BIP01 SPINE2",3,{-1,25,0}}, {"BIP01 HEAD",4,{-2,33,2}},
    {"BIP01 R CLAVICLE",4,{-3,0,-9}}, {"BIP01 R UPPERARM",6,{-10,0,-2}},
    {"BIP01 R FOREARM",7,{-12,0,0}}, {"ZS_RIGHTHAND",8,{-10,5,-7}},
    {"BIP01 L FOOT",1,{0,-95,12}}, {"BIP01 R FOOT",1,{0,-95,-12}},
    {"BIP01 L CLAVICLE",4,{-3,0,9}}, {"BIP01 L UPPERARM",12,{-10,0,2}}
    };
  for(const auto& bone:bones) {
    zenkit::ModelHierarchyNode node;
    node.name = bone.name;
    node.parent_index = bone.parent;
    auto transform = Matrix4x4::mkIdentity();
    transform.set(3,0,bone.offset.x);
    transform.set(3,1,bone.offset.y);
    transform.set(3,2,bone.offset.z);
    static_assert(sizeof(node.transform)==sizeof(transform));
    std::memcpy(&node.transform,&transform,sizeof(transform));
    source.nodes.push_back(node);
    }
  source.bbox.min = {-60,0,-30}; source.bbox.max = {20,190,30};
  source.collision_bbox = source.bbox;
  return source;
  }

struct Fixture final {
  Skeleton skeleton{hierarchy(),nullptr,"HUMANS.MDS"};

  std::vector<Matrix4x4> pose(const Vec3& origin, float yaw, const Vec3& pelvis,
                            const Vec3& scale = Vec3(1,1,1), float armDown = 0.f) const {
    const auto object = objectAt(origin,yaw,scale);
    auto armAnimation = Matrix4x4::mkIdentity();
    const float c = std::cos(armDown), s = std::sin(armDown);
    const Vec3 pivot = position(skeleton.tr[Chest]);
    armAnimation.set(0,0,c); armAnimation.set(0,1,s);
    armAnimation.set(1,0,-s); armAnimation.set(1,1,c);
    armAnimation.set(3,0,pivot.x-c*pivot.x+s*pivot.y);
    armAnimation.set(3,1,pivot.y-s*pivot.x-c*pivot.y);
    std::vector<Matrix4x4> result;
    for(size_t i=0; i<skeleton.tr.size(); ++i) {
      // A downward aiming animation can point the arms down while leaving the trunk upright.
      const bool arm = (i>=6 && i<=Slot) || i>=12;
      const auto animated = arm ? armAnimation*skeleton.tr[i] : skeleton.tr[i];
      auto matrix = object*animated;
      // Supply the post-FootIk state: counterweight translation above the planted feet.
      // This contract exercises Aim, not the separate leg IK solver.
      if(i!=LeftFoot && i!=RightFoot) {
        matrix.set(3,0,matrix.at(3,0)+pelvis.x);
        matrix.set(3,1,matrix.at(3,1)+pelvis.y);
        matrix.set(3,2,matrix.at(3,2)+pelvis.z);
        }
      result.push_back(matrix);
      }
    return result;
    }
  };

zenkit::ModelHierarchy footHierarchy() {
  zenkit::ModelHierarchy source;
  struct Bone { const char* name; int16_t parent; Vec3 offset; };
  const Bone bones[] = {
    {"BIP01",-1,{0,0,0}}, {"BIP01 PELVIS",0,{0,87,0}},
    {"BIP01 L THIGH",1,{0,0,10}}, {"BIP01 L CALF",2,{5,-42,0}},
    {"BIP01 L FOOT",3,{-5,-45,0}}, {"BIP01 L TOE0",4,{15,0,0}},
    {"BIP01 R THIGH",1,{0,0,-10}}, {"BIP01 R CALF",6,{5,-42,0}},
    {"BIP01 R FOOT",7,{-5,-45,0}}, {"BIP01 R TOE0",8,{15,0,0}}
    };
  for(const auto& bone:bones) {
    zenkit::ModelHierarchyNode node;
    node.name = bone.name;
    node.parent_index = bone.parent;
    auto transform = Matrix4x4::mkIdentity();
    transform.set(3,0,bone.offset.x);
    transform.set(3,1,bone.offset.y);
    transform.set(3,2,bone.offset.z);
    std::memcpy(&node.transform,&transform,sizeof(transform));
    source.nodes.push_back(node);
    }
  source.bbox.min = {-60,0,-30}; source.bbox.max = {60,190,30};
  source.collision_bbox = source.bbox;
  return source;
  }

void testEdgeStopAnkles() {
  Skeleton skeleton(footHierarchy(),nullptr,"HUMANS.MDS");
  FootIk feet;
  feet.setSkeleton(&skeleton);
  require(feet.isValid() && !feet.isQuadruped(),"synthetic leg chains must support humanoid FootIk");
  const std::array<size_t,2> ankles = {4,8};
  auto pose = [&](float stride) {
    auto palette = skeleton.tr;
    for(size_t leg=0; leg<ankles.size(); ++leg) {
      const size_t foot = ankles[leg];
      const float x = leg==0 ? stride : -stride;
      palette[foot-1].set(3,0,palette[foot-1].at(3,0)+x*.5f);
      for(size_t bone=foot; bone<=foot+1; ++bone)
        palette[bone].set(3,0,palette[bone].at(3,0)+x);
      }
    return palette;
    };
  auto anklePositions = [&](const std::vector<Matrix4x4>& palette) {
    return std::array<Vec3,2>{position(palette[ankles[0]]),position(palette[ankles[1]])};
    };
  float maximum = 0;
  auto bounded = [&](const std::vector<Matrix4x4>& palette, const std::array<Vec3,2>& previous, const char* message) {
    for(size_t leg=0; leg<ankles.size(); ++leg) {
      const float travel = length(position(palette[ankles[leg]])-previous[leg]);
      require(std::isfinite(travel) && travel<=6.f,message);
      maximum = std::max(maximum,travel);
      }
    };
  FootIk::Input input;
  input.enable = true;
  input.crouch = 20.f;
  input.dt = 33;
  for(auto& ground:input.ground)
    ground.hasCol = true;
  std::vector<Matrix4x4> palette;
  for(uint64_t tick=0; tick<20; ++tick) {
    feet.beginFrame(tick);
    feet.update(input);
    palette = pose(0.f);
    feet.apply(palette.data(),palette.size(),0.f);
    }
  auto previous = anklePositions(palette);

  // A pose rebuild may happen before update enables the stop, and several more may follow.
  // All of them must retain the preceding rendered frame as the ankle travel baseline.
  feet.beginFrame(20);
  palette = pose(24.f);
  feet.apply(palette.data(),palette.size(),0.f);
  require(length(position(palette[ankles[0]])-previous[0])>12.f,
          "the pre-update rebuild must move far enough to expose a late ankle baseline capture");
  input.aimStop = true;
  input.ground[0].y = -12.f;
  feet.update(input);
  palette = pose(30.f);
  feet.apply(palette.data(),palette.size(),0.f,Vec3(-12,0,0));
  bounded(palette,previous,"the first edge stop must limit final ankles after animation/support changes");
  for(int rebuild=0; rebuild<3; ++rebuild) {
    feet.beginFrame(20);
    input.dt = 0;
    feet.update(input);
    palette = pose(36.f+float(rebuild));
    feet.apply(palette.data(),palette.size(),0.f,Vec3(-12,0,0));
    bounded(palette,previous,"same-tick rebuilds and zero-dt updates must not reset or accumulate the ankle cap");
    }
  previous = anklePositions(palette);

  // Releasing the stop retains the cap for the rest of the 200 ms stance transition.
  input.aimStop = false;
  input.dt = 33;
  for(uint64_t frame=0; frame<6; ++frame) {
    feet.beginFrame(21+frame);
    input.ground[0].y = frame%2==0 ? 0.f : -12.f;
    input.ground[1].y = frame%2==0 ? -12.f : 0.f;
    feet.update(input);
    palette = pose(frame%2==0 ? 36.f : -36.f);
    feet.apply(palette.data(),palette.size(),0.f,Vec3(-12,0,0));
    bounded(palette,previous,"the ankle cap must survive 198 ms after the edge stop clears");
    previous = anklePositions(palette);
    }
  feet.beginFrame(27);
  input.dt = 2;
  feet.update(input);
  palette = pose(36.f);
  feet.apply(palette.data(),palette.size(),0.f,Vec3(-12,0,0));
  const float released = length(position(palette[ankles[0]])-previous[0]);
  require(maximum>1.f,"bounded ankles must still advance toward the final stance");
  require(std::isfinite(released) && released>6.f,"the temporary ankle cap must expire after 200 ms");
  std::printf("aim edge stop: ankle max=%.3f cm at 30 fps, repeated rebuilds/zero dt bounded, tail=200 ms, released=%.3f cm\n",
              double(maximum),double(released));
  }

struct Result final {
  Vec3 head;
  Vec3 upright;
  Vec3 halfway;
  Vec3 pelvis;
  };

Result settled(const Fixture& fixture, float leanDegrees, float yawDegrees, bool manual = true) {
  const Vec3 origin(250,35,-180);
  const float yaw = yawDegrees*Deg;
  const Vec3 facing = rotated(Vec3(-1,0,0),yaw);
  const Vec3 targetDir = facing*std::cos(80.f*Deg)+Vec3(0,-std::sin(80.f*Deg),0);
  Aim aim;
  aim.setSkeleton(&fixture.skeleton);
  require(aim.isValid(),"synthetic human must support the aiming post-pass");
  Aim::Input input;
  input.active = true;
  input.arrow = true;
  input.manual = manual;
  input.forward = facing;
  input.dir = targetDir;
  input.lean = leanDegrees*Deg;
  input.dt = 16;
  std::vector<Matrix4x4> palette;
  for(int frame=0; frame<120; ++frame) {
    aim.update(input);
    palette = fixture.pose(origin,yaw,aim.pelvisOffset());
    aim.apply(palette.data(),palette.size(),origin,facing);
    aim.finish(palette.data(),palette.size(),origin,facing);
    }

  Aim::Probe probe;
  aim.probe(probe);
  require(aim.isSteady(),"a fixed animation must settle in 120 frames");
  near(probe.weight,1.f,.0001f,"aim must finish blending before comparing geometry");
  near(probe.lean,leanDegrees,.03f,"the hip bend must reach the requested angle");
  const Vec3 predicted = aim.headAt(origin,facing,aim.appliedDirection(),aim.appliedLean());
  near(predicted,position(palette[Head]),.05f,"prospective head must match the applied skeleton");
  near(aim.arrowAt(origin,facing,aim.appliedDirection(),aim.appliedLean()),position(palette[Slot]),
       .05f,"prospective arrow slot must match the applied skeleton");
  near(length(aim.pelvisOffset()),manual ? 12.f*leanDegrees/70.f : 0.f,.01f,
       "only manual aiming must move the pelvis back, reaching 12 cm at 70 degrees");
  near(aim.pelvisOffset().y,0.f,.0001f,"counterweight must not lift the body");
  require(aim.pelvisOffset().x*facing.x+aim.pelvisOffset().z*facing.z<=.0001f,
          "counterweight must move backward from the feet");

  const auto neutral = fixture.pose(origin,yaw,{});
  near(position(palette[LeftFoot]),position(neutral[LeftFoot]),.0001f,"Aim must preserve the left foot anchor");
  near(position(palette[RightFoot]),position(neutral[RightFoot]),.0001f,"Aim must preserve the right foot anchor");
  for(const auto& segment:std::array<std::array<size_t,2>,3>{{{Spine,Waist},{Waist,Chest},{Chest,Head}}})
    near(length(position(palette[segment[1]])-position(palette[segment[0]])),
         length(position(neutral[segment[1]])-position(neutral[segment[0]])),.001f,
         "bending must preserve anatomical segment lengths");

  const Vec3 direction = aim.appliedDirection();
  const Vec3 upright = aim.headAt(origin,facing,direction,0.f);
  near(aim.headAt(origin,facing,direction,-10.f*Deg),upright,.0001f,"negative hip bend must clamp to standing");
  near(aim.headAt(origin,facing,direction,100.f*Deg),aim.headAt(origin,facing,direction,70.f*Deg),
       .0001f,"the prospective head must stop at 70 degrees");
  return {predicted-origin,upright-origin,aim.headAt(origin,facing,direction,35.f*Deg)-origin,aim.pelvisOffset()};
  }

void testHeadPathAndPelvis() {
  Fixture fixture;
  for(const float angle:{0.f,20.f,55.f,70.f})
    settled(fixture,angle,0.f);
  const auto full = settled(fixture,70.f,0.f);
  const Vec3 chordMidpoint = (full.upright+full.head)*.5f;
  require(length(full.halfway-chordMidpoint)>2.f,"head path must follow joint arcs rather than a linear reach/drop");
  require(full.head.x<full.upright.x-10.f && full.head.y<full.upright.y-20.f,
          "deep hip bending must reach beyond the standing head and lower it");
  near(full.pelvis,Vec3(12,0,0),.01f,"full forward bend must counterbalance 12 cm behind the feet");
  std::printf("aim: full hip=70 deg, head forward from feet=%.2f cm, pelvis back=%.2f cm\n",
              double(-full.head.x),double(length(full.pelvis)));
  }

void testUpwardShotCorrectionKeepsHipBend() {
  Fixture fixture;
  const Vec3 origin(0,0,0), facing(-1,0,0);
  const Vec3 targetDir = facing*std::cos(80.f*Deg)+Vec3(0,-std::sin(80.f*Deg),0);
  std::array<Vec3,2> heads;
  std::array<float,2> shotErrors;
  for(size_t sample=0; sample<heads.size(); ++sample) {
    Aim aim;
    aim.setSkeleton(&fixture.skeleton);
    Aim::Input input;
    input.active = true;
    input.arrow = true;
    input.manual = true;
    input.forward = facing;
    input.leanDirection = facing;
    input.dir = targetDir;
    input.lean = sample==0 ? 0.f : 70.f*Deg;
    input.dt = 16;
    std::vector<Matrix4x4> palette;
    for(int frame=0; frame<120; ++frame) {
      aim.update(input);
      palette = fixture.pose(origin,0.f,aim.pelvisOffset(),Vec3(1,1,1),60.f*Deg);
      aim.apply(palette.data(),palette.size(),origin,facing);
      aim.finish(palette.data(),palette.size(),origin,facing);
      }
    Aim::Probe probe;
    aim.probe(probe);
    require(aim.isSteady(),"the downward aiming animation must settle");
    near(probe.animPitch,-60.f,.01f,"the fixture must already aim its arrow 60 degrees down");
    near(probe.lean,sample==0 ? 0.f : 70.f,.03f,"the hip bend must settle for the upward-correction test");
    // At full hip bend the animated arrow points 130 degrees down. Retargeting it to
    // 80 degrees requires an upward correction of 50 degrees, without straightening the trunk.
    if(sample==1)
      require(probe.bend>40.f,"the deep-bend sample must require substantial upward shot correction");
    const Vec3 arrow(-palette[Slot].at(0,0),-palette[Slot].at(0,1),-palette[Slot].at(0,2));
    const Vec3 unitArrow = arrow*(1.f/length(arrow));
    const float dot = unitArrow.x*targetDir.x+unitArrow.y*targetDir.y+unitArrow.z*targetDir.z;
    shotErrors[sample] = std::acos(std::clamp(dot,-1.f,1.f))/Deg;
    near(shotErrors[sample],0.f,1.f,"the arms must still aim the arrow at the target after upward correction");
    heads[sample] = position(palette[Head]);
    near(aim.headAt(origin,facing,aim.appliedDirection(),aim.appliedLean()),heads[sample],
         .05f,"the retained hip bend must match the head prediction");
    }
  const float forwardGain = heads[0].x-heads[1].x;
  require(forwardGain>30.f,"upward shot correction must leave more than 30 cm of extra forward head reach at full hip bend");
  std::printf("aim upward correction: head forward gain=%.2f cm, arrow error standing/full=%.3f/%.3f deg\n",
              double(forwardGain),double(shotErrors[0]),double(shotErrors[1]));
  }

void testBodyYaw() {
  Fixture fixture;
  const auto reference = settled(fixture,70.f,0.f);
  // Cross both representations of the +/-pi boundary, plus a complete turn.
  for(const float yaw:{-181.f,-179.f,179.f,181.f,359.f}) {
    const auto actual = settled(fixture,70.f,yaw);
    near(actual.head,rotated(reference.head,yaw*Deg),.003f,"head geometry must rotate with body yaw across wraparound");
    near(actual.pelvis,rotated(reference.pelvis,yaw*Deg),.003f,"pelvis counterweight must rotate with body yaw");
    }

  // Pose capture also runs with the bow down, including after an unsmoothed body turn.
  // A query retains that actual torso state; a new direction must not create a new torso goal.
  Aim idle;
  idle.setSkeleton(&fixture.skeleton);
  const Vec3 origin(-73,10,29);
  Vec3 idleReference;
  for(const float yaw:{179.f,-179.f,181.f,-181.f}) {
    const Vec3 facing = rotated(Vec3(-1,0,0),yaw*Deg);
    Aim::Input input;
    input.forward = facing;
    input.dt = 16;
    idle.update(input);
    auto palette = fixture.pose(origin,yaw*Deg,{});
    idle.apply(palette.data(),palette.size(),origin,facing);
    idle.finish(palette.data(),palette.size(),origin,facing);
    near(idle.headAt(origin,facing,idle.appliedDirection(),idle.appliedLean()),position(palette[Head]),
         .003f,"inactive pose capture must match the current body frame");
    const Vec3 extended = idle.headAt(origin,facing,facing,70.f*Deg)-origin;
    if(yaw==179.f)
      idleReference = rotated(extended,-yaw*Deg);
    near(extended,rotated(idleReference,yaw*Deg),.003f,"inactive head path must rotate across body yaw wraparound");
    }
  }

struct SnapshotErrors final {
  float head = 0;
  float arrow = 0;
  };

void snapshotMatches(const Aim& aim, const std::vector<Matrix4x4>& palette, const Vec3& origin,
                     const Vec3& facing, int frame, const char* phase, SnapshotErrors& errors) {
  const Vec3 dir = aim.appliedDirection();
  const float lean = aim.appliedLean();
  const float headError = length(aim.headAt(origin,facing,dir,lean)-position(palette[Head]));
  const float arrowError = length(aim.arrowAt(origin,facing,dir,lean)-position(palette[Slot]));
  if(!std::isfinite(headError) || !std::isfinite(arrowError) || headError>.05f || arrowError>.05f) {
    std::fprintf(stderr,"FAIL: frame %d, %s: head error %.6f cm, arrow slot error %.6f cm\n",
                 frame,phase,double(headError),double(arrowError));
    std::exit(EXIT_FAILURE);
    }
  errors.head = std::max(errors.head,headError);
  errors.arrow = std::max(errors.arrow,arrowError);
  }

void testTransientSnapshot() {
  Fixture fixture;
  Aim aim;
  aim.setSkeleton(&fixture.skeleton);
  const Vec3 origin(131,27,-211);
  std::vector<Matrix4x4> palette;
  Vec3 previousFacing;
  Vec3 previousDirection;
  float previousLean = 0;
  SnapshotErrors errors;
  int partialWeightFrames = 0, laggingAimFrames = 0, laggingLeanFrames = 0;
  for(int frame=0; frame<240; ++frame) {
    // A continuous body turn crosses +pi/-pi while target yaw and pitch change independently.
    const float yaw = std::remainder(172.f+.7f*float(frame),360.f)*Deg;
    const Vec3 facing = rotated(Vec3(-1,0,0),yaw);
    const float targetYaw = 35.f*Deg*std::sin(float(frame)*.31f);
    const float targetPitch = (-50.f+35.f*std::sin(float(frame)*.19f))*Deg;
    const Vec3 targetDir = rotated(facing,targetYaw)*std::cos(targetPitch)+Vec3(0,std::sin(targetPitch),0);
    Aim::Input input;
    input.active = frame<112 || (frame>=136 && frame<208);
    input.arrow = true;
    input.manual = true;
    input.forward = facing;
    input.leanDirection = facing;
    input.dir = targetDir;
    input.lean = frame>=48 && frame<176 ? 70.f*Deg : 0.f;
    input.side = 8.f*Deg*std::sin(float(frame)*.13f);
    input.sway = {1.5f*Deg*std::sin(float(frame)*.37f),2.f*Deg*std::cos(float(frame)*.23f)};
    input.tremor = {2.f*Deg*std::cos(float(frame)*.53f),1.f*Deg*std::sin(float(frame)*.47f)};
    input.dt = frame%3==0 ? 23 : 16;
    aim.update(input);
    if(frame>0) {
      near(aim.appliedDirection(),previousDirection,.00001f,"new inputs must not change the captured aim direction");
      near(aim.appliedLean(),previousLean,.00001f,"new inputs must not change the captured hip bend");
      snapshotMatches(aim,palette,origin,previousFacing,frame,"after next update, before apply",errors);
      }

    // Vary all three model axes independently. Joint distances, not only the object origin,
    // must be captured from this actual animated palette.
    const Vec3 scale(.8f+.17f*std::sin(float(frame)*.11f),
                     1.2f+.22f*std::cos(float(frame)*.07f),
                     1.1f+.19f*std::sin(float(frame)*.17f));
    palette = fixture.pose(origin,yaw,aim.pelvisOffset(),scale);
    aim.apply(palette.data(),palette.size(),origin,facing);
    // BowDraw adjusts the release slot after Aim; finish must retain this final position.
    const Vec3 draw = rotated(Vec3(-3,.5f,1),yaw);
    palette[Slot].set(3,0,palette[Slot].at(3,0)+draw.x);
    palette[Slot].set(3,1,palette[Slot].at(3,1)+draw.y);
    palette[Slot].set(3,2,palette[Slot].at(3,2)+draw.z);
    aim.finish(palette.data(),palette.size(),origin,facing);
    snapshotMatches(aim,palette,origin,facing,frame,"after finish",errors);
    previousFacing = facing;
    previousDirection = aim.appliedDirection();
    previousLean = aim.appliedLean();
    Aim::Probe probe;
    aim.probe(probe);
    partialWeightFrames += probe.weight>0.f && probe.weight<1.f;
    laggingAimFrames += input.active && std::abs(probe.pitch-targetPitch/Deg)>2.f;
    laggingLeanFrames += input.active && std::abs(previousLean-input.lean)>2.f*Deg;
    }
  require(partialWeightFrames>10,"transient test must exercise blending in and out");
  require(laggingAimFrames>10,"transient test must distinguish smoothed direction from live input");
  require(laggingLeanFrames>10,"transient test must exercise hip bend lag at 0 and 70 degrees");
  std::printf("aim transient: 240 frames, head max error=%.6f cm, arrow slot max error=%.6f cm, fade=%d, aim lag=%d, lean lag=%d\n",
              double(errors.head),double(errors.arrow),partialWeightFrames,laggingAimFrames,laggingLeanFrames);
  }

void testLegacyNpcLimit() {
  near(Aim::leanForNpc(55.f*Deg),0.f,.0001f,"NPCs must still begin leaning after 55 degrees down");
  near(Aim::leanForNpc(67.5f*Deg)/Deg,27.5f,.001f,"NPCs must keep the old angular ramp");
  near(Aim::leanForNpc(80.f*Deg)/Deg,55.f,.001f,"NPC hip bend must retain its 55 degree maximum");
  near(Aim::leanForNpc(90.f*Deg)/Deg,55.f,.001f,"NPC hip bend must stay capped below the hero maximum");
  near(Aim::leanFor(80.f*Deg)/Deg,70.f,.001f,"the deep hip bend helper must allow the hero's 70 degree limit");
  Fixture fixture;
  const auto classic = settled(fixture,55.f,0.f,false);
  near(classic.pelvis,Vec3(),.0001f,"classic aiming must preserve the legacy pelvis position");
  }

void testEdgeApproach() {
  AimStep step;
  AimStep::Input in;
  in.drawn = in.edge = true;
  in.down = 60.f;
  in.distance = 200.f;
  require(!step.update(in),"an edge two metres away must not start an approach");
  in.distance = AimStep::SearchRange+.01f;
  require(!step.update(in),"an edge just outside 120 cm must not start an approach");
  in.distance = AimStep::SearchRange;
  require(step.update(in),"an edge at the 120 cm search limit must start an approach");
  in.distance = 100.f;
  AimStep metreStep;
  require(metreStep.update(in),"an edge one metre away must start an approach");
  require(step.update(in),"an edge one metre away must keep the approach active");
  in.distance = 36.f;
  require(step.update(in),"the approach must continue until inside 35 cm");
  in.distance = 34.f;
  require(!step.update(in),"the approach must settle once inside the guard's 20-35 cm band");
  in.distance = 30.f;
  in.blocked = true;
  require(!step.update(in),"the existing edge guard must stop the approach");
  in.blocked = false;
  in.distance = 60.f;
  require(!step.update(in),"a completed approach must not restart during the same downward look");
  in.down = 40.f;
  require(!step.update(in),"40 degrees must not start an approach");
  in.down = 40.1f;
  require(step.update(in),"looking down again must rearm the approach");
  in.moving = true;
  require(!step.update(in),"player movement must cancel the approach");
  in.moving = false;
  require(!step.update(in),"releasing movement must not immediately undo the player's cancellation");
  in.drawn = false;
  require(!step.update(in),"letting the string down must stop the approach");
  in.drawn = true;
  in.yaw = 350.f;
  require(step.update(in),"a fresh draw must rearm the approach");
  in.yaw = 15.f;
  require(step.update(in),"yaw cancellation must wrap around 360 degrees");
  in.yaw = 21.f;
  require(!step.update(in),"a cumulative turn greater than 30 degrees must cancel the approach");
  in.drawn = false;
  step.update(in);
  in.drawn = true;
  in.distance = 35.f;
  require(!step.update(in),"standing within 35 cm must not start an approach");
  in.distance = 60.f;
  in.edge = false;
  require(!step.update(in),"flat ground or a small drop must not start an approach");
  in.edge = true;
  require(step.update(in),"60 cm must start an approach");
  in.edge = false;
  require(!step.update(in),"losing the nearby edge must cancel the approach");
  std::puts("aim approach: distance, pitch, movement, yaw, draw and edge cancellation passed");
  }

}

int main() {
  Aim::setEnabled(true);
  testHeadPathAndPelvis();
  testUpwardShotCorrectionKeepsHipBend();
  testBodyYaw();
  testTransientSnapshot();
  testLegacyNpcLimit();
  testEdgeApproach();
  testEdgeStopAnkles();
  std::puts("aim geometry contract: passed");
  }
