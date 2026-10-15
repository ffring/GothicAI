#include "npcobserver.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <queue>
#include <unordered_map>
#include <utility>

#include "game/gamescript.h"
#include "physics/dynamicworld.h"
#include "world/aiqueue.h"
#include "world/waypoint.h"
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
constexpr float    HeardRadius    = 6000;  // cm: the player's voice is followed round rock and walls to those this near
constexpr float    AroundMax      = 10000; // cm: and this far round, at most
constexpr float    NotHeard       = 2*AroundMax; // "heard" of one his voice does not reach
constexpr float    WalledSide     = 40;    // cm: the lines either side of the one between the heads
constexpr float    WaySight       = 2000;  // cm: a voice goes round by the waynet in sight this near
constexpr float    WayEye         = 100;   // cm: above a point of the waynet, where it is looked at
constexpr float    NearEnter      = 500;   // cm: "player_near" when the player comes this close
constexpr float    NearLeave      = 700;   // and forgets it once they are this far again
constexpr uint64_t EventPeriodMs  = 1000;  // per npc, kind and who did it
constexpr uint64_t MonsterNearPeriodMs = 10000;
constexpr uint64_t NearPeriodMs   = 30000;
constexpr uint64_t ActMaxMs       = 30000; // npc_idle at the latest after this
constexpr uint64_t ActGraceMs     = 200;   // let the npc take the action from its queue
constexpr uint64_t StateMaxMs     = 120000; // fight/flee: npc_idle at the latest after this
constexpr uint64_t StateStartMs   = 3000;
constexpr uint64_t ActivityMaxMs  = 10*60*1000;
constexpr uint64_t HoldMs         = 600;    // a held threat goes to the scripts if the server has not decided
constexpr uint64_t VerdictMs      = 5000;   // a verdict covers repeats of the same pair this long

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

// rock or a wall between two heads: no straight line from one to the other, nor a hand's breadth to either
// side of it (a post or a tree trunk in between is no wall)
bool walled(const DynamicWorld& physic, const Tempest::Vec3& a, const Tempest::Vec3& b) {
  const float dx = b.x-a.x, dz = b.z-a.z;
  const float len = std::sqrt(dx*dx+dz*dz);
  if(len<1)
    return false;
  const Tempest::Vec3 side = Tempest::Vec3(-dz/len,0,dx/len)*WalledSide;
  return physic.ray(a,b).hasCol && physic.ray(a+side,b+side).hasCol && physic.ray(a-side,b-side).hasCol;
  }

// the nearest point of the waynet in sight of `eye`, not farther than WaySight
const WayPoint* wayInSight(World& world, const Tempest::Vec3& eye) {
  const DynamicWorld& physic = *world.physic();
  return world.findWayPoint(eye,[&](const WayPoint& w) {
    if(!w.isConnected() || (w.position()-eye).quadLength()>WaySight*WaySight)
      return false;
    return !physic.ray(eye,w.position()+Tempest::Vec3(0,WayEye,0)).hasCol;
    });
  }

// how far a voice goes from `a` to `b` round rock or a wall between them: to the waynet in sight, along it,
// and from it (it goes nowhere through the rock); farther than `limit`: NotHeard
float around(World& world, const WayPoint* from, const Tempest::Vec3& a, const Tempest::Vec3& b, float limit) {
  const WayPoint* to = wayInSight(world,b);
  if(from==nullptr || to==nullptr)
    return NotHeard;
  const float start = (from->position()-a).length(), end = (to->position()-b).length();
  using Step = std::pair<float,const WayPoint*>;
  std::priority_queue<Step,std::vector<Step>,std::greater<Step>> open;
  std::unordered_map<const WayPoint*,float> best;
  open.push({start,from});
  best[from] = start;
  while(!open.empty()) {
    auto [len,w] = open.top();
    open.pop();
    if(len>best[w])
      continue;
    if(len+end>limit)
      break;
    if(w==to)
      return len+end;
    for(auto& c:w->connections()) {
      const float next = len+float(c.len);
      auto it = best.find(c.point);
      if(it!=best.end() && it->second<=next)
        continue;
      best[c.point] = next;
      open.push({next,c.point});
      }
    }
  return NotHeard;
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

Seen classify(World& world, Npc& self, Npc& other, Npc* victim, PercType perc) {
  if(victim!=nullptr && victim->isMonster() && world.script().guildAttitude(*victim,self)==ATT_HOSTILE) {
    // a dangerous monster killed by a person (the hero, someone else, the npc itself): the danger is over.
    // Hits on it are no news (the hero fighting a wolf is not an attack on someone), nor is a kill by another
    // monster. Animals the npc does not fear (a sheep) are not dangers: their attackers are
    return perc==PERC_ASSESSMURDER && !other.isMonster() ? Seen{"monster_killed",victim} : Seen{};
    }
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
    case PERC_ASSESSWARN:
      // an enemy the npc senses, or a monster growling at it; repeats every perception tick: reported seldom
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
  held.clear();
  verdicts.clear();
  nextRoster = 0;
  }

void NpcObserver::tick(World& world) {
  const uint64_t now = world.tickCount();
  checkRunning(world,now);
  for(size_t i=0; i<held.size();) {
    if(now<held[i].at+HoldMs) {
      ++i;
      continue;
      }
    releaseHeld(world,held[i]);  // no verdict in time: the scripts react as they would have
    held.erase(held.begin()+ptrdiff_t(i));
    }
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
  const Tempest::Vec3 head = pl->mapHeadBone();
  const WayPoint*     way  = nullptr;  // the player's, once someone is behind a wall
  bool                wayFound = false;
  // how far the player's voice goes to the npc (v30): straight on, or round what is between them
  auto heard = [&](Npc& npc, float dist) {
    if(dist>HeardRadius)
      return NotHeard;
    const Tempest::Vec3 at = npc.mapHeadBone();
    if(!walled(*world.physic(),head,at))
      return dist;
    if(!wayFound) {
      way      = wayInSight(world,head);
      wayFound = true;
      }
    return around(world,way,head,at,AroundMax);
    };
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
       .num("heard",double(heard(npc,dist)))
       .str("activity",activity(npc,owner(symbol)))
       .str("state",AiNpc::stateName(world,npc.currentState()))
       .boolean("moving",AiNpc::isMoving(npc))
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
  if(self.isPlayer() || self.isDead() || self.isDown() || self.guild()>=GIL_SEPERATOR_HUM)
    return;
  const Seen seen = classify(world,self,other,victim,perc);
  if(seen.kind.empty() || (&other==&self && seen.kind!="monster_killed"))
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
  // monster_killed: `victim` is the monster's symbol, the threats from it are over
  const auto victimId = seen.kind=="monster_killed" ? npcId(world,*seen.about) : std::string();
  emitEvent(symbol,seen.kind,otherId,dist,detail,other.isPlayer() ? "" : TextCodec::toUtf8(other.displayName()),
            victimId);
  }

bool NpcObserver::intercept(World& world, Npc& self, Npc& other, Npc* victim, PercType perc) {
  if(!holdThreats || other.isPlayer() || &other==&self)
    return false;  // the hero's doings stay with the scripts (crimes, guards)
  if(perc!=PERC_ASSESSENEMY && perc!=PERC_ASSESSWARN && perc!=PERC_ASSESSDAMAGE && perc!=PERC_ASSESSOTHERSDAMAGE)
    return false;
  if(self.isPlayer() || self.isDead() || self.isDown() || self.guild()>=GIL_SEPERATOR_HUM || self.isTalk() ||
     self.isAttack())
    return false;  // already fighting by its scripts: they go on
  if(classify(world,self,other,victim,perc).kind.empty())
    return false;
  Npc* pl = world.player();
  if(pl==nullptr || std::sqrt(self.qDistTo(*pl))>EventRadius)
    return false;
  const auto symbol  = npcId(world,self);
  const auto otherId = npcId(world,other);
  if(symbol.empty() || otherId.empty() || !owner(symbol).empty())
    return false;

  const uint64_t now = world.tickCount();
  const auto     key = symbol + "/" + otherId;
  if(auto v = verdicts.find(key); v!=verdicts.end() && now<v->second.until)
    return v->second.server;  // decided a moment ago: the server acts, or the scripts take it
  verdicts[key] = Verdict{true,now+HoldMs};  // repeats while it is being decided are held too

  Held h;
  h.id     = "h" + std::to_string(nextHold++);
  h.key    = key;
  h.self   = &self;
  h.other  = &other;
  h.victim = victim;
  h.perc   = perc;
  h.at     = now;
  const Seen seen = classify(world,self,other,victim,perc);
  JsonWriter j("perception_held");
  j.str("hold",h.id).str("npc",symbol).str("kind",seen.kind).str("other",otherId)
   .str("other_name",TextCodec::toUtf8(other.displayName()))
   .num("distance",std::sqrt(double(self.qDistTo(other))));
  if(seen.about==&self)
    j.str("detail","you"); else
  if(seen.about!=nullptr)
    j.str("detail",seen.about->isPlayer() ? "PLAYER" : TextCodec::toUtf8(seen.about->displayName()));
  emit(j.finish());
  held.push_back(std::move(h));
  return true;
  }

void NpcObserver::verdict(World& world, const rapidjson::Value& msg) {
  const auto id = JsonRead::str(msg,"hold");
  auto it = std::find_if(held.begin(),held.end(),[&](const Held& h){ return h.id==id; });
  if(it==held.end())
    return;  // released already (too late)
  const bool server = JsonRead::str(msg,"to")=="server";
  verdicts[it->key] = Verdict{server,world.tickCount()+VerdictMs};
  if(!server)
    releaseHeld(world,*it);
  held.erase(it);
  }

void NpcObserver::releaseAll(World& world) {
  for(auto& h:held)
    releaseHeld(world,h);
  held.clear();
  verdicts.clear();
  }

void NpcObserver::releaseHeld(World& world, Held& h) {
  // the npc's pointers are only trusted if they still belong to the world
  Npc *self = nullptr, *other = nullptr, *victim = nullptr;
  if(Npc* pl = world.player())
    world.detectNpc(pl->position(),RosterRadius,[&](Npc& n) {
      if(&n==h.self)   self   = &n;
      if(&n==h.other)  other  = &n;
      if(&n==h.victim) victim = &n;
      });
  if(self==nullptr || other==nullptr || self->isDead() || self->isDown() || other->isDead())
    return;
  if(auto v = verdicts.find(h.key); v!=verdicts.end())
    v->second = Verdict{false,world.tickCount()+VerdictMs};
  self->passPerception(*other,h.victim!=nullptr ? victim : nullptr,h.perc);
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
  Running  run;  // what is tracked until npc_idle

  if(action=="say_svm") {
    std::string svm = upper(JsonRead::str(args,"svm"));
    if(svm.empty()) {
      reply(id,symbol,"rejected","say_svm needs svm");
      return;
      }
    if(svm[0]!='$')
      svm.insert(svm.begin(),'$');
    if(JsonRead::boolean(args,"overlay",false)) {
      // shouted on the move (AI_OutputSVM_Overlay): the body goes on with what it does
      npc->aiPush(AiQueue::aiOutputSvmOverlay(*target,svm,0));
      } else {
      npc->aiPush(AiQueue::aiTurnToNpc(target));
      npc->aiPush(AiQueue::aiOutputSvm(*target,svm,0));
      }
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
  else if(action=="start_activity") {
    // what a routine entry does (TA_Sit_Bench: ZS_SIT_BENCH at a waypoint), now, for `minutes` of game
    // time; afterwards the routine goes on by itself
    const auto name = upper(JsonRead::str(args,"state"));
    const auto wp   = world.findPoint(JsonRead::str(args,"waypoint"),false);
    state = name.starts_with("ZS_") ? AiNpc::scriptState(world,name) : ScriptFn();
    if(!state.isValid() || wp==nullptr) {
      reply(id,symbol,"rejected",!state.isValid() ? "unknown state: " + name : "unknown waypoint");
      return;
      }
    auto until = world.time();
    until.addMilis(uint64_t(std::clamp<int64_t>(JsonRead::integer(args,"minutes",60),1,24*60))*60*1000);
    npc->startState(state,wp->name,until,false);
    // `arrive` (v15): npc_idle "arrived" once it is there doing it, not only when it leaves it
    run.arrival = JsonRead::boolean(args,"arrive",false);
    run.at      = wp->position();
    run.radius  = float(std::clamp<int64_t>(JsonRead::integer(args,"radius",500),50,5000));
    }
  else if(action=="set_routine") {
    // Npc_ExchangeRoutine: another set of routine entries (Rtn_<name>_<id>), effective at once
    const auto   name = upper(JsonRead::str(args,"name"));
    const size_t fn   = world.script().findSymbolIndex("RTN_" + name + "_" + std::to_string(npc->handle().id));
    if(name.empty() || fn==size_t(-1)) {
      reply(id,symbol,"rejected","unknown routine: " + name);
      return;
      }
    npc->excRoutine(fn);
    npc->resumeAiRoutine();
    }
  else if(action=="give") {
    // to another npc (v15), as give_item does for an attached one: walks up if needed, turns, the item
    // changes hands once its queue is done (checkRunning). The hero gets things only through deals
    if(target==pl) {
      reply(id,symbol,"rejected","give: to an npc only");
      return;
      }
    const auto   name = upper(JsonRead::str(args,"item"));
    const size_t cls  = world.script().findSymbolIndex(name);
    const size_t have = cls==size_t(-1) ? 0 : npc->inventory().itemCount(cls);
    if(have==0) {
      reply(id,symbol,"rejected","npc has no item " + name);
      return;
      }
    run.item   = cls;
    run.count  = std::min(have,size_t(std::max<int64_t>(1,JsonRead::integer(args,"count",1))));
    run.giveTo = target->instanceSymbol();
    if(npc->qDistTo(*target)>AiNpc::GiveRange*AiNpc::GiveRange)
      npc->aiPush(AiQueue::aiGoToNpc(target));
    npc->aiPush(AiQueue::aiTurnToNpc(target));
    }
  else {
    reply(id,symbol,"rejected","unknown action: " + std::string(action));
    return;
    }

  // a newer action for the same npc replaces the tracking of the older one
  running.erase(std::remove_if(running.begin(),running.end(),[&](const Running& r){ return r.symbol==symbol; }),
                running.end());
  run.id       = std::string(id);
  run.symbol   = symbol;
  run.instance = world.script().findSymbolIndex(symbol);
  run.start    = world.tickCount();
  run.state    = state;
  run.maxMs    = action=="start_activity" ? ActivityMaxMs : (state.isValid() ? StateMaxMs : ActMaxMs);
  running.push_back(std::move(run));
  reply(id,symbol,"queued","");
  }

void NpcObserver::routine(World& world, const rapidjson::Value& msg) {
  const auto symbol = upper(JsonRead::str(msg,"npc"));
  Npc*       npc    = npcBySymbol(world,symbol);
  JsonWriter j("routine");
  j.str("req",JsonRead::str(msg,"req")).str("npc",symbol);
  if(npc==nullptr || npc->isPlayer()) {
    emit(j.str("error","npc not found").finish());
    return;
    }
  auto stateName = [&](ScriptFn fn) { return AiNpc::stateName(world,fn); };
  auto hhmm = [](gtime t) {
    char buf[8];
    std::snprintf(buf,sizeof(buf),"%02d:%02d",int(t.hour()),int(t.minute()));
    return std::string(buf);
    };
  j.str("current",stateName(npc->currentState())).array("entries");
  npc->forEachRoutine([&](const Npc::RoutineInfo& r) {
    j.object()
       .str("start",hhmm(r.start))
       .str("end",hhmm(r.end))
       .str("state",stateName(r.state))
       .str("waypoint",r.point)
     .end();
    });
  j.endArray().array("routines");
  // the npc's routine variants: Rtn_<name>_<id> in the scripts
  const std::string suffix = "_" + std::to_string(npc->handle().id);
  for(size_t i=0; i<world.script().symbolsCount(); ++i) {
    auto sym = world.script().findSymbol(i);
    if(sym==nullptr)
      continue;
    const std::string& n = sym->name();
    if(n.size()>4+suffix.size() && n.starts_with("RTN_") && n.ends_with(suffix))
      j.item(n.substr(4,n.size()-4-suffix.size()));
    }
  emit(j.endArray().finish());
  }

void NpcObserver::checkRunning(World& world, uint64_t now) {
  for(size_t i=0; i<running.size();) {
    auto& r   = running[i];
    Npc*  npc = world.findNpcByInstance(r.instance);
    const bool  gone   = npc==nullptr || npc->isDead() || npc->isDown() || !owner(r.symbol).empty();
    bool        done   = false;
    std::string reason = "done";
    std::string detail;
    if(!gone && r.state.isValid()) {
      // a script state: done once the npc has entered it and left it again
      const bool in = npc->isInState(r.state);
      r.entered = r.entered || in;
      done      = (r.entered && !in) || (!r.entered && now>r.start+StateStartMs);
      // there and doing it (v15): the state has begun (its start pushes the walk to the spot) and the walk
      // is over (queue, go-to and waits empty). From the feet: the waypoint is on the ground, and qDistTo's
      // center is about a metre above it, so a small radius could never be met
      if(r.arrival && in && now>r.start+ActGraceMs && npc->isAiQueueEmpty() &&
         (npc->position()-r.at).quadLength()<=r.radius*r.radius) {
        done   = true;
        reason = "arrived";
        }
      } else {
      done = !gone && now>r.start+ActGraceMs && npc->isAiQueueEmpty();
      }
    const bool late = now>r.start+r.maxMs;
    if(!gone && !done && !late) {
      ++i;
      continue;
      }
    if(gone)
      reason = "gone"; else
    if(!done)
      reason = "timeout"; else
    if(r.giveTo!=size_t(-1)) {
      // a give (v15): walked up and turned, the item changes hands now
      Npc* to = world.findNpcByInstance(r.giveTo);
      bool ok = false;
      if(to==nullptr || to->isDead())
        detail = "target npc is gone"; else
        ok = AiNpc::giveItem(*npc,*to,r.item,r.count,detail);
      if(!ok)
        reason = "failed";
      }
    JsonWriter j("npc_idle");
    j.str("npc",r.symbol).str("act",r.id).str("reason",reason);
    if(!detail.empty())
      j.str("detail",detail);
    emit(j.finish());
    running.erase(running.begin()+ptrdiff_t(i));
    }
  }

void NpcObserver::emitEvent(std::string_view npc, std::string_view kind, std::string_view other,
                            double distance, std::string_view detail, std::string_view otherName,
                            std::string_view victim) {
  JsonWriter j("npc_event");
  j.str("npc",npc).str("kind",kind).str("other",other).num("distance",distance);
  if(!otherName.empty())
    j.str("other_name",otherName);
  if(!victim.empty())
    j.str("victim",victim);
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
