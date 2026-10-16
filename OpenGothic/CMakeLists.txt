cmake_minimum_required(VERSION 3.20)

project(OpenGothic LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(BUILD_SHARED_LIBS OFF)

option(OPENGOTHIC_ENABLE_FRAME_PROFILE "Compile opt-in frame profiling diagnostics" OFF)
option(OPENGOTHIC_ENABLE_APP_CADENCE "Compile the normal-game death cadence benchmark" OFF)

set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/opengothic)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/opengothic)
set(CMAKE_DEBUG_POSTFIX "")
set(CMAKE_RELWITHDEBINFO_POSTFIX "")
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_SKIP_RPATH ON)

add_library(${PROJECT_NAME} STATIC)
if(OPENGOTHIC_ENABLE_FRAME_PROFILE)
  target_compile_definitions(${PROJECT_NAME} PUBLIC OPENGOTHIC_ENABLE_FRAME_PROFILE=1)
endif()
if(OPENGOTHIC_ENABLE_APP_CADENCE)
  target_compile_definitions(${PROJECT_NAME} PUBLIC OPENGOTHIC_ENABLE_APP_CADENCE=1)
endif()
if(ANDROID)
  add_library(Gothic2Notr SHARED)
  add_library(Spacer SHARED)
else()
  add_executable(Gothic2Notr)
  add_executable(Spacer)
endif()

if(MSVC)
  add_definitions(-D_USE_MATH_DEFINES)
  add_definitions(-D_CRT_SECURE_NO_WARNINGS)
  add_definitions(-D_CRT_NONSTDC_NO_DEPRECATE)
  add_definitions(-D_ITERATOR_DEBUG_LEVEL=0)
  set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /wd4800")
endif()

if(NOT MSVC)
  target_compile_options(${PROJECT_NAME} PRIVATE -Wall -Wconversion -Wno-strict-aliasing -Werror)
  if(CMAKE_COMPILER_IS_GNUCC AND CMAKE_CXX_COMPILER_VERSION VERSION_GREATER_EQUAL "8.0")
    target_compile_options(${PROJECT_NAME} PRIVATE -Wno-class-memaccess)
  endif()
  if(CMAKE_CXX_COMPILER_VERSION VERSION_GREATER_EQUAL "7.1" AND NOT APPLE AND NOT ${CMAKE_CXX_COMPILER_ID} MATCHES "Clang")
    target_compile_options(${PROJECT_NAME} PRIVATE -Wno-format-truncation)
  endif()
  if(UNIX)
    # -rdynamic is needed to allow obtaining meaningful backtraces
    target_link_options(${PROJECT_NAME} PRIVATE -rdynamic)
  endif()
  if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU") # gcc
    # required by pthread_setname_np()
    add_definitions(-D_GNU_SOURCE)
  endif()
endif()

if(WIN32)
  add_definitions(-DNOMINMAX)
  if(MSVC)
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} /SUBSYSTEM:WINDOWS /ENTRY:mainCRTStartup" )
  elseif(${CMAKE_CXX_COMPILER_ID} MATCHES "Clang")
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -mwindows -lpthread")
  else()
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -mwindows -lstdc++fs")
  endif()
elseif(IOS)
  enable_language(OBJCXX)
  set(CMAKE_OSX_DEPLOYMENT_TARGET 15.0)
elseif(APPLE)
  enable_language(OBJCXX)
endif()

if(IOS)
  file(GLOB_RECURSE ObjCSOURCES "common/*.mm")
elseif(APPLE)
  target_link_libraries(${PROJECT_NAME} "-framework AppKit")
  file(GLOB_RECURSE ObjCSOURCES "common/*.mm")
endif()

## OpenGothic

# sources
file(GLOB_RECURSE OPENGOTHIC_SOURCES "common/*.h" "common/*.cpp")
if(NOT OPENGOTHIC_ENABLE_FRAME_PROFILE)
  list(REMOVE_ITEM OPENGOTHIC_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/common/frameprofile.cpp")
endif()
if(NOT OPENGOTHIC_ENABLE_APP_CADENCE)
  list(REMOVE_ITEM OPENGOTHIC_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/common/appcadence.cpp")
endif()
file(GLOB_RECURSE SPACER_SOURCES "spacer/*.h" "spacer/*.cpp")

target_sources(${PROJECT_NAME} PRIVATE ${OPENGOTHIC_SOURCES} ${ObjCSOURCES})

# The movable-vob allowlist is data, embedded so installed builds do not depend
# on their working directory. Editing the CSV also triggers reconfiguration.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/data/movable-vobs.csv")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/data/movable-vobs.csv" MOVABLE_VOBS_CSV)
configure_file(common/world/movablevobsdata.h.in generated/movablevobsdata.h @ONLY)
target_include_directories(${PROJECT_NAME} PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")

# shaders
add_subdirectory(shader)
target_link_libraries(${PROJECT_NAME} GothicShaders)

# editor icons and such
add_subdirectory(spacer/assets)

include_directories("common")

## submodule dependencies

# edd-dbg
if(WIN32)
  add_subdirectory(lib/edd-dbg)
  include_directories(lib/edd-dbg/include)
  target_link_libraries(${PROJECT_NAME} edd_dbg)
endif()

if(WIN32)
  target_link_libraries(${PROJECT_NAME} shlwapi DbgHelp)
elseif(ANDROID)
  target_link_libraries(${PROJECT_NAME} dl)
elseif(UNIX)
  target_link_libraries(${PROJECT_NAME} -lpthread -ldl)
endif()

# ZenKit
set(BUILD_SQUISH_WITH_SSE2     OFF CACHE INTERNAL "")
set(ZK_ENABLE_ASAN             OFF CACHE INTERNAL "")
set(ZK_ENABLE_DEPRECATION      OFF CACHE INTERNAL "")

add_subdirectory(lib/ZenKit)
target_link_libraries(${PROJECT_NAME} zenkit)

# dmusic
set(DM_BUILD_STATIC            ON  CACHE INTERNAL "")
set(DM_ENABLE_ASAN             OFF CACHE INTERNAL "")

add_subdirectory(lib/dmusic)
target_link_libraries(${PROJECT_NAME} dmusic)

# TinySoundFont
add_definitions(-DTSF_NO_STDIO)
include_directories(lib/TinySoundFont)

# Rapidjson
include_directories(lib/rapidjson/include)

# Tempest
if(WIN32)
  set(TEMPEST_BUILD_SHARED ON  CACHE INTERNAL "")
else()
  set(TEMPEST_BUILD_SHARED OFF CACHE INTERNAL "")
endif()
add_subdirectory(lib/Tempest/Engine)
include_directories(lib/Tempest/Engine/include)
target_link_libraries(${PROJECT_NAME} Tempest)

# zip
include_directories(lib/miniz)
add_definitions(-DMINIZ_NO_STDIO)
add_library(miniz STATIC EXCLUDE_FROM_ALL "lib/miniz/miniz.h" "lib/miniz/miniz.c")
target_link_libraries(${PROJECT_NAME} miniz)

# Jolt Physics: single precision, static library, portable instruction sets.
set(JPH_BUILD_SHARED_LIBS                OFF CACHE INTERNAL "")
set(DOUBLE_PRECISION                     OFF CACHE INTERNAL "")
set(CROSS_PLATFORM_DETERMINISTIC          ON CACHE INTERNAL "")
set(OVERRIDE_CXX_FLAGS                    OFF CACHE INTERNAL "")
set(INTERPROCEDURAL_OPTIMIZATION          OFF CACHE INTERNAL "")
set(USE_STATIC_MSVC_RUNTIME_LIBRARY       OFF CACHE INTERNAL "")
set(ENABLE_ALL_WARNINGS                   OFF CACHE INTERNAL "")
set(ENABLE_INSTALL                       OFF CACHE INTERNAL "")
set(DEBUG_RENDERER_IN_DEBUG_AND_RELEASE   OFF CACHE INTERNAL "")
set(DEBUG_RENDERER_IN_DISTRIBUTION        OFF CACHE INTERNAL "")
set(PROFILER_IN_DEBUG_AND_RELEASE         OFF CACHE INTERNAL "")
set(PROFILER_IN_DISTRIBUTION              OFF CACHE INTERNAL "")
set(ENABLE_OBJECT_STREAM                 OFF CACHE INTERNAL "")
set(JPH_USE_DX12                         OFF CACHE INTERNAL "")
set(JPH_USE_VK                           OFF CACHE INTERNAL "")
set(JPH_USE_MTL                          OFF CACHE INTERNAL "")
set(JPH_USE_CPU_COMPUTE                  OFF CACHE INTERNAL "")
foreach(JOLT_X86_FEATURE SSE4_1 SSE4_2 AVX AVX2 AVX512 LZCNT TZCNT F16C FMADD)
  set(USE_${JOLT_X86_FEATURE} OFF CACHE INTERNAL "")
endforeach()
add_subdirectory(lib/JoltPhysics/Build)

# Jolt's inline headers inherit the same floating point behavior as its library.
# Treat the upstream headers as system headers under the engine's -Werror policy.
get_target_property(JOLT_INCLUDE_DIRECTORIES Jolt INTERFACE_INCLUDE_DIRECTORIES)
set_property(TARGET Jolt PROPERTY INTERFACE_SYSTEM_INCLUDE_DIRECTORIES
  "${JOLT_INCLUDE_DIRECTORIES}")
if(XCODE)
  # Upstream adds SSE4.2 for Xcode unconditionally; retain the portable x86 baseline.
  set_property(TARGET Jolt PROPERTY XCODE_ATTRIBUTE_OTHER_CPLUSPLUSFLAGS[arch=x86_64]
    "$(inherited) -msse2")
endif()
if(MSVC)
  target_compile_options(Jolt PUBLIC /fp:precise)
else()
  target_compile_options(Jolt PUBLIC -ffp-contract=off)
endif()
target_link_libraries(${PROJECT_NAME} Jolt)

# script for launching in binary directory
if(WIN32)
    add_custom_command(
        TARGET ${PROJECT_NAME} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy
        ${CMAKE_SOURCE_DIR}/scripts/Gothic2Notr.bat
        ${CMAKE_CURRENT_BINARY_DIR}/opengothic/Gothic2Notr.bat)
endif()

# in debug mode, enable sanitizers
if(${CMAKE_BUILD_TYPE} MATCHES "Debug" AND NOT ANDROID)
  add_compile_options(-fsanitize=address)
  add_compile_options(-fsanitize=leak)
  add_link_options   (-fsanitize=address)
  add_link_options   (-fsanitize=leak)
endif()

# the game
if(IOS)
  set_directory_properties("${CMAKE_SOURCE_DIR}" PROPERTIES
    PROPERTY XCODE_STARTUP_PROJECT "Gothic2Notr")
  set_target_properties(Gothic2Notr PROPERTIES
    MACOSX_BUNDLE_INFO_PLIST "Info.plist.in"
    MACOSX_BUNDLE_GUI_IDENTIFIER "opengothic.gothic2"
    MACOSX_BUNDLE_BUNDLE_VERSION 1
    MACOSX_BUNDLE_SHORT_VERSION_STRING "1.0"
    XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY "iPhone Developer")
endif()

target_sources(Gothic2Notr PRIVATE game/main.cpp)
if(WIN32)
  target_sources(Gothic2Notr PRIVATE icon.rc)
endif()
target_link_libraries(Gothic2Notr PRIVATE OpenGothic)

# external AI control bridge (fork-specific, see ai_bridge/)
add_subdirectory(ai_bridge)
target_link_libraries(Gothic2Notr PRIVATE AiBridge)

# Uses the shared engine/CPU simulation without constructing a window or graphics/audio device.
if(UNIX AND NOT APPLE AND NOT ANDROID)
  add_executable(gothic-body-runner game/bodyrunner.cpp)
  target_link_libraries(gothic-body-runner PRIVATE OpenGothic AiBridge)
endif()

# Asset-free contracts; run explicitly with their check-* targets.
if(NOT ANDROID AND NOT IOS)
  add_executable(gothic-aim-contract EXCLUDE_FROM_ALL tests/aim_contract.cpp)
  target_link_libraries(gothic-aim-contract PRIVATE OpenGothic AiBridge)
  add_custom_target(check-aim
    COMMAND $<TARGET_FILE:gothic-aim-contract>
    DEPENDS gothic-aim-contract USES_TERMINAL)
  add_executable(gothic-aimwalk-contract EXCLUDE_FROM_ALL tests/aimwalk_contract.cpp)
  target_link_libraries(gothic-aimwalk-contract PRIVATE OpenGothic AiBridge)
  add_custom_target(check-aimwalk
    COMMAND $<TARGET_FILE:gothic-aimwalk-contract>
    DEPENDS gothic-aimwalk-contract USES_TERMINAL)
  add_executable(gothic-physics-contract EXCLUDE_FROM_ALL tests/physics_contract.cpp)
  target_link_libraries(gothic-physics-contract PRIVATE OpenGothic AiBridge)
  add_custom_target(check-physics
    COMMAND $<TARGET_FILE:gothic-physics-contract>
    DEPENDS gothic-physics-contract
    USES_TERMINAL)
  add_executable(gothic-ragdoll-contract EXCLUDE_FROM_ALL tests/physics_ragdoll_contract.cpp)
  target_link_libraries(gothic-ragdoll-contract PRIVATE OpenGothic AiBridge)
  add_executable(gothic-ragdoll-save-contract EXCLUDE_FROM_ALL tests/ragdoll_save_contract.cpp)
  target_link_libraries(gothic-ragdoll-save-contract PRIVATE OpenGothic AiBridge)
  add_custom_target(check-ragdoll-save
    COMMAND $<TARGET_FILE:gothic-ragdoll-save-contract>
    DEPENDS gothic-ragdoll-save-contract USES_TERMINAL)
  add_custom_target(check-ragdoll
    COMMAND $<TARGET_FILE:gothic-ragdoll-contract>
    COMMAND $<TARGET_FILE:gothic-ragdoll-save-contract>
    DEPENDS gothic-ragdoll-contract gothic-ragdoll-save-contract USES_TERMINAL)
  add_executable(gothic-instance-storage-contract EXCLUDE_FROM_ALL tests/instance_storage_contract.cpp)
  target_link_libraries(gothic-instance-storage-contract PRIVATE OpenGothic AiBridge)
  add_custom_target(check-instance-storage
    COMMAND $<TARGET_FILE:gothic-instance-storage-contract>
    DEPENDS gothic-instance-storage-contract USES_TERMINAL)
endif()

# editor
target_sources(Spacer PRIVATE ${SPACER_SOURCES} spacer/main.cpp)
target_include_directories(Spacer PRIVATE "spacer")
target_link_libraries(Spacer PRIVATE OpenGothic SpacerAssets)

if(ANDROID)
  add_android_apk(OpenGothic-apk
    PACKAGE_NAME org.opengothic.app
    CODE Gothic2Notr
    MANIFEST android/AndroidManifest.xml)
endif()

# installation
install(TARGETS Gothic2Notr Spacer DESTINATION bin)
