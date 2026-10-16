#include "navtransitions.h"

#include "physics/navmesh.h"
#include "world/world.h"
#include "world/objects/npc.h"

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <stdexcept>

namespace {
using Tempest::Vec3;

constexpr uint64_t DefaultAttemptMs = 6000;
constexpr uint64_t PreparationLimitMs = 2500;
constexpr uint64_t GroundedPreparationMs = 400;
constexpr uint64_t GroundedLandingMs = 300;
constexpr uint64_t RecoveryLimitMs = 4000;
constexpr float LandingRadiusCm = 60.f;
constexpr float SupportToleranceCm = 20.f;
constexpr float TakeoffInsetM = .15f;
constexpr float MaxHorizontalM = 4.f;
constexpr float MaxVerticalM = 3.f;
constexpr size_t MaxCandidates = 8;

enum class Phase { Prepare, Traverse, Recover };
struct Attempt {
  NavMesh::Point start{}, end{};
  NavMesh::LinkType type = NavMesh::LinkType::Jump;
  size_t candidate = 0;
  bool reverse = false;
  };
struct Session {
  Npc* npc = nullptr;
  Npc* player = nullptr;
  NavMesh* mesh = nullptr;
  std::string from, to, output;
  std::vector<Attempt> attempts;
  size_t index = 0, candidates = 0, accepted = 0, completed = 0;
  uint64_t limit = DefaultAttemptMs, phaseBegan = 0, stableSince = 0;
  uint64_t actionBegan = 0;
  int hpBefore = 0, hpMin = 0;
  Vec3 placed{}, takeoff{};
  float maxRise = 0, maxDrop = 0;
  bool actionStarted = false, sawAir = false, sawJump = false, sawClimb = false;
  bool active = false;
  Phase phase = Phase::Prepare;
  } session;

NavMesh::Point meters(const Vec3& p) { return {p.x*.01f,p.y*.01f,p.z*.01f}; }
Vec3 centimeters(const NavMesh::Point& p) { return {p[0]*100,p[1]*100,p[2]*100}; }
void vectorJson(std::ostream& out,const Vec3& p) { out<<'['<<p.x<<','<<p.y<<','<<p.z<<']'; }
const char* typeName(NavMesh::LinkType type) {
  switch(type) {
    case NavMesh::LinkType::Jump: return "jump";
    case NavMesh::LinkType::Climb: return "climb";
    case NavMesh::LinkType::Drop: return "drop";
    }
  return "unknown";
  }
std::ofstream outputFile(const char* name,bool append=false) {
  std::ofstream out(std::filesystem::path(session.output)/name,append?std::ios::app:std::ios::out);
  if(!out) throw std::runtime_error(std::string("navmesh probe: cannot write ")+name);
  out<<std::setprecision(9)<<std::boolalpha;
  return out;
  }
bool supportedImpl(World& w,Npc& npc) {
  if(npc.isInAir() || npc.isFalling() || npc.isJump() || npc.isJumpUp() ||
     npc.isJumpAnim() || npc.bodyStateMasked()==BS_CLIMB || npc.isDown() || npc.isSwim())
    return false;
  const auto p=npc.position();
  const auto ground=w.physic()->landRay(p+Vec3(0,SupportToleranceCm,0),SupportToleranceCm*2);
  return ground.hasCol && std::abs(ground.v.y-p.y)<=SupportToleranceCm;
  }
bool stableFor(World& w,uint64_t duration) {
  if(!supportedImpl(w,*session.npc)) {session.stableSince=0;return false;}
  if(session.stableSince==0) session.stableSince=w.tickCount();
  return w.tickCount()-session.stableSince>=duration;
  }
void stopNpc() {
  if(!session.npc) return;
  session.npc->clearAiQueue();
  session.npc->clearGoTo();
  session.npc->stopWalking();
  session.npc->setWalkMode(WalkBit::WM_Walk);
  }
void finishSession(const std::string& reason) {
  stopNpc();
  auto out=outputFile("probe.json");
  out<<"{\"from\":"<<std::quoted(session.from)<<",\"to\":"<<std::quoted(session.to)
     <<",\"reason\":"<<std::quoted(reason)<<",\"candidates\":"<<session.candidates
     <<",\"attempts\":"<<session.completed<<",\"scheduled_attempts\":"<<session.attempts.size()
     <<",\"accepted_links\":"<<session.accepted<<",\"attempt_limit_ms\":"<<session.limit
     <<",\"max_horizontal_m\":"<<MaxHorizontalM<<",\"max_vertical_m\":"<<MaxVerticalM
     <<",\"takeoff_inset_m\":"<<TakeoffInsetM<<",\"landing_radius_cm\":"<<LandingRadiusCm
     <<",\"grounded_preparation_ms\":"<<GroundedPreparationMs
     <<",\"grounded_landing_ms\":"<<GroundedLandingMs<<"}\n";
  session.active=false;
  }
uint32_t linkId(const Attempt& a) {
  // Stable across separate per-edge runner processes; no implementation-defined hash.
  uint32_t value=2166136261u;
  for(unsigned char ch:session.from+"/"+session.to) {value^=ch;value*=16777619u;}
  value^=uint32_t(a.candidate*8+size_t(a.reverse)*4+size_t(a.type));
  value*=16777619u;
  return value==0?1:value;
  }
void record(World& w,const std::string& reason,bool accepted) {
  const auto& a=session.attempts[session.index];
  const auto& npc=*session.npc;
  const auto position=npc.position();
  const auto obstruction=w.physic()->navigationRay(position+Vec3(0,50,0),centimeters(a.end)+Vec3(0,50,0),&npc);
  auto out=outputFile("transitions.jsonl",true);
  out<<"{\"from\":"<<std::quoted(session.from)<<",\"to\":"<<std::quoted(session.to)
     <<",\"candidate\":"<<a.candidate<<",\"reverse\":"<<a.reverse
     <<",\"type\":"<<std::quoted(typeName(a.type))<<",\"accepted\":"<<accepted
     <<",\"reason\":"<<std::quoted(reason)<<",\"elapsed_ms\":"
     <<(session.phase==Phase::Traverse?w.tickCount()-session.actionBegan:0)
     <<",\"hp_before\":"<<session.hpBefore<<",\"hp_after\":"<<npc.attribute(ATR_HITPOINTS)
     <<",\"hp_min\":"<<session.hpMin<<",\"native_action_started\":"<<session.actionStarted
     <<",\"saw_air\":"<<session.sawAir<<",\"saw_jump\":"<<session.sawJump
     <<",\"saw_climb\":"<<session.sawClimb<<",\"max_rise_cm\":"<<session.maxRise
     <<",\"max_drop_cm\":"<<session.maxDrop<<",\"supported\":"<<supportedImpl(w,*session.npc)
     <<",\"obstruction_kind\":"<<int(obstruction.kind)<<",\"takeoff_cm\":";
  vectorJson(out,session.takeoff);out<<",\"expected_landing_cm\":";vectorJson(out,centimeters(a.end));
  out<<",\"final_position_cm\":";vectorJson(out,position);
  out<<",\"landing_distance_cm\":"<<(position-centimeters(a.end)).length()<<"}\n";
  ++session.completed;
  if(accepted) {
    auto links=outputFile("accepted-links.txt",true);
    const auto start=meters(session.takeoff);
    links<<start[0]<<' '<<start[1]<<' '<<start[2]<<' '
         <<a.end[0]<<' '<<a.end[1]<<' '<<a.end[2]<<' '<<int(a.type)<<' '<<linkId(a)<<'\n';
    ++session.accepted;
    }
  stopNpc();session.phase=Phase::Recover;session.phaseBegan=w.tickCount();session.stableSince=0;
  }
NavMesh::Point inset(const NavMesh::Point& origin,const NavMesh::Point& boundary,NavMesh& mesh) {
  const auto path=mesh.findPath(origin,boundary);
  if(!path.complete || path.corners.size()<2) return boundary;
  const auto previous=path.corners[path.corners.size()-2];
  const float dx=previous[0]-boundary[0],dz=previous[2]-boundary[2];
  const float length=std::sqrt(dx*dx+dz*dz);
  if(length<.01f) return boundary;
  const float amount=std::min(TakeoffInsetM,length*.5f);
  auto result=boundary;result[0]+=dx/length*amount;result[2]+=dz/length*amount;
  const auto verify=mesh.findPath(result,boundary);
  return verify.complete && verify.startSnapDistance<.1f?result:boundary;
  }
void prepare(World& w) {
  const auto& a=session.attempts[session.index];
  auto& npc=*session.npc;
  stopNpc();npc.setInteraction(nullptr,true);npc.clearState(false);npc.clearSpeed();
  npc.setExternalControl(true);npc.setProcessPolicy(NpcProcessPolicy::AiNormal);
  npc.setPosition(centimeters(a.start));npc.updateTransform();
  npc.setDirection(centimeters(a.end)-npc.position());
  npc.setAnim(Npc::Anim::Idle);
  session.player->setPosition(npc.position()+Vec3(0,0,500));session.player->updateTransform();
  session.phase=Phase::Prepare;session.phaseBegan=w.tickCount();session.stableSince=0;
  session.placed=npc.position();session.takeoff=npc.position();
  session.hpBefore=npc.attribute(ATR_HITPOINTS);session.hpMin=session.hpBefore;
  session.maxRise=0;session.maxDrop=0;session.actionStarted=false;
  session.sawAir=false;session.sawJump=false;session.sawClimb=false;
  }
void startAction(World& w) {
  const auto& a=session.attempts[session.index];
  auto& npc=*session.npc;
  session.takeoff=npc.position();session.hpBefore=npc.attribute(ATR_HITPOINTS);session.hpMin=session.hpBefore;
  session.phase=Phase::Traverse;session.actionBegan=w.tickCount();session.stableSince=0;
  npc.setDirection(centimeters(a.end)-npc.position());npc.setWalkMode(WalkBit::WM_Run);
  if(a.type==NavMesh::LinkType::Drop) {
    session.actionStarted=npc.goToPosition(centimeters(a.end));
    return;
    }
  const auto jump=npc.tryJump();
  if(a.type==NavMesh::LinkType::Jump) {
    if(jump.anim!=Npc::Anim::Jump) {record(w,"native_requests_climb",false);return;}
    session.actionStarted=npc.setAnim(Npc::Anim::Jump);
    }
  else {
    if(jump.anim==Npc::Anim::Jump || jump.anim==Npc::Anim::Idle) {
      record(w,"native_has_no_climb",false);return;
      }
    session.actionStarted=npc.startClimb(jump);
    }
  if(!session.actionStarted) record(w,"native_action_rejected",false);
  }
}

void NavTransitions::reset() { session=Session{}; }
bool NavTransitions::running() { return session.active; }
bool NavTransitions::supported(World& world,Npc& npc) { return supportedImpl(world,npc); }

bool NavTransitions::command(const std::vector<std::string>& args,World& world,Npc& player,
                             NavMesh& mesh,const std::string& output) {
  if(args.size()<2 || args[1]!="probe") return false;
  if(args.size()!=5 && args.size()!=6)
    throw std::runtime_error("navmesh probe NPC FROM TO [attempt_ms]");
  if(session.active) throw std::runtime_error("navmesh probe already running");
  auto* npc=world.findNpcByInstance(world.script().findSymbolIndex(args[2]));
  const auto* from=world.findPoint(args[3],false);
  const auto* to=world.findPoint(args[4],false);
  if(!npc || !from || !to) throw std::runtime_error("navmesh probe: missing actor or waypoint");
  session=Session{};session.npc=npc;session.player=&player;session.mesh=&mesh;
  session.from=args[3];session.to=args[4];session.output=output;
  session.limit=args.size()==6?std::stoull(args[5]):DefaultAttemptMs;
  if(session.limit==0) throw std::runtime_error("navmesh probe: attempt_ms must be positive");
  outputFile("transitions.jsonl");outputFile("accepted-links.txt");
  const auto candidates=mesh.gapCandidates(meters(from->groundPos),meters(to->groundPos),
                                         MaxHorizontalM,MaxVerticalM,MaxCandidates);
  session.candidates=candidates.size();
  for(size_t i=0;i<candidates.size();++i) {
    const auto& c=candidates[i];
    for(bool reverse:{false,true}) {
      const auto start=inset(meters(reverse?to->groundPos:from->groundPos),reverse?c.end:c.start,mesh);
      const auto end=reverse?c.start:c.end;
      // A drop is meaningful only downwards; jump and native climb are independent attempts.
      if(end[1]<start[1]-.2f) session.attempts.push_back({start,end,NavMesh::LinkType::Drop,i,reverse});
      session.attempts.push_back({start,end,NavMesh::LinkType::Jump,i,reverse});
      if(end[1]>start[1]+.15f) session.attempts.push_back({start,end,NavMesh::LinkType::Climb,i,reverse});
      }
    }
  session.active=true;
  if(session.attempts.empty()) finishSession("no_nearby_component_boundaries"); else prepare(world);
  return true;
  }

void NavTransitions::tick(World& world) {
  if(!session.active || !session.npc) return;
  auto& npc=*session.npc;
  session.hpMin=std::min(session.hpMin,npc.attribute(ATR_HITPOINTS));
  if(session.phase==Phase::Recover) {
    if(npc.isDown()) {finishSession("actor_down_after_rejected_attempt");return;}
    if(stableFor(world,GroundedLandingMs)) {
      ++session.index;
      if(session.index==session.attempts.size()) finishSession("complete"); else prepare(world);
      }
    else if(world.tickCount()-session.phaseBegan>=RecoveryLimitMs) finishSession("previous_attempt_did_not_settle");
    return;
    }
  if(session.phase==Phase::Prepare) {
    if(session.hpMin<session.hpBefore) {record(world,"damage_during_preparation",false);return;}
    if((npc.position()-session.placed).length()>SupportToleranceCm) {
      record(world,"takeoff_not_supported",false);return;
      }
    if(stableFor(world,GroundedPreparationMs)) startAction(world);
    else if(world.tickCount()-session.phaseBegan>=PreparationLimitMs) record(world,"takeoff_did_not_settle",false);
    return;
    }
  const auto& a=session.attempts[session.index];
  const auto p=npc.position();
  session.maxRise=std::max(session.maxRise,p.y-session.takeoff.y);
  session.maxDrop=std::max(session.maxDrop,session.takeoff.y-p.y);
  session.sawAir|=npc.isInAir() || npc.isFalling();
  session.sawJump|=npc.isJump() || npc.isJumpAnim();
  session.sawClimb|=npc.bodyStateMasked()==BS_CLIMB;
  if(session.hpMin<session.hpBefore || npc.isDown()) {record(world,"damage",false);return;}
  const bool witnessed=a.type==NavMesh::LinkType::Drop ? session.sawAir && session.maxDrop>20.f :
                       a.type==NavMesh::LinkType::Climb ? session.sawClimb && session.maxRise>15.f :
                       session.sawJump && (session.sawAir || session.maxRise>10.f);
  const bool close=(p-centimeters(a.end)).length()<=LandingRadiusCm;
  if(close && witnessed && supportedImpl(world,npc)) {
    // Proximity across a narrow crack is not proof of landing on the other component.
    const auto landing=session.mesh->findPath(meters(p),a.end);
    if(landing.complete && landing.length<=1.5f && landing.startSnapDistance<=.25f) {
      npc.clearGoTo();npc.stopWalking();
      if(stableFor(world,GroundedLandingMs)) {record(world,"landed_without_damage",true);return;}
      if(world.tickCount()-session.actionBegan>=session.limit) {record(world,"landing_did_not_settle",false);return;}
      return;
      }
    else session.stableSince=0;
    }
  else session.stableSince=0;
  if(world.tickCount()-session.actionBegan>=session.limit) {
    record(world,witnessed?"landing_not_reached":"native_transition_not_witnessed",false);return;
    }
  // Do not replace a native jump/climb animation with walking. Resume only on real support.
  if(supportedImpl(world,npc) && npc.moveHint()==Npc::GT_No) npc.goToPosition(centimeters(a.end));
  }
