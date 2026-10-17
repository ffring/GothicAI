# Pinned upstream v1.6.0. Only the two libraries used by the bench are vendored.
file(GLOB RECAST_SOURCES CONFIGURE_DEPENDS Recast/Source/*.cpp)
file(GLOB DETOUR_SOURCES CONFIGURE_DEPENDS Detour/Source/*.cpp)
add_library(Recast STATIC ${RECAST_SOURCES})
add_library(Detour STATIC ${DETOUR_SOURCES})
target_include_directories(Recast SYSTEM PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/Recast/Include)
target_include_directories(Detour SYSTEM PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/Detour/Include)
# Large worlds need enough polygon references for the tiled world, without
# trading tile capacity against polygons per tile in a 32-bit reference.
target_compile_definitions(Detour PUBLIC DT_POLYREF64)
