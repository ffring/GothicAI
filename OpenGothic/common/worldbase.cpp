#include "worldbase.h"

#include "worldchunks.h"

#include <zenkit/Stream.hh>

#include <chrono>
#include <cctype>
#include <stdexcept>

std::vector<std::byte> readWorldBytes(zenkit::Read& reader) {
  reader.seek(0,zenkit::Whence::END);
  std::vector<std::byte> bytes(reader.tell());
  reader.seek(0,zenkit::Whence::BEG);
  if(reader.read(bytes.data(),bytes.size())!=bytes.size())
    throw std::runtime_error("Unable to read complete ZEN source");
  return bytes;
  }

WorldBaseResult tryWorldBase(zenkit::Mesh& mesh, std::string_view world,
                             const std::vector<std::byte>& source,
                             const std::filesystem::path& dir,
                             const std::filesystem::path& overrideDir) {
  WorldBaseResult result;
  if(dir.empty())
    return result;
  const auto started = std::chrono::steady_clock::now();
  try {
    if(!std::filesystem::exists(dir/"world.json"))
      return result;
    const auto manifest = readWorldChunkManifest(dir);
    const auto separator = world.find_last_of("/\\");
    std::string name(separator==std::string_view::npos ? world : world.substr(separator+1));
    for(auto& c:name)
      c = char(std::toupper(static_cast<unsigned char>(c)));
    if(manifest.source.world!=name)
      throw std::runtime_error("ZEN source world mismatch");
    if(manifest.source.size!=source.size())
      throw std::runtime_error("ZEN source size mismatch");
    if(manifest.source.crc32!=worldChunkCrc32(source.data(),source.size()))
      throw std::runtime_error("ZEN source crc32 mismatch");
    auto base = readWorldChunks(dir,overrideDir);
    result.triangles = base.polygons.material_indices.size();
    result.chunks = manifest.chunks.size();
    mesh.materials = std::move(base.materials);
    mesh.vertices = std::move(base.vertices);
    mesh.features = std::move(base.features);
    mesh.polygons = std::move(base.polygons);
    result.replaced = true;
    }
  catch(const std::exception& e) {
    result.error = e.what();
    }
  result.readMs = std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
  return result;
  }
