#pragma once

#include <cstdint>
#include <filesystem>
#include <map>
#include <mutex>
#include <string>
#include <string_view>
#include <utility>

// Session-only accounting. Resources creates this object only with OPENGOTHIC_CENSUS.
class ContentCensus final {
  public:
    explicit ContentCensus(const char* output);

    void opened(std::string_view name, std::string_view source) noexcept;
    void replaced(std::string_view name, std::string_view replacement) noexcept;
    void missing(std::string_view name, std::string_view reason) noexcept;
    bool write() noexcept;

  private:
    using Key = std::pair<std::string, std::string>;
    std::filesystem::path output;
    std::mutex sync;
    std::map<Key, uint64_t> files;
    std::map<Key, uint64_t> replacements;
    std::map<Key, uint64_t> misses;
  };
