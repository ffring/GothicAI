#pragma once

#include "expr.h"

#include <cstdint>
#include <string>
#include <vector>

namespace zenkit {
  class DaedalusScript;
  }

namespace storygraph {

class Bytecode;

// One step of a program: a call, a store, a branch or a return.
struct Step final {
  enum Kind : uint8_t { Call, Store, If, Return };
  Kind              kind = Call;
  uint32_t          pc   = 0;
  ExprP             call;              // Call: a call, or an expression with calls; its value is dropped
  ExprP             target;            // Store: Var, Field or Inst
  ExprP             value;             // Store: what is stored (`x += v` is `x = x + v`); Return: nullptr in void
  ExprP             cond;              // If
  std::vector<Step> then, otherwise;   // If
  };

// A script function as the graph runs it (docs/story-graph.md, «Исполнение без VM: расчёт»). Unlike the lifted
// model (lifter.h), nothing is carried from the start of the function: steps keep the order of the bytecode and
// its branches, and an expression reads the state when its step runs, as the VM does. So a dialog that gives
// gold and then counts it sees the gold.
//
// Expressions are built as the bytecode computes them, without folding: every call is evaluated once, where
// the VM calls it. The operands of an operator are computed last to first (the VM pushes the right one first),
// the arguments of a call first to last. A variable is read when its value is taken, not when it is named: the
// VM pushes a reference (and a script function may return one, see Interpreter). Static locals and parameters
// are variables named FN.NAME, read and written like globals; the prologue stores the arguments (Expr::Param)
// into the parameters.
struct Program final {
  std::string       error;        // not empty: the bytecode was not understood, the VM runs this function
  bool              returns = false;
  std::vector<Step> steps;
  };

Program     liftProgram(const zenkit::DaedalusScript& script, const Bytecode& code, uint32_t fnSymbol);

// readable, UTF-8: one step per line, branches indented
std::string describe(const Program& p);
// steps as JSON: {"do":"call","call":<expr>}, {"do":"set","target":..,"value":..}, {"do":"if","cond":..,"then":[..],
// "else":[..]}, {"do":"return","value":..}; expressions as in the model (toJson(ExprP))
std::string toJson(const Program& p);

}
