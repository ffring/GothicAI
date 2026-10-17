#include "graphics/instancestorage.h"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

// Inspect only CPU state. No device is constructed: any production GPU access
// fails through Resources::device instead of being hidden by a graphics stub.
struct InstanceStorageContractAccess {
  static const std::vector<uint8_t>& cpu(const InstanceStorage& storage) { return storage.dataCpu; }
  static const std::vector<uint32_t>& dirty(const InstanceStorage& storage) { return storage.durty; }
  static void uploaded(InstanceStorage& storage) { std::fill(storage.durty.begin(),storage.durty.end(),0); }
  };

namespace {
  using Matrix = Tempest::Matrix4x4;
  constexpr size_t blockBytes = 64;

  void require(bool condition, const char* message) {
    if(condition) return;
    std::fprintf(stderr,"FAIL: %s\n",message);
    std::exit(EXIT_FAILURE);
    }

  size_t begin(const InstanceStorage::Id& id) { return size_t(id.offsetId<Matrix>())*sizeof(Matrix); }

  // An independent page image makes uninitialized GPU tail bytes observable.
  // The production CPU dirty state decides which blocks the image receives.
  struct MockPage {
    std::vector<uint8_t> bytes;

    void commit(InstanceStorage& storage) {
      const auto& cpu=InstanceStorageContractAccess::cpu(storage);
      const auto& dirty=InstanceStorageContractAccess::dirty(storage);
      const size_t capacity=((cpu.size()+4095)/4096)*4096;
      if(bytes.size()!=capacity) {
        bytes.assign(capacity,0xcd);
        std::copy(cpu.begin(),cpu.end(),bytes.begin());
        } else {
        // The constructor identity is uploaded by the full-copy path, but no
        // dirty metadata exists until the first nonempty allocation.
        for(size_t block=0;block<cpu.size()/blockBytes;++block)
          if(block/32<dirty.size() && (dirty[block/32] & (uint32_t(1)<<(block%32)))!=0)
            std::copy_n(cpu.data()+block*blockBytes,blockBytes,bytes.data()+block*blockBytes);
        }
      InstanceStorageContractAccess::uploaded(storage);
      }

    bool equal(size_t at, const void* source, size_t size) const {
      return at<=bytes.size() && size<=bytes.size()-at && std::memcmp(bytes.data()+at,source,size)==0;
      }
    };

  void expectDirty(const InstanceStorage& storage, const std::vector<size_t>& expected) {
    const auto& dirty=InstanceStorageContractAccess::dirty(storage);
    for(const auto block:expected)
      require(block/32<dirty.size(),"every expected dirty block must have metadata");
    for(size_t block=0;block<dirty.size()*32;++block) {
      const bool marked=(dirty[block/32] & (uint32_t(1)<<(block%32)))!=0;
      require(marked==(std::find(expected.begin(),expected.end(),block)!=expected.end()),
              "dirty blocks must match exactly, including old pending bits");
      }
    }

  void testZeroPaddingReuse() {
    InstanceStorage storage;
    MockPage gpu;
    gpu.commit(storage);
    const auto initial=gpu.bytes;
    auto empty=storage.alloc(0);
    empty.set(static_cast<const Matrix*>(nullptr));
    expectDirty(storage,{});
    gpu.commit(storage);
    require(gpu.bytes==initial,"empty palette must not read a null pointer or change storage");

    // 65 used bytes reserve 128 bytes, all within an existing GPU page.
    auto id=storage.alloc(65);
    require(begin(id)==64,"first allocation must follow the initial identity matrix");
    std::vector<Matrix> zero(2);
    id.set(zero.data());
    expectDirty(storage,{1,2});
    gpu.commit(storage);
    require(gpu.equal(begin(id),zero.data(),128),"cold zero palette and padding must replace unknown GPU tail");
    require(gpu.bytes[192]==0xcd,"initialization must not overwrite unrelated GPU capacity");

    std::vector<Matrix> partial(2,Matrix::mkIdentity());
    id.set(partial.data());
    expectDirty(storage,{1,2});
    gpu.commit(storage);
    require(gpu.equal(begin(id),partial.data(),65),"changed partial-block palette must upload its exact used bytes");
    id.set(zero.data());
    gpu.commit(storage);

    require(!storage.realloc(id,128),"growth within reserved padding must retain the region");
    id.set(zero.data());
    expectDirty(storage,{});
    gpu.commit(storage);
    require(gpu.equal(begin(id),zero.data(),128),"in-place growth must expose initialized padding");

    std::vector<Matrix> pose(2,Matrix::mkIdentity());
    pose[1].set(3,0,42.f);
    id.set(pose.data());
    expectDirty(storage,{1,2});
    id.set(pose.data());
    expectDirty(storage,{1,2});
    gpu.commit(storage);
    require(gpu.equal(begin(id),pose.data(),128),"identical second write must retain an earlier pending upload");

    const auto oldBegin=begin(id);
    id=InstanceStorage::Id();
    auto reused=storage.alloc(128);
    require(begin(reused)==oldBegin,"fixture must reuse the released range");
    reused.set(pose.data());
    expectDirty(storage,{});
    gpu.commit(storage);
    require(gpu.equal(begin(reused),pose.data(),128),"identical reused range must preserve the existing GPU palette");
    reused.set(zero.data());
    expectDirty(storage,{1,2});
    gpu.commit(storage);
    require(gpu.equal(begin(reused),zero.data(),128),"changed reused range must still upload exact bytes");
    }

  void testMovedGrowthAndPageResize() {
    InstanceStorage storage;
    MockPage gpu;
    gpu.commit(storage);
    auto id=storage.alloc(64);
    Matrix pose=Matrix::mkIdentity();
    pose.set(3,1,-17.f);
    id.set(&pose);
    gpu.commit(storage);
    auto blocker=storage.alloc(64);
    Matrix other=Matrix::mkIdentity();
    other.set(3,2,91.f);
    blocker.set(&other);
    gpu.commit(storage);

    const auto oldBegin=begin(id);
    require(storage.realloc(id,129),"growth beyond reserved capacity must move the region");
    require(begin(id)!=oldBegin && begin(id)==192,"moved-growth fixture must occupy the fresh range");
    std::vector<Matrix> expanded(4);
    expanded[0]=pose;
    // Identical used bytes must not discard initialization of all 256 reserved bytes.
    id.set(expanded.data());
    expectDirty(storage,{3,4,5,6});
    gpu.commit(storage);
    require(gpu.equal(begin(id),expanded.data(),256),"moved growth must copy old data and initialize new padding");
    require(gpu.equal(begin(blocker),&other,64),"moved growth must preserve another live region");

    require(!storage.realloc(id,256),"later in-capacity growth must not relocate");
    id.set(expanded.data());
    expectDirty(storage,{});
    gpu.commit(storage);
    require(gpu.equal(begin(id),expanded.data(),256),"exposed moved padding must already have exact GPU bytes");

    auto large=storage.alloc(8192);
    std::vector<Matrix> zeros(128);
    large.set(zeros.data());
    const auto oldCapacity=gpu.bytes.size();
    gpu.commit(storage);
    require(gpu.bytes.size()>oldCapacity,"fixture must force a new GPU page capacity");
    require(gpu.equal(begin(large),zeros.data(),8192),"GPU resize must initialize a byte-identical zero range");
    require(gpu.equal(begin(id),expanded.data(),256) && gpu.equal(begin(blocker),&other,64),
            "GPU resize must preserve every existing palette");
    expectDirty(storage,{});
    }

  void testMovedReusedRange() {
    InstanceStorage storage;
    MockPage gpu;
    auto id=storage.alloc(64);
    auto spare=storage.alloc(256);
    Matrix old=Matrix::mkIdentity();
    old.set(3,0,777.f);
    std::vector<Matrix> expected(4,Matrix::mkIdentity());
    for(size_t i=0;i<expected.size();++i) expected[i].set(3,0,float(10+i));
    id.set(&old);
    spare.set(expected.data());
    gpu.commit(storage);
    const auto destination=begin(spare);
    spare=InstanceStorage::Id();
    require(storage.realloc(id,129) && begin(id)==destination,"moved growth must reuse the initialized spare range");
    expected[0]=old;
    id.set(expected.data());
    expectDirty(storage,{destination/blockBytes});
    gpu.commit(storage);
    require(gpu.equal(destination,expected.data(),256),"copied prefix must upload even when the moved palette is otherwise identical");
    require(!storage.realloc(id,256),"reused reserved padding must support in-place growth");
    id.set(expected.data());
    expectDirty(storage,{});
    gpu.commit(storage);
    require(gpu.equal(destination,expected.data(),256),"reused padding must retain its initialized GPU bytes");
    }

  void testWordBoundaries() {
    for(const size_t prefix:{size_t(30),size_t(31)}) {
      InstanceStorage storage;
      MockPage gpu;
      std::vector<InstanceStorage::Id> earlier;
      for(size_t i=0;i<prefix;++i) earlier.push_back(storage.alloc(64));
      const size_t count=prefix==30 ? 37 : 32;
      auto id=storage.alloc(count*sizeof(Matrix));
      require(begin(id)/blockBytes==prefix+1,"boundary fixture must start at its declared block");
      gpu.commit(storage);
      const auto pending=Matrix::mkIdentity();
      earlier[0].set(&pending);
      std::vector<Matrix> pose(count,Matrix::mkIdentity());
      id.set(pose.data());
      std::vector<size_t> expected{1};
      for(size_t i=0;i<count;++i) expected.push_back(prefix+1+i);
      expectDirty(storage,expected);
      gpu.commit(storage);
      require(gpu.equal(begin(id),pose.data(),count*sizeof(Matrix)),"cross-word/full-word marking must upload the entire palette");
      }
    }

  void testConcurrentSharedWords() {
    InstanceStorage storage;
    MockPage gpu;
    constexpr size_t actors=96,workers=16;
    std::vector<InstanceStorage::Id> ids;
    std::vector<Matrix> pose(actors,Matrix::mkIdentity());
    for(size_t i=0;i<actors;++i) ids.push_back(storage.alloc(64));
    auto pending=storage.alloc(64);
    gpu.commit(storage);
    std::vector<size_t> expected;
    for(size_t i=1;i<=actors+1;++i) expected.push_back(i);

    for(size_t round=0;round<16;++round) {
      Matrix marker=Matrix::mkIdentity();
      marker.set(3,0,float(round+1));
      pending.set(&marker);
      for(size_t i=0;i<actors;++i) pose[i].set(3,0,float(1+round*actors+i));
      std::atomic<size_t> ready{0};
      std::atomic<bool> start{false};
      std::vector<std::thread> threads;
      for(size_t worker=0;worker<workers;++worker)
        threads.emplace_back([&,worker] {
          ready.fetch_add(1);
          while(!start.load(std::memory_order_acquire)) std::this_thread::yield();
          for(size_t i=worker;i<actors;i+=workers) ids[i].set(&pose[i]);
          });
      while(ready.load()!=workers) std::this_thread::yield();
      start.store(true,std::memory_order_release);
      for(auto& thread:threads) thread.join();
      expectDirty(storage,expected);
      gpu.commit(storage);
      for(size_t i=0;i<actors;++i)
        require(gpu.equal(begin(ids[i]),&pose[i],64),"shared-word atomic OR must retain every disjoint worker palette");
      require(gpu.equal(begin(pending),&marker,64),"concurrent marking must retain unrelated pending work");
      for(size_t i=0;i<actors;++i) ids[i].set(&pose[i]);
      expectDirty(storage,{});
      }
    }
  }

int main() {
  require(!Resources::hasGraphicsDevice(),"CPU storage contract must not construct a graphics device");
  testZeroPaddingReuse();
  testMovedGrowthAndPageResize();
  testMovedReusedRange();
  testWordBoundaries();
  testConcurrentSharedWords();
  std::puts("instance storage contracts passed");
  return EXIT_SUCCESS;
  }
