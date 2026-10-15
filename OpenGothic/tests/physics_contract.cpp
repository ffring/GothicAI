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

  std::vector<uint8_t> saveArchive(const std::function<void(Serialize&)>& write) {
    std::vector<uint8_t> bytes;
    Tempest::MemWriter writer(bytes);
    {
      Serialize output(writer);
      write(output);
    }
    return bytes;
    }

  void requireMatrix(const Tempest::Matrix4x4& actual, const Tempest::Matrix4x4& expected,
                     const char* message) {
    for(int c=0;c<4;++c)
      for(int r=0;r<4;++r)
        requireNear(actual.at(c,r),expected.at(c,r),c==3 ? .001f : .00001f,message);
    }

  void loadMovables(DynamicWorld& world, const std::vector<uint8_t>& bytes) {
    Tempest::MemReader reader(bytes);
    Serialize input(reader);
    world.loadMovableVobs(input);
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
    testMovableVobBodies();
    testMovableVobLegacyCollision();
    testMovableVobSave();
    testMovableVobArrowIdentity();
    testPhysicsNoise();
    testRagdollNoise();
    std::puts("Physics contracts passed");
    return EXIT_SUCCESS;
    }
  catch(const std::exception& error) {
    std::fprintf(stderr,"FAIL: physics contract exception: %s\n",error.what());
    return EXIT_FAILURE;
    }
  }
