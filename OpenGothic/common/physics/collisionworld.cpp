#include "collisionworld.h"
#include "physicvbo.h"
#include "dynamicworld.h"
#include "ragdoll.h"
#include "world/objects/item.h"
#include "world/objects/npc.h"
#include "world/world.h"

#include <Jolt/RegisterTypes.h>
#include <Jolt/Core/Factory.h>
#include <Jolt/Core/JobSystemThreadPool.h>
#include <Jolt/Core/TempAllocator.h>
#include <Jolt/Geometry/GJKClosestPoint.h>
#include <Jolt/Physics/PhysicsSystem.h>
#include <Jolt/Physics/Body/BodyCreationSettings.h>
#include <Jolt/Physics/Body/BodyLock.h>
#include <Jolt/Physics/Body/BodyFilter.h>
#include <Jolt/Physics/Character/CharacterVirtual.h>
#include <Jolt/Physics/Collision/CollisionCollectorImpl.h>
#include <Jolt/Physics/Collision/ContactListener.h>
#include <Jolt/Physics/Collision/EstimateCollisionResponse.h>
#include <Jolt/Physics/Collision/RayCast.h>
#include <Jolt/Physics/Collision/CastResult.h>
#include <Jolt/Physics/Collision/ShapeCast.h>
#include <Jolt/Physics/Collision/ShapeFilter.h>
#include <Jolt/Physics/Collision/TransformedShape.h>
#include <Jolt/Physics/Collision/Shape/MeshShape.h>
#include <Jolt/Physics/Collision/Shape/CapsuleShape.h>
#include <Jolt/Physics/Collision/Shape/SphereShape.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <mutex>
#include <limits>
#include <iterator>
#include <stdexcept>
#include <thread>
#include <tuple>
#ifdef __APPLE__
#include <sys/types.h>
#include <sys/sysctl.h>
#endif

namespace {
  constexpr JPH::BroadPhaseLayer staticLayer(0);
  constexpr JPH::BroadPhaseLayer dynamicLayer(1);
  constexpr JPH::BroadPhaseLayer sensorLayer(2);

  struct LayerInterface final : JPH::BroadPhaseLayerInterface {
    JPH::uint GetNumBroadPhaseLayers() const override { return 3; }
    JPH::BroadPhaseLayer GetBroadPhaseLayer(JPH::ObjectLayer layer) const override {
      if(layer==DynamicWorld::C_Item || layer==DynamicWorld::C_Ragdoll || layer==DynamicWorld::C_LivingBody || layer==DynamicWorld::C_ActiveBody || layer==DynamicWorld::C_MovableVob)
        return dynamicLayer;
      if(layer==DynamicWorld::C_Water || layer==DynamicWorld::C_Ghost || layer==DynamicWorld::C_MovableQuery || layer==DynamicWorld::C_SmallVobQuery)
        return sensorLayer;
      return staticLayer;
      }
#if defined(JPH_EXTERNAL_PROFILE) || defined(JPH_PROFILE_ENABLED)
    const char* GetBroadPhaseLayerName(JPH::BroadPhaseLayer layer) const override {
      return layer==staticLayer ? "World" : layer==dynamicLayer ? "Items" : "Sensors";
      }
#endif
    };

  struct LayerPairFilter final : JPH::ObjectLayerPairFilter {
    bool ShouldCollide(JPH::ObjectLayer a, JPH::ObjectLayer b) const override {
      if(a==DynamicWorld::C_MovableQuery || a==DynamicWorld::C_SmallVobQuery ||
         b==DynamicWorld::C_MovableQuery || b==DynamicWorld::C_SmallVobQuery)
        return false;
      if(a==DynamicWorld::C_ActiveBody)
        return b==DynamicWorld::C_Landscape || b==DynamicWorld::C_Object || b==DynamicWorld::C_Item || b==DynamicWorld::C_Ragdoll || b==DynamicWorld::C_MovableVob;
      if(b==DynamicWorld::C_ActiveBody)
        return a==DynamicWorld::C_Landscape || a==DynamicWorld::C_Object || a==DynamicWorld::C_Item || a==DynamicWorld::C_Ragdoll || a==DynamicWorld::C_MovableVob;
      if(a==DynamicWorld::C_LivingBody)
        return b==DynamicWorld::C_Item || b==DynamicWorld::C_Ragdoll || b==DynamicWorld::C_MovableVob;
      if(b==DynamicWorld::C_LivingBody)
        return a==DynamicWorld::C_Item || a==DynamicWorld::C_Ragdoll || a==DynamicWorld::C_MovableVob;
      return (a==DynamicWorld::C_Item || b==DynamicWorld::C_Item ||
              a==DynamicWorld::C_Ragdoll || b==DynamicWorld::C_Ragdoll ||
              a==DynamicWorld::C_MovableVob || b==DynamicWorld::C_MovableVob) &&
             a!=DynamicWorld::C_Null && b!=DynamicWorld::C_Null;
      }
    };

  struct BroadPhaseFilter final : JPH::ObjectVsBroadPhaseLayerFilter {
    bool ShouldCollide(JPH::ObjectLayer layer, JPH::BroadPhaseLayer broad) const override {
      if(layer==DynamicWorld::C_LivingBody)
        return broad==dynamicLayer;
      if(layer==DynamicWorld::C_MovableQuery || layer==DynamicWorld::C_SmallVobQuery)
        return false;
      return layer==DynamicWorld::C_Item || layer==DynamicWorld::C_Ragdoll || layer==DynamicWorld::C_ActiveBody || layer==DynamicWorld::C_MovableVob ||
             (layer!=DynamicWorld::C_Null && broad==dynamicLayer);
      }
    };

  struct QueryLayerFilter final : JPH::ObjectLayerFilter {
    explicit QueryLayerFilter(uint32_t mask):mask(mask) {}
    bool ShouldCollide(JPH::ObjectLayer layer) const override {
      return layer<32 && (mask & (1u<<layer))!=0;
      }
    const uint32_t mask;
    };

  struct QueryBroadPhaseFilter final : JPH::BroadPhaseLayerFilter {
    explicit QueryBroadPhaseFilter(uint32_t mask):mask(mask) {}
    bool ShouldCollide(JPH::BroadPhaseLayer layer) const override {
      constexpr uint32_t itemMask = (1u<<DynamicWorld::C_Item)|(1u<<DynamicWorld::C_Ragdoll)|
                                    (1u<<DynamicWorld::C_LivingBody)|(1u<<DynamicWorld::C_ActiveBody)|(1u<<DynamicWorld::C_MovableVob);
      constexpr uint32_t sensorMask = (1u<<DynamicWorld::C_Water)|(1u<<DynamicWorld::C_Ghost)|
                                      (1u<<DynamicWorld::C_MovableQuery)|(1u<<DynamicWorld::C_SmallVobQuery);
      if(layer==dynamicLayer)
        return (mask & itemMask)!=0;
      if(layer==sensorLayer)
        return (mask & sensorMask)!=0;
      return layer==staticLayer && (mask & ~(itemMask|sensorMask))!=0;
      }
    const uint32_t mask;
    };

  // Use simulated hulls, including small props. The movable query meshes are
  // duplicate sensor geometry used by the legacy controller and aiming only.
  constexpr uint32_t characterMask = (1u<<DynamicWorld::C_Landscape)|(1u<<DynamicWorld::C_Object)|
                                     (1u<<DynamicWorld::C_Item)|(1u<<DynamicWorld::C_MovableVob)|
                                     (1u<<DynamicWorld::C_Ragdoll);

  float angularMotionDisc(const JPH::Shape& shape) {
    const auto bounds = shape.GetLocalBounds();
    // Jolt's bounds are centered on the center of mass. The previous query
    // measured the bounding sphere from the original shape's local origin.
    return bounds.GetExtent().Length()+(bounds.GetCenter()+shape.GetCenterOfMass()).Length();
    }

  bool npcContactCandidate(const JPH::Shape& shape, JPH::Vec3Arg center, const JPH::Body& body,
                           JPH::Vec3Arg scale, const JPH::Vec3 (&vertices)[3], float contactDistance, JPH::Vec3* outNormal=nullptr) {
    if(shape.GetSubType()!=JPH::EShapeSubType::Capsule && shape.GetSubType()!=JPH::EShapeSubType::Sphere)
      return true;
    struct CapsuleCore {
      JPH::Vec3 center;
      float halfHeight;
      JPH::Vec3 GetSupport(JPH::Vec3Arg direction) const {
        // Keep the first endpoint on ties, including the zero direction.
        const bool upper = direction.GetY()>=0.f || direction.LengthSq()<std::numeric_limits<float>::epsilon()*std::numeric_limits<float>::epsilon();
        return center+JPH::Vec3(0,upper ? halfHeight : -halfHeight,0);
        }
      };
    struct TriangleCore {
      JPH::Vec3 vertices[3];
      JPH::Vec3 GetSupport(JPH::Vec3Arg direction) const {
        // The original mesh's support function kept its first vertex on ties.
        size_t index = 0;
        float best = vertices[0].Dot(direction);
        for(size_t i=1;i<3;++i) {
          const float projection = vertices[i].Dot(direction);
          if(projection>best) {
            index = i;
            best = projection;
            }
          }
        return vertices[index];
        }
      };
    const auto bodyPosition = JPH::Vec3(body.GetPosition());
    const auto offset = (center+bodyPosition)*0.5f;
    const float halfHeight = shape.GetSubType()==JPH::EShapeSubType::Capsule ?
                              static_cast<const JPH::CapsuleShape&>(shape).GetHalfHeightOfCylinder() : 0.f;
    const CapsuleCore capsule{center-offset,halfHeight};
    TriangleCore triangle;
    const auto transform = body.GetWorldTransform();
    const auto translation = bodyPosition-offset;
    for(size_t i=0;i<3;++i)
      triangle.vertices[i] = transform.Multiply3x3(scale*vertices[i])+translation;
    JPH::GJKClosestPoint gjk;
    auto direction = -JPH::Vec3::sAxisY();
    JPH::Vec3 pointA,pointB;
    // Scalar witness interpolation retains small components used by the old
    // movement query to choose a capsule support endpoint and its distance bound.
    const float distanceSq = gjk.GetClosestPoints<CapsuleCore,TriangleCore,true>(capsule,triangle,0.001f,contactDistance*contactDistance,direction,pointA,pointB);
    // The old query forwarded the previous simplex even when its conservative
    // core-distance bound stopped GJK early. An exact closest-distance query
    // would accept additional contacts and stop NPCs a frame before that query.
    if(distanceSq==std::numeric_limits<float>::max()) {
      JPH::Vec3 simplex[4],pointsA[4],pointsB[4];
      JPH::uint count = 0;
      gjk.GetClosestPointsSimplex(simplex,pointsA,pointsB,count);
      if(count==0)
        return false;
      }
    const float distance = (distanceSq==std::numeric_limits<float>::max() ? direction.Length() : std::sqrt(distanceSq))-shape.GetInnerRadius();
    const bool accepted = distance<0.f || distance*distance<contactDistance*contactDistance;
    // The controller also consumed the previous conservative witness direction
    // for separated contacts. It need not be the exact closest-surface normal.
    if(accepted && distance>=0.f && outNormal!=nullptr) {
      const auto axis = -direction;
      const float lengthSq = (axis.GetX()*axis.GetX()+axis.GetY()*axis.GetY())+axis.GetZ()*axis.GetZ();
      if(lengthSq>0.f && std::isfinite(lengthSq)) {
        const float inverseLength = 1.f/std::sqrt(lengthSq);
        *outNormal = JPH::Vec3(axis.GetX()*inverseLength,axis.GetY()*inverseLength,axis.GetZ()*inverseLength);
        }
      }
    return accepted;
    }

  struct SweepCollector final : JPH::CastShapeCollector {
    explicit SweepCollector(JPH::Vec3Arg direction):direction(direction) {}
    void AddHit(const JPH::ShapeCastResult& result) override {
      if(result.mFraction>=1.f)
        return;
      // The old conservative advancement rejected contacts whose separation grows.
      if(result.mFraction==0.f && direction.Dot(result.mPenetrationAxis)<=std::numeric_limits<float>::epsilon())
        return;
      if(!hadHit || result.mFraction<=hit.mFraction) {
        hit = result;
        hadHit = true;
        UpdateEarlyOutFraction(result.mFraction);
        }
      }
    bool HadHit() const { return hadHit; }
    const JPH::Vec3 direction;
    JPH::ShapeCastResult hit;
    bool hadHit = false;
    };

  struct Transform {
    JPH::Vec3 position;
    JPH::Quat rotation;
    JPH::Vec3 scale;
    };

  Transform toTransform(const Tempest::Matrix4x4& matrix) {
    const auto* m = matrix.data();
    auto x = JPH::Vec3(m[0],m[1],m[2]);
    auto y = JPH::Vec3(m[4],m[5],m[6]);
    auto z = JPH::Vec3(m[8],m[9],m[10]);
    auto s = JPH::Vec3(std::max(x.Length(),0.00001f), std::max(y.Length(),0.00001f), std::max(z.Length(),0.00001f));
    if(x.Cross(y).Dot(z)<0.f)
      s.SetX(-s.GetX());
    for(JPH::uint i=0;i<3;++i)
      if(std::abs(s[i]-1.f)<0.00001f)
        s.SetComponent(i,1.f);
    auto rotation = JPH::Mat44(JPH::Vec4(x/s.GetX(),0), JPH::Vec4(y/s.GetY(),0),
                              JPH::Vec4(z/s.GetZ(),0), JPH::Vec3::sZero()).GetQuaternion().Normalized();
    return {JPH::Vec3(m[12]*0.01f,m[13]*0.01f,m[14]*0.01f),rotation,s};
    }

  JPH::ShapeRefC scaledShape(const JPH::Shape* shape, JPH::Vec3Arg scale) {
    if(scale==JPH::Vec3::sOne())
      return shape;
    auto result = shape->ScaleShape(scale);
    if(result.HasError())
      throw std::runtime_error("Jolt shape scale: " + std::string(result.GetError().c_str()));
    return result.Get();
    }

  int workerCount() {
    unsigned int cores = std::max(1u,std::thread::hardware_concurrency());
#ifdef __APPLE__
    unsigned int performanceCores = 0;
    size_t size = sizeof(performanceCores);
    if(sysctlbyname("hw.perflevel0.physicalcpu",&performanceCores,&size,nullptr,0)==0 && performanceCores>0)
      cores = performanceCores;
#endif
    return int(cores>2 ? cores-2 : 1);
    }
  }

void Physics::initialize() {
  struct Registration {
    Registration() {
      JPH::RegisterDefaultAllocator();
      JPH::Factory::sInstance = new JPH::Factory();
      JPH::RegisterTypes();
      }
    ~Registration() {
      JPH::UnregisterTypes();
      delete JPH::Factory::sInstance;
      JPH::Factory::sInstance = nullptr;
      }
    };
  static Registration registration;
  }

uint32_t Physics::triangleData(const JPH::Shape& shape, JPH::SubShapeID subShape) {
  JPH::SubShapeID remainder;
  auto* leaf = shape.GetLeafShape(subShape,remainder);
  if(leaf!=nullptr && leaf->GetSubType()==JPH::EShapeSubType::Mesh)
    return static_cast<const JPH::MeshShape*>(leaf)->GetTriangleUserData(remainder);
  return 0;
  }

struct CollisionWorld::Impl final : JPH::ContactListener {
  struct NoiseImpact {
    JPH::BodyID body,other;
    Tempest::Vec3 point;
    zenkit::MaterialGroup surface;
    float impulse;
    uint64_t step;
    uint64_t logicalBody;
    bool otherPushing;
    };
  struct Impact {
    JPH::BodyID item;
    zenkit::MaterialGroup material;
    float impulse;
    float mass;
    uint64_t step;
    uint32_t landscape;
    uint32_t subShape;
    };

  Impl() : allocator(32*1024*1024), jobs(JPH::cMaxPhysicsJobs,JPH::cMaxPhysicsBarriers,workerCount()) {
    system.Init(65536,0,65536,32768,layers,broadFilter,pairFilter);
    system.SetGravity(JPH::Vec3(0,-DynamicWorld::gravityMS,0));
    system.SetCombineFriction([](const JPH::Body& a, const JPH::SubShapeID&, const JPH::Body& b, const JPH::SubShapeID&) {
      return std::clamp(a.GetFriction()*b.GetFriction(),-10.f,10.f);
      });
    auto settings = system.GetPhysicsSettings();
    settings.mTimeBeforeSleep = 2.f;
    settings.mPointVelocitySleepThreshold = 0.8f;
    system.SetPhysicsSettings(settings);
    system.SetContactListener(this);
    }

  void onContact(const JPH::Body& a, const JPH::Body& b, const JPH::ContactManifold& manifold,
                 const JPH::ContactSettings& settings) {
    auto markNpcContact = [](const JPH::Body& movable, const JPH::Body& other) {
      if(movable.GetObjectLayer()!=DynamicWorld::C_MovableVob ||
         (other.GetObjectLayer()!=DynamicWorld::C_LivingBody && other.GetObjectLayer()!=DynamicWorld::C_ActiveBody))
        return;
      auto* body = reinterpret_cast<CollisionBody*>(movable.GetUserData());
      if(body!=nullptr)
        body->npcContact.store(true,std::memory_order_relaxed);
      };
    markNpcContact(a,b);
    markNpcContact(b,a);
    if(a.GetObjectLayer()==DynamicWorld::C_MovableVob && b.GetObjectLayer()==DynamicWorld::C_MovableVob) {
      auto* first = reinterpret_cast<CollisionBody*>(a.GetUserData());
      auto* second = reinterpret_cast<CollisionBody*>(b.GetUserData());
      if(first!=nullptr && second!=nullptr && (first->touchedByNpc() || second->touchedByNpc())) {
        first->npcContact.store(true,std::memory_order_relaxed);
        second->npcContact.store(true,std::memory_order_relaxed);
        }
      }
    // Movable traces need the same push attribution even when audible noise
    // is disabled. Resolve identities after the native workers have finished.
    if((emitNoise || a.GetObjectLayer()==DynamicWorld::C_MovableVob || b.GetObjectLayer()==DynamicWorld::C_MovableVob) &&
       !manifold.mRelativeContactPointsOn1.empty() && !a.IsSensor() && !b.IsSensor()) {
      auto source=[this](const JPH::Body& body) {
        if(!emitNoise && body.GetObjectLayer()!=DynamicWorld::C_MovableVob) return false;
        return body.IsDynamic() && (body.GetObjectLayer()==DynamicWorld::C_Item ||
               body.GetObjectLayer()==DynamicWorld::C_MovableVob || body.GetObjectLayer()==DynamicWorld::C_Ragdoll ||
               body.GetObjectLayer()==DynamicWorld::C_ActiveBody);
        };
      const bool first=source(a),second=source(b);
      if(first || second) {
        const auto point=manifold.GetWorldSpaceContactPointOn1(0);
        const auto firstVelocity=a.GetPointVelocity(point),secondVelocity=b.GetPointVelocity(point);
        const float speed=(firstVelocity-secondVelocity).Dot(manifold.mWorldSpaceNormal);
        const bool firstNpc=b.GetObjectLayer()==DynamicWorld::C_LivingBody || b.GetObjectLayer()==DynamicWorld::C_ActiveBody ||
                            b.GetObjectLayer()==DynamicWorld::C_Ragdoll;
        const bool secondNpc=a.GetObjectLayer()==DynamicWorld::C_LivingBody || a.GetObjectLayer()==DynamicWorld::C_ActiveBody ||
                             a.GetObjectLayer()==DynamicWorld::C_Ragdoll;
        const bool firstPushed=first && firstNpc && secondVelocity.Dot(manifold.mWorldSpaceNormal)<-.05f;
        const bool secondPushed=second && secondNpc && firstVelocity.Dot(manifold.mWorldSpaceNormal)>.05f;
        // Resting contacts do not need the native impulse solver. Slow contact
        // with an NPC rig still retains who pushed a body before its fall.
        if(speed>=.9f || firstPushed || secondPushed) {
          float impulse=0.f;
          if(speed>=.9f) {
            JPH::CollisionEstimationResult response;
            JPH::EstimateCollisionResponse(a,b,manifold,response,settings.mCombinedFriction,settings.mCombinedRestitution);
            for(float contact:response.mContactImpulse) impulse+=contact;
            }
          auto surface=[](const JPH::Body& body,JPH::SubShapeID subShape) {
            const auto layer=body.GetObjectLayer();
            if(layer==DynamicWorld::C_Ragdoll || layer==DynamicWorld::C_LivingBody || layer==DynamicWorld::C_ActiveBody)
              return zenkit::MaterialGroup::UNDEFINED;
            auto* obj=reinterpret_cast<CollisionBody*>(body.GetUserData());
            if(obj==nullptr) return zenkit::MaterialGroup::STONE;
            if(obj->mesh!=nullptr)
              return obj->mesh->materialId(Physics::triangleData(*body.GetShape(),subShape));
            return obj->noiseSurface;
            };
          auto identity=[](const JPH::Body& body) -> uint64_t {
            const auto layer=body.GetObjectLayer();
            if(layer==DynamicWorld::C_Ragdoll || layer==DynamicWorld::C_ActiveBody)
              return (uint64_t(1)<<63)|body.GetCollisionGroup().GetGroupID();
            return body.GetID().GetIndexAndSequenceNumber();
            };
          std::lock_guard<std::mutex> guard(impactMutex);
          if(first && (impulse>=.08f || firstPushed))
            noises.push_back({a.GetID(),b.GetID(),toCentimeters(point),surface(b,manifold.mSubShapeID2),impulse,simulationStep,identity(a),
                              firstPushed});
          if(second && (impulse>=.08f || secondPushed))
            noises.push_back({b.GetID(),a.GetID(),toCentimeters(point),surface(a,manifold.mSubShapeID1),impulse,simulationStep,identity(b),
                              secondPushed});
          }
        }
      }
    if(!emitImpacts)
      return;
    const JPH::Body* item = nullptr;
    const JPH::Body* land = nullptr;
    JPH::SubShapeID landSubShape;
    if(a.GetObjectLayer()==DynamicWorld::C_Item && b.GetObjectLayer()==DynamicWorld::C_Landscape) {
      item = &a;
      land = &b;
      landSubShape = manifold.mSubShapeID2;
      }
    else if(b.GetObjectLayer()==DynamicWorld::C_Item && a.GetObjectLayer()==DynamicWorld::C_Landscape) {
      item = &b;
      land = &a;
      landSubShape = manifold.mSubShapeID1;
      }
    if(item==nullptr || manifold.mRelativeContactPointsOn1.empty())
      return;
    auto* obj = reinterpret_cast<DynamicBody*>(item->GetUserData());
    auto* terrain = reinterpret_cast<CollisionBody*>(land->GetUserData());
    if(obj==nullptr || obj->pointer==nullptr || obj->pointerType!=DynamicWorld::P_Item)
      return;
    JPH::CollisionEstimationResult response;
    JPH::EstimateCollisionResponse(a,b,manifold,response,settings.mCombinedFriction,settings.mCombinedRestitution);
    if(response.mContactImpulse.empty())
      return;
    const float impulse = response.mContactImpulse[0];
    if(impulse/obj->bodyMass<0.9f)
      return;
    auto material = zenkit::MaterialGroup::STONE;
    if(terrain!=nullptr && terrain->mesh!=nullptr)
      material = terrain->mesh->materialId(Physics::triangleData(*land->GetShape(),landSubShape));
    std::lock_guard<std::mutex> guard(impactMutex);
    impacts.push_back({item->GetID(),material,impulse,obj->bodyMass,simulationStep,land->GetID().GetIndexAndSequenceNumber(),landSubShape.GetValue()});
    }

  void OnContactAdded(const JPH::Body& a, const JPH::Body& b, const JPH::ContactManifold& manifold,
                      JPH::ContactSettings& settings) override {
    onContact(a,b,manifold,settings);
    }
  void OnContactPersisted(const JPH::Body& a, const JPH::Body& b, const JPH::ContactManifold& manifold,
                          JPH::ContactSettings& settings) override {
    onContact(a,b,manifold,settings);
    }

  LayerInterface layers;
  BroadPhaseFilter broadFilter;
  LayerPairFilter pairFilter;
  JPH::PhysicsSystem system;
  JPH::TempAllocatorImpl allocator;
  JPH::JobSystemThreadPool jobs;
  std::mutex impactMutex;
  std::vector<Impact> impacts;
  std::vector<NoiseImpact> noises;
  uint64_t simulationStep = 0;
  bool emitImpacts = false;
  bool noiseEnabled = true,emitNoise = false;
  std::atomic<uint64_t> rayCount {0};
  uint64_t lastRayCount = 0;
  float stepMilliseconds = 0;
  float characterMilliseconds = 0;
  std::vector<VirtualCharacter*> virtualCharacters;
  };

struct CollisionWorld::VirtualCharacter::Impl final : JPH::CharacterContactListener {
  explicit Impl(CollisionWorld& world):world(world) {}

  static bool isPushable(const JPH::Body& body) {
    const auto layer = body.GetObjectLayer();
    return body.IsDynamic() && (layer==DynamicWorld::C_Item || layer==DynamicWorld::C_MovableVob);
    }

  static bool blocksAutomaticStairs(const JPH::Body& body) {
    if(isPushable(body))
      return true;
    // C_Object bodies are CollisionBody wrappers, unlike native ragdoll parts.
    // Interactive furniture/containers are not architectural treads. Doors
    // retain their blocking/perception path and ladders use explicit climbing.
    if(body.GetObjectLayer()!=DynamicWorld::C_Object)
      return false;
    const auto* source = reinterpret_cast<const CollisionBody*>(body.GetUserData());
    return source!=nullptr && source->ptrType()==DynamicWorld::P_Interactive;
    }

  bool rejectsSupport(const JPH::Body& body, const JPH::CharacterContact& contact) const {
    if(!isPushable(body) || contact.mIsSensorB)
      return false;
    const auto up = character->GetUp();
    const float mass = 1.f/body.GetMotionProperties()->GetInverseMass();
    if(carrying && body.GetID()==ground && mass>=10.f)
      return false; // This top was accepted before the supporting body advanced.
    const float contactHeight = JPH::Vec3(contact.mPosition-character->GetPosition()).Dot(up);
    return mass<10.f ? contact.mSurfaceNormal.Dot(up)>1.e-3f :
                      contact.mSurfaceNormal.Dot(up)>.99f && contactHeight>.02f;
    }

  JPH::Vec3 initialIdleGravity(JPH::Vec3Arg gravity) const {
    // A tangent pose can have tiny negative distances from floating-point
    // contact calculations. Anything beyond one percent of the solver's
    // collision tolerance still needs the ordinary initial recovery solve.
    const float numericalPenetration = .01f*character->GetCharacterVirtualSettings().mCollisionTolerance;
    for(const auto& contact:character->GetActiveContacts())
      if(!contact.mWasDiscarded && !contact.mIsSensorB && contact.mDistance < -numericalPenetration)
        return gravity;

    const auto id = character->GetGroundBodyID();
    JPH::BodyLockRead lock(world.impl->system.GetBodyLockInterface(),id);
    if(!lock.SucceededAndIsInBroadPhase() || !lock.GetBody().IsStatic())
      return gravity;
    const auto& body = lock.GetBody();
    const auto subShape = character->GetGroundSubShapeID();
    JPH::SubShapeID remainder;
    const auto* leaf = body.GetShape()->GetLeafShape(subShape,remainder);
    if(leaf==nullptr || (leaf->GetSubType()!=JPH::EShapeSubType::Mesh && leaf->GetSubType()!=JPH::EShapeSubType::Box))
      return gravity;
    // Keep the full subshape ID here: compound and decorated shapes must
    // resolve their leaf's face in the body's world transform.
    const auto face = body.GetWorldSpaceSurfaceNormal(subShape,character->GetGroundPosition());
    constexpr float cosTwoDegrees = .999390827f;
    if(character->IsSlopeTooSteep(face) || face.Dot(character->GetGroundNormal())<cosTwoDegrees)
      return gravity;
    for(const auto& contact:character->GetActiveContacts()) {
      if(contact.mBodyB!=id || contact.mSubShapeIDB!=subShape || !contact.mHadCollision ||
         contact.mWasDiscarded || contact.mIsSensorB)
        continue;
      // A rounded toe on a tread edge is not an already acquired plane.
      if(face.Dot(contact.mContactNormal)>=cosTwoDegrees && face.Dot(contact.mSurfaceNormal)>=cosTwoDegrees)
        return face*gravity.Dot(face);
      }
    return gravity;
    }

  CharacterVirtualDiagnostics::Body diagnosticBody(JPH::BodyID id, JPH::RVec3Arg point) const {
    CharacterVirtualDiagnostics::Body result;
    result.id = id.GetIndexAndSequenceNumber();
    if(id.IsInvalid())
      return result;
    JPH::BodyLockRead lock(world.impl->system.GetBodyLockInterface(),id);
    if(!lock.SucceededAndIsInBroadPhase())
      return result;
    const auto& body = lock.GetBody();
    result.present = true;
    result.layer = body.GetObjectLayer();
    result.motionType = int(body.GetMotionType());
    result.nativeUserData = uintptr_t(body.GetUserData());
    result.mass = body.IsDynamic() ? 1.f/body.GetMotionProperties()->GetInverseMass() : 0.f;
    result.position = toCentimeters(JPH::Vec3(body.GetPosition()));
    result.linearVelocity = toCentimeters(body.GetLinearVelocity());
    const auto angular = body.GetAngularVelocity();
    result.angularVelocity = Tempest::Vec3(angular.GetX(),angular.GetY(),angular.GetZ());
    result.pointVelocity = toCentimeters(body.GetPointVelocity(point));
    // Ragdoll / living-body user data is not owned by CollisionBody. Resolve
    // metadata through this world's actual wrappers rather than casting it.
    const auto sourceIt = std::find_if(world.bodies.begin(),world.bodies.end(),
                                      [id](const auto* candidate) { return candidate->id==id; });
    if(sourceIt!=world.bodies.end()) {
      const auto* source = *sourceIt;
      result.pointerType = source->ptrType();
      result.payload = source->payload();
      result.label = source->label();
      }
    return result;
    }

  CharacterVirtualDiagnostics::Pose diagnosticPose() const {
    CharacterVirtualDiagnostics::Pose result;
    result.position = toCentimeters(JPH::Vec3(character->GetPosition()));
    result.velocity = toCentimeters(character->GetLinearVelocity());
    result.groundPosition = toCentimeters(JPH::Vec3(character->GetGroundPosition()));
    const auto normal = character->GetGroundNormal();
    result.groundNormal = Tempest::Vec3(normal.GetX(),normal.GetY(),normal.GetZ());
    result.groundVelocity = toCentimeters(character->GetGroundVelocity());
    result.ground = diagnosticBody(character->GetGroundBodyID(),character->GetGroundPosition());
    result.groundSubShape = character->GetGroundSubShapeID().GetValue();
    result.groundUserData = uintptr_t(character->GetGroundUserData());
    result.groundState = uint8_t(character->GetGroundState());
    const auto inverse = JPH::RMat44::sInverseRotationTranslation(character->GetRotation(),character->GetPosition());
    result.contacts.reserve(character->GetActiveContacts().size());
    for(const auto& contact:character->GetActiveContacts()) {
      CharacterVirtualDiagnostics::Contact out;
      out.body = diagnosticBody(contact.mBodyB,contact.mPosition);
      out.subShape = contact.mSubShapeIDB.GetValue();
      out.contactUserData = uintptr_t(contact.mUserData);
      out.position = toCentimeters(JPH::Vec3(contact.mPosition));
      out.contactNormal = Tempest::Vec3(contact.mContactNormal.GetX(),contact.mContactNormal.GetY(),contact.mContactNormal.GetZ());
      out.surfaceNormal = Tempest::Vec3(contact.mSurfaceNormal.GetX(),contact.mSurfaceNormal.GetY(),contact.mSurfaceNormal.GetZ());
      out.pointVelocity = toCentimeters(contact.mLinearVelocity);
      out.distance = contact.mDistance*100.f;
      const float supportingDistance = character->GetSupportingVolume().SignedDistance(JPH::Vec3(inverse*contact.mPosition));
      out.supportingVolumeDistance = supportingDistance*100.f;
      out.hadCollision = contact.mHadCollision;
      out.discarded = contact.mWasDiscarded;
      out.sensor = contact.mIsSensorB;
      out.canPushCharacter = contact.mCanPushCharacter;
      out.withinSupportingVolume = supportingDistance<=0.f;
      out.walkableSlope = !character->IsSlopeTooSteep(contact.mSurfaceNormal);
      out.supportAllowedByPolicy = true;
      if(out.body.present) {
        JPH::BodyLockRead lock(world.impl->system.GetBodyLockInterface(),contact.mBodyB);
        if(lock.SucceededAndIsInBroadPhase())
          out.supportAllowedByPolicy = !rejectsSupport(lock.GetBody(),contact);
        }
      out.supportEligible = out.hadCollision && !out.discarded && !out.sensor &&
                            out.withinSupportingVolume && out.walkableSlope && out.supportAllowedByPolicy;
      result.contacts.push_back(std::move(out));
      }
    return result;
    }

  bool OnContactValidate(const JPH::CharacterVirtual*, const JPH::CharacterContact& contact) override {
    bool rejectSupport = false;
    {
      JPH::BodyLockRead lock(world.impl->system.GetBodyLockInterface(),contact.mBodyB);
      if(!lock.SucceededAndIsInBroadPhase() || !isPushable(lock.GetBody()) || contact.mIsSensorB)
        return true;
      // Loose utensils never become a floor. A heavy body's top may support a
      // character placed on it, but walking into it must not acquire that top
      // as a new step. Keep the side contacts, which still stop and push it.
      rejectSupport = rejectsSupport(lock.GetBody(),contact);
      }
    if(!rejectSupport)
      return true;

    // Rejecting a support in Jolt also removes its solver impulse. Retain the
    // real contact point and horizontal push, within the same per-move budget.
    // The capsule's rounded toe must slide through a mug rather than climb it.
    // Sweeps also validate distant future contacts, so only push a point that
    // is already touching the current capsule (including its padding).
    auto local = JPH::Vec3(contact.mPosition-character->GetPosition());
    auto closest = character->GetUp()*std::clamp(local.Dot(character->GetUp()),radius,height-radius);
    if((local-closest).Length()<=radius+character->GetCharacterPadding()+.001f) {
      auto horizontal = contact.mContactNormal-contact.mContactNormal.Dot(character->GetUp())*character->GetUp();
      if(horizontal.IsNearZero()) {
        const auto velocity = character->GetLinearVelocity();
        horizontal = -(velocity-velocity.Dot(character->GetUp())*character->GetUp());
        }
      if(!horizontal.IsNearZero()) {
        auto pushContact = contact;
        pushContact.mContactNormal = horizontal.Normalized();
        pushContact.mDistance = std::max(contact.mDistance,0.f);
        pushContact.mHadCollision = true;
        JPH::CharacterContactSettings settings;
        onContact(pushContact,settings);
        }
      }
    return false;
    }

  void OnContactSolve(const JPH::CharacterVirtual*, const JPH::BodyID& bodyID, const JPH::SubShapeID&,
                      JPH::RVec3Arg point, JPH::Vec3Arg normal, JPH::Vec3Arg, const JPH::PhysicsMaterial*,
                      JPH::Vec3Arg velocity, JPH::Vec3& newVelocity) override {
    JPH::BodyLockRead lock(world.impl->system.GetBodyLockInterface(),bodyID);
    if(!lock.SucceededAndIsInBroadPhase() || !isPushable(lock.GetBody()) ||
       (carrying && bodyID==ground && !character->IsSlopeTooSteep(normal)))
      return;
    const auto up = character->GetUp();
    if(JPH::Vec3(point-character->GetPosition()).Dot(up)<=.02f)
      return; // Existing top support, including a moving body.
    // The rounded capsule can turn even a vertical furniture side into an
    // upward velocity. Side contacts still block us, but cannot lift the feet.
    const float gained = (newVelocity-velocity).Dot(up);
    if(gained>0.f)
      newVelocity -= gained*up;
    }

  void onContact(const JPH::CharacterContact& contact, JPH::CharacterContactSettings& settings) {
    // Jolt's native limit applies afresh to each contact / stair probe. Dose
    // the impulse here instead, sharing one force budget across the whole move.
    settings.mCanReceiveImpulses = false;
    JPH::BodyLockWrite lock(world.impl->system.GetBodyLockInterface(),contact.mBodyB);
    if(!lock.SucceededAndIsInBroadPhase())
      return;
    const auto& nativeBody = lock.GetBody();
    const auto layer = nativeBody.GetObjectLayer();
    if((layer!=DynamicWorld::C_Item && layer!=DynamicWorld::C_MovableVob) || !nativeBody.IsDynamic() || contact.mIsSensorB)
      return;
    const float inverseMass = nativeBody.GetMotionProperties()->GetInverseMass();
    if(inverseMass<=0.f)
      return;
    const float bodyMass = 1.f/inverseMass;
    // A rotating utensil's side-point velocity can enter the combined floor
    // constraint and lift the feet even with a perfectly horizontal normal.
    // Keep it as a blocking, pushable object without letting it move the walker.
    // Heavy supports retain their native velocity and continue to carry feet.
    if(bodyMass<10.f)
      settings.mCanPushCharacter = false;
    auto* body = reinterpret_cast<CollisionBody*>(contact.mUserData);
    if(body==nullptr)
      return;
    // Attribute an approaching contact, including the character's weight on a
    // simulated support, rather than a separating or predictive-only contact.
    const auto relative = character->GetLinearVelocity()-contact.mLinearVelocity;
    const float projectedVelocity = relative.Dot(contact.mContactNormal);
    if(projectedVelocity<-0.01f) {
      body->npcContact.store(true,std::memory_order_relaxed);
      body->setTraceCulprit(culprit);
      }
    if(!contact.mHadCollision || pushSeconds<=0.f || pushRemaining<=0.f)
      return;

    // Use the native effective contact mass, including the torque caused by an
    // off-centre push. Penetration recovery has the same damping as Jolt.
    const float deltaVelocity = -projectedVelocity*0.9f-std::min(contact.mDistance,0.f)*0.4f/pushSeconds;
    if(deltaVelocity<=0.f)
      return;
    const auto jacobian = JPH::Vec3(contact.mPosition-nativeBody.GetCenterOfMassPosition()).Cross(contact.mContactNormal);
    const float inverseEffectiveMass = nativeBody.GetInverseInertia().Multiply3x3(jacobian).Dot(jacobian)+inverseMass;
    auto impulse = -(deltaVelocity/inverseEffectiveMass)*contact.mContactNormal;
    // Downward weight is applied independently by CharacterVirtual::Update;
    // do not spend the walking push budget on that supporting contact.
    const float down = impulse.Dot(character->GetUp());
    if(down<0.f)
      impulse -= down*character->GetUp();
    const float magnitude = impulse.Length();
    if(magnitude<=0.f)
      return;

    auto used = std::find_if(pushBodies.begin(),pushBodies.end(),[&](const auto& entry) {return entry.first==contact.mBodyB;});
    if(used==pushBodies.end()) {
      pushBodies.emplace_back(contact.mBodyB,0.f);
      used = std::prev(pushBodies.end());
      }
    // Ordinary walking has less effective effort against a heavier load than
    // a braced push. Keep this continuous in both masses; no furniture classes.
    const float force = character->GetMaxStrength()*character->GetMass()/(character->GetMass()+bodyMass);
    const float available = std::min(pushRemaining,std::max(0.f,force*pushSeconds-used->second));
    const float amount = std::min(magnitude,available);
    if(amount<=0.f)
      return;
    pushRemaining -= amount;
    used->second += amount;
    // The write lock is already held. AddImpulse activates a sleeping body and
    // applies the torque at the real contact point, in native SI units.
    world.impl->system.GetBodyInterfaceNoLock().AddImpulse(contact.mBodyB,impulse*(amount/magnitude),contact.mPosition);
    }

  void OnContactAdded(const JPH::CharacterVirtual*, const JPH::CharacterContact& contact,
                      JPH::CharacterContactSettings& settings) override { onContact(contact,settings); }
  void OnContactPersisted(const JPH::CharacterVirtual*, const JPH::CharacterContact& contact,
                          JPH::CharacterContactSettings& settings) override { onContact(contact,settings); }

  void OnAdjustBodyVelocity(const JPH::CharacterVirtual*, const JPH::Body&, JPH::Vec3& linear,
                            JPH::Vec3& angular) override {
    // Carry sweeps through the already integrated world. Predicting the bodies
    // for another interval here would count their motion a second time.
    if(carrying)
      linear = angular = JPH::Vec3::sZero();
    }

  CollisionWorld& world;
  std::unique_ptr<JPH::CharacterVirtual> character;
  std::unique_ptr<CharacterVirtualDiagnostics> diagnostic;
  Npc* culprit = nullptr;
  float pushSeconds = 0.f;
  float pushRemaining = 0.f;
  std::vector<std::pair<JPH::BodyID,float>> pushBodies;
  bool contactsDirty = true;
  bool needsInitialUpdate = true;
  bool carrying = false;
  float radius = 0.f;
  float height = 0.f;
  JPH::BodyID ground;
  JPH::RMat44 groundTransform = JPH::RMat44::sIdentity();
  };

CollisionWorld::VirtualCharacter::VirtualCharacter(CollisionWorld& owner, float radius, float height,
                                                  const Tempest::Vec3& feet)
  :impl(std::make_unique<Impl>(owner)) {
  const float r = std::max(toMeters(radius),0.01f);
  const float h = std::max(toMeters(height),2.f*r);
  impl->radius = r;
  impl->height = h;
  JPH::CharacterVirtualSettings settings;
  settings.mShape = new JPH::CapsuleShape(std::max(h*0.5f-r,0.001f),r);
  settings.mShapeOffset = JPH::Vec3(0,h*0.5f,0);
  settings.mSupportingVolume = JPH::Plane(JPH::Vec3::sAxisY(),-r);
  settings.mCharacterPadding = 0.01f;
  settings.mPredictiveContactDistance = 0.05f;
  settings.mCollisionTolerance = 0.001f;
  settings.mEnhancedInternalEdgeRemoval = true;
  impl->character = std::make_unique<JPH::CharacterVirtual>(&settings,JPH::RVec3(toMeters(feet)),
                                                          JPH::Quat::sIdentity(),&owner.impl->system);
  impl->character->SetListener(impl.get());
  owner.impl->virtualCharacters.push_back(this);
  }

CollisionWorld::VirtualCharacter::~VirtualCharacter() {
  auto& characters = impl->world.impl->virtualCharacters;
  characters.erase(std::remove(characters.begin(),characters.end(),this),characters.end());
  }

std::unique_ptr<CollisionWorld::VirtualCharacter> CollisionWorld::createVirtualCharacter(
    float radius, float height, const Tempest::Vec3& feet) {
  return std::unique_ptr<VirtualCharacter>(new VirtualCharacter(*this,radius,height,feet));
  }

void CollisionWorld::VirtualCharacter::setPosition(const Tempest::Vec3& feet) {
  impl->character->SetPosition(JPH::RVec3(toMeters(feet)));
  impl->contactsDirty = true;
  impl->needsInitialUpdate = true;
  impl->ground = JPH::BodyID();
  }

void CollisionWorld::VirtualCharacter::setDiagnosticsEnabled(bool enabled) {
  if(enabled && impl->diagnostic==nullptr)
    impl->diagnostic = std::make_unique<CharacterVirtualDiagnostics>();
  if(!enabled)
    impl->diagnostic.reset();
  }

bool CollisionWorld::VirtualCharacter::diagnostics(CharacterVirtualDiagnostics& out) const {
  if(impl->diagnostic==nullptr || !impl->diagnostic->valid)
    return false;
  out = *impl->diagnostic;
  return true;
  }

CollisionWorld::CharacterMoveResult CollisionWorld::VirtualCharacter::move(
    const Tempest::Vec3& displacement, uint64_t dt, float stepHeight, float maxSlope, float mass, Npc* culprit) {
  const auto started = std::chrono::steady_clock::now();
  auto& state = *impl;
  auto& character = *state.character;
  auto& world = *state.world.impl;
  const QueryBroadPhaseFilter broadFilter(characterMask);
  const QueryLayerFilter layerFilter(characterMask);
  const JPH::BodyFilter bodyFilter;
  const JPH::ShapeFilter shapeFilter;
  state.culprit = culprit;
  character.SetMass(std::max(mass,1.f));
  // Jolt uses metres, kilograms and seconds: this is a 450 N human push,
  // capped independently of the character's weight on a supporting body.
  character.SetMaxStrength(std::min(character.GetMass()*DynamicWorld::gravityMS,450.f));
  const float seconds = float(dt)*0.001f;
  state.pushSeconds = seconds;
  state.pushRemaining = character.GetMaxStrength()*seconds;
  state.pushBodies.clear();
  character.SetMaxSlopeAngle(maxSlope);
  if(state.contactsDirty) {
    character.RefreshContacts(broadFilter,layerFilter,bodyFilter,shapeFilter,world.allocator);
    state.contactsDirty = false;
    }
  // A zero-dt refresh after NPC clipping must not replace the main move's trace.
  auto* diagnostic = state.diagnostic!=nullptr && (dt>0 || !state.diagnostic->valid) ? state.diagnostic.get() : nullptr;
  if(diagnostic!=nullptr) {
    diagnostic->valid = false;
    diagnostic->dt = dt;
    diagnostic->requested = displacement;
    diagnostic->requestedVelocity = {};
    diagnostic->stairDesiredVelocity = {};
    diagnostic->maxSlopeRadians = maxSlope;
    diagnostic->stairHeight = stepHeight;
    diagnostic->stairDesiredDistance = 0.f;
    diagnostic->stairAchievedDistance = 0.f;
    diagnostic->stairSupported = false;
    diagnostic->stairCanWalk = false;
    diagnostic->stairBlockedByPolicy = false;
    diagnostic->stairProgressBlocked = false;
    diagnostic->stairProbe = {};
    diagnostic->stairAttempted = false;
    diagnostic->stairSucceeded = false;
    diagnostic->stairObstacleId = uint32_t(-1);
    diagnostic->before = state.diagnosticPose();
    }
  auto inheritedGroundVelocity = character.GetGroundVelocity();
  bool inferredGroundVelocity = false;
  if(seconds>0.f) {
    bool onGround = character.GetGroundState()==JPH::CharacterBase::EGroundState::OnGround;
    auto carriedVelocity = JPH::Vec3::sZero();
    if(character.IsSupported() && state.ground==character.GetGroundBodyID() && !state.ground.IsInvalid()) {
      JPH::BodyLockRead lock(world.system.GetBodyLockInterface(),state.ground);
      if(!lock.SucceededAndIsInBroadPhase())
        onGround = false;
      else if(!lock.GetBody().IsStatic()) {
        // Both direct-matrix movers and fixed-step dynamic bodies have already
        // advanced. Carry the observed displacement at the feet, including the
        // finite rotation, rather than extrapolating their endpoint velocity
        // over a render interval that may contain a different number of steps.
        const auto transform = lock.GetBody().GetWorldTransform();
        const auto position = character.GetPosition();
        carriedVelocity = JPH::Vec3(transform*(state.groundTransform.InversedRotationTranslation()*position)-position)/seconds;
        inheritedGroundVelocity = carriedVelocity;
        inferredGroundVelocity = true;
        }
      }
    character.UpdateGroundVelocity();
    auto groundVelocity = character.GetGroundVelocity();
    if(inferredGroundVelocity) {
      groundVelocity = carriedVelocity;
      // The floor has already advanced vertically. Resolve its new height
      // through penetration recovery / floor sticking; adding its upward
      // speed again would detach the feet from a direct-matrix lift.
      groundVelocity.SetY(0.f);
      }
    auto velocity = toMeters(displacement)/seconds;
    if(onGround)
      velocity += groundVelocity;
    auto gravityStep = JPH::Vec3(0,-DynamicWorld::gravityMS*seconds,0);
    if(displacement==Tempest::Vec3() && onGround) {
      // A supported idle character has static friction. Feeding vertical
      // gravity to Jolt's frictionless contact solver adds a downhill tangent
      // every frame, even on a walkable slope. Keep only pressure into the
      // support; moving platforms still carry us through groundVelocity and
      // Update applies the character's weight independently. Steep slopes and
      // requested walking/jumping retain their ordinary gravity response.
      if(state.needsInitialUpdate)
        gravityStep = state.initialIdleGravity(gravityStep);
      else {
        const auto normal = character.GetGroundNormal();
        gravityStep = normal*gravityStep.Dot(normal);
        }
      }
    velocity += gravityStep;
    if(diagnostic!=nullptr)
      diagnostic->requestedVelocity = toCentimeters(velocity);
    // ExtendedUpdate cancels steep-slope motion internally, while preserving
    // this original request for the separate world-stair recipe below.
    character.SetLinearVelocity(velocity);
    JPH::CharacterVirtual::ExtendedUpdateSettings settings;
    const float step = std::max(toMeters(stepHeight),0.f);
    // A steep, slanted riser can be longer than Jolt's fixed 15 cm probe.
    // Search its possible horizontal run, bounded by the permitted step's
    // height and slope, plus the toe. This changes only the support test;
    // accepted forward travel still uses the original per-frame request.
    const float riserRun = maxSlope>0.f ? std::min(step,step/std::tan(maxSlope)) : step;
    settings.mWalkStairsStepForwardTest = std::max(settings.mWalkStairsStepForwardTest,
                                                  riserRun+state.radius+character.GetCharacterPadding());
    // Run Jolt's ordinary update and floor sticking first. Its automatic stair
    // recipe is applied below only for world geometry, after contact solving
    // has identified pushable objects or interactive walking obstacles.
    settings.mWalkStairsStepUp = JPH::Vec3::sZero();
    settings.mStickToFloorStepDown = JPH::Vec3(0,-step,0);
    const auto oldPosition = character.GetPosition();
    const auto desiredVelocity = character.GetLinearVelocity();
    if(diagnostic!=nullptr)
      diagnostic->stairDesiredVelocity = toCentimeters(desiredVelocity);
    character.ExtendedUpdate(seconds,JPH::Vec3(0,-DynamicWorld::gravityMS,0),settings,
                             broadFilter,layerFilter,bodyFilter,shapeFilter,world.allocator);
    // RefreshContacts only classifies the initial overlap. Let the first
    // physical solve after creation/teleport recover penetration with native
    // gravity. Only a verified nonpenetrating static plane can start at rest;
    // edges retain the native contact pose used to enter stairs.
    // A zero-dt refresh does not complete this acquisition.
    state.needsInitialUpdate = false;
    if(diagnostic!=nullptr)
      diagnostic->afterExtendedUpdate = state.diagnosticPose();
    bool stairObstacle = false;
    for(const auto& contact:character.GetActiveContacts()) {
      if(!contact.mHadCollision || contact.mWasDiscarded || contact.mIsSensorB ||
         contact.mSurfaceNormal.Dot(desiredVelocity-contact.mLinearVelocity)>=0.f)
        continue;
      JPH::BodyLockRead lock(world.system.GetBodyLockInterface(),contact.mBodyB);
      if(lock.SucceededAndIsInBroadPhase() && Impl::blocksAutomaticStairs(lock.GetBody())) {
        stairObstacle = true;
        if(diagnostic!=nullptr)
          diagnostic->stairObstacleId = contact.mBodyB.GetIndexAndSequenceNumber();
        break;
        }
      }
    // The remainder is the WalkStairs part of Jolt's ExtendedUpdate recipe,
    // including its direction and minimum-distance tests for oblique steps.
    const auto up = character.GetUp();
    // Static friction has a horizontal component on a slope; it is a contact
    // force, not a walking request. Keep support motion in the stair intent.
    const auto stairVelocity = desiredVelocity-gravityStep;
    const auto desiredStep = (stairVelocity-stairVelocity.Dot(up)*up)*seconds;
    const float desiredLength = desiredStep.Length();
    if(diagnostic!=nullptr) {
      const auto achieved = JPH::Vec3(character.GetPosition()-oldPosition);
      const float achievedLength = desiredLength>0.f ? std::max(0.f,achieved.Dot(desiredStep/desiredLength)) : 0.f;
      diagnostic->stairDesiredDistance = desiredLength*100.f;
      diagnostic->stairAchievedDistance = achievedLength*100.f;
      diagnostic->stairSupported = character.IsSupported();
      diagnostic->stairCanWalk = character.CanWalkStairs(desiredVelocity);
      diagnostic->stairBlockedByPolicy = stairObstacle;
      diagnostic->stairProgressBlocked = achievedLength+1.e-4f<desiredLength;
      }
    if(step>0.f && !stairObstacle && desiredLength>0.f && character.CanWalkStairs(desiredVelocity)) {
      const auto forward = desiredStep/desiredLength;
      const auto achieved = JPH::Vec3(character.GetPosition()-oldPosition);
      const float achievedLength = std::max(0.f,achieved.Dot(forward));
      if(achievedLength+1.e-4f<desiredLength) {
        auto direction = forward;
        float best = settings.mWalkStairsCosAngleForwardContact;
        for(const auto& contact:character.GetActiveContacts()) {
          if(!contact.mHadCollision || contact.mWasDiscarded ||
             contact.mSurfaceNormal.Dot(desiredVelocity-contact.mLinearVelocity)>=0.f ||
             !character.IsSlopeTooSteep(contact.mSurfaceNormal))
            continue;
          const auto candidate = contact.mSurfaceNormal.Dot(up)*up-contact.mSurfaceNormal;
          const float length = candidate.Length();
          if(length>1.e-6f && candidate.Dot(forward)/length>best) {
            direction = candidate/length;
            best = direction.Dot(forward);
            }
          }
        const auto forwardStep = direction*std::max(settings.mWalkStairsMinStepForward,desiredLength-achievedLength);
        JPH::CharacterVirtual::WalkStairsDiagnostics stairProbe;
        const bool stepped = character.WalkStairs(seconds,up*step,forwardStep,direction*settings.mWalkStairsStepForwardTest,
                                                settings.mWalkStairsStepDownExtra,broadFilter,layerFilter,bodyFilter,shapeFilter,world.allocator,
                                                diagnostic!=nullptr ? &stairProbe : nullptr);
        if(diagnostic!=nullptr) {
          diagnostic->stairAttempted = true;
          diagnostic->stairSucceeded = stepped;
          static constexpr const char* reasons[]={"notAttempted","upBlocked","noSteep","noHorizontal","noProgress","noFloor",
                                                   "noForwardTest","testNoProgress","testNoFloor","testSteep","noHeight","success"};
          auto& probe=diagnostic->stairProbe;
          probe.reason = reasons[unsigned(stairProbe.mResult)];
          probe.upPosition = toCentimeters(JPH::Vec3(stairProbe.mUpPosition));
          probe.forwardPosition = toCentimeters(JPH::Vec3(stairProbe.mForwardPosition));
          probe.finalPosition = toCentimeters(JPH::Vec3(stairProbe.mFinalPosition));
          probe.testPosition = toCentimeters(JPH::Vec3(stairProbe.mTestPosition));
          probe.testFinalPosition = toCentimeters(JPH::Vec3(stairProbe.mTestFinalPosition));
          const auto normal=stairProbe.mLandingNormal, testNormal=stairProbe.mTestLandingNormal;
          probe.landingNormal = Tempest::Vec3(normal.GetX(),normal.GetY(),normal.GetZ());
          probe.testLandingNormal = Tempest::Vec3(testNormal.GetX(),testNormal.GetY(),testNormal.GetZ());
          probe.upFraction = stairProbe.mUpFraction;
          probe.landingFraction = stairProbe.mLandingFraction;
          probe.testLandingFraction = stairProbe.mTestLandingFraction;
          }
        }
      }
    }
  if(diagnostic!=nullptr) {
    if(seconds<=0.f)
      diagnostic->afterExtendedUpdate = state.diagnosticPose();
    diagnostic->afterStairs = state.diagnosticPose();
    diagnostic->valid = true;
    }
  const auto previousGround = state.ground;
  state.ground = character.GetGroundBodyID();
  if(!state.ground.IsInvalid()) {
    JPH::BodyLockRead lock(world.system.GetBodyLockInterface(),state.ground);
    if(lock.Succeeded())
      state.groundTransform = lock.GetBody().GetWorldTransform();
    else
      state.ground = JPH::BodyID();
    }
  CharacterMoveResult result;
  result.position = toCentimeters(JPH::Vec3(character.GetPosition()));
  // Jolt keeps its requested velocity after moving. Resolve the carried
  // velocity against actual contacts so downhill acceleration survives, while
  // stair snaps and penetration recovery never turn into airborne momentum.
  auto solvedVelocity = character.GetLinearVelocity();
  for(int iteration=0;iteration<2;++iteration) {
    for(const auto& contact:character.GetActiveContacts()) {
      if(!contact.mHadCollision || contact.mWasDiscarded || contact.mIsSensorB)
        continue;
      const float into = (solvedVelocity-contact.mLinearVelocity).Dot(contact.mContactNormal);
      if(into<0.f)
        solvedVelocity -= contact.mContactNormal*into;
      }
    }
  result.velocity = toCentimeters(solvedVelocity);
  result.groundVelocity = toCentimeters(inferredGroundVelocity && previousGround==state.ground ?
                                        inheritedGroundVelocity : character.GetGroundVelocity());
  const auto normal = character.GetGroundNormal();
  result.groundNormal = Tempest::Vec3(normal.GetX(),normal.GetY(),normal.GetZ());
  result.grounded = character.GetGroundState()==JPH::CharacterBase::EGroundState::OnGround;
  result.steep = character.GetGroundState()==JPH::CharacterBase::EGroundState::OnSteepGround;
  world.characterMilliseconds += std::chrono::duration<float,std::milli>(std::chrono::steady_clock::now()-started).count();
  return result;
  }

CollisionWorld::CharacterMoveResult CollisionWorld::VirtualCharacter::carry(uint64_t dt) {
  auto& state = *impl;
  auto& character = *state.character;
  auto& world = *state.world.impl;
  CharacterMoveResult result;
  result.position = toCentimeters(JPH::Vec3(character.GetPosition()));
  if(dt==0 || character.GetGroundState()!=JPH::CharacterBase::EGroundState::OnGround ||
     state.ground.IsInvalid() || state.ground!=character.GetGroundBodyID())
    return result;
  JPH::RMat44 transform;
  {
    JPH::BodyLockRead lock(world.system.GetBodyLockInterface(),state.ground);
    if(!lock.SucceededAndIsInBroadPhase() || lock.GetBody().IsStatic())
      return result;
    transform = lock.GetBody().GetWorldTransform();
  }
  const auto position = character.GetPosition();
  const auto delta = JPH::Vec3(transform*(state.groundTransform.InversedRotationTranslation()*position)-position);
  state.groundTransform = transform;
  if(delta.IsNearZero(1.e-7f))
    return result;
  const auto started = std::chrono::steady_clock::now();
  const QueryBroadPhaseFilter broadFilter(characterMask);
  const QueryLayerFilter layerFilter(characterMask);
  const JPH::BodyFilter bodyFilter;
  const JPH::ShapeFilter shapeFilter;
  const float seconds = float(dt)*.001f;
  const auto velocity = character.GetLinearVelocity();
  state.pushSeconds = state.pushRemaining = 0.f;
  state.carrying = true;
  // This is a collision sweep of the support's observed motion, never a pose
  // assignment. No second root input, gravity impulse, push budget or stairs.
  character.SetLinearVelocity(delta/seconds);
  character.Update(seconds,JPH::Vec3::sZero(),broadFilter,layerFilter,bodyFilter,shapeFilter,world.allocator);
  // A rising support can separate relative to the frozen body's zero velocity.
  // Re-evaluate support at the swept endpoint without that velocity test.
  character.SetLinearVelocity(JPH::Vec3::sZero());
  character.RefreshContacts(broadFilter,layerFilter,bodyFilter,shapeFilter,world.allocator);
  state.carrying = false;
  character.SetLinearVelocity(velocity);
  character.UpdateGroundVelocity();
  state.ground = character.GetGroundBodyID();
  if(!state.ground.IsInvalid()) {
    JPH::BodyLockRead lock(world.system.GetBodyLockInterface(),state.ground);
    if(lock.SucceededAndIsInBroadPhase())
      state.groundTransform = lock.GetBody().GetWorldTransform();
    else
      state.ground = JPH::BodyID();
  }
  result.position = toCentimeters(JPH::Vec3(character.GetPosition()));
  result.velocity = toCentimeters(velocity);
  result.groundVelocity = toCentimeters(delta/seconds);
  const auto normal = character.GetGroundNormal();
  result.groundNormal = Tempest::Vec3(normal.GetX(),normal.GetY(),normal.GetZ());
  result.grounded = character.GetGroundState()==JPH::CharacterBase::EGroundState::OnGround;
  result.steep = character.GetGroundState()==JPH::CharacterBase::EGroundState::OnSteepGround;
  result.supportMoved = true;
  world.characterMilliseconds += std::chrono::duration<float,std::milli>(std::chrono::steady_clock::now()-started).count();
  return result;
  }

CollisionWorld::CollisionWorld() {
  Physics::initialize();
  impl = std::make_unique<Impl>();
  }

CollisionWorld::~CollisionWorld() {
  while(!ragdolls.empty())
    ragdolls.back()->detach();
  while(!bodies.empty()) {
    auto* body = bodies.back();
    removeBody(*body);
    body->owner = nullptr;
    }
  }

float CollisionWorld::toMeters(float v) {
  return v*0.01f;
  }

JPH::Vec3 CollisionWorld::toMeters(const Tempest::Vec3& v) {
  return JPH::Vec3(v.x*0.01f,v.y*0.01f,v.z*0.01f);
  }

Tempest::Vec3 CollisionWorld::toCentimeters(JPH::Vec3Arg v) {
  return Tempest::Vec3(v.GetX()*100.f,v.GetY()*100.f,v.GetZ()*100.f);
  }

CollisionWorld::CollisionBody::CollisionBody(CollisionWorld& owner, JPH::ShapeRefC shape, int category)
  :owner(&owner), baseShape(std::move(shape)), scaledShape(baseShape), categoryId(category) {
  }

CollisionWorld::CollisionBody::~CollisionBody() {
  if(owner!=nullptr)
    owner->removeBody(*this);
  }

CollisionWorld::DynamicBody::DynamicBody(CollisionWorld& owner, JPH::ShapeRefC shape, float mass, int category)
  :CollisionBody(owner,std::move(shape),category), bodyMass(mass) {
  }

void CollisionWorld::CollisionBody::setObjMatrix(const Tempest::Matrix4x4& matrix) {
  if(owner==nullptr || id.IsInvalid())
    return;
  auto transform = toTransform(matrix);
  if((scale-transform.scale).LengthSq()>1.0e-10f) {
    scale = transform.scale;
    scaledShape = ::scaledShape(baseShape.GetPtr(),scale);
    owner->impl->system.GetBodyInterface().SetShape(id,scaledShape.GetPtr(),false,JPH::EActivation::DontActivate);
    }
  owner->impl->system.GetBodyInterface().SetPositionAndRotationWhenChanged(id,transform.position,transform.rotation,JPH::EActivation::DontActivate);
  }

void CollisionWorld::CollisionBody::restoreTransform(const Tempest::Matrix4x4& matrix, bool sleeping) {
  setObjMatrix(matrix);
  auto& bodies = owner->impl->system.GetBodyInterface();
  bodies.SetLinearAndAngularVelocity(id,JPH::Vec3::sZero(),JPH::Vec3::sZero());
  if(sleeping)
    bodies.DeactivateBody(id);
  else
    bodies.ActivateBody(id);
  }

void CollisionWorld::CollisionBody::setPosition(const Tempest::Vec3& position) {
  owner->impl->system.GetBodyInterface().SetPosition(id,toMeters(position),JPH::EActivation::DontActivate);
  }

void CollisionWorld::CollisionBody::setScale(const Tempest::Vec3& value) {
  const auto next = JPH::Vec3(value.x,value.y,value.z);
  if(scale==next)
    return;
  auto result = ::scaledShape(baseShape.GetPtr(),next);
  scale = next;
  scaledShape = std::move(result);
  owner->impl->system.GetBodyInterface().SetShape(id,scaledShape.GetPtr(),false,JPH::EActivation::DontActivate);
  }

void CollisionWorld::CollisionBody::setShape(JPH::ShapeRefC shape) {
  auto result = ::scaledShape(shape.GetPtr(),scale);
  baseShape = std::move(shape);
  scaledShape = std::move(result);
  owner->impl->system.GetBodyInterface().SetShape(id,scaledShape.GetPtr(),false,JPH::EActivation::DontActivate);
  }

void CollisionWorld::CollisionBody::setPayload(void* payload, int type) {
  pointer = payload;
  pointerType = type;
  }

void CollisionWorld::CollisionBody::setNoiseProfile(ItemMaterial material, zenkit::MaterialGroup surface, std::string_view what) {
  noiseMaterial=material;
  noiseSurface=surface;
  noiseWhat=what;
  }

Tempest::Vec3 CollisionWorld::CollisionBody::position() const {
  return toCentimeters(owner->impl->system.GetBodyInterface().GetPosition(id));
  }

Tempest::Vec3 CollisionWorld::CollisionBody::centerOfMass() const {
  return toCentimeters(owner->impl->system.GetBodyInterface().GetCenterOfMassPosition(id));
  }

Tempest::Matrix4x4 CollisionWorld::CollisionBody::matrix() const {
  auto transform = owner->impl->system.GetBodyInterface().GetWorldTransform(id);
  Tempest::Matrix4x4 result;
  float m[16];
  for(JPH::uint c=0;c<4;++c) {
    auto column = transform.GetColumn4(c);
    const float factor = c<3 ? scale[c] : 1.f;
    m[c*4+0] = column.GetX()*factor;
    m[c*4+1] = column.GetY()*factor;
    m[c*4+2] = column.GetZ()*factor;
    m[c*4+3] = column.GetW();
    }
  m[12] *= 100.f;
  m[13] *= 100.f;
  m[14] *= 100.f;
  result.setData(m);
  return result;
  }

void CollisionWorld::CollisionBody::getAabb(JPH::Vec3& min, JPH::Vec3& max) const {
  JPH::BodyLockRead lock(owner->impl->system.GetBodyLockInterface(),id);
  if(!lock.Succeeded()) {
    min = max = JPH::Vec3::sZero();
    return;
    }
  const auto& box = lock.GetBody().GetWorldSpaceBounds();
  min = box.mMin;
  max = box.mMax;
  }

float CollisionWorld::CollisionBody::mass() const {
  if(owner==nullptr || id.IsInvalid()) return 0.f;
  JPH::BodyLockRead lock(owner->impl->system.GetBodyLockInterface(),id);
  if(!lock.Succeeded() || !lock.GetBody().IsDynamic()) return 0.f;
  return 1.f/lock.GetBody().GetMotionProperties()->GetInverseMass();
  }

bool CollisionWorld::CollisionBody::isMovable() const {
  if(owner==nullptr || id.IsInvalid()) return false;
  JPH::BodyLockRead lock(owner->impl->system.GetBodyLockInterface(),id);
  return lock.Succeeded() && !lock.GetBody().IsStatic();
  }

Tempest::Vec3 CollisionWorld::CollisionBody::velocity() const {
  if(owner==nullptr || id.IsInvalid()) return {};
  JPH::BodyLockRead lock(owner->impl->system.GetBodyLockInterface(),id);
  if(!lock.Succeeded() || !lock.GetBody().IsDynamic()) return {};
  return toCentimeters(lock.GetBody().GetLinearVelocity());
  }

Tempest::Vec3 CollisionWorld::CollisionBody::angularVelocity() const {
  if(owner==nullptr || id.IsInvalid()) return {};
  JPH::BodyLockRead lock(owner->impl->system.GetBodyLockInterface(),id);
  if(!lock.Succeeded() || !lock.GetBody().IsDynamic()) return {};
  const auto velocity=lock.GetBody().GetAngularVelocity();
  return {velocity.GetX(),velocity.GetY(),velocity.GetZ()};
  }

bool CollisionWorld::CollisionBody::isSleeping() const {
  if(owner==nullptr || id.IsInvalid()) return true;
  JPH::BodyLockRead lock(owner->impl->system.GetBodyLockInterface(),id);
  return !lock.Succeeded() || !lock.GetBody().IsDynamic() || !lock.GetBody().IsActive();
  }

void CollisionWorld::CollisionBody::setTraceCulprit(Npc* culprit) {
  traceCause.clear();
  if(culprit==nullptr)
    return;
  if(culprit->isPlayer())
    traceCause = "PLAYER";
  else if(auto symbol = culprit->world().script().findSymbol(size_t(culprit->instanceSymbol())))
    traceCause = symbol->name();
  }

bool CollisionWorld::CollisionBody::addImpulse(const Tempest::Vec3& impulseNs, Npc* culprit,
                                               const Tempest::Vec3* pointCm) {
  if(owner==nullptr || id.IsInvalid() || !std::isfinite(impulseNs.x) || !std::isfinite(impulseNs.y) ||
     !std::isfinite(impulseNs.z) || (impulseNs.x==0.f && impulseNs.y==0.f && impulseNs.z==0.f)) return false;
  if(pointCm!=nullptr && (!std::isfinite(pointCm->x) || !std::isfinite(pointCm->y) || !std::isfinite(pointCm->z)))
    return false;
  {
    JPH::BodyLockRead lock(owner->impl->system.GetBodyLockInterface(),id);
    if(!lock.Succeeded() || !lock.GetBody().IsDynamic()) return false;
    }
  // BodyInterface adds the impulse in SI units and activates a sleeping body.
  auto& interface=owner->impl->system.GetBodyInterface();
  if(pointCm!=nullptr)
    interface.AddImpulse(id,JPH::Vec3(impulseNs.x,impulseNs.y,impulseNs.z),JPH::RVec3(toMeters(*pointCm)));
  else
    interface.AddImpulse(id,JPH::Vec3(impulseNs.x,impulseNs.y,impulseNs.z));
  noiseCulprit=culprit!=nullptr ? culprit->handlePtr() : std::weak_ptr<zenkit::INpc>();
  noiseCulpritUntilStep=owner->impl->simulationStep+300;
  setTraceCulprit(culprit);
  return true;
  }

bool CollisionWorld::CollisionBody::addImpulseAtPoint(const Tempest::Vec3& impulseNs,
                                                      const Tempest::Vec3& pointCm, Npc* culprit) {
  return addImpulse(impulseNs,culprit,&pointCm);
  }

void CollisionWorld::addBody(CollisionBody& body, const Tempest::Matrix4x4& matrix, float friction,
                             bool movable, bool sensor, float mass, bool startSleeping) {
  const auto transform = toTransform(matrix);
  body.scale = transform.scale;
  body.scaledShape = ::scaledShape(body.baseShape.GetPtr(),body.scale);
  const auto type = mass>0 ? JPH::EMotionType::Dynamic : movable ? JPH::EMotionType::Kinematic : JPH::EMotionType::Static;
  JPH::BodyCreationSettings settings(body.scaledShape.GetPtr(),transform.position,transform.rotation,type,JPH::ObjectLayer(body.categoryId));
  settings.mFriction = friction;
  settings.mIsSensor = sensor;
  settings.mUserData = reinterpret_cast<JPH::uint64>(&body);
  if(type==JPH::EMotionType::Kinematic) {
    // Triangle meshes have no volume or inertia, but their animated bodies need motion properties.
    settings.mOverrideMassProperties = JPH::EOverrideMassProperties::MassAndInertiaProvided;
    settings.mMassPropertiesOverride.mMass = 1.f;
    settings.mMassPropertiesOverride.mInertia = JPH::Mat44::sIdentity();
    }
  if(type==JPH::EMotionType::Dynamic) {
    settings.mOverrideMassProperties = JPH::EOverrideMassProperties::CalculateInertia;
    settings.mMassPropertiesOverride.mMass = mass;
    settings.mMotionQuality = JPH::EMotionQuality::LinearCast;
    settings.mLinearDamping = 0.f;
    settings.mAngularDamping = 0.f;
    if(body.categoryId==DynamicWorld::C_MovableVob) {
      settings.mLinearDamping = 0.1f;
      settings.mAngularDamping = 0.2f;
      }
    }
  auto& interface = impl->system.GetBodyInterface();
  auto* native = interface.CreateBody(settings);
  if(native==nullptr)
    throw std::runtime_error("Jolt body capacity exhausted");
  body.id = native->GetID();
  interface.AddBody(body.id,type==JPH::EMotionType::Dynamic && !startSleeping ? JPH::EActivation::Activate : JPH::EActivation::DontActivate);
  bodies.push_back(&body);
  }

std::unique_ptr<CollisionWorld::CollisionBody> CollisionWorld::addCollisionBody(JPH::ShapeRefC shape, const Tempest::Matrix4x4& matrix,
                                                                               float friction, int category, bool movable, bool sensor,
                                                                               const PhysicVbo* mesh) {
  auto result = std::unique_ptr<CollisionBody>(new CollisionBody(*this,std::move(shape),category));
  result->mesh = mesh;
  addBody(*result,matrix,friction,movable,sensor,0.f);
  return result;
  }

std::unique_ptr<CollisionWorld::DynamicBody> CollisionWorld::addDynamicBody(JPH::ShapeRefC shape, const Tempest::Matrix4x4& matrix,
                                                                           float friction, float mass, int category, bool startSleeping) {
  mass = std::isfinite(mass) && mass>0.f ? mass : 1.f;
  auto result = std::unique_ptr<DynamicBody>(new DynamicBody(*this,std::move(shape),mass,category));
  addBody(*result,matrix,friction,false,false,mass,startSleeping);
  rigid.push_back(result.get());
  return result;
  }

void CollisionWorld::removeBody(CollisionBody& body) {
  if(body.id.IsInvalid())
    return;
  auto& interface = impl->system.GetBodyInterface();
  interface.RemoveBody(body.id);
  interface.DestroyBody(body.id);
  body.id = JPH::BodyID();
  bodies.erase(std::remove(bodies.begin(),bodies.end(),&body),bodies.end());
  rigid.erase(std::remove(rigid.begin(),rigid.end(),&body),rigid.end());
  }

void CollisionWorld::staticTriangles(const std::function<void(const StaticTriangle&)>& output) const {
  struct TriangleCollector final : JPH::TransformedShapeCollector {
    TriangleCollector(const std::function<void(const StaticTriangle&)>& output, bool water)
      :output(output),water(water) {
      }

    void AddHit(const JPH::TransformedShape& leaf) override {
      // CollectTransformedShapes resolves compounds and every scale/transform
      // wrapper before GetTrianglesStart, which only accepts leaf shapes.
      JPH::Shape::GetTrianglesContext context;
      const auto bounds = leaf.GetWorldSpaceBounds();
      leaf.GetTrianglesStart(context,bounds,JPH::RVec3::sZero());
      constexpr int batchSize = 128;
      JPH::Float3 vertices[batchSize*3];
      for(;;) {
        const int count = leaf.GetTrianglesNext(context,batchSize,vertices);
        if(count==0)
          break;
        for(int triangle=0;triangle<count;++triangle) {
          StaticTriangle result;
          result.water = water;
          for(int i=0;i<3;++i) {
            const auto& v = vertices[triangle*3+i];
            result.vertices[i] = Tempest::Vec3(v.x,v.y,v.z);
            }
          output(result);
          }
        }
      }

    const std::function<void(const StaticTriangle&)>& output;
    bool water;
    };

  for(const auto* object:bodies) {
    if(object->categoryId!=DynamicWorld::C_Landscape &&
       object->categoryId!=DynamicWorld::C_Object &&
       object->categoryId!=DynamicWorld::C_Water)
      continue;
    JPH::TransformedShape shape;
    {
      JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),object->id);
      if(!lock.Succeeded() || !lock.GetBody().IsStatic())
        continue; // no living bodies, dynamic props or animated/kinematic vobs
      shape = lock.GetBody().GetTransformedShape();
    }
    TriangleCollector collector(output,object->categoryId==DynamicWorld::C_Water);
    shape.CollectTransformedShapes(shape.GetWorldSpaceBounds(),collector);
    }
  }

void CollisionWorld::fillMaterial(QueryResult& result, const JPH::Shape& shape, JPH::SubShapeID subShape) const {
  if(result.body==nullptr || result.body->mesh==nullptr)
    return;
  const auto segment = Physics::triangleData(shape,subShape);
  result.material = result.body->mesh->materialId(segment);
  result.sector = result.body->mesh->sectorName(segment);
  }

void CollisionWorld::fillRagdoll(QueryResult& result, JPH::BodyID native) const {
  // Metadata for this selected native hit only: never cast another ray or keep
  // a stale BodyID table across corpse restores and native rig replacements.
  for(auto* body:ragdolls) {
    const size_t bone=body->boneForBodyId(native.GetIndexAndSequenceNumber());
    if(bone==size_t(-1)) continue;
    result.ragdoll=body;
    result.bone=bone;
    return;
    }
  }

int CollisionWorld::navigationBodyCategory(JPH::BodyID native) const {
  if(native.IsInvalid())
    return 0;
  JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),native);
  return lock.Succeeded() ? int(lock.GetBody().GetObjectLayer()) : 0;
  }

CollisionWorld::QueryResult CollisionWorld::rayCast(const Tempest::Vec3& from, const Tempest::Vec3& to, uint32_t mask,
                                                    bool backfaces, std::vector<QueryResult>* all,
                                                    const std::vector<const CollisionBody*>* excluded,
                                                    const Ragdoll* excludedRig) const {
  impl->rayCount.fetch_add(1,std::memory_order_relaxed);
  QueryResult result;
  if(all!=nullptr)
    all->clear();
  auto origin = toMeters(from);
  const auto end = toMeters(to);
  auto direction = end-origin;
  if(direction.IsNearZero())
    return result;
  JPH::RRayCast ray(origin,direction);
  JPH::RayCastSettings settings;
  settings.mTriangleEdgeTolerance = 1.0e-4f;
  settings.SetBackFaceMode(backfaces ? JPH::EBackFaceMode::CollideWithBackFaces : JPH::EBackFaceMode::IgnoreBackFaces);
  QueryLayerFilter filter(mask);
  QueryBroadPhaseFilter broadFilter(mask);
  struct RayBodyFilter final : JPH::IgnoreMultipleBodiesFilter {
    const Ragdoll* rig = nullptr;
    bool ShouldCollide(const JPH::BodyID& id) const override {
      return JPH::IgnoreMultipleBodiesFilter::ShouldCollide(id) &&
             (rig==nullptr || rig->boneForBodyId(id.GetIndexAndSequenceNumber())==size_t(-1));
      }
    } bodyFilter;
  bodyFilter.rig = excludedRig;
  if(excluded!=nullptr)
    for(auto* body:*excluded)
      if(body!=nullptr)
        bodyFilter.IgnoreBody(body->id);
  struct RayEdge {
    const PhysicVbo* mesh = nullptr;
    JPH::TransformedShape shape;
    JPH::Vec3 vertices[3];
    JPH::Vec3 from, to;
    uint32_t triangle = 0;
    } edge;
  auto convert = [&](const JPH::RayCastResult& hit, bool inspectEdge=false) {
    QueryResult value;
    JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),hit.mBodyID);
    if(!lock.Succeeded())
      return value;
    const auto& body = lock.GetBody();
    value.hasHit = true;
    value.fraction = hit.mFraction;
    value.nativeBodyId = hit.mBodyID;
    value.body = reinterpret_cast<CollisionBody*>(body.GetUserData());
    value.point = toCentimeters(origin*(1.f-hit.mFraction)+end*hit.mFraction);
    const auto normal = body.GetWorldSpaceSurfaceNormal(hit.mSubShapeID2,ray.GetPointOnRay(hit.mFraction));
    value.normal = Tempest::Vec3(normal.GetX(),normal.GetY(),normal.GetZ());
    if(value.body!=nullptr && value.body->mesh!=nullptr) {
      const auto* mesh = value.body->mesh;
      auto triangle = Physics::triangleData(*body.GetShape(),hit.mSubShapeID2);
      const bool duplicates = mesh->hasRayDuplicates(triangle);
      if(inspectEdge || duplicates || all==nullptr) {
        const auto inverse = body.GetWorldTransform().InversedRotationTranslation();
        const auto localFrom = JPH::Vec3(inverse*origin)/value.body->scale;
        const auto localTo = JPH::Vec3(inverse*end)/value.body->scale;
        const auto localPoint = localFrom*(1.f-hit.mFraction)+localTo*hit.mFraction;
        if(inspectEdge && mesh->isRayEdge(triangle,localPoint,settings.mTriangleEdgeTolerance) &&
           mesh->triangleVertices(triangle,edge.vertices)) {
          edge.mesh = mesh;
          edge.shape = body.GetTransformedShape();
          edge.from = localFrom;
          edge.to = localTo;
          edge.triangle = triangle;
          }
        if(duplicates) {
          // Coincident faces can carry different Gothic materials. Quantization
          // can reverse their fraction order; resolve their source metadata.
          triangle = mesh->resolveRayTriangle(triangle,localFrom,localTo,backfaces);
          }
        if(all==nullptr) {
          // Quantized floor heights change the game's slope and movement inputs.
          // Refine the selected native face from its original source plane.
          // Identity terrain also exports that plane's original scalar normal;
          // all-hit queries retain their native normals and fraction ordering.
          const float fraction = mesh->rayTriangleFraction(triangle,localFrom,localTo,backfaces);
          if(fraction<1.f) {
            value.fraction = fraction;
            value.point = toCentimeters(origin*(1.f-fraction)+end*fraction);
            if(fraction>0.f && value.body->category()==DynamicWorld::C_Landscape &&
               value.body->scale==JPH::Vec3::sOne() && body.GetRotation()==JPH::Quat::sIdentity()) {
              JPH::Vec3 sourceNormal;
              if(mesh->rayTriangleNormal(triangle,sourceNormal))
                value.normal = Tempest::Vec3(sourceNormal.GetX(),sourceNormal.GetY(),sourceNormal.GetZ());
              }
            }
          }
        }
      value.material = mesh->materialId(triangle);
      value.sector = mesh->sectorName(triangle);
      }
    if(body.GetObjectLayer()==DynamicWorld::C_Ragdoll)
      fillRagdoll(value,hit.mBodyID);
    return value;
    };
  if(all!=nullptr) {
    JPH::AllHitCollisionCollector<JPH::CastRayCollector> collector;
    impl->system.GetNarrowPhaseQuery().CastRay(ray,settings,collector,broadFilter,filter,bodyFilter);
    collector.Sort();
    for(const auto& hit:collector.mHits) {
      auto value = convert(hit);
      if(value.hasHit)
        all->push_back(value);
      }
    if(!all->empty())
      result = all->front();
    }
  else {
    JPH::ClosestHitCollisionCollector<JPH::CastRayCollector> collector;
    impl->system.GetNarrowPhaseQuery().CastRay(ray,settings,collector,broadFilter,filter,bodyFilter);
    if(collector.HadHit()) {
      result = convert(collector.mHit,true);
      if(edge.mesh!=nullptr) {
        // Compressed mesh vertices can reverse the closest face at a shared edge.
        // Requery this shape after releasing its body lock, and compare only
        // adjacent native hits against the original mesh planes. All-hit queries
        // retain their native fraction ordering.
        JPH::AllHitCollisionCollector<JPH::CastRayCollector> candidates;
        edge.shape.CastRay(ray,settings,candidates);
        auto closest = collector.mHit;
        auto triangle = edge.triangle;
        float fraction = edge.mesh->rayTriangleFraction(triangle,edge.from,edge.to,backfaces);
        for(const auto& hit:candidates.mHits) {
          if(hit.mBodyID!=closest.mBodyID)
            continue;
          const auto candidate = Physics::triangleData(*edge.shape.mShape,hit.mSubShapeID2);
          JPH::Vec3 vertices[3];
          if(!edge.mesh->triangleVertices(candidate,vertices))
            continue;
          unsigned shared = 0;
          for(const auto& vertex:edge.vertices)
            if(vertex==vertices[0] || vertex==vertices[1] || vertex==vertices[2])
              ++shared;
          if(shared<2)
            continue;
          const float candidateFraction = edge.mesh->rayTriangleFraction(candidate,edge.from,edge.to,backfaces);
          if(candidateFraction>=1.f || candidateFraction>fraction ||
             (candidateFraction==fraction && candidate>=triangle))
            continue;
          closest = hit;
          triangle = candidate;
          fraction = candidateFraction;
          }
        if(closest.mSubShapeID2!=collector.mHit.mSubShapeID2)
          result = convert(closest);
        }
      }
    }
  return result;
  }

CollisionWorld::QueryResult CollisionWorld::raySweep(const Tempest::Vec3& from, const Tempest::Vec3& to, float radius,
                                                     uint32_t mask, bool backfaces) const {
  if(radius<=0.f)
    return rayCast(from,to,mask,backfaces);
  QueryResult result;
  const auto origin = toMeters(from);
  const auto end = toMeters(to);
  const auto direction = end-origin;
  if(direction.IsNearZero())
    return result;
  const auto r = toMeters(radius);
  JPH::SphereShape probe(r);
  JPH::RShapeCast cast(&probe,JPH::Vec3::sOne(),JPH::RMat44::sTranslation(origin),direction);
  JPH::ShapeCastSettings settings;
  // The previous sphere sweep was two-sided; only the ray fallback rejects backfaces.
  settings.SetBackFaceMode(JPH::EBackFaceMode::CollideWithBackFaces);
  settings.mReturnDeepestPoint = true;
  settings.mActiveEdgeMode = JPH::EActiveEdgeMode::CollideWithAll;
  settings.mUsePreciseSphereTriangleCast = true;
  SweepCollector collector(direction);
  QueryLayerFilter filter(mask);
  QueryBroadPhaseFilter broadFilter(mask);
  impl->system.GetNarrowPhaseQuery().CastShape(cast,settings,JPH::RVec3::sZero(),collector,broadFilter,filter);
  if(!collector.HadHit())
    return result;
  const auto& hit = collector.hit;
  auto delta = end-hit.mContactPointOn2;
  delta.SetY(0.f);
  const bool fallback = delta.LengthSq()>=0.00001f;
  JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),hit.mBodyID2);
  if(!lock.Succeeded())
    return result;
  const auto& body = lock.GetBody();
  result.hasHit = true;
  // Walking expects the bottom of the probe projected onto the original vertical ray.
  // Preserve the operation order: shallow rays can extrapolate this fraction far
  // beyond the segment, where algebraically equivalent expressions round differently.
  const float ySphere = (1.f-hit.mFraction)*origin.GetY()+hit.mFraction*end.GetY();
  const float yHit = ySphere-r;
  result.fraction = direction.GetY()!=0.f ? (yHit-origin.GetY())/direction.GetY() : hit.mFraction;
  result.point = toCentimeters(origin*(1.f-result.fraction)+end*result.fraction);
  auto normal = -hit.mPenetrationAxis.NormalizedOr(JPH::Vec3::sAxisY());
  result.normal = Tempest::Vec3(normal.GetX(),normal.GetY(),normal.GetZ());
  result.body = reinterpret_cast<CollisionBody*>(body.GetUserData());
  fillMaterial(result,*body.GetShape(),hit.mSubShapeID2);
  if(body.GetObjectLayer()==DynamicWorld::C_Ragdoll)
    fillRagdoll(result,hit.mBodyID2);
  // A failed center ray kept the old sphere hit, while resetting only its fraction.
  if(fallback) {
    lock.ReleaseLock();
    auto ray = rayCast(from,to,mask,backfaces);
    if(ray.hasHit)
      return ray;
    result.fraction = 1.f;
    }
  return result;
  }

std::vector<CollisionWorld::Overlap> CollisionWorld::overlaps(const CollisionBody& body, float marginCm, uint32_t mask) const {
  std::vector<Overlap> ret;
  JPH::RMat44 com;
  JPH::ShapeRefC shape;
  {
    JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),body.id);
    if(!lock.Succeeded())
      return ret;
    com   = lock.GetBody().GetCenterOfMassTransform();
    shape = lock.GetBody().GetShape();
  }
  // The simulation's own narrow phase settings: back faces and inactive edges never push.
  JPH::CollideShapeSettings settings;
  settings.mActiveEdgeMode = JPH::EActiveEdgeMode::CollideOnlyWithActive;
  settings.mMaxSeparationDistance = toMeters(marginCm);
  JPH::AllHitCollisionCollector<JPH::CollideShapeCollector> collector;
  QueryLayerFilter filter(mask);
  QueryBroadPhaseFilter broadFilter(mask);
  JPH::IgnoreSingleBodyFilter self(body.id);
  const auto base = com.GetTranslation();
  impl->system.GetNarrowPhaseQuery().CollideShape(shape,JPH::Vec3::sOne(),com,settings,base,collector,
                                               broadFilter,filter,self);
  for(const auto& hit:collector.mHits) {
    JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),hit.mBodyID2);
    if(!lock.Succeeded())
      continue;
    const auto& native = lock.GetBody();
    Overlap o;
    o.body     = reinterpret_cast<CollisionBody*>(native.GetUserData());
    o.category = int(native.GetObjectLayer());
    o.depth    = hit.mPenetrationDepth*100.f;
    o.axis     = toCentimeters(hit.mPenetrationAxis.NormalizedOr(JPH::Vec3::sZero())*0.01f);
    o.point1   = toCentimeters(base+hit.mContactPointOn1);
    o.point2   = toCentimeters(base+hit.mContactPointOn2);
    if(o.body!=nullptr && o.body->mesh!=nullptr) {
      QueryResult info;
      info.body = o.body;
      fillMaterial(info,*native.GetShape(),hit.mSubShapeID2);
      o.material = info.material;
      o.sector   = info.sector;
      }
    ret.push_back(o);
    }
  return ret;
  }

bool CollisionWorld::hasCollision(const JPH::Shape* shape, const Tempest::Vec3& center,
                                  Tempest::Vec3& normal, Interactive*& vob) const {
  if(shape==nullptr)
    return false;
  JPH::CollideShapeSettings settings;
  settings.mBackFaceMode = JPH::EBackFaceMode::CollideWithBackFaces;
  settings.mActiveEdgeMode = JPH::EActiveEdgeMode::CollideWithAll;
  const auto bounds = shape->GetLocalBounds();
  const float disc = angularMotionDisc(*shape);
  const float radius = shape->GetInnerRadius();
  // The old contact callback also received separated contacts: its final GJK
  // distance bound included the capsule radius and relative breaking threshold.
  // Keep that query tolerance without changing the physical capsule.
  settings.mMaxSeparationDistance = radius+disc*0.02f;
  JPH::AllHitCollisionCollector<JPH::CollideShapeCollector> collector;
  constexpr uint32_t mask = (1u<<DynamicWorld::C_Landscape)|(1u<<DynamicWorld::C_Object)|(1u<<DynamicWorld::C_Ragdoll)|
                            (1u<<DynamicWorld::C_MovableQuery);
  QueryLayerFilter filter(mask);
  QueryBroadPhaseFilter broadFilter(mask);
  const auto position = toMeters(center);
  impl->system.GetNarrowPhaseQuery().CollideShape(shape,JPH::Vec3::sOne(),JPH::RMat44::sTranslation(position),
                                               settings,position,collector,broadFilter,filter);
  auto sum = JPH::Vec3::sZero();
  bool hasSourceWitnessNormal = false;
  size_t count = 0;
  Interactive* hitVob = nullptr;
  for(const auto& hit:collector.mHits) {
    JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),hit.mBodyID2);
    if(!lock.Succeeded())
      continue;
    const auto& native = lock.GetBody();
    // The animation controller collides with the actual corpse surface. The
    // legacy positive-gap compatibility rule applies only to the old world.
    if(native.GetObjectLayer()==DynamicWorld::C_Ragdoll && hit.mPenetrationDepth<=0.f)
      continue;
    auto* body = reinterpret_cast<CollisionBody*>(native.GetUserData());
    const auto* baseShape = body==nullptr ? native.GetShape() : body->baseShape.GetPtr();
    const float contactDistance = radius+std::min(disc,angularMotionDisc(*baseShape))*0.02f;
    if(-hit.mPenetrationDepth>=contactDistance)
      continue;
    auto contactNormal = -hit.mPenetrationAxis.NormalizedOr(JPH::Vec3::sAxisY());
    if(body!=nullptr && body->mesh!=nullptr) {
      JPH::Vec3 vertices[3];
      const auto triangle = Physics::triangleData(*native.GetShape(),hit.mSubShapeID2);
      if(!body->mesh->triangleVertices(triangle,vertices))
        continue;
      JPH::AABox triangleBounds;
      for(const auto& vertex:vertices)
        triangleBounds.Encapsulate(vertex);
      // HumShape ignored the transform basis when building its mesh-local AABB.
      // Bullet's inverse transposed the supplied basis, including its scale.
      // Preserve this original candidate box before accepting proximity hits;
      // Jolt's separation tolerance expands both broad and triangle queries.
      const auto inverse = native.GetWorldTransform().InversedRotationTranslation();
      const auto localCenter = body->scale*JPH::Vec3(inverse*position);
      if(!triangleBounds.Overlaps(JPH::AABox(localCenter+bounds.mMin,localCenter+bounds.mMax)))
        continue;
      if(hit.mPenetrationDepth<0.f) {
        auto sourceNormal = JPH::Vec3::sZero();
        if(!npcContactCandidate(*shape,position,native,body->scale,vertices,contactDistance,&sourceNormal))
          continue;
        if(!sourceNormal.IsNearZero()) {
          contactNormal = sourceNormal;
          hasSourceWitnessNormal = true;
          }
        }
      }
    sum += contactNormal;
    ++count;
    if(body!=nullptr && body->categoryId==DynamicWorld::C_Object)
      hitVob = body->pointerType==DynamicWorld::P_Interactive ? reinterpret_cast<Interactive*>(body->pointer) : nullptr;
    }
  if(count==0)
    return false;
  if(hasSourceWitnessNormal) {
    normal = Tempest::Vec3::normalize(Tempest::Vec3(sum.GetX(),sum.GetY(),sum.GetZ()));
    }
  else {
    sum = sum.NormalizedOr(JPH::Vec3::sZero());
    normal = Tempest::Vec3(sum.GetX(),sum.GetY(),sum.GetZ());
    }
  vob = hitVob;
  return true;
  }

void CollisionWorld::tick(uint64_t dt) {
  const auto started=std::chrono::steady_clock::now();
  constexpr float step = 1.f/60.f;
  bool advanced = false;
  if(!rigid.empty() || !ragdolls.empty()) {
    accumulatedTime += float(dt)/1000.f;
    int wholeSteps = 0;
    if(accumulatedTime>=step) {
      wholeSteps = int(accumulatedTime/step);
      // Preserve the previous float scheduling and discard steps beyond the
      // frame budget before applying the maximum of two simulated substeps.
      accumulatedTime -= float(wholeSteps)*step;
      }
    const int count = std::min(wholeSteps,2);
    for(int i=0;i<count;++i) {
      ++impl->simulationStep;
      for(auto* body:ragdolls) body->prepareKinematicStep(step);
      const bool dynamicRagdolls=std::any_of(ragdolls.begin(),ragdolls.end(),[](const Ragdoll* body) { return !body->isKinematic(); });
      // A capsule stopped by CCD otherwise compresses its connected limb while
      // the rest of the rig finishes the 1/60 s step. Two native collision steps
      // keep that late-contact displacement small. The public fixed-step clock
      // and two-update frame budget stay unchanged; worlds without corpses keep
      // their original single collision step.
      impl->system.Update(float(step),dynamicRagdolls ? 2 : 1,&impl->allocator,&impl->jobs);
      for(auto* body:ragdolls) body->finishKinematicStep();
      advanced = true;
      }
    }
  if(!advanced) {
    // Individually added bodies deepen Jolt's broad phase until Update maintains
    // its tree. Static worlds and frames below the fixed-step threshold need
    // this maintenance too. A zero delta rebuilds the tree without simulating
    // bodies, advancing contacts or emitting callbacks, at the main tick boundary.
    impl->system.Update(0.f,1,&impl->allocator,&impl->jobs);
    }

  std::vector<Impl::Impact> impacts;
  std::vector<Impl::NoiseImpact> noises;
  {
    std::lock_guard<std::mutex> guard(impl->impactMutex);
    impacts.swap(impl->impacts);
    noises.swap(impl->noises);
    }
  auto rigFor=[&](JPH::BodyID id) -> Ragdoll* {
    QueryResult result;
    fillRagdoll(result,id);
    return result.ragdoll;
    };
  // Resolve identities only after all Jolt workers finish. The native sequence
  // number rejects a removed/reused body; NPC handles are weak and user_ptr is
  // cleared by NPC destruction even while the script retains the instance.
  for(const auto& impact:noises) {
    Npc* culprit=nullptr;
    bool knownCulprit=false;
    {
      JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),impact.other);
      if(lock.Succeeded()) {
        const auto layer=lock.GetBody().GetObjectLayer();
        if(layer==DynamicWorld::C_LivingBody || layer==DynamicWorld::C_ActiveBody || layer==DynamicWorld::C_Ragdoll)
          if(auto* other=rigFor(impact.other)) {
            culprit=other->noiseCulprit();
            knownCulprit=culprit!=nullptr;
            // A stationary NPC struck by an unknown falling body is its
            // victim. Attribute an owner only when that rig moves into it.
            if(culprit==nullptr && impact.otherPushing) culprit=other->noiseNpc();
            }
        }
      }
    if(culprit==nullptr) continue;
    JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),impact.body);
    if(!lock.Succeeded()) continue;
    const auto layer=lock.GetBody().GetObjectLayer();
    // A body thrown by a known actor keeps that cause when it happens to
    // strike a bystander. A counterpart's known cause can still propagate.
    if(layer==DynamicWorld::C_Ragdoll || layer==DynamicWorld::C_ActiveBody || layer==DynamicWorld::C_LivingBody) {
      if(auto* rig=rigFor(impact.body))
        if(knownCulprit || rig->noiseCulprit()==nullptr) rig->setNoiseCulprit(culprit);
      }
    else if(auto* body=reinterpret_cast<CollisionBody*>(lock.GetBody().GetUserData())) {
      const auto current=impact.step<=body->noiseCulpritUntilStep ? body->noiseCulprit.lock() : nullptr;
      if(!knownCulprit && current!=nullptr && current->user_ptr!=nullptr) continue;
      body->noiseCulprit=culprit->handlePtr();
      body->noiseCulpritUntilStep=impact.step+300;
      body->setTraceCulprit(culprit);
      }
    }
  std::sort(noises.begin(),noises.end(),[](const auto& a,const auto& b) {
    return std::make_tuple(a.step,a.logicalBody,-a.impulse,a.body.GetIndexAndSequenceNumber()) <
           std::make_tuple(b.step,b.logicalBody,-b.impulse,b.body.GetIndexAndSequenceNumber());
    });
  if(impl->noiseEnabled && physicsNoise) {
    for(const auto& impact:noises) {
      if(impact.impulse<.08f) continue;
      PhysicsNoise noise;
      {
        JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),impact.body);
        if(!lock.Succeeded()) continue;
        const auto layer=lock.GetBody().GetObjectLayer();
        if(layer==DynamicWorld::C_Ragdoll || layer==DynamicWorld::C_ActiveBody || layer==DynamicWorld::C_LivingBody) {
          auto* rig=rigFor(impact.body);
          if(rig==nullptr || !rig->acceptNoise(impact.step)) continue;
          noise.body=rig->noiseId();
          noise.material=MAT_LEATHER;
          noise.what="ragdoll";
          noise.culprit=rig->noiseCulprit();
          }
        else {
          auto* body=reinterpret_cast<CollisionBody*>(lock.GetBody().GetUserData());
          if(body==nullptr || impact.step<body->nextNoiseStep) continue;
          body->nextNoiseStep=impact.step+30;
          noise.body=impact.body.GetIndexAndSequenceNumber();
          noise.material=body->noiseMaterial;
          noise.what=body->noiseWhat;
          const auto culprit=impact.step<=body->noiseCulpritUntilStep ? body->noiseCulprit.lock() : nullptr;
          noise.culprit=culprit!=nullptr ? static_cast<Npc*>(culprit->user_ptr) : nullptr;
          }
        }
      noise.position=impact.point;
      noise.surface=impact.surface;
      noise.impulse=impact.impulse;
      const float gain=noise.material==MAT_METAL ? 1.15f : noise.material==MAT_CLAY ? .9f :
                       noise.material==MAT_LEATHER ? .55f : 1.f;
      noise.volume=std::clamp(std::sqrt(noise.impulse)*gain/18.f,.04f,1.f);
      noise.radius=100.f+1100.f*noise.volume;
      // Game callbacks may remove bodies and must run without native locks.
      physicsNoise(noise);
      }
    }
  std::sort(impacts.begin(),impacts.end(),[](const auto& a, const auto& b) {
    return std::make_tuple(a.step,a.item.GetIndexAndSequenceNumber(),a.landscape,a.subShape,a.material,a.impulse,a.mass) <
           std::make_tuple(b.step,b.item.GetIndexAndSequenceNumber(),b.landscape,b.subShape,b.material,b.impulse,b.mass);
    });
  if(hitItem) {
    for(const auto& impact:impacts) {
      Item* item = nullptr;
      {
        JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),impact.item);
        if(lock.Succeeded()) {
          auto* body = reinterpret_cast<CollisionBody*>(lock.GetBody().GetUserData());
          if(body!=nullptr && body->pointerType==DynamicWorld::P_Item)
            item = reinterpret_cast<Item*>(body->pointer);
          }
        }
      // Game callbacks may remove bodies and must run without Jolt locks.
      if(item!=nullptr)
        hitItem(*item,impact.material,impact.impulse,impact.mass);
      }
    }

  std::vector<JPH::BodyID> active;
  active.reserve(rigid.size());
  for(auto* body:rigid)
    active.push_back(body->id);
  for(auto id:active) {
    Item* item = nullptr;
    Tempest::Matrix4x4 matrix;
    bool disable = false;
    {
      JPH::BodyLockRead lock(impl->system.GetBodyLockInterface(),id);
      if(!lock.Succeeded())
        continue;
      const auto& native = lock.GetBody();
      auto* body = reinterpret_cast<CollisionBody*>(native.GetUserData());
      if(body==nullptr || body->pointerType!=DynamicWorld::P_Item || body->pointer==nullptr)
        continue;
      item = reinterpret_cast<Item*>(body->pointer);
      auto transform = native.GetWorldTransform();
      float m[16];
      for(JPH::uint c=0;c<4;++c) {
        auto column = transform.GetColumn4(c);
        const float factor = c<3 ? body->scale[c] : 1.f;
        m[c*4+0] = column.GetX()*factor;
        m[c*4+1] = column.GetY()*factor;
        m[c*4+2] = column.GetZ()*factor;
        m[c*4+3] = column.GetW();
        }
      m[12] *= 100.f;
      m[13] *= 100.f;
      m[14] *= 100.f;
      matrix.setData(m);
      bool keepSleeping = false;
      if(!native.IsActive()) {
        const float range = toMeters(DynamicWorld::characterItemRange);
        for(const auto* character:impl->virtualCharacters) {
          if((native.GetPosition()-character->impl->character->GetPosition()).LengthSq()<=range*range) {
            keepSleeping = true;
            break;
            }
          }
        }
      // Legacy items lose their body at rest. A nearby virtual character needs
      // that sleeping body to remain queryable so a native contact can wake it.
      // Removing the opt-in controller restores the original cleanup policy.
      disable = (!native.IsActive() && !keepSleeping) || native.GetPosition().GetY()<bboxMin.GetY()-100.f;
      }
    item->setObjMatrix(matrix);
    if(disable)
      item->setPhysicsDisable();
    }
  impl->stepMilliseconds=std::chrono::duration<float,std::milli>(std::chrono::steady_clock::now()-started).count()+impl->characterMilliseconds;
  impl->characterMilliseconds = 0;
  impl->lastRayCount=impl->rayCount.exchange(0,std::memory_order_relaxed);
  }

std::unique_ptr<Ragdoll> CollisionWorld::createRagdoll(const Skeleton& skeleton, const HitZones& zones,
                                                      const Tempest::Matrix4x4* worldPose, size_t numBones,
                                                      const Tempest::Matrix4x4& objectMatrix,
                                                      const Tempest::Vec3& initialVelocity) {
  std::unique_ptr<Ragdoll> result;
  try {
    result.reset(new Ragdoll(*this,skeleton,zones,worldPose,numBones,objectMatrix,initialVelocity));
    }
  catch(const std::runtime_error&) {
    return nullptr;
    }
  return result->valid() ? std::move(result) : nullptr;
  }

std::unique_ptr<Ragdoll> CollisionWorld::createKinematicBody(const Skeleton& skeleton, const HitZones& zones,
                                                            const Tempest::Matrix4x4* worldPose, size_t numBones,
                                                            const Tempest::Matrix4x4& objectMatrix) {
  std::unique_ptr<Ragdoll> result;
  try {
    result.reset(new Ragdoll(*this,skeleton,zones,worldPose,numBones,objectMatrix,{},true));
    }
  catch(const std::runtime_error&) {
    return nullptr;
    }
  return result->valid() ? std::move(result) : nullptr;
  }

JPH::PhysicsSystem& CollisionWorld::physicsSystem() { return impl->system; }

uint32_t CollisionWorld::registerRagdoll(Ragdoll& body) {
  // Zero is the default group of ordinary world bodies.
  if(nextRagdollGroup==0) throw std::runtime_error("Jolt ragdoll collision group capacity exhausted");
  ragdolls.push_back(&body);
  return nextRagdollGroup++;
  }

void CollisionWorld::unregisterRagdoll(Ragdoll& body) {
  ragdolls.erase(std::remove(ragdolls.begin(),ragdolls.end(),&body),ragdolls.end());
  }

size_t CollisionWorld::bodyCount() const { return impl->system.GetNumBodies(); }
float CollisionWorld::stepMilliseconds() const { return impl->stepMilliseconds; }
uint64_t CollisionWorld::rayCount() const { return impl->lastRayCount; }
uint64_t CollisionWorld::simulationStep() const { return impl->simulationStep; }

void CollisionWorld::setBBox(JPH::Vec3Arg min, JPH::Vec3Arg) {
  bboxMin = min;
  }

void CollisionWorld::setItemHitCallback(std::function<void(Item&, zenkit::MaterialGroup, float, float)> callback) {
  hitItem = std::move(callback);
  impl->emitImpacts = bool(hitItem) && !impl->noiseEnabled;
  }

void CollisionWorld::setPhysicsNoiseEnabled(bool enabled) {
  impl->noiseEnabled=enabled;
  impl->emitNoise=enabled && bool(physicsNoise);
  impl->emitImpacts=!enabled && bool(hitItem);
  }

bool CollisionWorld::physicsNoiseEnabled() const {
  return impl->noiseEnabled;
  }

void CollisionWorld::setPhysicsNoiseCallback(std::function<void(const PhysicsNoise&)> callback) {
  physicsNoise=std::move(callback);
  impl->emitNoise=impl->noiseEnabled && bool(physicsNoise);
  }
