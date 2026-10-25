#include "baseimport.h"
#include "basestore.h"
#include "commandline.h"
#include "resources.h"

#include <chrono>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <csignal>
#include <stdexcept>
#include <sys/wait.h>
#include <unistd.h>

namespace fs = std::filesystem;

namespace {
void require(bool ok, const std::string& message) {
  if(!ok) throw std::runtime_error(message);
  }

std::string read(const fs::path& file) {
  std::ifstream in(file,std::ios::binary);
  require(bool(in),"cannot read "+file.string());
  return {std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()};
  }

std::map<std::string,std::string> hashes(const fs::path& root) {
  std::map<std::string,std::string> result;
  for(const auto& entry:fs::recursive_directory_iterator(root))
    if(entry.is_regular_file())
      result.emplace(entry.path().lexically_relative(root).generic_string(),BaseImport::sha256(read(entry.path())));
  return result;
  }

void equal(const fs::path& a, const fs::path& b) {
  const auto expected=hashes(a), actual=hashes(b);
  size_t mismatches=0;
  for(const auto& [path,hash]:expected) {
    const auto found=actual.find(path);
    if(found==actual.end() || found->second!=hash) {
      std::cerr << "SHA-256 mismatch: " << path << '\n';
      ++mismatches;
      }
    }
  for(const auto& [path,hash]:actual)
    if(!expected.count(path)) { std::cerr << "extra file: " << path << '\n'; ++mismatches; }
  require(mismatches==0,"base file SHA-256 mismatches: "+std::to_string(mismatches));
  std::cout << "equal SHA-256: " << expected.size() << " files, 0 mismatches\n";
  }

struct Temporary {
  fs::path path=fs::temp_directory_path()/("gothic-base-store-"+std::to_string(getpid()));
  Temporary() { require(fs::create_directory(path),"temporary directory exists"); }
  ~Temporary() { std::error_code error; fs::remove_all(path,error); }
  };
}

int main() {
  try {
    const char* game=std::getenv("GOTHIC_DIR");
    const char* referenceEnv=std::getenv("OPENGOTHIC_BASE");
    require(game && *game && referenceEnv && *referenceEnv,"GOTHIC_DIR and OPENGOTHIC_BASE (build-base.sh output) required");
    const fs::path reference=referenceEnv;
    const auto identity=BaseImport::identify(game);
    require(BaseImport::matches(reference,identity),"build-base.sh reference has a different base.json");
    require(BaseImport::sha256("abc")=="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "SHA-256 known vector");
    Temporary temporary;
    const auto cache=temporary.path/"cache", store=temporary.path/"store";
    const auto base=cache/identity.fingerprint;
    setenv("OPENGOTHIC_BASE_CACHE",cache.c_str(),1);
    setenv("OPENGOTHIC_BASE_STORE",store.c_str(),1);
    setenv("OPENGOTHIC_CONTENT","",1);
    setenv("OPENGOTHIC_TEXTURES","",1);
    unsetenv("OPENGOTHIC_BASE");
    // Obtain the engine's archive identity without opening any game resource.
    BaseImport::Files mounted;
    for(const auto& entry:fs::directory_iterator(fs::path(game)/"Data")) {
      auto name=entry.path().filename().string();
      for(auto& c:name) c=char(std::toupper(static_cast<unsigned char>(c)));
      if(entry.is_regular_file() && name.ends_with(".VDF")) mounted.emplace_back(name,entry.file_size());
      }
    require(BaseImport::identify(game,mounted).json==identity.json,"archive identity differs");
    const char* args[]={"gothic-base-store-contract","-g",game};
    CommandLine commandLine(3,args);
    // SIGTERM skips RAII cleanup. The next lock owner must remove these partial bases.
    const auto interrupted=fork(); require(interrupted>=0,"fork failed");
    if(interrupted==0) {
      try {
        BaseStore::resolve(game,mounted,[&](std::string_view message) {
          if(message!="base: importing installation") return;
          for(const auto& entry:fs::directory_iterator(cache))
            if(entry.path().filename().string().starts_with(identity.fingerprint+".tmp-"))
              std::ofstream(entry.path()/"partial") << "interrupted import";
          const auto partialStore=store/"base"/(identity.fingerprint+".tmp-1");
          fs::create_directory(partialStore);
          std::ofstream(partialStore/"partial") << "interrupted publication";
          std::raise(SIGTERM);
          });
        }
      catch(...) { _exit(1); }
      _exit(2);
      }
    int interruptedStatus=0;
    require(waitpid(interrupted,&interruptedStatus,0)==interrupted && WIFSIGNALED(interruptedStatus) &&
            WTERMSIG(interruptedStatus)==SIGTERM,"import child must terminate with SIGTERM");
    const auto otherPartial=store/"base"/(std::string(64,'0')+".tmp-1");
    fs::create_directory(otherPartial);
    // Exercise the actual engine startup hook for the empty-cache import.
    {
    Resources resources;
    Resources::loadVdfs({},true);
    require(Resources::worldBaseDir("NEWWORLD.ZEN")==base/"worlds/NEWWORLD","engine did not select automatic base");
    }
    equal(reference,base);
    equal(reference,store/"base"/identity.fingerprint);
    for(const auto& root:{cache,store/"base"})
      for(const auto& entry:fs::directory_iterator(root))
        require(!entry.path().filename().string().starts_with(identity.fingerprint+".tmp-"),
                "interrupted temporary directory survived: "+entry.path().string());
    require(fs::exists(otherPartial),"store cleanup must not touch another fingerprint");
    std::cout << "SIGTERM recovery: cache and store partial bases removed; other fingerprint untouched\n";

    std::string logs;
    const auto log=[&](std::string_view message) { logs+=std::string(message)+"\n"; };
    const auto select=[&](const char* expected) {
      logs.clear();
      require(BaseStore::resolve(game,mounted,log).path==base,"wrong base path");
      require(logs.find(std::string("base: ")+expected+" ")!=std::string::npos,"wrong source: "+logs);
      require(logs.find("base: importing installation")==std::string::npos,"unexpected import");
      std::cout << logs;
      };
    select("cache");
    fs::remove_all(cache);
    select("store");
    equal(reference,base);
    { std::ofstream broken(base/"base.json"); broken << "{broken"; }
    select("store");
    require(logs.find("rejected cache directory")!=std::string::npos,"corrupt cache was accepted");
    equal(reference,base);

    // Both children start together with empty roots; the lock spans the import.
    fs::remove_all(cache); fs::remove_all(store);
    int gate[2]; require(pipe(gate)==0,"pipe failed");
    std::cout.flush(); std::cerr.flush();
    pid_t children[2];
    for(int i=0;i<2;++i) {
      children[i]=fork(); require(children[i]>=0,"fork failed");
      if(children[i]==0) {
        close(gate[1]);
        char token;
        if(::read(gate[0],&token,1)!=1) _exit(2);
        try {
          std::ofstream events(temporary.path/("child-"+std::to_string(i)+".log"));
          BaseStore::resolve(game,mounted,[&](std::string_view message) { events << message << '\n'; });
          events.close();
          _exit(0);
          }
        catch(...) { _exit(1); }
        }
      }
    close(gate[0]); require(::write(gate[1],"xx",2)==2,"gate write failed"); close(gate[1]);
    for(auto child:children) {
      int status=0; require(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0,"child failed");
      }
    int imports=0, hits=0;
    for(int i=0;i<2;++i) {
      const auto events=read(temporary.path/("child-"+std::to_string(i)+".log"));
      if(events.find("base: importing installation")!=std::string::npos) ++imports;
      if(events.find("base: cache ")!=std::string::npos) ++hits;
      }
    require(imports==1 && hits==1,"two processes must produce exactly one import and one cache hit");
    equal(reference,base);
    std::cout << "concurrent processes: 2, imports: " << imports << ", cache hits: " << hits << '\n';

    const auto changedGame=temporary.path/"game";
    fs::create_directories(changedGame/"Data");
    fs::path smallest;
    for(const auto& entry:fs::directory_iterator(fs::path(game)/"Data")) {
      fs::create_symlink(entry.path(),changedGame/"Data"/entry.path().filename());
      auto extension=entry.path().extension().string();
      for(auto& c:extension) c=char(std::toupper(static_cast<unsigned char>(c)));
      if(extension==".VDF" && (smallest.empty() || entry.file_size()<fs::file_size(smallest))) smallest=entry.path();
      }
    for(const auto& entry:fs::directory_iterator(game)) {
      auto name=entry.path().filename().string();
      for(auto& c:name) c=char(std::toupper(static_cast<unsigned char>(c)));
      if(name=="_WORK") fs::create_directory_symlink(entry.path(),changedGame/entry.path().filename());
      }
    const auto changedArchive=changedGame/"Data"/smallest.filename();
    fs::remove(changedArchive);
    fs::copy_file(smallest,changedArchive);
    { std::ofstream append(changedArchive,std::ios::binary|std::ios::app); append.put('\0'); }
    require(BaseImport::identify(changedGame).fingerprint!=identity.fingerprint,"archive size +1 did not change fingerprint");
    std::cout << "archive size +1: different fingerprint\n";
    // Keep a real Resources owner alive while another process selects B, C and D.
    auto otherMounted=mounted;
    fs::path latest;
    {
    Resources resources;
    Resources::loadVdfs({},true);
    fs::last_write_time(base,fs::file_time_type::clock::now()-std::chrono::hours(1));
    const auto worldFile=Resources::worldBaseDir("NEWWORLD.ZEN")/"vobs.json";
    const auto expected=BaseImport::sha256(read(worldFile));
    for(int i=0;i<3;++i) {
      ++otherMounted.front().second;
      const auto other=BaseImport::identify(game,otherMounted);
      latest=cache/other.fingerprint;
      fs::create_directory(latest);
      { std::ofstream metadata(latest/"base.json"); metadata << other.json; }
      const auto child=fork(); require(child>=0,"fork failed");
      if(child==0) {
        try { BaseStore::resolve(game,otherMounted,log); _exit(0); }
        catch(...) { _exit(1); }
        }
      int status=0;
      require(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0,"cache selection child failed");
      require(BaseImport::sha256(read(worldFile))==expected,"active game lost its base world data");
      }
    require(fs::is_directory(base),"active base must survive three newer selections");
    }
    // Once the owner releases the lease, normal retention can remove A.
    require(BaseStore::resolve(game,otherMounted,log).path==latest,"wrong last fingerprint");
    require(!fs::exists(base),"released base should be pruned");
    select("store");
    std::cout << "active base survives B/C/D; released base is pruned\n";
    // Retention only touches fingerprint directories, keeps the current base and two recent ones.
    for(int i=0;i<4;++i) {
      const auto old=cache/std::string(64,char('0'+i));
      fs::create_directory(old);
      fs::last_write_time(old,fs::file_time_type::clock::now()-std::chrono::hours(i+1));
      }
    select("cache");
    size_t directories=0;
    for(const auto& entry:fs::directory_iterator(cache)) if(entry.is_directory()) ++directories;
    require(directories==3,"cache must retain three fingerprints");
    setenv("OPENGOTHIC_BASE","",1);
    require(BaseStore::resolve(game,mounted,log).path.empty(),"explicit empty base must disable automatic import");
    setenv("OPENGOTHIC_BASE",reference.c_str(),1);
    require(BaseStore::resolve(game,mounted,log).path==reference,"explicit base must win");
    std::cout << "base-store contract passed: import, cache, store, corrupt metadata, 2 processes, fingerprint, active lease, retention, SIGTERM recovery, explicit base\n";
    return 0;
    }
  catch(const std::exception& e) { std::cerr << "base-store contract: " << e.what() << '\n'; return 1; }
  }
