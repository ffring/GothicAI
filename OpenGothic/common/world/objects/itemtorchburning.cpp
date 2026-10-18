#include "itemtorchburning.h"

#include "world/world.h"

ItemTorchBurning::ItemTorchBurning(World& owner, size_t inst, Item::Type type, const Tempest::Matrix4x4& initial)
  :Item(owner,inst,type,initial) {
  view.setVisual(handle(),owner,false);
  view.setObjMatrix(transform());
  setPhysicsEnable(owner);
  }

ItemTorchBurning::ItemTorchBurning(Item&& loaded)
  :Item(std::move(loaded)) {
  // The legacy Item stream has already restored the script fields and pose.
  // Its optional physics entry will create the native body afterwards.
  view.setVisual(handle(),world,false);
  view.setObjMatrix(transform());
  }

void ItemTorchBurning::setPhysicsEnable(World& owner) {
  auto& sc = owner.script();
  size_t torchId = sc.findSymbolIndex("ItLsTorchburned");
  if(torchId!=size_t(-1)) {
    auto hitem = std::make_shared<zenkit::IItem>();
    sc.initializeInstanceItem(hitem, torchId);
    auto m = Resources::loadMesh(hitem->visual);
    Item::setPhysicsEnable(m);
    }
  owner.invalidateVobIndex();
  }

void ItemTorchBurning::clearView() {
  Item::clearView();
  view = ObjVisual();
  }

bool ItemTorchBurning::isTorchBurn() const {
  return true;
  }

void ItemTorchBurning::moveEvent() {
  Item::moveEvent();
  view.setObjMatrix(transform());
  }
