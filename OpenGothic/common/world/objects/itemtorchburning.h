#pragma once

#include "item.h"

class ItemTorchBurning : public Item {
  public:
    ItemTorchBurning(World& owner, size_t inst, Item::Type type,
                     const Tempest::Matrix4x4& initial = Tempest::Matrix4x4::mkIdentity());

    void      clearView() override;
    bool      isTorchBurn() const override;

  protected:
    void      moveEvent() override;

  private:
    ObjVisual view;
  };

