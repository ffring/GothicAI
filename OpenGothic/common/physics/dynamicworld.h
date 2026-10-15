#pragma once

#include <zenkit/Mesh.hh>

#include <Tempest/Matrix4x4>
#include <functional>
#include <memory>
#include <limits>

class PhysicMeshShape;
class PhysicVbo;
class PackedMesh;
class Skeleton;
class Bounds;
class HitZones;
class Ragdoll;

class World;
class Bullet;
class Npc;
class Item;
class Interactive;
class DbgPainter;

class CollisionWorld;

class DynamicWorld final {
  private:
    struct BodyData;
    struct WorldBodies;
    struct BBoxShape;
    struct NpcBody;
    struct NpcBodyList;
    struct BulletsList;
    struct BBoxList;

  public:
    static constexpr float gravityMS   = 9.8f; // meters per second^2
    static constexpr float gravity     = gravityMS*100.f/(1000.f*1000.f); // centimeters per milliseconds^2
    static constexpr float bulletSpeed = 3; // centimeters per milliseconds
    static constexpr float spellSpeed  = 1; // centimeters per milliseconds

    DynamicWorld(World *world, const zenkit::Mesh& mesh);
    DynamicWorld(const DynamicWorld&)=delete;
    ~DynamicWorld();

    enum Category {
      C_Null      = 1,
      C_Landscape = 2,
      C_Water     = 3,
      C_Object    = 4,
      C_Ghost     = 5,
      C_Item      = 6,
      C_Ragdoll   = 7,
      C_LivingBody= 8,
      C_ActiveBody= 9,
      };

    enum PtrType {
      P_Null,
      P_Item,
      P_Interactive,
      P_Editor
      };

    enum MoveCode : uint8_t {
      MC_Fail,
      MC_OK,
      MC_Skip,
      MC_Partial,
      };

    struct CollisionTest {
      Tempest::Vec3 partial = {};
      Tempest::Vec3 normal  = {};
      bool          preFall = false;

      Interactive*  vob     = nullptr;
      Npc*          npc     = nullptr;
      float         npcCol  = 0;
      bool          landCol = false;
      };

    struct NpcItem {
      public:
        NpcItem()=default;
        NpcItem(DynamicWorld* owner, NpcBody* obj):owner(owner),obj(obj){}
        NpcItem(NpcItem&& it):owner(it.owner),obj(it.obj){it.obj=nullptr;}
        ~NpcItem();

        NpcItem& operator = (NpcItem&& it){
          std::swap(owner,it.owner);
          std::swap(obj,it.obj);
          return *this;
          }

        void  setPosition(const Tempest::Vec3& pos);
        const Tempest::Vec3& position() const;

        void  setRotation(float a);

        void  setScale(const Tempest::Vec3& sz);

        void  debugDraw(DbgPainter& p) const;

        void  setEnable(bool e);
        void  setUserPointer(void* p);

        auto  center()  const -> Tempest::Vec3;
        float centerY() const;
        auto  centerAsym()  const -> Tempest::Vec3;
        float groundOffset() const;
        float radiusXZ() const;

        bool  testMove(const Tempest::Vec3& to, CollisionTest& out);
        bool  testMove(const Tempest::Vec3& to, const Tempest::Vec3& from, CollisionTest& out);
        auto  tryMove (const Tempest::Vec3& to, CollisionTest& out) -> DynamicWorld::MoveCode;

        bool  hasCollision() const;

      private:
        DynamicWorld*       owner  = nullptr;
        NpcBody*            obj    = nullptr;

        auto  implTryMove    (const Tempest::Vec3& dp, const Tempest::Vec3& pos0, CollisionTest& out) -> DynamicWorld::MoveCode;
        void  implSetPosition(const Tempest::Vec3& pos);

      friend class DynamicWorld;
      };

    struct Item {
      public:
        Item()=default;
        Item(DynamicWorld* owner, BodyData* obj):owner(owner),obj(obj){}
        Item(Item&& it):owner(it.owner),obj(it.obj){ it.obj=nullptr; }
        ~Item();

        Item& operator = (Item&& it){
          std::swap(owner,it.owner);
          std::swap(obj,  it.obj);
          return *this;
          }

        void setObjMatrix(const Tempest::Matrix4x4& m);
        void setItem(::Item* it);
        void setInteractive(Interactive* it);
        void setPayloadPtr(void* ptr);
        bool isEmpty() const { return obj==nullptr; }

      private:
        DynamicWorld*       owner  = nullptr;
        BodyData*           obj    = nullptr;
      };

    struct RayLandResult {
      Tempest::Vec3           v={};
      Tempest::Vec3           n={};
      zenkit::MaterialGroup   mat    = zenkit::MaterialGroup::UNDEFINED;
      bool                    hasCol = false;
      float                   hitFraction = 0;

      const char*             sector = nullptr;
      Interactive*            vob    = nullptr;
      void*                   uptr   = nullptr;
      };

    struct RayWaterResult {
      float               wdepth  = 0.f;
      bool                hasCol = false;
      };

    struct RayCamResult {
      int                 waterCol = 0;
      };

    struct RayQueryResult : RayLandResult {
      Npc* npcHit = nullptr;
      };

    struct BulletWorldHit {
      Tempest::Vec3          point = {}; // world centimetres
      Tempest::Vec3          normal = {}; // unit world normal, unchanged winding
      zenkit::MaterialGroup material = zenkit::MaterialGroup::UNDEFINED;
      // Borrowed only for onWorldHit; game code owns the attachment lifetime.
      Ragdoll*              ragdoll = nullptr;
      size_t                bone = size_t(-1);
      Interactive*          vob = nullptr;
      };

    struct BulletCallback {
      virtual ~BulletCallback()=default;
      virtual void onStop(){}
      virtual void onMove(){}
      virtual void onCollide(zenkit::MaterialGroup matId){(void)matId;}
      virtual bool onCollide(Npc& other){(void)other; return true; }
      virtual bool stopsAtWorldHit() const { return false; }
      virtual void onWorldHit(const BulletWorldHit& hit) { onCollide(hit.material); }
      // an arrow aimed by hand: hits an npc where it is (its bones), not its box; from-to: this step
      virtual bool isPrecise() const { return false; }
      virtual bool onCollidePrecise(Npc& other, const Tempest::Vec3& from, const Tempest::Vec3& to){(void)from; (void)to; return onCollide(other); }
      };

    struct BulletBody final {
      public:
        BulletBody(DynamicWorld* wrld, BulletCallback* cb);
        BulletBody(BulletBody&&);

        void  setSpellId(int spl);
        void  setTargetRange(float tgRange);

        void  move(const Tempest::Vec3& to);
        void  setPosition  (const Tempest::Vec3& pos);
        void  setDirection (const Tempest::Vec3& dir);
        float pathLength() const;
        void  addPathLen(float v);

        float               speed()       const { return dirL; }
        float               targetRange() const { return tgRange; }
        Tempest::Vec3       position()    const { return pos; }
        Tempest::Vec3       direction()   const { return dir; }
        Tempest::Matrix4x4  matrix()      const;
        bool                isSpell()     const { return spl!=std::numeric_limits<int>::max(); }
        int                 spellId()     const { return spl; }

        void                addHit() { hitCnt++; }
        int                 hitCount()    const { return hitCnt; }

      private:
        DynamicWorld*       owner = nullptr;
        BulletCallback*     cb    = nullptr;

        Tempest::Vec3       pos={};
        Tempest::Vec3       lastPos={};

        Tempest::Vec3       dir={};
        float               dirL=0.f;
        float               totalL=0.f;
        int                 spl=std::numeric_limits<int>::max();
        int                 hitCnt  = 0;
        float               tgRange = 0;
        bool                worldStopped = false;

      friend class DynamicWorld;
      };

    struct BBoxCallback {
      BBoxCallback() = default;
      virtual ~BBoxCallback()=default;
      virtual void onCollide(BulletBody& other){(void)other;}
      };

    struct BBoxBody final {
      public:
        BBoxBody() = default;
        BBoxBody(DynamicWorld* wrld, BBoxCallback* cb, const zenkit::AxisAlignedBoundingBox& bbox);
        BBoxBody(DynamicWorld* wrld, BBoxCallback* cb, const Tempest::Vec3& pos, float R);
        BBoxBody(BBoxBody&& other);
        ~BBoxBody();

        BBoxBody& operator=(BBoxBody&& other);

      private:
        DynamicWorld*       owner = nullptr;
        BBoxCallback*       cb    = nullptr;

        BBoxShape*          shape = nullptr;

      friend class DynamicWorld;
      };

    RayLandResult  landRay      (const Tempest::Vec3& from, float maxDy=0) const;
    RayLandResult  landSweep    (const Tempest::Vec3& from, float R, float maxDy) const;
    RayWaterResult waterRay     (const Tempest::Vec3& from, float stepHeight) const;
    RayCamResult   cameraRay    (const Tempest::Vec3& from, const Tempest::Vec3& to) const;

    RayLandResult  ray          (const Tempest::Vec3& from, const Tempest::Vec3& to) const;
    RayLandResult  sweep        (const Tempest::Vec3& from, const Tempest::Vec3& to, float R) const;
    RayQueryResult rayNpc       (const Tempest::Vec3& from, const Tempest::Vec3& to, const Npc* except) const;
    // where an arrow aimed by hand would stop flying straight from-to: the first npc 'hit' takes (it says
    // how far along, t), else the ground; v: the point
    RayQueryResult rayArrow     (const Tempest::Vec3& from, const Tempest::Vec3& to,
                                 const std::function<bool(Npc&,const Tempest::Vec3&,const Tempest::Vec3&,float&)>& hit) const;
    float          soundOclusion(const Tempest::Vec3& from, const Tempest::Vec3& to) const;

    NpcItem        addGhostObj  (const Skeleton* src);
    Item           addStaticObj (const PhysicMeshShape *src, const Tempest::Matrix4x4& m);
    Item           addMovableObj(const PhysicMeshShape *src, const Tempest::Matrix4x4& m);
    Item           addDynamicObj(const Tempest::Matrix4x4& pos, const Bounds& bbox, zenkit::MaterialGroup mat);
    std::unique_ptr<Ragdoll> createRagdoll(const Skeleton& skeleton, const HitZones& zones,
                                          const Tempest::Matrix4x4* worldPose, size_t numBones,
                                          const Tempest::Matrix4x4& objectMatrix,
                                          const Tempest::Vec3& initialVelocity = {});
    std::unique_ptr<Ragdoll> createKinematicBody(const Skeleton& skeleton, const HitZones& zones,
                                               const Tempest::Matrix4x4* worldPose, size_t numBones,
                                               const Tempest::Matrix4x4& objectMatrix);

    BulletBody*    bulletObj(BulletCallback* cb);
    BBoxBody       bboxObj(BBoxCallback* cb, const zenkit::AxisAlignedBoundingBox& bbox);
    BBoxBody       bboxObj(BBoxCallback* cb, const Tempest::Vec3& pos, float R);

    void           tick(uint64_t dt);

    struct PhysicsStats {
      float    stepMilliseconds = 0;
      size_t   bodies = 0;
      uint64_t rays = 0;
      };
    PhysicsStats physicsStats() const;

    void           deleteObj(BulletBody* obj);

    static float   materialFriction(zenkit::MaterialGroup mat);
    static float   materialDensity (zenkit::MaterialGroup mat);

    static float   rayBox(const Tempest::Vec3& orig, const Tempest::Vec3& dir, const float TMax,
                          const Tempest::Matrix4x4& obj, const Tempest::Vec3& min, const Tempest::Vec3& max,
                          const float padd = 0.f);
    static float   rayBox(const Tempest::Vec3& orig, const Tempest::Vec3& dir, const float TMax,
                          const Tempest::Vec3& min, const Tempest::Vec3& max,
                          const float padd = 0.f);

    std::string_view validateSectorName(std::string_view name) const;

  private:
    // True when the terminal world-hit branch already delivered onMove.
    bool           moveBullet(BulletBody& b, const Tempest::Vec3& dir, uint64_t dt);
    RayWaterResult implWaterRay(const Tempest::Vec3& from, const Tempest::Vec3& to, float stepHeight) const;
    bool           hasCollision(const NpcItem &it, CollisionTest& out);

    std::unique_ptr<CollisionWorld>    world;

    std::vector<std::string>           sectors;

    std::unique_ptr<PhysicVbo>         landMesh;
    std::unique_ptr<PhysicVbo>         waterMesh;
    std::unique_ptr<WorldBodies>       bodies;

    std::unique_ptr<NpcBodyList>       npcList;
    std::unique_ptr<BulletsList>       bulletList;
    std::unique_ptr<BBoxList>          bboxList;

    static const float                 worldHeight;
  };
