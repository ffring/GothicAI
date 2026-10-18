#pragma once

#include <Tempest/Point>
#include <cstdint>
#include <memory>

class Npc;
struct HeroDeed;

class PerceptionMsg final {
  public:
    int32_t       what=0;
    Tempest::Vec3 pos;
    Npc*          self  =nullptr;
    Npc*          other =nullptr;
    Npc*          victim=nullptr;
    size_t        item  =size_t(-1);
    std::shared_ptr<HeroDeed> deed;
  };
