// Read only the VobTree of the four stock worlds and export source meshes.
// Copyrighted game geometry stays in the user's local fracture cache.
#include <zenkit/Archive.hh>
#include <zenkit/MultiResolutionMesh.hh>
#include <zenkit/Stream.hh>
#include <zenkit/Vfs.hh>
#include <zenkit/world/VobTree.hh>

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <map>
#include <regex>
#include <sstream>
#include <stdexcept>

namespace fs = std::filesystem;

std::string upper(std::string s) {
  for(auto& c:s) c = char(std::toupper(static_cast<unsigned char>(c)));
  return s;
  }

int main(int argc, char** argv) {
  try {
    if(argc!=4) throw std::runtime_error("usage: fracture-extract <game-dir> <source-dir> <destructible-csv>");
    const fs::path game=argv[1], out=argv[2];
    fs::create_directories(out);
    std::vector<std::pair<std::regex,std::string>> rules;
    std::ifstream csv(argv[3]);
    if(!csv) throw std::runtime_error("cannot read destructible-vobs.csv");
    for(std::string line;std::getline(csv,line);) {
      if(line.empty() || line[0]=='#') continue;
      std::istringstream row(line);
      std::string glob,cls;
      std::getline(row,glob,','); std::getline(row,cls,',');
      if(cls!="crate" && cls!="barrel" && cls!="pot" && cls!="fence") continue;
      std::string re="^";
      for(char c:glob) re += c=='*' ? ".*" : c=='?' ? "." : std::string(1,c);
      rules.emplace_back(std::regex(re+"$"),cls);
      }
    // Pots must be ordinary visible mesh-vobs, never interactive inventory items.
    const std::regex pottery("(^|_)(POT|POTS|POTTERY|VASE|AMPHORA|AMPHORE|JAR)(_|[0-9]|$)");
    zenkit::Vfs vfs;
    std::vector<fs::path> archives;
    for(auto& e:fs::directory_iterator(game/"Data")) {
      auto name=upper(e.path().filename().string());
      if(e.is_regular_file() && name.ends_with(".VDF") &&
         (name.starts_with("MESHES") || name.starts_with("WORLDS"))) archives.push_back(e.path());
      }
    std::sort(archives.begin(),archives.end());
    for(auto& p:archives) vfs.mount_disk(p,zenkit::VfsOverwriteBehavior::OLDER);
    const char* worlds[]={"NEWWORLD.ZEN","OLDWORLD.ZEN","ADDONWORLD.ZEN","DRAGONISLAND.ZEN"};
    std::map<std::string,std::string> visuals;
    std::ofstream census(out/"census.csv");
    census << "world,visual,class,count\n";
    std::ofstream worldVisuals(out/"world-visuals.csv");
    worldVisuals << "world,visual,count\n";
    for(auto world:worlds) {
      const auto* node=vfs.find(world);
      if(!node) throw std::runtime_error(std::string("world missing: ")+world);
      auto stream=node->open_read();
      auto ar=zenkit::ReadArchive::from(stream.get());
      zenkit::ArchiveObject hdr;
      if(!ar->read_object_begin(hdr)) throw std::runtime_error("world root missing");
      std::map<std::string,size_t> counts;
      std::map<std::string,size_t> allVisuals;
      std::function<void(const std::shared_ptr<zenkit::VirtualObject>&)> visit;
      visit=[&](auto& v) {
        if(!v) return;
        if(v->type==zenkit::VirtualObjectType::zCVob && v->show_visual && v->visual &&
           (v->visual->type==zenkit::VisualType::MESH || v->visual->type==zenkit::VisualType::MULTI_RESOLUTION_MESH) &&
           !(v->anim_mode!=zenkit::AnimationType::NONE && v->anim_strength>0.f)) {
          const auto name=upper(fs::path(v->visual->name).stem().string());
          ++allVisuals[name];
          std::string cls;
          for(auto& [re,c]:rules) if(std::regex_match(name,re)) { cls=c; break; }
          if(cls.empty() && std::regex_search(name,pottery)) cls="pot";
          if(!cls.empty()) { visuals[name]=cls; ++counts[name]; }
          }
        for(auto& child:v->children) visit(child);
        };
      // Binary stock archives contain byte sizes, so skip the large MeshAndBsp.
      while(!ar->read_object_end()) {
        if(!ar->read_object_begin(hdr)) throw std::runtime_error(std::string(world)+": invalid world child at "+std::to_string(stream->tell()));
        if(hdr.object_name=="MeshAndBsp") {
          // This section embeds untyped binary mesh data even in a BINSAFE
          // archive, so the archive parser cannot skip it as normal entries.
          auto* raw=ar->get_stream();
          (void)raw->read_uint(); // BSP version
          const auto bytes=raw->read_uint();
          raw->seek(bytes,zenkit::Whence::CUR);
          if(!ar->read_object_end()) ar->skip_object(true);
          } else if(hdr.object_name=="VobTree") {
          auto count=ar->read_int();
          for(int i=0;i<count;++i) visit(zenkit::parse_vob_tree(*ar,zenkit::GameVersion::GOTHIC_2));
          if(!ar->read_object_end()) ar->skip_object(true);
          } else ar->skip_object(true);
        }
      for(auto& [name,count]:counts) census << world << ',' << name << ',' << visuals[name] << ',' << count << '\n';
      for(auto& [name,count]:allVisuals) worldVisuals << world << ',' << name << ',' << count << '\n';
      std::cout << world << ": " << counts.size() << " destructible visuals\n";
      }
    std::ofstream list(out/"visuals.csv");
    list << "visual,class,source_triangles\n";
    for(auto& [name,cls]:visuals) {
      auto* node=vfs.find(name+".MRM");
      if(!node) throw std::runtime_error("compiled mesh missing: "+name);
      auto stream=node->open_read();
      zenkit::MultiResolutionMesh mesh;
      mesh.load(stream.get());
      std::ofstream source(out/(name+".mesh"));
      source << std::setprecision(9);
      size_t count=0;
      for(auto& sm:mesh.sub_meshes) for(auto& tri:sm.triangles) {
        source << "tri " << sm.mat.texture << '\n';
        for(auto idx:tri.wedges) {
          auto& w=sm.wedges.at(idx);
          auto& p=mesh.positions.at(w.index);
          source << p.x << ' ' << p.y << ' ' << p.z << ' '
                 << w.normal.x << ' ' << w.normal.y << ' ' << w.normal.z << ' '
                 << w.texture.x << ' ' << w.texture.y << '\n';
          }
        ++count;
        }
      list << name << ',' << cls << ',' << count << '\n';
      std::cout << name << " " << cls << " " << count << " triangles\n";
      }
    return 0;
    } catch(const std::exception& e) {
    std::cerr << "fracture-extract: " << e.what() << '\n';
    return 1;
    }
  }
