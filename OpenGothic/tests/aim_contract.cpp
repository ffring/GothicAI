#include "graphics/mesh/aim.h"
#include "graphics/mesh/skeleton.h"

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
constexpr size_t Spine = 2, Waist = 3, Chest = 4, Head = 5, LeftFoot = 7, RightFoot = 8;

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

Matrix4x4 objectAt(const Vec3& origin, float yaw) {
  auto matrix = Matrix4x4::mkIdentity();
  const float c = std::cos(yaw), s = std::sin(yaw);
  matrix.set(0,0,c); matrix.set(0,2,-s);
  matrix.set(2,0,s); matrix.set(2,2,c);
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
    {"ZS_RIGHTHAND",4,{-35,5,-18}},
    {"BIP01 L FOOT",1,{0,-95,12}}, {"BIP01 R FOOT",1,{0,-95,-12}}
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

  std::vector<Matrix4x4> pose(const Vec3& origin, float yaw, const Vec3& pelvis) const {
    const auto object = objectAt(origin,yaw);
    std::vector<Matrix4x4> result;
    for(size_t i=0; i<skeleton.tr.size(); ++i) {
      auto matrix = object*skeleton.tr[i];
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

struct Result final {
  Vec3 head;
  Vec3 upright;
  Vec3 halfway;
  Vec3 pelvis;
  };

Result settled(const Fixture& fixture, float leanDegrees, float yawDegrees) {
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
  input.forward = facing;
  input.dir = targetDir;
  input.lean = leanDegrees*Deg;
  input.dt = 16;
  std::vector<Matrix4x4> palette;
  for(int frame=0; frame<120; ++frame) {
    aim.update(input);
    palette = fixture.pose(origin,yaw,aim.pelvisOffset());
    aim.apply(palette.data(),palette.size(),origin,facing);
    }

  Aim::Probe probe;
  aim.probe(probe);
  require(aim.isSteady(),"a fixed animation must settle in 120 frames");
  near(probe.weight,1.f,.0001f,"aim must finish blending before comparing geometry");
  near(probe.lean,leanDegrees,.03f,"the hip bend must reach the requested angle");
  const Vec3 predicted = aim.headAt(origin,facing,targetDir,input.lean);
  near(predicted,position(palette[Head]),.06f,"prospective head must match the applied skeleton");
  near(length(aim.pelvisOffset()),12.f*leanDegrees/70.f,.01f,"counterweight must reach 12 cm at 70 degrees");
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

  const Vec3 upright = aim.headAt(origin,facing,targetDir,0.f);
  near(aim.headAt(origin,facing,targetDir,-10.f*Deg),upright,.0001f,"negative hip bend must clamp to standing");
  near(aim.headAt(origin,facing,targetDir,100.f*Deg),aim.headAt(origin,facing,targetDir,70.f*Deg),
       .0001f,"the prospective head must stop at 70 degrees");
  return {predicted-origin,upright-origin,aim.headAt(origin,facing,targetDir,35.f*Deg)-origin,aim.pelvisOffset()};
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
  Aim idle;
  idle.setSkeleton(&fixture.skeleton);
  const Vec3 origin(-73,10,29);
  for(const float yaw:{179.f,-179.f,181.f,-181.f}) {
    const Vec3 facing = rotated(Vec3(-1,0,0),yaw*Deg);
    const Vec3 targetDir = facing*std::cos(80.f*Deg)+Vec3(0,-std::sin(80.f*Deg),0);
    Aim::Input input;
    input.forward = facing;
    input.dt = 16;
    idle.update(input);
    auto palette = fixture.pose(origin,yaw*Deg,{});
    idle.apply(palette.data(),palette.size(),origin,facing);
    near(idle.headAt(origin,facing,targetDir,70.f*Deg)-origin,rotated(reference.head,yaw*Deg),
         .003f,"inactive pose capture must use the current body frame");
    }
  }

void testLegacyNpcLimit() {
  near(Aim::leanForNpc(55.f*Deg),0.f,.0001f,"NPCs must still begin leaning after 55 degrees down");
  near(Aim::leanForNpc(67.5f*Deg)/Deg,27.5f,.001f,"NPCs must keep the old angular ramp");
  near(Aim::leanForNpc(80.f*Deg)/Deg,55.f,.001f,"NPC hip bend must retain its 55 degree maximum");
  near(Aim::leanForNpc(90.f*Deg)/Deg,55.f,.001f,"NPC hip bend must stay capped below the hero maximum");
  near(Aim::leanFor(80.f*Deg)/Deg,70.f,.001f,"manual fallback must allow the hero's deeper bend");
  }

}

int main() {
  Aim::setEnabled(true);
  testHeadPathAndPelvis();
  testBodyYaw();
  testLegacyNpcLimit();
  std::puts("aim geometry contract: passed");
  }
