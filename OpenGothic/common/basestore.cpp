#include "basestore.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <system_error>
#ifndef _WIN32
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace {
class Lock {
  public:
    explicit Lock(const fs::path& path) {
#ifndef _WIN32
      fd=::open(path.c_str(),O_CREAT|O_RDWR|O_CLOEXEC,0600);
      if(fd<0) throw std::system_error(errno,std::generic_category(),"open base lock");
      while(::flock(fd,LOCK_EX)!=0) {
        const int error=errno;
        if(error==EINTR) continue;
        ::close(fd);
        throw std::system_error(error,std::generic_category(),"lock base");
        }
#else
      (void)path;
      throw std::runtime_error("automatic base import requires Unix file locks");
#endif
      }
    ~Lock() {
#ifndef _WIN32
      ::close(fd);
#endif
      }
    Lock(const Lock&)=delete;
    Lock& operator=(const Lock&)=delete;
  private:
    int fd=-1;
  };

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
    if(entry.path()==current || !entry.is_directory() || name.size()!=64 ||
       name.find_first_not_of("0123456789abcdef")!=std::string::npos) continue;
    older.push_back(entry);
    }
  std::sort(older.begin(),older.end(),[](const auto& a,const auto& b) { return a.last_write_time()>b.last_write_time(); });
  for(size_t i=2;i<older.size();++i) fs::remove_all(older[i].path());
  }
}

fs::path BaseStore::resolve(const fs::path& gothic, const BaseImport::Files& mounted, const BaseImport::Progress& log) {
  if(const auto* explicitBase=std::getenv("OPENGOTHIC_BASE")) return explicitBase;
  const auto start=std::chrono::steady_clock::now();
  const auto identity=BaseImport::identify(gothic,mounted);
  fs::path cache;
  if(const auto* env=std::getenv("OPENGOTHIC_BASE_CACHE")) cache=env;
  else if(const auto* home=std::getenv("HOME")) cache=fs::path(home)/".cache/gothic-ai/base";
  if(cache.empty()) throw std::runtime_error("base cache path is empty");
  fs::create_directories(cache);
  Lock cacheLock(cache/".lock");
  const auto destination=cache/identity.fingerprint;
  const auto finish=[&](const char* source) {
    try { prune(cache,destination); }
    catch(const std::exception& e) { log(std::string("base cache cleanup: ")+e.what()); }
    const auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
    log(std::string("base: ")+source+" "+destination.string()+" ("+std::to_string(ms)+" ms)");
    return destination;
    };
  if(BaseImport::matches(destination,identity)) return finish("cache");
  if(fs::exists(destination)) log("base: rejected cache directory: base.json mismatch");

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
  std::unique_ptr<Lock> storeLock;
  fs::path stored;
  if(!store.empty()) {
    stored=store/"base"/identity.fingerprint;
    try {
      if(BaseImport::matches(stored,identity)) {
        copy(stored,destination,identity);
        return finish("store");
        }
      fs::create_directories(store/"base");
      log("base: waiting for store lock");
      storeLock=std::make_unique<Lock>(store/"base"/(identity.fingerprint+".lock"));
      // Another machine may have published while this process waited.
      if(BaseImport::matches(stored,identity)) {
        copy(stored,destination,identity);
        return finish("store");
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
  publish(temporary.path,destination,identity);
  if(storeLock) {
    try {
      copy(destination,stored,identity);
      log("base: published to store "+stored.string());
      }
    catch(const std::exception& e) { log(std::string("base: store publication failed: ")+e.what()); }
    }
  return finish("import");
  }
