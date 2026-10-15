#include "collisionworld.h"
#include "physicvbo.h"
#include "dynamicworld.h"
#include "ragdoll.h"
#include "world/objects/item.h"

#include <Jolt/RegisterTypes.h>
#include <Jolt/Core/Factory.h>
#include <Jolt/Core/JobSystemThreadPool.h>
#include <Jolt/Core/TempAllocator.h>
#include <Jolt/Geometry/GJKClosestPoint.h>
#include <Jolt/Physics/PhysicsSystem.h>
#include <Jolt/Physics/Body/BodyCreationSettings.h>
#include <Jolt/Physics/Body/BodyLock.h>
#include <Jolt/Physics/Collision/CollisionCollectorImpl.h>
#include <Jolt/Physics/Collision/ContactListener.h>
#include <Jolt/Physics/Collision/EstimateCollisionResponse.h>
#include <Jolt/Physics/Collision/RayCast.h>
#include <Jolt/Physics/Collision/CastResult.h>
#include <Jolt/Physics/Collision/ShapeCast.h>
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
      if(layer==DynamicWorld::C_Item || layer==DynamicWorld::C_Ragdoll || layer==DynamicWorld::C_LivingBody || layer==DynamicWorld::C_ActiveBody)
        return dynamicLayer;
      if(layer==DynamicWorld::C_Water || layer==DynamicWorld::C_Ghost)
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
      if(a==DynamicWorld::C_ActiveBody)
        return b==DynamicWorld::C_Landscape || b==DynamicWorld::C_Object || b==DynamicWorld::C_Item || b==DynamicWorld::C_Ragdoll;
      if(b==DynamicWorld::C_ActiveBody)
        return a==DynamicWorld::C_Landscape || a==DynamicWorld::C_Object || a==DynamicWorld::C_Item || a==DynamicWorld::C_Ragdoll;
      if(a==DynamicWorld::C_LivingBody)
        return b==DynamicWorld::C_Item || b==DynamicWorld::C_Ragdoll;
      if(b==DynamicWorld::C_LivingBody)
        return a==DynamicWorld::C_Item || a==DynamicWorld::C_Ragdoll;
      return (a==DynamicWorld::C_Item || b==DynamicWorld::C_Item ||
              a==DynamicWorld::C_Ragdoll || b==DynamicWorld::C_Ragdoll) &&
             a!=DynamicWorld::C_Null && b!=DynamicWorld::C_Null;
      }
    };

  struct BroadPhaseFilter final : JPH::ObjectVsBroadPhaseLayerFilter {
    bool ShouldCollide(JPH::ObjectLayer layer, JPH::BroadPhaseLayer broad) const override {
      if(layer==DynamicWorld::C_LivingBody)
        return broad==dynamicLayer;
      return layer==DynamicWorld::C_Item || layer==DynamicWorld::C_Ragdoll || layer==DynamicWorld::C_ActiveBody ||
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
                                    (1u<<DynamicWorld::C_LivingBody)|(1u<<DynamicWorld::C_ActiveBody);
      constexpr uint32_t sensorMask = (1u<<DynamicWorld::C_Water)|(1u<<DynamicWorld::C_Ghost);
      if(layer==dynamicLayer)
        return (mask & itemMask)!=0;
      if(layer==sensorLayer)
        return (mask & sensorMask)!=0;
      return layer==staticLayer && (mask & ~(itemMask|sensorMask))!=0;
      }
    const uint32_t mask;
    };

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
    if(impulse/obj->mass<0.9f)
      return;
    auto material = zenkit::MaterialGroup::STONE;
    if(terrain!=nullptr && terrain->mesh!=nullptr)
      material = terrain->mesh->materialId(Physics::triangleData(*land->GetShape(),landSubShape));
    std::lock_guard<std::mutex> guard(impactMutex);
    impacts.push_back({item->GetID(),material,impulse,obj->mass,simulationStep,land->GetID().GetIndexAndSequenceNumber(),landSubShape.GetValue()});
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
  uint64_t simulationStep = 0;
  bool emitImpacts = false;
  std::atomic<uint64_t> rayCount {0};
  uint64_t lastRayCount = 0;
  float stepMilliseconds = 0;
  };

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

CollisionWorld::DynamicBody::DynamicBody(CollisionWorld& owner, JPH::ShapeRefC shape, float mass)
  :CollisionBody(owner,std::move(shape),DynamicWorld::C_Item), mass(mass) {
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

Tempest::Vec3 CollisionWorld::CollisionBody::position() const {
  return toCentimeters(owner->impl->system.GetBodyInterface().GetPosition(id));
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

void CollisionWorld::addBody(CollisionBody& body, const Tempest::Matrix4x4& matrix, float friction,
                             bool movable, bool sensor, float mass) {
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
    }
  auto& interface = impl->system.GetBodyInterface();
  auto* native = interface.CreateBody(settings);
  if(native==nullptr)
    throw std::runtime_error("Jolt body capacity exhausted");
  body.id = native->GetID();
  interface.AddBody(body.id,type==JPH::EMotionType::Dynamic ? JPH::EActivation::Activate : JPH::EActivation::DontActivate);
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
                                                                           float friction, float mass) {
  mass = mass>0.f ? mass : 1.f;
  auto result = std::unique_ptr<DynamicBody>(new DynamicBody(*this,std::move(shape),mass));
  addBody(*result,matrix,friction,false,false,mass);
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

void CollisionWorld::fillMaterial(QueryResult& result, const JPH::Shape& shape, JPH::SubShapeID subShape) const {
  if(result.body==nullptr || result.body->mesh==nullptr)
    return;
  const auto segment = Physics::triangleData(shape,subShape);
  result.material = result.body->mesh->materialId(segment);
  result.sector = result.body->mesh->sectorName(segment);
  }

CollisionWorld::QueryResult CollisionWorld::rayCast(const Tempest::Vec3& from, const Tempest::Vec3& to, uint32_t mask,
                                                    bool backfaces, std::vector<QueryResult>* all) const {
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
    return value;
    };
  if(all!=nullptr) {
    JPH::AllHitCollisionCollector<JPH::CastRayCollector> collector;
    impl->system.GetNarrowPhaseQuery().CastRay(ray,settings,collector,broadFilter,filter);
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
    impl->system.GetNarrowPhaseQuery().CastRay(ray,settings,collector,broadFilter,filter);
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
  constexpr uint32_t mask = (1u<<DynamicWorld::C_Landscape)|(1u<<DynamicWorld::C_Object)|(1u<<DynamicWorld::C_Ragdoll);
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
  {
    std::lock_guard<std::mutex> guard(impl->impactMutex);
    impacts.swap(impl->impacts);
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
      disable = !native.IsActive() || native.GetPosition().GetY()<bboxMin.GetY()-100.f;
      }
    item->setObjMatrix(matrix);
    if(disable)
      item->setPhysicsDisable();
    }
  impl->stepMilliseconds=std::chrono::duration<float,std::milli>(std::chrono::steady_clock::now()-started).count();
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

void CollisionWorld::setBBox(JPH::Vec3Arg min, JPH::Vec3Arg) {
  bboxMin = min;
  }

void CollisionWorld::setItemHitCallback(std::function<void(Item&, zenkit::MaterialGroup, float, float)> callback) {
  hitItem = std::move(callback);
  impl->emitImpacts = bool(hitItem);
  }
