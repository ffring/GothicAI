// Stage 2 of docs/story-graph.md: the lifted model against the VM, on original saves.
//
// For every dialog the condition is computed twice over the state of a save: by the VM running the script
// function, and by the Evaluator on the lifted expression. Both ask the same engine (SaveWorld), so a
// difference is an error of the lifting, whatever the engine answers.

#include "check.h"
#include "eval.h"
#include "model.h"
#include "saveworld.h"
#include "textcodec.h"

#include <zenkit/DaedalusVm.hh>
#include <zenkit/Logger.hh>
#include <zenkit/Stream.hh>
#include <zenkit/addon/daedalus.hh>
#include <zenkit/vobs/Misc.hh>

#include <algorithm>
#include <cstdio>
#include <filesystem>
#include <memory>

using namespace storygraph;
namespace fs = std::filesystem;
using zenkit::DaedalusDataType;

namespace {

// Values of all script variables, to undo what checking a condition wrote (C_Beklauen stores the theft
// values, conditions keep static locals): every dialog is checked against the state of the save.
class Snapshot final {
  public:
    explicit Snapshot(zenkit::DaedalusVm& vm) {
      for(uint32_t i=0; i<vm.symbols().size(); ++i) {
        auto* s = vm.find_symbol_by_index(i);
        if(s==nullptr || s->is_const() || s->is_member() || s->is_external())
          continue;
        auto t = s->type();
        if(t==DaedalusDataType::INT || t==DaedalusDataType::FLOAT || t==DaedalusDataType::STRING ||
           t==DaedalusDataType::INSTANCE)
          syms.push_back(s);
        }
      }

    void take() {
      ints.clear();
      floats.clear();
      strs.clear();
      insts.clear();
      for(auto* s : syms)
        for(uint16_t i=0; i<count(*s); ++i)
          switch(s->type()) {
            case DaedalusDataType::INT:      ints.push_back(s->get_int(i));      break;
            case DaedalusDataType::FLOAT:    floats.push_back(s->get_float(i));  break;
            case DaedalusDataType::STRING:   strs.push_back(s->get_string(i));   break;
            default:                         insts.push_back(s->get_instance()); break;
            }
      }

    void restore() {
      size_t ni = 0, nf = 0, ns = 0, nn = 0;
      for(auto* s : syms)
        for(uint16_t i=0; i<count(*s); ++i)
          switch(s->type()) {
            case DaedalusDataType::INT:      s->set_int(ints[ni++], i);      break;
            case DaedalusDataType::FLOAT:    s->set_float(floats[nf++], i);  break;
            case DaedalusDataType::STRING:   s->set_string(strs[ns++], i);   break;
            default:                         s->set_instance(insts[nn++]);   break;
            }
      }

  private:
    static uint16_t count(const zenkit::DaedalusSymbol& s) {
      return s.type()==DaedalusDataType::INSTANCE ? 1 : uint16_t(s.count());
      }

    std::vector<zenkit::DaedalusSymbol*>                    syms;
    std::vector<int32_t>                                    ints;
    std::vector<float>                                      floats;
    std::vector<std::string>                                strs;
    std::vector<std::shared_ptr<zenkit::DaedalusInstance>>  insts;
  };

struct Difference final {
  const Node* node;
  int32_t     vm;
  int32_t     model;
  };

struct Failure final {
  const Node* node;
  std::string why;
  };

struct Totals final {
  size_t saves = 0, dialogs = 0, checked = 0, agree = 0, differ = 0, unknown = 0, vmErrors = 0, notLifted = 0;
  size_t trueBoth = 0, absent = 0;
  };

std::string top(const std::map<std::string,size_t>& counts, size_t n) {
  std::vector<std::pair<size_t,std::string>> v;
  for(auto& [k,c] : counts)
    v.emplace_back(c, k);
  std::sort(v.rbegin(), v.rend());
  std::string r;
  for(size_t i=0; i<v.size() && i<n; ++i)
    r += (i ? ", " : "") + ident(v[i].second) + " " + std::to_string(v[i].first);
  if(v.size()>n)
    r += ", ... (" + std::to_string(v.size()-n) + " more)";
  return r;
  }

std::string shorten(std::string s, size_t n) {
  if(s.size()>n) {
    s.resize(n);
    s += "...";
    }
  return s;
  }

// one save: false if it could not be read
bool checkSave(const char* datPath, const Model& m, const fs::path& dir, const char* traceNode, bool verbose,
               Totals& t) {
  auto vm = loadVm(datPath);
  if(vm==nullptr) {
    std::fprintf(stderr, "can't open %s\n", datPath);
    return false;
    }
  SaveWorld world(*vm);
  bindExternals(*vm, world);
  std::string error;
  if(!world.load(dir, error)) {
    std::printf("== %s: can't read the save: %s\n\n", dir.filename().string().c_str(), error.c_str());
    return false;
    }

  std::printf("== %s: \"%s\", %s, day %d %02d:%02d, KAPITEL %d\n", dir.filename().string().c_str(),
              TextCodec::toUtf8(world.title).c_str(), world.world.c_str(), world.day, world.hour, world.minute, world.kapitel);
  std::string worlds;
  for(auto& w : world.worlds)
    worlds += (worlds.empty() ? "" : ", ") + w;
  std::printf("   worlds read: %s; npcs %zu%s; story variables set %zu (not in the DAT: %zu)\n",
              worlds.empty() ? "none" : worlds.c_str(), world.npcCount(),
              world.heroStandIn ? ", no hero in the save: a stand-in" : "", world.symbolsSet, world.symbolsMissing);
  for(auto& e : world.worldErrors)
    std::printf("   world not read: %s\n", e.c_str());

  // the dialogs to compare, each with its npc; one that is not in the save is built from its instance and
  // stays invalid for the engine (Hlp_IsValidNpc is false, like for an npc that was never inserted)
  struct Check final {
    const Node*                   node;
    zenkit::DaedalusSymbol*       condition;
    std::shared_ptr<zenkit::INpc> self;
    };
  std::vector<Check> checks;
  std::map<uint32_t, std::shared_ptr<zenkit::INpc>> absentNpcs;
  size_t dialogs = 0, notLifted = 0, absent = 0;
  for(auto& n : m.nodes) {
    if(n.choice)
      continue;
    dialogs++;
    auto* infoSym = vm->find_symbol_by_name(n.id);
    if(infoSym==nullptr)
      continue;
    auto info = vm->init_instance<zenkit::IInfo>(infoSym);
    if(info->condition==0)
      continue;  // always offered, nothing to compare
    if(!n.notLifted.empty()) {
      notLifted++;
      continue;
      }
    auto  npcSym = uint32_t(info->npc);
    auto* npc    = world.npc(npcSym);
    std::shared_ptr<zenkit::INpc> self = npc ? npc->script : nullptr;
    if(self==nullptr) {
      absent++;
      if(!absentNpcs.count(npcSym)) {
        auto* s = vm->find_symbol_by_index(npcSym);
        if(s!=nullptr && s->type()==DaedalusDataType::INSTANCE) {
          try {
            absentNpcs[npcSym] = vm->init_instance<zenkit::INpc>(s);
            }
          catch(const std::exception&) {
            }
          }
        }
      self = absentNpcs[npcSym];
      }
    checks.push_back({&n, vm->find_symbol_by_index(uint32_t(info->condition)), self});
    }
  world.resetCounts();  // the instance functions above called the engine too

  Evaluator               ev(*vm, m.helpers, world);
  Snapshot                snap(*vm);
  std::vector<Difference> diffs;
  std::vector<Failure>    unknown, vmErrors;
  size_t checked = 0, agree = 0, trueBoth = 0;
  for(auto& c : checks) {
    auto& n = *c.node;
    vm->global_self()->set_instance(c.self);
    vm->global_other()->set_instance(world.hero);
    world.talkingTo(c.self.get());

    const bool tracing = traceNode!=nullptr && n.id==traceNode;
    if(tracing) {
      std::printf("\n   %s (%s): %s\n     model:\n", ident(n.id).c_str(), ident(n.npc).c_str(),
                  n.condition->str().c_str());
      world.trace = [](const std::string& s){ std::printf("       %s\n", s.c_str()); };
      }

    snap.take();
    auto model = ev.eval(n.condition);
    if(tracing) {
      auto text = model ? model->text(*vm) : "? (" + ev.why() + ")";
      std::printf("     = %s\n     vm:\n", text.c_str());
      }
    int32_t     vmValue = 0;
    std::string vmError;
    try {
      vmValue = vm->call_function<int32_t>(c.condition);
      }
    catch(const std::exception& e) {
      vmError = e.what();
      }
    snap.restore();
    if(tracing) {
      std::printf("     = %s\n\n", vmError.empty() ? std::to_string(vmValue).c_str() : vmError.c_str());
      world.trace = nullptr;
      }

    if(!vmError.empty()) {
      vmErrors.push_back({&n, vmError});
      continue;
      }
    if(!model || model->type!=Value::Int) {
      unknown.push_back({&n, model ? "not an int" : ev.why()});
      continue;
      }
    checked++;
    const bool vmTrue = vmValue!=0, modelTrue = model->i!=0;
    if(vmTrue==modelTrue) {
      agree++;
      trueBoth += vmTrue;
      } else {
      diffs.push_back({&n, vmValue, model->i});
      }
    }

  std::printf("   dialogs %zu: compared %zu, agree %zu, DIFFER %zu; true by both %zu; npc not in the save %zu\n",
              dialogs, checked, agree, diffs.size(), trueBoth, absent);
  std::printf("   not compared: condition not lifted %zu, model could not evaluate %zu, VM failed %zu\n",
              notLifted, unknown.size(), vmErrors.size());
  std::printf("   engine answered from the save: %s\n", top(world.answered, verbose ? 100 : 8).c_str());
  if(!world.defaulted.empty())
    std::printf("   engine not modelled (default answer): %s\n", top(world.defaulted, verbose ? 100 : 8).c_str());
  for(auto& d : diffs)
    std::printf("   DIFF %s (%s): vm %d, model %d\n        %s\n", ident(d.node->id).c_str(), ident(d.node->npc).c_str(),
                d.vm, d.model, shorten(d.node->condition->str(), verbose ? 4000 : 300).c_str());
  for(size_t i=0; i<unknown.size() && (verbose || i<10); ++i)
    std::printf("   model failed %s: %s\n", ident(unknown[i].node->id).c_str(), unknown[i].why.c_str());
  for(size_t i=0; i<vmErrors.size() && (verbose || i<10); ++i)
    std::printf("   vm failed %s: %s\n", ident(vmErrors[i].node->id).c_str(), vmErrors[i].why.c_str());
  std::printf("\n");

  t.saves++;
  t.dialogs   += dialogs;
  t.checked   += checked;
  t.agree     += agree;
  t.differ    += diffs.size();
  t.unknown   += unknown.size();
  t.vmErrors  += vmErrors.size();
  t.notLifted += notLifted;
  t.trueBoth  += trueBoth;
  t.absent    += absent;
  return true;
  }

}

namespace storygraph {

std::unique_ptr<zenkit::DaedalusVm> loadVm(const char* datPath) {
  zenkit::DaedalusScript script;
  auto r = zenkit::Read::from(fs::path(datPath));
  if(r==nullptr)
    return nullptr;
  script.load(r.get());
  zenkit::register_all_script_classes(script);
  return std::make_unique<zenkit::DaedalusVm>(std::move(script),
                                              zenkit::DaedalusVmExecutionFlag::ALLOW_NULL_INSTANCE_ACCESS);
  }

int checkSaves(const char* datPath, const Model& m, const std::vector<std::string>& saves, const char* traceNode,
               bool verbose) {
  // member access on null instances is normal in the scripts and logged by ZenKit on every call
  zenkit::Logger::use_logger([](zenkit::LogLevel, const std::string&){});
  Totals t;
  for(auto& s : saves)
    checkSave(datPath, m, fs::path(s), traceNode, verbose, t);
  std::printf("total: %zu saves, %zu dialog checks: compared %zu, agree %zu, DIFFER %zu; "
              "model could not evaluate %zu, VM failed %zu, not lifted %zu\n",
              t.saves, t.dialogs, t.checked, t.agree, t.differ, t.unknown, t.vmErrors, t.notLifted);
  return t.differ==0 && t.unknown==0 && t.saves==saves.size() ? 0 : 1;
  }

}
