#include "bytecode.h"

#include <zenkit/DaedalusScript.hh>

#include <algorithm>
#include <cstdio>

using namespace storygraph;
using zenkit::DaedalusDataType;
using zenkit::DaedalusOpcode;

static bool hasCode(const zenkit::DaedalusSymbol& s) {
  if(s.is_external() || s.address()==0)
    return false;
  auto t = s.type();
  return t==DaedalusDataType::FUNCTION || t==DaedalusDataType::INSTANCE || t==DaedalusDataType::PROTOTYPE;
  }

static bool isStore(DaedalusOpcode op) {
  switch(op) {
    case DaedalusOpcode::MOVI:
    case DaedalusOpcode::ADDMOVI:
    case DaedalusOpcode::SUBMOVI:
    case DaedalusOpcode::MULMOVI:
    case DaedalusOpcode::DIVMOVI:
    case DaedalusOpcode::MOVS:
    case DaedalusOpcode::MOVSS:
    case DaedalusOpcode::MOVVF:
    case DaedalusOpcode::MOVF:
    case DaedalusOpcode::MOVVI:
      return true;
    default:
      return false;
    }
  }

Bytecode::Bytecode(const zenkit::DaedalusScript& script)
  :script(script) {
  for(auto& s : script.symbols()) {
    if(hasCode(s)) {
      starts.push_back(s.address());
      if(s.type()==DaedalusDataType::FUNCTION)
        fnByAddress.emplace(s.address(), s.index());
      }
    }
  std::sort(starts.begin(), starts.end());
  starts.erase(std::unique(starts.begin(), starts.end()), starts.end());

  if(auto* c = script.find_symbol_by_name("C_INFO"))
    infoClass = c->index();
  }

uint32_t Bytecode::codeEnd(uint32_t address) const {
  auto it = std::upper_bound(starts.begin(), starts.end(), address);
  return it==starts.end() ? script.size() : *it;
  }

bool Bytecode::isInfo(const zenkit::DaedalusSymbol& sym) const {
  // instance -> prototype -> class, or instance -> class
  auto* s = &sym;
  for(int depth=0; depth<3 && s!=nullptr; ++depth) {
    if(s->index()==infoClass)
      return depth>0;
    s = script.find_symbol_by_index(s->parent());
    }
  return false;
  }

bool Bytecode::print(const std::string& fnName) const {
  auto* fn = script.find_symbol_by_name(fnName);
  if(fn==nullptr || !hasCode(*fn))
    return false;
  for(uint32_t pc=fn->address(), end=codeEnd(pc); pc<end;) {
    auto  in = script.instruction_at(pc);
    auto* s  = script.find_symbol_by_index(in.symbol);
    std::printf("%8u  op=%-3d imm=%-6d addr=%-8u sym=%s%s\n", pc, int(in.op), in.immediate, in.address,
                s ? s->name().c_str() : "-", s && s->is_const() ? " (const)" : "");
    pc += in.size;
    }
  return true;
  }

// An operand on the symbolic stack: what an instruction pushed, or the result of an expression.
namespace {
struct Operand final {
  enum Kind : uint8_t { Imm, Var, Inst, Expr };
  Kind     kind  = Expr;
  int32_t  value = 0;  // Imm: the immediate (a symbol index when the parameter is an instance or function)
  uint32_t sym   = 0;  // Var, Inst: the symbol
  };
}

// Engine functions that take an instance as a plain int (`var int`): argument positions.
static const std::unordered_map<std::string, std::vector<uint32_t>> InstanceAsInt = {
  {"NPC_KNOWSINFO",      {1}}, {"NPC_HASITEMS",      {1}}, {"MOB_HASITEMS", {1}}, {"HLP_ISITEM", {1}},
  {"CREATEINVITEMS",     {1}}, {"CREATEINVITEM",     {1}}, {"NPC_REMOVEINVITEMS", {1}},
  {"NPC_REMOVEINVITEM",  {1}}, {"WLD_INSERTITEM",    {0}}, {"WLD_INSERTNPC", {0}}, {"HLP_GETNPC", {0}},
  };

static bool isBinary(DaedalusOpcode op) {
  auto v = uint8_t(op);
  return v<=uint8_t(DaedalusOpcode::GT) || (v>=uint8_t(DaedalusOpcode::ORR) && v<=uint8_t(DaedalusOpcode::GTE));
  }

static bool isUnary(DaedalusOpcode op) {
  return op==DaedalusOpcode::PLUS || op==DaedalusOpcode::NEGATE || op==DaedalusOpcode::NOT || op==DaedalusOpcode::CMPL;
  }

FnSummary Bytecode::summarize(uint32_t fnSymbol) const {
  FnSummary f;
  auto* fn = script.find_symbol_by_index(fnSymbol);
  if(fn==nullptr || !hasCode(*fn))
    return f;

  std::vector<Operand> stack;
  auto pop = [&stack]() {
    Operand o;
    if(!stack.empty()) {
      o = stack.back();
      stack.pop_back();
      }
    return o;
    };
  auto addInstance = [&](const zenkit::DaedalusSymbol* s) {
    if(s==nullptr)
      return;
    if(isInfo(*s))
      f.infos.insert(s->name());
    else
      f.instances.insert(s->name());
    };
  // pops the arguments of a call; instances and functions are passed as PUSHI <symbol index>
  auto call = [&](const zenkit::DaedalusSymbol& callee) {
    std::vector<Operand> args(callee.count());
    for(size_t i=args.size(); i>0; --i)
      args[i-1] = pop();
    auto asInt = InstanceAsInt.find(callee.name());
    for(uint32_t i=0; i<args.size(); ++i) {
      auto* par = script.find_symbol_by_index(callee.index()+1+i);
      auto& a   = args[i];
      if(par==nullptr || (a.kind!=Operand::Imm && a.kind!=Operand::Inst))
        continue;
      auto* arg = script.find_symbol_by_index(a.kind==Operand::Imm ? uint32_t(a.value) : a.sym);
      bool instance = par->type()==DaedalusDataType::INSTANCE ||
                      (asInt!=InstanceAsInt.end() &&
                       std::find(asInt->second.begin(), asInt->second.end(), i)!=asInt->second.end());
      if(instance)
        addInstance(arg);
      else if(par->type()==DaedalusDataType::FUNCTION && a.kind==Operand::Imm && callee.name()=="INFO_ADDCHOICE")
        f.choices.push_back(uint32_t(a.value));
      }
    if(callee.has_return())
      stack.push_back(Operand{});
    };

  for(uint32_t pc=fn->address(), end=codeEnd(pc); pc<end;) {
    auto  in = script.instruction_at(pc);
    auto* s  = script.find_symbol_by_index(in.symbol);
    auto  op = in.op;
    if(op==DaedalusOpcode::BE) {
      if(s!=nullptr) {
        f.externals.insert(s->name());
        if(s->name()=="AI_OUTPUT")
          f.outputs++;
        call(*s);
        }
      }
    else if(op==DaedalusOpcode::BL) {
      auto it = fnByAddress.find(in.address);
      if(it!=fnByAddress.end()) {
        auto* callee = script.find_symbol_by_index(it->second);
        f.calls.insert(callee->name());
        call(*callee);
        }
      }
    else if(op==DaedalusOpcode::PUSHI) {
      stack.push_back(Operand{Operand::Imm, in.immediate, 0});
      }
    else if(op==DaedalusOpcode::PUSHV || op==DaedalusOpcode::PUSHVV) {
      stack.push_back(Operand{Operand::Var, 0, in.symbol});
      if(s!=nullptr && !s->is_const()) {
        // an assignment pushes its target right before the store
        bool store = isStore(script.instruction_at(pc+in.size).op);
        if(s->is_member())
          f.members.insert(s->name());
        else if(s->name().find('.')!=std::string::npos)
          ;  // local variable or parameter
        else if(store)
          f.writes.insert(s->name());
        else if(s->type()!=DaedalusDataType::FUNCTION)
          f.reads.insert(s->name());
        }
      }
    else if(op==DaedalusOpcode::PUSHVI) {
      stack.push_back(Operand{Operand::Inst, 0, in.symbol});
      addInstance(s);
      }
    else if(op==DaedalusOpcode::GMOVI) {
      addInstance(s);  // the instance whose member comes next
      }
    else if(isStore(op) || isBinary(op)) {
      pop();
      pop();
      if(isBinary(op))
        stack.push_back(Operand{});
      }
    else if(isUnary(op)) {
      pop();
      stack.push_back(Operand{});
      }
    else if(op==DaedalusOpcode::BZ) {
      pop();
      }
    else if(op==DaedalusOpcode::RSR) {
      stack.clear();
      }
    pc += in.size;
    }
  return f;
  }
