// P10 source manifest: which original inputs of the not yet migrated families the
// engine and the production tools select, from where, and their content identity.
// Import evidence only: nothing here converts a payload or changes a loader.
#include "baseimport.h"
#include "basemusic.h"
#include "gothicglb.h"
#include "sha256.h"
#include "dmusic/segment.h"
#include "dmusic/sourcefile.h"
#include "dmusic/style.h"
#include "utils/fileutil.h"

#include <Tempest/Dir>
#include <Tempest/File>

#include <zenkit/Archive.hh>
#include <zenkit/CutsceneLibrary.hh>
#include <zenkit/DaedalusScript.hh>
#include <zenkit/Stream.hh>
#include <zenkit/Vfs.hh>

#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <tuple>

namespace fs = std::filesystem;

namespace BaseImport {
namespace {

void require(bool ok, const std::string& what) {
  if(!ok)
    throw std::runtime_error(what);
  }

std::string upper(std::string s) {
  for(auto& c:s)
    c = char(std::toupper(static_cast<unsigned char>(c)));
  return s;
  }

std::string extension(const std::string& name) {
  const auto dot = name.find_last_of('.');
  return dot==std::string::npos ? std::string() : upper(name.substr(dot));
  }

std::string str(const std::string& s) {
  return gothicglb::jsonString(s);
  }

std::string hex(const uint8_t (&bytes)[16]) {
  static const char digits[] = "0123456789abcdef";
  std::string s;
  for(const auto b:bytes) {
    s += digits[b>>4];
    s += digits[b&15];
    }
  return s;
  }

using Tempest::Dir;

// CommandLine's form of the installation path: forward slashes and a trailing '/'.
std::u16string installation(const fs::path& gothic) {
  auto path = gothic.u16string();
  for(auto& c:path)
    if(c==u'\\')
      c = u'/';
  if(!path.empty() && path.back()!=u'/')
    path.push_back(u'/');
  return path;
  }

// Gothic::loadScript and GameScript::loadDialogOU widen each request byte to char16_t.
std::u16string widen(const std::string& s) {
  std::u16string out;
  for(const char c:s)
    out.push_back(char16_t(c));
  return out;
  }

// UTF-16 code units as JSON: exact, whatever the spelling.
std::string str16(const std::u16string& s) {
  static const char digits[] = "0123456789abcdef";
  std::string out = "\"";
  for(const char16_t c:s) {
    if(c>=0x20 && c<0x7F && c!=u'"' && c!=u'\\') {
      out.push_back(char(c));
      continue;
      }
    out += "\\u";
    for(int shift=12; shift>=0; shift-=4)
      out.push_back(digits[(c>>shift)&15]);
    }
  return out+"\"";
  }

const std::array<const char16_t*,8> scriptLanguages = {u"Scripts_EN",u"Scripts_DE",u"Scripts_PL",u"Scripts_RU",
                                                       u"Scripts_FR",u"Scripts_ES",u"Scripts_IT",u"Scripts_CZ"};
const std::array<const char*,8> languageNames = {"EN","DE","PL","RU","FR","ES","IT","CZ"};

struct Scanned {
  std::u16string name;
  int64_t        time = 0;
  uint16_t       ord = 0;
  bool           isMod = false;
  };

// Resources::vdfTimestamp, unchanged.
int64_t vdfTimestamp(const std::u16string& name) {
  enum {
    VDF_COMMENT_LENGTH   = 256,
    VDF_SIGNATURE_LENGTH = 16,
    };
  uint32_t count=0, timestamp=0;
  char sign[VDF_SIGNATURE_LENGTH]={};

  try {
    Tempest::RFile fin(name);
    fin.seek(VDF_COMMENT_LENGTH);
    fin.read(sign,VDF_SIGNATURE_LENGTH);
    fin.read(&count,sizeof(count));
    fin.seek(4);
    fin.read(&timestamp,sizeof(timestamp));

    return int64_t(timestamp);
    }
  catch(...) {
    return -1;
    }
  }

// Resources::detectVdf, unchanged apart from its result type.
void detectVdf(std::vector<Scanned>& ret, const std::u16string& root) {
  Dir::scan(root,[&root,&ret](const std::u16string& vdf, Dir::FileType t){
    if(t==Dir::FT_File) {
      Scanned ar;
      ar.name  = root+vdf;
      ar.time  = vdfTimestamp(ar.name);
      ar.ord   = uint16_t(ret.size());
      ar.isMod = vdf.rfind(u".mod")==vdf.size()-4;

      if(std::filesystem::file_size(ar.name)>0)
        ret.emplace_back(std::move(ar));
      return;
      }

    // switch uses NX extension for language dependant stuff
    if(vdf.find(u".NX.")!=std::string::npos)
      return;

    if(t==Dir::FT_Dir && vdf!=u".." && vdf!=u".") {
      auto dir = root + vdf + u"/";
      detectVdf(ret,dir);
      }
    });
  }

// Full virtual paths without a leading slash, as the game census writes them.
void collectPaths(const zenkit::VfsNode& node, const std::string& prefix,
                  std::map<const zenkit::VfsNode*,std::string>& out) {
  for(const auto& child:node.children()) {
    const auto path = prefix.empty() ? child.name() : prefix+"/"+child.name();
    if(child.type()==zenkit::VfsNodeType::DIRECTORY)
      collectPaths(child,path,out);
    else
      out[&child] = path;
    }
  }

struct Digest {
  uint64_t    size = 0;
  std::string sha256;
  std::vector<uint8_t> head; // first bytes, for header facts
  };

Digest digest(zenkit::Read& in, std::vector<uint8_t>* bytes = nullptr) {
  Digest d;
  Sha256 sha;
  uint8_t buf[1<<16];
  for(;;) {
    const auto n = in.read(buf,sizeof(buf));
    if(n==0)
      break;
    if(d.head.empty())
      d.head.assign(buf,buf+n);
    if(bytes!=nullptr)
      bytes->insert(bytes->end(),buf,buf+n);
    sha.update(buf,n);
    d.size += n;
    }
  d.sha256 = sha.finish();
  return d;
  }

uint32_t le32(const std::vector<uint8_t>& b, size_t o) {
  return uint32_t(b[o]) | uint32_t(b[o+1])<<8 | uint32_t(b[o+2])<<16 | uint32_t(b[o+3])<<24;
  }

uint16_t le16(const std::vector<uint8_t>& b, size_t o) {
  return uint16_t(b[o] | b[o+1]<<8);
  }

// Header facts only: the extension does not determine the codec.
std::string audioHeader(const std::vector<uint8_t>& b) {
  if(b.size()>=4 && std::memcmp(b.data(),"OggS",4)==0)
    return "{\"container\":\"ogg\"}";
  if(b.size()<12 || std::memcmp(b.data(),"RIFF",4)!=0 || std::memcmp(b.data()+8,"WAVE",4)!=0)
    return "{\"container\":\"unknown\"}";
  for(size_t o=12; o+8<=b.size();) {
    const auto size = le32(b,o+4);
    if(std::memcmp(b.data()+o,"fmt ",4)==0) {
      if(o+8+16>b.size())
        break;
      return "{\"container\":\"wav\",\"format_tag\":"+std::to_string(le16(b,o+8))+
             ",\"channels\":"+std::to_string(le16(b,o+10))+
             ",\"sample_rate\":"+std::to_string(le32(b,o+12))+
             ",\"bits_per_sample\":"+std::to_string(le16(b,o+22))+"}";
      }
    o += 8+size+(size&1);
    }
  return "{\"container\":\"wav\",\"format_tag\":null}";
  }

class Manifest {
  public:
    explicit Manifest(const fs::path& root)
      : root16(installation(fs::absolute(root))), gothic(fs::path(root16).parent_path()) {
      vfs.set_open_observer([this](std::string_view, std::string_view source) { opened = source; });
      // Resources::loadVdfs without a mod ini: every .mod is filtered out.
      for(auto& a:runtimeArchives(gothic)) {
        if(a.mod)
          continue;
        try {
          vfs.mount_disk(a.path,zenkit::VfsOverwriteBehavior::OLDER);
          }
        catch(const std::exception& e) {
          std::cerr << "sources: unable to load archive " << a.path.filename().string() << ": " << e.what() << "\n";
          continue;
          }
        archives.push_back(std::move(a));
        }
      checkSupportedSet();
      // Gothic::nestedPath({u"_work"}) ends with '/'; drop it so relative paths stay exact.
      work = fs::path(nested({u"_work"},Dir::FT_Dir)).parent_path();
      if(fs::is_directory(work)) {
        // Resources::mountWork: after the archives, never replacing them.
        vfs.mkdir("/_work");
        vfs.mount_host(work,"/_work",zenkit::VfsOverwriteBehavior::NONE);
        }
      collectPaths(vfs.root(),"",paths);
      }

    std::string json();

  private:
    struct Choice {
      std::string step, path, source;
      fs::path    host; // absolute, when a loose file was selected
      Digest      digest;
      };

    struct Attempt {
      std::string request;
      std::optional<Choice> found;
      };

    struct Trace {
      Manifest*                       self = nullptr;
      std::vector<Attempt>            attempts;
      std::vector<BaseMusicReference> references;
      std::vector<std::string>        strings; // owned copies of file and name per reference
      std::vector<bool>               hasFile;
      };

    void checkSupportedSet() const {
      Files runtime, supported;
      for(const auto& a:archives)
        runtime.emplace_back(a.name,a.size);
      for(const auto& e:fs::directory_iterator(gothic/"Data"))
        if(e.is_regular_file() && upper(e.path().extension().string())==".VDF")
          supported.emplace_back(upper(e.path().filename().string()),e.file_size());
      std::sort(runtime.begin(),runtime.end());
      std::sort(supported.begin(),supported.end());
      require(runtime==supported,"runtime archive set differs from the supported Gothic II import");
      }

    // Gothic::nestedPath: the game's case-insensitive path below the installation.
    std::u16string nested(const std::initializer_list<const char16_t*>& segments, Dir::FileType type) const {
      return FileUtil::nestedPath(root16,segments,type);
      }

    // Every path here starts with the installation exactly as spelled (it may contain
    // ".." or symlinks the I/O keeps): strip that prefix first, then normalize only the rest.
    std::string relative(const fs::path& p) const {
      return p.lexically_relative(gothic).lexically_normal().generic_string();
      }

    std::string sourceName(std::string_view source) const {
      const fs::path p(source);
      return p.is_absolute() ? relative(p) : upper(std::string(source));
      }

    // Opens through the VFS so the open observer reports the winning archive or host file.
    Choice openNode(const zenkit::VfsNode* node, const std::string& step, std::vector<uint8_t>* bytes = nullptr) {
      Choice c;
      c.step = step;
      c.path = paths.at(node);
      opened.clear();
      auto in = node->open_read();
      c.source = sourceName(opened);
      if(fs::path(opened).is_absolute())
        c.host = fs::path(opened);
      c.digest = digest(*in,bytes);
      return c;
      }

    Choice openHost(const fs::path& path, const std::string& step) {
      Choice c;
      c.step = step;
      c.path = relative(path);
      c.source = c.path;
      c.host = path;
      const auto key = path.lexically_normal().string();
      if(auto it=hostDigests.find(key); it!=hostDigests.end()) {
        c.digest = it->second;
        return c;
        }
      auto in = zenkit::Read::from(path);
      require(in!=nullptr,"cannot open "+path.string());
      c.digest = digest(*in);
      hostDigests[key] = c.digest;
      return c;
      }

    static std::string choiceJson(const Choice& c) {
      return "{\"step\":"+str(c.step)+",\"path\":"+str(c.path)+",\"source\":"+str(c.source)+
             ",\"size\":"+std::to_string(c.digest.size)+",\"sha256\":\""+c.digest.sha256+"\"}";
      }

    static void* resolveMusic(void* ctx, char const* file, size_t* len);
    static void  visitMusic(void* ctx, BaseMusicReference const* reference);

    std::string archivesJson() const;
    std::string filesJson();
    std::string musicJson();
    // `closed`: the root's traversal reached every reference it declares.
    std::string gothicKitJson(const std::string& root, bool& closed);
    std::string dx8Json(const std::vector<std::u16string>& dirs, const std::u16string& root, bool& closed);
    std::string scriptsJson();
    std::string outputUnitsJson();
    std::string serverJson();

    std::u16string                               root16;
    fs::path                                     gothic, work;
    zenkit::Vfs                                  vfs;
    std::string                                  opened;
    std::vector<RuntimeArchive>                  archives;
    std::map<const zenkit::VfsNode*,std::string> paths;
    std::map<std::string,Digest>                 hostDigests;
    // Host files of the default profile's GOTHIC.DAT and OU choices, for comparison with the
    // server readers; empty when the winner came from an archive.
    fs::path                                     engineGameDat, engineOu;
  };

std::string Manifest::archivesJson() const {
  std::string s = "[";
  for(size_t i=0; i<archives.size(); ++i)
    s += std::string(i ? "," : "")+"\n    {\"name\":"+str(archives[i].name)+",\"size\":"+std::to_string(archives[i].size)+
         ",\"timestamp\":"+std::to_string(archives[i].timestamp)+"}";
  s += "\n  ],\n  \"order_ties\":[";
  bool first = true;
  for(size_t i=0; i<archives.size();) {
    size_t j = i+1;
    while(j<archives.size() && archives[j].timestamp==archives[i].timestamp)
      ++j;
    if(j-i>1) {
      s += std::string(first ? "" : ",")+"\n    [";
      for(size_t k=i; k<j; ++k)
        s += std::string(k==i ? "" : ",")+str(archives[k].name);
      s += "]";
      first = false;
      }
    i = j;
    }
  return s+"\n  ]";
  }

// FNT, audio and BIK: the engine asks Vfs::find for a basename.
std::string Manifest::filesJson() {
  static const std::set<std::string> kinds = {".FNT",".WAV",".OGG",".BIK"};
  struct Copy {
    std::string source;
    int64_t     time = -1; // archives only
    };
  // Every copy of each virtual path, from each source mounted alone.
  std::map<std::string,std::vector<Copy>> copies;
  for(const auto& a:archives) {
    zenkit::Vfs single;
    single.mount_disk(a.path,zenkit::VfsOverwriteBehavior::OLDER);
    std::map<const zenkit::VfsNode*,std::string> own;
    collectPaths(single.root(),"",own);
    for(const auto& [node,path]:own)
      if(kinds.count(extension(node->name())))
        copies[upper(path)].push_back({a.name,a.timestamp});
    }
  if(fs::is_directory(work))
    for(const auto& e:fs::recursive_directory_iterator(work))
      if(e.is_regular_file() && kinds.count(extension(e.path().filename().string())))
        copies[upper("_work/"+e.path().lexically_relative(work).generic_string())].push_back({relative(e.path()),-1});

  std::map<std::string,std::vector<const zenkit::VfsNode*>> requests;
  for(const auto& [node,path]:paths)
    if(kinds.count(extension(node->name())))
      requests[upper(node->name())].push_back(node);

  std::string s = "[";
  size_t count = 0, collisions = 0, shadowed = 0, dependent = 0;
  for(auto& [request,nodes]:requests) {
    const auto* winner = vfs.find(request);
    require(winner!=nullptr,"Vfs::find does not resolve "+request);
    const auto c = openNode(winner,"vfs");
    const auto ext = extension(request);
    std::string e = "\n    {\"request\":"+str(request)+",\"path\":"+str(c.path)+",\"source\":"+str(c.source)+
                    ",\"size\":"+std::to_string(c.digest.size)+",\"sha256\":\""+c.digest.sha256+"\"";
    if(ext==".WAV" || ext==".OGG")
      e += ",\"audio\":"+audioHeader(c.digest.head);
    std::sort(nodes.begin(),nodes.end(),[&](auto* a, auto* b) { return upper(paths.at(a))<upper(paths.at(b)); });
    const auto& winnerCopies = copies[upper(c.path)];
    if(nodes.size()>1 || winnerCopies.size()>1) {
      if(nodes.size()>1) ++collisions;
      if(winnerCopies.size()>1) ++shadowed;
      e += ",\"candidates\":[";
      for(size_t i=0; i<nodes.size(); ++i) {
        const auto& path = paths.at(nodes[i]);
        e += std::string(i ? "," : "")+"{\"path\":"+str(path)+",\"selected\":"+(nodes[i]==winner ? "true" : "false")+",\"copies\":[";
        const auto& list = copies[upper(path)];
        for(size_t k=0; k<list.size(); ++k)
          e += std::string(k ? "," : "")+str(list[k].source);
        e += "]}";
        }
      e += "]";
      int64_t winnerTime = -1;
      size_t  atTime = 0;
      for(const auto& copy:winnerCopies)
        if(copy.source==c.source) winnerTime = copy.time;
      for(const auto& copy:winnerCopies)
        if(copy.time>=0 && copy.time==winnerTime) ++atTime;
      if(atTime>1) {
        e += ",\"order_dependent\":true";
        ++dependent;
        }
      }
    s += std::string(count++ ? "," : "")+e+"}";
    }
  std::cout << "sources: " << count << " FNT/audio/BIK requests, " << collisions << " basename collisions, "
            << shadowed << " shadowed paths, " << dependent << " mount-order dependent\n";
  return s+"\n  ]";
  }

// The game's GothicKit resolver: Vfs::find of the exact requested name, whole file in
// malloc'd memory that dmusic frees.
void* Manifest::resolveMusic(void* ctx, char const* file, size_t* len) {
  auto& trace = *static_cast<Trace*>(ctx);
  Attempt attempt;
  attempt.request = file;
  const auto* node = trace.self->vfs.find(file);
  if(node==nullptr) {
    trace.attempts.push_back(std::move(attempt));
    return nullptr;
    }
  std::vector<uint8_t> bytes;
  attempt.found = trace.self->openNode(node,"vfs",&bytes);
  trace.attempts.push_back(std::move(attempt));
  void* out = std::malloc(bytes.empty() ? 1 : bytes.size());
  if(out==nullptr)
    return nullptr;
  std::memcpy(out,bytes.data(),bytes.size());
  *len = bytes.size();
  return out;
  }

void Manifest::visitMusic(void* ctx, BaseMusicReference const* reference) {
  auto& trace = *static_cast<Trace*>(ctx);
  auto r = *reference;
  // The segment is released after the trace: keep the strings.
  trace.strings.emplace_back(r.file ? r.file : "");
  trace.strings.emplace_back(r.name ? r.name : "");
  trace.hasFile.push_back(r.file!=nullptr);
  r.file = nullptr;
  r.name = nullptr;
  trace.references.push_back(r);
  }

std::string Manifest::gothicKitJson(const std::string& root, bool& closed) {
  Trace trace;
  trace.self = this;
  int loaded = 0;
  const int result = BaseMusic_trace(root.c_str(),&Manifest::resolveMusic,&trace,&Manifest::visitMusic,&trace,&loaded);
  // DmSegment_download stops at the first failed style or band, so the references after it
  // were never opened: only a successful download is a complete traversal.
  closed = loaded && result==0;
  std::string s = "\n      {\"root\":"+str(root)+",\"result\":"+std::to_string(result)+",\"loaded\":"+(loaded ? "true" : "false")+
                  ",\"dependency_closure\":"+(closed ? "true" : "false")+",\"attempts\":[";
  for(size_t i=0; i<trace.attempts.size(); ++i) {
    const auto& a = trace.attempts[i];
    s += std::string(i ? "," : "")+"{\"request\":"+str(a.request)+",\"found\":"+(a.found ? choiceJson(*a.found) : std::string("null"))+"}";
    }
  s += "],\"references\":[";
  // Objects come from the loader's GUID caches: later references reuse the first load.
  std::map<const void*,std::pair<size_t,std::string>> objects; // id, file of the first reference
  for(size_t i=0; i<trace.references.size(); ++i) {
    const auto& r = trace.references[i];
    const auto& file = trace.strings[2*i];
    const bool hasFile = trace.hasFile[i];
    bool requested = false;
    for(const auto& a:trace.attempts)
      requested = requested || (hasFile && a.request==file);
    std::string e = std::string("{\"kind\":")+(r.style ? "\"style\"" : "\"instrument\"")+",\"message\":"+std::to_string(r.message);
    if(!r.style)
      e += std::string(",\"band\":")+(r.band==SIZE_MAX ? std::string("\"segment\"") : std::to_string(r.band))+
           ",\"instrument\":"+std::to_string(r.instrument)+",\"options\":"+std::to_string(r.options)+
           ",\"downloadable\":"+(r.downloadable ? "true" : "false");
    e += ",\"file\":"+(hasFile ? str(file) : std::string("null"))+",\"name\":"+str(trace.strings[2*i+1])+
         ",\"guid\":\""+hex(r.guid)+"\",\"name_requested\":"+(requested ? "true" : "false");
    if(r.object!=nullptr) {
      auto [it,inserted] = objects.emplace(r.object,std::make_pair(objects.size(),file));
      const bool alias = !inserted && it->second.second!=file;
      e += ",\"object\":{\"id\":"+std::to_string(it->second.first)+",\"guid\":\""+hex(r.object_guid)+
           "\",\"first_file\":"+str(it->second.second)+"}";
      if(alias || std::memcmp(r.guid,r.object_guid,16)!=0)
        e += ",\"cache_alias\":true";
      }
    else {
      e += ",\"object\":null";
      }
    s += std::string(i ? "," : "")+e+"}";
    }
  return s+"]}";
  }

std::string Manifest::musicJson() {
  static const std::set<std::string> kinds = {".SGT",".STY",".DLS"};
  std::string s = "{\n    \"corpus\":[";
  std::map<std::string,const zenkit::VfsNode*> roots;
  // Serialize by full virtual path, not by node address.
  std::map<std::string,const zenkit::VfsNode*> music;
  for(const auto& [node,path]:paths)
    if(kinds.count(extension(node->name())))
      music.emplace(path,node);
  size_t count = 0;
  for(const auto& [path,node]:music) {
    s += std::string(count++ ? "," : "")+"\n      "+choiceJson(openNode(node,"corpus"));
    if(extension(node->name())==".SGT")
      roots.emplace(upper(node->name()),node);
    }
  // Every selected segment is a root: full file coverage, not reachability from a theme.
  // Each root gets a cold loader, so its edges do not depend on other roots' caches.
  s += "\n    ],\n    \"gothickit\":{\"roots\":[";
  size_t traced = 0;
  bool gothicKitClosed = true;
  for(const auto& [key,any]:roots) {
    const auto* winner = vfs.find(any->name());
    require(winner!=nullptr,"Vfs::find does not resolve "+key);
    bool closed = false;
    s += std::string(traced++ ? "," : "")+gothicKitJson(winner->name(),closed);
    gothicKitClosed = gothicKitClosed && closed;
    }
  s += std::string("\n    ],\"dependency_closure\":")+(gothicKitClosed ? "true" : "false")+"},\n";

  // Resources: the Dx8::DirectMusic search paths, in order.
  const std::vector<std::u16string> dirs = {
    nested({u"_work",u"Data",u"Music",u"newworld"},  Dir::FT_Dir),
    nested({u"_work",u"Data",u"Music",u"AddonWorld"},Dir::FT_Dir),
    nested({u"_work",u"Data",u"Music",u"dungeon"},   Dir::FT_Dir),
    nested({u"_work",u"Data",u"Music",u"menu_men"},  Dir::FT_Dir),
    nested({u"_work",u"Data",u"Music",u"orchestra"}, Dir::FT_Dir),
    nested({u"_work",u"Data",u"Music"},              Dir::FT_Dir),
    };
  // Roots: every segment file in a search directory, by its own spelling.
  std::set<std::u16string> dx8Roots;
  s += "    \"dx8\":{\"search\":[";
  for(size_t i=0; i<dirs.size(); ++i) {
    s += std::string(i ? "," : "")+"{\"path\":"+str(relative(fs::path(dirs[i])))+",\"exists\":"+
         (FileUtil::exists(dirs[i]) ? "true" : "false")+"}";
    Dir::scan(dirs[i],[&dx8Roots](const std::u16string& name, Dir::FileType t) {
      if(t!=Dir::FT_File || name.size()<4)
        return;
      auto ext = name.substr(name.size()-4);
      for(auto& c:ext)
        if(u'A'<=c && c<=u'Z')
          c = char16_t(c-u'A'+u'a');
      if(ext==u".sgt")
        dx8Roots.insert(name);
      });
    }
  s += "],\"roots\":[";
  size_t dx8Count = 0;
  bool dx8Closed = true;
  for(const auto& root:dx8Roots) {
    bool closed = false;
    s += std::string(dx8Count++ ? "," : "")+dx8Json(dirs,root,closed);
    dx8Closed = dx8Closed && closed;
    }
  return s+"\n    ],\"dependency_closure\":"+(dx8Closed ? "true" : "false")+"}\n  }";
  }

// Dx8::DirectMusic::load and the PatternList constructor for one root with cold caches:
// the segment, then for every style reference the style (cached by its exact file string)
// and every non-empty instrument file of its bands (DLS, cached the same way). Files are
// found by the game's own search; DLS files are opened and hashed, not parsed.
std::string Manifest::dx8Json(const std::vector<std::u16string>& dirs, const std::u16string& root, bool& closed) {
  const auto open = [&](const std::u16string& file, std::vector<uint8_t>* bytes) {
    std::u16string opened;
    auto fin = Dx8::openSourceFile(dirs,file.c_str(),opened);
    std::vector<uint8_t> data(fin.size());
    (void)fin.read(data.data(),data.size());
    Choice c;
    c.step = "dx8";
    c.host = fs::path(opened);
    c.path = relative(c.host);
    c.source = c.path;
    Sha256 sha;
    sha.update(data.data(),data.size());
    c.digest.size = data.size();
    c.digest.sha256 = sha.finish();
    if(bytes!=nullptr)
      *bytes = std::move(data);
    return c;
    };
  std::vector<std::string> events;
  size_t failure = SIZE_MAX; // first event at which the game's load throws
  const auto event = [&](const std::string& where, const std::u16string& request, const std::string& result, bool failed) {
    if(failed && failure==SIZE_MAX)
      failure = events.size();
    events.push_back("{"+where+",\"request\":"+str16(request)+","+result+"}");
    };
  const auto error = [](const std::exception& e) { return "\"error\":"+str(e.what()); };

  std::map<std::u16string,std::shared_ptr<Dx8::Style>> styles;
  std::map<std::u16string,Choice> dls;
  bool unopenedStyle = false; // its bands, and so their DLS references, stay unknown
  size_t emptyFiles = 0;
  std::vector<uint8_t> data;
  std::optional<Dx8::Segment> segment;
  try {
    const auto c = open(root,&data);
    try {
      Dx8::Riff riff(data.data(),data.size());
      segment.emplace(riff);
      event("\"kind\":\"segment\"",root,"\"found\":"+choiceJson(c),false);
      }
    catch(const std::exception& e) { event("\"kind\":\"segment\"",root,"\"found\":"+choiceJson(c)+","+error(e),true); }
    }
  catch(const std::exception& e) { event("\"kind\":\"segment\"",root,error(e),true); }

  if(segment) {
    for(size_t t=0; t<segment->track.size(); ++t) {
      const auto& track = segment->track[t];
      if(track.sttr==nullptr)
        continue;
      for(size_t k=0; k<track.sttr->styles.size(); ++k) {
        const auto& ref = track.sttr->styles[k].reference;
        const auto where = "\"kind\":\"style\",\"track\":"+std::to_string(t)+",\"style\":"+std::to_string(k);
        std::shared_ptr<Dx8::Style> style;
        if(auto it=styles.find(ref.file); it!=styles.end()) {
          style = it->second;
          event(where,ref.file,"\"cache\":true",false);
          }
        else {
          try {
            std::vector<uint8_t> bytes;
            const auto c = open(ref.file,&bytes);
            try {
              Dx8::Riff riff{bytes.data(),bytes.size()};
              style = std::make_shared<Dx8::Style>(riff);
              styles[ref.file] = style;
              event(where,ref.file,"\"found\":"+choiceJson(c),false);
              }
            catch(const std::exception& e) {
              unopenedStyle = true;
              event(where,ref.file,"\"found\":"+choiceJson(c)+","+error(e),true);
              }
            }
          catch(const std::exception& e) {
            unopenedStyle = true;
            event(where,ref.file,error(e),true);
            }
          }
        if(style==nullptr)
          continue;
        for(size_t b=0; b<style->band.size(); ++b)
          for(size_t i=0; i<style->band[b].intrument.size(); ++i) {
            const auto& file = style->band[b].intrument[i].reference.file;
            if(file.empty()) {
              ++emptyFiles;
              continue;
              }
            const auto at = where+",\"band\":"+std::to_string(b)+",\"instrument\":"+std::to_string(i);
            const auto dlsWhere = "\"kind\":\"dls\""+at.substr(std::string("\"kind\":\"style\"").size());
            if(dls.count(file)) {
              event(dlsWhere,file,"\"cache\":true",false);
              continue;
              }
            try {
              const auto c = open(file,nullptr);
              dls.emplace(file,c);
              event(dlsWhere,file,"\"found\":"+choiceJson(c),false);
              }
            catch(const std::exception& e) { event(dlsWhere,file,error(e),true); }
            }
        }
      }
    }
  // A missing DLS is a known leaf; a segment or style that did not parse hides its children.
  closed = segment.has_value() && !unopenedStyle;
  std::string s = "\n      {\"root\":"+str16(root)+",\"dependency_closure\":"+(closed ? "true" : "false")+",\"engine_failure\":"+
                  (failure==SIZE_MAX ? std::string("null") : std::to_string(failure))+
                  ",\"empty_instrument_files\":"+std::to_string(emptyFiles)+",\"events\":[";
  for(size_t i=0; i<events.size(); ++i)
    s += std::string(i ? "," : "")+events[i];
  return s+"]}";
  }

std::string Manifest::scriptsJson() {
  struct Request {
    const char* name;
    bool        localized;
    bool        game;
    };
  // Gothic::createPhoenixVm callers, spelled as they are; GOTHIC.DAT is the default game DAT.
  const std::array<Request,8> requests = {{
    {"Camera.dat",false,false},{"Fight.dat",false,false},{"Sfx.dat",false,false},{"Music.dat",false,false},
    {"ParticleFx.dat",false,false},{"VisualFx.dat",false,false},{"MENU.DAT",true,false},{"GOTHIC.DAT",true,true},
    }};
  // CommandLine::scriptPath() and scriptPath(lang).
  const auto scripts = nested({u"_work",u"Data",u"Scripts",u"_compiled"},Dir::FT_Dir);
  std::string s = "[";
  size_t count = 0;
  for(const auto& r:requests) {
    const std::string name = r.name;
    const auto segment = name.find_last_of("\\/");
    const auto base = segment==std::string::npos ? name : name.substr(segment+1);
    std::unique_ptr<zenkit::Read> program;
    std::string chosen = "null", localized;
    // Gothic::loadScript resolution order.
    if(const auto* node = vfs.find(name)) {
      const auto c = openNode(node,"vfs");
      chosen = choiceJson(c);
      program = node->open_read();
      if(r.game) engineGameDat = c.host;
      }
    else if(const auto* node = segment==std::string::npos ? nullptr : vfs.find(base)) {
      const auto c = openNode(node,"vfs-basename");
      chosen = choiceJson(c);
      program = node->open_read();
      if(r.game) engineGameDat = c.host;
      }
    else if(const auto file = FileUtil::caseInsensitiveSegment(scripts,widen(name).c_str(),Dir::FT_File);
            FileUtil::exists(file)) {
      chosen = choiceJson(openHost(fs::path(file),"scripts"));
      program = zenkit::Read::from(fs::path(file));
      if(r.game) engineGameDat = fs::path(file);
      }
    else if(r.localized) {
      localized = ",\"localized\":{";
      for(size_t i=0; i<scriptLanguages.size(); ++i) {
        const auto dir = nested({u"_work",u"Data",scriptLanguages[i],u"_compiled"},Dir::FT_Dir);
        auto file = FileUtil::caseInsensitiveSegment(dir,widen(name).c_str(),Dir::FT_File);
        if(!FileUtil::exists(file))
          file = FileUtil::caseInsensitiveSegment(dir,widen(base).c_str(),Dir::FT_File);
        localized += std::string(i ? "," : "")+"\""+languageNames[i]+"\":"+
                     (FileUtil::exists(file) ? choiceJson(openHost(fs::path(file),"localized")) : std::string("null"));
        }
      localized += "}";
      }
    require(chosen!="null" || localized.find("\"step\"")!=std::string::npos,"script not found: "+name);
    std::string declared;
    if(program!=nullptr) {
      // Parsing only: no VM is created and no instance initializer runs.
      zenkit::DaedalusScript script;
      script.load(program.get());
      declared = ",\"symbols\":"+std::to_string(script.symbols().size())+(r.game ? ",\"declared_counts\":{" : ",\"declared\":{");
      std::map<std::string,std::vector<std::string>> classes;
      for(const auto& sym:script.symbols())
        if(sym.type()==zenkit::DaedalusDataType::CLASS)
          classes[sym.name()];
      for(auto& [cls,instances]:classes)
        script.enumerate_instances_by_class_name(cls,[&instances](zenkit::DaedalusSymbol& sym) { instances.push_back(sym.name()); });
      bool first = true;
      for(const auto& [cls,instances]:classes) {
        if(instances.empty())
          continue;
        declared += std::string(first ? "" : ",")+str(cls)+":";
        first = false;
        if(r.game) {
          declared += std::to_string(instances.size());
          continue;
          }
        declared += "[";
        for(size_t i=0; i<instances.size(); ++i)
          declared += std::string(i ? "," : "")+str(instances[i]);
        declared += "]";
        }
      declared += "}";
      }
    s += std::string(count++ ? "," : "")+"\n    {\"request\":"+str(name)+",\"language\":"+(r.localized ? "\"setting\"" : "\"none\"")+
         ",\"selected\":"+chosen+localized+declared+"}";
    }
  return s+"\n  ]";
  }

// GameScript::loadDialogOU with the default OUTPUTUNITS prefix, for no language and each
// language setting. Candidates are looked at only when the game reaches them: it returns
// after the first CutsceneLibrary that parses completely.
std::string Manifest::outputUnitsJson() {
  // CommandLine::cutscenePath() and cutscenePath(lang).
  const auto cutscene = nested({u"_work",u"Data",u"Scripts",u"content",u"CUTSCENE"},Dir::FT_Dir);
  // Host branch: any failure lets the game try the next candidate. A null object is
  // dereferenced by the game; it is not treated as a successful parse here.
  std::map<std::string,std::string> hostParse; // path -> "" when parsed, otherwise the reason
  const auto parseHost = [&](const fs::path& file) {
    const auto key = file.lexically_normal().string();
    if(auto it=hostParse.find(key); it!=hostParse.end())
      return it->second;
    std::string reason;
    try {
      auto in = zenkit::Read::from(file);
      auto zen = in==nullptr ? nullptr : zenkit::ReadArchive::from(in.get());
      if(zen==nullptr)
        reason = "not a ZenGin archive";
      else if(zen->read_object<zenkit::CutsceneLibrary>(zenkit::GameVersion::GOTHIC_2)==nullptr)
        reason = "no object";
      }
    catch(const std::exception& e) { reason = e.what(); }
    catch(...) { reason = "unknown error"; }
    return hostParse[key] = reason;
    };
  // VFS branch: a parse error ends the game's load; once reached, the import fails the same way.
  std::map<std::string,Choice> vfsParsed;
  const auto parseVfs = [&](const std::string& name, const zenkit::VfsNode* node) {
    if(auto it=vfsParsed.find(name); it!=vfsParsed.end())
      return it->second;
    const auto choice = openNode(node,"vfs");
    try {
      auto in = node->open_read();
      auto zen = zenkit::ReadArchive::from(in.get());
      require(zen!=nullptr,"not a ZenGin archive");
      require(zen->read_object<zenkit::CutsceneLibrary>(zenkit::GameVersion::GOTHIC_2)!=nullptr,"no object");
      }
    catch(const std::exception& e) { throw std::runtime_error(name+" in the VFS does not load: "+e.what()); }
    return vfsParsed[name] = choice;
    };

  std::string reached, selected;
  bool any = false;
  for(int lang=-1; lang<int(scriptLanguages.size()); ++lang) {
    const std::string language = lang<0 ? "NONE" : languageNames[size_t(lang)];
    std::optional<Choice> chosen;
    for(const char* candidate:{"OU.BIN","OU.DAT"}) {
      const std::string name = candidate;
      std::string result;
      if(const auto* node = vfs.find(name)) {
        chosen = parseVfs(name,node);
        result = choiceJson(*chosen);
        }
      else {
        auto file = fs::path(FileUtil::caseInsensitiveSegment(cutscene,widen(name).c_str(),Dir::FT_File));
        std::string step = "cutscene";
        if(!FileUtil::exists(file.u16string()) && lang>=0) {
          const auto dir = nested({u"_work",u"Data",scriptLanguages[size_t(lang)]},Dir::FT_Dir);
          file = fs::path(FileUtil::caseInsensitiveSegment(dir,widen(name).c_str(),Dir::FT_File));
          step = "localized";
          }
        if(!FileUtil::exists(file.u16string()))
          result = "{\"step\":"+str(step)+",\"path\":"+str(relative(file))+",\"missing\":true}";
        else if(const auto reason = parseHost(file); !reason.empty())
          result = "{\"step\":"+str(step)+",\"path\":"+str(relative(file))+",\"rejected\":"+str(reason)+"}";
        else {
          chosen = openHost(file,step);
          result = choiceJson(*chosen);
          }
        }
      reached += std::string(reached.empty() ? "" : ",")+"{\"language\":\""+language+"\",\"name\":"+str(name)+
                 ",\"result\":"+result+"}";
      if(chosen)
        break;
      }
    selected += std::string(selected.empty() ? "" : ",")+"\""+language+"\":"+(chosen ? choiceJson(*chosen) : std::string("null"));
    any = any || chosen.has_value();
    // The server readers are compared with the default profile's file; an empty host
    // means the winner came from an archive.
    if(lang<0 && chosen)
      engineOu = chosen->host;
    }
  require(any,"output units not found: no loadable OU.BIN/OU.DAT");
  return "{\"prefix\":\"OU\",\"reached\":["+reached+"],\"selected\":{"+selected+"}}";
  }

std::string Manifest::serverJson() {
  const auto firstChild = [](const fs::path& dir, const std::string& name) {
    std::error_code ec;
    for(const auto& e:fs::directory_iterator(dir,ec))
      if(upper(e.path().filename().string())==upper(name))
        return e.path();
    return fs::path();
    };
  const auto entry = [&](const fs::path& file, const fs::path& engine) {
    if(file.empty() || !fs::is_regular_file(file))
      return std::string("null");
    std::error_code ec;
    const bool same = !engine.empty() && fs::equivalent(file,engine,ec);
    auto json = choiceJson(openHost(file,"filesystem"));
    json.insert(json.size()-1,std::string(",\"same_as_engine\":")+(same ? "true" : "false"));
    return json;
    };
  // server/src/gothic_ai/gamedata/ou.py find_ou_file
  fs::path ou;
  if(const auto dir = gothic/"_work"/"Data"/"Scripts"/"CONTENT"/"CUTSCENE"; fs::is_directory(dir))
    ou = firstChild(dir,"OU.BIN");
  // server/src/gothic_ai/gamedata/storygraph.py find_dat
  fs::path dat = gothic;
  for(const char* part:{"_work","Data","Scripts","_compiled","GOTHIC.DAT"}) {
    if(!fs::is_directory(dat)) { dat.clear(); break; }
    const auto next = firstChild(dat,part);
    dat = next.empty() ? dat/part : next;
    }
  // OpenGothic/ai_bridge/storygraph findOu(dat)
  fs::path graphOu;
  if(!dat.empty() && fs::is_regular_file(dat)) {
    const auto content = firstChild(dat.parent_path().parent_path(),"CONTENT");
    const auto cut = content.empty() ? fs::path() : firstChild(content,"CUTSCENE");
    graphOu = cut.empty() ? fs::path() : firstChild(cut,"OU.BIN");
    }
  return "{\n    \"ou_py\":"+entry(ou,engineOu)+",\n    \"storygraph_dat\":"+entry(dat,engineGameDat)+
         ",\n    \"storygraph_ou\":"+entry(graphOu,engineOu)+"\n  }";
  }

std::string Manifest::json() {
  const auto files   = filesJson();
  const auto music   = musicJson();
  const auto scripts = scriptsJson();
  const auto ou      = outputUnitsJson();
  const auto server  = serverJson();
  return "{\n  \"format\":\"gothic-sources\",\n  \"version\":1,\n  \"evidence\":\"import\",\n"
         "  \"profile\":{\"game\":\"GOTHIC.DAT\",\"output_units\":\"OU\",\"mods\":[],\"languages\":[\"NONE\",\"EN\",\"DE\",\"PL\",\"RU\",\"FR\",\"ES\",\"IT\",\"CZ\"]},\n"
         "  \"archives\":"+archivesJson()+",\n  \"files\":"+files+",\n  \"music\":"+music+
         ",\n  \"scripts\":"+scripts+",\n  \"output_units\":"+ou+",\n  \"server\":"+server+",\n"
         "  \"not_covered\":[\"runtime reachability of listed files\",\"request name variants built at run time\","
         "\"names produced by instance initializers\",\"music caches shared across themes at run time\","
         "\"Dx8 DLS parsing (DLS files are opened and hashed only)\","
         "\"payload decoding\"]\n}\n";
  }

rapidjson::Document parse(const std::string& text, const std::string& what) {
  rapidjson::Document doc;
  doc.Parse(text.c_str(),text.size());
  require(!doc.HasParseError() && doc.IsObject(),"invalid JSON: "+what);
  return doc;
  }

std::string readText(const fs::path& file) {
  std::ifstream f(file,std::ios::binary);
  require(bool(f),"cannot open "+file.string());
  return std::string(std::istreambuf_iterator<char>(f),std::istreambuf_iterator<char>());
  }
}

std::vector<RuntimeArchive> runtimeArchives(const fs::path& gothic) {
  std::vector<Scanned> archives;
  detectVdf(archives,FileUtil::nestedPath(installation(gothic),{u"Data"},Dir::FT_Dir));

  // Resources::loadVdfs: addon archives first!
  std::stable_sort(archives.begin(),archives.end(),[](const Scanned& a,const Scanned& b){
    int aIsMod = a.isMod ? 1 : -1;
    int bIsMod = b.isMod ? 1 : -1;
    return std::make_tuple(aIsMod,a.time,int(a.ord)) >
           std::make_tuple(bIsMod,b.time,int(b.ord));
    });

  std::vector<RuntimeArchive> out;
  for(auto& a:archives) {
    RuntimeArchive r;
    r.path      = fs::path(a.name);
    r.name      = upper(r.path.filename().string());
    r.size      = fs::file_size(r.path);
    r.timestamp = a.time;
    r.mod       = a.isMod;
    out.push_back(std::move(r));
    }
  return out;
  }

int convertSources(const fs::path& gothic, const fs::path& out, bool verifyOnly) {
  const auto t0 = std::chrono::steady_clock::now();
  const auto text = Manifest(gothic).json();
  if(!verifyOnly) {
    fs::create_directories(out);
    const auto tmp = out/"sources.json.tmp";
    {
    std::ofstream f(tmp,std::ios::binary|std::ios::trunc);
    f.write(text.data(),std::streamsize(text.size()));
    require(bool(f),"cannot write "+tmp.string());
    }
    fs::rename(tmp,out/"sources.json");
    }
  require(parse(readText(out/"sources.json"),"sources.json")==parse(text,"expected sources"),
          "sources.json differs from the installation");
  const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t0).count();
  std::cout << (verifyOnly ? "verified" : "written and verified") << " source manifest (" << ms << " ms)\n";
  return 0;
  }

}
