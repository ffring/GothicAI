// Writes the textures of the game's characters as PNG files, for scripts/hd-textures.sh to upscale.
//
//   texdump <game dir> <out dir> [--creatures]
//
// The textures the human models (heads, bodies, armors: HUM_*, ARMOR_* meshes of the anims archives) use,
// with all their face and skin variants (_V<n>, _C<n> in the name, the engine picks one per npc); with
// --creatures those of every model. Each one as <NAME>.png (RGBA), and a line in <out dir>/textures.txt:
// name, width, height, whether it has alpha.
#include <zenkit/Vfs.hh>
#include <zenkit/ModelMesh.hh>
#include <zenkit/MorphMesh.hh>
#include <zenkit/Texture.hh>
#include <zenkit/Stream.hh>

#include <miniz.h>

#include <cctype>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <functional>
#include <map>
#include <regex>
#include <set>
#include <string>

namespace fs = std::filesystem;

namespace {

bool endsWith(const std::string& s, const char* e) {
  const size_t n = std::strlen(e);
  return s.size()>=n && s.compare(s.size()-n,n,e)==0;
  }

// the face and skin variants of a texture share this
std::string variantKey(std::string n) {
  static const std::regex v("_V[0-9]+"), c("_C[0-9]+");
  return std::regex_replace(std::regex_replace(n,v,"_V#"),c,"_C#");
  }

void walk(const zenkit::VfsNode& n, const std::function<void(const zenkit::VfsNode&)>& fn) {
  if(n.type()==zenkit::VfsNodeType::DIRECTORY) {
    for(auto& c:n.children())
      walk(c,fn);
    return;
    }
  fn(n);
  }

}

int main(int argc, char** argv) {
  if(argc<3) {
    std::fprintf(stderr,"usage: texdump <game dir> <out dir> [--creatures]\n");
    return 2;
    }
  const fs::path game = argv[1], out = argv[2];
  const bool     all  = argc>3 && std::string(argv[3])=="--creatures";

  zenkit::Vfs vfs;
  for(auto& e:fs::directory_iterator(game/"Data")) {
    const auto n = e.path().filename().string();
    // the meshes and the textures; not the worlds, the sounds, the speech
    std::string u = n;
    for(auto& ch:u)
      ch = char(std::toupper(ch));
    if(endsWith(u,".VDF") && (u.rfind("ANIMS",0)==0 || u.rfind("TEXTURES",0)==0 || u.rfind("MESHES",0)==0))
      vfs.mount_disk(e.path(),zenkit::VfsOverwriteBehavior::OLDER);
    }

  // the textures the models use
  std::set<std::string> used;
  walk(vfs.root(),[&](const zenkit::VfsNode& n) {
    const auto& name  = n.name();
    const bool  human = name.rfind("HUM_",0)==0 || name.rfind("ARMOR_",0)==0;
    if(!all && !human)
      return;
    try {
      if(endsWith(name,".MDM")) {
        zenkit::ModelMesh m;
        auto r = n.open_read();
        m.load(r.get());
        for(auto& s:m.meshes)
          for(auto& mat:s.mesh.materials)
            used.insert(mat.texture);
        for(auto& a:m.attachments)
          for(auto& mat:a.second.materials)
            used.insert(mat.texture);
        }
      else if(endsWith(name,".MMB")) {
        zenkit::MorphMesh m;
        auto r = n.open_read();
        m.load(r.get());
        for(auto& mat:m.mesh.materials)
          used.insert(mat.texture);
        }
      }
    catch(...) {
      std::fprintf(stderr,"texdump: unreadable %s\n",name.c_str());
      }
    });

  std::set<std::string> keys;
  for(auto& t:used)
    if(endsWith(t,".TGA"))
      keys.insert(variantKey(t.substr(0,t.size()-4)));

  fs::create_directories(out);
  std::FILE* list = std::fopen((out/"textures.txt").string().c_str(),"w");
  size_t     count = 0;
  walk(vfs.root(),[&](const zenkit::VfsNode& n) {
    const auto& name = n.name();
    if(!endsWith(name,"-C.TEX"))
      return;
    const auto base = name.substr(0,name.size()-6);
    if(keys.find(variantKey(base))==keys.end())
      return;
    try {
      zenkit::Texture tex;
      auto r = n.open_read();
      tex.load(r.get());
      const auto rgba  = tex.as_rgba8(0);
      bool       alpha = false;
      for(size_t i=3; i<rgba.size(); i+=4)
        alpha |= rgba[i]<255;
      size_t len = 0;
      void*  png = tdefl_write_image_to_png_file_in_memory_ex(rgba.data(),int(tex.width()),int(tex.height()),4,&len,6,false);
      if(png==nullptr)
        return;
      std::FILE* f = std::fopen((out/(base+".png")).string().c_str(),"wb");
      if(f!=nullptr) {
        std::fwrite(png,1,len,f);
        std::fclose(f);
        std::fprintf(list,"%s %u %u %d\n",base.c_str(),tex.width(),tex.height(),int(alpha));
        ++count;
        }
      mz_free(png);
      }
    catch(...) {
      std::fprintf(stderr,"texdump: unreadable %s\n",name.c_str());
      }
    });
  std::fclose(list);
  std::printf("texdump: %zu textures of %zu used ones and their variants\n",count,used.size());
  return 0;
  }
