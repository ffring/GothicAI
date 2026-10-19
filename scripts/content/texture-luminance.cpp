// Original texture pixels remain in memory. Only scalar properties leave the decoder.
// Build against the engine's ZenKit ABI as scripts/content-textures.sh does.
// texture-luminance [--properties] <game dir> <TEXTURE.TGA>... | --self-test
#include <zenkit/Stream.hh>
#include <zenkit/Texture.hh>
#include <zenkit/Vfs.hh>

#include <cmath>
#include <cctype>
#include <cstdint>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
namespace {
std::string upper(std::string value) {
  for(auto& ch:value) ch=char(std::toupper(static_cast<unsigned char>(ch)));
  return value;
}

double linear(uint8_t channel) {
  const double value=double(channel)/255;
  return value<=0.04045 ? value/12.92 : std::pow((value+0.055)/1.055,2.4);
}

double luminance(const std::vector<uint8_t>& rgba) {
  if(rgba.empty() || rgba.size()%4) throw std::runtime_error("invalid RGBA pixel buffer");
  double total=0, weights=0;
  for(size_t i=0;i<rgba.size();i+=4) {
    const double alpha=double(rgba[i+3])/255;
    total+=alpha*(0.2126*linear(rgba[i])+0.7152*linear(rgba[i+1])+0.0722*linear(rgba[i+2]));
    weights+=alpha;
  }
  if(weights==0) throw std::runtime_error("texture has no visible pixels");
  return total/weights;
}

double alphaFraction(const std::vector<uint8_t>& rgba) {
  if(rgba.empty() || rgba.size()%4) throw std::runtime_error("invalid RGBA pixel buffer");
  size_t nonOpaque=0;
  for(size_t i=3;i<rgba.size();i+=4) if(rgba[i]<255) ++nonOpaque;
  return double(nonOpaque)/(rgba.size()/4);
}

void selfTest() {
  const auto require=[](bool ok) { if(!ok) throw std::runtime_error("luminance self-test failed"); };
  require(luminance({0,0,0,255})==0);
  require(std::abs(luminance({255,255,255,255})-1)<1e-12);
  require(std::abs(luminance({255,255,255,255,255,0,0,0})-1)<1e-12);
  require(std::abs(luminance({255,255,255,255,0,0,0,128})-255.0/383)<1e-12);
  require(std::abs(luminance({128,128,128,255})-0.21586050011389926)<1e-12);
  require(std::abs(luminance({255,0,0,255})-0.2126)<1e-12);
  bool rejected=false;
  try { luminance({255,255,255,0}); } catch(const std::runtime_error&) { rejected=true; }
  require(rejected);
  require(alphaFraction({0,0,0,255})==0);
  require(alphaFraction({255,255,255,0,0,0,0,128})==1);
  require(alphaFraction({255,255,255,255,0,0,0,0})==0.5);
  std::cout << "self-test: 10 checks passed\n";
}
}

int main(int argc,char** argv) {
  try {
    if(argc==2 && std::string(argv[1])=="--self-test") { selfTest(); return 0; }
    const bool properties=argc>1 && std::string(argv[1])=="--properties";
    const int gameArg=properties ? 2 : 1;
    if(argc<gameArg+2) throw std::runtime_error("usage: texture-luminance [--properties] <game dir> <TEXTURE.TGA>... | --self-test");
    std::map<std::string,fs::path> archives;
    for(const auto& entry:fs::directory_iterator(fs::path(argv[gameArg])/"Data")) {
      const auto name=upper(entry.path().filename().string());
      if(entry.is_regular_file() && name.starts_with("TEXTURES") && name.ends_with(".VDF"))
        archives[name]=entry.path();
    }
    if(archives.empty()) throw std::runtime_error("no Textures*.vdf archives");
    zenkit::Vfs vfs;
    for(const auto& [name,path]:archives)
      vfs.mount_disk(path,zenkit::VfsOverwriteBehavior::OLDER); // same archive policy as texdump.cpp
    std::set<std::string> seen;
    struct Result { std::string name; double luminance, alphaFraction; uint32_t width, height; };
    std::vector<Result> results;
    for(int i=gameArg+1;i<argc;++i) {
      const std::string texture=upper(argv[i]);
      if(!texture.ends_with(".TGA") || texture.find_first_of("/\\,\r\n")!=std::string::npos)
        throw std::runtime_error("expected a texture basename ending in .TGA");
      if(!seen.insert(texture).second) throw std::runtime_error("duplicate texture "+texture);
      const auto compiled=texture.substr(0,texture.size()-4)+"-C.TEX";
      const auto* file=vfs.find(compiled);
      if(!file) throw std::runtime_error("missing original texture "+compiled);
      zenkit::Texture tex;
      auto stream=file->open_read();
      tex.load(stream.get());
      // Use the exact top-mip decode path of scripts/hd/texdump.cpp.
      const auto rgba=tex.as_rgba8(0);
      results.push_back({texture,luminance(rgba),alphaFraction(rgba),tex.width(),tex.height()});
    }
    std::cout << "texture,target_luminance";
    if(properties) std::cout << ",nonopaque_fraction,opaque,width,height";
    std::cout << '\n' << std::setprecision(12);
    for(const auto& result:results) {
      std::cout << result.name << ',' << result.luminance;
      if(properties) std::cout << ',' << result.alphaFraction << ',' << (result.alphaFraction==0 ? "true" : "false")
                               << ',' << result.width << ',' << result.height;
      std::cout << '\n';
    }
  } catch(const std::exception& error) {
    std::cerr << "texture-luminance: " << error.what() << '\n';
    return 1;
  }
  return 0;
}
