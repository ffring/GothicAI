# Shared by the standalone converter and the game. Paths in the digest are relative
# to OpenGothic so identical sources have the same identity on every machine.
get_filename_component(BASE_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
set(BASE_SOURCES
  common/sha256.cpp common/baseimport.cpp common/basesources.cpp common/basemusic.c common/worldchunks.cpp common/worlddata.cpp common/modeldata.cpp common/fontdata.cpp
  common/programdata.cpp common/outputunits.cpp common/basemesh.cpp common/gothicglb.cpp common/gltfmesh.cpp common/asset/fromzenkit.cpp common/asset/worldfromzenkit.cpp common/asset/modelfromzenkit.cpp
  # The game's own path helpers, Dx8 file search and Dx8 parsers, owned here once.
  common/utils/fileutil.cpp common/dmusic/sourcefile.cpp common/dmusic/riff.cpp common/dmusic/segment.cpp common/dmusic/track.cpp
  common/dmusic/style.cpp common/dmusic/band.cpp common/dmusic/reference.cpp common/dmusic/info.cpp common/dmusic/pattern.cpp)
# Tempest's native file, directory and text-codec code that FileUtil and the Dx8 search use.
# The game links the whole Tempest target; the converter builds only these sources.
set(BASE_NATIVE_IO
  lib/Tempest/Engine/io/rfile.cpp lib/Tempest/Engine/io/idevice.cpp lib/Tempest/Engine/utility/dir.cpp
  lib/Tempest/Engine/utility/textcodec.cpp lib/Tempest/Engine/exceptions/exception.cpp)
file(GLOB_RECURSE BASE_FORMAT_INPUTS CONFIGURE_DEPENDS RELATIVE "${BASE_ROOT}"
  "${BASE_ROOT}/common/asset/*.h" "${BASE_ROOT}/common/asset/fromzenkit.cpp"
  "${BASE_ROOT}/lib/ZenKit/src/*" "${BASE_ROOT}/lib/ZenKit/include/*"
  "${BASE_ROOT}/lib/ZenKit/vendor/libsquish/*.h" "${BASE_ROOT}/lib/ZenKit/vendor/libsquish/*.cpp"
  "${BASE_ROOT}/lib/rapidjson/include/*.h"
  "${BASE_ROOT}/lib/dmusic/src/*" "${BASE_ROOT}/lib/dmusic/include/*"
  "${BASE_ROOT}/lib/Tempest/Engine/io/*" "${BASE_ROOT}/lib/Tempest/Engine/utility/*" "${BASE_ROOT}/lib/Tempest/Engine/exceptions/*")
# One list for every build topology: the format digest never depends on which targets exist.
list(APPEND BASE_FORMAT_INPUTS ${BASE_SOURCES} ${BASE_NATIVE_IO}
  common/sha256.h common/baseimport.h common/basemusic.h common/worldchunks.h common/worlddata.h common/modeldata.h common/fontdata.h common/programdata.h common/outputunits.h common/basemesh.h
  common/gothicglb.h common/gltfmesh.h common/utils/fileutil.h common/dmusic/sourcefile.h common/dmusic/riff.h common/dmusic/segment.h
  common/dmusic/track.h common/dmusic/style.h common/dmusic/band.h common/dmusic/reference.h common/dmusic/info.h common/dmusic/pattern.h
  common/dmusic/structs.h lib/Tempest/Engine/system/platform.h lib/Tempest/Engine/include/Tempest/File lib/Tempest/Engine/include/Tempest/Dir
  lib/Tempest/Engine/include/Tempest/TextCodec lib/Tempest/Engine/include/Tempest/Except lib/Tempest/Engine/include/Tempest/IDevice
  lib/Tempest/Engine/include/Tempest/Platform lib/cgltf/cgltf.h lib/miniz/miniz.h lib/miniz/miniz.c
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
target_include_directories(gothic-base-import PUBLIC "${BASE_ROOT}/common" "${BASE_ROOT}/lib/Tempest/Engine/include")
target_include_directories(gothic-base-import PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/baseimport-generated")
target_include_directories(gothic-base-import SYSTEM PRIVATE "${BASE_ROOT}/lib/rapidjson/include" "${BASE_ROOT}/lib/miniz"
  "${BASE_ROOT}/lib/dmusic/src" "${BASE_ROOT}/lib/dmusic/vendor/TinySoundFont")
# The source manifest runs the game's dmusic loader (target `dmusic`, static, added by the
# including project) and reads its parsed structures, which need C11.
set_target_properties(gothic-base-import PROPERTIES C_STANDARD 11)
target_link_libraries(gothic-base-import PUBLIC zenkit miniz dmusic)
if(TARGET Tempest)
  target_link_libraries(gothic-base-import PUBLIC Tempest)
else()
  list(TRANSFORM BASE_NATIVE_IO PREPEND "${BASE_ROOT}/")
  add_library(gothic-native-io STATIC ${BASE_NATIVE_IO})
  target_include_directories(gothic-native-io PUBLIC "${BASE_ROOT}/lib/Tempest/Engine/include"
    PRIVATE "${BASE_ROOT}/lib/Tempest/Engine")
  if(WIN32)
    target_compile_definitions(gothic-native-io PRIVATE NOMINMAX)
  endif()
  target_link_libraries(gothic-base-import PUBLIC gothic-native-io)
endif()
if(WIN32)
  target_link_libraries(gothic-base-import PUBLIC shlwapi)
endif()
# Importing textures pulls ZenKit's Squish into the game alongside Tempest's
# different version. Keep both implementations and give the import copy its own symbols.
target_compile_definitions(squish PRIVATE squish=gothic_import_squish)
target_compile_definitions(zenkit PRIVATE squish=gothic_import_squish)
if(NOT MSVC)
  target_compile_options(gothic-base-import PRIVATE -Wall -Wconversion)
endif()
