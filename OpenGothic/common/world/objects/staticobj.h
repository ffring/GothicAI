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
    // Render sync deliberately leaves Vob's authored transform unchanged: moved
    // furniture is not persisted until the movable-vob save format is added.
    DynamicWorld::Item movable;
    std::string scheme;
  };

