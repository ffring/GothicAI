#include "expr.h"

#include "textcodec.h"
#include "vocab.h"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <set>

using namespace storygraph;

static bool isComparison(const std::string& o) {
  return o=="==" || o=="!=" || o=="<" || o==">" || o=="<=" || o==">=";
  }

static bool commutative(const std::string& o) {
  return o=="&&" || o=="||" || o=="==" || o=="!=" || o=="+" || o=="*" || o=="&" || o=="|";
  }

static ExprP make(Expr&& e) {
  std::string k;
  switch(e.kind) {
    case Expr::Int:     k = std::to_string(e.value); break;
    case Expr::Str:     k = "\"" + e.text + "\""; break;
    case Expr::Var:     k = "v:" + e.name; break;
    case Expr::Field:   k = "f:" + e.args[0]->key + "." + e.name; break;
    case Expr::Inst:    k = "n:" + e.name; break;
    case Expr::Sym:     k = "y:" + e.name; break;
    case Expr::Param:   k = "p:" + std::to_string(e.index); break;
    case Expr::Local:   k = "l:" + e.name; break;
    case Expr::Call:    k = "c:" + e.name; break;
    case Expr::Fn:      k = "F:" + e.name; break;
    case Expr::Op:      k = "o:" + e.name; break;
    case Expr::Cases:   k = "k:"; break;
    case Expr::Unknown: {
      static std::atomic<uint32_t> next{0};
      k = "u:" + std::to_string(next++);  // unknown values are never equal to each other
      break;
      }
    }
  if(e.index>=0 && e.kind!=Expr::Param)
    k += "[" + std::to_string(e.index) + "]";
  if(e.kind==Expr::Call || e.kind==Expr::Fn || e.kind==Expr::Op || e.kind==Expr::Cases) {
    std::vector<std::string> keys;
    for(auto& a : e.args)
      keys.push_back(a->key);
    if(e.kind==Expr::Op && commutative(e.name))
      std::sort(keys.begin(), keys.end());
    k += "(";
    for(size_t i=0; i<keys.size(); ++i)
      k += (i ? "," : "") + keys[i];
    k += ")";
    }
  e.key  = std::move(k);
  e.size = 1;
  for(auto& a : e.args)
    e.size = std::min<uint32_t>(e.size + a->size, 1u<<30);
  return std::make_shared<const Expr>(std::move(e));
  }

static ExprP make(Expr::Kind kind, std::string name = {}, std::vector<ExprP> args = {}) {
  Expr e;
  e.kind = kind;
  e.name = std::move(name);
  e.args = std::move(args);
  return make(std::move(e));
  }

bool Expr::isBool() const {
  if(kind==Op)
    return isComparison(name) || name=="&&" || name=="||" || name=="!";
  if(kind==Call)
    return vocab::BoolExternals.count(name)>0;
  return false;
  }

std::string storygraph::ident(const std::string& name) {
  std::string r;
  for(char ch : name) {
    auto c = uint8_t(ch);
    if(c<0x80) {
      r += ch;
      } else {
      r += char(0xC0 | (c>>6));
      r += char(0x80 | (c&0x3F));
      }
    }
  return r;
  }

static std::string lower(std::string s) {
  for(auto& c : s)
    c = char(std::tolower(uint8_t(c)));
  return s;
  }

std::string Expr::str() const {
  auto wrap = [](const ExprP& a) {
    bool paren = (a->kind==Op && a->name!="!" && a->name!="neg") || a->kind==Cases;
    return paren ? "(" + a->str() + ")" : a->str();
    };
  auto arg = [this,&wrap](size_t i) { return wrap(args[i]); };
  auto list = [this]() {
    std::string r;
    for(size_t i=0; i<args.size(); ++i)
      r += (i ? ", " : "") + args[i]->str();
    return r;
    };
  switch(kind) {
    case Int:
      return name.empty() ? std::to_string(value) : ident(name);
    case Str:
      return "\"" + TextCodec::toUtf8(text) + "\"";
    case Var:
      return ident(name) + (index>=0 ? "[" + std::to_string(index) + "]" : "");
    case Field: {
      std::string idx = index<0 ? "" : "[" + (text.empty() ? std::to_string(index) : ident(text)) + "]";
      return args[0]->str() + "." + lower(name) + idx;
      }
    case Inst:
      return (name=="SELF" || name=="OTHER" || name=="HERO") ? lower(name) : ident(name);
    case Sym:
      return ident(name);
    case Param:
      return "$" + std::to_string(index);
    case Local:
      return "local " + ident(name);
    case Call:
    case Fn:
      return ident(name) + "(" + list() + ")";
    case Op: {
      if(name=="!")
        return "!" + arg(0);
      if(name=="neg")
        return "-" + arg(0);
      std::string r;
      for(size_t i=0; i<args.size(); ++i)
        r += (i ? " " + name + " " : "") + arg(i);
      return r;
      }
    case Cases: {
      std::string r = "case ";
      for(size_t i=0; i+1<args.size(); i+=2)
        r += (i ? "; " : "") + wrap(args[i]) + " -> " + wrap(args[i+1]);
      return r + "; else 0";
      }
    case Unknown:
      return "?" + text;
    }
  return "?";
  }

ExprP ex::integer(int32_t v, std::string constName) {
  Expr e;
  e.kind  = Expr::Int;
  e.value = v;
  e.name  = std::move(constName);
  return make(std::move(e));
  }

ExprP ex::str(std::string text, std::string constName) {
  Expr e;
  e.kind = Expr::Str;
  e.text = std::move(text);
  e.name = std::move(constName);
  return make(std::move(e));
  }

ExprP ex::var(std::string name, int32_t index) {
  Expr e;
  e.kind  = Expr::Var;
  e.name  = std::move(name);
  e.index = index;
  return make(std::move(e));
  }

ExprP ex::field(ExprP inst, std::string member, int32_t index, std::string indexName) {
  Expr e;
  e.kind  = Expr::Field;
  e.name  = std::move(member);
  e.index = index;
  e.text  = std::move(indexName);
  e.args  = {std::move(inst)};
  return make(std::move(e));
  }

ExprP ex::inst(std::string name) { return make(Expr::Inst, std::move(name)); }
ExprP ex::sym (std::string name) { return make(Expr::Sym,  std::move(name)); }
ExprP ex::local(std::string name) { return make(Expr::Local, std::move(name)); }

ExprP ex::param(int32_t i) {
  Expr e;
  e.kind  = Expr::Param;
  e.index = i;
  return make(std::move(e));
  }

ExprP ex::call(std::string name, std::vector<ExprP> args) { return make(Expr::Call, std::move(name), std::move(args)); }
ExprP ex::fn  (std::string name, std::vector<ExprP> args) { return make(Expr::Fn,   std::move(name), std::move(args)); }
ExprP ex::cases(std::vector<ExprP> gv)                    { return make(Expr::Cases, {}, std::move(gv)); }

ExprP ex::unknown(std::string why) {
  Expr e;
  e.kind = Expr::Unknown;
  e.text = std::move(why);
  return make(std::move(e));
  }

static int32_t fold(const std::string& o, int32_t a, int32_t b, bool& ok) {
  ok = true;
  // wrap like the VM does instead of overflowing
  auto ua = uint32_t(a), ub = uint32_t(b);
  if(o=="+")  return int32_t(ua+ub);
  if(o=="-")  return int32_t(ua-ub);
  if(o=="*")  return int32_t(ua*ub);
  if(o=="/" && b!=0) return a/b;
  if(o=="%" && b!=0) return a%b;
  if(o=="&")  return a & b;
  if(o=="|")  return a | b;
  if(o=="<<") return int32_t(ua << (ub & 31));
  if(o==">>") return a >> (b & 31);
  if(o=="==") return a==b;
  if(o=="!=") return a!=b;
  if(o=="<")  return a<b;
  if(o==">")  return a>b;
  if(o=="<=") return a<=b;
  if(o==">=") return a>=b;
  ok = false;
  return 0;
  }

ExprP ex::op(const std::string& o, std::vector<ExprP> args) {
  if(o=="&&")
    return land(std::move(args));
  if(o=="||")
    return lor(std::move(args));
  if(o=="!")
    return lnot(args[0]);
  if(o=="neg" && args[0]->kind==Expr::Int)
    return integer(int32_t(0u-uint32_t(args[0]->value)));
  if(args.size()==2) {
    auto& a = args[0];
    auto& b = args[1];
    if(a->kind==Expr::Int && b->kind==Expr::Int) {
      bool ok   = false;
      auto v    = fold(o, a->value, b->value, ok);
      if(ok)
        return integer(v);
      }
    // predicate == FALSE -> !predicate, predicate == TRUE -> predicate
    if(o=="==" || o=="!=") {
      const ExprP* pred = a->isBool() && (b->isInt(0) || b->isInt(1)) ? &a :
                          b->isBool() && (a->isInt(0) || a->isInt(1)) ? &b : nullptr;
      if(pred!=nullptr) {
        auto& c       = (pred==&a) ? b : a;
        bool  truthy  = (o=="==") == (c->value==1);
        return truthy ? *pred : lnot(*pred);
        }
      }
    }
  return make(Expr::Op, o, std::move(args));
  }

ExprP ex::lnot(const ExprP& a) {
  if(a->kind==Expr::Int)
    return integer(a->value==0 ? 1 : 0);
  if(a->kind==Expr::Op) {
    auto& o = a->name;
    if(o=="!")
      return a->args[0];
    static const std::pair<const char*,const char*> neg[] = {
      {"==","!="}, {"!=","=="}, {"<",">="}, {">=","<"}, {">","<="}, {"<=",">"},
      };
    for(auto& [from,to] : neg)
      if(o==from)
        return make(Expr::Op, to, a->args);
    }
  return make(Expr::Op, "!", {a});
  }

static ExprP junction(const std::string& o, std::vector<ExprP> terms) {
  bool                  isAnd = (o=="&&");
  std::vector<ExprP>    out;
  std::set<std::string> seen;
  std::vector<ExprP>    todo(terms.rbegin(), terms.rend());
  while(!todo.empty()) {
    auto t = todo.back();
    todo.pop_back();
    if(t->kind==Expr::Op && t->name==o) {
      todo.insert(todo.end(), t->args.rbegin(), t->args.rend());
      continue;
      }
    if(t->kind==Expr::Int) {
      if(t->isTrue()==isAnd)
        continue;  // neutral element
      return ex::integer(isAnd ? 0 : 1);
      }
    if(seen.insert(t->key).second)
      out.push_back(t);
    }
  if(out.empty())
    return ex::integer(isAnd ? 1 : 0);
  if(out.size()==1)
    return out[0];
  return make(Expr::Op, o, std::move(out));
  }

ExprP ex::land(std::vector<ExprP> terms) { return junction("&&", std::move(terms)); }
ExprP ex::lor (std::vector<ExprP> terms) { return junction("||", std::move(terms)); }

bool ex::opaque(const ExprP& a) {
  bool r = false;
  walk(a, [&r](const Expr& e){ r |= (e.kind==Expr::Unknown || e.kind==Expr::Local); });
  return r;
  }
