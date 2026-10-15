#include "dynamicworld.h"

#include "collisionworld.h"
#include "physicmeshshape.h"
#include "physicvbo.h"
#include "ragdoll.h"
#include "graphics/mesh/skeleton.h"

#include <Jolt/Physics/Collision/Shape/CapsuleShape.h>
#include <Jolt/Physics/Collision/Shape/SphereShape.h>
#include <Jolt/Physics/Collision/Shape/BoxShape.h>
#include <Jolt/Physics/Collision/RayCast.h>
#include <Jolt/Physics/Collision/CastResult.h>
#include <Jolt/Physics/Collision/CollisionCollectorImpl.h>

#include <algorithm>
#include <cmath>
#include <cassert>
#include <cstddef>
#include <list>

#include "graphics/mesh/submesh/packedmesh.h"
#include "world/objects/item.h"
#include "world/bullet.h"
#include "world/world.h"

#include "utils/dbgpainter.h"

const float DynamicWorld::worldHeight = 20000; //TODO: remove

namespace {
constexpr uint32_t landscapeMask = (1u << DynamicWorld::C_Landscape) | (1u << DynamicWorld::C_Object);
constexpr uint32_t waterMask     = 1u << DynamicWorld::C_Water;

DynamicWorld::RayLandResult toResult(const CollisionWorld::QueryResult& hit, const Tempest::Vec3& to) {
  DynamicWorld::RayLandResult ret = {};
  ret.hasCol      = hit.hasHit;
  ret.v           = to;
  ret.hitFraction = hit.fraction;

  if(!hit.hasHit) {
    ret.v.y = -std::numeric_limits<float>::infinity();
    return ret;
    }

  ret.v      = hit.point;
  ret.mat    = hit.material;
  ret.sector = hit.sector;
  if(hit.body==nullptr)
    return ret;

  if(hit.body->category()==DynamicWorld::C_Landscape)
    ret.n = hit.normal;
  // Object normals stay zero: the movement code relies on this for sliding.
  if(hit.body->ptrType()==DynamicWorld::P_Editor)
    ret.uptr = hit.body->payload();
  else if(hit.body->category()==DynamicWorld::C_Object)
    ret.vob = reinterpret_cast<Interactive*>(hit.body->payload());
  return ret;
  }
}

struct DynamicWorld::BodyData {
  std::unique_ptr<CollisionWorld::CollisionBody> body;
  };

struct DynamicWorld::WorldBodies {
  std::vector<JPH::Vec3>                        vertices;
  std::unique_ptr<CollisionWorld::CollisionBody> landscape;
  std::unique_ptr<CollisionWorld::CollisionBody> water;
  };

struct DynamicWorld::BBoxShape {
  std::unique_ptr<CollisionWorld::CollisionBody> body;
  };

struct DynamicWorld::NpcBody {
  NpcBody(float radius, float height):radius(radius),cylinderHeight(height) {
    if(radius>0.f) {
      if(height>0.f)
        shape = new JPH::CapsuleShape(CollisionWorld::toMeters(height*0.5f), CollisionWorld::toMeters(radius));
      else
        shape = new JPH::SphereShape(CollisionWorld::toMeters(radius));
      }
    }

  JPH::ShapeRefC shape;
  void*         userPointer = nullptr;
  float         radius      = 0.f;
  float         cylinderHeight = 0.f;
  Tempest::Vec3 pos      = {};
  float         h        = 0;
  float         gPadd    = 0.f;
  float         stepSz   = 0.f;
  bool          enable   = true;
  size_t        frozen   = size_t(-1);
  uint64_t      lastMove = 0;

  // Relevant for npc-to-npc collision; Jolt does not move these ellipsoids.
  float         angle    = 0;
  Tempest::Vec3 bboxCen  = {};
  Tempest::Vec3 bboxSize = {};
  float         maxRXZ   = 0;
  float         maxRY    = 0;

  float         maxR     = 0;

  Tempest::Vec3 offsetCenter(float c, float s) const {
    auto off = bboxCen;
    return Tempest::Vec3(off.x*c - off.z*s,
                         off.y,
                         off.x*s + off.z*c);
    }

  float ellipsoidRadius(Tempest::Vec3 dir, float c, float s) const {
    s = -s; // need to cancel object rotation

    Tempest::Vec3 radius = bboxSize;
    dir = Tempest::Vec3(dir.x*c - dir.z*s,
                        dir.y,
                        dir.x*s + dir.z*c);
    return (dir * radius).length();
    }

  Npc* toNpc() {
    return reinterpret_cast<Npc*>(userPointer);
    }

  auto groundOffset() const {
    const float extPadding = 15.f; // Khorinis port hack
    return h*0.5f + gPadd*0.5f + extPadding;
    }

  auto centerPosition() const {
    float padd = groundOffset();
    return Tempest::Vec3(pos.x, pos.y+padd, pos.z);
    }

  void setPosition(const Tempest::Vec3& p) {
    pos = p;
    }
  };

struct DynamicWorld::NpcBodyList final {
  struct Record final {
    NpcBody* body = nullptr;
    float    x    = 0.f;
    };

  NpcBodyList(DynamicWorld& wrld):wrld(wrld){
    body  .reserve(1024);
    frozen.reserve(1024);
    }

  NpcBody* create(const Tempest::Vec3 &min, const Tempest::Vec3 &max) {
    auto  size    = max - min;
    float radius  = std::min(size.y*0.5f, std::min(size.x, size.z))*0.5f; // npc-to-landscape collision size
    float height  = size.y;

    // ghostPadding = 90;
    float ghostPadding = height*0.5f;
    float cHeight      = std::max(height-2.f*radius-ghostPadding, 0.f);

    // The custom movement controller queries this capsule without registering a simulated body.
    NpcBody* obj = new NpcBody(radius, cHeight);

    obj->h      = height;
    obj->gPadd  = ghostPadding;

    obj->bboxCen  = (max+min)*0.5f;
    obj->bboxSize = (max-min)*0.5f;

    // safe tunneling size
    obj->stepSz   = radius;
    obj->stepSz   = std::min(obj->stepSz, std::abs(max.x));
    obj->stepSz   = std::min(obj->stepSz, std::abs(min.x));
    obj->stepSz   = std::min(obj->stepSz, std::abs(max.z));
    obj->stepSz   = std::min(obj->stepSz, std::abs(min.z));

    // searchable radius, where we use logica-position(obj->pos), instead of physical center
    obj->maxRXZ   = std::max(obj->maxRXZ, std::abs(max.x));
    obj->maxRXZ   = std::max(obj->maxRXZ, std::abs(min.x));
    obj->maxRXZ   = std::max(obj->maxRXZ, std::abs(max.z));
    obj->maxRXZ   = std::max(obj->maxRXZ, std::abs(min.z));
    obj->maxRY    = std::max(obj->maxRY,  std::abs(max.y));
    obj->maxRY    = std::max(obj->maxRY,  std::abs(min.y));

    maxRXZ = std::max(maxRXZ, obj->maxRXZ);
    maxRY  = std::max(maxRY,  obj->maxRY);

    obj->maxR = std::max(obj->maxRXZ, obj->maxRY);

    add(obj);
    return obj;
    }

  void add(NpcBody* b){
    Record r;
    r.body = b;
    r.x    = b->pos.x;
    body.push_back(r);
    }

  bool del(void* b) {
    if(del(b,body,false))
      return true;
    if(del(b,frozen,true))
      return true;
    return false;
    }

  bool del(void* b, std::vector<Record>& arr, bool preserveOrder) {
    for(size_t i=0;i<arr.size();++i){
      if(arr[i].body!=b)
        continue;
      if(preserveOrder) {
        arr.erase(arr.begin()+ptrdiff_t(i));
        } else {
        arr[i]=arr.back();
        arr.pop_back();
        }
      return true;
      }
    return false;
    }

  void onMove(NpcBody& n){
    if(n.frozen==size_t(-1)) {
      n.lastMove = tick;
      return;
      }

    auto&       fr = frozen[n.frozen];
    const float x  = fr.x;
    if((n.frozen==0 || frozen[n.frozen-1].x<x) &&
       (n.frozen+1==frozen.size() || x<frozen[n.frozen+1].x)) {
      // still ordered
      fr.x = fr.body->pos.x;
      return;
      }

    fr.body = nullptr;

    Record r;
    r.body = &n;
    body.push_back(r);

    n.lastMove = tick;
    n.frozen   = size_t(-1);
    }

  float raySphereTest(const Tempest::Vec3& origin, const Tempest::Vec3& dir, const Tempest::Vec3& sphere, float R) const {
    auto oc = origin - sphere;

    // Quadratic coefficients for (ray.origin + t*ray.direction - center)^2 = radius^2
    float a = 1.f; // Always 1 if direction is normalized
    float b = 2.0f * Tempest::Vec3::dotProduct(oc, dir);
    float c = Tempest::Vec3::dotProduct(oc,oc) - R*R;

    float discriminant = b * b - 4 * a * c;
    if(discriminant < 0)
      return -1;
    // Find the nearest intersection point (smallest positive t)
    float t0 = (-b - std::sqrt(discriminant)) / (2.0f * a);
    float t1 = (-b + std::sqrt(discriminant)) / (2.0f * a);

    if(t0 > 0)
      return t0;
    if(t1 > 0)
      return t1;
    return -1; // Intersection is behind the ray
    }

  float rayEllipseTest(const Tempest::Vec3& origin, const Tempest::Vec3& dir, const Tempest::Vec3& cen, const Tempest::Vec3& size) const {
    if(size.x<=0.001f || size.y<=0.001f || size.z<=0.001f)
      return -1.f;

    auto  ori = origin - cen;
    auto  oc  = Tempest::Vec3(ori.x/size.x, ori.y/size.y, ori.z/size.z);
    auto  dx  = Tempest::Vec3(dir.x/size.x, dir.y/size.y, dir.z/size.z);
    auto  ndx = Tempest::Vec3::normalize(dx);

    float t   = raySphereTest(oc, ndx, Tempest::Vec3(0.f), 1.f);
    if(t<=0)
      return t;

    auto hit = (ndx*t);
    hit.x *= size.x;
    hit.y *= size.y;
    hit.z *= size.z;

    return hit.length();
    }

  bool rayTest(NpcBody& npc, const Tempest::Vec3& s, const Tempest::Vec3& dir, float tMax, float extR, float& proj) {
    if(!npc.enable)
      return false;

    if(raySphereTest(s, dir, npc.pos, npc.maxR+extR)<0.f)
      return false;

    const float acos = std::cos(npc.angle), asin = std::sin(npc.angle);
    const auto  pos  = npc.pos + npc.offsetCenter(acos, asin);

    static float multiplyer = 2; // to address overly small bboxes
    const float t = rayEllipseTest(s, dir, pos, npc.bboxSize*multiplyer+extR);
    if(t<0.f || t>=tMax)
      return false;
    proj = t;
    return true;
    }

  auto rayTest(const Tempest::Vec3& s, const Tempest::Vec3& e, float extR, const Npc* except) {
    NpcBody* ret     = nullptr;
    auto     dir     = Tempest::Vec3::normalize(e-s);
    auto     tMax    = (e-s).length();
    float    tHit    = tMax;

    for(auto i:body) {
      float proj = 0;
      if(i.body->toNpc()!=except && rayTest(*i.body, s, dir, tMax, extR, proj)) {
        if(proj<tHit) {
          ret  = i.body;
          tHit = proj;
          }
        }
      }
    for(auto i:frozen) {
      float proj = 0;
      if(i.body!=nullptr && i.body->toNpc()!=except && rayTest(*i.body, s, dir, tMax, extR, proj)) {
        if(proj<tHit) {
          ret  = i.body;
          tHit = proj;
          }
        }
      }
    return ret;
    }

  // the segment passes near the npc: within twice its box's reach of the box's middle (its bones may
  // stick out of the box: a head over a short collision box); proj: how far along
  bool nearTest(NpcBody& npc, const Tempest::Vec3& s, const Tempest::Vec3& dir, float tMax, float extR, float& proj) {
    if(!npc.enable)
      return false;
    const float acos = std::cos(npc.angle), asin = std::sin(npc.angle);
    const auto  cen  = npc.pos + npc.offsetCenter(acos, asin);
    const float r    = 2.f*npc.maxR + extR;
    const float t    = std::clamp(Tempest::Vec3::dotProduct(cen-s,dir), 0.f, tMax);
    if((s+dir*t-cen).quadLength()>r*r)
      return false;
    proj = t;
    return true;
    }

  // every npc the segment passes near, the nearest first, until f takes one (it looks at the bones)
  template<class F>
  bool rayTestAll(const Tempest::Vec3& s, const Tempest::Vec3& e, float extR, F f) {
    auto  dir  = Tempest::Vec3::normalize(e-s);
    auto  tMax = (e-s).length();
    std::vector<std::pair<float,NpcBody*>> hits;
    for(auto i:body) {
      float proj = 0;
      if(nearTest(*i.body, s, dir, tMax, extR, proj))
        hits.emplace_back(proj,i.body);
      }
    for(auto i:frozen) {
      float proj = 0;
      if(i.body!=nullptr && nearTest(*i.body, s, dir, tMax, extR, proj))
        hits.emplace_back(proj,i.body);
      }
    std::sort(hits.begin(),hits.end(),[](auto& a, auto& b){ return a.first<b.first; });
    for(auto& i:hits)
      if(f(*i.second->toNpc()))
        return true;
    return false;
    }

  bool hasCollision(const DynamicWorld::NpcItem& obj, Tempest::Vec3& normal, Npc*& npc, float& colDepth) {
    static bool disable=false;
    if(disable)
      return false;

    const NpcBody* pn = dynamic_cast<const NpcBody*>(obj.obj);
    if(pn==nullptr)
      return false;

    const NpcBody& n = *pn;
    if(hasCollision(n,frozen,normal,npc,colDepth,true))
      return true;
    return hasCollision(n,body,normal,npc,colDepth,false);
    }

  bool hasCollision(const NpcBody& n, const std::vector<Record>& arr, Tempest::Vec3& normal, Npc*& npc, float& colDepth, bool sorted) const {
    auto l = arr.begin();
    auto r = arr.end();

    if(sorted) {
      const float dX = maxRXZ + n.maxRXZ;
      l = std::lower_bound(arr.begin(),arr.end(),n.pos.x-dX,[](const Record& b,float x){ return b.x<x; });
      r = std::upper_bound(arr.begin(),arr.end(),n.pos.x+dX,[](float x,const Record& b){ return x<b.x; });
      }

    const auto dist = std::distance(l,r);
    if(dist<=1)
      return false;

    bool ret=false;
    for(;l!=r;++l){
      auto& v = (*l);
      if(v.body!=nullptr && v.body->enable && hasCollision(n,*v.body,normal,colDepth)) {
        npc = v.body->toNpc();
        ret = true;
        }
      }
    return ret;
    }

  bool hasCollision(const NpcBody& a, const NpcBody& b, Tempest::Vec3& normal, float& colDepth) const {
    const float objMaxRXZ = a.maxRXZ + b.maxRXZ;
    const float objMaxRY  = a.maxRY  + b.maxRY;

    if((Tempest::Vec2(a.pos.x,a.pos.z)-Tempest::Vec2(b.pos.x,b.pos.z)).quadLength() > objMaxRXZ*objMaxRXZ)
      return false;

    if(std::abs(a.pos.y-b.pos.y) > objMaxRY)
      return false;

    if(&a==&b)
      return false;

    const float acos = std::cos(a.angle), asin = std::sin(a.angle);
    const float bcos = std::cos(b.angle), bsin = std::sin(b.angle);

    const auto  apos = a.pos + a.offsetCenter(acos, asin);
    const auto  bpos = b.pos + b.offsetCenter(bcos, bsin);
    const auto  dir  = bpos - apos;

    auto dlen = dir.length();
    auto ndir = dlen==0 ? Tempest::Vec3(1,0,0) : Tempest::Vec3::normalize(dir);
    const float rA = a.ellipsoidRadius( ndir, acos, asin);
    const float rB = b.ellipsoidRadius(-ndir, bcos, bsin);

    if(dlen < rA + rB) {
      float depth = (rA + rB - dlen)*0.5f;
      colDepth = std::max(colDepth, depth);
      normal -= ndir*depth;
      return true;
      }
    return false;
    }

  void adjustSort() {
    std::sort(frozen.begin(),frozen.end(),[](Record& a,Record& b){
      return a.x < b.x;
      });
    for(size_t i=0; i<frozen.size(); ++i)
      frozen[i].body->frozen = i;
    }

  void tickAabbs() {
    for(size_t i=0;i<body.size();) {
      if(body[i].body->lastMove!=tick){
        auto b = body[i];
        body[i]=body.back();
        body.pop_back();

        b.x = b.body->pos.x;
        frozen.push_back(b);
        } else {
        ++i;
        }
      }

    for(size_t i=0;i<frozen.size();) {
      if(frozen[i].body==nullptr) {
        frozen[i]=frozen.back();
        frozen.pop_back();
        }
      else if(frozen[i].body->lastMove==tick){
        frozen[i].body->frozen=size_t(-1);
        body.push_back(frozen[i]);
        frozen[i]=frozen.back();
        frozen.pop_back();
        }
      else {
        ++i;
        }
      }

    adjustSort();
    tick++;
    }

  DynamicWorld&         wrld;
  std::vector<Record>   body, frozen;

  uint64_t              tick   = 0;
  float                 maxRXZ = 0;
  float                 maxRY  = 0;
  };

struct DynamicWorld::BulletsList final {
  BulletsList(DynamicWorld& wrld):wrld(wrld){
    }

  BulletBody* add(BulletCallback* cb) {
    BulletBody b(&wrld,cb);
    body.push_front(std::move(b));
    return &body.front();
    }

  void del(BulletBody* b) {
    for(auto i=body.begin(), e=body.end();i!=e;++i){
      if(&(*i)==b) {
        body.erase(i);
        return;
        }
      }
    }

  void tick(uint64_t dt) {
    for(auto& i:body) {
      if(i.worldStopped) continue;
      wrld.moveBullet(i,i.dir,dt);
      if(i.cb!=nullptr)
        i.cb->onMove();
      }
    }

  void onMoveNpc(NpcBody& npc, NpcBodyList& list){
    for(auto& i:body) {
      float proj = 0;
      auto  dp = i.pos - i.lastPos; // move is symetrical: move of npc and move of projectile is the same
      if(i.cb==nullptr || i.worldStopped)
        continue;
      const bool precise = i.cb->isPrecise();
      if(precise ? list.nearTest(npc, i.pos, Tempest::Vec3::normalize(dp), dp.length(), i.tgRange, proj)
                 : list.rayTest (npc, i.pos, Tempest::Vec3::normalize(dp), dp.length(), i.tgRange, proj)) {
        const bool hit = precise ? i.cb->onCollidePrecise(*npc.toNpc(),i.lastPos,i.pos) : i.cb->onCollide(*npc.toNpc());
        if(hit)
          i.cb->onStop();
        }
      }
    }

  std::list<BulletBody> body;
  DynamicWorld&         wrld;
  };

struct DynamicWorld::BBoxList final {
  BBoxList(DynamicWorld& wrld):wrld(wrld){
    }

  void add(BBoxBody* b) {
    body.emplace_back(b);
    }

  void del(BBoxBody* b) {
    for(auto i=body.begin(), e=body.end();i!=e;++i){
      if(*i==b) {
        body.erase(i);
        return;
        }
      }
    }

  BBoxBody* rayTest(const Tempest::Vec3& from, const Tempest::Vec3& to) {
    for(auto* i:body) {
      const auto& obj = *i->shape->body;
      const JPH::RayCast ray(CollisionWorld::toMeters(from-obj.position()), CollisionWorld::toMeters(to-from));
      JPH::RayCastSettings settings;
      settings.mTreatConvexAsSolid = false;
      JPH::ClosestHitCollisionCollector<JPH::CastRayCollector> hits;
      obj.shape()->CastRay(ray,settings,JPH::SubShapeIDCreator(),hits);
      if(hits.HadHit())
        return i;
      }
    return nullptr;
    }

  std::vector<BBoxBody*> body;
  DynamicWorld&          wrld;
  };

DynamicWorld::DynamicWorld(World* owner, const zenkit::Mesh& worldMesh) {
  world.reset(new CollisionWorld());
  bodies.reset(new WorldBodies());

  {
  PackedMesh pkg(worldMesh,PackedMesh::PK_Physic);
  sectors.resize(pkg.subMeshes.size());
  for(size_t i=0;i<sectors.size();++i)
    sectors[i] = pkg.subMeshes[i].material.name;

  bodies->vertices.resize(pkg.vertices.size());
  for(size_t i=0;i<pkg.vertices.size();++i) {
    auto v = pkg.vertices[i];
    bodies->vertices[i] = CollisionWorld::toMeters(Tempest::Vec3(v.pos[0],v.pos[1],v.pos[2]));
    }

  landMesh .reset(new PhysicVbo(&bodies->vertices));
  waterMesh.reset(new PhysicVbo(&bodies->vertices));

  for(size_t i=0;i<pkg.subMeshes.size();++i) {
    auto& sm = pkg.subMeshes[i];
    if(!sm.material.disable_collision && sm.iboLength>0) {
      if(sm.material.group==zenkit::MaterialGroup::WATER) {
        waterMesh->addIndex(pkg.indices,sm.iboOffset,sm.iboLength,sm.material.group);
        } else {
        landMesh ->addIndex(pkg.indices,sm.iboOffset,sm.iboLength,sm.material.group,sectors[i].c_str());
        }
      }
    }
  }

  JPH::Vec3 bbox[2] = {JPH::Vec3::sZero(), JPH::Vec3::sZero()};
  Tempest::Matrix4x4 mt;
  mt.identity();
  if(!landMesh->isEmpty() && landMesh->shape()!=nullptr) {
    bodies->landscape = world->addCollisionBody(landMesh->shape(),mt,materialFriction(zenkit::MaterialGroup::NONE),
                                               C_Landscape,false,false,landMesh.get());
    JPH::Vec3 min, max;
    bodies->landscape->getAabb(min,max);
    bbox[0] = JPH::Vec3::sMin(bbox[0],min);
    bbox[1] = JPH::Vec3::sMax(bbox[1],max);
    }

  if(!waterMesh->isEmpty() && waterMesh->shape()!=nullptr) {
    bodies->water = world->addCollisionBody(waterMesh->shape(),mt,0,C_Water,false,true,waterMesh.get());
    JPH::Vec3 min, max;
    bodies->water->getAabb(min,max);
    bbox[0] = JPH::Vec3::sMin(bbox[0],min);
    bbox[1] = JPH::Vec3::sMax(bbox[1],max);
    }

  world->setBBox(bbox[0],bbox[1]);
  npcList   .reset(new NpcBodyList(*this));
  bulletList.reset(new BulletsList(*this));
  bboxList  .reset(new BBoxList   (*this));

  if(owner!=nullptr) {
    world->setItemHitCallback([owner](::Item& itm, zenkit::MaterialGroup mat, float impulse, float mass) {
      auto  snd = owner->addLandHitEffect(ItemMaterial(itm.handle().material),mat,itm.transform());
      float v   = impulse/mass;
      float vol = snd.volume()*std::min(v/10.f,1.f);
      snd.setVolume(vol);
      snd.play();
      });
    }
  }

DynamicWorld::~DynamicWorld(){
  }

DynamicWorld::RayLandResult DynamicWorld::landRay(const Tempest::Vec3& from, float maxDy) const {
  world->updateAabbs();
  if(maxDy==0)
    maxDy = worldHeight;
  return ray(from, Tempest::Vec3(from.x,from.y-maxDy,from.z));
  }

DynamicWorld::RayLandResult DynamicWorld::landSweep(const Tempest::Vec3& from, float R, float maxDy) const {
  world->updateAabbs();
  if(maxDy==0)
    maxDy = worldHeight;
  return sweep(from, Tempest::Vec3(from.x,from.y-maxDy,from.z), R);
  }

DynamicWorld::RayLandResult DynamicWorld::sweep(const Tempest::Vec3& from, const Tempest::Vec3& to, float R) const {
  return toResult(world->raySweep(from,to,R,landscapeMask,false), to);
  }

DynamicWorld::RayWaterResult DynamicWorld::waterRay(const Tempest::Vec3& from, float stepHeight) const {
  world->updateAabbs();
  return implWaterRay(from, Tempest::Vec3(from.x,from.y+worldHeight,from.z), stepHeight);
  }

DynamicWorld::RayWaterResult DynamicWorld::implWaterRay(const Tempest::Vec3& from, const Tempest::Vec3& to, float stepHeight) const {
  const auto sFrom = from-Tempest::Vec3(0,stepHeight,0);
  const auto hit = world->rayCast(sFrom,to,waterMask,false);

  RayWaterResult ret;
  if(hit.hasHit) {
    const float waterY = hit.point.y;
    const auto  cave   = ray(from,Tempest::Vec3(to.x,std::max(waterY,from.y),to.z));
    if(cave.hasCol && cave.v.y<waterY) {
      ret.wdepth = -std::numeric_limits<float>::infinity();
      ret.hasCol = false;
      } else {
      ret.wdepth = waterY;
      ret.hasCol = true;
      }
    return ret;
    }

  ret.wdepth = -std::numeric_limits<float>::infinity();
  ret.hasCol = false;
  return ret;
  }

DynamicWorld::RayCamResult DynamicWorld::cameraRay(const Tempest::Vec3& from, const Tempest::Vec3& to) const {
  std::vector<CollisionWorld::QueryResult> hits;
  world->rayCast(from,to,waterMask,false,&hits);
  RayCamResult ret;
  ret.waterCol = int(hits.size());
  return ret;
  }

DynamicWorld::RayLandResult DynamicWorld::ray(const Tempest::Vec3& from, const Tempest::Vec3& to) const {
  return toResult(world->rayCast(from,to,landscapeMask,false), to);
  }

DynamicWorld::RayQueryResult DynamicWorld::rayNpc(const Tempest::Vec3& from, const Tempest::Vec3& to, const Npc* except) const {
  RayQueryResult r;
  static_cast<RayLandResult&>(r) = ray(from,to);
  if(auto ptr = npcList->rayTest(from, (r.hasCol ? r.v : to), 1, except)) {
    r.npcHit = ptr->toNpc();
    r.hasCol = true;
    }
  return r;
  }

DynamicWorld::RayQueryResult DynamicWorld::rayArrow(const Tempest::Vec3& from, const Tempest::Vec3& to,
                                                    const std::function<bool(Npc&,const Tempest::Vec3&,const Tempest::Vec3&,float&)>& hit) const {
  RayQueryResult r;
  static_cast<RayLandResult&>(r) = ray(from,to);
  const Tempest::Vec3 end = r.hasCol ? r.v : to;
  npcList->rayTestAll(from,end,0,[&](Npc& npc) {
    float t = 0;
    if(!hit(npc,from,end,t))
      return false;
    r.npcHit = &npc;
    r.hasCol = true;
    r.v      = from + Tempest::Vec3::normalize(end-from)*t;
    return true;
    });
  return r;
  }

float DynamicWorld::soundOclusion(const Tempest::Vec3& from, const Tempest::Vec3& to) const {
  std::vector<CollisionWorld::QueryResult> hits;
  world->rayCast(from,to,landscapeMask | waterMask,true,&hits);
  const size_t count = hits.size();
  if(count<2)
    return 0;
  if(count>=16)
    return 1;

  std::sort(hits.begin(),hits.end(),[](const auto& a, const auto& b) { return a.fraction<b.fraction; });
  const float tlen = CollisionWorld::toMeters((from-to).length());
  float tr = 1.f;
  for(size_t i=1; i<count; i+=2)
    tr *= std::exp(-(hits[i].fraction-hits[i-1].fraction)*tlen/3.5f);
  return std::clamp(1.f-tr, 0.f, 1.f);
  }

DynamicWorld::NpcItem DynamicWorld::addGhostObj(const Skeleton* src) {
  Tempest::Vec3 min={0,0,0}, max={0,0,0};
  if(src != nullptr) {
    min = src->bboxCol[0];
    max = src->bboxCol[1];

    min.y += src->rootTr.y;
    max.y += src->rootTr.y;
    }
  auto obj = npcList->create(min,max);
  return NpcItem(this,obj);
  }

DynamicWorld::Item DynamicWorld::addStaticObj(const PhysicMeshShape* shape, const Tempest::Matrix4x4& m) {
  if(shape==nullptr || shape->shape==nullptr)
    return Item();
  auto obj = std::make_unique<BodyData>();
  obj->body = world->addCollisionBody(shape->shape,m,shape->friction(),C_Object,false,false,&shape->mesh);
  return Item(this,obj.release());
  }

DynamicWorld::Item DynamicWorld::addMovableObj(const PhysicMeshShape* shape, const Tempest::Matrix4x4& m) {
  if(shape==nullptr || shape->shape==nullptr)
    return Item();
  auto obj = std::make_unique<BodyData>();
  obj->body = world->addCollisionBody(shape->shape,m,shape->friction(),C_Object,true,false,&shape->mesh);
  return Item(this,obj.release());
  }

DynamicWorld::Item DynamicWorld::addDynamicObj(const Tempest::Matrix4x4& pos, const Bounds& b, zenkit::MaterialGroup mat) {
  auto extent = CollisionWorld::toMeters(b.bbox[1]-b.bbox[0]);
  const auto* m=pos.data();
  const double volumeScale=std::abs(double(m[0])*(double(m[5])*m[10]-double(m[9])*m[6])-
                                    double(m[4])*(double(m[1])*m[10]-double(m[9])*m[2])+
                                    double(m[8])*(double(m[1])*m[6]-double(m[5])*m[2]));
  // Use the visual bounds' volume, before the minimum collision proxy padding.
  const float mass = float(materialDensity(mat)*double(extent.GetX())*extent.GetY()*extent.GetZ()*volumeScale);
  const auto halfExtent = JPH::Vec3::sMax(extent*0.5f,JPH::Vec3::sReplicate(0.15f));
  JPH::ShapeRefC shape = new JPH::BoxShape(halfExtent);
  auto obj = std::make_unique<BodyData>();
  obj->body = world->addDynamicBody(shape,pos,materialFriction(mat),mass);
  return Item(this,obj.release());
  }

DynamicWorld::BulletBody* DynamicWorld::bulletObj(BulletCallback* cb) {
  return bulletList->add(cb);
  }

DynamicWorld::BBoxBody DynamicWorld::bboxObj(BBoxCallback* cb, const zenkit::AxisAlignedBoundingBox& bbox) {
  return BBoxBody(this,cb,bbox);
  }

DynamicWorld::BBoxBody DynamicWorld::bboxObj(BBoxCallback* cb, const Tempest::Vec3& pos, float R) {
  return BBoxBody(this,cb,pos,R);
  }

void DynamicWorld::moveBullet(BulletBody &b, const Tempest::Vec3& dir, uint64_t dt) {
  const float dtF     = float(dt);
  const bool  isSpell = b.isSpell();

  auto  pos = b.pos;
  auto  to  = pos + dir*dtF - Tempest::Vec3(0,(isSpell ? 0 : 0.5f*gravity*dtF*dtF),0);

  if(auto ptr = bboxList->rayTest(pos,to)) {
    if(ptr->cb!=nullptr)
      ptr->cb->onCollide(b);
    }
  const bool attachAtWorldStop=!isSpell && b.cb!=nullptr && b.cb->stopsAtWorldHit();
  const auto hit = world->rayCast(pos,to,landscapeMask,false);
  const auto matId = hit.hasHit ? hit.material : zenkit::MaterialGroup::NONE;

  // an arrow aimed by hand: the npcs on the way to the wall first (one stands right before it), hit
  // where their bones are
  const bool precise = !isSpell && b.cb!=nullptr && b.cb->isPrecise();
  if(precise) {
    const float a   = matId!=zenkit::MaterialGroup::NONE ? hit.fraction : 1.f;
    const auto  end = pos + (to-pos)*a;
    if(npcList->rayTestAll(pos,end,b.targetRange(),[&](Npc& npc) { return b.cb->onCollidePrecise(npc,pos,end); })) {
      b.cb->onStop();
      return;
      }
    }

  bool stopBullet = false;
  if(matId != zenkit::MaterialGroup::NONE) {
    if(isSpell){
      if(b.cb!=nullptr) {
        BulletWorldHit result;
        result.point=hit.point;
        result.normal=hit.normal;
        result.material=matId;
        result.ragdoll=hit.ragdoll;
        result.bone=hit.bone;
        if(hit.body!=nullptr && hit.body->category()==C_Object && hit.body->ptrType()==P_Interactive)
          result.vob=reinterpret_cast<Interactive*>(hit.body->payload());
        b.cb->onSpellWorldHit(result);
        }
      stopBullet = true;
      } else {
      if(matId==zenkit::MaterialGroup::METAL ||
         matId==zenkit::MaterialGroup::STONE) {
        auto m = b.dir;
        auto n = Tempest::Vec3::normalize(hit.normal);
        const float l = b.speed();
        m/=l;

        auto reflected = m - n*(2.f*Tempest::Vec3::dotProduct(m,n));
        reflected*=(l*0.5f); // slow-down

        float a = hit.fraction;
        b.move(pos + (to-pos)*a);
        if(l*a>0.1f) {
          b.setDirection(reflected);
          b.addPathLen((to-pos).length()*a);
          b.addHit();
          if(b.cb!=nullptr)
            b.cb->onCollide(matId);
          }
        } else {
        float a = hit.fraction;
        b.move(pos + (to-pos)*a);
        b.addPathLen((to-pos).length()*a);
        b.addHit();
        if(b.cb!=nullptr)
          b.cb->onCollide(matId);
        stopBullet = true;
        }
      }
    } else {
    if(precise)
      ; // tested above
    else if(auto ptr = npcList->rayTest(pos,to,b.targetRange(),nullptr)) {
      if(b.cb!=nullptr)
        stopBullet |= b.cb->onCollide(*ptr->toNpc()); else
        stopBullet  = true;
      }
    const float l = b.speed();
    auto        d = b.direction();
    if(!isSpell)
      d.y -= (gravity)*dtF;
    b.move(to);
    b.setDirection(d);
    b.addPathLen(l*dtF);
    }

  if(stopBullet || b.hitCount()>3 || b.pathLength()>10000) {
    if(b.cb!=nullptr) {
      // Pin only after the unchanged flight code has decided to stop at a solid
      // world contact. In particular, stone/metal still reflect before this test.
      if(attachAtWorldStop && matId!=zenkit::MaterialGroup::NONE) {
        BulletWorldHit result;
        result.point=b.position();
        result.normal=hit.normal;
        result.material=matId;
        result.ragdoll=hit.ragdoll;
        result.bone=hit.bone;
        if(hit.body!=nullptr && hit.body->category()==C_Object && hit.body->ptrType()==P_Interactive)
          result.vob=reinterpret_cast<Interactive*>(hit.body->payload());
        b.worldStopped=true;
        b.cb->onWorldHit(result);
        }
      b.cb->onStop();
      }
    }
  }

void DynamicWorld::tick(uint64_t dt) {
  npcList   ->tickAabbs();
  bulletList->tick(dt);
  world     ->tick(dt);
  }

std::unique_ptr<Ragdoll> DynamicWorld::createRagdoll(const Skeleton& skeleton, const HitZones& zones,
                                                   const Tempest::Matrix4x4* worldPose, size_t numBones,
                                                   const Tempest::Matrix4x4& objectMatrix,
                                                   const Tempest::Vec3& initialVelocity) {
  return world->createRagdoll(skeleton,zones,worldPose,numBones,objectMatrix,initialVelocity);
  }

std::unique_ptr<Ragdoll> DynamicWorld::createKinematicBody(const Skeleton& skeleton, const HitZones& zones,
                                                        const Tempest::Matrix4x4* worldPose, size_t numBones,
                                                        const Tempest::Matrix4x4& objectMatrix) {
  return world->createKinematicBody(skeleton,zones,worldPose,numBones,objectMatrix);
  }

DynamicWorld::PhysicsStats DynamicWorld::physicsStats() const {
  return {world->stepMilliseconds(),world->bodyCount(),world->rayCount()};
  }

void DynamicWorld::deleteObj(BulletBody* obj) {
  bulletList->del(obj);
  }

float DynamicWorld::materialFriction(zenkit::MaterialGroup mat) {
  // https://www.thoughtspike.com/friction-coefficients-for-bullet-physics/
  switch(mat) {
    case zenkit::MaterialGroup::UNDEFINED:
      return 0.5f;
    case zenkit::MaterialGroup::METAL:
      return 1.1f;
    case zenkit::MaterialGroup::STONE:
      return 0.65f;
    case zenkit::MaterialGroup::WOOD:
      return 0.4f;
    case zenkit::MaterialGroup::EARTH:
      return 0.4f;
    case zenkit::MaterialGroup::WATER:
      return 0.01f;
    case zenkit::MaterialGroup::SNOW:
      return 0.2f;
    case zenkit::MaterialGroup::NONE:
      break;
    }
  return 0.75f;
  }

float DynamicWorld::materialDensity(zenkit::MaterialGroup mat) {
  switch (mat) {
    case zenkit::MaterialGroup::UNDEFINED:
    case zenkit::MaterialGroup::NONE:
      return 2000.0f;
    case zenkit::MaterialGroup::METAL:
      return 7800.f;
    case zenkit::MaterialGroup::STONE:
      return 2200.f;
    case zenkit::MaterialGroup::WOOD:
      return 700.f;
    case zenkit::MaterialGroup::EARTH:
      return 1500.f;
    case zenkit::MaterialGroup::WATER:
      return 1000.f;
    case zenkit::MaterialGroup::SNOW:
      return 1000.f;
    }
  return 2000.f;
  }

float DynamicWorld::rayBox(const Tempest::Vec3& orig, const Tempest::Vec3& dir, const float TMax,
                           const Tempest::Matrix4x4& obj, const Tempest::Vec3& min, const Tempest::Vec3& max,
                           const float padd) {
  using namespace Tempest;

  auto tmp = obj;
  tmp.inverse();

  auto tOri = orig;
  auto tDir = dir;
  auto zero = 0.f;
  tmp.project(tOri);
  tmp.project(tDir.x, tDir.y, tDir.z, zero);

  float tHit = rayBox(tOri, Vec3(tDir.x, tDir.y, tDir.z), TMax, min, max, padd);
  if(tHit==TMax)
    return TMax;

  //NOTE: worry about non-uniform scale matrix
  tDir *= tHit; zero = 0;
  tmp.project(tDir.x, tDir.y, tDir.z, zero);
  return tDir.length();
  }

float DynamicWorld::rayBox(const Tempest::Vec3& orig, const Tempest::Vec3& dir, const float TMax,
                           const Tempest::Vec3& boxMin, const Tempest::Vec3& boxMax,
                           const float padd) {
  using namespace Tempest;

  Vec3  invDir = Vec3(1.f/dir.x, 1.f/dir.y, 1.f/dir.z);

  Vec3  tMin  = (boxMin - orig)*invDir;
  Vec3  tMax  = (boxMax - orig)*invDir;
  Vec3  t1    = Vec3{std::min(tMin.x, tMax.x), std::min(tMin.y, tMax.y), std::min(tMin.z, tMax.z)};
  Vec3  t2    = Vec3{std::max(tMin.x, tMax.x), std::max(tMin.y, tMax.y), std::max(tMin.z, tMax.z)};

  float tNear = std::max(0.f,  std::max(t1.x, std::max(t1.y, t1.z)));
  float tFar  = std::min(TMax, std::min(t2.x, std::min(t2.y, t2.z)));

  return tNear > tFar ? TMax : std::max(0.f, tNear-padd);
  }

std::string_view DynamicWorld::validateSectorName(std::string_view name) const {
  return landMesh->validateSectorName(name);
  }

bool DynamicWorld::hasCollision(const NpcItem& it, CollisionTest& out) {
  bool ret = false;

  out.normal = Tempest::Vec3();
  if(npcList->hasCollision(it,out.normal,out.npc,out.npcCol)){
    ret = true;
    }
  if(it.obj->shape!=nullptr && world->hasCollision(it.obj->shape.GetPtr(),it.obj->centerPosition(),out.normal,out.vob)) {
    out.landCol = true;
    ret = true;
    }

  if(!ret)
    return false;
  out.normal = Tempest::Vec3::normalize(out.normal);
  return true;
  }

DynamicWorld::NpcItem::~NpcItem() {
  if(owner==nullptr || obj==nullptr)
    return;
  owner->npcList->del(obj);
  delete obj;
  }

void DynamicWorld::NpcItem::setPosition(const Tempest::Vec3& pos) {
  if(obj) {
    implSetPosition(pos);
    owner->npcList->onMove(*obj);
    //NOTE: not very usefull, but can create non-trivial cases, for spells
    // owner->bulletList->onMoveNpc(*obj,*owner->npcList);
    }
  }

void DynamicWorld::NpcItem::implSetPosition(const Tempest::Vec3& pos) {
  obj->setPosition(pos);
  }

void DynamicWorld::NpcItem::setEnable(bool e) {
  if(obj==nullptr || obj->enable==e)
    return;
  obj->enable = e;
  owner->world->touchAabbs();
  }

void DynamicWorld::NpcItem::setUserPointer(void *p) {
  obj->userPointer = p;
  }

auto DynamicWorld::NpcItem::center() const -> Tempest::Vec3 {
  return obj!=nullptr ? obj->centerPosition() : Tempest::Vec3();
  }

float DynamicWorld::NpcItem::centerY() const {
  return center().y;
  }

auto DynamicWorld::NpcItem::centerAsym() const -> Tempest::Vec3 {
  const float acos = std::cos(obj->angle), asin = std::sin(obj->angle);
  return obj->offsetCenter(acos, asin);
  }

float DynamicWorld::NpcItem::groundOffset() const {
  return obj->groundOffset();
  }

float DynamicWorld::NpcItem::radiusXZ() const {
  return obj->maxRXZ;
  }

const Tempest::Vec3& DynamicWorld::NpcItem::position() const {
  return obj->pos;
  }

void DynamicWorld::NpcItem::setRotation(float a) {
  obj->angle = float(a*M_PI)/180.f - float(M_PI_2);
  }

void DynamicWorld::NpcItem::setScale(const Tempest::Vec3&) {
  // NOP for now
  }

void DynamicWorld::NpcItem::debugDraw(DbgPainter& p) const {
  const auto extent = Tempest::Vec3(obj->radius, obj->cylinderHeight*0.5f+obj->radius, obj->radius);
  const auto center = obj->centerPosition();
  p.setPen(Tempest::Color(1,0.5f,0));
  p.drawAabb(center-extent,center+extent);
  /*
  auto tr = Tempest::Matrix4x4::mkIdentity();
  tr.translate(obj->pos);
  tr.rotateOY(-float(obj->angle*180.f/M_PI));

  p.setPen(Tempest::Color(1,0,1));
  p.drawObb(tr, obj->bbox);

  auto& a = *obj;
  const float acos = std::cos(a.angle), asin = std::sin(a.angle);
  const auto apos = a.pos + a.offsetCenter(acos, asin);
  p.drawPoint(apos, 10);
  */
  }

bool DynamicWorld::NpcItem::testMove(const Tempest::Vec3& to, CollisionTest& out) {
  if(!obj)
    return false;
  return testMove(to,obj->pos,out);
  }

bool DynamicWorld::NpcItem::testMove(const Tempest::Vec3& to, const Tempest::Vec3& pos0, CollisionTest& out) {
  if(!obj)
    return false;
  auto prev = obj->pos;
  auto code = implTryMove(to,pos0,out);
  implSetPosition(prev);
  return code==MoveCode::MC_OK;
  }

DynamicWorld::MoveCode DynamicWorld::NpcItem::tryMove(const Tempest::Vec3& to, CollisionTest& out) {
  // 2-santimeters
  static const float eps = 2;

  if(!obj)
    return MoveCode::MC_Fail;

  auto dp = to - obj->pos;
  if(std::abs(dp.x)<eps && std::abs(dp.y)<eps && std::abs(dp.z)<eps) {
    // skip-move
    return MoveCode::MC_Skip;
    }

  auto code = implTryMove(to,obj->pos,out);
  switch(code) {
    case MoveCode::MC_Fail:
    case MoveCode::MC_Skip:
      return code;
    case MoveCode::MC_Partial:
      setPosition(out.partial);
      return MoveCode::MC_Partial;
    case MoveCode::MC_OK:
      owner->npcList->onMove(*obj);
      owner->bulletList->onMoveNpc(*obj,*owner->npcList);
      return MoveCode::MC_OK;
    }
  return code;
  }

DynamicWorld::MoveCode DynamicWorld::NpcItem::implTryMove(const Tempest::Vec3& to, const Tempest::Vec3& pos0, CollisionTest& out) {
  const auto initial = pos0; // avoid self-reference

  auto r       = obj->stepSz;
  int  count   = 1;
  auto dp      = to-initial;

  if((dp.x*dp.x+dp.z*dp.z)>r*r || dp.y>obj->h*0.5f) {
    const int countXZ = int(std::ceil(std::sqrt(dp.x*dp.x+dp.z*dp.z)/r));
    const int countY  = int(std::ceil(std::abs(dp.y)/(obj->h*0.5f)));

    count = std::max(countXZ,countY);
    }

  float skipNpc    = 0;
  bool  skipLnd    = false;
  bool  secondPass = false;
  for(int i=1; i<=count; ++i) {
    const auto pos = initial + dp*(float(i)/float(count));
    implSetPosition(pos);

    if(!owner->hasCollision(*this,out))
      continue;

    if((skipNpc>=out.npcCol) && (!out.landCol || skipLnd))
      continue;

    if(i>1) {
      // moved a bit
      out.partial = initial + dp*(float(i-1)/float(count));
      implSetPosition(out.partial);
      return MoveCode::MC_Partial;
      }

    implSetPosition(initial);
    if(i==1 && !secondPass) {
      // maybe we were stuck into something(npc) from the start?
      CollisionTest tmpOut = {};
      if(!owner->hasCollision(*this,tmpOut)) {
        return MoveCode::MC_Fail;
        }
      skipNpc    = tmpOut.npcCol;
      skipLnd    = tmpOut.landCol;
      secondPass = true;
      i          = 0;
      continue;
      }

    return MoveCode::MC_Fail;
    }
  return MoveCode::MC_OK;
  }

bool DynamicWorld::NpcItem::hasCollision() const {
  if(!obj)
    return false;
  CollisionTest info;
  return owner->hasCollision(*this,info);
  }

DynamicWorld::Item::~Item() {
  delete obj;
  }

void DynamicWorld::Item::setObjMatrix(const Tempest::Matrix4x4& m) {
  if(obj!=nullptr)
    obj->body->setObjMatrix(m);
  }

void DynamicWorld::Item::setItem(::Item* it) {
  assert(obj->body->category()==DynamicWorld::C_Item);
  obj->body->setPayload(it,P_Item);
  }

void DynamicWorld::Item::setInteractive(Interactive* it) {
  assert(obj->body->category()==DynamicWorld::C_Object);
  obj->body->setPayload(it,P_Interactive);
  }

void DynamicWorld::Item::setPayloadPtr(void* ptr) {
  if(obj!=nullptr)
    obj->body->setPayload(ptr,P_Editor);
  }

float DynamicWorld::Item::mass() const {
  return obj!=nullptr ? obj->body->mass() : 0.f;
  }

Tempest::Vec3 DynamicWorld::Item::velocity() const {
  return obj!=nullptr ? obj->body->velocity() : Tempest::Vec3{};
  }

bool DynamicWorld::Item::isSleeping() const {
  return obj==nullptr || obj->body->isSleeping();
  }

bool DynamicWorld::Item::addImpulse(const Tempest::Vec3& impulseNs) {
  return obj!=nullptr && obj->body->addImpulse(impulseNs);
  }

DynamicWorld::BulletBody::BulletBody(DynamicWorld* wrld, DynamicWorld::BulletCallback* cb)
  :owner(wrld), cb(cb) {
  }

DynamicWorld::BulletBody::BulletBody(DynamicWorld::BulletBody&& other)
  : pos(other.pos), lastPos(other.lastPos),
    dir(other.dir), dirL(other.dirL), totalL(other.totalL), spl(other.spl), worldStopped(other.worldStopped){
  std::swap(owner,other.owner);
  std::swap(cb,other.cb);
  }

void DynamicWorld::BulletBody::setSpellId(int s) {
  spl = s;
  }

void DynamicWorld::BulletBody::setTargetRange(float t) {
  tgRange = t;
  }

void DynamicWorld::BulletBody::move(const Tempest::Vec3& to) {
  lastPos = pos;
  pos     = to;
  }

void DynamicWorld::BulletBody::setPosition(const Tempest::Vec3& p) {
  lastPos = p;
  pos     = p;
  }

void DynamicWorld::BulletBody::setDirection(const Tempest::Vec3& d) {
  dir  = d;
  dirL = d.length();
  }

float DynamicWorld::BulletBody::pathLength() const {
  return totalL;
  }

void DynamicWorld::BulletBody::addPathLen(float v) {
  totalL += v;
  }

Tempest::Matrix4x4 DynamicWorld::BulletBody::matrix() const {
  const float dx = dir.x/dirL;
  const float dy = dir.y/dirL;
  const float dz = dir.z/dirL;

  float a2  = std::asin(dy)*float(180/M_PI);
  float ang = std::atan2(dz,dx)*float(180/M_PI)+180.f;

  Tempest::Matrix4x4 mat;
  mat.identity();
  mat.translate(pos.x,pos.y,pos.z);
  mat.rotateOY(-ang);
  mat.rotateOZ(-a2);
  return mat;
  }

DynamicWorld::BBoxBody::BBoxBody(DynamicWorld* ow, BBoxCallback* cb, const zenkit::AxisAlignedBoundingBox& bbox)
  :owner(ow),cb(cb) {
  const auto extent = Tempest::Vec3(bbox.max.x-bbox.min.x,bbox.max.y-bbox.min.y,bbox.max.z-bbox.min.z)*0.5f;
  const auto center = Tempest::Vec3(bbox.max.x+bbox.min.x,bbox.max.y+bbox.min.y,bbox.max.z+bbox.min.z)*0.5f;
  const auto halfExtent = JPH::Vec3::sMax(CollisionWorld::toMeters(extent),JPH::Vec3::sReplicate(0.0001f));
  JPH::ShapeRefC box = new JPH::BoxShape(halfExtent,0.f);
  Tempest::Matrix4x4 m;
  m.identity();
  m.translate(center);
  auto data = std::make_unique<BBoxShape>();
  data->body = owner->world->addCollisionBody(box,m,0,C_Ghost,false,true);
  shape = data.release();
  owner->bboxList->add(this);
  }

DynamicWorld::BBoxBody::BBoxBody(DynamicWorld* ow, BBoxCallback* cb, const Tempest::Vec3& pos, float R)
  :owner(ow),cb(cb) {
  // This overload historically takes its radius in meters and its cylinder height is four meters.
  JPH::ShapeRefC capsule = new JPH::CapsuleShape(2.f,std::max(R,0.0001f));
  Tempest::Matrix4x4 m;
  m.identity();
  m.translate(pos);
  auto data = std::make_unique<BBoxShape>();
  data->body = owner->world->addCollisionBody(capsule,m,0,C_Ghost,false,true);
  shape = data.release();
  owner->bboxList->add(this);
  }

DynamicWorld::BBoxBody::BBoxBody(BBoxBody&& other)
  :owner(other.owner),cb(other.cb),shape(other.shape) {
  other.owner = nullptr;
  other.cb    = nullptr;
  other.shape = nullptr;

  if(owner!=nullptr) {
    owner->bboxList->del(&other);
    owner->bboxList->add(this);
    }
  }

DynamicWorld::BBoxBody& DynamicWorld::BBoxBody::operator=(BBoxBody&& other) {
  if(other.owner!=nullptr)
    other.owner->bboxList->del(&other);
  if(owner!=nullptr)
    owner->bboxList->del(this);

  std::swap(owner,other.owner);
  std::swap(cb,   other.cb);
  std::swap(shape,other.shape);

  if(other.owner!=nullptr)
    other.owner->bboxList->add(&other);
  if(owner!=nullptr)
    owner->bboxList->add(this);

  return *this;
  }

DynamicWorld::BBoxBody::~BBoxBody() {
  if(owner==nullptr)
    return;
  owner->bboxList->del(this);
  delete shape;
  }
