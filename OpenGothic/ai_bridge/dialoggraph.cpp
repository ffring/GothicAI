#include "dialoggraph.h"

#include <Tempest/Log>

#include <algorithm>
#include <cctype>
#include <cstdlib>

#include "commandline.h"
#include "game/compatibility/directmemory.h"
#include "gothic.h"
#include "world/aiqueue.h"
#include "world/objects/npc.h"
#include "world/world.h"

#include "bytecode.h"
#include "eval.h"
#include "exec.h"
#include "exprjson.h"
#include "lifter.h"
#include "predicates.h"

#include "jsonread.h"
#include "jsonwriter.h"
#include "textcodec.h"

#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

using namespace Tempest;
using storygraph::ExprP;
using storygraph::Expr;
using zenkit::DaedalusDataType;

namespace {

std::string upper(std::string_view s) {
  std::string r(s);
  for(auto& c:r)
    c = char(std::toupper(static_cast<unsigned char>(c)));
  return r;
  }

std::string str(const rapidjson::Value& v, const char* key) {
  return std::string(JsonRead::str(v,key));
  }

bool usesRandom(const ExprP& e) {
  bool r = false;
  storygraph::ex::walk(e, [&r](const Expr& x) { r |= (x.kind==Expr::Call && x.name=="HLP_RANDOM"); });
  return r;
  }

}

DialogGraph::DialogGraph(std::function<void(std::string&&)> send,
                         std::function<std::string(const std::string&,const std::string&)> addLine)
  :send(std::move(send)), addLine(std::move(addLine)) {
  const char* env = std::getenv("GOTHIC_AI_STORYCHECK");
  checking = env!=nullptr ? std::string_view(env)=="1" : CommandLine::inst().isDevMode();
  const char* exec = std::getenv("GOTHIC_AI_STORYEXEC");
  running  = exec==nullptr || std::string_view(exec)!="0";

  auto& g = Gothic::inst();
  g.dialogFilterFn       = [this](const zenkit::IInfo& info, bool& visible) { onFilter(info,visible); };
  g.dialogChoiceFilterFn = [this](const zenkit::IInfo& info, int32_t fn) { return onChoiceFilter(info,fn); };
  g.dialogExtraFn        = [this](const zenkit::INpc& npc, bool important, std::vector<GameScript::DlgChoice>& out) {
    onExtra(npc,important,out);
    };
  g.dialogExtraExecFn    = [this](const GameScript::DlgChoice& dlg, Npc& player, Npc& npc) { onExec(dlg,player,npc); };
  g.onDialogChosen.bind(this,&DialogGraph::onChosen);
  g.dialogScriptFn       = [this](zenkit::DaedalusSymbol& fn, int32_t& result) { return onScript(fn,result); };
  if(running)
    Log::i("[story] dialogs run as their programs");
  if(checking)
    Log::i("[story] conditions are checked against their lifted form");
  }

DialogGraph::~DialogGraph() {
  auto& g = Gothic::inst();
  g.dialogFilterFn       = nullptr;
  g.dialogChoiceFilterFn = nullptr;
  g.dialogExtraFn        = nullptr;
  g.dialogExtraExecFn    = nullptr;
  g.dialogScriptFn       = nullptr;
  g.onDialogChosen.ubind(this,&DialogGraph::onChosen);
  }

zenkit::DaedalusVm* DialogGraph::vm() const {
  World* w = Gothic::inst().world();
  return w!=nullptr ? &w->script().getVm() : nullptr;
  }

void DialogGraph::ensureLifter() {
  auto* v = vm();
  if(v==nullptr || v==liftedFor)
    return;
  predicates.reset();
  lifter.reset();
  code       = std::make_unique<storygraph::Bytecode>(*v);
  lifter     = std::make_unique<storygraph::Lifter>(*v,*code);
  predicates = std::make_unique<storygraph::Predicates>(*v,*lifter);
  liftedFor  = v;
  }

void DialogGraph::clear() {
  disabled.clear();
  conditions.clear();
  nodes.clear();
  told.clear();
  reported.clear();
  // the next game session has its own VM
  predicates.reset();
  lifter.reset();
  code.reset();
  liftedFor = nullptr;
  interp.reset();
  engine.reset();
  programs.clear();
  runFor = nullptr;
  }

std::vector<std::string> DialogGraph::apply(const rapidjson::Value& msg) {
  std::vector<std::string> errors;
  if(JsonRead::boolean(msg,"replace",false))
    clear();
  auto it = msg.FindMember("ops");
  if(it==msg.MemberEnd() || !it->value.IsArray())
    return errors;
  size_t i = 0;
  for(auto& op : it->value.GetArray()) {
    auto err = op.IsObject() ? applyOp(op) : std::string("not an object");
    if(!err.empty()) {
      errors.push_back("op " + std::to_string(i) + ": " + err);
      Log::e("[story] patch op ",i,": ",err);
      }
    ++i;
    }
  Log::i("[story] patch: ",i," operation(s), ",errors.size()," rejected; ",disabled.size()," off, ",
         conditions.size()," with conditions, ",nodes.size()," new");
  return errors;
  }

std::string DialogGraph::applyOp(const rapidjson::Value& op) {
  const auto kind = str(op,"op");
  if(kind=="add_node") {
    auto it = op.FindMember("node");
    if(it==op.MemberEnd() || !it->value.IsObject())
      return "add_node: no node";
    Node n;
    auto err = readNode(it->value,n);
    if(!err.empty())
      return err;
    auto id = n.id;
    nodes[id] = std::move(n);
    return {};
    }

  const auto node = upper(str(op,"node"));
  if(node.empty())
    return kind + ": no node";
  if(kind=="disable") {
    disabled.insert(node);
    return {};
    }
  if(kind=="enable") {
    disabled.erase(node);
    return {};
    }
  if(kind=="remove_node") {
    nodes.erase(node);
    told.erase(node);
    return {};
    }
  if(kind=="told") {
    told.insert(node);
    return {};
    }
  if(kind=="condition") {
    auto it = op.FindMember("expr");
    if(it==op.MemberEnd() || it->value.IsNull()) {
      conditions.erase(node);  // no expression: the ones added before are dropped
      return {};
      }
    std::string err;
    auto e = storygraph::readExpr(it->value,err);
    if(e==nullptr)
      return "condition: " + err;
    err = checkExpr(e);
    if(!err.empty())
      return "condition: " + err;
    conditions[node].push_back(e);
    return {};
    }
  return "unknown op " + kind;
  }

std::string DialogGraph::readNode(const rapidjson::Value& v, Node& n) {
  n.id          = upper(str(v,"id"));
  n.npc         = upper(str(v,"npc"));
  n.description = TextCodec::fromUtf8(JsonRead::str(v,"description"));
  n.nr          = int32_t(JsonRead::integer(v,"nr",0));
  n.important   = JsonRead::boolean(v,"important",false);
  n.permanent   = JsonRead::boolean(v,"permanent",false) && !n.important;
  if(n.id.empty() || n.npc.empty())
    return "add_node: needs id and npc";
  if(!n.important && n.description.empty())
    return "add_node: a dialog the player picks needs a description";
  auto* dat = vm();
  if(dat!=nullptr) {
    if(dat->find_symbol_by_name(n.id)!=nullptr)
      return "add_node: " + n.id + " is a script symbol";
    auto* npc = dat->find_symbol_by_name(n.npc);
    if(npc==nullptr || npc->type()!=DaedalusDataType::INSTANCE)
      return "add_node: no npc " + n.npc;
    }

  if(auto it = v.FindMember("condition"); it!=v.MemberEnd() && !it->value.IsNull()) {
    std::string err;
    n.condition = storygraph::readExpr(it->value,err);
    if(n.condition==nullptr)
      return "add_node condition: " + err;
    err = checkExpr(n.condition);
    if(!err.empty())
      return "add_node condition: " + err;
    }

  if(auto it = v.FindMember("lines"); it!=v.MemberEnd() && it->value.IsArray()) {
    for(auto& l : it->value.GetArray()) {
      Line line;
      line.hero = JsonRead::str(l,"speaker")=="other";
      line.id   = upper(str(l,"line"));
      line.text = TextCodec::fromUtf8(JsonRead::str(l,"text"));
      if(line.id.empty() && line.text.empty())
        return "add_node line: needs line or text";
      if(!line.id.empty() && Gothic::inst().messageByName(line.id).empty())
        return "add_node line: no original line " + line.id;
      n.lines.push_back(std::move(line));
      }
    }

  if(auto it = v.FindMember("effects"); it!=v.MemberEnd() && it->value.IsArray()) {
    for(auto& x : it->value.GetArray()) {
      StoryEffect e;
      auto err = StoryEffect::read(x,dat,e);
      if(!err.empty())
        return "add_node " + err;
      n.effects.push_back(std::move(e));
      }
    }
  return {};
  }

std::string DialogGraph::checkExpr(const ExprP& e) {
  auto* v = vm();
  if(v==nullptr)
    return {};  // no world yet: names are checked when the condition is computed
  std::string err;
  storygraph::ex::walk(e, [&](const Expr& x) {
    if(!err.empty())
      return;
    auto* s = (x.kind==Expr::Var || x.kind==Expr::Inst || x.kind==Expr::Sym || x.kind==Expr::Call ||
               x.kind==Expr::Fn) ? v->find_symbol_by_name(x.name) : nullptr;
    switch(x.kind) {
      case Expr::Var:
        if(s==nullptr || s->is_member())
          err = "no variable " + x.name;
        break;
      case Expr::Inst:
        if(s==nullptr || s->type()!=DaedalusDataType::INSTANCE)
          err = "no instance " + x.name;
        break;
      case Expr::Sym:
        if(s==nullptr)
          err = "no symbol " + x.name;
        break;
      case Expr::Call:
        if(s==nullptr || !s->is_external())
          err = "no engine function " + x.name;
        else if(v->find_parameters_for_function(s).size()!=x.args.size())
          err = x.name + " takes " + std::to_string(v->find_parameters_for_function(s).size()) + " arguments";
        break;
      case Expr::Fn:
        if(s==nullptr || s->type()!=DaedalusDataType::FUNCTION || s->is_external())
          err = "no script function " + x.name;
        break;
      default:
        break;
      }
    });
  if(!err.empty())
    return err;
  ensureLifter();
  return predicates->whyNotLifted(e);
  }

std::optional<bool> DialogGraph::test(const ExprP& e, std::string_view what) {
  auto* v = vm();
  if(v==nullptr)
    return std::nullopt;
  ensureLifter();
  predicates->whyNotLifted(e);  // lifts the script functions it calls
  storygraph::VmExternals engine(*v);
  storygraph::Evaluator   ev(*v,predicates->helpers(),engine);
  auto r = ev.eval(e);
  if(!r || r->type!=storygraph::Value::Int) {
    if(reported.insert(std::string("eval:") + std::string(what)).second)
      Log::e("[story] condition of ",what," not computed: ",r ? "not an int" : ev.why());
    return std::nullopt;
    }
  return r->i!=0;
  }

void DialogGraph::check(const zenkit::IInfo& info, bool visible) {
  auto* v = vm();
  if(v==nullptr || info.condition==0)
    return;
  ensureLifter();
  auto& e = predicates->condition(uint32_t(info.condition));
  if(!predicates->whyNotLifted(e).empty() || usesRandom(e))
    return;  // not lifted, or a dice roll of its own
  storygraph::VmExternals engine(*v);
  storygraph::Evaluator   ev(*v,predicates->helpers(),engine);
  auto r = ev.eval(e);
  checked++;
  auto* sym  = v->find_symbol_by_index(info.symbol_index());
  auto  name = sym ? sym->name() : std::string("?");
  if(r && r->type==storygraph::Value::Int && (r->i!=0)==visible)
    return;
  differ++;
  if(reported.insert("check:" + name).second) {
    differences.push_back(name);
    Log::e("[story] check: ",name," vm ",int(visible),", model ",
           r ? std::to_string(r->i) : "? (" + ev.why() + ")",": ",e->str());
    }
  }

void DialogGraph::onFilter(const zenkit::IInfo& info, bool& visible) {
  if(checking)
    check(info,visible);
  if(disabled.empty() && conditions.empty())
    return;
  auto* v   = vm();
  auto* sym = v ? v->find_symbol_by_index(info.symbol_index()) : nullptr;
  if(sym==nullptr)
    return;
  if(disabled.count(sym->name())) {
    visible = false;
    return;
    }
  if(!visible)
    return;
  auto it = conditions.find(sym->name());
  if(it==conditions.end())
    return;
  for(auto& c : it->second) {
    // a condition that can't be computed leaves the dialog as the scripts have it
    auto r = test(c,sym->name());
    if(r && !*r) {
      visible = false;
      return;
      }
    }
  }

bool DialogGraph::onChoiceFilter(const zenkit::IInfo&, int32_t fn) {
  if(disabled.empty())
    return true;
  auto* v   = vm();
  auto* sym = v ? v->find_symbol_by_index(uint32_t(fn)) : nullptr;
  return sym==nullptr || disabled.count(sym->name())==0;
  }

void DialogGraph::onExtra(const zenkit::INpc& npc, bool important, std::vector<GameScript::DlgChoice>& out) {
  if(nodes.empty())
    return;
  auto* v   = vm();
  auto* sym = v ? v->find_symbol_by_index(npc.symbol_index()) : nullptr;
  if(sym==nullptr)
    return;
  for(auto& [id,n] : nodes) {
    if(n.npc!=sym->name() || n.important!=important || disabled.count(id))
      continue;
    if(told.count(id) && !n.permanent)
      continue;
    if(n.condition!=nullptr) {
      auto r = test(n.condition,id);
      if(!r || !*r)
        continue;
      }
    GameScript::DlgChoice ch;
    ch.title    = important ? std::string() : n.description;
    ch.sort     = n.nr;
    ch.external = id;
    out.push_back(std::move(ch));
    }
  }

void DialogGraph::onExec(const GameScript::DlgChoice& dlg, Npc& player, Npc& npc) {
  auto it = nodes.find(dlg.external);
  if(it==nodes.end())
    return;
  auto& n = it->second;
  told.insert(n.id);

  // lines are queued like AI_Output in an info function; effects happen right away, like there
  auto* v = vm();
  for(auto& l : n.lines) {
    Npc& speaker  = l.hero ? player : npc;
    Npc& listener = l.hero ? npc : player;
    auto id = l.id;
    if(id.empty()) {
      auto* s = v ? v->find_symbol_by_index(speaker.instanceSymbol()) : nullptr;
      id = addLine(s ? s->name() : std::string(),l.text);
      }
    speaker.aiPush(AiQueue::aiOutput(listener,id,0));
    }
  if(v==nullptr)
    return;
  for(auto& e : n.effects)
    e.run(*v,npc,player,n.id);
  }

bool DialogGraph::onScript(zenkit::DaedalusSymbol& fn, int32_t& result) {
  auto* v = vm();
  if(!running || v==nullptr)
    return false;
  ensureLifter();
  if(runFor!=v) {
    // Ikarus loops move the VM's program counter from engine functions (DirectMemory): a program has none
    if(DirectMemory::isRequired(*v)) {
      Log::i("[story] Ikarus scripts: dialogs run in the VM");
      running = false;
      return false;
      }
    interp.reset();
    programs.clear();
    engine = std::make_unique<storygraph::VmExternals>(*v);  // the engine's own functions, as the VM calls them
    interp = std::make_unique<storygraph::Interpreter>(*v,*engine);
    runFor = v;
    }
  auto it = programs.find(fn.index());
  if(it==programs.end()) {
    it = programs.emplace(fn.index(), storygraph::liftProgram(*v,*code,fn.index())).first;
    if(!it->second.error.empty()) {
      notPrograms++;
      Log::e("[story] ", fn.name(), " is not a program (", it->second.error, "): the VM runs it");
      }
    }
  if(!it->second.error.empty())
    return false;
  auto r = interp->run(it->second);
  ran++;
  if(!r.ok) {
    // what ran before the error is done: the VM can't run it again
    runErrors++;
    Log::e("[story] program ", fn.name(), ": ", r.error);
    }
  result = r.returned.type==storygraph::Value::Int ? r.returned.i : 0;
  return true;
  }

void DialogGraph::onChosen(const GameScript::DlgChoice& dlg, Npc& player, Npc& npc) {
  auto* v = vm();
  if(v==nullptr || &player==&npc)
    return;
  std::string node = dlg.external;
  if(node.empty() && dlg.handle!=nullptr) {
    // the dialog itself, or one of its choices
    auto* s = dlg.scriptFn==uint32_t(dlg.handle->information) ? v->find_symbol_by_index(dlg.handle->symbol_index())
                                                                : v->find_symbol_by_index(dlg.scriptFn);
    if(s!=nullptr)
      node = s->name();
    }
  auto* npcSym = v->find_symbol_by_index(npc.instanceSymbol());
  if(node.empty() || npcSym==nullptr)
    return;
  send(JsonWriter("dialog_chosen")
         .str("npc",npcSym->name())
         .str("node",node)
         .boolean("made",!dlg.external.empty())
         .finish());
  }

std::vector<std::string> DialogGraph::offered(World& world, Npc& npc) {
  std::vector<std::string> ret;
  Npc* player = world.player();
  auto* v     = vm();
  if(player==nullptr || v==nullptr)
    return ret;
  // important dialogs start on their own and hide the others; list both
  for(bool important : {true,false}) {
    for(auto& ch : npc.dialogChoices(*player,{},important)) {
      std::string id = ch.external;
      if(id.empty() && ch.handle!=nullptr)
        if(auto* s = v->find_symbol_by_index(ch.handle->symbol_index()))
          id = s->name();
      if(!id.empty() && std::find(ret.begin(),ret.end(),id)==ret.end())
        ret.push_back(id);
      }
    }
  return ret;
  }

bool DialogGraph::choose(World& world, Npc& npc, std::string_view node, std::string& error) {
  Npc* player = world.player();
  auto* v     = vm();
  if(player==nullptr || v==nullptr) {
    error = "no world";
    return false;
    }
  const auto id = upper(node);
  for(bool important : {true,false}) {
    for(auto& ch : npc.dialogChoices(*player,{},important)) {
      auto* s = ch.handle!=nullptr ? v->find_symbol_by_index(ch.handle->symbol_index()) : nullptr;
      if(ch.external==id || (s!=nullptr && s->name()==id)) {
        Gothic::inst().dialogExec(ch,*player,npc);
        return true;
        }
      }
    }
  error = id + " is not offered";
  return false;
  }

bool DialogGraph::evaluate(World& world, Npc* npc, const rapidjson::Value& expr, std::string& json,
                           std::string& error) {
  auto* v = vm();
  if(v==nullptr) {
    error = "no world";
    return false;
    }
  auto e = storygraph::readExpr(expr,error);
  if(e==nullptr)
    return false;
  error = checkExpr(e);
  if(!error.empty())
    return false;
  auto* self  = v->global_self();
  auto* other = v->global_other();
  auto  prevSelf  = self->get_instance();
  auto  prevOther = other->get_instance();
  if(npc!=nullptr)
    self->set_instance(npc->handlePtr());
  if(world.player()!=nullptr)
    other->set_instance(world.player()->handlePtr());
  storygraph::VmExternals engine(*v);
  storygraph::Evaluator   ev(*v,predicates->helpers(),engine);
  auto r = ev.eval(e);
  self->set_instance(prevSelf);
  other->set_instance(prevOther);
  if(!r) {
    error = ev.why();
    return false;
    }
  rapidjson::StringBuffer                    buf;
  rapidjson::Writer<rapidjson::StringBuffer> w(buf);
  switch(r->type) {
    case storygraph::Value::Int:   w.Int(r->i);    break;
    case storygraph::Value::Float: w.Double(r->f); break;
    case storygraph::Value::Str: {
      auto s = TextCodec::toUtf8(r->s);
      w.String(s.data(),rapidjson::SizeType(s.size()));
      break;
      }
    case storygraph::Value::Inst:
      if(r->inst!=nullptr) {
        auto s = r->text(*v);
        w.String(s.data(),rapidjson::SizeType(s.size()));
        } else {
        w.Null();
        }
      break;
    }
  json.assign(buf.GetString(),buf.GetSize());
  return true;
  }

std::string DialogGraph::checkWorld(World& world) {
  const bool   was    = checking;
  const size_t before = checked, differBefore = differ;
  uint32_t     npcs   = 0;
  checking = true;
  for(uint32_t i=0; i<world.npcCount(); ++i) {
    Npc* npc = world.npcById(i);
    if(npc==nullptr || npc->isPlayer())
      continue;
    offered(world,*npc);
    npcs++;
    }
  checking = was;
  rapidjson::StringBuffer                    buf;
  rapidjson::Writer<rapidjson::StringBuffer> w(buf);
  w.StartObject();
  w.Key("npcs");     w.Uint(npcs);
  w.Key("computed"); w.Uint64(checked-before);
  w.Key("differ");   w.Uint64(differ-differBefore);
  w.Key("differences");
  w.StartArray();
  for(auto& d : differences)
    w.String(d.data(),rapidjson::SizeType(d.size()));
  w.EndArray();
  // dialogs' script functions run as programs: how many runs so far, failed, and functions left to the VM
  w.Key("programs");
  w.StartObject();
  w.Key("on");     w.Bool(running);
  w.Key("runs");   w.Uint64(ran);
  w.Key("failed"); w.Uint64(runErrors);
  w.Key("vm");     w.Uint64(notPrograms);
  w.EndObject();
  w.EndObject();
  Log::i("[story] check of the world: ",npcs," npcs, ",checked-before," conditions computed, ",
         differ-differBefore," differ");
  return std::string(buf.GetString(),buf.GetSize());
  }

std::string DialogGraph::status() const {
  return "story graph: " + std::to_string(disabled.size()) + " off, " + std::to_string(conditions.size()) +
         " with conditions, " + std::to_string(nodes.size()) + " new (" + std::to_string(told.size()) +
         " told); check " + (checking ? "on" : "off") + ": " + std::to_string(checked) + " computed, " +
         std::to_string(differ) + " differ; programs " + (running ? "on" : "off") + ": " + std::to_string(ran) +
         " runs, " + std::to_string(runErrors) + " failed, " + std::to_string(notPrograms) + " not programs";
  }
