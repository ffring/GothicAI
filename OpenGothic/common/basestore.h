#pragma once

#include "baseimport.h"

namespace BaseStore {
// OPENGOTHIC_BASE, including an empty value, bypasses automatic resolution.
std::filesystem::path resolve(const std::filesystem::path& gothic, const BaseImport::Files& mounted,
                              const BaseImport::Progress& log);
}
