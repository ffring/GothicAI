#pragma once

#include "item.h"

class ItemTorchBurning : public Item {
  public:
    ItemTorchBurning(World& owner, size_t inst, Item::Type type,
                     const Tempest::Matrix4x4& initial = Tempest::Matrix4x4::mkIdentity());
    explicit ItemTorchBurning(Item&& loaded);

    void      clearView() override;
    bool      isTorchBurn() const override;
    void      setPhysicsEnable(World& world) override;
    void      setPhysicsMatrix(const Tempest::Matrix4x4& m) override;

  protected:
    void      moveEvent() override;

  private:
    ObjVisual view;
  };

