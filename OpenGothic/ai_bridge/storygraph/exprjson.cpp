#include "exprjson.h"

#include "textcodec.h"

#include <cctype>
#include <set>

using namespace storygraph;

std::string storygraph::symbolName(std::string_view utf8) {
  std::string r;
  for(size_t i=0; i<utf8.size(); ++i) {
    auto c = uint8_t(utf8[i]);
    if(c<0x80) {
      r += char(std::toupper(c));
      continue;
      }
    // two-byte sequences up to U+00FF, the inverse of ident()
    if((c & 0xE0)!=0xC0 || c>0xC3 || i+1>=utf8.size())
      return {};
    auto cp = uint32_t(((c & 0x1F) << 6) | (uint8_t(utf8[++i]) & 0x3F));
    r += char(cp);
    }
  return r;
  }

namespace {

const std::set<std::string> Binary = {
  "==", "!=", "<", ">", "<=", ">=", "+", "-", "*", "/", "%", "&", "|", "<<", ">>",
  };

struct Reader final {
  std::string& error;

  ExprP fail(std::string why) {
    if(error.empty())
      error = std::move(why);
    return nullptr;
    }

  std::string name(const rapidjson::Value& v, const char* what) {
    if(!v.IsString()) {
      fail(std::string(what) + " is not a string");
      return {};
      }
    auto n = symbolName(std::string_view(v.GetString(), v.GetStringLength()));
    if(n.empty())
      fail(std::string("bad ") + what);
    return n;
    }

  ExprP read(const rapidjson::Value& v, int depth) {
    if(depth>200)
      return fail("expression nested too deep");
    if(!v.IsArray() || v.Empty() || !v[0].IsString())
      return fail("an expression is an array that starts with its form");
    const std::string tag = v[0].GetString();
    const auto        n   = v.Size();
    auto args = [&](rapidjson::SizeType from) {
      std::vector<ExprP> r;
      for(auto i=from; i<n; ++i) {
        auto a = read(v[i], depth+1);
        if(a==nullptr)
          return std::vector<ExprP>();
        r.push_back(std::move(a));
        }
      return r;
      };
    auto optName = [&](rapidjson::SizeType i) {
      return i<n && v[i].IsString() ? symbolName(std::string_view(v[i].GetString(), v[i].GetStringLength())) : std::string();
      };

    if(tag=="int") {
      if(n<2 || !v[1].IsInt())
        return fail("int: no value");
      return ex::integer(v[1].GetInt(), optName(2));
      }
    if(tag=="str") {
      if(n<2 || !v[1].IsString())
        return fail("str: no text");
      return ex::str(TextCodec::fromUtf8(std::string_view(v[1].GetString(), v[1].GetStringLength())), optName(2));
      }
    if(tag=="var") {
      if(n<2)
        return fail("var: no name");
      auto nm = name(v[1], "variable");
      if(nm.empty())
        return nullptr;
      return ex::var(nm, n>2 && v[2].IsInt() ? v[2].GetInt() : -1);
      }
    if(tag=="field") {
      if(n<3)
        return fail("field: needs an instance and a member");
      auto inst = read(v[1], depth+1);
      auto nm   = inst ? name(v[2], "member") : std::string();
      if(nm.empty())
        return nullptr;
      return ex::field(inst, nm, n>3 && v[3].IsInt() ? v[3].GetInt() : -1, optName(4));
      }
    if(tag=="inst" || tag=="sym") {
      if(n<2)
        return fail(tag + ": no name");
      auto nm = name(v[1], tag=="inst" ? "instance" : "symbol");
      if(nm.empty())
        return nullptr;
      return tag=="inst" ? ex::inst(nm) : ex::sym(nm);
      }
    if(tag=="param") {
      if(n<2 || !v[1].IsInt())
        return fail("param: no index");
      return ex::param(v[1].GetInt());
      }
    if(tag=="call" || tag=="fn") {
      if(n<2)
        return fail(tag + ": no function");
      auto nm = name(v[1], "function");
      if(nm.empty())
        return nullptr;
      auto a = args(2);
      if(a.size()!=n-2)
        return nullptr;
      return tag=="call" ? ex::call(nm, std::move(a)) : ex::fn(nm, std::move(a));
      }
    if(tag=="cases") {
      auto a = args(1);
      if(a.size()!=n-1)
        return nullptr;
      if(a.size()%2!=0)
        return fail("cases: guards and values come in pairs");
      return ex::cases(std::move(a));
      }
    if(tag=="unknown")
      return ex::unknown(n>1 && v[1].IsString() ? v[1].GetString() : "");
    if(tag=="&&" || tag=="||") {
      auto a = args(1);
      if(a.size()!=n-1 || a.empty())
        return a.empty() ? fail(tag + " without terms") : nullptr;
      return tag=="&&" ? ex::land(std::move(a)) : ex::lor(std::move(a));
      }
    if(tag=="!" || tag=="neg" || tag=="~") {
      auto a = args(1);
      if(a.size()!=1)
        return error.empty() ? fail(tag + " takes one argument") : nullptr;
      return tag=="!" ? ex::lnot(a[0]) : ex::op(tag, std::move(a));
      }
    if(Binary.count(tag)) {
      auto a = args(1);
      if(a.size()!=2)
        return error.empty() ? fail(tag + " takes two arguments") : nullptr;
      return ex::op(tag, std::move(a));
      }
    return fail("unknown form " + tag);
    }
  };

}

ExprP storygraph::readExpr(const rapidjson::Value& v, std::string& error) {
  error.clear();
  Reader r{error};
  return r.read(v, 0);
  }
