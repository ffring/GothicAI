#pragma once

#include "graphics/objvisual.h"
#include "vob.h"

class StaticObj : public Vob {
  public:
    StaticObj(Vob* parent, World& world, const zenkit::VirtualObject& vob, Flags flags);

  private:
    void  moveEvent() override;
    bool  setMobState(std::string_view scheme,int32_t st) override;

    ObjVisual   visual;
    // The authored Vob transform remains the save identity and toggle-OFF pose.
    // DynamicWorld persists the simulated transform in an optional entry.
    DynamicWorld::Item movable;
    std::string scheme;
  };

