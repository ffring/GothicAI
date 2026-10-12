// storygraph: lifts the story layer of a compiled GOTHIC.DAT into a graph (docs/story-graph.md).
//
//   storygraph <GOTHIC.DAT>                         statistics of the lifted model
//   storygraph <GOTHIC.DAT> --json story.json       the model for the AI server (--pretty: indented)
//   storygraph <GOTHIC.DAT> --node DIA_LOBART_WORKNOW
//   storygraph <GOTHIC.DAT> --quest MIS_LOBART_RUEBEN
//   storygraph <GOTHIC.DAT> --scan [--tsv dialogs.tsv] [--top N]   first measurement, per dialog bytecode summary
//   storygraph <GOTHIC.DAT> --disasm DIA_LOBART_RUEBENRUNNING_INFO
//   storygraph <GOTHIC.DAT> --check <save dir> [--check <save dir>...] [--node ID] [--verbose]
//                                  every lifted condition against the VM on original saves (stage 2);
//                                  with --node, both computations of that dialog are traced
//   storygraph <GOTHIC.DAT> --fuzz N [--node ID] [--verbose]
//                                  the same in N random states per dialog
//
// Subtitles come from OU.BIN next to the scripts (_work/Data/Scripts/Content/Cutscene/OU.BIN), or --ou <path>.

#include "bytecode.h"
#include "check.h"
#include "lifter.h"
#include "model.h"
#include "vocab.h"

#include <zenkit/Archive.hh>
#include <zenkit/CutsceneLibrary.hh>
#include <zenkit/DaedalusVm.hh>
#include <zenkit/Stream.hh>
#include <zenkit/addon/daedalus.hh>

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <map>
#include <string>
#include <vector>

using namespace storygraph;
namespace fs = std::filesystem;

namespace storygraph {
  int scanDialogs(zenkit::DaedalusVm& vm, const Bytecode& code, const char* tsvPath, size_t top);
  }

static std::string upper(std::string s) {
  for(auto& c : s)
    c = char(std::toupper(uint8_t(c)));
  return s;
  }

// a child of `dir` by name, ignoring case
static fs::path child(const fs::path& dir, const std::string& name) {
  std::error_code ec;
  for(auto& e : fs::directory_iterator(dir, ec))
    if(upper(e.path().filename().string())==upper(name))
      return e.path();
  return {};
  }

static fs::path findOu(const fs::path& dat) {
  auto scripts = dat.parent_path().parent_path();  // _work/Data/Scripts/_compiled/GOTHIC.DAT
  auto content = child(scripts, "CONTENT");
  auto cut     = content.empty() ? fs::path() : child(content, "CUTSCENE");
  return cut.empty() ? fs::path() : child(cut, "OU.BIN");
  }

static LineTexts loadLines(const fs::path& ou) {
  LineTexts lines;
  auto r = zenkit::Read::from(ou);
  if(r==nullptr)
    return lines;
  auto ar = zenkit::ReadArchive::from(r.get());
  if(ar==nullptr)
    return lines;
  auto lib = ar->read_object<zenkit::CutsceneLibrary>(zenkit::GameVersion::GOTHIC_2);
  if(lib==nullptr)
    return lines;
  for(auto& b : lib->blocks)
    if(auto msg = b->get_message())
      lines[upper(b->name)] = msg->text;
  return lines;
  }

static double pct(size_t a, size_t b) { return b==0 ? 0.0 : 100.0*double(a)/double(b); }

static void printStats(const Model& m, size_t lineTexts) {
  size_t dialogs=0, choices=0, lifted=0, errors=0, effects=0, complexGuards=0, sideEffects=0;
  std::map<std::string,size_t> why, kinds;
  for(auto& n : m.nodes) {
    (n.choice ? choices : dialogs)++;
    errors += !n.error.empty();
    for(auto& e : n.effects) {
      kinds[e.kind]++;
      effects++;
      complexGuards += e.when && ex::opaque(e.when);
      }
    if(n.choice)
      continue;
    sideEffects += !n.conditionEffects.empty();
    if(n.notLifted.empty())
      lifted++;
    else
      why[n.notLifted]++;
    }
  size_t helpersLifted = size_t(std::count_if(m.helpers.begin(), m.helpers.end(),
                                              [](auto& h){ return h.second.notLifted.empty(); }));
  size_t withTopic = size_t(std::count_if(m.quests.begin(), m.quests.end(), [](auto& q){ return !q.second.topics.empty(); }));

  std::printf("nodes: %zu dialogs, %zu choices; subtitles loaded: %zu\n", dialogs, choices, lineTexts);
  std::printf("conditions lifted: %zu (%.1f%%); checking them changes the world in %zu\n", lifted, pct(lifted,dialogs),
              sideEffects);
  std::printf("helpers used by conditions: %zu, lifted %zu\n", m.helpers.size(), helpersLifted);
  std::printf("nodes whose effects were not lifted: %zu\n", errors);
  std::printf("effects: %zu, with a guard that is not fully known: %zu\n", effects, complexGuards);
  std::printf("quests (MIS_*): %zu, with a journal topic %zu; story variables %zu; instance globals %zu\n",
              m.quests.size(), withTopic, m.vars.size(), m.instances.size());

  std::vector<std::pair<size_t,std::string>> v;
  for(auto& [k,c] : why)
    v.emplace_back(c,k);
  std::sort(v.rbegin(), v.rend());
  std::printf("\nnot lifted, by reason:\n");
  for(auto& [c,k] : v)
    std::printf("  %5zu  %s\n", c, k.c_str());

  v.clear();
  for(auto& [k,c] : kinds)
    v.emplace_back(c,k);
  std::sort(v.rbegin(), v.rend());
  std::printf("\neffects by kind:\n");
  for(auto& [c,k] : v)
    std::printf("  %6zu  %s\n", c, k.c_str());
  }

int main(int argc, char** argv) {
  std::setvbuf(stdout, nullptr, _IOLBF, 0);  // what was printed survives a crash
  const char* datPath = nullptr;
  const char* ouPath  = nullptr;
  const char* json    = nullptr;
  const char* node    = nullptr;
  const char* quest   = nullptr;
  const char* tsvPath = nullptr;
  const char* disasm  = nullptr;
  bool        pretty  = false;
  bool        scan    = false;
  bool        verbose = false;
  bool        effects = false;
  size_t      top     = 0;
  uint32_t    fuzz    = 0;
  std::vector<std::string> saves;
  for(int i=1; i<argc; ++i) {
    auto opt = [&](const char* name) { return std::strcmp(argv[i],name)==0 && i+1<argc; };
    if(opt("--ou"))
      ouPath = argv[++i];
    else if(opt("--json"))
      json = argv[++i];
    else if(opt("--node"))
      node = argv[++i];
    else if(opt("--quest"))
      quest = argv[++i];
    else if(opt("--tsv"))
      tsvPath = argv[++i];
    else if(opt("--disasm"))
      disasm = argv[++i];
    else if(opt("--top"))
      top = size_t(std::strtoul(argv[++i],nullptr,10));
    else if(std::strcmp(argv[i],"--pretty")==0)
      pretty = true;
    else if(opt("--check"))
      saves.push_back(argv[++i]);
    else if(opt("--fuzz"))
      fuzz = uint32_t(std::strtoul(argv[++i],nullptr,10));
    else if(std::strcmp(argv[i],"--scan")==0)
      scan = true;
    else if(std::strcmp(argv[i],"--verbose")==0)
      verbose = true;
    else if(std::strcmp(argv[i],"--effects")==0)
      effects = true;
    else
      datPath = argv[i];
    }
  if(datPath==nullptr) {
    std::fprintf(stderr, "usage: storygraph <GOTHIC.DAT> [--ou OU.BIN] [--json out.json [--pretty]] [--node ID] "
                         "[--quest MIS_*] [--scan [--tsv out.tsv] [--top N]] [--disasm FN] "
                         "[--check <save dir>... [--verbose]] [--fuzz N] [--effects]\n");
    return 2;
    }

  zenkit::DaedalusScript script;
  {
  auto r = zenkit::Read::from(fs::path(datPath));
  if(r==nullptr) {
    std::fprintf(stderr, "can't open %s\n", datPath);
    return 1;
    }
  script.load(r.get());
  }
  zenkit::register_all_script_classes(script);
  zenkit::DaedalusVm vm(std::move(script), zenkit::DaedalusVmExecutionFlag::ALLOW_NULL_INSTANCE_ACCESS);
  vm.register_default_external([](const zenkit::DaedalusSymbol&){});
  const zenkit::DaedalusScript& dat = vm;
  Bytecode code(dat);

  if(disasm!=nullptr)
    return code.print(upper(disasm)) ? 0 : 1;
  if(scan)
    return scanDialogs(vm, code, tsvPath, top);

  fs::path ou = ouPath ? fs::path(ouPath) : findOu(datPath);
  LineTexts lines;
  if(!ou.empty())
    lines = loadLines(ou);
  if(lines.empty())
    std::fprintf(stderr, "no subtitles (OU.BIN not found, use --ou): lines will have no text\n");

  Lifter lifter(dat, code);
  Model  m = buildModel(vm, code, lifter, lines);

  if(!saves.empty() || fuzz>0) {
    auto id = node ? upper(node) : std::string();
    int  r  = 0;
    if(!saves.empty())
      r |= checkSaves(datPath, m, saves, node ? id.c_str() : nullptr, verbose, effects);
    if(fuzz>0 && effects)
      r |= fuzzEffects(datPath, m, fuzz, node ? id.c_str() : nullptr, verbose);
    else if(fuzz>0)
      r |= fuzzConditions(datPath, m, fuzz, node ? id.c_str() : nullptr, verbose);
    return r;
    }
  if(node!=nullptr) {
    std::fputs(describeNode(m, upper(node)).c_str(), stdout);
    return 0;
    }
  if(quest!=nullptr) {
    std::fputs(describeQuest(m, upper(quest)).c_str(), stdout);
    return 0;
    }
  if(json!=nullptr) {
    auto text = toJson(m, pretty);
    FILE* f = std::fopen(json, "wb");
    if(f==nullptr) {
      std::fprintf(stderr, "can't write %s\n", json);
      return 1;
      }
    std::fwrite(text.data(), 1, text.size(), f);
    std::fclose(f);
    }
  printStats(m, lines.size());
  return 0;
  }
