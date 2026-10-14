#pragma once

#include <rapidjson/document.h>

#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "game/gamescript.h"

#include "expr.h"
#include "program.h"
#include "storyeffects.h"

class Npc;
class World;

namespace storygraph {
  class Bytecode;
  class Externals;
  class Interpreter;
  class Lifter;
  class Predicates;
  }

// The story graph in the game (docs/story-graph.md, stage 3): which dialogs npc's offer, through the
// engine's dialog hooks (Gothic::dialogFilterFn...). Without patches every dialog keeps its script
// condition. The server's patches (graph_patch) switch a dialog or a choice off, add a condition to it,
// or add dialogs of its own, whose lines and effects the bridge plays.
// With -devmode (or GOTHIC_AI_STORYCHECK=1) every script condition is also computed from its lifted form
// and differences are logged: the check of stage 2, on the live game.
// The script functions of dialogs (conditions, information, choices) run as their programs (program.h), by
// the Interpreter, not by the VM; script functions they call still run in the VM. GOTHIC_AI_STORYEXEC=0 turns
// it off; so do scripts with Ikarus, whose loops jump from engine functions.
class DialogGraph final {
  public:
    // send: a message to the server; addLine: registers a runtime line (npc symbol, text in the game
    // codepage) and returns its id
    DialogGraph(std::function<void(std::string&&)> send,
                std::function<std::string(const std::string&,const std::string&)> addLine);
    ~DialogGraph();

    // a graph_patch message: its operations in order; one that can't be applied is skipped and reported
    std::vector<std::string> apply(const rapidjson::Value& msg);
    // a load: the server sends the branch's patches again
    void clear();

    // the dialogs the npc offers the player now, as the menu would list them (node ids)
    std::vector<std::string> offered(World& world, Npc& npc);
    // a dialog the npc offers now, as if the player chose it in the menu; false if it is not offered
    bool choose(World& world, Npc& npc, std::string_view node, std::string& error);
    // an expression with self = npc, other = the hero, as JSON (5, "text", null, "PC_HERO"); false and `error`
    // if it can't be computed
    bool evaluate(World& world, Npc* npc, const rapidjson::Value& expr, std::string& json, std::string& error);
    // every npc of the world builds its dialog menu once, with the check on: the stage 2 check in the
    // live game. Checking runs the conditions, with their effects (C_Beklauen, Npc_SetRefuseTalk...).
    // JSON: {"npcs", "computed", "differ", "differences": [dialog ids]}
    std::string checkWorld(World& world);
    std::string status() const;

  private:
    struct Line final {
      bool        hero = false;   // spoken by the hero ("other"), else by the npc
      std::string id;             // an original line (DIA_..._15_00), or empty
      std::string text;           // a line of its own, game codepage
      };
    // a dialog made by the director
    struct Node final {
      std::string                       id;
      std::string                       npc;          // script symbol
      std::string                       description;  // game codepage
      int32_t                           nr = 0;
      bool                              important = false, permanent = false;
      storygraph::ExprP                 condition;    // nullptr: always
      std::vector<Line>                 lines;
      std::vector<StoryEffect>          effects;
      };

    void        onFilter(const zenkit::IInfo& info, bool& visible);
    bool        onChoiceFilter(const zenkit::IInfo& info, int32_t fn);
    void        onExtra(const zenkit::INpc& npc, bool important, std::vector<GameScript::DlgChoice>& out);
    void        onExec(const GameScript::DlgChoice& dlg, Npc& player, Npc& npc);
    void        onChosen(const GameScript::DlgChoice& dlg, Npc& player, Npc& npc);
    // a dialog's condition, information or choice: run as its program (docs/story-graph.md, «Исполнение без VM»)
    bool        onScript(zenkit::DaedalusSymbol& fn, int32_t& result);

    std::string applyOp(const rapidjson::Value& op);
    std::string readNode(const rapidjson::Value& v, Node& n);
    // the expression may be evaluated: names exist, script functions it calls can be lifted
    std::string checkExpr(const storygraph::ExprP& e);
    // nullopt: could not be computed (logged)
    std::optional<bool> test(const storygraph::ExprP& e, std::string_view what);
    void        check(const zenkit::IInfo& info, bool visible);
    void        ensureLifter();
    zenkit::DaedalusVm* vm() const;

    std::function<void(std::string&&)>                               send;
    std::function<std::string(const std::string&,const std::string&)> addLine;

    std::set<std::string>                                  disabled;    // dialogs and choices
    std::map<std::string,std::vector<storygraph::ExprP>>   conditions;  // added to script dialogs
    std::map<std::string,Node>                             nodes;       // made by the director
    std::set<std::string>                                  told;        // of those, the ones the hero chose

    // lifting, for conditions of the director and the check
    zenkit::DaedalusVm*                                    liftedFor = nullptr;
    std::unique_ptr<storygraph::Bytecode>                  code;
    std::unique_ptr<storygraph::Lifter>                    lifter;
    std::unique_ptr<storygraph::Predicates>                predicates;

    // the graph runs dialogs' script functions in place of the VM; GOTHIC_AI_STORYEXEC=0: the VM runs them
    bool                                                   running = true;
    zenkit::DaedalusVm*                                    runFor = nullptr;
    std::unordered_map<uint32_t,storygraph::Program>       programs;   // by function symbol, lifted when it first runs
    std::unique_ptr<storygraph::Externals>                 engine;
    std::unique_ptr<storygraph::Interpreter>               interp;
    size_t                                                 ran = 0, notPrograms = 0, runErrors = 0;

    bool                                                   checking = false;
    size_t                                                 checked = 0, differ = 0;
    std::set<std::string>                                  reported;   // differences, logged once
    std::vector<std::string>                               differences;
  };
