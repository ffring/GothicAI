#include <Tempest/File>

#include "commandline.h"
#include "gothic.h"
#include "gamemusic.h"
#include "resources.h"
#include "game/bodycontroller.h"
#include "game/bodyobservations.h"
#include "game/playercontrol.h"
#include "game/serialize.h"
#include "game/simulationstep.h"
#include "testbench.h"
#include "utils/workers.h"
#include "world/objects/npc.h"

#include <rapidjson/document.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <array>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unistd.h>

namespace {
using Json = rapidjson::Document;
size_t assertions=0, cases=0;

void expect(bool condition,std::string_view message) {
  ++assertions;
  if(!condition) {
    std::cerr << "body-watch-test failure: " << message << '\n';
    throw std::runtime_error(std::string(message));
    }
  }

Json parse(const std::string& encoded) {
  Json d;
  d.Parse(encoded.c_str());
  if(d.HasParseError() || !d.IsObject()) throw std::runtime_error("invalid body JSON");
  return d;
  }

void number(Json& d,const char* key,uint64_t value) {
  d.AddMember(rapidjson::Value(key,d.GetAllocator()),rapidjson::Value(value),d.GetAllocator());
  }

Json status(const Npc& actor) {
  auto d=parse(BodyController::statusJson(&actor));
  Json row;
  row.CopyFrom(d["actors"][0],row.GetAllocator());
  return row;
  }

Json request(const char* type,const Npc& actor) {
  auto row=status(actor);
  Json d(rapidjson::kObjectType);
  d.AddMember("type",rapidjson::Value(type,d.GetAllocator()),d.GetAllocator());
  d.AddMember("provider","body-watch-test",d.GetAllocator());
  for(const char* key:{"session_epoch","world_epoch","entity_id","control_generation"})
    number(d,key,row[key].GetUint64());
  return d;
  }

Json call(const Json& request) { return parse(BodyController::handleJson(request)); }

Json action(Npc& actor,FightAlgo::PolicyAction policy) {
  auto d=request("body_action",actor);
  const auto current=status(actor);
  for(const char* key:{"goal_version","target_entity_id","observation_seq"})
    number(d,key,current[key].GetUint64());
  number(d,"issued_game_ms",current["observed_game_ms"].GetUint64());
  number(d,"command_id",1); number(d,"action_id",uint64_t(policy)); number(d,"ttl_ms",1000);
  return call(d);
  }

Json acquire(Npc& actor,uint64_t awaitMs=0) {
  auto d=request("body_acquire",actor);
  d.AddMember("body_control",true,d.GetAllocator());
  d.AddMember("mode","lockstep",d.GetAllocator());
  number(d,"timeout_ms",5000);
  number(d,"await_boundary_ms",awaitMs);
  number(d,"target_entity_id",BodyObservations::entityId(*actor.target()));
  return call(d);
  }

void release(Npc& actor) {
  auto result=call(request("body_release",actor));
  expect(result["accepted"].GetBool(),"release accepted");
  expect(!BodyController::owns(actor),"idle lease released at safe boundary");
  }

using Identity = std::array<uint64_t,4>;
Identity identity(const rapidjson::Value& row) {
  return {row["session_epoch"].GetUint64(),row["world_epoch"].GetUint64(),
          row["entity_id"].GetUint64(),row["control_generation"].GetUint64()};
  }

void step(PlayerControl& player,unsigned count=1) {
  for(unsigned i=0;i<count;++i) {
    SimulationStep::worldPlayerStep(33,player);
    SimulationStep::finishStep(33,player);
    }
  }

void initialWatch(Npc& actor,uint64_t generation) {
  BodyObservations::reset(generation);
  BodyObservations::configure(BodyObservations::Config{});
  BodyObservations::watch({&actor});
  BodyController::setActors({&actor});
  BodyObservations::sampleAll();
  }

void liveLease(Npc& a,Npc& b,PlayerControl& player) {
  ++cases;
  initialWatch(a,100);
  expect(acquire(a)["accepted"].GetBool(),"acquire A");
  step(player,2);
  const auto before=status(a);
  const auto stable=identity(before);
  const auto sequence=BodyObservations::latestSequence(a);
  expect(sequence>1,"A has established observation stream");
  BodyObservations::drainJson();

  expect(std::string_view(BodyController::updateWatch({&a,&b},50))=="allowed","grow watch with lease A");
  expect(identity(status(a))==stable,"A identity survives grow");
  expect(BodyObservations::latestSequence(a)==sequence,"grow preserves A observation sequence");
  expect(BodyObservations::latestSequence(b)==0,"new B awaits next tick");
  step(player,2);
  expect(BodyObservations::latestSequence(a)>sequence,"A observation sequence continues growing");
  expect(BodyObservations::latestSequence(b)>0,"B receives observations");
  auto rows=parse(BodyObservations::drainJson());
  size_t aRows=0,bRows=0;
  uint64_t previous=sequence;
  for(const auto& row:rows["observations"].GetArray()) {
    if(row["entity_id"].GetUint64()==stable[2]) {
      ++aRows;
      expect(identity(row)==stable,"A stream identity survives grow");
      expect(row["observation_seq"].GetUint64()>previous,"A rows continue without sequence reset");
      previous=row["observation_seq"].GetUint64();
      }
    if(row["entity_id"].GetUint64()==BodyObservations::entityId(b)) ++bRows;
    }
  expect(aRows>0 && bRows>0,"both actors emit real observation rows");

  auto action=request("body_action",a);
  const auto current=status(a);
  for(const char* key:{"goal_version","target_entity_id","observation_seq"})
    number(action,key,current[key].GetUint64());
  number(action,"issued_game_ms",current["observed_game_ms"].GetUint64());
  number(action,"command_id",1); number(action,"action_id",0); number(action,"ttl_ms",1000);
  expect(call(action)["accepted"].GetBool(),"action on original A lease accepted after grow");
  b.setExternalControl(false);
  expect(acquire(b)["accepted"].GetBool(),"newly watched B can acquire lease");
  const auto bIdentity=identity(status(b));
  for(const auto& watch:std::array<std::vector<Npc*>,2>{{{&b},{}}}) {
    expect(std::string_view(BodyController::updateWatch(watch,50))!="allowed","cannot omit leased A");
    expect(identity(status(a))==stable && identity(status(b))==bIdentity,"refused watch leaves both leases intact");
    expect(BodyObservations::watchedCount()==2,"refused watch leaves subscription intact");
    }
  expect(std::string_view(BodyController::updateWatch({&a,&b},100))!="allowed","cannot change rate under lease");
  expect(BodyObservations::samplePeriodMs()==50,"refused rate leaves period intact");
  step(player,2);
  const auto cursor=BodyObservations::queuedTotal();
  release(b);
  BodyObservations::sampleAll();
  expect(!BodyObservations::queuedSince(cursor).empty(),"B release queues a row after the cursor");
  b.setExternalControl(true);
  expect(std::string_view(BodyController::updateWatch({&a},50))=="allowed","remove free B while A remains leased");
  expect(identity(status(a))==stable,"A identity survives shrink");
  for(const auto& row:BodyObservations::queuedSince(cursor))
    expect(row.enqueueIndex>cursor && row.entityId==stable[2],"queue cursor skips removed B without returning old A rows");
  rows=parse(BodyObservations::drainJson());
  expect(!rows["observations"].Empty(),"shrink retains already queued A rows");
  for(const auto& row:rows["observations"].GetArray())
    expect(row["entity_id"].GetUint64()==stable[2],"shrink drops already queued free B rows");
  step(player,2);
  rows=parse(BodyObservations::drainJson());
  expect(!rows["observations"].Empty(),"A stream continues after shrink");
  for(const auto& row:rows["observations"].GetArray())
    expect(row["entity_id"].GetUint64()==stable[2],"removed B emits no new rows");
  release(a);
  }

void pendingAcquire(Npc& a,Npc& b,PlayerControl& player) {
  ++cases;
  // Start the real script state once, then clean its initial work with the same fixture used
  // by the body API suite. fixtureLock installs an actual FAI game-time wait.
  const auto attack=a.world().script().findSymbolIndex("ZS_ATTACK");
  expect(attack!=size_t(-1),"native combat script exists");
  expect(a.startState(ScriptFn(attack),""),"start native combat state");
  a.setOther(&b);
  for(unsigned i=0;i<60 && !a.isStateStarted();++i) step(player);
  expect(a.isStateStarted() && a.currentState()==ScriptFn(attack),"native combat state started");
  BodyController::fixturePrepare(a,b,300,0);
  a.setExternalControl(false);
  initialWatch(a,101);
  BodyController::fixtureLock(a,200);
  const auto pending=acquire(a,2000);
  expect(pending["accepted"].GetBool() && std::string_view(pending["reason"].GetString())=="acquire_pending","A waits for real locked boundary");
  const auto requestId=pending["acquire_request_id"].GetUint64();
  const auto stable=identity(status(a));
  const auto sequence=BodyObservations::latestSequence(a);
  expect(BodyController::hasOwners(),"pending acquire counts as body owner");
  expect(std::string_view(BodyController::updateWatch({&b},50))!="allowed","cannot omit pending A");
  expect(std::string_view(BodyController::updateWatch({},50))!="allowed","cannot clear pending A");
  expect(std::string_view(BodyController::updateWatch({&a,&b},50))=="allowed","grow watch with pending A");
  auto current=status(a);
  expect(current["acquire_pending"].GetBool() && current["acquire_request_id"].GetUint64()==requestId,"pending request survives with same ID");
  expect(identity(current)==stable && BodyObservations::latestSequence(a)==sequence,"pending identity and sequence preserved");
  for(unsigned i=0;i<20 && !BodyController::owns(a);++i) step(player);
  expect(BodyController::owns(a),"pending A grants at natural boundary");
  current=status(a);
  expect(current["acquire_request_id"].GetUint64()==requestId,"granted lease retains original pending request ID");
  expect(current["session_epoch"].GetUint64()==stable[0] && current["world_epoch"].GetUint64()==stable[1] && current["entity_id"].GetUint64()==stable[2],"grant retains stream identity");
  expect(BodyObservations::latestSequence(a)>sequence,"pending A stream sequence keeps growing");
  release(a);
  }

void movementRelease(Npc& actor,Npc& target,PlayerControl& player) {
  struct Case final { FightAlgo::PolicyAction action; float distance,yaw; };
  const std::array<Case,7> fixtures{{
    {FightAlgo::PolicyAction::Approach,800,0},
    {FightAlgo::PolicyAction::TurnToTarget,300,30},
    {FightAlgo::PolicyAction::StrafeLeft,300,0},
    {FightAlgo::PolicyAction::StrafeRight,300,0},
    {FightAlgo::PolicyAction::JumpBack,150,0},
    {FightAlgo::PolicyAction::AttackFront,150,0},
    {FightAlgo::PolicyAction::Block,150,0},
    }};
  for(const auto& fixture:fixtures) {
    ++cases;
    BodyController::fixturePrepare(actor,target,fixture.distance,fixture.yaw);
    actor.setExternalControl(false);
    initialWatch(actor,200+uint64_t(fixture.action));
    expect(acquire(actor)["accepted"].GetBool(),"release fixture acquired");
    step(player,2);
    const auto prepared=status(actor);
    std::cerr << "release-fixture prepare action=" << uint64_t(fixture.action)
              << " pose=" << actor.animNames() << " can_decide=" << prepared["can_decide"].GetBool()
              << " mask=" << prepared["action_mask"][rapidjson::SizeType(fixture.action)].GetBool() << '\n';
    const auto accepted=action(actor,fixture.action);
    if(!accepted["accepted"].GetBool())
      std::cerr << "release-fixture rejected reason=" << accepted["reason"].GetString() << '\n';
    expect(accepted["accepted"].GetBool(),"release fixture action accepted");
    step(player);
    const auto before=actor.bodyObservationState();
    const auto pose=actor.animNames();
    const auto startedEvents=parse(BodyController::drainJson());
    bool started=false;
    for(const auto& event:startedEvents["events"].GetArray())
      if(std::string_view(event["phase"].GetString())=="started" &&
         event["action_id"].GetUint64()==uint64_t(fixture.action)) started=true;
    if(!started) {
      std::cerr << "release-fixture did not start action=" << uint64_t(fixture.action) << " pose=" << pose << '\n';
      for(const auto& event:startedEvents["events"].GetArray())
        std::cerr << "release-fixture event phase=" << event["phase"].GetString()
                  << " reason=" << event["reason"].GetString() << " action=" << event["action_id"].GetUint64() << '\n';
      }
    expect(started,"release fixture physically starts its requested action");
    const auto start=actor.world().tickCount();
    // Target replacement is deferred until the same safe handoff used when a live
    // opponent disappears. The explicit release must not let locomotion keep its lease.
    const auto releaseRequest=request("body_release",actor);
    bool deferredTargetCleared=false;
    actor.setTarget(nullptr);
    // Native perception may choose this enemy again on its next tick. Observe the
    // deferred mutation itself, immediately after setTarget and before native AI runs.
    expect(BodyController::deferNative(actor,[&] { deferredTargetCleared=actor.target()==nullptr; }),
           "release fixture queues target-loss verification before native handoff");
    const auto released=call(releaseRequest);
    expect(released["accepted"].GetBool() ||
           (std::string_view(released["reason"].GetString())=="not_owned" && !BodyController::owns(actor)),
           "moving release accepted or already returned to native after target loss");
    step(player);
    const bool ownedNextTick=BodyController::owns(actor);
    for(unsigned i=0;i<100 && BodyController::owns(actor);++i) step(player);
    const auto events=parse(BodyController::drainJson());
    bool restored=false;
    uint64_t elapsed=actor.world().tickCount()-start;
    for(const auto& event:events["events"].GetArray())
      if(std::string_view(event["reason"].GetString())=="native_restored") {
        restored=true; elapsed=event["t_game_ms"].GetUint64()-start;
        }
    std::cerr << "release-fixture action=" << uint64_t(fixture.action) << " pose=" << pose
              << " segment_ms=" << before.segmentRemainingMs << " fai_ms=" << before.faiWaitMs
              << " ani_ms=" << before.aniWaitMs << " ai_ms=" << before.aiWaitMs
              << " output_ms=" << before.outputWaitMs << " interruptible=" << before.interruptible
              << " owned_next_tick=" << ownedNextTick << " elapsed_game_ms=" << elapsed << '\n';
    expect(!BodyController::owns(actor),"release fixture returns native authority within 3.3 game seconds");
    expect(restored,"release fixture emits confirmed native_restored");
    expect(deferredTargetCleared,"release applies deferred target loss before native AI resumes");
    if(before.segmentRemainingMs==0)
      expect(!ownedNextTick,"unprotected movement returns native authority on the next engine tick");
    else {
      expect(elapsed>=before.segmentRemainingMs,"release waits for the whole protected combat segment");
      if(before.segmentRemainingMs>33)
        expect(ownedNextTick,"release preserves a protected combat segment");
      }
    }
  }
}

int main(int argc,const char** argv) {
  std::FILE* result=fdopen(dup(STDOUT_FILENO),"w");
  dup2(STDERR_FILENO,STDOUT_FILENO);
  try {
    std::string save,scenario;
    for(int i=1;i<argc;++i) {
      const std::string_view arg=argv[i];
      if(arg=="--save" || arg=="--scenario") {
        if(++i>=argc) throw std::runtime_error("missing fixture option value");
        (arg=="--save" ? save : scenario)=std::filesystem::absolute(argv[i]).string();
        }
      }
    if(scenario.empty()) throw std::runtime_error("--scenario scripts/bench/body-api.bench is required");
    setenv("OPENGOTHIC_PHYSICS_THREADS","0",0);
    Workers::setSerialExecution(true);
    CommandLine commandLine(argc,argv);
    Resources resources;
    Gothic gothic;
    gothic.isNpcInDialogFn=[](const Npc*) { return false; };
    gothic.setupGlobalScripts();
    GameMusic music;
    const auto output=std::filesystem::temp_directory_path()/("gothic-body-watch-test-"+std::to_string(getpid()));
    std::filesystem::create_directories(output);
    TestBench::configure(scenario,output.string(),17);
    if(save.empty()) {
      gothic.setGame(std::make_unique<GameSession>(std::string(gothic.defaultWorld())));
      } else {
      Tempest::RFile file(save);
      Serialize serialized(file);
      gothic.setGame(std::make_unique<GameSession>(serialized));
      }
    expect(gothic.world()!=nullptr && gothic.player()!=nullptr,"playable headless world");
    PlayerControl player;
    player.clearInput();
    gothic.gameSession()->setTimeMultiplyer(1.f);
    gothic.camera()->setViewport(640,480);
    player.clearFocus();
    SimulationStep::finishStep(0,player);
    for(unsigned i=0;i<300 && TestBench::bodyObservationActors().size()<2;++i) step(player);
    auto actors=TestBench::bodyObservationActors();
    expect(actors.size()==2,"body-api fixture created two real NPCs");
    auto& a=*actors[0]; auto& b=*actors[1];
    BodyController::fixturePrepare(b,a,300,0);
    BodyController::fixturePrepare(a,b,300,0);
    b.setDirection(a.position()-b.position()); b.updateTransform();
    a.setExternalControl(false);
    liveLease(a,b,player);
    movementRelease(a,b,player);
    pendingAcquire(a,b,player);
    expect(!Resources::hasGraphicsDevice() && !gothic.gameSession()->hasAudioDevice(),"fixture creates no graphics/audio device");
    BodyController::reset();
    BodyObservations::disable();
    TestBench::flushOutput();
    gothic.clearGame().reset();
    std::filesystem::remove_all(output);
    std::fprintf(result,"{\"type\":\"body_watch_test\",\"passed\":true,\"cases\":%zu,\"assertions\":%zu}\n",cases,assertions);
    std::fclose(result);
    return 0;
    } catch(const std::exception& error) {
    Json d(rapidjson::kObjectType);
    d.AddMember("type","body_watch_test",d.GetAllocator());
    d.AddMember("passed",false,d.GetAllocator());
    number(d,"cases",cases); number(d,"assertions",assertions);
    d.AddMember("failure",rapidjson::Value(error.what(),d.GetAllocator()),d.GetAllocator());
    rapidjson::StringBuffer buffer;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
    d.Accept(writer);
    std::fprintf(result,"%s\n",buffer.GetString());
    std::fclose(result);
    return 1;
    }
  }
