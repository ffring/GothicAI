#!/usr/bin/env python3
"""Inject identical temporary physics wall-time profiling into an isolated engine source copy.

The original checkout is left untouched. Run the copy with OPENGOTHIC_PHYSICS_TRACE=/path/trace.csv.
Nested public queries count once; each tick writes physics work accumulated since the previous tick.
"""

import argparse
from pathlib import Path


PROFILE = r'''
namespace {
thread_local unsigned physicsProfileDepth=0;
std::atomic<uint64_t> physicsProfileNs{0};
std::atomic<uint64_t> physicsProfileCalls{0};
struct PhysicsMigrationProfile {
  std::chrono::steady_clock::time_point begin=std::chrono::steady_clock::now();
  bool outer=physicsProfileDepth++==0;
  bool tick=false;
  uint64_t dt=0;
  PhysicsMigrationProfile(bool tick=false, uint64_t dt=0):tick(tick),dt(dt) {}
  ~PhysicsMigrationProfile() {
    const auto elapsed=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-begin).count();
    --physicsProfileDepth;
    if(outer) {
      physicsProfileNs.fetch_add(uint64_t(elapsed),std::memory_order_relaxed);
      physicsProfileCalls.fetch_add(1,std::memory_order_relaxed);
      }
    if(tick) {
      static std::FILE* file=[]() {
        const char* path=std::getenv("OPENGOTHIC_PHYSICS_TRACE");
        return path!=nullptr ? std::fopen(path,"w") : nullptr;
        }();
      const auto totalNs=physicsProfileNs.exchange(0,std::memory_order_relaxed);
      const auto totalCalls=physicsProfileCalls.exchange(0,std::memory_order_relaxed);
      if(file!=nullptr) {
        std::fprintf(file,"%llu,%llu,%llu,%llu\n",(unsigned long long)dt,(unsigned long long)totalNs,
                     (unsigned long long)totalCalls,(unsigned long long)elapsed);
        std::fflush(file);
        }
      }
    }
  };
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="isolated OpenGothic source root")
    parser.add_argument("--method-breakdown", action="store_true", help="diagnostic inclusive/outer per-method timing in a separate trace")
    args = parser.parse_args()
    path = args.source / "common/physics/dynamicworld.cpp"
    text = path.read_text()
    if "PhysicsMigrationProfile" in text:
        raise SystemExit("source already instrumented")
    methods = ("landRay", "landSweep", "waterRay", "cameraRay", "ray", "sweep", "rayNpc", "rayArrow", "soundOclusion", "NpcItem::implTryMove", "NpcItem::testMove", "NpcItem::hasCollision", "Item::setObjMatrix", "tick")
    profile = PROFILE
    if args.method_breakdown:
        methods += ("NpcItem::setScale", "NpcItem::setPosition")
        declaration = """
struct PhysicsMethodTimes {
  std::atomic<uint64_t> calls{0}, inclusiveNs{0}, outerCalls{0}, outerNs{0};
};
PhysicsMethodTimes physicsMethodTimes[METHOD_COUNT];
const char* physicsMethodNames[] = {METHOD_NAMES};
""".replace("METHOD_COUNT", str(len(methods))).replace("METHOD_NAMES", ",".join('"' + method + '"' for method in methods))
        profile = profile.replace("struct PhysicsMigrationProfile {", declaration + "\nstruct PhysicsMigrationProfile {\n  unsigned method;")
        profile = profile.replace("PhysicsMigrationProfile(bool tick=false, uint64_t dt=0):tick(tick),dt(dt) {}", "PhysicsMigrationProfile(unsigned method, bool tick=false, uint64_t dt=0):method(method),tick(tick),dt(dt) {}")
        profile = profile.replace("--physicsProfileDepth;", """--physicsProfileDepth;
    auto& methodTimes = physicsMethodTimes[method];
    methodTimes.calls.fetch_add(1,std::memory_order_relaxed);
    methodTimes.inclusiveNs.fetch_add(uint64_t(elapsed),std::memory_order_relaxed);
    if(outer) {
      methodTimes.outerCalls.fetch_add(1,std::memory_order_relaxed);
      methodTimes.outerNs.fetch_add(uint64_t(elapsed),std::memory_order_relaxed);
      }
""")
        profile = profile.replace("if(tick) {", """if(tick) {
      static std::FILE* methodsFile=[]() {
        const char* path=std::getenv("OPENGOTHIC_PHYSICS_TRACE_METHODS");
        return path!=nullptr ? std::fopen(path,"w") : nullptr;
        }();
      static uint64_t frame=0;
      ++frame;
      for(unsigned i=0;i<METHOD_COUNT;++i) {
        auto& value = physicsMethodTimes[i];
        auto calls = value.calls.exchange(0,std::memory_order_relaxed);
        auto inclusiveNs = value.inclusiveNs.exchange(0,std::memory_order_relaxed);
        auto outerCalls = value.outerCalls.exchange(0,std::memory_order_relaxed);
        auto outerNs = value.outerNs.exchange(0,std::memory_order_relaxed);
        if(methodsFile!=nullptr)
          std::fprintf(methodsFile,"%llu,%s,%llu,%llu,%llu,%llu\\n",(unsigned long long)frame,physicsMethodNames[i],
                       (unsigned long long)calls,(unsigned long long)inclusiveNs,(unsigned long long)outerCalls,(unsigned long long)outerNs);
        }
      if(methodsFile!=nullptr)
        std::fflush(methodsFile);
""".replace("METHOD_COUNT", str(len(methods))))
    text = text.replace('#include "dynamicworld.h"', '#include "dynamicworld.h"\n#include <atomic>\n#include <chrono>\n#include <cstdio>\n#include <cstdlib>\n' + profile, 1)
    for index, method in enumerate(methods):
        cursor = 0
        while True:
            start = text.find("DynamicWorld::" + method + "(", cursor)
            if start < 0:
                break
            brace = text.find("{", start)
            declaration = text[start:brace]
            if ";" in declaration:
                raise SystemExit("unexpected declaration for " + method)
            if args.method_breakdown:
                statement = f"\n  PhysicsMigrationProfile physicsProfile({index},true,dt);" if method == "tick" else f"\n  PhysicsMigrationProfile physicsProfile({index});"
            else:
                statement = "\n  PhysicsMigrationProfile physicsProfile(true,dt);" if method == "tick" else "\n  PhysicsMigrationProfile physicsProfile;"
            text = text[:brace + 1] + statement + text[brace + 1:]
            cursor = brace + len(statement) + 1
    path.write_text(text)
    print(path)


if __name__ == "__main__":
    main()
