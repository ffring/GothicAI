#include "instancestorage.h"
#include "shaders.h"
#include "utils/workers.h"
#include "frameprofile.h"

#include <Tempest/Log>
#include <algorithm>
#include <cstdint>
#include <atomic>

using namespace Tempest;

static uint32_t nextPot(uint32_t v) {
  v--;
  v |= v >> 1;
  v |= v >> 2;
  v |= v >> 4;
  v |= v >> 8;
  v |= v >> 16;
  v++;
  return v;
  }

static uint32_t alignAs(uint32_t sz, uint32_t alignment) {
  return ((sz+alignment-1)/alignment)*alignment;
  }

static void bitSet(std::vector<uint32_t>& b, size_t id) {
  static_assert(sizeof(std::atomic<uint32_t>)==sizeof(uint32_t));
  auto& bits = b[id/32];
  id %= 32;
  reinterpret_cast<std::atomic<uint32_t>&>(bits).fetch_or(1u << id, std::memory_order_relaxed);
  }

static void bitSetRange(std::vector<uint32_t>& b, size_t first, size_t count) {
  static_assert(sizeof(std::atomic<uint32_t>)==sizeof(uint32_t));
  while(count>0) {
    const size_t offset = first%32;
    const size_t span = std::min(count,32-offset);
    const uint32_t mask = (UINT32_MAX >> (32-span)) << offset;
    auto& bits = b[first/32];
    reinterpret_cast<std::atomic<uint32_t>&>(bits).fetch_or(mask, std::memory_order_relaxed);
    first += span;
    count -= span;
    }
  }

static bool bitAt(std::vector<uint32_t>& b, size_t id) {
  auto bits = b[id/32];
  id %= 32;
  return bits & (1u << id);
  }

using namespace Tempest;

InstanceStorage::Id::Id(Id&& other) noexcept
  :owner(other.owner), rgn(other.rgn) {
  other.owner = nullptr;
  }

InstanceStorage::Id& InstanceStorage::Id::operator = (Id&& other) noexcept {
  std::swap(owner, other.owner);
  std::swap(rgn,   other.rgn);
  return *this;
  }

InstanceStorage::Id::~Id() {
  if(owner!=nullptr)
    owner->free(rgn);
  }

void InstanceStorage::Id::set(const Tempest::Matrix4x4* mat) {
  if(owner==nullptr)
    return;

  auto data = reinterpret_cast<Matrix4x4*>(owner->dataCpu.data() + rgn.begin);
  const bool identical = rgn.asize==0 || std::memcmp(data,mat,rgn.asize)==0;
  if(FrameProfile::enabled()) {
    FrameProfile::add(FrameProfile::Amount::PaletteCalls,1);
    FrameProfile::add(FrameProfile::Amount::PaletteBytes,rgn.asize);
    if(identical) {
      FrameProfile::add(FrameProfile::Amount::IdenticalCalls,1);
      FrameProfile::add(FrameProfile::Amount::IdenticalBytes,rgn.asize);
      }
    }
  if(identical)
    return;
  std::memcpy(data, mat, rgn.asize);

  bitSetRange(owner->durty,rgn.begin/blockSz,(rgn.asize+blockSz-1)/blockSz);
  }

void InstanceStorage::Id::set(const Tempest::Matrix4x4& obj, size_t offset) {
  if(owner==nullptr)
    return;

  auto data = reinterpret_cast<Matrix4x4*>(owner->dataCpu.data() + rgn.begin);
  if(data[offset] == obj)
    return;
  data[offset] = obj;
  bitSet(owner->durty, (rgn.begin+offset*sizeof(Matrix4x4))/blockSz);
  }

void InstanceStorage::Id::set(const void* data, size_t offset, size_t size) {
  if(owner==nullptr)
    return;

  auto src = reinterpret_cast<const uint8_t*>(data);
  auto dst = (owner->dataCpu.data() + rgn.begin + offset);

  if(std::memcmp(src, dst, size)==0)
    return;

  if((offset%blockSz)==0) {
    for(size_t i=0; i<size; i+=blockSz) {
      const size_t sz = std::min(blockSz, size-i);
      std::memcpy(dst+i, src+i, sz);
      bitSet(owner->durty, (rgn.begin + offset + i)/blockSz);
      }
    } else {
    for(size_t i=0; i<size; ++i) {
      dst[i] = src[i];
      bitSet(owner->durty, (rgn.begin + offset + i)/blockSz);
      }
    }
  }


InstanceStorage::InstanceStorage() {
  dataCpu.reserve(131072);
  dataCpu.resize(sizeof(Matrix4x4)); // also avoid null-ssbo
  reinterpret_cast<Matrix4x4*>(dataCpu.data())->identity();

  patchCpu.reserve(4*1024*1024);
  patchBlock.reserve(16*1024);

  uploadTh = std::thread([this](){ uploadMain(); });
  }

InstanceStorage::~InstanceStorage() {
  {
    std::unique_lock<std::mutex> lck(sync);
    uploadFId = Resources::MaxFramesInFlight;
  }
  uploadCnd.notify_one();
  uploadTh.join();
  }

bool InstanceStorage::commit(Encoder<CommandBuffer>& cmd, uint8_t fId) {
  FrameProfile::Scope timing(FrameProfile::Zone::StorageCommit);
  auto& device = Resources::device();

  std::atomic_thread_fence(std::memory_order_acquire);
  join();

  const size_t dataSize = (dataCpu.size() + 0xFFF) & ~size_t(0xFFF);
  if(dataGpu.byteSize()!=dataSize) {
    FrameProfile::Scope growth(FrameProfile::Zone::StorageGrow);
    FrameProfile::add(FrameProfile::Amount::GrowBytes,dataCpu.size());
    Resources::recycle(std::move(dataGpu));
    dataGpu = device.ssbo(BufferHeap::Device,Tempest::Uninitialized,dataSize);
    dataGpu.update(dataCpu);
    std::memset(durty.data(), 0, durty.size()*sizeof(uint32_t));
    return true;
    }

  FrameProfile::Scope packing(FrameProfile::Zone::StoragePack);
  patchBlock.clear();
  size_t payloadSize = 0;
  for(size_t i = 0; i<blockCnt; ++i) {
    if(i%32==0 && durty[i/32]==0) {
      i+=31;
      continue;
      }
    if(!bitAt(durty,i))
      continue;
    auto begin = i; ++i;
    while(i<blockCnt) {
      if(!bitAt(durty,i))
        break;
      ++i;
      }

    uint32_t size    = uint32_t((i-begin)*blockSz);
    uint32_t chunkSz = 256;

    Path p = {};
    p.dst  = uint32_t(begin*blockSz);
    p.src  = uint32_t(payloadSize);
    while(size>0) {
      p.size       = std::min<uint32_t>(size, chunkSz);
      size        -= p.size;
      patchBlock.push_back(p);

      payloadSize += p.size;
      p.dst       += p.size;
      p.src       += p.size;
      }
    }
  std::memset(durty.data(), 0, durty.size()*sizeof(durty[0]));

  if(patchBlock.size()==0)
    return false;

  const size_t headerSize = patchBlock.size()*sizeof(Path);
  patchCpu.resize(headerSize + payloadSize);
  for(auto& i:patchBlock) {
    i.src += uint32_t(headerSize);
    std::memcpy(patchCpu.data()+i.src, dataCpu.data() + i.dst, i.size);

    // uint's in shader
    i.src  /= 4;
    i.dst  /= 4;
    i.size /= 4;
    }
  std::memcpy(patchCpu.data(), patchBlock.data(), headerSize);
  FrameProfile::add(FrameProfile::Amount::PatchCalls,1);
  FrameProfile::add(FrameProfile::Amount::PatchBytes,headerSize+payloadSize);
  FrameProfile::add(FrameProfile::Amount::PatchBlocks,patchBlock.size());
  packing.finish();

  FrameProfile::Scope dispatch(FrameProfile::Zone::StorageDispatch);
  auto& path = patchGpu[fId];
  if(path.byteSize() < headerSize + payloadSize) {
    path = device.ssbo(BufferHeap::Upload, Uninitialized, headerSize + payloadSize);
    }

  {
    std::unique_lock<std::mutex> lck(sync);
    uploadFId = fId;
  }
  uploadCnd.notify_one();
  //path.update(patchCpu);

  cmd.setFramebuffer({});
  cmd.setBinding(0, dataGpu);
  cmd.setBinding(1, path);
  cmd.setPipeline(Shaders::inst().patch);
  cmd.dispatch(patchBlock.size());
  return false;
  }

void InstanceStorage::join() {
  FrameProfile::Scope timing(FrameProfile::Zone::StorageJoin);
  while(true) {
    std::unique_lock<std::mutex> lck(sync);
    if(uploadFId<0)
      break;
    }
  }

InstanceStorage::Id InstanceStorage::alloc(const size_t size) {
  if(size==0)
    return Id(*this,Range());

  const auto nsize = alignAs(nextPot(uint32_t(size)), alignment);
  for(size_t i=0; i<rgn.size(); ++i) {
    if(rgn[i].size==nsize) {
      auto ret = rgn[i];
      ret.asize = size;
      rgn.erase(rgn.begin()+intptr_t(i));
      return Id(*this,ret);
      }
    }
  size_t retId = size_t(-1);
  for(size_t i=0; i<rgn.size(); ++i) {
    if(rgn[i].size>nsize && (retId==size_t(-1) || rgn[i].size<rgn[retId].size)) {
      retId = i;
      }
    }
  if(retId!=size_t(-1)) {
    Range ret = rgn[retId];
    ret.size  = nsize;
    ret.asize = size;
    rgn[retId].begin += nsize;
    rgn[retId].size  -= nsize;
    return Id(*this,ret);
    }
  Range r;
  r.begin = dataCpu.size();
  r.size  = nsize;
  r.asize = size;

  dataCpu.resize(dataCpu.size() + nsize);

  blockCnt = (dataCpu.size()+blockSz-1)/blockSz;
  durty.resize((blockCnt+32-1)/32, 0);
  // GPU capacity is page-rounded and its unused tail is uninitialized. Upload
  // every newly reserved block, including padding, even if the first palette is
  // zero or a later realloc exposes padding without changing the CPU bytes.
  bitSetRange(durty,r.begin/blockSz,r.size/blockSz);
  return Id(*this,r);
  }

bool InstanceStorage::realloc(Id& id, const size_t size) {
  if(size==0) {
    if(id.isEmpty())
      return false;
    id = Id(*this,Range());
    return true;
    }

  if(size<=id.rgn.size) {
    id.rgn.asize = size;
    return false;
    }

  auto next = alloc(size);
  if(id.isEmpty()) {
    id = std::move(next);
    return true;
    }

  auto data = dataCpu.data();
  std::memcpy(data+next.rgn.begin, data+id.rgn.begin, id.rgn.asize);
  bitSetRange(durty,next.rgn.begin/blockSz,(id.rgn.asize+blockSz-1)/blockSz);
  id = std::move(next);
  return true;
  }

const Tempest::StorageBuffer& InstanceStorage::ssbo() const {
  return dataGpu;
  }

void InstanceStorage::free(const Range& r) {
  for(auto& i:rgn) {
    if(i.begin+i.size==r.begin) {
      i.size  += r.size;
      return;
      }
    if(r.begin+r.size==i.begin) {
      i.begin -= r.size;
      i.size  += r.size;
      return;
      }
    }
  auto at = std::lower_bound(rgn.begin(),rgn.end(),r,[](const Range& l, const Range& r){
    return l.begin<r.begin;
    });
  rgn.insert(at,r);
  }

void InstanceStorage::uploadMain() {
  Workers::setThreadName("InstanceStorage upload");
  while(true) {
    std::unique_lock<std::mutex> lck(sync);
    // a frame set between two waits is not lost: join() would spin on it forever
    uploadCnd.wait(lck,[this](){ return uploadFId>=0; });
    if(uploadFId==Resources::MaxFramesInFlight)
      break;

    {
    // Execution interval and payload size, deliberately NOT the originating
    // dispatch frame. An async upload may overlap a later render attempt.
    FrameProfile::Scope upload(FrameProfile::Zone::StorageUploadWorker);
    upload.add(FrameProfile::Amount::UploadBytes,patchCpu.size());
    patchGpu[uploadFId].update(patchCpu);
    }
    uploadFId = -1;
    }
  }
