#include "storyeffects.h"

#include <Tempest/Log>

#include <cctype>

#include <zenkit/DaedalusVm.hh>

#include "world/objects/npc.h"

#include "eval.h"
#include "jsonread.h"
#include "textcodec.h"

using namespace Tempest;
using zenkit::DaedalusDataType;
using storygraph::Value;

static std::string upper(std::string_view s) {
  std::string r(s);
  for(auto& c:r)
    c = char(std::toupper(static_cast<unsigned char>(c)));
  return r;
  }

std::string StoryEffect::read(const rapidjson::Value& x, zenkit::DaedalusVm* vm, StoryEffect& e) {
  e.kind   = std::string(JsonRead::str(x,"do"));
  e.item   = upper(JsonRead::str(x,"item"));
  e.var    = upper(JsonRead::str(x,"var"));
  e.reason = upper(JsonRead::str(x,"reason"));
  e.topic  = TextCodec::fromUtf8(JsonRead::str(x,"topic"));
  e.text   = TextCodec::fromUtf8(JsonRead::str(x,"text"));
  e.status = upper(JsonRead::str(x,"status"));
  e.count  = int32_t(JsonRead::integer(x,"count",JsonRead::integer(x,"amount",
                     JsonRead::integer(x,"value",JsonRead::integer(x,"seconds",1)))));
  e.toHero = JsonRead::str(x,"to")!="self";
  auto* item = vm ? vm->find_symbol_by_name(e.item) : nullptr;
  auto* var  = vm ? vm->find_symbol_by_name(e.var) : nullptr;
  if(e.kind=="give") {
    if(vm && (item==nullptr || item->type()!=DaedalusDataType::INSTANCE))
      return "give: no item " + e.item;
    if(e.count<=0)
      return "give: count must be positive";
    return {};
    }
  if(e.kind=="set") {
    if(vm && (var==nullptr || var->type()!=DaedalusDataType::INT || var->is_const() || var->is_member()))
      return "set: no variable " + e.var;
    return {};
    }
  if(e.kind=="journal") {
    if(e.status.empty())
      e.status = "LOG_RUNNING";
    auto* st = vm ? vm->find_symbol_by_name(e.status) : nullptr;
    if(vm && (st==nullptr || !st->is_const() || st->type()!=DaedalusDataType::INT))
      return "journal: no status " + e.status;
    return e.topic.empty() ? "journal: needs a topic" : "";
    }
  if(e.kind=="attack") {
    if(e.reason.empty())
      e.reason = "AR_NONE";
    auto* r = vm ? vm->find_symbol_by_name(e.reason) : nullptr;
    if(vm && (r==nullptr || !r->is_const() || r->type()!=DaedalusDataType::INT))
      return "attack: no reason " + e.reason;
    return {};
    }
  if(e.kind=="refuse_talk")
    return e.count<=0 ? "refuse_talk: seconds must be positive" : "";
  if(e.kind=="xp" || e.kind=="end")
    return {};
  return "unknown effect " + e.kind;
  }

void StoryEffect::run(zenkit::DaedalusVm& vm, Npc& npc, Npc& player, std::string_view who) const {
  auto call = [&](const char* fn, std::vector<Value> args) {
    std::string err;
    auto* s = vm.find_symbol_by_name(fn);
    if(s==nullptr || !storygraph::callFunction(vm,*s,args,err))
      Log::e("[story] ",who,": ",fn," failed: ",s ? err : "no such function");
    };
  auto constant = [&](std::string_view name, int32_t def) {
    auto* s = vm.find_symbol_by_name(name);
    return s!=nullptr && s->type()==DaedalusDataType::INT ? s->get_int(0) : def;
    };
  // the scripts' own functions read self and other
  auto* self  = vm.global_self();
  auto* other = vm.global_other();
  auto  prevSelf  = self->get_instance();
  auto  prevOther = other->get_instance();
  self->set_instance(npc.handlePtr());
  other->set_instance(player.handlePtr());

  if(kind=="give") {
    if(auto* s = vm.find_symbol_by_name(item)) {
      auto& from = toHero ? npc : player;
      auto& to   = toHero ? player : npc;
      call("B_GIVEINVITEMS",{Value::instance(from.handlePtr()),Value::instance(to.handlePtr()),
                             Value::integer(int32_t(s->index())),Value::integer(count)});
      }
    }
  else if(kind=="xp") {
    call("B_GIVEPLAYERXP",{Value::integer(count)});
    }
  else if(kind=="set") {
    if(auto* s = vm.find_symbol_by_name(var))
      s->set_int(count);
    }
  else if(kind=="journal") {
    call("LOG_CREATETOPIC",{Value::str(topic),Value::integer(constant("LOG_MISSION",0))});
    call("LOG_SETTOPICSTATUS",{Value::str(topic),Value::integer(constant(status,1))});
    if(!text.empty())
      call("B_LOGENTRY",{Value::str(topic),Value::str(text)});
    }
  else if(kind=="end") {
    call("AI_STOPPROCESSINFOS",{Value::instance(npc.handlePtr())});
    }
  else if(kind=="attack") {
    // the scripts' own attack: the reason decides how far it goes (AR_NONE: until the hero is down)
    call("B_ATTACK",{Value::instance(npc.handlePtr()),Value::instance(player.handlePtr()),
                     Value::integer(constant(reason,0)),Value::integer(0)});
    }
  else if(kind=="refuse_talk") {
    call("NPC_SETREFUSETALK",{Value::instance(npc.handlePtr()),Value::integer(count)});
    }

  self->set_instance(prevSelf);
  other->set_instance(prevOther);
  }
