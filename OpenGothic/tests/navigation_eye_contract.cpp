#include "game/navigationeye.h"
#include "physics/collisionworld.h"
#include "physics/dynamicworld.h"
#include "physics/physicmeshshape.h"
#include "physics/ragdoll.h"
#include "graphics/mesh/hitzones.h"
#include "graphics/mesh/skeleton.h"
#include "graphics/mesh/submesh/packedmesh.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <stdexcept>

namespace {
using Tempest::Vec3;

void require(bool condition, const char* message) {
  if(condition)
    return;
  std::fprintf(stderr,"FAIL: %s\n",message);
  std::exit(EXIT_FAILURE);
  }

void near(float actual, float expected, const char* message) {
  require(std::isfinite(actual) && std::abs(actual-expected)<.001f,message);
  }

zenkit::Mesh wall(float x, zenkit::MaterialGroup material = zenkit::MaterialGroup::STONE) {
  zenkit::Mesh mesh;
  mesh.vertices = {{x,0,-100},{x,360,-100},{x,360,100},{x,0,100}};
  mesh.materials.resize(1);
  mesh.materials[0].group = material;
  // Both faces deliberately: the visibility check must not depend on triangle winding.
  mesh.polygons.vertex_indices = {0,1,2,0,2,3,2,1,0,3,2,0};
  mesh.polygons.material_indices = {0,0,0,0};
  return mesh;
  }

void layoutAndPhysicalWall(size_t layout, size_t count) {
  NavigationEye::Config cfg;
  cfg.layout = layout;
  NavigationEye eye(cfg);
  require(eye.rayCount()==count,"layout ray count");
  float minYaw = 1000, maxYaw = -1000, minPitch = 1000, maxPitch = -1000;
  size_t central = 0, peripheral = 0;
  DynamicWorld inFront(nullptr,wall(100));
  DynamicWorld behind(nullptr,wall(-100));
  size_t frontHits = 0, rearHits = 0;
  const Vec3 origin(0,180,0);
  for(const auto& ray:eye.samples()) {
    minYaw = std::min(minYaw,ray.yawDeg); maxYaw = std::max(maxYaw,ray.yawDeg);
    minPitch = std::min(minPitch,ray.pitchDeg); maxPitch = std::max(maxPitch,ray.pitchDeg);
    near(ray.direction.length(),1.f,"ray direction is normalized");
    require(std::abs(ray.yawDeg)<=100.f,"rear 160 degrees contain no ray");
    if(std::abs(ray.yawDeg)<=60.f) {
      ++central;
      near(ray.maxRangeCm,10000.f,"central 100 metre range");
      }
    else {
      ++peripheral;
      near(ray.maxRangeCm,3000.f,"peripheral 30 metre range");
      }
    const auto hit = inFront.navigationRay(origin,origin+ray.direction*ray.maxRangeCm,nullptr);
    if(hit.hasCol) {
      ++frontHits;
      require(hit.kind==DynamicWorld::NavigationHit::World,"world hit category");
      near(hit.point.x,100.f,"wall distance geometry");
      near(hit.normal.y,0.f,"vertical wall normal");
      }
    if(behind.navigationRay(origin,origin+ray.direction*ray.maxRangeCm,nullptr).hasCol)
      ++rearHits;
    }
  near(minYaw,-100.f,"left FOV boundary"); near(maxYaw,100.f,"right FOV boundary");
  near(minPitch,-70.f,"ground FOV boundary"); near(maxPitch,60.f,"sky FOV boundary");
  require(central>peripheral*2,"central angular sampling is denser than periphery");
  require(frontHits>0,"wall one metre in front is visible");
  require(rearHits==0,"wall one metre behind is invisible");
  std::printf("navigation eye %zu: %zu rays, wall front=%zu rear=%zu\n",layout,count,frontHits,rearHits);
  }

void rangesAndWater() {
  NavigationEye::Config cfg;
  cfg.centerRangeCm = 2000.f;
  cfg.rangeMultiplier = .5f;
  NavigationEye eye(cfg);
  for(const auto& ray:eye.samples())
    near(ray.maxRangeCm,1000.f,"one range multiplier also respects short centre range");
  DynamicWorld water(nullptr,wall(100,zenkit::MaterialGroup::WATER));
  const auto hit = water.navigationRay(Vec3(0,180,0),Vec3(1000,180,0),nullptr);
  require(hit.hasCol && hit.kind==DynamicWorld::NavigationHit::Water,"water has its own category");
  cfg.layout = 42;
  bool rejected = false;
  try { NavigationEye invalid(cfg); } catch(const std::invalid_argument&) { rejected = true; }
  require(rejected,"unsupported layouts are rejected");
  }

void staticAndMovingVobs() {
  const auto mesh = wall(100);
  const std::unique_ptr<PhysicMeshShape> shape(PhysicMeshShape::load(PackedMesh(mesh,PackedMesh::PK_Physic)));
  require(shape!=nullptr,"vob query fixture");
  DynamicWorld world(nullptr,zenkit::Mesh{});
  auto object = world.addStaticObj(shape.get(),Tempest::Matrix4x4::mkIdentity());
  auto hit = world.navigationRay(Vec3(0,180,0),Vec3(1000,180,0),nullptr);
  require(hit.hasCol && hit.kind==DynamicWorld::NavigationHit::StaticVob,"static vob category");
  object = DynamicWorld::Item();
  object = world.addMovableObj(shape.get(),Tempest::Matrix4x4::mkIdentity());
  hit = world.navigationRay(Vec3(0,180,0),Vec3(1000,180,0),nullptr);
  require(hit.hasCol && hit.kind==DynamicWorld::NavigationHit::MovableVob,"kinematic door/lift category");
  }

zenkit::ModelHierarchy humanoidHierarchy() {
  zenkit::ModelHierarchy source;
  struct Bone { const char* name; int16_t parent; Vec3 offset; };
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
    {"BIP01 R FOOT",17,{0,-40,0}}, {"BIP01 R TOE",18,{0,0,15}}
    };
  for(const auto& bone:bones) {
    zenkit::ModelHierarchyNode node;
    node.name = bone.name;
    node.parent_index = bone.parent;
    auto transform = Tempest::Matrix4x4::mkIdentity();
    transform.translate(bone.offset);
    static_assert(sizeof(node.transform)==sizeof(transform));
    std::memcpy(&node.transform,&transform,sizeof(transform));
    source.nodes.push_back(node);
    }
  source.bbox.min = {-80,0,-20}; source.bbox.max = {80,190,25};
  source.collision_bbox = source.bbox;
  return source;
  }

void livingRigQuerySemantics() {
  constexpr size_t forearm = 7;
  Skeleton skeleton(humanoidHierarchy(),nullptr,"HUMANS.MDS");
  HitZones zones;
  zones.setSkeleton(&skeleton);
  require(zones.isValid(),"humanoid fixture needs real physical hit capsules");
  auto object = Tempest::Matrix4x4::mkIdentity();
  object.translate(0,1000,0);
  auto pose = skeleton.tr;
  for(auto& bone:pose)
    bone = object*bone;
  const auto forearmPoint = [](const Ragdoll& body) {
    std::vector<Tempest::Matrix4x4> actual;
    require(body.getPose(actual),"live rig must expose its actual native pose");
    const auto& bone = actual[forearm];
    return Vec3(bone.at(3,0)+12.f,bone.at(3,1),bone.at(3,2));
    };

  // Exercise the established low-level API on actual living and promoted active parts.
  CollisionWorld collision;
  auto body = collision.createKinematicBody(skeleton,zones,pose.data(),pose.size(),object);
  require(body && body->isKinematic(),"regression fixture must create a native living rig");
  const auto checkGeneric = [&](int category) {
    const auto point = forearmPoint(*body);
    const auto from = point+Vec3(0,0,300), to = point-Vec3(0,0,300);
    const uint32_t mask = 1u<<category;
    const auto hit = collision.rayCast(from,to,mask);
    require(hit.hasHit,"explicit live/active layer must be hit by the generic ray");
    require(hit.ragdoll==nullptr && hit.bone==size_t(-1),
            "generic live/active hits must not acquire corpse attachment metadata");
    require(collision.navigationBodyCategory(hit.nativeBodyId)==category,
            "navigation must resolve the selected native live/active layer independently");
    std::vector<CollisionWorld::QueryResult> all;
    collision.rayCast(from,to,mask,false,&all);
    require(!all.empty(),"all-hit query must hit the real live/active parts");
    for(const auto& part:all)
      require(part.ragdoll==nullptr && part.bone==size_t(-1),
              "generic all-hit live/active metadata must retain its prior semantics");
    require(!collision.rayCast(from,to,mask,false,nullptr,nullptr,body.get()).hasHit,
            "eye self exclusion must omit every part of the current living/active rig");
    };
  checkGeneric(DynamicWorld::C_LivingBody);
  require(body->queueHit(forearm,forearmPoint(*body),Vec3(0,0,3),1.f),"forearm reaction must queue");
  collision.tick(17);
  require(!body->reactionBones().empty(),"the fixture must promote an actual active limb chain");
  checkGeneric(DynamicWorld::C_ActiveBody);

  // The engine-facing eye API sees both states as NPCs, without asking generic rays for a ragdoll.
  DynamicWorld navigation(nullptr,zenkit::Mesh{});
  auto seen = navigation.createKinematicBody(skeleton,zones,pose.data(),pose.size(),object);
  require(seen && seen->isKinematic(),"navigation fixture must create a native living rig");
  const auto checkEye = [&] {
    const auto point = forearmPoint(*seen);
    const auto hit = navigation.navigationRay(point+Vec3(0,0,300),point-Vec3(0,0,300),nullptr);
    require(hit.hasCol && hit.kind==DynamicWorld::NavigationHit::Npc,
            "navigation ray must classify a living/active native rig as NPC");
    };
  checkEye();
  require(seen->queueHit(forearm,forearmPoint(*seen),Vec3(0,0,3),1.f),"navigation reaction must queue");
  navigation.tick(17);
  require(!seen->reactionBones().empty(),"navigation fixture must contain actual active parts");
  checkEye();

  // Corpse attachment consumers keep their original selected rig and bone.
  CollisionWorld corpseWorld;
  auto corpse = corpseWorld.createRagdoll(skeleton,zones,pose.data(),pose.size(),object);
  require(corpse && corpse->valid(),"corpse fixture must create native parts");
  const auto point = forearmPoint(*corpse);
  const auto corpseHit = corpseWorld.rayCast(point+Vec3(0,0,300),point-Vec3(0,0,300),1u<<DynamicWorld::C_Ragdoll);
  require(corpseHit.hasHit && corpseHit.ragdoll==corpse.get() && corpseHit.bone!=size_t(-1),
          "generic corpse hits must preserve rig and bone attachment metadata");
  }
}

int main() {
  layoutAndPhysicalWall(250,238);
  layoutAndPhysicalWall(500,500);
  layoutAndPhysicalWall(1000,1036);
  rangesAndWater();
  staticAndMovingVobs();
  livingRigQuerySemantics();
  std::puts("navigation eye contracts passed");
  }
