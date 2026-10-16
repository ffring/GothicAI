#include "game/navigationeye.h"
#include "physics/dynamicworld.h"
#include "physics/physicmeshshape.h"
#include "graphics/mesh/submesh/packedmesh.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
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
}

int main() {
  layoutAndPhysicalWall(250,238);
  layoutAndPhysicalWall(500,500);
  layoutAndPhysicalWall(1000,1036);
  rangesAndWater();
  staticAndMovingVobs();
  std::puts("navigation eye contracts passed");
  }
