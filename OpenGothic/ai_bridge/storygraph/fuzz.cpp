// Random states for the check of stage 2 (docs/story-graph.md). A save shows each dialog in one state, so
// most branches of its condition never run. Here every condition is computed in many random states: the
// variables and members it reads and the answers of the engine are drawn from the constants it compares
// against and their neighbours. The VM and the model see the same draws, so a difference is again an
// error of the lifting. What is drawn comes from the bytecode, not from the model: an input the lifting
// lost must still vary.

#include "bytecode.h"
#include "check.h"
#include "eval.h"
#include "model.h"
#include "vocab.h"

#include <zenkit/DaedalusVm.hh>
#include <zenkit/Logger.hh>
#include <zenkit/addon/daedalus.hh>

#include <cstdio>
#include <functional>
#include <random>
#include <set>

using namespace storygraph;
using zenkit::DaedalusDataType;

namespace {

// What the draws are made of, for one condition and the script functions it calls.
struct Domain final {
  std::vector<int32_t>     ints;
  std::vector<std::string> strs;
  std::set<std::string>    vars;     // globals and static locals
  std::set<std::string>    members;  // C_NPC members, all elements are drawn
  std::set<std::string>    insts;    // instances and instance variables
  };

void collect(zenkit::DaedalusVm& vm, const Bytecode& code, uint32_t fnSymbol, Domain& d, std::set<uint32_t>& seen,
             std::set<int32_t>& ints, std::set<std::string>& strs) {
  auto* fn = vm.find_symbol_by_index(fnSymbol);
  if(fn==nullptr || !Bytecode::hasCode(*fn) || !seen.insert(fnSymbol).second)
    return;
  auto f = code.summarize(fnSymbol);
  d.vars.insert(f.reads.begin(), f.reads.end());
  d.insts.insert(f.instances.begin(), f.instances.end());
  for(auto& m : f.members)
    d.members.insert(m.substr(m.find('.')+1));
  // static locals: a condition may read what an earlier call left there
  for(auto& l : vm.find_locals_for_function(fn))
    d.vars.insert(l.name());
  // the constants it compares against, and their neighbours
  for(uint32_t pc=fn->address(), end=code.codeEnd(pc); pc<end;) {
    auto  in = vm.instruction_at(pc);
    auto* s  = vm.find_symbol_by_index(in.symbol);
    if(in.op==zenkit::DaedalusOpcode::PUSHI) {
      for(int32_t k : {-1, 0, 1})
        ints.insert(int32_t(uint32_t(in.immediate) + uint32_t(k)));
      }
    else if((in.op==zenkit::DaedalusOpcode::PUSHV || in.op==zenkit::DaedalusOpcode::PUSHVV) && s && s->is_const()) {
      auto i = uint16_t(in.op==zenkit::DaedalusOpcode::PUSHVV ? in.index : 0);
      if(s->type()==DaedalusDataType::INT && i<s->count())
        for(int32_t k : {-1, 0, 1})
          ints.insert(int32_t(uint32_t(s->get_int(i)) + uint32_t(k)));
      else if(s->type()==DaedalusDataType::STRING && i<s->count())
        strs.insert(s->get_string(i));
      }
    pc += in.size;
    }
  for(auto& c : f.calls)
    if(auto* callee = vm.find_symbol_by_name(c))
      collect(vm, code, callee->index(), d, seen, ints, strs);
  }

Domain domainOf(zenkit::DaedalusVm& vm, const Bytecode& code, uint32_t condition) {
  Domain                d;
  std::set<uint32_t>    seen;
  std::set<int32_t>     ints = {-1, 0, 1, 2};
  std::set<std::string> strs = {"", "X"};
  collect(vm, code, condition, d, seen, ints, strs);
  d.ints.assign(ints.begin(), ints.end());
  d.strs.assign(strs.begin(), strs.end());
  return d;
  }

// The engine answers at random, the same answer for the same question within a round.
class FuzzWorld final : public Externals {
  public:
    explicit FuzzWorld(zenkit::DaedalusVm& vm) : vm(vm) {}

    Value call(const zenkit::DaedalusSymbol& fn, const std::vector<Value>& args) override {
      if(!fn.has_return())
        return {};
      if(setup || domain==nullptr)
        return fn.name()=="HLP_GETNPC" ? Value::instance(npc(uint32_t(args[0].i))) : Value();
      std::string key = fn.name() + "(";
      for(size_t i=0; i<args.size(); ++i) {
        auto& a = args[i];
        key += i ? "," : "";
        switch(a.type) {
          case Value::Int:   key += std::to_string(a.i); break;
          case Value::Float: key += std::to_string(a.f); break;
          case Value::Str:   key += "\"" + a.s + "\""; break;
          case Value::Inst:  key += a.inst ? "#" + std::to_string(a.inst->symbol_index()) : "null"; break;
          }
        }
      key += ")";
      auto it = memo.find(key);
      if(it!=memo.end())
        return it->second;
      Value v;
      switch(fn.rtype()) {
        case DaedalusDataType::STRING:
          v = Value::str(str());
          break;
        case DaedalusDataType::INSTANCE:
          if(fn.name()=="HLP_GETNPC")
            v = Value::instance(rng()%4==0 ? nullptr : npc(uint32_t(args[0].i)));
          else
            v = Value::instance(rng()%3==0 ? nullptr : rng()%2 ? hero : self);
          break;
        case DaedalusDataType::FLOAT:
          v = Value::real(0);
          break;
        default:
          // predicates answer TRUE or FALSE: `pred == TRUE` is folded to `pred` on that promise
          v = Value::integer(vocab::BoolExternals.count(fn.name()) ? int32_t(rng()%2) : num());
          break;
        }
      return memo[key] = v;
      }

    // an npc built from its instance, one per instance
    std::shared_ptr<zenkit::INpc> npc(uint32_t symbol) {
      auto it = npcs.find(symbol);
      if(it!=npcs.end())
        return it->second;
      npcs[symbol] = nullptr;  // an instance function asking for itself
      std::shared_ptr<zenkit::INpc> r;
      auto* sym = vm.find_symbol_by_index(symbol);
      if(sym!=nullptr && sym->type()==DaedalusDataType::INSTANCE) {
        bool was = setup;
        setup = true;
        try {
          r = vm.init_instance<zenkit::INpc>(sym);
          }
        catch(const std::exception&) {
          r = nullptr;
          }
        setup = was;
        }
      return npcs[symbol] = r;
      }

    // flags are TRUE or FALSE most of the time, so 0 and 1 come up often; the rest is the constants
    int32_t num() {
      auto r = rng()%10;
      return r<4 ? 0 : r<6 ? 1 : domain->ints[rng()%domain->ints.size()];
      }
    std::string str() {
      return rng()%2 ? std::string() : domain->strs[rng()%domain->strs.size()];
      }

    zenkit::DaedalusVm&                                         vm;
    std::mt19937                                                rng;
    const Domain*                                               domain = nullptr;
    std::map<std::string,Value>                                 memo;
    std::shared_ptr<zenkit::INpc>                               hero, self;
    bool                                                        setup = true;

  private:
    std::unordered_map<uint32_t,std::shared_ptr<zenkit::INpc>>  npcs;
  };

struct Mismatch final {
  const Node* node;
  int32_t     vm, model;
  std::string state;
  };

}

namespace storygraph {

int fuzzConditions(const char* datPath, const Model& m, uint32_t rounds, const char* only, bool verbose) {
  zenkit::Logger::use_logger([](zenkit::LogLevel, const std::string&){});
  auto vm = loadVm(datPath);
  if(vm==nullptr) {
    std::fprintf(stderr, "can't open %s\n", datPath);
    return 1;
    }
  FuzzWorld world(*vm);
  bindExternals(*vm, world);
  std::string vmFault;
  vm->register_exception_handler([&vmFault](zenkit::DaedalusVm&, const zenkit::DaedalusScriptError& e,
                                            const zenkit::DaedalusInstruction&) {
    if(vmFault.empty())
      vmFault = e.what();
    return zenkit::DaedalusVmExceptionStrategy::RETURN;
    });
  if(auto* h = vm->find_symbol_by_name("PC_HERO"))
    world.hero = world.npc(h->index());
  vm->global_hero()->set_instance(world.hero);
  if(auto* init = vm->find_symbol_by_name("B_INITNPCGLOBALS"))
    vm->call_function(init);
  world.setup = false;

  Bytecode              code(*vm);
  Evaluator             ev(*vm, m.helpers, world);
  std::vector<Mismatch> found;
  size_t dialogs = 0, runs = 0, vmFaults = 0, modelFails = 0, bothTrue = 0, bothFalse = 0;
  std::map<std::string,size_t> failWhy;

  for(auto& n : m.nodes) {
    world.domain = nullptr;  // the previous dialog's is gone
    if(n.choice || !n.notLifted.empty() || (only!=nullptr && n.id!=only))
      continue;
    auto* infoSym = vm->find_symbol_by_name(n.id);
    if(infoSym==nullptr)
      continue;
    auto info = vm->init_instance<zenkit::IInfo>(infoSym);
    if(info->condition==0)
      continue;
    auto* cond = vm->find_symbol_by_index(uint32_t(info->condition));
    dialogs++;

    Domain d = domainOf(*vm, code, uint32_t(info->condition));
    world.domain = &d;
    world.self   = world.npc(uint32_t(info->npc));
    world.rng.seed(uint32_t(std::hash<std::string>()(n.id)));

    // instances whose members are drawn: the npc, the hero and the instance variables the condition reads
    std::vector<std::shared_ptr<zenkit::DaedalusInstance>> owners = {world.self, world.hero};
    for(auto& name : d.insts)
      if(auto* s = vm->find_symbol_by_name(name); s && s->type()==DaedalusDataType::INSTANCE && s->get_instance() &&
         !s->is_const())
        owners.push_back(s->get_instance());

    for(uint32_t r=0; r<rounds; ++r) {
      world.memo.clear();
      for(auto& v : d.vars) {
        auto* s = vm->find_symbol_by_name(v);
        if(s==nullptr || s->is_const() || s->is_member())
          continue;
        for(uint16_t i=0; i<s->count(); ++i) {
          if(s->type()==DaedalusDataType::INT)
            s->set_int(world.num(), i);
          else if(s->type()==DaedalusDataType::STRING)
            s->set_string(world.str(), i);
          }
        }
      for(auto& o : owners) {
        if(o==nullptr)
          continue;
        for(auto& name : d.members) {
          auto* mem = member(*vm, *o, name);
          if(mem==nullptr)
            continue;
          for(uint16_t i=0; i<mem->count(); ++i) {
            if(mem->type()==DaedalusDataType::INT)
              mem->set_int(world.num(), i, o.get());
            else if(mem->type()==DaedalusDataType::STRING)
              mem->set_string(world.str(), i, o.get());
            }
          }
        }
      vm->global_self()->set_instance(world.self);
      vm->global_other()->set_instance(world.hero);
      vm->global_hero()->set_instance(world.hero);

      auto model = ev.eval(n.condition);
      vmFault.clear();
      int32_t vmValue = 0;
      try {
        vmValue = vm->call_function<int32_t>(cond);
        }
      catch(const std::exception& e) {
        vmFault = e.what();
        }
      runs++;
      if(!vmFault.empty()) {
        vmFaults++;
        continue;
        }
      if(!model || model->type!=Value::Int) {
        modelFails++;
        failWhy[model ? "not an int" : ev.why()]++;
        continue;
        }
      if((vmValue!=0)==(model->i!=0)) {
        (vmValue!=0 ? bothTrue : bothFalse)++;
        continue;
        }
      // the draws the condition saw: globals it reads, members of the npc and the hero it reads, engine answers
      std::string state;
      for(auto& v : d.vars)
        if(auto* s = vm->find_symbol_by_name(v); s && s->type()==DaedalusDataType::INT && s->count()==1 &&
           v.find('.')==std::string::npos)
          state += " " + ident(v) + "=" + std::to_string(s->get_int(0));
      ex::walk(n.condition, [&](const Expr& x) {
        if(x.kind!=Expr::Field || x.args[0]->kind!=Expr::Inst)
          return;
        auto inst = vm->find_symbol_by_name(x.args[0]->name);
        auto* mem = inst && inst->get_instance() ? member(*vm, *inst->get_instance(), x.name) : nullptr;
        auto  i   = uint16_t(std::max(x.index, 0));
        if(mem && mem->type()==DaedalusDataType::INT && i<mem->count())
          state += " " + x.str() + "=" + std::to_string(mem->get_int(i, inst->get_instance().get()));
        });
      for(auto& [k,v] : world.memo)
        state += " " + ident(k) + "=" + v.text(*vm);
      found.push_back({&n, vmValue, model->i, state});
      break;  // one is enough to look at
      }
    }

  std::printf("random states: %zu dialogs x %u rounds = %zu runs; agree true %zu, false %zu; DIFFER in %zu dialogs\n",
              dialogs, rounds, runs, bothTrue, bothFalse, found.size());
  std::printf("not compared: VM failed %zu (division by zero and such), model could not evaluate %zu\n",
              vmFaults, modelFails);
  for(auto& [why,c] : failWhy)
    std::printf("   model: %zu x %s\n", c, why.c_str());
  for(size_t i=0; i<found.size() && (verbose || i<20); ++i) {
    auto& f = found[i];
    std::printf("DIFF %s (%s): vm %d, model %d\n   if %s\n   state:%s\n", ident(f.node->id).c_str(),
                ident(f.node->npc).c_str(), f.vm, f.model, f.node->condition->str().c_str(), f.state.c_str());
    }
  if(found.size()>20 && !verbose)
    std::printf("... %zu more (--verbose)\n", found.size()-20);
  return found.empty() && modelFails==0 ? 0 : 1;
  }

}
