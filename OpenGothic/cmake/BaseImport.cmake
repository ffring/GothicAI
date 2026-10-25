# Shared by the standalone converter and the game. Paths in the digest are relative
# to OpenGothic so identical sources have the same identity on every machine.
get_filename_component(BASE_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
set(BASE_SOURCES
  common/sha256.cpp common/baseimport.cpp common/worldchunks.cpp common/worlddata.cpp
  common/basemesh.cpp common/gothicglb.cpp common/gltfmesh.cpp common/asset/fromzenkit.cpp)
file(GLOB_RECURSE BASE_FORMAT_INPUTS CONFIGURE_DEPENDS RELATIVE "${BASE_ROOT}"
  "${BASE_ROOT}/common/asset/*.h" "${BASE_ROOT}/common/asset/fromzenkit.cpp"
  "${BASE_ROOT}/lib/ZenKit/src/*" "${BASE_ROOT}/lib/ZenKit/include/*"
  "${BASE_ROOT}/lib/ZenKit/vendor/libsquish/*.h" "${BASE_ROOT}/lib/ZenKit/vendor/libsquish/*.cpp"
  "${BASE_ROOT}/lib/rapidjson/include/*.h")
list(APPEND BASE_FORMAT_INPUTS ${BASE_SOURCES}
  common/sha256.h common/baseimport.h common/worldchunks.h common/worlddata.h common/basemesh.h
  common/gothicglb.h common/gltfmesh.h lib/cgltf/cgltf.h lib/miniz/miniz.h lib/miniz/miniz.c
  cmake/BaseImport.cmake)
list(REMOVE_DUPLICATES BASE_FORMAT_INPUTS)
list(SORT BASE_FORMAT_INPUTS)
set(BASE_FORMAT_SUMS "")
foreach(input IN LISTS BASE_FORMAT_INPUTS)
  if(NOT input STREQUAL "cmake/BaseImport.cmake")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${BASE_ROOT}/${input}")
  endif()
  file(SHA256 "${BASE_ROOT}/${input}" sum)
  string(APPEND BASE_FORMAT_SUMS "${input} ${sum}\n")
endforeach()
string(SHA256 BASE_FORMAT_SHA256 "${BASE_FORMAT_SUMS}")
file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/baseimport-generated/baseformat.h"
  CONTENT "#pragma once\ninline constexpr char BaseFormatSha256[] = \"@BASE_FORMAT_SHA256@\";\n" @ONLY)
list(TRANSFORM BASE_SOURCES PREPEND "${BASE_ROOT}/")
add_library(gothic-base-import STATIC ${BASE_SOURCES})
target_include_directories(gothic-base-import PUBLIC "${BASE_ROOT}/common")
target_include_directories(gothic-base-import PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/baseimport-generated")
target_include_directories(gothic-base-import SYSTEM PRIVATE "${BASE_ROOT}/lib/rapidjson/include" "${BASE_ROOT}/lib/miniz")
target_link_libraries(gothic-base-import PUBLIC zenkit miniz)
# Importing textures pulls ZenKit's Squish into the game alongside Tempest's
# different version. Keep both implementations and give the import copy its own symbols.
target_compile_definitions(squish PRIVATE squish=gothic_import_squish)
target_compile_definitions(zenkit PRIVATE squish=gothic_import_squish)
if(NOT MSVC)
  target_compile_options(gothic-base-import PRIVATE -Wall -Wconversion)
endif()
