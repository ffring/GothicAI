#include "navigationeye.h"

#include "physics/collisionworld.h"
#include "physics/dynamicworld.h"
#include "world/objects/npc.h"
#include "world/world.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <stdexcept>

namespace {
using Tempest::Vec3;
constexpr float Deg = 3.14159265358979323846f/180.f;

NavigationEye::HitKind eyeKind(DynamicWorld::NavigationHit kind) {
  using H = DynamicWorld::NavigationHit;
  using E = NavigationEye::HitKind;
  switch(kind) {
    case H::None:       return E::None;
    case H::World:      return E::World;
    case H::StaticVob:  return E::StaticVob;
    case H::MovableVob: return E::MovableVob;
    case H::Npc:        return E::Npc;
    case H::Water:      return E::Water;
    }
  return E::None;
  }

void setHit(NavigationEye::Ray& ray, const DynamicWorld::NavigationRayResult& hit, float distanceLimit) {
  ray.hit = hit.hasCol;
  ray.kind = eyeKind(hit.kind);
  ray.point = hit.hasCol ? hit.point : ray.origin+ray.direction*distanceLimit;
  ray.distanceCm = hit.hasCol ? (hit.point-ray.origin).length() : distanceLimit;
  const float normalLength = hit.normal.length();
  ray.normalY = normalLength>1.e-6f ? std::clamp(hit.normal.y/normalLength,-1.f,1.f) : 0.f;
  }
}

// Kept beside the sensor so the navigation experiment has no edits in walking code.
DynamicWorld::NavigationRayResult DynamicWorld::navigationRay(const Vec3& from, const Vec3& to,
                                                              const Npc* except) const {
  constexpr uint32_t mask = (1u<<C_Landscape)|(1u<<C_Object)|(1u<<C_Item)|(1u<<C_MovableVob)|
                            (1u<<C_Ragdoll)|(1u<<C_LivingBody)|(1u<<C_ActiveBody)|(1u<<C_Water);
  const Ragdoll* ownRig = except!=nullptr ? except->livingBody() : nullptr;
  if(ownRig==nullptr && except!=nullptr)
    ownRig = except->ragdollBody();
  const auto hit = world->rayCast(from,to,mask,false,nullptr,nullptr,ownRig);
  NavigationRayResult result;
  result.hasCol = hit.hasHit;
  result.point = hit.hasHit ? hit.point : to;
  result.normal = hit.normal;
  if(!hit.hasHit)
    return result;
  const int category = hit.body!=nullptr ? hit.body->category() : world->navigationBodyCategory(hit.nativeBodyId);
  switch(category) {
    case C_Ragdoll:
    case C_LivingBody:
    case C_ActiveBody: result.kind = NavigationHit::Npc; break;
    case C_Landscape: result.kind = NavigationHit::World; break;
    case C_Water: result.kind = NavigationHit::Water; break;
    case C_Object:
      result.kind = hit.body->isMovable() ? NavigationHit::MovableVob : NavigationHit::StaticVob;
      break;
    case C_Item:
    case C_MovableVob: result.kind = NavigationHit::MovableVob; break;
    default: result.kind = NavigationHit::StaticVob; break;
    }
  return result;
  }

NavigationEye::NavigationEye():NavigationEye(Config{}) {
  }

NavigationEye::NavigationEye(const Config& config):cfg(config) {
  if((cfg.layout!=250 && cfg.layout!=500 && cfg.layout!=1000) ||
     !std::isfinite(cfg.centerRangeCm) || cfg.centerRangeCm<=0.f || cfg.centerRangeCm>10000.f ||
     !std::isfinite(cfg.peripheralRangeCm) || cfg.peripheralRangeCm<=0.f || cfg.peripheralRangeCm>3000.f ||
     !std::isfinite(cfg.rangeMultiplier) || cfg.rangeMultiplier<=0.f || cfg.rangeMultiplier>1.f ||
     !std::isfinite(cfg.nearRangeCm) || cfg.nearRangeCm<=0.f || cfg.nearRangeCm>cfg.centerRangeCm ||
     cfg.nearPeriodMs==0 || cfg.farPeriodMs<cfg.nearPeriodMs)
    throw std::invalid_argument("invalid navigation eye configuration");

  const size_t centerIntervals = cfg.layout==250 ? 12 : cfg.layout==500 ? 20 : 28;
  const size_t verticalIntervals = cfg.layout==250 ? 13 : cfg.layout==500 ? 19 : 27;
  std::vector<float> yaw;
  for(size_t i=0; i<=centerIntervals; ++i)
    yaw.push_back(-60.f+120.f*float(i)/float(centerIntervals));
  const float peripheralStep = cfg.layout==1000 ? 10.f : 20.f;
  for(float angle=60.f+peripheralStep; angle<=100.f; angle+=peripheralStep) {
    yaw.push_back(-angle);
    yaw.push_back(angle);
    }
  std::sort(yaw.begin(),yaw.end());
  rays.reserve(yaw.size()*(verticalIntervals+1));
  localDirections.reserve(rays.capacity());
  for(float angle:yaw)
    for(size_t row=0; row<=verticalIntervals; ++row) {
      Ray ray;
      ray.yawDeg = angle;
      ray.pitchDeg = -70.f+130.f*float(row)/float(verticalIntervals);
      const bool central = std::abs(angle)<=60.f;
      ray.maxRangeCm = (central ? cfg.centerRangeCm : std::min(cfg.centerRangeCm,cfg.peripheralRangeCm))*cfg.rangeMultiplier;
      ray.distanceCm = ray.maxRangeCm;
      const float h = angle*Deg, v = ray.pitchDeg*Deg;
      localDirections.push_back(Vec3(std::cos(v)*std::cos(h),std::sin(v),std::cos(v)*std::sin(h)));
      ray.direction = localDirections.back(); // preview geometry in the canonical head frame
      ray.point = ray.direction*ray.maxRangeCm;
      rays.push_back(ray);
      }
  far.resize(rays.size());
  }

void NavigationEye::reset() {
  lastNear = lastFar = 0;
  sampled = false;
  for(size_t i=0; i<rays.size(); ++i) {
    auto& ray = rays[i];
    ray.origin = ray.direction = ray.point = Vec3();
    ray.distanceCm = ray.maxRangeCm;
    ray.normalY = 0;
    ray.kind = HitKind::None;
    ray.sampledAtMs = ray.nearSampledAtMs = 0;
    ray.hit = ray.complete = false;
    far[i].valid = false;
    }
  }

NavigationEye::Update NavigationEye::update(const Npc& npc, uint64_t gameMs) {
  const auto begin = std::chrono::steady_clock::now();
  Update result;
  if(sampled && gameMs<lastNear)
    reset();
  if(sampled && gameMs>=lastNear && gameMs-lastNear<cfg.nearPeriodMs)
    return result;
  const bool farDue = !sampled || gameMs<lastFar || gameMs-lastFar>=cfg.farPeriodMs;
  const auto pose = npc.navigationEyePose();
  const auto* physics = npc.world().physic();
  if(physics==nullptr)
    return result;
  const float nearRange = cfg.nearRangeCm;
  for(size_t i=0; i<rays.size(); ++i) {
    auto& ray = rays[i];
    const auto& local = localDirections[i];
    Ray next = ray;
    next.origin = pose.origin;
    next.direction = Vec3::normalize(pose.forward*local.x+pose.up*local.y+pose.right*local.z);
    next.sampledAtMs = next.nearSampledAtMs = gameMs;
    const float nearEnd = std::min(nearRange,next.maxRangeCm);
    const auto nearHit = physics->navigationRay(next.origin,next.origin+next.direction*nearEnd,&npc);
    ++result.nearCasts;
    setHit(next,nearHit,nearEnd);
    next.complete = next.hit || nearEnd>=next.maxRangeCm;
    if(!next.complete && farDue) {
      const auto farHit = physics->navigationRay(next.origin+next.direction*nearEnd,
                                                 next.origin+next.direction*next.maxRangeCm,&npc);
      ++result.farCasts;
      setHit(next,farHit,next.maxRangeCm);
      next.complete = true;
      far[i].ray = next;
      far[i].valid = true;
      }
    else if(!next.complete && far[i].valid) {
      // Far observations are memories, not freshly cast rays in the new head frame.
      next = far[i].ray;
      next.nearSampledAtMs = gameMs;
      }
    ray = next;
    }
  lastNear = sampled ? lastNear+((gameMs-lastNear)/cfg.nearPeriodMs)*cfg.nearPeriodMs : gameMs;
  if(farDue)
    lastFar = sampled ? lastFar+((gameMs-lastFar)/cfg.farPeriodMs)*cfg.farPeriodMs : gameMs;
  sampled = true;
  result.updated = true;
  result.farUpdated = farDue;
  result.casts = result.nearCasts+result.farCasts;
  result.costUs = std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-begin).count();
  return result;
  }

const char* NavigationEye::hitKindName(HitKind kind) {
  switch(kind) {
    case HitKind::None: return "none";
    case HitKind::World: return "world";
    case HitKind::StaticVob: return "static_vob";
    case HitKind::MovableVob: return "movable_vob";
    case HitKind::Npc: return "npc";
    case HitKind::Water: return "water";
    }
  return "none";
  }
