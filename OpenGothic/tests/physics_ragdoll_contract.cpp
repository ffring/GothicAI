#include "physics/collisionworld.h"
#include "physics/dynamicworld.h"
#include "physics/ragdoll.h"
#include "graphics/mesh/hitzones.h"
#include "graphics/mesh/skeleton.h"
#include "graphics/mesh/pose.h"
#include "graphics/mesh/animmath.h"
#include "graphics/mesh/ikmath.h"
#include "game/serialize.h"

#include <Tempest/MemReader>
#include <Tempest/MemWriter>

#include <Jolt/Physics/Collision/Shape/BoxShape.h>
#include <Jolt/Physics/Collision/Shape/CapsuleShape.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <fstream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#endif
#include <rapidjson/document.h>
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

static_assert(std::is_copy_constructible_v<Pose> && std::is_copy_assignable_v<Pose>,
              "origin replay tag must preserve the complete public Pose value copy");

// The contract keeps the legacy full-copy origin replay as an independent
// baseline. It never reimplements the new constructor's member selection.
class PoseOriginReplayContract final {
  public:
    static std::vector<Tempest::Matrix4x4> legacy(const Pose& source) {
      Pose scratch=source;
      scratch.clearRagdollPose();
      return replay(scratch,source);
      }
    static std::vector<Tempest::Matrix4x4> prepared(const Pose& source) {
      Pose scratch(source,Pose::OriginReplayTag{});
      return replay(scratch,source);
      }
    static void seed(Pose& pose, size_t bone, const Tempest::Matrix4x4& value) {
      pose.tr[bone]=value;
      }
  private:
    static std::vector<Tempest::Matrix4x4> replay(Pose& scratch, const Pose& source) {
      auto object=source.pos;
      object.set(3,0,0.f); object.set(3,1,0.f); object.set(3,2,0.f);
      scratch.mkSkeleton(object,source.pos.at(3,1));
      return std::vector<Tempest::Matrix4x4>(scratch.tr,scratch.tr+source.numBones);
      }
  };

namespace {

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

void exact(float actual, float expected, const char* message) {
  require(std::memcmp(&actual,&expected,sizeof(float))==0,message);
  }

Tempest::Matrix4x4 at(float x, float y, float z) {
  auto matrix = Tempest::Matrix4x4::mkIdentity();
  matrix.translate(x,y,z);
  return matrix;
  }

Tempest::Vec3 position(const Tempest::Matrix4x4& matrix) {
  Tempest::Vec3 result;
  matrix.project(result);
  return result;
  }

zenkit::ModelHierarchy hierarchy() {
  zenkit::ModelHierarchy source;
  struct Bone { const char* name; int16_t parent; Tempest::Vec3 offset; };
  // A small human hierarchy with actual limb chains and a nonphysical root.
  const Bone bones[] = {
    {"BIP01",-1,{0,0,0}}, {"BIP01 PELVIS",0,{0,95,0}},
    {"BIP01 SPINE",1,{0,22,0}}, {"BIP01 SPINE1",2,{0,22,0}},
    {"BIP01 NECK",3,{0,15,0}}, {"BIP01 HEAD",4,{0,15,0}},
    {"BIP01 L UPPERARM",3,{25,0,0}}, {"BIP01 L FOREARM",6,{25,0,0}},
    {"BIP01 L HAND",7,{23,0,0}},
    {"BIP01 R UPPERARM",3,{-25,0,0}}, {"BIP01 R FOREARM",9,{-25,0,0}},
    {"BIP01 R HAND",10,{-23,0,0}},
    {"BIP01 L THIGH",1,{12,-8,0}}, {"BIP01 L CALF",12,{0,-42,0}},
    {"BIP01 L FOOT",13,{0,-40,0}}, {"BIP01 L TOE",14,{0,0,15}},
    {"BIP01 R THIGH",1,{-12,-8,0}}, {"BIP01 R CALF",16,{0,-42,0}},
    {"BIP01 R FOOT",17,{0,-40,0}}, {"BIP01 R TOE",18,{0,0,15}},
    {"ZS_HELMET",5,{0,12,0}}
    };
  for(const auto& bone:bones) {
    zenkit::ModelHierarchyNode node;
    node.name = bone.name;
    node.parent_index = bone.parent;
    const auto transform = at(bone.offset.x,bone.offset.y,bone.offset.z);
    static_assert(sizeof(node.transform)==sizeof(transform));
    std::memcpy(&node.transform,&transform,sizeof(transform));
    source.nodes.push_back(node);
    }
  source.bbox.min = {-80,0,-20}; source.bbox.max = {80,190,25};
  source.collision_bbox = source.bbox;
  return source;
  }

struct Fixture final {
  Skeleton skeleton{hierarchy(),nullptr,"HUMANS.MDS"};
  HitZones zones;

  Fixture() { zones.setSkeleton(&skeleton); require(zones.isValid(),"synthetic hierarchy must have hit capsules"); }

  std::unique_ptr<Ragdoll> create(CollisionWorld& world, const Tempest::Matrix4x4& object,
                                const Tempest::Vec3& velocity = {}) const {
    std::vector<Tempest::Matrix4x4> pose;
    for(const auto& matrix:skeleton.tr)
      pose.push_back(object*matrix);
    auto body = world.createRagdoll(skeleton,zones,pose.data(),pose.size(),object,velocity);
    require(body!=nullptr && body->valid(),"valid capsule hierarchy must create a ragdoll");
    require(body->partCount()>10 && body->partCount()<skeleton.nodes.size(),"physical parts must cover limbs and exclude slots/root");
    require(body->boneCount()==skeleton.nodes.size(),"pose output must retain skeleton indexing");
    near(body->mass(),75,.001f,"human parts must sum to the human mass in kilograms");
    return body;
    }
  };

void finite(const Ragdoll::State& state) {
  require(!state.parts.empty(),"ragdoll snapshot must have parts");
  for(const auto& part:state.parts) {
    for(size_t i=0;i<16;++i)
      require(std::isfinite(part.transform.data()[i]),"physical transform must remain finite");
    if(state.version>=2)
      for(size_t i=0;i<16;++i)
        require(std::isfinite(part.referenceTransform.data()[i]),"initial affine reference must remain finite");
    require(std::isfinite(part.linearVelocity.x) && std::isfinite(part.linearVelocity.y) &&
            std::isfinite(part.linearVelocity.z),"linear velocity must remain finite");
    require(std::isfinite(part.angularVelocity.x) && std::isfinite(part.angularVelocity.y) &&
            std::isfinite(part.angularVelocity.z),"angular velocity must remain finite");
    if(state.version>=3)
      require(std::isfinite(part.centerOfMassMeters.x) && std::isfinite(part.centerOfMassMeters.y) &&
              std::isfinite(part.centerOfMassMeters.z) && std::isfinite(part.rotation.x) && std::isfinite(part.rotation.y) &&
              std::isfinite(part.rotation.z) && std::isfinite(part.rotation.w) &&
              std::isfinite(part.linearVelocityMeters.x) && std::isfinite(part.linearVelocityMeters.y) &&
              std::isfinite(part.linearVelocityMeters.z),"precise native COM/quaternion/velocity must remain finite");
    }
  if(state.version>=3)
    for(const auto& ancestor:state.ancestors)
      for(size_t i=0;i<16;++i) require(std::isfinite(ancestor.referenceTransform.data()[i]),"historic ancestor reference must remain finite");
  }

void same(const Ragdoll::State& actual, const Ragdoll::State& expected, float positionTolerance) {
  require(actual.version==expected.version && actual.skeleton==expected.skeleton &&
          actual.parts.size()==expected.parts.size(),"restored snapshot identity must match");
  for(size_t i=0;i<16;++i)
    near(actual.rootTransform.data()[i],expected.rootTransform.data()[i],
         i>=12 && i<=14 ? positionTolerance : .00001f,"restored root transform must match");
  for(size_t p=0;p<actual.parts.size();++p) {
    const auto& a=actual.parts[p]; const auto& e=expected.parts[p];
    require(a.bone==e.bone && a.sleeping==e.sleeping,"restored bone identity and sleep flag must match");
    for(size_t i=0;i<16;++i)
      near(a.transform.data()[i],e.transform.data()[i],
           i>=12 && i<=14 ? positionTolerance : .00001f,"restored part transform must match");
    if(actual.version>=2)
      for(size_t i=0;i<16;++i)
        near(a.referenceTransform.data()[i],e.referenceTransform.data()[i],
             i>=12 && i<=14 ? .001f : .00001f,"restored immutable affine reference must match");
    near((a.linearVelocity-e.linearVelocity).length(),0,.001f,"restored linear velocity must match in cm/s");
    near((a.angularVelocity-e.angularVelocity).length(),0,.00001f,"restored angular velocity must match in radians/s");
    if(actual.version>=3 && positionTolerance<=.001f) {
      exact(a.centerOfMassMeters.x,e.centerOfMassMeters.x,"instant restore must preserve native COM x bits");
      exact(a.centerOfMassMeters.y,e.centerOfMassMeters.y,"instant restore must preserve native COM y bits");
      exact(a.centerOfMassMeters.z,e.centerOfMassMeters.z,"instant restore must preserve native COM z bits");
      exact(a.linearVelocityMeters.x,e.linearVelocityMeters.x,"instant restore must preserve native velocity x bits");
      exact(a.linearVelocityMeters.y,e.linearVelocityMeters.y,"instant restore must preserve native velocity y bits");
      exact(a.linearVelocityMeters.z,e.linearVelocityMeters.z,"instant restore must preserve native velocity z bits");
      exact(a.rotation.x,e.rotation.x,"instant restore must preserve native quaternion x bits");
      exact(a.rotation.y,e.rotation.y,"instant restore must preserve native quaternion y bits");
      exact(a.rotation.z,e.rotation.z,"instant restore must preserve native quaternion z bits");
      exact(a.rotation.w,e.rotation.w,"instant restore must preserve native quaternion w bits");
      }
    }
  if(actual.version>=3) {
    require(actual.legacyAncestors==expected.legacyAncestors && actual.ancestors.size()==expected.ancestors.size(),"restored ancestor topology/legacy mode must match");
    for(size_t a=0;a<actual.ancestors.size();++a) {
      const auto& got=actual.ancestors[a]; const auto& want=expected.ancestors[a];
      require(got.bone==want.bone && got.anchorBone==want.anchorBone,"restored ancestor mapping must match");
      for(size_t i=0;i<16;++i) near(got.referenceTransform.data()[i],want.referenceTransform.data()[i],0,"ancestor birth references must remain exactly immutable");
      }
    }
  }

void joints(const Fixture& fixture, const Ragdoll& body, int frame=-1) {
  std::vector<Tempest::Matrix4x4> pose;
  require(body.getPose(pose) && pose.size()==fixture.skeleton.nodes.size(),"physical pose must be available by bone");
  const auto& indices=body.boneIndices();
  for(auto bone:indices) {
    const auto parent=fixture.skeleton.nodes[bone].parent;
    if(std::find(indices.begin(),indices.end(),parent)==indices.end())
      continue;
    const auto bindLength=(position(fixture.skeleton.tr[bone])-position(fixture.skeleton.tr[parent])).length();
    const auto actualLength=(position(pose[bone])-position(pose[parent])).length();
    if(!std::isfinite(actualLength) || std::abs(actualLength-bindLength)>5.f) {
      const auto childAt=position(pose[bone]), parentAt=position(pose[parent]);
      std::fprintf(stderr,"joint diagnostic frame=%d child=%zu %s parent=%zu %s child_cm=[%.9g,%.9g,%.9g] parent_cm=[%.9g,%.9g,%.9g]\n",
                   frame,bone,fixture.skeleton.nodes[bone].name.c_str(),parent,fixture.skeleton.nodes[parent].name.c_str(),
                   double(childAt.x),double(childAt.y),double(childAt.z),double(parentAt.x),double(parentAt.y),double(parentAt.z));
      for(const auto& part:body.state().parts)
        if(part.bone==bone || part.bone==parent)
          std::fprintf(stderr,"joint velocity bone=%u linear_cm_s=[%.9g,%.9g,%.9g] angular_rad_s=[%.9g,%.9g,%.9g]\n",part.bone,
                       double(part.linearVelocity.x),double(part.linearVelocity.y),double(part.linearVelocity.z),
                       double(part.angularVelocity.x),double(part.angularVelocity.y),double(part.angularVelocity.z));
      }
    near(actualLength,bindLength,5.f,
         "joint anchors must not separate adjacent bones by more than five centimeters");
    }
  }

auto floor(CollisionWorld& world, float friction=.75f) {
  return world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),
                                friction,DynamicWorld::C_Landscape);
  }

void testKinematicPoseAndInteraction() {
  Fixture fixture;
  {
    CollisionWorld world;
    const auto emptyCount=world.bodyCount();
    auto thinSource=hierarchy(); thinSource.nodes.resize(1);
    Skeleton thin(thinSource,nullptr,"NO_PHYSICAL_BONES.MDS");
    HitZones thinZones; thinZones.setSkeleton(&thin);
    require(!world.createKinematicBody(thin,thinZones,thin.tr.data(),thin.tr.size(),at(0,0,0)),
            "a skeleton without compatible physical owners must safely decline a live rig");
    require(world.bodyCount()==emptyCount,"an incompatible living skeleton must leave no native parts");
    auto object=at(23000,500,-18000); object.rotateOY(31); object.scale(.9f,1.f,1.f);
    auto pose=fixture.skeleton.tr;
    for(auto& bone:pose) bone=object*bone;
    auto body=world.createKinematicBody(fixture.skeleton,fixture.zones,pose.data(),pose.size(),object);
    require(body && body->isKinematic(),"an animation-driven rig must create independent kinematic parts");
    require(world.bodyCount()==emptyCount+body->partCount(),"every live capsule owner must belong to the shared physics world");
    const auto compare=[&] {
      std::vector<Tempest::Matrix4x4> actual;
      require(body->getPose(actual),"kinematic native pose must be readable");
      for(auto bone:body->poseIndices())
        for(size_t component=0;component<16;++component)
          near(actual[bone].data()[component],pose[bone].data()[component],
               component>=12 && component<=14 ? .02f : .00002f,
               "kinematic parts and ancestor frames must reproduce the requested animation pose");
      };
    compare();
    for(int frame=0;frame<6;++frame) {
      // Independent changes to head, forearm and calf detect rigs that update
      // only their root or accidentally retain the initial capsule orientation.
      for(size_t bone:{size_t(5),size_t(7),size_t(13)}) {
        pose[bone].rotateOZ(7.f);
        pose[bone].set(3,0,pose[bone].at(3,0)+float(bone));
        }
      require(body->moveKinematic(pose.data(),pose.size(),object,.007f),"a ready animation pose must queue without another solve");
      world.tick(17);
      compare();
      }
    for(int frame=0;frame<120;++frame) world.tick(17);
    compare();
    auto invalid=pose;
    invalid[7].set(3,0,std::numeric_limits<float>::quiet_NaN());
    require(!body->moveKinematic(invalid.data(),invalid.size(),object,.017f),"invalid live poses must be rejected atomically");
    world.tick(17); compare();
    const auto pelvis=position(body->pelvisTransform());
    require(world.rayCast(pelvis+Tempest::Vec3(0,0,300),pelvis-Tempest::Vec3(0,0,300),1u<<DynamicWorld::C_LivingBody).hasHit,
            "live geometry must be queryable only when its layer is explicitly requested");
    require(!world.rayCast(pelvis+Tempest::Vec3(0,0,300),pelvis-Tempest::Vec3(0,0,300),
                          (1u<<DynamicWorld::C_Landscape)|(1u<<DynamicWorld::C_Object)|(1u<<DynamicWorld::C_Ragdoll)).hasHit,
            "the established world and corpse hit layers must exclude living rigs");
    require(body->state().parts.empty(),"transient animation rigs must not export corpse save data");
    body.reset();
    require(world.bodyCount()==emptyCount,"destroying a live rig must remove every kinematic part");
    }
  {
    CollisionWorld world;
    auto ground=floor(world,.1f);
    auto item=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3::sReplicate(.15f)),at(0,15,0),.1f,1.f);
    auto object=at(-100,-75,0);
    auto pose=fixture.skeleton.tr;
    const auto update=[&] { for(size_t i=0;i<pose.size();++i) pose[i]=object*fixture.skeleton.tr[i]; };
    update();
    auto body=world.createKinematicBody(fixture.skeleton,fixture.zones,pose.data(),pose.size(),object);
    require(bool(body),"a live rig must be created for native contact checks");
    for(int frame=0;frame<50;++frame) {
      object.set(3,0,-100.f+2.f*float(frame+1)); update();
      require(body->moveKinematic(pose.data(),pose.size(),object,.017f),"a translated live rig must accept the next animation pose");
      world.tick(17);
      }
    require(item->position().x>15.f,"moving live capsules must transfer motion to a dynamic item through native contacts");
    }
  {
    CollisionWorld world,control;
    auto ground=floor(world,.1f),controlGround=floor(control,.1f);
    auto item=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3::sReplicate(.15f)),at(0,15,0),.1f,1.f);
    auto reference=control.addDynamicBody(new JPH::BoxShape(JPH::Vec3::sReplicate(.15f)),at(0,15,0),.1f,1.f);
    auto object=at(-25,-75,0);
    auto pose=fixture.skeleton.tr;
    for(size_t i=0;i<pose.size();++i) pose[i]=object*fixture.skeleton.tr[i];
    auto body=world.createKinematicBody(fixture.skeleton,fixture.zones,pose.data(),pose.size(),object);
    require(bool(body),"teleport contact check needs a live rig touching a dynamic item");
    const auto parts=world.bodyCount();
    // Each queued change is below the guard. Only the accumulated distance
    // from the real native frame reveals the 80 cm script/interaction jump.
    for(int step=0;step<2;++step) {
      object.set(3,0,object.at(3,0)+40.f);
      for(size_t i=0;i<pose.size();++i) pose[i]=object*fixture.skeleton.tr[i];
      require(body->moveKinematic(pose.data(),pose.size(),object,.017f),"script jumps must queue ordinary solved poses");
      }
    float maxItemError=0;
    for(int frame=0;frame<12;++frame) {
      world.tick(17);control.tick(17);
      maxItemError=std::max(maxItemError,(item->position()-reference->position()).length());
      require(world.bodyCount()==parts,"teleporting must preserve native part count");
      }
    std::vector<Tempest::Matrix4x4> actual;
    require(body->getPose(actual),"teleported native pose must remain available");
    for(auto bone:body->poseIndices())
      for(size_t component=0;component<16;++component)
        near(actual[bone].data()[component],pose[bone].data()[component],component>=12 && component<=14 ? .02f : .00002f,
             "a teleport must reach the full requested pose and retain zero residual motion");
    near(maxItemError,0,.01f,"an 80 cm kinematic jump must not launch a touching item along its old swept path");
    std::printf("kinematic teleport distance_cm=80 queued_steps=2 item_delta_cm=%.9g parts=%zu\n",double(maxItemError),body->partCount());
    }
  }

void testLocalMotorReactions() {
  Fixture fixture;
  for(const size_t hitBone:{size_t(6),size_t(13),size_t(3)}) {
    const bool chest=hitBone==3;
    CollisionWorld world;
    auto object=at(0,300,0);
    auto target=fixture.skeleton.tr;
    for(auto& frame:target) frame=object*frame;
    auto body=world.createKinematicBody(fixture.skeleton,fixture.zones,target.data(),target.size(),object);
    require(bool(body),"local motor checks need a valid living rig");
    const auto count=world.bodyCount(),parts=body->partCount();
    const auto hitPoint=position(target[hitBone])+Tempest::Vec3(12,0,0);
    require(!body->queueHit(fixture.skeleton.nodes.size(),hitPoint,{0,0,3},1),"unmapped hit bones must be rejected");
    require(body->queueHit(hitBone,hitPoint,{0,0,3},1),"a mapped bone must accept one point impulse in newton seconds");
    require(body->isKinematic(),"queued reactions must not mutate the native world before its fixed step");
    world.tick(17);
    require(!body->isKinematic() && !body->reactionBones().empty(),"a hit must activate a real dynamic limb chain");
    near(body->reactionWeight(),1,.0001f,"hit strength must control the initial physical pose weight");
    require(body->partCount()==parts && world.bodyCount()==count,"motor promotion must replace native parts without changing their count");
    const auto contains=[&](size_t bone) {
      const auto& bones=body->reactionBones(); return std::find(bones.begin(),bones.end(),bone)!=bones.end();
      };
    require(contains(hitBone) && !contains(1) && !contains(16) && (chest || !contains(9)),"physical reaction must exclude pelvis and unaffected limbs");
    require(chest ? !contains(2) && !contains(12) : hitBone==6 ? contains(7) && contains(8) && !contains(13) : contains(12) && contains(14) && !contains(6),
            "limb hits must select their anatomical chain while a chest hit leaves the lower spine and legs fixed");
    if(chest) require(body->reactionBones()==std::vector<size_t>{3},"a torso hit must activate only its spine branch, leaving head and arms kinematic");
    const auto probeBone=hitBone==6 ? size_t(8) : size_t(14);
    float peak=0,recovered=0;
    float chestAnglePeak=0,chestAngleRecovered=0;
    std::vector<Tempest::Matrix4x4> physical;
    for(int frame=0;frame<45;++frame) {
      // Keep the gameplay root moving during the physical limb response.
      object.set(3,0,float(frame)*.5f);
      for(size_t i=0;i<target.size();++i) target[i]=object*fixture.skeleton.tr[i];
      require(body->moveKinematic(target.data(),target.size(),object,.017f),"active rigs must continue consuming the ordinary ready animation target");
      world.tick(17);
      require(body->getPose(physical),"active native transforms must remain readable for the render overlay");
      for(const size_t fixed:{size_t(1),chest ? size_t(12) : size_t(9),size_t(16)})
        for(size_t component=0;component<16;++component)
          near(physical[fixed].data()[component],target[fixed].data()[component],component>=12 && component<=14 ? .02f : .00002f,
               "root and unaffected limbs must keep their animation transforms during a local reaction");
      const float displacement=std::abs(position(physical[probeBone]).z-position(target[probeBone]).z);
      peak=std::max(peak,displacement);
      if(frame==40) recovered=displacement;
      if(chest) {
        float trace=0;
        for(size_t axis=0;axis<3;++axis) {
          const auto* a=physical[hitBone].data()+axis*4;
          const auto* b=target[hitBone].data()+axis*4;
          trace+=Tempest::Vec3::dotProduct(Tempest::Vec3::normalize({a[0],a[1],a[2]}),Tempest::Vec3::normalize({b[0],b[1],b[2]}));
          }
        const float angle=std::acos(std::clamp((trace-1.f)*.5f,-1.f,1.f))*180.f/3.14159265358979323846f;
        chestAnglePeak=std::max(chestAnglePeak,angle);
        if(frame==40) chestAngleRecovered=angle;
        for(const size_t bone:body->boneIndices())
          if(bone!=hitBone)
            for(size_t component=0;component<16;++component)
              near(physical[bone].data()[component],target[bone].data()[component],component>=12 && component<=14 ? .02f : .00002f,
                   "head, both arms, pelvis and legs must retain their exact world animation pose during a chest reaction");
        }
      require(world.bodyCount()==count,"motor simulation must not append replacement bodies every frame");
      }
    // A spine's bone origin is its constrained pivot: rotation is the physical
    // response, while head/arms remain at their independent animation targets.
    const float responsePeak=chest ? chestAnglePeak : peak;
    const float responseRecovered=chest ? chestAngleRecovered : recovered;
    std::printf("local motor measured bone=%zu peak=%.9g recovered=%.9g unit=%s\n",hitBone,double(responsePeak),double(responseRecovered),chest?"degrees":"cm");
    require(responsePeak>(chest ? .1f : .3f),"the addressed limb/chest must show a measurable local native response after its point impulse");
    require(body->reactionWeight()>0.f,"motor recovery must be observed before the final kinematic deadline");
    require(responseRecovered<responsePeak*.65f+.05f,"real motors must return the limb/chest toward animation before the final kinematic reset");
    const auto point=position(physical[hitBone]);
    require(world.rayCast(point+Tempest::Vec3(0,0,300),point-Tempest::Vec3(0,0,300),1u<<DynamicWorld::C_ActiveBody).hasHit,
            "the activated physical chain must be present in its explicit contact layer");
    require(!world.rayCast(point+Tempest::Vec3(0,0,300),point-Tempest::Vec3(0,0,300),
                          (1u<<DynamicWorld::C_Landscape)|(1u<<DynamicWorld::C_Object)|(1u<<DynamicWorld::C_Ragdoll)).hasHit,
            "active living parts must remain excluded from the established primary hit layers");
    require(body->queueHit(hitBone,point,{0,0,-2},.35f) && body->queueHit(hitBone,point,{0,0,1},.35f),
            "an existing motor rig must accept repeated hits without allocating another rig");
    for(int frame=0;frame<100;++frame) {
      world.tick(17);
      require(world.bodyCount()==count && body->partCount()==parts,"repeated hits and recovery must preserve the native part count");
      }
    require(body->isKinematic() && body->reactionWeight()==0.f && body->reactionBones().empty(),"every finite reaction must return to pure kinematic mode within its deadline");
    require(body->getPose(physical),"the recovered kinematic pose must remain available");
    for(auto bone:body->poseIndices())
      for(size_t component=0;component<16;++component)
        near(physical[bone].data()[component],target[bone].data()[component],component>=12 && component<=14 ? .02f : .00002f,
             "the recovered rig must exactly follow its latest ordinary animation target");
    require(body->state().parts.empty(),"a partial active rig must never become corpse save data");
    std::printf("local motor bone=%zu peak=%.9g recovered_before_reset=%.9g unit=%s parts=%zu\n",hitBone,double(responsePeak),double(responseRecovered),chest?"degrees":"cm",parts);
    body.reset(); require(world.bodyCount()==0,"destroying a motor-capable rig must also remove its constraints and every native part");
    }
  }

void testLimpBoneImpulseOnce() {
  Fixture fixture;
  CollisionWorld hitWorld,controlWorld;
  auto hit=fixture.create(hitWorld,at(0,700,0));
  auto control=fixture.create(controlWorld,at(0,700,0));
  const auto initial=hit->state();
  const auto found=std::find_if(initial.parts.begin(),initial.parts.end(),[](const auto& part) { return part.bone==7; });
  require(found!=initial.parts.end(),"limp impulse fixture needs a forearm part");
  const auto point=position(found->transform)+Tempest::Vec3(12,0,0);
  require(hit->queueHit(7,point,{0,0,3},1),"a limp body must accept the same deferred point impulse API");
  same(hit->state(),initial,.001f);
  hitWorld.tick(17); controlWorld.tick(17);
  const auto hitState=hit->state(),controlState=control->state();
  const auto part=size_t(found-initial.parts.begin());
  require((hitState.parts[part].angularVelocity-controlState.parts[part].angularVelocity).length()>.05f,
          "a limp bone impulse away from its COM must generate local angular motion instead of a global velocity kick");
  require(hit->reactionWeight()==0.f && hit->reactionBones().empty() && !hit->isKinematic(),"limp hits must never enable active animation motors");
  require(hit->restore(initial) && control->restore(initial),"the one-shot impulse check must reset only observable physical state");
  hitWorld.tick(17); controlWorld.tick(17);
  same(hit->state(),control->state(),.01f);
  require(hitWorld.bodyCount()==hit->partCount(),"a limp point impulse must not allocate living or replacement parts");
  }

void testCorpseRayMetadata() {
  Fixture fixture;
  CollisionWorld world,reference;
  auto nearBody=fixture.create(world,at(0,300,0));
  auto farBody=fixture.create(world,at(0,300,-100));
  const auto a=position(fixture.skeleton.tr[7]),b=position(fixture.skeleton.tr[8]);
  std::vector<HitZones::Posed> capsules;
  fixture.zones.posed(fixture.skeleton.tr.data(),fixture.skeleton.tr.size(),1.f,capsules);
  const auto cap=std::find_if(capsules.begin(),capsules.end(),[&](const auto& c) { return (c.a-a).quadLength()<.0001f && (c.b-b).quadLength()<.0001f; });
  require(cap!=capsules.end(),"corpse query oracle needs the public posed forearm capsule");
  const auto center=(a+b)*.5f+Tempest::Vec3(0,300,0);
  auto frame=at(center.x,center.y,center.z); frame.rotateOZ(-90);
  auto plain=reference.addCollisionBody(new JPH::CapsuleShape((b-a).length()*.005f,cap->r*.01f),frame,.5f,DynamicWorld::C_Ragdoll);
  const Tempest::Vec3 from{center.x,center.y,300},to{center.x,center.y,-300};
  const uint32_t mask=1u<<DynamicWorld::C_Ragdoll;
  const auto expected=reference.rayCast(from,to,mask);
  require(expected.hasHit && expected.ragdoll==nullptr,"plain native primitive must give an independent geometry oracle without owner metadata");
  const auto compare=[&] {
    const auto hit=world.rayCast(from,to,mask);
    require(hit.hasHit && hit.ragdoll==nearBody.get() && hit.bone==7,"the selected corpse hit must identify its current owner and exact forearm bone");
    near(hit.fraction,expected.fraction,.000001f,"adding corpse ownership must preserve the selected native fraction");
    near((hit.point-expected.point).length(),0,.001f,"corpse metadata must preserve the selected world point");
    near((hit.normal-expected.normal).length(),0,.000001f,"corpse metadata must preserve the selected native normal");
    require(hit.material==expected.material,"corpse ownership must preserve the selected material");
    };
  compare();
  const auto saved=nearBody->state();
  require(nearBody->restore(saved),"corpse metadata oracle must survive native BodyID replacement on restore");
  compare();
  std::vector<CollisionWorld::QueryResult> all;
  world.rayCast(from,to,mask,false,&all);
  require(std::any_of(all.begin(),all.end(),[&](const auto& hit) { return hit.ragdoll==farBody.get() && hit.bone==7; }),
          "all-hit metadata must identify the far corpse independently of the closest one");
  nearBody.reset();
  require(world.rayCast(from,to,mask).ragdoll==farBody.get(),"destroyed corpse parts must never leave stale owner metadata");
  require(!world.rayCast(from,to,(1u<<DynamicWorld::C_Landscape)|(1u<<DynamicWorld::C_Object)).hasHit,
          "ordinary ray masks must remain unchanged by corpse metadata");
  }

void testProjectileWorldStopOptIn() {
  struct Callback final : DynamicWorld::BulletCallback {
    bool enabled=false;
    DynamicWorld::BulletBody* body=nullptr;
    DynamicWorld::BulletWorldHit hit;
    std::string order;
    int worlds=0,materials=0,stops=0,moves=0;
    bool stopsAtWorldHit() const override { return enabled; }
    void onMove() override { ++moves; order+='M'; }
    void onCollide(zenkit::MaterialGroup) override { ++materials; order+='C'; }
    void onWorldHit(const DynamicWorld::BulletWorldHit& value) override {
      ++worlds; order+='W'; hit=value;
      near((body->position()-value.point).length(),0,0,"world-hit callback must observe the exact selected projectile position");
      require(body->pathLength()>0 && body->hitCount()==1,"world-hit callback must observe updated travelled distance and hit count");
      DynamicWorld::BulletCallback::onWorldHit(value);
      }
    void onStop() override { ++stops; order+='S'; }
    };
  zenkit::Mesh wall;
  wall.vertices={{100,-2000,-2000},{100,2000,-2000},{100,0,2000}};
  wall.materials.resize(1); wall.materials[0].group=zenkit::MaterialGroup::STONE;
  wall.polygons.material_indices={0}; wall.polygons.vertex_indices={0,1,2};
  for(const bool enabled:{false,true}) {
    DynamicWorld world(nullptr,wall);
    Callback callback; callback.enabled=enabled;
    auto* arrow=world.bulletObj(&callback); callback.body=arrow;
    const Tempest::Vec3 from{0,500,0},direction{3,0,0};
    const auto to=from+direction*100.f-Tempest::Vec3(0,.5f*DynamicWorld::gravity*100.f*100.f,0);
    const auto expected=world.ray(from,to);
    require(expected.hasCol && expected.mat==zenkit::MaterialGroup::STONE,"projectile contract needs the same selected stone wall ray");
    arrow->setPosition(from); arrow->setDirection(direction);
    world.tick(100);
    if(enabled) {
      require(callback.worlds==1 && callback.materials==1 && callback.stops==1 && callback.moves==1 && callback.order=="MWCS",
              "opt-in world stop must deliver move, world/material and one stop in order");
      near((callback.hit.point-expected.v).length(),0,0,"sticking must use the exact pre-existing selected world hit point");
      near((callback.hit.normal-expected.n).length(),0,0,"sticking must preserve the selected world normal");
      near(arrow->pathLength(),(to-from).length()*expected.hitFraction,.0001f,"sticking must account for only the travelled segment");
      require(arrow->direction().x==3 && callback.hit.ragdoll==nullptr,"world sticking must retain incoming orientation without a metal/stone bounce");
      for(int frame=0;frame<4;++frame) world.tick(17);
      require(callback.worlds==1 && callback.stops==1 && callback.moves==1,"a terminal world stop must not repeat callbacks while game-side deletion is deferred");
      }
    else {
      require(callback.worlds==0 && callback.stops==0 && callback.materials==1 && callback.moves==1 && callback.order=="CM",
              "OFF must preserve the old stone bounce callbacks and move order");
      require(arrow->direction().x<0 && arrow->speed()<3,"OFF must preserve the old reflected arrow velocity");
      }
    world.deleteObj(arrow);
    }
  Fixture fixture;
  for(const bool enabled:{false,true}) {
    DynamicWorld world(nullptr,zenkit::Mesh{});
    auto pose=fixture.skeleton.tr;
    const auto object=at(0,300,0);
    for(auto& frame:pose) frame=object*frame;
    auto corpse=world.createRagdoll(fixture.skeleton,fixture.zones,pose.data(),pose.size(),object);
    Callback callback; callback.enabled=enabled;
    auto* arrow=world.bulletObj(&callback); callback.body=arrow;
    arrow->setPosition({200,439,0}); arrow->setDirection({-3,0,0});
    world.tick(100);
    require(enabled ? callback.worlds==1 && callback.stops==1 && callback.hit.ragdoll==corpse.get() && callback.hit.bone!=size_t(-1) :
                      callback.worlds==0 && callback.stops==0 && arrow->position().x<0,
            "only the sticking opt-in may add corpse parts to the projectile world-hit mask");
    world.deleteObj(arrow);
    }
  }

void testDropSleepWake() {
  Fixture fixture;
  CollisionWorld world;
  auto ground=floor(world);
  const auto emptyCount=world.bodyCount();
  auto body=fixture.create(world,at(0,400,0));
  require(world.bodyCount()==emptyCount+body->partCount(),"each physical part must belong to the shared world");
  const auto start=position(body->pelvisTransform());
  for(int i=0;i<60;++i) { world.tick(17); finite(body->state()); joints(fixture,*body,i); }
  require(position(body->pelvisTransform()).y<start.y-200.f,"a body without dropped items must fall under gravity");
  for(int i=0;i<1800 && !body->isSleeping();++i) { world.tick(17); finite(body->state()); }
  require(body->isSleeping() && body->settled(),"a corpse must settle and sleep on the floor within thirty seconds");
  require(world.bodyCount()==emptyCount+body->partCount(),"sleep must retain every corpse part in the world");
  const auto resting=body->state();
  const auto pelvis=position(body->pelvisTransform());
  require(pelvis.y>-5.f && pelvis.y<150.f,"the pelvis must rest above the floor instead of passing through it");
  const auto hit=world.rayCast(pelvis+Tempest::Vec3(0,300,0),pelvis-Tempest::Vec3(0,300,0),
                              1u<<DynamicWorld::C_Ragdoll);
  require(hit.hasHit,"a sleeping corpse must remain queryable in its explicit layer");
  for(int i=0;i<60;++i) world.tick(17);
  same(body->state(),resting,.001f);
  body->addImpulse({150,100,0});
  require(!body->isSleeping(),"a new impulse must wake a sleeping corpse");
  for(int i=0;i<60;++i) world.tick(17);
  require((position(body->pelvisTransform())-pelvis).length()>20.f,"woken corpse must move after a mass-distributed impulse");
  joints(fixture,*body);
  body.reset();
  require(world.bodyCount()==emptyCount,"destroying the owner must remove all ragdoll parts and joints");
  }

void testSlopeAndCliff() {
  Fixture fixture;
  {
    CollisionWorld world;
    auto tilted=at(0,-50,0); tilted.rotateOZ(-35);
    auto slope=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),tilted,
                                     .01f,DynamicWorld::C_Landscape);
    auto body=fixture.create(world,at(0,100,0));
    const auto start=position(body->pelvisTransform());
    for(int i=0;i<300;++i) { world.tick(17); finite(body->state()); }
    const auto end=position(body->pelvisTransform());
    require(end.x>start.x+75.f && end.y<start.y-100.f,"a limp body must move down a low-friction slope");
    joints(fixture,*body);
    }
  {
    CollisionWorld world;
    auto platform=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(1,.5f,2)),at(0,-50,0),
                                        .75f,DynamicWorld::C_Landscape);
    auto body=fixture.create(world,at(250,300,0),{150,0,0});
    const auto start=position(body->pelvisTransform());
    for(int i=0;i<90;++i) { world.tick(17); finite(body->state()); }
    const auto end=position(body->pelvisTransform());
    require(end.y<start.y-600.f,"a body beyond a cliff must continue falling instead of standing on its NPC capsule");
    require(end.x>start.x+100.f,"initial NPC velocity must transfer to the falling body in cm/s");
    joints(fixture,*body);
    }
  }

void testStateRoundTripAndAtomicRejection() {
  Fixture fixture;
  CollisionWorld originalWorld, restoredWorld;
  auto originalGround=floor(originalWorld), restoredGround=floor(restoredWorld);
  auto original=fixture.create(originalWorld,at(0,400,0),{120,25,-40});
  auto restored=fixture.create(restoredWorld,at(0,400,0));
  for(int i=0;i<10;++i) { originalWorld.tick(17); restoredWorld.tick(17); }
  const auto moving=original->state();
  require(!original->isSleeping(),"roundtrip fixture must be moving");
  require(restored->restore(moving),"a compatible moving snapshot must restore");
  same(restored->state(),moving,.001f);
  // Both worlds have the same accumulated fixed-step remainder. Compare continuation,
  // not just serialization of fields that never reach the solver.
  for(int i=0;i<30;++i) { originalWorld.tick(17); restoredWorld.tick(17); }
  same(restored->state(),original->state(),.1f);

  const auto before=restored->state();
  std::vector<Ragdoll::State> invalid;
  auto state=before; ++state.version; invalid.push_back(state);
  state=before; state.skeleton="OTHER.MDS"; invalid.push_back(state);
  state=before; state.parts.pop_back(); invalid.push_back(state);
  state=before; state.parts[1].bone=state.parts[0].bone; invalid.push_back(state);
  state=before; state.parts[0].bone=uint32_t(fixture.skeleton.nodes.size()+10); invalid.push_back(state);
  state=before; state.parts[0].transform.set(3,1,std::numeric_limits<float>::quiet_NaN()); invalid.push_back(state);
  state=before; state.parts[0].linearVelocity.y=std::numeric_limits<float>::infinity(); invalid.push_back(state);
  state=before; state.parts[0].angularVelocity.x=std::numeric_limits<float>::quiet_NaN(); invalid.push_back(state);
  state=before; state.parts[0].referenceTransform.set(3,0,std::numeric_limits<float>::quiet_NaN()); invalid.push_back(state);
  state=before; state.parts[0].referenceTransform.scale(-1.f,1.f,1.f); invalid.push_back(state);
  state=before; state.parts[0].referenceTransform.scale(.8f,1.f,1.f); invalid.push_back(state);
  state=before; state.parts[0].referenceTransform.set(0,1,.1f); invalid.push_back(state);
  state=before; state.parts[0].centerOfMassMeters.x=std::numeric_limits<float>::quiet_NaN(); invalid.push_back(state);
  state=before; state.parts[0].centerOfMassMeters.y+=1.f; invalid.push_back(state);
  state=before; state.parts[0].rotation={0,0,0,0}; invalid.push_back(state);
  state=before; state.parts[0].linearVelocityMeters.x+=1.f; invalid.push_back(state);
  require(!before.ancestors.empty(),"fresh synthetic state must include its nonphysical BIP ancestor");
  state=before; state.ancestors.pop_back(); invalid.push_back(state);
  state=before; state.ancestors.push_back(state.ancestors.front()); invalid.push_back(state);
  state=before; state.ancestors[0].anchorBone=uint32_t(fixture.skeleton.nodes.size()+1); invalid.push_back(state);
  state=before; state.ancestors[0].bone=state.parts.front().bone; invalid.push_back(state);
  state=before; state.ancestors[0].referenceTransform.set(3,0,std::numeric_limits<float>::quiet_NaN()); invalid.push_back(state);
  state=before; state.ancestors[0].referenceTransform.scale(.8f,1.f,1.f); invalid.push_back(state);
  state=before; state.legacyAncestors=true; invalid.push_back(state);
  state=before; state.rootTransform.scale(2); invalid.push_back(state);
  for(const auto& bad:invalid) {
    require(!restored->restore(bad),"incompatible or nonfinite state must be rejected");
    same(restored->state(),before,.001f);
    }

  for(int i=0;i<1800 && !original->isSleeping();++i) originalWorld.tick(17);
  require(original->isSleeping(),"save roundtrip must include a real sleeping body");
  const auto sleeping=original->state();
  require(restored->restore(sleeping) && restored->isSleeping(),"restoring a sleeping snapshot must preserve sleep");
  same(restored->state(),sleeping,.001f);
  for(int i=0;i<30;++i) restoredWorld.tick(17);
  same(restored->state(),sleeping,.001f);
  }

void testMovedStateRebaseAndReference() {
  Fixture fixture;
  const Tempest::Vec3 displacement{1250,200,-800};
  const auto translated=[&](Tempest::Matrix4x4 matrix) {
    matrix.set(3,0,matrix.at(3,0)+displacement.x);
    matrix.set(3,1,matrix.at(3,1)+displacement.y);
    matrix.set(3,2,matrix.at(3,2)+displacement.z);
    return matrix;
    };
  const auto translatedState=[&](Ragdoll::State state) {
    require(Ragdoll::translateState(fixture.skeleton,state,displacement),"explicit state translation must retain consistent native precision");
    // Birth references describe the original geometry, not the current location.
    return state;
    };
  for(const bool anisotropic:{false,true}) {
    CollisionWorld originalWorld, restoredWorld;
    auto ground=floor(originalWorld);
    auto movedGround=restoredWorld.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),
      translated(at(0,-50,0)),.75f,DynamicWorld::C_Landscape);
    auto object=at(0,400,0); object.rotateOY(31);
    if(anisotropic) object.scale(.9f,1.f,1.f);
    auto original=fixture.create(originalWorld,object,{120,25,-40});
    auto restored=fixture.create(restoredWorld,translated(object));
    const auto references=original->state();
    for(int i=0;i<10;++i) { originalWorld.tick(17); restoredWorld.tick(17); }
    const auto moved=translatedState(original->state());
    require(moved.version==3,"current bodies must save precise native poses plus affine creation references");
    require(restored->restore(moved),"moved compatible State3 snapshot must restore");
    same(restored->state(),moved,.001f);
    for(size_t part=0;part<moved.parts.size();++part)
      for(size_t i=0;i<16;++i)
        near(moved.parts[part].referenceTransform.data()[i],references.parts[part].referenceTransform.data()[i],0,
             "moving a body must leave every creation reference component unchanged");
    for(int i=0;i<30;++i) { originalWorld.tick(17); restoredWorld.tick(17); }
    same(restored->state(),translatedState(original->state()),.1f);
    }
  }

void testInvalidCreationAndScale() {
  Fixture fixture;
  CollisionWorld world;
  auto ground=floor(world);
  const auto count=world.bodyCount();
  auto pose=fixture.skeleton.tr;
  const auto object=at(0,300,0);
  for(auto& matrix:pose) matrix=object*matrix;
  require(!world.createRagdoll(fixture.skeleton,fixture.zones,nullptr,pose.size(),object),"null pose must not create parts");
  require(!world.createRagdoll(fixture.skeleton,fixture.zones,pose.data(),pose.size()-1,object),"incomplete pose must not create parts");
  auto invalidPose=pose;
  invalidPose[1].set(3,1,std::numeric_limits<float>::quiet_NaN());
  require(!world.createRagdoll(fixture.skeleton,fixture.zones,invalidPose.data(),invalidPose.size(),object),"nonfinite pose must not create parts");
  require(world.bodyCount()==count,"rejected creations must leave no partial bodies");
  auto scaled=at(0,300,0); scaled.scale(1.5f);
  auto body=fixture.create(world,scaled);
  std::vector<Tempest::Matrix4x4> output;
  require(body->getPose(output),"scaled ragdoll pose must be available");
  for(auto bone:body->boneIndices()) {
    const auto& matrix=output[bone];
    const auto axis=Tempest::Vec3(matrix.at(0,0),matrix.at(0,1),matrix.at(0,2));
    near(axis.length(),1.5f,.0001f,"physical bones must retain visual uniform scale");
    }
  finite(body->state());
  }

void replayHumanAnatomy(const Skeleton& skeleton, const HitZones& zones) {
  const auto rotateVector=[](const Tempest::Matrix4x4& matrix, const Tempest::Vec3& value) {
    const auto* m=matrix.data();
    return Tempest::Vec3(m[0]*value.x+m[4]*value.y+m[8]*value.z,
                        m[1]*value.x+m[5]*value.y+m[9]*value.z,
                        m[2]*value.x+m[6]*value.y+m[10]*value.z);
    };
  const auto inverseRotate=[](const Tempest::Matrix4x4& matrix, const Tempest::Vec3& value) {
    const auto* m=matrix.data();
    return Tempest::Vec3(m[0]*value.x+m[1]*value.y+m[2]*value.z,
                        m[4]*value.x+m[5]*value.y+m[6]*value.z,
                        m[8]*value.x+m[9]*value.y+m[10]*value.z);
    };
  size_t controls=0;
  for(size_t bone=0;bone<skeleton.nodes.size();++bone) {
    const auto& name=skeleton.nodes[bone].name;
    if(name.find("CALF")==std::string::npos && name.find("FOREARM")==std::string::npos)
      continue;
    const size_t parent=skeleton.nodes[bone].parent;
    size_t endpoint=skeleton.nodes.size();
    for(size_t child=0;child<skeleton.nodes.size();++child)
      if(skeleton.nodes[child].parent==bone && (skeleton.nodes[child].name.find("FOOT")!=std::string::npos ||
                                               skeleton.nodes[child].name.find("HAND")!=std::string::npos)) endpoint=child;
    require(parent<skeleton.nodes.size() && endpoint<skeleton.nodes.size(),"actual Human hinge needs its anatomical parent and endpoint");
    const auto pivot=position(skeleton.tr[bone]);
    const auto incoming=Tempest::Vec3::normalize(pivot-position(skeleton.tr[parent]));
    const auto outgoing=Tempest::Vec3::normalize(position(skeleton.tr[endpoint])-pivot);
    const Tempest::Vec3 cross {incoming.y*outgoing.z-incoming.z*outgoing.y,
                              incoming.z*outgoing.x-incoming.x*outgoing.z,
                              incoming.x*outgoing.y-incoming.y*outgoing.x};
    const auto axis=Tempest::Vec3::normalize(cross);
    const float bindBend=std::atan2(cross.length(),Tempest::Vec3::dotProduct(incoming,outgoing))*180.f/float(3.14159265358979323846);
    require(bindBend>5.f && bindBend<45.f,"actual Human bind must exercise a nonzero anatomical hinge offset");
    for(const float yaw:{0.f,37.f})
      for(const float wanted:{-30.f,30.f}) {
        auto object=at(0,700,0); object.rotateOY(yaw); object.scale(.9f,1.f,1.f);
        auto rotation=Tempest::Matrix4x4::mkIdentity(); rotation.rotate(wanted-bindBend,axis.x,axis.y,axis.z);
        const auto around=at(pivot.x,pivot.y,pivot.z)*rotation*at(-pivot.x,-pivot.y,-pivot.z);
        std::vector<Tempest::Matrix4x4> worldPose;
        for(size_t node=0;node<skeleton.nodes.size();++node) {
          bool child=false;
          for(size_t ancestor=node;ancestor<skeleton.nodes.size();ancestor=skeleton.nodes[ancestor].parent)
            if(ancestor==bone) { child=true; break; }
          worldPose.push_back(object*(child ? around*skeleton.tr[node] : skeleton.tr[node]));
          }
        CollisionWorld world;
        auto body=world.createRagdoll(skeleton,zones,worldPose.data(),worldPose.size(),object);
        require(body!=nullptr && body->valid(),"actual bent/yawed Human reference must create");
        const auto angle=[&] {
          const auto state=body->state();
          const Ragdoll::PartState* p=nullptr; const Ragdoll::PartState* c=nullptr;
          for(const auto& part:state.parts) { if(part.bone==parent) p=&part; if(part.bone==bone) c=&part; }
          require(p!=nullptr && c!=nullptr,"actual hinge native parent/child frames must be present");
          // Bring the same MODEL reference into each current native frame using
          // its canonical bind-local coordinates. Global yaw and parent tumbling
          // cancel; visual affine stretch never enters the native angle oracle.
          const auto axisNow=rotateVector(p->transform,inverseRotate(skeleton.tr[parent],axis));
          const auto planeParent=rotateVector(p->transform,inverseRotate(skeleton.tr[parent],outgoing));
          const auto planeChild=rotateVector(c->transform,inverseRotate(skeleton.tr[bone],outgoing));
          const Tempest::Vec3 crossed {planeParent.y*planeChild.z-planeParent.z*planeChild.y,
                                       planeParent.z*planeChild.x-planeParent.x*planeChild.z,
                                       planeParent.x*planeChild.y-planeParent.y*planeChild.x};
          return bindBend+std::atan2(Tempest::Vec3::dotProduct(axisNow,crossed),Tempest::Vec3::dotProduct(planeParent,planeChild))*
            180.f/float(3.14159265358979323846);
          };
        const float initial=angle();
        near(initial,wanted,.02f,"actual Human initial anatomical angle must survive native polar conversion and global yaw");
        for(int tick=0;tick<60;++tick) { world.tick(17); finite(body->state()); }
        const float final=angle();
        std::printf("actual Human hinge=%s yaw_deg=%.9g bind_deg=%.9g wanted_deg=%.9g initial_deg=%.9g after60_deg=%.9g\n",
                    name.c_str(),double(yaw),double(bindBend),double(wanted),double(initial),double(final));
        if(wanted<0) near(final,0,5.f,"actual Human hyperextension must settle at its anatomical straight limit");
        else near(final,30,5.f,"actual mirrored Human elbow/knee must retain allowed positive flexion");
        ++controls;
        }
    }
  require(controls==16,"actual Human must cover both knees/elbows, hyperextension/flexion and global yaw");
  }

void replayCreation(const char* input) {
  std::ifstream file(input);
  require(bool(file),"creation replay input must be readable");
  size_t cases=0;
  std::string line;
  while(std::getline(file,line)) {
    rapidjson::Document capture;
    capture.Parse(line.c_str());
    require(!capture.HasParseError() && capture.IsObject(),"creation replay must contain JSON objects");
    require(capture.HasMember("nodes") && capture["nodes"].IsArray() && capture.HasMember("skeleton") && capture["skeleton"].IsString(),
            "creation replay needs an actual skeleton");
    const auto matrix=[](const rapidjson::Value& array) {
      require(array.IsArray() && array.Size()==16,"creation replay matrix must have sixteen components");
      float values[16];
      for(size_t i=0;i<16;++i) {
        require(array[rapidjson::SizeType(i)].IsNumber(),"creation replay matrix component must be numeric");
        values[i]=float(array[rapidjson::SizeType(i)].GetDouble());
        require(std::isfinite(values[i]),"creation replay matrix must be finite");
        }
      Tempest::Matrix4x4 result; result.setData(values); return result;
      };
    const auto vector=[](const rapidjson::Value& array) {
      require(array.IsArray() && array.Size()==3,"creation replay vector must have three components");
      for(const auto& value:array.GetArray()) require(value.IsNumber() && std::isfinite(value.GetDouble()),"creation replay vector must be finite");
      return Tempest::Vec3(float(array[0].GetDouble()),float(array[1].GetDouble()),float(array[2].GetDouble()));
      };
    zenkit::ModelHierarchy source;
    // Captured Skeleton::nodes already include root_translation on the root.
    // Adding it again would change the fixture instead of replaying the input.
    source.root_translation={0,0,0};
    source.bbox.min={float(capture["bbox"][0][0].GetDouble()),float(capture["bbox"][0][1].GetDouble()),float(capture["bbox"][0][2].GetDouble())};
    source.bbox.max={float(capture["bbox"][1][0].GetDouble()),float(capture["bbox"][1][1].GetDouble()),float(capture["bbox"][1][2].GetDouble())};
    source.collision_bbox.min={float(capture["collision_bbox"][0][0].GetDouble()),float(capture["collision_bbox"][0][1].GetDouble()),float(capture["collision_bbox"][0][2].GetDouble())};
    source.collision_bbox.max={float(capture["collision_bbox"][1][0].GetDouble()),float(capture["collision_bbox"][1][1].GetDouble()),float(capture["collision_bbox"][1][2].GetDouble())};
    std::vector<Tempest::Matrix4x4> worldPose;
    for(const auto& node:capture["nodes"].GetArray()) {
      zenkit::ModelHierarchyNode output;
      output.name=node["name"].GetString(); output.parent_index=int16_t(node["parent"].GetInt());
      const auto local=matrix(node["local"]);
      std::memcpy(&output.transform,&local,sizeof(local));
      source.nodes.push_back(std::move(output)); worldPose.push_back(matrix(node["world"]));
      }
    Skeleton skeleton(source,nullptr,capture["skeleton"].GetString());
    for(size_t i=0;i<skeleton.tr.size();++i) {
      const auto expected=matrix(capture["nodes"][rapidjson::SizeType(i)]["bind"]);
      for(size_t c=0;c<16;++c)
        near(skeleton.tr[i].data()[c],expected.data()[c],0,"reconstructed capture bind must match every original float");
      }
    HitZones zones; zones.setSkeleton(&skeleton);
    require(zones.isValid(),"captured actual skeleton must reproduce valid canonical HitZones");
    const auto object=matrix(capture["object"]);
    const auto actorOrigin=position(object);
    float lowest=std::numeric_limits<float>::max();
    for(const auto& frame:worldPose) lowest=std::min(lowest,position(frame).y);
    const auto groundFrame=at(actorOrigin.x,lowest-250,actorOrigin.z);
    CollisionWorld world;
    auto ground=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),groundFrame,.75f,DynamicWorld::C_Landscape);
    auto body=world.createRagdoll(skeleton,zones,worldPose.data(),worldPose.size(),object,vector(capture["velocity"]));
    require(body!=nullptr && body->valid(),"exact captured real-model input must create a native body");
    require(body->partCount()==capture["physical_owners"].Size(),"actual captured physical owner coverage must match");
    near(body->mass(),Ragdoll::speciesMass(skeleton.name()),.001f,"actual native body must use its species mass");
    std::vector<Tempest::Matrix4x4> initial;
    require(body->getPose(initial),"actual native initial visual frames must be available");
    float positionError=0,basisError=0;
    for(auto bone:body->poseIndices()) {
      positionError=std::max(positionError,(position(initial[bone])-position(worldPose[bone])).length());
      for(size_t c=0;c<16;++c)
        if(c<12 || c==15) basisError=std::max(basisError,std::abs(initial[bone].data()[c]-worldPose[bone].data()[c]));
      }
    // 23,000 cm captured coordinates have binary32 ULP about .002 cm. This
    // creation check covers that conversion; the save/load bound stays .001 cm.
    std::printf("creation replay case=%zu model=%.*s parts=%zu mass_kg=%.9g initial_position_error_cm=%.9g initial_basis_error=%.9g\n",
                cases,int(skeleton.name().size()),skeleton.name().data(),body->partCount(),double(body->mass()),double(positionError),double(basisError));
    near(positionError,0,.005f,"native creation must preserve actual world bone positions within conversion precision");
    near(basisError,0,.00001f,"native creation must preserve actual affine bone orientation and scale");
    const auto state=body->state();
    require(state.version==3,"actual nonuniform bodies need precise native State3 with affine/ancestor references");
    require(body->restore(state),"actual captured moving state must restore on the native body");
    same(body->state(),state,.001f);
    for(int frame=0;frame<30;++frame) { world.tick(17); finite(body->state()); }
    const auto coldRoundtrip=[&](const char* phase) {
      const auto saved=body->state();
      std::vector<Tempest::Matrix4x4> before;
      require(body->getPose(before),"captured actor must expose all native and ancestor frames before a cold restore");
      CollisionWorld loadedWorld;
      auto loadedGround=loadedWorld.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),groundFrame,.75f,DynamicWorld::C_Landscape);
      auto loaded=loadedWorld.createRagdoll(skeleton,zones,worldPose.data(),worldPose.size(),object);
      require(loaded && loaded->restore(saved),"actual large-world actor must restore into a fresh native world");
      same(loaded->state(),saved,.001f);
      require(loaded->poseIndices()==body->poseIndices(),"cold restore must retain every physical and anchored ancestor frame");
      const auto compareFrames=[&] {
        std::vector<Tempest::Matrix4x4> after;
        require(loaded->getPose(after),"cold restored actor must expose final native and ancestor frames");
        float maxPosition=0,maxBasis=0;
        for(auto bone:loaded->poseIndices()) {
          maxPosition=std::max(maxPosition,(position(after[bone])-position(before[bone])).length());
          for(size_t c=0;c<16;++c) if(c<12 || c==15)
            maxBasis=std::max(maxBasis,std::abs(after[bone].data()[c]-before[bone].data()[c]));
          }
        near(maxPosition,0,.001f,"cold restore must preserve all native/ancestor world frames within the unchanged save bound");
        near(maxBasis,0,.00001f,"cold restore must preserve all native/ancestor frame bases");
        return maxPosition;
        };
      const float error=compareFrames();
      if(body->isSleeping()) {
        for(int tick=0;tick<60;++tick) loadedWorld.tick(17);
        same(loaded->state(),saved,.001f);
        compareFrames();
        }
      std::printf("State3 cold case=%zu phase=%s mapped_frames=%zu exact_native_fields=PASS max_world_position_error_cm=%.9g\n",
                  cases,phase,loaded->poseIndices().size(),double(error));
      // Explicit teleport keeps immutable birth references while rebuilding COM
      // from the requested public origin. Large -> near-zero cancellation is
      // deliberately exercised before a new native candidate is constructed.
      for(const auto target:{Tempest::Vec3(10,200,-12),Tempest::Vec3(-23000,900,-12000),Tempest::Vec3(30000,1100,25000)}) {
        auto moved=saved;
        const auto displacement=target-position(saved.rootTransform);
        const auto requestedOrigin=position(saved.rootTransform)+displacement;
        if(target.x==10) {
          auto naive=saved;
          for(size_t c=0;c<3;++c) {
            const float delta=c==0 ? displacement.x : c==1 ? displacement.y : displacement.z;
            naive.rootTransform.set(3,int(c),naive.rootTransform.at(3,int(c))+delta);
            for(auto& part:naive.parts) part.transform.set(3,int(c),part.transform.at(3,int(c))+delta);
            }
          for(auto& part:naive.parts) part.centerOfMassMeters+=displacement*.01f;
          require(!loaded->restore(naive),"independent large-world cm/COM translation must be rejected rather than weakening the precision guard");
          }
        require(Ragdoll::relocateState(skeleton,moved,target),"large-world active/dormant absolute teleport must rebuild consistent COM fields");
        // Keep the displacement-input rounding visible. The absolute API must
        // retain the original destination, without inheriting that error.
        std::printf("State3 teleport case=%zu phase=%s target_cm=[%.9g,%.9g,%.9g] displacement_rounding_cm=%.9g export_error_cm=%.9g\n",
                    cases,phase,double(target.x),double(target.y),double(target.z),double((requestedOrigin-target).length()),
                    double((position(moved.rootTransform)-target).length()));
        near((position(moved.rootTransform)-target).length(),0,.001f,"absolute state teleport must retain its requested destination");
        for(size_t p=0;p<moved.parts.size();++p)
          for(size_t c=0;c<16;++c)
            near(moved.parts[p].referenceTransform.data()[c],saved.parts[p].referenceTransform.data()[c],0,"teleport must not change native geometry birth references");
        for(size_t a=0;a<moved.ancestors.size();++a)
          for(size_t c=0;c<16;++c)
            near(moved.ancestors[a].referenceTransform.data()[c],saved.ancestors[a].referenceTransform.data()[c],0,"teleport must not change ancestor birth references");
        require(loaded->restore(moved),"teleported large-world DTO must restore atomically into native bodies");
        same(loaded->state(),moved,.001f);
        }
      };
    require(!body->isSleeping(),"actual corpus awake roundtrip must include moving native parts");
    coldRoundtrip("awake");
    for(int tick=0;tick<2000 && !body->isSleeping();++tick) { world.tick(17); finite(body->state()); }
    require(body->isSleeping(),"actual corpus sleeping roundtrip must use a settled native actor");
    coldRoundtrip("sleeping");
    if(cases==0 && skeleton.name().find("HUMAN")!=std::string_view::npos)
      replayHumanAnatomy(skeleton,zones);
    ++cases;
    }
  require(cases>0,"creation replay must not silently accept an empty corpus");
  std::printf("Actual creation corpus passed: %zu cases\n",cases);
  }

void testAnatomicalKneeWithLongitudinalBoneAxis() {
  auto source=hierarchy();
  // Keep the thigh/calf/foot origins anatomically straight, but align the calf's
  // LOCAL X with the limb, as Gothic's actual MAN skeleton does. A local-axis
  // projection must not replace the stable model sagittal flexion direction.
  auto calf=at(0,-42,0); calf.rotateOZ(90);
  auto foot=at(-40,0,0); foot.rotateOZ(-90);
  std::memcpy(&source.nodes[13].transform,&calf,sizeof(calf));
  std::memcpy(&source.nodes[14].transform,&foot,sizeof(foot));
  Skeleton skeleton(source,nullptr,"HUMANS_LONGITUDINAL_BIND.MDS");
  HitZones zones; zones.setSkeleton(&skeleton);
  auto object=at(0,700,0); object.scale(.9f,1.f,1.f);
  for(const float degrees:{-30.f,30.f}) {
    std::vector<Tempest::Matrix4x4> worldPose;
    for(const auto& frame:skeleton.tr) worldPose.push_back(object*frame);
    const auto pivot=position(worldPose[13]);
    auto turn=Tempest::Matrix4x4::mkIdentity(); turn.rotateOX(degrees);
    const auto around=at(pivot.x,pivot.y,pivot.z)*turn*at(-pivot.x,-pivot.y,-pivot.z);
    for(const auto bone:{13,14,15}) worldPose[size_t(bone)]=around*worldPose[size_t(bone)];
    CollisionWorld world;
    auto body=world.createRagdoll(skeleton,zones,worldPose.data(),worldPose.size(),object);
    require(body!=nullptr && body->valid(),"longitudinal-axis anatomical fixture must create");
    const auto kneeAngle=[&] {
      std::vector<Tempest::Matrix4x4> pose; require(body->getPose(pose),"knee pose must be available");
      const auto incoming=Tempest::Vec3::normalize(position(pose[13])-position(pose[12]));
      const auto outgoing=Tempest::Vec3::normalize(position(pose[14])-position(pose[13]));
      const float x=incoming.y*outgoing.z-incoming.z*outgoing.y;
      return std::atan2(x,Tempest::Vec3::dotProduct(incoming,outgoing))*180.f/float(3.14159265358979323846);
      };
    const float initial=kneeAngle();
    near(initial,degrees,.01f,"initial geometric knee bend must be independent of the bone's longitudinal local axis");
    for(int tick=0;tick<60;++tick) { world.tick(17); finite(body->state()); }
    const float final=kneeAngle();
    std::printf("anatomical calf bindX=longitudinal input_deg=%.9g initial_deg=%.9g after60_deg=%.9g\n",
                double(degrees),double(initial),double(final));
    if(degrees<0)
      require(final>-5.f && final<15.f,"a knee must correct thirty-degree anatomical hyperextension toward its straight limit");
    else
      require(final>5.f && final<60.f,"a knee must allow positive anatomical flexion rather than locking the projected local-axis plane");
    }
  }

void testPoseOverlayAndRecovery() {
  Fixture fixture;
  Pose pose;
  pose.setSkeleton(&fixture.skeleton);
  auto object=at(1100,120,-800); object.rotateOY(37);
  pose.setObjectMatrix(object,true);
  pose.update(1000,true);
  std::vector<Tempest::Matrix4x4> animated(pose.transform(),pose.transform()+pose.boneCount());
  auto physical=animated;
  physical[1].set(3,0,physical[1].at(3,0)+75.f);
  physical[1].set(3,1,physical[1].at(3,1)-30.f);
  physical[1].rotateOZ(42);
  require(pose.setRagdollPose(&fixture.skeleton,physical,{1}),"world-space physical pelvis must overlay the expected skeleton");
  pose.update(1016,true);
  for(size_t i=0;i<16;++i)
    near(pose.bone(1).data()[i],physical[1].data()[i],.0001f,"mapped bone must take the physical world frame exactly");
  auto inverse=animated[1]; inverse.inverse();
  const auto expectedSpine=physical[1]*(inverse*animated[2]);
  for(size_t i=0;i<16;++i)
    near(pose.bone(2).data()[i],expectedSpine.data()[i],.0002f,"unmapped child must retain its animated relation to the physical parent");
  std::vector<Tempest::Matrix4x4> shown(pose.transform(),pose.transform()+pose.boneCount());
  pose.clearRagdollPose(true,1016);
  require(!pose.hasRagdollPose() && pose.isRagdollBlending(),"physical overlay must transfer to an animation transition");
  pose.update(1016,true);
  for(size_t b=0;b<shown.size();++b)
    near((position(pose.bone(b))-position(shown[b])).length(),0,.001f,"first recovery frame must preserve every displayed world-bone position");
  for(uint64_t tick=1032;tick<=2000;tick+=16) pose.update(tick,true);
  require(!pose.isRagdollBlending(),"the physical-to-animation transition must finish");
  for(size_t b=0;b<animated.size();++b)
    near((position(pose.bone(b))-position(animated[b])).length(),0,.001f,"finished recovery must return to the animated pose");
  auto malformed=physical; malformed[1].set(3,0,std::numeric_limits<float>::quiet_NaN());
  require(!pose.setRagdollPose(&fixture.skeleton,malformed,{1}) && !pose.hasRagdollPose(),"nonfinite physical overlay must not reach rendering");
  require(!pose.setRagdollPose(&fixture.skeleton,physical,{physical.size()+1}),"out-of-range physical bone must be rejected");
  }

void testRecoveryMatchesAnimBlendAndSaves() {
  constexpr size_t count=3;
  zenkit::AnimationSample from[count],to[count],physical[count],ordinary[count];
  for(size_t i=0;i<count;++i) {
    from[i].rotation={1,0,0,0}; from[i].position={float(i*20),float(i*13),float(i*8)};
    to[i].rotation={std::cos(.3f*float(i+1)),std::sin(.3f*float(i+1)),0,0};
    to[i].position={float(i*4),float(i*18),float(i*6)};
    physical[i]=ordinary[i]=from[i];
    }
  Animation::Sequence oldAnimation,newAnimation;
  oldAnimation.name="old"; newAnimation.name="new";
  AnimBlend baseline,recovery;
  baseline.reset(count); recovery.reset(count);
  for(size_t i=0;i<count;++i) baseline.drive(i,oldAnimation,1000,from[i],from[i],60);
  baseline.apply(from,ordinary,count,1000);
  baseline.apply(from,ordinary,count,1016);
  for(size_t i=0;i<count;++i) baseline.drive(i,newAnimation,1016,to[i],to[i],60);
  recovery.transitionFrom(from,count,1016);
  for(uint64_t tick=1016;tick<=1600;tick+=16) {
    recovery.apply(to,physical,count,tick); baseline.apply(to,ordinary,count,tick);
    for(size_t b=0;b<count;++b) {
      const auto actual=mkMatrix(physical[b]), expected=mkMatrix(ordinary[b]);
      for(size_t i=0;i<16;++i)
        near(actual.data()[i],expected.data()[i],.0001f,"physical recovery must follow the ordinary AnimBlend transition");
      }
    }
  require(!recovery.apply(to,physical,count,1800),"physical recovery must settle after the usual blend interval");
  for(const uint64_t saveTick:{uint64_t(2000),uint64_t(2048)}) {
    AnimBlend before,after;
    before.reset(count); before.transitionFrom(from,count,2000);
    zenkit::AnimationSample first[count],second[count];
    std::copy_n(from,count,first);
    if(saveTick>2000) before.apply(to,first,count,saveTick);
    std::vector<uint8_t> bytes;
    { Tempest::MemWriter writer(bytes); Serialize out(writer); out.setEntry("return"); before.saveExternal(out); }
    { Tempest::MemReader reader(bytes); Serialize in(reader); require(in.setEntry("return"),"return archive entry must exist"); after.loadExternal(in,count); }
    std::copy_n(first,count,second);
    for(uint64_t tick=saveTick;tick<2400;tick+=16) {
      before.apply(to,first,count,tick); after.apply(to,second,count,tick);
      for(size_t b=0;b<count;++b)
        require(std::memcmp(&first[b],&second[b],sizeof(first[b]))==0,"pending and mid-curve saved transitions must continue exactly");
      }
    }
  }

void testMalformedRecoveryArchive() {
  // Construct the actual optional-entry wire format independently of AnimBlend's private Curve.
  // The valid control is 10*(1-10*u^3+15*u^4-6*u^5), u=t/.5, with no rotation or side offset.
  struct Curve {
    float t1=0, x0=0, v0=0, a0=0, A=0, B=0, C=0;
    };
  const Curve zero;
  const AnimBlend::Sample identity{{0,0,0},{1,0,0,0}};
  struct Archive {
    uint64_t outTime=1000, lastTime=0, externalStart=1000, start=1000;
    bool clockPending=false, active=true, fade=false;
    float end=.5f, fadeTime=0;
    Curve externalFade, rot, rotSide, pos{.5f,10.f,0,0,-1920.f,2400.f,-800.f}, posSide;
    Tempest::Vec3 axis{0,0,0}, axisSide{0,0,0}, dir{1,0,0}, dirSide{0,0,0};
    AnimBlend::Sample from{{0,0,0},{1,0,0,0}}, last{{0,0,0},{1,0,0,0}};
    std::vector<AnimBlend::Sample> external, previous;
    uint64_t previousTime=0;
    };
  const auto write=[&](const Archive& state) {
    std::vector<uint8_t> bytes;
    {
      Tempest::MemWriter writer(bytes); Serialize out(writer); out.setEntry("return");
      out.write(uint32_t(1),state.outTime,state.lastTime,state.externalStart,state.clockPending);
      const auto curve=[&](const Curve& c) { out.write(c.t1,c.x0,c.v0,c.a0,c.A,c.B,c.C); };
      curve(state.externalFade);
      out.write(state.active,state.start,state.end,state.fade,state.from,state.fadeTime);
      curve(state.rot); curve(state.rotSide); curve(state.pos); curve(state.posSide);
      out.write(state.axis,state.axisSide,state.dir,state.dirSide,state.last);
      out.write(uint32_t(state.external.size()));
      for(const auto& sample:state.external) out.write(sample);
      out.write(state.previousTime,uint32_t(state.previous.size()));
      for(const auto& sample:state.previous) out.write(sample);
    }
    return bytes;
    };
  const auto read=[&](const Archive& state, AnimBlend& blend) {
    const auto bytes=write(state);
    Tempest::MemReader reader(bytes); Serialize in(reader);
    require(in.setEntry("return"),"handcrafted recovery entry must exist");
    blend.loadExternal(in,1);
    };
  const auto reject=[&](const Archive& state, const char* message) {
    bool rejected=false;
    try { AnimBlend blend; read(state,blend); }
    catch(const std::runtime_error&) { rejected=true; }
    require(rejected,message);
    };

  const Archive control;
  AnimBlend valid; read(control,valid);
  AnimBlend::Sample out=identity;
  valid.apply(&identity,&out,1,1000); near(out.position.x,10.f,.00001f,"canonical recovery must retain its initial offset");
  valid.apply(&identity,&out,1,1250); near(out.position.x,5.f,.00001f,"canonical recovery must keep the quintic midpoint");
  valid.apply(&identity,&out,1,1500); near(out.position.x,0.f,.00001f,"canonical recovery must end at the raw pose");

  Archive state=control;
  state.pos={.3f,1.e38f,0,0,0,0,0}; state.end=.3f; state.dir={100,0,0};
  volatile float hugeOffset=state.pos.x0, hugeDirection=state.dir.x;
  require(!std::isfinite(hugeOffset*hugeDirection),"finite huge offset and direction must reproduce the first-frame overflow control");
  reject(state,"finite huge recovery offset and nonunit direction must be rejected before rendering");
  state.dir={1,0,0};
  reject(state,"a unit direction must not make an out-of-domain recovery offset admissible");

  state=control;
  state.pos={256.f,0,0,0,std::ldexp(1.f,92),-std::ldexp(1.f,100),0}; state.end=256.f;
  volatile float interiorTime=192.f;
  const float interior=((((state.pos.A*interiorTime+state.pos.B)*interiorTime+state.pos.C)*interiorTime+
                         .5f*state.pos.a0)*interiorTime+state.pos.v0)*interiorTime+state.pos.x0;
  require(std::isfinite(state.pos.A) && std::isfinite(state.pos.B) && state.pos.x0==0 &&
          !std::isfinite(interior),"unit-direction polynomial must overflow inside its interval despite finite coefficients and zero endpoint values");
  reject(state,"recovery must reject an interior-overflow polynomial with unit direction and harmless endpoint values");

  state=control; state.pos.A+=1.f;
  reject(state,"finite noncanonical recovery coefficients must be rejected");
  state=control; state.pos.x0=-10.f;
  reject(state,"negative recovery offset length must be rejected");
  state=control; state.pos.v0=7000.f;
  reject(state,"recovery velocity beyond the producer's clipped relative velocity must be rejected");
  state=control; state.pos.t1=.00001f; state.end=state.pos.t1;
  reject(state,"a positive subdomain recovery duration must be rejected");
  state=control; state.pos.t1=601.f; state.end=state.pos.t1;
  reject(state,"recovery duration beyond the bounded save domain must be rejected");
  state=control; state.pos.t1=-.5f;
  reject(state,"negative recovery duration must be rejected");
  state=control; state.pos.C=std::numeric_limits<float>::quiet_NaN();
  reject(state,"nonfinite recovery coefficients must be rejected");
  state=control; state.end=.4f;
  reject(state,"recovery end must agree with all serialized curve durations");
  state=control; state.active=true; state.pos=zero; state.end=0; state.dir={0,0,0};
  reject(state,"an active recovery offset must have a positive interval");
  state=control; state.lastTime=1001;
  reject(state,"recovery history must not lie after the saved output clock");
  state=control; state.externalStart=1001;
  reject(state,"a started recovery must not begin after its saved output clock");
  state=control; state.start=1001;
  reject(state,"a bone recovery start must not underflow the saved output clock");
  state=control; state.clockPending=true;
  reject(state,"pending recovery clock must require its pending external pose");
  state=control; state.previousTime=900;
  reject(state,"an empty previous recovery pose must not retain a velocity clock");
  state=control; state.previousTime=900; state.previous.push_back(identity);
  reject(state,"recovery velocity history must require its matching pending pose");
  state=control; state.dir={2,0,0};
  reject(state,"a used recovery displacement direction must be unit length");
  state=control; state.dir={0,0,0};
  reject(state,"a used recovery displacement direction must not be zero");
  state=control; state.last.rotation={2,0,0,0};
  reject(state,"used recovery history must contain unit quaternions");
  state=control; state.last.rotation={0,0,0,0};
  reject(state,"used recovery history must not contain an unused zero quaternion");
  state=control; state.last.position.x=1.e38f;
  reject(state,"finite recovery samples outside the saved coordinate domain must be rejected");
  state=control; state.fade=true;
  reject(state,"external recovery must not accept an ordinary cross-fade record");
  state=control; state.fadeTime=.5f;
  reject(state,"external recovery must not accept an unused cross-fade duration");

  // A pending seed has not initialized its offsets/history yet. Their default zero quaternions
  // are legitimate only in that unused state; the physical external pose itself is unit-valued.
  Archive pending;
  pending.clockPending=true; pending.active=false; pending.end=0; pending.pos=zero;
  pending.dir={0,0,0}; pending.from.rotation={0,0,0,0}; pending.last.rotation={0,0,0,0};
  pending.external.push_back(identity);
  AnimBlend pendingValid; read(pending,pendingValid);
  require(pendingValid.hasExternalSeed(),"valid pending recovery must retain its uninitialized history and external seed");
  state=pending; state.external[0].rotation={0,0,0,0};
  reject(state,"a pending physical pose must contain a unit quaternion");
  state=pending; state.externalFade.x0=1.f;
  reject(state,"pending recovery must not already contain an initialized fade curve");
  state=pending; state.previousTime=1000; state.previous.push_back(identity);
  reject(state,"pending velocity history must strictly precede the saved output clock");
  state=pending; state.previousTime=749; state.previous.push_back(identity);
  reject(state,"pending velocity history must remain within the measured producer gap");
  state=pending; state.previousTime=984; state.previous.push_back(identity);
  AnimBlend previousValid; read(state,previousValid);
  require(previousValid.hasExternalSeed(),"valid pending physical velocity history must remain loadable");
  state.previous[0].rotation={0,0,0,0};
  reject(state,"used pending physical velocity history must contain a unit quaternion");
  }

void testPoseActualDriverSave() {
  // Real MAN readers, AnimationSolver name resolution, Pose layers and its legacy ZIP entry:
  // an ordinary offset must already be active when the physical handoff takes ownership.
  Resources resources;
  const auto source=hierarchy();
  const size_t count=source.nodes.size();
  zenkit::ModelScript script;
  const char* names[]={"S_BEFORE_A","S_BEFORE_B","S_WOUNDED"};
  auto append=[](std::vector<std::byte>& bytes, const auto& value) {
    const size_t at=bytes.size(); bytes.resize(at+sizeof(value));
    std::memcpy(bytes.data()+at,&value,sizeof(value));
    };
  auto line=[](std::vector<std::byte>& bytes, const char* text) {
    const size_t size=std::strlen(text);
    const size_t at=bytes.size(); bytes.resize(at+size+1);
    std::memcpy(bytes.data()+at,text,size); bytes.back()=std::byte('\n');
    };
  for(size_t kind=0;kind<3;++kind) {
    std::vector<std::byte> bytes,header,samples;
    append(header,uint16_t(0xc)); line(header,names[kind]);
    append(header,uint32_t(0)); append(header,uint32_t(32)); append(header,uint32_t(count));
    append(header,60.f); append(header,60.f); append(header,-200.f); append(header,.01f);
    for(int i=0;i<6;++i) append(header,0.f);
    line(header,names[kind]);
    append(samples,uint32_t(0));
    for(size_t b=0;b<count;++b) append(samples,uint32_t(b));
    for(size_t frame=0;frame<32;++frame)
      for(size_t b=0;b<count;++b) {
        for(int axis=0;axis<3;++axis) append(samples,uint16_t(32767));
        Tempest::Matrix4x4 local;
        std::memcpy(&local,&source.nodes[b].transform,sizeof(local));
        for(int axis=0;axis<3;++axis) {
          const float offset=kind==1 ? (axis==0 ? 20.f : -12.f) :
                             kind==2 ? (axis==1 ? float(frame)*.2f : -10.f) : 0.f;
          const float value=local.at(3,axis)+offset;
          append(samples,uint16_t(std::lround((value+200.f)*100.f)));
          }
        }
    append(bytes,uint16_t(0xa020)); append(bytes,uint32_t(header.size()));
    bytes.insert(bytes.end(),header.begin(),header.end());
    append(bytes,uint16_t(0xa090)); append(bytes,uint32_t(samples.size()));
    bytes.insert(bytes.end(),samples.begin(),samples.end());
    auto* memory=new std::byte[bytes.size()];
    std::memcpy(memory,bytes.data(),bytes.size());
    const std::string file=std::string("POSE_RETURN-")+names[kind]+".MAN";
    auto& vfs=const_cast<zenkit::Vfs&>(Resources::vdfsIndex());
    vfs.mount(zenkit::VfsNode::file(file,zenkit::VfsFileDescriptor(memory,bytes.size(),true)),"/");
    zenkit::MdsAnimation ani{};
    ani.name=names[kind]; ani.next=names[kind]; ani.layer=0;
    ani.direction=zenkit::AnimationDirection::FORWARD;
    ani.first_frame=0; ani.last_frame=31; ani.fps=60;
    script.animations.push_back(std::move(ani));
    }
  Animation animation(script,"POSE_RETURN",false);
  Skeleton skeleton(source,&animation,"POSE_RETURN.MDS");
  AnimationSolver solver;
  solver.setSkeleton(&skeleton);
  const auto old=solver.solveFrm(names[0]);
  const auto moving=solver.solveFrm(names[1]);
  const auto wounded=solver.solveFrm(names[2]);
  require(old && moving && wounded,"synthetic MAN fixture must resolve real saved animation names");
  for(const auto scale:{Tempest::Vec3(1.125f,1.125f,1.125f),Tempest::Vec3(.9f,1.f,1.f)}) {
    auto object=at(100,-20,70); object.rotateOY(29); object.scale(scale);
    for(const uint64_t saveTick:{uint64_t(1064),uint64_t(1112)}) {
    Pose before;
    before.setSkeleton(&skeleton); before.setObjectMatrix(object,true);
    require(before.startAnim(solver,old,0,BS_STAND,Pose::Force,1000),"initial fixture driver must start");
    before.update(1000,true); before.update(1016,true);
    require(before.startAnim(solver,moving,0,BS_STAND,Pose::Force,1032),"second fixture driver must start");
    before.update(1032,true); before.update(1048,true);
    Pose::Probe probe;
    before.probe(probe);
    require(probe.blend.active>0,"full-pose save test must start with a real ordinary inertial offset");
    std::vector<Tempest::Matrix4x4> physical(before.transform(),before.transform()+before.boneCount());
    physical[1].translate(0,-35,12); physical[1].rotateOX(65);
    require(before.setRagdollPose(&skeleton,physical,{1}),"physical handoff fixture must overlay the pelvis");
    before.update(1064,true);
    const auto snapshot=std::vector<Tempest::Matrix4x4>(before.transform(),before.transform()+before.boneCount());
    before.stopAllAnim();
    require(before.startAnim(solver,wounded,0,BS_UNCONSCIOUS,Pose::Force,1064),"wounded pose must take the real layer");
    before.clearRagdollPose(true,1064);
    if(saveTick>1064) before.update(saveTick,true);
    std::vector<uint8_t> bytes;
    {
      Tempest::MemWriter writer(bytes); Serialize out(writer);
      out.setEntry("visual"); before.save(out);
      out.setEntry("ragdoll-recovery"); before.saveRagdollRecovery(out);
    }
    Pose after;
    after.setSkeleton(&skeleton); after.setObjectMatrix(object,true);
    {
      Tempest::MemReader reader(bytes); Serialize in(reader);
      require(in.setEntry("visual"),"real legacy pose entry must load"); after.load(in,solver);
      // Npc::load updates its object/animated transform before reading the optional entry.
      after.update(saveTick,true);
      require(in.setEntry("ragdoll-recovery"),"optional physical return must load separately");
      after.loadRagdollRecovery(in);
    }
    require(after.isInAnim(wounded),"full Pose load must resolve and retain the saved wounded driver");
    for(uint64_t tick=saveTick;tick<=1608;tick+=16) {
      before.update(tick,true); after.update(tick,true);
      for(size_t b=0;b<count;++b) {
        const auto actual=after.bone(b),expected=before.bone(b);
        for(size_t i=0;i<16;++i)
          near(actual.data()[i],expected.data()[i],.0001f,
               "loaded full-pose driver must continue the physical return without changing its target");
        if(tick==1064)
          near((position(expected)-position(snapshot[b])).length(),0,.001f,
               "full driver handoff must preserve every physical bone on its first frame");
        }
      }
    require(!before.isRagdollBlending() && !after.isRagdollBlending(),"saved full-pose returns must both finish");
    require(before.startAnim(solver,moving,0,BS_STAND,Pose::Force,1610) &&
            after.startAnim(solver,moving,0,BS_STAND,Pose::Force,1610),"post-return animation must start normally");
    before.update(1610,true); after.update(1610,true);
    before.probe(probe);
    require(probe.blend.active>0,"ordinary inertialization must resume for a later animation switch");
    for(size_t b=0;b<count;++b)
      for(size_t i=0;i<16;++i)
        near(after.bone(b).data()[i],before.bone(b).data()[i],.0001f,
             "loaded and uninterrupted poses must agree on the next ordinary transition");
      }
    }
  }

void testPoseAffineRecoveryAndSaves() {
  {
    // Stretch can be the only offset: its clock must survive even when all rigid curves are idle.
    zenkit::AnimationSample still;
    still.rotation={1,0,0,0}; still.position={0,0,0};
    auto shown=still;
    AnimBlend clock;
    clock.reset(1); clock.transitionFrom(&still,1,1000);
    require(clock.apply(&still,&shown,1,1000) && clock.externalWeight(1000)==1.f,
            "an affine-only handoff must keep the shared initial blend clock");
    require(clock.apply(&still,&shown,1,1016) && clock.externalWeight(1016)>0.f && clock.externalWeight(1016)<1.f,
            "affine stretch must use the standard continuous quintic blend curve");
    require(!clock.apply(&still,&shown,1,1400) && clock.externalWeight(1400)==0.f,
            "an affine-only blend must settle without a rigid offset");
  }
  auto source=hierarchy();
  for(const size_t b:{size_t(1),size_t(5)}) {
    Tempest::Matrix4x4 local;
    std::memcpy(&local,&source.nodes[b].transform,sizeof(local));
    local.rotateOY(b==1 ? 35.f : -27.f);
    std::memcpy(&source.nodes[b].transform,&local,sizeof(local));
    }
  Skeleton skeleton(source,nullptr,"AFFINE_HUMAN.MDS");
  AnimationSolver solver;
  solver.setSkeleton(&skeleton);
  auto object=at(120,-30,90); object.rotateOY(29); object.scale(.9f,1.f,1.f);
  for(const uint64_t saveTick:{uint64_t(1016),uint64_t(1064)}) {
    Pose before;
    before.setSkeleton(&skeleton); before.setObjectMatrix(object,true); before.update(1000,true);
    const std::vector<Tempest::Matrix4x4> animated(before.transform(),before.transform()+before.boneCount());
    auto physical=animated;
    const std::vector<size_t> mapped={1,5};
    for(const size_t b:mapped) {
      // R(native) * C(initial visual affine), independent of the production backend. This keeps
      // nonuniform object scale and the shear produced when the authored bone rotates under it.
      const auto rotation=IkMath::mul(IkMath::rotationOf(object),IkMath::rotationOf(skeleton.tr[b]));
      const auto nativeInitial=IkMath::compose(rotation,position(animated[b]));
      auto inverse=nativeInitial; inverse.inverse();
      const auto correction=inverse*animated[b];
      const auto initial=nativeInitial*correction;
      for(size_t i=0;i<16;++i)
        near(initial.data()[i],animated[b].data()[i],.0001f,"native frame times affine correction must preserve the initial world bone");
      auto nativeMoved=nativeInitial;
      nativeMoved.translate(0,-35,12);
      nativeMoved.rotateOZ(b==1 ? 65.f : -25.f);
      physical[b]=nativeMoved*correction;
      }
    require(before.setRagdollPose(&skeleton,physical,mapped),"positive affine physical frames must be accepted");
    before.update(1016,true);
    for(const size_t b:mapped)
      for(size_t i=0;i<16;++i)
        near(before.bone(b).data()[i],physical[b].data()[i],.0001f,"affine physical overlay must preserve every matrix component");
    auto inverseAnimatedHead=animated[5]; inverseAnimatedHead.inverse();
    const auto expectedSlot=physical[5]*(inverseAnimatedHead*animated[20]);
    for(size_t i=0;i<16;++i)
      near(before.bone(20).data()[i],expectedSlot.data()[i],.0002f,"unmapped attachment must retain its affine relation to the physical head");
    const std::vector<Tempest::Matrix4x4> shown(before.transform(),before.transform()+before.boneCount());
    before.clearRagdollPose(true,1016);
    if(saveTick>1016) before.update(saveTick,true);
    std::vector<uint8_t> bytes;
    {
      Tempest::MemWriter writer(bytes); Serialize out(writer);
      out.setEntry("visual"); before.save(out);
      out.setEntry("ragdoll-recovery"); before.saveRagdollRecovery(out);
    }
    Pose after;
    after.setSkeleton(&skeleton); after.setObjectMatrix(object,true);
    {
      Tempest::MemReader reader(bytes); Serialize in(reader);
      require(in.setEntry("visual"),"affine legacy pose entry must load"); after.load(in,solver);
      after.update(saveTick,true);
      require(in.setEntry("ragdoll-recovery"),"affine optional return entry must load"); after.loadRagdollRecovery(in);
    }
    for(uint64_t tick=saveTick;tick<=1800;tick+=16) {
      before.update(tick,true); after.update(tick,true);
      for(size_t b=0;b<shown.size();++b) {
        for(size_t i=0;i<16;++i) {
          near(after.bone(b).data()[i],before.bone(b).data()[i],.0001f,
               "pending and mid-return affine archives must preserve the complete pose continuation");
          if(tick==1016)
            near(before.bone(b).data()[i],shown[b].data()[i],.0001f,
                 "first affine return frame must preserve every shown world-matrix component");
          }
        const auto& m=before.bone(b);
        const Tempest::Vec3 x(m.at(0,0),m.at(0,1),m.at(0,2)),y(m.at(1,0),m.at(1,1),m.at(1,2)),z(m.at(2,0),m.at(2,1),m.at(2,2));
        require(IkMath::dot(x,IkMath::cross(y,z))>0.f,"affine recovery must remain finite and properly oriented");
        }
      }
    require(!before.isRagdollBlending() && !after.isRagdollBlending(),"affine recovery must finish within the normal blend duration");
    for(size_t b=0;b<animated.size();++b)
      for(size_t i=0;i<16;++i)
        near(before.bone(b).data()[i],animated[b].data()[i],.0001f,"completed affine recovery must preserve the full ordinary scaled animation");
    auto reflected=physical; reflected[1].scale(-1.f,1.f,1.f);
    require(!before.setRagdollPose(&skeleton,reflected,mapped),"reflected physical frames must be rejected");
    auto projective=physical; projective[1].set(0,3,.1f);
    require(!before.setRagdollPose(&skeleton,projective,mapped),"projective physical frames must be rejected");
    }
  }

void testPhysicalVelocityHandoff() {
  {
    // Compare a moving physical source and moving target against the existing ordinary switch.
    // Neither side's velocity may silently become zero at the handoff.
    zenkit::AnimationSample previous,current,target,next,shown,ordinary;
    previous.position={19.97f,0,0}; previous.rotation={1,0,0,0};
    current=previous; current.position.x=20.f;
    target=previous; target.position.x=0.f;
    next=target; next.position.x=.006f;
    Animation::Sequence first,second;
    first.name="physical-source-control"; second.name="moving-target-control";
    AnimBlend baseline,physical;
    baseline.reset(1); physical.reset(1);
    ordinary=previous; shown=current;
    baseline.drive(0,first,0,previous,current,1000.f);
    baseline.apply(&previous,&ordinary,1,1000);
    baseline.apply(&current,&ordinary,1,1001);
    baseline.drive(0,second,1001,target,next,1000.f);
    physical.transitionFrom(&current,1,1001,&previous,1000);
    physical.setExternalTarget(&target,&next,1,1000.f);
    for(uint64_t time=1001;time<1500;time+=1) {
      auto moving=target; moving.position.x=float(time-1001)*.006f;
      baseline.apply(&moving,&ordinary,1,time);
      physical.apply(&moving,&shown,1,time);
      near(shown.position.x,ordinary.position.x,.0001f,"physical source and target velocities must match ordinary inertialization");
      }
    for(const bool pending:{true,false}) {
      AnimBlend before,after;
      before.reset(1); before.transitionFrom(&current,1,2001,&previous,2000);
      before.setExternalTarget(&target,&next,1,1000.f);
      auto a=current,b=current;
      if(!pending) before.apply(&target,&a,1,2001);
      std::vector<uint8_t> bytes;
      { Tempest::MemWriter writer(bytes); Serialize out(writer); out.setEntry("velocity"); before.saveExternal(out); }
      { Tempest::MemReader reader(bytes); Serialize in(reader); require(in.setEntry("velocity"),"velocity archive must exist"); after.loadExternal(in,1); }
      after.setExternalTarget(&target,&next,1,1000.f); b=a;
      for(uint64_t time=2001;time<2400;time+=16) {
        auto moving=target; moving.position.x=float(time-2001)*.006f;
        before.apply(&moving,&a,1,time); after.apply(&moving,&b,1,time);
        require(std::memcmp(&a,&b,sizeof(a))==0,"pending and initialized velocity seeds must retain exact saved continuation");
        }
      }
    }
  zenkit::ModelHierarchy source;
  zenkit::ModelHierarchyNode node;
  node.name="BIP01"; node.parent_index=-1;
  const auto identity=Tempest::Matrix4x4::mkIdentity();
  std::memcpy(&node.transform,&identity,sizeof(identity)); source.nodes.push_back(node);
  source.bbox.min={-1,-1,-1}; source.bbox.max={1,1,1}; source.collision_bbox=source.bbox;
  Skeleton skeleton(source,nullptr,"VELOCITY.MDS");
  AnimationSolver solver; solver.setSkeleton(&skeleton);
  Animation::Sequence target;
  target.name="moving-root-target"; target.animCls=Animation::Loop;
  target.data=std::make_shared<Animation::AnimData>();
  target.data->numFrames=32; target.data->fpsRate=10.f; target.data->nodeIndex={0};
  for(int i=0;i<32;++i) {
    zenkit::AnimationSample sample;
    sample.rotation={1,0,0,0}; sample.position={float(i)*10.f,float(i)*.6f,0};
    target.data->samples.push_back(sample);
    }
  Pose pose;
  auto object=at(100,0,50); object.rotateOY(29); object.scale(.9f,1.f,1.f);
  pose.setSkeleton(&skeleton); pose.setObjectMatrix(object,true);
  require(pose.startAnim(solver,&target,0,BS_UNCONSCIOUS,Pose::Force,1000),"world-velocity fixture must have a moving animation target");
  auto world=object; world.translate(0,20,0);
  require(pose.setRagdollPose(&skeleton,{world},{0}),"world-velocity first physical sample must set"); pose.update(1000,true);
  world.set(3,1,20.03f);
  require(pose.setRagdollPose(&skeleton,{world},{0}),"world-velocity second physical sample must set"); pose.update(1001,true);
  const auto first=position(pose.bone(0));
  object.set(3,1,30.f); // Rebase from the body's world frame to the get-up frame.
  pose.setObjectMatrix(object,true);
  pose.clearRagdollPose(true,1010);
  pose.update(1010,true);
  near((position(pose.bone(0))-first).length(),0,.0001f,"velocity seeding must preserve the first physical world pose after rebasing");
  const auto atStart=position(pose.bone(0));
  pose.update(1011,true);
  const auto velocity=(position(pose.bone(0))-atStart)*1000.f;
  near(velocity.y,30.f,.15f,"recovery must preserve the measured previous world velocity");
  near(velocity.x,0.f,.15f,"cancelled MAN root motion must not inject horizontal world velocity");
  near(velocity.z,0.f,.15f,"rebasing and scaled yaw must not inject horizontal world velocity");
  }

std::vector<float> replayProducerState(Pose& pose) {
  std::vector<float> result;
  FootIk::Probe foot; pose.footIk().probe(foot,{});
  result.insert(result.end(),{foot.valid ? 1.f : 0.f,foot.weight,foot.pelvis,foot.chest,foot.target,foot.crouch,foot.floorY});
  for(const auto& leg:foot.leg) {
    result.insert(result.end(),{leg.smoothY,leg.plant,leg.slack,leg.kneeDev,leg.tilt,leg.roll,leg.yaw});
    for(size_t i=0;i<4;++i) {
      result.insert(result.end(),{leg.anim[i].x,leg.anim[i].y,leg.anim[i].z,leg.final[i].x,leg.final[i].y,leg.final[i].z});
      }
    }
  Gaze::Probe gaze; pose.gazeIk().probe(gaze);
  result.insert(result.end(),{gaze.valid ? 1.f : 0.f,gaze.weight,gaze.yaw,gaze.pitch,gaze.animYaw,gaze.animPitch,
                             gaze.headYaw,gaze.chestYaw,gaze.waistYaw,gaze.error,gaze.head.x,gaze.head.y,gaze.head.z,
                             gaze.facing.x,gaze.facing.y,gaze.facing.z,float(pose.gazeIk().shiftCount())});
  Lean::Probe lean; pose.leanIk().probe(lean);
  result.insert(result.end(),{lean.valid ? 1.f : 0.f,lean.roll,lean.pitch,lean.speed,lean.turn,lean.slope});
  Aim::Probe aim; pose.aimIk().probe(aim);
  result.insert(result.end(),{aim.valid ? 1.f : 0.f,aim.weight,aim.yaw,aim.pitch,aim.animYaw,aim.animPitch,
                             aim.turn,aim.bend,aim.lean,aim.side,aim.error});
  AimWalk::Probe walk; pose.aimWalk().probe(walk);
  result.insert(result.end(),{walk.valid ? 1.f : 0.f,walk.weight,walk.speed,walk.phase,walk.twist,walk.chest,walk.drop});
  BowDraw::Probe bow; pose.bowDrawIk().probe(bow);
  result.insert(result.end(),{bow.valid ? 1.f : 0.f,bow.weight,bow.draw,bow.span,bow.travel,bow.miss,bow.follow,
                             bow.fit,bow.offset,bow.angle,bow.aligned,bow.holding ? 1.f : 0.f,bow.nock,
                             bow.handAt.x,bow.handAt.y,bow.handAt.z});
  Flinch::Probe flinch; pose.flinchIk().probe(flinch);
  result.insert(result.end(),{flinch.valid ? 1.f : 0.f,flinch.bend,flinch.bendYaw,flinch.twist,flinch.head,
                             float(flinch.hits),flinch.strength,flinch.height});
  return result;
  }

void testOriginReplayCopy(Pose& pose) {
  const bool probing=FootIk::isProbing(); FootIk::setProbing(true);
  const auto before=std::vector<Tempest::Matrix4x4>(pose.transform(),pose.transform()+pose.boneCount());
  const auto producers=replayProducerState(pose);
  const auto expected=PoseOriginReplayContract::legacy(pose);
  const auto actual=PoseOriginReplayContract::prepared(pose);
  require(actual.size()==expected.size() && actual.size()==before.size(),"origin replay must preserve full skeleton indexing");
  for(size_t b=0;b<actual.size();++b)
    for(size_t i=0;i<16;++i) {
      require(std::isfinite(actual[b].data()[i]) && std::isfinite(expected[b].data()[i]),"origin replay must retain finite complete frames");
      exact(actual[b].data()[i],expected[b].data()[i],"replay-only scratch must match the preserved full-copy origin replay bit for bit");
      exact(pose.bone(b).data()[i],before[b].data()[i],"origin scratch must not mutate the parent's shown world pose");
      }
  const auto after=replayProducerState(pose);
  require(after.size()==producers.size(),"origin scratch must retain all producer probes");
  for(size_t i=0;i<after.size();++i)
    exact(after[i],producers[i],"origin scratch must not mutate the parent's retained procedural state");
  FootIk::setProbing(probing);
  }

void testOriginReplayHierarchySeed() {
  for(const bool unordered:{false,true}) {
    zenkit::ModelHierarchy source;
    const int16_t parentsOrdered[]={-1,0,1,2,1,4};
    const int16_t parentsUnordered[]={1,-1,0,2,1,4};
    for(int16_t i=0;i<6;++i) {
      zenkit::ModelHierarchyNode node; node.name="REPLAY_NODE_"+std::to_string(i);
      node.parent_index=unordered ? parentsUnordered[i] : parentsOrdered[i];
      auto local=at(float(i)*3.f,float(i)*5.f,float(i)*2.f); local.rotateOY(float(i)*7.f);
      std::memcpy(&node.transform,&local,sizeof(local)); source.nodes.push_back(node);
      }
    source.bbox.min={-30,0,-30}; source.bbox.max={30,80,30}; source.collision_bbox=source.bbox;
    Skeleton skeleton(source,nullptr,unordered ? "REPLAY_UNORDERED.MDS" : "REPLAY_FULL_SEED.MDS");
    require(skeleton.ordered!=unordered,"origin fixture must exercise the selected hierarchy branch");
    Pose pose; pose.setSkeleton(&skeleton);
    auto object=at(23446.3984375f,1122.7470703125f,-10364.30859375f); object.rotateOY(74); object.scale(.9f,1.f,1.f);
    pose.setFlags(Pose::NoTranslation); pose.setObjectMatrix(object,true);
    if(!unordered) {
      // The generic ordered pass intentionally permits a parent in the fixed
      // bank beyond numBones. Preserve its previously retained affine seed.
      skeleton.nodes[2].parent=Resources::MAX_NUM_SKELETAL_NODES-1;
      auto seed=at(17,-8,11); seed.rotateOY(23); seed.scale(1.05f,1.f,.95f);
      PoseOriginReplayContract::seed(pose,Resources::MAX_NUM_SKELETAL_NODES-1,seed);
      }
    std::vector<Tempest::Matrix4x4> physical;
    for(const auto& bone:skeleton.tr) physical.push_back(object*bone);
    require(pose.setRagdollPose(&skeleton,physical,{1,2,4}),"origin hierarchy fixture must accept a mixed physical mask");
    pose.update(1000,true); testOriginReplayCopy(pose);
    pose.update(1000,false); testOriginReplayCopy(pose);
    pose.update(1017,true); testOriginReplayCopy(pose);
    }
  }

void testPoseFarWorldAttachments() {
  // Independent column-wise Gaussian solve of P * local = child; no homogeneous inverse.
  auto relative=[](const Tempest::Matrix4x4& parent, const Tempest::Matrix4x4& child) {
    auto result=Tempest::Matrix4x4::mkIdentity();
    for(int column=0;column<4;++column) {
      double rows[3][4] = {};
      for(int r=0;r<3;++r) {
        for(int c=0;c<3;++c) rows[r][c]=parent.at(c,r);
        rows[r][3]=column<3 ? double(child.at(column,r)) : double(child.at(3,r))-parent.at(3,r);
        }
      for(int pivot=0;pivot<3;++pivot) {
        int best=pivot;
        for(int r=pivot+1;r<3;++r) if(std::abs(rows[r][pivot])>std::abs(rows[best][pivot])) best=r;
        if(best!=pivot) for(int c=0;c<4;++c) std::swap(rows[pivot][c],rows[best][c]);
        const double scale=rows[pivot][pivot];
        for(int c=pivot;c<4;++c) rows[pivot][c]/=scale;
        for(int r=0;r<3;++r) if(r!=pivot) {
          const double coefficient=rows[r][pivot];
          for(int c=pivot;c<4;++c) rows[r][c]-=coefficient*rows[pivot][c];
          }
        }
      for(int r=0;r<3;++r) result.set(column,r,float(rows[r][3]));
      }
    return result;
    };
  auto source=hierarchy();
  auto add=[&](const char* name, int16_t parent, Tempest::Matrix4x4 local) {
    zenkit::ModelHierarchyNode node; node.name=name; node.parent_index=parent;
    std::memcpy(&node.transform,&local,sizeof(local)); source.nodes.push_back(node);
    return source.nodes.size()-1;
    };
  const zenkit::AnimationSample actualRoot{{67.9932175f,11.873786f,90.8665161f},
                                         {.694189906f,-.213284492f,.381034523f,.572208703f}};
  const auto rootFrame=mkMatrix(actualRoot);
  std::memcpy(&source.nodes[0].transform,&rootFrame,sizeof(rootFrame));
  const auto spine2=add("BIP01 SPINE2",3,at(0,8,0));
  for(const size_t bone:{size_t(4),size_t(6),size_t(9)}) source.nodes[bone].parent_index=int16_t(spine2);
  auto arrow=at(0,2,0); arrow.rotateOY(180);
  const auto rightSlot=add("ZS_RIGHTHAND",11,arrow);
  const auto leftSlot=add("ZS_LEFTHAND",8,at(0,1,0));
  const auto unkeyed=add("UNKEYED_FOOT_ATTACHMENT",14,at(3,7,5));
  Skeleton skeleton(source,nullptr,"FAR_WORLD.MDS");
  AnimationSolver solver; solver.setSkeleton(&skeleton);
  Animation::Sequence stance,steps;
  for(auto* sequence:{&stance,&steps}) {
    sequence->name=sequence==&stance ? "keyed-stance" : "moving-steps";
    sequence->animCls=Animation::Loop; sequence->data=std::make_shared<Animation::AnimData>();
    auto& data=*sequence->data; data.numFrames=32; data.fpsRate=10;
    for(size_t b=0;b<skeleton.nodes.size();++b) if(b!=unkeyed) data.nodeIndex.push_back(uint32_t(b));
    for(size_t frame=0;frame<data.numFrames;++frame)
      for(auto b:data.nodeIndex) {
        const auto local=skeleton.nodes[b].tr;
        zenkit::AnimationSample sample;
        const auto q=IkMath::toQuat(IkMath::transpose(IkMath::rotationOf(local)));
        sample.rotation={q.w,q.x,q.y,q.z};
        sample.position={local.at(3,0),local.at(3,1),local.at(3,2)};
        if(sequence==&stance && b==20) { sample.position.x=5.7f; sample.position.y=13.4f; sample.position.z=-2.8f; }
        if(sequence==&steps) {
          if(b==0) sample.position.z+=float(frame)*3.f;
          if(b==1) sample.position.y+=float(frame%8)*.8f;
          }
        data.samples.push_back(sample);
        }
    data.setupMoveTr();
    }
  auto basis=Tempest::Matrix4x4::mkIdentity(); basis.rotateOY(74); basis.scale(.9f,1,1);
  auto object=basis; object.set(3,0,24023.61328125f); object.set(3,1,1122.7470703125f); object.set(3,2,-10364.30859375f);
  {
    // The actual v5 saved attachment's homogeneous W is one float above one. This seemingly tiny
    // local error scales the whole parent translation, rather than only the attachment offset.
    auto affine=at(8.1712675f,4.0605712f,2.5401063f);
    auto inverseResidue=affine;
    inverseResidue.set(3,3,std::nextafter(1.f,2.f));
    const auto exactWorld=object*affine, damagedWorld=object*inverseResidue;
    const auto delta=Tempest::Vec3(damagedWorld.at(3,0)-exactWorld.at(3,0),
                                 damagedWorld.at(3,1)-exactWorld.at(3,1),
                                 damagedWorld.at(3,2)-exactWorld.at(3,2));
    require(delta.length()>.001f,
            "the actual nonunit inverse W must reproduce the far-world attachment error");
  }
  std::vector<size_t> mapped;
  for(size_t b=0;b<skeleton.nodes.size();++b)
    if(b!=20 && b!=rightSlot && b!=leftSlot && b!=unkeyed) mapped.push_back(b);
  std::vector<Tempest::Matrix4x4> physical;
  auto native=at(24020,1200,-10360); native.rotateOX(49); native.rotateOY(-33); native.scale(.9f,1,1);
  for(const auto& bone:skeleton.tr) physical.push_back(native*bone);
  const size_t slots[]={20,rightSlot,leftSlot,unkeyed};
  for(const int producer:{0,1,2,3,4,5,6,7,8,9}) {
    Pose far,reference;
    far.setSkeleton(&skeleton); reference.setSkeleton(&skeleton);
    far.setObjectMatrix(object,true); reference.setObjectMatrix(basis,true);
    if(producer==9) {
      far.setFlags(Pose::NoTranslation); reference.setFlags(Pose::NoTranslation);
      far.setHeadRotation(11,-17); reference.setHeadRotation(11,-17);
      }
    require(far.startAnim(solver,&stance,0,BS_UNCONSCIOUS,Pose::Force,1000) &&
            reference.startAnim(solver,&stance,0,BS_UNCONSCIOUS,Pose::Force,1000),"far-world keyed stance must start");
    far.update(1000,true); reference.update(1000,true);
    auto configure=[&](Pose& pose, float floorY) {
      if(producer==1 || producer==9) {
        FootIk::Input input; input.enable=true; input.dt=200; input.floorY=floorY;
        for(size_t i=0;i<2;++i) { input.ground[i].hasCol=true; input.ground[i].y=floorY+(i==0 ? 10.f : -6.f); input.ground[i].n={.1f,.99f,.02f}; }
        pose.updateFootIk(input);
        }
      if(producer==2) {
        require(pose.aimWalk().isValid(),"far-world fixture must have an actual AimWalk hierarchy");
        AimWalk::Input input; input.legs=&steps; input.walk=1; input.dt=200; pose.updateAimWalk(input);
        require(pose.aimWalk().isActive(),"far-world fixture must exercise active AimWalk");
        }
      if(producer==3 || producer==4) {
        require(pose.bowDrawIk().isValid(),"far-world fixture must have an actual bow draw chain");
        BowDraw::Input input; input.active=true; input.ready=true; input.draw=.55f; input.span=40; input.arrow=true;
        input.hasString=true; input.stringRest={0,0,0}; input.stringPull={-40,0,0}; input.stringHigh={0,2,0}; input.bowAxis={0,1,0}; input.dt=200;
        pose.updateBowDraw(input);
        }
      if(producer==5) {
        require(pose.gazeIk().isValid(),"origin replay fixture must have a gaze hierarchy");
        Gaze::Input input; input.active=true; input.source=Gaze::Source::Attention; input.forward={0,0,1}; input.dt=200;
        input.target=position(pose.bone(5))+Tempest::Vec3(80,35,120); input.talk={.08f,.1f,-.04f}; pose.updateGaze(input);
        require(pose.gazeIk().isActive(),"origin replay fixture must retain active gaze");
        }
      if(producer==6) {
        require(pose.leanIk().isValid(),"origin replay fixture must have a lean hierarchy");
        Lean::Input input; input.active=true; input.position={0,floorY,0}; input.forward={0,0,1}; input.dt=200; pose.updateLean(input);
        input.position={40,floorY+15,80}; input.forward={.2f,0,.98f}; pose.updateLean(input);
        Lean::Probe probe; pose.leanIk().probe(probe);
        require(probe.active && std::abs(probe.pitch)+std::abs(probe.roll)>0,"origin replay fixture must retain a measured lean");
        }
      if(producer==7) {
        require(pose.flinchIk().isValid(),"origin replay fixture must have a flinch hierarchy");
        Flinch::Hit hit; hit.dir={.4f,0,-1}; hit.height=.9f; hit.twist=.4f; hit.strength=.8f; pose.flinchIk().hit(hit,{0,0,1});
        Flinch::Input input; input.forward={0,0,1}; input.dt=32; pose.updateFlinch(input);
        Flinch::Probe probe; pose.flinchIk().probe(probe);
        require(probe.active && probe.hits==1,"origin replay fixture must retain a real flinch kick");
        }
      if(producer==8) {
        require(pose.aimIk().isValid(),"origin replay fixture must have an aim hierarchy");
        Aim::Input input; input.active=true; input.arrow=true; input.manual=true; input.slot=rightSlot;
        input.dir={.3f,.15f,1}; input.forward={0,0,1}; input.lean=.2f; input.side=.1f;
        input.sway={.02f,-.01f}; input.tremor={-.01f,.015f}; input.dt=200;
        // Choosing the arrow slot resets its measurement. updateAim performs the
        // real mkSkeleton/apply pass; the next four steady measured frames make
        // the bow ready. A single large dt cannot replace that frame counter.
        for(int frame=0;frame<5;++frame)
          require(pose.updateAim(input),"manual aim initialization must rebuild each measured world pose");
        Aim::Probe probe; pose.aimIk().probe(probe);
        require(probe.active && probe.weight>0,"origin replay fixture must retain active manual aim");
        require(probe.steady && probe.error>=0,"origin replay fixture must retain a measured and applied arrow aim");
        }
      };
    configure(far,object.at(3,1)); configure(reference,0);
    far.update(1016,true); reference.update(1016,true);
    if(producer==4) {
      BowDraw::Input input; input.active=true; input.ready=false; input.reload=.2f; input.reloadTime=1.f; input.dt=16;
      far.updateBowDraw(input); reference.updateBowDraw(input);
      far.update(1032,true); reference.update(1032,true);
      BowDraw::Probe probe; reference.bowDrawIk().probe(probe);
      require(probe.holding,"far-world fixture must exercise a genuinely held reload pose");
      // heldRel was captured by the real far-world producer. Preserve that exact state in the
      // independent ordinary reference, rather than replacing its earlier capture with another one.
      reference=far;
      reference.setObjectMatrix(basis,true);
      for(size_t f=0;f<stance.data->numFrames;++f)
        stance.data->samples[f*stance.data->nodeIndex.size()+20].position.z+=30.f;
      far.update(1048,true); reference.update(1048,true);
      auto held=reference.bone(skeleton.nodes[20].parent); held.inverse();
      require(std::abs((held*reference.bone(20)).at(3,2)-stance.data->samples[20].position.z)>20.f,
              "held body must retain its captured local frame despite a different current keyed target");
      }
    if(producer==3) {
      BowDraw::Probe probe; reference.bowDrawIk().probe(probe);
      require(probe.aligned>0 && probe.bowMove>0,"far-world fixture must exercise an independently moved bow hand");
      }
    if(producer==0) {
      std::vector<Tempest::Matrix4x4> half;
      for(size_t b=0;b<skeleton.nodes.size();++b) half.push_back(IkMath::blend(far.bone(b),physical[b],.5f));
      require(far.setRagdollPose(&skeleton,physical,mapped,.5f),"far-world partial physical overlay must set"); far.update(1040,true);
      for(auto b:mapped)
        for(size_t i=0;i<16;++i)
          near(far.bone(b).data()[i],half[b].data()[i],.0001f,"partial physical weight must blend two world frames without moving the NPC toward the origin");
      }
    require(far.setRagdollPose(&skeleton,physical,mapped),"far-world physical overlay must set"); far.update(1048,true);
    testOriginReplayCopy(far);
    Pose repeated=far; repeated.update(1048,false); testOriginReplayCopy(repeated);
    if(producer>=5) continue; // New retained-state variants use the exact legacy replay oracle above.
    for(auto b:slots) {
      const auto expected=physical[skeleton.nodes[b].parent]*relative(reference.bone(skeleton.nodes[b].parent),reference.bone(b));
      require(far.bone(b).at(0,3)==0 && far.bone(b).at(1,3)==0 && far.bone(b).at(2,3)==0 && far.bone(b).at(3,3)==1,
              "a recomposed physical attachment must retain its exact affine homogeneous row");
      for(size_t i=0;i<16;++i) {
        if(std::abs(far.bone(b).data()[i]-expected.data()[i])>(i>=12 && i<=14 ? .001f : .00001f))
          std::fprintf(stderr,"far attachment diagnostic producer=%d bone=%zu component=%zu\n",producer,b,i);
        near(far.bone(b).data()[i],expected.data()[i],i>=12 && i<=14 ? .001f : .00001f,
             "far-world attachment must preserve the actual finished keyed/unkeyed procedural local frame");
        }
      }
    if(producer!=0) continue;
    const auto before=std::vector<Tempest::Matrix4x4>(far.transform(),far.transform()+far.boneCount());
    // A disabled corpse uses another object origin/alignment before the exact native state returns.
    auto fallback=object; fallback.set(3,1,999.f); fallback.rotateOZ(5.4f);
    far.setObjectMatrix(fallback,true); far.update(1064,true);
    far.setObjectMatrix(object,true); far.update(1080,true);
    std::vector<uint8_t> bytes;
    { Tempest::MemWriter writer(bytes); Serialize out(writer); out.setEntry("visual"); far.save(out); }
    Pose cold; cold.setSkeleton(&skeleton); cold.setObjectMatrix(fallback,true);
    { Tempest::MemReader reader(bytes); Serialize in(reader); require(in.setEntry("visual"),"far-world visual samples must archive"); cold.load(in,solver); }
    // No resolved layer is required for this static payload: Pose still owns its keyed base/raw samples.
    cold.update(1080,true); cold.setObjectMatrix(object,true);
    require(cold.setRagdollPose(&skeleton,physical,mapped),"cold far-world native overlay must restore"); cold.update(1080,true);
    for(size_t b=0;b<before.size();++b)
      for(size_t i=0;i<16;++i) {
        near(far.bone(b).data()[i],before[b].data()[i],i>=12 && i<=14 ? .001f : .00001f,"far-world object rebasing must retain every physical and attachment frame");
        near(cold.bone(b).data()[i],before[b].data()[i],i>=12 && i<=14 ? .001f : .00001f,"cold archived samples must reconstruct every far-world attachment frame");
        }
    }
  }

void testFiniteRecoveryHierarchy() {
  zenkit::ModelHierarchy source;
  const auto identity=Tempest::Matrix4x4::mkIdentity();
  constexpr uint32_t count=8;
  for(uint32_t b=0;b<count;++b) {
    zenkit::ModelHierarchyNode node;
    node.name="finite-local-"+std::to_string(b); node.parent_index=int16_t(b)-1;
    std::memcpy(&node.transform,&identity,sizeof(identity)); source.nodes.push_back(node);
    }
  source.bbox.min={-1,-1,-1}; source.bbox.max={1,1,1}; source.collision_bbox=source.bbox;
  Skeleton skeleton(source,nullptr,"FINITE_LOCALS.MDS");
  Pose pose; pose.setSkeleton(&skeleton); pose.setObjectMatrix(identity,true); pose.update(1000,true);
  const auto ordinary=std::vector<Tempest::Matrix4x4>(pose.transform(),pose.transform()+pose.boneCount());
  const AnimBlend::Sample unit{{0,0,0},{1,0,0,0}}, unused{{0,0,0},{0,0,0,0}};
  auto stretch=identity; stretch.scale(1.e6f,1.e6f,1.e6f);
  std::vector<uint8_t> bytes;
  {
    Tempest::MemWriter writer(bytes); Serialize out(writer); out.setEntry("recovery");
    out.write(true,uint16_t(3),skeleton.name(),count);
    for(uint32_t b=0;b<count;++b) out.write(unit);
    for(uint32_t b=0;b<count;++b) out.write(stretch);
    out.write(count,uint64_t(1000),uint64_t(1000),uint64_t(1000),true);
    auto zero=[&] { for(int i=0;i<7;++i) out.write(0.f); };
    zero();
    for(uint32_t b=0;b<count;++b) {
      out.write(false,uint64_t(0),0.f,false,unused,0.f);
      for(int i=0;i<4;++i) zero();
      out.write(Tempest::Vec3(),Tempest::Vec3(),Tempest::Vec3(),Tempest::Vec3(),unused);
      }
    out.write(count); for(uint32_t b=0;b<count;++b) out.write(unit);
    out.write(uint64_t(0),uint32_t(0));
  }
  {
    Tempest::MemReader reader(bytes); Serialize in(reader);
    require(in.setEntry("recovery"),"finite affine local hierarchy archive must exist"); pose.loadRagdollRecovery(in);
  }
  require(pose.isRagdollBlending(),"finite affine locals are valid individually before hierarchy composition");
  pose.update(1000,true);
  require(!pose.isRagdollBlending(),"an overflowing affine hierarchy must end recovery before rendering");
  for(size_t b=0;b<ordinary.size();++b)
    for(size_t i=0;i<16;++i)
      near(pose.bone(b).data()[i],ordinary[b].data()[i],0,
           "an overflowing recovery must restore the complete ordinary world pose transactionally");
  }

void testBenchmarkSleepPolicy() {
  Fixture fixture;
  CollisionWorld world;
  auto ground=floor(world);
  auto body=fixture.create(world,at(0,30,0));
  const auto count=world.bodyCount();
  for(int frame=0;frame<1800 && !body->isSleeping();++frame) world.tick(17);
  require(body->isSleeping(),"ordinary benchmark fixture must naturally sleep with default policy");
  body->setAllowSleeping(false);
  for(int frame=0;frame<600;++frame) {
    world.tick(17);
    require(world.bodyCount()==count,"disabling benchmark sleep must retain all native parts");
    const auto state=body->state();
    require(!body->isSleeping() && std::none_of(state.parts.begin(),state.parts.end(),[](const auto& p) { return p.sleeping; }),
            "benchmark allow=false must keep every native part actually active beyond the ordinary sleep window");
    for(const auto& part:state.parts)
      require(position(part.transform).length()<1000,"sleep policy must not launch or explode an otherwise settled body");
    }
  body->setAllowSleeping(true);
  for(int frame=0;frame<1800 && !body->isSleeping();++frame) world.tick(17);
  const auto sleeping=body->state();
  require(body->isSleeping() && std::all_of(sleeping.parts.begin(),sleeping.parts.end(),[](const auto& p) { return p.sleeping; }),
          "reenabling sleep must restore natural sleeping of all native parts");
  require(world.bodyCount()==count,"reenabling benchmark sleep must retain body lifetime and collision coverage");
  std::unique_ptr<Ragdoll> detached;
  { CollisionWorld temporary; detached=fixture.create(temporary,at(0,30,0)); }
  detached->setAllowSleeping(false); detached->setAllowSleeping(true);
  detached.reset();
  }

bool benchmarkTwentyBodies(const char* output, bool preflight) {
  Fixture fixture;
  struct Counts {
    size_t ragdolls=0, awake=0, sleeping=0, invalid=0;
    size_t rigParts=0, nativeParts=0, worldBodies=0;
    };
  struct Frame { Counts before, afterImpulse, afterTick; unsigned failureMask=0; };
  struct Result {
    const char* mode;
    size_t parts=0, staticBodies=0;
    int preparationFrames=0;
    Counts initial, prepared;
    std::vector<Frame> frames;
    std::vector<float> times;
    bool preparationValid=true, coverageValid=true, timingValid=true;
    };
  constexpr int frames=1020, warmup=120, signBlockFrames=5;
  std::vector<Result> results;
  for(const auto mode:{"disabled","active","sleeping"}) {
    CollisionWorld world;
    auto ground=floor(world);
    Result result; result.mode=mode; result.staticBodies=world.bodyCount();
    std::vector<std::unique_ptr<Ragdoll>> bodies;
    if(std::string_view(mode)!="disabled")
      for(int i=0;i<20;++i) {
        const auto object=at(float(i%5)*250.f-500.f,30,float(i/5)*250.f-375.f);
        std::vector<Tempest::Matrix4x4> pose;
        for(const auto& matrix:fixture.skeleton.tr) pose.push_back(object*matrix);
        // Retain failed creations in the counts instead of exiting before the raw output exists.
        bodies.push_back(world.createRagdoll(fixture.skeleton,fixture.zones,pose.data(),pose.size(),object,{}));
        }
    const size_t expectedRagdolls=std::string_view(mode)=="disabled" ? 0 : 20;
    const auto counts=[&] {
      Counts value; value.worldBodies=world.bodyCount();
      value.nativeParts=value.worldBodies>=result.staticBodies ? value.worldBodies-result.staticBodies : 0;
      for(const auto& body:bodies) {
        if(body==nullptr || !body->valid()) { ++value.invalid; continue; }
        ++value.ragdolls; value.rigParts+=body->partCount();
        if(body->isSleeping()) ++value.sleeping; else ++value.awake;
        }
      return value;
      };
    result.initial=counts(); result.parts=result.initial.rigParts;
    if(std::string_view(mode)=="active")
      for(const auto& body:bodies)
        if(body!=nullptr && body->valid()) body->setAllowSleeping(false);
    if(std::string_view(mode)=="sleeping") {
      for(;result.preparationFrames<1800 && counts().sleeping!=expectedRagdolls;++result.preparationFrames)
        world.tick(17);
      result.preparationValid=counts().sleeping==expectedRagdolls;
      }
    result.prepared=counts();
    const auto partsValid=[&](const Counts& value) {
      return result.staticBodies==1 && value.invalid==0 && value.ragdolls==expectedRagdolls &&
             value.awake+value.sleeping==expectedRagdolls && value.rigParts==result.parts &&
             value.nativeParts==result.parts && value.worldBodies==result.staticBodies+result.parts;
      };
    const auto activityValid=[&](const Counts& value) {
      if(std::string_view(mode)=="active") return value.awake==20 && value.sleeping==0;
      if(std::string_view(mode)=="sleeping") return value.sleeping==20 && value.awake==0;
      return value.awake==0 && value.sleeping==0;
      };
    result.coverageValid=result.preparationValid && partsValid(result.initial) && partsValid(result.prepared);
    result.frames.reserve(frames);
    if(!preflight) result.times.reserve(frames-warmup);
    for(int frame=0;frame<frames;++frame) {
      Frame value; value.before=counts();
      if(std::string_view(mode)=="active")
        for(size_t i=0;i<bodies.size();++i) {
          if(bodies[i]==nullptr || !bodies[i]->valid()) continue;
          // Fixed, preregistered input with conservative per-rig sleep disabled.
          // Fast bounded motion can still satisfy Jolt's spatial sleep test;
          // these samples measure twenty continuously active rigs, independently
          // of the normal game's unchanged sleeping policy.
          const float sign=((frame+2)/signBlockFrames+int(i))%2==0 ? 1.f : -1.f;
          bodies[i]->addImpulse({50.f*sign,5.f,25.f*sign});
          }
      value.afterImpulse=counts();
      world.tick(17);
      value.afterTick=counts();
      if(!partsValid(value.before)) value.failureMask|=1;
      if(!partsValid(value.afterImpulse)) value.failureMask|=2;
      if(!partsValid(value.afterTick)) value.failureMask|=4;
      if(!activityValid(value.before)) value.failureMask|=8;
      if(!activityValid(value.afterImpulse)) value.failureMask|=16;
      if(!activityValid(value.afterTick)) value.failureMask|=32;
      result.coverageValid=result.coverageValid && value.failureMask==0;
      result.frames.push_back(value);
      // The counts-only preflight neither reads nor emits any timing samples.
      if(!preflight && frame>=warmup) {
        const float time=world.stepMilliseconds(); result.times.push_back(time);
        result.timingValid=result.timingValid && std::isfinite(time) && time>=0;
        }
      }
    if(!preflight) result.timingValid=result.timingValid && result.times.size()==size_t(frames-warmup);
    results.push_back(std::move(result));
    }
  const bool valid=std::all_of(results.begin(),results.end(),[](const Result& result) {
    return result.coverageValid && result.timingValid;
    });
  std::FILE* file=std::fopen(output,"w");
  if(file==nullptr) { std::fprintf(stderr,"FAIL: twenty-body output is not writable: %s\n",output); return false; }
  std::fprintf(file,"{\"schema_version\":2,\"kind\":\"%s\",\"status\":\"%s\",\"coverage_valid\":%s,"
                    "\"scope\":\"%s\",\"dt_ms\":17,\"warmup_frames\":120,\"coverage_frames\":1020,\"body_count\":20,"
                    "\"active_policy\":\"conservative per-ragdoll allowSleeping=false; normal game policy unchanged\","
                    "\"active_input\":{\"impulse_ns\":[50,5,25],\"sign_block_frames\":%d,"
                    "\"schedule\":\"each frame; sign=(((frame+2)/5+actor_index)%%2==0?+1:-1); X and Z share sign; Y always positive\"},"
                    "\"coverage_failure_mask\":{\"1\":\"before input parts\",\"2\":\"after input parts\",\"4\":\"after tick parts\","
                    "\"8\":\"before input activity\",\"16\":\"after input activity\",\"32\":\"after tick activity\"}",
                 preflight ? "counts_only_preflight" : "native_cpu_benchmark",valid ? "PASS" : "FAIL",
                 std::all_of(results.begin(),results.end(),[](const Result& r) { return r.coverageValid; }) ? "true" : "false",
                 preflight ? "actual ragdoll activity and native part counts before input, after input and after each tick" :
                             "CollisionWorld::tick wall time; no game, rendering or query work",signBlockFrames);
  if(!preflight) std::fprintf(file,",\"measurement_frames\":900");
  std::fprintf(file,",\"cases\":[");
  const auto writeCounts=[&](const Counts& value) {
    std::fprintf(file,"{\"ragdolls\":%zu,\"awake\":%zu,\"sleeping\":%zu,\"invalid\":%zu,"
                      "\"rig_parts\":%zu,\"native_parts\":%zu,\"world_bodies\":%zu}",
                 value.ragdolls,value.awake,value.sleeping,value.invalid,value.rigParts,value.nativeParts,value.worldBodies);
    };
  for(size_t i=0;i<results.size();++i) {
    const auto& result=results[i];
    if(i>0) std::fprintf(file,",");
    std::fprintf(file,"{\"mode\":\"%s\",\"allow_sleeping\":%s,\"physical_parts\":%zu,\"static_bodies\":%zu,\"coverage_valid\":%s,"
                      "\"failed_frames\":%zu,\"preparation_frames\":%d,\"preparation_valid\":%s,\"initial\":",
                 result.mode,std::string_view(result.mode)=="active" ? "false" : "true",
                 result.parts,result.staticBodies,result.coverageValid ? "true" : "false",
                 size_t(std::count_if(result.frames.begin(),result.frames.end(),[](const Frame& f) { return f.failureMask!=0; })),
                 result.preparationFrames,result.preparationValid ? "true" : "false");
    writeCounts(result.initial); std::fprintf(file,",\"prepared\":"); writeCounts(result.prepared);
    std::fprintf(file,",\"counts\":[");
    for(size_t f=0;f<result.frames.size();++f) {
      const auto& value=result.frames[f];
      std::fprintf(file,"%s{\"frame\":%zu,\"failure_mask\":%u,\"before_input\":",f==0 ? "" : ",",f,value.failureMask);
      writeCounts(value.before); std::fprintf(file,",\"after_input\":"); writeCounts(value.afterImpulse);
      std::fprintf(file,",\"after_tick\":"); writeCounts(value.afterTick); std::fprintf(file,"}");
      }
    std::fprintf(file,"]");
    if(!preflight) {
      std::fprintf(file,",\"timing_valid\":%s",result.timingValid ? "true" : "false");
      if(result.timingValid) {
        auto sorted=result.times; std::sort(sorted.begin(),sorted.end());
        std::fprintf(file,",\"p50_ms\":%.9g,\"p95_ms\":%.9g",double(sorted[449]),double(sorted[854]));
        } else {
        std::fprintf(file,",\"p50_ms\":null,\"p95_ms\":null");
        }
      std::fprintf(file,",\"samples_ms\":[");
      for(size_t f=0;f<result.times.size();++f) {
        std::fprintf(file,"%s",f==0 ? "" : ",");
        if(std::isfinite(result.times[f]) && result.times[f]>=0) std::fprintf(file,"%.9g",double(result.times[f]));
        else std::fprintf(file,"null");
        }
      std::fprintf(file,"]");
      }
    std::fprintf(file,"}");
    }
  std::fprintf(file,"]}\n");
  const bool outputValid=std::ferror(file)==0;
  const bool closeValid=std::fclose(file)==0;
  // Even a disqualified sample window keeps all 1020 activity rows and all 900
  // native timing samples. Return failure only after the raw file is complete.
  if(!outputValid || !closeValid) std::fprintf(stderr,"FAIL: twenty-body raw output could not be completed\n");
  if(!valid) std::fprintf(stderr,"FAIL: twenty-body coverage or samples; full raw result written to %s\n",output);
  return valid && outputValid && closeValid;
  }

}

int main(int argc, const char** argv) {
  try {
    if(argc==3 && (std::string_view(argv[1])=="--benchmark20" || std::string_view(argv[1])=="--preflight20")) {
      return benchmarkTwentyBodies(argv[2],std::string_view(argv[1])=="--preflight20") ? EXIT_SUCCESS : EXIT_FAILURE;
      }
    if(argc==3 && std::string_view(argv[1])=="--replay-creation") {
      replayCreation(argv[2]);
      return EXIT_SUCCESS;
      }
    require(argc==1,"usage: gothic-ragdoll-contract [--benchmark20 output.json | --preflight20 output.json | --replay-creation captured.jsonl]");
    testInvalidCreationAndScale();
    testKinematicPoseAndInteraction();
    testLocalMotorReactions();
    testLimpBoneImpulseOnce();
    testCorpseRayMetadata();
    testProjectileWorldStopOptIn();
    testPoseOverlayAndRecovery();
    testRecoveryMatchesAnimBlendAndSaves();
    testMalformedRecoveryArchive();
    testPoseActualDriverSave();
    testPoseAffineRecoveryAndSaves();
    testPhysicalVelocityHandoff();
    testPoseFarWorldAttachments();
    testOriginReplayHierarchySeed();
    testFiniteRecoveryHierarchy();
    testBenchmarkSleepPolicy();
    testDropSleepWake();
    testSlopeAndCliff();
    testStateRoundTripAndAtomicRejection();
    testMovedStateRebaseAndReference();
    testAnatomicalKneeWithLongitudinalBoneAxis();
    std::puts("Ragdoll contracts passed");
    return EXIT_SUCCESS;
    } catch(const std::exception& error) {
      std::fprintf(stderr,"FAIL: ragdoll contract exception: %s\n",error.what());
      return EXIT_FAILURE;
      }
  }
