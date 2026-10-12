#pragma once

#include "expr.h"

#include <cstdint>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace zenkit {
  class DaedalusScript;
  class DaedalusSymbol;
  }

namespace storygraph {

class Bytecode;

// A guard: when an instruction runs, as a disjunction of conjunctions of literals.
// Empty = never; one empty conjunction = always. Too many conjunctions make it unknown.
class Dnf final {
  public:
    static Dnf always();
    static Dnf never() { return Dnf(); }
    static Dnf unknown();

    bool   isNever()   const { return !unknownFlag && terms.empty(); }
    bool   isAlways()  const { return !unknownFlag && terms.size()==1 && terms[0].empty(); }
    bool   isUnknown() const { return unknownFlag; }

    // `cond` is split on && and || (and on their negations), so branches can merge back later
    Dnf    operator&&(const ExprP& cond) const;
    Dnf    operator||(const Dnf& other)  const;
    ExprP  expr() const;

  private:
    using Conj = std::vector<ExprP>;  // sorted by key, no duplicates
    static constexpr size_t MaxTerms = 160;

    Dnf    andLiteral(const ExprP& lit) const;
    void   simplify();

    std::vector<Conj> terms;
    bool              unknownFlag = false;
  };

// One call or one store in a lifted function, with the guard it runs under.
struct Event final {
  enum Kind : uint8_t { Call, Store };
  Kind                          kind   = Call;
  uint32_t                      pc     = 0;
  const zenkit::DaedalusSymbol* callee = nullptr;  // Call
  std::vector<ExprP>            args;              // Call: arguments, instances and functions as Sym
  ExprP                         target;            // Store: Var, Field, Inst (instance variable) or Local
  ExprP                         value;             // Store
  Dnf                           guard;
  };

struct Lifted final {
  std::string                        error;    // not empty: control flow not understood
  std::vector<std::pair<Dnf,ExprP>>  returns;  // guard and returned value (nullptr in void functions)
  std::vector<Event>                 events;   // in code order
  };

// Symbolic execution of a Daedalus function. Daedalus has no loops, so the control flow is a DAG:
// instructions are visited in address order and states are merged where branches meet.
class Lifter final {
  public:
    Lifter(const zenkit::DaedalusScript& script, const Bytecode& code);

    Lifted       lift(uint32_t fnSymbol) const;

    // a function that returns TRUE/FALSE as one boolean expression
    static ExprP condition(const Lifted& l);
    // the returned value: a boolean expression if every return is boolean, Cases otherwise
    static ExprP value(const Lifted& l);

  private:
    static constexpr uint32_t MaxLocalSize = 256;  // nodes in a local's value merged from branches
    static constexpr uint32_t MaxCondSize  = 512;  // nodes in a branch condition
    struct State;
    ExprP  resolve(const State& s, const ExprP& e) const;
    ExprP  pushVar(const State& s, const zenkit::DaedalusSymbol& sym, int32_t index) const;
    void   merge(State& into, State&& from) const;
    ExprP  mergeValues(const Dnf& ga, const ExprP& va, const Dnf& gb, const ExprP& vb, const std::string& local) const;

    const zenkit::DaedalusScript&                script;
    const Bytecode&                              code;
    // constants naming the indices of array members, by member: AIVAR -> {value -> AIV_*}
    std::unordered_map<std::string, std::unordered_map<int32_t, std::string>> indexNames;
  };

}
