#include "physics/cloth.h"
#include "physics/collisionworld.h"
#include "physics/dynamicworld.h"
#include "physics/worldwind.h"

#include <Jolt/Physics/Collision/Shape/BoxShape.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace {
  void require(bool condition,const char* message) {
    if(condition) return;
    std::fprintf(stderr,"FAIL: %s\n",message);
    std::exit(EXIT_FAILURE);
    }
  struct Flag {
    std::vector<Tempest::Vec3> vertices;
    std::vector<uint32_t> indices,pins;
    Flag() {
      constexpr uint32_t width=9,height=13;
      for(uint32_t y=0;y<height;++y)
        for(uint32_t x=0;x<width;++x) {
          vertices.emplace_back(float(x)*15.f,200.f-float(y)*15.f,0.f);
          if(y==0) pins.push_back(uint32_t(vertices.size()-1));
          }
      for(uint32_t y=0;y+1<height;++y)
        for(uint32_t x=0;x+1<width;++x) {
          const uint32_t a=y*width+x,b=a+1,c=a+width,d=c+1;
          indices.insert(indices.end(),{a,b,c,b,d,c});
          }
      }
    };
  void tick(CollisionWorld& world,size_t count) { for(size_t i=0;i<count;++i) world.tick(17); }
  float lateralSpeed(const Cloth::Snapshot& from,const Cloth::Snapshot& to) {
    float speed=0;
    for(size_t i=0;i<from.worldPositionsCm.size();++i) {
      const auto delta=to.worldPositionsCm[i]-from.worldPositionsCm[i];
      speed=std::max(speed,std::sqrt(delta.x*delta.x+delta.z*delta.z)/.017f);
      }
    return speed;
    }
  float maxDisplacement(const Cloth::Snapshot& from,const Cloth::Snapshot& to) {
    float distance=0;
    for(size_t i=0;i<from.worldPositionsCm.size();++i)
      distance=std::max(distance,(to.worldPositionsCm[i]-from.worldPositionsCm[i]).length());
    return distance;
    }
  }

int main() {
  constexpr uint64_t windPeriod=10000;
  const auto windBefore=WorldWind::sample(windPeriod-1,windPeriod,true);
  const auto windAfter=WorldWind::sample(windPeriod+1,windPeriod,true);
  require((windAfter-windBefore).length()<.02f,"world wind has no discontinuity at a shader cycle boundary");
  require(WorldWind::sample(23456,windPeriod,false).quadLength()==0.f,"disabled world wind is exactly zero");
  require(WorldWind::sample(23456,windPeriod,true,0).quadLength()==0.f,"calm world wind is exactly zero");
  require(WorldWind::sample(23456,0,true).quadLength()==0.f,"zero wind period is exactly calm");
  require((WorldWind::sample(0,windPeriod,true)-WorldWind::sample(30000,windPeriod,true)).length()>.1f,"world wind actually changes over time");
  CollisionWorld collision;
  Cloth cloth(collision);
  Flag flag;
  auto instance=cloth.add(flag.vertices,flag.indices,flag.pins,Tempest::Matrix4x4::mkIdentity(),.85f,"contract-flag",7);
  require(instance!=nullptr,"valid cloth registers");
  require(!instance->active(),"registration alone creates no native soft body");
  cloth.setObserver({60,100,0});
  tick(collision,600);
  auto calm=cloth.snapshots().front();
  require(calm.active && calm.simulationWeight==1.f,"near cloth becomes fully simulated");
  require(calm.vertices==flag.vertices.size() && calm.pinned==flag.pins.size(),"soft mesh preserves visible vertices and pins");
  require(calm.maxAnchorErrorCm<=.5f,"pinned vertices stay within half a centimetre");
  require(calm.recoveryCount==0,"calm cloth never needs recovery");
  require(std::isfinite(calm.maxSpeedCmPerSec),"calm velocity remains finite");
  cloth.setWind({4,0,1});
  tick(collision,600);
  auto windy=cloth.snapshots().front();
  require(windy.maxSpeedEverCmPerSec<=Cloth::maxVelocityMS*100.f+1.f,"native vertex speed cap bounds gusts");
  require(windy.recoveryCount==0,"gusts never explode or fly away");
  require(windy.maxAnchorErrorCm<=.5f,"gusts preserve attachments");
  const auto& vertices=windy.worldPositionsCm;
  const uint32_t a=flag.indices[flag.indices.size()/2],b=flag.indices[flag.indices.size()/2+1],c=flag.indices[flag.indices.size()/2+2];
  const auto center=(vertices[a]+vertices[b]+vertices[c])*(1.f/3.f);
  auto normal=Tempest::Vec3::crossProduct(vertices[b]-vertices[a],vertices[c]-vertices[a]);
  normal=Tempest::Vec3::normalize(normal);
  require(cloth.projectile(center+normal*100.f,center-normal*100.f,normal*.35f,true)==1,"arrow crosses real deformed triangles");
  require(cloth.projectile(center+normal*100.f,center-normal*100.f,normal*5.f,false)==1,"fireball pushes real deformed triangles");
  auto hit=cloth.snapshots().front();
  require(hit.arrowPasses==1 && hit.projectileHits==2,"one pass-through arrow and one spell are counted");
  constexpr uint32_t solidMask=(1u<<DynamicWorld::C_Landscape)|(1u<<DynamicWorld::C_Object)|(1u<<DynamicWorld::C_ActiveBody);
  require(!collision.rayCast(center+normal*100.f,center-normal*100.f,solidMask,true).hasHit,"cloth never blocks solid or character queries");
  require(!collision.rayCast(center+normal*100.f,center-normal*100.f,~0u,true).hasHit,"even an all-layer gameplay ray passes through cloth");
  tick(collision,120);
  instance->setShaderReference([](uint32_t,const Tempest::Vec3& local,const Tempest::Vec3&) { return local+Tempest::Vec3(0,0,5); });
  // Walk through the transition band with fixed geometry and a stationary
  // shader phase: only cloth simulation and the fallback transition can move it.
  for(float distance=3450.f;distance<=3995.f;distance+=5.f) {
    cloth.setObserver({60.f+distance,110,0});
    collision.tick(17);
    }
  cloth.setObserver({4059,110,0}); // one centimetre inside the 40 m radius
  collision.tick(17);
  const auto boundaryBefore=cloth.snapshots().front();
  cloth.setObserver({4061,110,0}); // one centimetre outside
  collision.tick(17);
  const auto boundaryAfter=cloth.snapshots().front();
  const float boundaryDelta=maxDisplacement(boundaryBefore,boundaryAfter);
  require(boundaryDelta<=.5f,"rendered cloth crosses the radius without a half-centimetre pop");
  require(boundaryAfter.simulationWeight==0.f && !boundaryAfter.active,"crossing the radius reaches shader fallback");
  for(size_t i=0;i<flag.vertices.size();++i)
    require((boundaryAfter.positionsCm[i]-flag.vertices[i]-Tempest::Vec3(0,0,5)).length()<.001f,"outside radius visible vertices match the shader phase");
  cloth.setObserver({10000,100,0});
  tick(collision,40);
  require(!instance->active() && cloth.bodyCount()==0,"distant cloth releases native soft body");
  cloth.setObserver({60,100,0});
  tick(collision,40);
  require(instance->active(),"returning observer recreates transient cloth");
  size_t updateCalls=0;
  bool callbackEnabled=true;
  float callbackWeight=instance->simulationWeight();
  instance->setUpdateCallback([&](const std::vector<Tempest::Vec3>& positions,float weight) {
    require(positions.size()==flag.vertices.size(),"toggle callback receives complete visible geometry");
    ++updateCalls;
    callbackEnabled=cloth.enabled();
    callbackWeight=weight;
    });
  const float weightBeforeDisable=instance->simulationWeight();
  cloth.setEnabled(false);
  require(updateCalls==1 && !callbackEnabled && callbackWeight==weightBeforeDisable,
          "cloth OFF synchronizes render callbacks immediately without advancing physics");
  tick(collision,40);
  require(!instance->active() && cloth.bodyCount()==0,"cloth OFF returns entirely to shader");
  const auto callsBeforeEnable=updateCalls;
  cloth.setEnabled(true);
  require(updateCalls==callsBeforeEnable+1 && callbackEnabled && callbackWeight==0.f,
          "cloth ON restores fallback visibility before the next physics tick");
  tick(collision,40);
  require(instance->active(),"cloth ON recreates Jolt simulation");
  instance.reset();
  require(cloth.bodyCount()==0 && cloth.snapshots().empty(),"visual RAII releases native body");
  auto roofMatrix=Tempest::Matrix4x4::mkIdentity();
  roofMatrix.translate(60,500,0);
  auto roof=collision.addCollisionBody(new JPH::BoxShape(JPH::Vec3(4,.1f,4)),roofMatrix,.7f,DynamicWorld::C_Object);
  auto sheltered=cloth.add(flag.vertices,flag.indices,flag.pins,Tempest::Matrix4x4::mkIdentity(),.85f,"sheltered-flag",8);
  require(roof!=nullptr && sheltered!=nullptr,"roof and sheltered cloth register");
  cloth.setObserver({60,100,0});
  cloth.setWind({0,0,0});
  tick(collision,600);
  const auto roofCalmBefore=cloth.snapshots().front();
  collision.tick(17);
  const auto roofCalmAfter=cloth.snapshots().front();
  require(!roofCalmAfter.exposed,"a real native roof shelters cloth from world wind");
  const float calmLateral=lateralSpeed(roofCalmBefore,roofCalmAfter);
  cloth.setWind({8,0,4});
  tick(collision,600);
  const auto roofWindBefore=cloth.snapshots().front();
  collision.tick(17);
  const auto roofWindAfter=cloth.snapshots().front();
  const float shelteredLateral=lateralSpeed(roofWindBefore,roofWindAfter);
  require(!roofWindAfter.exposed,"shelter persists through strong wind");
  require(shelteredLateral<=calmLateral+.25f,"strong world wind adds no lateral motion under a roof");
  sheltered.reset();
  roof.reset();
  auto beamMatrix=Tempest::Matrix4x4::mkIdentity();
  beamMatrix.translate(60,250,0);
  auto beam=collision.addCollisionBody(new JPH::BoxShape(JPH::Vec3(.8f,.05f,.05f)),beamMatrix,.7f,DynamicWorld::C_Object);
  auto outdoor=cloth.add(flag.vertices,flag.indices,flag.pins,Tempest::Matrix4x4::mkIdentity(),.85f,"crossbar-flag",9);
  cloth.setWind({0,0,4});
  tick(collision,60);
  require(cloth.snapshots().front().exposed,"a narrow attachment crossbar does not suppress outdoor wind");
  outdoor.reset();
  beam.reset();
  auto wideBeam=collision.addCollisionBody(new JPH::BoxShape(JPH::Vec3(.8f,.05f,.4f)),beamMatrix,.7f,DynamicWorld::C_Object);
  wideBeam->setLabel("OWN-ATTACHMENT.mrm");
  auto attachmentFlag=flag.vertices;
  for(auto& vertex:attachmentFlag) vertex-=Tempest::Vec3(60,250,0);
  auto attached=cloth.add(attachmentFlag,flag.indices,flag.pins,beamMatrix,.85f,"meshes/own-attachment.MMS",12);
  tick(collision,60);
  require(cloth.snapshots().front().exposed,"a wide rigid part of the same visual and transform is not its cloth's roof");
  auto actualRoof=collision.addCollisionBody(new JPH::BoxShape(JPH::Vec3(4,.1f,4)),roofMatrix,.7f,DynamicWorld::C_Object);
  actualRoof->setLabel("separate-roof");
  tick(collision,60);
  require(!cloth.snapshots().front().exposed,"ignoring an own attachment still detects a separate roof above it");
  actualRoof.reset();
  attached.reset();
  wideBeam.reset();
  auto wallMatrix=Tempest::Matrix4x4::mkIdentity();
  wallMatrix.translate(60,100,30);
  auto wall=collision.addCollisionBody(new JPH::BoxShape(JPH::Vec3(5,5,.05f)),wallMatrix,.7f,DynamicWorld::C_Object);
  auto blocked=cloth.add(flag.vertices,flag.indices,flag.pins,Tempest::Matrix4x4::mkIdentity(),.85f,"wall-flag",10);
  cloth.setWind({0,0,8});
  tick(collision,600);
  const auto wallCloth=cloth.snapshots().front();
  float maxWallZ=0;
  for(const auto& position:wallCloth.worldPositionsCm) maxWallZ=std::max(maxWallZ,position.z);
  require(maxWallZ>20.f,"wind carries cloth into a nearby native obstacle");
  require(maxWallZ<=25.1f,"native static-object contact prevents cloth passing through its pole or a wall");
  require(wallCloth.maxAnchorErrorCm<=.5f && wallCloth.recoveryCount==0,"obstacle contacts preserve fixed pins without recovery");
  blocked.reset();
  wall.reset();
  auto character=collision.addCollisionBody(new JPH::BoxShape(JPH::Vec3(5,5,.05f)),wallMatrix,.7f,DynamicWorld::C_ActiveBody);
  auto passThrough=cloth.add(flag.vertices,flag.indices,flag.pins,Tempest::Matrix4x4::mkIdentity(),.85f,"character-flag",11);
  tick(collision,600);
  const auto characterCloth=cloth.snapshots().front();
  float maxCharacterZ=0;
  for(const auto& position:characterCloth.worldPositionsCm) maxCharacterZ=std::max(maxCharacterZ,position.z);
  require(maxCharacterZ>35.f,"cloth passes through the same obstacle on the character layer");
  require(characterCloth.recoveryCount==0,"ignored character contacts never destabilize cloth");
  std::printf("cloth contract passed: calm_10s_max_vertex_cm_s=%.6g max_vertex_cm_s=%.6g anchor_drift_cm=%.6g boundary_delta_cm=%.6g roof_calm_lateral_cm_s=%.6g roof_wind_lateral_cm_s=%.6g wall_vertex_max_z_cm=%.6g character_vertex_max_z_cm=%.6g recovery=0\n",
              double(calm.maxSpeedCmPerSec),double(windy.maxSpeedEverCmPerSec),double(windy.maxAnchorErrorCm),
              double(boundaryDelta),double(calmLateral),double(shelteredLateral),double(maxWallZ),double(maxCharacterZ));
  return EXIT_SUCCESS;
  }
