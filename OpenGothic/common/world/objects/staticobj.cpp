#include "staticobj.h"

#include <Tempest/Log>

#include "world/world.h"
#include "world/movablevobs.h"
#include "gothic.h"
#include "utils/string_frm.h"

using namespace Tempest;

StaticObj::StaticObj(Vob* parent, World& world, const zenkit::VirtualObject& vob, Flags flags)
  : Vob(parent, world, vob, flags) {
  const auto* rule = MovableVobs::classify(vob);
  // Static draw instances still update transforms and culling through
  // setObjMatrix; retaining the original draw group preserves the OFF path.
  visual.setVisual(vob,world,(flags & Flags::Static),rule==nullptr);
  visual.setObjMatrix(transform());

  scheme = vob.visual->name;

  if(rule!=nullptr && visual.protoMesh()!=nullptr) {
    auto& physics = *world.physic();
    physics.setMovableVobsEnabled(Gothic::physicsFlags().movableVobs);
    movable = physics.addMovableVob(*visual.protoMesh(),scheme,transform(),rule->massMultiplier,
                                   rule->blocksWalking,rule->className,vob.cd_dynamic,vob.id);
    if(!movable.isEmpty()) {
      movable.setTransformCallback([this](const Matrix4x4& matrix) { visual.setObjMatrix(matrix); });
      } else {
      // An invalid hull must preserve the original collision and rendering.
      visual.setVisual(vob,world,(flags & Flags::Static));
      visual.setObjMatrix(transform());
      }
    }
  }

void StaticObj::moveEvent() {
  Vob::moveEvent();
  visual.setObjMatrix(transform());
  if(!movable.isEmpty())
    movable.setObjMatrix(transform());
  }

bool StaticObj::setMobState(std::string_view sc, int32_t st) {
  const bool ret = Vob::setMobState(sc,st);

  if(scheme.find(sc)!=0)
    return ret;
  string_frm name("S_S",st);
  if(visual.startAnimAndGet(name,world.tickCount())!=nullptr) {
    // state = st;
    return ret;
    }
  return false;
  }
