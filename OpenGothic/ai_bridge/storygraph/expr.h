#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace storygraph {

struct Expr;
using ExprP = std::shared_ptr<const Expr>;

// An expression lifted from Daedalus bytecode. Names are script symbols as stored in the DAT (uppercase,
// Windows-1252), strings are in the game codepage. Expressions are immutable; `key` is computed once and
// is equal for equal expressions.
struct Expr final {
  enum Kind : uint8_t {
    Int,      // value; name: the constant it came from (LOG_RUNNING), may be empty
    Str,      // text; name: the constant it came from (TOPIC_*), may be empty
    Var,      // name: global variable; index: array element or -1
    Field,    // args[0]: instance; name: member (GUILD, AIVAR); index: array element or -1; text: its constant
    Inst,     // name: instance variable (SELF, OTHER, HERO, HILDA)
    Sym,      // name: a symbol passed by index (dialog, item, npc, function)
    Param,    // index: parameter of the function being lifted
    Local,    // name: local variable of the function being lifted; never left in results
    Call,     // name: engine function; args
    Fn,       // name: script function; args
    Op,       // name: ==, !=, <, >, <=, >=, &&, ||, !, +, -, *, /, %, &, |, <<, >>, neg; args
    Cases,    // args: guard, value, guard, value...; the first true guard picks the value, otherwise 0
    Unknown,  // text: why the value is not known
    };

  Kind               kind  = Unknown;
  int32_t            value = 0;
  int32_t            index = -1;
  std::string        name;
  std::string        text;
  std::vector<ExprP> args;
  std::string        key;
  uint32_t           size  = 1;  // nodes in the tree, as written out (shared subtrees counted each time)

  bool isInt(int32_t v) const { return kind==Int && value==v; }
  bool isTrue()  const { return kind==Int && value!=0; }
  bool isFalse() const { return isInt(0); }
  bool isBool()  const;  // a comparison, a logical operator or a predicate that returns TRUE/FALSE
  // readable text, UTF-8
  std::string str() const;
  };

namespace ex {
  ExprP integer(int32_t v, std::string constName = {});
  ExprP str(std::string text, std::string constName = {});
  ExprP var(std::string name, int32_t index = -1);
  ExprP field(ExprP inst, std::string member, int32_t index = -1, std::string indexName = {});
  ExprP inst(std::string name);
  ExprP sym(std::string name);
  ExprP param(int32_t i);
  ExprP local(std::string name);
  ExprP call(std::string name, std::vector<ExprP> args);
  ExprP fn(std::string name, std::vector<ExprP> args);
  ExprP unknown(std::string why);
  ExprP cases(std::vector<ExprP> guardsAndValues);

  // these fold constants and keep && and || flat
  ExprP op(const std::string& o, std::vector<ExprP> args);
  ExprP lnot(const ExprP& a);
  ExprP land(std::vector<ExprP> terms);
  ExprP lor(std::vector<ExprP> terms);
  // an operator as the bytecode computes it: nothing folded, flattened or dropped (programs, program.h)
  ExprP exact(const std::string& o, std::vector<ExprP> args);

  // true when `a` contains an Unknown
  bool  opaque(const ExprP& a);
  // calls every subexpression, `a` included
  template<class F>
  void  walk(const ExprP& a, const F& f) {
    if(a==nullptr)
      return;
    f(*a);
    for(auto& x : a->args)
      walk(x, f);
    }
  }

// Windows-1252 symbol name -> UTF-8
std::string ident(const std::string& name);

}
