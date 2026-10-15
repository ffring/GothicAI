#include "bodyreplay.h"

#include "bodyarena.h"
#include "bodycontroller.h"
#include "bodyobservations.h"
#include "bodytrajectory.h"
#include "gothic.h"
#include "testbench.h"
#include "world/objects/npc.h"
#include "world/world.h"

#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#endif
#include <rapidjson/document.h>
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

#include <filesystem>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace {
struct Command final {
  uint64_t step=0, commandId=0, actionId=0, goal=0, target=0, observation=0, ttl=0;
  };

struct Replay final {
  bool active=false, started=false;
  std::vector<Command> commands;
  size_t next=0;
  std::string provider;
  uint64_t tailMs=0, startStep=0, endAt=0;
  uint64_t entity=0, generation=0;
  unsigned accepted=0, rejected=0, observationMismatches=0;
  std::ofstream observations, control, arena, log;
  };

Replay& replay() {
  static Replay r;
  return r;
  }

uint64_t gameTime() {
  auto world=Gothic::inst().world();
  return world==nullptr ? 0 : world->tickCount();
  }

uint64_t field(const rapidjson::Value& v, const char* key) {
  if(!v.HasMember(key) || !v[key].IsUint64())
    throw std::runtime_error(std::string("bodyreplay: ")+key+" must be uint64");
  return v[key].GetUint64();
  }

void writeRow(std::ofstream& file, std::string_view row) {
  file.write(row.data(),std::streamsize(row.size()));
  file.put('\n');
  file.flush();
  if(!file)
    throw std::runtime_error("bodyreplay: journal write failed");
  }

// One body-control request, as the runner's IPC passes it to the common authority.
rapidjson::Document request(std::string_view type, uint64_t generation) {
  auto& r=replay();
  rapidjson::Document d(rapidjson::kObjectType);
  auto& a=d.GetAllocator();
  d.AddMember("type",rapidjson::Value(type.data(),rapidjson::SizeType(type.size()),a),a);
  d.AddMember("entity_id",r.entity,a);
  d.AddMember("session_epoch",BodyObservations::sessionEpoch(),a);
  d.AddMember("world_epoch",BodyObservations::worldEpoch(),a);
  d.AddMember("control_generation",generation,a);
  d.AddMember("provider",rapidjson::Value(r.provider.c_str(),a),a);
  return d;
  }

rapidjson::Document call(const rapidjson::Document& req) {
  rapidjson::Document reply;
  reply.Parse(BodyController::handleJson(req).c_str());
  if(reply.HasParseError() || !reply.IsObject())
    throw std::runtime_error("bodyreplay: invalid body control reply");
  return reply;
  }

bool accepted(const rapidjson::Document& reply) {
  return reply.HasMember("accepted") && reply["accepted"].IsBool() && reply["accepted"].GetBool();
  }

std::string reason(const rapidjson::Document& reply) {
  return reply.HasMember("reason") && reply["reason"].IsString() ? reply["reason"].GetString() : "";
  }

// The runner's reset tail and the driver's first calls: subscription, the sealed initial state,
// the lease on the primary and the arena start, all between the setup step and the next one.
void begin() {
  auto& r=replay();
  const auto actors=BodyArena::actors();
  if(actors.size()!=2)
    throw std::runtime_error("bodyreplay: the arena did not produce two actors");
  const auto step=BodyObservations::currentStep();
  BodyController::reset("observation_subscription_changed");
  BodyObservations::reset(2);
  BodyObservations::beginStep(step);
  BodyObservations::Config config;
  config.periodMs=50;
  config.maxActors=2;
  config.queueCapacity=256;
  BodyObservations::configure(config);
  BodyObservations::watch(actors);
  BodyController::setActors(actors);
  BodyObservations::sampleAll();
  BodyArena::sealInitialState();

  r.entity=BodyObservations::entityId(*actors[0]);
  auto acquire=request("body_acquire",0);
  auto& a=acquire.GetAllocator();
  acquire.AddMember("target_entity_id",BodyObservations::entityId(*actors[1]),a);
  acquire.AddMember("body_control",true,a);
  acquire.AddMember("mode","lockstep",a);
  acquire.AddMember("timeout_ms",uint64_t(5000),a);
  const auto lease=call(acquire);
  if(!accepted(lease))
    throw std::runtime_error("bodyreplay: acquire rejected: "+reason(lease));
  r.generation=lease["control_generation"].GetUint64();
  BodyArena::start("scripted");
  r.startStep=step;
  r.started=true;
  }

void issue(const Command& c) {
  auto& r=replay();
  auto* actor=BodyObservations::findEntity(r.entity);
  const uint64_t seq=actor!=nullptr ? BodyObservations::latestSequence(*actor) : 0;
  const uint64_t observed=actor!=nullptr ? BodyObservations::latestGameMs(*actor) : 0;
  if(seq!=c.observation)
    ++r.observationMismatches;
  auto action=request("body_action",r.generation);
  auto& a=action.GetAllocator();
  action.AddMember("goal_version",c.goal,a);
  action.AddMember("target_entity_id",c.target,a);
  action.AddMember("command_id",c.commandId,a);
  action.AddMember("action_id",c.actionId,a);
  action.AddMember("observation_seq",seq,a);
  action.AddMember("issued_game_ms",observed,a);
  action.AddMember("ttl_ms",c.ttl,a);
  const auto reply=call(action);
  if(accepted(reply)) ++r.accepted; else ++r.rejected;
  std::ostringstream row;
  row << "{\"kind\":\"replay_command\",\"step\":" << c.step << ",\"command_id\":" << c.commandId
      << ",\"action_id\":" << c.actionId << ",\"recorded_observation_seq\":" << c.observation
      << ",\"observation_seq\":" << seq << ",\"t_game_ms\":" << gameTime()
      << ",\"accepted\":" << (accepted(reply) ? "true" : "false") << ",\"reason\":\"" << reason(reply) << "\"}";
  writeRow(r.log,row.str());
  }

void finish() {
  auto& r=replay();
  std::ostringstream row;
  row << "{\"kind\":\"replay_end\",\"commands\":" << r.commands.size() << ",\"issued\":" << r.next
      << ",\"accepted\":" << r.accepted << ",\"rejected\":" << r.rejected
      << ",\"observation_mismatches\":" << r.observationMismatches << ",\"t_game_ms\":" << gameTime()
      << ",\"arena\":" << BodyArena::statusJson() << "}";
  writeRow(r.log,row.str());
  BodyReplay::stop("finished");
  }
}

void BodyReplay::load(const std::string& path, const std::string& outDir, uint64_t tailMs) {
  auto& r=replay();
  if(r.active)
    throw std::runtime_error("bodyreplay: a replay is already running");
  std::ifstream file(path);
  std::stringstream text;
  text << file.rdbuf();
  rapidjson::Document doc;
  doc.Parse(text.str().c_str());
  if(!file || doc.HasParseError() || !doc.IsObject() || !doc.HasMember("arena") || !doc["arena"].IsObject() ||
     !doc.HasMember("commands") || !doc["commands"].IsArray())
    throw std::runtime_error("bodyreplay: cannot read "+path);
  const auto& arena=doc["arena"];
  BodyArena::Config config;
  config.scenario=arena.HasMember("scenario") && arena["scenario"].IsString() ? arena["scenario"].GetString() : "";
  if(config.scenario!="native_duel")
    throw std::runtime_error("bodyreplay: only native_duel episodes are replayed");
  config.seed=uint32_t(field(arena,"seed"));
  config.side=unsigned(field(arena,"side"));
  config.distanceCm=float(field(arena,"distance_cm"));
  config.timeoutMs=field(arena,"timeout_ms");
  // The runner's optional reset fields: an arena against a wall, turned or moved actors.
  const auto number=[&](const char* key,float fallback) {
    return arena.HasMember(key) && arena[key].IsNumber() ? arena[key].GetFloat() : fallback;
    };
  if(arena.HasMember("geometry") && arena["geometry"].IsString())
    config.geometry=arena["geometry"].GetString();
  if(arena.HasMember("wall_clearance_cm"))
    config.wallClearanceCm=number("wall_clearance_cm",0);
  if(arena.HasMember("wall_tangent_sign") && arena["wall_tangent_sign"].IsInt())
    config.wallTangentSign=arena["wall_tangent_sign"].GetInt();
  config.yawOffsetDeg=number("yaw_offset_deg",0);
  config.opponentYawOffsetDeg=number("opponent_yaw_offset_deg",0);
  config.separationYawDeg=number("separation_yaw_deg",0);
  if(arena.HasMember("center_offset_cm") && arena["center_offset_cm"].IsArray() && arena["center_offset_cm"].Size()==2)
    config.centerOffsetCm={arena["center_offset_cm"][0].GetFloat(),arena["center_offset_cm"][1].GetFloat()};

  r.commands.clear();
  for(const auto& item:doc["commands"].GetArray()) {
    Command c;
    c.step=field(item,"step"); c.commandId=field(item,"command_id"); c.actionId=field(item,"action_id");
    c.goal=field(item,"goal_version"); c.target=field(item,"target_entity_id");
    c.observation=field(item,"observation_seq"); c.ttl=field(item,"ttl_ms");
    if(!r.commands.empty() && c.step<r.commands.back().step)
      throw std::runtime_error("bodyreplay: commands must be ordered by step");
    r.commands.push_back(c);
    }
  r.provider=doc.HasMember("provider") && doc["provider"].IsString() ? doc["provider"].GetString() : "stage9-rl";

  // The journals the runner writes for an episode, under the same names.
  const auto dir=std::filesystem::path(outDir);
  r.observations.open(dir/"observations.jsonl",std::ios::trunc);
  r.control.open(dir/"body-control-events.jsonl",std::ios::trunc);
  r.arena.open(dir/"arena-events.jsonl",std::ios::trunc);
  r.log.open(dir/"replay.jsonl",std::ios::trunc);
  if(!r.observations || !r.control || !r.arena || !r.log)
    throw std::runtime_error("bodyreplay: cannot create journals in "+outDir);
  BodyController::setSink([](std::string_view row) {
    writeRow(replay().control,row);
    BodyArena::noteControl(row);
    BodyTrajectory::noteControl(row);
    });
  BodyObservations::setSink([](std::string_view row) { writeRow(replay().observations,row); });
  BodyArena::setSink([](std::string_view row) { writeRow(replay().arena,row); });

  // As the runner's fork at the held `bodyarena` line: a new observation generation, same step.
  const auto step=BodyObservations::currentStep();
  BodyObservations::reset(1);
  BodyObservations::beginStep(step);
  BodyArena::configure(config,1);
  r.next=0; r.accepted=r.rejected=r.observationMismatches=0;
  r.tailMs=tailMs; r.endAt=0; r.started=false; r.generation=0;
  r.active=true;
  }

void BodyReplay::postStep() {
  auto& r=replay();
  if(!r.active)
    return;
  try {
    // The runner's step tail, then what its driver does before the next step.
    BodyArena::postStep();
    if(!r.started) {
      if(BodyArena::ready())
        begin();
      return;
      }
    if(!BodyArena::complete()) {
      BodyController::pumpWatchdogs();
      BodyArena::postStep();
      }
    BodyObservations::drainJson();
    BodyController::drainJson();
    if(!BodyArena::complete()) {
      const auto heartbeat=call(request("body_heartbeat",r.generation));
      if(!accepted(heartbeat) && reason(heartbeat)!="release_pending")
        throw std::runtime_error("bodyreplay: heartbeat rejected: "+reason(heartbeat));
      const auto step=BodyObservations::currentStep()-r.startStep;
      while(r.next<r.commands.size() && r.commands[r.next].step<=step)
        issue(r.commands[r.next++]);
      return;
      }
    if(r.endAt==0) {
      // The runner stops at the outcome: the tail kept for the camera is not part of the episode.
      BodyObservations::setSink({});
      BodyController::setSink({});
      r.observations.close();
      r.control.close();
      r.endAt=gameTime()+r.tailMs;
      }
    if(gameTime()>=r.endAt)
      finish();
    }
  catch(const std::exception& error) {
    if(r.log)
      writeRow(r.log,std::string("{\"kind\":\"replay_error\",\"reason\":\"")+error.what()+"\"}");
    stop("error");
    }
  }

bool BodyReplay::active() {
  return replay().active;
  }

void BodyReplay::stop(std::string_view reason) {
  auto& r=replay();
  if(!r.active)
    return;
  r.active=false;
  BodyController::setSink({});
  BodyObservations::setSink({});
  BodyArena::setSink({});
  r.observations.close(); r.control.close(); r.arena.close(); r.log.close();
  TestBench::endWait(reason);
  }
