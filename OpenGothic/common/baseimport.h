#pragma once

#include <filesystem>
#include <functional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>
#include <zenkit/Logger.hh>

namespace BaseImport {
using Files = std::vector<std::pair<std::string,uintmax_t>>;
using Progress = std::function<void(std::string_view)>;

struct Identity {
  std::string fingerprint;
  std::string json;
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

std::string sha256(std::string_view bytes);
Identity identify(const std::filesystem::path& gothic);
Identity identify(const std::filesystem::path& gothic, Files mounted);
bool matches(const std::filesystem::path& dir, const Identity& identity);
void build(const std::filesystem::path& gothic, const std::filesystem::path& out, const Progress& progress = {});
}
