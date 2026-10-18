#include "cloth.h"
#include "collisionworld.h"
#include "dynamicworld.h"

#include <Jolt/Physics/PhysicsSystem.h>
#include <Jolt/Physics/Body/BodyLock.h>
#include <Jolt/Physics/SoftBody/SoftBodyCreationSettings.h>
#include <Jolt/Physics/SoftBody/SoftBodyContactListener.h>
#include <Jolt/Physics/SoftBody/SoftBodyManifold.h>
#include <Jolt/Physics/SoftBody/SoftBodyMotionProperties.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <map>
#include <set>
#include <string_view>
#include <tuple>

namespace {
  Tempest::Vec3 project(const Tempest::Matrix4x4& matrix, Tempest::Vec3 point) {
    matrix.project(point);
    return point;
    }
  bool finite(const Tempest::Vec3& point) {
    return std::isfinite(point.x) && std::isfinite(point.y) && std::isfinite(point.z);
    }
  bool sameVisual(std::string_view a,std::string_view b) {
    const auto basename=[](std::string_view value) {
      const auto slash=value.find_last_of("/\\");
      if(slash!=std::string_view::npos) value.remove_prefix(slash+1);
      return value.substr(0,value.find_last_of('.'));
      };
    a=basename(a);
    b=basename(b);
    if(a.empty() || a.size()!=b.size()) return false;
    for(size_t i=0;i<a.size();++i) {
      const auto upper=[](char c) { return c>='a' && c<='z' ? char(c-'a'+'A') : c; };
      if(upper(a[i])!=upper(b[i])) return false;
      }
    return true;
    }
  bool segmentTriangle(const Tempest::Vec3& from, const Tempest::Vec3& to,
                       const Tempest::Vec3& a, const Tempest::Vec3& b, const Tempest::Vec3& c) {
    const auto dir=to-from, e1=b-a, e2=c-a;
    const auto p=Tempest::Vec3::crossProduct(dir,e2);
    const float det=Tempest::Vec3::dotProduct(e1,p);
    if(std::abs(det)<1.e-6f) return false;
    const auto t=from-a;
    const float u=Tempest::Vec3::dotProduct(t,p)/det;
    if(u<0.f || u>1.f) return false;
    const auto q=Tempest::Vec3::crossProduct(t,e1);
    const float v=Tempest::Vec3::dotProduct(dir,q)/det;
    if(v<0.f || u+v>1.f) return false;
    const float distance=Tempest::Vec3::dotProduct(e2,q)/det;
    return distance>=0.f && distance<=1.f;
    }
  JPH::Vec3 limited(JPH::Vec3Arg velocity) {
    if(velocity.IsNaN()) return JPH::Vec3::sZero();
    const float speed=velocity.Length();
    if(!std::isfinite(speed)) return JPH::Vec3::sZero();
    return speed>Cloth::maxVelocityMS ? velocity*(Cloth::maxVelocityMS/speed) : velocity;
    }
  }

struct Cloth::Instance::Impl {
  struct Contact {
    uint32_t vertex = 0;
    JPH::BodyID body;
    Tempest::Vec3 normal;
    };
  Cloth::Impl* owner = nullptr;
  JPH::BodyID body;
  Tempest::Matrix4x4 matrix, inverse;
  Tempest::Vec3 localCenter;
  std::vector<Tempest::Vec3> source, simulated, rendered;
  std::vector<uint32_t> mapping, indices;
  std::vector<JPH::Vec3> rest;
  std::vector<bool> pinned;
  JPH::Ref<JPH::SoftBodySharedSettings> settings;
  ShaderReference shader;
  UpdateCallback update;
  std::string visual;
  uint32_t zenId = uint32_t(-1);
  float stiffness = .8f, weight = 0, targetWeight = 0, shelterTimer = 0;
  float maxAnchorError = 0, maxSpeed = 0, maxSpeedEver = 0, transitionMaxDelta = 0;
  std::vector<Contact> contacts;
  size_t contactCount = 0;
  uint32_t maxSpeedVertex = uint32_t(-1), maxSpeedEverVertex = uint32_t(-1);
  Tempest::Vec3 maxSpeedSource, maxSpeedWorld, maxSpeedContactNormal;
  Tempest::Vec3 maxSpeedEverSource, maxSpeedEverWorld, maxSpeedEverContactNormal;
  bool maxSpeedContact = false, maxSpeedEverContact = false;
  JPH::BodyID maxSpeedContactBody, maxSpeedEverContactBody;
  std::vector<WindOccluder> windOccluders;
  uint64_t projectileHits = 0, arrowPasses = 0, recoveryCount = 0;
  bool exposed = true, recover = false;

  Tempest::Vec3 center() const { return project(matrix,localCenter); }
  Tempest::Vec3 translation() const { return Tempest::Vec3(matrix.at(3,0),matrix.at(3,1),matrix.at(3,2)); }
  Tempest::Vec3 reference(size_t vertex) const {
    if(!shader) return source[vertex];
    auto ret=shader(uint32_t(vertex),source[vertex],project(matrix,source[vertex]));
    return finite(ret) ? ret : source[vertex];
    }
  std::array<Tempest::Vec3,3> exposureOrigins() const {
    double meanX=0,meanZ=0;
    for(const auto& vertex:source) {
      const auto p=project(matrix,vertex);
      meanX+=p.x;
      meanZ+=p.z;
      }
    meanX/=double(source.size());
    meanZ/=double(source.size());
    double xx=0,xz=0,zz=0;
    for(const auto& vertex:source) {
      const auto p=project(matrix,vertex);
      const double x=double(p.x)-meanX,z=double(p.z)-meanZ;
      xx+=x*x;
      xz+=x*z;
      zz+=z*z;
      }
    // In the horizontal plane, the minor axis is normal to a hanging sheet
    // and across a furled sail. Triangle normals on a wrapped sail can instead
    // point along its yard, keeping every fixed-size probe under the wood.
    const double angle=.5*std::atan2(2*xz,xx-zz);
    const Tempest::Vec3 minor(float(-std::sin(angle)),0,float(std::cos(angle)));
    const auto middle=center();
    float lo=std::numeric_limits<float>::max(),hi=-lo;
    for(const auto& vertex:source) {
      const float extent=Tempest::Vec3::dotProduct(project(matrix,vertex)-middle,minor);
      lo=std::min(lo,extent);
      hi=std::max(hi,extent);
      }
    const auto origin=middle+minor*((lo+hi)*.5f);
    const auto offset=minor*std::max(25.f,(hi-lo)*.5f+2.f);
    return {origin-offset,origin,origin+offset};
    }
  bool ownAttachment(const CollisionWorld::CollisionBody* collider) const {
    return collider!=nullptr && sameVisual(visual,collider->label()) &&
           (collider->position()-translation()).quadLength()<=1.f;
    }
  };

struct Cloth::Impl : JPH::SoftBodyContactListener {
  CollisionWorld& world;
  std::vector<std::weak_ptr<Instance>> instances;
  Tempest::Vec3 observer, wind;
  std::function<bool(const Tempest::Vec3&)> exposure;
  bool enabled = true, hasObserver = false;
  float windScale = 1, currentWindScale = 1;
  explicit Impl(CollisionWorld& world):world(world) {}

  auto& system() { return world.physicsSystem(); }
  void OnSoftBodyContactAdded(const JPH::Body& native,const JPH::SoftBodyManifold& manifold) override {
    if(native.GetObjectLayer()!=DynamicWorld::C_Cloth || native.GetUserData()==0) return;
    auto& body=*reinterpret_cast<Instance::Impl*>(native.GetUserData());
    body.contacts.clear();
    const auto& vertices=manifold.GetVertices();
    const auto transform=native.GetCenterOfMassTransform();
    // Jolt resets mHasContact and its plane immediately after this callback.
    // Keep the native identity and normal until the main-thread finishStep.
    for(uint32_t i=0;i<vertices.size();++i) {
      if(!manifold.HasContact(vertices[i])) continue;
      const auto normal=transform.Multiply3x3(vertices[i].mCollisionPlane.GetNormal());
      body.contacts.push_back({i,manifold.GetContactBodyID(vertices[i]),
                                Tempest::Vec3(normal.GetX(),normal.GetY(),normal.GetZ())});
      }
    }
  void remove(Instance::Impl& body) {
    if(body.body.IsInvalid()) return;
    auto& native=system().GetBodyInterface();
    native.RemoveBody(body.body);
    native.DestroyBody(body.body);
    body.body=JPH::BodyID();
    body.contactCount=0;
    body.contacts.clear();
    }
  void create(Instance::Impl& body) {
    if(!body.body.IsInvalid()) return;
    body.settings=new JPH::SoftBodySharedSettings();
    auto& settings=*body.settings;
    body.rest.clear();
    body.rest.resize(body.pinned.size());
    for(size_t i=0;i<body.source.size();++i) {
      const auto position=CollisionWorld::toMeters(project(body.matrix,body.source[i])-body.translation());
      body.rest[body.mapping[i]]=position;
      }
    std::vector<float> vertexArea(body.rest.size(),0.f);
    std::set<std::array<uint32_t,3>> seen;
    for(size_t i=0;i+2<body.indices.size();i+=3) {
      std::array<uint32_t,3> face={body.mapping[body.indices[i]],body.mapping[body.indices[i+1]],body.mapping[body.indices[i+2]]};
      auto key=face;
      std::sort(key.begin(),key.end());
      if(key[0]==key[1] || key[1]==key[2] || !seen.insert(key).second) continue;
      const float twiceArea=(body.rest[face[1]]-body.rest[face[0]]).Cross(body.rest[face[2]]-body.rest[face[0]]).Length();
      if(twiceArea<1.e-6f) continue;
      settings.mFaces.emplace_back(face[0],face[1],face[2]);
      for(auto vertex:face) vertexArea[vertex]+=twiceArea/6.f;
      }
    if(settings.mFaces.empty()) return;
    // Each triangle contributes one third of its area to its particles. UV
    // seams and reversed back faces were welded above, so subdivision changes
    // resolution rather than the weight of the same piece of cloth.
    constexpr float densityKgPerSquareMeter=.35f;
    settings.mVertices.reserve(body.rest.size());
    for(size_t i=0;i<body.rest.size();++i) {
      JPH::Float3 point;
      body.rest[i].StoreFloat3(&point);
      const float inverseMass=1.f/std::max(vertexArea[i]*densityKgPerSquareMeter,.00025f);
      settings.mVertices.emplace_back(point,JPH::Float3(0,0,0),body.pinned[i] ? 0.f : inverseMass);
      }
    const float compliance=1.e-6f+(1.f-body.stiffness)*2.e-5f;
    const JPH::SoftBodySharedSettings::VertexAttributes attributes(compliance,compliance,compliance*10.f,
        JPH::SoftBodySharedSettings::ELRAType::GeodesicDistance,1.03f);
    settings.CreateConstraints(&attributes,1,JPH::SoftBodySharedSettings::EBendType::Distance);
    settings.Optimize();
    JPH::SoftBodyCreationSettings creation(&settings,CollisionWorld::toMeters(body.translation()),JPH::Quat::sIdentity(),DynamicWorld::C_Cloth);
    creation.mUserData=reinterpret_cast<uint64_t>(&body);
    // Fixed particles and geodesic long-range attachments bound the cloth;
    // four XPBD iterations keep nearby groups affordable at the 60 Hz step.
    creation.mNumIterations=4;
    creation.mLinearDamping=3.f;
    creation.mMaxLinearVelocity=Cloth::maxVelocityMS;
    creation.mVertexRadius=.005f;
    creation.mUpdatePosition=false;
    creation.mAllowSleeping=false;
    creation.mFacesDoubleSided=true;
    auto* native=system().GetBodyInterface().CreateSoftBody(creation);
    if(native==nullptr) return;
    body.body=native->GetID();
    // Keep constraints authored at rest. Seeding positions never changes their
    // rest lengths, and pins always start at their real world attachments.
    auto& motion=*static_cast<JPH::SoftBodyMotionProperties*>(native->GetMotionProperties());
    for(size_t i=0;i<body.source.size();++i) {
      const auto vertex=body.mapping[i];
      if(body.pinned[vertex]) continue;
      auto& particle=motion.GetVertex(vertex);
      particle.mPosition=CollisionWorld::toMeters(project(body.matrix,body.reference(i))-body.translation());
      particle.mPreviousPosition=particle.mPosition;
      }
    system().GetBodyInterface().AddBody(body.body,JPH::EActivation::Activate);
    }

  template<class F> void each(F&& callback) {
    for(auto it=instances.begin();it!=instances.end();) {
      if(auto instance=it->lock()) {
        callback(*instance->impl);
        ++it;
        } else it=instances.erase(it);
      }
    }
  };

Cloth::Instance::Instance(std::unique_ptr<Impl> impl):impl(std::move(impl)) {}
Cloth::Instance::~Instance() { if(impl->owner!=nullptr) impl->owner->remove(*impl); }
void Cloth::Instance::setMatrix(const Tempest::Matrix4x4& matrix) {
  if(matrix==impl->matrix) return;
  if(impl->owner!=nullptr) impl->owner->remove(*impl);
  impl->matrix=matrix;
  impl->inverse=matrix;
  impl->inverse.inverse();
  impl->weight=0;
  impl->shelterTimer=0;
  }
void Cloth::Instance::setShaderReference(ShaderReference reference) { impl->shader=std::move(reference); }
void Cloth::Instance::setUpdateCallback(UpdateCallback callback) { impl->update=std::move(callback); }
const std::vector<Tempest::Vec3>& Cloth::Instance::positionsCm() const { return impl->rendered; }
float Cloth::Instance::simulationWeight() const { return impl->weight; }
bool Cloth::Instance::active() const { return !impl->body.IsInvalid(); }

Cloth::Cloth(CollisionWorld& world):impl(std::make_unique<Impl>(world)) {
  world.cloth=this;
  impl->system().SetSoftBodyContactListener(impl.get());
  }
Cloth::~Cloth() {
  impl->each([&](Instance::Impl& body) { impl->remove(body); body.owner=nullptr; });
  impl->system().SetSoftBodyContactListener(nullptr);
  impl->world.cloth=nullptr;
  }
std::shared_ptr<Cloth::Instance> Cloth::add(const std::vector<Tempest::Vec3>& source,
                                         const std::vector<uint32_t>& indices,
                                         const std::vector<uint32_t>& pins,
                                         const Tempest::Matrix4x4& matrix,float stiffness,
                                         std::string visual,uint32_t zenId) {
  if(source.empty() || indices.empty() || pins.empty() || indices.size()%3!=0) return {};
  if(std::any_of(source.begin(),source.end(),[](const auto& v) { return !finite(v); })) return {};
  for(auto index:indices) if(index>=source.size()) return {};
  for(auto index:pins) if(index>=source.size()) return {};
  auto body=std::make_unique<Instance::Impl>();
  body->owner=impl.get();
  body->source=body->simulated=body->rendered=source;
  body->indices=indices;
  body->matrix=body->inverse=matrix;
  body->inverse.inverse();
  body->stiffness=std::clamp(stiffness,0.f,1.f);
  body->visual=std::move(visual);
  body->zenId=zenId;
  std::map<std::tuple<int64_t,int64_t,int64_t>,uint32_t> welded;
  auto min=source.front(),max=source.front();
  for(const auto& vertex:source) {
    // 0.01 cm: weld UV seams and the reversed back faces of Gothic cloth.
    const auto key=std::make_tuple(int64_t(std::llround(vertex.x*100)),int64_t(std::llround(vertex.y*100)),int64_t(std::llround(vertex.z*100)));
    const auto result=welded.emplace(key,uint32_t(welded.size()));
    body->mapping.push_back(result.first->second);
    min.x=std::min(min.x,vertex.x); min.y=std::min(min.y,vertex.y); min.z=std::min(min.z,vertex.z);
    max.x=std::max(max.x,vertex.x); max.y=std::max(max.y,vertex.y); max.z=std::max(max.z,vertex.z);
    }
  body->localCenter=(min+max)*.5f;
  body->pinned.resize(welded.size(),false);
  for(auto index:pins) body->pinned[body->mapping[index]]=true;
  if(std::all_of(body->pinned.begin(),body->pinned.end(),[](bool pin) { return pin; })) return {};
  auto ret=std::shared_ptr<Instance>(new Instance(std::move(body)));
  impl->instances.push_back(ret);
  return ret;
  }
void Cloth::setEnabled(bool value) {
  impl->enabled=value;
  // The static world-cloth fallback switches in this same call stack. Update
  // the individual views now so a render between physics ticks has no gap.
  finishFrame();
  }
bool Cloth::enabled() const { return impl->enabled; }
void Cloth::setObserver(const Tempest::Vec3& position) { if(finite(position)) { impl->observer=position; impl->hasObserver=true; } }
void Cloth::setWind(const Tempest::Vec3& wind) { impl->wind=finite(wind) ? wind : Tempest::Vec3(); }
void Cloth::setWindScale(float value) { if(std::isfinite(value)) impl->windScale=std::clamp(value,0.f,4.f); }
float Cloth::windScale() const { return impl->currentWindScale; }
void Cloth::setExposureTest(std::function<bool(const Tempest::Vec3&)> test) { impl->exposure=std::move(test); }
size_t Cloth::bodyCount() const {
  size_t count=0;
  for(const auto& weak:impl->instances) if(auto instance=weak.lock()) if(instance->active()) ++count;
  return count;
  }

void Cloth::prepareFrame(uint64_t dt) {
  const float seconds=std::min(float(dt)*.001f,.1f);
  impl->currentWindScale+=(impl->windScale-impl->currentWindScale)*(1.f-std::exp(-seconds*1.5f));
  impl->each([&](Instance::Impl& body) {
    if(body.recover) {
      impl->remove(body);
      body.recover=false;
      body.weight=0;
      }
    const auto center=body.center();
    const float distance=impl->hasObserver ? (center-impl->observer).length() : radiusCm+1.f;
    float target=impl->enabled ? std::clamp((radiusCm-distance)/(radiusCm-blendStartCm),0.f,1.f) : 0.f;
    body.targetWeight=target*target*(3.f-2.f*target);
    body.shelterTimer-=seconds;
    if(body.targetWeight>0.f && body.shelterTimer<=0.f) {
      constexpr uint32_t mask=(1u<<DynamicWorld::C_Landscape)|(1u<<DynamicWorld::C_Object);
      body.exposed=false;
      body.windOccluders.clear();
      if(!impl->exposure || impl->exposure(center)) {
        // A mast or the narrow bar holding a banner is not a roof. Probe both
        // sides of the cloth as well as its center; a ceiling covers all three.
        for(const auto& origin:body.exposureOrigins()) {
          std::vector<const CollisionWorld::CollisionBody*> ignored;
          for(;;) {
            const auto roof=impl->world.rayCast(origin+Tempest::Vec3(0,2,0),origin+Tempest::Vec3(0,20000,0),mask,true,
                                               nullptr,&ignored);
            if(!roof.hasHit) { body.exposed=true; break; }
            if(!body.ownAttachment(roof.body)) {
              body.windOccluders.push_back({roof.body!=nullptr ? roof.body->label() : std::string(),
                                            roof.point,(roof.point-origin).length()});
              break;
              }
            // A standard's skull and crossbar can span all three probes.
            // Ignore only the rigid part at this cloth's authored transform,
            // then keep tracing so a real roof above it still shelters cloth.
            ignored.push_back(roof.body);
            }
          if(body.exposed) break;
          }
        }
      body.shelterTimer=.5f;
      }
    if(body.targetWeight>0.f && body.body.IsInvalid()) impl->create(body);
    const float oldWeight=body.weight;
    if(body.body.IsInvalid()) body.weight=0;
    else body.weight+=std::clamp(body.targetWeight-body.weight,-seconds*2.f,seconds*2.f);
    const float weightChange=std::abs(oldWeight-body.weight);
    if(weightChange>0.f) {
      for(size_t i=0;i<body.source.size();++i)
        body.transitionMaxDelta=std::max(body.transitionMaxDelta,(body.simulated[i]-body.reference(i)).length()*weightChange);
      }
    if(body.weight<=0.f && body.targetWeight<=0.f) impl->remove(body);
    });
  }
void Cloth::prepareStep(float seconds) {
  impl->each([&](Instance::Impl& body) {
    if(body.body.IsInvalid()) return;
    body.contacts.clear();
    JPH::BodyLockWrite lock(impl->system().GetBodyLockInterface(),body.body);
    if(!lock.Succeeded()) return;
    auto& motion=*static_cast<JPH::SoftBodyMotionProperties*>(lock.GetBody().GetMotionProperties());
    const auto wind=body.exposed ? CollisionWorld::toMeters(impl->wind*100.f*impl->currentWindScale) : JPH::Vec3::sZero();
    for(auto& vertex:motion.GetVertices()) {
      if(vertex.mInvMass==0.f) { vertex.mVelocity=JPH::Vec3::sZero(); continue; }
      // Aerodynamic drag gives a bounded, gradual response to gusts. Native
      // damping remains active in calm weather to settle hanging cloth.
      vertex.mVelocity=limited(vertex.mVelocity+(wind-vertex.mVelocity)*(.8f*seconds));
      }
    });
  }
void Cloth::finishStep() {
  impl->each([&](Instance::Impl& body) {
    if(body.body.IsInvalid()) { body.maxSpeed=0; return; }
    JPH::BodyLockRead lock(impl->system().GetBodyLockInterface(),body.body);
    if(!lock.Succeeded()) return;
    const auto& native=lock.GetBody();
    const auto& motion=*static_cast<const JPH::SoftBodyMotionProperties*>(native.GetMotionProperties());
    body.maxSpeed=0;
    body.maxSpeedVertex=uint32_t(-1);
    body.maxSpeedContact=false;
    body.maxSpeedContactBody=JPH::BodyID();
    body.maxSpeedContactNormal=Tempest::Vec3();
    body.contactCount=body.contacts.size();
    bool invalid=false;
    for(size_t i=0;i<motion.GetVertices().size();++i) {
      const auto& vertex=motion.GetVertex(uint32_t(i));
      const float speed=vertex.mVelocity.Length()*100.f;
      invalid|=!std::isfinite(speed) || vertex.mPosition.IsNaN() || (vertex.mPosition-body.rest[i]).LengthSq()>10000.f;
      if(std::isfinite(speed) && speed>body.maxSpeed) {
        body.maxSpeed=speed;
        body.maxSpeedVertex=uint32_t(i);
        }
      if(body.pinned[i]) body.maxAnchorError=std::max(body.maxAnchorError,(vertex.mPosition-body.rest[i]).Length()*100.f);
      }
    if(body.maxSpeedVertex!=uint32_t(-1)) {
      const auto& vertex=motion.GetVertex(body.maxSpeedVertex);
      const auto transform=native.GetCenterOfMassTransform();
      body.maxSpeedSource=project(body.inverse,body.translation()+CollisionWorld::toCentimeters(body.rest[body.maxSpeedVertex]));
      body.maxSpeedWorld=CollisionWorld::toCentimeters(JPH::Vec3(transform*vertex.mPosition));
      for(const auto& contact:body.contacts)
        if(contact.vertex==body.maxSpeedVertex) {
          body.maxSpeedContact=true;
          body.maxSpeedContactBody=contact.body;
          body.maxSpeedContactNormal=contact.normal;
          break;
          }
      }
    if(body.maxSpeed>body.maxSpeedEver) {
      body.maxSpeedEver=body.maxSpeed;
      body.maxSpeedEverVertex=body.maxSpeedVertex;
      body.maxSpeedEverSource=body.maxSpeedSource;
      body.maxSpeedEverWorld=body.maxSpeedWorld;
      body.maxSpeedEverContact=body.maxSpeedContact;
      body.maxSpeedEverContactNormal=body.maxSpeedContactNormal;
      body.maxSpeedEverContactBody=body.maxSpeedContactBody;
      }
    if(invalid) {
      // Recover on the next main-thread frame by removing the native body.
      // No unchecked vertex ever reaches the renderer.
      ++body.recoveryCount;
      body.recover=true;
      body.simulated=body.source;
      return;
      }
    for(size_t i=0;i<body.source.size();++i) {
      const auto world=CollisionWorld::toCentimeters(JPH::Vec3(native.GetCenterOfMassTransform()*motion.GetVertex(body.mapping[i]).mPosition));
      body.simulated[i]=project(body.inverse,world);
      }
    });
  }
void Cloth::finishFrame() {
  impl->each([&](Instance::Impl& body) {
    for(size_t i=0;i<body.rendered.size();++i) {
      const auto reference=body.reference(i);
      body.rendered[i]=reference+(body.simulated[i]-reference)*body.weight;
      }
    if(body.update) body.update(body.rendered,body.weight);
    });
  }

std::vector<Cloth::Snapshot> Cloth::snapshots() const {
  std::vector<Snapshot> result;
  for(const auto& weak:impl->instances) {
    const auto instance=weak.lock();
    if(!instance) continue;
    const auto& body=*instance->impl;
    Snapshot snapshot;
    snapshot.id=reinterpret_cast<uintptr_t>(instance.get());
    snapshot.zenId=body.zenId;
    snapshot.visual=body.visual;
    snapshot.center=body.center();
    snapshot.active=instance->active();
    snapshot.exposed=body.exposed;
    snapshot.vertices=body.pinned.size();
    snapshot.pinned=size_t(std::count(body.pinned.begin(),body.pinned.end(),true));
    snapshot.simulationWeight=body.weight;
    snapshot.maxAnchorErrorCm=body.maxAnchorError;
    snapshot.maxSpeedCmPerSec=body.maxSpeed;
    snapshot.maxSpeedEverCmPerSec=body.maxSpeedEver;
    snapshot.contactVertices=body.contactCount;
    snapshot.maxSpeedVertex=body.maxSpeedVertex;
    snapshot.maxSpeedSourceCm=body.maxSpeedSource;
    snapshot.maxSpeedWorldCm=body.maxSpeedWorld;
    snapshot.maxSpeedContact=body.maxSpeedContact;
    snapshot.maxSpeedContactNormal=body.maxSpeedContactNormal;
    snapshot.maxSpeedEverVertex=body.maxSpeedEverVertex;
    snapshot.maxSpeedEverSourceCm=body.maxSpeedEverSource;
    snapshot.maxSpeedEverWorldCm=body.maxSpeedEverWorld;
    snapshot.maxSpeedEverContact=body.maxSpeedEverContact;
    snapshot.maxSpeedEverContactNormal=body.maxSpeedEverContactNormal;
    snapshot.windOccluders=body.windOccluders;
    const auto label=[&](JPH::BodyID id) -> std::string {
      if(id.IsInvalid()) return {};
      JPH::BodyLockRead lock(impl->system().GetBodyLockInterface(),id);
      if(!lock.Succeeded()) return {};
      const auto* collider=reinterpret_cast<const CollisionWorld::CollisionBody*>(lock.GetBody().GetUserData());
      return collider!=nullptr ? collider->label() : std::string();
      };
    std::set<std::string> contactLabels;
    std::set<uint32_t> contactIds;
    for(const auto& contact:body.contacts)
      if(contactIds.insert(contact.body.GetIndexAndSequenceNumber()).second)
        contactLabels.insert(label(contact.body));
    snapshot.contactBodies.assign(contactLabels.begin(),contactLabels.end());
    snapshot.maxSpeedContactBody=label(body.maxSpeedContactBody);
    snapshot.maxSpeedEverContactBody=label(body.maxSpeedEverContactBody);
    snapshot.transitionMaxDeltaCm=body.transitionMaxDelta;
    snapshot.projectileHits=body.projectileHits;
    snapshot.arrowPasses=body.arrowPasses;
    snapshot.recoveryCount=body.recoveryCount;
    snapshot.positionsCm=body.rendered;
    snapshot.indices=body.indices;
    for(size_t i=0;i<body.rendered.size();++i) {
      snapshot.worldPositionsCm.push_back(project(body.matrix,body.rendered[i]));
      if(body.pinned[body.mapping[i]]) snapshot.anchorsCm.push_back(project(body.matrix,body.source[i]));
      }
    result.push_back(std::move(snapshot));
    }
  return result;
  }
void Cloth::resetStats() {
  impl->each([](Instance::Impl& body) {
    body.maxAnchorError=body.maxSpeedEver=body.transitionMaxDelta=0;
    body.maxSpeedEverVertex=uint32_t(-1);
    body.maxSpeedEverSource=body.maxSpeedEverWorld=body.maxSpeedEverContactNormal=Tempest::Vec3();
    body.maxSpeedEverContact=false;
    body.maxSpeedEverContactBody=JPH::BodyID();
    body.projectileHits=body.arrowPasses=body.recoveryCount=0;
    });
  }
size_t Cloth::projectile(const Tempest::Vec3& from,const Tempest::Vec3& to,const Tempest::Vec3& kick,bool arrow) {
  if(!impl->enabled || !finite(kick) || !finite(from) || !finite(to)) return 0;
  size_t hitCount=0;
  impl->each([&](Instance::Impl& body) {
    if(body.body.IsInvalid() || body.weight<=0.f) return;
    std::set<uint32_t> touched;
    for(size_t i=0;i+2<body.indices.size();i+=3) {
      const auto a=body.indices[i],b=body.indices[i+1],c=body.indices[i+2];
      if(!segmentTriangle(from,to,project(body.matrix,body.rendered[a]),project(body.matrix,body.rendered[b]),project(body.matrix,body.rendered[c]))) continue;
      touched.insert(body.mapping[a]); touched.insert(body.mapping[b]); touched.insert(body.mapping[c]);
      }
    if(touched.empty()) return;
    JPH::BodyLockWrite lock(impl->system().GetBodyLockInterface(),body.body);
    if(!lock.Succeeded()) return;
    auto& motion=*static_cast<JPH::SoftBodyMotionProperties*>(lock.GetBody().GetMotionProperties());
    const auto velocity=CollisionWorld::toMeters(kick*100.f);
    for(auto index:touched) {
      auto& vertex=motion.GetVertex(index);
      if(vertex.mInvMass>0.f) vertex.mVelocity=limited(vertex.mVelocity+velocity);
      }
    ++body.projectileHits;
    if(arrow) ++body.arrowPasses;
    ++hitCount;
    });
  return hitCount;
  }
size_t Cloth::impulse(const Tempest::Vec3& center,float radius,const Tempest::Vec3& kick) {
  if(!impl->enabled || radius<=0.f || !std::isfinite(radius) || !finite(center) || !finite(kick)) return 0;
  size_t count=0;
  impl->each([&](Instance::Impl& body) {
    if(body.body.IsInvalid()) return;
    JPH::BodyLockWrite lock(impl->system().GetBodyLockInterface(),body.body);
    if(!lock.Succeeded()) return;
    auto& native=lock.GetBody();
    auto& motion=*static_cast<JPH::SoftBodyMotionProperties*>(native.GetMotionProperties());
    bool hit=false;
    for(auto& vertex:motion.GetVertices()) {
      if(vertex.mInvMass<=0.f) continue;
      const auto world=CollisionWorld::toCentimeters(JPH::Vec3(native.GetCenterOfMassTransform()*vertex.mPosition));
      const float distance=(world-center).length();
      if(distance>=radius) continue;
      vertex.mVelocity=limited(vertex.mVelocity+CollisionWorld::toMeters(kick*100.f)*(1.f-distance/radius));
      hit=true;
      }
    if(hit) { ++count; ++body.projectileHits; }
    });
  return count;
  }
