#pragma once

#include "baseimport.h"
#include <memory>
#include <future>
#include <stdexcept>

namespace BaseStore {
class Error : public std::runtime_error {
  public:
    explicit Error(const std::string& reason)
      : std::runtime_error("Cannot load the game base: "+reason+
          "\nUnset OPENGOTHIC_BASE to import automatically, or select a matching base."
          "\nCheck that -g points to a complete Gothic II installation with Data/*.vdf and _work,"
          "\nand that OPENGOTHIC_BASE_CACHE is writable.") {}
  };
class Lock;
struct Selection {
  std::filesystem::path path;
  // Keep the cache entry leased until its resource owner is destroyed.
  std::shared_ptr<Lock> lease;
  // Optional completion wait for callers that need the store copy, not shutdown.
  std::future<void> publication;
  };
// A nonempty OPENGOTHIC_BASE selects and validates an explicit base.
// Empty has the same meaning as unset: automatic resolution.
Selection resolve(const std::filesystem::path& gothic, const BaseImport::Files& mounted,
                  const BaseImport::Progress& log);
}
