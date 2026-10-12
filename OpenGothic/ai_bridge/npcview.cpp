#include "npcview.h"

#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <algorithm>
#include <cmath>
#include <vector>

#include "game/constants.h"
#include "game/gamescript.h"
#include "world/objects/interactive.h"
#include "world/objects/item.h"
#include "world/objects/npc.h"
#include "world/world.h"

#include "jsonwriter.h"
#include "npcfind.h"
#include "textcodec.h"

namespace {

constexpr uint64_t PushPeriodMs = 1000;
constexpr float    PushRadius   = 2000;  // cm from the player: npc's whose view is pushed
constexpr size_t   PushLimit    = 12;
constexpr size_t   SeenLimit    = 12;    // people and creatures in one view
constexpr float    SightMax     = 3000;  // cm: never further, whatever the senses range says
constexpr float    FacingCos    = 0.7f;  // within ~45 degrees: it faces you

using Writer = rapidjson::Writer<rapidjson::StringBuffer>;

void key(Writer& w, std::string_view k) {
  w.Key(k.data(),rapidjson::SizeType(k.size()));
  }

void str(Writer& w, std::string_view k, std::string_view v) {
  key(w,k);
  w.String(v.data(),rapidjson::SizeType(v.size()));
  }

void utf8(Writer& w, std::string_view k, std::string_view v) {
  str(w,k,TextCodec::toUtf8(v));
  }

std::string symbolOf(World& world, size_t cls) {
  auto sym = world.script().findSymbol(cls);
  return sym!=nullptr ? sym->name() : std::string();
  }

std::string npcId(World& world, Npc& npc) {
  if(npc.isPlayer())
    return "PLAYER";
  return symbolOf(world,npc.instanceSymbol());
  }

// what a mob is called in the game: its focus name is a script string (MOBNAME_BOOKSBOARD), shown by value
std::string mobName(World& world, std::string_view focus) {
  auto sym = world.script().findSymbol(focus);
  if(sym!=nullptr && sym->type()==zenkit::DaedalusDataType::STRING)
    return sym->get_string();
  return std::string(focus);
  }

std::string_view weapon(const Npc& npc) {
  switch(npc.weaponState()) {
    case WeaponState::NoWeapon: return "none";
    case WeaponState::Fist:     return "fists";
    case WeaponState::W1H:
    case WeaponState::W2H:      return "melee";
    case WeaponState::Bow:
    case WeaponState::CBow:     return "ranged";
    case WeaponState::Mage:     return "magic";
    }
  return "none";
  }

std::string_view body(const Npc& npc) {
  if(npc.isDead())
    return "dead";
  if(npc.isUnconscious())
    return "down";
  if(npc.interactive()!=nullptr)
    return "mob";
  switch(npc.bodyStateMasked()) {
    case BS_WALK:
    case BS_SNEAK:          return "walk";
    case BS_RUN:
    case BS_SPRINT:         return "run";
    case BS_SWIM:
    case BS_DIVE:           return "swim";
    case BS_SIT:            return "sit";
    case BS_LIE:            return "lie";
    case BS_ITEMINTERACT:   return "item";
    default:                return "stand";
    }
  }

// `other` faces `self`: its body turned towards it
bool faces(const Npc& other, const Npc& self) {
  const auto  d   = self.position()-other.position();
  const float len = std::sqrt(d.x*d.x+d.z*d.z);
  if(len<1)
    return true;
  const float rot = other.rotationRad();
  return (d.x*std::cos(rot)+d.z*std::sin(rot))/len>=FacingCos;
  }

void writeItem(Writer& w, World& world, std::string_view k, const Item* it) {
  if(it==nullptr)
    return;
  key(w,k);
  w.StartObject();
  str(w,"item",symbolOf(world,it->clsId()));
  utf8(w,"name",it->displayName());
  w.EndObject();
  }

// what one can tell about a person or creature by looking at it
void writeLook(Writer& w, World& world, Npc& self, Npc& other, float dist) {
  str(w,"id",npcId(world,other));
  utf8(w,"name",other.displayName());
  str(w,"kind",other.isDead() ? "body" : (other.guild()>=GIL_SEPERATOR_HUM ? "monster" : "human"));
  w.Key("guild");    w.Int(int(other.guild()));
  w.Key("distance"); w.Double(std::round(double(dist)));
  str(w,"state",AiNpc::stateName(world,other.currentState()));
  str(w,"body",body(other));
  w.Key("moving");   w.Bool(AiNpc::isMoving(other));
  str(w,"weapon",weapon(other));
  w.Key("faces_you"); w.Bool(faces(other,self));
  const int32_t hp = other.attribute(ATR_HITPOINTS), hpMax = other.attribute(ATR_HITPOINTSMAX);
  w.Key("hurt");     w.Bool(hpMax>0 && hp*2<hpMax);
  if(other.guild()<GIL_SEPERATOR_HUM) {
    auto& inv = const_cast<Inventory&>(other.inventory());
    writeItem(w,world,"armor",inv.currentArmor());
    writeItem(w,world,"in_hands",inv.stateSlotItem());
    }
  if(auto partner = other.talkPartner())
    str(w,"talking_to",npcId(world,*partner));
  }

void writeView(Writer& w, World& world, Npc& npc) {
  Npc* pl = world.player();
  w.StartObject();
  str(w,"npc",npcId(world,npc));
  utf8(w,"name",npc.displayName());
  if(auto wp = npc.currentWayPoint())
    str(w,"waypoint",wp->name);
  if(auto wp = world.findWayPoint(npc.position()))
    str(w,"nearest_waypoint",wp->name);
  const auto portal = npc.portalName();
  w.Key("indoors"); w.Bool(!portal.empty());
  if(!portal.empty()) {
    w.Key("room_guild"); w.Int(world.guildOfRoom(portal));
    }
  str(w,"state",AiNpc::stateName(world,npc.currentState()));
  str(w,"body",body(npc));
  if(auto mob = npc.interactive())
    utf8(w,"mob",mobName(world,mob->focusName()));
  str(w,"weapon",weapon(npc));
  writeItem(w,world,"in_hands",npc.inventory().stateSlotItem());
  w.Key("hp");     w.Int(npc.attribute(ATR_HITPOINTS));
  w.Key("hp_max"); w.Int(npc.attribute(ATR_HITPOINTSMAX));

  // the player: always there (it hears him if he speaks), seen or not
  if(pl!=nullptr && pl!=&npc) {
    const float dist = std::sqrt(npc.qDistTo(*pl));
    key(w,"hero");
    w.StartObject();
    writeLook(w,world,npc,*pl,dist);
    w.Key("seen"); w.Bool(dist<=SightMax && npc.canSeeNpc(*pl,false));
    w.EndObject();
    }

  // whom it sees: the nearest first
  struct Seen { Npc* npc; float dist; };
  std::vector<Seen> seen;
  const float range = std::min(float(npc.handle().senses_range),SightMax);
  world.detectNpc(npc.position(),range,[&](Npc& other) {
    if(&other==&npc || other.isPlayer())
      return;
    if(npc.canSeeNpc(other,false))
      seen.push_back({&other,std::sqrt(npc.qDistTo(other))});
    });
  std::sort(seen.begin(),seen.end(),[](const Seen& a, const Seen& b){ return a.dist<b.dist; });
  if(seen.size()>SeenLimit)
    seen.resize(SeenLimit);
  key(w,"sees");
  w.StartArray();
  for(auto& s:seen) {
    w.StartObject();
    writeLook(w,world,npc,*s.npc,s.dist);
    w.EndObject();
    }
  w.EndArray();
  w.EndObject();
  }

}

NpcView::NpcView(Emit emit):emit(std::move(emit)) {
  }

void NpcView::tick(World& world) {
  const uint64_t now = world.tickCount();
  if(now<next)
    return;
  next = now+PushPeriodMs;
  Npc* pl = world.player();
  if(pl==nullptr)
    return;
  struct Near { Npc* npc; float dist; };
  std::vector<Near> near;
  world.detectNpc(pl->position(),PushRadius,[&](Npc& npc) {
    if(npc.isPlayer() || npc.isDead() || npc.guild()>=GIL_SEPERATOR_HUM)
      return;  // people: monsters keep their own AI
    near.push_back({&npc,std::sqrt(npc.qDistTo(*pl))});
    });
  if(near.empty())
    return;
  std::sort(near.begin(),near.end(),[](const Near& a, const Near& b){ return a.dist<b.dist; });
  if(near.size()>PushLimit)
    near.resize(PushLimit);

  rapidjson::StringBuffer buf;
  Writer                  w(buf);
  w.StartArray();
  for(auto& n:near)
    writeView(w,world,*n.npc);
  w.EndArray();
  JsonWriter j("npc_views");
  j.integer("t",int64_t(now))
   .raw("views",std::string_view(buf.GetString(),buf.GetSize()));
  emit(j.finish());
  }

std::string NpcView::views(World& world, const rapidjson::Value& npcs) {
  rapidjson::StringBuffer buf;
  Writer                  w(buf);
  w.StartArray();
  if(npcs.IsArray()) {
    for(auto& v:npcs.GetArray()) {
      if(!v.IsString())
        continue;
      if(Npc* npc = AiNpc::findNpc(world,v.GetString()); npc!=nullptr && !npc->isPlayer())
        writeView(w,world,*npc);
      }
    }
  w.EndArray();
  return std::string(buf.GetString(),buf.GetSize());
  }
