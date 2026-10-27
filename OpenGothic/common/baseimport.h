#pragma once

#include <filesystem>
#include <functional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>
#include <zenkit/Logger.hh>

namespace zenkit { class Vfs; }

namespace BaseImport {
using Files = std::vector<std::pair<std::string,uintmax_t>>;
using Progress = std::function<void(std::string_view)>;

struct Identity {
  std::string fingerprint;
  std::string json;
  };

// One file of Resources::loadVdfs's scan of Data, in its mount order.
struct RuntimeArchive {
  std::filesystem::path path;
  std::string           name; // upper case file name, as the engine reports mounted archives
  uintmax_t             size = 0;
  int64_t               timestamp = -1; // VDF header value Resources::vdfTimestamp sorts by
  bool                  mod = false;
  };

// Forward the application's ZenKit log callback here. Only active import reads
// consume errors; normal archive loading keeps its existing behavior.
void onZenkitLog(zenkit::LogLevel level, const char* component, const char* message);

int convertWorld(const std::filesystem::path& gothic, const std::string& name, const std::filesystem::path& out);
int verifyWorld(const std::filesystem::path& gothic, const std::string& name, const std::filesystem::path& out,
                const std::filesystem::path& overrides = {});
int convertZen(const std::filesystem::path& gothic, const std::filesystem::path& out, bool verifyOnly);
int convertTextures(const std::filesystem::path& gothic, const std::filesystem::path& out, bool verifyOnly);
int convertMeshes(const std::filesystem::path& gothic, const std::filesystem::path& out, bool verifyOnly);
int convertModels(const std::filesystem::path& gothic, const std::filesystem::path& out, bool verifyOnly);
// P11: fonts/fonts.json and one record per selected FNT basename (common/fontdata.h),
// selected through the engine's runtime VFS (mountRuntime).
int convertFonts(const std::filesystem::path& gothic, const std::filesystem::path& out, bool verifyOnly);
// P10: sources/sources.json, the selected inputs of the not yet migrated families
// (FNT, audio, BIK, SGT/STY/DLS, the eight DATs, OU, server readers) with origin and SHA-256.
int convertSources(const std::filesystem::path& gothic, const std::filesystem::path& out, bool verifyOnly);

std::string sha256(std::string_view bytes);
std::string sha256File(const std::filesystem::path& file);
// Every nonempty file under Data (Resources::detectVdf) in the engine's mount order,
// .mod files included and flagged; the caller applies the profile's filter.
std::vector<RuntimeArchive> runtimeArchives(const std::filesystem::path& gothic);
// Resources::loadVdfs without a mod ini, then Resources::mountWork: the engine's VFS of the
// supported Gothic II installation. Returns the mounted archives in mount order and sets
// `work` to the game's _work path (mounted with NONE only when it is a directory).
std::vector<RuntimeArchive> mountRuntime(zenkit::Vfs& vfs, const std::filesystem::path& gothic,
                                         std::filesystem::path& work);
// The identity binds the format, the content and mount order of the mounted archives
// and the content of every loose _work input. `mounted` is the engine's list.
Identity identify(const std::filesystem::path& gothic);
Identity identify(const std::filesystem::path& gothic, Files mounted);
// base.json: the identity bound to dir/sources/sources.json by size and SHA-256.
// Written last; a base without a matching manifest never matches.
void writeBaseMetadata(const std::filesystem::path& dir, const Identity& identity);
bool matches(const std::filesystem::path& dir, const Identity& identity);
void build(const std::filesystem::path& gothic, const std::filesystem::path& out, const Progress& progress = {});
}
