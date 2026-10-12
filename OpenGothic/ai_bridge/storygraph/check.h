#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace zenkit {
  class DaedalusVm;
  }

// Stage 2 of docs/story-graph.md: every lifted dialog condition against the VM running the script.
namespace storygraph {

struct Model;

// a VM over GOTHIC.DAT with the script classes registered; nullptr if the file can't be read
std::unique_ptr<zenkit::DaedalusVm> loadVm(const char* datPath);

// on original saves (check.cpp); with `traceNode`, both computations of that dialog are printed
int checkSaves(const char* datPath, const Model& m, const std::vector<std::string>& saves, const char* traceNode,
               bool verbose);
// in `rounds` random states per dialog (fuzz.cpp); `only`: one dialog
int fuzzConditions(const char* datPath, const Model& m, uint32_t rounds, const char* only, bool verbose);

}
