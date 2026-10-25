#pragma once

#include "baseimport.h"
#include <memory>

namespace BaseStore {
class Lock;
struct Selection {
  std::filesystem::path path;
  // Keep the cache entry leased until its resource owner is destroyed.
  std::shared_ptr<Lock> lease;
  };
// OPENGOTHIC_BASE, including an empty value, bypasses automatic resolution.
Selection resolve(const std::filesystem::path& gothic, const BaseImport::Files& mounted,
                  const BaseImport::Progress& log);
}
