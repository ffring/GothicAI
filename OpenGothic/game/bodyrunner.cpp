#include <Tempest/File>
#include <Tempest/Log>
#include <zenkit/Logger.hh>
#include <rapidjson/document.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include "commandline.h"
#include "gothic.h"
#include "gamemusic.h"
#include "resources.h"
#include "game/playercontrol.h"
#include "game/serialize.h"
#include "game/simulationstep.h"
#include "game/bodyobservations.h"
#include "testbench.h"
#include "stucklogger.h"
#include "world/objects/npc.h"

#include <chrono>
#include <cmath>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <fcntl.h>
#include <iostream>
#include <optional>
#include <poll.h>
#include <stdexcept>
#include <thread>
#include <unistd.h>

namespace {
using Clock = std::chrono::steady_clock;
using Json = rapidjson::Document;
using Allocator = rapidjson::Document::AllocatorType;

struct Options {
  std::string save, scenario, output = ".body-runner", mode = "lockstep";
  bool stdio = false;
};

Options options(int argc, const char** argv) {
  Options result;
  for(int i=1; i<argc; ++i) {
    std::string arg = argv[i];
    auto value = [&]() -> std::string {
      if(++i>=argc)
        throw std::runtime_error("missing value for " + arg);
      return argv[i];
    };
    if(arg=="--save") result.save = value();
    else if(arg=="--scenario") result.scenario = value();
    else if(arg=="--output") result.output = value();
    else if(arg=="--mode") result.mode = value();
    else if(arg=="--stdio") result.stdio = true;
  }
  if(result.save.empty() || result.scenario.empty())
    throw std::runtime_error("--save and --scenario are required");
  if(result.mode!="lockstep" && result.mode!="realtime")
    throw std::runtime_error("mode must be lockstep or realtime");
  result.save = std::filesystem::absolute(result.save).string();
  result.scenario = std::filesystem::absolute(result.scenario).string();
  result.output = std::filesystem::absolute(result.output).string();
  return result;
}

void string(Json& json, const char* key, const std::string& value) {
  auto& a = json.GetAllocator();
  json.AddMember(rapidjson::Value(key,a), rapidjson::Value(value.c_str(),a),a);
}

std::string encode(const Json& json) {
  rapidjson::StringBuffer buffer;
  rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
  json.Accept(writer);
  return {buffer.GetString(),buffer.GetSize()};
}

std::string optionalString(const Json& command, const char* key, std::string fallback) {
  if(!command.HasMember(key))
    return fallback;
  if(!command[key].IsString())
    throw std::runtime_error(std::string(key) + " must be a string");
  return command[key].GetString();
}

class Runner final {
public:
  Runner(Gothic& gothic, Options options) : gothic(gothic), opts(std::move(options)) {}

  void reset(const Json& command) {
    const auto scenario = optionalString(command,"scenario",opts.scenario);
    const auto mode = optionalString(command,"mode",opts.mode);
    const auto output = optionalString(command,"output_dir",opts.output);
    if(mode!="lockstep" && mode!="realtime")
      throw std::runtime_error("mode must be lockstep or realtime");
    if(!std::filesystem::is_regular_file(scenario))
      throw std::runtime_error("scenario file is missing");
    int64_t seed = -1;
    if(command.HasMember("seed")) {
      if(!command["seed"].IsUint())
        throw std::runtime_error("seed must be uint32");
      seed = command["seed"].GetUint();
    }
    std::filesystem::create_directories(output);
    BodyObservations::reset(++observationGeneration);
    observationFile.close(); timingFile.close();
    watchedActors.clear(); watchRequested = false;
    player.reset();
    gothic.clearGame().reset();
    loaded = false;
    TestBench::configure(scenario,output,seed);
    // A process begins with C rand seed 1. Reconstruct that state for each independent episode,
    // separately from the script RNG reseeded by bodyduel. No wall-clock-derived randomness.
    std::srand(1);
    const auto from = Clock::now();
    Tempest::RFile file(opts.save);
    Serialize save(file);
    gothic.setGame(std::make_unique<GameSession>(save));
    if(gothic.world()==nullptr || gothic.player()==nullptr || gothic.world()->physic()==nullptr)
      throw std::runtime_error("save did not restore a playable world");
    player = std::make_unique<PlayerControl>();
    player->clearInput();
    gothic.gameSession()->setTimeMultiplyer(1.f);
    gothic.camera()->setViewport(640,480);
    player->clearFocus();
    // Match the rendered load-finalize frame: animation/camera/bench initialize at dt=0.
    SimulationStep::finishStep(0,*player);
    modeNow = mode;
    outputNow = output;
    generation++;
    steps = 0;
    elapsedSimMs = 0;
    resetWallMs = elapsed(from);
    started = Clock::now();
    due = started;
    loaded = true;
    maxLagMs = 0;
    observationFile.open(std::filesystem::path(outputNow)/"observations.jsonl",std::ios::trunc);
    timingFile.open(std::filesystem::path(outputNow)/"observer-timing.jsonl",std::ios::trunc);
    if(!observationFile || !timingFile)
      throw std::runtime_error("cannot create observation/timing journals");
    BodyObservations::setSink([this](std::string_view row) {
      observationFile.write(row.data(),std::streamsize(row.size()));
      observationFile.put('\n');
      if(!observationFile)
        throw std::runtime_error("observation journal write failed");
    });
  }

  void oneStep() {
    if(!loaded || TestBench::done())
      return;
    if(modeNow=="realtime")
      maxLagMs = std::max(maxLagMs,elapsed(due));
    const uint64_t dt = TestBench::frameDt(33);
    SimulationStep::worldPlayerStep(dt,*player);
    SimulationStep::finishStep(dt,*player);
    if(watchRequested) {
      const auto watchFrom = Clock::now();
      const auto actors = TestBench::bodyObservationActors();
      if(actors!=watchedActors) {
        const auto currentStep = BodyObservations::currentStep();
        BodyObservations::reset(++observationGeneration);
        BodyObservations::beginStep(currentStep);
        BodyObservations::watch(actors);
        watchedActors = actors;
      }
      const auto builderCost = BodyObservations::lastStepCostUs();
      const auto cost = builderCost+uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-watchFrom).count());
      timingFile << "{\"sim_step\":" << BodyObservations::currentStep()
                 << ",\"game_ms\":" << gothic.gameSession()->tickCount()
                 << ",\"wall_sample_us\":" << uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now().time_since_epoch()).count())
                 << ",\"kind\":\"sample_step\",\"watch_enabled\":true,\"actors\":" << BodyObservations::watchedCount()
                 << ",\"builder_wall_us\":" << builderCost << ",\"total_observer_wall_us\":" << cost << "}\n";
      if(!timingFile)
        throw std::runtime_error("observer timing journal write failed");
    }
    steps++;
    elapsedSimMs += dt;
    due = started + std::chrono::milliseconds(elapsedSimMs);
    if(TestBench::done())
      saveSummary();
  }

  void pace() {
    if(modeNow!="realtime")
      return;
    const auto now = Clock::now();
    if(now<due)
      std::this_thread::sleep_until(due);
    else
      maxLagMs = std::max(maxLagMs,elapsed(due));
  }

  Json status() const {
    Json json(rapidjson::kObjectType);
    auto& a = json.GetAllocator();
    json.AddMember("schema_version",1,a);
    json.AddMember("ok",true,a);
    json.AddMember("generation",generation,a);
    json.AddMember("game_ms",loaded ? gothic.gameSession()->tickCount() : uint64_t(0),a);
    json.AddMember("sim_step",steps,a);
    json.AddMember("done",loaded && TestBench::done(),a);
    json.AddMember("loaded",loaded,a);
    json.AddMember("elapsed_wall_ms",loaded ? elapsed(started) : uint64_t(0),a);
    json.AddMember("reset_wall_ms",resetWallMs,a);
    json.AddMember("simulated_ms",elapsedSimMs,a);
    json.AddMember("max_realtime_lag_ms",maxLagMs,a);
    json.AddMember("native_choices",TestBench::nativeChoices(),a);
    json.AddMember("graphics_device_created",Resources::hasGraphicsDevice(),a);
    json.AddMember("window_created",false,a); // No Window/Application is constructed in this entry point.
    json.AddMember("audio_device_created",loaded && gothic.gameSession()->hasAudioDevice(),a);
    Json observationStats;
    observationStats.Parse(BodyObservations::statsJson().c_str());
    if(observationStats.HasParseError())
      throw std::runtime_error("invalid native observation statistics");
    json.AddMember("body_observation",rapidjson::Value(observationStats,a),a);
    string(json,"mode",modeNow);
    string(json,"output_dir",outputNow);
    return json;
  }

  void bodyWatch(const Json& command) {
    if(!loaded)
      throw std::runtime_error("reset before body_watch");
    if(!command.HasMember("enabled") || !command["enabled"].IsBool())
      throw std::runtime_error("body_watch enabled must be boolean");
    if(!command["enabled"].GetBool()) {
      BodyObservations::disable(); watchRequested = false; watchedActors.clear();
      observationFile.flush(); timingFile.flush();
      return;
    }
    const auto integer = [&](const char* key,unsigned fallback) {
      if(!command.HasMember(key)) return fallback;
      if(!command[key].IsUint()) throw std::runtime_error(std::string(key)+" must be uint32");
      return command[key].GetUint();
    };
    const auto rate = integer("rate_hz",20);
    if(rate!=10 && rate!=20)
      throw std::runtime_error("rate_hz must be 10 or 20");
    BodyObservations::Config config;
    config.periodMs = 1000/rate;
    config.maxActors = integer("max_actors",4);
    config.queueCapacity = integer("queue_capacity",256);
    if(command.HasMember("ray_range_cm")) {
      if(!command["ray_range_cm"].IsNumber()) throw std::runtime_error("ray_range_cm must be finite numeric");
      config.rayRangeCm = command["ray_range_cm"].GetFloat();
    }
    if(config.maxActors==0 || config.maxActors>4 || config.queueCapacity==0 || config.queueCapacity>256 ||
       !std::isfinite(config.rayRangeCm) || config.rayRangeCm<=0 || config.rayRangeCm>2000)
      throw std::runtime_error("invalid bounded body observation configuration");
    const auto currentStep = BodyObservations::currentStep();
    BodyObservations::reset(++observationGeneration);
    BodyObservations::beginStep(currentStep);
    BodyObservations::configure(config);
    watchRequested = true;
    watchedActors = TestBench::bodyObservationActors();
    BodyObservations::watch(watchedActors);
  }

  Json observations(const Json& request) {
    if(request.HasMember("max") && (!request["max"].IsUint() || request["max"].GetUint()!=256))
      throw std::runtime_error("max must be 256; the configured bounded queue is drained in full");
    Json json;
    const auto packet = BodyObservations::drainJson();
    json.Parse(packet.c_str());
    if(json.HasParseError() || !json.IsObject())
      throw std::runtime_error("invalid native observation packet");
    auto current = status();
    for(auto it=current.MemberBegin();it!=current.MemberEnd();++it)
      if(!json.HasMember(it->name))
        json.AddMember(rapidjson::Value(it->name,json.GetAllocator()),rapidjson::Value(it->value,json.GetAllocator()),json.GetAllocator());
    return json;
  }

  void observationTransportCost(uint64_t wallUs) {
    if(!watchRequested)
      return;
    timingFile << "{\"kind\":\"packet_transport\",\"sim_step\":" << BodyObservations::currentStep()
               << ",\"game_ms\":" << gothic.gameSession()->tickCount()
               << ",\"watch_enabled\":true,\"actors\":" << BodyObservations::watchedCount()
               << ",\"builder_wall_us\":0,\"total_observer_wall_us\":" << wallUs << "}\n";
  }

  void saveSummary() const {
    if(!loaded)
      return;
    std::ofstream out(std::filesystem::path(outputNow)/"runner-summary.json");
    out << encode(status()) << '\n';
  }

  void close() {
    saveSummary();
    BodyObservations::disable(); BodyObservations::setSink({});
    observationFile.close(); timingFile.close();
    player.reset();
    gothic.clearGame().reset();
    loaded = false;
  }

  bool realtime() const { return loaded && modeNow=="realtime" && !TestBench::done(); }
  bool complete() const { return loaded && TestBench::done(); }
  bool isLoaded() const { return loaded; }
  Clock::time_point deadline() const { return due; }
  uint64_t step() const { return steps; }

private:
  static uint64_t elapsed(Clock::time_point from) {
    return uint64_t(std::max<int64_t>(0,std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now()-from).count()));
  }
  Gothic& gothic;
  std::unique_ptr<PlayerControl> player;
  Options opts;
  std::string modeNow = "lockstep", outputNow;
  uint64_t generation = 0, steps = 0, elapsedSimMs = 0, resetWallMs = 0, maxLagMs = 0;
  uint64_t observationGeneration = 0;
  Clock::time_point started{}, due{};
  bool loaded = false;
  bool watchRequested = false;
  std::vector<Npc*> watchedActors;
  std::ofstream observationFile, timingFile;
};

struct Advance {
  Json request;
  uint64_t startStep = 0, targetStep = 0, choices = 0;
  bool untilDecision = false;
};

class CommandInput final {
public:
  enum class State { Waiting, Line, End };
  struct Read {
    State state = State::Waiting;
    std::string line;
  };

  CommandInput() {
    originalFlags = fcntl(STDIN_FILENO,F_GETFL);
    if(originalFlags<0 || fcntl(STDIN_FILENO,F_SETFL,originalFlags | O_NONBLOCK)<0)
      throw std::runtime_error("cannot configure nonblocking command input");
  }

  ~CommandInput() {
    if(originalFlags>=0)
      fcntl(STDIN_FILENO,F_SETFL,originalFlags);
  }

  Read next(int timeoutMs) {
    if(auto line = bufferedLine())
      return {State::Line,std::move(*line)};
    if(ended)
      return end();

    pollfd input{STDIN_FILENO,POLLIN,0};
    const int ready = poll(&input,1,timeoutMs);
    if(ready<0) {
      if(errno==EINTR)
        return {};
      throw std::runtime_error("command input poll failed");
    }
    if(ready==0)
      return {};
    if(input.revents & (POLLERR | POLLNVAL))
      throw std::runtime_error("command input descriptor failed");

    char chunk[65536];
    const auto count = read(STDIN_FILENO,chunk,sizeof(chunk));
    if(count<0) {
      if(errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR)
        return {};
      throw std::runtime_error("command input read failed");
    }
    if(count==0) {
      ended = true;
      return end();
    }
    buffer.append(chunk,size_t(count));
    if(auto line = bufferedLine())
      return {State::Line,std::move(*line)};
    return {};
  }

private:
  std::optional<std::string> bufferedLine() {
    const auto newline = buffer.find('\n');
    if((newline==std::string::npos && buffer.size()>MaxFrameBytes) ||
       (newline!=std::string::npos && newline>MaxFrameBytes))
      throw std::runtime_error("JSONL command frame exceeds 1 MiB");
    if(newline==std::string::npos)
      return std::nullopt;
    auto line = buffer.substr(0,newline);
    buffer.erase(0,newline+1);
    return line;
  }

  Read end() const {
    if(!buffer.empty())
      throw std::runtime_error("truncated JSONL command at EOF: newline is required");
    return {State::End,{}};
  }

  static constexpr size_t MaxFrameBytes = 1024*1024;
  int originalFlags = -1;
  bool ended = false;
  std::string buffer;
};

void echoId(Json& response, const Json& request) {
  if(request.HasMember("id"))
    response.AddMember("id",rapidjson::Value(request["id"],response.GetAllocator()),response.GetAllocator());
}

void send(std::FILE* output, const Json& response) {
  const auto json = encode(response) + "\n";
  std::fwrite(json.data(),1,json.size(),output);
  std::fflush(output);
}

Advance advance(const Json& request, const Runner& runner) {
  if(!runner.isLoaded())
    throw std::runtime_error("reset before advance");
  if(!request.HasMember("substeps") || !request["substeps"].IsUint64() ||
     request["substeps"].GetUint64()==0 || request["substeps"].GetUint64()>100000)
    throw std::runtime_error("substeps must be an integer in [1,100000]");
  if(request.HasMember("until_decision") && !request["until_decision"].IsBool())
    throw std::runtime_error("until_decision must be boolean");
  Advance pending;
  pending.request.CopyFrom(request,pending.request.GetAllocator());
  pending.startStep = runner.step();
  pending.targetStep = pending.startStep + request["substeps"].GetUint64();
  pending.choices = TestBench::nativeChoices();
  pending.untilDecision = request.HasMember("until_decision") && request["until_decision"].GetBool();
  return pending;
}

bool finished(const Advance& pending, const Runner& runner) {
  return runner.complete() || runner.step()>=pending.targetStep ||
         (pending.untilDecision && TestBench::nativeChoices()!=pending.choices);
}

void advanceReply(std::FILE* output, const Advance& pending, const Runner& runner) {
  auto response = runner.status();
  response.AddMember("steps_advanced",runner.step()-pending.startStep,response.GetAllocator());
  string(response,"stopped_on",runner.complete() ? "completed" :
         (pending.untilDecision && TestBench::nativeChoices()!=pending.choices ? "native_decision" : "budget"));
  echoId(response,pending.request);
  send(output,response);
}
}

int main(int argc, const char** argv) {
  // Reserve protocol stdout before redirecting all engine/library logging (including C stdio).
  std::FILE* protocol = fdopen(dup(STDOUT_FILENO),"w");
  dup2(STDERR_FILENO,STDOUT_FILENO);
  try {
    const auto opts = options(argc,argv);
    std::filesystem::create_directories(opts.output);
    CommandLine commandLine(argc,argv);
    Resources resources;
    Gothic gothic;
    auto stuckLogger = StuckLogger::fromEnvironment();
    gothic.isNpcInDialogFn = [](const Npc*) { return false; };
    gothic.setupGlobalScripts();
    GameMusic music;
    zenkit::Logger::set(zenkit::LogLevel::WARNING,[](zenkit::LogLevel,const char*,const char* message) {
      Tempest::Log::e("[zenkit] ",message);
    });
    Runner runner(gothic,opts);
    if(!opts.stdio) {
      Json reset(rapidjson::kObjectType);
      runner.reset(reset);
      while(!runner.complete()) {
        runner.pace();
        runner.oneStep();
      }
      runner.saveSummary();
      send(protocol,runner.status());
      runner.close();
      return 0;
    }
    CommandInput input;
    std::optional<Advance> pending;
    for(;;) {
      if(pending && finished(*pending,runner)) {
        advanceReply(protocol,*pending,runner);
        pending.reset();
      }
      if(runner.realtime()) {
        if(Clock::now()>=runner.deadline())
          runner.oneStep();
      }
      // A due tick is serviced before every queued command. Partial input never
      // blocks this loop, and buffered complete frames do not depend on fd readiness.
      if(pending && finished(*pending,runner)) {
        advanceReply(protocol,*pending,runner);
        pending.reset();
      }
      int timeoutMs = -1;
      if(runner.realtime()) {
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(runner.deadline()-Clock::now()).count();
        timeoutMs = int(std::max<int64_t>(0,remaining));
      }
      auto frame = input.next(timeoutMs);
      if(frame.state==CommandInput::State::Waiting)
        continue;
      if(frame.state==CommandInput::State::End)
        break;
      Json request;
      request.Parse(frame.line.data(),frame.line.size());
      try {
        if(request.HasParseError() || !request.IsObject())
          throw std::runtime_error("expected a JSON command object");
        const auto cmd = optionalString(request,"cmd","");
        if(pending && cmd!="status" && cmd!="close" && cmd!="body_observations")
          throw std::runtime_error("advance is pending");
        if(cmd=="advance") {
          pending.emplace(advance(request,runner));
          if(!runner.realtime()) {
            while(!finished(*pending,runner))
              runner.oneStep();
            advanceReply(protocol,*pending,runner);
            pending.reset();
          }
          continue;
        }
        if(cmd=="reset") runner.reset(request);
        else if(cmd=="body_watch") runner.bodyWatch(request);
        else if(cmd=="body_observations") {
          const auto from = Clock::now();
          auto response = runner.observations(request);
          echoId(response,request);
          send(protocol,response);
          runner.observationTransportCost(uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now()-from).count()));
          continue;
        }
        else if(cmd!="status" && cmd!="close") throw std::runtime_error("unknown command");
        auto response = runner.status();
        echoId(response,request);
        send(protocol,response);
        if(cmd=="close") {
          runner.close();
          return 0;
        }
      } catch(const std::exception& e) {
        auto response = runner.status();
        response["ok"].SetBool(false);
        string(response,"error",e.what());
        if(request.IsObject()) echoId(response,request);
        send(protocol,response);
      }
    }
    runner.close();
    return 0;
  } catch(const std::exception& e) {
    Json response(rapidjson::kObjectType);
    response.AddMember("schema_version",1,response.GetAllocator());
    response.AddMember("ok",false,response.GetAllocator());
    string(response,"error",e.what());
    send(protocol,response);
    return 2;
  }
}
