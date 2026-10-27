#include "basestore.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <system_error>
#include <thread>
#ifndef _WIN32
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#else
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace fs = std::filesystem;

class BaseStore::Lock {
  public:
    enum Mode { Exclusive, Shared, TryExclusive };
    explicit Lock(const fs::path& path, Mode mode=Exclusive) {
#ifndef _WIN32
      fd=::open(path.c_str(),O_CREAT|O_RDWR|O_CLOEXEC,0600);
      if(fd<0) throw std::system_error(errno,std::generic_category(),"open base lock");
      const int operation=mode==Shared ? LOCK_SH : (mode==TryExclusive ? LOCK_EX|LOCK_NB : LOCK_EX);
      while(::flock(fd,operation)!=0) {
        const int error=errno;
        if(error==EINTR) continue;
        ::close(fd);
        fd=-1;
        if(mode==TryExclusive && (error==EWOULDBLOCK || error==EAGAIN || error==EACCES)) return;
        throw std::system_error(error,std::generic_category(),"lock base");
        }
#else
      // LockFileEx is flock's counterpart: held per handle, shared or exclusive, released with the handle
      // or the process. The lock files are never read or written, so its mandatory ranges block nothing.
      file=::CreateFileW(path.c_str(),GENERIC_READ|GENERIC_WRITE,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,
                         nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
      if(file==INVALID_HANDLE_VALUE)
        throw std::system_error(int(::GetLastError()),std::system_category(),"open base lock");
      DWORD flags=mode==Shared ? 0 : LOCKFILE_EXCLUSIVE_LOCK;
      if(mode==TryExclusive)
        flags|=LOCKFILE_FAIL_IMMEDIATELY;
      OVERLAPPED whole={};
      if(!::LockFileEx(file,flags,0,MAXDWORD,MAXDWORD,&whole)) {
        const DWORD error=::GetLastError();
        ::CloseHandle(file);
        file=INVALID_HANDLE_VALUE;
        if(mode==TryExclusive && error==ERROR_LOCK_VIOLATION) return;
        throw std::system_error(int(error),std::system_category(),"lock base");
        }
#endif
      }
    ~Lock() {
#ifndef _WIN32
      ::close(fd);
#else
      if(file!=INVALID_HANDLE_VALUE) {
        OVERLAPPED whole={};
        ::UnlockFileEx(file,0,MAXDWORD,MAXDWORD,&whole);
        ::CloseHandle(file);
        }
#endif
      }
    Lock(const Lock&)=delete;
    Lock& operator=(const Lock&)=delete;
#ifndef _WIN32
    bool acquired() const { return fd>=0; }
  private:
    int fd=-1;
#else
    bool acquired() const { return file!=INVALID_HANDLE_VALUE; }
  private:
    HANDLE file=INVALID_HANDLE_VALUE;
#endif
  };

namespace {
using BaseStore::Lock;

bool fingerprintName(std::string_view name) {
  return name.size()==64 && name.find_first_not_of("0123456789abcdef")==std::string_view::npos;
  }

// Called only while holding the cache lock or this store fingerprint's lock.
void removeInterrupted(const fs::path& root, const std::string& fingerprint={}) {
  for(const auto& entry:fs::directory_iterator(root)) {
    const auto name=entry.path().filename().string();
    if(name.size()>69 && fingerprintName(std::string_view(name).substr(0,64)) &&
       name.compare(64,5,".tmp-")==0 && name.find_first_not_of("0123456789",69)==std::string::npos &&
       (fingerprint.empty() || name.compare(0,64,fingerprint)==0) && entry.is_directory())
      fs::remove_all(entry.path());
    }
  }

struct Temporary {
  fs::path path;
  explicit Temporary(const fs::path& destination) {
    path=destination.string()+".tmp-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
    if(!fs::create_directory(path)) throw std::runtime_error("base temporary directory already exists");
    }
  ~Temporary() {
    std::error_code error;
    fs::remove_all(path,error);
    }
  };

void publish(const fs::path& temporary, const fs::path& destination, const BaseImport::Identity& identity) {
  if(!BaseImport::matches(temporary,identity)) throw std::runtime_error("base.json does not match installation");
  // The caller holds the destination lock. Valid published directories are immutable.
  if(fs::exists(destination)) fs::remove_all(destination);
  fs::rename(temporary,destination);
  }

void copy(const fs::path& source, const fs::path& destination, const BaseImport::Identity& identity) {
  Temporary temporary(destination);
  fs::copy(source,temporary.path,fs::copy_options::recursive);
  publish(temporary.path,destination,identity);
  }

void prune(const fs::path& cache, const fs::path& current) {
  fs::last_write_time(current,fs::file_time_type::clock::now());
  std::vector<fs::directory_entry> older;
  for(const auto& entry:fs::directory_iterator(cache)) {
    const auto name=entry.path().filename().string();
    if(entry.path()==current || !entry.is_directory() || !fingerprintName(name)) continue;
    older.push_back(entry);
    }
  std::sort(older.begin(),older.end(),[](const auto& a,const auto& b) { return a.last_write_time()>b.last_write_time(); });
  for(size_t i=2;i<older.size();++i) {
    Lock unused(cache/(older[i].path().filename().string()+".use"),Lock::TryExclusive);
    if(unused.acquired()) fs::remove_all(older[i].path());
    }
  }
}

BaseStore::Selection BaseStore::resolve(const fs::path& gothic, const BaseImport::Files& mounted, const BaseImport::Progress& log) {
  if(const auto* explicitBase=std::getenv("OPENGOTHIC_BASE"); explicitBase && *explicitBase) {
    if(!BaseImport::matches(explicitBase,BaseImport::identify(gothic,mounted)))
      throw std::runtime_error(std::string("OPENGOTHIC_BASE does not match this installation or importer: ")+explicitBase);
    return {explicitBase,{},{}};
    }
  const auto start=std::chrono::steady_clock::now();
  const auto identity=BaseImport::identify(gothic,mounted);
  fs::path cache;
  if(const auto* env=std::getenv("OPENGOTHIC_BASE_CACHE")) cache=env;
#ifdef _WIN32
  // HOME is usually unset on Windows; the wide variable keeps a Cyrillic user name intact.
  else if(const auto* local=_wgetenv(L"LOCALAPPDATA"); local && *local) cache=fs::path(local)/"gothic-ai"/"base";
#else
  else if(const auto* home=std::getenv("HOME")) cache=fs::path(home)/".cache/gothic-ai/base";
#endif
  if(cache.empty()) throw std::runtime_error("base cache path is empty");
  fs::create_directories(cache);
  Lock cacheLock(cache/".lock");
  removeInterrupted(cache);
  const auto destination=cache/identity.fingerprint;
  fs::path store;
  if(const auto* env=std::getenv("OPENGOTHIC_BASE_STORE")) store=env;
  else {
#ifdef __APPLE__
    const fs::path shared="/Volumes/shared/gothic-ai";
#else
    const fs::path shared="/mnt/shared/gothic-ai";
#endif
    std::error_code error;
    if(fs::is_directory(shared,error)) store=shared;
    }
  const auto finish=[&](const char* source, bool upload=true) {
    Selection selected{destination,std::make_shared<Lock>(cache/(identity.fingerprint+".use"),Lock::Shared),{}};
    try { prune(cache,destination); }
    catch(const std::exception& e) { log(std::string("base cache cleanup: ")+e.what()); }
    const auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
    log(std::string("base: ")+source+" "+destination.string()+" ("+std::to_string(ms)+" ms)");
    if(upload && !store.empty()) {
      // The worker owns a lease too: pruning cannot remove its source while copying.
      std::packaged_task<void()> publication([store,destination,identity,lease=selected.lease] {
        const auto started=std::chrono::steady_clock::now();
        try {
          const auto root=store/"base";
          fs::create_directories(root);
          Lock lock(root/(identity.fingerprint+".lock"));
          removeInterrupted(root,identity.fingerprint);
          const auto stored=root/identity.fingerprint;
          if(BaseImport::matches(stored,identity)) return;
          copy(destination,stored,identity);
          const auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-started).count();
          // The worker may outlive Resources and Tempest's logging callback.
          std::fprintf(stderr,"base: published to store %s (%lld ms, background)\n",
                       stored.string().c_str(),static_cast<long long>(ms));
          }
        catch(const std::exception& e) { std::fprintf(stderr,"base: store publication failed: %s\n",e.what()); }
        });
      selected.publication=publication.get_future();
      // A packaged-task future does not join on destruction. Process exit can
      // interrupt the copy; the next publisher removes its temporary directory.
      std::thread(std::move(publication)).detach();
      }
    return selected;
    };
  if(BaseImport::matches(destination,identity)) return finish("cache");
  if(fs::exists(destination)) log("base: rejected cache directory: base.json mismatch");

  std::unique_ptr<Lock> storeLock;
  fs::path stored;
  if(!store.empty()) {
    stored=store/"base"/identity.fingerprint;
    try {
      fs::create_directories(store/"base");
      log("base: waiting for store lock");
      storeLock=std::make_unique<Lock>(store/"base"/(identity.fingerprint+".lock"));
      removeInterrupted(store/"base",identity.fingerprint);
      }
    catch(const std::exception& e) {
      log(std::string("base: store unavailable: ")+e.what());
      storeLock.reset();
      }
    // Also allow copying from a read-only store when opening its lock failed.
    try {
      if(BaseImport::matches(stored,identity)) {
        copy(stored,destination,identity);
        return finish("store",false);
        }
      }
    catch(const std::exception& e) {
      log(std::string("base: store unavailable: ")+e.what());
      storeLock.reset();
      }
    }
  // The current importer supports the installed Gothic II Data/*.vdf set. Never
  // label a base built from a different set (e.g. selected mods) with this identity.
  if(BaseImport::identify(gothic).json!=identity.json)
    throw std::runtime_error("mounted archives differ from the supported installation import");
  Temporary temporary(destination);
  log("base: importing installation");
  BaseImport::build(gothic,temporary.path,log);
  storeLock.reset();
  publish(temporary.path,destination,identity);
  return finish("import");
  }
