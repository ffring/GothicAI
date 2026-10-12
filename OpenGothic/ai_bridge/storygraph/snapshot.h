#pragma once

#include <zenkit/DaedalusVm.hh>

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace storygraph {

using zenkit::DaedalusDataType;

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

}
