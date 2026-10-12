#include "pose.h"

#include <Tempest/Log>

#include "utils/string_frm.h"
#include "world/objects/npc.h"
#include "world/world.h"
#include "game/serialize.h"
#include "skeleton.h"
#include "animmath.h"

#include <cmath>

using namespace Tempest;

Pose::Pose() {
  lay.reserve(4);
  }

uint8_t Pose::calcAniComb(const Vec3& dpos, float rotation) {
  float   l   = std::sqrt(dpos.x*dpos.x+dpos.z*dpos.z);

  float   dir = 180.f*std::atan2(dpos.z,dpos.x)/float(M_PI);
  float   aXZ = (rotation-dir);
  float   aY  = -std::atan2(dpos.y,l)*180.f/float(M_PI);

  uint8_t cx  = (aXZ<-30.f) ? 0 : (aXZ<=30.f ? 1 : 2);
  uint8_t cy  = (aY <-45.f) ? 0 : (aY <=45.f ? 1 : 2);

  // sides angle: +/- 30 height angle: +/- 45
  return uint8_t(1u+cy*3u+cx);
  }

uint8_t Pose::calcAniCombVert(const Vec3& dpos) {
  float   l  = std::sqrt(dpos.x*dpos.x+dpos.z*dpos.z);
  float   aY = 180.f*std::atan2(dpos.y,l)/float(M_PI);
  uint8_t cy = (aY <-25.f) ? 0 : (aY <=25.f ? 1 : 2);

  if(dpos.y<-50)
    cy = 0;
  if(dpos.y>50)
    cy = 2;

  // height angle: +/- 25
  return uint8_t(cy+1u);
  }

void Pose::save(Serialize &fout) {
  uint8_t sz=uint8_t(lay.size());
  fout.write(sz);
  for(auto& i:lay) {
    fout.write(i.seq->name,i.sAnim,i.bs,uint64_t(0)); // was the start of a blend
    }
  fout.write(lastUpdate);
  fout.write(combo.bits);
  fout.write(rotation ? rotation->name : "");
  fout.write(itemUseSt,itemUseDestSt);
  fout.write(headRotX,headRotY);

  for(auto& i:hasSamples)
    fout.write(uint8_t(i));
  for(auto& i:base)
    fout.write(i);
  for(auto& i:raw)
    fout.write(i);
  for(auto& i:tr)
    fout.write(i);
  }

void Pose::load(Serialize &fin, const AnimationSolver& solver) {
  std::string name;
  uint8_t     sz = uint8_t(lay.size());

  fin.read(sz);
  lay.resize(sz);
  for(auto& i:lay) {
    fin.read(name,i.sAnim,i.bs);
    if(fin.version()>43) {
      uint64_t sBlend = 0;
      fin.read(sBlend);
      }
    i.seq = solver.solveFrm(name);
    }
  fin.read(lastUpdate);
  fin.read(combo.bits);
  removeIf(lay,[](const Layer& l){
    return l.seq==nullptr;
    });
  fin.read(name);
  for(auto& i:lay) {
    if(i.seq->name==name)
      rotation = i.seq;
    }
  fin.read(itemUseSt,itemUseDestSt);
  hasEvents = 0;
  isFlyCombined = 0;
  hasTransitions = 0;
  for(auto& i:lay)
    onAddLayer(i);
  fin.read(headRotX,headRotY);
  needToUpdate = true;

  numBones = skeleton==nullptr ? 0 : skeleton->nodes.size();
  for(auto& i:hasSamples)
    fin.read(reinterpret_cast<uint8_t&>(i));
  for(auto& i:base)
    fin.read(i);
  for(auto& i:raw)
    fin.read(i);
  for(auto& i:tr)
    fin.read(i);
  blend.reset(numBones);
  }

void Pose::setFlags(Pose::Flags f) {
  flag         = f;
  needToUpdate = true;
  }

BodyState Pose::bodyState() const {
  uint32_t b = BS_NONE;
  for(auto& i:lay)
    b = std::max(b,uint32_t(i.bs&(BS_MAX | BS_FLAG_MASK)));
  return BodyState(b);
  }

bool Pose::hasState(BodyState s) const {
  for(auto& i:lay)
    if(i.bs==s)
      return true;
  return false;
  }

bool Pose::hasStateFlag(BodyState f) const {
  for(auto& i:lay)
    if((i.bs & (BS_FLAG_MASK | BS_MOD_MASK))==f)
      return true;
  return false;
  }

void Pose::setSkeleton(const Skeleton* sk) {
  if(skeleton==sk)
    return;
  skeleton = sk;
  for(auto& i:tr)
    i.identity();
  if(skeleton!=nullptr) {
    numBones = skeleton->tr.size();
    } else {
    numBones = 0;
    }
  for(auto& i:hasSamples)
    i = S_None;
  blend.reset(numBones);
  ik.setSkeleton(skeleton);
  gaze.setSkeleton(skeleton);
  lean.setSkeleton(skeleton);
  aim.setSkeleton(skeleton);
  bowDraw.setSkeleton(skeleton);
  flinch.setSkeleton(skeleton);
  trY          = skeleton->rootTr.y;
  needToUpdate = true;
  if(lay.size()>0) //TODO
    Log::d("WARNING: ",__func__," animation adjustment is not implemented");
  lay.clear();

  if(skeleton!=nullptr)
    mkSkeleton(Matrix4x4::mkIdentity());
  }

bool Pose::startAnim(const AnimationSolver& solver, const Animation::Sequence *sq, uint8_t comb, BodyState bs,
                     StartHint hint, uint64_t tickCount) {
  if(sq==nullptr)
    return false;
  // NOTE: zero stands for no-comb, other numbers are comb-index+1
  comb = std::min<uint8_t>(comb, uint8_t(sq->comb.size()));

  const bool force = (hint&Force);

  for(auto& i:lay)
    if(i.seq->layer==sq->layer) {
      const bool hasNext   = !i.seq->next.empty();
      const bool finished  = i.seq->isFinished(layerTime(i,tickCount),i.sAnim,combo.len()) && !hasNext && (i.seq->animCls!=Animation::Loop);
      const bool interrupt = force || i.seq->canInterrupt(layerTime(i,tickCount),i.sAnim,combo.len());
      if(i.seq==sq && i.comb==comb && !finished) {
        i.bs = bs;
        return true;
        }
      if(!interrupt && !finished)
        return false;
      if(i.bs==BS_ITEMINTERACT) {
        stopItemStateAnim(solver,tickCount);
        return false;
        }
      //WA: Emulate behaviour of vanilla: lurker skips transition animations when entering the water
      const bool swimFix = (i.sAnim==tickCount && i.bs==BS_SWIM && bs==BS_SWIM);
      const Animation::Sequence* tr=nullptr;
      if(i.seq->shortName!=nullptr && sq->shortName!=nullptr && !swimFix) {
        string_frm tansition("T_",i.seq->shortName,"_2_",sq->shortName);
        tr = solver.solveFrm(tansition);
        }
      if(tr==nullptr && sq->shortName!=nullptr && i.bs==BS_STAND) {
        string_frm tansition("T_STAND_2_",sq->shortName);
        tr = solver.solveFrm(tansition);
        }
      if(tr==nullptr && i.seq->shortName!=nullptr && bs==BS_STAND) {
        string_frm tansition("T_",i.seq->shortName,"_2_STAND");
        tr = solver.solveFrm(tansition);
        bs = tr ? i.bs : bs;
        }
      onRemoveLayer(i);
      i.seq      = tr ? tr : sq;
      i.sAnim    = tickCount;
      i.comb     = comb;
      i.bs       = bs;
      onAddLayer(i);
      return true;
      }
  addLayer(sq,bs,comb,tickCount);
  return true;
  }

bool Pose::stopAnim(std::string_view name) {
  bool done=false;
  size_t ret=0;
  for(size_t i=0;i<lay.size();++i) {
    bool rm = (name.empty() || lay[i].seq->name==name);
    if(itemUseSt!=0 && lay[i].bs==BS_ITEMINTERACT)
      rm = false;

    if(!rm) {
      if(ret!=i)
        lay[ret] = lay[i];
      ret++;
      } else {
      onRemoveLayer(lay[i]);
      done=true;
      }
    }
  lay.resize(ret);
  return done;
  }

bool Pose::stopWalkAnim() {
  bool done=false;
  size_t ret=0;
  for(size_t i=0;i<lay.size();++i) {
    if(lay[i].bs!=BS_RUN && lay[i].bs!=BS_SPRINT && lay[i].bs!=BS_SNEAK && lay[i].bs!=BS_WALK) {
      if(ret!=i)
        lay[ret] = lay[i];
      ret++;
      } else {
      onRemoveLayer(lay[i]);
      done=true;
      }
    }
  lay.resize(ret);
  return done;
  }

void Pose::interrupt() {
  size_t ret=0;
  for(size_t i=0;i<lay.size();++i) {
    if(BS_FLAG_INTERRUPTABLE & lay[i].bs) {
      onRemoveLayer(lay[i]);
      } else {
      if(ret!=i)
        lay[ret] = lay[i];
      ret++;
      }
    }
  lay.resize(ret);
  }

void Pose::stopAllAnim() {
  for(auto& i:lay)
    onRemoveLayer(i);
  lay.clear();
  }

void Pose::processLayers(AnimationSolver& solver, uint64_t tickCount) {
  if(hasTransitions==0)
    return;

  size_t ret    = 0;
  bool   doSort = false;
  for(size_t i=0; i<lay.size(); ++i) {
    auto& l = lay[i];
    if(l.seq->animCls==Animation::Transition && l.seq->isFinished(layerTime(l,tickCount),l.sAnim,combo.len())) {
      auto next = solveNext(solver,l);
      if(next!=l.seq) {
        needToUpdate = true;
        onRemoveLayer(l);

        if(next!=nullptr) {
          if(l.seq==rotation)
            rotation = next;
          doSort  = l.seq->layer!=next->layer;
          auto bs = (l.bs & BS_MAX);
          // WA for:
          // 1. for swampshark, "T_STUMBLEB" has next animationn: "S_FISTRUN"
          // 2. for some of jump-back animnations, also 'next' is not empty
          if(bs==BS_STUMBLE || bs==BS_PARADE)
            l.bs = BS_NONE;
          l.seq   = next;
          l.sAnim = tickCount;
          onAddLayer(l);
          ret++;
          }
        continue;
        }
      }
    if(ret!=i)
      lay[ret] = lay[i];
    ret++;
    }
  lay.resize(ret);

  if(doSort) {
    std::sort(lay.begin(),lay.end(),[](const Layer& a,const Layer& b){
      return a.seq->layer<b.seq->layer;
      });
    }
  }

bool Pose::update(uint64_t tickCount, bool force) {
  if(lay.size()==0) {
    const bool ret = needToUpdate;
    if(needToUpdate || lastUpdate==0)
      mkSkeleton(pos);
    needToUpdate = false;
    lastUpdate   = tickCount;
    return ret;
    }

  bool needMkSkeleton = false;
  if(lastUpdate!=tickCount || force) {
    for(auto& i:lay) {
      const Animation::Sequence* seq = i.seq;
      if(0<i.comb && i.comb<=i.seq->comb.size()) {
        if(auto sx = i.seq->comb[size_t(i.comb-1)])
          seq = sx;
        }

      auto&        d         = *seq->data;
      const size_t numFrames = d.numFrames;
      if(numFrames==1 && !needToUpdate)
        continue; //mobsi

      needMkSkeleton |= updateFrame(*seq,i.bs,i.sAnim,layerTime(i,tickCount));
      }
    // switches of animations: smoothly from the pose shown
    needMkSkeleton |= blend.apply(raw,base,numBones,tickCount);
    lastUpdate   = tickCount;
    needToUpdate = needMkSkeleton;
    }

  if(needMkSkeleton || force) {
    mkSkeleton(pos);
    return true;
    }
  return false;
  }

bool Pose::updateFrame(const Animation::Sequence &s, BodyState bs, uint64_t sTime, uint64_t now) {
  auto&        d         = *s.data;
  const size_t idSize    = d.nodeIndex.size();
  const size_t numFrames = d.numFrames;
  if(numFrames==0 || idSize==0 || d.samples.size()%idSize!=0)
    return false; // error

  now = now-sTime;

  float    fpsRate = d.fpsRate;
  uint64_t frame   = uint64_t(float(now)*fpsRate);
  uint64_t frameA  = frame/1000;
  uint64_t frameB  = frame/1000+1; //next

  float    a       = float(frame%1000)/1000.f;

  if(s.animCls==Animation::Loop) {
    frameA%=d.numFrames;
    frameB%=d.numFrames;
    } else {
    frameA = std::min<uint64_t>(frameA,d.numFrames-1);
    frameB = std::min<uint64_t>(frameB,d.numFrames-1);
    }

  if(s.reverse) {
    frameA = d.numFrames-1-frameA;
    frameB = d.numFrames-1-frameB;
    }

  auto* sampleA = &d.samples[size_t(frameA*idSize)];
  auto* sampleB = &d.samples[size_t(frameB*idSize)];

  for(size_t i=0; i<idSize; ++i) {
    size_t idx = d.nodeIndex[i];
    if(idx>=numBones)
      continue;
    auto smp = mix(sampleA[i],sampleB[i],a);
    if(i==0) {
      if(bs==BS_CLIMB)
        smp.position.y = trY;
      else if(bs==BS_SWIM || bs==BS_DIVE)
        smp.position.y = trY;
      else if(bs==BS_JUMP) //else if(s.isFly())
        smp.position.y = trY;
      }

    hasSamples[idx] = S_Valid;
    raw       [idx] = smp;
    blend.drive(idx,s,sTime,sampleA[i],sampleB[i],fpsRate);
    }
  return true;
  }

void Pose::mkSkeleton(const Tempest::Matrix4x4& mt) {
  if(skeleton==nullptr)
    return;
  Matrix4x4 m = mt;
  m.translate(mkBaseTranslation());
  if(skeleton->ordered)
    implMkSkeleton(m); else
    implMkSkeleton(m,size_t(-1));
  lean.apply(tr,numBones,Vec3(mt.at(3,0),mt.at(3,1),mt.at(3,2)));
  ik.apply(tr,numBones,mt.at(3,1));
  bowDraw.hold(tr,numBones,mt);
  aim.apply(tr,numBones);
  bowDraw.apply(tr,numBones);
  gaze.apply(tr,numBones);
  flinch.apply(tr,numBones);
  }

void Pose::implMkSkeleton(const Matrix4x4 &mt) {
  if(skeleton==nullptr)
    return;
  auto& nodes      = skeleton->nodes;
  auto  BIP01_HEAD = skeleton->BIP01_HEAD;
  for(size_t i=0; i<nodes.size(); ++i) {
    size_t parent = nodes[i].parent;
    auto   mat    = hasSamples[i] ? mkMatrix(base[i]) : nodes[i].tr;

    if(parent<Resources::MAX_NUM_SKELETAL_NODES)
      tr[i] = tr[parent]*mat; else
      tr[i] = mt*mat;

    if(i==BIP01_HEAD && (headRotX!=0 || headRotY!=0)) {
      Matrix4x4& m = tr[i];
      m.rotateOY(headRotY);
      m.rotateOX(headRotX);
      }
    }
  }

void Pose::implMkSkeleton(const Tempest::Matrix4x4 &mt, size_t parent) {
  if(skeleton==nullptr)
    return;
  auto& nodes = skeleton->nodes;
  for(size_t i=0;i<nodes.size();++i){
    if(nodes[i].parent!=parent)
      continue;
    auto mat = hasSamples[i] ? mkMatrix(base[i]) : nodes[i].tr;
    tr[i] = mt*mat;
    implMkSkeleton(tr[i],i);
    }
  }

const Animation::Sequence* Pose::solveNext(const AnimationSolver &solver, const Layer& lay) {
  auto sq = lay.seq;

  if((lay.bs & BS_ITEMINTERACT)==BS_ITEMINTERACT && itemUseSt!=itemUseDestSt) {
    int sA = itemUseSt, sB = itemUseSt, nextState = 0;
    if(itemUseSt<itemUseDestSt) {
      sB++;
      nextState = itemUseSt+1;
      } else {
      sB--;
      nextState = itemUseSt-1;
      }
    char scheme[64]={};
    sq->schemeName(scheme);

    const Animation::Sequence* ret = nullptr;
    if(itemUseSt>itemUseDestSt) {
      string_frm T_ID_SX_2_STAND("T_",scheme,"_S",itemUseSt,"_2_STAND");
      ret = solver.solveFrm(T_ID_SX_2_STAND);
      }

    if(ret==nullptr) {
      string_frm T_ID_Sa_2_Sb("T_",scheme,"_S",sA,"_2_S",sB);
      ret = solver.solveFrm(T_ID_Sa_2_Sb);
      }

    if(ret==nullptr && itemUseDestSt>=0)
      return sq;
    itemUseSt = nextState;
    return ret;
    }

  if(sq==rotation)
    return sq;

  return solver.solveNext(*sq);
  }

void Pose::addLayer(const Animation::Sequence *seq, BodyState bs, uint8_t comb, uint64_t tickCount) {
  if(seq==nullptr)
    return;
  Layer l;
  l.seq   = seq;
  l.sAnim = tickCount;
  l.bs    = bs;
  l.comb  = comb;
  lay.push_back(l);
  onAddLayer(lay.back());
  std::sort(lay.begin(),lay.end(),[](const Layer& a,const Layer& b){
    return a.seq->layer<b.seq->layer;
    });
  }

void Pose::onAddLayer(Pose::Layer& l) {
  l.speed = speedOf(l.seq);
  if(hasLayerEvents(l))
    hasEvents++;
  if(l.seq->isFly())
    isFlyCombined++;
  if(l.seq->animCls==Animation::Transition)
    hasTransitions++;
  needToUpdate = true;
  }

void Pose::onRemoveLayer(const Pose::Layer &l) {
  if(l.seq==rotation)
    rotation=nullptr;
  if(hasLayerEvents(l))
    hasEvents--;
  if(l.seq->animCls==Animation::Transition)
    hasTransitions--;
  if(l.seq->isFly())
    isFlyCombined--;
  // the layers under it take the bones back (AnimBlend sees the switch): even a still one
  needToUpdate = true;
  }

bool Pose::hasLayerEvents(const Pose::Layer& l) {
  if(l.seq==nullptr)
    return false;
  return
      l.seq->data->events.size()>0 ||
      l.seq->data->mmStartAni.size()>0 ||
      l.seq->data->gfx.size()>0;
  }

void Pose::processSfx(Npc &npc, uint64_t tickCount) {
  for(auto& i:lay)
    i.seq->processSfx(layerTime(i,lastUpdate),i.sAnim,layerTime(i,tickCount),&npc,nullptr);
  }

void Pose::processSfx(Interactive& mob, uint64_t tickCount) {
  for(auto& i:lay)
    i.seq->processSfx(layerTime(i,lastUpdate),i.sAnim,layerTime(i,tickCount),nullptr,&mob);
  }

void Pose::processPfx(MdlVisual& visual, World& world, uint64_t tickCount) {
  for(auto& i:lay)
    i.seq->processPfx(layerTime(i,lastUpdate),i.sAnim,layerTime(i,tickCount),visual,world);
  }

bool Pose::processEvents(uint64_t &barrier, uint64_t now, Animation::EvCount &ev) const {
  if(hasEvents==0) {
    barrier=now;
    return false;
    }

  for(auto& i:lay)
    i.seq->processEvents(layerTime(i,barrier),i.sAnim,layerTime(i,now),ev);
  barrier=now;
  return hasEvents>0;
  }

void Pose::setObjectMatrix(const Tempest::Matrix4x4& obj, bool sync) {
  if(pos==obj)
    return;
  pos = obj;
  if(sync)
    mkSkeleton(pos); else
    needToUpdate = true;
  }

Tempest::Vec3 Pose::animMoveSpeed(uint64_t tickCount, uint64_t dt) const {
  for(size_t i=lay.size(); i>0; ) {
    --i;
    auto& lx = lay[i];
    if(!lx.seq->data->hasMoveTr)
      continue;
    return lx.seq->speed(layerTime(lx,tickCount)-lx.sAnim,uint64_t(float(dt)*lx.speed));
    }
  return Tempest::Vec3();
  }

bool Pose::isDefParWindow(uint64_t tickCount) const {
  for(auto& i:lay)
    if(i.seq->isDefParWindow(layerTime(i,tickCount)-i.sAnim))
      return true;
  return false;
  }

bool Pose::isDefWindow(uint64_t tickCount) const {
  for(auto& i:lay)
    if(i.seq->isDefWindow(layerTime(i,tickCount)-i.sAnim))
      return true;
  return false;
  }

bool Pose::isDefence(uint64_t tickCount) const {
  for(auto& i:lay) {
    if(i.bs==BS_PARADE && i.seq->isDefWindow(layerTime(i,tickCount)-i.sAnim))
      return true;
    }
  return false;
  }

bool Pose::isJumpBack(uint64_t tickCount) const {
  for(auto& i:lay) {
    if(i.bs==BS_PARADE && i.seq->data->defWindow.empty())
      return true;
    if(i.bs==BS_PARADE && i.seq->isDefWindow(layerTime(i,tickCount)-i.sAnim))
      return true;
    }
  return false;
  }

bool Pose::isJumpAnim() const {
  for(auto& i:lay) {
    if(i.bs!=BS_JUMP || i.seq->animCls!=Animation::Transition)
      continue;
    return true;
    }
  return false;
  }

bool Pose::isFlyAnim() const {
  return isFlyCombined>0;
  }

bool Pose::isStanding() const {
  if(lay.size()!=1 || lay[0].seq->animCls==Animation::Transition)
    return false;
  auto& s = *lay[0].seq;
  if(s.isIdle())
    return true;
  // check common idle animations
  return s.name=="S_FISTRUN"  || s.name=="S_MAGRUN"  ||
         s.name=="S_1HRUN"    || s.name=="S_BOWRUN"  ||
         s.name=="S_2HRUN"    || s.name=="S_CBOWRUN" ||
         s.name=="S_RUN"      || s.name=="S_WALK"    ||
         s.name=="S_FISTWALK" || s.name=="S_MAGWALK" ||
         s.name=="S_1HWALK"   || s.name=="S_BOWWALK" ||
         s.name=="S_2HWALK"   || s.name=="S_CBOWWALK";
  }

bool Pose::isPrehit(uint64_t now) const {
  for(auto& i:lay)
    if(i.seq->isPrehit(i.sAnim,layerTime(i,now)))
      return true;
  return false;
  }

bool Pose::isAttackAnim() const {
  for(auto& i:lay)
    if(i.seq->isAttackAnim())
      return true;
  return false;
  }

bool Pose::isIdle() const {
  for(auto& i:lay)
    if(!i.seq->isIdle())
      return false;
  return true;
  }

bool Pose::isInAnim(std::string_view sq) const {
  for(auto& i:lay)
    if(i.seq->name==sq)
      return true;
  return false;
  }

bool Pose::isInAnim(const Animation::Sequence *sq) const {
  for(auto& i:lay)
    if(i.seq==sq)
      return true;
  return false;
  }

bool Pose::hasAnim() const {
  return lay.size()>0;
  }

uint64_t Pose::animationTotalTime() const {
  uint64_t ret=0;
  for(auto& i:lay)
    ret = std::max(ret,uint64_t(i.seq->totalTime()/i.speed));
  return ret;
  }

uint64_t Pose::atkTotalTime() const {
  uint16_t comboLen = combo.len();
  uint64_t ret=0;
  for(auto& i:lay)
    ret = std::max(ret,uint64_t(float(i.seq->atkTotalTime(comboLen))/i.speed));
  return ret;
  }

const Animation::Sequence* Pose::continueCombo(const AnimationSolver &solver, const Animation::Sequence *sq,
                                               BodyState bs, uint64_t tickCount) {
  if(sq==nullptr)
    return nullptr;

  Layer* prev = nullptr;
  for(auto& i:lay)
    if(i.seq->layer==sq->layer) {
      prev = &i;
      break;
      }

  if(prev==nullptr) {
    combo = ComboState();
    return nullptr;
    }

  auto&    d     = *prev->seq->data;
  uint64_t t     = layerTime(*prev,tickCount)-prev->sAnim;
  size_t   id    = combo.len()*2;

  if(0==d.defWindow.size() || 0==d.defHitEnd.size()) {
    if(!startAnim(solver,sq,prev->comb,bs,Pose::NoHint,tickCount))
      return nullptr;
    combo = ComboState();
    return sq;
    }

  if(sq->data->defHitEnd.size()==0) {
    // hit -> block
    startAnim(solver,sq,prev->comb,bs,Pose::Force,tickCount);
    combo = ComboState();
    return sq;
    }

  if(id+1>=d.defWindow.size()) {
    combo.setBreak();
    return nullptr;
    }

  if(!(d.defWindow[id+0]<t && t<=d.defWindow[id+1])) {
    if(prev->seq->name==sq->name && sq->data->defHitEnd.size()>0)
      combo.setBreak();
    return nullptr;
    }

  if(combo.isBreak())
    return nullptr;

  if(prev->seq->name!=sq->name) {
    startAnim(solver,sq,prev->comb,bs,Pose::Force,tickCount);
    combo = ComboState();
    return sq;
    }

  if(combo.len()<d.defHitEnd.size())
    prev->sAnim = tickCount - d.defHitEnd[combo.len()];
  combo.incLen();
  return prev->seq;
  }

uint16_t Pose::comboLength() const {
  return combo.len();
  }

const Tempest::Matrix4x4 Pose::rootNode() const {
  size_t id = 0;
  if(skeleton->rootNodes.size())
    id = skeleton->rootNodes[0];

  const auto& nodes = skeleton->nodes;
  if(id>=nodes.size())
    return Matrix4x4::mkIdentity();

  auto ret = hasSamples[id] ? mkMatrix(base[id]) : nodes[id].tr;
  return ret;
  }

const Matrix4x4 Pose::rootBone() const {
  size_t id = 0;
  if(skeleton->rootNodes.size())
    id = skeleton->rootNodes[0];
  return tr[id];
  }

const Tempest::Matrix4x4& Pose::bone(size_t id) const {
  return tr[id];
  }

size_t Pose::boneCount() const {
  return numBones;
  }

size_t Pose::findNode(std::string_view b) const {
  if(skeleton!=nullptr)
    return skeleton->findNode(b);
  return size_t(-1);
  }

void Pose::setHeadRotation(float dx, float dz) {
  headRotX = dx;
  headRotY = dz;
  }

Vec2 Pose::headRotation() const {
  return Vec2(headRotX,headRotY);
  }

bool Pose::updateGaze(const Gaze::Input& in) {
  if(!gaze.update(in))
    return false;
  mkSkeleton(pos);
  return true;
  }

std::string Pose::animNames() const {
  std::string s;
  for(auto& i:lay)
    if(i.seq!=nullptr)
      s += (s.empty() ? "" : " ") + i.seq->name;
  return s;
  }

bool Pose::updateFlinch(const Flinch::Input& in) {
  if(!flinch.update(in))
    return false;
  mkSkeleton(pos);
  return true;
  }

bool Pose::updateAim(const Aim::Input& in) {
  if(!aim.update(in))
    return false;
  mkSkeleton(pos);
  return true;
  }

uint64_t Pose::layerTime(const Layer& l, uint64_t t) {
  if(l.speed==1.f || t<=l.sAnim)
    return t;
  return l.sAnim + uint64_t(double(t-l.sAnim)*double(l.speed));
  }

float Pose::speedOf(const Animation::Sequence* seq) const {
  if(seq==nullptr)
    return 1;
  for(auto& i:speeds)
    if(i.first==seq->name)
      return i.second;
  return 1;
  }

void Pose::setAnimSpeed(std::string_view name, float k) {
  for(size_t i=0; i<speeds.size(); ++i)
    if(speeds[i].first==name) {
      if(k==1.f)
        speeds.erase(speeds.begin()+ptrdiff_t(i)); else
        speeds[i].second = k;
      return;
      }
  if(k!=1.f)
    speeds.emplace_back(std::string(name),k);
  }

bool Pose::animRelative(std::string_view name, float at, size_t from, size_t to, Tempest::Matrix4x4& out) const {
  if(skeleton==nullptr || from>=skeleton->nodes.size() || to>=skeleton->nodes.size())
    return false;
  const Animation::Sequence* seq = nullptr;
  for(auto& i:lay)
    if(i.seq->name==name)
      seq = i.seq;
  if(seq==nullptr || seq->data==nullptr)
    return false;
  auto&        d      = *seq->data;
  const size_t idSize = d.nodeIndex.size();
  if(d.numFrames==0 || idSize==0 || d.samples.size()<size_t(d.numFrames)*idSize)
    return false;
  const float  f  = std::clamp(at,0.f,1.f)*float(d.numFrames-1);
  const size_t fa = std::min(size_t(f),size_t(d.numFrames-1));
  const size_t fb = std::min(fa+1,size_t(d.numFrames-1));
  const float  a  = f-float(fa);
  auto local = [&](size_t node) {
    for(size_t i=0; i<idSize; ++i)
      if(d.nodeIndex[i]==node)
        return mkMatrix(mix(d.samples[fa*idSize+i],d.samples[fb*idSize+i],a));
    return skeleton->nodes[node].tr;
    };
  Tempest::Matrix4x4 m;
  m.identity();
  for(size_t n=to; n!=from; n=skeleton->nodes[n].parent) {
    if(n>=skeleton->nodes.size())
      return false;
    m = local(n)*m;
    }
  out = m;
  return true;
  }

float Pose::animDuration(std::string_view name) const {
  for(auto& i:lay)
    if(i.seq->name==name)
      return i.seq->totalTime()/1000.f/i.speed;
  return 0;
  }

float Pose::animProgress(std::string_view name, uint64_t tickCount) const {
  for(auto& i:lay)
    if(i.seq->name==name) {
      const float total = i.seq->totalTime();
      if(total<=0.f)
        return 1;
      return std::clamp(float(layerTime(i,tickCount)-i.sAnim)/total, 0.f, 1.f);
      }
  return -1;
  }

bool Pose::updateBowDraw(const BowDraw::Input& input) {
  // the reload: where the drawing hand takes the arrow (the animation at that moment), relative to the chest
  BowDraw::Input in = input;
  size_t chest = size_t(-1), hand = size_t(-1), elbow = size_t(-1);
  if(in.reload>=0.f)
    in.reloadTime = animDuration(BowDraw::ReloadAnim);
  if(in.reload>=0.f && bowDraw.grabNodes(chest,hand,elbow))
    in.grabKnown = animRelative(BowDraw::ReloadAnim,BowDraw::GrabAt,chest,hand,in.grabHand) &&
                   animRelative(BowDraw::ReloadAnim,BowDraw::GrabAt,chest,elbow,in.grabElbow);
  if(!bowDraw.update(in))
    return false;
  mkSkeleton(pos);
  return true;
  }

bool Pose::updateLean(const Lean::Input& in) {
  if(!lean.update(in))
    return false;
  mkSkeleton(pos);
  return true;
  }

bool Pose::updateFootIk(const FootIk::Input& in) {
  if(!ik.update(in))
    return false;
  mkSkeleton(pos);
  return true;
  }

void Pose::setAnimRotate(const AnimationSolver &solver, Npc &npc, WeaponState fightMode, AnimationSolver::TurnType turn, int dir) {
  const Animation::Sequence *sq = nullptr;
  if(dir==0) {
    if(rotation!=nullptr) {
      if(stopAnim(rotation->name))
        rotation = nullptr;
      }
    return;
    }
  if(bodyState()!=BS_STAND)
    return;

  enum AnimationSolver::Anim ani;
  if(turn==AnimationSolver::TurnType::Whirl) {
    ani = (dir<0)?AnimationSolver::Anim::WhirlL:AnimationSolver::Anim::WhirlR;
    } else {
    ani = (dir<0)?AnimationSolver::Anim::RotL:AnimationSolver::Anim::RotR;
    }

  sq = solver.solveAnim(ani,fightMode,npc.walkMode(),*this);
  if(rotation!=nullptr) {
    if(sq!=nullptr && rotation->name==sq->name)
      return;
    if(!stopAnim(rotation->name))
      return;
    }
  if(sq==nullptr)
    return;
  if(startAnim(solver,sq,0,BS_FLAG_FREEHANDS,Pose::NoHint,npc.world().tickCount())) {
    rotation = sq;
    return;
    }
  }

const Animation::Sequence* Pose::setAnimItem(const AnimationSolver &solver, Npc &npc, std::string_view scheme, int state) {
  string_frm T_ID_STAND_2_S0("T_",scheme,"_STAND_2_S0");
  const Animation::Sequence *sq = solver.solveFrm(T_ID_STAND_2_S0);
  if(startAnim(solver,sq,0,BS_ITEMINTERACT,Pose::NoHint,npc.world().tickCount())) {
    itemUseSt     = 0;
    itemUseDestSt = state;
    return sq;
    }
  return nullptr;
  }

bool Pose::stopItemStateAnim(const AnimationSolver& solver, uint64_t tickCount) {
  if(itemUseSt<0)
    return true;
  itemUseDestSt = -1;
  for(auto& i:lay)
    if(i.bs==BS_ITEMINTERACT) {
      auto next = solveNext(solver,i);
      if(next==nullptr)
        continue;
      onRemoveLayer(i);
      i.seq   = next;
      i.sAnim = tickCount;
      onAddLayer(i);
      }
  return true;
  }

const Matrix4x4* Pose::transform() const {
  return tr;
  }

Pose::Probe Pose::playerProbe;
Pose::Probe Pose::subjectProbe;
const void* Pose::probeNpc = nullptr;

void Pose::probe(Probe& p) {
  p.layers.clear();
  for(auto& i:lay) {
    if(!p.layers.empty())
      p.layers += '+';
    p.layers += i.seq->name;
    p.layers += ':' + std::to_string(i.seq->blendIn) + '/' + std::to_string(i.seq->blendOut);
    }
  blend.probe(p.blend);
  p.valid    = skeleton!=nullptr;
  p.skeleton = skeleton;
  p.numBones = skeleton!=nullptr ? std::min(numBones,skeleton->nodes.size()) : 0;
  if(!p.valid)
    return;

  // the bones as the animation has them, without the object matrix, the root moved to x=z=0
  // like mkSkeleton does
  auto&     nodes = skeleton->nodes;
  Matrix4x4 m[Resources::MAX_NUM_SKELETAL_NODES];
  bool      done[Resources::MAX_NUM_SKELETAL_NODES] = {};
  const Matrix4x4 root = rootNode();
  Matrix4x4 org = Matrix4x4::mkIdentity();
  org.translate(Vec3(-root.at(3,0),0,-root.at(3,2)));

  for(size_t i=0; i<p.numBones; ++i) {
    // parents first, whatever the order of the nodes
    size_t chain[Resources::MAX_NUM_SKELETAL_NODES];
    size_t n = 0;
    for(size_t b=i; b<p.numBones && !done[b] && n<p.numBones; b=nodes[b].parent)
      chain[n++] = b;
    while(n>0) {
      const size_t b      = chain[--n];
      const size_t parent = nodes[b].parent;
      const auto   mat    = hasSamples[b] ? mkMatrix(base[b]) : nodes[b].tr;
      m[b]    = (parent<p.numBones ? m[parent] : org)*mat;
      done[b] = true;
      }
    p.joint[i] = Vec3(m[i].at(3,0),m[i].at(3,1),m[i].at(3,2));
    }
  }

Vec3 Pose::mkBaseTranslation() {
  if(numBones==0)
    return Vec3();

  auto  b0 = rootNode();

  float dx = b0.at(3,0);
  float dy = 0;
  float dz = b0.at(3,2);

  if((flag&NoTranslation)==NoTranslation) {
    dy = b0.at(3,1);
    // dy = b0.at(3,1) - translateY();
    }

  return Vec3(-dx,-dy,-dz);
  }

template<class T, class F>
void Pose::removeIf(T &t, F f) {
  size_t ret=0;
  for(size_t i=0;i<t.size();++i) {
    if( !f(t[i]) ) {
      if(ret!=i)
        t[ret] = t[i];
      ret++;
      }
    }
  t.resize(ret);
  }
