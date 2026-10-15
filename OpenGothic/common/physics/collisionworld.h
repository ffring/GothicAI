#pragma once

#include <Tempest/Matrix4x4>
#include <Tempest/Vec>
#include <zenkit/Material.hh>

#include <functional>
#include <memory>
#include <vector>

#include "physics/physics.h"
#include <Jolt/Physics/Body/BodyID.h>

class Item;
class Interactive;
class PhysicVbo;
class Skeleton;
class HitZones;
class Ragdoll;
namespace JPH { class PhysicsSystem; }

class CollisionWorld final {
  public:
    CollisionWorld();
    ~CollisionWorld();
    CollisionWorld(const CollisionWorld&) = delete;

    static float         toMeters(float v);
    static JPH::Vec3     toMeters(const Tempest::Vec3& v);
    static Tempest::Vec3 toCentimeters(JPH::Vec3Arg v);

    class DynamicBody;
    class CollisionBody {
      public:
        virtual ~CollisionBody();
        CollisionBody(const CollisionBody&) = delete;

        void                setObjMatrix(const Tempest::Matrix4x4& matrix);
        void                setPosition(const Tempest::Vec3& position);
        void                setScale(const Tempest::Vec3& scale);
        void                setShape(JPH::ShapeRefC shape);
        void                setPayload(void* pointer, int type);
        Tempest::Vec3       position() const;
        Tempest::Matrix4x4  matrix() const;
        void                getAabb(JPH::Vec3& min, JPH::Vec3& max) const;

        int                 category() const { return categoryId; }
        int                 ptrType() const { return pointerType; }
        void*               payload() const { return pointer; }
        const JPH::Shape*    shape() const { return scaledShape.GetPtr(); }

      private:
        CollisionBody(CollisionWorld& owner, JPH::ShapeRefC shape, int category);
        CollisionWorld*     owner = nullptr;
        JPH::BodyID         id;
        JPH::ShapeRefC      baseShape;
        JPH::ShapeRefC      scaledShape;
        JPH::Vec3           scale = JPH::Vec3::sOne();
        const PhysicVbo*    mesh = nullptr;
        int                 categoryId = 0;
        int                 pointerType = 0;
        void*               pointer = nullptr;

      friend class CollisionWorld;
      friend class DynamicBody;
      };

    class DynamicBody final : public CollisionBody {
      private:
        DynamicBody(CollisionWorld& owner, JPH::ShapeRefC shape, float mass);
        const float mass;
      friend class CollisionWorld;
      };

    struct QueryResult {
      bool                 hasHit = false;
      float                fraction = 1.f;
      Tempest::Vec3        point = {};
      Tempest::Vec3        normal = {};
      CollisionBody*       body = nullptr;
      zenkit::MaterialGroup material = zenkit::MaterialGroup::UNDEFINED;
      const char*          sector = nullptr;
      Ragdoll*             ragdoll = nullptr;
      size_t               bone = size_t(-1);
      };

    void tick(uint64_t dt);
    void setBBox(JPH::Vec3Arg min, JPH::Vec3Arg max);
    void setItemHitCallback(std::function<void(Item&, zenkit::MaterialGroup, float impulse, float mass)> f);

    // BodyInterface updates the broad phase immediately when a body moves.
    void updateAabbs() const {}
    void touchAabbs() const {}

    bool hasCollision(const JPH::Shape* shape, const Tempest::Vec3& center, Tempest::Vec3& normal, Interactive*& vob) const;

    std::unique_ptr<CollisionBody> addCollisionBody(JPH::ShapeRefC shape, const Tempest::Matrix4x4& matrix, float friction,
                                                   int category, bool movable = false, bool sensor = false, const PhysicVbo* mesh = nullptr);
    std::unique_ptr<DynamicBody> addDynamicBody(JPH::ShapeRefC shape, const Tempest::Matrix4x4& matrix, float friction, float mass);
    std::unique_ptr<Ragdoll> createRagdoll(const Skeleton& skeleton, const HitZones& zones,
                                         const Tempest::Matrix4x4* worldPose, size_t numBones,
                                         const Tempest::Matrix4x4& objectMatrix,
                                         const Tempest::Vec3& initialVelocity = {});
    std::unique_ptr<Ragdoll> createKinematicBody(const Skeleton& skeleton, const HitZones& zones,
                                               const Tempest::Matrix4x4* worldPose, size_t numBones,
                                               const Tempest::Matrix4x4& objectMatrix);
    size_t bodyCount() const;
    float stepMilliseconds() const;
    uint64_t rayCount() const;

    // Masks use 1u << DynamicWorld::Category. Normals keep the mesh's winding.
    QueryResult rayCast(const Tempest::Vec3& from, const Tempest::Vec3& to, uint32_t mask,
                        bool backfaces = false, std::vector<QueryResult>* all = nullptr) const;
    QueryResult raySweep(const Tempest::Vec3& from, const Tempest::Vec3& to, float radius, uint32_t mask,
                         bool backfaces = false) const;

  private:
    struct Impl;
    void addBody(CollisionBody& body, const Tempest::Matrix4x4& matrix, float friction,
                 bool movable, bool sensor, float mass);
    void removeBody(CollisionBody& body);
    void fillMaterial(QueryResult& result, const JPH::Shape& shape, JPH::SubShapeID subShape) const;
    void fillRagdoll(QueryResult& result, JPH::BodyID body) const;
    JPH::PhysicsSystem& physicsSystem();
    uint32_t registerRagdoll(Ragdoll& body);
    void unregisterRagdoll(Ragdoll& body);
    std::unique_ptr<Impl> impl;
    std::vector<CollisionBody*> bodies;
    std::vector<DynamicBody*> rigid;
    std::vector<Ragdoll*> ragdolls;
    uint32_t nextRagdollGroup = 1;
    std::function<void(Item&, zenkit::MaterialGroup, float, float)> hitItem;
    JPH::Vec3 bboxMin = JPH::Vec3::sZero();
    float accumulatedTime = 0;
    friend class Ragdoll;
  };
