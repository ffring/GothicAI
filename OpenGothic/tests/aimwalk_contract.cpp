#include "graphics/mesh/aimwalk.h"
#include "graphics/mesh/animmath.h"
#include "graphics/mesh/footik.h"
#include "graphics/mesh/ikmath.h"
#include "graphics/mesh/skeleton.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <vector>

namespace {

using Tempest::Matrix4x4;
using Tempest::Vec3;
using IkMath::length;
using IkMath::position;

constexpr float Pi = 3.14159265358979323846f;
constexpr uint64_t FrameMs = 33;
constexpr size_t Root = 0;
constexpr size_t LeftThigh = 7, LeftCalf = 8, LeftFoot = 9, LeftToe = 10;
constexpr size_t RightThigh = 11, RightCalf = 12, RightFoot = 13, RightToe = 14;
constexpr std::array<size_t,2> Feet = {LeftFoot,RightFoot};

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

void near(const Vec3& actual, const Vec3& expected, float tolerance, const char* message) {
  near(length(actual-expected),0.f,tolerance,message);
  }

Vec3 rotated(const Vec3& point, float yaw) {
  const float c = std::cos(yaw), s = std::sin(yaw);
  return {c*point.x+s*point.z,point.y,-s*point.x+c*point.z};
  }

Matrix4x4 objectAt(const Vec3& origin, float yaw) {
  auto result = Matrix4x4::mkIdentity();
  const float c = std::cos(yaw), s = std::sin(yaw);
  result.set(0,0,c); result.set(0,2,-s);
  result.set(2,0,s); result.set(2,2,c);
  result.set(3,0,origin.x); result.set(3,1,origin.y); result.set(3,2,origin.z);
  return result;
  }

zenkit::ModelHierarchy hierarchy() {
  zenkit::ModelHierarchy result;
  struct Bone final { const char* name; int16_t parent; Vec3 offset; };
  // Slightly bent knees give the solver a stable bend plane and room to settle the pelvis.
  const Bone bones[] = {
    {"BIP01",-1,{0,0,0}}, {"BIP01 PELVIS",0,{0,86,0}},
    {"BIP01 SPINE",1,{0,8,0}}, {"BIP01 SPINE1",2,{0,16,0}},
    {"BIP01 SPINE2",3,{0,18,0}},
    {"BIP01 L UPPERARM",4,{20,0,0}}, {"BIP01 R UPPERARM",4,{-20,0,0}},
    {"BIP01 L THIGH",1,{10,0,0}}, {"BIP01 L CALF",7,{0,-37,10}},
    {"BIP01 L FOOT",8,{0,-37,-10}}, {"BIP01 L TOE0",9,{0,-12,15}},
    {"BIP01 R THIGH",1,{-10,0,0}}, {"BIP01 R CALF",11,{0,-37,10}},
    {"BIP01 R FOOT",12,{0,-37,-10}}, {"BIP01 R TOE0",13,{0,-12,15}}
    };
  for(const auto& bone:bones) {
    zenkit::ModelHierarchyNode node;
    node.name = bone.name;
    node.parent_index = bone.parent;
    auto local = Matrix4x4::mkIdentity();
    local.set(3,0,bone.offset.x); local.set(3,1,bone.offset.y); local.set(3,2,bone.offset.z);
    static_assert(sizeof(node.transform)==sizeof(local));
    std::memcpy(&node.transform,&local,sizeof(local));
    result.nodes.push_back(node);
    }
  result.bbox.min = {-40,0,-40}; result.bbox.max = {40,160,40};
  result.collision_bbox = result.bbox;
  return result;
  }

struct Fixture final {
  Skeleton skeleton{hierarchy(),nullptr,"HUMANS.MDS"};
  std::vector<zenkit::AnimationSample> base;
  Animation::Sequence sequence;

  Fixture() {
    for(const auto& node:skeleton.nodes) {
      zenkit::AnimationSample sample;
      sample.position = {node.tr.at(3,0),node.tr.at(3,1),node.tr.at(3,2)};
      sample.rotation = {1,0,0,0};
      base.push_back(sample);
      }
    sequence.data = std::make_shared<Animation::AnimData>();
    auto& data = *sequence.data;
    data.numFrames = 9;
    data.fpsRate = 8.f;
    data.moveTr = {0,0,100};
    data.hasMoveTr = true;
    for(size_t bone=0; bone<base.size(); ++bone)
      data.nodeIndex.push_back(uint32_t(bone));
    for(size_t frame=0; frame<data.numFrames; ++frame) {
      const float angle = 2.f*Pi*float(frame)/float(data.numFrames-1);
      for(size_t bone=0; bone<base.size(); ++bone) {
        auto sample = base[bone];
        float bend = 0.f;
        if(bone==LeftThigh || bone==RightThigh)
          bend = .24f*std::sin(angle+(bone==RightThigh ? Pi : 0.f));
        if(bone==LeftCalf || bone==RightCalf)
          bend = -.18f*std::max(0.f,std::sin(angle+(bone==RightCalf ? Pi : 0.f)));
        sample.rotation = {std::cos(bend*.5f),std::sin(bend*.5f),0,0};
        if(bone==Root)
          sample.position.y -= 2.f*(1.f-std::cos(2.f*angle));
        data.samples.push_back(sample);
        }
      if(frame+1<data.numFrames)
        data.tr.push_back({0,0,100.f*float(frame)/float(data.numFrames-1)});
      }
    }

  std::vector<Matrix4x4> pose(const Matrix4x4& object) const {
    std::vector<Matrix4x4> result(base.size());
    for(size_t i=0; i<base.size(); ++i) {
      const auto parent = skeleton.nodes[i].parent;
      result[i] = (parent<result.size() ? result[parent] : object)*mkMatrix(base[i]);
      }
    return result;
    }
  };

using Contacts = std::array<Vec3,2>;

struct Actor final {
  const Fixture& fixture;
  AimWalk walk;
  FootIk ik;
  Matrix4x4 object;
  Vec3 bodyOffset;
  std::vector<Matrix4x4> palette;

  Actor(const Fixture& fixture, const Vec3& origin = {}, float yaw = 0.f, float counterweight = 0.f)
    :fixture(fixture),object(objectAt(origin,yaw)),bodyOffset(rotated({0,0,-counterweight},yaw)) {
    walk.setSkeleton(&fixture.skeleton);
    ik.setSkeleton(&fixture.skeleton);
    require(walk.isValid(),"the synthetic skeleton must support aim walking");
    require(ik.isValid() && !ik.isQuadruped(),"the synthetic skeleton must support human foot IK");
    rebuild();
    }

  void rebuild() {
    palette = fixture.pose(object);
    walk.apply(palette.data(),palette.size(),fixture.base.data(),object);
    ik.apply(palette.data(),palette.size(),object.at(3,1),bodyOffset,&walk);
    walk.captureFeet(palette.data(),palette.size());
    }

  void tick(float direction, bool blocked = false, bool active = true, int rebuilds = 1) {
    AimWalk::Input input;
    input.active = active;
    input.legs = &fixture.sequence;
    input.walk = direction;
    input.blocked = blocked;
    input.dt = FrameMs;
    walk.update(input);
    FootIk::Input ground;
    ground.enable = true;
    ground.floorY = object.at(3,1);
    ground.crouch = 8.f;
    ground.dt = FrameMs;
    for(size_t leg=0; leg<2; ++leg) {
      ground.ground[leg].hasCol = true;
      ground.ground[leg].toeCol = true;
      ground.ground[leg].y = ground.floorY;
      ground.ground[leg].toeY = ground.floorY;
      }
    ik.update(ground);
    for(int i=0; i<rebuilds; ++i)
      rebuild();
    }

  Contacts contacts() const {
    return {position(palette[LeftFoot]),position(palette[RightFoot])};
    }

  AimWalk::Probe probe() const {
    AimWalk::Probe result;
    walk.probe(result);
    return result;
    }
  };

void warm(Actor& actor, float direction, int frames = 40, int rebuilds = 1) {
  for(int frame=0; frame<frames; ++frame)
    actor.tick(direction,false,true,rebuilds);
  near(actor.probe().weight,1.f,.0001f,"walking must be fully blended before the stop");
  }

void testWalkingRootMotion(const Fixture& fixture) {
  for(float direction:{-1.f,1.f}) {
    AimWalk walk;
    walk.setSkeleton(&fixture.skeleton);
    float phase = 0.f;
    for(int frame=0; frame<40; ++frame) {
      AimWalk::Input input;
      input.active = true;
      input.legs = &fixture.sequence;
      input.walk = direction;
      input.dt = FrameMs;
      walk.update(input);
      const auto probe = [&] { AimWalk::Probe p; walk.probe(p); return p; }();
      const float weight = std::min(1.f,float(frame+1)*float(FrameMs)/250.f);
      const float speed = direction>0.f ? AimWalk::Forward : AimWalk::Back;
      phase = std::fmod(phase+direction*speed*float(FrameMs)/100000.f+1.f,1.f);
      near(probe.weight,weight,.00001f,"the moving gait must retain its original 250 ms blend-in");
      near(probe.phase,phase,.00001f,"the moving gait must retain its original signed cycle rate");
      near(walk.move(FrameMs),Vec3(0,0,direction*speed*float(FrameMs)/1000.f*weight),.0001f,
           "walking root motion must keep its original speed and blend weight");
      }
    }
  }

void testStop(const Fixture& fixture, float direction, bool blocked, int warmFrames) {
  Actor actor(fixture,{},0.f,blocked ? 6.f : 0.f);
  warm(actor,direction,warmFrames);
  const float phase = actor.probe().phase;
  std::vector<Contacts> trace{actor.contacts()};
  float maxTravel = 0.f;
  uint64_t settledAt = 0;
  for(int frame=0; frame<24; ++frame) {
    actor.tick(blocked ? direction : 0.f,blocked);
    near(actor.walk.move(FrameMs),{},.0001f,"stopping legs must never move the body");
    near(actor.probe().phase,phase,.000001f,"stopping must freeze the moving gait phase");
    const auto contacts = actor.contacts();
    for(size_t foot=0; foot<Feet.size(); ++foot)
      maxTravel = std::max(maxTravel,length(contacts[foot]-trace.back()[foot]));
    trace.push_back(contacts);
    if(settledAt==0 && actor.probe().weight<=.0001f)
      settledAt=uint64_t(frame+1)*FrameMs;
    }
  require(settledAt>=200 && settledAt<=400,"the legs must settle in 0.2 to 0.4 seconds");
  require(maxTravel<=6.f+.001f,"a final ankle must travel at most 6 cm per 30 fps frame");

  // Discover the support from the first three final poses, independently of the implementation's side.
  float earlyTravel[2] = {};
  for(size_t frame=1; frame<=3; ++frame)
    for(size_t foot=0; foot<Feet.size(); ++foot)
      earlyTravel[foot] += length(trace[frame][foot]-trace[frame-1][foot]);
  const size_t support = earlyTravel[0]<=earlyTravel[1] ? 0 : 1;
  const size_t swing = support^1;
  float supportTravel = 0.f;
  for(size_t frame=1; frame<trace.size(); ++frame) {
    supportTravel = std::max(supportTravel,length(trace[frame][support]-trace[frame-1][support]));
    near(trace[frame][support],trace.front()[support],.1f,"the support contact must remain planted throughout the stop");
    }
  require(supportTravel<=1.f+.001f,"the support foot must slide at most 1 cm per frame");

  const auto start = trace.front()[swing];
  const auto finish = trace.back()[swing];
  const Vec3 horizontal(finish.x-start.x,0,finish.z-start.z);
  const float horizontalSquared = IkMath::dot(horizontal,horizontal);
  require(horizontalSquared>1.f,"the fixture must leave a measurable horizontal step to its destination");
  float lift = 0.f;
  for(const auto& frame:trace) {
    const Vec3 offset(frame[swing].x-start.x,0,frame[swing].z-start.z);
    const float progress = std::clamp(IkMath::dot(offset,horizontal)/horizontalSquared,0.f,1.f);
    const float chordY = start.y+(finish.y-start.y)*progress;
    lift = std::max(lift,frame[swing].y-chordY);
    }
  require(lift>=3.f,"the free foot must lift at least 3 cm above a sliding return to the stance");
  const auto stance = fixture.pose(actor.object);
  near(finish.y,position(stance[Feet[swing]]).y,.1f,"the free foot must land at the standing ankle height");
  if(blocked) {
    // The synthetic edge is the stopped body's plane, perpendicular to the requested movement.
    const Vec3 towardEdge(0,0,direction);
    const Vec3 stop = position(actor.object);
    const size_t toe = swing==0 ? LeftToe : RightToe;
    require(IkMath::dot(finish-stop,towardEdge)<=.1f,
            "the free ankle must land on the safe side of the edge stop");
    require(IkMath::dot(position(actor.palette[toe])-stop,towardEdge)<=.1f,
            "the free toes must land on the safe side of the edge stop");
    }
  require(actor.walk.isActive(),"finished contacts must remain available while manual aim is held");
  for(size_t frame=trace.size()-8; frame<trace.size(); ++frame)
    for(size_t foot=0; foot<Feet.size(); ++foot)
      near(trace[frame][foot],trace.back()[foot],.01f,"contacts must remain stable after the blend weight reaches zero");
  std::printf("aimwalk stop %s %s: settle=%llu ms ankle=%.3f cm/frame support=%.3f cm/frame lift=%.3f cm\n",
              direction>0.f ? "forward" : "back",blocked ? "blocked" : "released",
              static_cast<unsigned long long>(settledAt),double(maxTravel),double(supportTravel),double(lift));
  }

void testRebuildAndWorldSpace(const Fixture& fixture) {
  const Vec3 origin(250,35,-180);
  constexpr float yaw = 1.3f;
  Actor once(fixture), repeated(fixture), transformed(fixture,origin,yaw);
  for(int frame=0; frame<65; ++frame) {
    const float direction = frame<40 ? 1.f : 0.f;
    once.tick(direction);
    repeated.tick(direction,false,true,3);
    transformed.tick(direction,false,true,3);
    const auto a = once.contacts(), b = repeated.contacts(), c = transformed.contacts();
    for(size_t foot=0; foot<Feet.size(); ++foot) {
      near(b[foot],a[foot],.002f,"rebuilding the pose three times must not advance the step or recapture contacts");
      near(c[foot],origin+rotated(a[foot],yaw),.01f,"stop contacts must remain in world space under model yaw and translation");
      }
    near(repeated.probe().phase,once.probe().phase,.000001f,"rebuild count must not advance phase");
    near(repeated.probe().weight,once.probe().weight,.000001f,"rebuild count must not advance the settling weight");
    }
  }

void testResumeAndLeaveManualAim(const Fixture& fixture) {
  Actor resumed(fixture), fresh(fixture);
  warm(resumed,1.f);
  for(int frame=0; frame<24; ++frame)
    resumed.tick(0.f);
  for(int frame=0; frame<12; ++frame) {
    resumed.tick(-1.f);
    fresh.tick(-1.f);
    near(resumed.probe().weight,fresh.probe().weight,.00001f,"resuming must use the ordinary walking blend-in");
    near(resumed.probe().phase,fresh.probe().phase,.00001f,"resuming must use the ordinary new walking phase");
    near(resumed.walk.move(FrameMs),fresh.walk.move(FrameMs),.0001f,"resuming must restore ordinary root motion");
    }
  for(int frame=0; frame<24; ++frame)
    resumed.tick(0.f,false,false);
  require(!resumed.walk.isActive(),"leaving manual aim must release the stopped contacts after its ordinary fade");

  Actor held(fixture);
  warm(held,1.f);
  for(int frame=0; frame<24; ++frame)
    held.tick(0.f);
  held.tick(0.f,false,false);
  require(!held.walk.isActive(),"leaving settled manual aim must release the retained stance");
  }

}

int main() {
  AimWalk::setEnabled(true);
  FootIk::setEnabled(true);
  const Fixture fixture;
  testWalkingRootMotion(fixture);
  for(float direction:{-1.f,1.f})
    for(bool blocked:{false,true})
      testStop(fixture,direction,blocked,direction>0.f ? 40 : 48);
  testRebuildAndWorldSpace(fixture);
  testResumeAndLeaveManualAim(fixture);
  std::puts("aimwalk contracts passed");
  }
