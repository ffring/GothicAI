#include "npcobserver.h"

#include <algorithm>
#include <cctype>
#include <cmath>

#include "game/gamescript.h"
#include "world/aiqueue.h"
#include "world/objects/npc.h"
#include "world/world.h"

#include "jsonread.h"
#include "jsonwriter.h"
#include "npcfind.h"
#include "textcodec.h"

namespace {

constexpr uint64_t RosterPeriodMs = 500;
constexpr float    RosterRadius   = 6000;  // cm: the engine simplifies AI beyond this
constexpr float    EventRadius    = 3000;  // cm: the engine perceives within this
constexpr float    ViewRadius     = 3000;
constexpr float    ViewHalfAngle  = 60;    // degrees around where the player faces
constexpr float    NearEnter      = 500;   // cm: "player_near" when the player comes this close
constexpr float    NearLeave      = 700;   // and forgets it once they are this far again
constexpr uint64_t EventPeriodMs  = 1000;  // per npc, kind and who did it
constexpr uint64_t MonsterNearPeriodMs = 10000;
constexpr uint64_t NearPeriodMs   = 30000;
constexpr uint64_t ActMaxMs       = 30000; // npc_idle at the latest after this
constexpr uint64_t ActGraceMs     = 200;   // let the npc take the action from its queue
constexpr uint64_t StateMaxMs     = 120000; // fight/flee: npc_idle at the latest after this
constexpr uint64_t StateStartMs   = 3000;

std::string upper(std::string_view s) {
  std::string r(s);
  for(auto& c:r)
    c = char(std::toupper(static_cast<unsigned char>(c)));
  return r;
  }

std::string npcId(World& world, Npc& npc) {
  if(npc.isPlayer())
    return "PLAYER";
  if(auto sym = world.script().findSymbol(npc.instanceSymbol()))
    return sym->name();
  return {};
  }

Npc* npcBySymbol(World& world, std::string_view symbol) {
  if(symbol=="PLAYER")
    return world.player();
  const size_t id = world.script().findSymbolIndex(upper(symbol));
  return id==size_t(-1) ? nullptr : world.findNpcByInstance(id);
  }

bool inView(Npc& player, Npc& npc, float dist) {
  if(dist>ViewRadius)
    return false;
  const auto  d   = npc.position()-player.position();
  const float len = std::sqrt(d.x*d.x+d.z*d.z);
  if(len<1)
    return true;
  const float rot = player.rotationRad();
  const float cos = (d.x*std::cos(rot)+d.z*std::sin(rot))/len;
  return cos>=std::cos(ViewHalfAngle*float(M_PI)/180.f);
  }

std::string_view activity(Npc& npc, std::string_view owner) {
  if(!owner.empty())
    return owner;
  if(npc.isDead() || npc.isDown())
    return "down";
  if(npc.isTalk())
    return "dialog";  // an original dialog with the player
  if(npc.weaponState()!=WeaponState::NoWeapon)
    return "fight";
  return "routine";
  }

// what a perception of `self` is about: the player's doings, or a threat from someone else
// (a monster, an armed npc). `about` is the victim for attacks and murders.
struct Seen final {
  std::string_view kind;
  Npc*             about    = nullptr;
  uint64_t         periodMs = EventPeriodMs;
  };

Seen classify(Npc& self, Npc& other, Npc* victim, PercType perc) {
  if(other.isPlayer()) {
    switch(perc) {
      case PERC_DRAWWEAPON:      return Seen{"weapon_drawn"};
      case PERC_ASSESSTHREAT:    return Seen{"threatened"};
      case PERC_ASSESSTHEFT:     return Seen{"theft"};
      case PERC_ASSESSENTERROOM: return Seen{"enter_room"};
      case PERC_ASSESSGIVENITEM: return Seen{"item_given"};
      case PERC_ASSESSMAGIC:
      case PERC_ASSESSCASTER:    return Seen{"magic"};
      case PERC_ASSESSDAMAGE:    return Seen{"attack",&self};
      case PERC_ASSESSOTHERSDAMAGE:
        return victim!=nullptr ? Seen{"attack",victim} : Seen{};
      case PERC_ASSESSMURDER:
        return victim!=nullptr ? Seen{"murder",victim} : Seen{};
      default:
        return Seen{};
      }
    }
  if(other.isDead() || other.isDown())
    return Seen{};
  switch(perc) {
    case PERC_ASSESSENEMY:
      // an enemy the npc senses; repeats every perception tick, so reported seldom
      return other.isMonster() ? Seen{"monster_near",nullptr,MonsterNearPeriodMs} : Seen{};
    case PERC_ASSESSDAMAGE:
      return Seen{other.isMonster() ? "monster_attack" : "npc_attack",&self};
    case PERC_ASSESSOTHERSDAMAGE:
      if(victim==nullptr)
        return Seen{};
      if(victim->isPlayer())
        return Seen{"hero_attacked",victim};
      return other.isMonster() ? Seen{"monster_attack",victim} : Seen{};
    default:
      return Seen{};
    }
  }

}

NpcObserver::NpcObserver(Emit emit, Owner owner):emit(std::move(emit)), owner(std::move(owner)) {
  }

void NpcObserver::clear() {
  close.clear();
  lastEvent.clear();
  running.clear();
  nextRoster = 0;
  }

void NpcObserver::tick(World& world) {
  const uint64_t now = world.tickCount();
  checkRunning(world,now);
  if(now<nextRoster)
    return;
  nextRoster = now+RosterPeriodMs;
  emitRoster(world);
  }

void NpcObserver::emitRoster(World& world) {
  Npc* pl = world.player();
  if(pl==nullptr)
    return;
  const auto t = world.time();
  JsonWriter j("npc_roster");
  j.integer("t",int64_t(world.tickCount()))
   .integer("hour",t.hour())
   .integer("minute",t.minute())
   .boolean("full",true)
   .array("npcs");
  struct Near { std::string symbol; float dist; };
  std::vector<Near> cameClose;  // reported after the roster, so the server already knows these npc's
  world.detectNpc(pl->position(),RosterRadius,[&](Npc& npc) {
    if(npc.isPlayer() || npc.guild()>=GIL_SEPERATOR_HUM)
      return;  // people only: monsters keep their own AI
    const auto symbol = npcId(world,npc);
    if(symbol.empty())
      return;
    const float dist = std::sqrt(npc.qDistTo(*pl));
    if(trackProximity(world,npc,symbol,dist))
      cameClose.push_back({symbol,dist});
    j.object()
       .str("id",symbol)
       .str("name",TextCodec::toUtf8(npc.displayName()))
       .integer("guild",int64_t(npc.guild()))
       .num("distance",double(dist))
       .boolean("in_view",inView(*pl,npc,dist))
       .str("activity",activity(npc,owner(symbol)))
     .end();
    });
  j.endArray();
  emit(j.finish());
  for(auto& n:cameClose)
    emitEvent(n.symbol,"player_near","PLAYER",double(n.dist),"","");
  }

bool NpcObserver::trackProximity(World& world, Npc& npc, std::string_view symbol, float dist) {
  auto& near = close[std::string(symbol)];
  if(near && dist>NearLeave)
    near = false;
  if(near || dist>NearEnter)
    return false;
  near = true;
  if(!owner(symbol).empty() || npc.isDead() || npc.isDown())
    return false;
  return !throttled(symbol,"player_near",world.tickCount(),NearPeriodMs);
  }

void NpcObserver::perception(World& world, Npc& self, Npc& other, Npc* victim, PercType perc) {
  if(self.isPlayer() || self.isDead() || self.isDown() || self.guild()>=GIL_SEPERATOR_HUM || &other==&self)
    return;
  const Seen seen = classify(self,other,victim,perc);
  if(seen.kind.empty())
    return;
  Npc* pl = world.player();
  if(pl==nullptr)
    return;
  if(std::sqrt(self.qDistTo(*pl))>EventRadius)
    return;
  const auto symbol = npcId(world,self);
  if(symbol.empty() || !owner(symbol).empty())
    return;
  const auto otherId = npcId(world,other);
  if(otherId.empty() || throttled(symbol,std::string(seen.kind)+"/"+otherId,world.tickCount(),seen.periodMs))
    return;
  std::string detail;
  if(seen.about==&self)
    detail = "you"; else
  if(seen.about!=nullptr)
    detail = seen.about->isPlayer() ? "PLAYER" : TextCodec::toUtf8(seen.about->displayName());
  const double dist = std::sqrt(double(self.qDistTo(other)));
  emitEvent(symbol,seen.kind,otherId,dist,detail,other.isPlayer() ? "" : TextCodec::toUtf8(other.displayName()));
  }

void NpcObserver::act(World& world, const rapidjson::Value& msg) {
  const auto id     = JsonRead::str(msg,"id");
  const auto symbol = upper(JsonRead::str(msg,"npc"));
  const auto action = JsonRead::str(msg,"action");
  Npc*       npc    = npcBySymbol(world,symbol);
  Npc*       pl     = world.player();
  if(npc==nullptr || npc->isPlayer() || pl==nullptr) {
    reply(id,symbol,"rejected","npc not found");
    return;
    }
  if(!owner(symbol).empty() || npc->isTalk()) {
    reply(id,symbol,"rejected","npc is busy (attached or in a dialog)");
    return;
    }
  if(npc->isDead() || npc->isDown()) {
    reply(id,symbol,"rejected","npc is down");
    return;
    }

  static const rapidjson::Value empty(rapidjson::kObjectType);
  const auto  argsIt = msg.FindMember("args");
  const auto& args   = (argsIt!=msg.MemberEnd() && argsIt->value.IsObject()) ? argsIt->value : empty;
  Npc*        target = pl;
  if(auto t = JsonRead::str(args,"npc"); !t.empty()) {
    target = AiNpc::findNpc(world,t,npc);
    if(target==nullptr || target==npc) {
      reply(id,symbol,"rejected","target not found");
      return;
      }
    }
  ScriptFn state;

  if(action=="say_svm") {
    std::string svm = upper(JsonRead::str(args,"svm"));
    if(svm.empty()) {
      reply(id,symbol,"rejected","say_svm needs svm");
      return;
      }
    if(svm[0]!='$')
      svm.insert(svm.begin(),'$');
    npc->aiPush(AiQueue::aiTurnToNpc(target));
    npc->aiPush(AiQueue::aiOutputSvm(*target,svm,0));
    }
  else if(action=="turn_to") {
    npc->aiPush(AiQueue::aiTurnToNpc(target));
    }
  else if(action=="look_at") {
    const auto ms = JsonRead::integer(args,"ms",3000);
    npc->aiPush(AiQueue::aiLookAtNpc(target));
    npc->aiPush(AiQueue::aiWait(uint64_t(std::clamp<int64_t>(ms,500,10000))));
    npc->aiPush(AiQueue::aiStopLookAt());
    }
  else if(action=="play_ani") {
    const auto name = JsonRead::str(args,"name");
    if(name.empty()) {
      reply(id,symbol,"rejected","play_ani needs name");
      return;
      }
    npc->aiPush(AiQueue::aiTurnToNpc(target));
    npc->aiPush(AiQueue::aiPlayAnim(name));
    }
  else if(action=="go_to_npc") {
    npc->aiPush(AiQueue::aiGoToNpc(target));
    npc->aiPush(AiQueue::aiTurnToNpc(target));
    }
  else if(action=="turn_away") {
    npc->aiPush(AiQueue::aiTurnAway(target));
    }
  else if(action=="fight" || action=="flee") {
    // the game's own behaviour, the way B_Attack / B_Flee start it; the routine resumes afterwards
    state = AiNpc::scriptState(world,action=="fight" ? "ZS_ATTACK" : "ZS_FLEE");
    if(!state.isValid() || target->isDead()) {
      reply(id,symbol,"rejected",state.isValid() ? "target is dead" : "the scripts have no such state");
      return;
      }
    npc->setTarget(target);
    npc->aiPush(AiQueue::aiStartState(state,0,target,nullptr,""));
    }
  else if(action=="draw_weapon") {
    const auto kind = JsonRead::str(args,"kind");
    npc->aiPush(kind=="ranged" ? AiQueue::aiReadyRangedWeapon()
                               : (kind=="melee" ? AiQueue::aiReadyMeleeWeapon() : AiQueue::aiDrawWeapon()));
    }
  else if(action=="remove_weapon") {
    npc->aiPush(AiQueue::aiRemoveWeapon());
    }
  else {
    reply(id,symbol,"rejected","unknown action: " + std::string(action));
    return;
    }

  // a newer action for the same npc replaces the tracking of the older one
  running.erase(std::remove_if(running.begin(),running.end(),[&](const Running& r){ return r.symbol==symbol; }),
                running.end());
  running.push_back({std::string(id),symbol,world.script().findSymbolIndex(symbol),world.tickCount(),
                     state,false,state.isValid() ? StateMaxMs : ActMaxMs});
  reply(id,symbol,"queued","");
  }

void NpcObserver::checkRunning(World& world, uint64_t now) {
  for(size_t i=0; i<running.size();) {
    auto& r   = running[i];
    Npc*  npc = world.findNpcByInstance(r.instance);
    const bool gone = npc==nullptr || npc->isDead() || npc->isDown() || !owner(r.symbol).empty();
    bool       done = false;
    if(!gone && r.state.isValid()) {
      // a script state: done once the npc has entered it and left it again
      const bool in = npc->isInState(r.state);
      r.entered = r.entered || in;
      done      = (r.entered && !in) || (!r.entered && now>r.start+StateStartMs);
      } else {
      done = !gone && now>r.start+ActGraceMs && npc->isAiQueueEmpty();
      }
    const bool late = now>r.start+r.maxMs;
    if(!gone && !done && !late) {
      ++i;
      continue;
      }
    emit(JsonWriter("npc_idle")
           .str("npc",r.symbol)
           .str("act",r.id)
           .str("reason",gone ? "gone" : (done ? "done" : "timeout"))
           .finish());
    running.erase(running.begin()+ptrdiff_t(i));
    }
  }

void NpcObserver::emitEvent(std::string_view npc, std::string_view kind, std::string_view other,
                            double distance, std::string_view detail, std::string_view otherName) {
  JsonWriter j("npc_event");
  j.str("npc",npc).str("kind",kind).str("other",other).num("distance",distance);
  if(!otherName.empty())
    j.str("other_name",otherName);
  if(!detail.empty())
    j.str("detail",detail);
  emit(j.finish());
  }

bool NpcObserver::throttled(std::string_view npc, std::string_view kind, uint64_t now, uint64_t periodMs) {
  std::string key(npc);
  key += '/';
  key += kind;
  auto it = lastEvent.find(key);
  if(it!=lastEvent.end() && now<it->second+periodMs)
    return true;
  lastEvent[key] = now;
  return false;
  }

void NpcObserver::reply(std::string_view id, std::string_view npc, std::string_view status, std::string_view detail) {
  JsonWriter j("npc_act_result");
  j.str("id",id).str("npc",npc).str("status",status);
  if(!detail.empty())
    j.str("detail",detail);
  emit(j.finish());
  }
