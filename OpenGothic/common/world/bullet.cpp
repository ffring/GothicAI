#include "bullet.h"

#include <Tempest/Log>

#include "graphics/visualfx.h"
#include "world/objects/item.h"
#include "world/objects/npc.h"
#include "world.h"
#include "testbench.h"

using namespace Tempest;

Bullet::Bullet(World& owner, const Item& itm, const Vec3& pos)
  :wrld(&owner) {
  obj = wrld->physic()->bulletObj(this);
  if(itm.isSpellOrRune()) {
    obj->setSpellId(itm.spellId());
    }

  if(itm.isSpellOrRune()) {
    material   = ItemMaterial::MAT_COUNT;
    int32_t id = itm.spellId();
    const VisualFx* vfx = owner.script().spellVfx(id);
    if(vfx!=nullptr) {
      auto e = Effect(*vfx,owner,pos,SpellFxKey::Cast);
      setView(std::move(e));
      }
    } else {
    material = uint8_t(itm.handle().material);
    setView(owner.addView(itm.handle()));
    }

  setPosition(pos);
  }

Bullet::~Bullet() {
  vfx.setBullet(nullptr,*wrld);
  wrld->physic()->deleteObj(obj);
  }

void Bullet::setPosition(const Tempest::Vec3& p) {
  obj->setPosition(p);
  updateMatrix();
  }

void Bullet::setPosition(float x, float y, float z) {
  obj->setPosition(Vec3(x,y,z));
  updateMatrix();
  }

void Bullet::setDirection(const Tempest::Vec3& dir) {
  obj->setDirection(dir);
  updateMatrix();
  }

void Bullet::setTargetRange(float t) {
  obj->setTargetRange(t);
  }

void Bullet::setView(MeshObjects::Mesh &&m) {
  view = std::move(m);
  updateMatrix();
  }

void Bullet::setView(Effect &&p) {
  vfx = std::move(p);
  vfx.setActive(true);
  vfx.setLooped(true);
  vfx.setBullet(this,*wrld);
  vfx.setPhysicsDisable();
  updateMatrix();
  }

bool Bullet::isSpell() const {
  return obj->isSpell();
  }

int32_t Bullet::spellId() const {
  return obj->spellId();
  }

void Bullet::setOrigin(Npc *n) {
  ow = n;
  vfx.setOrigin(n);
  }

Npc *Bullet::origin() const {
  return ow;
  }

void Bullet::setTarget(const Npc* n) {
  vfx.setTarget(n);
  }

ItemMaterial Bullet::itemMaterial() const {
  if(material>=ItemMaterial::MAT_COUNT)
    return ItemMaterial::MAT_WOOD;
  return ItemMaterial(material);
  }

bool Bullet::isFinished() const {
  if((flags()&Bullet::Stopped)!=Bullet::Stopped)
    return false;
  return true;
  }

float Bullet::pathLength() const {
  return obj->pathLength();
  }

bool Bullet::onEffectCollide(Npc& other) {
  const bool ret = onCollide(other);
  if(ret)
    onStop();
  return ret;
  }

void Bullet::onStop() {
  if(precise && zone==HitZones::None && !landed && !isFinished()) {
    const auto p = obj->position();
    char buf[160];
    std::snprintf(buf,sizeof(buf),"arrow missed: flew out at %.0f %.0f %.0f, flew %.0f cm",p.x,p.y,p.z,double(pathLength()));
    TestBench::note(buf);
    }
  flg = Flg(flg|Stopped);
  updateMatrix();
  }

Tempest::Vec3 Bullet::position() const {
  return obj!=nullptr ? obj->position() : Tempest::Vec3();
  }

Tempest::Vec3 Bullet::direction() const {
  return obj!=nullptr ? obj->direction() : Tempest::Vec3();
  }

void Bullet::onMove() {
  updateMatrix();
  }

void Bullet::onCollide(zenkit::MaterialGroup matId) {
  if(isFinished())
    return;
  if(precise && zone==HitZones::None && !landed) {
    // where it came down first: it may glance off stone and fly on
    landed = true;
    const auto p = obj->position();
    char buf[160];
    std::snprintf(buf,sizeof(buf),"arrow missed: came down at %.0f %.0f %.0f, flew %.0f cm",p.x,p.y,p.z,double(pathLength()));
    TestBench::note(buf);
    }
  if(matId != zenkit::MaterialGroup::NONE) {
    if(material < ItemMaterial::MAT_COUNT) {
      auto s = wrld->addLandHitEffect(ItemMaterial(material),matId,obj->matrix());
      s.play();
      }
    }
  Effect::onCollide(*wrld,vfx.handle(),obj->position(),nullptr,ow,spellId());
  vfx.setLooped(false);
  vfx.setPhysicsDisable();
  wrld->runEffect(std::move(vfx));
  }

bool Bullet::onCollidePrecise(Npc& npc, const Vec3& from, const Vec3& to) {
  if(&npc==origin() || isFinished())
    return false;
  // between the arm and the trunk, over the head: past it
  float t = 0;
  const auto z = npc.rayHitZone(from,to,t);
  if(z==HitZones::None)
    return false;
  zone = z;
  const int32_t hp = npc.attribute(ATR_HITPOINTS);
  const bool    ret = onCollide(npc);
  if(TestBench::isActive()) {
    // by its script symbol: the names are in the game's code page
    const auto       p   = from + Vec3::normalize(to-from)*t;
    auto             sym = wrld->script().findSymbol(size_t(npc.instanceSymbol()));
    std::string_view who = sym!=nullptr ? std::string_view(sym->name()) : std::string_view("npc");
    char buf[200];
    std::snprintf(buf,sizeof(buf),"arrow hit %.*s: %.*s, hp %d -> %d, draw %.2f, flew %.0f cm, at %.0f %.0f %.0f",
                  int(who.size()),who.data(),int(HitZones::name(z).size()),HitZones::name(z).data(),
                  int(hp),int(npc.attribute(ATR_HITPOINTS)),double(drawK),double(pathLength()+t),p.x,p.y,p.z);
    TestBench::note(buf);
    }
  return ret;
  }

bool Bullet::onCollide(Npc& npc) {
  if(&npc==origin() || isFinished())
    return false;

  if(ow!=nullptr) {
    // no damage between ally npc's, only emit pfx effect
    const bool friendlyFire = wrld->script().isFriendlyFire(*ow, npc);
    if(!friendlyFire) {
      if(isSpell())
        npc.takeDamage(*ow,this,vfx.handle(),spellId()); else
        npc.takeDamage(*ow,this);
      }
    }
  vfx.setKey(*wrld,SpellFxKey::Collide);
  vfx.setLooped(false);
  vfx.setPhysicsDisable();
  wrld->runEffect(std::move(vfx));

  return true;
  }

void Bullet::updateMatrix() {
  if(obj==nullptr)
    return;
  auto mat = obj->matrix();
  view.setObjMatrix(mat);
  // HACK: lighting bolt spell
  mat.rotateOY(90);
  vfx.setObjMatrix(mat);
  // vfx .setTarget(obj->position()+obj->direction());
  }
