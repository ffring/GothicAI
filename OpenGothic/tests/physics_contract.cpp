#include "physics/collisionworld.h"
#include "physics/dynamicworld.h"
#include "physics/physicvbo.h"
#include "physics/ragdoll.h"
#include "graphics/mesh/protomesh.h"
#include "graphics/mesh/skeleton.h"
#include "graphics/mesh/hitzones.h"
#include "graphics/mesh/submesh/packedmesh.h"
#include "game/serialize.h"

#include <Tempest/MemReader>
#include <Tempest/MemWriter>

#include <Jolt/Physics/Collision/Shape/BoxShape.h>
#include <Jolt/Physics/Collision/Shape/CapsuleShape.h>
#include <Jolt/Physics/Collision/Shape/MeshShape.h>
#include <Jolt/Physics/Collision/CastResult.h>
#include <Jolt/Physics/Collision/CollisionCollectorImpl.h>
#include <Jolt/Physics/Collision/RayCast.h>

#include <Jolt/Physics/Collision/CastSphereVsTriangles.h>
#include <Jolt/Physics/Collision/Shape/SphereShape.h>
#include <Jolt/Physics/Collision/Shape/ConvexHullShape.h>
#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <cstring>
#include <limits>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <functional>
#include <string>
#include <utility>
#include <vector>

namespace {
  constexpr uint32_t landscapeMask = 1u << DynamicWorld::C_Landscape;
  constexpr uint32_t objectMask    = 1u << DynamicWorld::C_Object;
  constexpr uint32_t sensorMask    = 1u << DynamicWorld::C_Ghost;

  void require(bool condition, const char* message) {
    // These checks must also run in Release builds, where assert is disabled.
    if(condition)
      return;
    std::fprintf(stderr,"FAIL: %s\n",message);
    std::exit(EXIT_FAILURE);
    }

  void requireNear(float actual, float expected, float tolerance, const char* message) {
    if(std::isfinite(actual) && std::abs(actual-expected)<=tolerance)
      return;
    std::fprintf(stderr,"FAIL: %s: actual %.9g, expected %.9g +/- %.9g\n",
                 message,double(actual),double(expected),double(tolerance));
    std::exit(EXIT_FAILURE);
    }

  Tempest::Matrix4x4 at(float x, float y, float z) {
    auto matrix = Tempest::Matrix4x4::mkIdentity();
    matrix.translate(x,y,z);
    return matrix;
    }

  void requireMatrix(const Tempest::Matrix4x4& actual, const Tempest::Matrix4x4& expected,
                     const char* message) {
    for(int c=0;c<4;++c)
      for(int r=0;r<4;++r)
        requireNear(actual.at(c,r),expected.at(c,r),c==3 ? .001f : .00001f,message);
    }

  zenkit::MultiResolutionMesh movableCube() {
    zenkit::MultiResolutionMesh mesh;
    mesh.positions = {{-10,-10,-10},{10,-10,-10},{10,10,-10},{-10,10,-10},
                      {-10,-10,10},{10,-10,10},{10,10,10},{-10,10,10}};
    mesh.bbox = {{-10,-10,-10},{10,10,10}};
    mesh.sub_meshes.resize(1);
    auto& part = mesh.sub_meshes[0];
    part.mat.group = zenkit::MaterialGroup::WOOD;
    for(size_t i=0;i<mesh.positions.size();++i)
      part.wedges.push_back({{0,1,0},{0,0},uint16_t(i)});
    part.triangles = {{{0,2,1}},{{0,3,2}},{{4,5,6}},{{4,6,7}},
                      {{0,4,7}},{{0,7,3}},{{1,2,6}},{{1,6,5}},
                      {{0,1,5}},{{0,5,4}},{{3,7,6}},{{3,6,2}}};
    return mesh;
    }

  zenkit::MultiResolutionMesh movableBox(const Tempest::Vec3& dimensionsCm) {
    auto mesh=movableCube();
    for(auto& vertex:mesh.positions) {
      vertex.x*=dimensionsCm.x/20.f;
      vertex.y*=dimensionsCm.y/20.f;
      vertex.z*=dimensionsCm.z/20.f;
      }
    mesh.bbox={{-.5f*dimensionsCm.x,-.5f*dimensionsCm.y,-.5f*dimensionsCm.z},
               { .5f*dimensionsCm.x, .5f*dimensionsCm.y, .5f*dimensionsCm.z}};
    return mesh;
    }

  zenkit::MultiResolutionMesh movableCollisionBox(const Tempest::Vec3& dimensionsCm) {
    auto mesh=movableBox(dimensionsCm);
    // movableCube's winding is suitable for its hull-only fixtures. Static
    // Gothic source meshes need clockwise faces: PhysicVbo reverses them.
    for(auto& triangle:mesh.sub_meshes[0].triangles)
      std::swap(triangle.wedges[1],triangle.wedges[2]);
    return mesh;
    }

  zenkit::Mesh movableFloor() {
    zenkit::Mesh mesh;
    mesh.vertices={{-2000,0,-2000},{-2000,0,2000},{2000,0,2000},{2000,0,-2000}};
    mesh.materials.resize(1);
    mesh.materials[0].group=zenkit::MaterialGroup::STONE;
    // Gothic source faces are reversed by PhysicVbo::addIndex.
    mesh.polygons.vertex_indices={0,2,1,0,3,2};
    mesh.polygons.material_indices={0,0};
    return mesh;
    }

  void requireVectorRelative(const Tempest::Vec3& actual, const Tempest::Vec3& expected,
                             const char* message) {
    requireNear(actual.x,expected.x,std::max(std::abs(expected.x)*.02f,.0001f),message);
    requireNear(actual.y,expected.y,std::max(std::abs(expected.y)*.02f,.0001f),message);
    requireNear(actual.z,expected.z,std::max(std::abs(expected.z)*.02f,.0001f),message);
    }

  struct ImpulseResponse {
    float mass;
    Tempest::Vec3 impulse,linear,angular;
    };

  ImpulseResponse movableImpulseResponse(const ProtoMesh& proto, const Tempest::Vec3& dimensionsCm,
                                         float massMultiplier, const Tempest::Vec3* sameImpulse=nullptr) {
    DynamicWorld world(nullptr,zenkit::Mesh{});
    const Tempest::Vec3 center(123,10000,-257);
    const auto origin=at(center.x,center.y,center.z);
    auto object=world.addMovableVob(proto,"CONTRACT_IMPULSE.MRM",origin,massMultiplier,true,"table",true,44);
    require(!object.isEmpty() && object.mass()>0,"impulse fixture must use a native movable vob");
    const float mass=object.mass();
    const auto impulse=sameImpulse!=nullptr ? *sameImpulse : Tempest::Vec3(.2f,.4f,-.1f)*mass;
    const Tempest::Vec3 r(dimensionsCm.x*.0035f,dimensionsCm.y*.003f,-dimensionsCm.z*.002f);
    const auto point=center+r*100.f;
    require(world.applyMovableVobImpulse(center,1.f,[&](const Tempest::Vec3&,float) { return impulse; },
                                        nullptr,&point)==1,
            "airborne movable vob must receive exactly one impulse at a world point");
    world.tick(17);

    const auto size=dimensionsCm*.01f;
    const Tempest::Vec3 inertia(mass*(size.y*size.y+size.z*size.z)/12.f,
                                mass*(size.x*size.x+size.z*size.z)/12.f,
                                mass*(size.x*size.x+size.y*size.y)/12.f);
    const Tempest::Vec3 momentum(r.y*impulse.z-r.z*impulse.y,
                                 r.z*impulse.x-r.x*impulse.z,
                                 r.x*impulse.y-r.y*impulse.x);
    const Tempest::Vec3 expectedAngular(momentum.x/inertia.x,momentum.y/inertia.y,momentum.z/inertia.z);
    auto expectedLinear=impulse*(100.f/mass);
    expectedLinear.y-=DynamicWorld::gravityMS*100.f/60.f;
    const auto actualAngular=object.angularVelocity(),actualLinear=object.velocity();
    std::printf("Movable impulse %.0fx%.0fx%.0f cm, multiplier %.3f, mass %.6f kg: "
                "omega expected (%.6f, %.6f, %.6f), actual (%.6f, %.6f, %.6f) rad/s; "
                "v expected (%.6f, %.6f, %.6f), actual (%.6f, %.6f, %.6f) cm/s\n",
                double(dimensionsCm.x),double(dimensionsCm.y),double(dimensionsCm.z),
                double(massMultiplier),double(mass),
                double(expectedAngular.x),double(expectedAngular.y),double(expectedAngular.z),
                double(actualAngular.x),double(actualAngular.y),double(actualAngular.z),
                double(expectedLinear.x),double(expectedLinear.y),double(expectedLinear.z),
                double(actualLinear.x),double(actualLinear.y),double(actualLinear.z));
    requireVectorRelative(actualAngular,expectedAngular,
                          "movable angular velocity after one step must match I^-1(r cross J) within two percent");
    requireVectorRelative(actualLinear,expectedLinear,
                          "movable linear velocity after one step must match J/m plus gravity within two percent");
    return {mass,impulse,actualLinear,actualAngular};
    }

  void testMovableVobImpulseInertia() {
    Resources resources;
    for(const auto dimensions:{Tempest::Vec3(20,20,20),Tempest::Vec3(40,60,80)}) {
      ProtoMesh proto(PackedMesh(movableBox(dimensions),PackedMesh::PK_Visual),"CONTRACT_IMPULSE.MRM");
      const auto normal=movableImpulseResponse(proto,dimensions,.25f);
      const auto doubled=movableImpulseResponse(proto,dimensions,.5f,&normal.impulse);
      requireNear(doubled.mass,normal.mass*2.f,normal.mass*.0001f,
                  "doubling massMultiplier must double native movable mass");
      requireVectorRelative(doubled.angular,normal.angular*.5f,
                            "doubling massMultiplier must halve angular response to the same impulse");
      requireNear(doubled.linear.x,normal.linear.x*.5f,.0001f,
                  "doubling massMultiplier must halve X velocity from the same impulse");
      requireNear(doubled.linear.z,normal.linear.z*.5f,.0001f,
                  "doubling massMultiplier must halve Z velocity from the same impulse");
      }
    }

  void testMovableTableImpulseCouple() {
    Resources resources;
    // NW_CITY_TABLE_PEASANT_01's source bounds are 200 x 90 x 120 cm.
    const Tempest::Vec3 dimensions(200,90,120);
    constexpr float tableMass=51.81584f;
    const float multiplier=tableMass/(2.f*.9f*1.2f*DynamicWorld::materialDensity(zenkit::MaterialGroup::WOOD));
    ProtoMesh proto(PackedMesh(movableBox(dimensions),PackedMesh::PK_Visual),"CONTRACT_TABLE.MRM");
    const auto origin=at(0,45,0);
    DynamicWorld world(nullptr,movableFloor());
    const auto floor=world.landRay({0,100,0},200.f);
    require(floor.hasCol && floor.n.y>.99f,"table fixture must have an upward-facing static floor");
    requireNear(floor.v.y,0.f,.001f,"table fixture's bottom face must start on the floor");
    auto table=world.addMovableVob(proto,"CONTRACT_TABLE.MRM",origin,multiplier,true,"table",true,45);
    require(!table.isEmpty(),"table couple fixture must use a native movable vob on a static floor");
    requireNear(table.mass(),tableMass,.001f,"table fixture must retain the tavern table's mass");
    const Tempest::Vec3 center(0,45,0),point(0,90,60);
    const Tempest::Vec3 force(0,0,6.f*table.mass());
    for(const auto& application:{std::pair(force,point),std::pair(force*-1.f,center)})
      require(world.applyMovableVobImpulse(center,140.f,[&](const Tempest::Vec3&,float) { return application.first; },
                                          nullptr,&application.second)==1,
              "table fixture must receive both opposing bench impulses");
    const auto afterPair=table.angularVelocity();
    float maximumTilt=0;
    for(int i=0;i<120;++i) {
      world.tick(i%3==2 ? 16 : 17);
      const auto matrix=world.movableVobSnapshot()[0].transform;
      const Tempest::Vec3 up(matrix.at(1,0),matrix.at(1,1),matrix.at(1,2));
      const float cosine=std::clamp(up.y/up.length(),-1.f,1.f);
      maximumTilt=std::max(maximumTilt,std::acos(cosine)*(180.f/3.14159265358979323846f));
      }
    std::printf("Movable table couple %.6f Ns: omega after pair (%.6f, %.6f, %.6f) rad/s; maximum tilt %.6f deg\n",
                double(force.z),double(afterPair.x),double(afterPair.y),double(afterPair.z),double(maximumTilt));
    require(maximumTilt>45.f,"the bench impulse couple must tilt a free-standing table beyond forty-five degrees");

    DynamicWorld untouched(nullptr,movableFloor());
    auto resting=untouched.addMovableVob(proto,"CONTRACT_TABLE.MRM",origin,multiplier,true,"table",true,46);
    for(int i=0;i<3600;++i)
      untouched.tick(i%3==2 ? 16 : 17);
    const auto settled=untouched.movableVobSnapshot()[0].transform;
    const Tempest::Vec3 displacement(settled.at(3,0)-origin.at(3,0),settled.at(3,1)-origin.at(3,1),
                                      settled.at(3,2)-origin.at(3,2));
    std::printf("Movable table untouched for 60 s: drift %.9f cm\n",double(displacement.length()));
    require(resting.isSleeping(),"an untouched table must remain asleep for sixty seconds");
    requireNear(displacement.length(),0.f,.00001f,"an untouched table must have zero drift after sixty seconds");
    requireMatrix(settled,origin,"an untouched table must retain its complete pose for sixty seconds");
    }

  std::vector<uint8_t> saveArchive(const std::function<void(Serialize&)>& write) {
    std::vector<uint8_t> bytes;
    Tempest::MemWriter writer(bytes);
    {
      Serialize output(writer);
      write(output);
    }
    return bytes;
    }

  void loadMovables(DynamicWorld& world, const std::vector<uint8_t>& bytes) {
    Tempest::MemReader reader(bytes);
    Serialize input(reader);
    world.loadMovableVobs(input);
    }

  const DynamicWorld::MovableVobContacts& movableContact(
      const std::vector<DynamicWorld::MovableVobContacts>& contacts, uint32_t zenId) {
    const auto found=std::find_if(contacts.begin(),contacts.end(),[&](const auto& entry) {
      return entry.zenId==zenId;
      });
    require(found!=contacts.end(),"geometry census must retain every candidate's ZEN identity");
    return *found;
    }

  void testMovableVobGeometrySupport() {
    Resources resources;
    ProtoMesh proto(PackedMesh(movableCube(),PackedMesh::PK_Visual),"CONTRACT_CLAMP.MRM");
    for(const bool initiallyEnabled:{false,true}) {
      DynamicWorld world(nullptr,movableFloor());
      world.setMovableVobsEnabled(initiallyEnabled);
      const auto supportedOrigin=at(0,10,0),hangingOrigin=at(100,100,0);
      auto supported=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",supportedOrigin,.25f,false,"utensil",true,101);
      auto hanging=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",hangingOrigin,.25f,false,"utensil",true,102);
      auto nonColliding=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(200,100,0),.25f,false,"utensil",false,103);
      require(!supported.isEmpty() && !hanging.isEmpty() && !nonColliding.isEmpty(),
              "geometry fixtures must retain handles with movable_vobs initially on or off");
      world.finalizeMovableVobs();
      world.setMovableVobsEnabled(true);
      const auto contacts=world.movableVobContacts(1.f);
      require(contacts.size()==3,"geometry validation must include colliding and non-colliding candidates");
      const auto& allowed=movableContact(contacts,101);
      require(allowed.enabled && allowed.disposition=="ok" && supported.mass()>0,
              "a hull resting on landscape must remain dynamic after load or runtime enable");
      for(const auto id:{102u,103u}) {
        const auto& rejected=movableContact(contacts,id);
        require(!rejected.enabled && rejected.disposition=="hangs",
                "a hull with no support within one centimetre must remain static");
        }
      require(hanging.mass()==0 && !hanging.addImpulse({1,0,0}),
              "a geometry-rejected vob must have no dynamic body or impulse response");
      require(!world.setMovableVobTransform(102,at(150,100,0)),
              "a geometry-rejected vob must not accept controlled dynamic movement");
      requireMatrix(world.movableVobPose(102)->transform,hangingOrigin,
                    "a rejected vob's attachment pose must remain at its original ZEN frame");
      require(world.landRay({100,130,0},60).hasCol && world.ray({70,100,0},{130,100,0}).hasCol,
              "a rejected small vob must restore its original C_Object source mesh queries");
      require(!world.landRay({200,130,0},60).hasCol && !world.ray({170,100,0},{230,100,0}).hasCol,
              "a rejected cd_dynamic=0 vob must preserve the original absence of C_Object collision");
      require(world.physicsStats().bodies==4,
              "rejected hulls must be removed, leaving landscape, one hull and two eligible source meshes");
      const auto state=world.movableVobSnapshot();
      const auto staticState=std::find_if(state.begin(),state.end(),[](const auto& entry) { return entry.zenId==102; });
      require(staticState!=state.end() && !staticState->trace && !staticState->tilted && staticState->traceTime==-1,
              "geometry rejection must not create a fallen-furniture trace");
      require(std::isfinite(world.movableVobValidationMilliseconds()) && world.movableVobValidationMilliseconds()>=0,
              "load-time geometry validation must expose a finite nonnegative duration");
      // A second toggle must preserve the authored decision and original pose.
      world.setMovableVobsEnabled(false);
      world.setMovableVobsEnabled(true);
      require(supported.mass()>0 && hanging.mass()==0 && nonColliding.mass()==0,
              "runtime re-enable must preserve geometry decisions for each individual vob");
      requireMatrix(world.movableVobPose(102)->transform,hangingOrigin,
                    "runtime toggles must not move a rejected static source mesh");
      }
    DynamicWorld gaps(nullptr,movableFloor());
    auto nearSupport=gaps.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(0,10.5f,0),.25f,true,"table",true,104);
    auto farSupport=gaps.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(100,11.5f,0),.25f,true,"table",true,105);
    gaps.finalizeMovableVobs();
    const auto gapContacts=gaps.movableVobContacts(1.f);
    require(movableContact(gapContacts,104).enabled && nearSupport.mass()>0,
            "a half-centimetre gap above landscape must count as support within the one-centimetre margin");
    require(movableContact(gapContacts,105).disposition=="hangs" && farSupport.mass()==0,
            "landscape one and a half centimetres below a hull must not count as support");
    }

  void testMovableVobGeometryPenetration() {
    Resources resources;
    ProtoMesh proto(PackedMesh(movableCube(),PackedMesh::PK_Visual),"CONTRACT_CLAMP.MRM");
    ProtoMesh wall(PackedMesh(movableCollisionBox({20,200,200}),PackedMesh::PK_Visual),"CONTRACT_CLAMP_WALL.MRM");
    DynamicWorld world(nullptr,movableFloor());
    auto shallow=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(-100,7,0),.25f,true,"table",true,111);
    auto deep=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(0,4,0),.25f,true,"table",true,112);
    // The hull crosses the middle of the wall's -X face by five centimetres,
    // far from its top/bottom faces and outer edges.
    auto structure=world.addStaticObj(wall.attach[0].shape.get(),at(115,10,0),"CONTRACT_CLAMP_WALL.MRM");
    auto embedded=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(100,10,0),.25f,true,"table",true,113);
    auto first=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(300,10,0),.25f,true,"table",true,114);
    auto second=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(315,10,0),.25f,true,"table",true,115);
    require(!structure.isEmpty(),"penetration fixture must install its authored static C_Object before validation");
    world.finalizeMovableVobs();
    const auto contacts=world.movableVobContacts(1.f);
    require(movableContact(contacts,111).enabled && shallow.mass()>0,
            "three centimetres of landscape penetration must remain below the four-centimetre clamp threshold");
    require(movableContact(contacts,112).disposition=="floor" && deep.mass()==0,
            "more than four centimetres of landscape penetration must restore the static source mesh");
    const auto& blocked=movableContact(contacts,113);
    std::printf("Movable C_Object fixture: %s, mass %.6f kg",blocked.disposition.c_str(),double(embedded.mass()));
    for(const auto& contact:blocked.contacts)
      std::printf(" [%s, side %d, depth %.6f cm]",contact.other.c_str(),contact.side,double(contact.depth));
    std::puts("");
    require(blocked.disposition=="structure" && embedded.mass()==0,
            "more than four centimetres of static C_Object penetration must restore the static source mesh");
    for(const auto id:{114u,115u}) {
      const auto& entry=movableContact(contacts,id);
      require(!entry.enabled && entry.disposition=="neighbour",
              "both members of a deeply intersecting movable pair must become static together");
      require(std::any_of(entry.contacts.begin(),entry.contacts.end(),[&](const auto& contact) {
        return contact.category==DynamicWorld::C_MovableVob && contact.otherZenId==(id==114 ? 115u : 114u) &&
               contact.depth>4.f;
        }),"the retained origin census must identify the deeply intersecting movable neighbour");
      }
    require(first.mass()==0 && second.mass()==0,
            "deep-pair decisions must be computed before replacing either member's hull");
    }

  void testMovableVobGeometryTopObjects() {
    Resources resources;
    ProtoMesh base(PackedMesh(movableBox({100,100,100}),PackedMesh::PK_Visual),"CONTRACT_CLAMP_BASE.MRM");
    ProtoMesh small(PackedMesh(movableCollisionBox({20,20,20}),PackedMesh::PK_Visual),"CONTRACT_CLAMP_SMALL.MRM");
    ProtoMesh large(PackedMesh(movableCollisionBox({100,20,20}),PackedMesh::PK_Visual),"CONTRACT_CLAMP_LARGE.MRM");
    DynamicWorld world(nullptr,movableFloor());
    auto exempt=world.addMovableVob(base,"CONTRACT_CLAMP_BASE.MRM",at(0,50,0),.25f,true,"table",true,121);
    auto held=world.addMovableVob(base,"CONTRACT_CLAMP_BASE.MRM",at(300,50,0),.25f,true,"table",true,122);
    auto trinket=world.addStaticObj(small.attach[0].shape.get(),at(0,105,0),"CONTRACT_CLAMP_SMALL.MRM");
    auto load=world.addStaticObj(large.attach[0].shape.get(),at(300,110,0),"CONTRACT_CLAMP_LARGE.MRM");
    require(!trinket.isEmpty() && !load.isEmpty(),"top-object fixture must install both static source meshes");
    world.finalizeMovableVobs();
    const auto contacts=world.movableVobContacts(1.f);
    const auto& item=movableContact(contacts,121);
    require(item.enabled && item.disposition=="item" && exempt.mass()>0,
            "a small C_Object beginning above ninety percent of hull height must not clamp its support");
    require(std::any_of(item.contacts.begin(),item.contacts.end(),[](const auto& contact) {
      return contact.category==DynamicWorld::C_Object && contact.depth>4.f;
      }),"small-top exemption fixture must exercise penetration deeper than four centimetres");
    require(!movableContact(contacts,122).enabled && movableContact(contacts,122).disposition=="carries" && held.mass()==0,
            "a C_Object larger than eighty centimetres resting on the top must clamp its support");
    }

  void testMovableVobGeometryAuthoredToggle() {
    Resources resources;
    ProtoMesh proto(PackedMesh(movableCube(),PackedMesh::PK_Visual),"CONTRACT_CLAMP.MRM");
    ProtoMesh wall(PackedMesh(movableCollisionBox({20,200,200}),PackedMesh::PK_Visual),"CONTRACT_CLAMP_WALL.MRM");
    DynamicWorld world(nullptr,movableFloor());
    world.setMovableVobsEnabled(false);
    auto blocked=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(0,10,0),.25f,true,"table",true,141);
    auto clear=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",at(300,10,0),.25f,true,"table",true,142);
    auto obstacle=world.addStaticObj(wall.attach[0].shape.get(),at(15,10,0),"CONTRACT_CLAMP_WALL.MRM");
    require(!obstacle.isEmpty(),"runtime-toggle fixture must install its authored C_Object collision");
    world.finalizeMovableVobs();
    const auto authored=world.movableVobContacts(1.f);
    require(!world.movableVobsEnabled() && blocked.mass()==0 && clear.mass()==0,
            "capturing geometry while disabled must restore the disabled physics setting");
    require(movableContact(authored,141).disposition=="structure" && movableContact(authored,142).disposition=="ok",
            "finalization while disabled must capture the original authored obstruction for both candidates");
    // A door or scripted object can move before physics is enabled. Its current
    // pose must neither release the original clamp nor create a new one.
    obstacle.setObjMatrix(at(315,10,0));
    world.setMovableVobsEnabled(true);
    const auto enabled=world.movableVobContacts(1.f);
    require(!movableContact(enabled,141).enabled && blocked.mass()==0,
            "runtime enable must retain an authored clamp after its static obstruction moves away");
    require(movableContact(enabled,142).enabled && clear.mass()>0,
            "runtime enable must not invent a clamp from a static obstruction's later scripted pose");
    }

  void testMovableVobClampedSavePose() {
    Resources resources;
    ProtoMesh proto(PackedMesh(movableCube(),PackedMesh::PK_Visual),"CONTRACT_CLAMP.MRM");
    const auto origin=at(0,100,0);
    auto moved=at(75,125,0);
    moved.rotateOZ(60);
    const auto saved=saveArchive([&](Serialize& output) {
      output.setEntry("worlds/_/movable-vobs");
      output.write(uint32_t(0x31564f4d),uint16_t(2),std::string_view("_"),uint32_t(1));
      output.write(uint32_t(131),std::string_view("CONTRACT_CLAMP.MRM"),origin,moved,true);
      output.write(true,uint64_t(1501),std::string_view("PLAYER"));
      });
    DynamicWorld world(nullptr,movableFloor());
    auto object=world.addMovableVob(proto,"CONTRACT_CLAMP.MRM",origin,.25f,true,"table",true,131);
    Tempest::Matrix4x4 rendered=origin;
    object.setTransformCallback([&](const auto& matrix) { rendered=matrix; });
    world.finalizeMovableVobs();
    loadMovables(world,saved);
    const auto state=world.movableVobSnapshot();
    require(state.size()==1 && !state[0].enabled && object.mass()==0,
            "a saved pose must not restore a dynamic body for an authored geometry clamp");
    requireMatrix(state[0].transform,origin,"a shifted save record for a clamped vob must retain the original ZEN pose");
    requireMatrix(rendered,origin,"ignoring a clamped saved pose must retain the original render transform");
    requireMatrix(world.movableVobPose(131)->transform,origin,
                  "ignoring a clamped saved pose must retain the original attachment transform");
    require(!state[0].trace && !state[0].tilted && state[0].traceTime==-1 && state[0].traceCulprit.empty(),
            "ignoring a clamped saved pose must also ignore its moved trace metadata");
    }

  void testMovableVobSave() {
    Resources resources;
    ProtoMesh proto(PackedMesh(movableCube(),PackedMesh::PK_Visual),"CONTRACT_CUBE.MRM");
    constexpr uint32_t zenId=42;
    auto origin=at(120,100,-200);
    origin.rotateOY(17.f);
    auto moved=at(170.125f,118.5f,-219.25f);
    moved.rotateOX(13.f);
    moved.rotateOY(39.f);
    // The independent optional entry can describe a settled pose without having to
    // wait for a game asset or a particular native solver trajectory in this fixture.
    auto savedRecord=[&](std::string_view worldName, uint32_t id, std::string_view visual,
                         const Tempest::Matrix4x4& birth) {
      return saveArchive([&](Serialize& output) {
        output.setEntry("worlds/_/movable-vobs");
        output.write(uint32_t(0x31564f4d),uint16_t(1),worldName,uint32_t(1));
        output.write(id,visual,birth,moved,true);
        });
      };
    const auto saved=savedRecord("_",zenId,"CONTRACT_CUBE.MRM",origin);
    DynamicWorld first(nullptr,zenkit::Mesh{});
    auto firstObject=first.addMovableVob(proto,"CONTRACT_CUBE.MRM",origin,.25f,true,"table",true,zenId);
    require(!firstObject.isEmpty(),"movable save fixture must have a physical body");
    loadMovables(first,saved);
    auto firstState=first.movableVobSnapshot();
    require(firstState.size()==1 && firstState[0].zenId==zenId && firstState[0].sleeping,
            "saved movable identity and sleep must restore in a fresh world");
    requireMatrix(firstState[0].origin,origin,"restore must retain the original ZEN frame");
    requireMatrix(firstState[0].transform,moved,"restore must retain moved translation and rotation");
    const auto roundtrip=saveArchive([&](Serialize& output) { first.saveMovableVobs(output); });

    DynamicWorld second(nullptr,zenkit::Mesh{});
    auto secondObject=second.addMovableVob(proto,"CONTRACT_CUBE.MRM",origin,.25f,true,"table",true,zenId);
    loadMovables(second,roundtrip);
    require(secondObject.isSleeping(),"a sleeping saved vob must remain asleep after a save roundtrip");
    auto pose=second.movableVobPose(zenId);
    require(pose!=nullptr && pose->zenId==zenId && pose->visual=="CONTRACT_CUBE.MRM",
            "attachments must find a loaded vob by its stable ZEN identity");
    requireMatrix(pose->transform,moved,"attachment pose must synchronize during restore before any tick");
    for(int i=0;i<1800;++i)
      second.tick(17);
    require(secondObject.isSleeping(),"restored furniture must remain asleep for thirty seconds");
    requireMatrix(second.movableVobSnapshot()[0].transform,moved,"sleeping restored furniture must not drift");
    const auto pinLocal=at(2,3,4);
    const auto pinBefore=pose->transform*pinLocal;
    require(secondObject.addImpulse({secondObject.mass(),0,0}),"restored furniture must still accept impulses");
    second.tick(17);
    const auto after=second.movableVobSnapshot()[0].transform;
    requireMatrix(pose->transform,after,"attachment pose must follow later furniture motion");
    require(std::abs((pose->transform*pinLocal).at(3,0)-pinBefore.at(3,0))>.01f,
            "a local arrow attachment must travel with a restored vob after another push");

    const std::vector<std::vector<uint8_t>> rejected = {
      savedRecord("OTHERWORLD.ZEN",zenId,"CONTRACT_CUBE.MRM",origin),
      savedRecord("_",zenId+1,"CONTRACT_CUBE.MRM",origin),
      savedRecord("_",zenId,"OTHER_CUBE.MRM",origin),
      savedRecord("_",zenId,"CONTRACT_CUBE.MRM",at(121,100,-200)),
      saveArchive([](Serialize& output) { output.setEntry("old-save"); output.write(uint8_t(1)); })
      };
    for(const auto& bytes:rejected) {
      DynamicWorld other(nullptr,zenkit::Mesh{});
      auto object=other.addMovableVob(proto,"CONTRACT_CUBE.MRM",origin,.25f,true,"table",true,zenId);
      loadMovables(other,bytes);
      requireMatrix(other.movableVobSnapshot()[0].transform,origin,
                    "old saves and mismatched world, ZEN id, visual or origin must retain the ZEN pose");
      require(object.isSleeping(),"ignoring an optional record must not wake furniture");
      }
    DynamicWorld disabled(nullptr,zenkit::Mesh{});
    disabled.setMovableVobsEnabled(false);
    auto disabledObject=disabled.addMovableVob(proto,"CONTRACT_CUBE.MRM",origin,.25f,true,"table",true,zenId);
    loadMovables(disabled,saved);
    requireMatrix(disabled.movableVobSnapshot()[0].transform,origin,
                  "movable_vobs=0 must ignore saved motion and keep the ZEN frame");
    }

  void testMovableTraceThresholds() {
    Resources resources;
    ProtoMesh proto(PackedMesh(movableCube(),PackedMesh::PK_Visual),"CONTRACT_CUBE.MRM");
    constexpr uint32_t zenId=43;
    const auto origin=at(0,100,0);
    DynamicWorld world(nullptr,zenkit::Mesh{});
    auto object=world.addMovableVob(proto,"CONTRACT_CUBE.MRM",origin,.25f,true,"table",true,zenId);
    require(!object.isEmpty(),"trace fixture must have a movable body");
    world.setTraceTime(1500);
    for(const float distance:{10.f,50.f}) {
      require(world.setMovableVobTransform(zenId,at(distance,100,0)),"trace fixture must accept controlled translation");
      const auto state=world.movableVobSnapshot()[0];
      require(!state.trace && !state.tilted && state.traceTime==-1,
              "ten or exactly fifty centimetres of displacement must not create a trace");
      }
    require(world.setMovableVobTransform(zenId,at(50.1f,100,0)),"trace fixture must accept above-threshold displacement");
    auto state=world.movableVobSnapshot()[0];
    require(state.trace && !state.tilted && state.traceTime==1500 && state.traceCulprit.empty(),
            "displacement beyond fifty centimetres must create an unknown-culprit trace at game time");
    world.setTraceTime(1530);
    require(world.setMovableVobTransform(zenId,at(75,100,0)),"an existing trace must accept further movement");
    require(world.movableVobSnapshot()[0].traceTime==1500,
            "later furniture movement must retain the first trace appearance time");

    const auto roundtrip=saveArchive([&](Serialize& output) { world.saveMovableVobs(output); });
    DynamicWorld restored(nullptr,zenkit::Mesh{});
    auto restoredObject=restored.addMovableVob(proto,"CONTRACT_CUBE.MRM",origin,.25f,true,"table",true,zenId);
    restored.setTraceTime(2000);
    loadMovables(restored,roundtrip);
    state=restored.movableVobSnapshot()[0];
    require(state.trace && state.traceTime==1500 && state.traceCulprit.empty(),
            "a v2 save roundtrip must retain trace appearance rather than load time");

    for(const float degrees:{30.f,31.f}) {
      auto rotated=origin;
      rotated.rotateOX(degrees);
      require(world.setMovableVobTransform(zenId,rotated),"trace fixture must accept a tilted pose");
      state=world.movableVobSnapshot()[0];
      require(state.trace==(degrees>30.f) && state.tilted==(degrees>30.f),
              "exactly thirty degrees must not be a trace, but thirty-one degrees must be tilted");
      }
    auto yaw=origin;
    yaw.rotateOY(140.f);
    require(world.setMovableVobTransform(zenId,yaw),"trace fixture must accept an upright yaw rotation");
    state=world.movableVobSnapshot()[0];
    require(!state.trace && !state.tilted && state.traceTime==-1,
            "yaw-only rotation must not create a fallen-furniture trace");

    const auto moved=at(75,100,0);
    for(const uint16_t schema:{uint16_t(1),uint16_t(2)}) {
      const auto saved=saveArchive([&](Serialize& output) {
        output.setEntry("worlds/_/movable-vobs");
        output.write(uint32_t(0x31564f4d),schema,std::string_view("_"),uint32_t(1));
        output.write(zenId,std::string_view("CONTRACT_CUBE.MRM"),origin,moved,true);
        if(schema>=2)
          output.write(true,uint64_t(1501),std::string_view("PLAYER"));
        });
      DynamicWorld loaded(nullptr,zenkit::Mesh{});
      auto loadedObject=loaded.addMovableVob(proto,"CONTRACT_CUBE.MRM",origin,.25f,true,"table",true,zenId);
      loaded.setTraceTime(2200);
      loadMovables(loaded,saved);
      state=loaded.movableVobSnapshot()[0];
      require(state.trace && !state.tilted && state.zenId==zenId,
              "both optional save schemas must recover the same moved trace identity");
      require(state.traceTime==(schema==1 ? -1 : 1500) && state.traceCulprit==(schema==1 ? "" : "PLAYER"),
              "legacy trace time and culprit must remain unknown; v2 must retain engine truth");
      require(loadedObject.addImpulse({loadedObject.mass(),0,0}),"a loaded trace must accept another impulse");
      loaded.tick(17);
      const auto afterImpulse=loaded.movableVobSnapshot()[0];
      require(afterImpulse.trace && afterImpulse.traceTime==state.traceTime && afterImpulse.traceCulprit==state.traceCulprit,
              "a later unattributed impulse must not rewrite an existing trace's first time or culprit");
      const auto savedAgain=saveArchive([&](Serialize& output) { loaded.saveMovableVobs(output); });
      DynamicWorld reloaded(nullptr,zenkit::Mesh{});
      auto reloadedObject=reloaded.addMovableVob(proto,"CONTRACT_CUBE.MRM",origin,.25f,true,"table",true,zenId);
      reloaded.setTraceTime(2300);
      loadMovables(reloaded,savedAgain);
      const auto reloadedState=reloaded.movableVobSnapshot()[0];
      require(reloadedState.trace && reloadedState.traceTime==state.traceTime && reloadedState.traceCulprit==state.traceCulprit,
              "saving either legacy unknown metadata or v2 known metadata must preserve it in the next process");
      }
    }

  void testPhysicsNoise() {
    CollisionWorld world;
    std::vector<PhysicsNoise> noises;
    std::vector<uint64_t> steps;
    world.setPhysicsNoiseCallback([&](const PhysicsNoise& noise) {
      noises.push_back(noise);
      steps.push_back(world.simulationStep());
      });
    require(world.physicsNoiseEnabled(),"physics noise must be enabled by default");
    auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.7f,
                                      DynamicWorld::C_Landscape);
    auto cup=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3::sReplicate(.05f)),at(0,100,0),.7f,.15f);
    cup->setNoiseProfile(MAT_CLAY,zenkit::MaterialGroup::STONE,"cup");
    auto table=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3::sReplicate(.35f)),at(300,100,0),.7f,50.f,
                                    DynamicWorld::C_MovableVob);
    table->setNoiseProfile(MAT_WOOD,zenkit::MaterialGroup::WOOD,"table");
    auto soft=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3::sReplicate(.05f)),at(-300,5.1f,0),.7f,.15f);
    soft->setNoiseProfile(MAT_CLAY,zenkit::MaterialGroup::STONE,"soft");
    auto wood=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(.3f,.1f,.3f)),at(600,40,0),.7f,DynamicWorld::C_Object);
    wood->setNoiseProfile(MAT_WOOD,zenkit::MaterialGroup::WOOD,"wood");
    auto metal=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3::sReplicate(.05f)),at(600,100,0),.7f,1.f);
    metal->setNoiseProfile(MAT_METAL,zenkit::MaterialGroup::METAL,"metal");
    for(int i=0;i<120;++i) world.tick(17);
    const auto find=[&](std::string_view what) -> const PhysicsNoise* {
      for(const auto& noise:noises) if(noise.what==what) return &noise;
      return nullptr;
      };
    const auto* cupNoise=find("cup");
    const auto* tableNoise=find("table");
    const auto* metalNoise=find("metal");
    require(cupNoise!=nullptr && tableNoise!=nullptr && metalNoise!=nullptr,
            "items, movable furniture and contact with another object must emit noise");
    require(find("soft")==nullptr,"resting/subthreshold contact must remain silent");
    require(cupNoise->material==MAT_CLAY && cupNoise->radius<300.f && cupNoise->culprit==nullptr,
            "a small fallen cup must retain clay material, local radius and unknown culprit");
    require(tableNoise->material==MAT_WOOD && tableNoise->radius>=600.f && tableNoise->volume>cupNoise->volume,
            "a heavy table must be louder and audible at six metres");
    require(metalNoise->surface==zenkit::MaterialGroup::WOOD && metalNoise->material==MAT_METAL,
            "noise must retain both source and struck surface materials");
    requireNear(cupNoise->position.y,0.f,2.f,"noise position must be the contact in world centimetres");
    for(size_t i=0;i<noises.size();++i)
      for(size_t j=i+1;j<noises.size();++j)
        if(noises[i].body==noises[j].body)
          require(steps[j]>=steps[i]+29,"a logical body must not emit more often than every half second");
    const auto before=noises.size();
    world.setPhysicsNoiseEnabled(false);
    cup->restoreTransform(at(0,100,0),false);
    for(int i=0;i<60;++i) world.tick(17);
    require(noises.size()==before && !world.physicsNoiseEnabled(),"disabled physics noise must not emit events");
    world.setPhysicsNoiseEnabled(true);
    cup->restoreTransform(at(0,100,0),false);
    for(int i=0;i<60 && noises.size()==before;++i) world.tick(17);
    require(noises.size()>before,"runtime re-enable must restore impact noise");
    // Repeated hard contacts before the same half-second expires must share
    // the body's cooldown, including multiple collision manifolds/substeps.
    const auto firstCount=noises.size();
    cup->restoreTransform(at(0,5,0),false);
    require(cup->addImpulse({0,-2.f,0}),"cooldown fixture must accept a second hard impact");
    world.tick(17);
    require(noises.size()==firstCount,"a second immediate impact must be throttled per body");
    }

  void testRagdollNoise() {
    zenkit::ModelHierarchy hierarchy;
    const char* names[]={"BIP01","BIP01 PELVIS","BIP01 SPINE","BIP01 HEAD"};
    const float offsets[]={0,95,40,40};
    for(int i=0;i<4;++i) {
      zenkit::ModelHierarchyNode node;
      node.name=names[i];
      node.parent_index=int16_t(i-1);
      const auto matrix=at(0,offsets[i],0);
      std::memcpy(&node.transform,&matrix,sizeof(matrix));
      hierarchy.nodes.push_back(node);
      }
    Skeleton skeleton(hierarchy,nullptr,"NOISE_HUMAN.MDS");
    HitZones zones;
    zones.setSkeleton(&skeleton);
    require(zones.isValid(),"noise ragdoll fixture must contain canonical capsules");
    CollisionWorld world;
    auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.7f,
                                      DynamicWorld::C_Landscape);
    auto object=at(0,100,0);
    object.rotateOZ(90.f);
    std::vector<Tempest::Matrix4x4> pose;
    for(const auto& bone:skeleton.tr) pose.push_back(object*bone);
    auto body=world.createRagdoll(skeleton,zones,pose.data(),pose.size(),object,{0,-600,0});
    require(body!=nullptr && body->partCount()>1,"noise fixture must create several physical limbs");
    std::vector<uint64_t> steps;
    uint64_t identity=0;
    world.setPhysicsNoiseCallback([&](const PhysicsNoise& noise) {
      require(noise.material==MAT_LEATHER && noise.what=="ragdoll","body noise must retain its material and source");
      if(identity!=0) require(noise.body==identity,"limbs must share one logical noise identity");
      identity=noise.body;
      if(!steps.empty()) require(world.simulationStep()>=steps.back()+29,"ragdoll limbs must share a half-second cooldown");
      steps.push_back(world.simulationStep());
      });
    for(int i=0;i<120;++i) world.tick(17);
    require(!steps.empty(),"a fallen ragdoll must emit impact noise");
    }

  void testOffCenterImpulse() {
    CollisionWorld world;
    auto centered=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3::sReplicate(.5f)),at(0,1000,0),.7f,2.f);
    auto tipped=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3::sReplicate(.5f)),at(300,1000,0),.7f,2.f);
    require(centered->addImpulse({2,0,0}),"centre-of-mass fixture must accept an impulse");
    require(tipped->addImpulseAtPoint({2,0,0},{300,1050,0}),"off-centre fixture must accept an impulse at a world point");
    require(!tipped->addImpulseAtPoint({2,0,0},{0,std::numeric_limits<float>::infinity(),0}),
            "a nonfinite application point must be rejected");
    world.tick(17);
    const auto straight=centered->matrix(),rotated=tipped->matrix();
    requireNear(straight.at(0,1),0.f,.00001f,"centre impulse must preserve the cube's orientation");
    require(std::abs(rotated.at(0,1))>.01f,"an impulse above the centre of mass must rotate the body");
    requireNear(rotated.at(3,0)-straight.at(3,0),300.f,.001f,
                "the application point changes angular response while preserving linear impulse");
    }

  void testMovableVobArrowIdentity() {
    Resources resources;
    ProtoMesh proto(PackedMesh(movableCube(),PackedMesh::PK_Visual),"CONTRACT_CUBE.MRM");
    DynamicWorld world(nullptr,zenkit::Mesh{});
    auto object=world.addMovableVob(proto,"CONTRACT_CUBE.MRM",at(0,100,0),.25f,true,"table",true,42);
    struct ArrowContact final : DynamicWorld::BulletCallback {
      bool stopsAtWorldHit() const override { return true; }
      void onWorldHit(const DynamicWorld::BulletWorldHit& hit) override {
        ++hits;
        movable=hit.movable;
        }
      int hits=0;
      std::weak_ptr<const DynamicWorld::MovableVobPose> movable;
      } contact;
    auto* arrow=world.bulletObj(&contact);
    arrow->setPosition({-40,100,0});
    arrow->setDirection({3,0,0});
    world.tick(17);
    require(contact.hits==1 && !contact.movable.expired(),
            "a world arrow contact must identify the movable body it hit");
    require(contact.movable.lock()==world.movableVobPose(42),
            "arrow contact identity must share the live ZEN vob attachment pose");
    world.deleteObj(arrow);
    object=DynamicWorld::Item();
    require(contact.movable.expired() && world.movableVobPose(42)==nullptr,
            "removing a vob must expire weak arrow attachments without leaving a dangling body");
    }

  void testMovableVobLegacyCollision() {
    // Exercise the real mesh cache and rebuild/toggle path, rather than constructing
    // query proxies directly. No game assets or graphics device are required.
    Resources resources;
    ProtoMesh proto(PackedMesh(movableCube(),PackedMesh::PK_Visual),"CONTRACT_CUBE.MRM");
    zenkit::ModelHierarchy hierarchy;
    hierarchy.collision_bbox = {{-5,-10,-5},{5,10,5}};
    Skeleton walkerShape(hierarchy,nullptr,"CONTRACT_WALKER");
    for(const bool blocksWalking:{false,true}) {
      for(const bool legacyCollision:{false,true}) {
        DynamicWorld world(nullptr,zenkit::Mesh{});
        world.setMovableVobsEnabled(false);
        auto object = world.addMovableVob(proto,"CONTRACT_CUBE.MRM",at(0,100,0),.25f,
                                          blocksWalking,blocksWalking ? "table" : "utensil",legacyCollision);
        require(!object.isEmpty(),"disabled movable vobs must retain their handle for re-enabling");
        for(const bool enabled:{false,true,false,true}) {
          world.setMovableVobsEnabled(enabled);
          const auto snapshot = world.movableVobSnapshot();
          require(snapshot.size()==1 && snapshot[0].legacyCollision==legacyCollision,
                  "movable snapshots must retain the original cd_dynamic flag across toggles");
          require(snapshot[0].enabled==enabled && (object.mass()>0)==enabled,
                  "cd_dynamic must not suppress an enabled movable simulation hull");
          require(world.physicsStats().bodies==size_t(enabled)+size_t(legacyCollision),
                  "only cd_dynamic vobs may create query bodies in either mode");
          require(object.isSleeping(),"rebuilding a movable vob must start its simulation asleep");
          const Tempest::Vec3 from{-30,100,0},to{30,100,0};
          require(world.ray(from,to).hasCol==legacyCollision,
                  "camera, foot IK and visibility rays must preserve original mesh collision eligibility");
          require(world.rayArrow(from,to,[](Npc&,const Tempest::Vec3&,const Tempest::Vec3&,float&) { return false; }).hasCol==legacyCollision,
                  "arrow rays must preserve original mesh collision eligibility");
          const bool groundHit = legacyCollision && (!enabled || blocksWalking);
          require(world.landRay({0,130,0},60).hasCol==groundHit,
                  "landRay must ignore non-colliding vobs and enabled small utensils");
          require(world.sweep({-30,100,0},{30,100,0},2).hasCol==groundHit,
                  "movement sweeps must preserve original mesh collision eligibility");
          auto walker = world.addGhostObj(&walkerShape);
          walker.setPosition({12,70,0});
          require(walker.hasCollision()==groundHit,
                  "walking must ignore cd_dynamic=0 vobs for large furniture and utensils alike");
          if(enabled) {
            require(object.addImpulse({object.mass(),0,0}),
                    "both colliding and non-colliding movable vobs must accept physical impulses");
            require(!object.isSleeping() && object.velocity().x>0,
                    "cd_dynamic=0 hulls must remain active simulation bodies after an impulse");
            }
          }
        }

      DynamicWorld world(nullptr,zenkit::Mesh{});
      auto moving = world.addMovableVob(proto,"CONTRACT_CUBE.MRM",at(0,100,0),.25f,
                                        blocksWalking,"impactor",false);
      auto target = world.addMovableVob(proto,"CONTRACT_CUBE.MRM",at(30,100,0),.25f,
                                        blocksWalking,"target",false);
      require(!moving.isEmpty() && !target.isEmpty() && target.isSleeping(),
              "physical contact fixture must start with two sleeping cd_dynamic=0 hulls");
      require(moving.addImpulse({moving.mass()*2.f,0,0}),"contact fixture must wake the impactor");
      for(int i=0;i<15;++i)
        world.tick(17);
      require(!target.isSleeping() && target.velocity().x>1.f,
              "a cd_dynamic=0 movable hull must wake and push another hull through physical contact");
      require(world.physicsStats().bodies==2,
              "physical contact between cd_dynamic=0 hulls must not require query proxies");
      }
    }

  void testMovableVobBodies() {
    CollisionWorld world;
    const JPH::Vec3 points[] = {
      {-.1f,-.1f,-.1f},{-.1f,-.1f,.1f},{-.1f,.1f,-.1f},{-.1f,.1f,.1f},
      {.1f,-.1f,-.1f},{.1f,-.1f,.1f},{.1f,.1f,-.1f},{.1f,.1f,.1f}
      };
    const auto hull = JPH::ConvexHullShapeSettings(points,8,0.f).Create();
    require(!hull.HasError(),"movable vob convex hull must be constructible");
    const float mass = hull.Get()->GetVolume()*DynamicWorld::materialDensity(zenkit::MaterialGroup::WOOD)*.25f;
    requireNear(mass,.008f*700.f*.25f,.00001f,"mass must use convex hull volume and class multiplier");
    auto floor = world.addCollisionBody(new JPH::BoxShape({10,.1f,10}),at(0,-10,0),.65f,DynamicWorld::C_Landscape);
    auto movable = world.addDynamicBody(hull.Get(),at(0,100,0),.5f,mass,DynamicWorld::C_MovableVob,true);
    const auto initial = movable->position();
    for(int i=0;i<3600;++i)
      world.tick(17);
    require(movable->isSleeping(),"fresh movable vobs must stay asleep for 60 seconds");
    require((movable->position()-initial).length()<=.001f,"sleeping movable vobs must not drift under gravity");
    require(world.bodyCount()==2,"sleeping movable vobs must remain registered bodies");
    Tempest::Vec3 normal;
    Interactive* vob = nullptr;
    JPH::SphereShape walker(.05f);
    require(!world.hasCollision(&walker,{0,100,0},normal,vob),"convex simulation hull must not replace legacy walking queries");

    JPH::TriangleList triangles;
    triangles.emplace_back(JPH::Float3(-.2f,0,-.2f),JPH::Float3(0,0,.2f),JPH::Float3(.2f,0,-.2f));
    const auto queryMesh = JPH::MeshShapeSettings(triangles).Create();
    require(!queryMesh.HasError(),"movable source query mesh must be constructible");
    auto smallQuery = world.addCollisionBody(queryMesh.Get(),at(0,100,0),.5f,DynamicWorld::C_SmallVobQuery,true,true);
    require(!world.hasCollision(&walker,{0,101,0},normal,vob),"small utensil mesh must not obstruct walking");
    auto largeQuery = world.addCollisionBody(queryMesh.Get(),at(50,100,0),.5f,DynamicWorld::C_MovableQuery,true,true);
    require(world.hasCollision(&walker,{50,101,0},normal,vob),"large furniture must obstruct walking through its source mesh");
    const uint32_t queryMask = (1u<<DynamicWorld::C_MovableQuery)|(1u<<DynamicWorld::C_SmallVobQuery);
    require(world.rayCast({0,130,0},{0,90,0},queryMask).hasHit,"small vob source mesh must remain aimable");
    const std::vector<const CollisionWorld::CollisionBody*> excluded = {smallQuery.get()};
    require(!world.rayCast({0,130,0},{0,90,0},queryMask,false,nullptr,&excluded).hasHit,
            "spell visibility must exclude the target's own source mesh");
    require(movable->addImpulse({mass*2.f,mass*.5f,0}),"spell impulse must wake a movable vob");
    require(!movable->isSleeping(),"an impulse must activate a sleeping movable vob");
    for(int i=0;i<900;++i)
      world.tick(17);
    require(std::abs(movable->position().x-initial.x)>20.f,"a movable vob impulse must produce physical displacement");
    require(movable->isSleeping(),"movable vob must settle within 15 seconds after an impulse");
    require(world.bodyCount()==4,"settled movable vob must retain its registered body");

    // Source vertices keep attachment-local transforms before hull creation.
    std::vector<JPH::Vec3> source = {{1,2,3},{2,2,3},{1,3,3}};
    PhysicVbo sourceMesh(&source);
    sourceMesh.addIndex({0,1,2},0,3,zenkit::MaterialGroup::WOOD);
    auto local = at(100,200,300);
    local.scale(2,3,4);
    std::vector<JPH::Vec3> transformed;
    sourceMesh.appendVertices(transformed,local);
    require(transformed.size()==3,"hull input must retain every referenced source vertex");
    requireNear(transformed[0].GetX(),3.f,.00001f,"hull source input must apply local attachment X transform");
    requireNear(transformed[0].GetY(),8.f,.00001f,"hull source input must apply local attachment Y transform");
    requireNear(transformed[0].GetZ(),15.f,.00001f,"hull source input must apply local attachment Z transform");
    }

  struct SpherePrecisionCollector final : JPH::CastShapeCollector {
    void AddHit(const JPH::ShapeCastResult& result) override {
      if(!hasHit || result.mFraction<hit.mFraction) {
        hit = result;
        hasHit = true;
        }
      }
    JPH::ShapeCastResult hit;
    bool hasHit = false;
    };

  SpherePrecisionCollector castSphereTriangle(const std::array<JPH::Vec3,3>& triangle,
                                              JPH::Vec3Arg start, JPH::Vec3Arg direction,
                                              float radius, const JPH::ShapeCastSettings& settings,
                                              JPH::Vec3Arg scale = JPH::Vec3::sOne(),
                                              JPH::Mat44Arg transform = JPH::Mat44::sIdentity(),
                                              float cutoff = 1.f+FLT_EPSILON) {
    JPH::SphereShape sphere(radius);
    const JPH::ShapeCast cast(&sphere,JPH::Vec3::sOne(),JPH::Mat44::sTranslation(start),direction);
    SpherePrecisionCollector collector;
    collector.ResetEarlyOutFraction(cutoff);
    JPH::CastSphereVsTriangles query(cast,settings,scale,transform,JPH::SubShapeIDCreator(),collector);
    query.Cast(triangle[0],triangle[1],triangle[2],0b111,JPH::SubShapeID());
    return collector;
    }

  void testSphereQueryPrecision() {
    JPH::ShapeCastSettings defaults;
    defaults.SetBackFaceMode(JPH::EBackFaceMode::CollideWithBackFaces);
    defaults.mActiveEdgeMode = JPH::EActiveEdgeMode::CollideWithAll;
    require(!defaults.mUsePreciseSphereTriangleCast,"native sphere triangle precision must remain an explicit opt-in");
    auto disabled = defaults;
    disabled.mUsePreciseSphereTriangleCast = false;
    auto precise = defaults;
    precise.mUsePreciseSphereTriangleCast = true;

    // Query 2179, with the actual decoded child-local input. The complete segment
    // misses this triangle by 2.54 mm beyond the 5 cm sphere radius. Float products
    // make the edge quadratic discriminant positive; the precise query rejects it.
    const std::array<JPH::Vec3,3> separatedEdge = {
      JPH::Vec3(-39.3174744f,-4.08496571f,15.2057877f),
      JPH::Vec3(-39.835434f,-2.79672146f,16.8955078f),
      JPH::Vec3(-38.9809036f,-3.76561832f,15.1747742f)
      };
    const JPH::Vec3 edgeStart(61.133728f,1.71995163f,12.8343124f);
    const JPH::Vec3 edgeDirection(-217.075928f,-11.5681381f,5.39838409f);
    const auto nativeEdge = castSphereTriangle(separatedEdge,edgeStart,edgeDirection,.049999997f,defaults);
    const auto explicitNativeEdge = castSphereTriangle(separatedEdge,edgeStart,edgeDirection,.049999997f,disabled);
    require(nativeEdge.hasHit && explicitNativeEdge.hasHit,
            "default native edge behavior must remain available to callers without the precision opt-in");
    require(nativeEdge.hit.mFraction==explicitNativeEdge.hit.mFraction &&
            nativeEdge.hit.mContactPointOn2==explicitNativeEdge.hit.mContactPointOn2 &&
            nativeEdge.hit.mPenetrationAxis==explicitNativeEdge.hit.mPenetrationAxis,
            "default and explicitly disabled precision must preserve the full native edge result");
    require(!castSphereTriangle(separatedEdge,edgeStart,edgeDirection,.049999997f,precise).hasHit,
            "a sphere segment separated from an edge must reject a rounded false discriminant");

    // Query 37252 is a very narrow face. Float Gram products report an interior
    // point while the entire segment is 60.5 cm beyond the one-meter radius.
    const std::array<JPH::Vec3,3> separatedThinFace = {
      JPH::Vec3(.549634933f,3.00050545f,64.0834045f),
      JPH::Vec3(1.74020004f,3.00050545f,59.6401825f),
      JPH::Vec3(3.03430557f,3.00050545f,54.8105469f)
      };
    const JPH::Vec3 thinStart(1.00256348f,17.6009464f,56.1907692f);
    const JPH::Vec3 thinDirection(0,-20.4721184f,0);
    require(castSphereTriangle(separatedThinFace,thinStart,thinDirection,1.f,defaults).hasHit,
            "default precision must preserve the native narrow-face result");
    require(!castSphereTriangle(separatedThinFace,thinStart,thinDirection,1.f,precise).hasHit,
            "a sphere cast outside a narrow face must reject rounded interior barycentrics");

    const std::array<JPH::Vec3,3> triangle = {
      JPH::Vec3(0,0,0),JPH::Vec3(0,0,2),JPH::Vec3(2,0,0)
      };
    const auto edge = castSphereTriangle(triangle,{-.25f,2,1},{0,-4,0},.5f,precise);
    require(edge.hasHit,"precise casts must retain a genuine edge contact");
    requireNear(edge.hit.mFraction,(2.f-std::sqrt(.5f*.5f-.25f*.25f))/4.f,0.000001f,
                "edge entry fraction must match the sphere cross section");
    const auto tangent = castSphereTriangle(triangle,{-.5f,2,1},{0,-4,0},.5f,precise);
    require(tangent.hasHit,"precise casts must retain exact edge tangencies");
    requireNear(tangent.hit.mFraction,.5f,0.000001f,"tangent contact must lie at the triangle plane");
    require(!castSphereTriangle(triangle,{-std::nextafter(.5f,1.f),2,1},{0,-4,0},.5f,precise).hasHit,
            "one representable step beyond the tangent must miss");
    const auto vertex = castSphereTriangle(triangle,{-.25f,2,-.25f},{0,-4,0},.5f,precise);
    require(vertex.hasHit,"precise casts must retain a genuine vertex contact");
    requireNear(vertex.hit.mFraction,(2.f-std::sqrt(.5f*.5f-2.f*.25f*.25f))/4.f,0.000001f,
                "vertex entry fraction must match the sphere cross section");

    // The game's sweep collector accepts fractions strictly below one.
    require(!castSphereTriangle(triangle,{.5f,2,.5f},{0,-1.5f,0},.5f,precise,
                               JPH::Vec3::sOne(),JPH::Mat44::sIdentity(),1.f).hasHit,
            "the precise query must respect an exclusive endpoint cutoff");
    const auto beforeEnd = castSphereTriangle(triangle,{.5f,2,.5f},{0,-1.5001f,0},.5f,precise,
                                              JPH::Vec3::sOne(),JPH::Mat44::sIdentity(),1.f);
    require(beforeEnd.hasHit && beforeEnd.hit.mFraction<1.f,
            "a real contact immediately before the endpoint must survive");
    require(!castSphereTriangle(triangle,{.5f,2,.5f},{0,-1.4999f,0},.5f,precise,
                               JPH::Vec3::sOne(),JPH::Mat44::sIdentity(),1.f).hasHit,
            "a contact beyond the segment endpoint must miss");

    const auto transform = JPH::Mat44::sRotationTranslation(
        JPH::Quat::sRotation(JPH::Vec3::sAxisY(),.5f*JPH::JPH_PI),JPH::Vec3(10,20,-30));
    const auto mirrored = castSphereTriangle(triangle,{-1,2,1.5f},{0,-4,0},.25f,precise,
                                             JPH::Vec3(-2,.5f,3),transform);
    require(mirrored.hasHit,"precise sphere queries must retain contacts with mirrored nonuniform meshes");
    requireNear(mirrored.hit.mFraction,.4375f,.000001f,"mesh scale must preserve the plane entry fraction");
    requireNear(mirrored.hit.mContactPointOn2.GetX(),11.5f,.00001f,"converted contact must preserve rotated world X");
    requireNear(mirrored.hit.mContactPointOn2.GetY(),20.f,.00001f,"converted contact must preserve world Y");
    requireNear(mirrored.hit.mContactPointOn2.GetZ(),-29.f,.00001f,"converted contact must preserve rotated world Z");
    }

  // These 12 original terrain faces reproduce all 107 logged Bullet normals.
  // Source log values used %.9g, which round-trip to the exact binary32 bits.
  void testSourceTerrainNormals() {
    struct Fixture {
      JPH::Vec3 vertices[3];
      uint32_t expected[3];
      };
    const Fixture fixtures[] = {
      { { {88.6986008f,5.17765617f,121.866676f},
          {91.4136581f,2.34256101f,112.210762f},
          {86.9217529f,5.54688644f,119.338539f} },
        { 0x3ece733du,0x3f67077eu,0xbe1b3b85u } }, // original source triangle 243603
      { { {86.9217529f,5.54688644f,119.338539f},
          {83.5601044f,7.12607956f,127.19075f},
          {88.6986008f,5.17765617f,121.866676f} },
        { 0x3e95ea55u,0x3f743014u,0xbd882751u } }, // original source triangle 243690
      { { {83.5601044f,7.12607956f,127.19075f},
          {86.9217529f,5.54688644f,119.338539f},
          {81.0413666f,7.49531031f,124.353905f} },
        { 0x3e78f42au,0x3f773e8au,0xbdb8a232u } }, // original source triangle 243717
      { { {86.9217529f,5.54688644f,119.338539f},
          {91.4136581f,2.34256101f,112.210762f},
          {86.245079f,5.06495762f,118.298805f} },
        { 0xbc2262c0u,0x3f68e0a7u,0xbed49493u } }, // original source triangle 243511
      { { {80.3646851f,7.01338053f,123.314186f},
          {86.9217529f,5.54688644f,119.338539f},
          {86.245079f,5.06495762f,118.298805f} },
        { 0xbd1a570cu,0x3f6a6f8cu,0xbeccc585u } }, // original source triangle 243513
      { { {86.245079f,3.88401794f,118.298828f},
          {79.8359985f,4.35840225f,123.528473f},
          {80.3646851f,5.83244085f,123.314186f} },
        { 0xbf1f942bu,0x3de37085u,0xbf4625b5u } }, // original source triangle 50088
      { { {79.8359985f,4.35840225f,123.528473f},
          {86.245079f,3.88401794f,118.298828f},
          {83.1608734f,3.53122926f,116.41391f} },
        { 0xbd08c995u,0x3f7da809u,0xbe05f250u } }, // original source triangle 243510
      { { {86.9217529f,5.54688644f,119.338539f},
          {80.3646851f,7.01338053f,123.314186f},
          {81.0413666f,7.49531031f,124.353905f} },
        { 0xbd1a59e8u,0x3f6a6f6du,0xbeccc606u } }, // original source triangle 243512
      { { {79.8359985f,4.35840225f,123.528473f},
          {83.1608734f,3.53122926f,116.41391f},
          {78.0155869f,3.55711174f,116.974243f} },
        { 0xbc02ca6au,0x3f7e2aefu,0xbdf40bdeu } }, // original source triangle 243504
      { { {83.1608734f,3.53122926f,116.41391f},
          {83.4825745f,3.9189806f,108.036804f},
          {78.0155869f,3.55711174f,116.974243f} },
        { 0x3c258252u,0x3f7fb563u,0x3d3ef6feu } }, // original source triangle 243502
      { { {78.0155869f,3.55711174f,116.974243f},
          {72.7686081f,1.26712513f,120.608086f},
          {72.7628021f,4.55114794f,122.417816f} },
        { 0xbf21fc20u,0x3ebee96du,0xbf2dbd0eu } }, // original source triangle 50059
      { { {72.7628021f,4.55114794f,122.417816f},
          {79.8359985f,4.35840225f,123.528473f},
          {78.0155869f,3.55711174f,116.974243f} },
        { 0x3d44e327u,0x3f7d615au,0xbe099467u } }, // original source triangle 243531
      };
    const std::vector<uint32_t> indices = {0,2,1}; // addIndex preserves Gothic winding by swapping 1/2.
    for(const auto& fixture:fixtures) {
      const std::vector<JPH::Vec3> vertices(fixture.vertices,fixture.vertices+3);
      PhysicVbo mesh(&vertices);
      mesh.addIndex(indices,0,3,zenkit::MaterialGroup::STONE);
      JPH::Vec3 normal;
      require(mesh.rayTriangleNormal(0,normal),"source terrain normal must be finite and nonzero");
      for(JPH::uint axis=0;axis<3;++axis)
        require(std::bit_cast<uint32_t>(normal[axis])==fixture.expected[axis],
                "source terrain normal must retain original Bullet float bits");
      require(!mesh.rayTriangleNormal(1,normal),"invalid source triangle must retain the native normal");
      }

    const float inf = std::numeric_limits<float>::infinity();
    const float nan = std::numeric_limits<float>::quiet_NaN();
    const std::vector<JPH::Vec3> invalid[] = {
      {{0,0,0},{0,0,0},{0,0,0}},
      {{0,0,0},{1,1,1},{2,2,2}},
      {{0,0,0},{1e-20f,0,0},{0,1e-20f,0}},
      {{0,0,0},{3e38f,0,0},{0,3e38f,0}},
      {{0,0,0},{inf,0,0},{0,1,0}},
      {{0,0,0},{nan,0,0},{0,1,0}},
      };
    for(const auto& vertices:invalid) {
      PhysicVbo mesh(&vertices);
      mesh.addIndex(indices,0,3,zenkit::MaterialGroup::STONE);
      JPH::Vec3 normal(7,8,9);
      require(!mesh.rayTriangleNormal(0,normal),"unusable source planes must retain native output");
      require(normal==JPH::Vec3(7,8,9),"invalid source normal must not overwrite native output");
      }
    }

  void testTerrainRayNormalScope() {
    const std::vector<JPH::Vec3> vertices = {
      {88.6986008f,5.17765617f,121.866676f},
      {91.4136581f,2.34256101f,112.210762f},
      {86.9217529f,5.54688644f,119.338539f},
      };
    const std::vector<uint32_t> indices = {0,2,1};
    PhysicVbo mesh(&vertices);
    mesh.addIndex(indices,0,3,zenkit::MaterialGroup::STONE);
    CollisionWorld world;
    auto landscape = world.addCollisionBody(mesh.shape(),at(0,0,0),.75f,
                                            DynamicWorld::C_Landscape,false,false,&mesh);
    auto object = world.addCollisionBody(mesh.shape(),at(0,0,0),.75f,
                                         DynamicWorld::C_Object,false,false,&mesh);
    const auto from = CollisionWorld::toCentimeters(JPH::Vec3(87.7866058f,7.30819273f,120.217987f));
    const auto to = CollisionWorld::toCentimeters(JPH::Vec3(87.7866058f,2.80819273f,120.217987f));
    const auto closest = world.rayCast(from,to,landscapeMask);
    JPH::Vec3 expected;
    require(mesh.rayTriangleNormal(0,expected),"terrain scope fixture must have a valid source normal");
    require(closest.hasHit && closest.body==landscape.get(),"identity terrain ray must hit its source face");
    const float normal[] = {closest.normal.x,closest.normal.y,closest.normal.z};
    for(JPH::uint axis=0;axis<3;++axis)
      require(std::bit_cast<uint32_t>(normal[axis])==std::bit_cast<uint32_t>(expected[axis]),
              "closest identity terrain ray must export the source plane normal");
    std::vector<CollisionWorld::QueryResult> all;
    world.rayCast(from,to,landscapeMask,false,&all);
    const auto objectHit = world.rayCast(from,to,objectMask);
    require(all.size()==1 && objectHit.hasHit && objectHit.body==object.get(),
            "native all-hit and object controls must hit the same geometry");
    const float native[] = {all[0].normal.x,all[0].normal.y,all[0].normal.z};
    const float objectNormal[] = {objectHit.normal.x,objectHit.normal.y,objectHit.normal.z};
    for(size_t axis=0;axis<3;++axis)
      require(std::bit_cast<uint32_t>(native[axis])==std::bit_cast<uint32_t>(objectNormal[axis]),
              "all-hit terrain and object queries must retain native normals");
    }

  void testMeshRayEdges() {
    CollisionWorld world;
    JPH::TriangleList triangles;
    triangles.emplace_back(JPH::Vec3(0,0,0),JPH::Vec3(0,0,1),JPH::Vec3(1,0,0));
    // This anchor admits the ray through the body's broad phase without hitting it.
    triangles.emplace_back(JPH::Vec3(-2,0,2),JPH::Vec3(-2,0,3),JPH::Vec3(-1,0,2));
    for(int i=1;i<=14;++i) {
      const float offset = float(i)*2.f;
      triangles.emplace_back(JPH::Vec3(offset,0,offset),JPH::Vec3(offset,0,offset+1),
                             JPH::Vec3(offset+1,0,offset));
      }
    JPH::MeshShapeSettings meshSettings(triangles);
    meshSettings.mMaxTrianglesPerLeaf = 1;
    const auto mesh = meshSettings.Create();
    require(!mesh.HasError(),"mesh ray edge test must construct its 16-leaf BVH");
    auto body = world.addCollisionBody(mesh.Get(),at(0,0,0),.75f,DynamicWorld::C_Landscape);

    // The target leaf has x >= 0; this point lies just outside that triangle with
    // one barycentric weight -5e-5. Both the edge test and BVH traversal need tolerance.
    const JPH::RayCast ray(JPH::Vec3(-5.0e-5f,1,.5f),JPH::Vec3(0,-2,0));
    const auto planePoint = ray.GetPointOnRay(.5f);
    JPH::Vec3 boundsMin, boundsMax;
    body->getAabb(boundsMin,boundsMax);
    require(JPH::AABox(boundsMin,boundsMax).Contains(planePoint),
            "anchor geometry must put the edge ray inside the overall body bounds");

    JPH::RayCastSettings exactSettings;
    JPH::ClosestHitCollisionCollector<JPH::CastRayCollector> exact;
    mesh.Get()->CastRay(ray,exactSettings,JPH::SubShapeIDCreator(),exact);
    require(!exact.HadHit(),"native default mesh rays must retain the exact triangle edge test");

    JPH::RayCastSettings compatibleSettings;
    compatibleSettings.mTriangleEdgeTolerance = 1.0e-4f;
    JPH::ClosestHitCollisionCollector<JPH::CastRayCollector> compatible;
    mesh.Get()->CastRay(ray,compatibleSettings,JPH::SubShapeIDCreator(),compatible);
    require(compatible.HadHit(),"mesh edge tolerance must reach triangles beyond their BVH leaf bounds");
    requireNear(compatible.mHit.mFraction,.5f,.000001f,"tolerant native mesh ray must hit the triangle plane");

    const auto gameHit = world.rayCast(CollisionWorld::toCentimeters(ray.mOrigin),
                                     CollisionWorld::toCentimeters(ray.GetPointOnRay(1)),landscapeMask);
    require(gameHit.hasHit && gameHit.body==body.get(),
            "game mesh rays must opt into the previous backend's edge tolerance");
    requireNear(gameHit.point.y,0,.001f,"compatible game mesh ray must report the triangle plane");
    require(!world.rayCast({-.02f,100,50},{-.02f,-100,50},landscapeMask).hasHit,
            "game mesh edge tolerance must reject points beyond its barycentric allowance");
    }

  void testCoincidentMaterials() {
    CollisionWorld world;
    // Two pairs of cyclic copies from the original city mesh. Their float plane
    // fractions prefer wood for the first ray and stone for the second; a fixed
    // material or source-order preference therefore cannot preserve both cases.
    const std::vector<JPH::Vec3> vertices = {
      CollisionWorld::toMeters({6181.77686f,1425.00293f,8387.23633f}),
      CollisionWorld::toMeters({6415.78125f,1242.17615f,8519.63574f}),
      CollisionWorld::toMeters({6371.84814f,1237.50293f,8494.77344f}),
      CollisionWorld::toMeters({5791.45264f,1425.0033f,5789.07715f}),
      CollisionWorld::toMeters({5923.85352f,1242.17651f,5555.07324f}),
      CollisionWorld::toMeters({5898.98828f,1237.5033f,5599.00586f}),
      };
    const std::vector<uint32_t> wood = {2,1,0,5,4,3};
    const std::vector<uint32_t> stone = {0,2,1,3,5,4};
    PhysicVbo mesh(&vertices);
    mesh.addIndex(wood,0,wood.size(),zenkit::MaterialGroup::WOOD,"wood-face");
    mesh.addIndex(stone,0,stone.size(),zenkit::MaterialGroup::STONE,"stone-face");
    auto body = world.addCollisionBody(mesh.shape(),at(0,0,0),.75f,
                                       DynamicWorld::C_Landscape,false,false,&mesh);
    const Tempest::Vec3 from[] = {{6477.52686f,1279.26453f,7595.64355f},
                                 {3314.40405f,1255.13806f,5864.34229f}};
    const Tempest::Vec3 to[] = {{5786.84668f,1274.92175f,11801.4824f},
                               {65382.3594f,2494.34717f,376.108551f}};
    const uint32_t expected[] = {0,3};
    for(size_t i=0;i<2;++i) {
      require(mesh.hasRayDuplicates(uint32_t(i)) && mesh.hasRayDuplicates(uint32_t(i+2)),
              "cyclic faces must retain their original triangle IDs and duplicate groups");
      for(const auto triangle:{uint32_t(i),uint32_t(i+2)})
        require(mesh.resolveRayTriangle(triangle,CollisionWorld::toMeters(from[i]),
                                        CollisionWorld::toMeters(to[i]))==expected[i],
                "coincident ray metadata must use original float plane fractions");
      const auto hit = world.rayCast(from[i],to[i],landscapeMask);
      require(hit.hasHit && hit.body==body.get(),"coincident material ray must hit its native mesh");
      require(hit.material==mesh.materialId(expected[i]),"game rays must resolve coincident face materials");
      require(hit.sector!=nullptr && std::string(hit.sector)==mesh.sectorName(expected[i]),
              "game rays must resolve coincident face sectors with their materials");
      }
    require(mesh.materialId(0)==zenkit::MaterialGroup::WOOD && mesh.materialId(2)==zenkit::MaterialGroup::STONE,
            "non-ray callers must retain each original triangle's material");
    }

  void testSharedRayEdge() {
    CollisionWorld world;
    // Original city faces from ray 97350 share an edge. Compressed vertices put
    // the wood face first, while the original planes put the stone face first.
    const std::vector<JPH::Vec3> vertices = {
      {652.533203f,44.7002907f,149.346542f},
      {653.475281f,44.2155151f,154.1633f},
      {652.22406f,46.061451f,149.986694f},
      {648.160217f,48.0254517f,146.484192f},
      };
    PhysicVbo mesh(&vertices);
    mesh.addIndex({0,2,1},0,3,zenkit::MaterialGroup::WOOD,"first-native-face");
    mesh.addIndex({2,0,3},0,3,zenkit::MaterialGroup::STONE,"first-original-face");
    auto body = world.addCollisionBody(mesh.shape(),at(0,0,0),.75f,
                                       DynamicWorld::C_Landscape,false,false,&mesh);
    const Tempest::Vec3 from(64818.9414f,4486.26904f,15276.9766f);
    const Tempest::Vec3 to(68676.2578f,5028.1626f,12438.8008f);
    std::vector<CollisionWorld::QueryResult> all;
    const auto first = world.rayCast(from,to,landscapeMask,false,&all);
    require(all.size()==2 && first.body==body.get(),"shared-edge ray must retain both native face hits");
    require(all[0].material==zenkit::MaterialGroup::WOOD && all[1].material==zenkit::MaterialGroup::STONE &&
            all[0].fraction<all[1].fraction && first.fraction==all[0].fraction,
            "all-hit queries must preserve native fraction ordering at shared edges");
    const auto closest = world.rayCast(from,to,landscapeMask);
    require(closest.hasHit && closest.body==body.get() && closest.material==zenkit::MaterialGroup::STONE,
            "closest shared-edge ray must choose the valid nearest original plane");
    require(std::string(closest.sector)=="first-original-face",
            "shared-edge correction must keep the chosen native face's metadata");
    requireNear(closest.fraction,.108205222f,.00000001f,
                "closest mesh rays must export the chosen original plane fraction");
    requireNear(closest.normal.x,all[1].normal.x,.000001f,"shared-edge correction must retain the chosen native normal x");
    requireNear(closest.normal.y,all[1].normal.y,.000001f,"shared-edge correction must retain the chosen native normal y");
    requireNear(closest.normal.z,all[1].normal.z,.000001f,"shared-edge correction must retain the chosen native normal z");
    requireNear(closest.point.x,65236.3203f,.005f,"closest mesh rays must export the source-plane point x");
    requireNear(closest.point.y,4544.90479f,.001f,"closest mesh rays must export the source-plane point y");
    requireNear(closest.point.z,14969.8701f,.005f,"closest mesh rays must export the source-plane point z");

    // A ray through the first face's interior must keep its ordinary closest hit.
    const auto center = (vertices[0]+vertices[1]+vertices[2])/3.f;
    const auto normal = (vertices[1]-vertices[0]).Cross(vertices[2]-vertices[0]).Normalized();
    const auto interior = world.rayCast(CollisionWorld::toCentimeters(center+normal),
                                       CollisionWorld::toCentimeters(center-normal),landscapeMask);
    require(interior.hasHit && interior.material==zenkit::MaterialGroup::WOOD,
            "interior mesh rays must retain their closest native face");
    }

  void testOriginalFloorProjection() {
    CollisionWorld world;
    constexpr float height = 37.1234818f;
    const std::vector<JPH::Vec3> vertices = {
      {0,height,0},{0,height,1},{1,height,0},
      // Distant triangles widen the codec's bounds without intersecting the ray.
      {10,0,10},{11,0,10},{10,0,11},
      {10,100,10},{10,100,11},{11,100,10},
      };
    const std::vector<uint32_t> indices = {0,2,1,3,5,4,6,8,7};
    PhysicVbo mesh(&vertices);
    mesh.addIndex(indices,0,indices.size(),zenkit::MaterialGroup::STONE,"original-floor");
    auto body = world.addCollisionBody(mesh.shape(),at(0,0,0),.75f,
                                       DynamicWorld::C_Landscape,false,false,&mesh);
    const Tempest::Vec3 from(25,5000,25), to(25,-5000,25);
    std::vector<CollisionWorld::QueryResult> all;
    const auto native = world.rayCast(from,to,landscapeMask,false,&all);
    require(all.size()==1 && native.body==body.get(),"floor projection must retain its selected native face");
    require(std::abs(native.point.y-height*100.f)>.001f,
            "floor fixture must expose the compressed native height through the all-hit API");
    const auto closest = world.rayCast(from,to,landscapeMask);
    require(closest.hasHit && closest.body==body.get() && closest.material==zenkit::MaterialGroup::STONE,
            "source-plane projection must preserve the selected native body and material");
    requireNear(closest.point.y,height*100.f,.0005f,
                "interior floor rays must recover their original geometric height");
    requireNear(closest.point.x,25,.00001f,"floor projection must keep the ray's x coordinate");
    requireNear(closest.point.z,25,.00001f,"floor projection must keep the ray's z coordinate");
    requireNear(closest.normal.y,native.normal.y,0,"floor projection must preserve its native normal");
    requireNear(closest.fraction,.128765181f,.00000001f,
                "floor projection must export the fraction consistent with its original height");
    }

  void testShallowOverlapProjection() {
    CollisionWorld world;
    const Tempest::Vec3 from(71271.125f,3618.42529f,-12934.1797f);
    const Tempest::Vec3 to(66624.5391f,3618.77539f,-17199.3848f);
    // The sphere initially overlaps this small box and moves toward it, while
    // its center ray misses. The bottom projection extrapolates by about -286.
    auto body = world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(.01f,.01f,.01f)),
                                       at(from.x-70,from.y,from.z+60),.75f,DynamicWorld::C_Landscape);
    const auto hit = world.raySweep(from,to,100,landscapeMask);
    require(hit.hasHit && hit.body==body.get(),"shallow initial overlap must retain its sphere hit");
    requireNear(hit.fraction,1,.000001f,"failed center ray must reset only the exported fraction");
    requireNear(hit.point.x,1399596.875f,.01f,"shallow overlap must preserve endpoint interpolation in x");
    requireNear(hit.point.y,3518.45703125f,.005f,"shallow overlap must preserve the projected bottom in y");
    requireNear(hit.point.z,1206365.625f,.01f,"shallow overlap must preserve endpoint interpolation in z");
    }

  void testNpcContactDistance() {
    auto contact = [](float diagonal, bool outside, bool rotated) {
      CollisionWorld world;
      const std::vector<JPH::Vec3> vertices = outside ? std::vector<JPH::Vec3>{
        {.104f,-.3f,-.2f},{.104f,.3f,-.2f},{.104f,0,.2f},
        {100,100,100},{101,100,100},{100,101,100},
        } : std::vector<JPH::Vec3>{
        {diagonal,-.3f,diagonal},{diagonal,.3f,diagonal},{diagonal+.2f,0,diagonal-.2f},
        {100,100,100},{101,100,100},{100,101,100},
        };
      PhysicVbo mesh(&vertices);
      const std::vector<uint32_t> indices = {0,1,2,3,4,5};
      mesh.addIndex(indices,0,indices.size(),zenkit::MaterialGroup::STONE,"contact-test");
      auto matrix = at(0,0,0);
      if(rotated)
        matrix.rotateOY(45);
      auto body = world.addCollisionBody(mesh.shape(),matrix,.75f,DynamicWorld::C_Landscape,false,false,&mesh);
      JPH::CapsuleShape capsule(.1f,.1f);
      Tempest::Vec3 normal;
      Interactive* vob = nullptr;
      const bool result = world.hasCollision(&capsule,{0,0,0},normal,vob);
      if(result)
        require(normal.x<-.5f && normal.z<-.5f,"separated diagonal contact must face away from its triangle");
      return result;
      };
    // The previous contact callback reported this 40 mm gap even though the
    // relative contact threshold is only 4.9 mm. Its final GJK distance bound
    // included the capsule radius. Movement consumes these separated contacts.
    require(contact(.099f,false,false),"NPC query must preserve separated contacts inside the original triangle AABB");
    // Expanding the distance query also reaches this 4 mm gap, but the original
    // triangle candidate box excluded it. The distant anchor admits broad phase.
    require(!contact(0,true,false),"NPC proximity must retain the unexpanded triangle candidate box");
    require(!contact(0,true,true),"rotated NPC mesh candidates must use the original mesh-local box");

    CollisionWorld world;
    const std::vector<JPH::Vec3> vertices = {
      {.0207614303f,.400143862f,-.305011988f},
      {.250788718f,.276874304f,.318776399f},
      {.225168914f,-.396727622f,-.0763103366f},
      {100,100,100},{101,100,100},{100,101,100},
      };
    PhysicVbo mesh(&vertices);
    const std::vector<uint32_t> indices = {0,1,2,3,4,5};
    mesh.addIndex(indices,0,indices.size(),zenkit::MaterialGroup::STONE,"bounded-contact-test");
    auto body = world.addCollisionBody(mesh.shape(),at(0,0,0),.75f,DynamicWorld::C_Landscape,false,false,&mesh);
    JPH::CapsuleShape capsule(.3f,.08f);
    Tempest::Vec3 normal;
    Interactive* vob = nullptr;
    // Its true surface gap is 50.8 mm, inside the 87.93 mm distance envelope,
    // and its triangle AABB overlaps. The old bounded GJK stopped with a 98.7 mm
    // simplex approximation and excluded it; accepting the exact gap blocks early.
    require(!world.hasCollision(&capsule,{0,0,0},normal,vob),
            "NPC contact eligibility must preserve the bounded query's previous simplex");
    }

  void testNpcScalarWitness() {
    CollisionWorld world;
    // This source face stopped the player one frame early in ik-sweep. The
    // scalar witness keeps the tiny vertical direction that selects the next
    // capsule support endpoint; the selected source geometry stays intact.
    const std::vector<JPH::Vec3> vertices = {
      {368.17807f,44.3249741f,-322.601501f},
      {366.899994f,45.7681885f,-329.008514f},
      {368.358978f,43.3533669f,-322.625275f},
      };
    PhysicVbo mesh(&vertices);
    mesh.addIndex({0,2,1},0,3,zenkit::MaterialGroup::STONE,"npc-witness-face");
    auto body = world.addCollisionBody(mesh.shape(),at(0,0,0),.75f,
                                       DynamicWorld::C_Landscape,false,false,&mesh);
    JPH::CapsuleShape capsule(.231234491f,.0817193016f);
    Tempest::Vec3 normal;
    Interactive* vob = nullptr;
    require(!world.hasCollision(&capsule,{36718.7188f,4488.04932f,-32690.3535f},normal,vob),
            "NPC sweep frame 19 must retain the unblocked source-face pose");
    require(world.hasCollision(&capsule,{36720.9766f,4488.3374f,-32688.2539f},normal,vob),
            "NPC sweep frame 20 must block at the next source-face pose");
    // Frozen original scalar callback direction from this accepted pose.
    requireNear(normal.x,-.976384342f,.000002f,"NPC separated contact must preserve the original conservative X direction");
    requireNear(normal.y,-.126087219f,.000002f,"NPC separated contact must preserve the original conservative Y direction");
    requireNear(normal.z,.175430045f,.000002f,"NPC separated contact must preserve the original conservative Z direction");

    // These neighboring poses exercise the tetrahedron's BDC/ADB face weights.
    // Changing interpolation order along with face evaluation regresses the second.
    require(!world.hasCollision(&capsule,{36717.918f,4487.64941f,-32690.9551f},normal,vob),
            "tetrahedron face weights must reject the original boundary pose");
    require(world.hasCollision(&capsule,{36717.7148f,4488.04932f,-32691.7539f},normal,vob),
            "tetrahedron face weights must preserve the neighboring accepted pose");
    }

  void testNpcSeparatedContactResponse() {
    CollisionWorld world;
    // Frozen tick 80027 of ik-sweep: the same rejected downward movement touched
    // an edge and an interior face. A tiny normal change redirected the fall,
    // then changed the next slope heading and the later collision frame.
    const std::vector<JPH::Vec3> vertices = {
      {86.245079f,5.06495762f,118.298805f},
      {80.3646851f,5.83244085f,123.314186f},
      {80.3646851f,7.01338053f,123.314186f},
      {80.3646851f,7.01338053f,123.314186f},
      {86.9217529f,5.54688644f,119.338539f},
      {86.245079f,5.06495762f,118.298805f},
      };
    PhysicVbo mesh(&vertices);
    mesh.addIndex({0,2,1,3,5,4},0,6,zenkit::MaterialGroup::STONE,"npc-fall-contact");
    auto body = world.addCollisionBody(mesh.shape(),at(0,0,0),.75f,
                                       DynamicWorld::C_Landscape,false,false,&mesh);
    JPH::CapsuleShape capsule(.231234491f,.0817193016f);
    Tempest::Vec3 normal;
    Interactive* vob = nullptr;
    require(world.hasCollision(&capsule,{8081.23877f,719.952087f,12290.1709f},normal,vob),
            "NPC fall contact must block the frozen downward sample");
    requireNear(normal.x,.00157767383f,.0000002f,"NPC fall contact must preserve original edge/face normal X");
    requireNear(normal.y,.932768404f,.0000002f,"NPC fall contact must preserve original edge/face normal Y");
    requireNear(normal.z,-.360472828f,.0000002f,"NPC fall contact must preserve original edge/face normal Z");

    // Recorded gravity-controller response to this public collision normal.
    // Test the branch's slope redirection without requiring NPC/world assets.
    const float horizontal = std::sqrt(normal.x*normal.x+normal.z*normal.z);
    const float complement = std::sqrt(1.f-horizontal*horizontal);
    const auto redirected = Tempest::Vec3(complement*normal.x/horizontal,
                                          horizontal*normal.y/complement,
                                          complement*normal.z/horizontal)*.35966f;
    requireNear(redirected.x,.00146827195f,.0000002f,"NPC fall response must preserve original horizontal velocity");
    requireNear(redirected.y,.129648894f,.0000002f,"NPC fall response must preserve original vertical velocity");
    requireNear(redirected.z,-.335476279f,.0000002f,"NPC fall response must preserve original forward velocity");
    }

  void testFixedStepCarry() {
    CollisionWorld world;
    auto first = world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.1f,.1f,.1f)),at(0,700,0),.4f,1);
    world.tick(8);
    requireNear(first->position().y,700,.001f,"eight milliseconds must remain below the fixed step");
    first.reset();
    // An empty world ignores elapsed time while retaining the existing remainder.
    world.tick(1000);
    auto next = world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.1f,.1f,.1f)),at(0,700,0),.4f,1);
    world.tick(9);
    const float step = 1.f/60.f;
    requireNear(next->position().y,700.f-DynamicWorld::gravityMS*100.f*step*step,.005f,
                "fixed-step remainder must survive removal and addition of dynamic bodies");
    }

  void testFixedStepCadence() {
    CollisionWorld world;
    auto body = world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.1f,.1f,.1f)),at(0,700,0),.4f,1);
    const float step = 1.f/60.f;
    const float acceleration = DynamicWorld::gravityMS*100.f*step*step;
    world.tick(16);
    requireNear(body->position().y,700,.005f,"sixteen milliseconds must not advance a new body");
    world.tick(17);
    requireNear(body->position().y,700.f-acceleration,.005f,"first seventeen millisecond frame must advance one step");
    world.tick(17);
    requireNear(body->position().y,700.f-3.f*acceleration,.005f,"second seventeen millisecond frame must preserve the previous float cadence");
    world.tick(16);
    requireNear(body->position().y,700.f-6.f*acceleration,.005f,"next sixteen millisecond frame must advance the retained step");
    }

  void testCharacterVirtualSkippedMove() {
    Resources resources;
    ProtoMesh wall(PackedMesh(movableCollisionBox({20,400,600}),PackedMesh::PK_Visual),"VIRTUAL_SKIP_WALL.MRM");
    zenkit::ModelHierarchy hierarchy;
    hierarchy.collision_bbox = {{-25,0,-25},{25,180,25}};
    Skeleton shape(hierarchy,nullptr,"VIRTUAL_SKIP_CONTRACT");
    for(bool obstructed:{false,true}) {
      DynamicWorld world(nullptr,movableFloor());
      auto structure=obstructed ? world.addStaticObj(wall.attach[0].shape.get(),at(50,200,0),"VIRTUAL_SKIP_WALL.MRM") :
                                 DynamicWorld::Item();
      auto walker=world.addGhostObj(&shape);
      const Tempest::Vec3 initial(14,0,0);
      for(const Tempest::Vec3 request:{Tempest::Vec3(.11f,0,0),Tempest::Vec3(1.9f,0,1.9f)}) {
        walker.setPosition(initial);
        DynamicWorld::CollisionTest collision;
        require(walker.tryMove(initial+request,collision)==DynamicWorld::MC_Skip,
                "legacy skip must remain component-wise even when the diagonal length exceeds two centimetres");
        const auto moved=walker.moveVirtual(request,17,50,.785398163f,80);
        require(!moved.blocked,"a legacy-skipped animation sway must not report virtual movement failure, with or without a wall");
        }
      walker.setPosition(initial);
      DynamicWorld::CollisionTest collision;
      require(walker.tryMove(initial+Tempest::Vec3(2,0,0),collision)!=DynamicWorld::MC_Skip,
              "a component exactly two centimetres must retain the legacy non-skip boundary");
      walker.setPosition(initial);
      const auto step=walker.moveVirtual({5,0,0},17,50,.785398163f,80);
      require(step.blocked==obstructed,"a real walking step must report a blocking wall while remaining free on an unobstructed floor");
      if(obstructed)
        require(!structure.isEmpty() && step.collision.landCol,"a blocked virtual walking step must retain its world collision notification");
      else
        require(step.position.x>initial.x+4.f,"suppressing animation-sway failures must preserve native walking displacement");
      }
    }

  void testCharacterVirtualInteractiveStep() {
    constexpr float slope=.785398163f;
    for(bool movable:{false,true}) for(bool interactive:{false,true}) {
      CollisionWorld world;
      auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.7f,
                                        DynamicWorld::C_Landscape);
      // A chair seat and an architectural tread can have the same geometry.
      // The interactive classification determines whether walking may step up.
      auto seat=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(.5f,.2f,1)),at(100,20,0),.7f,
                                       DynamicWorld::C_Object,movable);
      if(interactive)
        seat->setPayload(nullptr,DynamicWorld::P_Interactive);
      auto walker=world.createVirtualCharacter(25,180,{0,0,0});
      float highest=0.f;
      CollisionWorld::CharacterMoveResult state;
      for(int i=0;i<60;++i) {
        state=walker->move({5,0,0},17,50,slope,80,nullptr);
        highest=std::max(highest,state.position.y);
        }
      std::printf("CharacterVirtual 40 cm edge: movable %d, interactive %d, max feet %.4f cm, final x %.3f cm\n",
                  movable,interactive,double(highest),double(state.position.x));
      if(interactive) {
        require(highest<1.f && state.position.x<30.f,
                "automatic world stairs must not step onto or pass through interactive furniture");
        walker->setPosition({100,40,0});
        state=walker->move({},17,50,slope,80,nullptr);
        require(state.grounded,"interactive furniture must retain support for feet explicitly placed on its top");
        requireNear(state.position.y,40.f,1.f,"disabling automatic furniture stepping must not drop existing top support");
        } else {
        require(highest>39.f && state.position.x>200.f,
                "ordinary architectural C_Object treads must retain automatic world stair walking");
        }
      }
    }

  void testCharacterVirtualMovingUtensil() {
    float maximumJump=0.f;
    for(bool kicked:{false,true}) {
      CollisionWorld world;
      auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.7f,
                                        DynamicWorld::C_Landscape);
      auto matrix=at(150,25,0);
      if(!kicked)
        matrix.rotateOZ(45.f);
      // Real dropped items have a centred proxy padded to 15 cm per half-axis.
      // Its mass is still calculated from the smaller visual, like ITFO_BEER.
      auto mug=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.15f,.15f,.15f)),matrix,.5f,5.80991459f,
                                     DynamicWorld::C_Item,false);
      for(int i=0;i<90;++i)
        world.tick(17);
      auto walker=world.createVirtualCharacter(25,180,{0,0,0});
      float highest=0.f,jump=0.f,last=0.f;
      CollisionWorld::CharacterMoveResult state;
      for(int frame=0;frame<65;++frame) {
        if(kicked && frame==8) {
          const auto point=mug->position()+Tempest::Vec3(0,10,10);
          require(mug->addImpulseAtPoint({-5,0,0},point),
                  "moving utensil fixture must apply its off-centre native impulse");
          }
        state=walker->move({11,0,0},33,50,.785398163f,80,nullptr);
        highest=std::max(highest,state.position.y);
        jump=std::max(jump,state.position.y-last);
        last=state.position.y;
        world.tick(33);
        }
      maximumJump=std::max(maximumJump,std::max(highest,jump));
      std::printf("CharacterVirtual moving 5.81 kg utensil: kicked %d, max feet %.4f cm, max jump %.4f cm, final x %.3f cm\n",
                  kicked,double(highest),double(jump),double(state.position.x));
      require(mug->touchedByNpc() && mug->position().x>200.f,
              "a rotating loose utensil must retain its native character contact and walking push");
      require(state.position.x>600.f,"a moving loose utensil must not prevent ordinary walking progress");
      }
    require(maximumJump<1.f,
            "a moving or rotating light item's side velocity must not lift feet through the floor contact solver");
    }

  void testCharacterVirtualIdleSlopes() {
    constexpr float maxSlope=.785398163f;
    const auto slopeNormal=[](float degrees) {
      const float angle=degrees*.01745329252f;
      const auto heading=JPH::Vec3(.120f,0,-.071f).Normalized();
      return heading*std::sin(angle)+JPH::Vec3::sAxisY()*std::cos(angle);
      };
    // The first normal is the reported farm-path support; the others approach
    // the walkable limit and cross it. Exercise both human capsule sizes.
    const std::array<JPH::Vec3,4> normals={JPH::Vec3(.120f,.990f,-.071f).Normalized(),
                                          slopeNormal(25),slopeNormal(44),slopeNormal(60)};
    for(float radius:{25.f,20.f}) for(const auto normal:normals) {
      CollisionWorld world;
      const auto vertex=[&](float x,float z) {return JPH::Vec3(x,-(normal.GetX()*x+normal.GetZ()*z)/normal.GetY(),z);};
      JPH::TriangleList triangles;
      triangles.emplace_back(vertex(-20,-20),vertex(-20,20),vertex(20,20));
      triangles.emplace_back(vertex(-20,-20),vertex(20,20),vertex(20,-20));
      const auto mesh=JPH::MeshShapeSettings(triangles).Create();
      require(!mesh.HasError(),"idle-slope fixture must construct its landscape plane");
      auto floor=world.addCollisionBody(mesh.Get(),at(0,0,0),.7f,DynamicWorld::C_Landscape);
      auto walker=world.createVirtualCharacter(radius,180,{0,radius*(1.f/normal.GetY()-1.f),0});
      CollisionWorld::CharacterMoveResult state;
      for(int frame=0;frame<30;++frame)
        state=walker->move({},17,50,maxSlope,80,nullptr);
      const auto initial=state.position;
      const bool walkable=normal.GetY()>std::cos(maxSlope);
      require(walkable ? state.grounded : state.steep,"idle-slope fixture must identify walkable and sliding support");
      for(int frame=0;frame<180;++frame)
        state=walker->move({},frame%3==0 ? 16 : 17,50,maxSlope,80,nullptr);
      const float drift=(state.position-initial).length();
      std::printf("CharacterVirtual idle slope: radius %.0f cm, incline %.3f degrees, 3 s drift %.5f cm, grounded %d, steep %d\n",
                  double(radius),double(std::acos(normal.GetY())*57.2957795f),double(drift),state.grounded,state.steep);
      if(walkable) {
        require(state.grounded && drift<.5f,"idle human capsules must drift less than half a centimetre in three seconds below the slide threshold");
        const auto jumped=walker->move({0,20,0},17,50,maxSlope,80,nullptr);
        require(!jumped.grounded && jumped.position.y>state.position.y+10.f,
                "static slope friction must preserve an explicit jump away from support");
        } else {
        require(state.steep && state.position.y<initial.y-1.f,
                "idle human capsules must retain downhill sliding above the slide threshold");
        }
      }
    }

  void testCharacterVirtualSupports() {
    constexpr float slope=.785398163f;
    // Sample every rider immediately after move(), against the support pose
    // that move() actually observed. Comparing a pre-physics rider with a
    // post-physics body hides a one-frame error inside the drift tolerance.
    const auto frameDt=[](int frame) -> uint64_t { return frame%3==2 ? 16 : 17; };
    const auto carriedPoint=[](const Tempest::Matrix4x4& matrix, Tempest::Vec3 local) {
      matrix.project(local);
      return local;
      };
    for(bool gamePhase:{false,true}) for(bool rotating:{false,true}) {
      CollisionWorld world;
      auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.7f,
                                        DynamicWorld::C_Landscape);
      auto platform=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(2,.1f,2)),at(0,90,0),.7f,
                                           DynamicWorld::C_Object,true);
      // Hero and a second human-sized controller exercise distinct points of
      // the same support, including a nonzero radius for the turning platform.
      std::array<std::unique_ptr<CollisionWorld::VirtualCharacter>,2> riders={
        world.createVirtualCharacter(25,180,{100,100,-60}),
        world.createVirtualCharacter(25,180,{-80,100,70})};
      std::array<CollisionWorld::CharacterMoveResult,2> state;
      std::array<Tempest::Vec3,2> initial,local;
      std::array<float,2> maximumDrift={};
      for(size_t rider=0;rider<riders.size();++rider) {
        state[rider]=riders[rider]->move({},17,50,slope,80,nullptr);
        require(state[rider].grounded,"both virtual riders must acquire the initial kinematic support");
        initial[rider]=state[rider].position;
        local[rider]=initial[rider]-platform->position();
        }
      uint64_t elapsed=0;
      const int frames=rotating ? 180 : 240;
      bool supported=true;
      for(int frame=0;frame<frames;++frame) {
        const auto dt=frameDt(frame);
        if(gamePhase)
          for(size_t rider=0;rider<riders.size();++rider)
            state[rider]=riders[rider]->move({},dt,50,slope,80,nullptr);
        elapsed+=dt;
        auto matrix=rotating ? at(0,90,0) : at(.08f*float(elapsed),90.f+.0045f*float(elapsed),0);
        if(rotating)
          matrix.rotateOY(.04f*float(elapsed)); // 120 degrees in three seconds.
        platform->setObjMatrix(matrix);
        world.tick(dt);
        for(size_t rider=0;rider<riders.size();++rider) {
          if(gamePhase) {
            const auto carried=riders[rider]->carry(dt);
            require(carried.supportMoved,"a mover updated after NPC movement must exercise the late carry sweep");
            state[rider]=carried;
            } else {
            state[rider]=riders[rider]->move({},dt,50,slope,80,nullptr);
            }
          supported&=state[rider].grounded;
          maximumDrift[rider]=std::max(maximumDrift[rider],
                                      (state[rider].position-carriedPoint(matrix,local[rider])).length());
          }
        }
      const auto matrix=platform->matrix();
      for(size_t rider=0;rider<riders.size();++rider) {
        const auto expected=carriedPoint(matrix,local[rider]);
        const auto drift=state[rider].position-expected;
        const auto displacement=state[rider].position-initial[rider];
        std::printf("CharacterVirtual %s %s support rider %zu: support travel %.6f cm, angle %.3f deg, "
                    "rider displacement (%.6f, %.6f, %.6f) cm, drift (%.6f, %.6f, %.6f) cm, "
                    "final %.6f / maximum %.6f cm, ground speed %.6f cm/s\n",
                    gamePhase ? "NPC->mover->physics->carry" : "physics->NPC",
                    rotating ? "turning kinematic" : "linear kinematic",rider,
                    double((platform->position()-Tempest::Vec3(0,90,0)).length()),
                    rotating ? .04*double(elapsed) : 0.,
                    double(displacement.x),double(displacement.y),double(displacement.z),
                    double(drift.x),double(drift.y),double(drift.z),double(drift.length()),
                    double(maximumDrift[rider]),double(state[rider].groundVelocity.length()));
        }
      require(supported,"both virtual riders must retain kinematic support throughout movement");
      if(rotating)
        require(.04f*float(elapsed)>=90.f,"the turning support contract must rotate at least ninety degrees");
      else
        require(platform->position().x>=300.f,"the linear support contract must travel at least three metres");
      for(const float drift:maximumDrift)
        requireNear(drift,0,rotating ? 2.f : 1.f,
                    rotating ? "a turning support must retain each rider's local point within two centimetres" :
                               "a three-metre kinematic translation must carry each rider within one centimetre");
      require(state[0].groundVelocity.length()>50.f,
              "kinematic support must expose its actual support-point velocity for a later jump");
      // No residual inferred velocity is allowed after the transform stops.
      for(size_t rider=0;rider<riders.size();++rider) {
        const auto stopped=riders[rider]->move({},17,50,slope,80,nullptr);
        requireNear((stopped.position-state[rider].position).length(),0,.1f,
                    "stopping a direct-matrix support must not retain its previous carried displacement");
        if(gamePhase) {
          world.tick(17);
          const auto carried=riders[rider]->carry(17);
          require(!carried.supportMoved,"a stopped mover must not schedule another carry sweep");
          requireNear((carried.position-stopped.position).length(),0,.001f,
                      "the late carry phase must not move feet after their support has stopped");
          }
        }
      platform.reset();
      if(gamePhase)
        require(!riders[0]->carry(17).supportMoved,"removing a support must invalidate its late carry sweep");
      const auto falling=riders[0]->move({},17,50,slope,80,nullptr);
      require(!falling.grounded && falling.position.y<state[0].position.y,
              "virtual feet must lose support and fall after the moving platform disappears");
      }
    for(bool gamePhase:{false,true}) for(bool variableCadence:{false,true}) {
      if(!gamePhase && variableCadence)
        continue; // Preserve the original nominal physics-before-NPC contract.
      CollisionWorld world;
      auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.01f,
                                        DynamicWorld::C_Landscape);
      // A heavy, broad platform has an accepted horizontal top under the step-1
      // support policy. Low floor friction makes the impulse travel observable;
      // a nearly stationary body cannot establish a carry-distance contract.
      auto platform=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(2,.1f,2)),at(0,10,0),.01f,122.f,
                                         DynamicWorld::C_MovableVob,true);
      auto rider=world.createVirtualCharacter(25,180,{0,20,0});
      auto state=rider->move({},17,50,slope,80,nullptr);
      require(state.grounded,"virtual feet must acquire the heavy dynamic support before its impulse");
      require(platform->mass()>=10.f,"the dynamic carry contract must use an accepted support mass");
      const auto bodyStart=platform->position(),riderStart=state.position;
      const auto local=riderStart-bodyStart;
      require(platform->addImpulse({1.8f*platform->mass(),0,0}),
              "the dynamic support must receive one native linear impulse");
      float maximumDrift=0.f,maximumTravel=0.f;
      Tempest::Vec3 endpointPrediction;
      float maximumPredictionError=0.f,spikeObserved=0.f,spikePredicted=0.f;
      bool supported=true;
      for(int frame=0;frame<120;++frame) {
        constexpr std::array<uint64_t,4> cadence={25,45,16,65};
        const auto dt=variableCadence ? cadence[size_t(frame)%cadence.size()] : frameDt(frame);
        if(gamePhase)
          state=rider->move({},dt,50,slope,80,nullptr);
        // Physics advances before this character sample, just as the preceding
        // World::tick has already advanced bodies before the next NPC tick.
        const auto previous=platform->position();
        world.tick(dt);
        const auto velocityBeforeCarry=platform->velocity();
        const auto angularBeforeCarry=platform->angularVelocity();
        endpointPrediction+=velocityBeforeCarry*(float(dt)*.001f);
        maximumPredictionError=std::max(maximumPredictionError,
                                        (endpointPrediction-(platform->position()-bodyStart)).length());
        if(dt==65) {
          spikeObserved=(platform->position()-previous).length();
          spikePredicted=velocityBeforeCarry.length()*.065f;
          }
        if(gamePhase) {
          const auto carried=rider->carry(dt);
          if(carried.supportMoved)
            state=carried;
          requireNear((platform->velocity()-velocityBeforeCarry).length(),0,.0001f,
                      "a late carry sweep must not apply a second weight or contact impulse to its support");
          requireNear((platform->angularVelocity()-angularBeforeCarry).length(),0,.000001f,
                      "a late carry sweep must not apply torque to its support");
          } else {
          state=rider->move({},dt,50,slope,80,nullptr);
          }
        supported&=state.grounded;
        maximumTravel=std::max(maximumTravel,(platform->position()-bodyStart).length());
        maximumDrift=std::max(maximumDrift,(state.position-carriedPoint(platform->matrix(),local)).length());
        }
      const auto drift=state.position-carriedPoint(platform->matrix(),local);
      std::printf("CharacterVirtual %s dynamic support %s: mass %.6f kg, impulse %.6f Ns, "
                  "support travel %.6f cm, rider travel %.6f cm, drift (%.6f, %.6f, %.6f) cm, "
                  "final %.6f / maximum %.6f cm; endpoint-v*dt prediction error %.6f cm, "
                  "last 65 ms observed %.6f vs predicted %.6f cm\n",
                  gamePhase ? "NPC->physics->carry" : "physics->NPC",variableCadence ? "25/45/16/65 ms" : "17/17/16 ms",
                  double(platform->mass()),double(1.8f*platform->mass()),
                  double(maximumTravel),double((state.position-riderStart).length()),
                  double(drift.x),double(drift.y),double(drift.z),double(drift.length()),double(maximumDrift),
                  double(maximumPredictionError),double(spikeObserved),double(spikePredicted));
      require(maximumTravel>=50.f,"the impulse-driven dynamic support must travel at least fifty centimetres");
      require(supported,"the rider must remain grounded throughout the dynamic carry contract");
      requireNear(maximumDrift,0,2.f,"a heavy impulse-driven support must carry virtual feet within two centimetres");
      if(variableCadence)
        require(maximumPredictionError>2.f,
                "the variable cadence must expose endpoint velocity integration differing from native fixed-step displacement");
      }
    {
      CollisionWorld world;
      auto platform=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(4,.1f,2)),at(0,90,0),.7f,
                                           DynamicWorld::C_Object,true);
      auto wall=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(.1f,2,2)),at(100,200,0),.7f,
                                       DynamicWorld::C_Object);
      auto rider=world.createVirtualCharacter(25,180,{0,100,0});
      auto state=rider->move({},17,50,slope,80,nullptr);
      require(state.grounded,"the blocked carry fixture must begin on its kinematic support");
      for(int frame=1;frame<=160;++frame) {
        state=rider->move({},17,50,slope,80,nullptr);
        platform->setObjMatrix(at(2.f*float(frame),90,0));
        world.tick(17);
        const auto carried=rider->carry(17);
        if(carried.supportMoved)
          state=carried;
        require(state.position.x<=65.1f,"a late carry sweep must stop at a world wall instead of pinning feet to the support");
        }
      std::printf("CharacterVirtual late carry blocked by wall: support %.6f cm, rider %.6f cm, slip %.6f cm\n",
                  double(platform->position().x),double(state.position.x),double(platform->position().x-state.position.x));
      require(state.position.x>50.f && platform->position().x-state.position.x>250.f,
              "the wall fixture must carry the rider to the obstruction while the support continues underneath");
      }
    }

  void testCharacterVirtual() {
    testCharacterVirtualSkippedMove();
    testCharacterVirtualInteractiveStep();
    testCharacterVirtualMovingUtensil();
    testCharacterVirtualIdleSlopes();
    testCharacterVirtualSupports();
    constexpr float slope = .785398163f;
    const auto printState=[](const char* stage, int frames, const CollisionWorld::CharacterMoveResult& state) {
      std::fprintf(stderr,"CharacterVirtual %s: frames=%d feet=(%.3f,%.3f,%.3f) velocity=(%.3f,%.3f,%.3f) grounded=%d steep=%d normal=(%.3f,%.3f,%.3f)\n",
                   stage,frames,double(state.position.x),double(state.position.y),double(state.position.z),
                   double(state.velocity.x),double(state.velocity.y),double(state.velocity.z),state.grounded,state.steep,
                   double(state.groundNormal.x),double(state.groundNormal.y),double(state.groundNormal.z));
      };
    {
      CollisionWorld world;
      auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.7f,
                                        DynamicWorld::C_Landscape);
      std::vector<std::unique_ptr<CollisionWorld::CollisionBody>> steps;
      for(int i=1;i<=3;++i)
        steps.push_back(world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(.5f,.1f*float(i),2)),
                                               at(100.f*float(i)+50.f,10.f*float(i),0),.7f,DynamicWorld::C_Object));
      auto hero=world.createVirtualCharacter(25,180,{0,0,0});
      auto state=hero->move({},0,50,slope,80,nullptr);
      printState("stairs-start",0,state);
      int frames=0;
      // Finish inside the last tread (300..400), with room for collision loss
      // at each riser. Unobstructed 80*5 cm is not a traversal-time contract.
      for(;frames<120 && state.position.x<350.f;++frames) {
        state=hero->move({5,0,0},17,50,slope,80,nullptr);
        if((frames+1)%20==0)
          printState("stairs-up",frames+1,state);
        }
      printState("stairs-up-end",frames,state);
      require(state.grounded && state.position.x>=350.f,"virtual walking must reach the interior of the last twenty centimetre step");
      requireNear(state.position.y,60,2,"virtual capsule feet must remain on the final stair");
      frames=0;
      for(;frames<120 && state.position.x>20.f;++frames)
        state=hero->move({-5,0,0},17,50,slope,80,nullptr);
      printState("stairs-down-end",frames,state);
      require(state.grounded && state.position.x<=20.f,"virtual walking must descend the same steps");
      requireNear(state.position.y,0,2,"floor sticking must return the feet to the floor");
      auto wall=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(.1f,3,10)),at(600,300,0),.7f,
                                       DynamicWorld::C_Object);
      for(int i=0;i<150;++i)
        state=hero->move({5,0,5},17,50,slope,80,nullptr);
      printState("wall-slide-end",150,state);
      require(state.position.x<567 && state.position.z>650,
              "virtual walking must stop at the wall while retaining motion along it");
      hero->setPosition({-300,0,0});
      state=hero->move({},17,50,slope,80,nullptr);
      printState("teleport",1,state);
      requireNear(state.position.x,-300,.01f,"a virtual teleport must not retain old ground displacement");
      require(state.grounded,"a teleported virtual character must refresh its support");
    }
    auto push=[&](float mass, int category) {
      CollisionWorld world;
      auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.7f,
                                        DynamicWorld::C_Landscape);
      auto prop=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.2f,.4f,.4f)),at(80,40,0),.7f,mass,category,true);
      auto hero=world.createVirtualCharacter(25,180,{0,0,0});
      CollisionWorld::CharacterMoveResult state;
      for(int i=0;i<60;++i) {
        state=hero->move({5,0,0},17,10,slope,80,nullptr);
        world.tick(17);
        }
      printState("push-end",60,state);
      std::fprintf(stderr,"CharacterVirtual pushed body: mass=%.3f kg x=%.3f cm\n",double(mass),double(prop->position().x));
      require(state.position.x<prop->position().x-30,
              "virtual contacts must not allow the hero through a dynamic hull");
      return prop->position().x-80.f;
      };
    const float light=push(1,DynamicWorld::C_Item);
    const float heavy=push(122,DynamicWorld::C_MovableVob);
    require(light>20 && heavy<light*.5f,"the same character strength must push a light item faster than heavy furniture");
    std::printf("CharacterVirtual push: 1 kg %.2f cm, 122 kg %.2f cm\n",double(light),double(heavy));
    for(const int category:{DynamicWorld::C_Item,DynamicWorld::C_MovableVob}) {
      CollisionWorld world;
      auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.7f,
                                        DynamicWorld::C_Landscape);
      auto mug=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.07f,.08f,.07f)),at(80,8,0),.7f,5.8f,category,true);
      auto hero=world.createVirtualCharacter(25,180,{0,0,0});
      float highest=0.f;
      CollisionWorld::CharacterMoveResult state;
      for(int i=0;i<60;++i) {
        state=hero->move({5,0,0},17,50,slope,80,nullptr);
        highest=std::max(highest,state.position.y);
        world.tick(17);
        }
      std::printf("CharacterVirtual loose utensil: category %d moved %.3f cm, max feet %.3f cm\n",
                  category,double(mug->position().x-80.f),double(highest));
      require(highest<1.f,"walking into a loose item or small movable utensil must not lift the feet");
      require(mug->position().x>100.f && mug->touchedByNpc(),
              "a loose utensil excluded as a support must still receive native contact impulses");
      require(state.position.x>250.f,"a loose utensil must be pushed aside while the hero keeps walking");
    }
    {
      CollisionWorld world;
      auto mug=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.2f,.08f,.2f)),at(0,8,0),.7f,5.8f,
                                     DynamicWorld::C_Item,true);
      auto hero=world.createVirtualCharacter(25,180,{0,16,0});
      CollisionWorld::CharacterMoveResult state;
      // Leave the rigid body suspended without ticking it: the feet can only
      // remain at 16 cm if this light body is erroneously accepted as a floor.
      for(int i=0;i<10;++i)
        state=hero->move({},17,50,slope,80,nullptr);
      require(!state.grounded && state.position.y<14.f,
              "even feet initially placed on a light item must fall rather than acquire it as support");
      requireNear(mug->velocity().y,0,.001f,"an excluded light support must not receive character support weight");
    }
    {
      CollisionWorld world;
      auto floor=world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),.7f,
                                        DynamicWorld::C_Landscape);
      // A low table is deliberately within the 50 cm stair limit, including
      // the capsule's rounded toe. It must remain furniture rather than a step.
      auto table=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.2f,.1f,.6f)),at(100,10,0),1.f,122.f,
                                       DynamicWorld::C_MovableVob,true);
      auto hero=world.createVirtualCharacter(25,180,{0,0,0});
      CollisionWorld::CharacterMoveResult state;
      float highest=0.f;
      for(int i=0;i<90;++i) {
        state=hero->move({5,0,0},17,50,slope,80,nullptr);
        highest=std::max(highest,state.position.y);
        world.tick(17);
        }
      std::printf("CharacterVirtual low table: hero %.3f cm, table %.3f cm, max feet %.3f cm\n",
                  double(state.position.x),double(table->position().x),double(highest));
      require(highest<1.f,"ordinary walking must neither auto-step nor roll the capsule up a movable table");
      require(state.position.x<table->position().x-35.f,
              "a table excluded from automatic stepping must still block the capsule");
    }
    {
      CollisionWorld world;
      // No floor or simulation tick: measured linear momentum comes only from
      // character contact impulses, without gravity, support weight or friction.
      // Two narrow bodies block the same capsule. Each can demand a full force
      // allowance; repeated contacts / stair probes must share the move budget.
      constexpr float mass=20.f,seconds=.1f;
      auto a=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.1f,.9f,.1f)),at(50,190,-20),.4f,mass,
                                  DynamicWorld::C_MovableVob,true);
      auto b=world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.1f,.9f,.1f)),at(50,190,20),.4f,mass,
                                  DynamicWorld::C_MovableVob,true);
      auto hero=world.createVirtualCharacter(25,180,{0,100,0});
      hero->move({100,0,0},100,50,slope,80,nullptr);
      const float impulseA=a->velocity().length()*.01f*mass;
      const float impulseB=b->velocity().length()*.01f*mass;
      constexpr float totalLimit=450.f*seconds;
      constexpr float bodyLimit=450.f*80.f/(80.f+mass)*seconds;
      std::printf("CharacterVirtual shared push: %.3f + %.3f Ns, total limit %.3f Ns, per-body %.3f Ns\n",
                  double(impulseA),double(impulseB),double(totalLimit),double(bodyLimit));
      require(impulseA>1.f && impulseB>1.f,"the shared push fixture must exercise two native contact bodies");
      require(impulseA<=bodyLimit+.01f && impulseB<=bodyLimit+.01f,
              "all contacts of one body must share its mass-dependent walking effort");
      require(impulseA+impulseB<=totalLimit+.01f,
              "all bodies and repeated contacts must share one 450 newton walking budget per move");
    }
    {
      zenkit::ModelHierarchy hierarchy;
      hierarchy.collision_bbox = {{-25,0,-25},{25,180,25}};
      Skeleton shape(hierarchy,nullptr,"VIRTUAL_NPC_CONTRACT");
      DynamicWorld world(nullptr,zenkit::Mesh{});
      auto hero=world.addGhostObj(&shape);
      auto npc=world.addGhostObj(&shape);
      hero.setPosition({0,0,0});
      npc.setPosition({100,0,0});
      world.tick(0);
      world.tick(0); // leave the other NPC in the frozen broad-phase list
      for(int i=0;i<30;++i)
        hero.moveVirtual({5,0,0},17,50,slope,80);
      require(hero.position().x<55,"virtual hero walking must retain existing NPC ellipsoid obstacles");
      requireNear(npc.position().x,100,0,"virtual hero contacts must never move another NPC");
      hero.setPosition({-200,50,30});
      hero.resetVirtual();
      const auto after=hero.moveVirtual({},0,50,slope,80);
      requireNear(after.position.x,-200,.001f,"controller recreation must retain the saved or teleported feet x");
      requireNear(after.position.y,50,.001f,"controller recreation must retain the saved or teleported feet y");
      requireNear(after.position.z,30,.001f,"controller recreation must retain the saved or teleported feet z");
    }
    {
      zenkit::ModelHierarchy hierarchy;
      hierarchy.collision_bbox = {{-25,0,-25},{25,180,25}};
      Skeleton shape(hierarchy,nullptr,"VIRTUAL_NPC_PAIR_CONTRACT");
      DynamicWorld world(nullptr,movableFloor());
      auto left=world.addGhostObj(&shape);
      auto right=world.addGhostObj(&shape);
      left.setPosition({0,0,0});
      right.setPosition({200,0,0});
      world.tick(0);
      world.tick(0);
      for(int i=0;i<40;++i) {
        left.moveVirtual({5,0,0},17,50,slope,80);
        right.moveVirtual({-5,0,0},17,50,slope,80);
        require(right.position().x-left.position().x>=49.9f,
                "two virtual NPCs moving towards each other must never cross or interpenetrate");
        }
      require(left.position().x>60.f && right.position().x<140.f,
              "both virtual NPCs must advance before their mutual blocking contact");
      requireNear(left.position().y,0.f,1.f,"an NPC contact must not lift virtual feet onto another NPC");
      requireNear(right.position().y,0.f,1.f,"an opposing NPC contact must retain floor support");
      const auto stoppedRight=right.position();
      const auto blocked=left.moveVirtual({5,0,0},17,50,slope,80);
      require(blocked.blocked && blocked.collision.npcCol>0.f,
              "unsuccessful virtual NPC advancement must expose its blocking character contact to MoveAlgo");
      requireNear(right.position().x,stoppedRight.x,0.f,"a virtual NPC contact must never push its neighbour");
      const auto sway=left.moveVirtual({1.9f,0,0},17,50,slope,80);
      require(!sway.blocked,"a legacy-skipped sway into another NPC must not notify AI of movement failure");
      requireNear(right.position().x,stoppedRight.x,0.f,"a skipped sway must preserve the neighbouring NPC obstacle");
      right.setEnable(false);
      for(int i=0;i<20;++i)
        left.moveVirtual({5,0,0},17,50,slope,80);
      require(left.position().x>150.f,"a seated character with disabled collision must not leave a virtual obstacle");
    }
    {
      zenkit::ModelHierarchy hierarchy;
      hierarchy.collision_bbox = {{-25,0,-25},{25,180,25}};
      Skeleton shape(hierarchy,nullptr,"VIRTUAL_NPC_OVERLAP_CONTRACT");
      DynamicWorld world(nullptr,movableFloor());
      auto walker=world.addGhostObj(&shape);
      auto overlapping=world.addGhostObj(&shape);
      auto clearNeighbour=world.addGhostObj(&shape);
      walker.setPosition({0,0,0});
      overlapping.setPosition({40,0,0});
      clearNeighbour.setPosition({-55,0,0});
      for(int i=0;i<4;++i)
        walker.moveVirtual({-5,0,0},17,50,slope,80);
      require(walker.position().x>=-5.01f && walker.position().x<-4.f,
              "separating a teleported overlap must not transfer penetration to a previously clear neighbour");
      requireNear(overlapping.position().x,40.f,0.f,"overlap recovery must not displace the original neighbour");
      requireNear(clearNeighbour.position().x,-55.f,0.f,"overlap recovery must not displace the clear neighbour");
    }
    {
      Resources resources;
      ProtoMesh wall(PackedMesh(movableCollisionBox({20,400,600}),PackedMesh::PK_Visual),"VIRTUAL_NPC_WALL.MRM");
      zenkit::ModelHierarchy hierarchy;
      hierarchy.collision_bbox = {{-25,0,-25},{25,180,25}};
      Skeleton shape(hierarchy,nullptr,"VIRTUAL_NPC_WALL_CONTRACT");
      DynamicWorld world(nullptr,movableFloor());
      auto structure=world.addStaticObj(wall.attach[0].shape.get(),at(50,200,0),"VIRTUAL_NPC_WALL.MRM");
      auto walker=world.addGhostObj(&shape);
      auto obstacle=world.addGhostObj(&shape);
      walker.setPosition({0,0,0});
      obstacle.setPosition({15,0,150});
      // The diagonal desired motion misses the NPC, while wall sliding would
      // encounter it. The post-solve clip must retain a world-safe pose.
      const auto stopped=walker.moveVirtual({200,0,200},100,50,slope,80);
      require(!structure.isEmpty(),"virtual NPC wall fixture must install its static collision mesh");
      require(stopped.position.x<=15.1f && stopped.position.z<100.f,
              "a wall-resolved virtual move must not cross a character or interpolate feet into the wall");
      requireNear(obstacle.position().x,15.f,0.f,"wall sliding must not push a neighbouring NPC through the wall");
      requireNear(obstacle.position().z,150.f,0.f,"wall sliding must leave the neighbouring NPC at its own feet");
    }
    }

  void testVirtualFloorGuard() {
    constexpr float slope=.785398163f;
    zenkit::ModelHierarchy hierarchy;
    hierarchy.collision_bbox={{-25,0,-25},{25,180,25}};
    Skeleton shape(hierarchy,nullptr,"VIRTUAL_FLOOR_GUARD_CONTRACT");
    auto ledge=movableFloor();
    for(auto& vertex:ledge.vertices)
      vertex.x=vertex.x>0 ? 0.f : vertex.x;
    for(bool lowerFloor:{false,true}) {
      auto mesh=ledge;
      if(lowerFloor) {
        auto lower=movableFloor();
        for(auto vertex:lower.vertices) {
          vertex.y=-500.f;
          mesh.vertices.push_back(vertex);
          }
        for(auto index:lower.polygons.vertex_indices)
          mesh.polygons.vertex_indices.push_back(index+4);
        mesh.polygons.material_indices.insert(mesh.polygons.material_indices.end(),{0,0});
        }
      DynamicWorld world(nullptr,mesh);
      auto walker=world.addGhostObj(&shape);
      walker.setPosition({-35,0,0});
      walker.moveVirtual({},17,50,slope,80);
      const auto start=walker.position();
      require(walker.hasFloorBelow(start),"the virtual floor guard fixture must start over supported world");
      require(walker.hasFloorBelow({45,80,20})==lowerFloor,
              "jump handoff must distinguish a bottomless lateral step from a lower landing floor");
      auto moved=walker.moveVirtual({80,0,20},100,50,slope,80);
      if(!lowerFloor) {
        require(moved.blocked && moved.collision.preFall,
                "a virtual step beyond all floor must report the legacy pre-fall failure");
        requireNear((moved.position-start).length(),0,.001f,
                    "a rejected bottomless virtual step must retain the known-safe feet");
        for(int i=0;i<180;++i)
          moved=walker.moveVirtual({5,0,1},17,50,slope,80);
        require(moved.position.x<=0.f && moved.position.y>-.5f,
                "repeated virtual steps must not enter a bottomless world boundary");
        } else {
        require(!moved.collision.preFall && moved.position.x>0.f,
                "a real drop with a lower floor must remain accessible to virtual walking");
        for(int i=0;i<180;++i)
          moved=walker.moveVirtual({0,moved.velocity.y*.017f,0},17,50,slope,80);
        require(moved.position.y<-450.f && moved.grounded,
                "virtual feet must be able to fall and land on a lower floor");
        }
      }
    }

  void testContracts() {
    CollisionWorld world;
    auto ground = world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(20,.5f,20)),at(0,-50,0),
                                         .75f,DynamicWorld::C_Landscape);

    // The game supplies centimeters; shapes are expressed in meters.
    const auto hit = world.rayCast({0,100,0},{0,-100,0},landscapeMask);
    require(hit.hasHit,"ground ray must hit");
    requireNear(hit.point.y,0,.001f,"ground ray must report centimeters");
    requireNear(hit.normal.y,1,.001f,"ground ray normal must face upward");

    // Overlap alone must not block a sphere moving away from the surface.
    const auto departing = world.raySweep({0,10,0},{0,1000,0},50,landscapeMask);
    require(!departing.hasHit,"separating sphere overlap must not report a hit");
    const auto approaching = world.raySweep({0,10,0},{0,-1000,0},50,landscapeMask);
    require(approaching.hasHit,"approaching sphere overlap must report a hit");
    requireNear(approaching.point.y,-40,.01f,"overlap sweep must project the sphere bottom onto the game ray");

    // NPC movement consumes the overlap normal, so its orientation is gameplay behavior.
    Tempest::Vec3 normal;
    auto* staleVob = reinterpret_cast<Interactive*>(ground.get());
    Interactive* vob = staleVob;
    JPH::CapsuleShape capsule(.1f,.1f);
    require(world.hasCollision(&capsule,{0,5,0},normal,vob),"NPC capsule must overlap the ground");
    require(normal.y>.9f,"NPC overlap normal must face away from the ground");
    require(vob==nullptr,"terrain contact must clear a previous interactive pointer");
    vob = staleVob;
    require(!world.hasCollision(&capsule,{0,10000,0},normal,vob),"distant NPC query must miss the terrain");
    require(vob==staleVob,"missed NPC query must preserve the caller's interactive pointer");

    auto sensor = world.addCollisionBody(new JPH::BoxShape(JPH::Vec3(1,.1f,1)),at(0,500,0),0,
                                         DynamicWorld::C_Ghost,false,true);
    require(!world.rayCast({0,700,0},{0,300,0},landscapeMask).hasHit,
            "trigger sensor must be excluded from landscape rays");
    const auto sensorHit = world.rayCast({0,700,0},{0,300,0},sensorMask);
    require(sensorHit.hasHit,"trigger sensor must remain explicitly queryable");
    requireNear(sensorHit.point.y,510,.01f,"sensor query must report its surface in centimeters");

    auto matrix = at(0,700,0);
    matrix.scale(2,3,4);
    auto item = world.addDynamicBody(new JPH::BoxShape(JPH::Vec3(.15f,.15f,.15f)),matrix,.4f,1);
    world.tick(1000);
    // At most two 1/60 s updates may run after a long frame. Semi-implicit integration
    // moves the box by g * step^2 + 2 * g * step^2 on those two updates.
    const float step = 1.f/60.f;
    const float expectedY = 700.f-3.f*DynamicWorld::gravityMS*100.f*step*step;
    requireNear(item->position().y,expectedY,.01f,"long frame must simulate two fixed steps");
    const auto itemMatrix = item->matrix();
    requireNear(itemMatrix.at(0,0),2,.001f,"dynamic body must retain its x scale");
    requireNear(itemMatrix.at(1,1),3,.001f,"dynamic body must retain its y scale");
    requireNear(itemMatrix.at(2,2),4,.001f,"dynamic body must retain its z scale");

    for(int i=0;i<300;++i)
      world.tick(17);
    require(item->position().y>30 && item->position().y<50,
            "dropped box must fall through the sensor and settle on the ground");

    // Animated models use kinematic triangle meshes, whose mass cannot be inferred.
    JPH::TriangleList triangles;
    triangles.emplace_back(JPH::Float3(-1,0,-1),JPH::Float3(0,0,1),JPH::Float3(1,0,-1));
    const auto mesh = JPH::MeshShapeSettings(triangles).Create();
    require(!mesh.HasError(),"kinematic test mesh must be constructible");
    auto moving = world.addCollisionBody(mesh.Get(),at(300,100,0),.4f,DynamicWorld::C_Object,true);
    require(world.rayCast({300,200,0},{300,0,0},objectMask).hasHit,
            "kinematic mesh must participate in object queries");
    moving->setObjMatrix(at(300,200,0));
    const auto moved = world.rayCast({300,300,0},{300,0,0},objectMask);
    require(moved.hasHit,"moved kinematic mesh must remain queryable");
    requireNear(moved.point.y,200,.01f,"kinematic move must update the broad phase immediately");


    // Sector counts exceed Jolt's native 32 mesh materials. Triangle user data must
    // survive BVH reordering and the compound children used for large landscapes.
    std::vector<JPH::Vec3> vertices;
    std::vector<uint32_t> indices;
    std::vector<std::string> sectors(40);
    for(size_t i=0;i<sectors.size();++i) {
      const float x = float(i)*10.f;
      vertices.emplace_back(x-1,0,-1);
      vertices.emplace_back(x+1,0,-1);
      vertices.emplace_back(x,0,1);
      indices.push_back(uint32_t(i*3));
      indices.push_back(uint32_t(i*3+1));
      indices.push_back(uint32_t(i*3+2));
      sectors[i] = "sector-"+std::to_string(i);
      }
    PhysicVbo metadataMesh(&vertices);
    for(size_t i=0;i<sectors.size();++i) {
      const auto material = i%2==0 ? zenkit::MaterialGroup::STONE : zenkit::MaterialGroup::WOOD;
      metadataMesh.addIndex(indices,i*3,3,material,sectors[i].c_str());
      }
    auto metadataBody = world.addCollisionBody(metadataMesh.shape(),at(500,-1000,0),.75f,
                                               DynamicWorld::C_Landscape,false,false,&metadataMesh);
    for(size_t i=0;i<sectors.size();++i) {
      const float x = 500.f+float(i)*1000.f;
      const auto triangleHit = world.rayCast({x,-900,0},{x,-1100,0},landscapeMask);
      require(triangleHit.hasHit,"translated landscape triangle must be queryable");
      requireNear(triangleHit.point.y,-1000,.01f,"translated compound mesh must preserve its shape origin");
      const auto material = i%2==0 ? zenkit::MaterialGroup::STONE : zenkit::MaterialGroup::WOOD;
      require(triangleHit.material==material,"mesh hit must retain its Gothic material");
      require(triangleHit.sector!=nullptr && sectors[i]==triangleHit.sector,
              "mesh hit must retain its sector beyond 32 segments");
      }
    }
  }

int main() {
  try {
    testMovableVobImpulseInertia();
    testMovableTableImpulseCouple();
    testMeshRayEdges();
    testCoincidentMaterials();
    testSharedRayEdge();
    testOriginalFloorProjection();
    testSourceTerrainNormals();
    testTerrainRayNormalScope();
    testSphereQueryPrecision();
    testShallowOverlapProjection();
    testNpcContactDistance();
    testNpcScalarWitness();
    testNpcSeparatedContactResponse();
    testFixedStepCarry();
    testFixedStepCadence();
    testContracts();
    testCharacterVirtual();
    testVirtualFloorGuard();
    testMovableVobBodies();
    testMovableVobLegacyCollision();
    testMovableVobGeometrySupport();
    testMovableVobGeometryPenetration();
    testMovableVobGeometryTopObjects();
    testMovableVobGeometryAuthoredToggle();
    testMovableVobClampedSavePose();
    testMovableVobSave();
    testMovableTraceThresholds();
    testMovableVobArrowIdentity();
    testPhysicsNoise();
    testRagdollNoise();
    testOffCenterImpulse();
    std::puts("Physics contracts passed");
    return EXIT_SUCCESS;
    }
  catch(const std::exception& error) {
    std::fprintf(stderr,"FAIL: physics contract exception: %s\n",error.what());
    return EXIT_FAILURE;
    }
  }
